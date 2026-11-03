from fastapi.testclient import TestClient
from paperagent_cloud import app as cloud
import base64
import hashlib
import importlib
import json
import sys
from pathlib import Path
from docx import Document


def test_agent_reads_local_material_then_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_CLOUD_DB', str(tmp_path/'cloud.sqlite'))
    monkeypatch.setenv('PAPERAGENT_LOCAL_DB', str(tmp_path/'local.sqlite'))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'frontend' / 'src'))
    desktop = importlib.import_module('paperagent_desktop.app')
    local = TestClient(desktop.app)
    origin = {'Origin':'http://127.0.0.1:8766'}
    source = tmp_path/'method.docx'
    document = Document()
    document.add_heading('研究方法', level=1)
    document.add_paragraph('方法内容')
    document.save(source)
    raw = source.read_bytes()
    grant = local.post('/local/materials', headers=origin, json={'filename':'method.docx','data':base64.b64encode(raw).decode(),'allow_cloud':True}).json()
    class SampleModel:
        def chat_json(self, system, user):
            assert 'Research Agent' in system
            observation = json.loads(user)
            assert observation['research']['workflow']['id'] == 'paper_writing'
            assert observation['research']['skills'][0]['id'] == 'paper_review'
            if not observation['method_results']:
                return {'action':'read_context','kind':'skill_resource','key':observation['research']['skills'][0]['key'],'resource_id':'general'}
            assert observation['method_results'][0]['content']
            if observation['results']:
                return {'action':'finish','summary':'分析：' + observation['results'][0]['pages'][0]['text']}
            return {'action':'read_pdf','path':observation['file_ref']}
    monkeypatch.setattr(cloud, 'LLMClient', SampleModel)
    with TestClient(cloud.app) as client:
        auth = client.post('/api/v1/auth/register', json={'username':'alice','password':'test-password-123'}).json()
        headers = {'Authorization':'Bearer '+auth['token']}
        device = client.post('/api/v1/devices', headers=headers, json={'name':'电脑'}).json()
        device_headers = {**headers,'X-Device-Token':device['device_token']}
        space = client.post('/api/v1/spaces', headers=headers, json={'name':'论文'}).json()['id']
        task = client.post(f'/api/v1/spaces/{space}/agent-tasks', headers=headers, json={'goal':'分析文档','request_id':'agent_loop_1'}).json()
        advance = f"/api/v1/agent-tasks/{task['id']}/advance"
        inputs = {'file_ref':grant['file_ref'],'sha256':hashlib.sha256(raw).hexdigest(),'device_id':device['id'], 'workflow_id':'paper_writing', 'skill_id':'paper_review'}
        step = client.post(advance, headers=headers, json=inputs)
        assert step.status_code == 200
        assert step.json()['status'] == 'queued'
        step = client.post(advance, headers=headers, json=inputs)
        assert step.json()['status'] == 'waiting_local'
        op = step.json()['operation']
        assert local.post('/local/queue', headers=origin, json={'operation_id':op['id'], 'file_ref':inputs['file_ref'], 'sha256':inputs['sha256']}).status_code == 201
        lease = client.post(f"/api/v1/agent-operations/{op['id']}/claim", headers=device_headers).json()
        local_result = local.post(f"/local/queue/{op['id']}/execute", headers=origin).json()
        result = {'file_ref':inputs['file_ref'],'sha256':local_result['sha256'],'pages':local_result['pages'],'total_pages':local_result['total_pages'],'pages_truncated':local_result['pages_truncated']}
        assert client.post(f"/api/v1/agent-operations/{op['id']}/result", headers={**device_headers,'X-Lease-Token':lease['lease_token']}, json=result).status_code == 200
        finished = client.post(advance, headers=headers, json=inputs)
        assert finished.json()['status'] == 'succeeded'
        assert finished.json()['answer'] == '分析：研究方法\n方法内容'
        assert source.read_bytes() == raw


def test_device_revoked_during_decision_prevents_local_operation(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_CLOUD_DB', str(tmp_path/'cloud.sqlite'))
    with TestClient(cloud.app) as client:
        token = client.post('/api/v1/auth/register', json={'username':'alice','password':'test-password-123'}).json()['token']
        headers = {'Authorization':'Bearer '+token}
        device = client.post('/api/v1/devices', headers=headers, json={'name':'设备'}).json()
        space = client.post('/api/v1/spaces', headers=headers, json={'name':'论文'}).json()['id']
        task = client.post(f'/api/v1/spaces/{space}/agent-tasks', headers=headers, json={'goal':'分析','request_id':'revoke_123'}).json()
        def decide(obs):
            client.post(f"/api/v1/devices/{device['id']}/revoke", headers=headers)
            return {'action':'read_pdf', 'path':obs['file_ref']}
        monkeypatch.setattr(cloud, 'agent_decide', decide)
        response = client.post(f"/api/v1/agent-tasks/{task['id']}/advance", headers=headers, json={'file_ref':'file_123','sha256':'a'*64,'device_id':device['id']})
        assert response.status_code == 409
        assert client.get(f"/api/v1/agent-tasks/{task['id']}", headers=headers).json()['status'] == 'paused'
