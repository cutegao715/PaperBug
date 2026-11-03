"""A1/A2/A3 run beside workflows, using explicitly enabled watcher sources."""
from __future__ import annotations
from paperagent.quality.policies import proactive_run

import hashlib
import json
from datetime import timedelta

from paperagent.domain.entities import iso
from paperagent.tasks.notification_service import NotificationService


class ProactiveRuntime:
    def __init__(self, store, *, search=None, fetch=None, literature=None):
        from paperagent.tools.web import run_search, run_fetch
        from paperagent.radar.pipeline import LiteratureRadar
        self.store = store
        self.search = search or run_search
        self.fetch = fetch or run_fetch
        self.literature = literature or LiteratureRadar(store)

    @proactive_run
    def tick(self, now):
        from paperagent.tasks.scheduler import _parse
        log = []
        for watcher in self.store.list_watchers():
            service = watcher.query.get("service")
            if not watcher.enabled or service not in {"research_radar", "opportunity_radar", "progress_watcher"}:
                continue
            seconds = max(60, int(watcher.query.get("interval_seconds", 3600)))
            if watcher.last_checked and _parse(watcher.last_checked) + timedelta(seconds=seconds) > now:
                continue
            try:
                if service == "research_radar":
                    query = str(watcher.query.get("query", "")).strip()
                    result = self.literature.run(extra_topics=[query] if query else [])
                elif service == "opportunity_radar":
                    result = self.opportunities(watcher, now)
                else:
                    result = self.progress(watcher)
                self.store.update_watcher(watcher.watcher_id, last_checked=iso(now))
                log.append(f"{service} {watcher.watcher_id}: {result}")
            except Exception as exc:
                # Isolate sources and retry at the configured cadence.
                self.store.update_watcher(watcher.watcher_id, last_checked=iso(now))
                log.append(f"{service} {watcher.watcher_id} failed: {exc}")
        return log

    def opportunities(self, watcher, now):
        from paperagent.tools.web import html_extract
        from paperagent.tools.opportunities import run_opportunity_extract
        urls = list(watcher.query.get("urls", []))[:5]
        query = watcher.query.get("query")
        if query:
            results = self.search({"query": query, "max_results": 5})
            if results.get("error"):
                raise ValueError(results["error"])
            urls += [r["url"] for r in results.get("results", []) if r.get("url")]
        if not urls:
            return {"new": 0, "note": "configure source URLs or a query"}
        known = self.store.opportunity_dedup_keys()
        count = 0
        failures = []
        for url in list(dict.fromkeys(urls))[:5]:
            try:
                page = self.fetch({"url": url})
                if page.get("error"):
                    raise ValueError(page["error"])
                extracted = html_extract(page.get("html", ""), page.get("url", url))
                candidates = run_opportunity_extract({**extracted, "source": "opportunity_radar"})["opportunities"]
                for item in candidates:
                    if item["opportunity_type"] not in {"grant", "journal", "conference"}:
                        continue
                    key = "watch-opportunity:" + hashlib.sha256((url + extracted["text"]).encode()).hexdigest()
                    if key in known:
                        continue
                    known.add(key)
                    # Extraction is a candidate, never a verified eligibility or deadline.
                    row = self.store.add_opportunity(
                        opportunity_type=item["opportunity_type"], title=item["title"],
                        source="opportunity_radar", source_url=url, dedup_key=key,
                        topic_query=query or watcher.name, matched=True,
                        payload={**item, "verification": "candidate_requires_official_review", "snapshot": page.get("snapshot"), "checked_at": iso(now)})
                    NotificationService(self.store).notify(type="radar", title="新科研机会：" + item["title"],
                        body="来源：" + url + "\n资格、费用和截止日期需按当前官方通知核对。", ref=row.opportunity_id, dedup_key=key)
                    self.store.update_opportunity(row.opportunity_id, notified=True)
                    count += 1
            except PermissionError:
                raise
            except Exception as exc:
                failures.append({"url": url, "error": str(exc)})
        return {"new": count, "failures": failures}

    def progress(self, watcher):
        selected = watcher.query.get("project_id")
        count = 0
        for event in self.store.list_trigger_events():
            if event.type not in {"external", "state_change"}:
                continue
            payload = event.payload
            if selected and payload.get("project_id") != selected:
                continue
            content = json.dumps(payload, ensure_ascii=False)
            key = "feedback:" + watcher.watcher_id + ":" + hashlib.sha256(content.encode()).hexdigest()
            before = len(self.store.list_notifications())
            NotificationService(self.store).notify(type="system", title="进度与反馈更新：" + watcher.name,
                body=content[:2000], ref=event.ref, dedup_key=key)
            count += len(self.store.list_notifications()) - before
        return {"new": count}
