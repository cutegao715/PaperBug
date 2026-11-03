"""Inline consent and a cloud decision provider for the existing local execution harness."""
import copy
import hashlib
import json
import time
import uuid
import threading
from pathlib import Path
from paperagent.quality.policies import author_write,PermissionGate
from paperagent.workspace.state_store import read_json,write_json,state_lock
from paperagent.workspace.workspace import Workspace
from paperagent.tasks.cloud_tasks import CloudTasks
from paperagent.agent.action_permissions import action_writes


class CloudConversation:
    def __init__(self,ws,transport=None):
        self.ws=ws;self.transport=CloudTasks(ws,transport);self.path=ws.dir/'cloud_conversation.json'

    def cards(self): return read_json(self.path).get('authorizations',{})

    def row(self,aid):
        row=self.cards().get(aid)
        if not row: raise ValueError('Unknown conversation authorization')
        return row

    def check(self,aid):
        row=self.row(aid)
        if row['status'] not in ('approved','running') or row['expires']<=time.time(): raise PermissionError('Conversation authorization inactive')
        binding=self.transport._binding()
        if self._identity(binding)!=row['binding']: raise PermissionError('Conversation account or project changed')
        return row

    @staticmethod
    def _identity(binding): return {k:binding[k] for k in ('user_id','space_id','device_id','cloud_url')}

    @author_write
    def prepare(self,request):
        aid=uuid.uuid4().hex
        try: binding=self._identity(self.transport._binding())
        except ValueError: binding=None
        roots=[str(Path(p).resolve()) for p in self.ws.load_permissions().get('allowed_roots',[])]
        row={'id':aid,'request':copy.deepcopy(request),'binding':binding,'status':'pending' if binding else 'setup_required',
             'local_run_id':uuid.uuid4().hex[:12],'project_root':str(self.ws.dir.resolve()),'available_roots':roots,'expires':time.time()+3600}
        with state_lock(self.path):
            state=read_json(self.path);state.setdefault('authorizations',{})[aid]=row;write_json(self.path,state)
            self.ws.append_message('user',request['text'],authorization_id=aid,execution_owner='cloud')
            self.ws.append_message('assistant','本轮将由云端 Agent 决策，本机执行工具和验证。请确认下方授权范围后继续。' if binding else '当前项目尚未连接云端。请完成云端绑定后重新发送；不会自动切换到本地模型。',authorization_id=aid,execution_owner='cloud')
        return {'status':'authorization_required' if binding else 'cloud_setup_required','authorization_id':aid,'execution_owner':'cloud'}

    @author_write
    def reject(self,aid):
        with state_lock(self.path):
            state=read_json(self.path);row=state.get('authorizations',{}).get(aid)
            if not row or row['status'] not in ('pending','setup_required'): raise ValueError('Authorization is no longer pending')
            row['status']='rejected';write_json(self.path,state)
            self.ws.append_message('assistant','已取消本轮请求，未上传上下文。',authorization_id=aid,execution_owner='cloud')
        return {'status':'rejected'}

    @author_write
    def approve(self,aid,allow_write=False,include_personal=False,include_external=False,allow_mcp=False):
        choices={'allow_write':bool(allow_write),'include_personal':bool(include_personal),'include_external':bool(include_external),'allow_mcp':bool(allow_mcp)}
        with state_lock(self.path):
            state=read_json(self.path);row=state.get('authorizations',{}).get(aid)
            if not row or row['expires']<=time.time(): raise ValueError('Authorization expired')
            if row['status'] in ('approved','running','finished'):
                if row['choices']!=choices: raise ValueError('Approval choices conflict')
                return row
            if row['status']!='pending': raise ValueError('Authorization is not pending')
            if self._identity(self.transport._binding())!=row['binding']: raise ValueError('Cloud account changed; request new authorization')
            roots=[row['project_root']]+(row['available_roots'] if include_external else [])
            digest=hashlib.sha256(json.dumps({'roots':roots,'choices':choices},sort_keys=True).encode()).hexdigest()
            result=self.transport._call('POST',f"/api/v1/control/spaces/{row['binding']['space_id']}/conversations",{
                'device_id':row['binding']['device_id'],'request_id':row['local_run_id'],'goal':row['request']['text'],
                'scope_digest':digest,'confirmed':True,'allow_write':allow_write,'include_personal':include_personal,'allow_mcp':allow_mcp,'ttl':3600})
            row.update(status='approved',choices=choices,roots=roots,cloud_run_id=result['id']);write_json(self.path,state)
            return row

    def update(self,aid,**values):
        with state_lock(self.path):
            state=read_json(self.path);state['authorizations'][aid].update(values);write_json(self.path,state)

    def flush_pending(self,only=None):
        for aid,row in self.cards().items():
            if only and aid!=only: continue
            if not row.get('pending_finish'): continue
            self.check(aid)
            path='/api/v1/control/conversations/'+row['cloud_run_id']
            if row.get('last_result') is not None and not row.get('last_result_synced'):
                self.transport._call('POST',path+f"/decisions/{row['last_sequence']}/result",{'result':row['last_result']})
                self.update(aid,last_result_synced=True)
            self.transport._call('POST',path+'/finish',row['pending_finish'])
            self.update(aid,status='finished',outcome=row['pending_finish']['status'],pending_finish=None)

    def remote_status(self,aid):
        return self.transport._call('GET','/api/v1/control/conversations/'+self.row(aid)['cloud_run_id'])

    def revoke(self,aid):
        row=self.row(aid);self.update(aid,status='cancelled')
        if row.get('cloud_run_id'): return self.transport._call('DELETE','/api/v1/control/conversations/'+row['cloud_run_id'])
        return {'status':'cancelled'}


class ScopedWorkspace(Workspace):
    def __init__(self,ws,authorization,owner,aid):
        self.__dict__.update(ws.__dict__);self.authorization=authorization;self.consent_owner=owner;self.consent_id=aid

    def list_messages(self,limit=None):
        cards=self.consent_owner.cards()
        values=[]
        for msg in super().list_messages():
            aid=msg.get("authorization_id")
            row=cards.get(aid,{})
            if aid and aid!=self.consent_id and not (row.get("started") and row.get("status") in ("running","finished")):
                continue
            values.append(msg)
        return values[-limit:] if limit else values

    def list_allowed_roots(self): return self.authorization()['roots']

    def is_path_allowed(self,path,*,write=False):
        try:
            row=self.authorization();target=Path(path).resolve()
            roots=[self.dir.resolve()] if write else [Path(p).resolve() for p in row['roots']]
            return (not write or row['choices']['allow_write']) and any(target.is_relative_to(root) for root in roots)
        except (ValueError,PermissionError): return False

    def resolve_path(self,path):
        p=Path(path)
        if not p.is_absolute():
            project=self.dir/p
            p=project if project.exists() or str(path).startswith(('drafts/','files/','figures/')) else self.root/p
        p=p.resolve()
        if not self.is_path_allowed(p): raise PermissionError('File outside authorized conversation scope; import or authorize it first')
        return p

    def authorize_root(self,path):
        if not self.is_path_allowed(path): raise PermissionError('Cloud action cannot expand filesystem authorization')
        return super().authorize_root(path)


class ConversationGate(PermissionGate):
    def __init__(self,agent): super().__init__();self.agent=agent
    def check_loop_action(self,name,role=None):
        try: self.agent.owner.check(self.agent.aid)
        except (PermissionError,ValueError): return 'deny'
        return super().check_loop_action(name,role)


class CloudDecisionAgent:
    def __init__(self,owner,aid,cancel_event=None,poll_interval=.5):
        self.owner,self.aid=owner,aid;self.cancel_event=cancel_event or threading.Event()
        self.sequence=0;self.poll_interval=poll_interval;self.pending_result=None;self.pending_decision=None
        self.path='/api/v1/control/conversations/'+owner.check(aid)['cloud_run_id']

    def workspace(self): return ScopedWorkspace(self.owner.ws,lambda:self.owner.check(self.aid),self.owner,self.aid)

    def check_action(self,action):
        row=self.owner.check(self.aid)
        remote=self.owner.remote_status(self.aid)
        if not remote['active'] or remote['status']!='running' or remote['expires']<=time.time(): raise PermissionError('Cloud authorization inactive')
        if self.cancel_event.is_set(): raise PermissionError('Conversation cancelled')
        if not row['choices']['allow_write'] and action_writes(action): raise PermissionError('Project writing is not authorized')
        ws=self.workspace()
        if action.get('action') in ('read_pdf','import_file','import_folder','save_evidence'):
            for key in ('path','folder','source_path'):
                if action.get(key): ws.resolve_path(action[key])
        if action.get('action')=='tool_call':
            if action['tool_id'].startswith('mcp_') and not row['choices'].get('allow_mcp'):
                raise PermissionError('MCP external tools require separate conversation consent')
            payload=action.get('input') or {}
            for key in ('conversion_dir','output_path','output_dir'):
                if payload.get(key):
                    target=Path(payload[key])
                    if not target.is_absolute(): target=ws.root/target
                    if not ws.is_path_allowed(target,write=True): raise PermissionError('Tool output outside approved project')
            from paperagent.harness.tool_executor import ToolExecutor
            from paperagent.schemas.policy import ResearchPolicy
            from paperagent.schemas.artifacts import ToolCall
            from paperagent.tools.registry import REGISTRY
            desc=REGISTRY.get(action['tool_id'])
            if desc:
                error=ToolExecutor(ResearchPolicy(),ws)._check_filesystem_permission(ToolCall(tool_id=action['tool_id'],input=action.get('input',{})),desc.permission)
                if error: raise PermissionError(error)

    def model_context(self,obs):
        row=self.owner.check(self.aid)
        value=copy.deepcopy(obs.get('assistant_state') or obs)
        value['iteration']=obs.get('iteration',self.sequence+1)
        ctx=value.setdefault('current_context',{})
        ctx['filesystem']={'allowed_roots':row['roots'],'policy':'Only these approved paths may be read. Writes require permission and remain in this project.'}
        ctx['cloud_authorization']={'allow_write':row['choices']['allow_write'],'allow_mcp':row['choices'].get('allow_mcp',False),'execution_owner':'cloud','local_execution':True}
        if not row['choices']['include_personal']:
            ctx['profile']={};ctx['memories']={k:v for k,v in ctx.get('memories',{}).items() if k not in ('user','prospective')}
            ctx['connector_state']=[];value['research_state']={};value['pending_actions']=[];value['deadline']=None
        # Local authorization cards are UI controls, not model instructions.
        ctx['recent_messages']=[m for m in ctx.get('recent_messages',[]) if not str(m.get('content','')).startswith(('本轮将由云端 Agent','已取消本轮请求'))]
        return value

    def _send_result(self):
        if self.pending_result is not None:
            self.owner.check(self.aid)
            self.owner.transport._call('POST',self.path+f'/decisions/{self.sequence}/result',{'result':self.pending_result})
            self.pending_result=None
            self.owner.update(self.aid,last_result_synced=True)

    def decide(self,obs):
        self.owner.check(self.aid)
        self._send_result()
        if self.pending_decision is None:
            self.pending_decision={'sequence':self.sequence+1,'observation':self.model_context(obs)}
        self.owner.transport._call('POST',self.path+'/decisions',self.pending_decision)
        self.sequence=self.pending_decision['sequence']
        deadline=time.monotonic()+900
        while time.monotonic()<deadline:
            self.owner.check(self.aid)
            if self.cancel_event.is_set():
                self.owner.revoke(self.aid);raise PermissionError('Conversation cancelled')
            reply=self.owner.transport._call('GET',self.path+f'/decisions/{self.sequence}')
            if reply['status']=='ready':
                action=reply['action']
                from paperagent.agent.llm_agent import _action_adapter
                return _action_adapter.validate_python(action)
            if reply['status'] in ('failed','cancelled'): raise RuntimeError('Cloud decision '+reply['status'])
            self.cancel_event.wait(self.poll_interval)
        raise TimeoutError('Cloud decision pending; retry after checking the worker')

    def record_step(self,step):
        if 'action' not in step or not self.sequence:return
        self.pending_decision=None
        result=step.get('result',{})
        raw=json.dumps(result,ensure_ascii=False,default=str)
        self.pending_result=result if len(raw)<=90000 else {'status':result.get('status'),'done':result.get('done',False),'excerpt':raw[:80000],'truncated':True}
        # Journal locally before any network ACK; never replay a filesystem action on lost ACK.
        self.owner.update(self.aid,last_sequence=self.sequence,last_result=self.pending_result,last_result_synced=False)
        try: self._send_result()
        except (ValueError,RuntimeError,OSError): pass

    def finish(self,outcome):
        if self.cancel_event.is_set():
            self.owner.revoke(self.aid);return
        self.owner.update(self.aid,pending_finish={'status':outcome.status,'summary':outcome.summary[:40000]})
        self._send_result()
        self.owner.flush_pending(self.aid)
        return {'status':outcome.status}


def tick(root):
    logs=[]
    for path in (Path(root)/'projects').glob('*/cloud_conversation.json'):
        cc=CloudConversation(Workspace(path.parent.name,Path(root)))
        try: cc.flush_pending()
        except (ValueError,RuntimeError,PermissionError,OSError): logs.append('conversation completion pending: '+path.parent.name)
    return logs
