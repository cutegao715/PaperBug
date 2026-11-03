"""Real upload and draft editing used by the V6 result-first frontend."""
from fastapi.testclient import TestClient
import pytest

from paperagent.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_WORKSPACE', str(tmp_path))
    monkeypatch.setenv('PAPERAGENT_SCHEDULER', 'off')
    with TestClient(app) as client:
        yield client


def test_v6_upload_edit_and_conflict(client, tmp_path):
    pid = client.post('/api/v1/projects', json={'name':'V6 real import'}).json()['project_id']
    base = f'/api/v1/projects/{pid}'
    response = client.post(base+'/imports/upload', params={'filename':'research.md'},
                           content=b'# Research\n\nA real imported research note.')
    assert response.status_code == 200, response.text
    detail = response.json()['project']
    assert len(detail['materials']) == 1
    assert detail['references']
    name = detail['drafts'][0]
    draft = client.get(base+'/drafts/'+name).json()
    edited = '# Revised research\n\nSaved by the author.'
    response = client.put(base+'/drafts/'+name,
                          json={'raw':edited,'expected_raw':draft['raw']})
    assert response.status_code == 200
    assert client.get(base+'/drafts/'+name).json()['raw'] == edited
    stale = client.put(base+'/drafts/'+name,
                       json={'raw':'stale edit','expected_raw':draft['raw']})
    assert stale.status_code == 409
    versions = list((tmp_path/'projects'/pid/'draft_versions').glob('*.md'))
    assert len(versions) == 1
    assert versions[0].read_text(encoding='utf-8') == draft['raw']


def test_upload_rejects_bad_names_empty_and_unsupported(client):
    pid = client.post('/api/v1/projects', json={'name':'Validate uploads'}).json()['project_id']
    path = f'/api/v1/projects/{pid}/imports/upload'
    for filename, content in [('../outside.md',b'x'),('folder\\outside.md',b'x'),
                               ('program.exe',b'x'),('empty.md',b'')]:
        assert client.post(path, params={'filename':filename},content=content).status_code == 422
    assert client.get(f'/api/v1/projects/{pid}').json()['materials'] == []


def test_v6_shell_and_advanced_view(client):
    response=client.get('/')
    assert '/v6/app.js' in response.text
    assert 'initialProjects' not in response.text
    assert client.get('/v6/app.js').status_code == 200
    assert client.get('/v6/prototype.css').status_code == 200
    assert client.get('/classic').status_code == 200
    assert client.get('/v6/missing.png').status_code == 404
