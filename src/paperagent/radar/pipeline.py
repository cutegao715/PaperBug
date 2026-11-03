"""Literature Radar: deterministic pipeline, no agent loop.

Source Fetch -> Normalize -> Deduplicate -> Hard Filter -> Rule Match
-> evaluator advisory judgment -> NotificationService.

Reuses the existing literature_search tool (Semantic Scholar with
OpenAlex fallback), so radar works offline with provider="offline_stub".
"""
from __future__ import annotations
from paperagent.quality.policies import proactive_run

from typing import Callable, Optional

from paperagent.domain.entities import radar_dedup_key
from paperagent.domain.store import DomainStore
from paperagent.quality.evaluators import (DeterministicRadarEvaluator,
                                           RadarRelevanceEvaluator)
from paperagent.tasks.notification_service import NotificationService

SearchFn = Callable[[dict], dict]


class LiteratureRadar:
    def __init__(self, store: DomainStore, search_fn: Optional[SearchFn] = None,
                 evaluator: Optional[RadarRelevanceEvaluator] = None, sources=None):
        self.store = store
        self.search_fn = search_fn
        self.sources = sources
        self.evaluator = evaluator or DeterministicRadarEvaluator()

    def topics(self) -> list[str]:
        seen: list[str] = []
        for si in self.store.list_standing_intents():
            if si.enabled and si.type == "literature":
                topic = _intent_topic(si)
                if topic and topic not in seen:
                    seen.append(topic)
        for topic in self.store.get_profile().topics:
            if topic and topic not in seen:
                seen.append(topic)
        return seen

    @proactive_run
    def run(self, provider: Optional[str] = None,
            extra_topics: Optional[list[str]] = None) -> dict:
        topics = self.topics()
        if extra_topics:
            topics += [t for t in extra_topics if t not in topics]
        if not topics:
            return {"topics": 0, "new_hits": 0,
                    "note": "no topics: add profile topics or standing intents"}

        self._pending_source_states = []
        known = self.store.radar_dedup_keys()
        new_hits = []
        for topic in topics:
            result = self._fetch(topic, provider)
            for c in result.get("candidates") or []:
                title = c.get("title") or ""
                if not title or title.startswith("[SYNTHETIC STUB"):
                    continue  # hard filter: never record synthetic stubs as hits
                key = radar_dedup_key(title, c.get("doi"))
                if key in known:
                    continue
                known.add(key)
                matched = _rule_match(topic, title)
                match_score = _match_score(topic, title)
                evaluation = self.evaluator.evaluate(
                    topic=topic, candidate=c, match_score=match_score)
                notify_worthy = matched and evaluation.should_notify
                opp = self.store.add_opportunity(
                    opportunity_type="literature",
                    title=title,
                    source=c.get("source", "unknown"),
                    source_url=c.get("url"),
                    dedup_key=key,
                    topic_query=topic,
                    match_score=match_score,
                    matched=notify_worthy,
                    ai_evaluation=evaluation.model_dump(),
                    payload={
                        "ref_id": c.get("ref_id"),
                        "authors": c.get("authors") or [],
                        "year": c.get("year"),
                        "doi": c.get("doi"),
                        "venue": c.get("venue"),
                    },
                )
                hit = self.store.add_radar_hit(
                    radar_type="literature", title=title,
                    authors=c.get("authors") or [], year=c.get("year"),
                    doi=c.get("doi"), url=c.get("url"),
                    source=c.get("source", "unknown"),
                    topic_query=topic, matched=notify_worthy)
                new_hits.append(hit)

        matched_hits = [h for h in new_hits if h.matched]
        if matched_hits:
            lines = "\n".join(
                f"· {h.title[:80]}（{h.year or 'n.d.'}，来源 {h.source}）"
                for h in matched_hits[:5])
            NotificationService(self.store).notify(
                type="radar",
                title=f"Research Radar：{len(matched_hits)} 篇新文献与你的研究相关",
                body=lines,
                ref=matched_hits[0].hit_id,
                dedup_key="radar:" + "|".join(h.hit_id for h in matched_hits[:5]))
            for opp in self.store.list_opportunities(limit=len(new_hits) + 10):
                if opp.opportunity_type == "literature" and opp.matched and not opp.notified:
                    self.store.update_opportunity(opp.opportunity_id, notified=True)
        from paperagent.workspace.state_store import state_lock, read_json, write_json
        for path, key, value in self._pending_source_states:
            with state_lock(path):
                state = read_json(path)
                state[key] = value
                write_json(path, state)
        return {"topics": len(topics), "new_hits": len(new_hits),
                "matched": len(matched_hits)}


    def _fetch(self, topic, provider):
        import hashlib
        from datetime import datetime, timezone, timedelta
        from paperagent.radar.sources import configured_sources
        from paperagent.workspace.state_store import state_lock, read_json, write_json
        path = self.store.dir / 'radar_sources.json'
        sources = self.sources if self.sources is not None else configured_sources(self.search_fn, provider)
        items = []
        today = datetime.now(timezone.utc).date()
        # One page per source/topic/tick. Persist only after candidates are processed.
        for source in sources:
            key = hashlib.sha256((source.name + ':' + topic).encode()).hexdigest()
            with state_lock(path):
                state = read_json(path)
                previous = state.get(key, {})
            since = previous.get('since', (today - timedelta(days=30)).isoformat())
            try:
                batch = source.fetch(topic, since, previous.get('cursor'))
            except PermissionError:
                raise
            except Exception as exc:
                with state_lock(path):
                    state = read_json(path)
                    state[key] = previous | {'error': type(exc).__name__, 'source': source.name}
                    write_json(path, state)
                continue
            items.extend(batch.items)
            next_since = since if batch.next_cursor else (today - timedelta(days=7)).isoformat()
            self._pending_source_states.append((path, key, {
                'source': source.name, 'since': next_since, 'cursor': batch.next_cursor,
                'coverage': batch.coverage, 'checked_at': datetime.now(timezone.utc).isoformat()}))
        return {'candidates': items}


def _rule_match(topic: str, title: str) -> bool:
    """Keyword rule: significant topic words appear in the title."""
    words = [w for w in topic.lower().split() if len(w) > 2]
    if not words:
        return True
    title_l = title.lower()
    return any(w in title_l for w in words)


def _match_score(topic: str, title: str) -> float:
    words = [w for w in topic.lower().split() if len(w) > 2]
    if not words:
        return 1.0
    title_l = title.lower()
    matched = sum(1 for w in words if w in title_l)
    return round(matched / len(words), 3)


def _intent_topic(intent) -> str:
    structured = intent.structured_query or {}
    for key in ("query", "topic"):
        value = structured.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    keywords = structured.get("keywords")
    if isinstance(keywords, list):
        return " ".join(str(k).strip() for k in keywords if str(k).strip())
    return intent.topic
