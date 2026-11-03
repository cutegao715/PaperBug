"""Cloud execution service, separate from local conversation checkpoints.

agent_context is authoritative for cloud tasks (research context, bounded steps,
material/device/method identity). It is never a local resume checkpoint. Lifecycle
replication changes domain state only, not task ownership or execution mode.
Run behind TLS; SQLite is for a single service instance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from paperagent.env import load_env_file
from paperagent.harness.llm import LLMClient
from paperagent_cloud.research.capabilities import research_context, capability_catalog, read_skill_resource
from paperagent_cloud.research.original_agent import decide as original_decide

load_env_file()
app = FastAPI(title="PaperAgent Cloud Conversation")
router = APIRouter()
from paperagent.api.compat import LegacyAPIDeprecation
app.add_middleware(LegacyAPIDeprecation)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("PAPERAGENT_CLOUD_ORIGINS", "http://127.0.0.1:8766,http://localhost:8766").split(","), allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Device-Token", "X-Lease-Token"])


def db():
    path = Path(os.getenv("PAPERAGENT_CLOUD_DB", "./cloud-data/cloud.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt BLOB NOT NULL, password BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS spaces(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, status TEXT NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id), request_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, created INTEGER NOT NULL, UNIQUE(space_id,request_id,role));
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_per_space ON messages(space_id) WHERE role='user' AND status='pending';
        CREATE TABLE IF NOT EXISTS agent_tasks(id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id), request_id TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL, created INTEGER NOT NULL, UNIQUE(space_id, request_id));
        CREATE TABLE IF NOT EXISTS agent_operations(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES agent_tasks(id), file_ref TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, created INTEGER NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_waiting_operation_per_task ON agent_operations(task_id) WHERE status='waiting_local';
        CREATE TABLE IF NOT EXISTS agent_context(task_id TEXT PRIMARY KEY REFERENCES agent_tasks(id), file_ref TEXT NOT NULL, sha256 TEXT NOT NULL, device_id TEXT NOT NULL, steps INTEGER NOT NULL DEFAULT 0, answer TEXT);
        CREATE TABLE IF NOT EXISTS agent_method_results(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES agent_tasks(id), result_json TEXT NOT NULL);
        """)
        columns = {row['name'] for row in c.execute('PRAGMA table_info(agent_operations)')}
        if 'device_id' not in columns:
            c.execute('ALTER TABLE agent_operations ADD COLUMN device_id TEXT REFERENCES devices(id)')
            c.execute("UPDATE agent_tasks SET status='paused' WHERE id IN (SELECT task_id FROM agent_operations WHERE status='waiting_local')")
            c.execute("UPDATE agent_operations SET status='needs_reauthorization' WHERE status='waiting_local'")
        if 'lease_hash' not in columns:
            c.execute('ALTER TABLE agent_operations ADD COLUMN lease_hash TEXT')
            c.execute('ALTER TABLE agent_operations ADD COLUMN lease_until INTEGER')
        from paperagent_cloud.control import migrate as control_migrate
        control_migrate(c)
        from paperagent_cloud.conversation_api import migrate as chat_migrate
        chat_migrate(c)
        from paperagent_cloud.monitors import migrate as monitor_migrate
        monitor_migrate(c)
        from paperagent_cloud.sync_api import migrate
        migrate(c)
        from paperagent_cloud.usage import migrate as migrate_usage
        migrate_usage(c)
        from paperagent_cloud.proactive_api import migrate as migrate_proactive
        migrate_proactive(c)
        context_columns = {row['name'] for row in c.execute('PRAGMA table_info(agent_context)')}
        if 'research_json' not in context_columns:
            c.execute('ALTER TABLE agent_context ADD COLUMN research_json TEXT')
            c.execute('ALTER TABLE agent_context ADD COLUMN workflow_id TEXT')
            c.execute('ALTER TABLE agent_context ADD COLUMN skill_id TEXT')


@app.on_event("startup")
def startup():
    init_db()
    from paperagent_cloud.usage import recover_reservations
    with db() as c:
        active_worker = c.execute("SELECT 1 FROM control_jobs WHERE status='leased' AND lease_until>?", (time.time(),)).fetchone()
    if not active_worker:
        recover_reservations()
    # A single instance owns this database. Interrupted requests can be retried.
    with db() as c:
        c.execute("UPDATE messages SET status='failed' WHERE status='pending'")
        c.execute("UPDATE agent_tasks SET status='queued' WHERE status='running' AND id NOT IN (SELECT task_id FROM control_jobs)")


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=128)


class SpaceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DeviceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class MessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class AgentTaskInput(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class OperationInput(BaseModel):
    file_ref: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class AgentAdvanceInput(OperationInput):
    workflow_id: str | None = Field(default=None, max_length=100)
    skill_id: str | None = Field(default=None, max_length=100)


class MaterialPage(BaseModel):
    page: int = Field(ge=1)
    text: str = Field(max_length=3500)
    truncated: bool = False


class OperationResult(BaseModel):
    file_ref: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pages: list[MaterialPage] = Field(default_factory=list, max_length=5)
    data: dict | None = None
    error: str | None = Field(default=None,pattern=r'^material_unavailable$')

    @model_validator(mode="after")
    def bounded_result(self):
        if not self.pages and self.data is None and not self.error:
            raise ValueError("Operation result must contain pages or data")
        if len(json.dumps(self.data, ensure_ascii=False)) > 60000:
            raise ValueError("Operation result too large")
        return self
    total_pages: int | None = Field(default=None, ge=1)
    pages_truncated: bool = False
    locator_kind: str = Field(default='page', pattern=r'^(page|text_section)$')


def token_for(c, user_id):
    token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO tokens VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, int(time.time()) + 86400))
    return token


def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "请登录")
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as c:
        row = c.execute("SELECT user_id FROM tokens WHERE hash=? AND expires>?", (digest, int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401, "登录已失效")
    return row["user_id"]


def require_space(c, user_id, space_id):
    row = c.execute("SELECT id,name FROM spaces WHERE id=? AND user_id=?", (space_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "研究空间不存在")
    return row


@router.get("/auth/me")
def me(user_id: str = Depends(current_user)):
    return {"user_id": user_id}


@router.post("/auth/register", status_code=201)
def register(body: Credentials):
    salt = secrets.token_bytes(16)
    user_id = uuid.uuid4().hex
    try:
        with db() as c:
            c.execute("INSERT INTO users VALUES(?,?,?,?)", (user_id, body.username.lower(), salt, password_hash(body.password, salt)))
            token = token_for(c, user_id)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "用户名已存在")
    return {"token": token, "user_id": user_id}


@router.post("/auth/login")
def login(body: Credentials):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (body.username.lower(),)).fetchone()
        salt = row["salt"] if row else b"\x00" * 16
        actual = password_hash(body.password, salt)
        if not row or not hmac.compare_digest(actual, row["password"]):
            raise HTTPException(401, "用户名或密码错误")
        return {"token": token_for(c, row["id"]), "user_id": row["id"]}


@router.post("/auth/logout")
def logout(authorization: str = Header(default=""), user_id: str = Depends(current_user)):
    with db() as c:
        c.execute("DELETE FROM tokens WHERE hash=? AND user_id=?", (hashlib.sha256(authorization[7:].encode()).hexdigest(), user_id))
    return {"ok": True}


@router.get("/spaces")
def spaces(user_id: str = Depends(current_user)):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT id,name FROM spaces WHERE user_id=? ORDER BY rowid DESC", (user_id,))]


@router.get('/research/capabilities')
def research_capabilities(user_id: str = Depends(current_user)):
    return capability_catalog()


@router.post('/devices', status_code=201)
def register_device(body: DeviceInput, user_id: str = Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, '请输入设备名称')
    device_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
    with db() as c:
        c.execute('INSERT INTO devices VALUES(?,?,?,?,?,?)', (device_id, user_id, name, hashlib.sha256(token.encode()).hexdigest(), 'active', int(time.time())))
    return {'id':device_id, 'name':name, 'status':'active', 'device_token':token}


@router.get('/devices')
def list_devices(user_id: str = Depends(current_user)):
    with db() as c:
        return [dict(row) for row in c.execute('SELECT id,name,status,created FROM devices WHERE user_id=? ORDER BY rowid DESC', (user_id,))]


@router.post('/devices/{device_id}/revoke')
def revoke_device(device_id: str, user_id: str = Depends(current_user)):
    with db() as c:
        result = c.execute("UPDATE devices SET status='revoked' WHERE id=? AND user_id=?", (device_id,user_id))
        if not result.rowcount:
            raise HTTPException(404, '设备不存在')
    return {'id':device_id, 'status':'revoked'}


@router.post("/spaces", status_code=201)
def create_space(body: SpaceInput, user_id: str = Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请输入名称")
    space = {"id": uuid.uuid4().hex, "name": name}
    with db() as c:
        c.execute("INSERT INTO spaces(id,user_id,name) VALUES(?,?,?)", (space["id"], user_id, name))
    return space


@router.post("/spaces/{space_id}/agent-tasks", status_code=201)
def create_agent_task(space_id: str, body: AgentTaskInput, user_id: str = Depends(current_user)):
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(422, "请输入任务目标")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            require_space(c, user_id, space_id)
            row = c.execute("SELECT id,space_id,goal,status FROM agent_tasks WHERE space_id=? AND request_id=?", (space_id, body.request_id)).fetchone()
            if row and row['goal'] != goal:
                raise HTTPException(409, "请求标识已用于其他任务")
            if row:
                result = dict(row)
            else:
                result = {'id':uuid.uuid4().hex, 'space_id':space_id, 'goal':goal, 'status':'queued'}
                c.execute("INSERT INTO agent_tasks VALUES(?,?,?,?,?,?)", (result['id'], space_id, body.request_id, goal, 'queued', int(time.time())))
            c.execute("COMMIT")
            return result
        except Exception:
            c.execute("ROLLBACK")
            raise


@router.get("/agent-tasks/{task_id}")
def get_agent_task(task_id: str, user_id: str = Depends(current_user)):
    with db() as c:
        row = c.execute("SELECT t.id,t.space_id,t.goal,t.status FROM agent_tasks t JOIN spaces s ON t.space_id=s.id WHERE t.id=? AND s.user_id=?", (task_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    return dict(row)


def agent_decide(observation):
    return original_decide(LLMClient(), observation)


@router.post('/agent-tasks/{task_id}/advance')
def advance_agent_task(task_id: str, body: AgentAdvanceInput, user_id: str = Depends(current_user)):
    return advance_task(task_id, body, user_id)


def advance_task(task_id, body, user_id, worker_lease=None):
    """Shared advance engine; managed tasks require the current worker fence."""
    from paperagent_cloud import control
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            task = c.execute('SELECT t.* FROM agent_tasks t JOIN spaces s ON s.id=t.space_id WHERE t.id=? AND s.user_id=?', (task_id,user_id)).fetchone()
            managed = control.check_managed(c, task_id, worker_lease)
            if not task:
                raise HTTPException(404, '任务不存在')
            device = c.execute("SELECT id FROM devices WHERE id=? AND user_id=? AND status='active'", (body.device_id,user_id)).fetchone()
            if not device:
                raise HTTPException(404, '设备不存在')
            context = c.execute('SELECT * FROM agent_context WHERE task_id=?', (task_id,)).fetchone()
            if context and (context['file_ref'],context['sha256'],context['device_id']) != (body.file_ref,body.sha256,body.device_id):
                raise HTTPException(409, '任务材料或设备不匹配')
            if context and context['research_json'] and (context['workflow_id'], context['skill_id']) != (body.workflow_id, body.skill_id):
                raise HTTPException(409, '任务科研方法不匹配')
            if not context:
                try:
                    research = research_context(task['goal'], body.workflow_id, body.skill_id)
                except ValueError as exc:
                    raise HTTPException(422, '科研方法或流程不存在') from exc
                c.execute('INSERT INTO agent_context(task_id,file_ref,sha256,device_id,research_json,workflow_id,skill_id) VALUES(?,?,?,?,?,?,?)', (task_id,body.file_ref,body.sha256,body.device_id,json.dumps(research,ensure_ascii=False),body.workflow_id,body.skill_id))
                context = c.execute('SELECT * FROM agent_context WHERE task_id=?', (task_id,)).fetchone()
            if task['status'] == 'succeeded':
                c.execute('COMMIT')
                return {'id':task_id,'status':'succeeded','answer':context['answer']}
            if task['status'] == 'waiting_local':
                op = c.execute("SELECT id,file_ref,sha256,device_id,status FROM agent_operations WHERE task_id=? AND status='waiting_local'", (task_id,)).fetchone()
                c.execute('COMMIT')
                return {'id':task_id,'status':'waiting_local','operation':dict(op)}
            if task['status'] != 'queued' or context['steps'] >= 10:
                raise HTTPException(409, '任务不可继续或已达到步骤上限')
            results = [json.loads(r['result_json']) for r in c.execute("SELECT result_json FROM agent_operations WHERE task_id=? AND status='completed' ORDER BY created,rowid", (task_id,))]
            research = json.loads(context['research_json']) if context['research_json'] else research_context(task['goal'])
            method_results = [json.loads(r['result_json']) for r in c.execute('SELECT result_json FROM agent_method_results WHERE task_id=? ORDER BY rowid', (task_id,))]
            observation = {'goal':task['goal'], 'file_ref':body.file_ref, 'results':results, 'method_results':method_results, 'steps_remaining':10-context['steps'], 'research':research}
            if managed:
                grant = control.valid_grant(c, managed['grant_id'], user_id, task['space_id'])
                observation['allowed_local_tools'] = json.loads(grant['actions'])
                observation['memories'] = control.memory_context(user_id, task['space_id'], task['goal']) if grant['include_memories'] else {}
                observation['execution_owner'] = 'cloud'

            c.execute("UPDATE agent_tasks SET status='running' WHERE id=?", (task_id,))
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK')
            raise
    try:
        from paperagent_cloud.usage import metered
        with metered(user_id, "agent:" + task_id):
            if managed:
                from paperagent.harness.cloud_context import fit_cloud_observation
                observation = fit_cloud_observation(observation)
            action = agent_decide(observation)
        kind = action.get('action') if isinstance(action, dict) else None
        if kind == 'read_pdf' and action.get('path') != body.file_ref:
            raise ValueError('unapproved file reference')
        if kind not in {'read_pdf','read_context','finish','chat_reply'} and not (managed and kind == 'tool_call'):
            raise ValueError('unsupported agent action')
        if kind == 'read_context':
            if action.get('kind') != 'skill_resource' or action.get('key') not in {s['key'] for s in research['skills']}:
                raise ValueError('unsupported or unauthorized research resource')
            method_result = read_skill_resource(action['key'], action.get('resource_id',''), offset=int(action.get('offset',0)), expected_sha256=action.get('expected_sha256',''))
        operation_kind = 'read_pdf'
        operation_payload = {}
        if managed and kind in {'tool_call','read_pdf'}:
            operation_kind = action.get('tool_id') if kind == 'tool_call' else 'read_pdf'
            if operation_kind not in observation['allowed_local_tools']:
                raise ValueError('operation outside material grant')
            operation_payload = action.get('input', {}) if kind == 'tool_call' else {}
            if not isinstance(operation_payload, dict) or len(json.dumps(operation_payload)) > 120000:
                raise ValueError('operation payload too large')
        answer = action.get('summary' if kind == 'finish' else 'text') if kind in {'finish','chat_reply'} else None
        if kind in {'finish','chat_reply'} and (not isinstance(answer,str) or not answer.strip() or len(answer)>30000):
            raise ValueError('invalid answer')
    except Exception as exc:
        with db() as c:
            if not worker_lease:
                c.execute("UPDATE agent_tasks SET status='queued' WHERE id=? AND status='running'", (task_id,))
            else:
                c.execute("UPDATE agent_tasks SET status='queued' WHERE id=? AND status='running' AND EXISTS (SELECT 1 FROM control_jobs WHERE task_id=? AND lease_token=? AND status='leased' AND lease_until>?)",(task_id,task_id,worker_lease,time.time()))
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, '科研 Agent 调用失败或返回不支持的动作') from exc
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            control.check_managed(c, task_id, worker_lease)
            active = c.execute("SELECT id FROM devices WHERE id=? AND user_id=? AND status='active'", (body.device_id,user_id)).fetchone()
            if not active:
                c.execute("UPDATE agent_tasks SET status='paused' WHERE id=? AND status='running'", (task_id,))
                c.execute('COMMIT')
                raise HTTPException(409, '设备已撤销，任务暂停')
            if kind in {'read_pdf','tool_call'}:
                op_id = uuid.uuid4().hex
                c.execute('INSERT INTO agent_operations(id,task_id,file_ref,sha256,status,created,device_id) VALUES(?,?,?,?,?,?,?)', (op_id,task_id,body.file_ref,body.sha256,'waiting_local',int(time.time()),body.device_id))
                c.execute('UPDATE agent_operations SET kind=?,payload=? WHERE id=?', (operation_kind,json.dumps(operation_payload,ensure_ascii=False),op_id))
                c.execute("UPDATE agent_tasks SET status='waiting_local' WHERE id=? AND status='running'", (task_id,))
                result = {'id':task_id,'status':'waiting_local','operation':{'id':op_id,'file_ref':body.file_ref,'sha256':body.sha256,'device_id':body.device_id,'status':'waiting_local'}}
            elif kind == 'read_context':
                c.execute('INSERT INTO agent_method_results VALUES(?,?,?)', (uuid.uuid4().hex,task_id,json.dumps(method_result,ensure_ascii=False)))
                c.execute("UPDATE agent_tasks SET status='queued' WHERE id=? AND status='running'", (task_id,))
                result = {'id':task_id,'status':'queued'}
            else:
                c.execute("UPDATE agent_tasks SET status='succeeded' WHERE id=? AND status='running'", (task_id,))
                c.execute('UPDATE agent_context SET answer=? WHERE task_id=?', (answer,task_id))
                result = {'id':task_id,'status':'succeeded','answer':answer}
            c.execute('UPDATE agent_context SET steps=steps+1 WHERE task_id=?', (task_id,))
            c.execute('COMMIT')
            return result
        except Exception:
            if c.in_transaction:
                c.execute('ROLLBACK')
            raise


def owned_operation(c, operation_id, user_id):
    row = c.execute("SELECT o.* FROM agent_operations o JOIN agent_tasks t ON t.id=o.task_id JOIN spaces s ON s.id=t.space_id WHERE o.id=? AND s.user_id=?", (operation_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "操作不存在")
    return row


@router.post('/agent-tasks/{task_id}/operations', status_code=201)
def create_operation(task_id: str, body: OperationInput, user_id: str = Depends(current_user)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            task = c.execute('SELECT t.id,t.status FROM agent_tasks t JOIN spaces s ON s.id=t.space_id WHERE t.id=? AND s.user_id=?', (task_id,user_id)).fetchone()
            if not task:
                raise HTTPException(404, '任务不存在')
            from paperagent_cloud.control import check_managed
            check_managed(c, task_id)
            device = c.execute("SELECT id FROM devices WHERE id=? AND user_id=? AND status='active'", (body.device_id,user_id)).fetchone()
            if not device:
                raise HTTPException(404, '设备不存在')
            if task['status'] != 'queued':
                raise HTTPException(409, '任务无法创建操作')
            op_id = uuid.uuid4().hex
            c.execute('INSERT INTO agent_operations(id,task_id,file_ref,sha256,status,result_json,created,device_id) VALUES(?,?,?,?,?,?,?,?)', (op_id,task_id,body.file_ref,body.sha256,'waiting_local',None,int(time.time()),body.device_id))
            c.execute("UPDATE agent_tasks SET status='waiting_local' WHERE id=?", (task_id,))
            c.execute('COMMIT')
            return {'id':op_id, 'task_id':task_id, 'file_ref':body.file_ref, 'sha256':body.sha256, 'status':'waiting_local'}
        except Exception:
            c.execute('ROLLBACK')
            raise


@router.get('/agent-operations/{operation_id}')
def get_operation(operation_id: str, user_id: str = Depends(current_user)):
    with db() as c:
        row = owned_operation(c, operation_id, user_id)
        return {key:row[key] for key in ('id','task_id','file_ref','sha256','status')}


def verify_device(c, row, user_id, device_token):
    device = c.execute("SELECT token_hash FROM devices WHERE id=? AND user_id=? AND status='active'", (row['device_id'], user_id)).fetchone()
    if not device or not device_token or not hmac.compare_digest(device['token_hash'], hashlib.sha256(device_token.encode()).hexdigest()):
        raise HTTPException(401, '设备凭证无效或已撤销')


@router.post('/agent-operations/{operation_id}/claim')
def claim_operation(operation_id: str, user_id: str = Depends(current_user), x_device_token: str = Header(default='')):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            row = owned_operation(c, operation_id, user_id)
            verify_device(c, row, user_id, x_device_token)
            from paperagent_cloud.control import operation_allowed
            operation_allowed(c, row)
            now = int(time.time())
            if row['status'] != 'waiting_local' or (row['lease_until'] or 0) > now:
                raise HTTPException(409, '操作不可领取或已被领取')
            token = secrets.token_urlsafe(32)
            until = now + 300
            c.execute('UPDATE agent_operations SET lease_hash=?,lease_until=? WHERE id=?', (hashlib.sha256(token.encode()).hexdigest(), until, operation_id))
            c.execute('COMMIT')
            return {'id':operation_id, 'file_ref':row['file_ref'], 'sha256':row['sha256'], 'lease_token':token, 'lease_until':until, 'kind':row['kind'], 'payload':json.loads(row['payload'])}
        except Exception:
            c.execute('ROLLBACK')
            raise


@router.post('/agent-operations/{operation_id}/result')
def submit_operation_result(operation_id: str, body: OperationResult, user_id: str = Depends(current_user), x_device_token: str = Header(default=''), x_lease_token: str = Header(default='')):
    payload = json.dumps(body.model_dump(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            row = owned_operation(c, operation_id, user_id)
            verify_device(c, row, user_id, x_device_token)
            from paperagent_cloud.control import operation_allowed
            operation_allowed(c, row)
            valid_lease = bool(x_lease_token and row['lease_hash'] and hmac.compare_digest(row['lease_hash'], hashlib.sha256(x_lease_token.encode()).hexdigest()))
            if not valid_lease or (row['status'] != 'completed' and (row['lease_until'] or 0) <= int(time.time())):
                raise HTTPException(409, '租约无效或已过期')
            if not body.error and ((row['kind']=='read_pdf' and not body.pages) or (row['kind']!='read_pdf' and body.data is None)):
                raise HTTPException(422, 'Operation result type mismatch')
            if body.file_ref != row['file_ref'] or body.sha256 != row['sha256']:
                raise HTTPException(409, '文件版本不匹配')
            if row['status'] == 'completed':
                if row['result_json'] != payload:
                    raise HTTPException(409, '操作结果与已确认结果不一致')
            elif row['status'] == 'waiting_local':
                c.execute("UPDATE agent_operations SET status='completed', result_json=? WHERE id=?", (payload,operation_id))
                c.execute("UPDATE agent_tasks SET status='queued' WHERE id=?", (row['task_id'],))
                c.execute("UPDATE control_jobs SET status='ready',next_attempt=0 WHERE task_id=? AND status='waiting'", (row['task_id'],))
                from paperagent_cloud.control import event
                event(c,row['task_id'],'local_result',{'operation_id':operation_id})
                if body.error:
                    c.execute("UPDATE agent_tasks SET status='failed' WHERE id=?",(row['task_id'],))
                    c.execute("UPDATE control_jobs SET status='failed',lease_token=NULL,last_error=? WHERE task_id=?",(body.error,row['task_id']))
                    event(c,row['task_id'],'failed',{'reason':body.error})
            else:
                raise HTTPException(409, '操作状态不允许回传')
            c.execute('COMMIT')
            return {'id':operation_id, 'status':'completed'}
        except Exception:
            c.execute('ROLLBACK')
            raise


@router.get("/spaces/{space_id}/messages")
def messages(space_id: str, user_id: str = Depends(current_user)):
    with db() as c:
        require_space(c, user_id, space_id)
        return [dict(r) for r in c.execute("SELECT id,request_id,role,content,status FROM messages WHERE space_id=? ORDER BY rowid", (space_id,))]


def generate_reply(history):
    result = LLMClient().chat_json("你是个人科研助手。根据对话回答用户，不要虚构来源。只返回 JSON 对象，字段 reply 是回答文本。", json.dumps(history, ensure_ascii=False))
    reply = result.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("模型未返回有效回答")
    return reply.strip()


@router.post("/spaces/{space_id}/messages")
def send_message(space_id: str, body: MessageInput, user_id: str = Depends(current_user)):
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "请输入内容")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            require_space(c, user_id, space_id)
            previous = c.execute("SELECT content,status FROM messages WHERE space_id=? AND request_id=? AND role='user'", (space_id, body.request_id)).fetchone()
            if previous and previous['content'] != text:
                raise HTTPException(409, "请求标识已用于其他消息")
            if previous and previous['status'] == 'done':
                reply = c.execute("SELECT content FROM messages WHERE space_id=? AND request_id=? AND role='assistant'", (space_id, body.request_id)).fetchone()
                c.execute("COMMIT")
                return {"reply": reply['content'], "request_id": body.request_id}
            if previous and previous['status'] == 'pending':
                raise HTTPException(409, "该消息正在处理")
            if previous:
                c.execute("UPDATE messages SET status='pending' WHERE space_id=? AND request_id=? AND role='user'", (space_id, body.request_id))
            else:
                c.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, space_id, body.request_id, 'user', text, 'pending', int(time.time())))
            history = [dict(r) for r in c.execute("SELECT role,content FROM messages WHERE space_id=? AND (status='done' OR request_id=?) ORDER BY rowid DESC LIMIT 20", (space_id, body.request_id))][::-1]
            c.execute("COMMIT")
        except sqlite3.IntegrityError:
            c.execute("ROLLBACK")
            raise HTTPException(409, "该研究空间已有回复正在生成")
        except Exception:
            c.execute("ROLLBACK")
            raise
    try:
        from paperagent_cloud.usage import metered
        with metered(user_id, "message:" + space_id + ":" + body.request_id):
            reply = generate_reply(history)
    except Exception as exc:
        with db() as c:
            c.execute("UPDATE messages SET status='failed' WHERE space_id=? AND request_id=? AND role='user'", (space_id, body.request_id))
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, "模型调用失败，请检查服务配置后重试")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, space_id, body.request_id, 'assistant', reply, 'done', int(time.time())))
        c.execute("UPDATE messages SET status='done' WHERE space_id=? AND request_id=? AND role='user'", (space_id, body.request_id))
        c.execute("COMMIT")
    return {"reply": reply, "request_id": body.request_id}


@router.get("/usage/summary")
def usage_summary(month: str | None = None, user_id: str = Depends(current_user)):
    from paperagent_cloud.usage import summary
    return summary(user_id, month)


import sys
from paperagent_cloud.sync_api import build as build_sync
router.include_router(build_sync(sys.modules[__name__]))
from paperagent_cloud.proactive_api import build as build_proactive
router.include_router(build_proactive(sys.modules[__name__]))

from paperagent_cloud.control import build as build_control
router.include_router(build_control(sys.modules[__name__]))
from paperagent_cloud.conversation_api import build as build_conversations
router.include_router(build_conversations(sys.modules[__name__]))
from paperagent_cloud.monitors import build as build_monitors
router.include_router(build_monitors(sys.modules[__name__]))

app.include_router(router, prefix="/api/v1")
app.include_router(router, prefix="/api", deprecated=True)
