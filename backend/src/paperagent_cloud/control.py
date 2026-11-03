"""Authenticated control plane; no model invocation in queue endpoints."""
import json
import secrets
import time
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, ConfigDict, Field
from paperagent.schemas.references import Reference
from paperagent.domain.entities import MemoryRecord


def app_module():
    from paperagent_cloud import app
    return app


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS control_grants(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,space_id TEXT NOT NULL,
      device_id TEXT NOT NULL,file_ref TEXT NOT NULL,sha256 TEXT NOT NULL,actions TEXT NOT NULL,
      expires REAL NOT NULL,active INTEGER NOT NULL,include_memories INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS control_jobs(task_id TEXT PRIMARY KEY REFERENCES agent_tasks(id),user_id TEXT NOT NULL,
      grant_id TEXT NOT NULL REFERENCES control_grants(id),input_json TEXT NOT NULL,status TEXT NOT NULL,
      lease_token TEXT,lease_until REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
      next_attempt REAL NOT NULL DEFAULT 0,last_error TEXT);
    CREATE TABLE IF NOT EXISTS control_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,
      kind TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS control_memories(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,space_id TEXT,
      value TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS control_schedules(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,space_id TEXT NOT NULL,
      grant_id TEXT NOT NULL,goal TEXT NOT NULL,interval_seconds INTEGER NOT NULL,next_run REAL NOT NULL,enabled INTEGER NOT NULL);
    ''')
    columns={r['name'] for r in c.execute('PRAGMA table_info(agent_operations)')}
    for name,definition in [('kind',"TEXT NOT NULL DEFAULT 'read_pdf'"),('payload',"TEXT NOT NULL DEFAULT '{}'")]:
        if name not in columns: c.execute(f'ALTER TABLE agent_operations ADD COLUMN {name} {definition}')


class GrantBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id: str=Field(pattern=r'^[a-f0-9]{32}$')
    file_ref: str=Field(min_length=8,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    actions: list[Literal['read_pdf','verify_citations','search_local','draft_propose']]=Field(min_length=1,max_length=4)
    ttl: int=Field(default=86400,ge=60,le=2592000)
    confirmed: bool=False
    include_memories: bool=False


class TaskBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    goal: str=Field(min_length=1,max_length=12000)
    grant_id: str
    request_id: str=Field(min_length=8,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    workflow_id: str | None=None
    skill_id: str | None=None


class MemoryBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    content: str=Field(min_length=1,max_length=10000)
    layer: Literal['user','project']='project'
    confirmed: bool=False


class ScheduleBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    grant_id: str
    goal: str=Field(min_length=1,max_length=12000)
    interval_seconds: int=Field(ge=300,le=2592000)
    confirmed: bool=False


def event(c,task_id,kind,payload=None):
    c.execute('INSERT INTO control_events(task_id,kind,payload,created) VALUES(?,?,?,?)',
              (task_id,kind,json.dumps(payload or {},ensure_ascii=False),time.time()))


def owned_task(c,task_id,user_id):
    row=c.execute('SELECT t.* FROM agent_tasks t JOIN spaces s ON s.id=t.space_id WHERE t.id=? AND s.user_id=?',(task_id,user_id)).fetchone()
    if not row: raise HTTPException(404,'Task not found')
    return row


def valid_grant(c,grant_id,user_id,space_id=None):
    row=c.execute('SELECT g.* FROM control_grants g JOIN spaces s ON s.id=g.space_id AND s.user_id=g.user_id JOIN devices d ON d.id=g.device_id AND d.user_id=g.user_id WHERE g.id=? AND g.user_id=? AND g.active=1 AND g.expires>? AND d.status=\'active\'',(grant_id,user_id,time.time())).fetchone()
    if not row or (space_id is not None and row['space_id']!=space_id):
        raise HTTPException(403,'Grant missing, expired, revoked or outside project')
    return row


def check_managed(c,task_id,lease_token=None):
    job=c.execute('SELECT * FROM control_jobs WHERE task_id=?',(task_id,)).fetchone()
    if not job: return None
    if lease_token is None: raise HTTPException(409,'Managed task is advanced by its worker')
    return require_lease(c,task_id,lease_token)


def require_lease(c,task_id,token,now=None):
    now=time.time() if now is None else now
    row=c.execute('SELECT j.*,t.status AS task_status FROM control_jobs j JOIN agent_tasks t ON t.id=j.task_id WHERE j.task_id=?',(task_id,)).fetchone()
    if not row or row['status']!='leased' or row['lease_token']!=token or row['lease_until']<=now or row['task_status'] in ('cancelled','failed','succeeded'):
        raise HTTPException(409,'Worker lease no longer owns task')
    valid_grant(c,row['grant_id'],row['user_id'])
    return row


def submit(c,user_id,space_id,body):
    app=app_module();app.require_space(c,user_id,space_id)
    g=valid_grant(c,body.grant_id,user_id,space_id)
    from paperagent_cloud.research.capabilities import research_context
    try: research_context(body.goal,body.workflow_id,body.skill_id)
    except ValueError: raise HTTPException(422,'Unknown workflow or skill')
    old=c.execute('SELECT t.id,t.goal,j.grant_id,j.input_json FROM agent_tasks t LEFT JOIN control_jobs j ON j.task_id=t.id WHERE t.space_id=? AND t.request_id=?',(space_id,body.request_id)).fetchone()
    inp={'file_ref':g['file_ref'],'sha256':g['sha256'],'device_id':g['device_id'],
         'workflow_id':body.workflow_id,'skill_id':body.skill_id}
    encoded=json.dumps(inp,sort_keys=True)
    if old:
        if (old['goal'],old['grant_id'],old['input_json'])!=(body.goal,body.grant_id,encoded): raise HTTPException(409,'Request ID content conflict')
        return {'id':old['id'],'execution_owner':'cloud'}
    tid=uuid.uuid4().hex
    c.execute('INSERT INTO agent_tasks(id,space_id,request_id,goal,status,created) VALUES(?,?,?,?,?,?)',(tid,space_id,body.request_id,body.goal,'queued',int(time.time())))
    c.execute('INSERT INTO control_jobs(task_id,user_id,grant_id,input_json,status) VALUES(?,?,?,?,?)',(tid,user_id,body.grant_id,encoded,'ready'))
    event(c,tid,'queued')
    return {'id':tid,'execution_owner':'cloud','status':'queued'}


def claim_job(now=None,lease_seconds=600):
    now=time.time() if now is None else now
    with app_module().db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM control_jobs WHERE (status='ready' AND next_attempt<=?) OR (status='leased' AND lease_until<=?) ORDER BY next_attempt,task_id LIMIT 1",(now,now)).fetchone()
        if not row: c.commit();return None
        task_status=c.execute('SELECT status FROM agent_tasks WHERE id=?',(row['task_id'],)).fetchone()[0]
        if task_status in ('succeeded','failed','cancelled','waiting_local'):
            recovered={'succeeded':'done','waiting_local':'waiting'}.get(task_status,task_status)
            c.execute('UPDATE control_jobs SET status=?,lease_token=NULL,lease_until=0 WHERE task_id=?',(recovered,row['task_id']))
            event(c,row['task_id'],'recovered',{'status':recovered});c.commit();return None
        try: valid_grant(c,row['grant_id'],row['user_id'])
        except HTTPException:
            cancel(c,row['task_id']);c.commit();return None
        token=secrets.token_hex(24)
        c.execute("UPDATE control_jobs SET status='leased',lease_token=?,lease_until=? WHERE task_id=?",(token,now+lease_seconds,row['task_id']))
        c.execute("UPDATE agent_tasks SET status='queued' WHERE id=? AND status='running'",(row['task_id'],))
        event(c,row['task_id'],'claimed')
        c.commit()
        return dict(row)|{'lease_token':token}


def cancel(c,task_id):
    c.execute("UPDATE control_jobs SET status='cancelled',lease_token=NULL WHERE task_id=?",(task_id,))
    c.execute("UPDATE agent_tasks SET status='cancelled' WHERE id=? AND status NOT IN ('succeeded','failed')",(task_id,))
    c.execute("UPDATE agent_operations SET status='cancelled' WHERE task_id=? AND status='waiting_local'",(task_id,))
    event(c,task_id,'cancelled')


def operation_allowed(c,row):
    job=c.execute('SELECT * FROM control_jobs WHERE task_id=?',(row['task_id'],)).fetchone()
    if not job: return
    task=owned_task(c,row['task_id'],job['user_id'])
    if row['status']!='completed' and task['status']!='waiting_local': raise HTTPException(409,'Task is not waiting for this device')
    g=valid_grant(c,job['grant_id'],job['user_id'])
    if row['kind'] not in json.loads(g['actions']): raise HTTPException(403,'Operation outside grant')


class CloudSessionRepository:
    # Mutations are owned by the fenced advance transaction, never arbitrary client saves.
    def __init__(self,user_id): self.user_id=user_id
    def get(self,task_id):
        with app_module().db() as c:
            c.execute('BEGIN')
            task=dict(owned_task(c,task_id,self.user_id))
            context=c.execute('SELECT answer,steps FROM agent_context WHERE task_id=?',(task_id,)).fetchone()
            task.update(dict(context) if context else {})
            task['execution_owner']='cloud'
            task['events']=[dict(r)|{'payload':json.loads(r['payload'])} for r in c.execute('SELECT seq,kind,payload,created FROM control_events WHERE task_id=? ORDER BY seq DESC LIMIT 100',(task_id,))][::-1]
            task['revision']=task['events'][-1]['seq'] if task['events'] else 0
            c.commit();return task


class CloudMemoryRepository:
    def __init__(self,user_id,space_id): self.user_id,self.space_id=user_id,space_id
    def list_memories(self,layer=None,limit=None):
        with app_module().db() as c:
            app_module().require_space(c,self.user_id,self.space_id)
            rows=c.execute('SELECT value FROM control_memories WHERE user_id=? AND (space_id=? OR space_id IS NULL) ORDER BY id',(self.user_id,self.space_id)).fetchall()
        values=[MemoryRecord.model_validate_json(r['value']) for r in rows]
        values=[m for m in values if m.status=='active' and (layer is None or m.layer==layer)]
        return values if limit is None else values[:limit]


def memory_context(user_id,space_id,goal):
    from paperagent.tasks.memory_manager import MemoryManager
    return MemoryManager(CloudMemoryRepository(user_id,space_id)).relevant(project_id=space_id,goal=goal)


def schedule_due(now=None):
    now=time.time() if now is None else now
    with app_module().db() as c:
        c.execute('BEGIN IMMEDIATE')
        rows=c.execute('SELECT * FROM control_schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 20',(now,)).fetchall()
        for row in rows:
            try:
                valid_grant(c,row['grant_id'],row['user_id'],row['space_id'])
                body=TaskBody(goal=row['goal'],grant_id=row['grant_id'],request_id='schedule_'+row['id']+'_'+str(int(row['next_run'])))
                submit(c,row['user_id'],row['space_id'],body)
                c.execute('UPDATE control_schedules SET next_run=? WHERE id=?',(now+row['interval_seconds'],row['id']))
            except HTTPException:
                c.execute('UPDATE control_schedules SET enabled=0 WHERE id=?',(row['id'],))
        c.commit()
        return len(rows)


def build(app):
    router=APIRouter(prefix='/control')
    @router.post('/spaces/{space_id}/grants',status_code=201)
    def create_grant(space_id:str,body:GrantBody,user_id:str=Depends(app.current_user)):
        if not body.confirmed: raise HTTPException(422,'Explicit material authorization required')
        with app.db() as c:
            app.require_space(c,user_id,space_id)
            if not c.execute("SELECT 1 FROM devices WHERE id=? AND user_id=? AND status='active'",(body.device_id,user_id)).fetchone(): raise HTTPException(404,'Device not found')
            gid=uuid.uuid4().hex;expires=time.time()+body.ttl
            c.execute('INSERT INTO control_grants VALUES(?,?,?,?,?,?,?,?,?,?)',(gid,user_id,space_id,body.device_id,body.file_ref,body.sha256,json.dumps(sorted(set(body.actions))),expires,1,int(body.include_memories)))
        return body.model_dump(exclude={'confirmed','ttl'})|{'id':gid,'expires':expires}

    @router.delete('/grants/{grant_id}')
    def revoke(grant_id:str,user_id:str=Depends(app.current_user)):
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('UPDATE control_grants SET active=0 WHERE id=? AND user_id=?',(grant_id,user_id)).rowcount: raise HTTPException(404,'Grant not found')
            for row in c.execute("SELECT task_id FROM control_jobs WHERE grant_id=? AND status NOT IN ('done','cancelled','failed')",(grant_id,)).fetchall(): cancel(c,row['task_id'])
            c.commit()
        return {'status':'revoked'}

    @router.post('/spaces/{space_id}/tasks',status_code=201)
    def enqueue(space_id:str,body:TaskBody,user_id:str=Depends(app.current_user)):
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE')
            result=submit(c,user_id,space_id,body);c.commit();return result

    @router.get('/tasks/{task_id}')
    def status(task_id:str,user_id:str=Depends(app.current_user)):
        return CloudSessionRepository(user_id).get(task_id)

    @router.post('/tasks/{task_id}/cancel')
    def cancel_task(task_id:str,user_id:str=Depends(app.current_user)):
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE');task=owned_task(c,task_id,user_id)
            if task['status'] in ('succeeded','failed'): raise HTTPException(409,'Task already terminal')
            cancel(c,task_id);c.commit()
        return {'status':'cancelled'}

    @router.get('/devices/{device_id}/operations')
    def operations(device_id:str,space_id:str | None=None,user_id:str=Depends(app.current_user),x_device_token:str=Header(default='')):
        with app.db() as c:
            app.verify_device(c,{'device_id':device_id},user_id,x_device_token)
            rows=c.execute("SELECT o.* FROM agent_operations o JOIN control_jobs j ON j.task_id=o.task_id WHERE j.user_id=? AND o.device_id=? AND o.status='waiting_local' AND COALESCE(o.lease_until,0)<=? AND (? IS NULL OR o.task_id IN (SELECT id FROM agent_tasks WHERE space_id=?)) LIMIT 20",(user_id,device_id,time.time(),space_id,space_id)).fetchall()
            result=[]
            for row in rows:
                try: operation_allowed(c,row)
                except HTTPException: continue
                job=c.execute('SELECT grant_id FROM control_jobs WHERE task_id=?',(row['task_id'],)).fetchone()
                result.append({k:row[k] for k in ('id','task_id','file_ref','sha256','kind')}|{'payload':json.loads(row['payload']),'grant_id':job['grant_id']})
            return {'operations':result}

    @router.get('/spaces/{space_id}/memories')
    def memories(space_id:str,user_id:str=Depends(app.current_user)):
        return {'memories':[m.model_dump(mode='json') for m in CloudMemoryRepository(user_id,space_id).list_memories()]}

    @router.post('/spaces/{space_id}/memories',status_code=201)
    def remember(space_id:str,body:MemoryBody,user_id:str=Depends(app.current_user)):
        if not body.confirmed: raise HTTPException(422,'Explicit memory sharing required')
        with app.db() as c:
            app.require_space(c,user_id,space_id)
            m=MemoryRecord(memory_id='mem_'+uuid.uuid4().hex,content=body.content,layer=body.layer,
                           trust='user_adopted',confirmation='provided',scope={'project_id':space_id} if body.layer=='project' else {})
            c.execute('INSERT INTO control_memories(id,user_id,space_id,value) VALUES(?,?,?,?)',(m.memory_id,user_id,space_id if body.layer=='project' else None,m.model_dump_json()))
        return m.model_dump(mode='json')

    @router.delete('/spaces/{space_id}/memories/{memory_id}')
    def forget(space_id:str,memory_id:str,user_id:str=Depends(app.current_user)):
        with app.db() as c:
            app.require_space(c,user_id,space_id)
            if not c.execute('DELETE FROM control_memories WHERE id=? AND user_id=? AND (space_id=? OR space_id IS NULL)',(memory_id,user_id,space_id)).rowcount: raise HTTPException(404,'Memory not found')
        return {'deleted':True}

    @router.post('/spaces/{space_id}/schedules',status_code=201)
    def schedule(space_id:str,body:ScheduleBody,user_id:str=Depends(app.current_user)):
        if not body.confirmed: raise HTTPException(422,'Explicit recurring authorization required')
        with app.db() as c:
            g=valid_grant(c,body.grant_id,user_id,space_id)
            if 'draft_propose' in json.loads(g['actions']): raise HTTPException(422,'Recurring grants must be read-only')
            sid=uuid.uuid4().hex
            c.execute('INSERT INTO control_schedules VALUES(?,?,?,?,?,?,?,1)',(sid,user_id,space_id,body.grant_id,body.goal,body.interval_seconds,time.time()))
        return {'id':sid,'status':'enabled'}

    @router.delete('/schedules/{schedule_id}')
    def unschedule(schedule_id:str,user_id:str=Depends(app.current_user)):
        with app.db() as c:
            if not c.execute('UPDATE control_schedules SET enabled=0 WHERE id=? AND user_id=?',(schedule_id,user_id)).rowcount: raise HTTPException(404,'Schedule not found')
        return {'status':'disabled'}
    return router
