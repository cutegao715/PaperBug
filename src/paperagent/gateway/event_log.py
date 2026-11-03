"""Gateway event log: minimal deduplication and audit trail."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paperagent.gateway.events import AssistantEvent


class EventLog:
    def __init__(self, root: Path):
        self.path = Path(root) / "domain" / "gateway_events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def seen(self, event_id: str) -> bool:
        if not self.path.exists():
            return False
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                if json.loads(line).get("event_id") == event_id:
                    return True
            except json.JSONDecodeError:
                continue
        return False

    def record(self, event: AssistantEvent, route_result: dict[str, Any]) -> None:
        item = {
            "event_id": event.event_id,
            "type": event.type,
            "source": event.source,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "project_id": event.project_id,
            "created_at": event.created_at,
            "runtime": route_result.get("runtime"),
            "handled": route_result.get("handled"),
            "note": route_result.get("note"),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
