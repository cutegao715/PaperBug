"""Read-only views of existing evidence and locally generated research outputs."""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from paperagent.tools.literature_matrix import run as matrix
from paperagent.tools.research_delivery import run_check


def create_router(resolve_ws):
    app = APIRouter()
    @app.get("/projects/{project_id}/literature-matrix")
    def literature(project_id: str, offset: int = 0, limit: int = 20, theme: str = ""):
        try:
            return matrix({"offset":offset,"limit":limit,"theme":theme}, resolve_ws(project_id))
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/projects/{project_id}/research-outputs")
    def outputs(project_id: str, offset: int = 0, limit: int = 20):
        if offset < 0 or not 1 <= limit <= 50:
            raise HTTPException(422, "Invalid output page")
        ws = resolve_ws(project_id)
        root = (ws.dir / "exports/research").resolve()
        if not root.is_relative_to(ws.dir.resolve()):
            raise HTTPException(422, "Invalid research output root")
        paths = sorted(root.glob("*/manifest.json"), key=lambda p:p.stat().st_mtime, reverse=True)
        rows = []
        for path in paths[offset:offset+limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                check = run_check({"manifest_path":str(path)}, ws)
                files = [r["path"] for r in data["outputs"]]
                if (path.parent / "submission.zip").is_file():
                    files.append("submission.zip")
                rows.append({"output_id":path.parent.name, "kind":data["kind"], "files":files,
                             "warnings":data["warnings"], **check})
            except (ValueError, OSError, KeyError) as exc:
                rows.append({"output_id":path.parent.name,"kind":"unavailable","files":[],"needs_review":True,"issues":[str(exc)]})
        return {"outputs":rows,"total":len(paths),"next_offset":offset+limit if offset+limit<len(paths) else None}

    @app.get("/projects/{project_id}/research-outputs/{output_id}/file")
    def download(project_id: str, output_id: str, name: str):
        ws = resolve_ws(project_id)
        root = (ws.dir / "exports/research").resolve()
        folder = (root / output_id).resolve()
        path = (folder / name).resolve()
        if (not root.is_relative_to(ws.dir.resolve()) or folder.parent != root
                or not path.is_relative_to(folder) or not path.is_file()):
            raise HTTPException(404, "Research output not found")
        # Downloads are attachments, including HTML; no arbitrary active file is hosted inline.
        return FileResponse(path, filename=path.name)

    return app


def control_router(service):
    router = APIRouter()
    router.add_api_route('/tools', service.tools, methods=['GET'])
    router.add_api_route('/journals/recommend', service.recommend_journals, methods=['POST'])
    router.add_api_route('/grants/discover', service.discover_grants, methods=['POST'])
    router.add_api_route('/disciplines', service.list_disciplines, methods=['GET'])
    router.add_api_route('/disciplines/{discipline_id}', service.get_discipline, methods=['GET'])
    router.add_api_route('/skills', service.list_skills, methods=['GET'])
    router.add_api_route('/skills/{skill_key}', service.get_skill, methods=['GET'])
    router.add_api_route('/skills/{skill_key}/resource', service.get_skill_resource, methods=['GET'])
    return router
