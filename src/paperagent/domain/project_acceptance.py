"""Derived goal matrix. Numeric comparisons are not semantic acceptance decisions."""
from collections import Counter
from datetime import date

from paperagent.domain.project_dossier import ProjectDossier

STAGES = {"application": 0, "midterm": 1, "final": 2}


def objective_matrix(ws, stage, as_of=None):
    if stage not in STAGES:
        raise ValueError("Unknown project stage")
    cutoff = date.fromisoformat(as_of) if as_of else date.today()
    ds = ProjectDossier(ws)
    records = ds.active_records()
    baseline = (ds.bindings().get("baseline") or {}).get("binding_id")
    items = []
    for target in records:
        if target["kind"] != "objective" or target["status"] != "confirmed":
            continue
        if STAGES[target["stage"]] > STAGES[stage]:
            continue
        criteria = target.get("criteria") or {}
        linked = [r for r in records if r["kind"] in {"progress", "output"}
                  and target["record_id"] in r["relates_to"] and STAGES[r["stage"]] <= STAGES[stage]]
        candidates = []
        for row in linked:
            for measurement in row.get("measurements", []):
                if measurement["objective_id"] == target["record_id"] and measurement["as_of"] <= cutoff.isoformat() and row["status"] == "confirmed":
                    candidates.append((measurement, row))
        latest_date = max((m["as_of"] for m, _ in candidates), default=None)
        latest = [(m, r) for m, r in candidates if m["as_of"] == latest_date]
        latest.sort(key=lambda pair: (STAGES[pair[1]["stage"]], pair[1]["created_at"]), reverse=True)
        issues, blockers = [], []
        if baseline and target.get("baseline_binding_id") != baseline:
            blockers.append("目标与当前基线尚未复核")
        blockers.extend(ds.source_issues(target))
        signatures = {(m["quantity"], m["unit"], m["completion"]) for m, _ in latest}
        measurement, source = latest[0] if latest else (None, None)
        if len(signatures) > 1:
            blockers.append("同一日期存在互相冲突的累计值或完成状态")
        for _, row in latest:
            if row["related_versions"].get(target["record_id"]) != target["revision"]:
                blockers.append("目标已更正，进展记录需要重新核对")
            if baseline and row.get("baseline_binding_id") != baseline:
                blockers.append("进展记录与当前基线尚未复核")
            blockers.extend(ds.source_issues(row))
        target_value = criteria.get("quantity")
        observed = measurement.get("quantity") if measurement else None
        state = "unreported"
        if measurement:
            state = "qualitative_review"
            if target_value is not None and observed is not None:
                if criteria.get("unit") != measurement["unit"]:
                    blockers.append("目标和实际值单位不同，不能直接比较")
                else:
                    state = "meets_quantity" if observed >= target_value else "shortfall"
                    if state == "shortfall" and measurement["completion"] == "completed":
                        blockers.append("声明全部完成，但累计数量低于目标")
            elif target_value is not None:
                issues.append("缺少可比较的实际数量")
                if measurement["completion"] == "completed":
                    blockers.append("声明全部完成，但缺少约定数量的实际值")
            if measurement["completion"] == "not_applicable":
                state = "not_applicable"
            if source["stage"] != stage:
                issues.append("沿用前期观测，尚未更新本阶段数据")
            if criteria.get("evidence_requirement") == "documented" and not any(
                any(s["kind"] in {"material", "evidence"} for s in row["sources"]) for _, row in latest
            ):
                blockers.append("此目标要求材料/证据卡，当前只有自述或草稿")
        elif linked:
            issues.append("已有文字进展，尚无结构化累计观测；可人工核对")
        if target_value is not None and not measurement and stage != "application":
            blockers.append("目标已约定数量，但尚无截至报告日期的累计观测")
        if any(r["status"] == "proposed" for r in linked):
            issues.append("另有候选进展尚未采纳")
        if blockers:
            state = "needs_review"
        due = criteria.get("due_date")
        if due and latest_date and latest_date < due <= cutoff.isoformat():
            issues.append("最新观测早于目标期限，不能据此判断是否按期完成")
        items.append({"objective_id": target["record_id"], "objective_revision": target["revision"],
                      "objective": target["content"], "criteria": criteria, "state": state,
                      "measurement": measurement, "source_record_id": source["record_id"] if source else None,
                      "source_stage": source["stage"] if source else None,
                      "record_ids": [r["record_id"] for r in linked],
                      "issues": list(dict.fromkeys(issues)), "blockers": list(dict.fromkeys(blockers)),
                      "past_due": bool(due and due < cutoff.isoformat()),
                      "rule": "累计值不相加；数量达到目标仍需核对成果内容、质量和证据"})
    return {"stage": stage, "as_of": cutoff.isoformat(), "items": items,
            "summary": dict(Counter(i["state"] for i in items))}
