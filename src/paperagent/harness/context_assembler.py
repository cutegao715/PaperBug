"""Context Assembler: builds the compact observation the agent sees.

Token strategy: full texts never enter context unbounded. Search results
are capped, PDF pages are capped per page, evidence is previewed. The
agent sees state, not raw dumps.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from paperagent.agent.state import (AssistantBudget, AssistantState,
                                    CurrentContext, EvidenceState,
                                    PendingAction, ResearchState)
from paperagent.schemas.artifacts import ToolResult
from paperagent.skills.context import workspace_skill_metadata
from paperagent.tasks.memory_manager import MemoryManager, tokens
from paperagent.tools.registry import describe_tools
from paperagent.workflows import select_workflow
from paperagent.workspace.workspace import Workspace
from paperagent.tasks.session import SessionManager
from paperagent.harness.context_budget import fit_context

MAX_CANDIDATES = 8
MAX_CHARS_PER_PDF_PAGE = 1800
MAX_PDF_PAGES_SHOWN = 5
MAX_OBS_CHARS = 9000


def _evidence_relevance(evidence, goal_tokens: set[str], reference_titles: dict[str, str]) -> int:
    text = evidence.quote + " " + reference_titles.get(evidence.ref_id, "")
    return len(goal_tokens & tokens(text))


def _evidence_time(evidence) -> datetime:
    # Old/imported cards can contain naive or malformed timestamps.
    try:
        value = datetime.fromisoformat(evidence.created_at.replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _evidence_preview(evidence, references: list[dict], goal: str) -> list[dict]:
    goal_tokens = tokens(goal)
    titles = {r["ref_id"]: r.get("title") or "" for r in references}
    ranked = [(_evidence_relevance(e, goal_tokens, titles), i, e)
              for i, e in enumerate(evidence)]
    has_matches = any(score for score, _, _ in ranked)
    if has_matches:
        ranked.sort(key=lambda row: (row[0], _evidence_time(row[2]), row[1]), reverse=True)
        selected = ranked[:10]
    else:
        selected = ranked[-10:]  # Preserve historical chronological fallback.
    return [{"ref_id": e.ref_id, "page": e.page, "quote": e.quote[:80],
             **({"relevance_score": score} if has_matches else {})}
            for score, _, e in selected]


def _compact_candidates(data: dict) -> list[dict]:
    out = []
    for c in (data.get("candidates") or [])[:MAX_CANDIDATES]:
        out.append({
            "ref_id": c.get("ref_id"),
            "title": c.get("title"),
            "year": c.get("year"),
            "venue": c.get("venue"),
            "citation_count": c.get("citation_count"),
            "abstract_snippet": (c.get("abstract_snippet") or "")[:200] or None,
        })
    return out


def _compact_pdf(data: dict) -> dict:
    pages = (data.get("pages") or [])[:MAX_PDF_PAGES_SHOWN]
    return {
        "path": data.get("path"),
        "total_pages": data.get("total_pages"),
        "pages": [
            {"page": p["page"],
             "text": p["text"][:MAX_CHARS_PER_PDF_PAGE],
             "truncated": p.get("truncated")}
            for p in pages
        ],
    }


def compact_result(result: ToolResult) -> dict:
    if result.status == "error":
        return {"status": "error", "errors": result.errors}
    if result.tool_id == "literature_search":
        return {"status": "ok", "candidates": _compact_candidates(result.data or {})}
    if result.tool_id == "pdf_reader":
        return {"status": "ok", **_compact_pdf(result.data or {})}
    return {"status": "ok", "data": str(result.data)[:1000]}


def assemble_observation(ws: Workspace, goal: str, iteration: int,
                         iterations_left: int, tool_calls_left: int,
                         last_action: str | None,
                         last_result_compact: dict | None,
                         notes: list[str],
                         store=None) -> dict:
    state = assemble_assistant_state(
        ws, goal, iteration, iterations_left, tool_calls_left,
        last_action, last_result_compact, notes, store=store)
    obs = state.to_observation()
    obs["iteration"] = iteration
    return obs


def assemble_assistant_state(ws: Workspace, goal: str, iteration: int,
                             iterations_left: int, tool_calls_left: int,
                             last_action: str | None,
                             last_result_compact: dict | None,
                             notes: list[str],
                             store=None) -> AssistantState:
    refs = [{
        "ref_id": r.ref_id, "title": r.title, "year": r.year,
        "venue": r.venue, "source": r.source,
    } for r in ws.list_references()]
    evidence = ws.list_evidence()
    drafts = sorted(p.name for p in ws.drafts_dir.glob("*.md")) if ws.drafts_dir.exists() else []
    draft_plan = ws.read_run_state().get("draft_plan") or {}
    task_id = getattr(ws, "task_id", None)
    checkpoint = SessionManager(ws).context(task_id) if task_id else {}
    from paperagent.conversation import ConversationManager, select_conversation_workflow, CONTINUE
    conversation = ConversationManager(ws).context() if not task_id else {}
    if conversation:
        checkpoint = conversation
    if checkpoint:
        draft_plan = checkpoint.get("draft_plan") or {}
    selection_goal = goal
    if checkpoint and (goal.strip().lower() in CONTINUE or (conversation and getattr(ws, "conversation_id", None))):
        selection_goal = checkpoint.get("request") or checkpoint.get("objective") or goal
    workflow = select_conversation_workflow(ws, selection_goal, conversation) if conversation else select_workflow(
        ws, selection_goal, checkpoint.get("workflow_id"), checkpoint.get("workflow_options"))
    project_memory = ws.refresh_project_memory(workflow=workflow)
    if checkpoint:
        project_memory = {**project_memory, "draft_plan": draft_plan,
                          "current_draft": (checkpoint.get("target") or {}).get("filename")}
    recent_messages = [
        {
            "role": m.get("role"),
            "content": str(m.get("content", "")),
            "created_at": m.get("created_at"),
            "task_id": m.get("task_id"),
            "conversation_id": m.get("conversation_id"),
        }
        for m in ws.list_messages(limit=12)
    ]
    current_context = CurrentContext(
        saved_references=refs,
        evidence_preview=_evidence_preview(evidence, refs, selection_goal),
        drafts=drafts,
        draft_plan=draft_plan,
        project_memory=project_memory,
        recent_messages=recent_messages,
        filesystem={
            "allowed_roots": ws.list_allowed_roots(),
            "policy": "Read access is limited to the project workspace and user-authorized roots. Importing a folder authorizes that folder for this project.",
        },
        recent_tool_calls=[
            {
                "call_id": c.get("call_id"),
                "tool_id": c.get("tool_id"),
                "status": c.get("status"),
                "errors": c.get("errors", [])[:3],
                "created_at": c.get("created_at"),
            }
            for c in ws.list_tool_calls(limit=8)
        ],
        last_action=last_action,
        last_result=last_result_compact,
        notes=notes,
    )
    current_context.workflow = workflow
    if workflow:
        from paperagent.workflows.runtime import WorkflowRuntime
        workflow["cases"] = WorkflowRuntime(ws).summary()
    from paperagent.workflows.lifecycle import detect_lifecycle
    current_context.lifecycle = workflow.get("lifecycle") if workflow else detect_lifecycle(ws, goal)
    current_context.task_checkpoint = checkpoint if task_id else {}
    current_context.conversation = conversation
    evidence_state = EvidenceState(evidence_count=len(evidence))
    research_state = ResearchState()
    pending_actions: list[PendingAction] = []
    deadline = None
    project_profile = None
    if store is not None:
        research_state, pending_actions, deadline = _assemble_research_state(store, ws.project_id)
        profile = store.get_profile()
        current_context.profile = profile.model_dump(exclude_none=True)
        project_profile_obj = store.get_project_profile(ws.project_id)
        project_profile = project_profile_obj.model_dump(exclude_none=True)
        current_context.memories = MemoryManager(store).relevant(
            project_id=ws.project_id, goal=selection_goal, task_id=task_id)
        current_context.connector_state = [
            {"watcher_id": w.watcher_id, "name": w.name,
             "watcher_type": w.watcher_type, "source": w.source,
             "enabled": w.enabled, "cursor": w.cursor,
             "last_checked": w.last_checked}
            for w in store.list_watchers()
            if w.query.get("project_id") in (None, ws.project_id)
        ][:10]
    current_context.skills = workspace_skill_metadata(
        ws, "\n".join(dict.fromkeys([selection_goal, goal])), workflow=workflow,
        lifecycle=current_context.lifecycle, store=store,
    )
    from paperagent.domain.manuscript import ManuscriptContext
    current_context.manuscript = ManuscriptContext(ws).context()
    from paperagent.domain.paper_review import PaperReviews
    current_context.paper_reviews = PaperReviews(ws).context()
    current_context.tools = describe_tools(ws.root)
    state = AssistantState(
        goal=goal,
        intent=goal,
        project={"project_id": ws.project_id, "goal": ws.goal,
                 "profile": project_profile} if project_profile else {
            "project_id": ws.project_id, "goal": ws.goal},
        task={"task_id": task_id, "objective": checkpoint["objective"], "status": checkpoint["status"]}
        if task_id and checkpoint else None,
        current_context=current_context,
        evidence_state=evidence_state,
        research_state=research_state,
        pending_actions=pending_actions,
        quality_risk=_quality_risk(last_result_compact),
        deadline=deadline,
        budget=AssistantBudget(iterations_left=iterations_left,
                               tool_calls_left=tool_calls_left),
        stop_condition=_stop_condition(iterations_left, tool_calls_left),
    )
    return fit_context(state)


def _assemble_research_state(store, project_id: str | None = None) -> tuple[ResearchState, list[PendingAction], dict[str, Any] | None]:
    """Prospective Memory view: what the future demands, from Domain State.

    Upcoming deadlines and open tasks come from the Domain Layer (the
    truth); this only shapes them into assistant context.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    deadlines = []
    for dl in store.list_deadlines():
        if project_id and dl.scope != project_id:
            continue
        try:
            when = datetime.fromisoformat(dl.when)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when < now:
            continue
        deadlines.append({"deadline_id": dl.deadline_id, "title": dl.title,
                          "when": dl.when, "scope": dl.scope,
                          "hours_left": round((when - now).total_seconds() / 3600)})
    deadlines.sort(key=lambda d: d["hours_left"])
    tasks = [{"task_id": t.task_id, "title": t.title, "status": t.status,
              "priority": t.priority, "due_date": t.due_date,
              "project_id": t.project_id}
             for t in store.list_tasks() if t.status in ("todo", "in_progress")
             and (not project_id or t.project_id == project_id)]
    tasks.sort(key=lambda t: (t["priority"], t.get("due_date") or "9999"))
    intents = [{"intent_id": si.intent_id, "description": si.description,
                "type": si.type, "topic": si.topic,
                "structured_query": si.structured_query,
                "scope": si.scope,
                "notify_policy": si.notify_policy,
                "dedup_key": si.dedup_key}
               for si in store.list_standing_intents() if si.enabled
               and (not project_id or si.scope.get("project_id") == project_id)]
    opportunities = [] if project_id else store.list_opportunities(limit=5)
    hits = [{"opportunity_id": o.opportunity_id, "title": o.title,
             "radar_type": o.opportunity_type, "matched": o.matched,
             "match_score": o.match_score, "seen": o.seen,
             "notified": o.notified, "created_at": o.created_at}
            for o in opportunities]
    if not hits and not project_id:
        hits = [{"hit_id": h.hit_id, "title": h.title, "radar_type": h.radar_type,
                 "matched": h.matched, "seen": h.seen, "created_at": h.created_at}
                for h in store.list_radar_hits(limit=5)]
    unread = 0 if project_id else sum(1 for n in store.list_notifications() if not n.read)
    pending_actions: list[PendingAction] = []
    for task in tasks[:5]:
        pending_actions.append(PendingAction(
            kind="task", title=task["title"], ref=task["task_id"],
            priority=task["priority"], due_at=task.get("due_date")))
    for dl in deadlines[:3]:
        priority = 1 if dl["hours_left"] <= 24 else 2
        pending_actions.append(PendingAction(
            kind="deadline", title=dl["title"], ref=dl["deadline_id"],
            priority=priority, due_at=dl["when"]))
    if unread:
        pending_actions.append(PendingAction(
            kind="notification", title=f"{unread} unread notifications",
            priority=3))
    pending_actions.sort(key=lambda a: (a.priority, a.due_at or "9999"))
    return (
        ResearchState(
            upcoming_deadlines=deadlines[:5],
            pending_tasks=tasks[:10],
            standing_intents=intents[:10],
            recent_radar_hits=hits,
            unread_notifications=unread,
        ),
        pending_actions[:10],
        deadlines[0] if deadlines else None,
    )


def _quality_risk(last_result: dict | None) -> str:
    if not last_result:
        return "low"
    if last_result.get("status") == "error" or last_result.get("errors"):
        return "high"
    kernel = last_result.get("kernel") or {}
    if kernel.get("errors"):
        return "high"
    if kernel.get("warnings"):
        return "medium"
    return "low"


def _stop_condition(iterations_left: int, tool_calls_left: int) -> str | None:
    if iterations_left <= 1:
        return "iteration_budget_low"
    if tool_calls_left <= 1:
        return "tool_budget_low"
    return None
