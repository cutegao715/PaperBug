import json
import pytest
from fastapi.testclient import TestClient
from paperagent_cloud import app as cloud


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_CLOUD_DB', str(tmp_path / 'cloud.sqlite'))
    with TestClient(cloud.app) as client:
        auth = client.post('/api/v1/auth/register', json={'username':'syncuser','password':'test-password-123'}).json()
        headers = {'Authorization': 'Bearer ' + auth['token']}
        device = client.post('/api/v1/devices', headers=headers, json={'name':'test'}).json()
        space = client.post('/api/v1/spaces', headers=headers, json={'name':'paper'}).json()['id']
        yield client, {**headers, 'X-Device-Token':device['device_token']}, device['id'], space


def bind(client, headers, device, space, project='p'):
    path = f'/api/v1/spaces/{space}'
    body = {'device_id':device, 'project_id':project}
    response = client.post(path+'/bind_challenge', headers=headers, json=body)
    assert response.status_code == 200, response.text
    challenge = response.json()['challenge']
    response = client.post(path+'/bind_confirm', headers=headers, json={**body, 'challenge':challenge})
    assert response.status_code == 200, response.text
    return challenge


def test_binding_auth_one_use_and_modes(account):
    client, headers, device, space = account
    path = f'/api/v1/spaces/{space}'
    assert client.post(path+'/bind_challenge', json={'device_id':device,'project_id':'p'}).status_code == 401
    challenge = bind(*account)
    assert client.post(path+'/bind_confirm', headers=headers, json={'device_id':device,'project_id':'p','challenge':challenge}).status_code == 409
    assert client.get(path+'/sync',headers=headers).json()['sync_mode'] == 'local_only'
    assert client.post(path+'/sync',headers=headers,json={'sync_mode':'cloud_sync'}).status_code == 422
    assert client.post(path+'/sync',headers=headers,json={'sync_mode':'cloud_sync','confirmed':True}).status_code == 200
    cloud.init_db()  # migration is repeatable


def test_local_gate_never_calls_network(tmp_path):
    from paperagent.tasks.cloud_sync import CloudSync
    from paperagent.workspace.workspace import Workspace
    ws = Workspace('p',tmp_path).init('test')
    def forbidden(*a, **k): raise AssertionError('network called')
    sync = CloudSync(ws, transport=forbidden)
    assert sync.sync()['status'] == 'unbound'
    from paperagent.workspace.state_store import write_json
    write_json(ws.dir/'meta.json', {'project_id':'p','workspace_path':str(ws.dir.resolve()),
        'space_id':'s','device_id':'d','cloud_url':'https://example.invalid','sync_mode':'local_only'})
    assert sync.sync()['status'] == 'local_only'


def test_offline_events_replay_and_two_devices_converge(account, tmp_path):
    from paperagent.tasks.cloud_sync import CloudSync
    from paperagent.domain.lifecycle import LifecycleService
    from paperagent.workspace.workspace import Workspace
    client, headers, device, space = account
    second = client.post('/api/v1/devices',headers=headers,json={'name':'second'}).json()
    device_headers = [headers, {**headers,'X-Device-Token':second['device_token']}]
    devices = [device,second['id']]
    def transport_for(h):
        def transport(method, origin, path, body=None):
            response = client.request(method,path,headers=h,json=body)
            response.raise_for_status()
            return response.json()
        return transport
    workspaces = [Workspace('p',tmp_path/name).init('test') for name in ('one','two')]
    services = [LifecycleService(ws) for ws in workspaces]
    syncs = [CloudSync(ws,transport_for(h)) for ws,h in zip(workspaces,device_headers)]
    for sync, dev in zip(syncs,devices):
        challenge = sync.challenge('https://cloud.invalid',space,dev)['challenge']
        sync.bind('https://cloud.invalid',space,dev,challenge,confirmed=True)
        sync.set_mode('cloud_sync',confirmed=True)
    for lifecycle in services:
        lifecycle.save(lifecycle_type='thesis',stage='topic',reason='start')
        lifecycle.save(lifecycle_type='thesis',stage='drafting',reason='progress',status='paused',focus_stages=['topic'])
    assert len(services[0].pending_events()) == 2
    syncs[0].sync(); syncs[1].sync(); syncs[0].sync()
    events = client.get(f'/api/v1/spaces/{space}/lifecycle/events',headers=headers).json()['events']
    assert len(events) == 4 and [e['server_seq'] for e in events] == [1,2,3,4]
    assert any(e['conflict'] for e in events)
    for lifecycle in services:
        assert lifecycle.pending_events() == []
        assert lifecycle.get().status == 'paused' and lifecycle.get().focus_stages == ['topic']
    retry = {k:events[0][k] for k in ('device_id','project_id','idempotency_key','state','stage_from','reason','actor')}
    path = f'/api/v1/spaces/{space}/lifecycle/events'
    assert client.post(path,headers=headers,json=retry).status_code == 200
    assert client.post(path,headers=headers,json={**retry,'reason':'changed'}).status_code == 409
    assert client.patch(path,headers=headers,json=retry).status_code == 405


def test_lifecycle_snapshot_failure_recovers_from_event(tmp_path, monkeypatch):
    from paperagent.domain import lifecycle as module
    from paperagent.workspace.workspace import Workspace
    ws = Workspace('p',tmp_path).init('test')
    service = module.LifecycleService(ws)
    original = module.write_json
    def fail_snapshot(path, data):
        if path.name == 'lifecycle.json': raise OSError('crash')
        return original(path,data)
    monkeypatch.setattr(module,'write_json',fail_snapshot)
    with pytest.raises(OSError): service.save(lifecycle_type='thesis',stage='topic',reason='saved event')
    monkeypatch.setattr(module,'write_json',original)
    assert module.LifecycleService(ws).get().stage == 'topic'
    assert len(service.pending_events()) == 1


def test_binding_expiry_wrong_device_and_copied_directory(account,tmp_path):
    client,headers,device,space=account
    path=f'/api/v1/spaces/{space}'
    body={'device_id':device,'project_id':'p'}
    challenge=client.post(path+'/bind_challenge',headers=headers,json=body).json()['challenge']
    second=client.post('/api/v1/devices',headers=headers,json={'name':'second'}).json()
    other={**headers,'X-Device-Token':second['device_token']}
    assert client.post(path+'/bind_confirm',headers=other,json={**body,'challenge':challenge}).status_code==401
    with cloud.db() as c: c.execute('UPDATE bind_challenges SET expires=0')
    assert client.post(path+'/bind_confirm',headers=headers,json={**body,'challenge':challenge}).status_code==409
    from paperagent.tasks.cloud_sync import CloudSync
    from paperagent.workspace.workspace import Workspace
    from paperagent.workspace.state_store import write_json
    ws=Workspace('p',tmp_path).init('test')
    write_json(ws.dir/'meta.json',{'project_id':'other','workspace_path':'wrong','sync_mode':'cloud_sync'})
    with pytest.raises(ValueError): CloudSync(ws,lambda *a:pytest.fail('network')).sync()


def test_lost_ack_retry_and_scheduler_drain(account,tmp_path,monkeypatch):
    from paperagent.tasks.cloud_sync import CloudSync
    from paperagent.tasks.scheduler import Scheduler
    from paperagent.domain.store import DomainStore
    from paperagent.domain.lifecycle import LifecycleService
    from paperagent.workspace.workspace import Workspace
    client,headers,device,space=account
    lose=[False]
    def transport(method,origin,path,body=None):
        response=client.request(method,path,headers=headers,json=body)
        response.raise_for_status()
        if lose[0] and method=='POST' and path.endswith('/lifecycle/events'):
            lose[0]=False
            raise OSError('response lost after commit')
        return response.json()
    ws=Workspace('p',tmp_path).init('test')
    sync=CloudSync(ws,transport)
    challenge=sync.challenge('https://cloud.invalid',space,device)['challenge']
    sync.bind('https://cloud.invalid',space,device,challenge,confirmed=True)
    sync.set_mode('cloud_sync',confirmed=True)
    service=LifecycleService(ws)
    service.save(lifecycle_type='thesis',stage='topic',reason='offline')
    lose[0]=True
    with pytest.raises(OSError): sync.sync()
    assert len(service.pending_events())==1
    monkeypatch.setattr(CloudSync,'_request',lambda self,*a,**k:transport(*a,**k))
    assert any('lifecycle sync' in line for line in Scheduler(DomainStore(tmp_path)).tick())
    assert service.pending_events()==[]
    assert len(client.get(f'/api/v1/spaces/{space}/lifecycle/events',headers=headers).json()['events'])==1


def test_legacy_snapshot_migrates_lazily_once(tmp_path):
    from paperagent.domain.lifecycle import LifecycleState,LifecycleService
    from paperagent.workspace.workspace import Workspace
    from paperagent.workspace.state_store import write_json
    ws=Workspace('p',tmp_path).init('test')
    old=LifecycleState(lifecycle_type='thesis',stage='topic',status='paused',focus_stages=['proposal'],history=[{'reason':'original history'}])
    write_json(ws.dir/'lifecycle.json',old.model_dump())
    service=LifecycleService(ws)
    assert not service.events_path.exists()
    assert service.get().history[0]['reason']=='original history'
    assert service.get().status=='paused'
    assert len(service.events())==1


def test_prebinding_history_reaches_new_device(account,tmp_path):
    from paperagent.domain.lifecycle import LifecycleState,LifecycleService
    from paperagent.workspace.workspace import Workspace
    from paperagent.workspace.state_store import write_json
    from paperagent.tasks.cloud_sync import CloudSync
    client,headers,device,space=account
    def transport(method,origin,path,body=None):
        response=client.request(method,path,headers=headers,json=body)
        response.raise_for_status()
        return response.json()
    ws=Workspace('p',tmp_path/'one').init('test')
    original=LifecycleState(lifecycle_type='thesis',stage='topic',history=[{'reason':'prebinding history','after':{'stage':'topic'}}])
    write_json(ws.dir/'lifecycle.json',original.model_dump())
    sync=CloudSync(ws,transport)
    token=sync.challenge('https://cloud.invalid',space,device)['challenge']
    sync.bind('https://cloud.invalid',space,device,token,True)
    sync.set_mode('cloud_sync',True)
    sync.sync()
    rows=client.get(f'/api/v1/spaces/{space}/lifecycle/events',headers=headers).json()['events']
    fresh=LifecycleService(Workspace('p',tmp_path/'fresh').init('test'))
    fresh.accept_remote(rows)
    assert fresh.get().history[0]['reason']=='prebinding history'


def test_disable_stops_local_and_updates_server(account,tmp_path):
    from paperagent.workspace.workspace import Workspace
    from paperagent.tasks.cloud_sync import CloudSync
    client,headers,device,space=account
    def transport(method,origin,path,body=None):
        response=client.request(method,path,headers=headers,json=body)
        response.raise_for_status()
        return response.json()
    sync=CloudSync(Workspace('p',tmp_path).init('test'),transport)
    token=sync.challenge('https://cloud.invalid',space,device)['challenge']
    sync.bind('https://cloud.invalid',space,device,token,True)
    sync.set_mode('cloud_sync',True)
    sync.set_mode('local_only')
    assert client.get(f'/api/v1/spaces/{space}/sync',headers=headers).json()['sync_mode']=='local_only'
    sync.transport=lambda *a,**k:pytest.fail('disabled background made a network call')
    assert sync.sync()['status']=='local_only'


def test_cloud_event_defaults_do_not_break_idempotency(account,monkeypatch):
    from datetime import datetime, timezone
    values=iter([datetime(2026,1,1,tzinfo=timezone.utc),datetime(2026,1,2,tzinfo=timezone.utc)])
    monkeypatch.setattr('paperagent.domain.lifecycle.utcnow',lambda:next(values))
    client,headers,device,space=account
    bind(*account)
    client.post(f'/api/v1/spaces/{space}/sync',headers=headers,json={'sync_mode':'cloud_sync','confirmed':True})
    event={'device_id':device,'project_id':'p','idempotency_key':'event_defaults',
           'state':{'lifecycle_type':'thesis','stage':'topic'},'reason':'created'}
    path=f'/api/v1/spaces/{space}/lifecycle/events'
    first=client.post(path,headers=headers,json=event)
    assert first.status_code==200
    second=client.post(path,headers=headers,json=event)
    assert second.status_code==200 and second.json()==first.json()
