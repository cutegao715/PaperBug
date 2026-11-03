"""Grant-writing quality checks.

This deterministic kernel is intentionally heuristic. It catches common
AI-generated proposal problems so the agent can revise before final delivery.
It has two layers:
- writing_norms: wording, repetition, placeholders, section balance
- academic_quality: evidence, literature review, method design, innovation,
  feasibility, and logical alignment
"""
from __future__ import annotations

import re

from paperagent.schemas.artifacts import ValidationReport


COLLOQUIAL_TERMS = {
    "两张皮": "教学活动与就业指导服务之间缺乏有效衔接机制",
    "大水漫灌": "资源配置缺乏差异化与精准化机制",
    "底数不清": "需求识别缺乏系统性数据支撑",
    "服务好不好": "服务实施效果及其影响因素缺乏系统评价",
    "就业季的突击性工作": "就业指导服务集中于毕业阶段，过程性支持不足",
}

VAGUE_SELF_EVALUATION = [
    "鲜明的时代性与紧迫性",
    "重要意义",
    "显著提升",
    "创新性突出",
    "新的分析框架",
    "具有较强的理论价值",
    "具有重要实践意义",
]

CONCEPT_STACKING_TERMS = [
    "三向匹配",
    "全链条",
    "动态反馈闭环",
    "人机协同",
    "学情画像",
    "职业画像",
    "岗位画像",
    "五位一体",
    "四链",
    "三维",
]

ABSOLUTE_CLAIMS = [
    "现有研究多停留在",
    "多数研究缺少",
    "国内外尚无",
    "首次",
    "填补空白",
]

AUTHORITATIVE_DATA_CLAIMS = [
    "持续处于高位",
    "日益突出",
    "规模持续",
    "结构性矛盾",
    "快速发展",
]

GENERATION_MARKERS_RE = re.compile(r"\{\{ref:[^}]*\}\}|\[待补充[^\]]*\]|\[待核实[^\]]*\]|待补充|待核实")
APPLICATION_MATERIAL_RE = re.compile(
    r"《[^》]{0,40}(?:选题指南|申报指南|申报通知|申请书|申报书|论证活页|承诺书)[^》]{0,40}》"
    r"|(?:20\d{2}年度)?[^。\n]{0,20}(?:选题指南|申报指南|申报通知)"
)
QUOTE_RE = re.compile(r"[“”]")
REF_RE = re.compile(r"\{\{ref:[^}]+?\}\}")
HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$", re.M)


def check_grant_writing_quality(draft_text: str, workflow: dict | None = None) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    stats = {
        "colloquial_terms": 0,
        "quote_marks": len(QUOTE_RE.findall(draft_text)),
        "citation_placeholders": len(REF_RE.findall(draft_text)),
        "generation_markers": len(GENERATION_MARKERS_RE.findall(draft_text)),
        "duplicate_paragraphs": 0,
        "short_sections": 0,
    }
    if not _is_grant_text(draft_text, workflow):
        return ValidationReport(kernel="grant_writing_quality", stats=stats)

    _check_writing_norms(draft_text, errors, warnings, stats)
    _check_academic_quality(draft_text, warnings, stats)

    return ValidationReport(
        kernel="grant_writing_quality",
        errors=errors,
        warnings=warnings,
        stats=stats,
    )


def _is_grant_text(draft_text: str, workflow: dict | None) -> bool:
    if (workflow or {}).get("workflow_id") == "grant_application":
        return True
    head = draft_text[:800]
    return any(key in head for key in ["申请书", "申报书", "课题", "论证活页"])


def _check_writing_norms(text: str, errors: list[str], warnings: list[str],
                         stats: dict[str, int]) -> None:
    found_colloquial = [term for term in COLLOQUIAL_TERMS if term in text]
    stats["colloquial_terms"] = len(found_colloquial)
    for term in found_colloquial[:5]:
        warnings.append(
            f"WRITING_NORM: 口语化/新闻化表达“{term}”，建议改为“{COLLOQUIAL_TERMS[term]}”"
        )

    if stats["quote_marks"] >= 20:
        warnings.append(
            f"WRITING_NORM: 引号使用过多（{stats['quote_marks']}处），仅在首次界定核心概念、引用原文或特定术语时保留"
        )

    if stats["generation_markers"]:
        errors.append(
            f"WRITING_NORM: 存在未清理的生成/占位标记（{stats['generation_markers']}处），正式交付前必须处理"
        )

    duplicate_count = _duplicate_paragraph_count(text)
    stats["duplicate_paragraphs"] = duplicate_count
    if duplicate_count:
        warnings.append(
            f"WRITING_NORM: 检测到疑似重复段落（{duplicate_count}处），建议删除重复版本并明确各章节功能"
        )

    short_sections = _short_section_count(text)
    stats["short_sections"] = short_sections
    if short_sections >= 3:
        warnings.append(
            f"WRITING_NORM: 多个标题下内容过短（{short_sections}处），建议扩展论证或合并小节"
        )

    vague_hits = [phrase for phrase in VAGUE_SELF_EVALUATION if phrase in text]
    if vague_hits:
        warnings.append(
            "WRITING_NORM: 存在空泛自我评价式表述："
            + "、".join(vague_hits[:5])
            + "；建议用事实、数据或研究设计论证价值"
        )

    concept_hits = [term for term in CONCEPT_STACKING_TERMS if term in text]
    if len(concept_hits) >= 4:
        warnings.append(
            "WRITING_NORM: 概念集中堆砌："
            + "、".join(concept_hits[:8])
            + "；建议定义核心概念并说明相互关系"
        )


def _check_academic_quality(text: str, warnings: list[str],
                            stats: dict[str, int]) -> None:
    material_mentions = APPLICATION_MATERIAL_RE.findall(text)
    stats["application_material_mentions"] = len(material_mentions)
    if material_mentions:
        warnings.append(
            "ACADEMIC_REVIEW: 正文出现申报材料/模板名称："
            + "、".join(sorted(set(m.strip() for m in material_mentions))[:5])
            + "；申报指南和模板应作为规则/匹配依据，不宜写入研究背景、选题依据或正文论证"
        )

    guide_count = text.count("选题指南") + text.count("申报指南")
    if guide_count >= 2:
        warnings.append(
            "ACADEMIC_REVIEW: 申报指南被反复作为背景/依据使用；指南主要用于说明选题契合度，不宜替代国家政策、权威数据和学术文献"
        )

    if "国内外研究现状" in text or "研究现状" in text:
        if stats["citation_placeholders"] < 5:
            warnings.append(
                "ACADEMIC_REVIEW: 文献综述支撑不足；应补充代表性文献，并说明作者、方法、结论与研究不足"
            )

    if any(claim in text for claim in AUTHORITATIVE_DATA_CLAIMS) and not re.search(r"\d{4}年|[0-9]+(\.[0-9]+)?万|[0-9]+(\.[0-9]+)?%|教育部|国家统计局", text):
        warnings.append(
            "ACADEMIC_REVIEW: 背景中的规模、趋势或结构性矛盾判断缺少年份、数据或权威来源"
        )

    if "事实" not in text and "证据" not in text and "数据" not in text and "案例" not in text:
        warnings.append(
            "ACADEMIC_REVIEW: 研究背景缺少“事实—问题—研究必要性”链条，建议加入统计数据、调查事实或典型案例"
        )

    if "准实验" in text:
        needed = ["样本", "对照", "周期", "指标"]
        missing = [word for word in needed if word not in text]
        if missing:
            warnings.append(
                "ACADEMIC_REVIEW: 准实验设计缺少关键细节："
                + "、".join(missing)
                + "；建议说明样本来源、试点/对照条件、干预周期和评价指标"
            )

    if "研究方法" in text and _method_names_count(text) >= 4 and not re.search(r"样本|访谈对象|问卷|变量|指标|对照|周期|数据来源", text):
        warnings.append(
            "ACADEMIC_REVIEW: 研究方法名称较多但可执行细节不足，建议补充样本、变量、数据来源、评价指标和实施步骤"
        )

    absolute_hits = [phrase for phrase in ABSOLUTE_CLAIMS if phrase in text]
    if absolute_hits and stats["citation_placeholders"] < 8:
        warnings.append(
            "ACADEMIC_REVIEW: 存在未经充分文献支撑的绝对化判断："
            + "、".join(absolute_hits[:5])
            + "；应限定范围或补充文献依据"
        )

    if "创新" in text and not re.search(r"已有研究|既有研究|现有研究|不同|差异|增量|尚未解决", text):
        warnings.append(
            "ACADEMIC_REVIEW: 创新点论证偏弱；应说明已有研究做到什么程度、本研究的实质差异及增量价值"
        )

    if re.search(r"整合.*学生|学业表现|职业倾向|岗位需求", text) and not re.search(r"数据来源|样本规模|试点|平台|合作|授权|隐私", text):
        warnings.append(
            "ACADEMIC_REVIEW: 可行性论证不足；涉及学生、岗位或平台数据时应说明数据获取条件、样本规模、试点资源和合规保障"
        )

    if "就业指导精准化" in text and "教学与就业指导一体化" in text:
        warnings.append(
            "ACADEMIC_REVIEW: 研究对象可能漂移；需统一题目、研究问题、目标、内容和成果中的核心对象"
        )


def _duplicate_paragraph_count(text: str) -> int:
    seen: set[str] = set()
    duplicates = 0
    for para in re.split(r"\n\s*\n", text):
        normalized = re.sub(r"\s+", "", para)
        normalized = re.sub(r"[，。；：、“”‘’（）()《》0-9一二三四五六七八九十#\-|]", "", normalized)
        if len(normalized) < 80:
            continue
        key = normalized[:140]
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _short_section_count(text: str) -> int:
    matches = list(HEADING_RE.finditer(text))
    count = 0
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = re.sub(r"\s+", "", text[start:end])
        if 0 < len(body) < 80:
            count += 1
    return count


def _method_names_count(text: str) -> int:
    method_names = [
        "文献研究", "问卷调查", "访谈", "案例研究",
        "行动研究", "实验", "准实验", "数据分析",
    ]
    return sum(1 for name in method_names if name in text)
