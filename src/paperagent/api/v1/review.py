"""Review artifacts live within the workspace, with existing run protection."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import Field

from paperagent.domain.manuscript import StrictModel
from paperagent.domain.paper_review import PaperReviewInput, PaperReviews, ReviewFollowupInput


class AdoptFollowup(StrictModel):
    expected_revision: int = Field(ge=1)
    followup_id: str
    reason: str = Field(min_length=1, max_length=3000)


def create_router(resolve_ws, idle):
    app = APIRouter()
    def execute(fn):
        try:
            return fn()
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/projects/{project_id}/paper-reviews")
    def listing(project_id: str):
        return execute(lambda: PaperReviews(resolve_ws(project_id)).view())

    @app.get("/projects/{project_id}/paper-reviews/basis")
    def basis(project_id: str, filename: str):
        return execute(lambda: PaperReviews(resolve_ws(project_id)).basis(filename))

    @app.post("/projects/{project_id}/paper-reviews")
    def create(project_id: str, body: PaperReviewInput):
        ws = resolve_ws(project_id)
        idle(project_id)
        return execute(lambda: PaperReviews(ws).create(body))

    @app.get("/projects/{project_id}/paper-reviews/{report_id}/report", response_class=PlainTextResponse)
    def report(project_id: str, report_id: str):
        return execute(lambda: PaperReviews(resolve_ws(project_id)).report_text(report_id))

    @app.post("/projects/{project_id}/paper-reviews/{report_id}/issues/{issue_id}")
    def followup(project_id: str, report_id: str, issue_id: str, body: ReviewFollowupInput):
        ws = resolve_ws(project_id)
        idle(project_id)
        return execute(lambda: PaperReviews(ws).followup(report_id, issue_id, body))

    @app.post("/projects/{project_id}/paper-reviews/{report_id}/issues/{issue_id}/adopt")
    def adopt(project_id: str, report_id: str, issue_id: str, body: AdoptFollowup):
        ws = resolve_ws(project_id)
        idle(project_id)
        return execute(lambda: PaperReviews(ws).adopt(report_id, issue_id, **body.model_dump()))

    return app


def control_router(service):
    router = APIRouter()
    return router
