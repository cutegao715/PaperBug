import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.agent.llm_agent import LLMAgent
from paperagent.domain.lifecycle import LifecycleService
from paperagent.domain.store import DomainStore
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.harness.context_budget import ContextBudgetExceeded, fit_context
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.actions import (ChatReplyAction, ReadContextAction, ReadDraftAction,
                                       UpdateTaskAction, WriteDraftAction)
from paperagent.schemas.policy import ResearchPolicy
from paperagent.tasks.memory_manager import MemoryManager
from paperagent.tasks.session import SessionManager
from paperagent.workflows.catalog import describe_lifecycles
from paperagent.workflows.registry import select_workflow
from paperagent.workspace.workspace import Workspace


def observe(ws, goal="继续", store=None):
    return assemble_assistant_state(ws, goal, 1, 10, 10, None, None, [], store=store)


def test_explicit_lifecycle_survives_old_materials_and_task_stage_changes(tmp_path):
    ws = Workspace("study", tmp_path).init("研究课题")
    ws.upsert_material(str(tmp_path / "课题申请书.docx"), "imported")
    service = LifecycleService(ws)
    service.save(lifecycle_type="thesis", stage="midterm", reason="硕士论文", focus_stages=["drafting"])
    workflow = select_workflow(ws, "准备盲审")
    assert workflow["workflow_id"] == "thesis_blind_review"
    assert workflow["lifecycle"]["current_stage"] == "midterm"
    assert service.get().stage == "midterm"
    service.save(lifecycle_type="thesis", stage="proposal", reason="回到开题修改")
    restarted = LifecycleService(Workspace("study", tmp_path)).get()
    assert restarted.stage == "proposal"
    assert len(restarted.history) == 2


@pytest.mark.parametrize("kind", ["project", "paper_conference", "thesis"])
def test_every_catalog_workflow_selectable_at_any_stage(tmp_path, kind):
    ws = Workspace(kind, tmp_path).init("research")
    LifecycleService(ws).save(lifecycle_type=kind, stage="topic", reason="选择类型")
    catalog = next(c for c in describe_lifecycles() if c["lifecycle_type"] == kind)
    for stage in catalog["stages"]:
        workflow = select_workflow(ws, "继续处理材料", stage["workflow_id"])
        assert workflow["workflow_id"] == stage["workflow_id"]
        assert workflow["execution_policy"]["ordered_steps"] is False
        assert LifecycleService(ws).get().stage == "topic"


def test_invalid_stage_and_cross_lifecycle_workflow_rejected(tmp_path):
    ws = Workspace("paper", tmp_path).init("paper")
    service = LifecycleService(ws)
    with pytest.raises(ValueError):
        service.save(lifecycle_type="thesis", stage="application", reason="bad")
    assert service.get() is None
    service.save(lifecycle_type="paper_conference", stage="revision", reason="start here")
    with pytest.raises(ValueError):
        select_workflow(ws, "continue", "thesis_defense")


def test_partial_request_does_not_block_on_full_application(tmp_path):
    ws = Workspace("grant", tmp_path).init("课题申请书")
    workflow = select_workflow(ws, "只修改申请书摘要")
    assert workflow["execution_policy"]["scope"] == "partial"
    from paperagent.kernel.application_completeness import check_application_completeness
    report = check_application_completeness("# 申请书\n摘要", "申请书.md")
    assert not DecisionLoop._application_kernel_should_block(workflow, report)
    assert DecisionLoop._workflow_short_draft_blockers(workflow, report) == []


def test_memory_replacement_delete_expiry_and_chinese_relevance(tmp_path):
    manager = MemoryManager(DomainStore(tmp_path))
    old = manager.remember("project", "导师建议使用问卷调查", scope={"project_id": "p"},
                           source={"type": "user", "message": "m1"}, kind="decision")
    new = manager.replace(old.memory_id, "导师决定使用案例研究", source={"type": "user", "message": "m2"})
    manager.remember("project", "午饭安排", scope={"project_id": "p"})
    manager.remember("project", "案例研究旧规则", scope={"project_id": "p"},
                     expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    rows = manager.relevant(project_id="p", goal="按照导师的案例研究要求修改")["project"]
    assert [r["memory_id"] for r in rows] == [new.memory_id]
    assert rows[0]["supersedes"] == old.memory_id
    assert rows[0]["source"]["message"] == "m2"
    assert manager.forget(old.memory_id)
    assert manager.relevant(project_id="p", goal="案例研究")["project"] == []


def test_memory_filters_before_limit_and_isolates_task(tmp_path):
    manager = MemoryManager(DomainStore(tmp_path))
    for i in range(105):
        manager.remember("project", f"研究方法 {i}", scope={"project_id": "other"}, importance=1)
    own = manager.remember("project", "研究方法", scope={"project_id": "p"})
    manager.remember("working", "研究方法", scope={"project_id": "p", "task_id": "other-task"})
    rows = manager.relevant(project_id="p", goal="研究方法", task_id="my-task")
    assert [r["memory_id"] for r in rows["project"]] == [own.memory_id]
    assert rows["working"] == []


def test_project_context_does_not_import_other_project_or_global_tasks(tmp_path):
    ws = Workspace("p", tmp_path).init("research")
    store = DomainStore(tmp_path)
    for project in ("p", "other", None):
        store.add_task(title=str(project), project_id=project)
        store.add_deadline(title=str(project), scope=project, when="2099-01-01T00:00:00+00:00")
    state = observe(ws, store=store)
    assert [t["title"] for t in state.research_state.pending_tasks] == ["p"]
    assert [d["title"] for d in state.research_state.upcoming_deadlines] == ["p"]


def test_message_tail_survives_and_skills_are_loaded_on_demand(tmp_path):
    ws = Workspace("p", tmp_path).init("毕业论文")
    text = "说明" * 300 + "最终决定：取消问卷，只使用案例。"
    ws.append_message("user", text)
    state = observe(ws, "毕业论文写作")
    assert state.current_context.recent_messages[-1]["content"] == text
    skill = state.current_context.skills[0]
    assert "procedure" not in skill
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), None)
    result = loop._read_context(ReadContextAction(kind="skill", key=skill["id"]))
    assert result["status"] == "ok"
    assert "principles" in result["content"]


def test_context_budget_protects_request_and_reports_omissions(tmp_path):
    ws = Workspace("p", tmp_path).init("research")
    state = observe(ws, "保留这个目标")
    state.current_context.evidence_preview = [{"quote": "a" * 10000}] * 10
    limited = fit_context(state, input_budget=18000)
    assert limited.goal == "保留这个目标"
    assert "evidence_preview" in limited.current_context.context_diagnostics["omitted"]
    assert limited.current_context.context_diagnostics["estimated_input_tokens"] <= 18000
    state.goal = "不可静默删除的重要要求" * 20000
    with pytest.raises(ContextBudgetExceeded):
        fit_context(state, input_budget=18000)


def test_llm_receives_canonical_state_once(tmp_path):
    class Client:
        def chat_json(self, system, user):
            self.payload = json.loads(user)
            return {"action": "chat_reply", "text": "ok"}
    client = Client()
    ws = Workspace("p", tmp_path).init("research")
    LLMAgent(client).decide(observe(ws).to_observation())
    assert "assistant_state" not in client.payload
    assert "current_context" in client.payload
    assert "saved_references" not in client.payload


def test_large_result_is_retrievable_after_context_deferral(tmp_path):
    ws = Workspace("p", tmp_path).init("research")
    manager = SessionManager(ws)
    task = manager.begin("read", "run1")
    manager.checkpoint(task.task_id, step={"action": {"action": "read_context"},
        "result": {"status": "ok", "content": "长资料" * 20000}})
    state = observe(ws)
    state.current_context.last_result = {"status": "ok", "content": "长资料" * 20000}
    fitted = fit_context(state, input_budget=18000)
    action = ReadContextAction(**fitted.current_context.last_result["read_more"])
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), None, task_id=task.task_id)
    retrieved = loop._read_context(action)
    assert "长资料" in retrieved["content"]
    assert retrieved["next_offset"] is not None


def test_early_task_constraints_survive_many_followups(tmp_path):
    ws = Workspace("p", tmp_path).init("research")
    manager = SessionManager(ws)
    state = manager.begin("必须使用真实访谈资料", "r1")
    for i in range(7):
        manager.begin("继续", f"r{i+2}", task_id=state.task_id)
    assert manager.context(state.task_id)["instructions"][0] == "必须使用真实访谈资料"


def test_task_resume_after_reconstruction_and_new_task_plan_isolation(tmp_path):
    ws = Workspace("p", tmp_path).init("毕业论文")
    class InitialAgent:
        def __init__(self): self.n = 0
        def decide(self, obs):
            self.n += 1
            if self.n == 1:
                return UpdateTaskAction(plan={"next": "第二章"}, decisions=["取消问卷"])
            if self.n == 2:
                return WriteDraftAction(filename="论文.md", content="# 论文\n\n案例研究。")
            return ChatReplyAction(text="本次进展已保存")
    legacy = SessionManager(ws).create("先写案例研究论文")
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), InitialAgent(), task_id=legacy.task_id, log=lambda _: None)
    loop.run("先写案例研究论文")
    task_id = loop.task_id
    ws2 = Workspace("p", tmp_path)
    class ResumeAgent:
        def decide(self, obs):
            assert obs["task_checkpoint"]["decisions"] == ["取消问卷"]
            assert obs["task_checkpoint"]["plan"]["next"] == "第二章"
            assert obs["task_checkpoint"]["target"]["filename"] == "论文.md"
            return ChatReplyAction(text="继续第二章")
    resumed = DecisionLoop(ws2, ToolExecutor(ResearchPolicy(), ws2), ResumeAgent(),
                           task_id=task_id, log=lambda _: None)
    assert resumed.run("继续").status == "finished"
    assert len(SessionManager(ws2).get(task_id).run_ids) == 2
    class NewAgent:
        def decide(self, obs):
            assert obs["task_checkpoint"]["plan"] == {}
            assert obs["draft_plan"] == {}
            assert obs["task_checkpoint"]["target"] is None
            return ChatReplyAction(text="新任务")
    legacy_new = SessionManager(ws2).create("另外一个任务")
    new = DecisionLoop(ws2, ToolExecutor(ResearchPolicy(), ws2), NewAgent(), task_id=legacy_new.task_id, log=lambda _: None)
    new.run("另外一个任务")
    assert new.task_id != task_id


def test_draft_versions_and_external_change_detection(tmp_path):
    ws = Workspace("p", tmp_path).init("research")
    ws.write_draft("paper.md", "# Introduction\noriginal\n## Method\ncase study")
    manager = SessionManager(ws)
    session = manager.begin("revise", "run1")
    manager.checkpoint(session.task_id, draft="paper.md")
    old = manager.get(session.task_id).target
    ws.replace_draft_section("paper.md", "Method", "new method")
    current = manager.context(session.task_id)["target"]
    assert current["changed"] is True
    from pathlib import Path
    assert "case study" in Path(old["snapshot_path"]).read_text(encoding="utf-8")
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), None)
    result = loop._read_draft(ReadDraftAction(filename="paper.md", heading="Method"))
    assert "new method" in result["content"]
    assert "original" not in result["content"]


def test_lifecycle_and_memory_http_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent.server import app, RUNS
    RUNS.clear()
    with TestClient(app) as client:
        assert len(client.get("/api/v1/lifecycles").json()["lifecycles"]) == 3
        bad = client.post("/api/v1/projects", json={"name": "bad", "lifecycle_type": "thesis", "stage": "application"})
        assert bad.status_code == 422
        assert not (tmp_path / "projects" / "bad").exists()
        result = client.post("/api/v1/projects", json={"name": "thesis", "lifecycle_type": "thesis", "stage": "blind_review"})
        pid = result.json()["project_id"]
        detail = client.get(f"/api/v1/projects/{pid}").json()
        assert detail["lifecycle"]["stage"] == "blind_review"
        preview = client.post(f"/api/v1/projects/{pid}/workflows/preview", json={"goal": "只改摘要"}).json()
        assert preview["workflow"]["execution_policy"]["scope"] == "partial"
        context = client.get(f"/internal/projects/{pid}/context", params={"goal": "准备盲审"})
        assert context.status_code == 200
        assert context.json()["current_context"]["lifecycle"]["current_stage"] == "blind_review"
        assert context.json()["current_context"]["context_diagnostics"]["input_budget"] > 0
        memory = client.post("/api/v1/memories", json={"layer": "project", "content": "使用问卷", "scope": {"project_id": pid}}).json()
        replacement = client.patch(f"/api/v1/memories/{memory['memory_id']}", json={"content": "取消问卷"}).json()
        assert replacement["supersedes"] == memory["memory_id"]
        assert client.delete(f"/api/v1/memories/{replacement['memory_id']}").status_code == 200
        assert client.get(f"/api/v1/projects/{pid}/memories").json()["memories"] == []
        assert client.get("/lifecycle.js").status_code == 200


def test_context_can_reload_reference_and_material_inventories(tmp_path):
    ws = Workspace("p", tmp_path).init("research")
    ws.upsert_material(str(tmp_path / "guide.md"), "imported")
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), None)
    materials = loop._read_context(ReadContextAction(kind="materials"))
    assert "guide.md" in materials["content"]
    references = loop._read_context(ReadContextAction(kind="references"))
    assert references["status"] == "ok"
    assert references["content"] == "[]"


def test_http_task_resume_is_explicit_and_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent.server import app, RUNS
    RUNS.clear()
    with TestClient(app) as client:
        pid = client.post("/api/v1/projects", json={"name": "paper", "lifecycle_type": "paper_conference"}).json()["project_id"]
        def run(task_id=None):
            body = {"text": "写论文", "mode": "offline"}
            if task_id: body["task_id"] = task_id
            response = client.post(f"/api/v1/projects/{pid}/messages", json=body)
            assert response.status_code == 200
            data = response.json()
            with client.stream("GET", f"/api/v1/runs/{data['run_id']}/events") as stream:
                list(stream.iter_lines())
            return data
        legacy = SessionManager(Workspace(pid, tmp_path)).create("写论文")
        first = run(legacy.task_id)
        task_id = first["task_id"]
        patch = client.patch(f"/api/v1/projects/{pid}/sessions/{task_id}", json={"decisions": ["只用真实数据"]})
        assert patch.status_code == 200
        RUNS.clear()  # reconstruct server run registry, durable task survives
        second = run(task_id)
        assert second["task_id"] == task_id
        context = client.get(f"/api/v1/projects/{pid}/sessions/{task_id}").json()
        assert context["decisions"] == ["只用真实数据"]
        assert len(context["run_ids"]) == 2
        assert client.get(f"/api/v1/projects/{pid}/lifecycle").json()["lifecycle"]["stage"] == "topic"
