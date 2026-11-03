"""AI Evaluator contracts.

Evaluators produce advisory judgments. They do not block execution; hard
blocking remains the job of deterministic validators / kernels.
"""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    evaluator: str
    score: float = Field(ge=0.0, le=1.0)
    should_notify: bool = False
    rationale: str = ""
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RadarRelevanceEvaluator(Protocol):
    def evaluate(self, *, topic: str, candidate: dict[str, Any],
                 match_score: float) -> EvaluationResult:
        ...


class DeterministicRadarEvaluator:
    """Fallback evaluator for offline/dev runs.

    It mirrors the V2 shape of an AI evaluator result without making a
    model call. This keeps the pipeline contract stable and testable.
    """

    def __init__(self, notify_threshold: float = 0.34):
        self.notify_threshold = notify_threshold

    def evaluate(self, *, topic: str, candidate: dict[str, Any],
                 match_score: float) -> EvaluationResult:
        title = candidate.get("title") or ""
        should_notify = match_score >= self.notify_threshold
        rationale = (
            f"Deterministic fallback: title '{title[:80]}' scored "
            f"{match_score:.2f} against topic '{topic}'."
        )
        warnings = []
        if match_score == 0:
            warnings.append("No significant topic words matched the title.")
        return EvaluationResult(
            evaluator="deterministic_radar_v1",
            score=match_score,
            should_notify=should_notify,
            rationale=rationale,
            warnings=warnings,
            metadata={"threshold": self.notify_threshold},
        )


class EvidenceEvaluationReport(BaseModel):
    evaluator: str = "deterministic_evidence_v1"
    evidence_count: int = 0
    reference_count: int = 0
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    unsupported_references: list[str] = Field(default_factory=list)


class EvidenceEvaluator:
    """Advisory evidence quality evaluator.

    Deterministic in V2-m1: it checks whether saved references have evidence
    cards and whether evidence cards declare the claim they support.
    """

    def evaluate(self, *, references, evidence) -> EvidenceEvaluationReport:
        ref_ids = {r.ref_id for r in references}
        evidence_refs = {e.ref_id for e in evidence}
        unsupported = sorted(ref_ids - evidence_refs)
        warnings = []
        if not references:
            warnings.append("No saved references.")
        if not evidence:
            warnings.append("No evidence cards.")
        missing_claims = [e.evidence_id for e in evidence if not e.supports]
        if missing_claims:
            warnings.append(
                "Evidence cards without supports claim: " + ", ".join(missing_claims[:5]))
        coverage = 1.0 if not ref_ids else round(len(ref_ids & evidence_refs) / len(ref_ids), 3)
        return EvidenceEvaluationReport(
            evidence_count=len(evidence),
            reference_count=len(references),
            coverage_score=coverage,
            warnings=warnings,
            unsupported_references=unsupported,
        )
