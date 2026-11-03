"""Deterministic project contract checks. Never claims semantic fact verification."""
import re

from paperagent.domain.project_dossier import ProjectDossier
from paperagent.schemas.artifacts import ValidationReport


def check_project_delivery(ws, text, workflow, filename=""):
    contract = (workflow or {}).get("project_contract")
    if not contract:
        return None
    errors, warnings = [], []
    strict = contract["delivery"] == "ready"
    full = contract["scope"] == "full"
    stage = contract["stage"]
    from paperagent.domain.project_acceptance import objective_matrix, STAGES
    from paperagent.domain.project_claims import ProjectClaims
    matrix = objective_matrix(ws, stage, contract.get("report_date"))
    claims = ProjectClaims(ws).audit(text, filename)
    dossier = ProjectDossier(ws)
    rows = dossier.active_records()
    records = {r["record_id"]: r for r in rows}
    bindings = dossier.bindings()
    placeholders = re.findall(r"\[to_be_confirmed\]|\[待[^\]]*\]|【待[^】]*】|待补充|待核实|待确认", text, re.I)
    used = set(re.findall(r"\{\{project:([^{}]+)\}\}", text))
    # Explicitly reviewed source links count even if the author avoids inline markers.
    used.update(key for claim in claims["claims"] for key in claim["record_ids"])
    if text.count("{{project:") != len(re.findall(r"\{\{project:([^{}]+)\}\}", text)):
        errors.append("项目事实引用格式不完整，请使用 {{project:记录ID}}")
    if placeholders:
        (errors if strict else warnings).append(f"存在 {len(placeholders)} 处待补事实标记")
    # Explicit task sections apply to partial requests too; whole template does not.
    required = contract["required_sections"] if full or contract["sections_source"] == "task" else []
    headings = [re.sub(r"^[\d一二三四五六七八九十、.（）()\s]+", "", h.strip())
                for h in re.findall(r"^\s*#{1,6}\s+(.+?)\s*$", text, re.M)]
    missing = [s for s in required if not any(s.strip() in h for h in headings)]
    if missing:
        (errors if strict else warnings).append("缺少约定章节：" + "、".join(missing))
    if full and stage != "application" and "baseline" not in bindings:
        (errors if strict else warnings).append("尚未绑定申请/任务书基线，不能核对原定承诺")
    for role, binding in bindings.items():
        applies = (role == f"template_{stage}" and full and contract["sections_source"] == "template") or (full and stage != "application" and role == "baseline")
        if binding["working_copy_changed"]:
            warnings.append(f"{role} 工作副本已变化；仍引用已绑定的固定版本")
        try:
            dossier.source_text(binding["binding_id"])
        except (ValueError, OSError):
            (errors if applies else warnings).append(f"{role} 绑定的源版本损坏或缺失，需重新绑定有效版本")
    for key in used:
        row = records.get(key)
        if not row:
            errors.append(f"项目事实引用不存在或已撤回：{key}")
        elif row["status"] != "confirmed":
            (errors if strict else warnings).append(f"引用记录尚未采纳：{key}")
    relevant = [r for r in rows if r["stage"] == stage and r["kind"] in {"progress", "output"}]
    objectives = [r for r in rows if r["kind"] == "objective" and r["status"] == "confirmed"
                  and STAGES[r["stage"]] <= STAGES[stage]]
    if full and stage != "application":
        if not objectives:
            (errors if strict else warnings).append("没有已采纳的目标记录；请先核对基线")
        for objective in objectives:
            if "baseline" in bindings and objective.get("baseline_binding_id") != bindings["baseline"]["binding_id"]:
                (errors if strict else warnings).append(f"目标 {objective['record_id']} 未按当前基线复核")
            if not any(objective["record_id"] in r["relates_to"] and r["status"] == "confirmed" for r in relevant):
                (errors if strict else warnings).append(f"目标缺少本阶段进展/未完成说明：{objective['record_id']}")
        if not relevant:
            (errors if strict else warnings).append("本阶段尚无进展/成果记录；未完成也应据实说明")
    for row in rows:
        if row["record_id"] not in used and (not full or row not in relevant):
            continue
        for issue in dossier.source_issues(row):
            (errors if strict else warnings).append(f"{row['record_id']}：{issue}")
        current_baseline = (bindings.get("baseline") or {}).get("binding_id")
        if stage != "application" and current_baseline and row.get("baseline_binding_id") != current_baseline:
            (errors if strict else warnings).append(f"{row['record_id']} 未按当前基线复核；采用新基线不会自动改写历史记录")
        for key, revision in row["related_versions"].items():
            if key not in records or records[key]["revision"] != revision:
                (errors if strict else warnings).append(f"{row['record_id']} 关联记录 {key} 已更正或撤回，需重新核对")
            elif strict:
                for issue in dossier.source_issues(records[key]):
                    errors.append(f"关联记录 {key}：{issue}")
        if row["kind"] in {"progress", "output"} and not row["relates_to"]:
            warnings.append(f"{row['record_id']} 未映射项目目标；额外成果可说明原因")
    if stage != "application" and not used:
        warnings.append("正文未使用 {{project:记录ID}} 来源标记；请人工核查正文与事实记录的对应关系")
    related_goals = set(used)
    for key in used:
        related_goals.update((records.get(key) or {}).get("relates_to", []))
    for item in matrix["items"]:
        if (full and stage != "application") or item["objective_id"] in related_goals:
            for issue in item["blockers"]:
                (errors if strict else warnings).append(f"目标对照 {item['objective_id']}：{issue}")
            for issue in item["issues"]:
                warnings.append(f"目标对照 {item['objective_id']}：{issue}")
            if item["state"] == "shortfall":
                warnings.append(f"目标 {item['objective_id']} 数量尚未达到，请据实说明；不会要求虚构完成")
    claim_policy = contract.get("claim_policy", "advisory")
    for claim in claims["claims"]:
        unresolved = claim["state"] not in ({"supported", "not_factual"} if claim_policy == "reviewed"
                                           else {"sourced", "supported", "not_factual"})
        if unresolved:
            target = errors if strict and claim_policy != "advisory" else warnings
            target.append(f"关键声明需核查（第 {claim['line']} 行）：{claim['text'][:90]} [{claim['state']}]")
    warnings.append("本检查只校验结构、来源关联与确认状态；事实真实性、完成程度及经费合规仍需人工核查")
    return ValidationReport(kernel="project_delivery", errors=errors, warnings=warnings,
                            stats={"placeholders": len(placeholders), "missing_sections": len(missing),
                                   "project_citations": len(used), "records": len(rows),
                                   "objective_count": len(matrix["items"]),
                                   "claim_candidates": claims["candidate_count"],
                                   "claims_unresolved": claims["unresolved"]})
