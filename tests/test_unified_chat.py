import json
import pytest
from test_control_plane import service


def begin(c,a,**options):
    body={'device_id':a[2]['id'],'request_id':'conversation_test_001','goal':'Review and improve paper',
          'scope_digest':'a'*64,'confirmed':True,'allow_write':True}
    r=c.post(f'/api/v1/control/spaces/{a[3]}/conversations',headers=a[1]|{'X-Device-Token':a[2]['device_token']},json=body|options)
    assert r.status_code==201,r.text
    return r.json()['id']


def headers(a): return a[1]|{'X-Device-Token':a[2]['device_token']}


def test_cloud_full_protocol_and_sequence_idempotency(service,monkeypatch):
    from paperagent_cloud import conversation_api as api
    from paperagent_cloud.worker import tick
    c,(a,b)=service;rid=begin(c,a);path='/api/v1/control/conversations/'+rid
    obs={'goal':'Review','iteration':1,'current_context':{'drafts':['paper.md']}}
    payload={'sequence':1,'observation':obs}
    assert c.post(path+'/decisions',headers=headers(a),json=payload).status_code==201
    calls=[]
    monkeypatch.setattr(api,'decide',lambda observation:(calls.append(observation) or {'action':'write_draft','filename':'paper.md','content':'Revised'}))
    tick();assert len(calls)==1
    action=c.get(path+'/decisions/1',headers=headers(a)).json()['action']
    assert action['action']=='write_draft'
    assert c.post(path+'/decisions',headers=headers(a),json=payload).status_code==201
    assert c.post(path+'/decisions',headers=headers(a),json={'sequence':1,'observation':{'changed':True}}).status_code==409
    assert c.post(path+'/decisions',headers=headers(a),json={'sequence':2,'observation':obs}).status_code==409
    assert c.post(path+'/decisions/1/result',headers=headers(a),json={'result':{'status':'ok'}}).status_code==200
    assert c.post(path+'/decisions',headers=headers(a),json={'sequence':2,'observation':obs}).status_code==201
    assert c.get(path,headers=headers(b)).status_code==404
    assert c.get(path,headers=a[1]).status_code==401


def test_cloud_conversation_fences_revocation_and_expired_worker(service,monkeypatch):
    from paperagent_cloud import conversation_api as api
    from paperagent_cloud.worker import tick
    from paperagent_cloud import app as cloud
    c,(a,b)=service;rid=begin(c,a);path='/api/v1/control/conversations/'+rid
    c.post(path+'/decisions',headers=headers(a),json={'sequence':1,'observation':{'goal':'test'}})
    def expire(obs):
        with cloud.db() as db: db.execute('UPDATE chat_decisions SET lease_until=0')
        return {'action':'chat_reply','text':'stale'}
    monkeypatch.setattr(api,'decide',expire);tick()
    assert c.get(path+'/decisions/1',headers=headers(a)).json()['action'] is None
    def revoke(obs):
        assert c.delete(path,headers=headers(a)).status_code==200
        return {'action':'chat_reply','text':'revoked'}
    monkeypatch.setattr(api,'decide',revoke);tick()
    assert c.get(path,headers=headers(a)).json()['status']=='cancelled'
    assert c.get(path+'/decisions/1',headers=headers(a)).json()['action'] is None


def test_cloud_readonly_rejects_write_action(service,monkeypatch):
    from paperagent_cloud import conversation_api as api
    from paperagent_cloud.worker import tick
    c,(a,b)=service;rid=begin(c,a,allow_write=False);path='/api/v1/control/conversations/'+rid
    c.post(path+'/decisions',headers=headers(a),json={'sequence':1,'observation':{'goal':'test'}})
    monkeypatch.setattr(api,'decide',lambda obs:{'action':'write_draft','filename':'paper.md','content':'bad'})
    tick()
    assert c.get(path+'/decisions/1',headers=headers(a)).json()['action']['action']=='ask_user'


def local_workspace(tmp_path,a):
    from paperagent.workspace.workspace import Workspace
    from paperagent.workspace.state_store import write_json
    ws=Workspace('paper',tmp_path/'local').init('research')
    ws.write_draft('paper.md','Original')
    write_json(ws.dir/'meta.json',{'user_id':a[0],'project_id':'paper','workspace_path':str(ws.dir.resolve()),
        'space_id':a[3],'device_id':a[2]['id'],'cloud_url':'https://example.test','sync_mode':'local_only'})
    return ws


def test_inline_authorization_uploads_nothing_until_approved(service,tmp_path):
    from paperagent.tasks.cloud_conversation import CloudConversation
    c,(a,b)=service;ws=local_workspace(tmp_path,a);calls=[]
    cc=CloudConversation(ws,lambda *args,**kw:calls.append(args))
    card=cc.prepare({'text':'Review','mode':'auto','workflow_mode':'off'})
    assert card['status']=='authorization_required' and calls==[]
    assert cc.cards()[card['authorization_id']]['status']=='pending'
    assert len(ws.list_messages())==2
    cc.reject(card['authorization_id'])
    assert calls==[] and cc.cards()[card['authorization_id']]['status']=='rejected'


def test_cloud_adapter_full_local_loop_and_personal_filter(service,tmp_path,monkeypatch):
    from paperagent.tasks.cloud_conversation import CloudConversation,CloudDecisionAgent
    from paperagent.agent.decision_loop import DecisionLoop
    from paperagent.harness.tool_executor import ToolExecutor
    from paperagent.schemas.policy import ResearchPolicy
    from paperagent_cloud import conversation_api as api
    from paperagent_cloud.worker import tick
    c,(a,b)=service;ws=local_workspace(tmp_path,a)
    def transport(method,origin,path,body=None,headers=None):
        if method=='GET' and '/decisions/' in path: tick()
        r=c.request(method,path,json=body,headers=a[1]|{'X-Device-Token':a[2]['device_token']}|(headers or {}))
        if r.status_code>=300: raise RuntimeError(str(r.status_code))
        return r.json()
    cc=CloudConversation(ws,transport);card=cc.prepare({'text':'Improve','mode':'auto','workflow_mode':'off'})
    approved=cc.approve(card['authorization_id'],allow_write=True)
    actions=iter([{'action':'read_draft','filename':'paper.md'},
                  {'action':'write_draft','filename':'paper.md','content':'Improved'},
                  {'action':'chat_reply','text':'Done'}])
    observations=[]
    monkeypatch.setattr(api,'decide',lambda obs:(observations.append(obs) or next(actions)))
    agent=CloudDecisionAgent(cc,card['authorization_id']);scoped=agent.workspace()
    loop=DecisionLoop(scoped,ToolExecutor(ResearchPolicy(),scoped),agent,run_id=approved['local_run_id'],workflow_mode='off',on_step=agent.record_step)
    outcome=loop.run('Improve');agent.finish(outcome)
    assert outcome.status=='finished' and ws.read_draft('paper.md')=='Improved'
    assert len(observations)==3 and observations[1]['current_context']['last_action']=='read_draft'
    assert observations[0]['current_context']['profile']=={}
    assert cc.remote_status(card['authorization_id'])['status']=='finished'
    assert cc.approve(card['authorization_id'],allow_write=True)['cloud_run_id']==approved['cloud_run_id']
    with pytest.raises(ValueError): cc.approve(card['authorization_id'],allow_write=False)


def test_scoped_workspace_and_readonly_actions(service,tmp_path):
    from paperagent.tasks.cloud_conversation import CloudConversation,CloudDecisionAgent
    c,(a,b)=service;ws=local_workspace(tmp_path,a)
    def transport(method,origin,path,body=None,headers=None):
        r=c.request(method,path,json=body,headers=a[1]|{'X-Device-Token':a[2]['device_token']}|(headers or {}));r.raise_for_status();return r.json()
    cc=CloudConversation(ws,transport);card=cc.prepare({'text':'Review','mode':'auto'})
    cc.approve(card['authorization_id'],allow_write=False)
    agent=CloudDecisionAgent(cc,card['authorization_id']);scoped=agent.workspace()
    outside=ws.root/'projects/other/private.txt';outside.parent.mkdir(parents=True);outside.write_text('secret')
    assert not scoped.is_path_allowed(outside)
    with pytest.raises(PermissionError): scoped.resolve_path(str(outside))
    with pytest.raises(PermissionError): agent.check_action({'action':'write_draft','filename':'paper.md','content':'bad'})
    with pytest.raises(PermissionError): agent.check_action({'action':'import_file','path':str(outside)})


def test_main_chat_default_inline_consent_and_explicit_offline(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from paperagent.server import app
    from paperagent.api import control
    from paperagent.workspace.workspace import Workspace
    monkeypatch.setenv('PAPERAGENT_WORKSPACE',str(tmp_path));monkeypatch.delenv('PAPERAGENT_ACCOUNT',raising=False)
    monkeypatch.setenv('PAPERAGENT_SCHEDULER','off')
    control.RUNS.clear();Workspace('paper',tmp_path).init('test')
    calls=[];monkeypatch.setattr(control,'_run_agent',lambda run:calls.append(run))
    with TestClient(app) as c:
        r=c.post('/api/v1/projects/paper/messages',json={'text':'Hello'})
        assert r.status_code==200 and r.json()['status']=='cloud_setup_required'
        assert not calls and not control.RUNS
        assert c.get('/api/v1/projects/paper').json()['messages'][-1]['execution_owner']=='cloud'
        r=c.post('/api/v1/projects/paper/messages',json={'text':'Offline','mode':'offline'})
        assert r.status_code==200 and r.json()['mode']=='offline' and r.json()['run_id']
    control.RUNS.clear()


def test_main_chat_approval_starts_once_and_preserves_workflow(service,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from paperagent.server import app
    from paperagent.api import control
    from paperagent.tasks.cloud_conversation import CloudConversation
    c,(a,b)=service;ws=local_workspace(tmp_path,a)
    monkeypatch.setenv('PAPERAGENT_WORKSPACE',str(ws.root));monkeypatch.delenv('PAPERAGENT_ACCOUNT',raising=False)
    monkeypatch.setenv('PAPERAGENT_SCHEDULER','off');control.RUNS.clear()
    original=CloudConversation.__init__
    def transport(method,origin,path,body=None,headers=None):
        r=c.request(method,path,json=body,headers=a[1]|{'X-Device-Token':a[2]['device_token']}|(headers or {}));r.raise_for_status();return r.json()
    monkeypatch.setattr(CloudConversation,'__init__',lambda self,ws:original(self,ws,transport))
    runs=[];monkeypatch.setattr(control,'_run_agent',lambda run:runs.append(run))
    with TestClient(app) as local:
        reply=local.post('/api/v1/projects/paper/messages',json={'text':'Review','workflow_mode':'off'}).json()
        aid=reply['authorization_id'];path=f'/api/v1/projects/paper/messages/authorizations/{aid}'
        first=local.post(path,json={'approved':True,'allow_write':True});assert first.status_code==200,first.text
        second=local.post(path,json={'approved':True,'allow_write':True});assert first.json()['run_id']==second.json()['run_id']
        assert len(runs)==1 and runs[0].mode=='cloud' and runs[0].workflow_mode=='off'
        assert len([m for m in ws.list_messages() if m['role']=='user'])==1
        assert local.post(path,json={'approved':True,'allow_write':False}).status_code==409
    control.RUNS.clear()


def test_completion_outbox_survives_offline_ack(service,tmp_path,monkeypatch):
    from paperagent.tasks.cloud_conversation import CloudConversation,CloudDecisionAgent
    from paperagent_cloud import conversation_api as api
    from paperagent_cloud.worker import tick
    from types import SimpleNamespace
    c,(a,b)=service;ws=local_workspace(tmp_path,a);offline=False
    def transport(method,origin,path,body=None,headers=None):
        if offline and path.endswith('/finish'): raise RuntimeError('offline')
        if method=='GET' and '/decisions/' in path: tick()
        r=c.request(method,path,json=body,headers=a[1]|{'X-Device-Token':a[2]['device_token']}|(headers or {}));r.raise_for_status();return r.json()
    cc=CloudConversation(ws,transport);card=cc.prepare({'text':'Hello','mode':'auto'})
    cc.approve(card['authorization_id']);agent=CloudDecisionAgent(cc,card['authorization_id'])
    monkeypatch.setattr(api,'decide',lambda obs:{'action':'chat_reply','text':'Done'})
    agent.decide({'goal':'Hello'});agent.record_step({'action':{'action':'chat_reply','text':'Done'},'result':{'done':True,'chat_reply':'Done'}})
    offline=True
    with pytest.raises(RuntimeError): agent.finish(SimpleNamespace(status='finished',summary='Done'))
    assert cc.row(card['authorization_id'])['pending_finish']['status']=='finished'
    offline=False;cc.flush_pending()
    assert cc.remote_status(card['authorization_id'])['status']=='finished'
    assert not cc.row(card['authorization_id']).get('pending_finish')


def test_main_chat_cloud_http_run_and_followup(service,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from paperagent.server import app
    from paperagent.api import control
    from paperagent.tasks.cloud_conversation import CloudConversation
    from paperagent_cloud import conversation_api as api
    from paperagent_cloud.worker import tick
    c,(a,b)=service;ws=local_workspace(tmp_path,a)
    monkeypatch.setenv('PAPERAGENT_WORKSPACE',str(ws.root));monkeypatch.delenv('PAPERAGENT_ACCOUNT',raising=False)
    monkeypatch.setenv('PAPERAGENT_SCHEDULER','off');control.RUNS.clear()
    original=CloudConversation.__init__
    def transport(method,origin,path,body=None,headers=None):
        if method=='GET' and '/decisions/' in path: tick()
        r=c.request(method,path,json=body,headers=a[1]|{'X-Device-Token':a[2]['device_token']}|(headers or {}));r.raise_for_status();return r.json()
    monkeypatch.setattr(CloudConversation,'__init__',lambda self,ws:original(self,ws,transport))
    monkeypatch.setattr(control,'LLMClient',lambda:pytest.fail('Local model must never be created in cloud mode'))
    observations=[]
    def decide(obs):
        observations.append(obs)
        if len(observations)==1:return {'action':'write_draft','filename':'paper.md','content':'Cloud revised'}
        if len(observations)==2:return {'action':'chat_reply','text':'First done'}
        assert any('First done' in m['content'] for m in obs['current_context']['recent_messages'])
        return {'action':'chat_reply','text':'Followup done'}
    monkeypatch.setattr(api,'decide',decide)
    with TestClient(app) as local:
        for text in ('Improve','继续'):
            response=local.post('/api/v1/projects/paper/messages',json={'text':text,'workflow_mode':'off'}).json()
            aid=response['authorization_id']
            started=local.post(f'/api/v1/projects/paper/messages/authorizations/{aid}',json={'approved':True,'allow_write':True}).json()
            with local.stream('GET','/api/v1/runs/'+started['run_id']+'/events') as stream:
                events=list(stream.iter_lines())
            assert any('run_finished' in event and 'finished' in event for event in events)
        assert ws.read_draft('paper.md')=='Cloud revised'
        assert local.get('/api/v1/projects/paper').json()['messages'][-1]['content']=='Followup done'
    control.RUNS.clear()


@pytest.mark.parametrize('tool,input',[
    ('formula_check',{'formula':'x+y','save':True}),
    ('diagram_generate',{'description':'test','save':True}),
    ('chart_generate',{'output_filename':'chart.svg','data':[1]}),
    ('fill_docx_template',{'template_path':'x.docx','output_filename':'out.docx'}),
    ('file_import',{'path':'x.doc','conversion_dir':'converted'}),
])
def test_readonly_cannot_use_tools_with_write_side_effects(tool,input):
    from paperagent.agent.action_permissions import action_writes
    assert action_writes({'action':'tool_call','tool_id':tool,'input':input})


def test_rejected_request_never_enters_other_cloud_context(service,tmp_path):
    from paperagent.tasks.cloud_conversation import CloudConversation,CloudDecisionAgent
    from paperagent.harness.context_assembler import assemble_observation
    from paperagent.agent.decision_loop import DecisionLoop
    from paperagent.harness.tool_executor import ToolExecutor
    from paperagent.schemas.policy import ResearchPolicy
    from paperagent.schemas.actions import ReadContextAction
    c,(a,b)=service;ws=local_workspace(tmp_path,a)
    def transport(method,origin,path,body=None,headers=None):
        r=c.request(method,path,json=body,headers=a[1]|{'X-Device-Token':a[2]['device_token']}|(headers or {}));r.raise_for_status();return r.json()
    cc=CloudConversation(ws,transport)
    first=cc.prepare({'text':'Safe request','mode':'auto'})
    rejected=cc.prepare({'text':'UNAPPROVED_SECRET','mode':'auto'});cc.reject(rejected['authorization_id'])
    cc.approve(first['authorization_id']);agent=CloudDecisionAgent(cc,first['authorization_id']);scoped=agent.workspace()
    obs=assemble_observation(scoped,'Safe request',1,10,10,None,None,[])
    assert 'UNAPPROVED_SECRET' not in json.dumps(agent.model_context(obs))
    loop=DecisionLoop(scoped,ToolExecutor(ResearchPolicy(),scoped),agent)
    assert 'UNAPPROVED_SECRET' not in json.dumps(loop._read_context(ReadContextAction(kind='messages')))


def test_cloud_tool_output_scope_and_mcp_need_separate_consent(service,tmp_path):
    from paperagent.tasks.cloud_conversation import CloudConversation,CloudDecisionAgent
    c,(a,b)=service;ws=local_workspace(tmp_path,a)
    def transport(method,origin,path,body=None,headers=None):
        r=c.request(method,path,json=body,headers=a[1]|{'X-Device-Token':a[2]['device_token']}|(headers or {}));r.raise_for_status();return r.json()
    cc=CloudConversation(ws,transport);card=cc.prepare({'text':'Import','mode':'auto'})
    cc.approve(card['authorization_id'],allow_write=True);agent=CloudDecisionAgent(cc,card['authorization_id'])
    source=ws.dir/'input.wps';source.write_text('fixture')
    with pytest.raises(PermissionError): agent.check_action({'action':'tool_call','tool_id':'file_import','input':{'path':str(source),'conversion_dir':str(tmp_path/'outside')}})
    with pytest.raises(PermissionError): agent.check_action({'action':'tool_call','tool_id':'mcp_unscoped','input':{'directory':str(tmp_path)}})
