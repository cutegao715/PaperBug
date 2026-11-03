"""Conversation-first execution, flexible skill use and restart recovery."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.conversation import ConversationManager, select_conversation_workflow
from paperagent.domain.lifecycle import LifecycleService
from paperagent.domain.store import DomainStore
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.actions import ChatReplyAction, ReadContextAction, UpdateTaskAction, WriteDraftAction
from paperagent.schemas.policy import ResearchPolicy
from paperagent.tasks.memory_manager import MemoryManager
from paperagent.workspace.workspace import Workspace


class Reply:
    def decide(self, obs):
        assert obs["task"] is None
        assert not obs["task_checkpoint"]
        return ChatReplyAction(text="已记录")


def loop(ws, agent=None, **kwargs):
    return DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=2), ws), agent or Reply(), log=lambda _:None, **kwargs)


def test_default_dialogue_creates_no_tasks_and_reuses_conversation(tmp_path):
    ws = Workspace("p", tmp_path).init("项目申请书")
    first = loop(ws)
    assert first.run("讨论研究方法").status == "finished"
    assert first.task_id is None and not (ws.dir / "tasks").exists()
    previous = ConversationManager(ws).get().run_id
    fresh = Workspace("p", tmp_path)
    assert loop(fresh).run("换个角度解释研究问题").status == "finished"
    state = ConversationManager(fresh).get()
    assert state.request == "换个角度解释研究问题"
    assert state.conversation_id == "main" and state.run_id != previous
    assert (ws.dir / "conversations" / "runs" / f"{previous}.json").exists()
    assert fresh.read_run_state()["workflow"] is None


def test_budget_interruption_resume_preserves_plan_constraints_and_changed_draft(tmp_path):
    ws = Workspace("p", tmp_path).init("案例研究")
    class Writer:
        def __init__(self): self.n = 0
        def decide(self, obs):
            self.n += 1
            if self.n == 1:
                return UpdateTaskAction(action="update_conversation_context", plan={"next":"第二章"}, decisions=["用户已明确采用案例比较"])
            return WriteDraftAction(filename="论文.md", content="# 方法\n案例比较。")
    assert loop(ws, Writer(), workflow_options={"delivery":"ready"}).run("写论文，不使用问卷").status == "budget_exhausted"
    original = ws.read_draft("论文.md")
    ws.write_draft("论文.md", original + "\n用户补充材料。")
    fresh = Workspace("p", tmp_path)
    class Resume:
        def decide(self, obs):
            c = obs["conversation"]
            assert c["plan"]["next"] == "第二章"
            assert c["instructions"][0] == "写论文，不使用问卷"
            assert c["target"]["changed"]
            assert c["workflow_options"]["delivery"] == "ready"
            assert c["decisions"] == ["用户已明确采用案例比较"]
            return ChatReplyAction(text="已读取接续信息")
    assert loop(fresh, Resume()).run("继续").status == "finished"
    with pytest.raises(ValueError, match="cannot be changed"):
        ConversationManager(fresh).update(workflow_options={"delivery":"draft"})
    ConversationManager(fresh).begin("继续", "adjust_scope", workflow_options={"scope":"partial"})
    assert ConversationManager(fresh).get().workflow_options == {"delivery":"ready", "scope":"partial"}
    assert fresh.read_draft("论文.md") == original + "\n用户补充材料。"


def test_direction_change_archives_plan_and_explicit_resume_reuses_latest_request(tmp_path):
    ws = Workspace("p", tmp_path).init("课题")
    manager = ConversationManager(ws)
    manager.begin("写中期报告", "r1", workflow_id="project_midterm_report", workflow_options={"scope":"full"})
    manager.update(plan={"next":"写成果"}, open_questions=["缺少案例数量"])
    manager.begin("先讨论研究方法", "r2")
    state = manager.get()
    assert state.plan == {} and state.workflow_options == {} and state.workflow_id is None
    old = json.loads((manager.dir / "runs" / "r1.json").read_text(encoding="utf-8"))
    assert old["plan"] == {"next":"写成果"} and old["open_questions"]
    assert manager.context()["recent_runs"][0]["run_id"] == "r1"
    manager.begin("接着分析", "r3", resume=True)
    assert manager.get().request == "先讨论研究方法"
    assert manager.get().instructions[-1] == "接着分析"


@pytest.mark.parametrize("goal", ["你好", "只修改中期报告摘要", "评审结题报告", "解释研究方法", "讨论申请书的创新点"])
def test_stage_does_not_force_workflow_but_skills_and_lifecycle_remain(tmp_path, goal):
    ws = Workspace("p", tmp_path).init("项目申请书")
    LifecycleService(ws).save(lifecycle_type="project", stage="application", reason="测试")
    ConversationManager(ws).begin(goal, "r1")
    state = assemble_assistant_state(ws, goal, 1, 8, 10, None, None, [])
    assert state.current_context.workflow is None
    assert state.current_context.lifecycle["current_stage"] == "application"
    assert state.current_context.skills


def test_optional_workflow_preserves_existing_delivery_and_explicit_off(tmp_path):
    ws = Workspace("p", tmp_path).init("科研项目")
    selected = select_conversation_workflow(ws, "帮我写完整中期报告", {})
    assert selected["workflow_id"] == "project_midterm_report"
    assert selected["execution_policy"]["ordered_steps"] is False
    assert select_conversation_workflow(ws, "写中期报告", {"workflow_mode":"off"}) is None
    assert select_conversation_workflow(ws, "只改摘要", {"workflow_id":"grant_application"})


def test_focus_and_memory_are_project_scoped_and_history_is_readable(tmp_path):
    a, b = Workspace("a", tmp_path).init("案例研究"), Workspace("b", tmp_path).init("另一研究")
    a.write_draft("论文.md", "# 方法\n案例比较")
    manager = ConversationManager(a)
    manager.focus("论文.md")
    manager.begin("检查方法", "r1")
    manager.begin("另外讨论", "r2")
    store = DomainStore(tmp_path)
    MemoryManager(store).remember("project", "采用案例比较", scope={"project_id":"a"}, kind="decision", pinned=True)
    state = assemble_assistant_state(a, "另外讨论", 1, 8, 10, None, None, [], store=store)
    assert state.current_context.conversation["target"]["filename"] == "论文.md"
    assert state.current_context.memories["project"]
    other = assemble_assistant_state(b, "检查方法", 1, 8, 10, None, None, [], store=store)
    assert not other.current_context.conversation["target"]
    assert not other.current_context.memories.get("project")
    restored = loop(a)._read_context(ReadContextAction(kind="conversation", key="r1"))
    assert "检查方法" in restored["content"]
    assert loop(a)._read_context(ReadContextAction(kind="conversation", key="../r1"))["status"] == "error"


def test_cancelled_run_is_checkpointed_without_replaying_writes(tmp_path):
    ws = Workspace("p", tmp_path).init("研究")
    cancellation = threading.Event()
    cancellation.set()
    assert loop(ws, cancel_event=cancellation).run("整理材料").status == "cancelled"
    assert ConversationManager(ws).get().status == "cancelled"
    assert loop(Workspace("p", tmp_path)).run("继续").status == "finished"
    assert not list(ws.drafts_dir.glob("*.md"))


def test_http_default_conversation_focus_and_restart(tmp_path, monkeypatch):
    from paperagent import server
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERAGENT_SCHEDULER", "off")
    monkeypatch.setattr(server.control, "ScriptedAgent", lambda **_: Reply())
    server.RUNS.clear()
    with TestClient(server.app) as client:
        pid = client.post("/api/v1/projects", json={"name":"对话研究", "lifecycle_type":"project"}).json()["project_id"]
        ws = Workspace(pid, tmp_path)
        ws.write_draft("稿件.md", "# 方法\n正文")
        base = f"/api/v1/projects/{pid}"
        assert client.post(base + "/conversation/focus", json={"filename":"稿件.md"}).status_code == 200
        assert client.post(base + "/conversation/focus", json={"filename":"../project.json"}).status_code == 422
        def send(text, **options):
            response = client.post(base + "/messages", json={"text":text, "mode":"offline", **options})
            assert response.status_code == 200
            data = response.json()
            assert data["task_id"] is None and data["conversation_id"] == "main"
            assert server.RUNS[data["run_id"]].done.wait(10)
            return data
        send("聊聊研究方法")
        assert not (ws.dir / "tasks").exists()
        manager = ConversationManager(ws)
        manager.checkpoint(status="running", summary="模拟进程中断")
        server.RUNS.clear()
        detail = client.get(base).json()
        assert detail["conversation"]["can_resume"] and detail["conversation"]["status"] == "interrupted"
        assert detail["conversation"]["target"]["filename"] == "稿件.md"
        send("继续", resume=True)
        assert manager.get().request == "聊聊研究方法"
        send("先看看另一种方法")
        assert manager.get().request == "先看看另一种方法"
        assert client.post(base + "/messages", json={"text":"写", "workflow_id":"unknown"}).status_code == 422
        assert client.post(base + "/conversation/focus", json={"filename":None}).status_code == 200
        assert manager.get().target is None
