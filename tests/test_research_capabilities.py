"""Research methods use existing evidence stores and versioned local outputs."""
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.evidence import Evidence
from paperagent.schemas.policy import ResearchPolicy
from paperagent.schemas.references import Reference
from paperagent.workspace.workspace import Workspace


def call(ws, tool, **data):
    return ToolExecutor(ResearchPolicy(), ws).execute(ToolCall(tool_id=tool, input=data))


def test_matrix_is_a_derived_view_with_contradictions_and_source_freshness(tmp_path):
    ws = Workspace("p", tmp_path).init("review")
    ws.save_reference_obj(Reference(ref_id="r1", title="Primary study"))
    ws.save_reference_obj(Reference(ref_id="r2", title="Unread study"))
    source = ws.dir / "source.md"
    source.write_text("The effect was uncertain.", encoding="utf-8")
    ev = Evidence(evidence_id="e1", ref_id="r1", quote="The effect was uncertain.",
                  source_path=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  relation="contradicts", supports="Strong efficacy", themes=["efficacy"],
                  method="observational", limitations=["narrow sample"], source_coverage="partial")
    ws.save_evidence(ev)
    before = ws.evidence_path.read_bytes()
    matrix = call(ws, "literature_matrix", limit=1).data
    card = matrix["rows"][0]["evidence"][0]
    assert card["relation"] == "contradicts" and card["source_freshness"] == "current"
    assert card["verification"] == "not_evaluated"
    assert matrix["next_offset"] == 1
    assert call(ws, "literature_matrix", offset=1).data["rows"][0]["coverage"] == "metadata_only"
    source.write_text("Changed source", encoding="utf-8")
    assert call(ws, "literature_matrix", theme="efficacy").data["rows"][0]["evidence"][0]["source_freshness"] == "changed"
    assert ws.evidence_path.read_bytes() == before
    assert call(ws, "literature_matrix", ref_ids=["missing"]).status == "error"
    assert not (ws.dir / "tasks").exists()


def test_style_defaults_and_negative_scatter_axes_are_real_data(tmp_path):
    ws = Workspace("p", tmp_path).init("figures")
    assert call(ws, "figure_style").data["style"]["palette"]
    assert not (ws.dir / "figure_style.json").exists()
    assert call(ws, "figure_style", action="set", style={"palette":["#112233"], "font_size":16}).status == "ok"
    chart = call(ws, "chart_generate", chart_type="bar", data=[-4, 6], title="Outcomes", output_filename="result.svg", save=True)
    assert chart.status == "ok", chart.errors
    spec = chart.data["spec"]
    root = ET.fromstring(spec)
    rects = [n for n in root if n.tag.endswith("rect") and n.get("fill") == "#112233"]
    assert len(rects) == 2 and all(float(n.get("height")) > 0 for n in rects)
    result = call(ws, "figure_quality_check", figure=chart.data["artifact"], requirements={"expected_text":["Outcomes"]})
    assert result.status == "ok" and result.data["sha256"] == hashlib.sha256(spec.encode()).hexdigest()
    assert result.data["checks"]["geometry"]["status"] == "unavailable"
    scatter = call(ws, "chart_generate", chart_type="scatter", data=[{"x":-3,"y":-2},{"x":5,"y":4}])
    assert scatter.status == "ok"
    coords = re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)"', scatter.data["spec"])
    assert len(coords) == 2 and all(0 <= float(x) <= 720 and 0 <= float(y) <= 420 for x,y in coords)


@pytest.mark.parametrize("data", [[], [float("nan")], [float("inf")]])
def test_chart_refuses_missing_or_nonfinite_data(tmp_path, data):
    assert call(Workspace("p",tmp_path).init("chart"), "chart_generate", data=data).status == "error"


def test_checks_do_not_approve_unavailable_or_unsafe_svg(tmp_path):
    ws = Workspace("p", tmp_path).init("figures")
    for spec in ['<svg><script>alert(1)</script></svg>', '<!DOCTYPE svg><svg/>', '<svg><image href="https://example.com/a.png"/></svg>']:
        assert call(ws, "figure_quality_check", figure={"format":"svg","spec":spec}).status == "error"
    good = '<svg xmlns="http://www.w3.org/2000/svg"><text>Actual</text></svg>'
    result = call(ws, "figure_quality_check", figure={"format":"svg","spec":good}, requirements={"expected_text":["Missing"]})
    assert result.status == "error" and result.data["checks"]["font_size"]["status"] == "partial"
    assert call(ws, "chart_generate", data=[1], output_filename="../escape.svg").status == "error"
    assert call(ws, "figure_style", action="set", style={"palette":["red;script"]}).status == "error"


def test_legacy_evidence_remains_readable():
    old = Evidence(evidence_id="old", ref_id="r", quote="legacy")
    assert old.source_coverage == "unknown" and old.source_sha256 is None


def test_agent_evidence_action_preserves_annotations_and_captures_actual_source_hash(tmp_path):
    from paperagent.agent.decision_loop import DecisionLoop
    from paperagent.schemas.actions import SaveEvidenceAction
    ws = Workspace("p",tmp_path).init("research")
    ws.save_reference_obj(Reference(ref_id="r",title="Source"))
    source=ws.dir/"source.md";source.write_text("Limited evidence",encoding="utf-8")
    loop=DecisionLoop(ws,ToolExecutor(ResearchPolicy(),ws),object(),log=lambda _:None)
    saved=loop._save_evidence(SaveEvidenceAction(ref_id="r",quote="Limited evidence",source_path=str(source),
                 relation="contradicts",source_coverage="partial",themes=["effect"],method="observational"))
    assert saved["status"]=="ok"
    evidence=ws.list_evidence()[0]
    assert evidence.source_sha256==hashlib.sha256(source.read_bytes()).hexdigest()
    assert evidence.relation=="contradicts" and evidence.themes==["effect"]
    assert loop._save_evidence(SaveEvidenceAction(ref_id="r",quote="x",source_path=str(tmp_path.parent/"outside.md")))["status"]=="error"


def test_numeric_line_preserves_actual_x_spacing(tmp_path):
    ws=Workspace("p",tmp_path).init("plot")
    result=call(ws,"chart_generate",chart_type="line",data=[{"x":-3,"y":1},{"x":-2,"y":2},{"x":5,"y":3}])
    root=ET.fromstring(result.data["spec"])
    xs=[float(n.get("cx")) for n in root if n.tag.endswith("circle")]
    assert len(xs)==3 and (xs[1]-xs[0])/(xs[2]-xs[1])==pytest.approx(1/7,rel=.001)


def test_presentation_uses_sources_notes_figures_and_retains_old_versions(tmp_path):
    ws = Workspace("p",tmp_path).init("talk")
    ws.write_draft("paper.md", "# Results\nA limited finding.")
    chart = call(ws,"chart_generate",data=[2,4],save=True,output_filename="plot.svg").data
    result = call(ws,"presentation_build",title="Study",slides=[{
        "title":"Results <script>unsafe</script>", "points":["A limited finding."], "notes":"Explain the uncertainty.",
        "sources":[{"kind":"draft","key":"paper.md","locator":"Results"}], "figure_id":chart["artifact"]["figure_id"]}])
    assert result.status == "ok", result.errors
    manifest = result.data["manifest_path"]
    text = Path(result.data["output_path"]).read_text(encoding="utf-8")
    assert "data:image/svg+xml;base64," in text and "Explain the uncertainty" in text
    assert "<script>unsafe</script>" not in text
    assert not call(ws,"research_output_check",manifest_path=manifest).data["needs_review"]
    ws.write_draft("paper.md", "# Results\nDifferent finding.")
    check = call(ws,"research_output_check",manifest_path=manifest)
    assert check.status == "ok" and any("Draft changed" in i for i in check.data["issues"])
    assert Path(result.data["output_path"]).read_text(encoding="utf-8") == text
    assert call(ws,"presentation_build",slides=[{"title":"New","figure_id":"missing"}]).status == "error"
    assert call(ws,"presentation_build",slides=[{"title":"New","sources":[{"kind":"evidence","key":"missing"}]}]).status == "error"


def test_submission_package_is_local_traceable_and_checks_archive_integrity(tmp_path):
    from zipfile import ZipFile
    from docx import Document
    ws = Workspace("p",tmp_path).init("submit")
    ws.save_reference_obj(Reference(ref_id="r1", title="Study",authors=["Author"],year=2025))
    ws.write_draft("paper.md", "# Paper\nBased on a study {{ref:r1}}.")
    ws.write_draft("cover.md", "# Cover letter\nPlease consider the enclosed manuscript.")
    output = call(ws,"submission_package",filename="paper.md",companions=["cover.md"])
    assert output.status == "ok", output.errors
    archive = Path(output.data["output_path"])
    with ZipFile(archive) as stream:
        assert {"paper.md","paper.docx","cover.md","cover.docx","package-manifest.json"} <= set(stream.namelist())
        assert "manifest.json" not in stream.namelist()
        assert str(ws.dir) not in stream.read("package-manifest.json").decode()
        assert "{{ref:r1}}" not in stream.read("paper.md").decode()
    doc = Document(archive.parent/"paper.docx")
    assert any("Based on a study" in p.text for p in doc.paragraphs)
    manifest = output.data["manifest_path"]
    assert not call(ws,"research_output_check",manifest_path=manifest).data["needs_review"]
    archive.write_bytes(b"changed")
    assert any("submission.zip" in i for i in call(ws,"research_output_check",manifest_path=manifest).data["issues"])
    ws.write_draft("bad.md","Unknown {{ref:unknown}}")
    assert call(ws,"submission_package",filename="bad.md").status == "error"
    assert call(ws,"research_output_check",manifest_path=str(tmp_path/"manifest.json")).status == "error"


def test_grant_review_reuses_versioned_reports_and_requires_criteria_sources(tmp_path):
    from paperagent.domain.paper_review import PaperReviewInput, PaperReviews
    ws = Workspace("p",tmp_path).init("grant")
    ws.write_draft("proposal.md","# Proposal\nSpecific research question.")
    reviews = PaperReviews(ws)
    basis = reviews.basis("proposal.md")
    body = dict(review_kind="grant",review_mode="readability",title="Clarity",scope="Opening paragraph",
                artifacts=[{"filename":"proposal.md","expected_sha256":basis["version"]}],
                checks=[{"dimension":"readability","coverage":"checked","basis":"Opening paragraph only"}],
                summary="No merit score; scientific validity outside scope.")
    saved = reviews.create(PaperReviewInput.model_validate(body))
    assert saved["review_kind"] == "grant" and "Review mode: readability" in reviews.report_text(saved["report_id"])
    with pytest.raises(ValueError,match="profile"):
        reviews.create(PaperReviewInput.model_validate({**body,"review_mode":"criteria"}))
    ws.write_draft("call.md","# Funding call\nSpecify objectives and feasible methods.")
    criteria = reviews.create(PaperReviewInput.model_validate({**body,"review_mode":"criteria","review_profile":"Provided call / current round",
                          "sources":[{"kind":"draft","key":"call.md"}]}))
    assert criteria["sources"][0]["snapshot"]["version"]
    ws.write_draft("call.md","New funding call")
    assert next(r for r in reviews.view()["reports"] if r["report_id"]==criteria["report_id"])["needs_review"]
    assert not (ws.dir/"grant_reviews.json").exists()


def test_research_http_views_and_download_boundaries(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("PAPERAGENT_WORKSPACE",str(tmp_path))
    from paperagent.server import app
    ws = Workspace("p",tmp_path).init("research")
    ws.write_draft("paper.md","# Paper\nA draft.")
    result = call(ws,"presentation_build",slides=[{"title":"Study","points":["Draft"],"sources":[{"kind":"draft","key":"paper.md"}]}])
    output_id = Path(result.data["manifest_path"]).parent.name
    with TestClient(app) as client:
        base = "/api/v1/projects/p"
        assert client.get(base+"/literature-matrix?limit=0").status_code == 422
        assert client.get(base+"/research-outputs").json()["outputs"][0]["output_id"] == output_id
        response = client.get(base+f"/research-outputs/{output_id}/file",params={"name":"presentation.html"})
        assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
        assert client.get(base+f"/research-outputs/{output_id}/file",params={"name":"../../../project.json"}).status_code == 404
        ws.write_draft("paper.md","Changed")
        assert client.get(base+"/research-outputs").json()["outputs"][0]["needs_review"]
