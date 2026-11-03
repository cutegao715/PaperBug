"""Reviewable, versioned assessment artifacts shared by UI and runtime delivery."""
import hashlib
import json

from paperagent.domain.project_acceptance import objective_matrix
from paperagent.domain.project_claims import ProjectClaims
from paperagent.workspace.state_store import write_json


def assess_project(ws, filename, workflow):
    contract = workflow["project_contract"]
    return {"contract": contract,
            "acceptance": objective_matrix(ws, contract["stage"], contract.get("report_date")),
            "claims": ProjectClaims(ws).audit(ws.read_draft(filename), filename)}


def assessment_markdown(data):
    states = {"unreported":"尚未记录", "qualitative_review":"需定性核查", "meets_quantity":"已记录数量达到目标",
              "shortfall":"已记录数量未达目标", "needs_review":"需复核", "not_applicable":"不适用",
              "sourced":"已有来源，待核含义", "supported":"用户核对支持", "not_factual":"非成果事实",
              "needs_evidence":"缺少依据", "disputed":"存在争议", "not_started":"尚未开始",
              "in_progress":"进行中", "completed":"已完成", "not_completed":"未完成"}
    def cell(value):
        return str(value if value is not None else "未填写").replace("|", "\\|").replace("\n", " ")
    lines = ["# 项目目标对照与事实核查清单", "", f"报告截止日期：{data['acceptance']['as_of']}",
             "", "累计观测不相加；数量达标不代表成果质量已验收。以下声明为规则提取的候选，仍需人工通读。", "",
             "| 目标 | 约定数量 | 最新累计值 | 观测日期 | 声明完成状态 | 核查结果 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in data["acceptance"]["items"]:
        criteria, measurement = row["criteria"], row["measurement"] or {}
        values = [row["objective"], f"{cell(criteria.get('quantity'))} {criteria.get('unit', '')}",
                  f"{cell(measurement.get('quantity'))} {measurement.get('unit', '')}",
                  measurement.get("as_of"), states.get(measurement.get("completion"), "尚未记录"),
                  "；".join([states.get(row["state"], row["state"])] + row["blockers"] + row["issues"])]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    lines.extend(["", "## 候选关键声明", ""])
    for row in data["claims"]["claims"]:
        lines.extend([f"- 第 {row['line']} 行：{row['text']}", f"  核查状态：{states.get(row['state'], row['state'])}；来源记录：{', '.join(row['record_ids']) or '缺少'}"])
        if row["review"]:
            lines.append("  用户说明：" + row["review"]["reason"])
        for source in row["source_summary"]:
            lines.append("  依据：" + source["content"])
            lines.extend("  来源：" + s["kind"] + " " + s["key"] + " " + s["locator"] for s in source["sources"])
        lines.extend("  需复核：" + issue for issue in row["issues"])
    return "\n".join(lines) + "\n"


def save_assessment(ws, filename, workflow):
    from urllib.parse import quote
    data = assess_project(ws, filename, workflow)
    content_hash = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    root = ws.dir / "reviews"
    path = root / f"{workflow['project_contract']['stage']}-{content_hash}.json"
    write_json(path, data)
    markdown = path.with_suffix(".md")
    markdown.write_text(assessment_markdown(data), encoding="utf-8")
    base = f"/api/v1/projects/{quote(ws.project_id, safe='')}/dossier/reports/"
    return [{"status": "ok", "format": "review_json", "path": str(path), "download_url": base + path.name},
            {"status": "ok", "format": "review_markdown", "path": str(markdown), "download_url": base + markdown.name}]
