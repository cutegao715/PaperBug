"""Offline project lifecycle regression: source versions, corrections and delivery."""
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from docx import Document

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.agent.llm_agent import _action_adapter
from paperagent.domain.lifecycle import LifecycleService
from paperagent.domain.project_dossier import ProjectDossier, ProjectRecordInput, ProjectBindingInput
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.kernel.project_delivery import check_project_delivery
from paperagent.schemas.actions import WriteDraftAction, FinishAction, ReadContextAction, RecordProjectAction
from paperagent.schemas.policy import ResearchPolicy
from paperagent.tasks.session import SessionManager
from paperagent.workflows.registry import select_workflow
from paperagent.workspace.workspace import Workspace


@pytest.fixture
def project(tmp_path):
    ws = Workspace("project", tmp_path).init("研究项目申请与实施")
    LifecycleService(ws).save(lifecycle_type="project", stage="application", reason="建立项目")
    return ws


def record(ds, kind="objective", stage="application", content="完成两个案例研究", relates_to=None, **kwargs):
    body = ProjectRecordInput(kind=kind, stage=stage, content=content,
                              sources=[{"kind": "user_note", "key": content}],
                              relates_to=relates_to or [], reason="用户说明", **kwargs)
    return ds.record(body)


def baseline(ws):
    ws.write_draft("申请依据.md", "# 项目申请书\n\n## 目标\n两个案例研究")
    return ProjectDossier(ws).bind(ProjectBindingInput(role="baseline", filename="申请依据.md", reason="采用任务书依据"))


def wf(ws, stage="midterm", **options):
    ids = {"application": "grant_application", "midterm": "project_midterm_report", "final": "project_final_report"}
    return select_workflow(ws, "按当前材料撰写", ids[stage], options)


def test_baseline_is_explicit_and_immutable_not_latest_draft(project):
    project.write_draft("无关.md", "结题草稿")
    project.update_run_state(draft="无关.md")
    assert wf(project)["state"]["source_application_draft"] is None
    original = baseline(project)
    project.write_draft("申请依据.md", "新的目标，尚未采用")
    ds = ProjectDossier(Workspace(project.project_id, project.root))
    assert ds.bindings()["baseline"]["working_copy_changed"]
    assert "两个案例" in ds.source_text(original["binding_id"])
    assert wf(project)["state"]["source_application_draft"] == "申请依据.md"
    assert LifecycleService(project).get().stage == "application"


def test_candidate_adoption_correction_and_downstream_recheck(project):
    ds = ProjectDossier(project)
    baseline(project)
    target = record(ds)
    progress = record(ds, "progress", "midterm", "只完成一个案例，第二个延期", [target["record_id"]])
    content = "# 中期进展\n实际仅完成一个案例。{{project:" + progress["record_id"] + "}}"
    assert not check_project_delivery(project, content, wf(project, delivery="ready")).errors
    updated = record(ds, content="完成三个案例研究", record_id=target["record_id"], expected_revision=1)
    assert updated["revision"] == 2
    report = check_project_delivery(project, content, wf(project, delivery="ready"))
    assert any("已更正" in e for e in report.errors)
    assert len(ds.read()["records"]) == 3
    with pytest.raises(ValueError, match="changed"):
        record(ds, record_id=target["record_id"], expected_revision=1)
    revised = record(ds, "progress", "midterm", "已按三案例目标重新核对，仅完成一个", [target["record_id"]],
                     record_id=progress["record_id"], expected_revision=1)
    assert revised["related_versions"][target["record_id"]] == 2
    assert not check_project_delivery(project, content, wf(project, delivery="ready")).errors


def test_agent_cannot_promote_or_overwrite_user_facts(project):
    ds = ProjectDossier(project)
    original = record(ds)
    payload = dict(kind="objective", stage="application", content="改为问卷",
                   sources=[{"kind": "user_note", "key": "候选说明"}], reason="模型提取")
    candidate = ds.record(ProjectRecordInput(**payload), actor="agent", task_id="task-x")
    assert candidate["status"] == "proposed"
    with pytest.raises(ValueError, match="cannot replace"):
        ds.record(ProjectRecordInput(**payload, record_id=original["record_id"], expected_revision=1), actor="agent")
    adopted = ds.review(candidate["record_id"], expected_revision=1, status="confirmed", reason="用户核对")
    assert adopted["revision"] == 2
    ds.review(candidate["record_id"], expected_revision=2, status="withdrawn", reason="撤回错误提取")
    assert [r["record_id"] for r in ds.active_records()] == [original["record_id"]]
    assert len(ds.read()["records"]) == 4
    with pytest.raises(ValueError):
        _action_adapter.validate_python({"action": "record_project_context", **payload, "status": "confirmed"})


@pytest.mark.parametrize("stage", ["application", "midterm", "final"])
def test_stage_specific_template_scope_and_explicit_override(project, stage):
    ds = ProjectDossier(project)
    project.write_draft("模板.md", "# 官方章节\n## 成果附件\n")
    ds.bind(ProjectBindingInput(role=f"template_{stage}", filename="模板.md", reason="当前指南",
                                required_sections=["官方章节", "成果附件"]))
    full = wf(project, stage, scope="full", delivery="ready")
    report = check_project_delivery(project, "# 摘要\n摘要文本", full)
    assert any("缺少约定章节" in e for e in report.errors)
    partial = wf(project, stage, scope="partial", delivery="ready")
    assert not check_project_delivery(project, "# 摘要\n摘要文本", partial).errors
    overridden = wf(project, stage, scope="partial", delivery="ready", sections=["自定说明"])
    assert any("自定说明" in e for e in check_project_delivery(project, "# 摘要\n文本", overridden).errors)
    assert not check_project_delivery(project, "# 自定说明\n文本", overridden).errors
    other = "final" if stage != "final" else "midterm"
    assert wf(project, other)["project_contract"]["required_sections"] == []


def test_draft_unknowns_warn_ready_unknowns_block_and_scope_inference(project):
    text = "# 中期\n[to_be_confirmed]"
    assert not check_project_delivery(project, text, wf(project)).errors
    assert check_project_delivery(project, text, wf(project, delivery="ready")).errors
    assert select_workflow(project, "润色全文中期报告")["project_contract"]["scope"] == "full"
    assert select_workflow(project, "修改中期报告的问题与调整部分")["project_contract"]["scope"] == "partial"
    assert select_workflow(project, "申请书提纲")["project_contract"]["scope"] == "partial"


def test_direct_final_without_midterm_and_no_automatic_state_transition(project):
    baseline(project)
    ds = ProjectDossier(project)
    objective = record(ds)
    outcome = record(ds, "progress", "final", "第二案例未完成，按现有结果结题说明", [objective["record_id"]])
    text = "# 结题\n据实说明未完成内容 {{project:" + outcome["record_id"] + "}}"
    report = check_project_delivery(project, text, wf(project, "final", delivery="ready"))
    assert not report.errors
    assert "midterm" not in ds.bindings()
    assert LifecycleService(project).get().stage == "application"


def test_changed_baseline_or_material_forces_review(project, tmp_path):
    baseline(project)
    ds = ProjectDossier(project)
    objective = record(ds)
    path = tmp_path / "实验记录.txt"
    path.write_text("完成第一个案例", encoding="utf-8")
    material = project.upsert_material(str(path), "imported")
    body = ProjectRecordInput(kind="progress", stage="midterm", content="完成一个案例",
                              sources=[{"kind": "material", "key": material["material_id"]}],
                              relates_to=[objective["record_id"]], reason="原始实验记录")
    progress = ds.record(body)
    text = "# 进展\n完成一个案例 {{project:" + progress["record_id"] + "}}"
    assert not check_project_delivery(project, text, wf(project, delivery="ready")).errors
    path.write_text("更正：尚未完成", encoding="utf-8")
    assert any("材料内容已变化" in e for e in check_project_delivery(project, "# 进展", wf(project, delivery="ready")).errors)
    baseline(project)  # explicit rebinding, even same bytes, requires re-evaluation
    assert any("当前基线" in e for e in check_project_delivery(project, "# 进展", wf(project, delivery="ready")).errors)


def test_withdrawn_foreign_and_corrupt_sources_rejected(project, tmp_path):
    ds = ProjectDossier(project)
    source = baseline(project)
    other = Workspace("other", tmp_path).init("other")
    foreign = record(ProjectDossier(other))
    with pytest.raises(ValueError):
        record(ds, relates_to=[foreign["record_id"]])
    local = record(ds)
    ds.review(local["record_id"], expected_revision=1, status="withdrawn", reason="错误资料")
    text = "# 来源\n{{project:" + local["record_id"] + "}}"
    assert any("已撤回" in e for e in check_project_delivery(project, text, wf(project)).errors)
    Path(source["snapshot_path"]).write_text("damaged", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        ds.source_text(source["binding_id"])
    assert any("损坏" in e for e in check_project_delivery(project, "报告", wf(project)).errors)
    assert not check_project_delivery(project, "摘要文本", wf(project, scope="partial")).errors
    with pytest.raises(ValueError):
        ds.bind(ProjectBindingInput(role="baseline", filename="../escape.md", reason="bad"))


def test_handoff_persists_decisions_and_deferred_records_on_new_task(project):
    ds = ProjectDossier(project)
    baseline(project)
    decision = record(ds, "decision", "application", "取消问卷，仅采用案例比较")
    for i in range(28):
        record(ds, "issue", "midterm", f"历史问题{i}")
    session = SessionManager(project).create("写结题", "project_final_report")
    SessionManager(project).update(session.task_id, workflow_options={"scope": "partial", "delivery": "draft"})
    restarted = Workspace(project.project_id, project.root)
    restarted.task_id = session.task_id
    state = assemble_assistant_state(restarted, "继续", 0, 20, 20, None, None, [])
    workflow = state.current_context.workflow
    handoff = workflow["project_handoff"]
    assert handoff["records"][0]["record_id"] == decision["record_id"]
    assert handoff["omitted_records"] == 5
    assert workflow["project_contract"]["scope"] == "partial"
    loop = DecisionLoop(restarted, ToolExecutor(ResearchPolicy(), restarted), None)
    parts, offset = [], 0
    while True:
        result = loop._read_context(ReadContextAction(kind="project_dossier", offset=offset, max_chars=1000))
        parts.append(result["content"])
        if result["next_offset"] is None:
            break
        offset = result["next_offset"]
    assert "历史问题27" in "".join(parts)


@pytest.mark.parametrize("stage", ["application", "midterm", "final"])
def test_offline_loop_delivers_versioned_docx_and_provenance(project, stage):
    baseline(project)
    ds = ProjectDossier(project)
    objective = record(ds)
    row = record(ds, "progress", stage, "实际完成一个案例", [objective["record_id"]])
    workflow_id = wf(project, stage)["workflow_id"]
    task = SessionManager(project).create("准备正文", workflow_id)
    SessionManager(project).update(task.task_id, workflow_options={"scope": "partial", "delivery": "ready", "sections": ["实际工作"]})
    class Writer:
        def __init__(self): self.step = 0
        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return WriteDraftAction(filename="阶段文档.md", content="## 实际工作\n按申请书计划执行，完成一个案例 {{project:" + row["record_id"] + "}}")
            return FinishAction(summary="已完成当前章节")
    loop = DecisionLoop(project, ToolExecutor(ResearchPolicy(max_iterations=2), project), Writer(),
                        task_id=task.task_id, log=lambda _: None)
    outcome = loop.run("继续")
    assert outcome.status == "finished", outcome.summary
    assert outcome.quality_report.project_delivery.passed
    if stage != "application":
        assert outcome.quality_report.application_completeness is None
    delivery = ds.read()["deliveries"][-1]
    assert delivery["record_versions"][row["record_id"]] == 1
    exported = Path(delivery["exports"][0]["path"])
    assert exported.exists()
    text = "\n".join(p.text for p in Document(exported).paragraphs)
    assert "项目事实来源" in text and "用户采纳" in text
    assert "{{project:" not in text
    before = hashlib.sha256(exported.read_bytes()).hexdigest()
    project.write_draft("阶段文档.md", "后来修改的版本")
    assert hashlib.sha256(exported.read_bytes()).hexdigest() == before
    assert LifecycleService(project).get().stage == "application"
    assert "midterm" not in ds.bindings()  # finish never silently adopts artifacts


def test_loop_saves_candidate_with_task_provenance(project):
    class Extractor:
        def decide(self, obs):
            return RecordProjectAction(kind="objective", stage="application", content="候选目标",
                                       sources=[{"kind": "user_note", "key": "用户原话"}], reason="提取")
    loop = DecisionLoop(project, ToolExecutor(ResearchPolicy(max_iterations=1), project), Extractor(), log=lambda _: None)
    loop.run("提取项目目标")
    row = ProjectDossier(project).active_records()[0]
    assert row["status"] == "proposed" and row["task_id"] == loop.task_id


def test_releasing_binding_retains_history_and_requires_fresh_pointer(project):
    ds = ProjectDossier(project)
    first = baseline(project)
    second = baseline(project)
    with pytest.raises(ValueError, match="changed"):
        ds.release_binding("baseline", first["binding_id"], "旧请求")
    ds.release_binding("baseline", second["binding_id"], "用户取消错误绑定")
    assert "baseline" not in ds.bindings()
    assert len(ds.read()["bindings"]) == 3
    assert "两个案例" in ds.source_text(first["binding_id"])


def test_agent_cannot_downgrade_explicit_user_delivery_requirements(project):
    manager = SessionManager(project)
    task = manager.create("项目中期提交准备", "project_midterm_report")
    manager.update(task.task_id, workflow_options={"delivery": "ready", "scope": "partial"})
    with pytest.raises(ValueError, match="Explicit user"):
        manager.update(task.task_id, workflow_options={"delivery": "draft"}, workflow_options_actor="agent")
    state = manager.update(task.task_id, workflow_options={"sections": ["阶段成果"]}, workflow_options_actor="agent")
    assert state.workflow_options["delivery"] == "ready"
    assert state.workflow_options["scope"] == "partial"
    assert state.workflow_option_locks == ["scope", "delivery"]
    manager.update(task.task_id, workflow_options={})  # user can return to automatic mode
    state = manager.update(task.task_id, workflow_options={"delivery": "draft"}, workflow_options_actor="agent")
    assert state.workflow_options["delivery"] == "draft"


def test_bad_markers_and_unrelated_template_integrity_are_scoped(project):
    ds = ProjectDossier(project)
    project.write_draft("模板.md", "## 完整报告章节")
    bound = ds.bind(ProjectBindingInput(role="template_midterm", filename="模板.md", reason="模板",
                                       required_sections=["完整报告章节"]))
    Path(bound["snapshot_path"]).write_text("damaged", encoding="utf-8")
    partial = wf(project, scope="partial", delivery="ready")
    assert not check_project_delivery(project, "摘要正文", partial).errors
    for bad in ("{{project:wrong}}", "{{project:pr_abc", "{{project:}}"):
        assert check_project_delivery(project, bad, partial).errors


def test_http_project_dossier_and_check(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent.server import app, RUNS
    RUNS.clear()
    with TestClient(app) as client:
        pid = client.post("/api/v1/projects", json={"name": "项目验证", "lifecycle_type": "project"}).json()["project_id"]
        ws = Workspace(pid, tmp_path)
        ws.write_draft("模板.md", "# 进展")
        base = f"/api/v1/projects/{pid}/dossier"
        bind = client.post(base + "/bindings", json={"role": "template_midterm", "filename": "模板.md", "reason": "学校要求", "required_sections": ["进展"]})
        assert bind.status_code == 200
        body = dict(kind="decision", stage="application", content="使用案例",
                    sources=[{"kind": "user_note", "key": "导师说明"}], reason="用户补充")
        row = client.post(base + "/records", json=body).json()
        changed = client.post(base + "/records", json={**body, "content": "不使用问卷", "record_id": row["record_id"], "expected_revision": 1})
        assert changed.status_code == 200
        assert client.post(base + "/records", json={**body, "record_id": row["record_id"], "expected_revision": 1}).status_code == 422
        report = client.post(base + "/check", json={"filename": "模板.md", "workflow_id": "project_midterm_report", "workflow_options": {"scope": "partial", "delivery": "ready"}})
        assert report.status_code == 200 and not report.json()["report"]["errors"]
        assert len(client.get(base).json()["records"]) == 2
        assert client.post(base + "/bindings", json={"role": "baseline", "filename": "../project.json", "reason": "bad"}).status_code == 422
