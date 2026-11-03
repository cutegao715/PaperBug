"""Opportunity extraction from web pages, emails, and notices."""
from __future__ import annotations

import re
from typing import Any


TYPE_KEYWORDS = {
    "conference": [
        "call for papers", "cfp", "conference", "workshop", "symposium",
        "征稿", "会议", "论坛", "研讨会",
    ],
    "journal": [
        "special issue", "journal", "author guidelines", "期刊", "专刊",
        "专题征稿",
    ],
    "grant": [
        "grant", "funding", "foundation", "proposal", "项目申报", "基金",
        "课题", "申报通知",
    ],
    "school_notice": [
        "department", "school", "university", "学院", "学校", "研究生院",
        "通知",
    ],
    "policy": ["policy", "regulation", "guideline", "政策", "办法", "指南"],
}


def run_opportunity_extract(input: dict, timeout: float = 20.0) -> dict[str, Any]:
    title = str(input.get("title") or "").strip()
    text = str(input.get("text") or "").strip()
    url = input.get("url")
    source = input.get("source", "unknown")
    opportunity_type = input.get("opportunity_type") or _classify(" ".join([title, text]))
    deadline = _deadline(text) or _deadline(title)
    keywords = _keywords(" ".join([title, text]))
    extracted_title = title or _first_heading(text) or "Untitled opportunity"
    confidence = _confidence(opportunity_type, " ".join([title, text]), deadline)
    return {
        "opportunities": [{
            "opportunity_type": opportunity_type,
            "title": extracted_title,
            "source": source,
            "source_url": url,
            "deadline": deadline,
            "keywords": keywords,
            "confidence": confidence,
            "summary": _summary(text),
        }],
        "provenance": url or source or "inline_text",
    }


def _classify(text: str) -> str:
    lower = text.lower()
    scores = {}
    for kind, terms in TYPE_KEYWORDS.items():
        scores[kind] = sum(1 for term in terms if term.lower() in lower)
    kind, score = max(scores.items(), key=lambda item: item[1])
    return kind if score > 0 else "policy"


def _deadline(text: str) -> str | None:
    patterns = [
        r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b",
        r"\b(20\d{2})年(\d{1,2})月(\d{1,2})日\b",
        r"(deadline|due|截止|截至|申报截止)[^\n。；;]{0,30}?"
        r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        nums = [g for g in match.groups() if g and g.isdigit()]
        if len(nums) >= 3:
            year, month, day = nums[-3:]
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return None


def _keywords(text: str) -> list[str]:
    out = []
    for term in [
        "AI", "LLM", "academic writing", "digital transformation",
        "management", "education", "信息系统", "人工智能", "大模型",
        "数字化转型", "教育", "管理",
    ]:
        if term.lower() in text.lower():
            out.append(term)
    return out[:10]


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        line = line.strip(" #\t")
        if 6 <= len(line) <= 140:
            return line
    return ""


def _summary(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:500]


def _confidence(kind: str, text: str, deadline: str | None) -> float:
    terms = TYPE_KEYWORDS.get(kind, [])
    hits = sum(1 for term in terms if term.lower() in text.lower())
    score = min(0.9, 0.25 + hits * 0.15)
    if deadline:
        score += 0.1
    return round(min(score, 1.0), 3)
