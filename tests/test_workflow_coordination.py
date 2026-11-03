"""Ten business entries remain independent of stages, skills and execution order."""
import json

import pytest
from fastapi.testclient import TestClient

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.conversation import ConversationManager, select_conversation_workflow
from paperagent.domain.lifecycle import LifecycleService
from paperagent.domain.manuscript import ManuscriptContext, ManuscriptRecordInput
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.actions import ChatReplyAction, ReadContextAction
from paperagent.schemas.policy import ResearchPolicy
from paperagent.skills.context import workflow_skill_metadata
from paperagent.skills.loader import SkillLoader
from paperagent.workflows.catalog import WORKFLOW_CATALOG, describe_lifecycles, workflow_spec
from paperagent.workflows.registry import select_workflow
from paperagent.workspace.workspace import Workspace


def space(root, kind="paper_conference", stage="submission"):
    ws = Workspace(kind, root).init("科研研究")
    LifecycleService(ws).save(lifecycle_type=kind, stage=stage, reason="研究空间背景")
    return ws


def test_catalog_has_ten_entries_with_independent_stage_suggestions():
    catalog = {c["lifecycle_type"]: c for c in describe_lifecycles()}
    assert len(WORKFLOW_CATALOG) == 10
    assert [w["id"] for w in catalog["paper_conference"]["workflows"]] == ["paper_writing", "venue_matching", "submission_tracking"]
    assert [w["id"] for w in catalog["project"]["workflows"]] == ["grant_application", "project_midterm_report", "project_final_report"]
    assert [w["id"] for w in catalog["thesis"]["workflows"]] == ["thesis_proposal", "thesis_midterm_report", "thesis_writing", "thesis_defense"]
    assert all(c["ordered"] is False for c in catalog.values())
    assert next(s for s in catalog["project"]["stages"] if s["id"] == "topic")["suggested_workflows"] == ["grant_application"]


@pytest.mark.parametrize("identifier", [w["id"] for w in WORKFLOW_CATALOG])
def test_every_entry_works_without_changing_lifecycle_or_creating_a_task(tmp_path, identifier):
    spec = workflow_spec(identifier)
    ws = space(tmp_path, spec["lifecycle_types"][0], "archive" if spec["lifecycle_types"][0] != "thesis" else "blind_review")
    before = (ws.dir / "lifecycle.json").read_bytes()
    workflow = select_workflow(ws, "先整理现有材料", identifier)
    assert workflow["workflow_id"] == identifier and workflow["canonical_workflow_id"] == identifier
    assert not workflow["legacy_compatibility"]
    assert workflow["execution_policy"]["allow_skip"] and workflow["execution_policy"]["allow_replan"]
    assert not workflow["execution_policy"]["ordered_steps"]
    assert not workflow["execution_policy"]["auto_advance_lifecycle"]
    assert (ws.dir / "lifecycle.json").read_bytes() == before
    metadata = workflow_skill_metadata(ws, workflow)
    assert metadata and all("procedure" not in s for s in metadata)
    for row in metadata:
        SkillLoader(tmp_path).resolve(row["key"], ws.project_id)
    if spec["implementation"] == "coordination":
        assert not workflow["quality_gates"]
        assert workflow["deliverable_contracts"]["requested"] == []
        assert not workflow["drafting_contract"]["full_document_required"]
        assert not workflow["drafting_contract"]["write_section_by_section"]
        assert workflow["capability_status"]["available_capabilities"]
    assert not (ws.dir / "tasks").exists()


@pytest.mark.parametrize("kind,goal,expected", [
    ("paper_conference", "撰写完整期刊论文", "paper_writing"),
    ("paper_conference", "帮我管理投稿与评审", "submission_tracking"),
    ("paper_conference", "寻找近期投稿机会", "venue_matching"),
    ("project", "寻找项目机会", "project_opportunity_discovery"),
    ("thesis", "准备完整学位论文", "thesis_writing"),
    ("thesis", "制作答辩PPT", "thesis_defense"),
])
def test_explicit_business_intent_can_use_new_entry(tmp_path, kind, goal, expected):
    ws = space(tmp_path, kind, "topic")
    assert select_conversation_workflow(ws, goal, {})["workflow_id"] == expected
    assert select_conversation_workflow(ws, goal, {"workflow_mode":"off"}) is None


@pytest.mark.parametrize("goal", ["只修改论文摘要", "评审这篇论文", "解释投稿管理是什么", "分析拒稿原因", "只改答辩讲稿"])
def test_local_work_still_uses_skills_without_automatic_workflow(tmp_path, goal):
    ws = space(tmp_path)
    assert select_conversation_workflow(ws, goal, {}) is None
    explicit = select_conversation_workflow(ws, goal, {"workflow_id":"publication_management"})
    assert explicit["workflow_id"] == "publication_management"


@pytest.mark.parametrize("kind,legacy,canonical", [
    ("paper_conference", "paper_conference_drafting", "paper_writing"),
    ("paper_conference", "paper_conference_revision", "submission_tracking"),
    ("thesis", "thesis_blind_review", "thesis_defense"),
    ("project", "project_topic", "project_opportunity_discovery"),
])
def test_legacy_ids_keep_identity_and_can_read_current_family_advice(tmp_path, kind, legacy, canonical):
    ws = space(tmp_path, kind, "topic")
    manager = ConversationManager(ws)
    manager.begin("整理材料", "old-run", workflow_id=legacy)
    manager.checkpoint(status="budget_exhausted")
    fresh = Workspace(kind, tmp_path)
    ConversationManager(fresh).begin("继续", "new-run", resume=True)
    state = assemble_assistant_state(fresh, "继续", 1, 5, 10, None, None, [])
    workflow = state.current_context.workflow
    assert workflow["workflow_id"] == legacy
    assert workflow["canonical_workflow_id"] == canonical and workflow["legacy_compatibility"]
    assert workflow["skill_recommendations"]
    assert ConversationManager(fresh).get().workflow_id == legacy


def test_new_coordination_reuses_paper_knowledge_and_resume_without_ordered_skill_calls(tmp_path):
    ws = space(tmp_path)
    context = ManuscriptContext(ws)
    context.record(ManuscriptRecordInput(kind="term", title="Population", term={"preferred":"participants"},
        sources=[{"kind":"user_note", "key":"Use participants"}], reason="Author terminology"))
    manager = ConversationManager(ws)
    manager.begin("写论文的引言", "r1", workflow_id="paper_writing")
    manager.update(plan={"next":"核对一个段落"}, decisions=["保留推断边界"])
    manager.checkpoint(status="budget_exhausted")
    class Reply:
        def decide(self, obs):
            assert obs["workflow"]["workflow_id"] == "paper_writing"
            assert obs["conversation"]["plan"]["next"] == "核对一个段落"
            assert obs["manuscript"]["records"][0]["title"] == "Population"
            assert obs["task"] is None
            return ChatReplyAction(text="本轮先讨论段落，不要求执行完整写作清单。")
    fresh = Workspace(ws.project_id, tmp_path)
    outcome = DecisionLoop(fresh, ToolExecutor(ResearchPolicy(max_iterations=2), fresh), Reply(), log=lambda _:None).run("继续")
    assert outcome.status == "finished" and not (ws.dir / "tasks").exists()
    # Switching coordination preserves paper knowledge and real-world stage.
    manager.begin("整理投稿情况", "r3", workflow_id="publication_management")
    assert context.context()["records"][0]["title"] == "Population"
    assert LifecycleService(ws).get().stage == "submission"


def test_explicit_scope_and_missing_capabilities_are_not_silently_rewritten(tmp_path):
    ws = space(tmp_path)
    workflow = select_workflow(ws, "只改摘要", "paper_writing", {"scope":"full", "sections":["作者指定部分"]})
    assert workflow["execution_policy"]["scope"] == "full"
    assert workflow["drafting_contract"]["full_document_required"]
    assert workflow["deliverable_contracts"]["requested"] == ["作者指定部分"]
    publication = select_workflow(ws, "整理投稿", "publication_management")
    assert publication["canonical_workflow_id"] == "submission_tracking"
    assert "publication_strategy" not in {s["skill_id"] for s in publication["skill_recommendations"]}
    with pytest.raises(ValueError, match="different lifecycle"):
        select_workflow(ws, "整理材料", "thesis_writing")


def test_http_all_recommendations_are_visible_and_preview_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent.server import app
    ws = space(tmp_path)
    before = (ws.dir / "lifecycle.json").read_bytes()
    (ws.dir / "skills").mkdir()
    (ws.dir / "skills/research.yaml").write_text(
        'id: research_design\nversion: "1.0"\nprocedure: [PRIVATE_METHOD_BODY]\n', encoding="utf-8")
    with TestClient(app) as client:
        catalog = client.get("/api/v1/lifecycles").json()["lifecycles"]
        assert sum(len(c["workflows"]) for c in catalog) == 10
        base = f"/api/v1/projects/{ws.project_id}/workflows/preview"
        preview = client.post(base, json={"goal":"整理材料", "workflow_id":"paper_writing", "workflow_mode":"auto"})
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert len(data["skills"]) <= 6 and len(data["workflow_skills"]) == 13
        assert "procedure" not in json.dumps(data["workflow_skills"])
        private = next(s for s in data["workflow_skills"] if s["id"] == "research_design")
        assert private["key"] == "workspace:research_design@1.0"
        assert "PRIVATE_METHOD_BODY" not in json.dumps(data["workflow_skills"])
        off = client.post(base, json={"goal":"撰写完整论文", "workflow_mode":"off"}).json()
        assert off["workflow"] is None and off["workflow_skills"] == []
        assert client.post(base, json={"goal":"写", "workflow_id":"thesis_defense"}).status_code == 422
        assert (ws.dir / "lifecycle.json").read_bytes() == before
        assert not (ws.dir / "conversations").exists()
