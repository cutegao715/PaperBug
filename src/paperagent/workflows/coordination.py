"""Request-scoped coordination for writing, discovery and long-running management.

These configurations reuse the existing runtime and knowledge. They do not start
background monitors, create tasks or impose a sequence of deliverables.
"""
from paperagent.workflows.base import WorkflowMatch


SUGGESTED_OUTPUTS = {
    "paper_writing": ["研究定位", "提纲或所需章节", "稿件修订", "待补证据与适用检查"],
    "publication_management": ["目标候选与依据", "投稿状态说明", "意见分析", "修订或回复材料"],
    "project_opportunity_discovery": ["机会及原始来源", "资格与方向匹配", "未知条件", "当前截止日期"],
    "thesis_writing": ["开题或章节材料", "导师意见处理", "论文修订", "适用的送审准备"],
    "thesis_defense": ["汇报内容与结构", "讲稿文本", "有依据的问答", "实际意见处理"],
}


def build_coordination_workflow(spec, lifecycle, options=None):
    from paperagent.workflows.project_contract import ProjectWorkflowOptions
    options = ProjectWorkflowOptions.model_validate(options or {}).model_dump(mode="json", exclude_none=True)
    identifier = spec["id"]
    writing = spec["mode"] in {"writing", "defense"}
    outputs = spec.get("deliverables", SUGGESTED_OUTPUTS.get(identifier, []))
    return WorkflowMatch(
        workflow_id=identifier, name=spec["name"], confidence=1.0,
        reason="Selected a business coordination context; the real-world lifecycle is unchanged.",
        lifecycle=lifecycle,
        state={"lifecycle_type": lifecycle["lifecycle_type"], "focus_stage": lifecycle["stage"],
               "coordination_mode": spec["mode"]},
        next_actions=[
            "从当前请求、相关资料、作者决定和已有记录中选择有用的行动；以下产物不构成必做清单。",
            "按需读取推荐 Skill、内部资源、稿件与来源；不要求全部调用或按顺序调用。",
            "恢复时核对当前版本与先前结果，不重放已完成操作；未知信息如实保留。",
            "只报告实际完成的操作；未来连接、主动监测、策略和文件生成能力不能由流程入口存在来推断。",
        ],
        quality_gates=[],
        drafting_contract={
            "full_document_required": options.get("scope") == "full" if writing else False,
            "do_not_finish_with_outline_only": False,
            "write_section_by_section": False,
            "required_deliverable_parts": options.get("sections", []),
            "suggested_deliverables": outputs,
            "scope": options.get("scope", "request_defined"),
            "delivery": options.get("delivery", "request_defined"),
            "rule": "Only the current explicit request defines required outputs. Suggested outputs are optional, not gates.",
        },
        playbook={
            "principle": "The request, evidence, observed events and author decisions determine next actions, not a fixed stage sequence.",
            "context_sources": [
                {"action":"read_context", "kind":"conversation"},
                {"action":"read_context", "kind":"materials"},
                *([{"action":"read_context", "kind":"manuscript"},
                   {"action":"read_context", "kind":"paper_reviews"}] if lifecycle["lifecycle_type"] != "project" else [
                   {"action":"read_context", "kind":"project_dossier"}]),
            ],
            "management": "Use only supplied or recorded statuses and review evidence; selection does not connect accounts or start a watcher.",
        },
        deliverable_contracts={"requested": options.get("sections", []), "suggested": outputs},
        interaction_policy={"principle":"Start useful local work with available information; ask only when an unresolved fact materially changes the action."},
        missing_information=[],
    )
