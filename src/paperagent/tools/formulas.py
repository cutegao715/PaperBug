"""Formula support tools."""
from __future__ import annotations

import re
from typing import Any

from paperagent.schemas.artifacts import FormulaArtifact

GREEK = {
    "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda",
    "mu", "pi", "rho", "sigma", "tau", "phi", "omega",
}
LATEX_COMMANDS = {
    "frac", "sum", "prod", "int", "left", "right", "cdot", "times",
    "log", "ln", "exp", "sin", "cos", "tan", "sqrt", "bar", "hat",
    "tilde", "mathbf", "mathrm", "text", "begin", "end", "label",
}


def run_formula_check(input: dict, ws=None, timeout: float = 20.0) -> dict[str, Any]:
    latex = str(input.get("latex") or "")
    definitions = input.get("symbols") or {}
    if isinstance(definitions, list):
        definitions = {str(item.get("symbol")): item.get("meaning", "")
                       for item in definitions if isinstance(item, dict)}
    symbols = _extract_symbols(latex)
    undefined = [s for s in symbols if s not in definitions]
    duplicate_definitions = _duplicate_definitions(input.get("symbols"))
    warnings = []
    if undefined:
        warnings.append("formula has symbols without definitions")
    if duplicate_definitions:
        warnings.append("formula has duplicate symbol definitions")
    result = {
        "latex": latex,
        "symbols": [
            {"symbol": s, "meaning": definitions.get(s, ""), "defined": s in definitions}
            for s in symbols
        ],
        "undefined_symbols": undefined,
        "duplicate_definitions": duplicate_definitions,
        "warnings": warnings,
        "provenance": "inline_formula",
    }
    if ws is not None and input.get("save"):
        artifact = FormulaArtifact(
            formula_id=str(input.get("formula_id") or _formula_id(latex)),
            latex=latex,
            display_name=str(input.get("display_name") or ""),
            symbols=result["symbols"],
            assumptions=[str(a) for a in input.get("assumptions", [])],
            source_or_derivation=str(input.get("source_or_derivation") or ""),
            used_in_sections=[str(s) for s in input.get("used_in_sections", [])],
            provenance="formula_check",
        )
        ws.save_artifact(artifact)
        result["artifact"] = artifact.model_dump()
    return result


def run_symbol_table_builder(input: dict, timeout: float = 20.0) -> dict[str, Any]:
    symbols = input.get("symbols") or []
    if isinstance(symbols, dict):
        rows = [{"symbol": k, "meaning": v} for k, v in symbols.items()]
    else:
        rows = [s for s in symbols if isinstance(s, dict)]
    markdown = ["| Symbol | Meaning | Unit |", "|---|---|---|"]
    for row in rows:
        markdown.append(
            f"| {row.get('symbol', '')} | {row.get('meaning', '')} | {row.get('unit', '')} |"
        )
    return {
        "symbols": rows,
        "markdown": "\n".join(markdown),
        "provenance": "inline_symbol_table",
    }


def _extract_symbols(latex: str) -> list[str]:
    command_symbols = [
        "\\" + c for c in re.findall(r"\\([A-Za-z]+)", latex)
        if c in GREEK and c not in LATEX_COMMANDS
    ]
    text = re.sub(r"\\[A-Za-z]+", " ", latex)
    text = re.sub(r"\\.", " ", text)
    letter_symbols = re.findall(r"\b[A-Za-z]\b(?:_\{?[\w]+\}?|\^\{?[\w]+\}?)*", text)
    raw = command_symbols + letter_symbols
    out = []
    seen = set()
    for symbol in raw:
        cleaned = symbol.replace("{", "").replace("}", "")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def _duplicate_definitions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen = set()
    dupes = []
    for item in value:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "")
        if not symbol:
            continue
        if symbol in seen and symbol not in dupes:
            dupes.append(symbol)
        seen.add(symbol)
    return dupes


def _formula_id(latex: str) -> str:
    import hashlib
    return "formula_" + hashlib.sha1(latex.encode("utf-8")).hexdigest()[:10]
