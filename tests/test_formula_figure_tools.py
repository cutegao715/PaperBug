from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy
from paperagent.tools.registry import REGISTRY, describe_tools
from paperagent.workspace.workspace import Workspace


def test_formula_and_figure_tools_registered():
    tool_ids = {tool["tool_id"] for tool in describe_tools()}

    assert {
        "formula_check",
        "symbol_table_builder",
        "chart_generate",
        "diagram_generate",
        "figure_caption",
        "figure_quality_check",
    } <= tool_ids
    assert REGISTRY["formula_check"].permission == "local_read"
    assert REGISTRY["symbol_table_builder"].permission == "local_read"
    assert REGISTRY["chart_generate"].permission == "local_write"
    assert REGISTRY["diagram_generate"].permission == "local_read"
    assert REGISTRY["figure_caption"].permission == "local_read"
    assert REGISTRY["figure_quality_check"].permission == "local_read"


def test_formula_check_identifies_undefined_symbols(tmp_path):
    ws = Workspace("formula", root=tmp_path).init("check formula")
    ex = ToolExecutor(ResearchPolicy(), ws)

    result = ex.execute(ToolCall(tool_id="formula_check", input={
        "latex": "Y = \\alpha + \\beta X + \\epsilon",
        "symbols": {
            "Y": "outcome",
            "\\alpha": "intercept",
            "\\beta": "effect size",
        },
    }))

    assert result.status == "ok"
    assert "X" in result.data["undefined_symbols"]
    assert "\\epsilon" in result.data["undefined_symbols"]
    assert any(s["symbol"] == "\\beta" and s["defined"]
               for s in result.data["symbols"])


def test_formula_check_can_save_artifact_and_build_symbol_table(tmp_path):
    ws = Workspace("formula-artifact", root=tmp_path).init("save formula")
    ex = ToolExecutor(ResearchPolicy(), ws)

    checked = ex.execute(ToolCall(tool_id="formula_check", input={
        "formula_id": "f_regression",
        "display_name": "Regression model",
        "latex": "Y = \\alpha + \\beta X",
        "symbols": {
            "Y": "outcome",
            "\\alpha": "intercept",
            "\\beta": "effect",
            "X": "predictor",
        },
        "save": True,
    }))
    table = ex.execute(ToolCall(tool_id="symbol_table_builder", input={
        "symbols": checked.data["symbols"],
    }))

    assert checked.status == "ok"
    assert checked.data["artifact"]["formula_id"] == "f_regression"
    assert ws.list_artifacts()[0].artifact_type == "formula"
    assert "| Symbol | Meaning | Unit |" in table.data["markdown"]
    assert "\\beta" in table.data["markdown"]


def test_diagram_generate_mermaid_and_caption(tmp_path):
    ws = Workspace("figure", root=tmp_path).init("make diagram")
    ex = ToolExecutor(ResearchPolicy(), ws)

    diagram = ex.execute(ToolCall(tool_id="diagram_generate", input={
        "title": "Research Framework",
        "nodes": [{"id": "theory", "label": "Theory"},
                  {"id": "method", "label": "Method"},
                  {"id": "findings", "label": "Findings"}],
        "edges": [{"from": "theory", "to": "method", "label": "guides"},
                  {"from": "method", "to": "findings"}],
    }))
    caption = ex.execute(ToolCall(tool_id="figure_caption", input={
        "figure_id": "Figure 1",
        "title": "Research framework",
        "purpose": "Shows the logic from theory to findings.",
        "variables": ["Theory", "Method", "Findings"],
    }))

    assert diagram.status == "ok"
    assert diagram.data["format"] == "mermaid"
    assert "theory -- guides --> method" in diagram.data["spec"]
    assert caption.status == "ok"
    assert caption.data["caption"].startswith("Figure 1. Research framework")


def test_chart_generate_saves_svg_artifact_and_quality_check(tmp_path):
    ws = Workspace("chart", root=tmp_path).init("make chart")
    ex = ToolExecutor(ResearchPolicy(), ws)

    chart = ex.execute(ToolCall(tool_id="chart_generate", input={
        "figure_id": "fig_outcomes",
        "chart_type": "bar",
        "title": "Learning outcomes",
        "data": [{"label": "Control", "value": 72},
                 {"label": "Treatment", "value": 88}],
        "caption": "Figure 2. Learning outcomes by group.",
        "output_filename": "outcomes.svg",
        "target_requirements": {"min_width": 600, "min_height": 300},
        "save": True,
    }))
    quality = ex.execute(ToolCall(tool_id="figure_quality_check", input={
        "figure": chart.data["artifact"],
        "requirements": {"min_width": 600, "min_height": 300},
    }))

    assert chart.status == "ok"
    assert chart.data["format"] == "svg"
    assert "<svg" in chart.data["spec"]
    assert "outcomes.svg" in chart.data["output_path"]
    assert ws.list_artifacts()[0].artifact_type == "figure"
    assert quality.status == "ok"
    assert quality.data["passed"] is True
    assert quality.data["warnings"] == []
