"""Local explicit binding controls. No cloud credentials are accepted or returned here."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from paperagent.tasks.cloud_sync import CloudSync
from paperagent.domain.lifecycle import LifecycleService


class BindBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cloud_url: str
    space_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    device_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    challenge: str | None = None
    confirmed: bool = False


class ModeBody(BaseModel):
    sync_mode: Literal['local_only','cloud_sync']
    confirmed: bool = False


class PreferencesBody(BaseModel):
    intents: bool = False
    profile_intents: bool = False
    notifications: bool = False
    confirmed: bool = False


class CloudTaskBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: str = Field(min_length=1,max_length=12000)
    relative: str = Field(min_length=1,max_length=1000)
    actions: list[str] = Field(default_factory=lambda:['read_pdf','verify_citations','search_local'])
    confirmed: bool = False
    include_memories: bool = False
    ttl: int = Field(default=86400,ge=60,le=2592000)
    interval_seconds: int | None = None


class CloudMonitorBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    topic:str=Field(min_length=2,max_length=500)
    confirmed:bool=False
    interval_seconds:int=Field(default=86400,ge=3600,le=2592000)
    sources:list[str]=Field(default_factory=lambda:['arxiv_rss'])


def control_router(service):
    router = APIRouter()
    from paperagent.tasks.cloud_tasks import CloudTasks

    @router.post('/projects/{project_id}/cloud-monitors')
    def monitor(project_id:str,body:CloudMonitorBody):
        return run(lambda:CloudTasks(service._ws(project_id)).monitor(**body.model_dump()))

    @router.delete('/projects/{project_id}/cloud-monitors/{monitor_id}')
    def stop_monitor(project_id:str,monitor_id:str):
        return run(lambda:CloudTasks(service._ws(project_id)).stop_monitor(monitor_id))

    @router.post('/projects/{project_id}/cloud-tasks')
    def start_task(project_id: str, body: CloudTaskBody):
        return run(lambda:CloudTasks(service._ws(project_id)).start(**body.model_dump()))

    @router.get('/projects/{project_id}/cloud-tasks')
    def task_list(project_id: str):
        from paperagent.workspace.state_store import read_json
        state=read_json(service._ws(project_id).dir/'cloud_tasks.json')
        return {'enabled':state.get('enabled',False),'tasks':state.get('tasks',{}),
                'schedules':state.get('schedules',{}),'grants':state.get('grants',{}),'monitors':state.get('monitors',{})}

    @router.post('/projects/{project_id}/cloud-tasks/poll')
    def poll_task(project_id: str):
        return run(lambda:CloudTasks(service._ws(project_id)).poll())

    @router.post('/projects/{project_id}/cloud-tasks/enabled')
    def enable_task(project_id: str, enabled: bool):
        return CloudTasks(service._ws(project_id)).set_enabled(enabled)

    @router.get('/projects/{project_id}/cloud-tasks/{task_id}')
    def task_status(project_id: str,task_id: str):
        return run(lambda:CloudTasks(service._ws(project_id)).status(task_id))

    @router.post('/projects/{project_id}/cloud-tasks/{task_id}/cancel')
    def cancel_task(project_id: str,task_id: str):
        return run(lambda:CloudTasks(service._ws(project_id)).cancel(task_id))

    @router.get('/projects/{project_id}/cloud-proposals')
    def proposals(project_id: str):
        return {'proposals':CloudTasks(service._ws(project_id)).executor.proposals()}

    @router.post('/projects/{project_id}/cloud-proposals/{operation_id}/undo')
    def undo_proposal(project_id: str,operation_id: str,confirmed: bool=False):
        return run(lambda:CloudTasks(service._ws(project_id)).executor.undo(operation_id,confirmed))

    @router.post('/projects/{project_id}/cloud-proposals/{operation_id}/apply')
    def apply_proposal(project_id: str,operation_id: str,confirmed: bool=False):
        return run(lambda:CloudTasks(service._ws(project_id)).executor.apply(operation_id,confirmed))

    @router.delete('/projects/{project_id}/cloud-grants/{grant_id}')
    def revoke_grant(project_id: str,grant_id: str):
        return run(lambda:CloudTasks(service._ws(project_id)).revoke(grant_id))

    def run(fn):
        try:
            return fn()
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(422, '同步设置或连接无效，请检查绑定、云地址和凭据') from exc

    @router.get('/projects/{project_id}/sync')
    def settings(project_id: str):
        ws = service._ws(project_id)
        binding = run(lambda: CloudSync(ws).metadata())
        lifecycle = LifecycleService(ws)
        state = lifecycle.get()
        from paperagent.workspace.state_store import read_json
        relay=read_json(ws.dir/'proactive_sync.json')
        relay_status={'pending':sum(n.get('status')=='pending' for n in relay.get('notifications',{}).values()),'next_retry':relay.get('next_retry',0)}
        return {'binding':binding,'proactive_sync':relay_status,'pending':len(lifecycle.pending_events()),
                'history':state.history if state else [], 'execution_mode':'local'}

    @router.post('/projects/{project_id}/sync/bind')
    def bind(project_id: str, body: BindBody):
        sync = CloudSync(service._ws(project_id))
        if body.challenge is None:
            return run(lambda: sync.challenge(body.cloud_url,body.space_id,body.device_id))
        return run(lambda: sync.bind(body.cloud_url,body.space_id,body.device_id,body.challenge,body.confirmed))

    @router.post('/projects/{project_id}/sync/mode')
    def mode(project_id: str, body: ModeBody):
        return run(lambda: CloudSync(service._ws(project_id)).set_mode(body.sync_mode,body.confirmed))

    @router.post('/projects/{project_id}/sync/preferences')
    def preferences(project_id: str, body: PreferencesBody):
        return run(lambda: CloudSync(service._ws(project_id)).set_preferences(**body.model_dump()))

    @router.post('/projects/{project_id}/sync/run')
    def sync_now(project_id: str):
        ws = service._ws(project_id)
        result = run(lambda: CloudSync(ws).sync())
        from paperagent.tasks.proactive_sync import sync_project
        result['proactive_sync'] = run(lambda: sync_project(ws))
        return result

    return router
