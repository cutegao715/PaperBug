"""Completeness checks for structured grant application documents."""
from __future__ import annotations

import re
from pathlib import Path

from paperagent.schemas.artifacts import ValidationReport


APPLICATION_SECTIONS = [
    "课题基本信息", "选题依据", "研究目标", "研究内容", "研究方法",
    "实施步骤", "创新", "预期成果", "研究基础", "保障条件",
]

ARGUMENT_SHEET_SECTIONS = [
    "选题依据", "研究目标", "研究内容", "研究思路", "研究方法",
    "实施步骤", "创新", "预期成果",
]

PLACEHOLDER_RE = re.compile(r"【[^】]{0,30}】|\[[^\]]{0,30}待填[^\]]{0,30}\]|待补充|请据实")
LOW_QUALITY_TOPIC_RE = re.compile(
    r"人工智能赋能教育研究|AI赋能教育研究|教育数字化研究|高质量发展研究"
)
EXAGGERATED_INNOVATION_RE = re.compile(r"首次研究|填补国内空白|国内外尚无研究|国际领先")


def check_application_completeness(
    draft_text: str,
    filename: str = "",
    workflow: dict | None = None,
) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    stats = {
        "chars": len(draft_text.strip()),
        "placeholders": len(PLACEHOLDER_RE.findall(draft_text)),
    }
    doc_type = _doc_type(filename, draft_text, workflow)
    if doc_type is None:
        return ValidationReport(
            kernel="application_completeness",
            warnings=["document is not recognized as a grant application artifact"],
            stats=stats,
        )

    min_chars = 9000 if doc_type == "application" else 6500
    required = _required_sections(doc_type, workflow)
    project_contract = (workflow or {}).get("project_contract")
    if project_contract:
        required = project_contract["required_sections"] if (
            project_contract["scope"] == "full" or project_contract["sections_source"] == "task") else []
    missing = [s for s in required if s not in draft_text]
    stats["missing_sections"] = len(missing)
    stats["required_section_count"] = len(required)

    if stats["chars"] < min_chars:
        warnings.append(
            f"{doc_type} draft is too short ({stats['chars']} chars; expected at least {min_chars})"
        )
    if missing:
        target = warnings if project_contract and project_contract["delivery"] == "draft" else errors
        target.append(f"missing required section(s): {', '.join(missing[:8])}")
    if required and required not in (APPLICATION_SECTIONS, ARGUMENT_SHEET_SECTIONS):
        warnings.append("using material-derived required sections: " + ", ".join(required))
    if stats["placeholders"] >= 6:
        warnings.append(
            f"too many unresolved placeholders ({stats['placeholders']}); ask the user for missing facts or mark a clear待补信息清单"
        )
    elif stats["placeholders"]:
        warnings.append(f"contains unresolved placeholders: {stats['placeholders']}")

    title = _extract_title(draft_text)
    if title:
        stats["title_chars"] = len(title)
        if len(title) > 40:
            warnings.append(f"课题名称超过40个汉字/字符：{len(title)}")
        if LOW_QUALITY_TOPIC_RE.search(title):
            warnings.append("SUGGESTION: 课题名称可能过大或过泛，建议限定对象、场景和实践问题")
    if EXAGGERATED_INNOVATION_RE.search(draft_text):
        warnings.append("SUGGESTION: 创新性表述可能过度，避免无证据使用“首次研究/填补空白”等说法")
    if _has_method_list_without_purpose(draft_text):
        warnings.append("SUGGESTION: 研究方法可能只是罗列名称，建议说明每种方法解决什么问题")
    if re.search(r"提升[^。；\n]{0,12}能力|促进[^。；\n]{0,12}发展", draft_text) and "预期成果" in draft_text:
        warnings.append("WARNING: 注意区分成果和成效；“提升能力/促进发展”不是可交付成果")
    if doc_type == "argument_sheet" and re.search(r"申请人|责任单位|姓名|职称|手机号|电话|邮箱", draft_text):
        warnings.append("argument sheet may contain identity fields; check anonymous-review requirements")

    return ValidationReport(
        kernel="application_completeness",
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


def should_check_application(filename: str, draft_text: str,
                             workflow: dict | None = None) -> bool:
    return _doc_type(filename, draft_text, workflow) is not None


def _doc_type(filename: str, draft_text: str, workflow: dict | None) -> str | None:
    name = Path(filename).name
    wf_id = (workflow or {}).get("workflow_id")
    if wf_id in {"project_midterm_report", "project_final_report"}:
        return None  # Reports can quote an application without being applications.
    mode = ((workflow or {}).get("state") or {}).get("document_mode")
    if "论证活页" in name or "活页" in name or mode == "argument_sheet":
        return "argument_sheet"
    if "申请书" in name or "申报书" in name or "申请书" in draft_text[:500]:
        return "application"
    if wf_id == "grant_application" and mode == "application":
        return "application"
    return None


def _required_sections(doc_type: str, workflow: dict | None) -> list[str]:
    contract = (workflow or {}).get("drafting_contract") or {}
    material_required = ((workflow or {}).get("state") or {}).get(
        "material_required_sections"
    ) or {}
    if doc_type == "argument_sheet":
        required = (
            contract.get("required_argument_sheet_sections")
            or material_required.get("argument_sheet_sections")
        )
        return list(required or ARGUMENT_SHEET_SECTIONS)
    required = (
        contract.get("required_application_sections")
        or material_required.get("application_sections")
    )
    return list(required or APPLICATION_SECTIONS)


def _extract_title(text: str) -> str:
    for pattern in [
        r"课题名称[:：]\s*([^\n|]+)",
        r"\*\*课题名称[:：]\s*([^*]+)\*\*",
    ]:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(" ：:|")
    return ""


def _has_method_list_without_purpose(text: str) -> bool:
    method_names = [
        "文献研究法", "问卷调查法", "访谈法", "案例研究法",
        "行动研究法", "实验法", "准实验", "数据分析",
    ]
    count = sum(1 for name in method_names if name in text)
    if count < 4:
        return False
    method_section = ""
    match = re.search(r"研究方法(.{0,500})", text, flags=re.S)
    if match:
        method_section = match.group(1)
    return not re.search(r"用于|以便|解决|验证|分析|识别|解释", method_section)
