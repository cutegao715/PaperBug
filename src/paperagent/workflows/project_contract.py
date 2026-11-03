"""Per-task project delivery contract: suggestions by default, explicit overrides."""
from __future__ import annotations

from typing import Literal
from datetime import date
from pydantic import BaseModel, ConfigDict, Field
from paperagent.domain.project_dossier import ProjectDossier


class ProjectWorkflowOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["partial", "full"] | None = None
    delivery: Literal["draft", "ready"] | None = None
    sections: list[str] | None = Field(default=None, max_length=100)
    claim_policy: Literal["advisory", "traceable", "reviewed"] | None = None
    report_date: date | None = None
    case_id: str | None = Field(default=None, pattern=r"^wf_[a-f0-9]{16}$")


SUGGESTED_SECTIONS = {
    "application": ["项目基本信息", "立项依据", "研究目标", "研究内容与方法", "实施计划", "预期成果", "研究基础与条件", "经费预算"],
    "midterm": ["项目基本信息", "原定目标与计划", "已完成工作", "阶段成果与证据", "问题与调整", "后续计划", "经费使用情况"],
    "final": ["项目基本信息", "目标完成情况", "主要研究工作", "成果与证据", "创新与应用价值", "问题与反思", "经费决算与附件"],
}


def attach_project_contract(ws, result, goal, options=None):
    stage = {"grant_application": "application", "project_midterm_report": "midterm",
             "project_final_report": "final"}.get(result["workflow_id"])
    if not stage:
        return result
    options = ProjectWorkflowOptions.model_validate(options or {}).model_dump(mode="json", exclude_none=True)
    partial = result["execution_policy"]["scope"] == "partial" or (
        not any(term in goal for term in ("全文", "完整", "整篇", "整份")) and any(
            term in goal.lower() for term in ("摘要", "大纲", "提纲", "outline", "abstract", "某一节", "这一节", "部分", "单节")))
    if stage == "application" and result["state"].get("stage") in {"topic_selection", "title_optimization", "outline", "section_seed"}:
        partial = True
    scope = options.get("scope", "partial" if partial else "full")
    delivery = options.get("delivery", "ready" if any(term in goal for term in
                          ("正式交付", "提交版", "定稿交付", "可提交版本")) else "draft")
    dossier = ProjectDossier(ws)
    handoff = dossier.context(stage)
    template = handoff["bindings"].get(f"template_{stage}")
    required = options.get("sections")
    sections_source = "task" if required is not None else "template" if template else "suggested"
    if required is None:
        required = template.get("required_sections", []) if template else []
        if not template and stage == "application":
            key = "argument_sheet_sections" if result["state"].get("document_mode") == "argument_sheet" else "application_sections"
            required = result["state"].get("material_required_sections", {}).get(key) or []
            if required:
                sections_source = "imported_application_template"
    result["project_contract"] = {
        "stage": stage, "scope": scope, "delivery": delivery,
        "claim_policy": options.get("claim_policy", "traceable" if delivery == "ready" else "advisory"),
        "report_date": options.get("report_date"),
        "required_sections": required, "sections_source": sections_source,
        "suggested_sections": SUGGESTED_SECTIONS[stage],
        "template_binding_id": template["binding_id"] if template else None,
        "rule": "Suggested sections are optional. Check only the task scope; placeholders allowed in draft delivery. Steps may be reordered or skipped.",
    }
    result["execution_policy"]["scope"] = scope
    result["project_handoff"] = handoff
    result["state"]["source_application_draft"] = (handoff["bindings"].get("baseline") or {}).get("filename")
    result["state"]["template_sections"] = required
    if stage == "application" and required:
        key = "required_argument_sheet_sections" if result["state"].get("document_mode") == "argument_sheet" else "required_application_sections"
        result["drafting_contract"][key] = required
    result["drafting_contract"]["full_document_required"] = scope == "full"
    result["drafting_contract"]["do_not_finish_with_outline_only"] = scope == "full" and delivery == "ready"
    result["drafting_contract"]["contract_priority"] = "project_contract controls this task's scope and delivery; default lengths and sections are advisory"
    result["drafting_contract"]["source_of_truth"] = "project_dossier versioned records + bound source snapshots; project_memory is a derived view"
    rows = dossier.active_records()
    missing = []
    if stage != "application" and "baseline" not in handoff["bindings"]:
        missing.append("尚未绑定申请/任务书基线；可先起草，不能把最近草稿视为已批准依据")
    if not any(r["kind"] == "objective" and r["status"] == "confirmed" for r in rows):
        missing.append("待确认项目目标与预期成果指标")
    if stage != "application" and not any(r["stage"] == stage and r["kind"] in {"progress", "output"} for r in rows):
        missing.append("待补本阶段实际进展、成果状态及其来源")
    if any(r["status"] == "proposed" for r in rows):
        missing.append("项目记录中有 Agent 提取的候选信息，尚待用户采纳")
    # Grant-specific missing information still applies to complete applications.
    prior = result.get("missing_information", []) if stage == "application" else []
    result["missing_information"] = [] if scope == "partial" else list(dict.fromkeys(prior + missing))
    result["next_actions"] = [
        "按当前请求选择起草、补证据、局部修订、审查或交付；不要求逐项执行。",
        "读取 project_handoff 中绑定的源版本及目标、决定、问题；不从最近草稿猜测基线。",
        "用 record_project_context 保存提取的候选记录，关联目标 ID 与来源；由用户采纳或更正。",
    ] + result["next_actions"]
    return result
