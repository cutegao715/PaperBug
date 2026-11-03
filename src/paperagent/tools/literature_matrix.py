"""Derived, paginated synthesis view over the existing reference/evidence stores."""
from __future__ import annotations

import hashlib
from collections import defaultdict


def run(input, ws, timeout=20.0):
    offset, limit = int(input.get("offset", 0)), int(input.get("limit", 20))
    if offset < 0 or not 1 <= limit <= 50:
        raise ValueError("Invalid literature matrix page")
    evidence_limit = int(input.get("evidence_limit", 5))
    if not 1 <= evidence_limit <= 20:
        raise ValueError("Invalid evidence preview limit")
    refs = {r.ref_id: r for r in ws.list_references()}
    evidence = ws.list_evidence()
    groups = defaultdict(list)
    selected = set(input.get("ref_ids") or [])
    if selected - refs.keys():
        raise ValueError("Unknown reference IDs")
    theme = input.get("theme")
    for ev in evidence:
        if (not selected or ev.ref_id in selected) and (not theme or theme in ev.themes):
            groups[ev.ref_id].append(ev)
    ids = [key for key in refs if (not selected or key in selected) and (not theme or key in groups)]
    rows = []
    for key in ids[offset:offset + limit]:
        cards = []
        for ev in groups[key][-evidence_limit:]:
            freshness = "untracked"
            if ev.source_path and ev.source_sha256:
                path = ws.resolve_path(ev.source_path)
                if not ws.is_path_allowed(path) or not path.is_file():
                    freshness = "unavailable"
                else:
                    freshness = "current" if hashlib.sha256(path.read_bytes()).hexdigest() == ev.source_sha256 else "changed"
            cards.append({**ev.model_dump(), "quote":ev.quote[:800], "quote_truncated":len(ev.quote)>800,
                          "source_freshness": freshness,
                          "verification": "not_evaluated", "read_with": {
                              "action": "read_context", "kind": "evidence", "key": ev.evidence_id}})
        rows.append({"reference": refs[key].model_dump(), "evidence": cards,
                     "evidence_count":len(groups[key]), "omitted_evidence":max(0,len(groups[key])-len(cards)),
                     "coverage": "has_evidence" if cards else "metadata_only"})
    return {"rows": rows, "total": len(ids), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(ids) else None,
            "provenance": "workspace:references+evidence", "derived_view": True,
            "interpretation": "Relations, findings and themes are annotations; source freshness is not quote or scientific verification. This is not an exhaustive-search certificate."}
