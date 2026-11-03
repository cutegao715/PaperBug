"""Grant / CFP opportunity discovery.

The real connectors can feed this service with institutional notices. V2-m1
accepts normalized seed records, matches them against ResearchProfile and
StandingIntent, persists Opportunity rows, and notifies only useful matches.
"""
from __future__ import annotations

import hashlib
from typing import Any

from paperagent.tasks.notification_service import NotificationService


class GrantDiscovery:
    def __init__(self, store):
        self.store = store

    def discover(self, opportunities: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        seeds = opportunities or []
        topics = self._topics()
        created = []
        known = self.store.opportunity_dedup_keys()
        for item in seeds:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            key = item.get("dedup_key") or _key(title, item.get("source_url"))
            if key in known:
                continue
            known.add(key)
            score = _best_score(title + " " + str(item.get("summary", "")), topics)
            matched = score >= 0.25
            opp = self.store.add_opportunity(
                opportunity_type="grant",
                title=title,
                source=item.get("source", "manual"),
                source_url=item.get("source_url"),
                dedup_key=key,
                topic_query=", ".join(topics),
                match_score=score,
                matched=matched,
                ai_evaluation={
                    "evaluator": "deterministic_grant_discovery_v1",
                    "score": score,
                    "rationale": "Matched grant text against profile topics and standing intents.",
                },
                payload=item,
            )
            created.append(opp)
        matched_items = [o for o in created if o.matched]
        if matched_items:
            NotificationService(self.store).notify(
                type="radar",
                title=f"课题/基金机会：{matched_items[0].title}",
                body=f"本次发现 {len(matched_items)} 个匹配机会",
                ref=matched_items[0].opportunity_id,
                dedup_key="grant:" + "|".join(o.opportunity_id for o in matched_items[:5]),
            )
            for opp in matched_items:
                self.store.update_opportunity(opp.opportunity_id, notified=True)
        return {"new_opportunities": len(created),
                "matched": len(matched_items),
                "opportunities": [o.model_dump() for o in created]}

    def _topics(self) -> list[str]:
        topics = list(self.store.get_profile().topics)
        for intent in self.store.list_standing_intents():
            if intent.enabled and intent.type == "grant":
                topic = intent.topic or " ".join(intent.structured_query.get("keywords", []))
                if topic:
                    topics.append(topic)
        return [t for t in topics if t]


def _best_score(text: str, topics: list[str]) -> float:
    text_l = text.lower()
    words = []
    for topic in topics:
        words.extend(w for w in topic.lower().split() if len(w) > 1)
    if not words:
        return 0.0
    return round(sum(1 for w in set(words) if w in text_l) / len(set(words)), 3)


def _key(title: str, url: str | None) -> str:
    raw = (url or title).lower()
    return "grant:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
