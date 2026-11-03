"""Local Email connector.

This is a deterministic local connector, not a Gmail integration. It
normalizes email-like payloads into AssistantEvent records and can route
submission-related email text into the Submission Parser/Manager chain.
"""
from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from paperagent.domain.entities import iso, utcnow
from paperagent.domain.store import DomainStore
from paperagent.gateway.events import AssistantEvent
from paperagent.tasks.submission_manager import SubmissionManager
from paperagent.tasks.watcher import WatcherManager


class EmailMessage(BaseModel):
    message_id: str = ""
    subject: str = ""
    text: str = ""
    from_addr: str = ""
    received_at: str = Field(default_factory=lambda: iso(utcnow()))


class LocalEmailConnector:
    connector_id = "local_email"

    def __init__(self, store: DomainStore):
        self.store = store
        self.watchers = WatcherManager(store)
        self.submissions = SubmissionManager(store)

    def normalize(self, payload: dict[str, Any]) -> EmailMessage:
        msg = EmailMessage(
            message_id=(payload.get("message_id") or "").strip(),
            subject=(payload.get("subject") or "").strip(),
            text=(payload.get("text") or payload.get("body") or "").strip(),
            from_addr=(payload.get("from_addr") or payload.get("from") or "").strip(),
            received_at=payload.get("received_at") or iso(utcnow()),
        )
        if not msg.message_id:
            msg.message_id = _message_id(msg)
        return msg

    def to_event(self, normalized: EmailMessage,
                 *, project_id: str | None = None) -> AssistantEvent:
        return AssistantEvent(
            event_id=f"email:{normalized.message_id}",
            type="connector_event",
            source="connector:email",
            project_id=project_id,
            payload=normalized.model_dump(),
        )

    def ingest_submission_email(self, payload: dict[str, Any],
                                *, watcher_id: str | None = None,
                                project_id: str | None = None) -> dict:
        message = self.normalize(payload)
        assistant_event = self.to_event(message, project_id=project_id)
        trigger = None
        if watcher_id:
            trigger = self.watchers.ingest_change(
                watcher_id,
                {"action": "submission_email_received",
                 "message_id": message.message_id,
                 "subject": message.subject,
                 "text": message.text,
                 "from_addr": message.from_addr,
                 "project_id": project_id,
                 "cursor": message.message_id},
            )
        submission = self.submissions.apply_email(
            message.text, subject=message.subject, source="email_connector:" + message.message_id, project_id=project_id)
        return {
            "event": assistant_event,
            "message": message,
            "trigger": trigger,
            "submission": submission,
        }


def _message_id(message: EmailMessage) -> str:
    raw = "\n".join([message.from_addr, message.subject, message.text])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
