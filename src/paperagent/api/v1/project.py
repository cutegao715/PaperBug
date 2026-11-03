"""Project workflows: explicit user adoption, scoped requirements and audit history."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import re
from pydantic import BaseModel, Field

from paperagent.domain.project_dossier import ProjectDossier, ProjectRecordInput, ProjectBindingInput
from paperagent.kernel.project_delivery import check_project_delivery
from paperagent.workflows.registry import select_workflow
from paperagent.domain.project_claims import ProjectClaims, ClaimReviewInput
from paperagent.domain.project_acceptance import objective_matrix
from paperagent.domain.project_assessment import assess_project, save_assessment


class RecordReview(BaseModel):
    expected_revision: int = Field(ge=1)
    status: str
    reason: str = Field(min_length=1)


class CheckProjectDraft(BaseModel):
    filename: str
    workflow_id: str
    goal: str = "检查当前文档"
    workflow_options: dict = Field(default_factory=dict)
    save_report: bool = False


class ReleaseBinding(BaseModel):
    expected_binding_id: str
    reason: str = Field(min_length=1)


def create_router(resolve_ws, idle):
    app = APIRouter()
    def execute(fn):
        try:
            return fn()
        except (ValueError, FileNotFoundError, KeyError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/projects/{project_id}/dossier")
    def dossier(project_id: str):
        service = ProjectDossier(resolve_ws(project_id))
        return {**service.read(), "active_records": service.active_records(), "current_bindings": service.bindings()}

    @app.post("/projects/{project_id}/dossier/records")
    def record(project_id: str, body: ProjectRecordInput):
        ws = resolve_ws(project_id)
        idle(project_id)
        row = execute(lambda: ProjectDossier(ws).record(body))
        from paperagent.domain.store import DomainStore
        from paperagent.tasks.memory_manager import MemoryManager
        MemoryManager(DomainStore(ws.root)).sync_adopted_record(project_id, "project", row)
        return row

    @app.post("/projects/{project_id}/dossier/records/{record_id}/review")
    def review(project_id: str, record_id: str, body: RecordReview):
        ws = resolve_ws(project_id)
        idle(project_id)
        row = execute(lambda: ProjectDossier(ws).review(record_id, **body.model_dump()))
        from paperagent.domain.store import DomainStore
        from paperagent.tasks.memory_manager import MemoryManager
        MemoryManager(DomainStore(ws.root)).sync_adopted_record(project_id, "project", row)
        return row

    @app.post("/projects/{project_id}/dossier/bindings")
    def bind(project_id: str, body: ProjectBindingInput):
        ws = resolve_ws(project_id)
        idle(project_id)
        return execute(lambda: ProjectDossier(ws).bind(body))

    @app.post("/projects/{project_id}/dossier/check")
    def check(project_id: str, body: CheckProjectDraft):
        ws = resolve_ws(project_id)
        def run():
            workflow = select_workflow(ws, body.goal, body.workflow_id, body.workflow_options)
            text = ws.read_draft(body.filename)
            report = check_project_delivery(ws, text, workflow, body.filename)
            if report is None:
                raise ValueError("Select a project application/midterm/final workflow")
            from paperagent.kernel.citation_integrity import build_quality_report
            citations = build_quality_report(text, ws.list_references(), ws.list_evidence(),
                                             resolve=ws.resolve_path).citation_integrity
            report.errors.extend(citations.errors)
            report.warnings.extend(citations.warnings)
            return {"contract": workflow["project_contract"], "report": report.model_dump(),
                    "citation_report": citations.model_dump(),
                    **assess_project(ws, body.filename, workflow),
                    "exports": save_assessment(ws, body.filename, workflow) if body.save_report else []}
        if body.save_report:
            idle(project_id)
        return execute(run)

    @app.get("/projects/{project_id}/dossier/acceptance")
    def acceptance(project_id: str, stage: str = "midterm", as_of: str | None = None):
        return execute(lambda: objective_matrix(resolve_ws(project_id), stage, as_of))

    @app.post("/projects/{project_id}/dossier/claims/review")
    def review_claim(project_id: str, body: ClaimReviewInput):
        ws = resolve_ws(project_id)
        idle(project_id)
        return execute(lambda: ProjectClaims(ws).review(body))

    @app.get("/projects/{project_id}/dossier/reports/{filename}")
    def download_report(project_id: str, filename: str):
        ws = resolve_ws(project_id)
        if not re.fullmatch(r"(?:application|midterm|final)-[a-f0-9]{20}\.(?:json|md)", filename):
            raise HTTPException(404, "Review report not found")
        path = ws.dir / "reviews" / filename
        if not path.is_file() or not path.resolve().is_relative_to((ws.dir / "reviews").resolve()):
            raise HTTPException(404, "Review report not found")
        return FileResponse(path, filename=filename)

    @app.post("/projects/{project_id}/dossier/bindings/{role}/release")
    def release(project_id: str, role: str, body: ReleaseBinding):
        ws = resolve_ws(project_id)
        idle(project_id)
        return execute(lambda: ProjectDossier(ws).release_binding(role, **body.model_dump()))

    return app


def control_router(service):
    router = APIRouter()
    router.add_api_route('/projects', service.list_projects, methods=['GET'])
    router.add_api_route('/projects', service.create_project, methods=['POST'])
    router.add_api_route('/projects/{project_id}', service.project_detail, methods=['GET'])
    router.add_api_route('/projects/{project_id}/evidence-evaluation', service.evidence_evaluation, methods=['GET'])
    router.add_api_route('/projects/{project_id}/imports/file', service.import_project_file, methods=['POST'])
    router.add_api_route('/projects/{project_id}/imports/folder', service.import_project_folder, methods=['POST'])
    router.add_api_route('/projects/{project_id}/profile', service.get_project_profile, methods=['GET'])
    router.add_api_route('/projects/{project_id}/profile', service.set_project_profile, methods=['POST'])
    router.add_api_route('/projects/{project_id}/memories', service.project_memories, methods=['GET'])
    router.add_api_route('/projects/{project_id}/imports/upload', service.upload_project_material, methods=['POST'])
    return router
