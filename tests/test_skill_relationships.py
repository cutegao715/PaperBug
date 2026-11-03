"""Shared capabilities, local customization and advisory workflow discovery."""
import json

import pytest
import yaml
from fastapi.testclient import TestClient

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.conversation import ConversationManager
from paperagent.domain.lifecycle import LifecycleService
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.actions import ChatReplyAction, ReadContextAction
from paperagent.schemas.policy import ResearchPolicy
from paperagent.skills.loader import SkillLoader
from paperagent.skills.context import workflow_skill_metadata
from paperagent.workflows.catalog import WORKFLOW_CATALOG, workflow_skill_recommendations
from paperagent.workspace.workspace import Workspace


def skill_file(directory, identifier="custom_review", version="1.0", **extra):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identifier}-{version}.yaml"
    path.write_text(yaml.safe_dump({
        "id": identifier, "version": version, "layer": "task",
        "description": "按需核查案例材料", "triggers": ["案例核查"],
        "procedure": ["FULL_METHOD_ONLY_ON_READ"], **extra,
    }, allow_unicode=True), encoding="utf-8")
    return path


def reader(ws):
    return DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), object(), log=lambda _: None)


def test_source_precedence_is_independent_of_legacy_layer_and_explicitly_reported(tmp_path):
    shared = tmp_path / "skills"
    local = tmp_path / "projects" / "a" / "skills"
    skill_file(shared, "academic_writing", layer="project", procedure=["shared method"])
    skill_file(local, "academic_writing", layer="general", procedure=["local method"])
    loader = SkillLoader(tmp_path)
    effective = loader.resolve("academic_writing@1.0", "a")
    assert effective.procedure == ["local method"]
    assert effective.source_kind == "workspace" and effective.source_project_id == "a"
    assert effective.shadowed_sources == ["builtin:academic_writing@1.0", "shared:academic_writing@1.0"]
    # Exact source keys remain readable even after a more local variant is added.
    assert loader.resolve("shared:academic_writing@1.0", "a").procedure == ["shared method"]
    assert loader.resolve("builtin:academic_writing@1.0", "a").source_kind == "builtin"
    assert loader.resolve("academic_writing@1.0", "b").source_kind == "shared"


def test_versions_are_explicit_and_same_source_duplicates_are_rejected(tmp_path):
    skill_file(tmp_path / "skills", version="1.0")
    skill_file(tmp_path / "skills", version="2.0")
    loader = SkillLoader(tmp_path)
    with pytest.raises(ValueError, match="Ambiguous"):
        loader.resolve("custom_review")
    assert loader.resolve("custom_review@2.0").version == "2.0"
    skill_file(tmp_path / "skills" / "duplicate", version="2.0")
    with pytest.raises(ValueError, match="Duplicate skill key"):
        loader.load_all()


def test_private_skills_stay_in_workspace_and_metadata_does_not_inject_body(tmp_path):
    a = Workspace("a", tmp_path).init("研究")
    b = Workspace("b", tmp_path).init("研究")
    skill_file(a.dir / "skills", source_kind="builtin", source_project_id="b")
    loader = SkillLoader(tmp_path)
    metadata = loader.resolve("custom_review", "a").metadata_view()
    assert metadata["source_kind"] == "workspace" and metadata["source_project_id"] == "a"
    assert "FULL_METHOD_ONLY_ON_READ" not in json.dumps(metadata)
    result = reader(a)._read_context(ReadContextAction(kind="skill", key=metadata["read_with"]["key"]))
    assert "FULL_METHOD_ONLY_ON_READ" in result["content"]
    assert reader(b)._read_context(ReadContextAction(kind="skill", key=metadata["key"]))["status"] == "error"
    with pytest.raises(ValueError, match="Invalid project"):
        loader.load_all("../a")


def test_catalog_can_discover_skills_outside_shortlist_without_reading_procedures(tmp_path):
    ws = Workspace("a", tmp_path).init("研究")
    skill_file(ws.dir / "skills")
    loop = reader(ws)
    first = loop._read_context(ReadContextAction(kind="skill_catalog", max_chars=500))
    assert first["next_offset"] == 500
    chunks = [first["content"]]
    while first["next_offset"] is not None:
        first = loop._read_context(ReadContextAction(kind="skill_catalog", offset=first["next_offset"], max_chars=20000))
        chunks.append(first["content"])
    catalog = json.loads("".join(chunks))
    assert any(s["key"] == "workspace:custom_review@1.0" for s in catalog)
    assert "FULL_METHOD_ONLY_ON_READ" not in "".join(chunks)
    search = loop._read_context(ReadContextAction(kind="skill_catalog", key="案例核查", max_chars=20000))
    assert "custom_review" in search["content"]


@pytest.mark.parametrize("workflow_id", [w["id"] for w in WORKFLOW_CATALOG])
def test_workflow_recommendations_are_resolvable_optional_metadata(tmp_path, workflow_id):
    ws = Workspace("a", tmp_path).init("科研项目")
    spec = next(w for w in WORKFLOW_CATALOG if w["id"] == workflow_id)
    kind = spec["lifecycle_types"][0]
    LifecycleService(ws).save(lifecycle_type=kind, stage="topic", reason="测试")
    ConversationManager(ws).begin("整理材料", "r1", workflow_id=workflow_id)
    state = assemble_assistant_state(ws, "整理材料", 1, 8, 10, None, None, [])
    workflow = state.current_context.workflow
    recommendations = workflow["skill_recommendations"]
    assert recommendations and all(r["optional"] and r["when"] for r in recommendations)
    assert workflow["execution_policy"]["ordered_steps"] is False
    assert workflow["execution_policy"]["skill_recommendations_required"] is False
    assert workflow["execution_policy"]["allow_other_skills"] is True
    for recommendation in recommendations:
        SkillLoader(tmp_path).resolve(f"{recommendation['skill_id']}@{recommendation['version']}", "a")
    recommended_ids = {r["skill_id"] for r in recommendations}
    assert recommended_ids == {s["id"] for s in workflow_skill_metadata(ws, workflow)}
    assert len(state.current_context.skills) <= 6
    assert all("procedure" not in s for s in state.current_context.skills)
    assert state.current_context.lifecycle["current_stage"] == "topic"


def test_workflow_off_still_allows_shared_and_local_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "100000")
    ws = Workspace("a", tmp_path).init("科研项目")
    skill_file(ws.dir / "skills")
    ConversationManager(ws).begin("案例核查，使用 project_evidence_review", "r1", workflow_mode="off")
    state = assemble_assistant_state(ws, "案例核查，使用 project_evidence_review", 1, 8, 10, None, None, [])
    assert state.current_context.workflow is None
    assert {"custom_review", "project_evidence_review"} <= {s["id"] for s in state.current_context.skills}
    assert all(not s["workflow_recommendations"] for s in state.current_context.skills)
    assert not (ws.dir / "tasks").exists()


def test_resume_rediscovers_current_skills_and_preserves_workspace_context(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "100000")
    ws = Workspace("a", tmp_path).init("科研项目")
    manager = ConversationManager(ws)
    manager.begin("整理材料", "r1", workflow_id="project_midterm_report")
    manager.update(decisions=["研究已调整为案例比较"], plan={"next": "核查进展"})
    skill_file(ws.dir / "skills", "grant_midterm_report", layer="general")
    resumed = Workspace("a", tmp_path)
    ConversationManager(resumed).begin("继续", "r2", resume=True)
    state = assemble_assistant_state(resumed, "继续", 1, 8, 10, None, None, [])
    assert state.current_context.conversation["decisions"] == ["研究已调整为案例比较"]
    assert state.current_context.workflow["workflow_id"] == "project_midterm_report"
    selected = next(s for s in state.current_context.skills if s["id"] == "grant_midterm_report")
    assert selected["source_kind"] == "workspace"
    assert selected["workflow_recommendations"]


def test_workflow_does_not_require_recommended_skill_calls_or_sequence(tmp_path):
    ws = Workspace("a", tmp_path).init("科研项目")
    class Agent:
        def __init__(self): self.calls = 0
        def decide(self, obs):
            self.calls += 1
            if self.calls == 1:
                assert obs["workflow"]["skill_recommendations"]
                return ReadContextAction(kind="skill", key="builtin:literature_review@1.0")
            return ChatReplyAction(text="本轮只讨论文献，无需运行报告写作方法。")
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=3), ws), Agent(),
                        workflow_id="project_midterm_report", log=lambda _: None)
    assert loop.run("先讨论已有材料").status == "finished"
    assert not (ws.dir / "tasks").exists()


def test_http_metadata_preview_detail_and_workspace_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "100000")
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent.server import app
    ws = Workspace("a", tmp_path).init("科研项目")
    Workspace("b", tmp_path).init("另一项目")
    skill_file(ws.dir / "skills", "grant_midterm_report")
    with TestClient(app) as client:
        listing = client.get("/api/v1/skills", params={"project_id": "a"}).json()["skills"]
        assert all("procedure" not in s for s in listing)
        preview = client.post("/api/v1/projects/a/workflows/preview", json={
            "goal": "整理材料", "workflow_id": "project_midterm_report", "workflow_mode": "auto",
        }).json()
        local = next(s for s in preview["skills"] if s["id"] == "grant_midterm_report")
        assert local["source_kind"] == "workspace" and local["workflow_recommendations"]
        key = local["key"]
        assert client.get(f"/api/v1/skills/{key}", params={"project_id": "a"}).json()["skill"]["procedure"] == ["FULL_METHOD_ONLY_ON_READ"]
        assert client.get(f"/api/v1/skills/{key}", params={"project_id": "b"}).status_code == 404
        assert client.get("/api/v1/skills", params={"project_id": "../a"}).status_code == 404
        off = client.post("/api/v1/projects/a/workflows/preview", json={"goal": "写中期报告", "workflow_mode": "off"}).json()
        assert off["workflow"] is None and off["skills"]
        assert all(not s["workflow_recommendations"] for s in off["skills"])
        ConversationManager(ws).begin("整理材料", "r1", workflow_id="project_midterm_report")
        detail = client.get("/api/v1/projects/a").json()
        assert detail["skills"] == preview["skills"]
        context = client.get("/internal/projects/a/context").json()
        assert context["current_context"]["skills"] == detail["skills"]
