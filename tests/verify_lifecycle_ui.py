"""Optional browser smoke test: isolated workspace, no live LLM or real user data.

Run with python tests/verify_lifecycle_ui.py (requires Playwright Chromium).
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import requests
from playwright.sync_api import sync_playwright, expect


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="paperagent-ui-") as temp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, PAPERAGENT_WORKSPACE=temp, PYTHONPATH=str(root / "src"),
                   PAPERAGENT_LLM_API_KEY="", PAPERAGENT_LLM_MODEL="", PAPERAGENT_LLM_BASE_URL="")
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "paperagent.server:app",
                                    "--host", "127.0.0.1", "--port", str(port)],
                                   cwd=root, env=env, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(150):
                try:
                    if requests.get(url + "/api/v1/config", timeout=0.5).ok:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("UI verification server did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.locator("#newPit").click()
                page.locator("#projectNameInput").fill("生命周期界面验证")
                page.locator("#createLifecycle").select_option("thesis")
                page.locator("#confirmCreate").click()
                summary = page.locator("#lifecycleContext > details > summary")
                expect(summary).to_contain_text("毕业与学位论文")
                if not summary.locator("..").get_attribute("open") is not None:
                    summary.click()
                page.locator("#lifecycleStage").select_option("blind_review")
                page.once("dialog", lambda dialog: dialog.accept("进入盲审准备"))
                page.locator("#saveLifecycle").click()
                expect(page.locator("#lifecycleStage")).to_have_value("blind_review")
                # Refresh collapses the panel; open the actual refreshed controls.
                page.reload()
                page.locator('[data-project]').first.click()
                summary = page.locator("#lifecycleContext > details > summary")
                expect(summary).to_contain_text("毕业与学位论文")
                if not summary.locator("..").get_attribute("open") is not None:
                    summary.click()
                expect(page.locator("#lifecycleStage")).to_have_value("blind_review")
                expect(page.locator("#resumeTask")).to_have_count(0)
                expect(page.locator('#workflowChoice option[value="thesis_drafting"]')).to_have_count(0)
                expect(page.locator('#workflowChoice option[value="thesis_writing"]')).to_have_count(1)
                expect(page.locator('#workflowChoice option[value="thesis_defense"]')).to_have_count(1)
                page.locator("#workflowChoice").select_option("thesis_defense")
                expect(page.locator("#workflowDescription")).to_contain_text("可编辑答辩 PPTX")
                expect(page.locator("#workflowDescription")).to_contain_text("后续接入")
                expect(page.locator("#lifecycleStage")).to_have_value("blind_review")
                page.locator("#workflowChoice").select_option("thesis_writing")
                expect(page.locator("#skillPalette")).to_contain_text("流程建议")
                expect(page.locator('#skillPalette [title^="builtin:advisor_feedback@1.0"]')).to_have_count(1)
                assert page.locator('#skillPalette [data-skill-index]').count() >= 11
                page.locator("#workflowChoice").select_option("off")
                expect(page.locator("#skillPalette")).not_to_contain_text("流程建议")
                page.locator("#lifecycleContext details details summary").click()
                page.once("dialog", lambda dialog: dialog.accept("取消问卷，只用案例研究"))
                page.locator("#addProjectMemory").click()
                expect(page.locator("#memoryRecords")).to_contain_text("取消问卷")
                page.once("dialog", lambda dialog: dialog.accept("采用两个案例进行比较"))
                page.locator("#memoryRecords button", has_text="更正").click()
                expect(page.locator("#memoryRecords")).to_contain_text("两个案例")
                page.locator("#memoryRecords button", has_text="移出记忆").click()
                expect(page.locator("#memoryRecords")).to_contain_text("尚无")
                # Exercise the project dossier UI against isolated real HTTP/storage.
                page.locator("#newPitTop").click()
                page.locator("#projectNameInput").fill("项目流程验证")
                page.locator("#createLifecycle").select_option("project")
                page.locator("#confirmCreate").click()
                expect(page.locator("#lifecycleContext > details > summary")).to_contain_text("项目")
                projects = requests.get(url + "/api/v1/projects", timeout=5).json()
                rows = projects.get("projects", []) if isinstance(projects, dict) else projects
                pid = next(p["project_id"] for p in rows if "项目流程验证" in (p.get("name", "") + p.get("goal", "") + p["project_id"]))
                from paperagent.workspace.workspace import Workspace
                workspace = Workspace(pid, Path(temp))
                (workspace.dir / "skills").mkdir(exist_ok=True)
                (workspace.dir / "skills" / "local_review.yaml").write_text(
                    'id: grant_midterm_report\nversion: "1.0"\nname: 本课题进展核查\n'
                    'triggers: [项目流程验证, 中期]\nprocedure: [核对本课题案例材料]\n', encoding="utf-8")
                workspace.write_draft("项目申请.md", "# 研究目标\n计划完成两个案例研究")
                workspace.write_draft("中期报告.md", "# 中期报告\n待填写")
                page.reload()
                page.locator('[data-project]', has_text="项目流程验证").first.click()
                if page.locator("#lifecycleContext > details").get_attribute("open") is None:
                    page.locator("#lifecycleContext > details > summary").click()
                expect(page.locator('#workflowChoice option[value="project_opportunity_discovery"]')).to_have_count(1)
                page.locator("#workflowChoice").select_option("project_opportunity_discovery")
                expect(page.locator("#projectWorkflowOptions")).not_to_be_visible()
                expect(page.locator("#workflowDescription")).to_contain_text("无人值守网站发现")
                page.locator("#workflowChoice").select_option("project_midterm_report")
                expect(page.locator("#projectWorkflowOptions")).to_be_visible()
                local_skill = page.locator("#skillPalette button", has_text="本课题进展核查")
                expect(local_skill).to_contain_text("本空间私有")
                expect(local_skill).to_contain_text("流程建议")
                local_skill.click()
                expect(page.locator("#skillDetails")).to_contain_text("核对本课题案例材料")
                page.locator("#workflowChoice").select_option("off")
                expect(page.locator("#skillPalette")).not_to_contain_text("流程建议")
                expect(page.locator("#skillPalette")).to_contain_text("本空间私有")
                page.locator("#conversationFocus").select_option("中期报告.md")
                # Focus saves and refreshes; reopening must restore the selected document.
                expect(page.locator("#conversationFocus")).to_have_value("中期报告.md")
                page.reload()
                page.locator('[data-project]', has_text="项目流程验证").first.click()
                if page.locator("#lifecycleContext > details").get_attribute("open") is None:
                    page.locator("#lifecycleContext > details > summary").click()
                expect(page.locator("#conversationFocus")).to_have_value("中期报告.md")
                expect(page.locator("#resumeTask")).to_have_count(0)
                # Simulate an interrupted run using the real persisted conversation.
                from paperagent.conversation import ConversationManager
                conversation = ConversationManager(workspace)
                conversation.begin("解释材料用途", "ui_interrupted")
                conversation.checkpoint(status="budget_exhausted")
                page.reload()
                page.locator('[data-project]', has_text="项目流程验证").first.click()
                if page.locator("#lifecycleContext > details").get_attribute("open") is None:
                    page.locator("#lifecycleContext > details > summary").click()
                expect(page.locator("#resumeConversation")).to_be_visible()
                # Avoid a model call: the server's offline run is tested through HTTP unit tests.
                page.locator("#projectDossierPanel > summary").click()
                page.locator("#projectBindingFile").select_option("项目申请.md")
                page.once("dialog", lambda dialog: dialog.accept("作为后续报告基线"))
                page.locator("#bindProjectArtifact").click()
                expect(page.locator("#projectBindings")).to_contain_text("申请/任务书基线")
                page.locator("#projectRecordEditorTitle").click()
                page.locator("#projectRecordContent").fill("完成两个案例研究")
                page.locator("#projectRecordSource").fill("用户确认的任务书目标")
                page.locator("#projectCriteriaFields > summary").click()
                page.locator("#projectTargetQuantity").fill("2")
                page.locator("#projectTargetUnit").fill("个")
                page.locator("#saveProjectRecord").click()
                expect(page.locator("#projectRecordList")).to_contain_text("完成两个案例研究")
                page.locator("#projectRecordList button", has_text="更正").click()
                page.locator("#projectRecordContent").fill("改为三个案例研究")
                page.locator("#projectRecordSource").fill("更新后的任务书目标")
                page.locator("#projectCriteriaFields > summary").click()
                page.locator("#projectTargetQuantity").fill("3")
                page.locator("#saveProjectRecord").click()
                expect(page.locator("#projectRecordList")).to_contain_text("改为三个案例研究")
                expect(page.locator("#projectRecordList")).to_contain_text("v2")
                page.locator("#projectScope").select_option("partial")
                page.locator("#projectDeliveryMode").select_option("ready")
                page.locator("#projectCheckStage").select_option("midterm")
                page.locator("#checkProjectDocument").click()
                expect(page.locator("#projectCheckResult")).to_contain_text("局部 / 提交准备")
                assert "需处理：" not in page.locator("#projectCheckResult").inner_text()
                page.locator("#projectRecordEditorTitle").click()
                page.locator("#projectRecordKind").select_option("progress")
                page.locator("#projectRecordStage").select_option("midterm")
                page.locator("#projectRecordContent").fill("已完成1个案例")
                page.locator("#projectRecordSource").fill("用户提供的阶段进展说明")
                page.locator("#projectMeasurementFields > summary").click()
                page.locator("#projectMeasuredGoal").select_option(label="改为三个案例研究")
                page.locator("#projectMeasuredDate").fill("2026-09-01")
                page.locator("#projectMeasuredQuantity").fill("1")
                page.locator("#projectMeasuredUnit").fill("个")
                page.locator("#saveProjectRecord").click()
                expect(page.locator("#projectRecordList")).to_contain_text("已完成1个案例")
                dossier = requests.get(url + f"/api/v1/projects/{pid}/dossier", timeout=5).json()
                progress = next(r for r in dossier["active_records"] if r["kind"] == "progress")
                workspace.write_draft("中期报告.md", "# 中期报告\n已完成1个案例。{{project:" + progress["record_id"] + "}}")
                page.locator("#projectCheckFile").select_option("中期报告.md")
                page.locator("#projectCheckStage").select_option("midterm")
                page.locator("#projectClaimPolicy").select_option("reviewed")
                page.locator("#checkProjectDocument").click()
                expect(page.locator("#projectAcceptanceMatrix")).to_contain_text("约定：3 个")
                expect(page.locator("#projectCheckResult")).to_contain_text("关键声明需核查")
                page.locator('#projectClaimsList [aria-label="核查结论"]').select_option("supported")
                page.locator('#projectClaimsList [aria-label="核对说明"]').fill("已对照阶段进展记录")
                page.locator("#projectClaimsList button", has_text="保存核查结论").click()
                expect(page.locator("#projectClaimsList")).to_contain_text("用户核对支持")
                expect(page.locator("#projectCheckResult")).not_to_contain_text("需处理：")
                page.locator("#saveProjectAssessment").click()
                expect(page.locator("#toast")).to_contain_text("核查清单已保存")
                page.once("dialog", lambda dialog: dialog.accept("测试撤回错误目标"))
                page.locator("#projectRecordList button", has_text="撤回").first.click()
                expect(page.locator("#projectRecordList")).not_to_contain_text("改为三个案例研究")
                screenshot = Path(tempfile.gettempdir()) / "paperagent-lifecycle-ui.png"
                page.screenshot(path=str(screenshot), full_page=True)
                assert not errors, errors
                print(f"UI smoke passed; screenshot: {screenshot}")
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
