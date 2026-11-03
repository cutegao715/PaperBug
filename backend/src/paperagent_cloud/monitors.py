"""Public-source monitoring. Private project files never enter this fetch path."""
import hashlib
import json
import time
import uuid
from datetime import datetime,timezone,timedelta
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field


def app_module():
    from paperagent_cloud import app
    return app


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS control_monitors(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,space_id TEXT NOT NULL,
      topic TEXT NOT NULL,sources TEXT NOT NULL,interval_seconds INTEGER NOT NULL,next_run REAL NOT NULL,
      enabled INTEGER NOT NULL,state TEXT NOT NULL DEFAULT '{}',lease TEXT,lease_until REAL NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS control_monitor_hits(monitor_id TEXT NOT NULL,key TEXT NOT NULL,PRIMARY KEY(monitor_id,key));
    CREATE TABLE IF NOT EXISTS control_public_cache(key TEXT PRIMARY KEY,result TEXT NOT NULL,expires REAL NOT NULL);
    ''')


class MonitorBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    topic:str=Field(min_length=2,max_length=500)
    sources:list[Literal['arxiv_rss','openalex_incremental','literature_search']]=Field(default_factory=lambda:['arxiv_rss'],min_length=1,max_length=3)
    interval_seconds:int=Field(default=86400,ge=3600,le=2592000)
    confirmed:bool=False


def build(app):
    router=APIRouter(prefix='/control')
    @router.post('/spaces/{space_id}/monitors',status_code=201)
    def create(space_id:str,body:MonitorBody,user_id:str=Depends(app.current_user)):
        if not body.confirmed: raise HTTPException(422,'Confirm the public-source query and schedule')
        mid=uuid.uuid4().hex
        with app.db() as c:
            app.require_space(c,user_id,space_id)
            c.execute('INSERT INTO control_monitors(id,user_id,space_id,topic,sources,interval_seconds,next_run,enabled) VALUES(?,?,?,?,?,?,?,1)',
                      (mid,user_id,space_id,body.topic,json.dumps(list(dict.fromkeys(body.sources))),body.interval_seconds,time.time()))
        return {'id':mid,'enabled':True}

    @router.get('/spaces/{space_id}/monitors')
    def listing(space_id:str,user_id:str=Depends(app.current_user)):
        with app.db() as c:
            app.require_space(c,user_id,space_id)
            return {'monitors':[dict(r) for r in c.execute('SELECT id,topic,sources,interval_seconds,next_run,enabled,state FROM control_monitors WHERE user_id=? AND space_id=?',(user_id,space_id))]}

    @router.get('/spaces/{space_id}/monitor-updates')
    def updates(space_id:str,after:int=0,user_id:str=Depends(app.current_user)):
        with app.db() as c:
            app.require_space(c,user_id,space_id)
            rows=c.execute("SELECT rowid,idempotency_key,payload FROM relayed_notifications WHERE user_id=? AND space_id=? AND idempotency_key LIKE 'monitor:%' AND rowid>? ORDER BY rowid LIMIT 100",(user_id,space_id,after)).fetchall()
            return {'items':[json.loads(r['payload'])|{'id':r['idempotency_key']} for r in rows], 'next':rows[-1]['rowid'] if rows else after}

    @router.delete('/monitors/{monitor_id}')
    def disable(monitor_id:str,user_id:str=Depends(app.current_user)):
        with app.db() as c:
            if not c.execute('UPDATE control_monitors SET enabled=0,lease=NULL WHERE id=? AND user_id=?',(monitor_id,user_id)).rowcount: raise HTTPException(404,'Monitor not found')
        return {'enabled':False}
    return router


def fetch_source(name,topic,since,cursor):
    from paperagent.radar.sources import ArxivRSSSource,OpenAlexSource,LiteratureSearchSource
    return {'arxiv_rss':ArxivRSSSource,'openalex_incremental':OpenAlexSource,'literature_search':LiteratureSearchSource}[name]().fetch(topic,since,cursor)


def tick():
    app=app_module();now=time.time();lease=uuid.uuid4().hex
    with app.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM control_monitors WHERE enabled=1 AND next_run<=? AND lease_until<=? ORDER BY next_run LIMIT 1',(now,now)).fetchone()
        if not row: c.commit();return 0
        c.execute('UPDATE control_monitors SET lease=?,lease_until=? WHERE id=?',(lease,now+300,row['id']));c.commit()
    state=json.loads(row['state']);items=[];today=datetime.now(timezone.utc).date()
    from paperagent_cloud.usage import reserve,settle
    usage_key='monitor:'+row['id']+':'+str(row['next_run'])
    try:
        reserve(row['user_id'],usage_key,{'radar_runs':1})
        for name in json.loads(row['sources']):
            previous=state.get(name,{})
            since=previous.get('since',(today-timedelta(days=30)).isoformat())
            query=[name,row['topic'],since,previous.get('cursor')]
            # This cache contains only public provider metadata, never memory or document content.
            key=hashlib.sha256(json.dumps(query).encode()).hexdigest()
            with app.db() as c: cache=c.execute('SELECT result FROM control_public_cache WHERE key=? AND expires>?',(key,time.time())).fetchone()
            try:
                if cache:
                    data=json.loads(cache['result'])
                    from paperagent.radar.sources import SourceBatch
                    batch=SourceBatch(**data)
                else:
                    batch=fetch_source(*query)
                    from dataclasses import asdict
                    with app.db() as c:
                        c.execute('DELETE FROM control_public_cache WHERE expires<=?',(time.time(),))
                        c.execute('INSERT OR REPLACE INTO control_public_cache VALUES(?,?,?)',(key,json.dumps(asdict(batch)),time.time()+900))
                items.extend(batch.items[:100])
                state[name]={'cursor':batch.next_cursor,'since':since if batch.next_cursor else (today-timedelta(days=7)).isoformat(),'coverage':batch.coverage}
            except Exception as exc:
                state[name]=previous|{'error':type(exc).__name__}
        settle(row['user_id'],usage_key,{'radar_runs':1})
    except HTTPException:
        state['quota']='deferred'
    with app.db() as c:
        c.execute('BEGIN IMMEDIATE')
        active=c.execute('SELECT 1 FROM control_monitors WHERE id=? AND lease=? AND enabled=1 AND lease_until>?',(row['id'],lease,time.time())).fetchone()
        if not active: c.rollback();return 1
        app.require_space(c,row['user_id'],row['space_id'])
        from paperagent.domain.entities import radar_dedup_key
        from paperagent.radar.pipeline import _rule_match
        fresh=[]
        for item in items:
            title=item.get('title') or ''
            if not title or title.startswith('[SYNTHETIC STUB') or not _rule_match(row['topic'],title): continue
            key=radar_dedup_key(title,item.get('doi'))
            if c.execute('INSERT OR IGNORE INTO control_monitor_hits VALUES(?,?)',(row['id'],key)).rowcount: fresh.append(item)
        if fresh:
            note={'type':'radar','title':f"Research Radar: {len(fresh)} new candidates",'body':'\n'.join((i['title'][:300]+' '+str(i.get('url') or '')) for i in fresh[:10]),'monitor_id':row['id']}
            c.execute('INSERT OR IGNORE INTO relayed_notifications VALUES(?,?,?,?,?,?)',(row['user_id'],'monitor:'+row['id']+':'+str(row['next_run']),row['space_id'],None,json.dumps(note,ensure_ascii=False),int(time.time())))
        c.execute('UPDATE control_monitors SET state=?,lease=NULL,lease_until=0,next_run=? WHERE id=?',(json.dumps(state),time.time()+row['interval_seconds'],row['id']))
        c.commit()
    return 1
