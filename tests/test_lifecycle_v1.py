"""Lifecycle V1 acceptance: contracts, real persistence, approval and routing."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperagent.domain.lifecycle import LifecycleService
from paperagent.domain.research_links import ResearchLinks
from paperagent.domain.store import DomainStore
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy
from paperagent.skills.loader import SkillLoader
from paperagent.tasks.proactive import ProactiveRuntime
from paperagent.tasks.watcher import WatcherManager
from paperagent.workflows.catalog import WORKFLOW_CATALOG, workflow_skill_recommendations
from paperagent.workflows.registry import select_workflow
from paperagent.workflows.runtime import WorkflowRuntime
from paperagent.workspace.workspace import Workspace


def space(root, kind="thesis", identifier="a"):
    ws = Workspace(identifier, root).init("研究")
    LifecycleService(ws).save(lifecycle_type=kind, stage="topic", reason="作者建立研究")
    return ws


@pytest.mark.parametrize("spec", WORKFLOW_CATALOG, ids=lambda w: w["code"])
def test_ten_contracts_can_enter_at_any_real_stage_and_persist_independently(tmp_path, spec):
    ws = space(tmp_path, spec["lifecycle_types"][0])
    before = (ws.dir / "lifecycle.json").read_bytes()
    selected = select_workflow(ws, "整理本次研究材料", spec["code"])
    assert selected["canonical_workflow_id"] == spec["id"]
    assert selected["workflow_contract"]["inputs"] and selected["playbook"]["steps"]
    runtime = WorkflowRuntime(ws)
    case = runtime.start(spec["id"])
    last_step = spec["steps"][-1]["id"]
    case = runtime.update(case["case_id"], "step", {"step_id": last_step, "status": "active"})
    assert case["steps"][spec["steps"][0]["id"]]["status"] == "pending"
    runtime.update(case["case_id"], "status", {"status": "paused"}, actor="user")
    fresh = WorkflowRuntime(Workspace(ws.project_id, tmp_path))
    assert fresh.get(case["case_id"])["status"] == "paused"
    with pytest.raises(ValueError, match="Resume"):
        fresh.update(case["case_id"], "step", {"step_id": last_step, "status": "done"})
    fresh.update(case["case_id"], "status", {"status": "active"}, actor="user")
    fresh.update(case["case_id"], "step", {"step_id": last_step, "status": "pending"})
    assert (ws.dir / "lifecycle.json").read_bytes() == before
    assert not (ws.dir / "tasks").exists()
    for recommendation in workflow_skill_recommendations(spec["id"]):
        assert SkillLoader(tmp_path).resolve(recommendation["skill_id"]).id == recommendation["skill_id"]


def test_formal_delivery_cannot_skip_author_decisions_quality_or_current_version(tmp_path):
    ws = space(tmp_path)
    ws.write_draft("report.md", "# 中期报告\n实际进度由作者核对。")
    runtime = WorkflowRuntime(ws)
    case = runtime.start("T2", required_deliverables=["中期检查报告"])
    case_id = case["case_id"]
    with pytest.raises(ValueError, match="Missing"):
        runtime.update(case_id, "complete")
    for subject in case["decisions"]:
        case = runtime.update(case_id, "decision", {"subject": subject, "content": "作者提供的具体情况"})
        with pytest.raises(ValueError, match="Agent proposals"):
            runtime.update(case_id, "approve", {"subject": subject})
        case = runtime.update(case_id, "approve", {"subject": subject, "fingerprint": case["decision_fingerprints"][subject]}, actor="user")
    case = runtime.update(case_id, "output", {"label": "中期检查报告", "path": "drafts/report.md"})
    preserved = Path(case["outputs"][0]["version_path"])
    original = preserved.read_bytes()
    case = runtime.update(case_id, "quality", {"issues": [{"level": "ERROR", "message": "缺少真实进展证据"}]})
    case = runtime.update(case_id, "approve", {"subject": "final_delivery", "fingerprint": case["fingerprint"]}, actor="user")
    with pytest.raises(ValueError, match="ERROR"):
        runtime.update(case_id, "complete")
    case = runtime.update(case_id, "quality", {"issues": []})
    with pytest.raises(ValueError, match="current formal"):
        runtime.update(case_id, "complete")
    case = runtime.update(case_id, "approve", {"subject": "final_delivery", "fingerprint": case["fingerprint"]}, actor="user")
    assert runtime.update(case_id, "complete")["status"] == "completed"
    ws.write_draft("report.md", "# 已修改的报告")
    changed = runtime.get(case_id)
    assert changed["needs_review"] and not changed["final_approved"]
    assert preserved.read_bytes() == original
    with pytest.raises(ValueError, match="reload"):
        runtime.update(case_id, "status", {"status": "active"}, actor="user", expected_revision=1)


def test_case_http_and_agent_tool_keep_approvals_and_workspaces_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERAGENT_SCHEDULER", "off")
    from paperagent.server import app
    ws = space(tmp_path)
    space(tmp_path, identifier="b")
    with TestClient(app) as client:
        catalog = client.get("/api/v1/lifecycles").json()
        assert len(catalog["proactive_services"]) == 3
        assert sum(len(c["workflows"]) for c in catalog["lifecycles"]) == 10
        endpoint = "/api/v1/projects/a/workflow-cases"
        created = client.post(endpoint, json={"workflow_id": "T1"})
        assert created.status_code == 200
        case = created.json()
        assert client.get("/api/v1/projects/b/workflow-cases/" + case["case_id"]).status_code == 404
        assert client.patch(endpoint + "/" + case["case_id"], json={"operation": "approve", "data": {"subject": "选题"}, "expected_revision": case["revision"]}).status_code == 422
        assert client.post(endpoint, json={"workflow_id": "G1"}).status_code == 422
    executor = ToolExecutor(ResearchPolicy(), ws)
    for operation in ("approve", "complete", "status"):
        result = executor.execute(ToolCall(tool_id="workflow_case", input={"operation": operation, "case_id": case["case_id"], "expected_revision": 1}))
        assert result.status == "error"


def test_removed_skills_are_aliases_only_and_old_resources_survive(tmp_path):
    loader = SkillLoader(tmp_path)
    ids = {s.id for s in loader.load_all()}
    assert len(ids) == 35
    for old, new in loader.alias_metadata()["aliases"].items():
        assert old not in ids
        assert loader.resolve("builtin:" + old + "@1.0").id == new
    old = loader.read_resource("builtin:english_academic_writing@1.0", "argument")
    assert "references\\english\\argument.md" in old["source"] or "references/english/argument.md" in old["source"]
    with pytest.raises(ValueError, match="not found"):
        loader.resolve("workspace:english_academic_writing@1.0", "a")
    thesis_ids = {s.id for s in loader.select(goal="毕业论文中期报告", lifecycle_type="thesis", limit=35)}
    assert "thesis_midterm_report" in thesis_ids and "grant_midterm_report" not in thesis_ids
    neutral = loader.select(goal="你好", lifecycle_type="thesis", limit=35)
    assert all(not s.scope.get("discipline") for s in neutral)


def test_reuse_pins_source_version_without_transferring_lifecycle_or_approval(tmp_path):
    grant = space(tmp_path, "project", "grant")
    thesis = space(tmp_path, "thesis", "thesis")
    grant.write_draft("research.md", "# 已有研究\n这里是可复用的研究背景。")
    before = (thesis.dir / "lifecycle.json").read_bytes()
    links = ResearchLinks(thesis)
    linked = links.link("grant", "research.md")
    assert not linked["approval_transfer"] and not links.view()[0]["needs_review"]
    preserved = Path(linked["local_path"]).read_text(encoding="utf-8")
    grant.write_draft("research.md", "# 新版本")
    assert links.view()[0]["needs_review"]
    assert Path(linked["local_path"]).read_text(encoding="utf-8") == preserved
    assert (thesis.dir / "lifecycle.json").read_bytes() == before


def test_opportunity_radar_is_opt_in_deduplicated_and_isolates_failed_sources(tmp_path):
    store = DomainStore(tmp_path)
    watcher = WatcherManager(store).create(name="教育项目", watcher_type="grant", source="user", query={
        "service": "opportunity_radar", "project_id": "a", "urls": ["https://example.org/fail", "https://example.org/notice"], "interval_seconds": 60})
    calls = []
    def fetch(input):
        calls.append(input["url"])
        if input["url"].endswith("fail"):
            raise TimeoutError("offline source")
        return {"url": input["url"], "html": "<title>教育项目申报通知</title><p>教育课题申报，截止 2027-01-31。</p>"}
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    runtime = ProactiveRuntime(store, fetch=fetch)
    assert len(runtime.tick(now)) == 1
    assert len(store.list_opportunities()) == 1
    assert len(store.list_notifications()) == 1
    assert not runtime.tick(now + timedelta(seconds=10))
    runtime.tick(now + timedelta(seconds=61))
    assert len(store.list_notifications()) == 1
    store.update_watcher(watcher.watcher_id, enabled=False)
    before = len(calls)
    runtime.tick(now + timedelta(seconds=125))
    assert len(calls) == before


def test_progress_feedback_only_notifies_project_changes_once(tmp_path):
    from paperagent.tasks.trigger_engine import TriggerEngine
    store = DomainStore(tmp_path)
    WatcherManager(store).create(name="我的进度", watcher_type="external", source="user", query={"service": "progress_watcher", "project_id": "a", "interval_seconds": 60})
    engine = TriggerEngine(store)
    engine.record_external("advisor", {"project_id": "b", "text": "另一个项目"})
    engine.record_external("advisor", {"project_id": "a", "text": "请补充方法说明"})
    runtime = ProactiveRuntime(store)
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    runtime.tick(now)
    runtime.tick(now + timedelta(seconds=61))
    assert len(store.list_notifications()) == 1


def test_finish_registers_draft_version_but_cannot_complete_the_author_workflow(tmp_path):
    from paperagent.agent.decision_loop import DecisionLoop
    from paperagent.schemas.actions import WriteDraftAction, FinishAction
    ws = space(tmp_path)
    runtime = WorkflowRuntime(ws)
    case = runtime.start("T2", required_deliverables=["中期检查报告"], scope="partial")
    actions = [WriteDraftAction(filename="report.md", content="# 中期报告草稿\n实际进展待作者核对。"), FinishAction(summary="已保存供核对。")]
    class Writer:
        def decide(self, observation):
            return actions.pop(0)
    outcome = DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=2), ws), Writer(),
                           workflow_id="thesis_midterm_report", log=lambda _: None).run("先起草报告")
    assert outcome.status == "finished", outcome.summary
    saved = runtime.get(case["case_id"])
    assert saved["outputs"][0]["label"] == "中期检查报告"
    assert Path(saved["outputs"][0]["version_path"]).is_file()
    assert saved["quality_reviewed"] and saved["status"] == "active" and not saved["final_approved"]
    assert LifecycleService(ws).get().stage == "topic"


def test_defense_presentation_includes_editable_pptx_notes_and_manifest(tmp_path):
    from pptx import Presentation
    from paperagent.tools.research_delivery import run_presentation, run_check
    ws = space(tmp_path)
    result = run_presentation({"title": "答辩测试", "slides": [
        {"title": "研究问题", "points": ["解释本研究的问题与边界"], "notes": "这是一份测试讲稿。"}]}, ws)
    deck = Presentation(result["pptx_path"])
    assert len(deck.slides) == 1
    assert "研究问题" in "\n".join(s.text for s in deck.slides[0].shapes if s.has_text_frame)
    assert "测试讲稿" in deck.slides[0].notes_slide.notes_text_frame.text
    assert not run_check({"manifest_path": result["manifest_path"]}, ws)["needs_review"]


def test_email_status_requires_identity_and_does_not_cross_research_spaces(tmp_path):
    from paperagent.tasks.submission_manager import SubmissionManager
    store = DomainStore(tmp_path)
    manager = SubmissionManager(store)
    a = manager.create(title="A", manuscript_id="ID-1", journal_name="Journal", project_id="a")
    b = manager.create(title="B", manuscript_id="ID-1", journal_name="Journal", project_id="b")
    with pytest.raises(ValueError, match="manuscript ID"):
        manager.apply_email("Status: Accepted")
    with pytest.raises(ValueError, match="Ambiguous"):
        manager.apply_email("Manuscript ID: ID-1\nStatus: Under Review")
    result = manager.apply_email("Manuscript ID: ID-1\nStatus: Under Review", project_id="a")
    assert result.submission_id == a.submission_id
    assert next(s for s in store.list_submissions() if s.submission_id == b.submission_id).status == "submitted"
