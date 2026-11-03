"""Explicit cloud task authorization and a durable local result outbox."""
import json
import time
import uuid
from pathlib import Path
from paperagent.quality.policies import author_write
from paperagent.tasks.cloud_sync import CloudSync
from paperagent.workspace.operation_executor import LocalExecutor
from paperagent.workspace.state_store import state_lock,read_json,write_json


class CloudTasks:
    def __init__(self,ws,transport=None):
        self.ws=ws;self.sync=CloudSync(ws,transport);self.path=ws.dir/'cloud_tasks.json'
        self.executor=LocalExecutor(ws)

    def _binding(self):
        data=self.sync.metadata()
        if not data or not data.get('user_id'): raise ValueError('Bind this project to a verified cloud account first')
        account=read_json(self.ws.root/'account.json')
        if account and (account['user_id'],account['origin'])!=(data['user_id'],data['cloud_url']):
            raise ValueError('Project binding belongs to another account')
        return data

    def _call(self,method,path,body=None,headers=None):
        binding=self._binding()
        # Check identity before any private data leaves the local workspace.
        me=self.sync.transport('GET',binding['cloud_url'],'/api/v1/auth/me',None)
        if me.get('user_id')!=binding['user_id']: raise ValueError('Cloud credentials belong to a different account')
        if headers:
            # Injected transports may expose a headers keyword for deterministic tests.
            if self.sync.transport!=self.sync._request:
                return self.sync.transport(method,binding['cloud_url'],path,body,headers=headers)
            return self.sync._request(method,binding['cloud_url'],path,body,extra_headers=headers)
        return self.sync.transport(method,binding['cloud_url'],path,body)

    @author_write
    def start(self,goal,relative,actions=None,confirmed=False,include_memories=False,ttl=86400,interval_seconds=None,request_id=None):
        if not confirmed: raise ValueError('Explicit cloud task consent required')
        binding=self._binding()
        actions=actions or ['read_pdf','verify_citations','search_local']
        if interval_seconds is not None and ('draft_propose' in actions or not 300<=interval_seconds<=2592000):
            raise ValueError('Scheduled tasks require read-only actions and a bounded interval')
        material=self.executor.authorize(relative,actions,confirmed,ttl)
        grant=self._call('POST',f"/api/v1/control/spaces/{binding['space_id']}/grants",{
            'device_id':binding['device_id'],'file_ref':material['file_ref'],'sha256':material['sha256'],
            'actions':actions,'ttl':ttl,'confirmed':True,'include_memories':include_memories})
        with state_lock(self.path):
            state=read_json(self.path);state['enabled']=True
            state.setdefault('grants',{})[grant['id']]={'file_ref':material['file_ref'],'expires':grant['expires']}
            write_json(self.path,state)
        if interval_seconds is not None:
            result=self._call('POST',f"/api/v1/control/spaces/{binding['space_id']}/schedules",{
                'grant_id':grant['id'],'goal':goal,'interval_seconds':interval_seconds,'confirmed':True})
            with state_lock(self.path):
                state=read_json(self.path);state.setdefault('schedules',{})[result['id']]=grant['id'];write_json(self.path,state)
        else:
            result=self._call('POST',f"/api/v1/control/spaces/{binding['space_id']}/tasks",{
                'grant_id':grant['id'],'goal':goal,'request_id':request_id or uuid.uuid4().hex})
            with state_lock(self.path):
                state=read_json(self.path);state.setdefault('tasks',{})[result['id']]={'grant_id':grant['id'],'status':'queued'};write_json(self.path,state)
        return result|{'grant_id':grant['id'],'file_ref':material['file_ref']}

    @author_write
    def monitor(self,topic,confirmed=False,interval_seconds=86400,sources=None):
        if not confirmed: raise ValueError('Explicit public query consent required')
        binding=self._binding()
        result=self._call('POST',f"/api/v1/control/spaces/{binding['space_id']}/monitors",{
            'topic':topic,'confirmed':True,'interval_seconds':interval_seconds,'sources':sources or ['arxiv_rss']})
        with state_lock(self.path):
            state=read_json(self.path);state['enabled']=True;state.setdefault('monitors',{})[result['id']]=topic;write_json(self.path,state)
        return result

    @author_write
    def stop_monitor(self,monitor_id):
        return self._call('DELETE','/api/v1/control/monitors/'+monitor_id)

    def status(self,task_id): return self._call('GET','/api/v1/control/tasks/'+task_id)

    @author_write
    def cancel(self,task_id): return self._call('POST','/api/v1/control/tasks/'+task_id+'/cancel',{})

    @author_write
    def set_enabled(self,enabled):
        with state_lock(self.path):
            state=read_json(self.path);state['enabled']=bool(enabled);write_json(self.path,state)
        return {'enabled':bool(enabled)}

    @author_write
    def revoke(self,grant_id):
        with state_lock(self.path):
            state=read_json(self.path);g=state.get('grants',{}).pop(grant_id,None);write_json(self.path,state)
        if g: self.executor.revoke(g['file_ref'])
        # Local revocation is effective even if this request fails.
        return self._call('DELETE','/api/v1/control/grants/'+grant_id)

    @author_write
    def share_memory(self,memory_id,confirmed=False):
        if not confirmed: raise ValueError('Explicit memory upload confirmation required')
        from paperagent.workspace.repositories import FileMemoryRepository
        binding=self._binding()
        rows=FileMemoryRepository(self.ws.root).list_memories(limit=None)
        row=next((m for m in rows if m.memory_id==memory_id and m.status=='active' and
                  (m.layer=='user' and not m.scope.get('project_id') or m.scope.get('project_id')==self.ws.project_id)),None)
        if row is None: raise ValueError('Memory is outside this project')
        return self._call('POST',f"/api/v1/control/spaces/{binding['space_id']}/memories",{
            'content':row.content,'layer':'user' if row.layer=='user' else 'project','confirmed':True})

    def poll(self):
        # Separate poll lock from UI enable/revoke locks; no network holds the settings lock.
        with state_lock(self.path.with_suffix('.poll.lock')):
            state=read_json(self.path)
            if not state.get('enabled'): return {'status':'disabled','completed':0}
            if state.get('next_retry',0)>time.time(): return {'status':'backoff','completed':0}
            binding=self._binding();completed=0
            try:
                rows=self._call('GET',f"/api/v1/control/devices/{binding['device_id']}/operations?space_id={binding['space_id']}")['operations']
                for op in rows:
                    current=read_json(self.path)
                    if not current.get('enabled'): break
                    grant=current.get('grants',{}).get(op['grant_id'])
                    if not grant or grant['file_ref']!=op['file_ref'] or grant['expires']<=time.time(): continue
                    lease=self._call('POST','/api/v1/agent-operations/'+op['id']+'/claim',{})
                    try: result=self.executor.execute(op)
                    except (ValueError,OSError):
                        result={'file_ref':op['file_ref'],'sha256':op['sha256'],'error':'material_unavailable'}
                    with state_lock(self.path):
                        current=read_json(self.path)
                        current.setdefault('outbox',{})[op['id']]={'result':result,'lease':lease['lease_token'],
                            'until':lease['lease_until'],'grant_id':op['grant_id']}
                        current.setdefault('tasks',{}).setdefault(op['task_id'],{'grant_id':op['grant_id'],'status':'waiting_local'})
                        write_json(self.path,current)
                for oid,row in list(read_json(self.path).get('outbox',{}).items())[:20]:
                    current=read_json(self.path)
                    if not current.get('enabled'): break
                    if row['grant_id'] not in current.get('grants',{}): continue
                    if row['until']<=time.time(): continue # the server will offer a fresh lease
                    self._call('POST','/api/v1/agent-operations/'+oid+'/result',row['result'],
                               {'X-Lease-Token':row['lease']})
                    with state_lock(self.path):
                        current=read_json(self.path);current.get('outbox',{}).pop(oid,None);write_json(self.path,current)
                    completed+=1
                from paperagent.tasks.notification_service import NotificationService
                from paperagent.domain.store import DomainStore
                if read_json(self.path).get('monitors'):
                    updates=self._call('GET',f"/api/v1/control/spaces/{binding['space_id']}/monitor-updates?after={read_json(self.path).get('monitor_cursor',0)}")
                    for item in updates['items']:
                        NotificationService(DomainStore(self.ws.root)).notify(type='system',title=item['title'],body=item['body'],dedup_key=item['id'],project_id=self.ws.project_id)
                    with state_lock(self.path):
                        current=read_json(self.path);current['monitor_cursor']=updates['next'];write_json(self.path,current)
                for tid,local in list(read_json(self.path).get('tasks',{}).items())[-20:]:
                    if local.get('status') in {'succeeded','failed','cancelled'}: continue
                    result=self.status(tid)
                    if result['status'] in {'succeeded','failed','cancelled'}:
                        NotificationService(DomainStore(self.ws.root)).notify(type='system',title='云端研究任务：'+result['status'],
                            body=(result.get('answer') or '')[:10000],dedup_key='cloud-task:'+tid,project_id=self.ws.project_id)
                    with state_lock(self.path):
                        current=read_json(self.path);current['tasks'][tid]['status']=result['status'];write_json(self.path,current)
                with state_lock(self.path):
                    current=read_json(self.path);current.update(attempts=0,next_retry=0);write_json(self.path,current)
                return {'status':'ok','completed':completed}
            except (ValueError,RuntimeError,OSError):
                with state_lock(self.path):
                    current=read_json(self.path);attempts=min(8,current.get('attempts',0)+1)
                    current.update(attempts=attempts,next_retry=time.time()+min(3600,30*2**attempts));write_json(self.path,current)
                return {'status':'deferred','completed':completed}


def tick(root):
    from paperagent.workspace.workspace import Workspace
    result=[]
    for path in (Path(root)/'projects').glob('*/cloud_tasks.json'):
        try: result.append('cloud task '+path.parent.name+': '+CloudTasks(Workspace(path.parent.name,root)).poll()['status'])
        except (ValueError,RuntimeError,OSError): result.append('cloud task '+path.parent.name+': invalid state')
    return result
