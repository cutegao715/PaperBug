"""Figure and diagram support tools."""
from __future__ import annotations

from typing import Any

from paperagent.tools.figure_checks import figure_style, inspect_svg

from paperagent.schemas.artifacts import FigureArtifact


def run_chart_generate(input: dict, ws=None, timeout: float = 20.0) -> dict[str, Any]:
    chart_type = str(input.get("chart_type") or "bar")
    title = str(input.get("title") or "")
    data = input.get("data") or []
    width = int(input.get("width") or 720)
    height = int(input.get("height") or 420)
    svg = _svg_chart(data, chart_type=chart_type, title=title,
                     width=width, height=height, style=figure_style(ws, input.get("style")))
    output_path = None
    artifact_payload = None
    if ws is not None and input.get("output_filename"):
        output_path = ws.write_figure_file(str(input["output_filename"]), svg)
    if ws is not None and input.get("save"):
        artifact = FigureArtifact(
            figure_id=str(input.get("figure_id") or _figure_id(title, svg)),
            kind=chart_type,
            title=title,
            format="svg",
            spec=svg,
            image_path=str(output_path) if output_path else None,
            caption=str(input.get("caption") or ""),
            source_data=data,
            provenance="chart_generate",
            target_requirements=input.get("target_requirements") or {},
        )
        ws.save_artifact(artifact)
        artifact_payload = artifact.model_dump()
    return {
        "kind": chart_type,
        "title": title,
        "format": "svg",
        "spec": svg,
        "output_path": str(output_path) if output_path else None,
        "artifact": artifact_payload,
        "provenance": "inline_chart_data",
        "source_data_sha256": __import__("hashlib").sha256(__import__("json").dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
    }


def run_diagram_generate(input: dict, ws=None, timeout: float = 20.0) -> dict[str, Any]:
    kind = input.get("kind", "flowchart")
    title = input.get("title", "")
    nodes = input.get("nodes") or []
    edges = input.get("edges") or []
    direction = input.get("direction", "TD")
    mermaid = _mermaid_flowchart(nodes, edges, direction=direction)
    result = {
        "kind": kind,
        "title": title,
        "format": "mermaid",
        "spec": mermaid,
        "provenance": "inline_diagram_spec",
    }
    if ws is not None and input.get("save"):
        artifact = FigureArtifact(
            figure_id=str(input.get("figure_id") or _figure_id(title, mermaid)),
            kind=str(kind),
            title=str(title),
            format="mermaid",
            spec=mermaid,
            caption=str(input.get("caption") or ""),
            source_data={"nodes": nodes, "edges": edges},
            provenance="diagram_generate",
            target_requirements=input.get("target_requirements") or {},
        )
        ws.save_artifact(artifact)
        result["artifact"] = artifact.model_dump()
    return result


def run_figure_caption(input: dict, timeout: float = 20.0) -> dict[str, Any]:
    figure_id = input.get("figure_id", "Figure")
    title = str(input.get("title") or "").strip()
    purpose = str(input.get("purpose") or "").strip()
    variables = input.get("variables") or []
    source = str(input.get("source") or "").strip()
    parts = [f"{figure_id}. {title}" if title else f"{figure_id}."]
    if purpose:
        parts.append(purpose)
    if variables:
        parts.append("Variables: " + ", ".join(str(v) for v in variables) + ".")
    if source:
        parts.append("Source: " + source + ".")
    return {
        "caption": " ".join(parts).strip(),
        "provenance": "inline_figure_metadata",
    }


def run_figure_quality_check(input: dict, ws=None, timeout: float = 20.0) -> dict[str, Any]:
    figure = input.get("figure") or input
    fmt = str(figure.get("format") or "").lower()
    caption = str(figure.get("caption") or "").strip()
    spec = str(figure.get("spec") or "")
    source = figure.get("source_data")
    requirements = {**(figure.get("target_requirements") or {}), **(input.get("requirements") or {})}
    if figure.get("image_path"):
        if ws is None:
            raise ValueError("A workspace is required for file inspection")
        path = ws.resolve_path(figure["image_path"])
        if not ws.is_path_allowed(path) or path.suffix.lower() != ".svg":
            raise ValueError("Only authorized local SVG files can be inspected")
        spec = path.read_text(encoding="utf-8")
        fmt = "svg"
    errors = []
    warnings = []
    if fmt not in {"svg", "mermaid", "text"}:
        errors.append(f"unsupported figure format: {fmt or '<none>'}")
    if not spec:
        errors.append("figure spec is empty")
    if not caption:
        warnings.append("figure caption is missing")
    if source in (None, "", []):
        warnings.append("figure source data/provenance is missing")
    min_width = int(requirements.get("min_width", 0) or 0)
    min_height = int(requirements.get("min_height", 0) or 0)
    width, height = _svg_size(spec) if fmt == "svg" else (None, None)
    if min_width and width is not None and width < min_width:
        warnings.append(f"figure width {width} is below target {min_width}")
    if min_height and height is not None and height < min_height:
        warnings.append(f"figure height {height} is below target {min_height}")
    if fmt == "svg" and "<text" not in spec:
        warnings.append("svg has no text labels")
    detailed = inspect_svg(spec, requirements) if fmt == "svg" and spec else {"checks": {"visual_quality": {"status": "not_evaluated"}}}
    errors.extend(detailed.get("errors", []))
    warnings.extend(detailed.get("warnings", []))
    return {
        "checks": detailed.get("checks", {}), "sha256": detailed.get("sha256"),
        "interpretation": "Passed means no deterministic errors in executed checks; unavailable checks and visual quality are not certified.",
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "width": width or 0,
            "height": height or 0,
            "spec_chars": len(spec),
        },
        "provenance": "deterministic_figure_quality_check_v1",
    }


def _svg_chart(data: list[Any], chart_type: str, title: str,
               width: int, height: int, style=None) -> str:
    import math
    style = style or figure_style(None, {})
    if chart_type not in {"bar", "line", "scatter"}:
        raise ValueError("Supported chart types: bar, line, scatter")
    if not 240 <= width <= 4000 or not 200 <= height <= 4000:
        raise ValueError("Chart dimensions outside supported bounds")
    rows = _rows(data)
    if not rows or len(rows) > 200:
        raise ValueError("Chart requires 1-200 actual data rows")
    values = [r["value"] for r in rows]
    numeric_x = chart_type == "scatter" or (chart_type == "line" and all(isinstance(r["x"], (int, float)) for r in rows))
    xs = [float(r["x"] if r["x"] is not None else i) if numeric_x else float(i) for i, r in enumerate(rows)]
    if any(not math.isfinite(v) for v in values + xs):
        raise ValueError("Chart values must be finite")
    lo, hi = min(0, min(values)), max(0, max(values))
    if hi == lo:
        hi = lo + 1
    xmin, xmax = min(xs), max(xs)
    if xmin == xmax:
        xmin, xmax = xmin - .5, xmax + .5
    margin, bottom = 62, height - 62
    pw, ph = width - 124, height - 124
    y = lambda v: bottom - (v - lo) / (hi - lo) * ph
    color = style["palette"][0]
    font = _xml(style["font_family"])
    fs = style["font_size"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             f'<rect width="100%" height="100%" fill="{style["background"]}"/>',
             f'<text x="{width/2:g}" y="28" text-anchor="middle" font-family="{font}" font-size="{fs+4:g}">{_xml(title)}</text>',
             f'<path d="M {margin} {margin} V {bottom} H {width-margin}" fill="none" stroke="#333333"/>',
             f'<line x1="{margin}" x2="{width-margin}" y1="{y(0):g}" y2="{y(0):g}" stroke="#666666"/>']
    for value in sorted({lo, hi, 0}):
        parts.append(f'<text x="{margin-8}" y="{y(value):g}" text-anchor="end" font-family="{font}" font-size="{fs:g}">{value:g}</text>')
    points = []
    for i, row in enumerate(rows):
        x = margin + ((xs[i]-xmin)/(xmax-xmin)*pw if numeric_x else (i+.5)/len(rows)*pw)
        yy, zero = y(row["value"]), y(0)
        if chart_type == "bar":
            bw = pw / len(rows) * .7
            parts.append(f'<rect x="{x-bw/2:g}" y="{min(yy,zero):g}" width="{bw:g}" height="{abs(yy-zero):g}" fill="{color}"/>')
        else:
            points.append(f'{x:g},{yy:g}')
            parts.append(f'<circle cx="{x:g}" cy="{yy:g}" r="4" fill="{color}"/>')
        label = f'{xs[i]:g}' if numeric_x else row["label"]
        parts.append(f'<text x="{x:g}" y="{height-24}" text-anchor="middle" font-family="{font}" font-size="{fs:g}">{_xml(label)}</text>')
    if chart_type == "line":
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>')
    parts.append('</svg>')
    return "\n".join(parts)


def _rows(data: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for i, item in enumerate(data):
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("x") or f"item {i + 1}")
            value = float(item.get("value") if item.get("value") is not None else item.get("y", 0))
            x = item.get("x")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            label = str(item[0])
            value = float(item[1])
            x = item[0] if isinstance(item[0], (int, float)) else i
        else:
            label = str(i + 1)
            value = float(item)
            x = i
        rows.append({"label": label, "value": value, "x": x})
    return rows


def _svg_size(spec: str) -> tuple[int | None, int | None]:
    import re
    w = re.search(r'width="(\d+)', spec)
    h = re.search(r'height="(\d+)', spec)
    return (int(w.group(1)) if w else None, int(h.group(1)) if h else None)


def _figure_id(title: str, spec: str) -> str:
    import hashlib
    raw = f"{title}\n{spec}"
    return "fig_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _xml(value: str) -> str:
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mermaid_flowchart(nodes: list[Any], edges: list[Any], direction: str = "TD") -> str:
    lines = [f"flowchart {direction}"]
    known = set()
    for node in nodes:
        if isinstance(node, dict):
            node_id = _safe_id(str(node.get("id") or node.get("label") or "node"))
            label = str(node.get("label") or node_id)
        else:
            node_id = _safe_id(str(node))
            label = str(node)
        known.add(node_id)
        lines.append(f"  {node_id}[{_escape(label)}]")
    for edge in edges:
        if isinstance(edge, dict):
            source = _safe_id(str(edge.get("from") or edge.get("source") or ""))
            target = _safe_id(str(edge.get("to") or edge.get("target") or ""))
            label = str(edge.get("label") or "")
        elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
            source = _safe_id(str(edge[0]))
            target = _safe_id(str(edge[1]))
            label = str(edge[2]) if len(edge) > 2 else ""
        else:
            continue
        if not source or not target:
            continue
        if source not in known:
            lines.append(f"  {source}[{source}]")
            known.add(source)
        if target not in known:
            lines.append(f"  {target}[{target}]")
            known.add(target)
        arrow = f" -- {_escape(label)} --> " if label else " --> "
        lines.append(f"  {source}{arrow}{target}")
    return "\n".join(lines)


def _safe_id(value: str) -> str:
    import re
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if cleaned and cleaned[0].isdigit():
        cleaned = "n_" + cleaned
    return cleaned or "node"


def _escape(value: str) -> str:
    return value.replace("[", "(").replace("]", ")").replace("\n", " ")
