"""Local research presentations and submission packages with source manifests."""
from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from pydantic import BaseModel, Field, ConfigDict

from paperagent.domain.project_dossier import ProjectDossier, ProjectSource
from paperagent.tools.figure_checks import svg_root
from paperagent.workspace.render import render_project_draft


class Slide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=150)
    points: list[str] = Field(default_factory=list, max_length=10)
    notes: str = Field(default="", max_length=12000)
    sources: list[ProjectSource] = Field(default_factory=list, max_length=20)
    figure_id: str | None = None


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _folder(ws, kind):
    path = (ws.dir / "exports" / "research" / (kind + "_" + uuid.uuid4().hex[:16])).resolve()
    if not path.is_relative_to(ws.dir.resolve()):
        raise ValueError("Export must remain inside this workspace")
    path.mkdir(parents=True)
    return path


def _figures(ws, ids):
    known = {a.figure_id: a for a in ws.list_artifacts() if a.artifact_type == "figure"}
    results = []
    for key in dict.fromkeys(ids):
        if key not in known or known[key].format != "svg":
            raise ValueError("Choose an existing SVG figure artifact: " + key)
        artifact = known[key]
        if artifact.image_path:
            path = ws.resolve_path(artifact.image_path)
            if not ws.is_path_allowed(path):
                raise ValueError("Figure is outside allowed roots")
            raw = path.read_bytes()
        else:
            raw = artifact.spec.encode()
        svg_root(raw.decode("utf-8-sig"))
        results.append((artifact, raw))
    return results


def _manifest(ws, folder, kind, sources, figures, warnings):
    rows = []
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            rows.append({"path": str(path.relative_to(folder)).replace("\\", "/"), "sha256": digest(path.read_bytes())})
    payload = {"schema_version": 1, "kind": kind, "sources": sources,
               "figures": [{"figure_id": a.figure_id, "sha256": digest(raw),
                            "artifact": a.model_dump()} for a, raw in figures],
               "outputs": rows, "warnings": warnings,
               "interpretation": "Source linkage and file integrity only; not scientific, visual-layout or venue-compliance certification."}
    path = folder / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_presentation(input, ws, timeout=20.0):
    values = input.get("slides") or []
    if not 1 <= len(values) <= 80:
        raise ValueError("Presentation needs 1-80 slides")
    slides = [Slide.model_validate(s) for s in values]
    title = str(input.get("title") or "Research presentation")
    figures = _figures(ws, [s.figure_id for s in slides if s.figure_id])
    images = {a.figure_id: base64.b64encode(raw).decode() for a, raw in figures}
    dossier = ProjectDossier(ws)
    sources = [dossier.capture_source(s) for slide in slides for s in slide.sources]
    warnings = []
    for i, slide in enumerate(slides, 1):
        if not slide.sources:
            warnings.append(f"Slide {i}: no source links; factual support remains unverified")
        if sum(len(p) for p in slide.points) > 700:
            warnings.append(f"Slide {i}: dense content requires rendered inspection")
    folder = _folder(ws, "presentation")
    sections = []
    for i, slide in enumerate(slides, 1):
        points = "".join("<li>" + html.escape(p) + "</li>" for p in slide.points)
        image = f'<img alt="{html.escape(slide.figure_id, quote=True)}" src="data:image/svg+xml;base64,{images[slide.figure_id]}">' if slide.figure_id else ""
        links = "; ".join(s.kind + ":" + s.key + (" · " + s.locator if s.locator else "") for s in slide.sources)
        sections.append(f'<section id="slide-{i}"><div class="page">{i}/{len(slides)}</div><h1>{html.escape(slide.title)}</h1><div class="body"><ul>{points}</ul>{image}</div><footer>{html.escape(links)}</footer><details><summary>讲稿 / Speaker notes</summary><p>{html.escape(slide.notes)}</p></details></section>')
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + html.escape(title) + '''</title><style>
body{margin:0;background:#e9edf1;color:#183347;font:24px Arial,"Microsoft YaHei",sans-serif}section{box-sizing:border-box;position:relative;max-width:1200px;min-height:675px;margin:30px auto;padding:46px 60px;background:white;box-shadow:0 4px 18px #0001}h1{font-size:36px;line-height:1.25;margin:0 0 30px;border-bottom:4px solid #0072b2;padding-bottom:20px}.body{display:flex;gap:30px;align-items:center}.body ul{flex:1;min-width:0;line-height:1.55;padding-left:30px}li{margin-bottom:15px;overflow-wrap:anywhere}img{max-width:48%;max-height:360px}footer{font-size:13px;color:#526576;margin-top:30px;overflow-wrap:anywhere}.page{position:absolute;top:15px;right:22px;font-size:14px}details{font-size:16px;margin-top:20px}details p{white-space:pre-wrap;line-height:1.6}@media print{body{background:white}section{box-shadow:none;margin:0;break-after:page}details{display:none}@page{size:landscape;margin:8mm}}
</style><main>''' + "".join(sections) + '''</main><script>document.addEventListener('keydown',e=>{if(!['ArrowRight','ArrowLeft'].includes(e.key))return;const s=[...document.querySelectorAll('section')];let n=s.findIndex(x=>x.getBoundingClientRect().bottom>80);s[Math.max(0,Math.min(s.length-1,n+(e.key==='ArrowRight'?1:-1)))].scrollIntoView({behavior:'smooth'});});</script></html>'''
    (folder / "presentation.html").write_text(document, encoding="utf-8")
    (folder / "slides.json").write_text(json.dumps({"title":title,"slides":[s.model_dump() for s in slides]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "speaker-notes.md").write_text("\n\n".join(
        f"## {i}. {slide.title}\n\n{slide.notes}" for i, slide in enumerate(slides, 1)), encoding="utf-8")
    _editable_pptx(folder, title, slides, figures, warnings)
    manifest = _manifest(ws, folder, "presentation", sources, figures, warnings)
    return {"output_path": str(folder / "presentation.html"), "manifest_path": str(manifest),
            "format": "html", "pptx_path": str(folder / "presentation.pptx"), "slide_count": len(slides), "warnings": warnings,
            "provenance": "workspace:structured_slides", "checks": {"structure":"checked", "visual_layout":"not_evaluated"}}


def _editable_pptx(folder, title, slides, figures, warnings):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(13.333), Inches(7.5)
    presentation.core_properties.title = title
    for i, source in enumerate(slides, 1):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        heading = slide.shapes.add_textbox(Inches(.6), Inches(.4), Inches(12.1), Inches(.85)).text_frame
        heading.word_wrap = True
        heading.text = source.title
        heading.paragraphs[0].font.size = Pt(30)
        heading.paragraphs[0].font.bold = True
        heading.paragraphs[0].font.color.rgb = RGBColor.from_string("183347")
        body = slide.shapes.add_textbox(Inches(.8), Inches(1.6), Inches(11.7), Inches(4.8)).text_frame
        body.word_wrap = True
        for n, point in enumerate(source.points):
            paragraph = body.paragraphs[0] if n == 0 else body.add_paragraph()
            paragraph.text = "• " + point
            paragraph.font.name = "Microsoft YaHei"
            paragraph.font.size = Pt(22 if len(source.points) <= 5 else 18)
            paragraph.space_after = Pt(16)
        footer = slide.shapes.add_textbox(Inches(.6), Inches(6.8), Inches(12), Inches(.45)).text_frame
        footer.text = f"{i}/{len(slides)} · " + "; ".join(s.key for s in source.sources)
        footer.paragraphs[0].font.size = Pt(10)
        slide.notes_slide.notes_text_frame.text = source.notes
        if source.figure_id:
            # Keep originals with the editable deck; HTML is the visual figure preview.
            artifact, raw = next((a, b) for a, b in figures if a.figure_id == source.figure_id)
            assets = folder / "figures"
            assets.mkdir(exist_ok=True)
            (assets / (artifact.figure_id + ".svg")).write_bytes(raw)
            slide.notes_slide.notes_text_frame.text += "\nFigure source: figures/" + artifact.figure_id + ".svg"
            warnings.append(f"PPTX slide {i}: insert supplied SVG figure {artifact.figure_id}; HTML preview includes it")
        if sum(len(p) for p in source.points) > 500:
            warnings.append(f"PPTX slide {i}: review text density and overflow in PowerPoint")
    presentation.save(folder / "presentation.pptx")


def run_submission(input, ws, timeout=20.0):
    requested = [input.get("filename", "draft.md"), *(input.get("companions") or [])]
    filenames = list(dict.fromkeys(n if n.endswith(".md") else n + ".md" for n in requested))
    if len(filenames) > 20:
        raise ValueError("At most 20 manuscript/companion drafts")
    dossier = ProjectDossier(ws)
    sources = [dossier.capture_source(ProjectSource(kind="draft", key=name)) for name in filenames]
    sources += [dossier.capture_source(ProjectSource.model_validate(s)) for s in input.get("requirement_sources", [])]
    figures = _figures(ws, input.get("figure_ids") or [])
    folder = _folder(ws, "submission")
    warnings = ["Venue compliance and final document layout require review against current sourced requirements."]
    from paperagent.kernel.citation_integrity import check_citation_integrity
    from paperagent.workspace.export import export_draft_docx
    for name in filenames:
        # Source names are validated by the existing draft reader and snapshotter.
        text = ws.read_draft(name)
        report = check_citation_integrity(text, ws.list_references(), ws.list_evidence(), resolve=ws.resolve_path)
        if report.errors:
            raise ValueError("Unresolved citation integrity: " + "; ".join(report.errors))
        warnings.extend(report.warnings)
        stem = Path(name).stem
        (folder / (stem + ".md")).write_text(render_project_draft(ws, name), encoding="utf-8")
        docx = export_draft_docx(ws, name)
        (folder / (stem + ".docx")).write_bytes(docx.read_bytes())
    if figures:
        (folder / "figures").mkdir()
        for i, (artifact, raw) in enumerate(figures, 1):
            (folder / "figures" / f"figure-{i}.svg").write_bytes(raw)
            (folder / "figures" / f"figure-{i}-caption.txt").write_text(artifact.caption, encoding="utf-8")
            if not artifact.caption:
                warnings.append("Missing figure caption: " + artifact.figure_id)
    if not input.get("requirement_sources"):
        warnings.append("No venue requirement source supplied")
    # The distributable inventory excludes internal source paths, evidence quotes
    # and author notes. Keep those in the workspace manifest outside the ZIP.
    inventory = [{"path":str(p.relative_to(folder)).replace("\\", "/"), "sha256":digest(p.read_bytes())}
                 for p in sorted(folder.rglob("*")) if p.is_file()]
    (folder / "package-manifest.json").write_text(json.dumps({"files":inventory,
        "checks":{"venue_compliance":"not_evaluated","visual_layout":"not_evaluated"}}, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = _manifest(ws, folder, "submission", sources, figures, warnings)
    archive = folder / "submission.zip"
    with ZipFile(archive, "x", compression=ZIP_DEFLATED) as stream:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path not in {archive, manifest}:
                stream.write(path, path.relative_to(folder))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["archive"] = {"path":"submission.zip", "sha256":digest(archive.read_bytes())}
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_path": str(archive), "manifest_path": str(manifest), "warnings": warnings,
            "archive_sha256": digest(archive.read_bytes()), "provenance": "workspace:submission_package",
            "checks": {"citation_integrity":"checked", "venue_compliance":"not_evaluated", "visual_layout":"not_evaluated"}}


def run_check(input, ws, timeout=20.0):
    path = ws.resolve_path(input["manifest_path"]).resolve()
    if not path.is_relative_to((ws.dir / "exports" / "research").resolve()) or path.name != "manifest.json":
        raise ValueError("Choose a research delivery manifest in this workspace")
    data = json.loads(path.read_text(encoding="utf-8"))
    issues = ProjectDossier(ws).source_issues({"sources":data["sources"]})
    for source in data["sources"]:
        if source["kind"] == "draft":
            current = ws._draft_path(source["snapshot"]["filename"])
            if not current.is_file() or digest(current.read_bytes()) != source["snapshot"]["version"]:
                issues.append("Draft changed: " + source["key"])
        elif source["kind"] == "evidence":
            current = next((e for e in ws.list_evidence() if e.evidence_id == source["key"]), None)
            if current is None or current.model_dump(mode="json") != source["evidence"]:
                issues.append("Evidence annotation changed: " + source["key"])
            if current and current.source_path and current.source_sha256:
                p = ws.resolve_path(current.source_path)
                if not ws.is_path_allowed(p) or not p.is_file() or digest(p.read_bytes()) != current.source_sha256:
                    issues.append("Evidence source changed or unavailable: " + source["key"])
    for row in data["figures"]:
        try:
            artifact, current = _figures(ws, [row["figure_id"]])[0]
            if digest(current) != row["sha256"] or artifact.model_dump() != row["artifact"]:
                issues.append("Figure changed: " + row["figure_id"])
        except (ValueError, OSError):
            issues.append("Figure unavailable: " + row["figure_id"])
    for row in [*data["outputs"], *([data["archive"]] if data.get("archive") else [])]:
        target = (path.parent / row["path"]).resolve()
        if not target.is_relative_to(path.parent) or not target.is_file() or digest(target.read_bytes()) != row["sha256"]:
            issues.append("Output changed or unavailable: " + row["path"])
    return {"needs_review":bool(issues), "issues":list(dict.fromkeys(issues)), "kind":data["kind"],
            "provenance":"workspace:delivery_manifest", "interpretation":data["interpretation"]}
