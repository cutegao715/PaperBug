"""Reviews are optional version-bound artifacts, never a second task system."""
import hashlib
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.conversation import ConversationManager
from paperagent.domain.lifecycle import LifecycleService
from paperagent.domain.manuscript import ManuscriptContext, ManuscriptRecordInput
from paperagent.domain.paper_review import PaperReviewInput, PaperReviews, ReviewFollowupInput
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.actions import AgentAction, ChatReplyAction, ReadContextAction, ReadDraftAction, SavePaperReviewAction
from paperagent.schemas.policy import ResearchPolicy
from paperagent.skills.loader import SkillLoader
from paperagent.workspace.workspace import Workspace


def setup(root):
    ws = Workspace("paper", root).init("English manuscript")
    LifecycleService(ws).save(lifecycle_type="paper_conference", stage="drafting", reason="Begin")
    ws.write_draft("paper.md", "# Methods\nWe recruited participants.\n# Results\nThe groups differ.\n")
    ws.write_draft("response.md", "We fixed the manuscript.")
    return ws, PaperReviews(ws)


def review(service, **changes):
    row = {"title": "Methods review", "scope": "Methods and inference boundary only", "round_label": "substantive",
           "artifacts": [{"filename": "paper.md", "expected_sha256": service.basis("paper.md")["version"]},
                         {"filename": "response.md", "expected_sha256": service.basis("response.md")["version"], "role": "response"}],
           "modules": ["paper_review:general"],
           "checks": [{"dimension": "Sampling", "coverage": "checked", "basis": "Read Methods"},
                      {"dimension": "Raw data", "coverage": "unavailable", "basis": "Not supplied"}],
           "findings": [{"title": "Population denominator", "location": "paper.md / Methods",
                         "problem": "The denominator is not specified.", "basis": "The Methods only says participants.",
                         "action_needed": "State the verified denominator.", "severity": "major", "evidence_status": "supported",
                         "classification": "reporting", "affected_artifacts": ["paper.md"]}],
           "summary": "Reporting can be clarified; raw data and scientific validity not verified."}
    return PaperReviewInput.model_validate({**row, **changes})


def correction(service, revision=1, **changes):
    return ReviewFollowupInput.model_validate({"expected_revision": revision, "status": "resolved", "reason": "Checked actual Methods",
        "verifications": [{"filename": "paper.md", "expected_sha256": service.basis("paper.md")["version"],
                           "locator": "Methods", "quote": "We recruited 40 participants."}], **changes})


def reader(ws, agent=None, iterations=6, **kwargs):
    return DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=iterations), ws), agent or object(), log=lambda _: None, **kwargs)


@pytest.mark.parametrize("goal,expected", [("评审论文", "paper_review"), ("review my paper", "paper_review"),
                                           ("回复审稿人", "reviewer_response"), ("draft a response letter", "reviewer_response")])
def test_review_and_response_discovery_are_distinct(tmp_path, goal, expected):
    loader = SkillLoader(tmp_path)
    selected = loader.select(goal=goal, lifecycle_type="paper_conference", limit=1)
    assert selected[0].id == expected
    spec = loader.resolve("builtin:paper_review@1.0")
    assert spec.metadata_view()["resource_count"] == 8
    assert "resources" not in spec.metadata_view()
    assert "resolved assessment" not in json.dumps(spec.context_view())
    for resource in spec.resources:
        result = loader.read_resource(spec.key, resource.id)
        assert result["content"] and result["content_role"] == "skill_guidance"
    assert all("ethan" not in r.path and "precedents" not in r.path for r in spec.resources)


def test_report_preserves_scope_candidate_and_no_change_outcomes(tmp_path):
    ws, service = setup(tmp_path)
    assert service.context() == {} and not service.path.exists()
    before = ws.read_draft("paper.md")
    report = service.create(review(service), actor="agent", run_id="r1")
    assert service.view()["reports"][0]["report_url"].startswith("/api/v1/projects/")
    original = service.report_text(report["report_id"])
    assert "Raw data: unavailable" in original and "Scope: Methods and inference" in original
    assert ws.read_draft("paper.md") == before
    assert not service.view()["reports"][0]["needs_review"]
    assert service.view()["reports"][0]["findings"][0]["status"] == "open"
    assert not (ws.dir / "tasks").exists() and not (ws.dir / "manuscript.json").exists()
    clean = service.create(review(service, findings=[], summary="No issue found in the inspected unit; no inference about unread data."))
    assert clean["findings"] == []
    assert len(list((ws.dir / "reviews/paper").glob("*.md"))) == 2
    assert service.report_text(report["report_id"]) == original


def test_report_rejects_stale_candidates_and_wrong_workspace_links(tmp_path):
    ws, service = setup(tmp_path)
    body = review(service)
    ws.write_draft("paper.md", "Modified outside the review")
    with pytest.raises(ValueError, match="changed"):
        service.create(body)
    report = service.create(review(service))
    other = PaperReviews(Workspace("other", tmp_path).init("other"))
    with pytest.raises(ValueError, match="not found"):
        other.report_text(report["report_id"])
    with pytest.raises(ValueError, match="workspace"):
        service.create(review(service, prior_report_id="other-report"))
    with pytest.raises(ValueError, match="Knowledge"):
        service.create(review(service, knowledge_ids=["other-record"]))
    with pytest.raises(ValueError):
        service.basis("../outside.md")
    with pytest.raises(ValidationError, match="artifacts in this review"):
        bad = body.model_dump()
        bad["findings"][0]["affected_artifacts"] = ["outside.md"]
        PaperReviewInput.model_validate(bad)


def test_historical_source_survives_current_draft_changes_and_detects_tampering(tmp_path):
    ws, service = setup(tmp_path)
    report = service.create(review(service))
    rid = report["report_id"]
    old = service.source_text(rid, "paper.md")
    ws.write_draft("paper.md", "new manuscript")
    assert service.source_text(rid, "paper.md") == old
    assert service.view()["reports"][0]["needs_review"]
    assert service.source_text(rid, "response.md") == "We fixed the manuscript."
    with pytest.raises(ValueError, match="Artifact not found"):
        service.source_text(rid, "../outside.md")
    snapshot = Path(report["artifacts"][0]["snapshot_path"])
    snapshot.write_text("tampered source", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot changed"):
        service.source_text(rid, "paper.md")
    assert any("校验失败" in s for s in service.view()["reports"][0]["review_issues"])
    memo = ws.dir / "reviews/paper" / (rid + ".md")
    memo.write_text("tampered report", encoding="utf-8")
    with pytest.raises(ValueError, match="report changed"):
        service.report_text(rid)


def test_reply_or_unmatched_quote_cannot_resolve_an_issue(tmp_path):
    ws, service = setup(tmp_path)
    report = service.create(review(service))
    rid, iid = report["report_id"], report["findings"][0]["issue_id"]
    with pytest.raises(ValidationError, match="located evidence"):
        ReviewFollowupInput(expected_revision=1, status="resolved", reason="Author replied fixed")
    response = {"filename": "response.md", "expected_sha256": service.basis("response.md")["version"], "locator": "Reply", "quote": "We fixed the manuscript."}
    with pytest.raises(ValueError, match="Response letters"):
        service.followup(rid, iid, correction(service, verifications=[response]))
    with pytest.raises(ValueError, match="quote is absent"):
        service.followup(rid, iid, correction(service))
    assert service.view()["reports"][0]["findings"][0]["status"] == "open"
    ws.write_draft("paper.md", "# Methods\nWe recruited 40 participants.\n")
    bad_hash = correction(service).model_dump()
    bad_hash["verifications"][0]["expected_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        service.followup(rid, iid, ReviewFollowupInput.model_validate(bad_hash))


def test_agent_resolution_is_proposal_and_user_adoption_is_version_checked(tmp_path):
    ws, service = setup(tmp_path)
    original_text = ws.read_draft("paper.md")
    report = service.create(review(service))
    rid, iid = report["report_id"], report["findings"][0]["issue_id"]
    ws.write_draft("paper.md", "# Methods\nWe recruited 40 participants.\n")
    proposal = service.followup(rid, iid, correction(service), actor="agent", run_id="check-r2")
    issue = service.view()["reports"][0]["findings"][0]
    assert issue["status"] == "open" and issue["revision"] == 2
    assert issue["proposals"][0]["status"] == "resolved"
    service.adopt(rid, iid, expected_revision=2, followup_id=proposal["followup_id"], reason="I checked the denominator against source records")
    issue = service.view()["reports"][0]["findings"][0]
    assert issue["status"] == "resolved" and issue["proposals"] == []
    with pytest.raises(ValueError, match="already adopted"):
        service.adopt(rid, iid, expected_revision=3, followup_id=proposal["followup_id"], reason="Duplicate")
    ws.write_draft("paper.md", original_text)
    issue = service.view()["reports"][0]["findings"][0]
    assert issue["status"] == "not_verifiable" and issue["recorded_status"] == "resolved"
    assert issue["needs_review"]
    assert service.read()["followups"][-1]["status"] == "resolved"  # historical judgment preserved
    assert service.context()["reports"][0]["needs_review"]  # original candidate returned, but later resolution is stale


def test_followup_history_allows_partial_waiver_and_reopen_without_fixed_order(tmp_path):
    ws, service = setup(tmp_path)
    report = service.create(review(service))
    rid, iid = report["report_id"], report["findings"][0]["issue_id"]
    service.followup(rid, iid, correction(service, status="partial", verifications=[]), actor="user")
    with pytest.raises(ValueError, match="changed"):
        service.followup(rid, iid, correction(service, status="open", verifications=[]))
    waiver = service.followup(rid, iid, correction(service, revision=2, status="waived", verifications=[]), actor="agent")
    assert service.view()["reports"][0]["findings"][0]["status"] == "partial"
    ws.write_draft("paper.md", "Edited candidate")
    with pytest.raises(ValueError, match="basis changed"):
        service.adopt(rid, iid, expected_revision=3, followup_id=waiver["followup_id"], reason="Stale proposal")
    service.followup(rid, iid, correction(service, revision=3, status="waived", verifications=[]), actor="user")
    service.followup(rid, iid, correction(service, revision=4, status="open", verifications=[]), actor="user")
    assert service.view()["reports"][0]["findings"][0]["status"] == "open"
    assert len(service.read()["followups"]) == 4


def test_paper_knowledge_and_venue_changes_invalidate_review_basis(tmp_path):
    ws, service = setup(tmp_path)
    context = ManuscriptContext(ws)
    term = context.record(ManuscriptRecordInput(kind="term", title="Population", term={"preferred": "participants"},
        sources=[{"kind": "user_note", "key": "Author definition"}], reason="Keep term"))
    report = service.create(review(service, knowledge_ids=[term["record_id"]]))
    ws.write_draft("paper.md", "We recruited 40 participants.")
    rid, iid = report["report_id"], report["findings"][0]["issue_id"]
    service.followup(rid, iid, correction(service), actor="user")
    assert service.view()["reports"][0]["findings"][0]["status"] == "resolved"
    context.review(term["record_id"], expected_revision=1, status="withdrawn", reason="Concept no longer applies")
    assert service.view()["reports"][0]["findings"][0]["status"] == "not_verifiable"
    # A linked prior report does not automatically assert its issues were fixed.
    next_report = service.create(review(service, prior_report_id=rid))
    assert next_report["prior_report_id"] == rid
    assert service.view()["reports"][-1]["findings"][0]["status"] == "open"


def test_review_context_is_bounded_and_resume_uses_current_state_without_tasks(tmp_path):
    ws, service = setup(tmp_path)
    for n in range(7):
        service.create(review(service, title=f"Review {n}"))
    ConversationManager(ws).begin("评审论文", "r1", workflow_mode="off")
    ConversationManager(ws).checkpoint(status="budget_exhausted")
    fresh = Workspace("paper", tmp_path)
    class Resume:
        def decide(self, obs):
            assert obs["workflow"] is None and obs["task"] is None
            assert obs["paper_reviews"]["total"] == 7 and obs["paper_reviews"]["omitted"] == 2
            assert "Population denominator" not in json.dumps(obs["paper_reviews"])
            return ChatReplyAction(text="继续按已保存的评审依据复核")
    assert reader(fresh, Resume()).run("继续").status == "finished"
    assert not (ws.dir / "tasks").exists()
    rid = service.read()["reports"][0]["report_id"]
    full = reader(fresh)._read_context(ReadContextAction(kind="paper_reviews", key=rid, max_chars=20000))
    assert "Population denominator" in full["content"]
    source = reader(fresh)._read_context(ReadContextAction(kind="paper_review_source", key=rid, resource_id="paper.md"))
    assert "We recruited participants" in source["content"]
    other = Workspace("other", tmp_path).init("other")
    assert reader(other)._read_context(ReadContextAction(kind="paper_reviews", key=rid))["status"] == "error"


def test_agent_can_save_review_and_propose_followup_with_existing_action_protocol(tmp_path):
    ws, service = setup(tmp_path)
    actions = [ReadDraftAction(filename="paper.md"), SavePaperReviewAction(**review(service).model_dump())]
    class Reviewer:
        def decide(self, obs):
            if actions:
                return actions.pop(0)
            return ChatReplyAction(text="评审已保存，当前只检查方法报告。")
    original = ws.read_draft("paper.md")
    assert reader(ws, Reviewer(), workflow_mode="off").run("评审论文，不修改正文").status == "finished"
    assert ws.read_draft("paper.md") == original
    report = service.read()["reports"][0]
    assert report["actor"] == "agent" and report["run_id"]
    assert reader(ws)._read_draft(ReadDraftAction(filename="paper.md"))["version"] == service.basis("paper.md")["version"]
    body = {"action": "record_review_followup", "report_id": report["report_id"], "issue_id": report["findings"][0]["issue_id"],
            "expected_revision": 1, "status": "not_verifiable", "reason": "Data are unavailable"}
    action = TypeAdapter(AgentAction).validate_python(body)
    result = reader(ws)._execute(action, "复核")
    assert result["adoption"] == "proposed"


def test_http_review_reporting_followups_and_idle_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent.server import app, RUNS
    ws, service = setup(tmp_path)
    with TestClient(app) as client:
        base = "/api/v1/projects/paper/paper-reviews"
        assert client.get("/paper-review.js").status_code == 200
        assert client.get(base).json()["reports"] == []
        assert client.get(base + "/basis", params={"filename": "../outside.md"}).status_code == 422
        response = client.post(base, json=review(service).model_dump())
        assert response.status_code == 200, response.text
        row = response.json()
        rid, iid = row["report_id"], row["findings"][0]["issue_id"]
        assert client.get(base + f"/{rid}/report").text.startswith("# Methods review")
        ws.write_draft("paper.md", "We recruited 40 participants.")
        result = client.post(base + f"/{rid}/issues/{iid}", json=correction(service).model_dump())
        assert result.status_code == 200 and result.json()["adoption"] == "accepted"
        assert client.post(base + f"/{rid}/issues/{iid}", json=correction(service).model_dump()).status_code == 422
        assert client.get("/api/v1/projects/missing/paper-reviews").status_code == 404
        monkeypatch.setitem(RUNS, "review-live-test", SimpleNamespace(project_id="paper", done=Event()))
        assert client.post(base, json=review(service).model_dump()).status_code == 409
        assert client.get(base).status_code == 200
