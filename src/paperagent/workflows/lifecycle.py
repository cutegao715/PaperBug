"""Resolve task context without mutating the persisted real-world stage."""
from __future__ import annotations

from paperagent.workflows.catalog import CATALOG, workflow_location, workflow_spec
from paperagent.workspace.workspace import Workspace

LIFECYCLE_TYPES = list(CATALOG)
STAGE_TERMS = {
    "project": [
        ("midterm", ("中期", "进展报告", "阶段报告", "midterm", "progress report")),
        ("final", ("结题", "结项", "验收", "final report", "completion report")),
        ("archive", ("归档", "archive")),
        ("application", ("申请书", "申报", "活页", "application", "proposal")),
        ("kickoff", ("启动", "获批", "立项", "kickoff")),
        ("execution", ("进展记录", "成果登记", "计划调整", "研究实施")),
        ("topic", ("选题", "题目优化")),
    ],
    "paper_conference": [
        ("revision", ("返修", "审稿意见", "改投", "revision", "reviewer")),
        ("production", ("录用", "校样", "海报", "camera-ready", "proof")),
        ("archive", ("归档", "发表登记", "archive")),
        ("submission", ("投稿", "submission", "提交")),
        ("literature_review", ("文献综述", "literature review")),
        ("research", ("数据分析", "结果整理", "研究实施")),
        ("topic", ("选题", "研究设计", "research question")),
        ("drafting", ("写作", "撰写", "初稿", "drafting")),
    ],
    "thesis": [
        ("blind_review", ("盲审", "送审", "评审意见", "blind review")),
        ("defense", ("答辩", "defense")),
        ("advisor_review", ("导师意见", "导师修改", "导师反馈", "advisor feedback")),
        ("final", ("定稿", "归档", "archive")),
        ("midterm", ("中期", "midterm")),
        ("proposal", ("开题", "proposal")),
        ("research", ("数据分析", "研究实施", "结果整理")),
        ("topic", ("选题",)),
        ("drafting", ("撰写", "写作", "初稿", "drafting")),
    ],
}


def _kind(text: str) -> str | None:
    text = text.lower()
    for kind, terms in [
        ("thesis", ("毕业论文", "学位论文", "开题", "答辩", "盲审", "thesis", "dissertation")),
        ("project", ("课题", "基金", "申报", "申请书", "活页", "结题", "grant")),
        ("paper_conference", ("论文", "期刊", "会议", "投稿", "返修", "paper", "journal", "conference")),
        ("project", ("项目", "中期", "结项", "验收", "proposal")),
    ]:
        if any(term in text for term in terms):
            return kind
    return None


def _stage(kind: str, text: str) -> str | None:
    return next((stage for stage, terms in STAGE_TERMS[kind]
                 if any(term in text.lower() for term in terms)), None)


def detect_lifecycle(ws: Workspace, goal: str, preferred_workflow: str | None = None) -> dict | None:
    from paperagent.domain.lifecycle import LifecycleService
    saved = LifecycleService(ws).get()
    if preferred_workflow:
        kind, stage = workflow_location(preferred_workflow)
        if saved and saved.lifecycle_type != kind:
            raise ValueError("Workflow belongs to a different lifecycle")
        spec = workflow_spec(preferred_workflow)
        if spec and spec.get("implementation") == "coordination":
            focus = _stage(kind, goal)
            if focus in spec["suggested_stages"]:
                stage = focus
        source = "explicit_workflow"
    else:
        kind = saved.lifecycle_type if saved else (_kind(ws.goal) or _kind(goal))
        if not kind:
            kind = _kind(" ".join(m.get("filename", "") for m in ws.list_materials()))
        if not kind:
            return None
        stage = _stage(kind, goal) or (saved.stage if saved else _stage(kind, ws.goal))
        stage = stage or ("application" if kind == "project" else "drafting")
        source = "persisted" if saved else "inferred"
    return {"lifecycle_type": kind, "stage": stage,
            "current_stage": saved.stage if saved else None,
            "focus_stages": saved.focus_stages if saved else [],
            "confidence": 1.0 if saved or preferred_workflow else 0.8,
            "source": source,
            "reason": "Task stage is advisory; real-world lifecycle changes are explicit."}
