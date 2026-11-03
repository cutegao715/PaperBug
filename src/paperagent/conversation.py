"""One ongoing conversation per research space; no task creation or task lifecycle.

Each run checkpoints its operations. Starting another request archives the previous
working context; only explicit continuation reuses its plan and delivery contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from pydantic import BaseModel, Field
from paperagent.workspace.state_store import read_json, state_lock, write_json
from paperagent.workspace.repositories import FileSessionRepository


CONTINUE = {"继续", "继续吧", "接着写", "继续上次", "continue", "resume"}


class ConversationState(BaseModel):
    conversation_id: str = "main"
    request: str = ""
    instructions: list[str] = Field(default_factory=list)
    run_id: str | None = None
    status: str = "idle"
    plan: dict = Field(default_factory=dict)
    draft_plan: dict = Field(default_factory=dict)
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    target: dict | None = None
    workflow_id: str | None = None
    workflow_mode: str = "auto"
    workflow_options: dict = Field(default_factory=dict)
    workflow_option_locks: list[str] = Field(default_factory=list)
    summary: str = ""
    last_step: dict | None = None
    step_count: int = 0


class ConversationManager:
    def __init__(self, ws):
        self.ws = ws
        self.dir = ws.dir / "conversations"

    def path(self, key="main"):
        if key != "main":
            raise ValueError("Unknown conversation")
        return self.dir / "main.json"

    def get(self, key="main"):
        return ConversationState.model_validate(FileSessionRepository(self.path(key)).get())

    def save(self, state):
        FileSessionRepository(self.path()).save(state.model_dump())

    def begin(self, goal, run_id, *, resume=False, workflow_id=None,
              workflow_mode=None, workflow_options=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError("Invalid run ID")
        with state_lock(self.path()):
            state = self.get()
            continuing = bool(state.request and (resume or goal.strip().lower() in CONTINUE))
            if state.run_id:
                write_json(self.dir / "runs" / f"{state.run_id}.json", state.model_dump())
            if not continuing:
                state.request = goal
                state.instructions = []
                state.plan, state.draft_plan = {}, {}
                state.open_questions = []
                state.workflow_id, state.workflow_mode = None, "auto"
                state.workflow_options, state.workflow_option_locks = {}, []
            state.instructions.append(goal)
            if workflow_id is not None:
                state.workflow_id = workflow_id
            if workflow_mode is not None:
                state.workflow_mode = workflow_mode
                if workflow_mode == "off":
                    state.workflow_id = None
            if workflow_options is not None:
                from paperagent.workflows.project_contract import ProjectWorkflowOptions
                options = ProjectWorkflowOptions.model_validate(workflow_options).model_dump(mode="json", exclude_none=True)
                state.workflow_options = {**state.workflow_options, **options} if continuing and options else options
                state.workflow_option_locks = list(state.workflow_options)
            state.run_id, state.status = run_id, "running"
            state.summary = ""
            self.save(state)
            self.ws.conversation_id = "main"
            self.ws.task_id = None
            return state

    def focus(self, filename):
        # Validate and snapshot through Workspace before changing the pointer.
        target = self.ws.snapshot_draft(filename) if filename else None
        with state_lock(self.path()):
            state = self.get()
            state.target = target
            self.save(state)
        return self.context()

    def update(self, key="main", *, plan=None, open_questions=None, decisions=None,
               workflow_options=None, workflow_options_actor="agent"):
        with state_lock(self.path(key)):
            state = self.get(key)
            if workflow_options is not None:
                from paperagent.workflows.project_contract import ProjectWorkflowOptions
                options = ProjectWorkflowOptions.model_validate(workflow_options).model_dump(mode="json", exclude_none=True)
                if workflow_options_actor == "agent":
                    if any(k in options and options[k] != state.workflow_options.get(k) for k in state.workflow_option_locks):
                        raise ValueError("Explicit user workflow requirements cannot be changed by the Agent")
                    options = {**state.workflow_options, **options}
                else:
                    state.workflow_option_locks = list(options)
                state.workflow_options = options
            for name, value in {"plan":plan, "open_questions":open_questions, "decisions":decisions}.items():
                if value is not None:
                    setattr(state, name, value)
            self.save(state)
            return state

    def checkpoint(self, key="main", *, step=None, status=None, summary=None, draft=None):
        with state_lock(self.path(key)):
            state = self.get(key)
            if step is not None:
                self.dir.mkdir(parents=True, exist_ok=True)
                event_path = self.dir / "main.events.jsonl"
                with event_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(step, ensure_ascii=False, default=str) + "\n")
                state.step_count += 1
                action, result = step.get("action") or {}, step.get("result") or {}
                name = action.get("action") if isinstance(action, dict) else action
                state.last_step = {"event":state.step_count, "action":name,
                                   "status":result.get("status"),
                                   "errors":result.get("errors", []) or ([step["error"]] if step.get("error") else []),
                                   "source":str(event_path)}
                if name == "ask_user":
                    state.open_questions = [action["question"]]
                if name == "update_draft_plan" and result.get("status") == "ok":
                    state.draft_plan = self.ws.read_run_state().get("draft_plan") or {}
            if status is not None:
                state.status = status
            if summary is not None:
                state.summary = summary
            if draft:
                state.target = self.ws.snapshot_draft(draft)
            self.save(state)
            if state.run_id:
                write_json(self.dir / "runs" / f"{state.run_id}.json", state.model_dump())
            return state

    def context(self, key="main"):
        state = self.get(key)
        data = state.model_dump()
        data["source"] = str(self.path(key))
        data["history_source"] = str(self.dir / "runs")
        history = sorted((self.dir / "runs").glob("*.json"), key=lambda p:p.stat().st_mtime_ns, reverse=True)
        data["recent_runs"] = []
        for path in history:
            if path.stem == state.run_id:
                continue
            previous = read_json(path)
            data["recent_runs"].append({"run_id":path.stem, "request":previous.get("request", "")[:200],
                                        "status":previous.get("status"), "read_with":{"action":"read_context", "kind":"conversation", "key":path.stem}})
            if len(data["recent_runs"]) == 6:
                break
        if state.target:
            path = self.ws.drafts_dir / state.target["filename"]
            version = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            data["target"] = {**state.target, "current_version":version, "changed":version != state.target["version"]}
        return data


def select_conversation_workflow(ws, goal, context):
    """Business delivery intent activates a workflow; a stage alone never does."""
    from paperagent.workflows.registry import select_workflow
    if context.get("workflow_mode") == "off":
        return None
    preferred = context.get("workflow_id")
    if preferred or (context.get("workflow_options") or {}).get("case_id"):
        return select_workflow(ws, goal, preferred, context.get("workflow_options"))
    local = r"只|仅|摘要|这一段|这段|润色|修改|优化|大纲|提纲|格式|翻译|解释|什么|如何|怎么|建议|思路|解读|评审|检查|评价|分析|讨论|选题|综述"
    deliverable = r"申请书|申报材料|论证活页|中期报告|进展报告|阶段报告|结题报告|结题材料|结项报告|投稿材料|投稿包|返修材料|回复信|送审材料|答辩材料|归档包"
    local_goal = goal.replace("投稿与评审管理", "投稿管理").replace("管理投稿与评审", "管理投稿")
    if re.search(local, local_goal):
        return None
    from paperagent.workflows.lifecycle import detect_lifecycle
    lifecycle = detect_lifecycle(ws, goal)
    kind = (lifecycle or {}).get("lifecycle_type")
    selected = None
    if kind == "project" and re.search(r"(?:项目|课题|基金|申报|资助)机会|(?:寻找|搜索|查找|发掘).*(?:申报指南|课题通知|基金通知)", goal):
        selected = "project_opportunity_discovery"
    elif kind == "paper_conference":
        if re.search(r"投稿管理|投稿与评审管理|投稿机会|期刊匹配|选刊|推荐.*(?:期刊|会议)|匹配.*(?:期刊|会议)|投稿材料|投稿包|返修材料|回复信|改投材料|管理.*投稿|(?:跟踪|追踪|登记|监测).*投稿|投稿状态|(?:寻找|搜索|查找|发掘).*征稿", goal):
            selected = "venue_matching" if re.search(r"投稿机会|期刊匹配|选刊|推荐.*(?:期刊|会议)|匹配.*(?:期刊|会议)|投稿材料|投稿包|征稿", goal) else "submission_tracking"
        elif re.search(r"论文全文|论文初稿|完整.*论文|撰写.*论文", goal):
            selected = "paper_writing"
    elif kind == "thesis":
        if re.search(r"答辩(?:材料|ppt|讲稿|草稿|问答|演练)|(?:准备|制作|组织).*答辩", goal, re.I):
            selected = "thesis_defense"
        elif re.search(r"开题报告", goal):
            selected = "thesis_proposal"
        elif re.search(r"中期报告|中期检查", goal):
            selected = "thesis_midterm_report"
        elif re.search(r"论文全文|论文初稿|完整.*论文|(?:学位|毕业)论文撰写", goal):
            selected = "thesis_writing"
    if selected:
        return select_workflow(ws, goal, selected, context.get("workflow_options"))
    if not re.search(deliverable, goal):
        return None
    return select_workflow(ws, goal, workflow_options=context.get("workflow_options"))
