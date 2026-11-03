"""Lifecycle and durable task HTTP surface, sharing the existing workspace resolver."""
from __future__ import annotations
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from paperagent.domain.lifecycle import LifecycleService, LifecycleType
from paperagent.tasks.session import SessionManager
from paperagent.workflows.catalog import describe_lifecycles
from paperagent.workflows.registry import select_workflow


class LifecycleBody(BaseModel):
    lifecycle_type: LifecycleType
    stage: str
    reason: str = Field(min_length=1)
    focus_stages: list[str] = Field(default_factory=list)
    status: str = "active"


class WorkflowPreviewBody(BaseModel):
    goal: str
    workflow_id: str | None = None
    workflow_options: dict | None = None
    workflow_mode: Literal["auto", "off"] | None = None


class TaskContextBody(BaseModel):
    plan: dict | None = None
    open_questions: list[str] | None = None
    decisions: list[str] | None = None
    workflow_options: dict | None = None


class FocusBody(BaseModel):
    filename: str | None = None


class WorkflowCaseBody(BaseModel):
    workflow_id: str
    title: str = ""
    required_deliverables: list[str] | None = None
    inputs: list[str] = Field(default_factory=list)
    scope: Literal["full", "partial"] = "full"


class WorkflowUpdateBody(BaseModel):
    operation: str
    data: dict = Field(default_factory=dict)
    expected_revision: int = Field(ge=1)


class ResearchLinkBody(BaseModel):
    source_project_id: str
    filename: str
    relation: str = "related_research"


class ProactiveBody(BaseModel):
    service: Literal["research_radar", "opportunity_radar", "progress_watcher"]
    query: str = ""
    urls: list[str] = Field(default_factory=list, max_length=5)
    interval_seconds: int = Field(default=3600, ge=60)


def create_router(resolve_ws, runs):
    app = APIRouter()
    def idle(project_id):
        if any(r.project_id == project_id and not r.done.is_set() for r in runs.values()):
            raise HTTPException(409, "请先停止或等待当前运行完成")

    from paperagent.api.v1.project import create_router as create_project_router
    app.include_router(create_project_router(resolve_ws, idle))
    from paperagent.api.v1.manuscript import create_router as create_manuscript_router
    app.include_router(create_manuscript_router(resolve_ws, idle))
    from paperagent.api.v1.research import create_router as create_research_router
    app.include_router(create_research_router(resolve_ws))
    from paperagent.api.v1.connections import create_router as create_connection_router
    app.include_router(create_connection_router(resolve_ws, idle))

    @app.get("/projects/{project_id}/proactive")
    def proactive_services(project_id: str):
        from paperagent.domain.store import DomainStore
        from paperagent.api.control import SCHEDULER
        ws = resolve_ws(project_id)
        return {"scheduler_running": SCHEDULER is not None,
                "watchers": [w.model_dump() for w in DomainStore(ws.root).list_watchers()
                             if w.query.get("project_id") == project_id and w.query.get("service")]}

    @app.post("/projects/{project_id}/proactive")
    def configure_proactive(project_id: str, body: ProactiveBody):
        from urllib.parse import urlparse
        from paperagent.domain.store import DomainStore
        from paperagent.tasks.watcher import WatcherManager
        ws = resolve_ws(project_id)
        if any(urlparse(url).scheme not in {"https", "http"} for url in body.urls):
            raise HTTPException(422, "Sources must be HTTP(S) URLs")
        if body.service == "opportunity_radar" and not body.query.strip() and not body.urls:
            raise HTTPException(422, "Opportunity Radar needs source URLs or a query")
        store = DomainStore(ws.root)
        query = {**body.model_dump(), "project_id": project_id}
        # Updating a service does not create duplicate recurring subscriptions.
        previous = next((w for w in store.list_watchers() if w.query.get("project_id") == project_id
                         and w.query.get("service") == body.service), None)
        if previous:
            return store.update_watcher(previous.watcher_id, query=query, enabled=True, last_checked=None).model_dump()
        return WatcherManager(store).create(name=body.service + " · " + project_id,
            watcher_type={"research_radar": "literature", "opportunity_radar": "grant", "progress_watcher": "external"}[body.service],
            source="user_configured", query=query).model_dump()

    @app.delete("/projects/{project_id}/proactive/{watcher_id}")
    def disable_proactive(project_id: str, watcher_id: str):
        from paperagent.domain.store import DomainStore
        store = DomainStore(resolve_ws(project_id).root)
        previous = next((w for w in store.list_watchers() if w.watcher_id == watcher_id
                         and w.query.get("project_id") == project_id), None)
        if not previous:
            raise HTTPException(404, "Watcher not found in this research space")
        return store.update_watcher(watcher_id, enabled=False).model_dump()

    @app.get("/projects/{project_id}/research-links")
    def research_links(project_id: str):
        from paperagent.domain.research_links import ResearchLinks
        return {"links": ResearchLinks(resolve_ws(project_id)).view()}

    @app.post("/projects/{project_id}/research-links")
    def link_research(project_id: str, body: ResearchLinkBody):
        from paperagent.domain.research_links import ResearchLinks
        idle(project_id)
        # Reuse is explicit. Nothing is pulled from unrelated spaces by discovery.
        resolve_ws(body.source_project_id)
        try:
            return ResearchLinks(resolve_ws(project_id)).link(**body.model_dump())
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/lifecycles")
    def catalog():
        from paperagent.workflows.design import PROACTIVE_SERVICES, CONFIRMATION_POLICY, QUALITY_LEVELS
        return {"lifecycles": describe_lifecycles(), "proactive_services": PROACTIVE_SERVICES,
                "confirmation_policy": CONFIRMATION_POLICY, "quality_levels": QUALITY_LEVELS}

    @app.get("/projects/{project_id}/workflow-cases")
    def workflow_cases(project_id: str):
        from paperagent.workflows.runtime import WorkflowRuntime
        return {"cases": WorkflowRuntime(resolve_ws(project_id)).list()}

    @app.post("/projects/{project_id}/workflow-cases")
    def start_workflow_case(project_id: str, body: WorkflowCaseBody):
        from paperagent.workflows.runtime import WorkflowRuntime
        idle(project_id)
        try:
            return WorkflowRuntime(resolve_ws(project_id)).start(**body.model_dump())
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/projects/{project_id}/workflow-cases/{case_id}")
    def get_workflow_case(project_id: str, case_id: str):
        from paperagent.workflows.runtime import WorkflowRuntime
        try:
            return WorkflowRuntime(resolve_ws(project_id)).get(case_id)
        except KeyError:
            raise HTTPException(404, "Workflow case not found")
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/projects/{project_id}/workflow-cases/{case_id}/file")
    def download_workflow_version(project_id: str, case_id: str, label: str):
        from pathlib import Path
        from fastapi.responses import FileResponse
        from paperagent.workflows.runtime import WorkflowRuntime
        ws = resolve_ws(project_id)
        try:
            case = WorkflowRuntime(ws).get(case_id)
            output = next((o for o in case["outputs"] if o["label"] == label), None)
            if output is None or not output.get("version_path"):
                raise KeyError(label)
            path = Path(output["version_path"]).resolve()
            if not path.is_relative_to((ws.dir / "workflow_versions").resolve()) or not path.is_file():
                raise KeyError(label)
            return FileResponse(path, filename=Path(output["path"]).name)
        except KeyError:
            raise HTTPException(404, "Workflow file version not found")
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))

    @app.patch("/projects/{project_id}/workflow-cases/{case_id}")
    def update_workflow_case(project_id: str, case_id: str, body: WorkflowUpdateBody):
        from paperagent.workflows.runtime import WorkflowRuntime
        idle(project_id)
        try:
            return WorkflowRuntime(resolve_ws(project_id)).update(case_id, **body.model_dump(), actor="user")
        except KeyError:
            raise HTTPException(404, "Workflow case not found")
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/projects/{project_id}/lifecycle")
    def get_lifecycle(project_id: str):
        ws = resolve_ws(project_id)
        state = LifecycleService(ws).get()
        return {"lifecycle": state.model_dump() if state else None}

    @app.post("/projects/{project_id}/lifecycle")
    def set_lifecycle(project_id: str, body: LifecycleBody):
        ws = resolve_ws(project_id)
        idle(project_id)
        try:
            return LifecycleService(ws).save(**body.model_dump()).model_dump()
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/projects/{project_id}/workflows/preview")
    def preview(project_id: str, body: WorkflowPreviewBody):
        try:
            from paperagent.conversation import select_conversation_workflow
            from paperagent.skills.context import workspace_skill_metadata, workflow_skill_metadata
            ws = resolve_ws(project_id)
            if body.workflow_mode == "off" and body.workflow_id:
                raise ValueError("Cannot select a workflow while workflow_mode is off")
            workflow = select_conversation_workflow(ws, body.goal, body.model_dump()) if body.workflow_mode else select_workflow(
                ws, body.goal, body.workflow_id, body.workflow_options)
            return {"workflow": workflow,
                    "skills": workspace_skill_metadata(ws, body.goal, workflow=workflow),
                    "workflow_skills": workflow_skill_metadata(ws, workflow)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/projects/{project_id}/sessions")
    def sessions(project_id: str):
        manager = SessionManager(resolve_ws(project_id))
        return {"sessions": [s.model_dump() for s in manager.list()]}

    @app.post("/projects/{project_id}/conversation/focus")
    def focus(project_id: str, body: FocusBody):
        from paperagent.conversation import ConversationManager
        ws = resolve_ws(project_id)
        idle(project_id)
        try:
            return ConversationManager(ws).focus(body.filename)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/projects/{project_id}/context")
    def context_preview(project_id: str, goal: str = "", task_id: str | None = None):
        from paperagent.domain.store import DomainStore
        from paperagent.harness.context_assembler import assemble_assistant_state
        from paperagent.harness.context_budget import ContextBudgetExceeded
        ws = resolve_ws(project_id)
        if task_id:
            try:
                task = SessionManager(ws).get(task_id)
            except (ValueError, KeyError):
                raise HTTPException(404, "Task not found")
            ws.task_id = task_id
            goal = goal or task.objective
        else:
            from paperagent.conversation import ConversationManager
            goal = goal or ConversationManager(ws).get().request
        try:
            return assemble_assistant_state(ws, goal or ws.goal, 0, 24, 40, None, None, [],
                                             store=DomainStore(ws.root)).model_dump(mode="json")
        except ContextBudgetExceeded as e:
            raise HTTPException(413, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/projects/{project_id}/sessions/{task_id}")
    def session(project_id: str, task_id: str):
        try:
            return SessionManager(resolve_ws(project_id)).context(task_id)
        except (KeyError, ValueError):
            raise HTTPException(404, "Task not found")

    @app.patch("/projects/{project_id}/sessions/{task_id}")
    def update_session(project_id: str, task_id: str, body: TaskContextBody):
        ws = resolve_ws(project_id)
        idle(project_id)
        try:
            manager = SessionManager(ws)
            manager.get(task_id)
        except (KeyError, ValueError):
            raise HTTPException(404, "Task not found")
        try:
            return manager.update(task_id, **body.model_dump()).model_dump()
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    return app


def control_router(service):
    router = APIRouter()
    return router
