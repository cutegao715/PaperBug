"""Trigger Engine: turn domain facts and external changes into events."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from paperagent.domain.entities import TriggerEvent
from paperagent.domain.store import DomainStore


class TriggerEngine:
    """Deterministic trigger detection for the proactive runtime.

    The engine only discovers and records trigger events. Execution stays
    in Scheduler / Gateway handlers so detection and effects remain
    independently testable.
    """

    def __init__(self, store: DomainStore):
        self.store = store

    def collect_due(self, now: datetime) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []
        events.extend(self._schedule_events(now))
        events.extend(self._deadline_events(now))
        return events

    def record_external(self, source: str, payload: dict,
                        ref: str | None = None) -> TriggerEvent:
        return self._add_once(
            type="external",
            source=source,
            ref=ref,
            payload=payload,
            action=payload.get("action"),
        )

    def record_state_change(self, source: str, ref: str,
                            before: str, after: str,
                            payload: dict | None = None) -> TriggerEvent:
        body = dict(payload or {})
        body.update({"before": before, "after": after})
        return self._add_once(
            type="state_change",
            source=source,
            ref=ref,
            payload=body,
            action=body.get("action"),
        )

    def _schedule_events(self, now: datetime) -> list[TriggerEvent]:
        out = []
        for am in self.store.list_automations():
            if not am.enabled or am.trigger_type != "schedule":
                continue
            if am.next_run is None or _parse(am.next_run) > now:
                continue
            out.append(self._add_once(
                type="schedule",
                source="automation",
                ref=am.automation_id,
                action=am.action,
                payload={"automation_id": am.automation_id,
                         "name": am.name,
                         "due_at": am.next_run},
            ))
        return out

    def _deadline_events(self, now: datetime) -> list[TriggerEvent]:
        out = []
        for dl in self.store.list_deadlines():
            when = _parse(dl.when)
            for hours in dl.reminder_hours:
                key = f"h{hours}"
                if key in dl.reminders_sent:
                    continue
                if when - timedelta(hours=hours) <= now < when:
                    out.append(self._add_once(
                        type="deadline",
                        source="deadline",
                        ref=dl.deadline_id,
                        action="deadline_reminder",
                        payload={"deadline_id": dl.deadline_id,
                                 "title": dl.title,
                                 "key": key,
                                 "when": dl.when},
                    ))
            if now >= when and not dl.overdue_notified:
                out.append(self._add_once(
                    type="deadline",
                    source="deadline",
                    ref=dl.deadline_id,
                    action="deadline_overdue",
                    payload={"deadline_id": dl.deadline_id,
                             "title": dl.title,
                             "when": dl.when},
                ))
        return out

    def _add_once(self, **kwargs) -> TriggerEvent:
        for existing in self.store.list_trigger_events():
            if (existing.type == kwargs.get("type")
                    and existing.source == kwargs.get("source")
                    and existing.ref == kwargs.get("ref")
                    and existing.action == kwargs.get("action")
                    and existing.payload == kwargs.get("payload")):
                return existing
        return self.store.add_trigger_event(**kwargs)


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
