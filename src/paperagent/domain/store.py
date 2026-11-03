"""DomainStore: user-scoped JSONL persistence for domain entities.

Lives at <workspace_root>/domain/. Append-only for notifications and
radar hits; rewrite-on-update for tasks/deadlines/intents/automations
(single-user V2-m1 scale - fine for files, swap for a DB later).
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Iterator, Optional

from paperagent.domain.entities import (Automation, Deadline, ImportedFile,
                                        Notification, MemoryRecord, Opportunity, RadarHit,
                                        ProjectProfile, ResearchProfile,
                                        StandingIntent, Submission, Task,
                                        ZoteroItem,
                                        TriggerEvent, Watcher)


def _counter(prefix: str) -> Iterator[str]:
    for i in itertools.count(1):
        yield f"{prefix}{i:04d}"


class DomainStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "domain"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ids = {
            "task": _counter("task"),
            "deadline": _counter("dl"),
            "notification": _counter("ntf"),
            "intent": _counter("si"),
            "automation": _counter("am"),
            "trigger": _counter("trg"),
            "watcher": _counter("watch"),
            "opportunity": _counter("opp"),
            "submission": _counter("sub"),
            "memory": _counter("mem"),
            "hit": _counter("hit"),
            "imported_file": _counter("file"),
            "zotero_item": _counter("zot"),
        }

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.jsonl"

    def _next_id(self, kind: str) -> str:
        existing = {e.id_key for e in self._iter_all(kind)}
        for candidate in self._ids[kind]:
            if candidate not in existing:
                return candidate
        raise RuntimeError("id space exhausted")  # pragma: no cover

    # -- generic jsonl helpers -------------------------------------------

    def _append(self, name: str, model) -> None:
        from paperagent.quality.policies import check_store_write
        check_store_write(name)
        with self._path(name).open("a", encoding="utf-8") as f:
            f.write(model.model_dump_json() + "\n")

    def _load(self, name: str, cls):
        p = self._path(name)
        if not p.exists():
            return []
        return [cls.model_validate_json(line)
                for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def _rewrite(self, name: str, models) -> None:
        from paperagent.quality.policies import check_store_write
        from paperagent.quality.policies import effective_role
        internal = {'deadlines':(Deadline,{'reminders_sent','overdue_notified'}),
                    'watchers':(Watcher,{'last_checked','cursor'}),
                    'automations':(Automation,{'last_run','next_run'}),
                    'trigger_events':(TriggerEvent,{'handled'}),
                    'opportunities':(Opportunity,{'notified'})}
        if name in internal and effective_role() == 'proactive':
            model,fields=internal[name]
            old = self._load(name, model)
            clean = lambda rows: [m.model_dump(exclude=fields) for m in rows]
            if clean(old) != clean(models):
                raise PermissionError('proactive configuration change denied: '+name)
        else:
            check_store_write(name)
        p = self._path(name)
        tmp = p.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for m in models:
                f.write(m.model_dump_json() + "\n")
        try:
            tmp.replace(p)
        except PermissionError:
            # Windows can transiently reject replacing an existing file in
            # temp directories. The store owns this JSONL, so fall back to an
            # explicit unlink + replace while preserving the same atomic-ish
            # write discipline for the common path.
            if p.exists():
                p.unlink()
            tmp.replace(p)

    def _iter_all(self, kind: str):
        mapping = {
            "task": (Task, "tasks"),
            "deadline": (Deadline, "deadlines"),
            "notification": (Notification, "notifications"),
            "intent": (StandingIntent, "standing_intents"),
            "automation": (Automation, "automations"),
            "trigger": (TriggerEvent, "trigger_events"),
            "watcher": (Watcher, "watchers"),
            "opportunity": (Opportunity, "opportunities"),
            "submission": (Submission, "submissions"),
            "memory": (MemoryRecord, "memories"),
            "hit": (RadarHit, "radar_hits"),
            "imported_file": (ImportedFile, "imported_files"),
            "zotero_item": (ZoteroItem, "zotero_items"),
        }
        cls, name = mapping[kind]

        class _Wrap:
            def __init__(self, m):
                self.m = m
                self.id_key = getattr(m, _id_field(kind))

        return iter([_Wrap(m) for m in self._load(name, cls)])

    # -- tasks -------------------------------------------------------------

    def add_task(self, **kwargs) -> Task:
        task = Task(task_id=self._next_id("task"), **kwargs)
        self._append("tasks", task)
        return task

    def list_tasks(self) -> list[Task]:
        return self._load("tasks", Task)

    def update_task(self, task_id: str, **changes) -> Optional[Task]:
        tasks = self.list_tasks()
        for t in tasks:
            if t.task_id == task_id:
                for k, v in changes.items():
                    setattr(t, k, v)
                self._rewrite("tasks", tasks)
                return t
        return None

    # -- deadlines -----------------------------------------------------------

    def add_deadline(self, **kwargs) -> Deadline:
        dl = Deadline(deadline_id=self._next_id("deadline"), **kwargs)
        self._append("deadlines", dl)
        return dl

    def list_deadlines(self) -> list[Deadline]:
        return self._load("deadlines", Deadline)

    def update_deadline(self, deadline_id: str, **changes) -> Optional[Deadline]:
        items = self.list_deadlines()
        for d in items:
            if d.deadline_id == deadline_id:
                for k, v in changes.items():
                    setattr(d, k, v)
                self._rewrite("deadlines", items)
                return d
        return None

    # -- notifications -------------------------------------------------------

    def add_notification(self, **kwargs) -> Notification:
        n = Notification(notification_id=self._next_id("notification"), **kwargs)
        self._append("notifications", n)
        return n

    def list_notifications(self, limit: int = 50) -> list[Notification]:
        return self._load("notifications", Notification)[-limit:]

    def mark_notification_read(self, notification_id: str) -> bool:
        items = self._load("notifications", Notification)
        found = False
        for n in items:
            if n.notification_id == notification_id:
                n.read = True
                found = True
        if found:
            self._rewrite("notifications", items)
        return found

    def dismiss_notification(self, notification_id: str) -> bool:
        items = self._load("notifications", Notification)
        found = False
        for n in items:
            if n.notification_id == notification_id:
                n.read = True
                n.dismissed = True
                found = True
        if found:
            self._rewrite("notifications", items)
        return found

    # -- memory ----------------------------------------------------------------

    def add_memory(self, **kwargs) -> MemoryRecord:
        mem = MemoryRecord(memory_id=self._next_id("memory"), **kwargs)
        self._append("memories", mem)
        return mem

    def list_memories(self, layer: str | None = None,
                      scope: dict | None = None,
                      limit: int | None = 50) -> list[MemoryRecord]:
        items = self._load("memories", MemoryRecord)
        if layer:
            items = [m for m in items if m.layer == layer]
        if scope:
            def matches(m: MemoryRecord) -> bool:
                return all(m.scope.get(k) == v for k, v in scope.items())
            items = [m for m in items if matches(m)]
        items.sort(key=lambda m: (m.importance, m.created_at))
        return items[:limit] if limit is not None else items

    def update_memory(self, memory_id: str, **changes) -> Optional[MemoryRecord]:
        items = self._load("memories", MemoryRecord)
        for m in items:
            if m.memory_id == memory_id:
                for k, v in changes.items():
                    setattr(m, k, v)
                from paperagent.domain.entities import iso, utcnow
                m.updated_at = iso(utcnow())
                self._rewrite("memories", items)
                return m
        return None

    # -- standing intents ------------------------------------------------------

    def add_standing_intent(self, **kwargs) -> StandingIntent:
        from paperagent.tasks.standing_intent import normalize_intent_payload, stamp_intent, persist_intents
        from paperagent.workspace.state_store import state_lock
        from paperagent.quality.policies import check_store_write
        check_store_write('standing_intents')
        with state_lock(self._path('standing_intents')):
            kwargs = normalize_intent_payload(kwargs)
            si = StandingIntent(intent_id=self._next_id('intent'), **kwargs)
            stamp_intent(self,si)
            persist_intents(self,[*self.list_standing_intents(include_deleted=True),si])
            return si

    def list_standing_intents(self, include_deleted=False) -> list[StandingIntent]:
        rows = self._load('standing_intents',StandingIntent)
        return rows if include_deleted else [r for r in rows if not r.deleted]

    def update_standing_intent(self, intent_id: str, **changes) -> Optional[StandingIntent]:
        from paperagent.tasks.standing_intent import stamp_intent,persist_intents
        from paperagent.domain.intent_sync import PAYLOAD_FIELDS
        from paperagent.workspace.state_store import state_lock
        from paperagent.quality.policies import check_store_write
        check_store_write('standing_intents')
        if not set(changes) <= PAYLOAD_FIELDS:
            raise ValueError('Invalid intent update fields')
        with state_lock(self._path('standing_intents')):
            rows = self.list_standing_intents(include_deleted=True)
            for i,si in enumerate(rows):
                if si.intent_id==intent_id and not si.deleted:
                    if 'scope' in changes and changes['scope'] != si.scope:
                        # Moving configuration changes its remote authority domain.
                        # Keep a tombstone in the old scope, and create a fresh identity.
                        data = {**si.model_dump(),**changes,'intent_id':self._next_id('intent'),
                                'sync_id':'','sync_version':0,'sync_updated':0,'sync_origin':''}
                        replacement = stamp_intent(self,StandingIntent.model_validate(data))
                        si.deleted=True
                        si.enabled=False
                        stamp_intent(self,si)
                        persist_intents(self,[*rows,replacement])
                        return replacement
                    rows[i] = stamp_intent(self,StandingIntent.model_validate({**si.model_dump(),**changes}))
                    persist_intents(self,rows)
                    return rows[i]
        return None

    def delete_standing_intent(self, intent_id):
        from paperagent.tasks.standing_intent import stamp_intent,persist_intents
        from paperagent.workspace.state_store import state_lock
        from paperagent.quality.policies import check_store_write
        check_store_write('standing_intents')
        with state_lock(self._path('standing_intents')):
            rows=self.list_standing_intents(include_deleted=True)
            for si in rows:
                if si.intent_id==intent_id:
                    if not si.deleted:
                        si.deleted=True
                        si.enabled=False
                        stamp_intent(self,si)
                        persist_intents(self,rows)
                    return True
        return False

    # -- automations -----------------------------------------------------------

    def add_automation(self, **kwargs) -> Automation:
        am = Automation(automation_id=self._next_id("automation"), **kwargs)
        self._append("automations", am)
        return am

    def list_automations(self) -> list[Automation]:
        return self._load("automations", Automation)

    def update_automation(self, automation_id: str, **changes) -> Optional[Automation]:
        items = self.list_automations()
        for a in items:
            if a.automation_id == automation_id:
                for k, v in changes.items():
                    setattr(a, k, v)
                self._rewrite("automations", items)
                return a
        return None

    # -- trigger events ---------------------------------------------------------

    def add_trigger_event(self, **kwargs) -> TriggerEvent:
        event = TriggerEvent(trigger_id=self._next_id("trigger"), **kwargs)
        self._append("trigger_events", event)
        return event

    def list_trigger_events(self) -> list[TriggerEvent]:
        return self._load("trigger_events", TriggerEvent)

    def update_trigger_event(self, trigger_id: str, **changes) -> Optional[TriggerEvent]:
        items = self.list_trigger_events()
        for e in items:
            if e.trigger_id == trigger_id:
                for k, v in changes.items():
                    setattr(e, k, v)
                self._rewrite("trigger_events", items)
                return e
        return None

    # -- watchers ---------------------------------------------------------------

    def add_watcher(self, **kwargs) -> Watcher:
        watcher = Watcher(watcher_id=self._next_id("watcher"), **kwargs)
        self._append("watchers", watcher)
        return watcher

    def list_watchers(self) -> list[Watcher]:
        return self._load("watchers", Watcher)

    def update_watcher(self, watcher_id: str, **changes) -> Optional[Watcher]:
        items = self.list_watchers()
        for w in items:
            if w.watcher_id == watcher_id:
                for k, v in changes.items():
                    setattr(w, k, v)
                self._rewrite("watchers", items)
                return w
        return None

    # -- radar hits --------------------------------------------------------------

    def add_opportunity(self, **kwargs) -> Opportunity:
        opp = Opportunity(opportunity_id=self._next_id("opportunity"), **kwargs)
        self._append("opportunities", opp)
        return opp

    def list_opportunities(self, limit: int = 50) -> list[Opportunity]:
        return self._load("opportunities", Opportunity)[-limit:]

    def update_opportunity(self, opportunity_id: str, **changes) -> Optional[Opportunity]:
        items = self._load("opportunities", Opportunity)
        for o in items:
            if o.opportunity_id == opportunity_id:
                for k, v in changes.items():
                    setattr(o, k, v)
                self._rewrite("opportunities", items)
                return o
        return None

    def opportunity_dedup_keys(self) -> set[str]:
        return {o.dedup_key for o in self._load("opportunities", Opportunity)}

    def add_radar_hit(self, **kwargs) -> RadarHit:
        hit = RadarHit(hit_id=self._next_id("hit"), **kwargs)
        self._append("radar_hits", hit)
        return hit

    def list_radar_hits(self, limit: int = 50) -> list[RadarHit]:
        return self._load("radar_hits", RadarHit)[-limit:]

    def radar_dedup_keys(self) -> set[str]:
        keys = set(self.opportunity_dedup_keys())
        for h in self._load("radar_hits", RadarHit):
            from paperagent.domain.entities import radar_dedup_key
            keys.add(radar_dedup_key(h.title, h.doi))
        return keys

    # -- submissions ------------------------------------------------------------

    def add_submission(self, **kwargs) -> Submission:
        sub = Submission(submission_id=self._next_id("submission"), **kwargs)
        self._append("submissions", sub)
        return sub

    def list_submissions(self) -> list[Submission]:
        return self._load("submissions", Submission)

    def update_submission(self, submission_id: str, **changes) -> Optional[Submission]:
        items = self.list_submissions()
        for s in items:
            if s.submission_id == submission_id:
                for k, v in changes.items():
                    setattr(s, k, v)
                self._rewrite("submissions", items)
                return s
        return None

    # -- profile -------------------------------------------------------------------

    @property
    def profile_path(self) -> Path:
        return self.dir / "profile.json"

    def get_profile(self) -> ResearchProfile:
        if not self.profile_path.exists():
            return ResearchProfile()
        return ResearchProfile.model_validate_json(
            self.profile_path.read_text(encoding="utf-8"))

    def save_profile(self, profile: ResearchProfile) -> ResearchProfile:
        from paperagent.quality.policies import check_store_write
        check_store_write("profile")
        self.profile_path.write_text(profile.model_dump_json(), encoding="utf-8")
        return profile

    # -- project profiles ---------------------------------------------------------

    def list_project_profiles(self) -> list[ProjectProfile]:
        return self._load("project_profiles", ProjectProfile)

    def get_project_profile(self, project_id: str) -> ProjectProfile:
        for profile in self.list_project_profiles():
            if profile.project_id == project_id:
                return profile
        return ProjectProfile(project_id=project_id)

    def save_project_profile(self, profile: ProjectProfile) -> ProjectProfile:
        profiles = self.list_project_profiles()
        from paperagent.domain.entities import iso, utcnow
        profile.updated_at = iso(utcnow())
        for i, existing in enumerate(profiles):
            if existing.project_id == profile.project_id:
                profile.created_at = existing.created_at
                profiles[i] = profile
                self._rewrite("project_profiles", profiles)
                return profile
        profiles.append(profile)
        self._rewrite("project_profiles", profiles)
        return profile

    # -- imported files -----------------------------------------------------------

    def add_imported_file(self, **kwargs) -> ImportedFile:
        file = ImportedFile(file_id=self._next_id("imported_file"), **kwargs)
        self._append("imported_files", file)
        return file

    def list_imported_files(self, project_id: str | None = None,
                            limit: int = 100) -> list[ImportedFile]:
        files = self._load("imported_files", ImportedFile)
        if project_id:
            files = [f for f in files if f.project_id == project_id]
        return files[-limit:]

    def imported_file_dedup_keys(self) -> set[str]:
        keys = set()
        for file in self._load("imported_files", ImportedFile):
            keys.add(file.sha256 or file.path)
        return keys

    # -- zotero items -------------------------------------------------------------

    def list_zotero_items(self, project_id: str | None = None,
                          limit: int = 200) -> list[ZoteroItem]:
        items = self._load("zotero_items", ZoteroItem)
        if project_id:
            items = [i for i in items if i.project_id == project_id]
        return items[-limit:]

    def upsert_zotero_item(self, **kwargs) -> ZoteroItem:
        items = self._load("zotero_items", ZoteroItem)
        key = kwargs.get("zotero_key")
        if not key:
            raise ValueError("zotero_key is required")
        from paperagent.domain.entities import iso, utcnow
        for i, existing in enumerate(items):
            if existing.zotero_key == key and existing.project_id == kwargs.get("project_id"):
                updated = existing.model_copy(update={**kwargs, "synced_at": iso(utcnow())})
                items[i] = updated
                self._rewrite("zotero_items", items)
                return updated
        item = ZoteroItem(item_id=self._next_id("zotero_item"), **kwargs)
        self._append("zotero_items", item)
        return item

    def zotero_item_keys(self, project_id: str | None = None) -> set[str]:
        return {i.zotero_key for i in self.list_zotero_items(project_id=project_id)}


def _id_field(kind: str) -> str:
    return {"task": "task_id", "deadline": "deadline_id",
            "notification": "notification_id", "intent": "intent_id",
            "automation": "automation_id", "trigger": "trigger_id",
            "watcher": "watcher_id", "opportunity": "opportunity_id",
            "submission": "submission_id", "memory": "memory_id",
            "hit": "hit_id", "imported_file": "file_id",
            "zotero_item": "item_id"}[kind]
