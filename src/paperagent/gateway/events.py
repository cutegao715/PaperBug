"""AssistantEvent: one schema for everything entering the system.

User messages, UI actions, scheduled fires, deadline checks, radar
sweeps - all become events routed through the Gateway. The chat
message is just one event type among many (V2 principle #4).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def _now() -> str:
    from paperagent.domain.entities import iso, utcnow
    return iso(utcnow())


class AssistantEvent(BaseModel):
    event_id: str
    type: Literal[
        "user_message",      # interactive: user typed something
        "ui_action",         # e.g. marked a task done
        "schedule_fire",     # scheduler tick found due work
        "deadline_check",    # deadline reminder evaluation
        "radar_run",         # manual or scheduled radar sweep
        "digest_request",    # daily digest build
        "connector_event",   # normalized external connector payload
    ]
    source: str = "api"  # api | scheduler | cli | connector
    user_id: str = "local"
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
