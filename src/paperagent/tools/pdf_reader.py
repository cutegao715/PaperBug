"""Paper Reading tool: PDF -> structured per-page text for evidence cards.

Deterministic extraction (pypdf). Output is capped per page so one call
can never blow the agent context budget; the agent compresses what it
reads into Evidence cards with verbatim quotes + page anchors.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader

MAX_CHARS_PER_PAGE = 3500
DEFAULT_MAX_PAGES = 5


def read_pdf(path: Path, pages: list[int] | None = None,
             max_pages: int = DEFAULT_MAX_PAGES) -> dict[str, Any]:
    reader = PdfReader(str(path))
    total = len(reader.pages)
    wanted = pages if pages else list(range(1, min(total, max_pages) + 1))

    page_texts: list[dict[str, Any]] = []
    for page_no in wanted:
        if not 1 <= page_no <= total:
            return {"error": f"page {page_no} out of range (document has {total})"}
        text = (reader.pages[page_no - 1].extract_text() or "").strip()
        truncated = len(text) > MAX_CHARS_PER_PAGE
        page_texts.append({
            "page": page_no,
            "text": text[:MAX_CHARS_PER_PAGE],
            "truncated": truncated,
        })
    return {
        "path": str(path),
        "total_pages": total,
        "pages": page_texts,
        "provenance": f"local:{path}",
    }


def run(input: dict, resolve, timeout: float = 20.0) -> dict[str, Any]:
    path = resolve(input["path"])
    if not path.exists():
        return {"error": f"file not found: {path}"}
    pages = input.get("pages")
    result = read_pdf(path, pages=pages)
    result.setdefault("provenance", f"local:{path}")
    return result
