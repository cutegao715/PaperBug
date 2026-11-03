"""Cloud-owned decisions for the full research protocol; devices execute tools."""
import json
import time
import uuid
from typing import Literal
from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from paperagent.agent.action_permissions import action_writes


def app_module():
    from paperagent_cloud import app
    return app


def migrate(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS chat_runs(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,space_id TEXT NOT NULL,
      device_id TEXT NOT NULL,request_id TEXT NOT NULL,input_json TEXT NOT NULL,expires REAL NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'running',answer TEXT NOT NULL DEFAULT '',
      UNIQUE(user_id,space_id,request_id));
    CREATE TABLE IF NOT EXISTS chat_decisions(run_id TEXT NOT NULL REFERENCES chat_runs(id),seq INTEGER NOT NULL,
      observation TEXT NOT NULL,status TEXT NOT NULL,action TEXT,result TEXT,lease TEXT,lease_until REAL NOT NULL DEFAULT 0,
      attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,error TEXT,
      PRIMARY KEY(run_id,seq));
    """)


class Start(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    request_id:str=Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')
    goal:str=Field(min_length=1,max_length=12000)
    scope_digest:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirmed:bool=False
    allow_write:bool=False
    include_personal:bool=False
    allow_mcp:bool=False
    ttl:int=Field(default=3600,ge=60,le=86400)


class DecisionInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sequence:int=Field(ge=1,le=100)
    observation:dict

    @model_validator(mode='after')
    def bounded(self):
        from paperagent.harness.context_budget import estimate_tokens
        from paperagent.product_config import load
        if len(json.dumps(self.observation,ensure_ascii=False))>250000 or estimate_tokens(self.observation)>load().budget.context_input_tokens:
            raise ValueError('Context exceeds input budget')
        return self


class ResultInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    result:dict
    @model_validator(mode='after')
    def bounded(self):
        if len(json.dumps(self.result,ensure_ascii=False))>100000: raise ValueError('Result too large')
        return self


class FinishInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['finished','cancelled','error','budget_exhausted','too_many_errors','interrupted']
    summary:str=Field(default='',max_length=40000)


def owned(c,rid,uid,device_token):
    row=c.execute('SELECT r.* FROM chat_runs r JOIN spaces s ON s.id=r.space_id AND s.user_id=r.user_id WHERE r.id=? AND r.user_id=?',(rid,uid)).fetchone()
    if not row: raise HTTPException(404,'Conversation not found')
    app_module().verify_device(c,row,uid,device_token)
    return row


def active(c,row):
    device=c.execute("SELECT 1 FROM devices WHERE id=? AND user_id=? AND status='active'",(row['device_id'],row['user_id'])).fetchone()
    if not row['active'] or row['expires']<=time.time() or row['status']!='running' or not device:
        raise HTTPException(409,'Conversation authorization inactive')


def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def build(app):
    router=APIRouter(prefix='/control')
    @router.post('/spaces/{sid}/conversations',status_code=201)
    def start(sid:str,body:Start,uid:str=Depends(app.current_user),x_device_token:str=Header(default='')):
        if not body.confirmed: raise HTTPException(422,'Conversation authorization required')
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE');app.require_space(c,uid,sid)
            app.verify_device(c,{'device_id':body.device_id},uid,x_device_token)
            encoded=encode(body.model_dump())
            old=c.execute('SELECT * FROM chat_runs WHERE user_id=? AND space_id=? AND request_id=?',(uid,sid,body.request_id)).fetchone()
            if old:
                if old['input_json']!=encoded: raise HTTPException(409,'Request identity conflict')
                c.commit();return {'id':old['id'],'status':old['status']}
            rid=uuid.uuid4().hex
            c.execute('INSERT INTO chat_runs(id,user_id,space_id,device_id,request_id,input_json,expires) VALUES(?,?,?,?,?,?,?)',(rid,uid,sid,body.device_id,body.request_id,encoded,time.time()+body.ttl));c.commit()
            return {'id':rid,'status':'running'}

    @router.get('/conversations/{rid}')
    def status(rid:str,uid:str=Depends(app.current_user),x_device_token:str=Header(default='')):
        with app.db() as c:
            row=owned(c,rid,uid,x_device_token)
            return {'id':rid,'status':row['status'],'answer':row['answer'],'active':bool(row['active']),'expires':row['expires'],'execution_owner':'cloud'}

    @router.delete('/conversations/{rid}')
    def cancel(rid:str,uid:str=Depends(app.current_user),x_device_token:str=Header(default='')):
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE');owned(c,rid,uid,x_device_token)
            c.execute("UPDATE chat_runs SET active=0,status=CASE WHEN status='running' THEN 'cancelled' ELSE status END WHERE id=?",(rid,))
            c.execute("UPDATE chat_decisions SET status='cancelled',lease=NULL WHERE run_id=? AND status IN ('queued','running','ready')",(rid,));c.commit()
        return {'status':'cancelled'}

    @router.post('/conversations/{rid}/decisions',status_code=201)
    def enqueue(rid:str,body:DecisionInput,uid:str=Depends(app.current_user),x_device_token:str=Header(default='')):
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE');row=owned(c,rid,uid,x_device_token);active(c,row)
            old=c.execute('SELECT * FROM chat_decisions WHERE run_id=? AND seq=?',(rid,body.sequence)).fetchone()
            encoded=encode(body.observation)
            if old:
                if old['observation']!=encoded: raise HTTPException(409,'Decision sequence content conflict')
                c.commit();return {'sequence':body.sequence,'status':old['status']}
            prev=c.execute('SELECT seq,status FROM chat_decisions WHERE run_id=? ORDER BY seq DESC LIMIT 1',(rid,)).fetchone()
            if body.sequence!=(prev['seq']+1 if prev else 1) or (prev and prev['status']!='done'):
                raise HTTPException(409,'Previous local result required')
            c.execute("INSERT INTO chat_decisions(run_id,seq,observation,status) VALUES(?,?,?,'queued')",(rid,body.sequence,encoded));c.commit()
            return {'sequence':body.sequence,'status':'queued'}

    @router.get('/conversations/{rid}/decisions/{seq}')
    def decision(rid:str,seq:int,uid:str=Depends(app.current_user),x_device_token:str=Header(default='')):
        with app.db() as c:
            owned(c,rid,uid,x_device_token)
            row=c.execute('SELECT * FROM chat_decisions WHERE run_id=? AND seq=?',(rid,seq)).fetchone()
            if not row: raise HTTPException(404,'Decision not found')
            return {'sequence':seq,'status':row['status'],'action':json.loads(row['action']) if row['action'] else None,'error':row['error']}

    @router.post('/conversations/{rid}/decisions/{seq}/result')
    def result(rid:str,seq:int,body:ResultInput,uid:str=Depends(app.current_user),x_device_token:str=Header(default='')):
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE');row=owned(c,rid,uid,x_device_token);active(c,row)
            decision=c.execute('SELECT * FROM chat_decisions WHERE run_id=? AND seq=?',(rid,seq)).fetchone()
            if not decision or decision['status'] not in ('ready','done'): raise HTTPException(409,'Decision is not executable')
            encoded=encode(body.result)
            if decision['result'] is not None and decision['result']!=encoded: raise HTTPException(409,'Result conflict')
            c.execute("UPDATE chat_decisions SET result=?,status='done' WHERE run_id=? AND seq=?",(encoded,rid,seq));c.commit()
        return {'status':'done'}

    @router.post('/conversations/{rid}/finish')
    def finish(rid:str,body:FinishInput,uid:str=Depends(app.current_user),x_device_token:str=Header(default='')):
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE');row=owned(c,rid,uid,x_device_token)
            if row['status']!='running':
                if row['status']!=body.status or row['answer']!=body.summary: raise HTTPException(409,'Terminal result conflict')
                c.commit();return {'status':row['status']}
            if body.status=='finished':
                active(c,row)
                last=c.execute('SELECT * FROM chat_decisions WHERE run_id=? ORDER BY seq DESC LIMIT 1',(rid,)).fetchone()
                if not last or last['status']!='done' or not json.loads(last['result']).get('done'): raise HTTPException(409,'Local completion verification required')
            c.execute('UPDATE chat_runs SET status=?,answer=?,active=0 WHERE id=?',(body.status,body.summary,rid))
            c.execute("UPDATE chat_decisions SET status='cancelled',lease=NULL WHERE run_id=? AND status IN ('queued','running','ready')",(rid,));c.commit()
        return {'status':body.status}
    return router


def decide(observation):
    from paperagent.agent.llm_agent import LLMAgent
    from paperagent.harness.llm import LLMClient
    return LLMAgent(LLMClient()).decide(observation).model_dump(exclude_none=True)


def tick():
    app=app_module();now=time.time();lease=uuid.uuid4().hex
    with app.db() as c:
        c.execute('BEGIN IMMEDIATE')
        job=c.execute("SELECT * FROM chat_decisions WHERE (status='queued' AND next_attempt<=?) OR (status='running' AND lease_until<=?) ORDER BY next_attempt,run_id,seq LIMIT 1",(now,now)).fetchone()
        if not job: c.commit();return 0
        run=c.execute('SELECT * FROM chat_runs WHERE id=?',(job['run_id'],)).fetchone()
        try: active(c,run)
        except HTTPException:
            c.execute("UPDATE chat_decisions SET status='cancelled',lease=NULL WHERE run_id=? AND seq=?",(job['run_id'],job['seq']));c.commit();return 1
        c.execute("UPDATE chat_decisions SET status='running',lease=?,lease_until=? WHERE run_id=? AND seq=?",(lease,now+600,job['run_id'],job['seq']));c.commit()
    try:
        from paperagent_cloud.usage import metered
        with metered(run['user_id'],'chat:'+run['id']): action=decide(json.loads(job['observation']))
        from paperagent.agent.llm_agent import _action_adapter
        action=_action_adapter.validate_python(action).model_dump(exclude_none=True)
        options=json.loads(run['input_json'])
        if action.get('action')=='tool_call' and action.get('tool_id','').startswith('mcp_') and not options.get('allow_mcp'):
            action={'action':'ask_user','question':'此操作需要外部 MCP 工具访问。请在下一条对话授权中单独启用 MCP；其外部资源范围由已有工具配置决定。'}
        if not options['allow_write'] and action_writes(action):
            action={'action':'ask_user','question':'当前仅授权读取与验证。需要修改资料时，请在下一条对话授权中启用项目写入。'}
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE')
            fresh=c.execute('SELECT * FROM chat_runs WHERE id=?',(run['id'],)).fetchone()
            try: active(c,fresh)
            except HTTPException: c.rollback();return 1
            c.execute("UPDATE chat_decisions SET status='ready',action=?,lease=NULL,error=NULL WHERE run_id=? AND seq=? AND status='running' AND lease=? AND lease_until>?",(encode(action),run['id'],job['seq'],lease,time.time()));c.commit()
    except Exception as exc:
        with app.db() as c:
            attempts=job['attempts']+1
            c.execute("UPDATE chat_decisions SET status=?,attempts=?,error=?,next_attempt=?,lease=NULL WHERE run_id=? AND seq=? AND status='running' AND lease=? AND lease_until>?",('failed' if attempts>=3 else 'queued',attempts,type(exc).__name__,time.time()+min(120,10*2**attempts),run['id'],job['seq'],lease,time.time()))
    return 1
