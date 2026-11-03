from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy
from paperagent.schemas.references import Reference
from paperagent.tools.registry import REGISTRY, describe_tools
from paperagent.workspace.workspace import Workspace


def test_writing_tools_registered():
    tool_ids = {tool["tool_id"] for tool in describe_tools()}

    assert {"citation_format", "draft_render"} <= tool_ids
    assert REGISTRY["citation_format"].permission == "local_read"
    assert REGISTRY["draft_render"].permission == "local_write"


def test_citation_format_uses_workspace_references(tmp_path):
    ws = Workspace("writing", root=tmp_path).init("format references")
    ws.save_reference_obj(Reference(
        ref_id="smith2025research",
        title="Research Agents for Academic Workflows",
        authors=["Jane Smith", "Li Chen"],
        year=2025,
        venue="Journal of Research Tools",
        doi="10.1234/agents",
    ))
    ex = ToolExecutor(ResearchPolicy(), ws)

    result = ex.execute(ToolCall(
        tool_id="citation_format",
        input={"style": "ieee"},
    ))

    assert result.status == "ok"
    assert result.data["style"] == "ieee"
    assert '"Research Agents for Academic Workflows"' in result.data["bibliography"][0]
    assert "doi: 10.1234/agents" in result.data["bibliography"][0]


def test_draft_render_tool_writes_rendered_draft(tmp_path):
    ws = Workspace("render", root=tmp_path).init("render draft")
    ws.save_reference_obj(Reference(
        ref_id="smith2025research",
        title="Research Agents",
        authors=["Jane Smith"],
        year=2025,
    ))
    ws.write_draft("draft.md", "Claim {{ref:smith2025research}}.")
    ex = ToolExecutor(ResearchPolicy(), ws)

    result = ex.execute(ToolCall(
        tool_id="draft_render",
        input={"filename": "draft.md", "output_filename": "draft.rendered.md"},
    ))

    assert result.status == "ok"
    assert "Claim [1]." in result.data["rendered"]
    assert "1. Jane Smith (2025). Research Agents" in result.data["rendered"]
    assert ws.read_draft("draft.rendered.md") == result.data["rendered"]
