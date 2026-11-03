"""Versioned conversation routes."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict


class AuthorizationBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    approved:bool
    allow_write:bool=False
    include_personal:bool=False
    include_external:bool=False
    allow_mcp:bool=False



def control_router(service):
    router = APIRouter()
    router.add_api_route('/projects/{project_id}/messages', service.send_message, methods=['POST'])
    router.add_api_route('/runs/{run_id}/cancel', service.cancel_run, methods=['POST'])
    router.add_api_route('/runs/{run_id}/events', service.run_events, methods=['GET'])
    @router.post('/projects/{project_id}/messages/authorizations/{authorization_id}')
    def authorize(project_id:str,authorization_id:str,body:AuthorizationBody):
        from paperagent.tasks.cloud_conversation import CloudConversation
        from paperagent.workspace.state_store import state_lock
        cc=CloudConversation(service._ws(project_id))
        try:
            with state_lock(cc.path):
                if not body.approved: return cc.reject(authorization_id)
                row=cc.approve(authorization_id,body.allow_write,body.include_personal,body.include_external,body.allow_mcp)
                if row['local_run_id'] in service.RUNS:
                    run=service.RUNS[row['local_run_id']]
                    return {'run_id':run.run_id,'mode':'cloud','execution_owner':'cloud','status':run.status,'task_id':run.task_id,'conversation_id':None if run.task_id else 'main'}
                if row.get('started'):
                    raise HTTPException(409,'本轮已启动；如服务重启，请通过新消息继续，避免重复执行文件操作')
                cc.update(authorization_id,started=True,status='running')
                try:
                    return service._send_message(project_id,service.SendMessage(**{**row['request'],'mode':'cloud'}),authorization=row)
                except Exception:
                    cc.update(authorization_id,started=False,status='approved')
                    raise
        except (ValueError,RuntimeError,PermissionError,OSError) as exc:
            raise HTTPException(409,'授权或云连接无效，请检查账户、连接和请求状态') from exc

    @router.delete('/projects/{project_id}/messages/authorizations/{authorization_id}')
    def revoke(project_id:str,authorization_id:str):
        from paperagent.tasks.cloud_conversation import CloudConversation
        cc=CloudConversation(service._ws(project_id))
        try:
            row=cc.row(authorization_id)
            run=service.RUNS.get(row['local_run_id'])
            if run: run.cancel_event.set()
            return cc.revoke(authorization_id)
        except (ValueError,RuntimeError,PermissionError,OSError) as exc:
            raise HTTPException(409,'本地已停止授权，云端撤销未确认；请检查连接') from exc
    return router
