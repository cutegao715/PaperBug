"""User-isolated intent replication and durable in-app notification relay."""
import json
import time
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field
from paperagent_cloud.sync_api import Binding, Strict
from paperagent.domain.intent_sync import IntentEnvelope, rank


def migrate(c):
    c.executescript('''
      CREATE TABLE IF NOT EXISTS sync_preferences(space_id TEXT NOT NULL,device_id TEXT NOT NULL,project_id TEXT NOT NULL,
        intents INTEGER NOT NULL DEFAULT 0,profile_intents INTEGER NOT NULL DEFAULT 0,notifications INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(space_id,device_id,project_id));
      CREATE TABLE IF NOT EXISTS standing_intents(user_id TEXT NOT NULL,id TEXT NOT NULL,space_id TEXT NOT NULL,
        scope TEXT NOT NULL,payload TEXT NOT NULL,updated INTEGER NOT NULL,PRIMARY KEY(user_id,id));
      CREATE TABLE IF NOT EXISTS relayed_notifications(user_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,
        space_id TEXT NOT NULL,target_device TEXT,payload TEXT NOT NULL,created INTEGER NOT NULL,
        PRIMARY KEY(user_id,idempotency_key));
    ''')


class Preferences(Binding):
    intents: bool = False
    profile_intents: bool = False
    notifications: bool = False
    confirmed: bool = False


class Batch(Binding):
    items: list[IntentEnvelope] = Field(max_length=100)


class Relay(Binding):
    space_id: str
    idempotency_key: str = Field(min_length=8,max_length=200)
    target_device: str | None = None
    title: str = Field(max_length=2000)
    body: str = Field(default='',max_length=20000)
    type: str = Field(default='system',max_length=50)


def build(service):
    router=APIRouter()
    db,user=service.db,service.current_user

    def bound(c,uid,sid,device,project,token,enabled=True):
        service.require_space(c,uid,sid)
        service.verify_device(c,{'device_id':device},uid,token)
        if not c.execute('SELECT 1 FROM space_bindings WHERE space_id=? AND device_id=? AND project_id=?',(sid,device,project)).fetchone():
            raise HTTPException(403,'绑定不存在')
        if enabled and c.execute('SELECT sync_mode FROM spaces WHERE id=?',(sid,)).fetchone()[0]!='cloud_sync':
            raise HTTPException(403,'同步关闭')
        return c.execute('SELECT * FROM sync_preferences WHERE space_id=? AND device_id=? AND project_id=?',(sid,device,project)).fetchone()

    @router.post('/spaces/{sid}/sync/preferences')
    def preferences(sid: str,body: Preferences,uid: str=Depends(user),x_device_token: str=Header(default='')):
        if (body.intents or body.profile_intents or body.notifications) and not body.confirmed:
            raise HTTPException(422,'需明确确认同步范围')
        if body.profile_intents and not body.intents:
            raise HTTPException(422,'需先启用意图同步')
        with db() as c:
            bound(c,uid,sid,body.device_id,body.project_id,x_device_token,enabled=False)
            c.execute('INSERT OR REPLACE INTO sync_preferences VALUES(?,?,?,?,?,?)',(sid,body.device_id,body.project_id,body.intents,body.profile_intents,body.notifications))
        return body.model_dump(exclude={'confirmed','device_id','project_id'})

    @router.post('/spaces/{sid}/intents')
    def push(sid: str,body: Batch,uid: str=Depends(user),x_device_token: str=Header(default='')):
        result=[]
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            pref=bound(c,uid,sid,body.device_id,body.project_id,x_device_token)
            if not pref or not pref['intents']: raise HTTPException(403,'意图同步未授权')
            for item in body.items:
                incoming=item.model_dump()
                scope='profile' if item.payload['scope'].get('profile') else 'project'
                if scope=='profile' and not pref['profile_intents']: raise HTTPException(403,'全局意图未授权')
                existing=c.execute('SELECT * FROM standing_intents WHERE user_id=? AND id=?',(uid,item.id)).fetchone()
                if existing and (existing['scope']!=scope or (scope=='project' and existing['space_id']!=sid)):
                    raise HTTPException(409,'意图不可跨空间改绑')
                old=json.loads(existing['payload']) if existing else None
                winner=incoming if old is None or rank(incoming)>rank(old) else old
                if winner is incoming:
                    c.execute('INSERT OR REPLACE INTO standing_intents VALUES(?,?,?,?,?,?)',(uid,item.id,sid,scope,json.dumps(incoming,sort_keys=True),item.updated))
                result.append(winner)
        return {'items':result}

    @router.get('/spaces/{sid}/intents')
    def pull(sid: str,device_id: str,project_id: str,after: str='',uid: str=Depends(user),x_device_token: str=Header(default='')):
        with db() as c:
            pref=bound(c,uid,sid,device_id,project_id,x_device_token)
            if not pref or not pref['intents']: raise HTTPException(403,'意图同步未授权')
            rows=c.execute("SELECT * FROM standing_intents WHERE user_id=? AND id>? AND ((scope='project' AND space_id=?) OR (scope='profile' AND ?)) ORDER BY id LIMIT 100",(uid,after,sid,pref['profile_intents'])).fetchall()
            return {'items':[json.loads(r['payload']) for r in rows], 'next':rows[-1]['id'] if len(rows)==100 else None}

    @router.post('/notifications/relay')
    def relay(body: Relay,uid: str=Depends(user),x_device_token: str=Header(default='')):
        payload=json.dumps(body.model_dump(),sort_keys=True,ensure_ascii=False)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            pref=bound(c,uid,body.space_id,body.device_id,body.project_id,x_device_token)
            if not pref or not pref['notifications']: raise HTTPException(403,'通知转发未授权')
            if body.target_device and not c.execute("SELECT 1 FROM devices WHERE id=? AND user_id=? AND status='active'",(body.target_device,uid)).fetchone():
                raise HTTPException(404,'目标设备不存在')
            old=c.execute('SELECT payload FROM relayed_notifications WHERE user_id=? AND idempotency_key=?',(uid,body.idempotency_key)).fetchone()
            if old and old['payload']!=payload: raise HTTPException(409,'通知幂等键冲突')
            if not old:
                c.execute('INSERT INTO relayed_notifications VALUES(?,?,?,?,?,?)',(uid,body.idempotency_key,body.space_id,body.target_device,payload,int(time.time())))
        return {'idempotency_key':body.idempotency_key,'status':'delivered'}

    @router.get('/notifications/relayed')
    def inbox(device_id: str,uid: str=Depends(user),x_device_token: str=Header(default='')):
        with db() as c:
            service.verify_device(c,{'device_id':device_id},uid,x_device_token)
            return {'items':[json.loads(r[0]) for r in c.execute('SELECT payload FROM relayed_notifications WHERE user_id=? AND (target_device IS NULL OR target_device=?) ORDER BY created DESC,rowid DESC LIMIT 100',(uid,device_id))]}
    return router
