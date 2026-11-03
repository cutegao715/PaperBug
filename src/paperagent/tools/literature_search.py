"""Literature Search tool.

Providers:
- Semantic Scholar Graph API (default; works keyless with rate limits,
  ``S2_API_KEY`` env var raises the quota)
- offline stub (``provider="offline_stub"``): returns clearly-labelled
  SYNTHETIC candidates so tests and demos run without network. Stub
  references are marked ``source="offline_stub"`` and the renderer adds
  a warning banner - fabricated literature must never look real.
"""
from __future__ import annotations

import os
from typing import Any

import requests

from paperagent.schemas.references import ReferenceCandidate, make_ref_id

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,authors,year,venue,externalIds,url,abstract,citationCount"
OPENALEX_URL = "https://api.openalex.org/works"


def _s2_to_candidate(paper: dict) -> ReferenceCandidate:
    authors = [a.get("name", "") for a in paper.get("authors", [])]
    title = paper.get("title") or "Untitled"
    year = paper.get("year")
    first_word = (title.split() or ["untitled"])[0]
    return ReferenceCandidate(
        ref_id=make_ref_id(authors[0].split()[-1] if authors else "anon",
                           year, first_word),
        title=title,
        authors=authors,
        year=year,
        venue=paper.get("venue") or None,
        doi=(paper.get("externalIds") or {}).get("DOI"),
        url=paper.get("url"),
        citation_count=paper.get("citationCount"),
        abstract_snippet=(paper.get("abstract") or "")[:300] or None,
        source="semantic_scholar",
    )


def _openalex_to_candidate(work: dict) -> ReferenceCandidate:
    authorships = work.get("authorships") or []
    authors = [a.get("author", {}).get("display_name", "")
               for a in authorships if a.get("author")]
    title = (work.get("display_name") or "Untitled").strip()
    year = work.get("publication_year")
    venue = ((work.get("primary_location") or {}).get("source") or {}) \
        .get("display_name")
    cited = work.get("cited_by_count")
    first_word = (title.split() or ["untitled"])[0]
    return ReferenceCandidate(
        ref_id=make_ref_id(authors[0].split()[-1] if authors else "anon",
                           year, first_word),
        title=title,
        authors=authors,
        year=year,
        venue=venue or None,
        doi=(work.get("doi") or "").replace("https://doi.org/", "") or None,
        url=work.get("id"),
        citation_count=cited if isinstance(cited, int) else None,
        source="openalex",
    )


def search_semantic_scholar(query: str, max_results: int = 5,
                            timeout: float = 20.0) -> dict[str, Any]:
    headers = {}
    key = os.environ.get("S2_API_KEY", "").strip()
    if key:
        headers["x-api-key"] = key
    resp = requests.get(
        S2_SEARCH_URL,
        params={"query": query, "limit": max_results, "fields": S2_FIELDS},
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    candidates = [_s2_to_candidate(p) for p in payload.get("data", [])]
    return {"candidates": [c.model_dump() for c in candidates],
            "provenance": f"{S2_SEARCH_URL}?query={query}"}


def search_openalex(query: str, max_results: int = 5,
                    timeout: float = 20.0) -> dict[str, Any]:
    """OpenAlex: free, generous rate limits - the fallback when S2 is 429."""
    resp = requests.get(
        OPENALEX_URL,
        params={"search": query, "per-page": min(max_results, 10)},
        timeout=timeout,
    )
    resp.raise_for_status()
    candidates = [_openalex_to_candidate(w)
                  for w in resp.json().get("results", [])]
    return {"candidates": [c.model_dump() for c in candidates],
            "provenance": f"{OPENALEX_URL}?search={query}"}


def search_offline_stub(query: str, max_results: int = 5,
                        timeout: float = 20.0) -> dict[str, Any]:
    """Deterministic synthetic results. Clearly marked, never citable as real."""
    topic = query.strip() or "research"
    def stub(i: int) -> ReferenceCandidate:
        return ReferenceCandidate(
            ref_id=f"stub{i}0000paper",
            title=f"[SYNTHETIC STUB {i}] A study on {topic}",
            authors=[f"Stub Author {i}"],
            year=2020 + i,
            venue="Offline Demo Journal",
            source="offline_stub",
        )
    candidates = [stub(i) for i in range(1, min(max_results, 3) + 1)]
    return {"candidates": [c.model_dump() for c in candidates],
            "provenance": "offline_stub (synthetic data for tests/demo)",
            "warning": "offline mode: references are synthetic, not real literature"}


def run(input: dict, timeout: float = 20.0) -> dict[str, Any]:
    """Tool entrypoint. ``input.provider`` selects backend; real providers
    fall back to OpenAlex when the primary fails (e.g. S2 rate limits)."""
    query = input["query"]
    max_results = int(input.get("max_results", 5))
    provider = input.get("provider", "semantic_scholar")
    if provider == "offline_stub":
        return search_offline_stub(query, max_results, timeout)
    try:
        return search_semantic_scholar(query, max_results, timeout)
    except Exception:
        return search_openalex(query, max_results, timeout)
