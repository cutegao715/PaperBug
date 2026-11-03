import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from paperagent_cloud import app as cloud


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_CLOUD_DB', str(tmp_path/'cloud.sqlite'))
    with TestClient(cloud.app) as client:
        users=[]
        for name in ('alice','bob'):
            auth=client.post('/api/v1/auth/register',json={'username':name,'password':'test-password-123'}).json()
            headers={'Authorization':'Bearer '+auth['token']}
            device=client.post('/api/v1/devices',headers=headers,json={'name':'desktop'}).json()
            space=client.post('/api/v1/spaces',headers=headers,json={'name':'paper'}).json()['id']
            users.append((auth['user_id'],headers,device,space))
        yield client,users


def grant(client, user, **changes):
    uid,h,d,s=user
    body=dict(device_id=d['id'],file_ref='material_test',sha256='a'*64,
              actions=['read_pdf','verify_citations','search_local','draft_propose'],confirmed=True,ttl=3600)
    r=client.post(f'/api/v1/control/spaces/{s}/grants',headers=h,json=body|changes)
    assert r.status_code==201,r.text
    return r.json()


def submit(client,user,g, key='request_001'):
    uid,h,d,s=user
    r=client.post(f'/api/v1/control/spaces/{s}/tasks',headers=h,
        json={'goal':'Review this paper','grant_id':g['id'],'request_id':key})
    assert r.status_code==201,r.text
    return r.json()


def test_identity_roots_and_repository(tmp_path):
    from paperagent.identity import Identity
    from paperagent.workspace.repositories import FileSessionRepository,FileMemoryRepository
    a=Identity(user_id='alice',origin='https://example.com')
    b=Identity(user_id='bob',origin='https://example.com')
    assert a.local_root(tmp_path)!=b.local_root(tmp_path)
    ma=FileMemoryRepository(a.local_root(tmp_path));mb=FileMemoryRepository(b.local_root(tmp_path))
    from paperagent.tasks.memory_manager import MemoryManager
    MemoryManager(ma).remember('user','Alice private preference')
    assert MemoryManager(mb).relevant()['user']==[]
    p=a.local_root(tmp_path)/'session.json'
    store=FileSessionRepository(p);store.save({'value':1})
    assert store.get()['value']==1


def test_scoped_memory_and_session(service):
    c,(a,b)=service
    assert c.get('/api/v1/auth/me',headers=a[1]).json()['user_id']==a[0]
    path=f'/api/v1/control/spaces/{a[3]}/memories'
    r=c.post(path,headers=a[1],json={'content':'Private project decision','layer':'project','confirmed':True})
    assert r.status_code==201,r.text
    assert c.get(path,headers=b[1]).status_code==404
    assert c.get(path,headers=a[1]).json()['memories'][0]['content']=='Private project decision'
    sid=r.json()['memory_id']
    assert c.delete(path+'/'+sid,headers=b[1]).status_code==404
    task=submit(c,a,grant(c,a))
    assert c.get('/api/v1/control/tasks/'+task['id'],headers=b[1]).status_code==404
    assert c.get('/api/v1/control/tasks/'+task['id'],headers=a[1]).json()['execution_owner']=='cloud'


def test_enqueue_worker_finish_and_legacy_fence(service,monkeypatch):
    from paperagent_cloud.worker import tick
    c,(a,b)=service;g=grant(c,a)
    calls=[]
    monkeypatch.setattr(cloud,'agent_decide',lambda obs:(calls.append(obs) or {'action':'finish','summary':'done'}))
    task=submit(c,a,g)
    assert calls==[]
    body={'file_ref':g['file_ref'],'sha256':g['sha256'],'device_id':a[2]['id']}
    assert c.post('/api/v1/agent-tasks/'+task['id']+'/advance',headers=a[1],json=body).status_code==409
    assert tick()['processed']==1
    result=c.get('/api/v1/control/tasks/'+task['id'],headers=a[1]).json()
    assert result['status']=='succeeded' and result['answer']=='done'
    assert len(calls)==1
    assert tick()['processed']==0


def test_revoked_during_model_and_retry(service,monkeypatch):
    from paperagent_cloud.worker import tick
    c,(a,b)=service;g=grant(c,a);task=submit(c,a,g)
    def decide(obs):
        assert c.delete('/api/v1/control/grants/'+g['id'],headers=a[1]).status_code==200
        return {'action':'finish','summary':'must not commit'}
    monkeypatch.setattr(cloud,'agent_decide',decide)
    tick()
    row=c.get('/api/v1/control/tasks/'+task['id'],headers=a[1]).json()
    assert row['status']=='cancelled'
    assert not row.get('answer')


def test_claim_and_stale_worker_fencing(service):
    from paperagent_cloud import control
    c,(a,b)=service;task=submit(c,a,grant(c,a))
    first=control.claim_job(now=1000,lease_seconds=10)
    # Explicit clock is independent of enqueue's real next_attempt; use a far-future lease test.
    if first is None:
        with cloud.db() as db: db.execute('UPDATE control_jobs SET next_attempt=0')
        first=control.claim_job(now=1000,lease_seconds=10)
    assert first and control.claim_job(now=1001) is None
    second=control.claim_job(now=1011)
    assert second and second['lease_token']!=first['lease_token']
    with cloud.db() as db:
        with pytest.raises(Exception): control.require_lease(db,task['id'],first['lease_token'],now=1012)


def test_local_executor_permissions_versions_and_proposal(tmp_path):
    from paperagent.workspace.operation_executor import LocalExecutor
    from paperagent.workspace.workspace import Workspace
    ws=Workspace('paper',tmp_path).init('paper');ws.write_draft('paper.md','Original {{ref:missing}}')
    executor=LocalExecutor(ws)
    local=executor.authorize('drafts/paper.md',actions=['read_pdf','verify_citations','search_local','draft_propose'],confirmed=True)
    base={'id':'op1','file_ref':local['file_ref'],'sha256':local['sha256'],'kind':'verify_citations','payload':{}}
    result=executor.execute(base)
    assert result['data']['errors']
    assert executor.execute(base)==result
    proposal=executor.execute(base|{'id':'op2','kind':'draft_propose','payload':{'content':'Proposed'}})
    assert ws.read_draft('paper.md').startswith('Original')
    executor.apply('op2',confirmed=True)
    assert ws.read_draft('paper.md')=='Proposed'
    with pytest.raises(ValueError): executor.execute(base|{'id':'op3'})
    with pytest.raises(ValueError): executor.execute(base|{'id':'op4','kind':'shell'})


def test_complete_local_cloud_roundtrip_and_memory_scope(service,tmp_path,monkeypatch):
    from paperagent_cloud.worker import tick
    from paperagent.tasks.cloud_tasks import CloudTasks
    from paperagent.workspace.workspace import Workspace
    from paperagent.workspace.state_store import write_json
    c,(a,b)=service
    ws=Workspace('paper',tmp_path/'local').init('paper');ws.write_draft('paper.md','Test {{ref:missing}}')
    write_json(ws.dir/'meta.json',{'user_id':a[0],'project_id':'paper','workspace_path':str(ws.dir.resolve()),
        'space_id':a[3],'device_id':a[2]['id'],'cloud_url':'https://example.test','sync_mode':'local_only'})
    headers=a[1]|{'X-Device-Token':a[2]['device_token']}
    def transport(method,origin,path,body=None,headers=None):
        r=c.request(method,path,json=body,headers=(a[1]|{'X-Device-Token':a[2]['device_token']}| (headers or {})))
        r.raise_for_status();return r.json()
    c.post(f'/api/v1/control/spaces/{a[3]}/memories',headers=a[1],json={'content':'Alice secret','confirmed':True})
    c.post(f'/api/v1/control/spaces/{b[3]}/memories',headers=b[1],json={'content':'Bob secret','confirmed':True})
    def decide(obs):
        assert 'Alice secret' in json.dumps(obs['memories'])
        assert 'Bob secret' not in json.dumps(obs)
        if not obs['results']: return {'action':'tool_call','tool_id':'verify_citations','input':{}}
        assert obs['results'][0]['data']['errors']
        return {'action':'finish','summary':'Citation requires correction'}
    monkeypatch.setattr(cloud,'agent_decide',decide)
    tasks=CloudTasks(ws,transport)
    task=tasks.start('Review Alice secret','drafts/paper.md',confirmed=True,include_memories=True)
    first=tick(); polled=tasks.poll(); assert polled['completed']==1, (first,polled,tasks.status(task['id']))
    tick(); assert tasks.status(task['id'])['status']=='succeeded'
    assert tasks.poll()['status']=='ok'
    from paperagent.domain.store import DomainStore
    assert len(DomainStore(ws.root).list_notifications())==1
    tasks.poll();assert len(DomainStore(ws.root).list_notifications())==1
    from paperagent_cloud import monitors
    from paperagent.radar.sources import SourceBatch
    monkeypatch.setattr(monitors,'fetch_source',lambda *args:SourceBatch([{'title':'Graph research','url':'https://example.org/paper'}]))
    tasks.monitor('graph',confirmed=True)
    monitors.tick();tasks.poll();tasks.poll()
    assert len(DomainStore(ws.root).list_notifications())==2


def test_recurring_is_readonly_idempotent_and_revocable(service):
    from paperagent_cloud import control
    c,(a,b)=service;g=grant(c,a,actions=['read_pdf'])
    path=f'/api/v1/control/spaces/{a[3]}/schedules'
    response=c.post(path,headers=a[1],json={'goal':'Review updates','grant_id':g['id'],'interval_seconds':300,'confirmed':True})
    assert response.status_code==201,response.text
    control.schedule_due();control.schedule_due()
    with cloud.db() as db: assert db.execute('SELECT COUNT(*) FROM control_jobs').fetchone()[0]==1
    c.delete('/api/v1/control/grants/'+g['id'],headers=a[1])
    control.schedule_due(now=10**12)
    with cloud.db() as db:
        assert db.execute('SELECT enabled FROM control_schedules').fetchone()[0]==0
        assert db.execute('SELECT status FROM control_jobs').fetchone()[0]=='cancelled'


def test_worker_retry_and_process_restart_does_not_steal_lease(service,monkeypatch):
    from paperagent_cloud import control
    from paperagent_cloud.worker import tick
    c,(a,b)=service;task=submit(c,a,grant(c,a))
    def fail(obs): raise RuntimeError('provider down')
    monkeypatch.setattr(cloud,'agent_decide',fail)
    tick()
    with cloud.db() as db:
        row=db.execute('SELECT * FROM control_jobs').fetchone()
        assert row['status']=='ready' and row['attempts']==1 and row['next_attempt']>0
        db.execute('UPDATE control_jobs SET next_attempt=0')
    lease=control.claim_job()
    cloud.startup()
    assert control.claim_job() is None
    with cloud.db() as db: assert control.require_lease(db,task['id'],lease['lease_token'])


def test_worker_local_result_arrives_before_job_finalization(service,monkeypatch):
    from paperagent_cloud.worker import tick
    c,(a,b)=service;task=submit(c,a,grant(c,a))
    def advance(*args):
        # A device already returned its result while worker still holds the lease.
        with cloud.db() as db: db.execute("UPDATE agent_tasks SET status='queued' WHERE id=?",(task['id'],))
        return {'status':'waiting_local'}
    monkeypatch.setattr(cloud,'advance_task',advance)
    tick()
    with cloud.db() as db: assert db.execute('SELECT status FROM control_jobs').fetchone()[0]=='ready'


def test_worker_recovers_committed_terminal_step(service,monkeypatch):
    from paperagent_cloud import control
    c,(a,b)=service;task=submit(c,a,grant(c,a));control.claim_job()
    with cloud.db() as db:
        db.execute("UPDATE agent_tasks SET status='succeeded' WHERE id=?",(task['id'],))
        db.execute('UPDATE control_jobs SET lease_until=0')
    assert control.claim_job() is None
    with cloud.db() as db: assert db.execute('SELECT status FROM control_jobs').fetchone()[0]=='done'


def test_public_monitor_dedup_owner_and_revocation(service,monkeypatch):
    from paperagent_cloud import monitors
    from paperagent.radar.sources import SourceBatch
    c,(a,b)=service
    path=f'/api/v1/control/spaces/{a[3]}/monitors'
    assert c.post(path,headers=b[1],json={'topic':'graph','confirmed':True}).status_code==404
    mid=c.post(path,headers=a[1],json={'topic':'graph','confirmed':True}).json()['id']
    monkeypatch.setattr(monitors,'fetch_source',lambda *args:SourceBatch([{'title':'Graph research','doi':'10.1/graph','url':'https://example.org/paper'}]))
    monitors.tick()
    with cloud.db() as db:
        assert db.execute('SELECT COUNT(*) FROM relayed_notifications').fetchone()[0]==1
        db.execute('UPDATE control_monitors SET next_run=0')
    monitors.tick()
    with cloud.db() as db: assert db.execute('SELECT COUNT(*) FROM relayed_notifications').fetchone()[0]==1
    assert c.delete('/api/v1/control/monitors/'+mid,headers=b[1]).status_code==404
    c.delete('/api/v1/control/monitors/'+mid,headers=a[1])
    assert monitors.tick()==0


def test_account_mismatch_blocks_private_upload(tmp_path):
    from paperagent.tasks.cloud_tasks import CloudTasks
    from paperagent.workspace.workspace import Workspace
    from paperagent.workspace.state_store import write_json
    ws=Workspace('paper',tmp_path).init('paper')
    write_json(ws.dir/'meta.json',{'user_id':'alice','project_id':'paper','workspace_path':str(ws.dir.resolve()),
        'space_id':'space','device_id':'device','cloud_url':'https://example.test','sync_mode':'local_only'})
    calls=[]
    def transport(method,origin,path,body=None):
        calls.append((method,path,body));return {'user_id':'bob'}
    with pytest.raises(ValueError): CloudTasks(ws,transport)._call('POST','/private',{'secret':'draft'})
    assert calls==[('GET','/api/v1/auth/me',None)]


def test_cloud_context_budget_preserves_goal_and_marks_omissions():
    from paperagent.harness.cloud_context import fit_cloud_observation
    from paperagent.harness.context_budget import estimate_tokens
    from paperagent.product_config import load
    obs={'goal':'protected goal','allowed_local_tools':['read_pdf'],'results':[{'data':'x '*100000}]*3}
    fitted=fit_cloud_observation(obs)
    assert fitted['goal']==obs['goal'] and fitted['results'][0]['truncated']
    assert estimate_tokens(fitted)<=load().budget.context_input_tokens
    assert len(obs['results'])==3


def test_proposal_crash_recovery_and_safe_undo(tmp_path,monkeypatch):
    from paperagent.workspace.operation_executor import LocalExecutor
    from paperagent.workspace.workspace import Workspace
    ws=Workspace('paper',tmp_path).init('paper');ws.write_draft('paper.md','Original')
    executor=LocalExecutor(ws);g=executor.authorize('drafts/paper.md',['draft_propose'],confirmed=True)
    executor.execute({'id':'proposal','file_ref':g['file_ref'],'sha256':g['sha256'],'kind':'draft_propose','payload':{'content':'Proposed'}})
    original=ws.write_draft
    def crash(*args): original(*args);raise OSError('crash after write')
    monkeypatch.setattr(ws,'write_draft',crash)
    with pytest.raises(OSError): executor.apply('proposal',True)
    monkeypatch.setattr(ws,'write_draft',original)
    assert executor.apply('proposal',True)['status']=='applied'
    assert executor.proposals()[0]['before']=='Original'
    assert executor.undo('proposal',True)['status']=='undone'
    assert ws.read_draft('paper.md')=='Original'


def test_expired_worker_cannot_cancel_reclaimable_task(service,monkeypatch):
    from paperagent_cloud.worker import tick
    c,(a,b)=service;task=submit(c,a,grant(c,a))
    def expire(obs):
        with cloud.db() as db: db.execute('UPDATE control_jobs SET lease_until=0')
        return {'action':'finish','summary':'obsolete'}
    monkeypatch.setattr(cloud,'agent_decide',expire);tick()
    row=c.get('/api/v1/control/tasks/'+task['id'],headers=a[1]).json()
    assert row['status'] in ('queued','running') and not row.get('answer')
    monkeypatch.setattr(cloud,'agent_decide',lambda obs:{'action':'finish','summary':'fresh'})
    tick();assert c.get('/api/v1/control/tasks/'+task['id'],headers=a[1]).json()['answer']=='fresh'


def test_citation_grant_does_not_read_or_expose_unrelated_sources(tmp_path,monkeypatch):
    from paperagent.workspace.operation_executor import LocalExecutor
    from paperagent.workspace.workspace import Workspace
    from paperagent.schemas.evidence import Evidence
    from paperagent.kernel import citation_integrity
    ws=Workspace('paper',tmp_path).init('paper');ws.write_draft('paper.md','{{ref:ref_test}}')
    evidence=Evidence(evidence_id='ev_private',ref_id='ref_test',quote='secret',source_path='C:/HighlyPrivate/secret.pdf')
    monkeypatch.setattr(ws,'list_evidence',lambda:[evidence])
    accessed=[]
    monkeypatch.setattr(citation_integrity,'_quote_in_page',lambda *args:accessed.append(args))
    executor=LocalExecutor(ws);g=executor.authorize('drafts/paper.md',['verify_citations'],confirmed=True)
    result=executor.execute({'id':'check','file_ref':g['file_ref'],'sha256':g['sha256'],'kind':'verify_citations','payload':{}})
    assert not accessed
    assert 'HighlyPrivate' not in json.dumps(result) and 'secret.pdf' not in json.dumps(result)
    assert result['data']['source_verification']=='not_authorized'


def test_invalid_material_does_not_block_other_operations(service,tmp_path,monkeypatch):
    from paperagent_cloud.worker import tick
    from paperagent.tasks.cloud_tasks import CloudTasks
    from paperagent.workspace.workspace import Workspace
    from paperagent.workspace.state_store import write_json
    c,(a,b)=service
    ws=Workspace('paper',tmp_path/'local').init('paper')
    ws.write_draft('bad.md','before');ws.write_draft('good.md','valid')
    write_json(ws.dir/'meta.json',{'user_id':a[0],'project_id':'paper','workspace_path':str(ws.dir.resolve()),
        'space_id':a[3],'device_id':a[2]['id'],'cloud_url':'https://example.test','sync_mode':'local_only'})
    invalid_ref=None
    def transport(method,origin,path,body=None,headers=None):
        r=c.request(method,path,json=body,headers=a[1]|{'X-Device-Token':a[2]['device_token']}|(headers or {}))
        r.raise_for_status();result=r.json()
        if 'operations' in result: result['operations'].sort(key=lambda op:op['file_ref']!=invalid_ref)
        return result
    tasks=CloudTasks(ws,transport)
    bad=tasks.start('read','drafts/bad.md',confirmed=True);invalid_ref=bad['file_ref']
    good=tasks.start('read','drafts/good.md',confirmed=True)
    monkeypatch.setattr(cloud,'agent_decide',lambda obs:{'action':'read_pdf','path':obs['file_ref']})
    tick();tick();ws.write_draft('bad.md','changed')
    assert tasks.poll()['completed']==2
    assert tasks.status(bad['id'])['status']=='failed'
    assert tasks.status(good['id'])['status']=='queued'
