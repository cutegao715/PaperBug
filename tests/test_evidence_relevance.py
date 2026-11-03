"""Goal-related evidence selection and budget eviction must agree."""
import pytest
from paperagent.agent.state import AssistantState, CurrentContext
from paperagent.agent.prompts import ACTION_PROTOCOL
from paperagent.conversation import ConversationManager
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.harness.context_budget import estimate_tokens, fit_context
from paperagent.schemas.evidence import Evidence
from paperagent.schemas.references import ReferenceCandidate
from paperagent.workspace.workspace import Workspace


def observe(ws, goal):
    return assemble_assistant_state(ws, goal, 0, 10, 10, None, None, []).current_context.evidence_preview


def add(ws, i, quote, created_at=None):
    kw = {"created_at": created_at} if created_at else {}
    ws.save_evidence(Evidence(evidence_id=f"e{i}", ref_id=f"r{i}", quote=quote, **kw))


def test_older_goal_matches_survive_recent_unrelated_cards(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "100000")
    ws = Workspace("p", tmp_path).init("unrelated project")
    for i in range(20):
        add(ws, i, "Bayesian network result" if i < 5 else "unrelated observation", f"2026-01-{i+1:02d}T00:00:00+00:00")
    preview = observe(ws, "Bayesian network")
    assert len(preview) == 10
    assert [r["ref_id"] for r in preview[:5]] == [f"r{i}" for i in reversed(range(5))]
    assert all(r["relevance_score"] == 2 for r in preview[:5])


def test_title_and_untruncated_quote_use_resumed_request(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "100000")
    ws = Workspace("p", tmp_path).init("unrelated")
    ws.save_reference(ReferenceCandidate(ref_id="r0", title="Bayesian network"))
    add(ws, 0, "source statement")
    add(ws, 1, "x" * 100 + " Bayesian network")
    for i in range(2, 20):
        add(ws, i, "unrelated")
    ConversationManager(ws).begin("Bayesian network", "test-run", workflow_mode="off")
    preview = observe(ws, "继续")
    assert {r["ref_id"] for r in preview[:2]} == {"r0", "r1"}
    assert next(r["quote"] for r in preview if r["ref_id"] == "r1") == "x" * 80


@pytest.mark.parametrize("goal", ["", "missingtopic"])
def test_zero_matches_keep_legacy_shape_order_and_storage(tmp_path, monkeypatch, goal):
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "100000")
    ws = Workspace("p", tmp_path).init("test")
    for i in range(20):
        add(ws, i, "unrelated " + str(i))
    before = ws.evidence_path.read_bytes()
    assert observe(ws, goal) == [{"ref_id": f"r{i}", "page": None, "quote": "unrelated " + str(i)} for i in range(10, 20)]
    assert ws.evidence_path.read_bytes() == before


def test_score_ties_use_actual_time_then_append_order(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "100000")
    ws = Workspace("p", tmp_path).init("test")
    add(ws, 0, "Bayesian", "2026-01-01T10:00:00+08:00")
    add(ws, 1, "Bayesian", "2026-01-01T01:00:00+00:00")
    add(ws, 2, "Bayesian", "2026-01-01T02:00:00+00:00")
    add(ws, 3, "Bayesian", "invalid old timestamp")
    assert [r["ref_id"] for r in observe(ws, "Bayesian")] == ["r2", "r0", "r1", "r3"]


@pytest.mark.parametrize("ranked", [True, False])
def test_budget_removes_low_rank_or_oldest_fallback(ranked):
    cards = [{"ref_id": f"r{i}", "quote": "x" * 4500, **({"relevance_score": 4-i} if ranked else {})} for i in range(4)]
    empty = AssistantState(goal="test", current_context=CurrentContext())
    budget = estimate_tokens(empty.model_dump(mode="json", exclude_none=True)) + estimate_tokens(ACTION_PROTOCOL) + 22000
    state = empty.model_copy(deep=True)
    state.current_context.evidence_preview = cards
    result = fit_context(state, input_budget=budget)
    kept = result.current_context.evidence_preview
    assert len(kept) == 1
    assert kept[0]["ref_id"] == ("r0" if ranked else "r3")
    assert len(state.current_context.evidence_preview) == 4
    assert "evidence_preview" in result.current_context.context_diagnostics["omitted"]

