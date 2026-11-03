import pytest
from paperagent.agent.state import AssistantState, CurrentContext
from paperagent.harness.context_budget import fit_context, ContextBudgetExceeded


def test_oversized_evidence_does_not_displace_other_sections():
    state=AssistantState(goal="keep request",current_context=CurrentContext(
        evidence_preview=[{"quote":"x"*100000}],
        saved_references=[{"ref_id":"keep","title":"Relevant"}],
        recent_messages=[{"role":"user","content":"keep message"}]))
    result=fit_context(state,input_budget=24000)
    c=result.current_context
    assert c.saved_references==state.current_context.saved_references
    assert c.recent_messages==state.current_context.recent_messages
    assert c.context_diagnostics["per_section"]["evidence_preview"]["estimated_tokens"] <= c.context_diagnostics["per_section"]["evidence_preview"]["limit"]
    assert "evidence_preview" in c.context_diagnostics["omitted"]
    assert c.context_diagnostics["estimated_input_tokens"] <= 24000


def test_protected_content_still_fails_explicitly():
    state=AssistantState(goal="x"*100000)
    with pytest.raises(ContextBudgetExceeded):fit_context(state,input_budget=12000)


def test_deferred_result_does_not_consume_other_section_budgets():
    state = AssistantState(goal="request", current_context=CurrentContext(
        last_result={"content": "x" * 100000},
        evidence_preview=[{"quote": "keep"}], saved_references=[{"title": "keep"}]))
    result = fit_context(state)
    assert result.current_context.evidence_preview == state.current_context.evidence_preview
    assert result.current_context.saved_references == state.current_context.saved_references
    assert result.current_context.last_result["deferred"]
