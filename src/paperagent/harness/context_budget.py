"""Provider-neutral, conservative estimate. Never silently drop protected state."""
from __future__ import annotations

import copy
import json
import math
import os


class ContextBudgetExceeded(ValueError):
    pass


def estimate_tokens(value) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    non_ascii = sum(ord(c) > 127 for c in text)
    return math.ceil((len(text) - non_ascii) / 3) + non_ascii * 2


from paperagent.product_config import SECTION_RATIOS, load


def _protected_memory(row):
    return row.get("trust") == "user_adopted" or row.get("pinned") or row.get("kind") in {"decision", "preference"}


def _trim_section(context, key):
    value = context.get(key)
    if not value:
        return False
    if key == "memories":
        for layer in ("episodic", "working", "prospective", "project", "user"):
            rows = value.get(layer, [])
            index = next((i for i in range(len(rows)-1, -1, -1) if not _protected_memory(rows[i])), None)
            if index is not None:
                rows.pop(index)
                return True
    elif key == "project_memory":
        for field in ("materials", "template_sections", "drafts", "last_run"):
            if field in value:
                value.pop(field)
                return True
    elif key == "workflow":
        if value.get("playbook"):
            value.pop("playbook")
            return True
        if value.get("skill_recommendations"):
            value["skill_recommendations"].pop()
            return True
    elif isinstance(value, list):
        if key == "recent_messages" and len(value) <= 2:
            return False
        if key == "evidence_preview" and any(r.get("relevance_score", 0) > 0 for r in value):
            index = min(range(len(value)), key=lambda i: (value[i].get("relevance_score", 0), -i))
        else:
            index = -1 if key == "skills" else 0
        value.pop(index)
        return True
    return False


def fit_context(state, *, input_budget: int | None = None):
    from paperagent.agent.prompts import ACTION_PROTOCOL
    config = load().budget
    budget = input_budget if input_budget is not None else config.context_input_tokens
    ratios = config.section_ratios
    data = state.model_dump(mode="json", exclude_none=True)
    context = data["current_context"]
    dropped = []

    def size():
        return estimate_tokens(data) + estimate_tokens(ACTION_PROTOCOL)

    initial = size()
    # Declarative workflow guidance is rebuildable and readable on demand. Do
    # not evict author decisions or fail a grant run because the directory grew.
    workflow = context.get("workflow") or {}
    if workflow.get("workflow_contract"):
        workflow["workflow_contract"] = {
            "code": workflow["workflow_contract"]["code"],
            "rules": workflow["workflow_contract"].get("rules", []),
            "read_with": {"action": "read_context", "kind": "workflow_contract",
                          "key": workflow.get("canonical_workflow_id", workflow["workflow_id"])} }
        workflow.get("playbook", {}).pop("steps", None)
    if size() > budget - 256:
        last = context.get("last_result") or {}
        if last.get("content") or last.get("data"):
            context["last_result"] = {
                key: value for key, value in last.items()
                if key in {"status", "errors", "draft", "source", "offset", "next_offset"}}
            context["last_result"].update({
                "deferred": True,
                "read_more": {"action": "read_context", "kind": "conversation_events" if context.get("conversation") else "task_events",
                              "key": f"event:{(context.get('conversation') or context.get('task_checkpoint', {})).get('step_count', 0)}",
                              "max_chars": 4000},
                "note": "Full result persisted in the execution event log; read in bounded pages."})
            dropped.append("last_result_body")
    # Reserve immutable state, unsegmented fields, protocol, and diagnostic space first.
    minimal = copy.deepcopy(data)
    for key in SECTION_RATIOS:
        while _trim_section(minimal["current_context"], key):
            pass
    protected_size = estimate_tokens(minimal) + estimate_tokens(ACTION_PROTOCOL)
    available = max(0, budget - protected_size - 1400)
    per_section = {}
    for key, ratio in ratios.items():
        protected_tokens = estimate_tokens(minimal["current_context"].get(key))
        limit = protected_tokens + int(available * ratio)
        before = estimate_tokens(context.get(key))
        while estimate_tokens(context.get(key)) > limit and _trim_section(context, key):
            dropped.append(key)
        per_section[key] = {"initial_tokens": before, "limit": limit,
                            "protected_tokens": protected_tokens,
                            "read_more": "read_context or source files"}
    if size() > budget - 256:
        # Specialized guidance is also reconstructible. Keep scope, explicit
        # requirements, user decisions and actual state in the observation.
        if workflow.get("playbook"):
            workflow["playbook"] = {"principle": workflow["playbook"].get("principle", "Read the current contract as needed"),
                                    "read_with": {"action": "read_context", "kind": "workflow_contract",
                                                  "key": workflow.get("canonical_workflow_id", workflow["workflow_id"])}}
            dropped.append("workflow.playbook")
    if size() > budget - 256:
        # Full conditional directory is available through workflow_contract.
        refs = workflow.get("skill_recommendations", [])
        if len(refs) > 3:
            workflow["skill_recommendations"] = refs[:3]
            dropped.append("workflow.support_recommendations")
    # The original material remains readable through read_context / filesystem tools.
    for key in ("connector_state", "recent_tool_calls", "evidence_preview", "saved_references"):
        while size() > budget - 256 and context.get(key):
            rows = context[key]
            if key == "evidence_preview" and any(row.get("relevance_score", 0) > 0 for row in rows):
                # Ranked previews are best-first, with newest cards first on ties.
                index = min(range(len(rows)), key=lambda i: (rows[i].get("relevance_score", 0), -i))
            else:
                index = 0  # Legacy/zero-match previews are oldest-first.
            rows.pop(index)
            if key not in dropped:
                dropped.append(key)
    for layer in ("episodic", "working", "prospective", "project", "user"):
        rows = context.get("memories", {}).get(layer, [])
        while size() > budget - 256:
            index = next((i for i in range(len(rows) - 1, -1, -1)
                          if not _protected_memory(rows[i])), None)
            if index is None:
                break
            rows.pop(index)
            dropped.append(f"memories.{layer}")
    while size() > budget - 256 and len(context.get("recent_messages", [])) > 2:
        context["recent_messages"].pop(0)
        dropped.append("older_messages")
    if size() > budget - 256:
        # Rebuildable inventory, not the current request, decisions or task plan.
        project_memory = context.get("project_memory", {})
        for key in ("materials", "template_sections", "drafts", "last_run"):
            if key in project_memory and size() > budget - 256:
                project_memory.pop(key)
                dropped.append(f"project_memory.{key}")
    for key, values in per_section.items():
        values["estimated_tokens"] = estimate_tokens(context.get(key))
    diagnostics = {"per_section": per_section, "estimated_input_tokens": size(), "input_budget": budget,
                   "initial_estimate": initial, "omitted": sorted(set(dropped)),
                   "counting": "conservative_estimate_including_system_prompt",
                   "read_more": "read_context or the source files; omissions are not deleted"}
    context["context_diagnostics"] = diagnostics
    diagnostics["estimated_input_tokens"] = size()
    if size() > budget:
        raise ContextBudgetExceeded(
            f"Protected context exceeds input budget ({size()} > {budget}); "
            "narrow the active task/material or increase PAPERAGENT_CONTEXT_INPUT_TOKENS. "
            "The current request and task decisions were preserved.")
    return type(state).model_validate(data)
