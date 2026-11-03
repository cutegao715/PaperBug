"""Explicit bindings and append-only lifecycle replication; never uploads files."""
import hashlib
import json
import secrets
import time
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from paperagent.domain.lifecycle import LifecycleState


def migrate(c):
    columns = {r['name'] for r in c.execute('PRAGMA table_info(spaces)')}
    for name, sql in [('sync_mode', "TEXT NOT NULL DEFAULT 'local_only'"), ('stage', 'TEXT'), ('lifecycle_type', 'TEXT')]:
        if name not in columns:
            c.execute(f'ALTER TABLE spaces ADD COLUMN {name} {sql}')
    c.executescript('''
        CREATE TABLE IF NOT EXISTS bind_challenges(hash TEXT PRIMARY KEY, space_id TEXT NOT NULL,
            user_id TEXT NOT NULL, device_id TEXT NOT NULL, project_id TEXT NOT NULL, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS space_bindings(space_id TEXT NOT NULL REFERENCES spaces(id),
            device_id TEXT NOT NULL REFERENCES devices(id), project_id TEXT NOT NULL, bound_at INTEGER NOT NULL,
            PRIMARY KEY(space_id,device_id,project_id));
        CREATE TABLE IF NOT EXISTS lifecycle_events(id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id),
            idempotency_key TEXT NOT NULL, server_seq INTEGER NOT NULL, payload TEXT NOT NULL,
            conflict INTEGER NOT NULL, created INTEGER NOT NULL,
            UNIQUE(space_id,idempotency_key), UNIQUE(space_id,server_seq));
    ''')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Binding(Strict):
    device_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    project_id: str = Field(min_length=1, max_length=150, pattern=r'^[\w-]+$')


class Confirmation(Binding):
    challenge: str = Field(min_length=20, max_length=200)


class Mode(Strict):
    sync_mode: Literal['local_only','cloud_sync']
    confirmed: bool = False


class Event(Binding):
    idempotency_key: str = Field(min_length=8, max_length=100)
    stage_from: str | None = Field(default=None, max_length=100)
    state: LifecycleState
    reason: str = Field(min_length=1, max_length=2000)
    actor: str = Field(default='user', max_length=150)
    legacy_history: list[dict] = Field(default_factory=list)


def build(service):
    router = APIRouter()
    db, user, space = service.db, service.current_user, service.require_space

    def verify(c, uid, body, token):
        service.verify_device(c, {'device_id':body.device_id}, uid, token)

    @router.post('/spaces/{sid}/bind_challenge')
    def challenge(sid: str, body: Binding, uid: str = Depends(user), x_device_token: str = Header(default='')):
        with db() as c:
            space(c, uid, sid)
            verify(c, uid, body, x_device_token)
            token = secrets.token_urlsafe(32)
            c.execute('INSERT INTO bind_challenges VALUES(?,?,?,?,?,?,0)',
                (hashlib.sha256(token.encode()).hexdigest(),sid,uid,body.device_id,body.project_id,int(time.time())+600))
        return {'challenge':token,'expires_in':600}

    @router.post('/spaces/{sid}/bind_confirm')
    def confirm(sid: str, body: Confirmation, uid: str = Depends(user), x_device_token: str = Header(default='')):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            space(c, uid, sid)
            verify(c, uid, body, x_device_token)
            digest = hashlib.sha256(body.challenge.encode()).hexdigest()
            changed = c.execute('UPDATE bind_challenges SET used=1 WHERE hash=? AND space_id=? AND user_id=? AND device_id=? AND project_id=? AND expires>? AND used=0',
                (digest,sid,uid,body.device_id,body.project_id,int(time.time())))
            if not changed.rowcount:
                raise HTTPException(409,'绑定挑战无效、过期或已使用')
            c.execute('INSERT OR REPLACE INTO space_bindings VALUES(?,?,?,?)',(sid,body.device_id,body.project_id,int(time.time())))
        return {'space_id':sid,'device_id':body.device_id,'project_id':body.project_id,'user_id':uid}

    @router.get('/spaces/{sid}/sync')
    def get_mode(sid: str, uid: str = Depends(user)):
        with db() as c:
            space(c, uid, sid)
            return dict(c.execute('SELECT id,sync_mode,stage,lifecycle_type FROM spaces WHERE id=?',(sid,)).fetchone())

    @router.post('/spaces/{sid}/sync')
    def mode(sid: str, body: Mode, uid: str = Depends(user)):
        if body.sync_mode == 'cloud_sync' and not body.confirmed:
            raise HTTPException(422,'启用云同步需要明确确认')
        with db() as c:
            space(c, uid, sid)
            c.execute('UPDATE spaces SET sync_mode=? WHERE id=?',(body.sync_mode,sid))
        return {'sync_mode':body.sync_mode}

    def wire(row):
        from datetime import datetime, timezone
        payload = json.loads(row['payload'])
        payload['state'].setdefault('updated_at',datetime.fromtimestamp(row['created'],timezone.utc).isoformat())
        return {**payload, 'id':row['id'], 'server_seq':row['server_seq'],
                'conflict':bool(row['conflict']), 'server_created':row['created']}

    @router.post('/spaces/{sid}/lifecycle/events')
    def append(sid: str, body: Event, uid: str = Depends(user), x_device_token: str = Header(default='')):
        if body.state.history or not body.reason.strip():
            raise HTTPException(422,'事件须为单次状态变更，并包含原因')
        normalized = body.model_dump(mode='json')
        if 'updated_at' not in body.state.model_fields_set:
            normalized['state'].pop('updated_at')  # generated defaults are not client request content
        payload = json.dumps(normalized, ensure_ascii=False,sort_keys=True,separators=(',',':'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            space(c, uid, sid)
            verify(c, uid, body, x_device_token)
            binding = c.execute('SELECT 1 FROM space_bindings WHERE space_id=? AND device_id=? AND project_id=?',(sid,body.device_id,body.project_id)).fetchone()
            mode = c.execute('SELECT * FROM spaces WHERE id=?',(sid,)).fetchone()
            if not binding or mode['sync_mode'] != 'cloud_sync':
                raise HTTPException(403,'空间未绑定或云同步未启用')
            prior = c.execute('SELECT * FROM lifecycle_events WHERE space_id=? AND idempotency_key=?',(sid,body.idempotency_key)).fetchone()
            if prior:
                if prior['payload'] != payload:
                    raise HTTPException(409,'幂等键已用于不同事件')
                return wire(prior)
            seq = c.execute('SELECT COALESCE(MAX(server_seq),0)+1 FROM lifecycle_events WHERE space_id=?',(sid,)).fetchone()[0]
            conflict = seq > 1 and (body.stage_from != mode['stage'] or body.state.lifecycle_type != mode['lifecycle_type'])
            ident = uuid.uuid4().hex
            c.execute('INSERT INTO lifecycle_events VALUES(?,?,?,?,?,?,?)',(ident,sid,body.idempotency_key,seq,payload,int(conflict),int(time.time())))
            c.execute('UPDATE spaces SET stage=?,lifecycle_type=? WHERE id=?',(body.state.stage,body.state.lifecycle_type,sid))
            result = wire(c.execute('SELECT * FROM lifecycle_events WHERE id=?',(ident,)).fetchone())
        return result

    @router.get('/spaces/{sid}/lifecycle/events')
    def events(sid: str, after_seq: int = 0, uid: str = Depends(user)):
        with db() as c:
            space(c, uid, sid)
            return {'events':[wire(r) for r in c.execute('SELECT * FROM lifecycle_events WHERE space_id=? AND server_seq>? ORDER BY server_seq LIMIT 500',(sid,after_seq))]}

    return router
