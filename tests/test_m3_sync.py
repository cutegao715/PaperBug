import pytest
from test_m2_sync import account, bind
from paperagent.workspace.workspace import Workspace
from paperagent.domain.store import DomainStore


def setup_sync(account,root,monkeypatch):
    from paperagent.tasks.cloud_sync import CloudSync
    client,headers,device,space=account
    def transport(method,origin,path,body=None):
        response=client.request(method,path,headers=headers,json=body)
        response.raise_for_status()
        return response.json()
    monkeypatch.setattr(CloudSync,'_request',lambda self,*a,**k:transport(*a,**k))
    ws=Workspace('p',root).init('test')
    sync=CloudSync(ws)
    challenge=sync.challenge('https://cloud.invalid',space,device)['challenge']
    sync.bind('https://cloud.invalid',space,device,challenge,True)
    sync.set_mode('cloud_sync',True)
    sync.set_preferences(intents=True,profile_intents=True,notifications=True,confirmed=True)
    return ws,sync


def test_intents_merge_tombstone_and_scope(account,tmp_path,monkeypatch):
    from paperagent.tasks.proactive_sync import sync_project
    ws,sync=setup_sync(account,tmp_path/'a',monkeypatch)
    store=DomainStore(ws.root)
    item=store.add_standing_intent(description='topic',scope={'project_id':'p'})
    other=store.add_standing_intent(description='private',scope={'project_id':'other'})
    assert sync_project(ws,now=100)['status']=='synced'
    ws2,_=setup_sync(account,tmp_path/'b',monkeypatch)
    sync_project(ws2,now=100)
    second=DomainStore(ws2.root)
    assert [i.description for i in second.list_standing_intents()]==['topic']
    second.delete_standing_intent(second.list_standing_intents()[0].intent_id)
    sync_project(ws2,now=200)
    # Stale source cannot resurrect a tombstone, even after a local edit.
    store.update_standing_intent(item.intent_id,description='stale edit')
    sync_project(ws,now=200)
    assert [i.intent_id for i in store.list_standing_intents()]==[other.intent_id]


def test_outage_backoff_does_not_block_local_notifications(account,tmp_path,monkeypatch):
    from paperagent.tasks.proactive_sync import sync_project, enqueue_notification
    from paperagent.tasks.notification_service import NotificationService
    from paperagent.tasks.cloud_sync import CloudSync
    ws,sync=setup_sync(account,tmp_path,monkeypatch)
    store=DomainStore(ws.root)
    calls=[]
    original=CloudSync._request
    def fail(self,*a,**k): calls.append(a); raise OSError('offline')
    monkeypatch.setattr(CloudSync,'_request',fail)
    note=NotificationService(store).notify(title='local notification',body='still works')
    assert len(store.list_notifications())==1 and calls==[]
    for hour in range(24):
        assert sync_project(ws,now=100+hour*3600)['status']=='deferred'
    before=len(calls)
    assert sync_project(ws,now=100+23*3600+1)['status']=='backoff'
    assert len(calls)==before
    monkeypatch.setattr(CloudSync,'_request',original)
    assert sync_project(ws,now=100000)['status']=='synced'
    assert sync_project(ws,now=100100)['status']=='synced'
    client,headers,device,space=account
    rows=client.get('/api/v1/notifications/relayed',params={'device_id':device},headers=headers).json()['items']
    assert len(rows)==1 and rows[0]['title']=='local notification'


def test_disabled_or_revoked_scope_never_contacts_cloud(account,tmp_path,monkeypatch):
    from paperagent.tasks.proactive_sync import sync_project
    from paperagent.tasks.cloud_sync import CloudSync
    ws,sync=setup_sync(account,tmp_path,monkeypatch)
    def fail(*a,**k): raise OSError('offline')
    sync.transport=fail
    with pytest.raises(OSError):
        sync.set_preferences(intents=True,profile_intents=True,notifications=False,confirmed=True)
    assert sync.metadata()['sync_preferences']['notifications'] is False
    with pytest.raises(OSError): sync.set_preferences()
    monkeypatch.setattr(CloudSync,'_request',lambda *a,**k:pytest.fail('network'))
    assert sync_project(ws)['status']=='disabled'


def test_notification_target_idempotency_and_user_isolation(account,tmp_path,monkeypatch):
    client,headers,device,space=account
    setup_sync(account,tmp_path,monkeypatch)
    second=client.post('/api/v1/devices',headers=headers,json={'name':'target'}).json()
    payload={'space_id':space,'project_id':'p','device_id':device,'idempotency_key':'relay_test_1',
             'target_device':second['id'],'title':'private','body':'body'}
    assert client.post('/api/v1/notifications/relay',headers=headers,json=payload).status_code==200
    assert client.post('/api/v1/notifications/relay',headers=headers,json=payload).status_code==200
    assert client.post('/api/v1/notifications/relay',headers=headers,json={**payload,'body':'changed'}).status_code==409
    assert client.get('/api/v1/notifications/relayed',headers=headers,params={'device_id':device}).json()['items']==[]
    target_headers={**headers,'X-Device-Token':second['device_token']}
    assert len(client.get('/api/v1/notifications/relayed',headers=target_headers,params={'device_id':second['id']}).json()['items'])==1
    auth=client.post('/api/v1/auth/register',json={'username':'outsider','password':'test-password-123'}).json()
    assert client.get('/api/v1/notifications/relayed',headers={**headers,'Authorization':'Bearer '+auth['token']},params={'device_id':device}).status_code==401
    assert client.get(f'/api/v1/spaces/{space}/intents',headers={**headers,'Authorization':'Bearer '+auth['token']},params={'device_id':device,'project_id':'p'}).status_code==404


def test_tied_intent_merge_is_order_independent(tmp_path):
    from paperagent.tasks.proactive_sync import merge,collect
    row={'id':'a'*32,'updated':123,'version':1,'origin':'first','deleted':False,
         'payload':{'description':'first','scope':{'profile':True},'created_at':'2026-01-01'}}
    other={**row,'origin':'second','payload':{**row['payload'],'description':'second'}}
    a,b=DomainStore(tmp_path/'a'),DomainStore(tmp_path/'b')
    merge(a,'p',[row,other],True)
    merge(b,'p',[other,row],True)
    assert collect(a,'p',True)==collect(b,'p',True)


def test_scheduler_records_hits_during_cloud_outage(account,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from paperagent.tasks.cloud_sync import CloudSync
    from paperagent.tasks import proactive_sync
    from paperagent.tasks.scheduler import Scheduler
    from paperagent.radar.pipeline import LiteratureRadar
    ws,sync=setup_sync(account,tmp_path,monkeypatch)
    store=DomainStore(tmp_path)
    store.add_automation(name='radar',action='radar_literature',cadence='daily',next_run='2000-01-01T00:00:00Z')
    radar=LiteratureRadar(store,search_fn=lambda _: {'candidates':[{'title':'Bayesian research methods','year':2026,'source':'test'}]})
    monkeypatch.setattr(CloudSync,'_request',lambda *a,**k: (_ for _ in ()).throw(OSError('offline')))
    monkeypatch.setattr(proactive_sync,'time',SimpleNamespace(time=lambda:100))
    log=Scheduler(store,radar_run=lambda:radar.run(extra_topics=['Bayesian'])).tick()
    assert len(store.list_radar_hits())==1
    assert store.list_notifications()
    assert any('deferred' in item for item in log)


def test_scope_change_creates_new_identity_and_deletes_old(account,tmp_path,monkeypatch):
    from paperagent.tasks.proactive_sync import sync_project
    ws,_=setup_sync(account,tmp_path,monkeypatch)
    store=DomainStore(tmp_path)
    item=store.add_standing_intent(description='scope change',scope={'project_id':'p'})
    assert sync_project(ws)['status']=='synced'
    updated=store.update_standing_intent(item.intent_id,scope={'profile':True})
    assert updated.sync_id!=item.sync_id
    assert sync_project(ws)['status']=='synced'
    rows=store.list_standing_intents(include_deleted=True)
    assert next(r for r in rows if r.sync_id==item.sync_id).deleted


def test_notify_returns_while_cloud_request_is_blocked(account,tmp_path,monkeypatch):
    import threading
    from paperagent.tasks.proactive_sync import sync_project
    from paperagent.tasks.cloud_sync import CloudSync
    from paperagent.tasks.notification_service import NotificationService
    ws,_=setup_sync(account,tmp_path,monkeypatch)
    entered,release,done=threading.Event(),threading.Event(),threading.Event()
    def blocked(*a,**k):
        entered.set();release.wait(5);raise OSError('offline')
    monkeypatch.setattr(CloudSync,'_request',blocked)
    worker=threading.Thread(target=lambda:sync_project(ws))
    worker.start();assert entered.wait(2)
    def notify():
        NotificationService(DomainStore(tmp_path)).notify(title='must return promptly')
        done.set()
    notifier=threading.Thread(target=notify);notifier.start()
    try:
        assert done.wait(1),'notification waited for cloud-held locks'
    finally:
        release.set();worker.join(5);notifier.join(5)
