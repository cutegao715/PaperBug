"""Deterministic parser for editorial submission emails.

This parser is intentionally conservative. It extracts common explicit
fields first (Manuscript ID, Journal, Title, Status) and then falls back
to keyword status detection. Ambiguous emails should produce status=None
so the caller can route them for human/LLM review later.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from paperagent.domain.entities import SubmissionStatus


class ParsedSubmissionEmail(BaseModel):
    manuscript_id: str = ""
    journal_name: str = ""
    title: str = ""
    status: Optional[SubmissionStatus] = None
    note: str = ""


class SubmissionEmailParser:
    def parse(self, text: str, subject: str = "") -> ParsedSubmissionEmail:
        blob = f"{subject}\n{text}".strip()
        manuscript_id = _first_match(blob, [
            r"\bmanuscript\s*(?:id|number|no\.?)\s*[:#-]?\s*([A-Za-z0-9._/-]+)",
            r"\bms\s*(?:id|number|no\.?)\s*[:#-]?\s*([A-Za-z0-9._/-]+)",
        ])
        journal_name = _first_match(blob, [
            r"(?im)^journal\s*:\s*(.+)$",
            r"(?im)^submitted\s+to\s*:\s*(.+)$",
        ])
        title = _first_match(blob, [
            r"(?im)^title\s*:\s*(.+)$",
            r"(?im)^manuscript\s+title\s*:\s*(.+)$",
        ])
        status_text = _first_match(blob, [
            r"(?im)^status\s*:\s*(.+)$",
            r"(?im)^decision\s*:\s*(.+)$",
        ])
        status = _status_from_text(status_text or blob)
        note = _compact_note(subject, status_text, blob)
        return ParsedSubmissionEmail(
            manuscript_id=manuscript_id.strip(),
            journal_name=journal_name.strip(),
            title=title.strip(),
            status=status,
            note=note,
        )


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip()
    return ""


def _status_from_text(text: str) -> Optional[SubmissionStatus]:
    t = re.sub(r"\s+", " ", text.lower())
    checks: list[tuple[SubmissionStatus, list[str]]] = [
        ("major_revision", ["major revision", "major revisions", "revise and resubmit"]),
        ("minor_revision", ["minor revision", "minor revisions"]),
        ("under_review", ["under review", "reviewers assigned", "reviewer invited", "in review"]),
        ("with_editor", ["with editor", "editor assigned", "editorial assessment"]),
        ("accepted", ["accepted", "acceptance", "we are pleased to accept"]),
        ("rejected", ["rejected", "declined", "not suitable", "cannot accept"]),
        ("published", ["published", "available online", "online publication"]),
        ("withdrawn", ["withdrawn", "withdrawal"]),
        ("submitted", ["submitted", "submission received", "received your manuscript"]),
    ]
    for status, needles in checks:
        if any(n in t for n in needles):
            return status
    return None


def _compact_note(subject: str, status_text: str, blob: str) -> str:
    if subject:
        return subject[:180]
    if status_text:
        return status_text[:180]
    return blob[:180]
