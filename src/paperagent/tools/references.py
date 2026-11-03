"""Reference metadata resolution tools."""
from __future__ import annotations

from typing import Any

import requests

from paperagent.schemas.references import ReferenceCandidate, make_ref_id

CROSSREF_WORKS = "https://api.crossref.org/works"


def run_reference_resolve(input: dict, timeout: float = 20.0) -> dict[str, Any]:
    provider = input.get("provider", "crossref")
    if provider == "offline_stub":
        return _offline_stub(input)
    if provider != "crossref":
        return {"error": f"unsupported reference provider: {provider}"}
    doi = (input.get("doi") or "").strip()
    title = (input.get("title") or "").strip()
    if doi:
        return _resolve_doi(doi, timeout)
    if title:
        return _resolve_title(title, int(input.get("max_results", 5)), timeout)
    return {"error": "reference_resolve requires doi or title"}


def _resolve_doi(doi: str, timeout: float) -> dict[str, Any]:
    resp = requests.get(f"{CROSSREF_WORKS}/{doi}", timeout=timeout)
    resp.raise_for_status()
    item = resp.json().get("message", {})
    candidate = _crossref_to_candidate(item)
    return {
        "candidates": [candidate.model_dump() | {"publication_years": _years(item)}],
        "provenance": f"{CROSSREF_WORKS}/{doi}",
    }


def _resolve_title(title: str, max_results: int, timeout: float) -> dict[str, Any]:
    resp = requests.get(
        CROSSREF_WORKS,
        params={"query.title": title, "rows": min(max_results, 20)},
        timeout=timeout,
    )
    resp.raise_for_status()
    items = resp.json().get("message", {}).get("items", [])
    candidates = [_crossref_to_candidate(item) for item in items]
    return {
        "candidates": [c.model_dump() | {"publication_years": _years(item)} for c, item in zip(candidates, items)],
        "provenance": f"{CROSSREF_WORKS}?query.title={title}",
    }


def _crossref_to_candidate(item: dict[str, Any]) -> ReferenceCandidate:
    title = (item.get("title") or ["Untitled"])[0]
    authors = [
        " ".join(p for p in [a.get("given"), a.get("family")] if p).strip()
        for a in item.get("author", [])
    ]
    authors = [a for a in authors if a]
    year = _year(item)
    first = authors[0].split()[-1] if authors else "anon"
    first_word = (title.split() or ["untitled"])[0]
    return ReferenceCandidate(
        ref_id=make_ref_id(first, year, first_word),
        title=title,
        authors=authors,
        year=year,
        venue=(item.get("container-title") or [""])[0] or None,
        doi=item.get("DOI"),
        url=item.get("URL"),
        source="crossref",
    )


def _year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])[0]
        if parts:
            try:
                return int(parts[0])
            except Exception:
                return None
    return None


def _years(item):
    years = []
    for key in ("published-print", "published-online", "issued"):
        year = _year({key: item.get(key)})
        if year is not None and year not in years:
            years.append(year)
    return years


def _offline_stub(input: dict) -> dict[str, Any]:
    title = input.get("title") or f"Resolved metadata for {input.get('doi', 'unknown DOI')}"
    year = input.get("year") or 2026
    candidate = ReferenceCandidate(
        ref_id=make_ref_id("stub", int(year), (title.split() or ["resolved"])[0]),
        title=title,
        authors=["Stub Author"],
        year=int(year),
        venue="Offline Metadata Source",
        doi=input.get("doi"),
        url=input.get("url"),
        source="offline_stub",
    )
    return {
        "candidates": [candidate.model_dump()],
        "provenance": "offline_stub (synthetic metadata for tests/demo)",
        "warning": "offline mode: resolved metadata is synthetic",
    }
