"""Session routing helpers for Gateway events."""
from __future__ import annotations

from paperagent.gateway.events import AssistantEvent


class SessionRouter:
    def resolve(self, event: AssistantEvent, project_id: str | None = None) -> dict:
        session_id = event.session_id or event.payload.get("session_id")
        if not session_id:
            base = project_id or event.user_id or "local"
            session_id = f"{base}:{event.type}"
        return {"session_id": session_id}
