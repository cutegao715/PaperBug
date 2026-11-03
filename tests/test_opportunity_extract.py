from fastapi.testclient import TestClient

from paperagent import server
from paperagent.domain.store import DomainStore
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy
from paperagent.tasks.opportunity_manager import OpportunityManager
from paperagent.workspace.workspace import Workspace


NOTICE_TEXT = """
Call for Papers: International Conference on AI for Academic Writing
We invite submissions on LLM, education, and research workflows.
Submission deadline: 2026-12-15.
"""


def test_opportunity_extract_tool_detects_conference_deadline(tmp_path):
    ws = Workspace("opp", root=tmp_path).init("find CFPs")
    ex = ToolExecutor(ResearchPolicy(), ws)

    result = ex.execute(ToolCall(tool_id="opportunity_extract", input={
        "title": "AI Academic Writing CFP",
        "text": NOTICE_TEXT,
        "url": "https://conf.example/cfp",
        "source": "web",
    }))

    assert result.status == "ok"
    opp = result.data["opportunities"][0]
    assert opp["opportunity_type"] == "conference"
    assert opp["deadline"] == "2026-12-15"
    assert "LLM" in opp["keywords"]


def test_opportunity_manager_saves_and_deduplicates(tmp_path):
    store = DomainStore(tmp_path)
    extracted = [{
        "opportunity_type": "conference",
        "title": "AI Academic Writing CFP",
        "source": "web",
        "source_url": "https://conf.example/cfp",
        "deadline": "2026-12-15",
        "keywords": ["LLM"],
        "confidence": 0.8,
    }]

    first = OpportunityManager(store).save_extracted(extracted, notify=True)
    second = OpportunityManager(store).save_extracted(extracted, notify=True)

    assert first["new_opportunities"] == 1
    assert second["new_opportunities"] == 0
    assert store.list_opportunities()[0].opportunity_type == "conference"
    assert store.list_opportunities()[0].payload["deadline"] == "2026-12-15"
    assert store.list_notifications()[0].type == "radar"


def test_opportunity_extract_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERAGENT_SCHEDULER", "off")
    server.RUNS.clear()

    with TestClient(server.app) as client:
        response = client.post("/api/v1/opportunities/extract", json={
            "title": "项目申报通知",
            "text": "2026年度人工智能教育项目申报通知，申报截止：2026年11月20日。",
            "source": "school",
            "url": "https://school.example/notice",
            "save": True,
        })
        listed = client.get("/api/v1/opportunities").json()

    assert response.status_code == 200
    assert response.json()["opportunities"][0]["opportunity_type"] == "grant"
    assert response.json()["saved"]["new_opportunities"] == 1
    assert listed["opportunities"][0]["payload"]["deadline"] == "2026-11-20"
