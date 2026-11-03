"""Scheduler + deadline triggers: the Proactive Runtime heartbeat.

Deterministic work runs here without spinning up the agent loop:
- automations whose next_run is due (radar sweep, daily digest)
- deadline reminders at reminder_hours before `when`, each fired once
- overdue deadline alerts, fired once

Clock is injectable for tests. In the API server this ticks on a
daemon thread; in the CLI `paperagent tick` runs one pass on demand.
"""
from __future__ import annotations
from paperagent.quality.policies import proactive_run

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from paperagent.domain.entities import (AUTOMATION_INTERVALS, iso, utcnow)
from paperagent.domain.store import DomainStore
from paperagent.tasks.notification_service import NotificationService
from paperagent.tasks.task_manager import TaskManager
from paperagent.tasks.trigger_engine import TriggerEngine

Clock = Callable[[], datetime]


class Scheduler:
    def __init__(self, store: DomainStore,
                 radar_run: Optional[Callable[[], dict]] = None,
                 digest_build: Optional[Callable[[], dict]] = None,
                 clock: Clock = utcnow):
        self.store = store
        self.task_manager = TaskManager(store)
        self.trigger_engine = TriggerEngine(store)
        self.notifications = NotificationService(store)
        self.radar_run = radar_run or (lambda: {"skipped": "no radar configured"})
        self.digest_build = digest_build or (lambda: {"title": "今日科研简报",
                                                      "body": "暂无内容"})
        self.clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- one deterministic pass ------------------------------------------

    @proactive_run
    def tick(self) -> list[str]:
        log: list[str] = []
        now = self.clock()
        log.extend(self._run_due_automations(now))
        log.extend(self._check_deadlines(now))
        from paperagent.tasks.proactive import ProactiveRuntime
        log.extend(ProactiveRuntime(self.store).tick(now))
        from paperagent.tasks.cloud_sync import sync_workspaces
        log.extend(sync_workspaces(self.store.dir.parent))
        from paperagent.tasks.proactive_sync import tick as sync_proactive
        log.extend(sync_proactive(self.store.dir.parent))
        from paperagent.tasks.cloud_tasks import tick as cloud_tasks_tick
        log.extend(cloud_tasks_tick(self.store.dir.parent))
        from paperagent.tasks.cloud_conversation import tick as conversation_tick
        log.extend(conversation_tick(self.store.dir.parent))
        return log

    def _run_due_automations(self, now: datetime) -> list[str]:
        log = []
        triggers = [e for e in self.trigger_engine.collect_due(now)
                    if e.type == "schedule" and not e.handled]
        for event in triggers:
            am = next((a for a in self.store.list_automations()
                       if a.automation_id == event.ref), None)
            if am is None:
                self.store.update_trigger_event(event.trigger_id, handled=True)
                continue
            if not am.enabled:
                continue
            try:
                if am.action == "radar_literature":
                    result = self.radar_run()
                    log.append(f"automation {am.automation_id} radar: "
                               f"{result.get('new_hits', 0)} new hits")
                elif am.action == "daily_digest":
                    d = self.digest_build()
                    self.notifications.notify(
                        type="digest", title=d.get("title", "科研简报"),
                        body=d.get("body", ""),
                        dedup_key=f"digest:{am.automation_id}:{iso(now)}")
                    log.append(f"automation {am.automation_id} digest created")
            except Exception as e:  # keep the loop alive; record the failure
                log.append(f"automation {am.automation_id} failed: {e}")
            interval = AUTOMATION_INTERVALS[am.cadence]
            self.store.update_automation(
                am.automation_id, last_run=iso(now),
                next_run=iso(now + timedelta(seconds=interval)))
            self.store.update_trigger_event(event.trigger_id, handled=True)
        return log

    def _check_deadlines(self, now: datetime) -> list[str]:
        log = []
        triggers = [e for e in self.trigger_engine.collect_due(now)
                    if e.type == "deadline" and not e.handled]
        for event in triggers:
            dl = next((d for d in self.store.list_deadlines()
                       if d.deadline_id == event.ref), None)
            if dl is None:
                self.store.update_trigger_event(event.trigger_id, handled=True)
                continue
            when = _parse(dl.when)
            action = event.action
            if action == "deadline_reminder":
                key = event.payload.get("key")
                if key and key not in dl.reminders_sent:
                    hours_left = round((when - now).total_seconds() / 3600)
                    self.notifications.notify(
                        type="deadline",
                        title=f"Deadline 逼近：{dl.title}",
                        body=f"距截止约 {hours_left} 小时（{dl.when}）",
                        ref=dl.deadline_id,
                        dedup_key=f"deadline:{dl.deadline_id}:{key}",
                        priority=1 if hours_left <= 24 else 2)
                    dl.reminders_sent.append(key)
                    self.store.update_deadline(dl.deadline_id,
                                               reminders_sent=dl.reminders_sent)
                    log.append(f"deadline {dl.deadline_id} reminded {key}")
            elif action == "deadline_overdue" and not dl.overdue_notified:
                self.notifications.notify(
                    type="deadline",
                    title=f"已过截止：{dl.title}",
                    body=f"截止时间为 {dl.when}，请确认状态或更新",
                    ref=dl.deadline_id,
                    dedup_key=f"deadline:{dl.deadline_id}:overdue",
                    priority=1)
                self.store.update_deadline(dl.deadline_id, overdue_notified=True)
                log.append(f"deadline {dl.deadline_id} overdue notified")
            self.store.update_trigger_event(event.trigger_id, handled=True)
        return log

    # -- background loop ----------------------------------------------------

    def start(self, interval_s: float = 60.0) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(interval_s):
                try:
                    self.tick()
                except Exception:
                    pass  # a failed tick must not kill the heartbeat

        self._thread = threading.Thread(target=loop, daemon=True,
                                        name="paperagent-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
