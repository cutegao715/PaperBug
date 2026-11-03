"""Gateway Event Router: classify + dispatch, no business logic.

Routes each AssistantEvent to the right runtime:
- user_message  -> Interactive Runtime (agent loop) - handled by the
  caller (server), which owns run threads; the router classifies it
- everything scheduled/checked -> Proactive Runtime handlers injected
  at construction (radar sweep, scheduler tick, digest)
"""
from __future__ import annotations

from typing import Callable, Optional

from paperagent.gateway.events import AssistantEvent
from paperagent.gateway.session_router import SessionRouter


class EventRouter:
    def __init__(self, *, radar_run: Optional[Callable[[dict], dict]] = None,
                 scheduler_tick: Optional[Callable[[], list[str]]] = None,
                 digest_build: Optional[Callable[[], dict]] = None,
                 project_resolver=None,
                 session_router: Optional[SessionRouter] = None,
                 event_log=None):
        self._radar_run = radar_run
        self._scheduler_tick = scheduler_tick
        self._digest_build = digest_build
        self._project_resolver = project_resolver
        self._session_router = session_router or SessionRouter()
        self._event_log = event_log

    def route(self, event: AssistantEvent) -> dict:
        """Returns {runtime, handled, result} for observability."""
        if self._event_log is not None and self._event_log.seen(event.event_id):
            return {"runtime": "gateway", "handled": True, "duplicate": True,
                    "event_id": event.event_id,
                    "note": "duplicate event ignored"}

        resolution = self._resolve(event)

        if resolution.get("error"):
            result = {"runtime": "gateway", "handled": False,
                      "event_id": event.event_id,
                      "resolution": resolution,
                      "note": resolution["error"]}
            self._record(event, result)
            return result

        if event.type == "user_message":
            result = {"runtime": "interactive", "handled": False,
                      "event_id": event.event_id,
                      "resolution": resolution,
                      "note": "caller starts the agent run"}
            self._record(event, result)
            return result

        if event.type in ("schedule_fire", "deadline_check"):
            if self._scheduler_tick is None:
                result = {"runtime": "proactive", "handled": False,
                          "event_id": event.event_id,
                          "resolution": resolution,
                          "note": "no scheduler bound"}
                self._record(event, result)
                return result
            log = self._scheduler_tick()
            result = {"runtime": "proactive", "handled": True,
                      "event_id": event.event_id,
                      "resolution": resolution, "log": log}
            self._record(event, result)
            return result

        if event.type == "radar_run":
            if self._radar_run is None:
                result = {"runtime": "proactive", "handled": False,
                          "event_id": event.event_id,
                          "resolution": resolution,
                          "note": "no radar bound"}
                self._record(event, result)
                return result
            result = self._radar_run(event.payload or {})
            out = {"runtime": "proactive", "handled": True,
                   "event_id": event.event_id,
                   "resolution": resolution, "result": result}
            self._record(event, out)
            return out

        if event.type == "digest_request":
            if self._digest_build is None:
                result = {"runtime": "proactive", "handled": False,
                          "event_id": event.event_id,
                          "resolution": resolution,
                          "note": "no digest builder bound"}
                self._record(event, result)
                return result
            result = self._digest_build()
            out = {"runtime": "proactive", "handled": True,
                   "event_id": event.event_id,
                   "resolution": resolution, "result": result}
            self._record(event, out)
            return out

        if event.type == "connector_event":
            result = {"runtime": "proactive", "handled": False,
                      "event_id": event.event_id,
                      "resolution": resolution,
                      "note": "caller handles normalized connector payload"}
            self._record(event, result)
            return result

        result = {"runtime": "unknown", "handled": False,
                  "event_id": event.event_id,
                  "resolution": resolution,
                  "note": f"unrouted event type: {event.type}"}
        self._record(event, result)
        return result

    def _resolve(self, event: AssistantEvent) -> dict:
        project = (self._project_resolver.resolve(event)
                   if self._project_resolver is not None
                   else {"project_id": event.project_id, "project_exists": None})
        if project.get("project_id") and project.get("project_exists") is False:
            return {**project, "error": f"project not found: {project['project_id']}"}
        session = self._session_router.resolve(event, project.get("project_id"))
        return {**project, **session}

    def _record(self, event: AssistantEvent, result: dict) -> None:
        if self._event_log is not None:
            self._event_log.record(event, result)
