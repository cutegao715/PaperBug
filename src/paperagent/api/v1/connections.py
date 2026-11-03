"""Functional bindings for existing V6 controls and dialogs; no prototype changes."""
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict

from paperagent.domain.entities import SubmissionStatus
from paperagent.domain.store import DomainStore
from paperagent.quality.validators import SUBMISSION_TRANSITIONS
from paperagent.tasks.submission_manager import SubmissionManager
from paperagent.workspace.state_store import state_lock


def file_options(ws):
    rows, seen = [], set()
    def add(path, kind, label=None, output=True):
        path = Path(path).resolve()
        if path in seen or not path.is_file() or not ws.is_path_allowed(path):
            return
        seen.add(path)
        local = path.is_relative_to(ws.dir.resolve())
        value = path.relative_to(ws.dir.resolve()).as_posix() if local else str(path)
        rows.append({"value": value, "label": label or path.name, "kind": kind,
                     "can_bind_output": output and local, "size": path.stat().st_size})
    for path in sorted(ws.drafts_dir.glob("*.md")):
        if not path.name.endswith(".rendered.md"):
            add(path, "draft")
    for item in ws.list_materials():
        if item.get("path"):
            add(item["path"], "material", item.get("filename"), output=False)
    exports = (ws.dir / "exports").resolve()
    if exports.is_relative_to(ws.dir.resolve()):
        for path in sorted(exports.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".docx", ".pptx", ".pdf", ".zip", ".svg", ".json", ".csv", ".html"}:
                add(path, "export", path.relative_to(exports).as_posix())
    return rows


class SubmissionCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=1000)
    journal_name: str = ""
    manuscript_id: str = ""
    status: SubmissionStatus = "submitted"
    submitted_at: date | None = None
    previous_submission_id: str | None = None


class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SubmissionStatus
    expected_status: SubmissionStatus
    source_note: str = Field(min_length=1, max_length=8000)


class EmailInput(BaseModel):
    subject: str = ""
    text: str = Field(min_length=1, max_length=100000)
    apply: bool = False


class PackageInput(BaseModel):
    filename: str
    companions: list[str] = Field(default_factory=list, max_length=19)


class PresentationInput(BaseModel):
    filename: str
    title: str = Field(default="", max_length=150)
    # An author-approved slide/notes JSON draft, not generated scientific claims.


def submission_view(sub):
    return {**sub.model_dump(), "allowed_statuses": sorted(SUBMISSION_TRANSITIONS[sub.status]),
            "next_workflow": "venue_matching" if sub.status == "rejected" else "submission_tracking",
            "primary_complete": sub.status in {"accepted", "published"},
            "ended": sub.status in {"rejected", "withdrawn", "published"}}


def create_router(resolve_ws, idle):
    app = APIRouter()
    @app.get("/projects/{project_id}/file-options")
    def files(project_id: str):
        return {"files": file_options(resolve_ws(project_id))}

    @app.get("/projects/{project_id}/files/download")
    def download(project_id: str, value: str):
        ws = resolve_ws(project_id)
        row = next((r for r in file_options(ws) if r["value"] == value), None)
        if row is None:
            raise HTTPException(404, "Select a listed research file")
        path = Path(value) if Path(value).is_absolute() else ws.dir / value
        return FileResponse(path, filename=path.name)

    @app.get("/projects/{project_id}/submissions")
    def submissions(project_id: str):
        ws = resolve_ws(project_id)
        return {"submissions": [submission_view(s) for s in DomainStore(ws.root).list_submissions()
                                if s.project_id == project_id]}

    @app.post("/projects/{project_id}/submissions")
    def create_submission(project_id: str, body: SubmissionCreateInput):
        ws = resolve_ws(project_id)
        idle(project_id)
        store = DomainStore(ws.root)
        try:
            with state_lock(store.dir / "submissions.jsonl"):
                sub = SubmissionManager(store).create(project_id=project_id, source="user_record",
                    **body.model_dump(mode="json"))
            return submission_view(sub)
        except KeyError:
            raise HTTPException(404, "Previous submission not found")
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/projects/{project_id}/submissions/{submission_id}/status")
    def status(project_id: str, submission_id: str, body: StatusInput):
        ws = resolve_ws(project_id)
        idle(project_id)
        store = DomainStore(ws.root)
        with state_lock(store.dir / "submissions.jsonl"):
            manager = SubmissionManager(store)
            try:
                current = manager._get(submission_id)
                if current.project_id != project_id:
                    raise KeyError(submission_id)
                if current.status != body.expected_status:
                    raise HTTPException(409, "Submission changed; reload before recording a decision")
                return submission_view(manager.transition(submission_id, body.status,
                    source="user_record", note=body.source_note))
            except KeyError:
                raise HTTPException(404, "Submission not found in this research space")
            except ValueError as exc:
                raise HTTPException(422, str(exc))

    @app.post("/projects/{project_id}/submission-email")
    def email(project_id: str, body: EmailInput):
        from paperagent.tasks.submission_parser import SubmissionEmailParser
        ws = resolve_ws(project_id)
        parsed = SubmissionEmailParser().parse(body.text, subject=body.subject)
        result = {"parsed": parsed.model_dump()}
        if body.apply:
            idle(project_id)
            try:
                store = DomainStore(ws.root)
                with state_lock(store.dir / "submissions.jsonl"):
                    result["submission"] = submission_view(SubmissionManager(store).apply_parsed_email(
                        parsed, project_id=project_id, source="user_supplied_email"))
            except ValueError as exc:
                raise HTTPException(422, str(exc))
        return result

    @app.post("/projects/{project_id}/deliveries/submission-package")
    def package(project_id: str, body: PackageInput):
        from paperagent.tools.research_delivery import run_submission
        idle(project_id)
        try:
            return run_submission(body.model_dump(), resolve_ws(project_id))
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))

    @app.post("/projects/{project_id}/deliveries/presentation")
    def presentation(project_id: str, body: PresentationInput):
        import json
        from paperagent.tools.research_delivery import run_presentation
        idle(project_id)
        ws = resolve_ws(project_id)
        try:
            data = json.loads(ws.read_draft(body.filename))
            if not isinstance(data, dict) or not isinstance(data.get("slides"), list):
                raise ValueError("Select a saved slide JSON draft with a slides array")
            # Bind the author-approved structure to its real input version.
            for slide in data["slides"]:
                slide.setdefault("sources", []).append({"kind": "draft", "key": body.filename})
            return run_presentation({"title": body.title or data.get("title", "科研演示"), "slides": data["slides"]}, ws)
        except json.JSONDecodeError:
            raise HTTPException(422, "所选文件还不是演示结构稿，请先让助手基于论文准备演示结构与讲稿")
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(422, str(exc))

    return app


def control_router(service):
    router = APIRouter()
    router.add_api_route('/submissions', service.list_submissions, methods=['GET'])
    router.add_api_route('/submissions', service.create_submission, methods=['POST'])
    router.add_api_route('/submissions/{submission_id}/status', service.set_submission_status, methods=['POST'])
    router.add_api_route('/submissions/parse-email', service.parse_submission_email, methods=['POST'])
    router.add_api_route('/connectors/email/submission', service.ingest_submission_email, methods=['POST'])
    router.add_api_route('/connectors/local-folder/scan', service.scan_local_folder, methods=['POST'])
    router.add_api_route('/connectors/zotero/items', service.list_zotero_items, methods=['GET'])
    router.add_api_route('/connectors/zotero/sync', service.sync_zotero_items, methods=['POST'])
    return router
