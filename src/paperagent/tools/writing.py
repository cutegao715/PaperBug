"""Writing tools: citation formatting and draft rendering."""
from __future__ import annotations

from typing import Any

from paperagent.schemas.references import Reference
from paperagent.workspace.export import (
    check_docx_layout,
    export_draft_docx_with_template,
    extract_docx_template_fields,
)
from paperagent.workspace.render import render_project_draft
from paperagent.workspace.workspace import Workspace


def run_citation_format(input: dict, ws: Workspace | None = None,
                        timeout: float = 20.0) -> dict[str, Any]:
    style = str(input.get("style") or "apa").lower()
    refs = _refs(input, ws)
    return {
        "style": style,
        "bibliography": [_format_reference(ref, style) for ref in refs],
        "provenance": "workspace:references" if not input.get("references") else "inline_references",
    }


def run_draft_render(input: dict, ws: Workspace,
                     timeout: float = 20.0) -> dict[str, Any]:
    filename = input["filename"]
    rendered = render_project_draft(ws, filename)
    output_filename = input.get("output_filename")
    output_path = None
    if output_filename:
        output_path = ws.write_draft(output_filename, rendered)
    return {
        "filename": filename,
        "rendered": rendered,
        "output_path": str(output_path) if output_path else None,
        "provenance": f"workspace:{ws.project_id}:{filename}",
    }


def run_template_extract(input: dict, ws: Workspace,
                         timeout: float = 20.0) -> dict[str, Any]:
    path = ws.resolve_path(input["path"])
    if path.suffix.lower() != ".docx":
        return {"error": "template_extract currently supports .docx templates"}
    return extract_docx_template_fields(path)


def run_fill_docx_template(input: dict, ws: Workspace,
                           timeout: float = 20.0) -> dict[str, Any]:
    filename = input["filename"]
    template_path = ws.resolve_path(input["template_path"])
    if template_path.suffix.lower() != ".docx":
        return {"error": "fill_docx_template currently supports .docx templates"}
    output = export_draft_docx_with_template(ws, filename, template_path)
    return {
        "filename": filename,
        "template_path": str(template_path),
        "output_path": str(output),
        "layout_check": check_docx_layout(output),
        "provenance": f"workspace:{ws.project_id}:template_export",
    }


def _refs(input: dict, ws: Workspace | None) -> list[Reference]:
    if input.get("references"):
        return [Reference.model_validate(ref) for ref in input["references"]]
    if ws is None:
        return []
    return ws.list_references()


def _format_reference(ref: Reference, style: str) -> str:
    if style in {"apa", "apa7"}:
        return ref.to_bibliography_line()
    if style in {"gbt7714", "gb/t", "gb"}:
        authors = ", ".join(ref.authors) if ref.authors else ref.ref_id
        year = f"{ref.year}" if ref.year else "n.d."
        venue = f"[J]. {ref.venue}" if ref.venue else "[J]."
        doi = f" DOI: {ref.doi}" if ref.doi else (f" {ref.url}" if ref.url else "")
        return f"{authors}. {ref.title}{venue} {year}.{doi}".strip()
    if style == "ieee":
        authors = ", ".join(ref.authors) if ref.authors else ref.ref_id
        venue = f", {ref.venue}" if ref.venue else ""
        year = f", {ref.year}" if ref.year else ""
        doi = f", doi: {ref.doi}" if ref.doi else (f", {ref.url}" if ref.url else "")
        return f"{authors}, \"{ref.title}\"{venue}{year}{doi}."
    if style == "chicago":
        authors = ", ".join(ref.authors) if ref.authors else ref.ref_id
        year = f"{ref.year}. " if ref.year else ""
        venue = f" {ref.venue}." if ref.venue else ""
        doi = f" https://doi.org/{ref.doi}" if ref.doi else (f" {ref.url}" if ref.url else "")
        return f"{authors}. {year}\"{ref.title}.\"{venue}{doi}".strip()
    if style == "vancouver":
        authors = ", ".join(ref.authors) if ref.authors else ref.ref_id
        venue = f" {ref.venue}." if ref.venue else ""
        year = f" {ref.year}." if ref.year else ""
        doi = f" doi:{ref.doi}." if ref.doi else (f" {ref.url}" if ref.url else "")
        return f"{authors}. {ref.title}.{venue}{year}{doi}".strip()
    return ref.to_bibliography_line()
