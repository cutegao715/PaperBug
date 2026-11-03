"""V2-m1: Literature Radar pipeline, Permission Gate, Gateway routing."""
import threading

from paperagent.domain.store import DomainStore
from paperagent.gateway.events import AssistantEvent
from paperagent.gateway.router import EventRouter
from paperagent.quality.policies import ActionSpec, PermissionGate


def _fake_search(candidates):
    def search(inp):
        return {"candidates": candidates}
    return search


REAL_CANDIDATES = [
    {"ref_id": "a2024llm", "title": "LLM-assisted academic writing study",
     "authors": ["Alice"], "year": 2024, "doi": "10.1/a", "source": "openalex"},
    {"ref_id": "b2023quantum", "title": "Quantum chemistry benchmark",
     "authors": ["Bob"], "year": 2023, "doi": "10.1/b", "source": "openalex"},
]


def test_radar_records_and_dedups(tmp_path):
    from paperagent.radar.pipeline import LiteratureRadar
    store = DomainStore(tmp_path)
    store.add_standing_intent(description="关注 LLM 写作", topic="LLM academic writing")
    radar = LiteratureRadar(store, search_fn=_fake_search(REAL_CANDIDATES))

    r1 = radar.run()
    assert r1["new_hits"] == 2
    assert r1["matched"] == 1  # only the LLM-titled paper matches the topic

    r2 = radar.run()  # identical results: everything deduped
    assert r2["new_hits"] == 0

    hits = store.list_radar_hits()
    opportunities = store.list_opportunities()
    assert len(opportunities) == 2
    assert opportunities[0].opportunity_type == "literature"
    assert opportunities[0].match_score > 0
    assert opportunities[0].ai_evaluation["evaluator"] == "deterministic_radar_v1"
    assert "rationale" in opportunities[0].ai_evaluation
    assert len(hits) == 2
    matched = [h for h in hits if h.matched]
    assert matched[0].title.startswith("LLM-assisted")
    assert any(o.notified for o in store.list_opportunities() if o.matched)
    notifs = [n for n in store.list_notifications() if n.type == "radar"]
    assert len(notifs) == 1  # aggregated, only for the matched run
    assert "1 篇新文献" in notifs[0].title


def test_radar_never_records_synthetic_stubs(tmp_path):
    from paperagent.radar.pipeline import LiteratureRadar
    store = DomainStore(tmp_path)
    store.add_standing_intent(description="demo", topic="anything")
    stub = [{"ref_id": "s1", "title": "[SYNTHETIC STUB 1] A study on anything",
             "authors": ["Stub"], "year": 2021, "source": "offline_stub"}]
    result = LiteratureRadar(store, search_fn=_fake_search(stub)).run()
    assert result["new_hits"] == 0
    assert store.list_radar_hits() == []
    assert store.list_opportunities() == []


def test_radar_topics_from_profile_and_intents(tmp_path):
    from paperagent.domain.entities import ResearchProfile
    from paperagent.radar.pipeline import LiteratureRadar
    store = DomainStore(tmp_path)
    store.save_profile(ResearchProfile(topics=["digital transformation"]))
    radar = LiteratureRadar(store, search_fn=_fake_search(REAL_CANDIDATES))
    assert radar.topics() == ["digital transformation"]
    radar.run()
    assert len(store.list_radar_hits()) == 2


def test_radar_topics_from_structured_intent(tmp_path):
    from paperagent.radar.pipeline import LiteratureRadar
    store = DomainStore(tmp_path)
    store.add_standing_intent(
        description="watch writing",
        structured_query={"query": "LLM academic writing"},
    )
    radar = LiteratureRadar(store, search_fn=_fake_search(REAL_CANDIDATES))
    assert radar.topics() == ["LLM academic writing"]
    result = radar.run()
    assert result["matched"] == 1


def test_radar_uses_pluggable_evaluator(tmp_path):
    from paperagent.quality.evaluators import EvaluationResult
    from paperagent.radar.pipeline import LiteratureRadar

    class NeverNotify:
        def evaluate(self, *, topic, candidate, match_score):
            return EvaluationResult(
                evaluator="test_never_notify",
                score=match_score,
                should_notify=False,
                rationale="test override",
            )

    store = DomainStore(tmp_path)
    store.add_standing_intent(description="watch", topic="LLM writing")
    radar = LiteratureRadar(store, search_fn=_fake_search(REAL_CANDIDATES),
                            evaluator=NeverNotify())
    result = radar.run()
    assert result["new_hits"] == 2
    assert result["matched"] == 0
    assert store.list_opportunities()[0].ai_evaluation["evaluator"] == "test_never_notify"
    assert not [n for n in store.list_notifications() if n.type == "radar"]


def test_permission_gate_levels():
    gate = PermissionGate()
    assert gate.check(ActionSpec("local_read")) == "allow"
    assert gate.check(ActionSpec("email_send")) == "require_approval"
    assert gate.check(ActionSpec("project_delete")) == "deny"
    assert gate.check(ActionSpec("totally_unknown")) == "deny"
    # loop action mapping covers everything the loop can emit
    for name in ("search", "read_pdf", "import_file", "import_folder", "tool_call", "save_reference", "save_evidence",
                 "write_draft", "ask_user", "chat_reply", "finish"):
        assert gate.check_loop_action(name) == "allow", name


def test_permission_gate_blocks_loop_action(tmp_path):
    """A denying gate must stop the action and surface the reason."""
    from paperagent.agent.decision_loop import DecisionLoop
    from paperagent.agent.scripted import ScriptedAgent
    from paperagent.harness.tool_executor import ToolExecutor
    from paperagent.schemas.policy import ResearchPolicy
    from paperagent.workspace.workspace import Workspace

    ws = Workspace("gate", root=tmp_path).init("goal")
    agent = ScriptedAgent(goal="g", provider="offline_stub")
    gate = PermissionGate(policy={"local_read": "deny"})
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), agent,
                        log=lambda m: None, permission_gate=gate)
    outcome = loop.run("g")
    assert outcome.status == "too_many_errors"
    assert any("permission gate: search -> deny" in str(s.get("error", ""))
               or "permission gate" in str((s.get("result") or {}).get("errors", ""))
               for s in outcome.steps)


def test_event_router_routing():
    tick_log = []
    router = EventRouter(
        scheduler_tick=lambda: tick_log.append("ticked") or ["ticked"],
        radar_run=lambda payload: {"new_hits": 3, "provider": payload.get("provider")},
    )
    r1 = router.route(AssistantEvent(event_id="e1", type="user_message",
                                     payload={"text": "hi"}))
    assert r1["runtime"] == "interactive" and r1["handled"] is False
    r2 = router.route(AssistantEvent(event_id="e2", type="schedule_fire"))
    assert r2["handled"] is True and "ticked" in tick_log
    r3 = router.route(AssistantEvent(event_id="e3", type="radar_run",
                                     payload={"provider": "openalex"}))
    assert r3["result"]["new_hits"] == 3
    r4 = router.route(AssistantEvent(event_id="e4", type="ui_action"))
    assert r4["handled"] is False
    r5 = router.route(AssistantEvent(event_id="e5", type="connector_event",
                                     source="connector:email",
                                     payload={"subject": "hi"}))
    assert r5["runtime"] == "proactive"
    assert r5["handled"] is False


def test_gateway_dedup_and_project_resolution(tmp_path):
    from paperagent.gateway.event_log import EventLog
    from paperagent.gateway.project_resolver import ProjectResolver
    from paperagent.gateway.session_router import SessionRouter
    from paperagent.workspace.workspace import Workspace

    Workspace("proj", root=tmp_path).init("goal")
    calls = []
    router = EventRouter(
        scheduler_tick=lambda: calls.append(1) or ["tick"],
        project_resolver=ProjectResolver(tmp_path),
        session_router=SessionRouter(),
        event_log=EventLog(tmp_path),
    )

    event = AssistantEvent(event_id="same", type="schedule_fire",
                           project_id="proj")
    first = router.route(event)
    second = router.route(event)
    assert first["handled"] is True
    assert first["resolution"]["project_id"] == "proj"
    assert first["resolution"]["session_id"] == "proj:schedule_fire"
    assert second["duplicate"] is True
    assert len(calls) == 1

    missing = router.route(AssistantEvent(event_id="missing",
                                          type="user_message",
                                          project_id="nope",
                                          payload={"text": "hi"}))
    assert missing["handled"] is False
    assert "project not found" in missing["note"]


def test_observation_includes_prospective_memory(tmp_path):
    from paperagent.harness.context_assembler import (assemble_assistant_state,
                                                      assemble_observation)
    from paperagent.workspace.workspace import Workspace

    ws = Workspace("p", root=tmp_path).init("goal")
    store = DomainStore(tmp_path)
    from paperagent.tasks.task_manager import TaskManager
    TaskManager(store).create("改写第二章", due_date="2026-10-05T00:00:00+00:00", project_id="p")
    store.add_deadline(title="课题申报", when="2099-01-01T00:00:00+00:00", scope="p")

    obs = assemble_observation(ws, "goal", 1, 10, 5, None, None, [], store=store)
    assert obs["pending_tasks"][0]["title"] == "改写第二章"
    assert obs["upcoming_deadlines"][0]["title"] == "课题申报"
    assert obs["assistant_state"]["goal"] == "goal"
    assert obs["assistant_state"]["project"]["project_id"] == "p"
    assert obs["assistant_state"]["research_state"]["pending_tasks"][0]["title"] == "改写第二章"
    assert obs["assistant_state"]["pending_actions"][0]["kind"] in {"task", "deadline"}
    assert obs["assistant_state"]["deadline"]["title"] == "课题申报"

    state = assemble_assistant_state(
        ws, "goal", 1, 0, 1, "write_draft",
        {"status": "error", "errors": ["bad citation"]}, [], store=store)
    assert state.quality_risk == "high"
    assert state.stop_condition == "iteration_budget_low"
    assert state.to_observation()["last_action"] == "write_draft"
    # without a store the keys stay absent (V0 behaviour unchanged)
    plain = assemble_observation(ws, "goal", 1, 10, 5, None, None, [])
    assert "pending_tasks" not in plain
    assert plain["assistant_state"]["research_state"]["pending_tasks"] == []
