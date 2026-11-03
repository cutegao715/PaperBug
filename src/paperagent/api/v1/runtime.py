"""Versioned runtime routes."""
from fastapi import APIRouter, HTTPException


def control_router(service):
    router = APIRouter()
    router.add_api_route('/config', service.config, methods=['GET'])
    router.add_api_route('/today', service.today, methods=['GET'])
    router.add_api_route('/tasks', service.list_tasks, methods=['GET'])
    router.add_api_route('/tasks', service.create_task, methods=['POST'])
    router.add_api_route('/tasks/{task_id}/status', service.set_task_status, methods=['POST'])
    router.add_api_route('/deadlines', service.list_deadlines, methods=['GET'])
    router.add_api_route('/deadlines', service.create_deadline, methods=['POST'])
    router.add_api_route('/standing-intents', service.list_intents, methods=['GET'])
    router.add_api_route('/standing-intents', service.create_intent, methods=['POST'])
    router.add_api_route('/radar/hits', service.radar_hits, methods=['GET'])
    router.add_api_route('/opportunities', service.opportunities, methods=['GET'])
    router.add_api_route('/opportunities/extract', service.extract_opportunity, methods=['POST'])
    router.add_api_route('/radar/run', service.radar_run, methods=['POST'])
    router.add_api_route('/scheduler/tick', service.scheduler_tick, methods=['POST'])
    router.add_api_route('/notifications', service.notifications, methods=['GET'])
    router.add_api_route('/notifications', service.create_notification, methods=['POST'])
    router.add_api_route('/notifications/{notification_id}/read', service.read_notification, methods=['POST'])
    router.add_api_route('/notifications/{notification_id}/dismiss', service.dismiss_notification, methods=['POST'])
    router.add_api_route('/profile', service.get_profile, methods=['GET'])
    router.add_api_route('/profile', service.set_profile, methods=['POST'])
    router.add_api_route('/memories', service.list_memories, methods=['GET'])
    router.add_api_route('/memories', service.create_memory, methods=['POST'])
    router.add_api_route('/memories/{memory_id}', service.replace_memory, methods=['PATCH'])
    router.add_api_route('/memories/{memory_id}', service.forget_memory, methods=['DELETE'])
    router.add_api_route('/automations', service.list_automations, methods=['GET'])
    router.add_api_route('/automations', service.create_automation, methods=['POST'])
    router.add_api_route('/watchers', service.list_watchers, methods=['GET'])
    router.add_api_route('/watchers', service.create_watcher, methods=['POST'])
    router.add_api_route('/watchers/{watcher_id}/ingest', service.ingest_watcher_change, methods=['POST'])
    @router.patch('/standing-intents/{intent_id}')
    def update_intent(intent_id: str, body: dict):
        try:
            result=service._store().update_standing_intent(intent_id,**body)
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        if result is None: raise HTTPException(404,'Intent not found')
        return result.model_dump()

    @router.delete('/standing-intents/{intent_id}')
    def delete_intent(intent_id: str):
        if not service._store().delete_standing_intent(intent_id): raise HTTPException(404,'Intent not found')
        return {'deleted':True}
    return router
