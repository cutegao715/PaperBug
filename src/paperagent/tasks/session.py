"""Durable task checkpoints; resume re-observes state, never replays side effects."""
from __future__ import annotations

import json
import re
import uuid
from pydantic import BaseModel, Field
from paperagent.domain.entities import iso, utcnow
from paperagent.workspace.state_store import read_json, state_lock, write_json
from paperagent.workspace.repositories import FileSessionRepository


class ResearchSession(BaseModel):
    task_id: str
    project_id: str
    objective: str
    instructions: list[str] = Field(default_factory=list)
    workflow_id: str | None = None
    workflow_options: dict = Field(default_factory=dict)
    workflow_option_locks: list[str] = Field(default_factory=list)
    status: str = "pending"
    run_ids: list[str] = Field(default_factory=list)
    plan: dict = Field(default_factory=dict)
    draft_plan: dict = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    summary: str = ""
    target: dict | None = None
    last_step: dict | None = None
    step_count: int = 0
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    updated_at: str = Field(default_factory=lambda: iso(utcnow()))


class SessionManager:
    def __init__(self, ws):
        self.ws = ws
        self.dir = ws.dir / "tasks"

    def path(self, task_id: str):
        if not re.fullmatch(r"task_[a-f0-9]{16}", task_id):
            raise ValueError("Invalid task ID")
        return self.dir / f"{task_id}.json"

    def get(self, task_id: str) -> ResearchSession:
        path = self.path(task_id)
        if not path.exists():
            raise KeyError(task_id)
        return ResearchSession.model_validate(FileSessionRepository(path).get())

    def list(self) -> list[ResearchSession]:
        if not self.dir.exists():
            return []
        return sorted((ResearchSession.model_validate(read_json(p)) for p in self.dir.glob("task_*.json")),
                      key=lambda s: s.updated_at, reverse=True)

    def create(self, goal: str, workflow_id: str | None = None) -> ResearchSession:
        state = ResearchSession(task_id=f"task_{uuid.uuid4().hex[:16]}",
                                project_id=self.ws.project_id, objective=goal,
                                workflow_id=workflow_id)
        self.save(state)
        return state

    def begin(self, goal: str, run_id: str, *, task_id: str | None = None,
              workflow_id: str | None = None) -> ResearchSession:
        task_id = task_id or f"task_{uuid.uuid4().hex[:16]}"
        with state_lock(self.path(task_id)):
            state = self.get(task_id) if self.path(task_id).exists() else ResearchSession(
                task_id=task_id, project_id=self.ws.project_id, objective=goal)
            state.instructions.append(goal)
            if workflow_id is not None:
                state.workflow_id = workflow_id
            state.run_ids.append(run_id)
            state.status = "running"
            self.save(state)
            self.ws.task_id = task_id
            return state

    def save(self, state: ResearchSession):
        state.updated_at = iso(utcnow())
        FileSessionRepository(self.path(state.task_id)).save(state.model_dump())

    def update(self, task_id: str, *, plan: dict | None = None,
               open_questions: list[str] | None = None,
               decisions: list[str] | None = None,
               workflow_options: dict | None = None,
               workflow_options_actor: str = "user") -> ResearchSession:
        with state_lock(self.path(task_id)):
            state = self.get(task_id)
            if workflow_options is not None:
                from paperagent.workflows.project_contract import ProjectWorkflowOptions
                workflow_options = ProjectWorkflowOptions.model_validate(workflow_options).model_dump(mode="json", exclude_none=True)
                if workflow_options_actor == "agent":
                    if any(key in workflow_options and workflow_options[key] != state.workflow_options.get(key)
                           for key in state.workflow_option_locks):
                        raise ValueError("Explicit user workflow requirements cannot be changed by the Agent")
                    workflow_options = {**state.workflow_options, **workflow_options}
                else:
                    state.workflow_option_locks = list(workflow_options)
            for key, value in {"plan": plan, "open_questions": open_questions,
                               "decisions": decisions, "workflow_options": workflow_options}.items():
                if value is not None:
                    setattr(state, key, value)
            self.save(state)
            return state

    def checkpoint(self, task_id: str, *, step: dict | None = None,
                   status: str | None = None, summary: str | None = None,
                   draft: str | None = None):
        with state_lock(self.path(task_id)):
            state = self.get(task_id)
            if step is not None:
                event_path = self.dir / f"{task_id}.events.jsonl"
                with event_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(step, ensure_ascii=False, default=str) + "\n")
                action = step.get("action") or {}
                result = step.get("result") or {}
                state.step_count += 1
                state.last_step = {"event": state.step_count, "iteration": step.get("iteration"),
                                   "action": action.get("action") if isinstance(action, dict) else action,
                                   "status": result.get("status"),
                                   "errors": result.get("errors", []) or ([step["error"]] if step.get("error") else []),
                                   "source": str(event_path)}
                if isinstance(action, dict) and action.get("action") == "ask_user":
                    state.open_questions = [action["question"]]
                if isinstance(action, dict) and action.get("action") == "update_draft_plan" and result.get("status") == "ok":
                    state.draft_plan = self.ws.read_run_state().get("draft_plan") or {}
            if status is not None:
                state.status = status
            if summary is not None:
                state.summary = summary
            if draft and (self.ws.drafts_dir / draft).exists():
                state.target = self.ws.snapshot_draft(draft)
            self.save(state)
            return state

    def context(self, task_id: str) -> dict:
        state = self.get(task_id)
        view = state.model_dump()
        # Preserve all explicit task steering until a reviewed compaction exists.
        # Budget overflow is explicit rather than silently losing early constraints.
        view["instructions"] = state.instructions
        view["instruction_count"] = len(state.instructions)
        view["source"] = str(self.path(task_id))
        if state.target:
            path = self.ws.drafts_dir / state.target["filename"]
            import hashlib
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            view["target"] = {**state.target, "current_version": current,
                              "changed": current != state.target["version"]}
        return view
