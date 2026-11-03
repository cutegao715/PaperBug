"""Reference entities: the single source of truth for citations.

Draft text never owns citation numbers. It uses stable ``{{ref:<id>}}``
placeholders; numbering and bibliography are recomputed from the Reference
DB at render time. This is what makes section-level revision safe.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

# Placeholder pattern used in draft bodies: {{ref:yang2020dynamic}}
REF_PLACEHOLDER_RE = re.compile(r"\{\{ref:([A-Za-z0-9_-]+)\}\}")


def make_ref_id(first_author: str, year: Optional[int], title_word: str) -> str:
    """Deterministic ref id like ``yang2020dynamic`` from author/year/title."""
    def _slug(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    author = _slug(first_author) or "anon"
    year_part = str(year) if year else "nd"
    word = _slug(title_word) or "untitled"
    return f"{author}{year_part}{word}"


class Reference(BaseModel):
    """A committed literature entity in the project Reference DB."""

    ref_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    # provenance: where this reference came from (semantic_scholar / manual / ...)
    source: str = "manual"
    source_query: Optional[str] = None
    citation_count: Optional[int] = None
    verification: dict = Field(default_factory=dict)

    def to_bibliography_line(self) -> str:
        """Plain APA-ish rendering. Deterministic, no LLM involved."""
        first = self.authors[0] if self.authors else self.ref_id
        et_al = " et al." if len(self.authors) > 1 else ""
        year = f"({self.year})" if self.year else "(n.d.)"
        venue = f". {self.venue}" if self.venue else ""
        doi = f". https://doi.org/{self.doi}" if self.doi else (
            f". {self.url}" if self.url else ""
        )
        return f"{first}{et_al} {year}. {self.title}{venue}{doi}"


class ReferenceCandidate(BaseModel):
    """Uncommitted search result; the agent must save it before citing."""

    ref_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    citation_count: Optional[int] = None
    abstract_snippet: Optional[str] = None
    source: str = "semantic_scholar"

    def to_reference(self, source_query: Optional[str] = None) -> Reference:
        data = self.model_dump(exclude={"abstract_snippet"})
        data["source_query"] = source_query
        return Reference(**data)
