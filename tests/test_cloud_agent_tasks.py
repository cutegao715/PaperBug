from fastapi.testclient import TestClient
import pytest
from paperagent_cloud import app as cloud


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_CLOUD_DB', str(tmp_path / 'tasks.sqlite'))
    with TestClient(cloud.app) as client:
        def create(name):
            token = client.post('/api/v1/auth/register', json={'username':name,'password':'test-password-123'}).json()['token']
            headers = {'Authorization':'Bearer '+token}
            space = client.post('/api/v1/spaces', headers=headers, json={'name':'论文'}).json()['id']
            return headers, space
        yield client, create('alice'), create('bob')


def test_task_creation_is_idempotent_and_isolated(clients):
    client, (a, space), (b, _) = clients
    url = f'/api/v1/spaces/{space}/agent-tasks'
    body = {'goal':'分析这篇论文', 'request_id':'task_request_1'}
    first = client.post(url, headers=a, json=body)
    assert first.status_code == 201
    task = first.json()
    assert task['status'] == 'queued'
    assert client.post(url, headers=a, json=body).json()['id'] == task['id']
    assert client.post(url, headers=a, json={**body,'goal':'另一项任务'}).status_code == 409
    assert client.get(f"/api/v1/agent-tasks/{task['id']}", headers=b).status_code == 404
    assert client.get(f"/api/v1/agent-tasks/{task['id']}", headers=a).json()['goal'] == body['goal']
    assert client.post(url, headers=b, json=body).status_code == 404


def test_task_survives_service_restart(clients):
    client, (a, space), _ = clients
    task = client.post(f'/api/v1/spaces/{space}/agent-tasks', headers=a, json={'goal':'读取文档','request_id':'task_request_2'}).json()
    cloud.startup()
    assert client.get(f"/api/v1/agent-tasks/{task['id']}", headers=a).json()['status'] == 'queued'


def test_operation_result_is_owner_scoped_and_idempotent(clients):
    client, (a, space), (b, _) = clients
    task = client.post(f'/api/v1/spaces/{space}/agent-tasks', headers=a, json={'goal':'分析文档', 'request_id':'task_request_3'}).json()
    device = client.post('/api/v1/devices', headers=a, json={'name':'电脑'}).json()
    device_headers = {**a, 'X-Device-Token':device['device_token']}
    path = f"/api/v1/agent-tasks/{task['id']}/operations"
    operation = client.post(path, headers=a, json={'file_ref':'file_123', 'sha256':'a'*64, 'device_id':device['id']}).json()
    assert operation['status'] == 'waiting_local'
    assert client.get(f"/api/v1/agent-operations/{operation['id']}", headers=b).status_code == 404
    result_path = f"/api/v1/agent-operations/{operation['id']}/result"
    result = {'file_ref':'file_123', 'sha256':'a'*64, 'pages':[{'page':1,'text':'研究方法'}]}
    assert client.post(result_path, headers=b, json=result).status_code == 404
    lease = client.post(f"/api/v1/agent-operations/{operation['id']}/claim", headers=device_headers).json()
    device_headers['X-Lease-Token'] = lease['lease_token']
    assert client.post(result_path, headers=device_headers, json={**result,'sha256':'b'*64}).status_code == 409
    assert client.post(result_path, headers=device_headers, json=result).json()['status'] == 'completed'
    assert client.post(result_path, headers=device_headers, json=result).json()['status'] == 'completed'
    assert client.post(result_path, headers=device_headers, json={**result,'pages':[{'page':1,'text':'changed'}]}).status_code == 409


def test_devices_are_owned_revocable_and_persist(clients):
    client, (a, _), (b, _) = clients
    created = client.post('/api/v1/devices', headers=a, json={'name':'我的电脑'})
    assert created.status_code == 201
    device = created.json()
    assert device['device_token']
    assert client.get('/api/v1/devices', headers=b).json() == []
    listed = client.get('/api/v1/devices', headers=a).json()
    assert listed[0]['id'] == device['id'] and 'device_token' not in listed[0]
    assert client.post(f"/api/v1/devices/{device['id']}/revoke", headers=b).status_code == 404
    cloud.startup()
    assert client.get('/api/v1/devices', headers=a).json()[0]['status'] == 'active'
    assert client.post(f"/api/v1/devices/{device['id']}/revoke", headers=a).json()['status'] == 'revoked'


def test_operation_lease_expiry_and_recovery(clients, monkeypatch):
    client, (a, space), (b, _) = clients
    task = client.post(f'/api/v1/spaces/{space}/agent-tasks', headers=a, json={'goal':'读取资料','request_id':'task_lease_1'}).json()
    device = client.post('/api/v1/devices', headers=a, json={'name':'设备'}).json()
    headers = {**a, 'X-Device-Token':device['device_token']}
    op = client.post(f"/api/v1/agent-tasks/{task['id']}/operations", headers=a, json={'file_ref':'file_123','sha256':'a'*64,'device_id':device['id']}).json()
    claim_path = f"/api/v1/agent-operations/{op['id']}/claim"
    assert client.post(claim_path, headers=b).status_code == 404
    first = client.post(claim_path, headers=headers).json()
    assert client.post(claim_path, headers=headers).status_code == 409
    with cloud.db() as conn:
        conn.execute('UPDATE agent_operations SET lease_until=0 WHERE id=?', (op['id'],))
    second = client.post(claim_path, headers=headers).json()
    assert second['lease_token'] != first['lease_token']
    result = {'file_ref':'file_123','sha256':'a'*64,'pages':[{'page':1,'text':'内容'}]}
    path = f"/api/v1/agent-operations/{op['id']}/result"
    assert client.post(path, headers={**headers, 'X-Lease-Token':first['lease_token']}, json=result).status_code == 409
    assert client.post(path, headers={**headers, 'X-Lease-Token':second['lease_token']}, json=result).json()['status'] == 'completed'
    assert client.post(f"/api/v1/devices/{device['id']}/revoke", headers=a).json()['status'] == 'revoked'


def test_operation_requires_bound_active_device(clients):
    client, (a, space), (b, _) = clients
    task = client.post(f'/api/v1/spaces/{space}/agent-tasks', headers=a, json={'goal':'读取资料', 'request_id':'task_device_1'}).json()
    device = client.post('/api/v1/devices', headers=a, json={'name':'电脑'}).json()
    other = client.post('/api/v1/devices', headers=b, json={'name':'其他电脑'}).json()
    path = f"/api/v1/agent-tasks/{task['id']}/operations"
    assert client.post(path, headers=a, json={'file_ref':'file_123', 'sha256':'a'*64, 'device_id':other['id']}).status_code == 404
    operation = client.post(path, headers=a, json={'file_ref':'file_123', 'sha256':'a'*64, 'device_id':device['id']}).json()
    result_path = f"/api/v1/agent-operations/{operation['id']}/result"
    payload = {'file_ref':'file_123', 'sha256':'a'*64, 'pages':[{'page':1,'text':'内容'}]}
    assert client.post(result_path, headers=a, json=payload).status_code == 401
    assert client.post(result_path, headers={**a,'X-Device-Token':other['device_token']}, json=payload).status_code == 401
    client.post(f"/api/v1/devices/{device['id']}/revoke", headers=a)
    assert client.post(result_path, headers={**a,'X-Device-Token':device['device_token']}, json=payload).status_code == 401
    assert client.post(f"/api/v1/devices/{device['id']}/revoke", headers=a).json()['status'] == 'revoked'
