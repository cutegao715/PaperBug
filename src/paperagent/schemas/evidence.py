"""Evidence cards: per-paper compressed evidence objects.

A PDF is read once, then reduced to Evidence objects (verbatim quote +
locator). Full text never lingers in agent context - this is the token
strategy the architecture review committed to.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Evidence(BaseModel):
    """One verbatim quote from one source, tied to the claim it supports."""

    evidence_id: str
    ref_id: str
    quote: str
    # locator: page number in the source PDF (1-based), or section label
    page: Optional[int] = None
    section: Optional[str] = None
    # what the agent claims this quote supports
    supports: Optional[str] = None
    # local path to the source file, enables deterministic quote verification
    source_path: Optional[str] = None
    source_sha256: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_coverage: Literal["full_text", "partial", "abstract_only", "unknown"] = "unknown"
    relation: Literal["supports", "contradicts", "context"] = "supports"
    themes: list[str] = Field(default_factory=list, max_length=30)
    method: str = ""
    population: str = ""
    finding: str = ""
    limitations: list[str] = Field(default_factory=list, max_length=30)
    created_at: str = Field(default_factory=_now)
