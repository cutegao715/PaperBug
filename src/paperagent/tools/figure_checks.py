"""Measured figure checks. No heuristic is represented as visual approval."""
from __future__ import annotations

import hashlib
import math
import re
from xml.etree import ElementTree as ET

from paperagent.workspace.state_store import read_json, write_json, state_lock


DEFAULT_STYLE = {"palette": ["#0072B2", "#D55E00", "#009E73"], "font_family": "Arial",
                 "font_size": 14, "background": "#FFFFFF"}


def validate_style(value):
    if set(value) - DEFAULT_STYLE.keys():
        raise ValueError("Unknown figure style field")
    data = {**DEFAULT_STYLE, **value}
    colors = data["palette"]
    if not isinstance(colors, list) or not 1 <= len(colors) <= 12:
        raise ValueError("Palette needs 1-12 colors")
    if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in [*colors, data["background"]]):
        raise ValueError("Colors must be six-digit hex values")
    if not re.fullmatch(r"[A-Za-z0-9 ,_-]{1,80}", data["font_family"]):
        raise ValueError("Invalid font family")
    size = float(data["font_size"])
    if not math.isfinite(size) or not 8 <= size <= 48:
        raise ValueError("Font size must be 8-48 SVG user units")
    data["font_size"] = size
    return data


def run_style(input, ws, timeout=20.0):
    path = ws.dir / "figure_style.json"
    if input.get("action", "get") == "set":
        style = validate_style(input.get("style") or {})
        with state_lock(path):
            write_json(path, style)
    elif input.get("action", "get") != "get":
        raise ValueError("Figure style action must be get or set")
    style = validate_style(read_json(path, DEFAULT_STYLE))
    return {"style": style, "provenance": "workspace:figure_style", "scope": "workspace_default; per-figure overrides allowed"}


def figure_style(ws, override):
    return validate_style({**(run_style({}, ws)["style"] if ws else DEFAULT_STYLE), **(override or {})})


def svg_root(spec):
    if re.search(r"<!DOCTYPE|<!ENTITY", spec, re.I):
        raise ValueError("SVG document types and entities are unsupported")
    root = ET.fromstring(spec)
    if root.tag.split("}")[-1] != "svg":
        raise ValueError("Figure is not an SVG document")
    for node in root.iter():
        if node.tag.split("}")[-1] in {"script", "foreignObject", "iframe"}:
            raise ValueError("Active SVG content is unsupported")
        for key, value in node.attrib.items():
            local = key.split("}")[-1].lower()
            if local.startswith("on") or (local in {"href", "src"} and not value.startswith("#")):
                raise ValueError("External or active SVG content is unsupported")
            if any(not target.strip(" \t\r\n'\"").startswith("#")
                   for target in re.findall(r"url\(([^)]*)\)", value, re.I)):
                raise ValueError("External SVG style resources are unsupported")
        if node.tag.split("}")[-1] == "style" and re.search(r"@import|url\(", node.text or "", re.I):
            raise ValueError("External SVG styles are unsupported")
    return root


def inspect_svg(spec, requirements):
    errors, warnings, checks = [], [], {}
    try:
        root = svg_root(spec)
    except (ValueError, ET.ParseError) as exc:
        return {"errors": [str(exc)], "warnings": [], "checks": {"svg_parse": "failed"}}
    checks["svg_parse"] = "checked"
    texts = [n for n in root.iter() if n.tag.split("}")[-1] == "text"]
    content = " ".join("".join(n.itertext()) for n in texts)
    expected = requirements.get("expected_text") or []
    missing = [s for s in expected if s not in content]
    if missing:
        errors.append("Missing expected SVG text: " + ", ".join(missing))
    checks["expected_text"] = {"status": "checked" if expected else "not_requested", "missing": missing}
    sizes = []
    unresolved = 0
    for node in texts:
        value = node.get("font-size")
        if value and re.fullmatch(r"[0-9.]+", value):
            sizes.append(float(value))
        else:
            unresolved += 1
    if sizes and min(sizes) < float(requirements.get("min_font_size", 10)):
        warnings.append("Some explicit text is smaller than the requested SVG font size")
    checks["font_size"] = {"status": "partial" if unresolved else "checked",
                            "minimum_explicit": min(sizes) if sizes else None,
                            "unresolved_text_nodes": unresolved, "unit": "SVG user units; not measured print points"}
    checks["geometry"] = {"status": "unavailable", "reason": "No text shaping or geometry renderer; overlap and clipping require rendered inspection"}
    checks["visual_quality"] = {"status": "not_evaluated"}
    return {"errors": errors, "warnings": warnings, "checks": checks,
            "sha256": hashlib.sha256(spec.encode()).hexdigest()}
