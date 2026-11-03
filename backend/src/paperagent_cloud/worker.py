"""One bounded job step per tick; persisted leases fence obsolete workers."""
import json
import time
from fastapi import HTTPException
from paperagent_cloud import control


def tick():
    control.schedule_due()
    from paperagent_cloud.monitors import tick as monitor_tick
    try: monitor_tick()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Public monitor deferred; task queue continues")
    from paperagent_cloud.conversation_api import tick as chat_tick
    if chat_tick(): return {'processed':1,'kind':'conversation'}
    job=control.claim_job()
    if not job: return {'processed':0}
    app=control.app_module();tid=job['task_id'];token=job['lease_token']
    try:
        result=app.advance_task(tid,app.AgentAdvanceInput(**json.loads(job['input_json'])),job['user_id'],token)
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM control_jobs WHERE task_id=?',(tid,)).fetchone()
            if row['lease_token']!=token or row['status']!='leased': c.rollback();return {'processed':1,'stale':True}
            current=c.execute('SELECT status FROM agent_tasks WHERE id=?',(tid,)).fetchone()[0]
            status={'succeeded':'done','waiting_local':'waiting','cancelled':'cancelled','failed':'failed'}.get(current,'ready')
            c.execute('UPDATE control_jobs SET status=?,lease_token=NULL,lease_until=0,attempts=0 WHERE task_id=?',(status,tid))
            control.event(c,tid,result['status'])
            c.commit()
        return {'processed':1,'status':status}
    except Exception as exc:
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM control_jobs WHERE task_id=?',(tid,)).fetchone()
            if row['lease_token']==token and row['status']=='leased' and row['lease_until']>time.time():
                terminal=isinstance(exc,HTTPException) and exc.status_code in (403,404,409,422)
                attempts=row['attempts']+1
                if terminal: control.cancel(c,tid)
                else:
                    state='failed' if attempts>=5 else 'ready'
                    c.execute('UPDATE control_jobs SET status=?,attempts=?,next_attempt=?,lease_token=NULL,last_error=? WHERE task_id=?',
                              (state,attempts,time.time()+min(3600,30*2**attempts),type(exc).__name__,tid))
                    c.execute('UPDATE agent_tasks SET status=? WHERE id=?',('failed' if state=='failed' else 'queued',tid))
                    control.event(c,tid,'retry' if state=='ready' else 'failed',{'attempts':attempts})
            c.commit()
        return {'processed':1,'status':'deferred'}


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');args=p.parse_args()
    app=control.app_module();app.init_db()
    while True:
        result=tick()
        if args.once: print(json.dumps(result));return
        if not result['processed']: time.sleep(2)


if __name__=='__main__': main()
