"""Opportunity persistence helpers for radar and connector pipelines."""
from __future__ import annotations

import hashlib
from typing import Any

from paperagent.tasks.notification_service import NotificationService


class OpportunityManager:
    def __init__(self, store):
        self.store = store

    def save_extracted(self, extracted: list[dict[str, Any]],
                       *, topic_query: str = "",
                       notify: bool = False) -> dict[str, Any]:
        known = self.store.opportunity_dedup_keys()
        created = []
        for item in extracted:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            key = item.get("dedup_key") or _key(
                item.get("opportunity_type", "unknown"),
                title,
                item.get("source_url"),
            )
            if key in known:
                continue
            known.add(key)
            opp = self.store.add_opportunity(
                opportunity_type=item.get("opportunity_type", "policy"),
                title=title,
                source=item.get("source", "unknown"),
                source_url=item.get("source_url"),
                dedup_key=key,
                topic_query=topic_query or ", ".join(item.get("keywords") or []),
                match_score=float(item.get("confidence") or 0.0),
                matched=float(item.get("confidence") or 0.0) >= 0.25,
                ai_evaluation={
                    "evaluator": "deterministic_opportunity_extract_v1",
                    "score": float(item.get("confidence") or 0.0),
                    "rationale": "Keyword and deadline based opportunity extraction.",
                },
                payload=item,
            )
            created.append(opp)
        if notify and created:
            NotificationService(self.store).notify(
                type="radar",
                title=f"科研机会：{created[0].title}",
                body=f"本次发现 {len(created)} 个机会",
                ref=created[0].opportunity_id,
                dedup_key="opportunity:" + "|".join(o.opportunity_id for o in created[:5]),
            )
            for opp in created:
                self.store.update_opportunity(opp.opportunity_id, notified=True)
        return {
            "new_opportunities": len(created),
            "opportunities": [o.model_dump() for o in created],
        }


def _key(kind: str, title: str, url: str | None) -> str:
    raw = f"{kind}:{url or title}".lower()
    return "opp:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
