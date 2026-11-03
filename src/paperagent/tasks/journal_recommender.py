"""Journal recommendation service.

V2-m1 keeps this deterministic: it turns a manuscript/profile snapshot into
ranked journal opportunities. The contract can later be backed by Scopus,
OpenAlex, publisher APIs, or an LLM evaluator.
"""
from __future__ import annotations

import hashlib
from typing import Any

from paperagent.tasks.notification_service import NotificationService


DEFAULT_JOURNALS = [
    {
        "name": "Journal of Academic Writing",
        "scope": ["academic writing", "llm writing", "education"],
        "risk": "medium",
    },
    {
        "name": "Information Systems Research",
        "scope": ["information systems", "digital transformation", "ai"],
        "risk": "high",
    },
    {
        "name": "Research Policy",
        "scope": ["innovation", "science policy", "grant"],
        "risk": "high",
    },
    {
        "name": "Scientometrics",
        "scope": ["bibliometrics", "research evaluation", "literature"],
        "risk": "medium",
    },
]


class JournalRecommender:
    def __init__(self, store):
        self.store = store

    def recommend(self, *, title: str = "", abstract: str = "",
                  keywords: list[str] | None = None,
                  project_id: str | None = None,
                  candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        profile = self.store.get_profile()
        text = " ".join([
            title,
            abstract,
            " ".join(keywords or []),
            " ".join(profile.topics),
            " ".join(profile.directions),
        ]).lower()
        rows = []
        known = self.store.opportunity_dedup_keys()
        for journal in candidates or DEFAULT_JOURNALS:
            scope = [str(s).lower() for s in journal.get("scope", [])]
            score = _score(text, scope)
            if score <= 0:
                continue
            key = _key(journal.get("name", ""), project_id)
            if key in known:
                continue
            known.add(key)
            opp = self.store.add_opportunity(
                opportunity_type="journal",
                title=journal.get("name", "Unknown journal"),
                source="journal_recommender",
                dedup_key=key,
                topic_query=", ".join(keywords or profile.topics),
                match_score=score,
                matched=score >= 0.25,
                ai_evaluation={
                    "evaluator": "deterministic_journal_recommender_v1",
                    "score": score,
                    "rationale": "Scope keywords matched manuscript/profile text.",
                    "risk": journal.get("risk", "unknown"),
                },
                payload={**journal, "project_id": project_id},
            )
            rows.append(opp)
        rows.sort(key=lambda o: o.match_score, reverse=True)
        if rows:
            NotificationService(self.store).notify(
                type="radar",
                title=f"期刊推荐：{rows[0].title}",
                body=f"最高匹配分 {rows[0].match_score:.2f}",
                ref=rows[0].opportunity_id,
                dedup_key=f"journal:{rows[0].dedup_key}",
            )
        return {"recommendations": [o.model_dump() for o in rows]}


def _score(text: str, scope: list[str]) -> float:
    if not scope:
        return 0.1
    matched = sum(1 for term in scope if term and term in text)
    return round(matched / len(scope), 3)


def _key(name: str, project_id: str | None) -> str:
    raw = f"journal:{project_id or ''}:{name.lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
