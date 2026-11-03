"""V2-m1 HTTP surface: today / tasks / deadlines / intents / radar / notifications."""
import json

from fastapi.testclient import TestClient

from paperagent import server
from paperagent.server import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERAGENT_SCHEDULER", "off")  # no background thread in tests
    server.RUNS.clear()
    return TestClient(app)


def test_v2_end_to_end_flow(tmp_path, monkeypatch):
    with _client(monkeypatch, tmp_path) as client:
        assert client.post("/api/v1/projects", json={
            "name": "demo",
            "goal": "LLM academic writing support",
        }).status_code == 200

        # profile topics feed the radar
        r = client.post("/api/v1/profile", json={
            "directions": ["信息系统"], "topics": ["LLM academic writing"],
            "language": {"writing_language": "bilingual",
                         "citation_style": "gbt7714"},
            "disciplines": [{"discipline_id": "management",
                             "methods": ["survey"]}]})
        assert r.status_code == 200
        assert r.json()["language"]["citation_style"] == "gbt7714"
        assert r.json()["disciplines"][0]["discipline_id"] == "management"

        project_profile = client.post("/api/v1/projects/demo/profile", json={
            "project_type": "journal_article",
            "title": "LLM academic writing support",
            "discipline_ids": ["management"],
            "target_venue": "Journal of Academic Writing",
        })
        assert project_profile.status_code == 200
        assert client.get("/api/v1/projects/demo/profile").json()["target_venue"] == \
            "Journal of Academic Writing"

        # standing intent adds a second topic
        r = client.post("/api/v1/standing-intents", json={
            "description": "关注 LLM 写作进展",
            "structured_query": {"query": "LLM writing"},
            "notify_policy": {"cadence": "daily"}})
        assert r.status_code == 200
        intent = r.json()
        assert intent["topic"] == "LLM writing"
        assert intent["dedup_key"]
        assert intent["notify_policy"]["cadence"] == "daily"
        assert len(client.get("/api/v1/standing-intents").json()["intents"]) == 1

        # radar runs through the gateway with a stubbed search backend
        real_search = server._radar  # keep reference
        from paperagent.radar.pipeline import LiteratureRadar
        original_init = LiteratureRadar.__init__

        def patched_init(self, store, search_fn=None):
            candidates = [
                {"ref_id": "a2024llm", "title": "LLM writing survey 2024",
                 "authors": ["A"], "year": 2024, "doi": "10.1/a",
                 "source": "openalex"},
            ]
            original_init(self, store,
                          search_fn=lambda inp: {"candidates": candidates})

        LiteratureRadar.__init__ = patched_init
        try:
            r = client.post("/api/v1/radar/run", json={})
        finally:
            LiteratureRadar.__init__ = original_init
        assert r.status_code == 200
        result = r.json()
        assert result["new_hits"] == 1

        # hit stored, notification aggregated
        hits = client.get("/api/v1/radar/hits").json()["hits"]
        assert len(hits) == 1 and hits[0]["matched"] is True
        opps = client.get("/api/v1/opportunities").json()["opportunities"]
        assert len(opps) == 1
        assert opps[0]["opportunity_type"] == "literature"
        assert opps[0]["notified"] is True
        assert opps[0]["ai_evaluation"]["evaluator"] == "deterministic_radar_v1"
        notifs = client.get("/api/v1/notifications").json()["notifications"]
        assert any(n["type"] == "radar" for n in notifs)

        # mark read
        nid = notifs[0]["notification_id"]
        assert client.post(f"/api/v1/notifications/{nid}/read").json()["ok"] is True
        assert client.post("/api/v1/notifications/nope/read").status_code == 404

        # tasks
        t = client.post("/api/v1/tasks", json={"title": "写开题报告",
                                            "due_date": "2026-10-10T00:00:00+00:00"}).json()
        assert client.post(f"/api/v1/tasks/{t['task_id']}/status",
                           json={"status": "in_progress"}).json()["status"] == "in_progress"
        assert client.post(f"/api/v1/tasks/{t['task_id']}/status",
                           json={"status": "done"}).json()["status"] == "done"
        assert client.post(f"/api/v1/tasks/{t['task_id']}/status",
                           json={"status": "todo"}).status_code == 422  # done is terminal

        # deadlines
        from paperagent.domain.entities import iso, utcnow
        from datetime import timedelta
        d = client.post("/api/v1/deadlines", json={
            "title": "基金截止", "when": iso(utcnow() + timedelta(days=2))}).json()
        assert client.post("/api/v1/deadlines", json={
            "title": "bad", "when": "not-a-date"}).status_code == 422

        # manual scheduler tick with a due automation -> digest notification
        from paperagent.domain.entities import iso, utcnow
        from datetime import timedelta
        auto = client.post("/api/v1/automations", json={
            "name": "digest", "cadence": "daily", "action": "daily_digest",
            "next_run": iso(utcnow() - timedelta(minutes=1)),
        })
        assert auto.status_code == 200
        assert len(client.get("/api/v1/automations").json()["automations"]) == 1
        assert client.post("/api/v1/automations", json={
            "name": "bad", "cadence": "daily", "action": "daily_digest",
            "next_run": "not-a-date",
        }).status_code == 422
        r = client.post("/api/v1/scheduler/tick")
        assert r.status_code == 200
        assert any("digest" in line for line in r.json()["log"])

        # V2 context support: skills, tools, memories, watchers
        assert any(s["id"] == "academic_writing"
                   for s in client.get("/api/v1/skills").json()["skills"])
        disciplines = client.get("/api/v1/disciplines").json()["disciplines"]
        assert any(d["discipline_id"] == "management" for d in disciplines)
        assert client.get("/api/v1/disciplines/management").json()["name"]
        phase5_skills = client.get("/api/v1/skills", params={
            "project_type": "conference_paper",
            "discipline_id": "management",
        }).json()["skills"]
        phase5_ids = {s["id"] for s in phase5_skills}
        assert "submission_preparation" in phase5_ids
        assert "management_discipline" in phase5_ids
        assert any(t["tool_id"] == "literature_search"
                   for t in client.get("/api/v1/tools").json()["tools"])
        mem = client.post("/api/v1/memories", json={
            "layer": "user",
            "content": "偏好紧凑、证据先行的写法",
            "tags": ["writing"],
        })
        assert mem.status_code == 200
        assert client.get("/api/v1/memories", params={
            "goal": "writing",
        }).json()["memories"]["user"]
        watcher = client.post("/api/v1/watchers", json={
            "name": "submission inbox",
            "watcher_type": "submission",
            "source": "email",
        })
        assert watcher.status_code == 200
        wid = watcher.json()["watcher_id"]
        assert client.post(f"/api/v1/watchers/{wid}/ingest", json={
            "payload": {"subject": "status", "cursor": "msg-1"},
        }).status_code == 200
        assert len(client.get("/api/v1/watchers").json()["watchers"]) == 1

        journal = client.post("/api/v1/journals/recommend", json={
            "title": "LLM academic writing support",
            "keywords": ["academic writing"],
            "project_id": "demo",
        })
        assert journal.status_code == 200
        assert journal.json()["recommendations"]
        grant = client.post("/api/v1/grants/discover", json={
            "opportunities": [{
                "title": "LLM academic writing grant",
                "summary": "LLM academic writing",
                "source": "manual",
            }],
        })
        assert grant.status_code == 200
        assert grant.json()["new_opportunities"] == 1

        ev = client.get("/api/v1/projects/demo/evidence-evaluation")
        assert ev.status_code == 200
        assert ev.json()["evaluator"] == "deterministic_evidence_v1"

        # submissions: valid state transition emits domain state
        sub = client.post("/api/v1/submissions", json={
            "title": "LLM writing paper",
            "journal_name": "Journal X",
            "manuscript_id": "JX-001",
        }).json()
        assert sub["status"] == "submitted"
        transitioned = client.post(
            f"/api/v1/submissions/{sub['submission_id']}/status",
            json={"status": "with_editor", "source": "email"},
        )
        assert transitioned.status_code == 200
        assert transitioned.json()["status"] == "with_editor"
        assert client.post(
            f"/api/v1/submissions/{sub['submission_id']}/status",
            json={"status": "published"},
        ).status_code == 422
        parsed = client.post("/api/v1/submissions/parse-email", json={
            "subject": "Manuscript status",
            "text": "Journal: Journal X\nManuscript ID: JX-001\nStatus: Under Review",
            "apply": True,
        })
        assert parsed.status_code == 200
        assert parsed.json()["parsed"]["status"] == "under_review"
        assert parsed.json()["submission"]["status"] == "under_review"
        connector = client.post("/api/v1/connectors/email/submission", json={
            "from_addr": "editor@example.org",
            "subject": "Decision",
            "text": "Journal: Journal X\nManuscript ID: JX-001\nStatus: Minor Revision",
        })
        assert connector.status_code == 200
        assert connector.json()["event"]["type"] == "connector_event"
        assert connector.json()["submission"]["status"] == "minor_revision"

        # today digest ties it together
        today = client.get("/api/v1/today").json()
        assert today["digest"]["title"] == "今日科研简报"
        assert today["deadlines"][0]["title"] == "基金截止"
        assert {o["opportunity_type"] for o in today["recent_opportunities"]} >= {
            "literature", "journal", "grant"}
        assert len(today["recent_radar_hits"]) == 1
