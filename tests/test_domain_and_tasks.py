"""V2-m1: Domain Layer, Task Manager, Scheduler (fake clock)."""
import pytest

from paperagent.domain.entities import (DisciplineProfile, LanguageProfile,
                                        ProjectProfile, ResearchProfile)
from paperagent.domain.store import DomainStore
from paperagent.tasks.scheduler import Scheduler
from paperagent.tasks.task_manager import InvalidTransition, TaskManager


def _store(tmp_path):
    return DomainStore(tmp_path)


def test_task_crud_and_transitions(tmp_path):
    tm = TaskManager(_store(tmp_path))
    t = tm.create("完成第三章", origin="user", priority=1)
    assert t.status == "todo"
    t2 = tm.transition(t.task_id, "in_progress")
    assert t2.status == "in_progress"
    tm.complete(t.task_id)
    with pytest.raises(InvalidTransition):
        tm.transition(t.task_id, "todo")  # done is terminal
    assert len(tm.open_tasks()) == 0


def test_store_roundtrips(tmp_path):
    store = _store(tmp_path)
    store.add_deadline(title="课题截止", when="2026-11-20T00:00:00+00:00")
    store.add_notification(type="radar", title="hit", body="b")
    store.add_standing_intent(description="AI 课题提醒", topic="AI social science")
    store.add_radar_hit(radar_type="literature", title="A paper", doi="10.1/x")
    store.save_profile(ResearchProfile(directions=["信息系统"], topics=["digital transformation"]))

    assert len(store.list_deadlines()) == 1
    assert len(store.list_notifications()) == 1
    assert len(store.list_standing_intents()) == 1
    assert len(store.list_radar_hits()) == 1
    assert store.get_profile().topics == ["digital transformation"]
    assert store.mark_notification_read("ntf0001")
    assert store.list_notifications()[0].read is True
    assert not store.mark_notification_read("ntf9999")


def test_profile_backbone_roundtrips(tmp_path):
    store = _store(tmp_path)
    profile = ResearchProfile(
        name="Dr. Chen",
        roles=["phd_student"],
        affiliations=["School of Management"],
        directions=["信息系统"],
        topics=["digital transformation"],
        methods=["survey", "regression"],
        target_journals=["Information Systems Research"],
        advisor_rules=["Prefer theory-first introductions."],
        language=LanguageProfile(
            ui_language="zh",
            writing_language="bilingual",
            terminology_policy="bilingual_glossary",
            citation_style="gbt7714",
        ),
        disciplines=[
            DisciplineProfile(
                discipline_id="management",
                name="Management",
                methods=["SEM", "case study"],
                quality_checks=["method_design_alignment"],
            )
        ],
    )

    store.save_profile(profile)
    loaded = store.get_profile()

    assert loaded.name == "Dr. Chen"
    assert loaded.topics == ["digital transformation"]
    assert loaded.language.writing_language == "bilingual"
    assert loaded.language.citation_style == "gbt7714"
    assert loaded.disciplines[0].discipline_id == "management"


def test_project_profile_upsert_roundtrips(tmp_path):
    store = _store(tmp_path)

    created = store.save_project_profile(ProjectProfile(
        project_id="paper-a",
        project_type="journal_article",
        title="LLM academic writing support",
        discipline_ids=["management"],
        target_venue="Information Systems Research",
        keywords=["LLM", "academic writing"],
        methods=["survey"],
    ))
    updated = store.save_project_profile(created.model_copy(update={
        "target_venue": "MIS Quarterly",
        "constraints": ["Use English for submission draft."],
    }))

    assert updated.created_at == created.created_at
    assert store.get_project_profile("paper-a").target_venue == "MIS Quarterly"
    assert store.get_project_profile("missing").project_type == "general"
    assert len(store.list_project_profiles()) == 1


def test_standing_intent_normalizes_structured_query(tmp_path):
    store = _store(tmp_path)
    si = store.add_standing_intent(
        description="关注浙江 AI 社科课题",
        type="grant",
        structured_query={"region": "浙江", "keywords": ["AI", "社科"]},
        notify_policy={"cadence": "weekly", "min_score": 0.7},
    )
    assert si.topic == "AI 社科"
    assert si.scope == {"profile": True}
    assert si.notify_policy["channel"] == "in_app"
    assert si.notify_policy["cadence"] == "weekly"
    assert si.notify_policy["min_score"] == 0.7
    assert si.dedup_key


def test_scheduler_deadline_reminder_fires_once(tmp_path):
    from datetime import timedelta
    from paperagent.domain.entities import iso, utcnow
    store = _store(tmp_path)
    now = utcnow()
    # deadline in 30 hours -> the h72 window is active, h24 is not yet
    store.add_deadline(title="投稿截止", when=iso(now + timedelta(hours=30)),
                       reminder_hours=[72, 24])
    sched = Scheduler(store, clock=lambda: now)
    log1 = sched.tick()
    assert any("h72" in line for line in log1)
    assert any(n.type == "deadline" for n in store.list_notifications())
    # same moment again: reminder must not repeat
    log2 = sched.tick()
    assert not any("h72" in line for line in log2)
    deadline_notifs = [n for n in store.list_notifications() if n.type == "deadline"]
    assert len(deadline_notifs) == 1


def test_scheduler_overdue_notified_once(tmp_path):
    from datetime import timedelta
    from paperagent.domain.entities import iso, utcnow
    store = _store(tmp_path)
    now = utcnow()
    store.add_deadline(title="过期课题", when=iso(now - timedelta(hours=1)))
    sched = Scheduler(store, clock=lambda: now)
    sched.tick()
    sched.tick()
    overdue = [n for n in store.list_notifications() if "已过截止" in n.title]
    assert len(overdue) == 1


def test_scheduler_runs_due_automation(tmp_path):
    from datetime import timedelta
    from paperagent.domain.entities import iso, utcnow
    store = _store(tmp_path)
    now = utcnow()
    am = store.add_automation(name="daily radar", cadence="daily",
                              action="radar_literature",
                              next_run=iso(now - timedelta(minutes=5)))
    calls = []

    def fake_radar():
        calls.append(1)
        return {"new_hits": 2}

    sched = Scheduler(store, radar_run=fake_radar, clock=lambda: now)
    log = sched.tick()
    assert calls, "due automation must run its action"
    assert any("radar" in line for line in log)
    updated = store.list_automations()[0]
    assert updated.automation_id == am.automation_id
    assert updated.last_run is not None
    assert updated.next_run > iso(now)  # rescheduled into the future
    triggers = store.list_trigger_events()
    assert len(triggers) == 1
    assert triggers[0].type == "schedule"
    assert triggers[0].handled is True


def test_trigger_engine_collects_and_dedups(tmp_path):
    from datetime import timedelta
    from paperagent.domain.entities import iso, utcnow
    from paperagent.tasks.trigger_engine import TriggerEngine

    store = _store(tmp_path)
    now = utcnow()
    store.add_automation(name="daily digest", cadence="daily",
                         action="daily_digest",
                         next_run=iso(now - timedelta(minutes=1)))
    store.add_deadline(title="答辩材料", when=iso(now + timedelta(hours=10)),
                       reminder_hours=[24])

    engine = TriggerEngine(store)
    first = engine.collect_due(now)
    second = engine.collect_due(now)
    assert len(first) == 2
    assert {e.type for e in first} == {"schedule", "deadline"}
    assert [e.trigger_id for e in first] == [e.trigger_id for e in second]
    assert len(store.list_trigger_events()) == 2


def test_watcher_ingests_external_change(tmp_path):
    from paperagent.tasks.watcher import WatcherManager

    store = _store(tmp_path)
    manager = WatcherManager(store)
    watcher = manager.create(
        name="submission inbox",
        watcher_type="submission",
        source="email",
        query={"from": "editor@example.org"},
    )

    event = manager.ingest_change(
        watcher.watcher_id,
        {"subject": "Decision letter", "action": "submission_update",
         "cursor": "msg-1"},
    )
    assert event.type == "external"
    assert event.ref == watcher.watcher_id
    assert event.payload["subject"] == "Decision letter"
    updated = store.list_watchers()[0]
    assert updated.cursor == "msg-1"
    assert updated.last_checked is not None


def test_submission_manager_state_machine(tmp_path):
    from paperagent.quality.validators import InvalidSubmissionTransition
    from paperagent.tasks.submission_manager import SubmissionManager

    store = _store(tmp_path)
    manager = SubmissionManager(store)
    sub = manager.create(title="Paper A", journal_name="Journal X",
                         manuscript_id="JX-001")
    assert sub.status == "submitted"
    assert sub.status_history[0].status == "submitted"

    updated = manager.transition(sub.submission_id, "with_editor",
                                 source="email", note="editor received")
    assert updated.status == "with_editor"
    assert len(updated.status_history) == 2
    triggers = store.list_trigger_events()
    assert triggers[-1].type == "state_change"
    assert triggers[-1].action == "submission_status_changed"
    assert triggers[-1].payload["before"] == "submitted"
    assert triggers[-1].payload["after"] == "with_editor"
    assert store.list_notifications()[-1].type == "submission"

    manager.transition(sub.submission_id, "rejected")
    with pytest.raises(InvalidSubmissionTransition):
        manager.transition(sub.submission_id, "published")


def test_submission_email_parser_and_apply(tmp_path):
    from paperagent.tasks.submission_manager import SubmissionManager
    from paperagent.tasks.submission_parser import SubmissionEmailParser

    text = """
Journal: Journal X
Manuscript ID: JX-001
Title: LLM writing paper
Status: Under Review
"""
    parsed = SubmissionEmailParser().parse(text, subject="Status update")
    assert parsed.manuscript_id == "JX-001"
    assert parsed.journal_name == "Journal X"
    assert parsed.title == "LLM writing paper"
    assert parsed.status == "under_review"

    store = _store(tmp_path)
    manager = SubmissionManager(store)
    sub = manager.create(title="LLM writing paper", journal_name="Journal X",
                         manuscript_id="JX-001")
    updated = manager.apply_email(text, subject="Status update")
    assert updated.submission_id == sub.submission_id
    assert updated.status == "under_review"
    assert store.list_trigger_events()[-1].type == "state_change"
    assert store.list_notifications()[-1].type == "submission"


def test_local_email_connector_submission_ingest(tmp_path):
    from paperagent.connectors.email import LocalEmailConnector
    from paperagent.tasks.watcher import WatcherManager

    store = _store(tmp_path)
    watcher = WatcherManager(store).create(
        name="submission inbox",
        watcher_type="submission",
        source="email",
    )
    connector = LocalEmailConnector(store)
    result = connector.ingest_submission_email({
        "from_addr": "editor@example.org",
        "subject": "Paper A status",
        "text": "Journal: Journal X\nManuscript ID: JX-001\nTitle: Paper A\nStatus: With Editor",
    }, watcher_id=watcher.watcher_id)

    assert result["event"].type == "connector_event"
    assert result["event"].source == "connector:email"
    assert result["message"].message_id
    assert result["trigger"].type == "external"
    assert result["trigger"].action == "submission_email_received"
    assert result["submission"].status == "with_editor"
    assert store.list_watchers()[0].cursor == result["message"].message_id
