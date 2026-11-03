"""Durable, bounded configuration sync and notification relay with backoff."""
import json
import time
import uuid
from urllib.parse import urlencode
from paperagent.workspace.state_store import read_json,write_json,state_lock
from paperagent.domain.entities import StandingIntent
from paperagent.domain.intent_sync import IntentEnvelope,PAYLOAD_FIELDS,rank
from paperagent.tasks.standing_intent import stamp_intent,persist_intents


def envelope(item, project):
    payload=item.model_dump(include=PAYLOAD_FIELDS)
    payload['scope']={'project':True} if item.scope.get('project_id')==project else {'profile':True}
    return IntentEnvelope(id=item.sync_id,updated=item.sync_updated,version=item.sync_version,
        origin=item.sync_origin,deleted=item.deleted,payload=payload).model_dump()


def eligible(item, project, profile):
    return item.scope.get('project_id')==project or (profile and item.scope.get('profile') is True and not item.scope.get('project_id'))


def collect(store,project,profile):
    with state_lock(store._path('standing_intents')):
        rows=store.list_standing_intents(include_deleted=True)
        changed=False
        selected=[]
        for row in rows:
            if not eligible(row,project,profile): continue
            if not row.sync_id:
                stamp_intent(store,row);changed=True
            selected.append(envelope(row,project))
        if changed: persist_intents(store,rows)
        return selected


def merge(store,project,incoming,profile=False):
    with state_lock(store._path('standing_intents')):
        rows=store.list_standing_intents(include_deleted=True)
        for raw in incoming:
            remote=IntentEnvelope.model_validate(raw).model_dump()
            if remote['payload']['scope'].get('profile') and not profile: continue
            old=next((r for r in rows if r.sync_id==remote['id']),None)
            if old and not eligible(old,project,profile):
                continue  # never overwrite a different local project's configuration
            if old and rank(envelope(old,project))>=rank(remote): continue
            payload=dict(remote['payload'])
            payload['scope']={'project_id':project} if payload['scope'].get('project') else {'profile':True}
            new=StandingIntent(intent_id=old.intent_id if old else 'si_'+uuid.uuid4().hex,
                **payload,sync_id=remote['id'],sync_updated=remote['updated'],sync_version=remote['version'],
                sync_origin=remote['origin'],deleted=remote['deleted'])
            if old: rows[rows.index(old)]=new
            else: rows.append(new)
        persist_intents(store,rows)


def _path(ws): return ws.dir/'proactive_sync.json'


def _state(ws):
    return read_json(_path(ws),{'notifications':{},'ack':{},'attempts':0,'next_retry':0})


def _source_entries(store, note=None, project_id=None, target_device=None):
    # This lock is never held during cloud requests. notify() does not need any binding lock.
    path=store.dir/'notification_outbox.json'
    with state_lock(path):
        data=read_json(path,{})
        rows=[note] if note is not None else store.list_notifications(limit=1000)
        changed=False
        for item in rows:
            if item.notification_id not in data:
                data[item.notification_id]={'id':item.notification_id,'key':uuid.uuid4().hex,
                    'title':item.title,'body':item.body,'type':item.type,
                    'project_id':getattr(item,'relay_project_id',None) or project_id,
                    'target_device':getattr(item,'relay_target_device',None) or target_device}
                changed=True
        if changed: write_json(path,data)
        return list(data.values())


def _queue(state, entry, space):
    if entry['id'] not in state['notifications']:
        state['notifications'][entry['id']]={**entry,'key':entry['key']+':'+space,'status':'pending'}


def enqueue_notification(store,note,project_id=None,target_device=None):
    """Pure local outbox. Routing requires explicit per-project authorization at delivery."""
    _source_entries(store,note,project_id,target_device)


def sync_project(ws,now=None):
    from paperagent.tasks.cloud_sync import CloudSync
    from paperagent.domain.store import DomainStore
    now=time.time() if now is None else now
    sync=CloudSync(ws)
    with state_lock(sync.path),state_lock(_path(ws)):
        binding=sync.metadata()
        if not binding or binding['sync_mode']!='cloud_sync': return {'status':'local_only'}
        pref=binding.get('sync_preferences',{})
        if not (pref.get('intents') or pref.get('notifications')): return {'status':'disabled'}
        state=_state(ws)
        if now<state.get('next_retry',0): return {'status':'backoff'}
        store=DomainStore(ws.root)
        common={'device_id':binding['device_id'],'project_id':ws.project_id}
        path=f"/api/v1/spaces/{binding['space_id']}"
        def request(method,url,body=None):
            return sync.transport(method,binding['cloud_url'],url,body)
        try:
            if request('GET',path+'/sync')['sync_mode']!='cloud_sync':
                binding['sync_mode']='local_only';write_json(sync.path,binding)
                return {'status':'local_only'}
            if pref.get('intents'):
                rows=collect(store,ws.project_id,pref.get('profile_intents',False))
                pending=[r for r in rows if state['ack'].get(r['id'])!=r]
                if pending:
                    reply=request('POST',path+'/intents',{**common,'items':pending[:100]})['items']
                    merge(store,ws.project_id,reply,pref.get('profile_intents',False))
                    for row in reply: state['ack'][row['id']]=row
                cursor=state.get('pull_cursor','')
                reply=request('GET',path+'/intents?'+urlencode({**common,'after':cursor}))
                merge(store,ws.project_id,reply['items'],pref.get('profile_intents',False))
                for row in reply['items']: state['ack'][row['id']]=row
                state['pull_cursor']=reply.get('next') or ''  # bounded full cycles observe updates to old IDs
            if pref.get('notifications'):
                # Recover enqueue-after-local-save crashes. Explicit setting covers existing notifications too.
                for entry in _source_entries(store):
                    if entry.get('project_id') in (None,ws.project_id):
                        _queue(state,entry,binding['space_id'])
                write_json(_path(ws),state)  # keys survive a crash/lost response
                count=0
                for note in state['notifications'].values():
                    if note['status']=='delivered': continue
                    request('POST','/api/v1/notifications/relay',{**common,'space_id':binding['space_id'],
                        'idempotency_key':note['key'],'target_device':note.get('target_device'),
                        'title':note['title'][:2000],'body':note['body'][:20000],'type':note['type']})
                    note['status']='delivered';count+=1
                    write_json(_path(ws),state)
                    if count>=20: break
            state['attempts']=0;state['next_retry']=0
            write_json(_path(ws),state)
            return {'status':'synced'}
        except Exception:
            state['attempts']=min(state.get('attempts',0)+1,10)
            state['next_retry']=now+min(3600,60*2**(state['attempts']-1))
            write_json(_path(ws),state)
            return {'status':'deferred'}


def tick(root):
    from pathlib import Path
    from paperagent.workspace.workspace import Workspace
    log=[]
    for path in (Path(root)/'projects').glob('*/meta.json'):
        try:
            result=sync_project(Workspace(path.parent.name,root))
            if result['status'] not in {'disabled','local_only','backoff'}:
                log.append(f'proactive sync {path.parent.name}: {result["status"]}')
        except Exception:
            log.append(f'proactive sync {path.parent.name}: invalid local state')
    return log
