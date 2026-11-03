"""Tool Executor: the single enforcement point for every external call.

Responsibilities (and nothing else): schema validation, timeout, tool
call budget, provenance recording, uniform ToolResult. Policy provides
parameters; this module enforces them.
"""
from __future__ import annotations

import uuid
import time
from pathlib import Path

from paperagent.schemas.artifacts import ToolCall, ToolResult
from paperagent.schemas.policy import ResearchPolicy
from paperagent.tools import (figure_checks, literature_matrix, research_delivery, figures, formulas, importers, literature_search,
                              opportunities, pdf_reader, references, web,
                              writing)
from paperagent.tools.registry import REGISTRY
from paperagent.workspace.workspace import Workspace

class BudgetExceeded(Exception):
    pass


class ToolExecutor:
    def __init__(self, policy: ResearchPolicy, ws: Workspace,
                 parent_run_id: str | None = None, role=None):
        from paperagent.quality.policies import effective_role
        self.role = effective_role(role)
        self.policy = policy
        self.ws = ws
        self.calls_made = 0
        self.parent_run_id = parent_run_id

    @property
    def budget_remaining(self) -> int:
        return max(0, self.policy.max_tool_calls - self.calls_made)

    def execute(self, call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        call_id = call.call_id or f"call_{uuid.uuid4().hex[:12]}"
        self.calls_made += 1
        if self.calls_made > self.policy.max_tool_calls:
            raise BudgetExceeded(
                f"tool call budget exhausted ({self.policy.max_tool_calls})")

        from paperagent.product_config import load
        if call.tool_id in load().tools.disabled:
            result = self._fail(call.tool_id, started, ["tool disabled by product configuration"], provenance="")
            self._record(call_id, call, None, result)
            return result
        desc = REGISTRY.get(call.tool_id)
        if desc is None and call.tool_id.startswith("mcp_"):
            from paperagent.tools.mcp_bridge import get_bridge
            desc = get_bridge(self.ws.root).discover().get(call.tool_id)
        if desc is None:
            result = self._fail(call.tool_id, started,
                                [f"unknown tool: {call.tool_id}"], provenance="")
            self._record(call_id, call, desc, result)
            return result
        from paperagent.quality.policies import ActionSpec, PermissionGate
        decision = PermissionGate().check(ActionSpec(desc.permission), role=self.role)
        if decision != "allow":
            result = self._fail(call.tool_id, started,
                                [f"permission gate: {call.tool_id} -> {decision}"],
                                provenance="")
            self._record(call_id, call, desc, result)
            return result

        path_error = self._check_filesystem_permission(call, desc.permission)
        if path_error:
            result = self._fail(call.tool_id, started, [path_error], provenance="")
            self._record(call_id, call, desc, result)
            return result

        try:
            if call.tool_id.startswith("mcp_"):
                from paperagent.tools.mcp_bridge import get_bridge
                data = get_bridge(self.ws.root).call(call.tool_id, call.input)
            elif call.tool_id == "workflow_case":
                from paperagent.tools.workflow_cases import run
                data = run(call.input, self.ws)
            elif call.tool_id == "literature_search":
                data = literature_search.run(call.input, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "pdf_reader":
                data = pdf_reader.run(call.input, resolve=self.ws.resolve_path,
                                      timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "web_search":
                data = web.run_search(call.input, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "page_fetch":
                data = web.run_fetch(call.input, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "html_extract":
                data = web.run_extract(call.input, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "file_import":
                file_input = dict(call.input)
                file_input.setdefault("conversion_dir", str(self.ws.dir / "converted"))
                data = importers.run_file_import(
                    file_input, resolve=self.ws.resolve_path,
                    timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "reference_resolve":
                data = references.run_reference_resolve(
                    call.input, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "opportunity_extract":
                data = opportunities.run_opportunity_extract(
                    call.input, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "citation_format":
                data = writing.run_citation_format(
                    call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "draft_render":
                data = writing.run_draft_render(
                    call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "template_extract":
                data = writing.run_template_extract(
                    call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "fill_docx_template":
                data = writing.run_fill_docx_template(
                    call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "formula_check":
                data = formulas.run_formula_check(
                    call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "symbol_table_builder":
                data = formulas.run_symbol_table_builder(
                    call.input, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "chart_generate":
                data = figures.run_chart_generate(
                    call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "diagram_generate":
                data = figures.run_diagram_generate(
                    call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "figure_caption":
                data = figures.run_figure_caption(
                    call.input, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "presentation_build":
                data = research_delivery.run_presentation(call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "submission_package":
                data = research_delivery.run_submission(call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "research_output_check":
                data = research_delivery.run_check(call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "literature_matrix":
                data = literature_matrix.run(call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "figure_style":
                data = figure_checks.run_style(call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            elif call.tool_id == "figure_quality_check":
                data = figures.run_figure_quality_check(
                    call.input, ws=self.ws, timeout=self.policy.tool_timeout_s)
            else:  # pragma: no cover - registry dispatch is explicit in V2-m1
                data = {"error": f"no handler bound for {call.tool_id}"}
        except TimeoutError as e:
            result = self._fail(call.tool_id, started, [f"timeout: {e}"], provenance="")
            self._record(call_id, call, desc, result)
            return result
        except Exception as e:  # network errors, extraction errors, ...
            result = self._fail(call.tool_id, started,
                                [f"{type(e).__name__}: {e}"], provenance="")
            self._record(call_id, call, desc, result)
            return result

        errors = data.get("errors", [])
        if "error" in data:
            errors.append(data["error"])
        result = ToolResult(
            tool_id=call.tool_id,
            status="ok" if not errors else "error",
            data=data,
            errors=errors,
            latency_ms=int((time.perf_counter() - started) * 1000),
            provenance=data.get("provenance", ""),
        )
        self._record(call_id, call, desc, result)
        return result

    def _check_filesystem_permission(self, call: ToolCall, permission: str) -> str:
        if not permission.startswith("local_"):
            return ""
        path_value = call.input.get("path") or call.input.get("template_path")
        if not path_value:
            return ""
        path = Path(str(path_value))
        resolved = path if path.is_absolute() else self.ws.resolve_path(str(path))
        if self.ws.is_path_allowed(resolved, write=permission == "local_write"):
            return ""
        allowed = self.ws.list_allowed_roots()
        return (
            f"filesystem permission denied for {resolved}. "
            f"Allowed roots: {allowed}. Ask the user to import or authorize this folder first."
        )

    def _record(self, call_id: str, call: ToolCall, desc, result: ToolResult) -> None:
        try:
            self.ws.record_tool_call({
                "call_id": call_id,
                "parent_run_id": call.parent_run_id or self.parent_run_id,
                "tool_id": call.tool_id,
                "permission": getattr(desc, "permission", ""),
                "input": self._compact(call.input),
                "status": result.status,
                "errors": result.errors,
                "latency_ms": result.latency_ms,
                "provenance": result.provenance,
                "output": self._compact(result.data),
            })
        except Exception:
            pass

    @staticmethod
    def _compact(value):
        if isinstance(value, str):
            return value[:1000]
        if isinstance(value, list):
            return [ToolExecutor._compact(v) for v in value[:10]]
        if isinstance(value, dict):
            return {k: ToolExecutor._compact(v)
                    for k, v in list(value.items())[:20]}
        return value

    @staticmethod
    def _fail(tool_id: str, started: float, errors: list[str],
              provenance: str) -> ToolResult:
        return ToolResult(
            tool_id=tool_id,
            status="error",
            errors=errors,
            latency_ms=int((time.perf_counter() - started) * 1000),
            provenance=provenance,
        )
