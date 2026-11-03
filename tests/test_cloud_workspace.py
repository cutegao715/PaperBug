import pytest
from fastapi.testclient import TestClient
from paperagent_cloud import app as cloud


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CLOUD_DB", str(tmp_path / "cloud.sqlite"))
    monkeypatch.setattr(cloud, "generate_reply", lambda history: "回复：" + history[-1]["content"])
    with TestClient(cloud.app) as client:
        yield client


def register(client, name):
    r = client.post('/api/v1/auth/register', json={'username': name, 'password': 'strong-password-123'})
    assert r.status_code == 201
    return {'Authorization': 'Bearer ' + r.json()['token']}


def test_conversation_isolation_and_idempotency(client):
    a, b = register(client, 'alice'), register(client, 'bob')
    space = client.post('/api/v1/spaces', headers=a, json={'name':'论文'}).json()['id']
    assert client.get('/api/v1/spaces', headers=b).json() == []
    path = f'/api/v1/spaces/{space}/messages'
    assert client.get(path, headers=b).status_code == 404
    body = {'text':'分析研究问题', 'request_id':'request_123'}
    assert client.post(path, headers=b, json=body).status_code == 404
    assert client.post(path, headers=a, json=body).json()['reply'] == '回复：分析研究问题'
    assert client.post(path, headers=a, json=body).json()['reply'] == '回复：分析研究问题'
    assert client.post(path, headers=a, json={**body, 'text':'另一条'}).status_code == 409
    assert len(client.get(path, headers=a).json()) == 2
    assert client.get(path).status_code == 401
    assert client.post('/api/v1/auth/logout', headers=a).status_code == 200
    assert client.get(path, headers=a).status_code == 401


def test_failed_reply_can_retry_without_duplicate(client, monkeypatch):
    a = register(client, 'alice')
    space = client.post('/api/v1/spaces', headers=a, json={'name':'论文'}).json()['id']
    path = f'/api/v1/spaces/{space}/messages'
    body = {'text':'继续', 'request_id':'retry_123'}
    def fail(history):
        raise RuntimeError('provider failed')
    monkeypatch.setattr(cloud, 'generate_reply', fail)
    assert client.post(path, headers=a, json=body).status_code == 502
    monkeypatch.setattr(cloud, 'generate_reply', lambda history: '完成')
    assert client.post(path, headers=a, json=body).json()['reply'] == '完成'
    assert len(client.get(path, headers=a).json()) == 2
