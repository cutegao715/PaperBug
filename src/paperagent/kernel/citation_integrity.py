"""Citation Integrity kernel - fully deterministic, no LLM calls.

Errors (block finish):
- malformed {{ref:...}} placeholders
- cited ref_id not present in the Reference DB (fabricated/uncommitted citation)
- evidence quote not found verbatim in its source PDF page

Warnings (advise only):
- reference in DB never cited
- cited reference without any evidence card
- evidence whose source file is unavailable for verification
"""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from paperagent.schemas.artifacts import QualityReport, ValidationReport
from paperagent.schemas.evidence import Evidence
from paperagent.schemas.references import REF_PLACEHOLDER_RE, Reference

# catches '{{ref:' fragments that are not well-formed placeholders
_BARE_RE = re.compile(r"\{\{ref:")


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _quote_in_page(pdf_path, page: int | None, quote: str) -> str | None:
    """Return None if verified; otherwise a human-readable status."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return "source-unavailable"
    if pdf_path.suffix.lower() != ".pdf":
        return "source-not-pdf"
    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return "source-unreadable"
    target = _normalize(quote)
    page_range = [page] if page else range(1, len(reader.pages) + 1)
    for page_no in page_range:
        if not 1 <= page_no <= len(reader.pages):
            continue
        text = _normalize(reader.pages[page_no - 1].extract_text() or "")
        if target and target in text:
            return None
    return "quote-not-found"


def check_citation_integrity(draft_text: str, refs: list[Reference],
                             evidence: list[Evidence],
                             resolve=lambda p: Path(p), strict_external=None) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []

    known_ids = {r.ref_id for r in refs}

    # 1. malformed placeholders
    well_formed_refs = REF_PLACEHOLDER_RE.findall(draft_text)
    well_formed = set(well_formed_refs)
    bare_count = len(_BARE_RE.findall(draft_text))
    if bare_count > len(well_formed_refs):
        errors.append(
            f"malformed ref placeholder(s): {bare_count - len(well_formed_refs)} "
            "'{{ref:...' occurrence(s) do not match {{ref:<id>}}")

    # 2. unknown citation ids
    for ref_id in sorted(well_formed - known_ids):
        errors.append(f"cited ref '{ref_id}' not in Reference DB (fabricated or unsaved)")

    # 3. uncited references
    for r in refs:
        if r.ref_id not in well_formed:
            warnings.append(f"reference '{r.ref_id}' in DB is never cited in the draft")

    # 4. evidence verification (verbatim quote in source)
    from paperagent.kernel.external_source_check import current_result, strict_enabled
    strict = strict_enabled() if strict_external is None else strict_external
    for ref in refs:
        if ref.ref_id not in well_formed:
            continue
        verification = current_result(ref)
        status = verification.get('status')
        if status in {'not_found', 'mismatch'}:
            message = f"external reference '{ref.ref_id}': {status} ({verification.get('provider')}); provider metadata only"
            if strict and ref.doi and status == 'not_found' and verification.get('explicit_doi_not_found'):
                errors.append(message)
            else:
                warnings.append(message)

    # 4. evidence verification (verbatim quote in source)
    evidence_by_ref: dict[str, list[Evidence]] = {}
    for ev in evidence:
        evidence_by_ref.setdefault(ev.ref_id, []).append(ev)
        if not ev.source_path:
            continue
        status = _quote_in_page(resolve(ev.source_path), ev.page, ev.quote)
        if status == "quote-not-found":
            errors.append(f"evidence '{ev.evidence_id}' quote not found in source "
                          f"(ref '{ev.ref_id}', page {ev.page})")
        elif status in ("source-unavailable", "source-unreadable", "source-not-pdf"):
            warnings.append(f"evidence '{ev.evidence_id}': cannot verify quote, "
                            f"{status} ({ev.source_path})")

    # 5. citations without evidence
    for ref_id in sorted(well_formed & known_ids):
        if not evidence_by_ref.get(ref_id):
            warnings.append(f"cited ref '{ref_id}' has no evidence card (weak grounding)")

    return ValidationReport(
        kernel="citation_integrity",
        errors=errors,
        warnings=warnings,
        stats={
            "citations": len(well_formed),
            "references_in_db": len(refs),
            "evidence_cards": len(evidence),
        },
    )


def build_quality_report(draft_text: str, refs: list[Reference],
                         evidence: list[Evidence],
                         resolve=lambda p: Path(p)) -> QualityReport:
    return QualityReport(
        citation_integrity=check_citation_integrity(draft_text, refs, evidence, resolve)
    )
