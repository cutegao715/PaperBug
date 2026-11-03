"""Workspace-scoped manuscript knowledge, using the existing idle guard."""
from fastapi import APIRouter, HTTPException
from pydantic import Field

from paperagent.domain.manuscript import ManuscriptContext, ManuscriptProfile, ManuscriptRecordInput, StrictModel


class ProfileUpdate(StrictModel):
    profile: ManuscriptProfile
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1)


class ManuscriptReview(StrictModel):
    expected_revision: int = Field(ge=1)
    status: str
    reason: str = Field(min_length=1)


class ManuscriptCheck(StrictModel):
    filename: str


def create_router(resolve_ws, idle):
    app = APIRouter()
    from paperagent.api.v1.review import create_router as create_paper_review_router
    app.include_router(create_paper_review_router(resolve_ws, idle))

    def execute(fn):
        try:
            return fn()
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/projects/{project_id}/manuscript")
    def get_context(project_id: str):
        return execute(lambda: ManuscriptContext(resolve_ws(project_id)).view())

    @app.put("/projects/{project_id}/manuscript/profile")
    def profile(project_id: str, body: ProfileUpdate):
        ws = resolve_ws(project_id)
        idle(project_id)
        return execute(lambda: ManuscriptContext(ws).set_profile(**body.model_dump(exclude={"profile"}), profile=body.profile))

    @app.post("/projects/{project_id}/manuscript/records")
    def record(project_id: str, body: ManuscriptRecordInput):
        ws = resolve_ws(project_id)
        idle(project_id)
        row = execute(lambda: ManuscriptContext(ws).record(body))
        from paperagent.domain.store import DomainStore
        from paperagent.tasks.memory_manager import MemoryManager
        MemoryManager(DomainStore(ws.root)).sync_adopted_record(project_id, "manuscript", row)
        return row

    @app.post("/projects/{project_id}/manuscript/records/{record_id}/review")
    def review(project_id: str, record_id: str, body: ManuscriptReview):
        ws = resolve_ws(project_id)
        idle(project_id)
        row = execute(lambda: ManuscriptContext(ws).review(record_id, **body.model_dump()))
        from paperagent.domain.store import DomainStore
        from paperagent.tasks.memory_manager import MemoryManager
        MemoryManager(DomainStore(ws.root)).sync_adopted_record(project_id, "manuscript", row)
        return row

    @app.post("/projects/{project_id}/manuscript/check")
    def check(project_id: str, body: ManuscriptCheck):
        ws = resolve_ws(project_id)
        idle(project_id)
        return execute(lambda: ManuscriptContext(ws).inspect_text(body.filename))

    return app


def control_router(service):
    router = APIRouter()
    router.add_api_route('/projects/{project_id}/drafts/{name}', service.get_draft, methods=['GET'])
    router.add_api_route('/projects/{project_id}/templates/extract', service.extract_template, methods=['GET'])
    router.add_api_route('/projects/{project_id}/drafts/{name}/export', service.export_draft, methods=['GET'])
    router.add_api_route('/projects/{project_id}/drafts/{name}', service.save_project_draft, methods=['PUT'])
    return router
