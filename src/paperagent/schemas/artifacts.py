"""Tool results, validation reports, and the artifact quality report.

Kernel output is split the way the architecture review demanded:
errors are deterministic blockers; warnings are advisory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    call_id: Optional[str] = None
    tool_id: str
    input: dict[str, Any]
    parent_run_id: Optional[str] = None


class ToolResult(BaseModel):
    tool_id: str
    status: Literal["ok", "error"] = "ok"
    data: Any = None
    errors: list[str] = Field(default_factory=list)
    latency_ms: Optional[int] = None
    provenance: str = ""  # e.g. "https://api.semanticscholar.org/..." or "local:pdf"


class FormulaArtifact(BaseModel):
    artifact_type: Literal["formula"] = "formula"
    formula_id: str
    latex: str
    display_name: str = ""
    symbols: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    source_or_derivation: str = ""
    used_in_sections: list[str] = Field(default_factory=list)
    provenance: str = "manual"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


class FigureArtifact(BaseModel):
    artifact_type: Literal["figure"] = "figure"
    figure_id: str
    kind: str
    title: str = ""
    format: Literal["svg", "mermaid", "text"] = "svg"
    spec: str = ""
    image_path: Optional[str] = None
    caption: str = ""
    source_data: Any = None
    provenance: str = "manual"
    target_requirements: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


class ValidationReport(BaseModel):
    """Deterministic check result. errors block, warnings advise."""

    kernel: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.errors


class QualityReport(BaseModel):
    """Attached to every artifact before it can be called finished."""

    citation_integrity: Optional[ValidationReport] = None
    application_completeness: Optional[ValidationReport] = None
    grant_writing_quality: Optional[ValidationReport] = None
    project_delivery: Optional[ValidationReport] = None
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def passed(self) -> bool:
        reports = [
            r for r in [
                self.citation_integrity,
                self.application_completeness,
                self.grant_writing_quality,
                self.project_delivery,
            ]
            if r is not None
        ]
        return bool(reports) and all(r.passed for r in reports)
