"""Connector contracts."""
from __future__ import annotations

from typing import Any, Protocol

from paperagent.gateway.events import AssistantEvent


class Connector(Protocol):
    connector_id: str

    def normalize(self, payload: dict[str, Any]):
        ...

    def to_event(self, normalized, *, project_id: str | None = None) -> AssistantEvent:
        ...
