import base64
import importlib
import sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'frontend' / 'src'))
desktop = importlib.import_module('paperagent_desktop.app')


def test_selected_text_extraction_is_local_and_bounded():
    client = TestClient(desktop.app)
    body = {'filename':'notes.txt', 'data':base64.b64encode('研究内容'.encode()).decode()}
    result = client.post('/local/material/read', json=body, headers={'Origin':'http://127.0.0.1:8766'})
    assert result.status_code == 200
    assert result.json()['pages'][0]['text'] == '研究内容'
    assert 'path' not in result.json()


def test_local_read_rejects_other_origins_and_invalid_data():
    client = TestClient(desktop.app)
    body = {'filename':'notes.txt', 'data':base64.b64encode(b'private').decode()}
    assert client.post('/local/material/read', json=body, headers={'Origin':'https://attacker.example'}).status_code == 403
    assert client.post('/local/material/read', json={'filename':'notes.exe','data':'YWJj'}, headers={'Origin':'http://127.0.0.1:8766'}).status_code == 422


def test_local_read_reports_content_hash_and_truncation():
    import hashlib
    client = TestClient(desktop.app)
    raw = b'a' * 3600
    result = client.post('/local/material/read', json={'filename':'paper.txt', 'data':base64.b64encode(raw).decode()}, headers={'Origin':'http://127.0.0.1:8766'})
    assert result.json()['sha256'] == hashlib.sha256(raw).hexdigest()
    assert result.json()['pages'][0]['truncated'] is True


def test_authorized_material_queue_survives_restart_and_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_LOCAL_DB', str(tmp_path / 'local.sqlite'))
    client = TestClient(desktop.app)
    headers = {'Origin':'http://127.0.0.1:8766'}
    raw = b'local notes'
    body = {'filename':'notes.txt', 'data':base64.b64encode(raw).decode(), 'allow_cloud':True}
    grant = client.post('/local/materials', json=body, headers=headers)
    assert grant.status_code == 201
    material = grant.json()
    assert 'path' not in material
    op = {'operation_id':'operation_123', 'file_ref':material['file_ref'], 'sha256':material['sha256']}
    assert client.post('/local/queue', json=op, headers=headers).status_code == 201
    assert client.post('/local/queue', json=op, headers=headers).status_code == 201
    assert client.get('/local/queue/operation_123', headers=headers).json()['status'] == 'ready'
    result = client.post('/local/queue/operation_123/execute', headers=headers)
    assert result.json()['pages'][0]['text'] == 'local notes'
    assert client.post('/local/queue/operation_123/ack', headers=headers).json()['status'] == 'confirmed'
    assert client.post('/local/queue/operation_123/ack', headers=headers).json()['status'] == 'confirmed'
    assert client.get('/local/queue/operation_123', headers=headers).json()['status'] == 'confirmed'
    assert client.post(f"/local/materials/{material['file_ref']}/revoke", headers=headers).status_code == 200
    assert client.post('/local/queue/operation_123/execute', headers=headers).status_code == 403
    assert client.post('/local/queue', json={**op,'operation_id':'operation_456'}, headers=headers).status_code == 403


def test_material_requires_explicit_cloud_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_LOCAL_DB', str(tmp_path / 'local.sqlite'))
    client = TestClient(desktop.app)
    body = {'filename':'notes.txt','data':base64.b64encode(b'private').decode(),'allow_cloud':False}
    assert client.post('/local/materials', json=body, headers={'Origin':'http://127.0.0.1:8766'}).status_code == 403


def test_word_document_read_locally(tmp_path):
    from docx import Document
    file = tmp_path / 'sample.docx'
    document = Document()
    document.add_heading('研究方法', level=1)
    document.add_paragraph('采用访谈和主题分析。')
    document.save(file)
    raw = file.read_bytes()
    body = {'filename':'sample.docx', 'data':base64.b64encode(raw).decode()}
    response = TestClient(desktop.app).post('/local/material/read', json=body, headers={'Origin':'http://127.0.0.1:8766'})
    assert response.status_code == 200
    assert '采用访谈和主题分析。' in response.json()['pages'][0]['text']
