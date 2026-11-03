"""Resolve Gateway events to workspace projects."""
from __future__ import annotations

from pathlib import Path

from paperagent.gateway.events import AssistantEvent


class ProjectResolver:
    def __init__(self, root: Path):
        self.root = Path(root)

    def resolve(self, event: AssistantEvent) -> dict:
        project_id = event.project_id or event.payload.get("project_id")
        if not project_id:
            return {"project_id": None, "project_exists": None}
        project_path = self.root / "projects" / project_id
        return {
            "project_id": project_id,
            "project_exists": project_path.exists(),
            "project_path": str(project_path),
        }
