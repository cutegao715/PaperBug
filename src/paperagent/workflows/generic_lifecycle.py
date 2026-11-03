from __future__ import annotations

from paperagent.workflows.base import WorkflowMatch
from paperagent.workflows.lifecycle import detect_lifecycle
from paperagent.workflows.catalog import CATALOG, workflow_id
from paperagent.workspace.workspace import Workspace


LIFECYCLE_STAGE_HINTS = {
    "paper_conference": {
        "topic": ["research_question", "scope", "target_venue_fit"],
        "literature_review": ["source_map", "research_gap", "citation_integrity"],
        "drafting": ["outline", "section_drafts", "figures_tables"],
        "submission": ["venue_requirements", "format_check", "cover_letter"],
        "revision": ["reviewer_comments", "response_letter", "tracked_changes"],
    },
    "thesis": {
        "topic": ["topic_options", "advisor_constraints", "feasibility"],
        "proposal": ["proposal_template", "research_design", "timeline"],
        "midterm": ["proposal_alignment", "progress_status", "problems_next_plan"],
        "drafting": ["chapter_plan", "chapter_drafts", "citation_style"],
        "defense": ["slides", "script", "q_and_a"],
        "final": ["format_check", "plagiarism_risk", "archive_package"],
    },
}


class GenericLifecycleWorkflow:
    workflow_id = "lifecycle"
    name = "generic_lifecycle_workflow"

    def match(self, ws: Workspace, goal: str, lifecycle: dict | None = None) -> WorkflowMatch | None:
        lifecycle = lifecycle or detect_lifecycle(ws, goal)
        if not lifecycle:
            return None
        lifecycle_type = lifecycle.get("lifecycle_type")
        stage = lifecycle.get("stage") or "drafting"
        required = LIFECYCLE_STAGE_HINTS.get(lifecycle_type, {}).get(stage) or CATALOG[lifecycle_type]["stages"][stage][1]
        return WorkflowMatch(
            workflow_id=workflow_id(lifecycle_type, stage),
            name=f"{lifecycle_type}_{stage}_workflow",
            confidence=float(lifecycle.get("confidence") or 0.58),
            reason=f"Matched {lifecycle_type} lifecycle at {stage} stage.",
            lifecycle=lifecycle,
            state={
                "lifecycle_type": lifecycle_type,
                "stage": stage,
                "drafts": sorted(p.name for p in ws.drafts_dir.glob("*.md"))[-12:]
                if ws.drafts_dir.exists() else [],
            },
            next_actions=[
                "Recover project_memory and imported templates/materials.",
                "Identify the current lifecycle stage and required deliverable.",
                "Draft or review section by section using the stage contract.",
            ],
            quality_gates=[
                "citation_integrity",
                "template_or_format_compliance",
                "stage_deliverable_completeness",
                "placeholder_check",
            ],
            drafting_contract={
                "lifecycle_type": lifecycle_type,
                "stage": stage,
                "required_deliverable_parts": required,
                "write_section_by_section": True,
            },
            playbook={
                "principle": "Lifecycle coordinates long-lived context; workflow handles the current stage.",
                "lifecycle_type": lifecycle_type,
                "stage": stage,
            },
            deliverable_contracts={stage: required},
            interaction_policy={
                "principle": "Do useful work first; ask only for facts required before final delivery.",
            },
        )
