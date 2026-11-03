from __future__ import annotations

from paperagent.workflows.base import WorkflowMatch
from paperagent.workflows.lifecycle import detect_lifecycle
from paperagent.workspace.workspace import Workspace


PROJECT_REPORT_PLAYBOOK = {
    "lifecycle_type": "project",
    "principles": [
        "Use the application/proposal as the project blueprint.",
        "Compare planned objectives, research content, schedule, and expected outputs against actual progress.",
        "Do not invent papers, awards, data, cases, partners, funding use, or application effects.",
        "Use [to_be_confirmed] placeholders for missing factual achievements.",
    ],
    "stage_views": {
        "midterm": "progress tense: planned work -> completed work -> interim outputs -> problems -> next plan",
        "final": "completion tense: original objectives -> completion status -> final outputs -> value/application -> limitations",
    },
}


PROJECT_REPORT_CONTRACTS = {
    "midterm": [
        "basic_project_info",
        "planned_objectives_review",
        "completed_work",
        "interim_outputs",
        "problems_and_adjustments",
        "next_stage_plan",
        "missing_facts",
    ],
    "final": [
        "basic_project_info",
        "objective_completion",
        "main_research_work",
        "final_outputs",
        "innovation_and_value",
        "application_and_dissemination",
        "problems_and_reflection",
        "missing_facts",
    ],
}


PROJECT_REPORT_QUALITY = {
    "ERROR": [
        "Invented achievements, data, publications, awards, partners, or application effects.",
        "Midterm/final report does not correspond to the approved application objectives.",
        "Required official template sections are missing.",
    ],
    "WARNING": [
        "Progress is described without completion status or evidence.",
        "Problems and adjustments are omitted.",
        "Expected outputs and actual outputs are not mapped one by one.",
    ],
    "SUGGESTION": [
        "Language is overly promotional.",
        "The report lacks concrete dates, activities, cases, or deliverables.",
    ],
}


class ProjectReportWorkflow:
    workflow_id = "project_report"
    name = "project_report_workflow"

    def match(self, ws: Workspace, goal: str, lifecycle: dict | None = None) -> WorkflowMatch | None:
        lifecycle = lifecycle or detect_lifecycle(ws, goal)
        if not lifecycle or lifecycle.get("lifecycle_type") != "project":
            return None
        stage = lifecycle.get("stage")
        if stage not in {"midterm", "final"}:
            return None
        from paperagent.domain.project_dossier import ProjectDossier
        bindings = ProjectDossier(ws).bindings()
        workflow_id = "project_midterm_report" if stage == "midterm" else "project_final_report"
        confidence = max(float(lifecycle.get("confidence") or 0.0), 0.78)
        return WorkflowMatch(
            workflow_id=workflow_id,
            name=f"{stage}_report_workflow",
            confidence=confidence,
            reason=f"The task is a project lifecycle {stage} report.",
            lifecycle=lifecycle,
            state={
                "lifecycle_type": "project",
                "stage": stage,
                "source_application_draft": (bindings.get("baseline") or {}).get("filename"),
                "template_sections": (bindings.get(f"template_{stage}") or {}).get("required_sections", []),
                "available_materials": [
                    item.get("filename") for item in ws.list_materials()
                ][-12:],
            },
            next_actions=[
                "Read the explicitly bound baseline snapshot; user adoption alone does not imply external approval.",
                "Ask only for missing factual progress/results that cannot be inferred from materials.",
                "Draft the report section by section and map planned work to actual progress/results.",
            ],
            quality_gates=[
                "citation_integrity",
                "project_lifecycle_consistency",
                "placeholder_check",
                "no_fabricated_outputs",
                "template_section_completeness",
            ],
            drafting_contract={
                "write_section_by_section": True,
                "source_of_truth": "project_memory + imported official templates + user facts",
                "stage": stage,
                "required_sections": PROJECT_REPORT_CONTRACTS[stage],
                "alignment_requirements": [
                    "Objectives, research content, schedule, and outputs must trace back to the application/proposal.",
                    "Completed work must be stated as completed only when supported by user facts or imported materials.",
                    "Missing achievements must remain [to_be_confirmed], not invented.",
                ],
            },
            playbook=PROJECT_REPORT_PLAYBOOK,
            deliverable_contracts={stage: PROJECT_REPORT_CONTRACTS[stage]},
            quality_review_levels=PROJECT_REPORT_QUALITY,
            interaction_policy={
                "principle": "Do useful drafting first, but never fabricate progress or results.",
                "ask_before_final_draft": [
                    "actual_completed_work",
                    "publications_or_reports",
                    "data_or_cases",
                    "dissemination_or_application",
                    "budget_or_management_notes",
                ],
                "missing_but_non_blocking": "Use [to_be_confirmed] placeholders and continue the structure.",
            },
            missing_information=[
                "actual completed work and dates",
                "verified interim/final outputs",
                "problems, adjustments, and supporting evidence",
            ],
        )
