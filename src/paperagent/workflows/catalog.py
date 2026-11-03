"""Advisory stage/workflow catalog; list order never constrains execution."""
from __future__ import annotations

CATALOG = {
    "project": {
        "name": "科研项目",
        "stages": {
            "topic": ("选题准备", ["指南要求", "候选题与依据"]),
            "application": ("项目申请", ["申请书或论证活页", "待补事实"]),
            "kickoff": ("立项启动", ["获批目标基线", "实施计划"]),
            "execution": ("研究实施", ["实际进展", "成果证据", "变更记录"]),
            "midterm": ("中期检查", ["计划与进展对照", "阶段成果", "后续计划"]),
            "final": ("结题验收", ["目标完成情况", "成果证据", "结题材料"]),
            "archive": ("归档延伸", ["归档清单", "关联成果"]),
        },
    },
    "paper_conference": {
        "name": "期刊与会议论文",
        "stages": {
            "topic": ("研究定位", ["研究问题", "贡献与目标匹配"]),
            "literature_review": ("文献与设计", ["文献矩阵", "研究缺口", "研究方案"]),
            "research": ("研究实施", ["方法记录", "结果证据"]),
            "drafting": ("论文写作", ["章节稿件", "图表", "引用"]),
            "submission": ("投稿准备与跟踪", ["目标要求", "投稿材料", "投稿轮次"]),
            "revision": ("返修与改投", ["意见处理表", "修订稿", "回复信"]),
            "production": ("录用后交付", ["终稿或校样", "会议展示材料（适用时）"]),
            "archive": ("发表归档", ["发表信息", "最终版本"]),
        },
    },
    "thesis": {
        "name": "毕业与学位论文",
        "stages": {
            "topic": ("选题规划", ["选题方案", "导师约束", "时间表"]),
            "proposal": ("开题", ["开题报告", "研究设计", "开题意见"]),
            "research": ("研究实施", ["方法记录", "数据与结果"]),
            "midterm": ("中期检查", ["开题对照", "实际进展", "后续计划"]),
            "drafting": ("论文撰写", ["章节稿件", "图表与附录"]),
            "advisor_review": ("导师审阅", ["导师意见", "修订稿", "处理记录"]),
            "blind_review": ("送审与盲审", ["学校要求", "匿名稿", "评审意见处理"]),
            "defense": ("答辩", ["汇报材料", "讲稿与问答", "答辩后修改"]),
            "final": ("定稿归档", ["格式检查", "最终版本", "归档清单"]),
        },
    },
}


# Routing is declarative; the skill catalog is deployed alongside workflow code.
from importlib.resources import files
import yaml
from paperagent.workflows.design import WORKFLOWS, CODE_IDS, CATALOG_KEYS, ALIASES, PROACTIVE_SERVICES

WORKFLOW_CATALOG = WORKFLOWS
SKILL_CATALOG = yaml.safe_load(files("paperagent.skills").joinpath("skill_catalog.yaml").read_text(encoding="utf-8-sig"))

METHOD_CONDITIONS = {
    "requirements_interpretation": "需要从当前指南、通知或模板提取硬约束并保留来源时",
    "grant_application": "需要项目选题、申报论证、分栏正文或活页时",
    "research_question_formulation": "已有题目需要深入论证研究问题、缺口与可验证性，或需要发现问题时",
    "grant_review": "需要独立模拟申请评审，核对价值、创新、基础、可行性与指南契合度时",
    "grant_midterm_report": "科研项目需要对照任务书组织真实中期进展及后续计划时",
    "grant_closeout_report": "科研项目需要建立承诺—实际成果—证明材料闭环并撰写结题报告时",
    "project_evidence_review": "需要核对项目目标、实际进展、成果承诺与证据对应关系时",
    "research_design": "需要确定理论框架、研究方案、数据需求或贡献与验证关系时",
    "methodology": "需要比较研究方法或说明实际采用的方法及其适用边界时",
    "experiment_design": "本研究确实包含实验或实证验证，需完善变量、基线、控制与测量方案时",
    "literature_review": "需要检索文献、梳理研究脉络或形成综述时",
    "evidence_synthesis": "需要围绕主题比较证据、解释分歧或归纳研究缺口时",
    "citation_verification": "需要核对引文身份、元数据、原文支持或发表状态时",
    "academic_writing": "需要依据真实证据撰写或修订中文／英文正文时；英文场景按需读英文资源",
    "paper_review": "需要独立评审论文或复核具体问题时；不因评审请求自动修改正文",
    "paper_revision": "作者要求实际修订稿件，或处理导师与评审反馈时",
    "abstract_title": "需要拟定或改写标题、摘要、关键词或 Highlights 时",
    "reproducibility_reporting": "需要核对数据、代码、流程、报告透明性及可复现性披露时",
    "scientific_figure": "需要制作科研图表或审查已有图表的证据、标注与表达时",
    "scientific_presentation": "需要基于已有研究组织演示、讲稿或答辩表达时",
    "journal_strategy": "需要按当前刊会权威信息筛选目标、排序候选或制定改投策略时",
    "submission_preparation": "已确定目标，需要适配期刊／会议材料、格式、Cover Letter 和检查清单时",
    "submission_tracking": "需要整理有来源的投稿状态、编辑决定、期限或轮次记录时",
    "reviewer_response": "已收到实际审稿意见，需要制定回应策略或逐条回复时",
    "thesis_planning": "需要结合学历层次、学校和导师要求规划学位论文结构与进度时",
    "thesis_midterm_report": "毕业论文需要对照开题计划组织真实进度、偏差和后续任务时",
    "thesis_blind_review_defense": "需要核对盲审匿名与材料、处理真实意见、准备答辩或归档时",
    "research_radar": "需要围绕研究方向发现、去重和推荐文献或机会时；常态关注由主动服务承载",
}


def canonical_workflow_id(identifier: str) -> str:
    identifier = ALIASES.get(identifier, identifier)
    if identifier.startswith("paper_conference_"):
        stage = identifier.removeprefix("paper_conference_")
        if stage in CATALOG["paper_conference"]["stages"]:
            return "submission_tracking" if stage in {"revision", "production", "archive"} else "venue_matching" if stage == "submission" else "paper_writing"
    if identifier.startswith("thesis_") and identifier.removeprefix("thesis_") in CATALOG["thesis"]["stages"]:
        stage = identifier.removeprefix("thesis_")
        return "thesis_proposal" if stage in {"topic", "proposal"} else "thesis_midterm_report" if stage == "midterm" else "thesis_defense" if stage in {"blind_review", "defense"} else "thesis_writing"
    return identifier


def workflow_spec(identifier: str) -> dict | None:
    identifier = canonical_workflow_id(identifier)
    if identifier == "project_opportunity_discovery" or identifier == "project_topic":
        # Existing saved discovery requests remain resumable; discovery lives in A2.
        return {"id": identifier, "name": "项目机会寻找（兼容）", "lifecycle_types": ["project"],
                "default_stage": "topic", "suggested_stages": ["topic", "application"],
                "mode": "discovery", "implementation": "coordination", "description": "A2 机会发现", "legacy": True}
    return next((w for w in WORKFLOW_CATALOG if w["id"] == identifier), None)


def workflow_id(kind: str, stage: str) -> str:
    # Retain stage IDs for saved checkpoints and specialized legacy workflows.
    return {("project", "application"): "grant_application",
            ("project", "midterm"): "project_midterm_report",
            ("project", "final"): "project_final_report"}.get((kind, stage), f"{kind}_{stage}")


def workflow_location(identifier: str) -> tuple[str, str]:
    spec = workflow_spec(identifier)
    if spec:
        return spec["lifecycle_types"][0], spec["default_stage"]
    for kind, item in CATALOG.items():
        for stage in item["stages"]:
            if workflow_id(kind, stage) == identifier:
                return kind, stage
    raise ValueError(f"Unknown workflow: {identifier}")


def related_workflows(kind: str, stage: str) -> list[str]:
    return [w["id"] for w in WORKFLOW_CATALOG if kind in w["lifecycle_types"] and stage in w["suggested_stages"]]


def workflow_skill_recommendations(identifier: str) -> list[dict]:
    spec = workflow_spec(identifier)
    if not spec:
        return []
    if spec.get("legacy"):
        route = {"core": SKILL_CATALOG["proactive_services"]["opportunity_radar"], "support": []}
    else:
        route = SKILL_CATALOG["workflows"][CATALOG_KEYS[spec["code"]]]
    return [{"skill_id": skill, "version": "1.0", "role": role,
             "when": METHOD_CONDITIONS.get(skill, "本次请求需要该方法且材料适用时；可跳过或独立使用"),
             "workflow_id": identifier, "optional": True}
            for role in ("core", "support") for skill in route.get(role, [])]


def describe_lifecycles() -> list[dict]:
    from paperagent.workflows.design import contract
    return [{"lifecycle_type": kind, "name": spec["name"],
             "stages": [{"id": stage, "name": label, "workflow_id": workflow_id(kind, stage),
                         "suggested_deliverables": parts, "suggested_workflows": related_workflows(kind, stage)}
                        for stage, (label, parts) in spec["stages"].items()],
             "workflows": [{**contract(w), "skill_recommendations": workflow_skill_recommendations(w["id"])}
                           for w in WORKFLOW_CATALOG if kind in w["lifecycle_types"]],
             "ordered": False} for kind, spec in CATALOG.items()]


# Compatibility export, generated from the one deployed routing catalog.
WORKFLOW_SKILLS = {w["id"]: workflow_skill_recommendations(w["id"]) for w in WORKFLOW_CATALOG}
