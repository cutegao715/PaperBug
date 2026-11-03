"""Offline browser verification of paper knowledge and internal Skill resources."""
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
    with tempfile.TemporaryDirectory(prefix="paperagent-manuscript-ui-") as temp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, PAPERAGENT_WORKSPACE=temp, PYTHONPATH=str(root / "src"),
                   PAPERAGENT_LLM_API_KEY="", PAPERAGENT_LLM_MODEL="", PAPERAGENT_LLM_BASE_URL="")
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "paperagent.server:app",
                                    "--host", "127.0.0.1", "--port", str(port)],
                                   cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                raise RuntimeError("UI server did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)
                page.locator("#newPit").click()
                page.locator("#projectNameInput").fill("英文学术论文界面验证")
                page.locator("#createLifecycle").select_option("paper_conference")
                page.locator("#confirmCreate").click()
                expect(page.locator("#manuscriptContext > details > summary")).to_contain_text("论文知识")
                page.locator("#lifecycleContext > details > summary").click()
                expect(page.locator('#workflowChoice option[value="paper_writing"]')).to_have_count(1)
                expect(page.locator('#workflowChoice option[value="publication_management"]')).to_have_count(1)
                page.locator("#workflowChoice").select_option("publication_management")
                expect(page.locator("#workflowDescription")).to_contain_text("真实邮箱与投稿账号同步")
                expect(page.locator("#workflowDescription")).to_contain_text("后续接入")
                page.locator("#workflowChoice").select_option("paper_writing")
                expect(page.locator("#skillPalette button", has_text="英文学术论文写作")).to_contain_text("流程建议")
                page.locator("#workflowChoice").select_option("off")
                page.locator("#lifecycleContext > details > summary").click()
                projects = requests.get(url + "/api/v1/projects", timeout=5).json()
                rows = projects.get("projects", []) if isinstance(projects, dict) else projects
                pid = rows[0]["project_id"]
                from paperagent.workspace.workspace import Workspace
                ws = Workspace(pid, Path(temp))
                ws.write_draft("paper.md", "# Results\nThe subjects improved.\n")
                page.locator("#manuscriptContext > details > summary").click()
                page.locator("#reloadManuscript").click()
                expect(page.locator('#paperCheckFile option[value="paper.md"]')).to_have_count(1)
                page.get_by_text("论文与目标刊会", exact=True).click()
                page.locator("#paperVenueType").select_option("conference")
                page.locator("#paperVenue").fill("ExampleConf 2026")
                page.locator("#paperTrack").fill("Research")
                page.locator("#savePaperProfile").click()
                expect(page.locator("#paperVenue")).to_have_value("ExampleConf 2026")
                expect(page.locator("#paperVenue")).not_to_be_visible()
                page.locator("#manuscriptEditor > summary").click()
                page.locator("#paperRecordTitle").fill("Population")
                page.locator("#paperPreferred").fill("participants")
                page.locator("#paperDiscouraged").fill("subjects")
                page.locator("#paperSourceKey").fill("The author chooses participants for recruited people.")
                page.locator("#paperRecordReason").fill("Unified author terminology")
                # Background detail refresh must not erase the unsaved form.
                page.evaluate("refreshProjectDetail()")
                expect(page.locator("#paperPreferred")).to_have_value("participants")
                page.locator("#savePaperRecord").click()
                expect(page.locator("#manuscriptRecords")).to_contain_text("Population · 已采纳 · v1")
                page.locator("#checkPaperContext").click()
                expect(page.locator("#paperCheckResult")).to_contain_text("subjects 是否应为 participants")
                expect(page.locator("#paperCheckResult")).to_contain_text("尚未验证科学论证")
                page.locator("#manuscriptRecords button", has_text="更正").click()
                page.locator("#paperDefinition").fill("People enrolled in this study")
                page.locator("#paperRecordReason").fill("Clarify definition")
                page.locator("#savePaperRecord").click()
                expect(page.locator("#manuscriptRecords")).to_contain_text("Population · 已采纳 · v2")
                page.locator("#manuscriptEditor > summary").click()
                page.locator("#paperRecordKind").select_option("argument")
                page.locator("#paperRecordTitle").fill("Inference boundary")
                page.locator("#arg_question").fill("Is X associated with Y?")
                page.locator("#arg_boundary").fill("Association does not establish causality.")
                page.locator("#paperSourceKey").fill("Observational design selected by the author")
                page.locator("#paperRecordReason").fill("Preserve inference boundary")
                page.locator("#savePaperRecord").click()
                expect(page.locator("#manuscriptRecords")).to_contain_text("Association does not establish causality.")
                page.locator("#manuscriptEditor > summary").click()
                page.locator("#paperRecordKind").select_option("venue_rule")
                page.locator("#paperRecordTitle").fill("Review copy")
                page.locator("#paperRecordContent").fill("Check anonymization against current track rules")
                page.locator("#paperRuleURL").fill("https://example.org/fictional-cfp")
                page.locator("#paperRuleScope").fill("review")
                page.locator("#paperSourceKey").fill("Fictional demonstration; official requirements remain unverified")
                page.locator("#paperRecordReason").fill("Record a requirement pending verification")
                page.locator("#savePaperRecord").click()
                expect(page.locator("#manuscriptRecords")).to_contain_text("Review copy · 已采纳 · v1")
                page.get_by_text("论文与目标刊会", exact=True).click()
                page.locator("#paperVenue").fill("Example Journal")
                page.locator("#paperVenueType").select_option("journal")
                page.locator("#savePaperProfile").click()
                expect(page.locator("#manuscriptRecords")).to_contain_text("目标刊会或稿件配置已变化")
                # A review is an optional artifact within the existing paper panel.
                review_base = f"{url}/api/v1/projects/{pid}/paper-reviews"
                basis = requests.get(review_base + "/basis", params={"filename": "paper.md"}, timeout=5).json()
                response = requests.post(review_base, json={
                    "title": "Population evidence review", "scope": "Results wording only", "round_label": "substantive",
                    "artifacts": [{"filename": "paper.md", "expected_sha256": basis["version"]}],
                    "checks": [{"dimension": "Outcome wording", "coverage": "checked", "basis": "Read Results"},
                               {"dimension": "Raw data", "coverage": "unavailable", "basis": "Not supplied"}],
                    "findings": [{"title": "Clarify population", "location": "Results", "problem": "The population is unspecified.",
                                  "basis": "Current Results only says subjects", "action_needed": "State the verified population.",
                                  "severity": "major", "evidence_status": "supported", "classification": "reporting",
                                  "affected_artifacts": ["paper.md"]}],
                    "summary": "Review covers wording; underlying science not verified.",
                }, timeout=5)
                response.raise_for_status()
                review = response.json()
                report_id, issue_id = review["report_id"], review["findings"][0]["issue_id"]
                page.locator("#paperReviewPanel > summary").click()
                page.locator("#refreshPaperReviews").click()
                review_card = page.locator(f'[data-review-id="{report_id}"]')
                expect(review_card).to_have_count(1)
                review_card.locator(":scope > summary").click()
                expect(review_card).to_contain_text("Raw data：资料不可用")
                review_card.get_by_role("button", name="查看原报告").click()
                expect(review_card.locator("[data-memo]")).to_contain_text("SHA256")
                ws.write_draft("paper.md", "# Results\nAll 40 participants improved.\n")
                from paperagent.domain.paper_review import PaperReviews, ReviewFollowupInput
                service = PaperReviews(ws)
                service.followup(report_id, issue_id, ReviewFollowupInput(
                    expected_revision=1, status="resolved", reason="The current Results now reports the population.",
                    verifications=[{"filename": "paper.md", "expected_sha256": service.basis("paper.md")["version"],
                                    "locator": "Results", "quote": "All 40 participants improved."}]), actor="agent")
                page.locator("#refreshPaperReviews").click()
                review_card.locator(":scope > summary").click()
                issue = page.locator(f'[data-issue-id="{issue_id}"]')
                expect(issue.locator("b")).to_contain_text("待处理")
                expect(issue).to_contain_text("助手待采纳判断：已复核解决")
                page.once("dialog", lambda dialog: dialog.accept("作者已核对当前结果与来源"))
                issue.get_by_role("button", name="采纳复核建议").click()
                expect(issue.locator("b")).to_contain_text("已复核解决")
                ws.write_draft("paper.md", "# Results\nAll 42 participants improved.\n")
                page.locator("#refreshPaperReviews").click()
                review_card.locator(":scope > summary").click()
                expect(issue.locator("b")).to_contain_text("尚无法核对")
                issue.get_by_text("记录人工复核", exact=True).click()
                issue.locator('select[name="status"]').select_option("resolved")
                issue.locator('textarea[name="reason"]').fill("已对照来源核实更新后的人数")
                issue.locator('input[name="locator"]').fill("Results")
                issue.locator('textarea[name="quote"]').fill("All 42 participants improved.")
                # A background workspace refresh keeps the unsubmitted follow-up.
                page.evaluate("refreshProjectDetail()")
                expect(issue.locator('textarea[name="quote"]')).to_have_value("All 42 participants improved.")
                issue.get_by_role("button", name="保存人工复核").click()
                expect(issue.locator("b")).to_contain_text("已复核解决")
                assert "42" in ws.read_draft("paper.md")
                # Read the relevant Skill guide without a Workflow or model call.
                page.locator("#lifecycleContext > details > summary").click()
                page.locator("#workflowChoice").select_option("off")
                page.locator("#skillPalette button", has_text="英文学术论文写作").click()
                expect(page.locator("#skillDetails > p").first).to_contain_text("生命周期专用 · 系统共享")
                expect(page.locator("#skillDetails button", has_text="引言与研究背景")).to_be_visible()
                resource = page.locator("#skillDetails button", has_text="英文学术表达与局部修改")
                expect(resource).to_be_visible()
                # A late/repeated workflow preview must retain the selected guide.
                with page.expect_response(lambda r: r.url.endswith('/workflows/preview')):
                    page.evaluate("document.getElementById('workflowChoice').dispatchEvent(new Event('change'))")
                expect(resource).to_be_visible()
                expect(page.locator("#skillDetails pre").filter(has_text="Meaning before fluency")).to_have_count(0)
                resource.click()
                expect(resource).to_contain_text("已读取")
                assert any(page.locator("#skillDetails pre").nth(i).inner_text() for i in range(page.locator("#skillDetails pre").count()))
                page.locator("#skillDetails").screenshot(path=str(Path(tempfile.gettempdir()) / "paperagent-skill-taxonomy-ui.png"))
                page.locator("#lifecycleContext > details > summary").click()
                page.locator("#manuscriptContext").scroll_into_view_if_needed()
                screenshot = Path(tempfile.gettempdir()) / "paperagent-manuscript-ui.png"
                page.screenshot(path=str(screenshot), full_page=True)
                assert not errors, errors
                assert not (ws.dir / "tasks").exists()
                print(f"Manuscript UI PASS: profile, terms, argument, venue freshness, diagnostics, resources, review reports and follow-up adoption/staleness; screenshot: {screenshot}")
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
