from __future__ import annotations

from paperagent.workflows.base import WorkflowMatch
from paperagent.workflows.generic_lifecycle import GenericLifecycleWorkflow
from paperagent.workflows.grant_application import GrantApplicationWorkflow
from paperagent.workflows.project_reports import ProjectReportWorkflow
from paperagent.workspace.workspace import Workspace
from paperagent.workflows.lifecycle import detect_lifecycle
from paperagent.workflows.catalog import workflow_skill_recommendations, workflow_spec, canonical_workflow_id


WORKFLOWS = [
    ProjectReportWorkflow(),
    GrantApplicationWorkflow(),
    GenericLifecycleWorkflow(),
]


def select_workflow(ws: Workspace, goal: str, preferred_workflow: str | None = None,
                    workflow_options: dict | None = None) -> dict | None:
    requested_id = preferred_workflow
    chosen_case = None
    if (workflow_options or {}).get("case_id"):
        from paperagent.workflows.runtime import WorkflowRuntime
        try:
            chosen_case = WorkflowRuntime(ws).get(workflow_options["case_id"])
        except KeyError:
            raise ValueError("Selected workflow case was not found in this research space")
        if preferred_workflow and canonical_workflow_id(preferred_workflow) != chosen_case["workflow_id"]:
            raise ValueError("Selected case belongs to a different workflow")
        preferred_workflow = chosen_case["workflow_id"]
        workflow_options = {**workflow_options, "scope": chosen_case["scope"]}
    if preferred_workflow:
        preferred_workflow = canonical_workflow_id(preferred_workflow)
    lifecycle = detect_lifecycle(ws, goal, preferred_workflow)
    if not lifecycle:
        return None
    spec = workflow_spec(preferred_workflow) if preferred_workflow else None
    matches: list[WorkflowMatch] = []
    if spec and spec.get("implementation") == "coordination":
        from paperagent.workflows.coordination import build_coordination_workflow
        matches.append(build_coordination_workflow(spec, lifecycle, workflow_options))
    else:
        for workflow in WORKFLOWS:
            match = workflow.match(ws, goal, lifecycle=lifecycle)
            if match is not None and (not preferred_workflow or match.workflow_id == preferred_workflow):
                matches.append(match)
    if not matches:
        return None
    best = matches[0]  # specialized strategy before the generic fallback
    if best.confidence < 0.55:
        return None
    result = best.to_observation()
    canonical = canonical_workflow_id(best.workflow_id)
    result["canonical_workflow_id"] = canonical
    result["legacy_compatibility"] = canonical != best.workflow_id
    descriptor = workflow_spec(canonical)
    result["capability_status"] = {key: descriptor.get(key, []) for key in (
        "available_capabilities", "planned_capabilities", "planned_skill_ids")} if descriptor else {}
    result["description"] = descriptor.get("description", "") if descriptor else "历史阶段配置"
    if descriptor and descriptor.get("code"):
        from paperagent.workflows.design import contract
        design = contract(descriptor)
        result["workflow_contract"] = design
        result["playbook"]["steps"] = design["steps"]
        result["playbook"]["rules"] = design["rules"]
        result["interaction_policy"] = {**result["interaction_policy"], **design["confirmation_policy"]}
        result["quality_review_levels"] = {level: list(dict.fromkeys(
            [*result["quality_review_levels"].get(level, []), *items]))
            for level, items in design["quality_review_levels"].items()}
        result["deliverable_contracts"] = {**result["deliverable_contracts"], "requested": (workflow_options or {}).get("sections", []),
                                           "suggested": design["deliverables"]}
    result["skill_recommendations"] = workflow_skill_recommendations(best.workflow_id)
    partial = any(term in goal.lower() for term in
                  ("只改", "仅修改", "只修改", "修改摘要", "润色摘要", "这一段", "这段", "局部", "only"))
    result["execution_policy"] = {
        "ordered_steps": False, "allow_skip": True, "allow_revisit": True,
        "allow_replan": True, "allow_pause": True, "allow_resume": True, "auto_advance_lifecycle": False,
        "skill_recommendations_required": False, "allow_other_skills": True,
        "scope": (workflow_options or {}).get("scope", "partial" if partial else "task_defined"),
        "principle": "Match checks and deliverables to this request; next_actions are suggestions.",
    }
    if partial and result["execution_policy"]["scope"] != "full":
        result["drafting_contract"]["full_document_required"] = False
        result["drafting_contract"]["do_not_finish_with_outline_only"] = False
        result["missing_information"] = []
    from paperagent.workflows.project_contract import attach_project_contract
    result = attach_project_contract(ws, result, goal, workflow_options)
    if chosen_case:
        result["case_id"] = chosen_case["case_id"]
        result["case_status"] = chosen_case["status"]
        result["deliverable_contracts"]["requested"] = chosen_case["required_deliverables"]
        result["drafting_contract"]["requested_artifacts"] = chosen_case["required_deliverables"]
        if chosen_case.get("submission_id"):
            from paperagent.domain.store import DomainStore
            sub = next((s for s in DomainStore(ws.root).list_submissions()
                        if s.submission_id == chosen_case["submission_id"] and s.project_id == ws.project_id), None)
            if sub:
                result["linked_submission"] = sub.model_dump()
    if requested_id and requested_id != result["workflow_id"]:
        result["requested_workflow_id"] = requested_id
        result["legacy_compatibility"] = True
        if requested_id not in {"G1", "G2", "G3", "P1", "P2", "P3", "T1", "T2", "T3", "T4"} and not requested_id[:3].endswith("_"):
            result["workflow_id"] = requested_id
    return result
