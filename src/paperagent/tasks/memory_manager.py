"""Scoped, attributable memory. Domain state remains authoritative for facts."""
from __future__ import annotations

import json
import math
from collections import Counter
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from paperagent.domain.entities import MemoryRecord, iso, utcnow
from paperagent.workspace.state_store import state_lock

LAYER_LIMITS = {"working": 8, "project": 8, "user": 8, "episodic": 8, "prospective": 10}


def tokens(text: str) -> set[str]:
    result = set(re.findall(r"[a-z0-9_]{2,}", text.lower()))
    for chunk in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return result


class MemoryManager:
    def __init__(self, store):
        self.store = store

    def remember(self, layer: str, content: str, *, scope: dict[str, Any] | None = None,
                 tags: list[str] | None = None, importance: int = 2, ref: str | None = None,
                 kind: str = "note", source: dict | None = None,
                 confirmation: str = "provided", pinned: bool = False,
                 expires_at=None, trust="model_transient") -> MemoryRecord:
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        memory = MemoryRecord(memory_id=f"mem_{uuid.uuid4().hex[:16]}", layer=layer,
                              content=content.strip(), scope=scope or {}, tags=tags or [],
                              importance=importance, ref=ref, kind=kind,
                              source=source or {"type": "manual"}, confirmation=confirmation,
                              pinned=pinned, expires_at=expires_at, trust=trust)
        with state_lock(self.store._path("memories")):
            self.store._append("memories", memory)
        return memory

    def replace(self, memory_id: str, content: str, *, source: dict | None = None,
                confirmation: str = "provided", actor: str = "model") -> MemoryRecord:
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        with state_lock(self.store._path("memories")):
            items = self.store.list_memories(limit=None)
            old = next((m for m in items if m.memory_id == memory_id), None)
            if old is None:
                raise KeyError(memory_id)
            if old.status != "active":
                raise ValueError("Only active memories can be replaced")
            if old.trust == "user_adopted" and actor != "user":
                raise ValueError("Only the user can replace adopted memory")
            data = old.model_dump()
            data.update(memory_id=f"mem_{uuid.uuid4().hex[:16]}", content=content.strip(),
                        source=source or {"type": "manual"}, confirmation=confirmation,
                        supersedes=old.memory_id, replaced_by=None, created_at=iso(utcnow()),
                        updated_at=iso(utcnow()))
            new = MemoryRecord.model_validate(data)
            old.status, old.replaced_by, old.updated_at = "superseded", new.memory_id, iso(utcnow())
            self.store._rewrite("memories", [*items, new])
            return new

    def sync_adopted_record(self, project_id: str, namespace: str, row: dict):
        """Mirror explicit domain adoption, keyed by source; never infer model approval."""
        ref = f"{namespace}:{row['record_id']}"
        with state_lock(self.store._path("memories")):
            items = self.store.list_memories(limit=None)
            linked = [m for m in items if m.ref == ref and m.scope.get("project_id") == project_id]
            latest = max((m.source.get("record_revision", 0) for m in linked), default=0)
            if row["revision"] <= latest:
                return
            existing = next((m for m in linked if m.status == "active"), None)
            if row["status"] != "confirmed":
                if existing:
                    existing.status = "deleted"
                # Persist a revision tombstone even if no confirmed memory existed.
                tombstone = MemoryRecord(memory_id=f"mem_{uuid.uuid4().hex[:16]}", layer="project",
                    content="Domain adoption withdrawn", status="deleted", ref=ref,
                    scope={"project_id": project_id}, source={"type": "domain_record",
                    "namespace": namespace, "record_revision": row["revision"]})
                self.store._rewrite("memories", [*items, tombstone])
                return
            content = json.dumps({k: row[k] for k in ("title", "kind", "content", "term", "argument", "venue_rule")
                                  if row.get(k)}, ensure_ascii=False)
            memory = MemoryRecord(memory_id=f"mem_{uuid.uuid4().hex[:16]}", layer="project", content=content,
                scope={"project_id": project_id}, ref=ref, trust="user_adopted", confirmation="provided",
                source={"type": "domain_record", "namespace": namespace, "record_revision": row["revision"]},
                supersedes=existing.memory_id if existing else None)
            if existing:
                existing.status, existing.replaced_by = "superseded", memory.memory_id
            self.store._rewrite("memories", [*items, memory])

    def forget(self, memory_id: str) -> bool:
        with state_lock(self.store._path("memories")):
            items = self.store.list_memories(limit=None)
            target = next((m for m in items if m.memory_id == memory_id), None)
            if target is None:
                return False
            # Remove the replacement chain from recall as well as the selected record.
            ids = {memory_id}
            while True:
                expanded = ids | {m.memory_id for m in items
                                  if m.supersedes in ids or m.replaced_by in ids}
                if expanded == ids:
                    break
                ids = expanded
            for m in items:
                if m.memory_id in ids:
                    m.status, m.updated_at = "deleted", iso(utcnow())
            self.store._rewrite("memories", items)
            return True

    def relevant(self, *, project_id: str | None = None, goal: str = "",
                 task_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        query = tokens(goal)
        now = datetime.now(timezone.utc)
        result = {layer: [] for layer in LAYER_LIMITS}
        candidates = self.store.list_memories(limit=None)
        for layer, limit in LAYER_LIMITS.items():
            eligible = []
            for memory in candidates:
                if memory.layer != layer or memory.status != "active":
                    continue
                if memory.expires_at:
                    expiry = memory.expires_at
                    if (expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)) <= now:
                        continue
                scope_project = memory.scope.get("project_id")
                if scope_project is not None and scope_project != project_id:
                    continue
                if layer != "user" and (not project_id or scope_project != project_id):
                    continue
                if memory.scope.get("task_id") not in (None, task_id):
                    continue
                eligible.append((memory, tokens(memory.content + " " + " ".join(memory.tags))))
            frequencies = Counter(token for _, terms in eligible for token in terms)
            ranked = []
            for memory, terms in eligible:
                score = sum(math.log1p(len(eligible) / (1 + frequencies[t])) for t in query & terms)
                if query and not score and not memory.pinned and layer != "user":
                    continue
                ranked.append((memory, score))
            ranked.sort(key=lambda pair: pair[0].updated_at, reverse=True)
            ranked.sort(key=lambda pair: (not pair[0].pinned, {"user_adopted": 0, "distilled_candidate": 1, "model_transient": 2}[pair[0].trust], -pair[1], pair[0].importance))
            result[layer] = [m.model_dump(mode="json", exclude_none=True) for m, _ in ranked[:limit]]
        return result
