from fastapi.testclient import TestClient
from paperagent_cloud import app as cloud


def test_local_resume_needs_no_cloud(tmp_path, monkeypatch):
    from paperagent.workspace.workspace import Workspace
    from paperagent.conversation import ConversationManager
    from paperagent.harness.context_assembler import assemble_assistant_state
    def forbidden(*a, **k): raise AssertionError('local resume contacted cloud')
    monkeypatch.setattr('requests.request',forbidden)
    monkeypatch.setattr('requests.post',forbidden)
    ws=Workspace('p',tmp_path).init('local')
    manager=ConversationManager(ws)
    manager.begin('write with evidence','first',workflow_mode='off')
    manager.update(plan={'next':'verify'},decisions=['retain local decision'])
    manager.checkpoint(status='interrupted')
    resumed=ConversationManager(Workspace('p',tmp_path)).begin('continue','second',resume=True)
    assert resumed.plan == {'next':'verify'} and resumed.decisions == ['retain local decision']
    state=assemble_assistant_state(ws,'continue',1,5,5,None,None,[])
    assert state.current_context.conversation['decisions'] == ['retain local decision']


def test_cloud_resume_keeps_all_execution_guards(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_CLOUD_DB',str(tmp_path/'cloud.sqlite'))
    monkeypatch.setattr(cloud,'agent_decide',lambda obs: {'action':'read_pdf','path':obs['file_ref']})
    with TestClient(cloud.app) as client:
        auth=client.post('/api/v1/auth/register',json={'username':'resume','password':'test-password-123'}).json()
        headers={'Authorization':'Bearer '+auth['token']}
        device=client.post('/api/v1/devices',headers=headers,json={'name':'test'}).json()
        second=client.post('/api/v1/devices',headers=headers,json={'name':'second'}).json()
        sid=client.post('/api/v1/spaces',headers=headers,json={'name':'test'}).json()['id']
        task=client.post(f'/api/v1/spaces/{sid}/agent-tasks',headers=headers,json={'goal':'research','request_id':'resume_task'}).json()['id']
        path=f'/api/v1/agent-tasks/{task}/advance'
        body={'file_ref':'file_123','sha256':'a'*64,'device_id':device['id'],'workflow_id':'paper_writing','skill_id':'paper_review'}
        assert client.post(path,headers=headers,json=body).status_code==200
        for changes in ({'sha256':'b'*64},{'file_ref':'file_456'},{'device_id':second['id']}, {'workflow_id':None},{'skill_id':None}):
            assert client.post(path,headers=headers,json={**body,**changes}).status_code==409
        assert client.post(path,json=body).status_code==401
        with cloud.db() as c:
            c.execute("UPDATE agent_tasks SET status='queued' WHERE id=?",(task,))
            c.execute('UPDATE agent_context SET steps=10 WHERE task_id=?',(task,))
        assert client.post(path,headers=headers,json=body).status_code==409
