"""V2 tool registry: static metadata + execution contracts.

Health/SLA/gray-rollout discovery is a later concern (see architecture
notes); with two tools it would be pure overhead. Dispatch lives in the
ToolExecutor so enforcement stays in one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDescriptor:
    tool_id: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    permission: str = "local_read"
    output_contract: dict[str, Any] = field(default_factory=dict)
    timeout_s: int = 30
    retry_policy: dict[str, Any] = field(default_factory=lambda: {
        "max_attempts": 1,
        "retry_on": ["timeout", "transient_error"],
    })
    side_effects: list[str] = field(default_factory=list)


REGISTRY: dict[str, ToolDescriptor] = {
    "workflow_case": ToolDescriptor(
        tool_id="workflow_case", description="Read durable workflow cases or propose step status, decisions, input/output versions and quality findings. User approval and lifecycle changes are unavailable to agents.",
        input_schema={"type": "object", "required": ["operation"], "properties": {
            "operation": {"type": "string", "enum": ["list", "read", "step", "decision", "inputs", "output", "quality"]},
            "case_id": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 1},
            "data": {"type": "object"}}},
        permission="local_write", output_contract={"contains": "case or cases"}),
    "literature_search": ToolDescriptor(
        tool_id="literature_search",
        description="Search academic literature. Input: {query, max_results?, provider?}",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                "provider": {"type": "string",
                             "enum": ["semantic_scholar", "offline_stub"]},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "candidates"},
    ),
    "pdf_reader": ToolDescriptor(
        tool_id="pdf_reader",
        description="Read a local PDF page by page. Input: {path, pages?}",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "pages": {"type": "array", "items": {"type": "integer"}},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "pages"},
    ),
    "web_search": ToolDescriptor(
        tool_id="web_search",
        description="Search the public web. Input: {query, max_results?, provider?}",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                "provider": {"type": "string",
                             "enum": ["duckduckgo", "offline_stub"]},
            },
        },
        permission="network_read",
        output_contract={"type": "object", "contains": "results"},
    ),
    "page_fetch": ToolDescriptor(
        tool_id="page_fetch",
        description="Fetch public HTML and source snapshot metadata. Input: {url}",
        input_schema={
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "respect_robots": {"type": "boolean"},
                "min_delay_s": {"type": "number", "minimum": 0},
            },
        },
        permission="network_read",
        output_contract={"type": "object", "contains": "html"},
    ),
    "html_extract": ToolDescriptor(
        tool_id="html_extract",
        description="Extract title, readable text, date, and links from HTML. Input: {html, url?}",
        input_schema={
            "type": "object",
            "required": ["html"],
            "properties": {
                "html": {"type": "string"},
                "url": {"type": "string"},
                "max_links": {"type": "integer", "minimum": 0, "maximum": 100},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "text"},
    ),
    "file_import": ToolDescriptor(
        tool_id="file_import",
        description="Import a local PDF, DOC/DOCX/WPS, Markdown, BibTeX, or RIS file. Legacy DOC/WPS is converted with local Word/WPS when available. Input: {path}",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "kind"},
    ),
    "reference_resolve": ToolDescriptor(
        tool_id="reference_resolve",
        description="Resolve reference metadata by DOI or title. Input: {doi?, title?, provider?}",
        input_schema={
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
                "title": {"type": "string"},
                "provider": {"type": "string",
                             "enum": ["crossref", "offline_stub"]},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        },
        permission="network_read",
        output_contract={"type": "object", "contains": "candidates"},
    ),
    "opportunity_extract": ToolDescriptor(
        tool_id="opportunity_extract",
        description="Extract journal/conference/grant/school opportunities from text. Input: {title?, text, url?, source?}",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {
                "title": {"type": "string"},
                "text": {"type": "string"},
                "url": {"type": "string"},
                "source": {"type": "string"},
                "opportunity_type": {"type": "string"},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "opportunities"},
    ),
    "citation_format": ToolDescriptor(
        tool_id="citation_format",
        description="Format references in APA, GB/T 7714, IEEE, Chicago, or Vancouver style. Input: {style?, references?}",
        input_schema={
            "type": "object",
            "properties": {
                "style": {"type": "string"},
                "references": {"type": "array"},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "bibliography"},
    ),
    "draft_render": ToolDescriptor(
        tool_id="draft_render",
        description="Render a workspace draft's {{ref:id}} placeholders into numbered citations. Input: {filename, output_filename?}",
        input_schema={
            "type": "object",
            "required": ["filename"],
            "properties": {
                "filename": {"type": "string"},
                "output_filename": {"type": "string"},
            },
        },
        permission="local_write",
        output_contract={"type": "object", "contains": "rendered"},
    ),
    "template_extract": ToolDescriptor(
        tool_id="template_extract",
        description="Inspect a DOCX application template and extract table fields/labels. Input: {path}",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "fields"},
    ),
    "fill_docx_template": ToolDescriptor(
        tool_id="fill_docx_template",
        description="Fill a DOCX template's table fields from a workspace draft. Input: {filename, template_path}",
        input_schema={
            "type": "object",
            "required": ["filename", "template_path"],
            "properties": {
                "filename": {"type": "string"},
                "template_path": {"type": "string"},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "output_path"},
    ),
    "formula_check": ToolDescriptor(
        tool_id="formula_check",
        description="Check a LaTeX formula for symbol definitions. Input: {latex, symbols?, save?}",
        input_schema={
            "type": "object",
            "required": ["latex"],
            "properties": {
                "latex": {"type": "string"},
                "symbols": {},
                "save": {"type": "boolean"},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "symbols"},
    ),
    "symbol_table_builder": ToolDescriptor(
        tool_id="symbol_table_builder",
        description="Build a Markdown symbol table. Input: {symbols}",
        input_schema={
            "type": "object",
            "required": ["symbols"],
            "properties": {"symbols": {}},
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "markdown"},
    ),
    "chart_generate": ToolDescriptor(
        tool_id="chart_generate",
        description="Generate an SVG bar, line, or scatter chart. Input: {data, chart_type?, title?, output_filename?, save?}",
        input_schema={
            "type": "object",
            "required": ["data"],
            "properties": {
                "chart_type": {"type": "string"},
                "title": {"type": "string"},
                "data": {"type": "array"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "output_filename": {"type": "string"},
                "save": {"type": "boolean"},
            },
        },
        permission="local_write",
        output_contract={"type": "object", "contains": "spec"},
    ),
    "diagram_generate": ToolDescriptor(
        tool_id="diagram_generate",
        description="Generate a Mermaid diagram from nodes and edges. Input: {nodes, edges?, title?, kind?, save?}",
        input_schema={
            "type": "object",
            "required": ["nodes"],
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "nodes": {"type": "array"},
                "edges": {"type": "array"},
                "direction": {"type": "string"},
                "save": {"type": "boolean"},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "spec"},
    ),
    "figure_caption": ToolDescriptor(
        tool_id="figure_caption",
        description="Generate a structured figure caption. Input: {figure_id?, title?, purpose?, variables?, source?}",
        input_schema={
            "type": "object",
            "properties": {
                "figure_id": {"type": "string"},
                "title": {"type": "string"},
                "purpose": {"type": "string"},
                "variables": {"type": "array"},
                "source": {"type": "string"},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "caption"},
    ),
    "figure_quality_check": ToolDescriptor(
        tool_id="figure_quality_check",
        description="Check figure format, caption, source data, and basic size constraints. Input: {figure, requirements?}",
        input_schema={
            "type": "object",
            "properties": {
                "figure": {"type": "object"},
                "requirements": {"type": "object"},
            },
        },
        permission="local_read",
        output_contract={"type": "object", "contains": "passed"},
    ),
}


REGISTRY.update({
    "literature_matrix": ToolDescriptor(
        tool_id="literature_matrix", description="Read a paginated synthesis matrix derived from saved references and evidence; annotations are not scientific verification.",
        input_schema={"type":"object", "properties":{"ref_ids":{"type":"array"}, "theme":{"type":"string"}, "offset":{"type":"integer"}, "limit":{"type":"integer"}}},
        permission="local_read", output_contract={"contains":"rows"}),
    "figure_style": ToolDescriptor(
        tool_id="figure_style", description="Read or set optional workspace figure defaults; individual figures may override them.",
        input_schema={"type":"object", "properties":{"action":{"type":"string", "enum":["get","set"]}, "style":{"type":"object"}}},
        permission="local_write", output_contract={"contains":"style"}),
})
REGISTRY["chart_generate"].input_schema["properties"]["style"] = {"type":"object"}

REGISTRY.update({
    "presentation_build": ToolDescriptor(
        tool_id="presentation_build", description="Create an editable PPTX, HTML preview, slides JSON, speaker notes and version manifest. SVG figures are supplied separately for PPTX insertion; visual layout requires review.",
        input_schema={"type":"object","required":["slides"],"properties":{"title":{"type":"string"},"slides":{"type":"array"}}},
        permission="local_write", output_contract={"contains":"output_path"}),
    "submission_package": ToolDescriptor(
        tool_id="submission_package", description="Package saved manuscript and companion drafts as rendered Markdown/DOCX with selected SVG figures, captions and source manifest; never submit.",
        input_schema={"type":"object","required":["filename"],"properties":{"filename":{"type":"string"},"companions":{"type":"array"},"figure_ids":{"type":"array"},"requirement_sources":{"type":"array"}}},
        permission="local_write", output_contract={"contains":"output_path"}),
    "research_output_check": ToolDescriptor(
        tool_id="research_output_check", description="Check an existing delivery manifest for changed sources, figures and outputs; does not certify science or layout.",
        input_schema={"type":"object","required":["manifest_path"],"properties":{"manifest_path":{"type":"string"}}},
        permission="local_read", output_contract={"contains":"needs_review"}),
})


def describe_tools(workspace_root=None) -> list[dict[str, Any]]:
    from paperagent.product_config import load
    disabled = load().tools.disabled
    descriptors = dict(REGISTRY)
    if workspace_root is not None:
        from paperagent.tools.mcp_bridge import get_bridge
        descriptors.update(get_bridge(workspace_root).discover())
    return [
        {
            "tool_id": desc.tool_id,
            "description": desc.description,
            "input_schema": desc.input_schema,
            "permission": desc.permission,
            "output_contract": desc.output_contract,
            "timeout_s": desc.timeout_s,
            "retry_policy": desc.retry_policy,
            "side_effects": desc.side_effects,
        }
        for desc in descriptors.values() if desc.tool_id not in disabled
    ]
