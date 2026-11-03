"""Control Service: FastAPI app wrapping the decision loop.

- one research space has an ongoing conversation; each message starts a run
- run steps stream to the browser via SSE, straight from the loop's
  on_step callback - no faked progress
- stop button hits /cancel; the loop exits at the next iteration boundary
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.agent.llm_agent import LLMAgent
from paperagent.agent.scripted import ScriptedAgent
from paperagent.connectors.email import LocalEmailConnector
from paperagent.domain.store import DomainStore
from paperagent.gateway.event_log import EventLog
from paperagent.gateway.events import AssistantEvent
from paperagent.gateway.project_resolver import ProjectResolver
from paperagent.gateway.router import EventRouter
from paperagent.gateway.session_router import SessionRouter
from paperagent.harness.llm import LLMClient
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.quality.evaluators import EvidenceEvaluator
from paperagent.radar.pipeline import LiteratureRadar
from paperagent.schemas.policy import ResearchPolicy
from paperagent.skills.loader import SkillLoader
from paperagent.tasks.grant_discovery import GrantDiscovery
from paperagent.tasks.journal_recommender import JournalRecommender
from paperagent.tasks.memory_manager import MemoryManager
from paperagent.tasks.notification_service import NotificationService
from paperagent.tasks.scheduler import Scheduler
from paperagent.tasks.submission_manager import SubmissionManager
from paperagent.tasks.submission_parser import SubmissionEmailParser
from paperagent.tasks.task_manager import TaskManager
from paperagent.tasks.watcher import WatcherManager
from paperagent.tools.importers import run_file_import
from paperagent.tools.registry import describe_tools
from paperagent.workspace.render import render_project_draft
from paperagent.workspace.export import (
    export_draft_docx,
    export_draft_docx_with_template,
    export_draft_wps,
    extract_docx_template_fields,
)
from paperagent.workspace.workspace import Workspace

from paperagent.env import load_env_file

load_env_file()  # allow `uvicorn paperagent.server:app` without going through the CLI

WEB_DIR = Path(__file__).resolve().parents[1] / "web"



def _root() -> Path:
    import os
    from paperagent.identity import selected_root
    return selected_root(Path(os.environ.get("PAPERAGENT_WORKSPACE", "./workspace")))


def _store() -> DomainStore:
    from paperagent.workspace.repositories import FileMemoryRepository
    return FileMemoryRepository(_root())


def _radar() -> LiteratureRadar:
    return LiteratureRadar(_store())


def _build_digest() -> dict:
    store = _store()
    from paperagent.tasks.scheduler import _parse
    now = datetime.now(timezone.utc)
    upcoming = [d for d in store.list_deadlines()
                if _parse(d.when) >= now]
    upcoming.sort(key=lambda d: d.when)
    open_tasks = [t for t in store.list_tasks()
                  if t.status in ("todo", "in_progress")]
    unread = [n for n in store.list_notifications() if not n.read]
    hits = store.list_radar_hits(limit=5)
    opportunities = store.list_opportunities(limit=5)
    lines = [f"待办任务 {len(open_tasks)} 项",
             f"未读通知 {len(unread)} 条",
             f"Radar 最近命中 {len(opportunities) or len(hits)} 篇"]
    if upcoming:
        d = upcoming[0]
        lines.append(f"最近截止：{d.title}（{d.when}）")
    return {"title": "今日科研简报", "body": "\n".join(lines)}


SCHEDULER: Optional[Scheduler] = None


def start_scheduler():
    global SCHEDULER
    import os
    if os.environ.get("PAPERAGENT_SCHEDULER", "on") == "off":
        SCHEDULER = None
        return
    SCHEDULER = Scheduler(
        _store(),
        radar_run=lambda: _radar().run(),
        digest_build=_build_digest,
    )
    SCHEDULER.start(interval_s=float(os.environ.get(
        "PAPERAGENT_SCHEDULER_INTERVAL", "60")))


def stop_scheduler():
    global SCHEDULER
    if SCHEDULER is not None:
        SCHEDULER.stop()
        SCHEDULER = None


def _router() -> EventRouter:
    root = _root()
    return EventRouter(
        radar_run=lambda payload: _radar().run(
            provider=(payload or {}).get("provider")),
        scheduler_tick=lambda: SCHEDULER.tick() if SCHEDULER
        else Scheduler(_store(), radar_run=lambda: _radar().run(),
                       digest_build=_build_digest).tick(),
        digest_build=_build_digest,
        project_resolver=ProjectResolver(root),
        session_router=SessionRouter(),
        event_log=EventLog(root),
    )


def _llm_configured() -> bool:
    from paperagent.harness.llm import LLMClient, LLMConfigError
    try:
        LLMClient()
        return True
    except LLMConfigError:
        return False


def _slug(name: str) -> str:
    s = re.sub(r"[^\w-]+", "-", name.strip(), flags=re.UNICODE).strip("-")
    return s or "project"


class RunState:
    def __init__(self, project_id: str, goal: str, mode: str):
        self.run_id = uuid.uuid4().hex[:12]
        self.project_id = project_id
        self.goal = goal
        self.mode = mode
        self.status = "running"
        self.events: list[dict] = []
        self.done = threading.Event()
        self.cancel_event = threading.Event()
        self.started_at = datetime.now(timezone.utc)
        self.task_id: str | None = None
        self.workflow_id: str | None = None
        self.resume = False
        self.workflow_mode = None
        self.workflow_options = None

    def emit(self, event: dict) -> None:
        self.events.append(event)


RUNS: dict[str, RunState] = {}


class CreateProject(BaseModel):
    name: str
    goal: str = ""
    lifecycle_type: Optional[str] = None
    stage: Optional[str] = None


class SendMessage(BaseModel):
    text: str
    mode: Literal["auto", "cloud", "offline", "llm"] = "auto"
    task_id: Optional[str] = None
    workflow_id: Optional[str] = None
    workflow_options: Optional[dict] = None
    resume: bool = False
    workflow_mode: Literal["auto", "off"] | None = None


class ProjectFileImportBody(BaseModel):
    path: str


class ProjectFolderImportBody(BaseModel):
    folder: str
    recursive: bool = True


class SaveDraftBody(BaseModel):
    raw: str
    expected_raw: str


# ------------------------------------------------------------------ projects

def config():
    return {"llm_configured": _llm_configured(), "default_execution": "cloud"}


def tools():
    return {"tools": describe_tools(_root())}


def list_projects():
    root = _root()
    projects = []
    if (root / "projects").exists():
        for d in sorted((root / "projects").iterdir()):
            if not d.is_dir():
                continue
            ws = Workspace(d.name, root=root)
            if not ws.project_path.exists():
                continue
            meta = json.loads(ws.project_path.read_text(encoding="utf-8"))
            projects.append({
                "project_id": d.name,
                "name": meta.get("name") or meta.get("goal") or d.name,
                "goal": meta.get("goal", ""),
                "created_at": meta.get("created_at", ""),
                "references": len(ws.list_references()),
                "drafts": len(list(ws.drafts_dir.glob("*.md")))
                if ws.drafts_dir.exists() else 0,
            })
    projects.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    return {"projects": projects}


def create_project(body: CreateProject):
    from paperagent.domain.lifecycle import LifecycleState, LifecycleService
    lifecycle = None
    if body.lifecycle_type:
        try:
            lifecycle = LifecycleState(lifecycle_type=body.lifecycle_type, stage=body.stage or "topic")
        except ValueError as e:
            raise HTTPException(422, str(e))
    root = _root()
    pid = _slug(body.name)
    if (root / "projects" / pid).exists():
        i = 2
        while (root / "projects" / f"{pid}-{i}").exists():
            i += 1
        pid = f"{pid}-{i}"
    ws = Workspace(pid, root=root).init(body.goal or body.name)
    project_meta = json.loads(ws.project_path.read_text(encoding="utf-8"))
    project_meta["name"] = body.name
    ws.project_path.write_text(json.dumps(project_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if lifecycle:
        LifecycleService(ws).save(lifecycle_type=lifecycle.lifecycle_type,
                                  stage=lifecycle.stage, reason="创建时选择")
    return {"project_id": pid}


def _ws(project_id: str) -> Workspace:
    if not project_id or Path(project_id).name != project_id or project_id in {".", ".."}:
        raise HTTPException(404, "project not found")
    ws = Workspace(project_id, root=_root())
    if not ws.dir.exists():
        raise HTTPException(404, f"project not found: {project_id}")
    return ws




def project_detail(project_id: str):
    from paperagent.domain.lifecycle import LifecycleService
    from paperagent.tasks.session import SessionManager
    ws = _ws(project_id)
    refs = [{
        "ref_id": r.ref_id, "title": r.title, "authors": r.authors,
        "year": r.year, "venue": r.venue, "doi": r.doi, "url": r.url,
        "source": r.source, "citation_count": r.citation_count,
    } for r in ws.list_references()]
    evidence = [{
        "evidence_id": e.evidence_id, "ref_id": e.ref_id, "quote": e.quote,
        "page": e.page, "section": e.section, "supports": e.supports,
    } for e in ws.list_evidence()]
    drafts = []
    if ws.drafts_dir.exists():
        drafts = [p.name for p in sorted(ws.drafts_dir.glob("*.md"))
                  if not p.name.endswith(".rendered.md")]
    runs = []
    if ws.runs_dir.exists():
        for f in sorted(ws.runs_dir.glob("*.json"), reverse=True):
            try:
                trace = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            runs.append({
                "trace_id": f.stem,
                "goal": trace.get("goal", ""),
                "status": trace.get("status", ""),
                "iterations": trace.get("iterations", 0),
                "steps": trace.get("steps", []),
                "quality_report": trace.get("quality_report"),
            })
    lifecycle = LifecycleService(ws).get()
    from paperagent.conversation import ConversationManager
    conversation = ConversationManager(ws).context()
    live = next((r for r in RUNS.values() if r.project_id == project_id and not r.done.is_set()), None)
    if conversation["status"] == "running" and not live:
        conversation["status"] = "interrupted"
    conversation["running"] = bool(live)
    conversation["can_resume"] = bool(conversation["request"] and not live and
                                     conversation["status"] not in {"idle", "finished"})
    from paperagent.conversation import select_conversation_workflow
    from paperagent.skills.context import workspace_skill_metadata
    skill_goal = conversation["request"] or ws.goal
    workflow_notice = ""
    try:
        skill_workflow = select_conversation_workflow(ws, skill_goal, conversation)
    except ValueError as exc:
        # A saved run may refer to an old lifecycle/case. Keep its history and
        # let the user reopen the room to select a valid current configuration.
        skill_workflow, workflow_notice = None, str(exc)
    skills = workspace_skill_metadata(ws, skill_goal, workflow=skill_workflow, store=_store())
    from paperagent.tasks.cloud_conversation import CloudConversation
    return {"project_id": project_id, "goal": ws.goal,
            "cloud_authorizations": CloudConversation(ws).cards(),
            "lifecycle": lifecycle.model_dump() if lifecycle else None,
            "sessions": [s.model_dump() for s in SessionManager(ws).list()],
            "conversation": conversation,
            "skills": skills,
            "workflow_notice": workflow_notice,
            "references": refs, "evidence": evidence,
            "drafts": drafts, "runs": runs,
            "materials": ws.list_materials(),
            "project_memory": ws.read_project_memory(),
            "messages": ws.list_messages(limit=100),
            "runtime": ws.read_run_state(),
            "permissions": ws.load_permissions(),
            "tool_calls": ws.list_tool_calls(limit=50),
            "workflow": ws.read_run_state().get("workflow")}


# --------------------------------------------------------------------- runs

def _run_agent(run: RunState) -> None:
    run.emit({"type": "run_started", "run_id": run.run_id,
              "goal": run.goal, "mode": run.mode})
    cloud_agent = None
    try:
        ws = Workspace(run.project_id, root=_root())
        if run.mode == "cloud":
            from paperagent.tasks.cloud_conversation import CloudConversation, CloudDecisionAgent
            cloud_agent = CloudDecisionAgent(CloudConversation(ws), run.authorization_id, run.cancel_event)
            agent = cloud_agent
            ws = cloud_agent.workspace()
        elif run.mode == "llm":
            agent = LLMAgent(LLMClient())
        else:
            agent = ScriptedAgent(goal=run.goal, provider="offline_stub")
        executor = ToolExecutor(ResearchPolicy(), ws, parent_run_id=run.run_id)
        def step_callback(step):
            if cloud_agent: cloud_agent.record_step(step)
            run.emit({"type": "step", **step})
        from paperagent.tasks.cloud_conversation import ConversationGate
        loop = DecisionLoop(
            ws, executor, agent,
            permission_gate=ConversationGate(cloud_agent) if cloud_agent else None,
            # web has no blocking answer channel: None makes the loop end the
            # turn with the question itself, like a chat assistant would
            ask_user_fn=lambda q: None,
            log=lambda m: None,
            on_step=step_callback,
            cancel_event=run.cancel_event,
            run_id=run.run_id,
            store=_store(),
            task_id=run.task_id,
            workflow_id=run.workflow_id,
            resume=run.resume, workflow_mode=run.workflow_mode,
            workflow_options=run.workflow_options,
        )
        outcome = loop.run(run.goal)
        if cloud_agent:
            try: cloud_agent.finish(outcome)
            except (ValueError, RuntimeError, OSError, PermissionError):
                run.emit({"type":"cloud_sync_pending","message":"本机结果已保存，云端状态待同步"})
        report = outcome.quality_report.model_dump() \
            if outcome.quality_report else None
        if outcome.summary:
            ws.append_message("assistant", outcome.summary,
                              run_id=run.run_id, task_id=loop.task_id,
                              conversation_id=None if loop.task_id else "main", status=outcome.status)
        run.emit({"type": "run_finished", "status": outcome.status,
                  "task_id": loop.task_id,
                  "conversation_id": None if loop.task_id else "main",
                  "summary": outcome.summary, "draft": outcome.draft,
                  "iterations": outcome.iterations, "quality_report": report})
        run.status = outcome.status
    except Exception as e:
        if cloud_agent:
            try:
                from types import SimpleNamespace
                cloud_agent.finish(SimpleNamespace(status="error", summary=str(e)[:40000]))
            except Exception:
                pass  # locally journaled completion can be retried without repeating tools
        try:
            failed_ws = Workspace(run.project_id, root=_root())
            failed_ws.update_run_state(status="error", phase="failed", last_error=str(e))
            if run.task_id:
                from paperagent.tasks.session import SessionManager
                SessionManager(failed_ws).checkpoint(run.task_id, status="error", summary=str(e))
            else:
                from paperagent.conversation import ConversationManager
                manager = ConversationManager(failed_ws)
                if manager.get().run_id == run.run_id:
                    manager.checkpoint(status="error", summary=str(e))
            failed_ws.append_message(
                "assistant", f"{type(e).__name__}: {e}",
                run_id=run.run_id, status="error")
        except Exception:
            pass
        run.emit({"type": "run_finished", "status": "error",
                  "summary": f"{type(e).__name__}: {e}"})
        run.status = "error"
    finally:
        run.done.set()


def send_message(project_id: str, body: SendMessage):
    return _send_message(project_id, body)


def _send_message(project_id: str, body: SendMessage, authorization=None):
    ws = _ws(project_id)  # 404 if missing
    goal = body.text.strip()
    if not goal:
        raise HTTPException(422, "消息不能为空")
    event = AssistantEvent(
        event_id=uuid.uuid4().hex[:8],
        type="user_message",
        source="api",
        project_id=project_id,
        payload={"text": body.text, "mode": body.mode},
    )
    routed = _router().route(event)
    if routed.get("resolution", {}).get("error"):
        raise HTTPException(404, routed["resolution"]["error"])
    for r in RUNS.values():
        if r.project_id == project_id and not r.done.is_set():
            raise HTTPException(409, "该研究空间正在处理上一条消息，请先停止或等待完成")

    mode = body.mode
    if mode == "auto":
        mode = "cloud"

    from paperagent.tasks.session import SessionManager
    from paperagent.workflows.registry import select_workflow
    manager = SessionManager(ws)
    if body.task_id:
        try:
            session = manager.get(body.task_id)
        except (KeyError, ValueError):
            raise HTTPException(404, "Task not found")
    else:
        session = None
    preferred = body.workflow_id or (session.workflow_id if session else None)
    try:
        from paperagent.workflows.project_contract import ProjectWorkflowOptions
        if body.workflow_options is not None:
            ProjectWorkflowOptions.model_validate(body.workflow_options)
        if body.workflow_mode == "off" and preferred:
            raise ValueError("不能同时关闭 Workflow 并指定 Workflow")
        options = body.workflow_options
        if body.resume and options is None and not session:
            from paperagent.conversation import ConversationManager
            options = ConversationManager(ws).get().workflow_options
        if body.workflow_mode == "off" and (options or {}).get("case_id"):
            raise ValueError("关闭流程时不能指定交付记录")
        if preferred or (options or {}).get("case_id"):
            selected = select_workflow(ws, goal, preferred, options)
            if selected and selected.get("case_status", "active") != "active":
                raise ValueError("请先恢复所选交付记录，再继续对话")
            if selected and (options or {}).get("case_id") and not preferred:
                preferred = selected["canonical_workflow_id"]
    except ValueError as e:
        raise HTTPException(422, str(e))
    if mode == "cloud" and authorization is None:
        from paperagent.tasks.cloud_conversation import CloudConversation
        return CloudConversation(ws).prepare(body.model_dump())
    if session and body.workflow_options is not None:
        session = manager.update(session.task_id, workflow_options=body.workflow_options)
    if authorization is None:
        ws.append_message("user", goal, mode=mode, task_id=session.task_id if session else None,
                          conversation_id=None if session else "main")
    run = RunState(project_id, goal, mode)
    if authorization:
        run.run_id = authorization["local_run_id"]
        run.authorization_id = authorization["id"]
    run.task_id, run.workflow_id = session.task_id if session else None, preferred
    run.resume, run.workflow_mode, run.workflow_options = body.resume, body.workflow_mode, body.workflow_options
    RUNS[run.run_id] = run
    threading.Thread(target=_run_agent, args=(run,), daemon=True).start()
    return {"run_id": run.run_id, "mode": mode, "execution_owner": "cloud" if mode == "cloud" else "local", "task_id": run.task_id,
            "conversation_id": None if session else "main"}


def cancel_run(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, f"run not found: {run_id}")
    if run.done.is_set():
        return {"status": run.status, "note": "run already finished"}
    run.cancel_event.set()
    return {"status": "cancelling"}


async def run_events(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, f"run not found: {run_id}")

    async def stream():
        import asyncio
        idx = 0
        while True:
            while idx < len(run.events):
                event = run.events[idx]
                idx += 1
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if run.done.is_set() and idx >= len(run.events):
                yield "data: [DONE]\n\n"
                return
            await asyncio.sleep(0.12)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ------------------------------------------------------------------- drafts

_DRAFT_NAME_RE = re.compile(r"^[\w\-\u4e00-\u9fff]+\.md$")


def get_draft(project_id: str, name: str):
    ws = _ws(project_id)
    if not _DRAFT_NAME_RE.match(name):
        raise HTTPException(400, "invalid draft name")
    try:
        raw = ws.read_draft(name)
    except FileNotFoundError:
        raise HTTPException(404, f"draft not found: {name}")
    return {"name": name, "raw": raw,
            "rendered": render_project_draft(ws, name)}


def extract_template(project_id: str, path: str):
    ws = _ws(project_id)
    template_path = ws.resolve_path(path)
    if not template_path.exists():
        raise HTTPException(404, f"template not found: {path}")
    if template_path.suffix.lower() != ".docx":
        raise HTTPException(422, "template extraction currently supports .docx")
    return extract_docx_template_fields(template_path)


def export_draft(project_id: str, name: str, format: str = "docx",
                 template_path: Optional[str] = None):
    ws = _ws(project_id)
    if not _DRAFT_NAME_RE.match(name):
        raise HTTPException(400, "invalid draft name")
    if not (ws.drafts_dir / name).exists():
        raise HTTPException(404, f"draft not found: {name}")
    fmt = format.lower()
    try:
        if fmt == "docx":
            path = export_draft_docx(ws, name)
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif fmt in {"template_docx", "docx_template"}:
            if not template_path:
                raise HTTPException(422, "template_path is required for template_docx export")
            path = export_draft_docx_with_template(
                ws, name, ws.resolve_path(template_path))
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif fmt == "wps":
            path = export_draft_wps(ws, name)
            media_type = "application/octet-stream"
        elif fmt == "pdf":
            raise HTTPException(
                422,
                "PDF export is not enabled yet. Export DOCX first, then convert with Word/WPS/LibreOffice in the next milestone.",
            )
        else:
            raise HTTPException(422, "format must be docx, template_docx, wps, or pdf")
    except RuntimeError as e:
        raise HTTPException(422, str(e))
    return FileResponse(path, media_type=media_type, filename=path.name)


def evidence_evaluation(project_id: str):
    ws = _ws(project_id)
    return EvidenceEvaluator().evaluate(
        references=ws.list_references(),
        evidence=ws.list_evidence(),
    ).model_dump()


def _first_text_from_import(result: dict) -> tuple[str, Optional[int]]:
    if result.get("pages"):
        page = result["pages"][0]
        return str(page.get("text") or "").strip(), page.get("page")
    return str(result.get("text") or "").strip(), None


def _import_file_into_project(project_id: str, path: str) -> dict:
    from paperagent.schemas.evidence import Evidence
    from paperagent.schemas.references import ReferenceCandidate, make_ref_id
    ws = _ws(project_id).init(_ws(project_id).goal or project_id)
    original_path = Path(path).resolve()
    ws.authorize_root(original_path)
    ws.upsert_material(str(original_path), "processing")
    result = run_file_import(
        {"path": path, "conversion_dir": str(ws.dir / "converted")},
        resolve=lambda p: Path(p).resolve(),
    )
    if result.get("error"):
        suffix = original_path.suffix.lower()
        status = "pending_conversion" if suffix in {".doc", ".wps"} else "error"
        ws.upsert_material(
            str(original_path),
            status,
            error=result["error"],
            supported=suffix in {".doc", ".wps"},
        )
        raise HTTPException(422, result["error"])

    store = _store()
    known = store.imported_file_dedup_keys()
    if result.get("sha256") and result["sha256"] not in known:
        store.add_imported_file(
            path=result["path"],
            content_type=result.get("content_type", "application/octet-stream"),
            size=result.get("size", 0),
            mtime=result.get("mtime"),
            sha256=result.get("sha256", ""),
            source="manual",
            project_id=project_id,
            metadata={
                "filename": result.get("filename", ""),
                "suffix": Path(result.get("path", "")).suffix.lower(),
                "kind": result.get("kind", ""),
            },
        )

    imported_refs = []
    imported_evidence = []
    imported_drafts = []

    if result.get("references"):
        for item in result["references"]:
            ref = ws.save_reference(ReferenceCandidate.model_validate(item),
                                    source_query=f"import:{result['filename']}")
            imported_refs.append(ref.model_dump())
    else:
        title = Path(result["filename"]).stem.replace("_", " ").replace("-", " ")
        ref_id = make_ref_id("local", None, title.split()[0] if title.split() else "file")
        ref = ws.save_reference(ReferenceCandidate(
            ref_id=ref_id,
            title=title,
            authors=[],
            year=None,
            venue="Local import",
            url=Path(result["path"]).as_uri(),
            source="local_file",
        ), source_query=f"import:{result['filename']}")
        imported_refs.append(ref.model_dump())
        text, page = _first_text_from_import(result)
        if text:
            quote = " ".join(text.split())[:600]
            ev = ws.save_evidence(Evidence(
                evidence_id=f"ev_{result.get('sha256', uuid.uuid4().hex)[:12]}",
                ref_id=ref.ref_id,
                quote=quote,
                page=page,
                section="imported excerpt",
                supports=f"Imported material from {result['filename']}",
                source_path=result["path"],
            ))
            imported_evidence.append(ev.model_dump())
        if result.get("kind") in {"text", "docx"} and text:
            filename = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", Path(result["filename"]).stem).strip("-") or "imported"
            draft_path = ws.write_draft(f"imported-{filename}.md", text)
            imported_drafts.append(draft_path.name)

    material_status = "converted" if result.get("converted_path") else "imported"
    ws.upsert_material(
        result.get("path", str(original_path)),
        material_status,
        kind=result.get("kind"),
        content_type=result.get("content_type"),
        size=result.get("size"),
        sha256=result.get("sha256"),
        converted_path=result.get("converted_path"),
        references=len(imported_refs),
        evidence=len(imported_evidence),
        drafts=list(imported_drafts),
    )

    return {
        "file": result,
        "references": imported_refs,
        "evidence": imported_evidence,
        "drafts": imported_drafts,
    }


def import_project_file(project_id: str, body: ProjectFileImportBody):
    imported = _import_file_into_project(project_id, body.path)
    return {"imported": [imported], "project": project_detail(project_id)}


def import_project_folder(project_id: str, body: ProjectFolderImportBody):
    from paperagent.connectors.local_folder import (
        SUPPORTED_SUFFIXES,
        split_supported_files,
        summarize_unsupported_files,
    )
    try:
        _ws(project_id).authorize_root(body.folder)
        root, supported, unsupported_paths = split_supported_files(
            body.folder,
            recursive=body.recursive,
        )
    except NotADirectoryError as e:
        raise HTTPException(422, f"not a folder: {e}")
    imported = []
    errors = []
    for path in supported:
        try:
            imported.append(_import_file_into_project(project_id, str(path)))
        except HTTPException as e:
            errors.append({"path": str(path), "error": e.detail})
    unsupported = summarize_unsupported_files(unsupported_paths)
    ws = _ws(project_id)
    for item in unsupported:
        suffix = item.get("suffix")
        ws.upsert_material(
            item["path"],
            "unsupported",
            error=f"unsupported file type: {suffix}",
            supported=False,
        )
    return {"folder": str(root), "imported": imported, "errors": errors,
            "unsupported": unsupported,
            "unsupported_count": len(unsupported_paths),
            "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
            "project": project_detail(project_id)}


# ------------------------------------------------------------------- V2: domain

class TaskBody(BaseModel):
    title: str
    priority: int = 2
    due_date: Optional[str] = None
    project_id: Optional[str] = None


class TaskStatusBody(BaseModel):
    status: str


class DeadlineBody(BaseModel):
    title: str
    when: str  # ISO datetime
    scope: Optional[str] = None
    reminder_hours: list[int] = [72, 24]


class IntentBody(BaseModel):
    description: str
    topic: Optional[str] = None
    type: str = "literature"
    structured_query: dict = {}
    scope: dict = {}
    notify_policy: dict = {}
    enabled: bool = True


class RadarRunBody(BaseModel):
    provider: Optional[str] = None


class ProfileBody(BaseModel):
    name: str = ""
    roles: list[str] = []
    affiliations: list[str] = []
    directions: list[str] = []
    topics: list[str] = []
    institution: Optional[str] = None
    methods: list[str] = []
    publications: list[dict] = []
    projects: list[dict] = []
    target_journals: list[str] = []
    target_conferences: list[str] = []
    target_funders: list[str] = []
    advisor_rules: list[str] = []
    language: dict = {}
    disciplines: list[dict] = []
    notes: str = ""


class ProjectProfileBody(BaseModel):
    project_type: str = "general"
    title: str = ""
    language: Optional[dict] = None
    discipline_ids: list[str] = []
    target_venue: Optional[str] = None
    target_funder: Optional[str] = None
    target_school: Optional[str] = None
    keywords: list[str] = []
    methods: list[str] = []
    constraints: list[str] = []
    deliverables: list[str] = []
    status: str = "active"


class AutomationBody(BaseModel):
    name: str
    cadence: str = "daily"
    action: str = "radar_literature"
    enabled: bool = True
    next_run: Optional[str] = None


class SubmissionBody(BaseModel):
    title: str
    journal_name: str = ""
    manuscript_id: str = ""
    project_id: Optional[str] = None
    paper_id: Optional[str] = None
    journal_id: Optional[str] = None
    status: str = "submitted"


class SubmissionStatusBody(BaseModel):
    status: str
    source: str = "manual"
    note: str = ""


class SubmissionEmailBody(BaseModel):
    subject: str = ""
    text: str
    apply: bool = False


class EmailConnectorBody(BaseModel):
    subject: str = ""
    text: str
    message_id: str = ""
    from_addr: str = ""
    watcher_id: Optional[str] = None
    project_id: Optional[str] = None


class LocalFolderScanBody(BaseModel):
    folder: str
    watcher_id: Optional[str] = None
    project_id: Optional[str] = None
    recursive: bool = True


class ZoteroSyncBody(BaseModel):
    items: list[dict]
    watcher_id: Optional[str] = None
    project_id: Optional[str] = None


class MemoryBody(BaseModel):
    trust: Literal["user_adopted", "distilled_candidate", "model_transient"] = "model_transient"
    layer: str
    content: str
    scope: dict = {}
    tags: list[str] = []
    importance: int = 2
    ref: Optional[str] = None
    kind: str = "note"
    source: dict = {}
    confirmation: str = "provided"
    pinned: bool = False
    expires_at: Optional[str] = None


class WatcherBody(BaseModel):
    name: str
    watcher_type: str
    source: str
    query: dict = {}
    enabled: bool = True


class WatcherChangeBody(BaseModel):
    payload: dict


class JournalRecommendationBody(BaseModel):
    title: str = ""
    abstract: str = ""
    keywords: list[str] = []
    project_id: Optional[str] = None
    candidates: list[dict] = []


class GrantDiscoveryBody(BaseModel):
    opportunities: list[dict] = []


class OpportunityExtractBody(BaseModel):
    title: str = ""
    text: str
    url: Optional[str] = None
    source: str = "manual"
    opportunity_type: Optional[str] = None
    topic_query: str = ""
    save: bool = True
    notify: bool = False


def today():
    """Today-page feed: what the assistant found, what needs pushing."""
    from paperagent.tasks.scheduler import _parse
    store = _store()
    now = datetime.now(timezone.utc)
    deadlines = sorted(
        (d for d in store.list_deadlines() if _parse(d.when) >= now),
        key=lambda d: d.when)
    digest = _build_digest()
    return {
        "digest": digest,
        "deadlines": [d.model_dump() for d in deadlines[:5]],
        "open_tasks": [t.model_dump() for t in TaskManager(store).open_tasks()[:10]],
        "unread_notifications": sum(1 for n in store.list_notifications()
                                    if not n.read),
        "recent_opportunities": [o.model_dump()
                                 for o in store.list_opportunities(limit=5)],
        "recent_radar_hits": [h.model_dump() for h in store.list_radar_hits(limit=5)],
    }


def list_tasks():
    return {"tasks": [t.model_dump() for t in _store().list_tasks()]}


def create_task(body: TaskBody):
    if not body.title.strip():
        raise HTTPException(422, "任务标题不能为空")
    task = TaskManager(_store()).create(
        body.title.strip(), origin="user", priority=body.priority,
        due_date=body.due_date, project_id=body.project_id)
    return task.model_dump()


def set_task_status(task_id: str, body: TaskStatusBody):
    try:
        task = TaskManager(_store()).transition(task_id, body.status)
    except KeyError:
        raise HTTPException(404, f"task not found: {task_id}")
    except Exception as e:
        raise HTTPException(422, str(e))
    return task.model_dump()


def list_deadlines():
    return {"deadlines": [d.model_dump() for d in _store().list_deadlines()]}


def create_deadline(body: DeadlineBody):
    from paperagent.tasks.scheduler import _parse
    try:
        _parse(body.when)
    except ValueError:
        raise HTTPException(422, f"invalid datetime: {body.when}")
    dl = _store().add_deadline(title=body.title, when=body.when,
                               scope=body.scope,
                               reminder_hours=body.reminder_hours)
    return dl.model_dump()


def list_intents():
    return {"intents": [si.model_dump()
                        for si in _store().list_standing_intents()]}


def create_intent(body: IntentBody):
    si = _store().add_standing_intent(
        description=body.description,
        topic=(body.topic or "").strip(),
        type=body.type,
        structured_query=body.structured_query,
        scope=body.scope,
        notify_policy=body.notify_policy,
        enabled=body.enabled,
    )
    return si.model_dump()


def radar_hits():
    return {"hits": [h.model_dump() for h in _store().list_radar_hits()]}


def opportunities(limit: int = 50):
    return {"opportunities": [o.model_dump()
                              for o in _store().list_opportunities(limit)]}


def extract_opportunity(body: OpportunityExtractBody):
    from paperagent.tasks.opportunity_manager import OpportunityManager
    from paperagent.tools.opportunities import run_opportunity_extract
    extracted = run_opportunity_extract(body.model_dump())["opportunities"]
    result = {"opportunities": extracted}
    if body.save:
        result["saved"] = OpportunityManager(_store()).save_extracted(
            extracted,
            topic_query=body.topic_query,
            notify=body.notify,
        )
    return result


def recommend_journals(body: JournalRecommendationBody):
    return JournalRecommender(_store()).recommend(
        title=body.title,
        abstract=body.abstract,
        keywords=body.keywords,
        project_id=body.project_id,
        candidates=body.candidates or None,
    )


def discover_grants(body: GrantDiscoveryBody):
    return GrantDiscovery(_store()).discover(body.opportunities)


def radar_run(body: RadarRunBody):
    event = AssistantEvent(event_id=uuid.uuid4().hex[:8], type="radar_run",
                           source="api", payload={"provider": body.provider})
    result = _router().route(event)
    if not result.get("handled"):
        raise HTTPException(500, result.get("note", "radar not available"))
    return result["result"]


def scheduler_tick():
    event = AssistantEvent(event_id=uuid.uuid4().hex[:8],
                           type="schedule_fire", source="api")
    result = _router().route(event)
    return {"log": result.get("log", [])}


def notifications(limit: int = 50):
    return {"notifications": [n.model_dump()
                              for n in _store().list_notifications(limit)
                              if not n.dismissed]}


def create_notification(body: dict):
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "notification title is required")
    n = NotificationService(_store()).notify(
        type=body.get("type", "system"),
        title=title,
        body=body.get("body", ""),
        channel=body.get("channel", "in_app"),
        ref=body.get("ref"),
        dedup_key=body.get("dedup_key"),
        priority=body.get("priority", 2),
    )
    return n.model_dump()


def read_notification(notification_id: str):
    if not _store().mark_notification_read(notification_id):
        raise HTTPException(404, f"notification not found: {notification_id}")
    return {"ok": True}


def dismiss_notification(notification_id: str):
    if not _store().dismiss_notification(notification_id):
        raise HTTPException(404, f"notification not found: {notification_id}")
    return {"ok": True}


def get_profile():
    return _store().get_profile().model_dump()


def set_profile(body: ProfileBody):
    from paperagent.domain.entities import ResearchProfile
    return _store().save_profile(
        ResearchProfile.model_validate(body.model_dump())
    ).model_dump()


def get_project_profile(project_id: str):
    return _store().get_project_profile(project_id).model_dump()


def set_project_profile(project_id: str, body: ProjectProfileBody):
    from paperagent.domain.entities import ProjectProfile
    payload = body.model_dump()
    payload["project_id"] = project_id
    try:
        profile = ProjectProfile.model_validate(payload)
    except Exception as e:
        raise HTTPException(422, str(e))
    return _store().save_project_profile(profile).model_dump()


def list_memories(layer: Optional[str] = None,
                  project_id: Optional[str] = None,
                  goal: str = ""):
    store = _store()
    if goal or project_id:
        return {"memories": MemoryManager(store).relevant(
            project_id=project_id, goal=goal)}
    scope = {"project_id": project_id} if project_id else None
    return {"memories": [m.model_dump() for m in store.list_memories(
        layer=layer, scope=scope, limit=100)]}


def create_memory(body: MemoryBody):
    try:
        memory = MemoryManager(_store()).remember(
            body.layer,
            body.content,
            scope=body.scope,
            tags=body.tags,
            importance=body.importance,
            ref=body.ref,
            kind=body.kind, source=body.source, confirmation=body.confirmation,
            pinned=body.pinned, expires_at=body.expires_at, trust=body.trust,
        )
    except Exception as e:
        raise HTTPException(422, str(e))
    return memory.model_dump()


class MemoryReplacement(BaseModel):
    content: str
    source: dict = {}


def replace_memory(memory_id: str, body: MemoryReplacement):
    try:
        return MemoryManager(_store()).replace(memory_id, body.content, source=body.source, actor="user").model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, "Memory not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


def forget_memory(memory_id: str):
    if not MemoryManager(_store()).forget(memory_id):
        raise HTTPException(404, "Memory not found")
    return {"status": "deleted"}


def list_disciplines():
    from paperagent.domain.discipline_presets import list_builtin_disciplines
    return {"disciplines": [d.model_dump() for d in list_builtin_disciplines()]}


def get_discipline(discipline_id: str):
    from paperagent.domain.discipline_presets import get_builtin_discipline
    discipline = get_builtin_discipline(discipline_id)
    if discipline is None:
        raise HTTPException(404, f"discipline not found: {discipline_id}")
    return discipline.model_dump()


def list_skills(project_id: Optional[str] = None,
                goal: str = "",
                task_type: Optional[str] = None,
                project_type: Optional[str] = None,
                discipline_id: Optional[str] = None,
                include_content: bool = False):
    loader = SkillLoader(_root())
    if project_id:
        _ws(project_id)
    discipline_ids = [discipline_id] if discipline_id else None
    if goal or task_type or project_type or discipline_id:
        skills = loader.select(goal=goal, project_id=project_id,
                               task_type=task_type,
                               project_type=project_type,
                               discipline_ids=discipline_ids,
                               limit=8)
    else:
        skills = loader.load_all(project_id=project_id)
    return {"skills": [s.context_view() if include_content else s.metadata_view() for s in skills]}


def get_skill(skill_key: str, project_id: Optional[str] = None):
    if project_id:
        _ws(project_id)
    try:
        skill = SkillLoader(_root()).resolve(skill_key, project_id)
        return {"skill": skill.context_view()}
    except ValueError as exc:
        raise HTTPException(422 if "Ambiguous" in str(exc) else 404, str(exc))


def get_skill_resource(skill_key: str, resource_id: str, project_id: Optional[str] = None,
                       offset: int = 0, max_chars: int = 6000, expected_sha256: str = ""):
    if project_id:
        _ws(project_id)
    try:
        return SkillLoader(_root()).read_resource(skill_key, resource_id, project_id,
            offset=offset, max_chars=max_chars, expected_sha256=expected_sha256)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc))


def list_tools():
    return {"tools": describe_tools(_root())}


def list_automations():
    return {"automations": [a.model_dump()
                            for a in _store().list_automations()]}


def create_automation(body: AutomationBody):
    from paperagent.domain.entities import AUTOMATION_INTERVALS, Automation
    from paperagent.tasks.scheduler import _parse
    payload = body.model_dump()
    if not body.name.strip():
        raise HTTPException(422, "automation name cannot be empty")
    if body.cadence not in AUTOMATION_INTERVALS:
        raise HTTPException(422, f"invalid cadence: {body.cadence}")
    if body.action not in ("radar_literature", "daily_digest"):
        raise HTTPException(422, f"invalid action: {body.action}")
    if body.next_run is not None:
        try:
            _parse(body.next_run)
        except ValueError:
            raise HTTPException(422, f"invalid datetime: {body.next_run}")
    payload["name"] = body.name.strip()
    # Validate against the domain model before writing, keeping API and
    # store semantics aligned as the automation schema grows.
    Automation(automation_id="am0000", **payload)
    return _store().add_automation(**payload).model_dump()


def list_watchers():
    return {"watchers": [w.model_dump() for w in _store().list_watchers()]}


def create_watcher(body: WatcherBody):
    try:
        watcher = WatcherManager(_store()).create(
            name=body.name,
            watcher_type=body.watcher_type,
            source=body.source,
            query=body.query,
        )
    except Exception as e:
        raise HTTPException(422, str(e))
    if body.enabled != watcher.enabled:
        watcher = _store().update_watcher(watcher.watcher_id,
                                          enabled=body.enabled) or watcher
    return watcher.model_dump()


def ingest_watcher_change(watcher_id: str, body: WatcherChangeBody):
    try:
        event = WatcherManager(_store()).ingest_change(watcher_id, body.payload)
    except KeyError:
        raise HTTPException(404, f"watcher not found: {watcher_id}")
    except Exception as e:
        raise HTTPException(422, str(e))
    return event.model_dump()


def list_submissions():
    return {"submissions": [s.model_dump()
                            for s in _store().list_submissions()]}


def create_submission(body: SubmissionBody):
    if not body.title.strip() and not body.manuscript_id.strip():
        raise HTTPException(422, "submission title or manuscript_id is required")
    try:
        sub = SubmissionManager(_store()).create(
            title=body.title,
            journal_name=body.journal_name,
            manuscript_id=body.manuscript_id,
            project_id=body.project_id,
            paper_id=body.paper_id,
            journal_id=body.journal_id,
            status=body.status,  # pydantic/domain validation handles invalid values
        )
    except Exception as e:
        raise HTTPException(422, str(e))
    return sub.model_dump()


def set_submission_status(submission_id: str, body: SubmissionStatusBody):
    try:
        sub = SubmissionManager(_store()).transition(
            submission_id, body.status, source=body.source, note=body.note)
    except KeyError:
        raise HTTPException(404, f"submission not found: {submission_id}")
    except Exception as e:
        raise HTTPException(422, str(e))
    return sub.model_dump()


def parse_submission_email(body: SubmissionEmailBody):
    parsed = SubmissionEmailParser().parse(body.text, subject=body.subject)
    result = {"parsed": parsed.model_dump()}
    if body.apply:
        try:
            sub = SubmissionManager(_store()).apply_parsed_email(parsed)
        except Exception as e:
            raise HTTPException(422, str(e))
        result["submission"] = sub.model_dump()
    return result


def ingest_submission_email(body: EmailConnectorBody):
    connector = LocalEmailConnector(_store())
    payload = body.model_dump()
    message = connector.normalize(payload)
    event = connector.to_event(message, project_id=body.project_id)
    routed = _router().route(event)
    if routed.get("resolution", {}).get("error"):
        raise HTTPException(404, routed["resolution"]["error"])
    try:
        result = connector.ingest_submission_email(
            payload, watcher_id=body.watcher_id, project_id=body.project_id)
    except KeyError:
        raise HTTPException(404, f"watcher not found: {body.watcher_id}")
    except Exception as e:
        raise HTTPException(422, str(e))
    return {
        "event": result["event"].model_dump(),
        "message": result["message"].model_dump(),
        "trigger": (result["trigger"].model_dump()
                    if result["trigger"] is not None else None),
        "submission": result["submission"].model_dump(),
        "route": routed,
    }


def scan_local_folder(body: LocalFolderScanBody):
    from paperagent.connectors.local_folder import LocalFolderConnector
    try:
        result = LocalFolderConnector(_store()).scan(
            body.folder,
            watcher_id=body.watcher_id,
            project_id=body.project_id,
            recursive=body.recursive,
        )
    except KeyError:
        raise HTTPException(404, f"watcher not found: {body.watcher_id}")
    except Exception as e:
        raise HTTPException(422, str(e))
    return {
        "folder": result["folder"],
        "imported": [f.model_dump() for f in result["imported"]],
        "events": [
            {
                "event": item["event"].model_dump(),
                "trigger": (item["trigger"].model_dump()
                            if item["trigger"] is not None else None),
            }
            for item in result["events"]
        ],
    }


def list_zotero_items(project_id: Optional[str] = None, limit: int = 200):
    return {"items": [i.model_dump()
                      for i in _store().list_zotero_items(project_id, limit)]}


def sync_zotero_items(body: ZoteroSyncBody):
    from paperagent.connectors.zotero import ZoteroConnector
    try:
        result = ZoteroConnector(_store()).sync_items(
            body.items,
            watcher_id=body.watcher_id,
            project_id=body.project_id,
        )
    except KeyError:
        raise HTTPException(404, f"watcher not found: {body.watcher_id}")
    except Exception as e:
        raise HTTPException(422, str(e))
    return {
        "synced": [i.model_dump() for i in result["synced"]],
        "references": [r.model_dump() for r in result["references"]],
        "imported_files": [f.model_dump() for f in result["imported_files"]],
        "events": [
            {
                "event": item["event"].model_dump(),
                "trigger": (item["trigger"].model_dump()
                            if item["trigger"] is not None else None),
            }
            for item in result["events"]
        ],
    }


def index():
    return FileResponse(WEB_DIR / "index.html")


def lifecycle_script():
    return FileResponse(WEB_DIR / "lifecycle.js", media_type="text/javascript")


def manuscript_script():
    return FileResponse(WEB_DIR / "manuscript.js", media_type="text/javascript")


def paper_review_script():
    return FileResponse(WEB_DIR / "paper-review.js", media_type="text/javascript")


def project_memories(project_id: str):
    _ws(project_id)
    return {"memories": [m.model_dump(mode="json") for m in _store().list_memories(
        scope={"project_id": project_id}, limit=None) if m.status == "active"]}


def research_script():
    return FileResponse(WEB_DIR / "research.js", media_type="text/javascript")


def character_sprite(action: str):
    names = {"idle": "10_30_52-1", "peek": "10_30_53-2",
             "wave": "10_30_53-3", "love": "10_30_54-4",
             "fly": "10_30_55-5", "cheer": "10_30_56-6"}
    if action not in names:
        raise HTTPException(404, "Unknown character action")
    path = Path(__file__).resolve().parents[3] / "characters" / (
        "ChatGPT 图像 2026年10月27日 " + names[action] + ".png")
    if not path.is_file():
        raise HTTPException(404, "Character asset missing")
    return FileResponse(path, media_type="image/png")


def frontend_v6_asset(name: str):
    if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise HTTPException(404, "Asset not found")
    path = WEB_DIR / "v6" / name
    if not path.is_file():
        raise HTTPException(404, "Asset not found")
    return FileResponse(path)


def classic_frontend():
    return FileResponse(WEB_DIR / "classic.html")


def save_project_draft(project_id: str, name: str, body: SaveDraftBody):
    ws = _ws(project_id)
    if not _DRAFT_NAME_RE.fullmatch(name):
        raise HTTPException(422, "Invalid draft filename")
    if any(r.project_id == project_id and not r.done.is_set() for r in RUNS.values()):
        raise HTTPException(409, "请先等待当前研究完成，再编辑草稿")
    try:
        current = ws.read_draft(name)
    except FileNotFoundError:
        raise HTTPException(404, "Draft not found")
    if current != body.expected_raw:
        raise HTTPException(409, "草稿已更新，请重新载入后再编辑")
    if len(body.raw.encode('utf-8')) > 2 * 1024 * 1024:
        raise HTTPException(413, "Draft exceeds 2 MB")
    versions = ws.dir / "draft_versions"
    versions.mkdir(exist_ok=True)
    (versions / f"{uuid.uuid4().hex}-{name}").write_text(current, encoding="utf-8")
    ws.write_draft(name, body.raw)
    return get_draft(project_id, name)


async def upload_project_material(project_id: str, request: Request, filename: str):
    from paperagent.connectors.local_folder import SUPPORTED_SUFFIXES
    from starlette.concurrency import run_in_threadpool
    ws = _ws(project_id)
    if Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise HTTPException(422, "Invalid filename")
    if Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(422, "不支持此文件类型，请使用 PDF、Word、Markdown、TXT、Bib 或 RIS")
    limit = 32 * 1024 * 1024
    incoming = ws.dir / "uploads" / uuid.uuid4().hex
    incoming.mkdir(parents=True, exist_ok=True)
    path = incoming / filename
    size = 0
    try:
        with path.open('wb') as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "文件不能超过 32 MB")
                target.write(chunk)
        if not size:
            raise HTTPException(422, "文件为空")
        imported = await run_in_threadpool(_import_file_into_project, project_id, str(path))
        return {"imported": [imported], "project": await run_in_threadpool(project_detail, project_id)}
    except Exception:
        path.unlink(missing_ok=True)
        raise
