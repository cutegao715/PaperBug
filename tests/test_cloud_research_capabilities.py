from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend' / 'src'))


def test_original_skill_library_and_workflows_are_available_in_cloud(tmp_path):
    from paperagent_cloud.research.capabilities import research_context
    context = research_context('请评审这篇论文的方法与证据', workflow_id='paper_writing', skill_id='paper_review')
    assert context['workflow']['id'] == 'paper_writing'
    assert context['skills']
    assert any(skill['id'] == 'paper_review' for skill in context['skills'])
    assert any(skill.get('procedure') or skill.get('instructions') or skill.get('principles') for skill in context['skills'])


def test_unknown_workflow_is_not_silently_accepted():
    import pytest
    from paperagent_cloud.research.capabilities import research_context
    with pytest.raises(ValueError):
        research_context('写作', workflow_id='nonexistent')


def test_capability_catalog_requires_auth_and_exposes_no_method_text(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from paperagent_cloud import app as cloud
    monkeypatch.setenv('PAPERAGENT_CLOUD_DB', str(tmp_path/'cloud.sqlite'))
    with TestClient(cloud.app) as client:
        assert client.get('/api/v1/research/capabilities').status_code == 401
        token = client.post('/api/v1/auth/register', json={'username':'alice','password':'test-password-123'}).json()['token']
        catalog = client.get('/api/v1/research/capabilities', headers={'Authorization':'Bearer '+token}).json()
        assert len(catalog['skills']) == 35
        assert len(catalog['workflows']) == 10
        assert all('procedure' not in entry for entry in catalog['skills'])


def test_cloud_decision_uses_original_agent_action_validation():
    from paperagent_cloud.research.original_agent import decide
    class Model:
        def chat_json(self, system, user):
            assert 'Research Agent' in system
            assert 'read_pdf' in system
            return {'action':'read_pdf', 'path':'file_123'}
    action = decide(Model(), {'goal':'读文档','file_ref':'file_123'})
    assert action == {'action':'read_pdf','path':'file_123'}
