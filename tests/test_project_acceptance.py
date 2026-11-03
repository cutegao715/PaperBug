"""Goal comparison, versioned human review and flexible factual delivery policies."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from paperagent.domain.project_dossier import ProjectDossier, ProjectRecordInput, ProjectBindingInput
from paperagent.domain.project_acceptance import objective_matrix
from paperagent.domain.project_claims import ProjectClaims, ClaimReviewInput, extract_claims
from paperagent.domain.project_assessment import save_assessment
from paperagent.kernel.project_delivery import check_project_delivery
from paperagent.workflows.registry import select_workflow
from paperagent.workspace.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    ws = Workspace("acceptance", tmp_path).init("项目结题研究")
    ws.write_draft("基线.md", "# 虚构验收样例\n计划完成3个案例，形成一份分析报告")
    ProjectDossier(ws).bind(ProjectBindingInput(role="baseline", filename="基线.md", reason="验收样例"))
    return ws


def add(ws, kind="objective", stage="application", content="完成三个案例", **kwargs):
    actor = kwargs.pop("actor", "user")
    return ProjectDossier(ws).record(ProjectRecordInput(kind=kind, stage=stage, content=content,
        sources=[{"kind": "user_note", "key": content}], reason="测试说明", **kwargs), actor=actor)


def target(ws, **overrides):
    return add(ws, criteria={"quantity": 3, "unit": "个", "due_date": "2026-10-01", **overrides})


def progress(ws, goal, quantity, *, as_of="2026-09-01", stage="midterm", completion="in_progress", **kw):
    return add(ws, "progress", stage, f"已完成{quantity}个案例", relates_to=[goal["record_id"]],
        measurements=[{"objective_id": goal["record_id"], "as_of": as_of, "quantity": quantity,
                       "unit": "个", "completion": completion, "explanation": "按真实进展说明"}], **kw)


def workflow(ws, **options):
    return select_workflow(ws, "项目结题", "project_final_report", {"scope": "partial", "delivery": "ready", **options})


def test_latest_cumulative_values_are_not_added_and_future_stage_is_excluded(ws):
    goal = target(ws)
    progress(ws, goal, 1, as_of="2026-08-01")
    progress(ws, goal, 2, as_of="2026-09-01")
    progress(ws, goal, 3, as_of="2026-10-01", stage="final", completion="completed")
    matrix = objective_matrix(ws, "midterm", "2026-10-13")
    assert matrix["items"][0]["measurement"]["quantity"] == 2
    assert matrix["items"][0]["state"] == "shortfall"
    assert matrix["items"][0]["past_due"]
    assert objective_matrix(ws, "final", "2026-10-13")["items"][0]["state"] == "meets_quantity"
    assert objective_matrix(ws, "final", "2026-08-15")["items"][0]["measurement"]["quantity"] == 1


def test_same_date_conflicts_and_unconfirmed_observations(ws):
    goal = target(ws)
    progress(ws, goal, 1)
    progress(ws, goal, 8, actor="agent")
    assert objective_matrix(ws, "midterm", "2026-10-13")["items"][0]["measurement"]["quantity"] == 1
    progress(ws, goal, 2)
    item = objective_matrix(ws, "midterm", "2026-10-13")["items"][0]
    assert item["state"] == "needs_review" and any("冲突" in s for s in item["blockers"])


def test_revisiting_midterm_does_not_require_future_stage_objectives(ws):
    goal = target(ws)
    row = progress(ws, goal, 1)
    add(ws, stage="final", content="结题阶段新增的归档目标")
    options = select_workflow(ws, "完整中期报告", "project_midterm_report",
                              {"scope":"full", "delivery":"ready", "report_date":"2026-10-13"})
    text = "截至2026-09-01已完成1个案例 {{project:" + row["record_id"] + "}}"
    report = check_project_delivery(ws, text, options)
    assert not report.errors
    assert any("不能据此判断是否按期完成" in s for s in report.warnings)


def test_claiming_completion_below_target_blocks_but_honest_shortfall_does_not(ws):
    goal = target(ws)
    row = progress(ws, goal, 1, stage="final", completion="completed")
    text = "实际完成了1个案例 {{project:" + row["record_id"] + "}}"
    report = check_project_delivery(ws, text, workflow(ws, scope="full", report_date="2026-10-13"))
    assert any("低于目标" in s for s in report.errors)
    progress(ws, goal, 1, stage="final", completion="not_completed", record_id=row["record_id"], expected_revision=1)
    assert not check_project_delivery(ws, text, workflow(ws, scope="full", report_date="2026-10-13")).errors


def test_units_evidence_requirement_and_revised_goals_require_review(ws):
    goal = target(ws, evidence_requirement="documented")
    row = progress(ws, goal, 3, stage="final", completion="completed")
    assert any("材料/证据" in s for s in objective_matrix(ws, "final", "2026-10-13")["items"][0]["blockers"])
    add(ws, content="完成三份报告", record_id=goal["record_id"], expected_revision=1,
        criteria={"quantity": 3, "unit": "份"})
    issues = objective_matrix(ws, "final", "2026-10-13")["items"][0]["blockers"]
    assert any("单位不同" in s for s in issues) and any("已更正" in s for s in issues)


@pytest.mark.parametrize("criteria", [{"quantity": float("nan"), "unit": "份"}, {"quantity": -1,"unit":"份"}, {"quantity": 2}])
def test_invalid_or_unitless_targets_rejected(ws, criteria):
    with pytest.raises(ValidationError):
        add(ws, criteria=criteria)


def test_measurements_require_real_goal_links_and_explanation(ws):
    goal = target(ws)
    with pytest.raises(ValidationError):
        add(ws, "progress", "final", measurements=[{"objective_id": goal["record_id"], "as_of": "2026-10-01", "completion": "not_completed"}])
    not_goal = add(ws, "issue", "midterm", "问题")
    with pytest.raises(ValueError, match="objective records"):
        progress(ws, not_goal, 1)


def test_claim_extraction_distinguishes_plans_and_citation_after_punctuation():
    claims = extract_claims("# 已完成3份报告\n计划完成3个案例。\n已完成1个案例。{{project:pr_abc}}\n已发表论文2篇。")
    assert len(claims) == 3
    assert claims[1]["record_ids"] == ["pr_abc"]
    planned = extract_claims("## 问题与下一步\n一是完成3个案例；二是形成2份报告。\n但目前已完成1个案例。")
    assert len(planned) == 1 and "已完成1个" in planned[0]["text"]
    assert extract_claims("## 问题与下一步\n截至报告日无论文发表、无获奖。")
    assert extract_claims("本项目无可报告的论文、获奖或其他量化成果。")


def test_traceable_and_human_review_modes_with_version_invalidation(ws):
    goal = target(ws)
    row = progress(ws, goal, 1, stage="final")
    text = "已完成1个案例。{{project:" + row["record_id"] + "}}"
    ws.write_draft("结题.md", text)
    assert not check_project_delivery(ws, text, workflow(ws), "结题.md").errors
    reviewed = workflow(ws, claim_policy="reviewed")
    assert check_project_delivery(ws, text, reviewed, "结题.md").errors
    service = ProjectClaims(ws)
    audit = service.audit(text, "结题.md")
    body = ClaimReviewInput(filename="结题.md", draft_version=audit["draft_version"], claim_id=audit["claims"][0]["claim_id"], verdict="supported", reason="已核对用户实际进展")
    service.review(body)
    assert not check_project_delivery(ws, text, reviewed, "结题.md").errors
    changed = text + "\n补充其他说明"
    ws.write_draft("结题.md", changed)
    assert service.audit(changed, "结题.md")["claims"][0]["state"] == "needs_review"
    with pytest.raises(ValueError, match="Draft changed"):
        service.review(body)
    ws.write_draft("结题.md", text)
    progress(ws, goal, 2, stage="final", record_id=row["record_id"], expected_revision=1)
    assert service.audit(text, "结题.md")["claims"][0]["stale"]


def test_uncited_factual_claims_need_evidence_but_user_can_classify_false_positive(ws):
    text = "已完成2份成果材料。"
    ws.write_draft("说明.md", text)
    assert check_project_delivery(ws, text, workflow(ws), "说明.md").errors
    assert not check_project_delivery(ws, text, workflow(ws, claim_policy="advisory"), "说明.md").errors
    service = ProjectClaims(ws)
    audit = service.audit(text, "说明.md")
    body = dict(filename="说明.md", draft_version=audit["draft_version"], claim_id=audit["claims"][0]["claim_id"], reason="这是拟用示例措辞，非本项目事实")
    with pytest.raises(ValueError, match="require available"):
        service.review(ClaimReviewInput(**body, verdict="supported"))
    service.review(ClaimReviewInput(**body, verdict="not_factual"))
    assert not check_project_delivery(ws, text, workflow(ws, claim_policy="reviewed"), "说明.md").errors


def test_scoped_delivery_does_not_block_on_unrelated_bad_measurement(ws):
    goal = target(ws)
    progress(ws, goal, 1, stage="final", completion="completed")
    assert not check_project_delivery(ws, "# 摘要\n研究设计介绍。", workflow(ws)).errors


def test_numeric_claim_cannot_borrow_a_citation_for_different_actual_quantity(ws):
    goal = target(ws)
    row = progress(ws, goal, 1, stage="final")
    citation = "{{project:" + row["record_id"] + "}}"
    bad = "实际完成了3个案例" + citation
    assert any("不一致" in s for s in ProjectClaims(ws).audit(bad)["claims"][0]["issues"])
    assert check_project_delivery(ws, bad, workflow(ws)).errors
    good = "计划完成3个，目前已完成1个案例" + citation
    assert not check_project_delivery(ws, good, workflow(ws)).errors
    mixed = "对照原定完成3个教学案例的目标{{project:" + goal["record_id"] + "}}，实际仅完成1个案例" + citation
    assert not check_project_delivery(ws, mixed, workflow(ws)).errors
    disclaimer = "实际完成1个案例" + citation + "，不写成完成3个。"
    assert not check_project_delivery(ws, disclaimer, workflow(ws)).errors


def test_missing_observation_is_unknown_not_zero_and_plan_does_not_prove_results(ws):
    goal = target(ws)
    item = objective_matrix(ws, "application", "2026-10-13")["items"][0]
    assert item["measurement"] is None and item["state"] == "unreported"
    text = "实际完成为0 {{project:" + goal["record_id"] + "}}"
    assert any("计划记录" in s for s in ProjectClaims(ws).audit(text)["claims"][0]["issues"])


def test_human_support_cannot_override_known_numeric_source_conflict(ws):
    goal = target(ws)
    row = progress(ws, goal, 1, stage="final")
    text = "已完成3个案例 {{project:" + row["record_id"] + "}}"
    ws.write_draft("冲突.md", text)
    service = ProjectClaims(ws)
    audit = service.audit(text, "冲突.md")
    with pytest.raises(ValueError, match="quantity conflicts"):
        service.review(ClaimReviewInput(filename="冲突.md",draft_version=audit["draft_version"],claim_id=audit["claims"][0]["claim_id"],verdict="supported",reason="直接确认"))


def test_manual_source_links_still_enforce_related_goal_constraints(ws):
    goal = target(ws)
    row = progress(ws, goal, 1, stage="final", completion="completed")
    text = "已完成1个案例。"
    ws.write_draft("无内联标记.md", text)
    service = ProjectClaims(ws)
    audit = service.audit(text, "无内联标记.md")
    service.review(ClaimReviewInput(filename="无内联标记.md",draft_version=audit["draft_version"],claim_id=audit["claims"][0]["claim_id"],verdict="supported",record_ids=[row["record_id"]],reason="核对数量"))
    assert any("低于目标" in s for s in check_project_delivery(ws,text,workflow(ws),"无内联标记.md").errors)


def test_assessment_artifact_retains_previous_review_versions(ws):
    ws.write_draft("报告.md", "已完成2份报告。")
    options = workflow(ws, claim_policy="advisory")
    first = save_assessment(ws, "报告.md", options)
    assert all(item["download_url"].startswith("/api/v1/projects/") for item in first)
    original = Path(first[1]["path"]).read_text(encoding="utf-8")
    assert "关键声明" in original
    audit = ProjectClaims(ws).audit(ws.read_draft("报告.md"), "报告.md")
    ProjectClaims(ws).review(ClaimReviewInput(filename="报告.md", draft_version=audit["draft_version"], claim_id=audit["claims"][0]["claim_id"], verdict="disputed", reason="没有报告可供核对"))
    second = save_assessment(ws, "报告.md", options)
    assert first[0]["path"] != second[0]["path"]
    assert Path(first[1]["path"]).read_text(encoding="utf-8") == original


def test_acceptance_and_review_http_is_project_scoped(ws, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(ws.root))
    from paperagent.server import app, RUNS
    RUNS.clear()
    goal = target(ws)
    row = progress(ws, goal, 1)
    ws.write_draft("报告.md", "已完成1个案例 {{project:" + row["record_id"] + "}}")
    with TestClient(app) as client:
        base = f"/api/v1/projects/{ws.project_id}/dossier"
        matrix = client.get(base + "/acceptance", params={"stage":"midterm","as_of":"2026-10-13"})
        assert matrix.status_code == 200 and matrix.json()["items"][0]["measurement"]["quantity"] == 1
        check = client.post(base + "/check", json={"filename":"报告.md","workflow_id":"project_midterm_report","workflow_options":{"scope":"partial","delivery":"ready","claim_policy":"reviewed"},"save_report":True})
        assert check.status_code == 200 and check.json()["report"]["errors"]
        download = client.get(check.json()["exports"][1]["download_url"])
        assert download.status_code == 200 and "目标对照" in download.text
        assert client.get(base + "/reports/not-a-report.md").status_code == 404
        data = check.json()["claims"]
        body = {"filename":"报告.md","draft_version":data["draft_version"],"claim_id":data["claims"][0]["claim_id"],"verdict":"supported","reason":"人工对照材料"}
        assert client.post(base + "/claims/review", json=body).status_code == 200
        assert client.get(base + "/acceptance", params={"stage":"not-a-stage"}).status_code == 422
