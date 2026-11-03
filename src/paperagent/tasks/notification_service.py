"""Unified notification service.

All user-facing nudges should pass through this service so deduplication,
priority, and later channel policy stay in one place.
"""
from __future__ import annotations

import hashlib

from paperagent.domain.entities import Notification


class NotificationService:
    def __init__(self, store):
        self.store = store

    def notify(self, *, type: str = "system", title: str, body: str = "",
               channel: str = "in_app", ref: str | None = None,
               dedup_key: str | None = None,
               priority: int = 2, project_id: str | None = None,
               target_device: str | None = None) -> Notification:
        key = dedup_key or _dedup_key(type, title, ref)
        from paperagent.workspace.state_store import state_lock
        with state_lock(self.store._path('notifications')):
            existing = next((n for n in self.store.list_notifications(limit=1000) if n.dedup_key==key),None)
            note = existing or self.store.add_notification(type=type,title=title,body=body,
                channel=channel,ref=ref,dedup_key=key,priority=priority,
                relay_project_id=project_id,relay_target_device=target_device)
        try:
            from paperagent.tasks.proactive_sync import enqueue_notification
            enqueue_notification(self.store,note)
        except (ValueError,OSError):
            pass  # local notification is authoritative; sync scan recovers enqueue failures
        return note

    def unread(self) -> list[Notification]:
        return [n for n in self.store.list_notifications(limit=1000) if not n.read]


def _dedup_key(type: str, title: str, ref: str | None) -> str:
    raw = "|".join([type, title.strip().lower(), ref or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
