"""Functional V6 bindings with isolated stores and real artifact bytes."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.domain.lifecycle import LifecycleService
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.actions import WriteDraftAction, FinishAction
from paperagent.schemas.policy import ResearchPolicy
from paperagent.workflows.registry import select_workflow
from paperagent.workflows.runtime import WorkflowRuntime
from paperagent.workspace.workspace import Workspace


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_WORKSPACE', str(tmp_path))
    monkeypatch.setenv('PAPERAGENT_SCHEDULER', 'off')
    from paperagent.server import app
    with TestClient(app) as client:
        yield client


def workspace(root, identifier='a', kind='paper_conference'):
    ws = Workspace(identifier, root).init('连接验收')
    LifecycleService(ws).save(lifecycle_type=kind, stage='topic', reason='测试研究')
    return ws


def test_files_are_selectable_and_downloads_do_not_expose_unlisted_state(client, tmp_path):
    ws = workspace(tmp_path)
    other = workspace(tmp_path, 'b')
    ws.write_draft('main.md', '# Current manuscript')
    other.write_draft('private.md', 'OTHER_PROJECT_PRIVATE')
    options = client.get('/api/v1/projects/a/file-options').json()['files']
    assert [r['value'] for r in options] == ['drafts/main.md']
    assert options[0]['can_bind_output']
    content = client.get('/api/v1/projects/a/files/download', params={'value': 'drafts/main.md'})
    assert content.status_code == 200 and content.content.startswith(b'# Current')
    for path in ['../b/drafts/private.md', 'lifecycle.json', str(other.drafts_dir / 'private.md')]:
        assert client.get('/api/v1/projects/a/files/download', params={'value': path}).status_code == 404


def test_bound_download_returns_the_approved_snapshot_after_current_file_changes(client, tmp_path):
    ws = workspace(tmp_path)
    ws.write_draft('main.md', 'version one')
    runtime = WorkflowRuntime(ws)
    case = runtime.start('P1', scope='partial', required_deliverables=['论文正文'])
    runtime.update(case['case_id'], 'output', {'label': '论文正文', 'path': 'drafts/main.md'})
    ws.write_draft('main.md', 'version two')
    url = '/api/v1/projects/a/workflow-cases/' + case['case_id'] + '/file'
    response = client.get(url, params={'label': '论文正文'})
    assert response.content == b'version one'
    assert client.get(url.replace('/projects/a/', '/projects/b/'), params={'label': '论文正文'}).status_code == 404


def test_selected_partial_case_reaches_runtime_and_only_receives_its_own_draft(tmp_path):
    ws = workspace(tmp_path, kind='thesis')
    runtime = WorkflowRuntime(ws)
    first = runtime.start('T2', scope='full')
    chosen = runtime.start('T2', scope='partial', required_deliverables=['进度分析'])
    strategy = select_workflow(ws, '只整理进度分析', workflow_options={'case_id': chosen['case_id']})
    assert strategy['case_id'] == chosen['case_id']
    assert strategy['execution_policy']['scope'] == 'partial'
    assert strategy['drafting_contract']['requested_artifacts'] == ['进度分析']
    actions = [WriteDraftAction(filename='analysis.md', content='# 进度分析\n真实进度尚待作者核对。'), FinishAction(summary='已保存')]
    class Writer:
        def decide(self, obs):
            assert obs['workflow']['case_id'] == chosen['case_id']
            return actions.pop(0)
    outcome = DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=2), ws), Writer(),
        workflow_options={'case_id': chosen['case_id']}, log=lambda _: None).run('只整理进度分析')
    assert outcome.status == 'finished', outcome.summary
    assert runtime.get(chosen['case_id'])['outputs'][0]['label'] == '进度分析'
    assert not runtime.get(first['case_id'])['outputs']


def test_paused_case_does_not_break_loading_but_blocks_new_run(client, tmp_path):
    from paperagent.conversation import ConversationManager
    ws = workspace(tmp_path, kind='thesis')
    runtime = WorkflowRuntime(ws)
    case = runtime.start('T2', scope='partial')
    ConversationManager(ws).begin('整理进度', 'paused-case', workflow_id='thesis_midterm_report', workflow_options={'case_id': case['case_id']})
    runtime.update(case['case_id'], 'status', {'status': 'paused'}, actor='user')
    assert client.get('/api/v1/projects/a').status_code == 200
    body = {'text': '继续', 'mode': 'offline', 'resume': True}
    assert client.post('/api/v1/projects/a/messages', json=body).status_code == 422
    assert client.post('/api/v1/projects/a/messages', json={'text': '继续', 'workflow_options': {'case_id': 'wf_' + 'f' * 16}}).status_code == 422


def test_changed_lifecycle_keeps_room_accessible_with_old_conversation(client, tmp_path):
    from paperagent.conversation import ConversationManager
    ws = workspace(tmp_path, kind='thesis')
    c = WorkflowRuntime(ws).start('T2')
    ConversationManager(ws).begin('毕业论文中期', 'old-lifecycle', workflow_id='thesis_midterm_report', workflow_options={'case_id': c['case_id']})
    LifecycleService(ws).save(lifecycle_type='project', stage='application', reason='用户调整研究类型')
    response = client.get('/api/v1/projects/a')
    assert response.status_code == 200, response.text
    assert response.json()['workflow_notice']
    assert response.json()['lifecycle']['lifecycle_type'] == 'project'


def test_submission_rounds_retargeting_and_project_scoping(client, tmp_path):
    ws = workspace(tmp_path)
    workspace(tmp_path, 'b')
    base = '/api/v1/projects/a/submissions'
    sub = client.post(base, json={'title': 'Paper', 'journal_name': 'Journal A', 'manuscript_id': 'A-1', 'submitted_at': '2026-10-01'}).json()
    assert sub['submitted_at'] == '2026-10-01'
    for new in ['under_review', 'major_revision', 'submitted', 'under_review', 'minor_revision']:
        updated = client.post(base + '/' + sub['submission_id'] + '/status', json={
            'status': new, 'expected_status': sub['status'], 'source_note': '作者核对编辑来信后登记'})
        assert updated.status_code == 200, updated.text
        sub = updated.json()
    assert sub['revision_round'] == 2
    assert client.post(base + '/' + sub['submission_id'] + '/status', json={
        'status': 'accepted', 'expected_status': 'under_review', 'source_note': '过时页面'}).status_code == 409
    assert client.get('/api/v1/projects/b/submissions').json()['submissions'] == []
    assert client.post(base.replace('/a/', '/b/') + '/' + sub['submission_id'] + '/status', json={
        'status': 'accepted', 'expected_status': sub['status'], 'source_note': '不属于本研究'}).status_code == 404
    sub = client.post(base + '/' + sub['submission_id'] + '/status', json={
        'status': 'rejected', 'expected_status': sub['status'], 'source_note': '真实拒稿决定'}).json()
    assert sub['next_workflow'] == 'venue_matching'
    second = client.post(base, json={'title': 'Paper', 'journal_name': 'Journal B', 'manuscript_id': 'B-1', 'previous_submission_id': sub['submission_id']})
    assert second.status_code == 200 and second.json()['previous_submission_id'] == sub['submission_id']
    assert client.get(base).json()['submissions'][0]['status'] == 'rejected'


def test_submission_email_previews_then_applies_to_selected_research(client, tmp_path):
    workspace(tmp_path)
    workspace(tmp_path, 'b')
    email = {'subject': '编辑决定', 'text': 'Journal: Journal X\nManuscript ID: X-1\nStatus: Under Review'}
    assert client.post('/api/v1/projects/a/submission-email', json=email).json()['parsed']['status'] == 'under_review'
    assert client.get('/api/v1/projects/a/submissions').json()['submissions'] == []
    applied = client.post('/api/v1/projects/a/submission-email', json={**email, 'apply': True})
    assert applied.status_code == 200 and applied.json()['submission']['project_id'] == 'a'
    assert client.get('/api/v1/projects/b/submissions').json()['submissions'] == []


def test_submission_link_is_visible_to_agent_and_status_change_invalidates_approval(client, tmp_path):
    ws = workspace(tmp_path)
    sub = client.post('/api/v1/projects/a/submissions', json={'title': 'Paper', 'manuscript_id': 'X1'}).json()
    runtime = WorkflowRuntime(ws)
    case = runtime.start('P3', scope='partial', required_deliverables=['回复信'])
    case = runtime.update(case['case_id'], 'submission', {'submission_id': sub['submission_id']}, actor='user')
    strategy = select_workflow(ws, '整理本轮', workflow_options={'case_id': case['case_id']})
    assert strategy['linked_submission']['submission_id'] == sub['submission_id']
    old = case['fingerprint']
    client.post('/api/v1/projects/a/submissions/' + sub['submission_id'] + '/status', json={
        'status': 'under_review', 'expected_status': 'submitted', 'source_note': '编辑状态通知'})
    assert runtime.get(case['case_id'])['fingerprint'] != old


def test_presentation_and_submission_dialog_endpoints_create_downloadable_files(client, tmp_path):
    ws = workspace(tmp_path, kind='thesis')
    ws.write_draft('paper.md', '# My research\nAuthor supplied content.')
    ws.write_draft('slides.md', json.dumps({'title': '答辩', 'slides': [
        {'title': '研究问题', 'points': ['基于作者提供的材料'], 'notes': '讲稿内容'}]}, ensure_ascii=False))
    response = client.post('/api/v1/projects/a/deliveries/presentation', json={'filename': 'slides.md'})
    assert response.status_code == 200, response.text
    package = client.post('/api/v1/projects/a/deliveries/submission-package', json={'filename': 'paper.md', 'companions': []})
    assert package.status_code == 200, package.text
    files = client.get('/api/v1/projects/a/file-options').json()['files']
    for suffix in ['presentation.pptx', 'speaker-notes.md', 'submission.zip']:
        row = next(f for f in files if f['value'].endswith(suffix))
        download = client.get('/api/v1/projects/a/files/download', params={'value': row['value']})
        assert download.status_code == 200 and len(download.content) > 10
    assert client.post('/api/v1/projects/a/deliveries/presentation', json={'filename': 'paper.md'}).status_code == 422


def test_prototype_markup_styles_and_reference_files_are_unchanged():
    root = Path(__file__).resolve().parents[1]
    manifest = root / 'tests/fixtures/frontend_protected.json'
    assert manifest.is_file()
    for name, expected in json.loads(manifest.read_text(encoding='utf-8')).items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
