"""Offline integration: evidence, outputs, grant reviews and rendered slides."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import requests
from playwright.sync_api import sync_playwright, expect

from paperagent.domain.paper_review import PaperReviews, PaperReviewInput
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.evidence import Evidence
from paperagent.schemas.policy import ResearchPolicy
from paperagent.schemas.references import Reference
from paperagent.workspace.workspace import Workspace


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="paperagent-research-ui-") as temp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
        env = dict(os.environ,PAPERAGENT_WORKSPACE=temp,PYTHONPATH=str(root/"src"),
                   PAPERAGENT_LLM_API_KEY="",PAPERAGENT_LLM_MODEL="",PAPERAGENT_LLM_BASE_URL="")
        process = subprocess.Popen([sys.executable,"-m","uvicorn","paperagent.server:app","--host","127.0.0.1","--port",str(port)],
                                   cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(150):
                try:
                    if requests.get(url+"/api/v1/config",timeout=.5).ok:break
                except requests.RequestException:pass
                time.sleep(.1)
            else:raise RuntimeError("Server did not start")
            created=requests.post(url+"/api/v1/projects",json={"name":"Research delivery UI","lifecycle_type":"project","stage":"application"},timeout=5)
            created.raise_for_status(); pid=created.json()["project_id"]
            ws=Workspace(pid,Path(temp))
            ws.save_reference_obj(Reference(ref_id="r",title="Evidence study"))
            ws.save_evidence(Evidence(evidence_id="e",ref_id="r",quote="The result remains uncertain.",relation="contradicts",themes=["outcome"],source_coverage="abstract_only"))
            ws.write_draft("proposal.md","# Proposal\nA focused research question.")
            def tool(id,**input):
                result=ToolExecutor(ResearchPolicy(),ws).execute(ToolCall(tool_id=id,input=input))
                assert result.status=="ok",result.errors
                return result.data
            chart=tool("chart_generate",title="Observed outcomes",data=[{"label":"Group A","value":-2},{"label":"Group B","value":4}],caption="Observed values; illustrative test data.",save=True,output_filename="outcomes.svg")
            slides=tool("presentation_build",title="Research overview",slides=[{
                "title":"Observed outcomes", "points":["Explain the research question.","Keep the inference within the evidence."],
                "notes":"Explain the limitations and alternative interpretations.",
                "sources":[{"kind":"draft","key":"proposal.md","locator":"Proposal"}],"figure_id":chart["artifact"]["figure_id"]}])
            tool("submission_package",filename="proposal.md",figure_ids=[chart["artifact"]["figure_id"]])
            service=PaperReviews(ws);basis=service.basis("proposal.md")
            service.create(PaperReviewInput(title="Proposal clarity",scope="Opening",review_kind="grant",review_mode="readability",
                artifacts=[{"filename":"proposal.md","expected_sha256":basis["version"]}],checks=[{"dimension":"clarity","coverage":"checked","basis":"Opening only"}],summary="Readability only; merit not evaluated."))
            with sync_playwright() as p:
                browser=p.chromium.launch();page=browser.new_page(viewport={"width":1440,"height":1050})
                errors=[];page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(url);page.locator('[data-project]').first.click()
                summary=page.locator('#researchOutputs > details > summary');expect(summary).to_be_visible();summary.click()
                page.locator('#researchOutputs [data-matrix]').click()
                expect(page.locator('#researchOutputs [data-matrix-view]')).to_contain_text("Evidence study")
                page.locator('#researchOutputs [data-matrix-view] summary').click()
                expect(page.locator('#researchOutputs [data-matrix-view]')).to_contain_text("反驳")
                expect(page.locator('#researchOutputs [data-outputs]')).to_contain_text("科研演示")
                expect(page.locator('#researchOutputs [data-outputs]')).to_contain_text("投稿包")
                expect(page.locator('#grantReviewContext')).to_contain_text("申请评审与复核")
                page.locator('#grantReviewContext > details > summary').click()
                expect(page.locator('#grantReviewContext')).to_contain_text("Proposal clarity")
                output=page.locator('#researchOutputs [data-outputs] details').filter(has_text="submission.zip")
                output.locator('summary').click()
                with page.expect_download() as download:
                    output.get_by_role('link',name='下载 submission.zip',exact=True).click()
                assert download.value.suggested_filename=="submission.zip"
                ws.write_draft("proposal.md","# Proposal\nUpdated question.")
                page.locator('#researchOutputs [data-refresh]').click()
                expect(page.locator('#researchOutputs [data-outputs]')).to_contain_text("需要复核")
                page.screenshot(path=str(Path(tempfile.gettempdir())/'paperagent-research-ui.png'),full_page=True)
                assert not errors,errors
                talk=browser.new_page(viewport={"width":1400,"height":850})
                talk.goto(Path(slides["output_path"]).as_uri())
                expect(talk.locator('h1')).to_have_text("Observed outcomes")
                assert talk.locator('img').evaluate('(e)=>e.complete && e.naturalWidth>0')
                assert talk.locator('section').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
                talk.screenshot(path=str(Path(tempfile.gettempdir())/'paperagent-research-presentation.png'))
                assert not (ws.dir/'tasks').exists()
                browser.close()
            print("Research UI PASS: matrix, download, source staleness, grant review and rendered HTML slides")
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill()


if __name__=="__main__":main()
