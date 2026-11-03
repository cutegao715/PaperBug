"""Watcher skeleton: persistent monitors that emit external trigger events."""
from __future__ import annotations

from paperagent.domain.entities import Watcher
from paperagent.domain.store import DomainStore
from paperagent.tasks.trigger_engine import TriggerEngine


class WatcherManager:
    def __init__(self, store: DomainStore, trigger_engine: TriggerEngine | None = None):
        self.store = store
        self.trigger_engine = trigger_engine or TriggerEngine(store)

    def create(self, *, name: str, watcher_type: str, source: str,
               query: dict | None = None, enabled: bool = True) -> Watcher:
        return self.store.add_watcher(
            name=name.strip(),
            watcher_type=watcher_type,
            source=source,
            query=query or {},
            enabled=enabled,
        )

    def ingest_change(self, watcher_id: str, payload: dict):
        watcher = next((w for w in self.store.list_watchers()
                        if w.watcher_id == watcher_id), None)
        if watcher is None:
            raise KeyError(watcher_id)
        if not watcher.enabled:
            return None
        event = self.trigger_engine.record_external(
            source=f"watcher:{watcher.watcher_type}",
            ref=watcher.watcher_id,
            payload={"watcher": watcher.name, **payload},
        )
        if cursor := payload.get("cursor"):
            from paperagent.domain.entities import iso, utcnow
            self.store.update_watcher(
                watcher.watcher_id, cursor=cursor, last_checked=iso(utcnow()))
        return event
