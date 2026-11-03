from paperagent.quality.policies import author_write
"""Candidate statement audit and explicit human review, tied to exact draft bytes.

Rules intentionally produce candidates, not a claim of exhaustive fact extraction.
"""
import hashlib
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from paperagent.domain.project_dossier import ProjectDossier
from paperagent.workspace.state_store import read_json, state_lock, write_json

MARKER = re.compile(r"\{\{project:([^{}]+)\}\}")
NEGATIVE_FACT = r"(?:无|没有|未有)(?:任何|可报告的)?(?:论文(?:发表)?|获奖|实际成果|其他可计数的阶段成果)"
FACT = re.compile(NEGATIVE_FACT + r"|已完成|实际完成|完成了|已发表|发表了|已录用|已获|获批|获得.{0,15}(?:奖励|奖项|专利)|"
                  r"已授权|已应用|已推广|已支出|实际支出|到账|提升了|降低了|增长了|下降了|"
                  r"(?:完成|发表|形成|获得|收集|回收|支出|投入|覆盖).{0,8}[0-9零一二三四五六七八九十百千万两]+|"
                  r"\b(?:completed|published|achieved|spent|collected)\b", re.I)
FUTURE = re.compile(r"^(?:本项目|本研究)?\s*(?:计划|拟|预计|预期|将|目标是|目标为|力争|下一步|后续)|\b(?:plan to|will|expected to)\b", re.I)
ACTUAL = re.compile(NEGATIVE_FACT + r"|实际完成|已完成|完成了|已发表|发表了|已录用|已授权|实际支出|已支出|\b(?:completed|published|spent)\b", re.I)


class ClaimReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    filename: str
    draft_version: str
    claim_id: str
    verdict: Literal["supported", "disputed", "not_factual", "needs_evidence"]
    record_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)


def draft_version(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_claims(text):
    rows = []
    seen = set()
    in_code = False
    future_section = False
    # Keep source markers alongside the sentence they support, including after punctuation.
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not line.strip() or in_code:
            continue
        if line.lstrip().startswith("#"):
            heading = line.lstrip("# ").strip()
            future_section = bool(re.search(r"计划|下一步|预期|^研究目标$", heading))
        for match in re.finditer(r"[^。！？!?；;]+[。！？!?；;]?(?:\s*\{\{project:[^{}]+\}\})*", line):
            content = match.group().strip()
            plain = MARKER.sub("", content).strip().lstrip("-*># ")
            markers = MARKER.findall(content)
            planned = (FUTURE.search(plain) or future_section) and not ACTUAL.search(plain)
            if not plain or (not markers and (not FACT.search(plain) or planned)):
                continue
            claim_id = "pc_" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:20]
            if claim_id in seen:
                continue
            seen.add(claim_id)
            rows.append({"claim_id": claim_id, "text": content, "line": line_number, "record_ids": markers})
    return rows


class ProjectClaims:
    def __init__(self, ws):
        self.ws = ws
        self.path = ws.dir / "project_claim_reviews.json"

    def audit(self, text, filename="", review_override=None):
        if filename and not filename.endswith(".md"):
            filename += ".md"
        ds = ProjectDossier(self.ws)
        records = {r["record_id"]: r for r in ds.active_records()}
        version = draft_version(text)
        reviews = read_json(self.path, [])
        if review_override is not None:
            reviews.append(review_override)
        rows = extract_claims(text)
        for row in rows:
            review = next((r for r in reversed(reviews) if r["filename"] == filename
                           and r["draft_version"] == version and r["claim_id"] == row["claim_id"]), None)
            ids = list(dict.fromkeys(row["record_ids"] + (review["record_ids"] if review else [])))
            issues = []
            for key in ids:
                record = records.get(key)
                if record is None or record["status"] != "confirmed":
                    issues.append(f"{key} 不存在、已撤回或未采纳")
                else:
                    issues.extend(ds.source_issues(record))
                    for related_id, revision in record["related_versions"].items():
                        related = records.get(related_id)
                        if not related or related["revision"] != revision:
                            issues.append(f"{key} 的关联记录已变化")
            # Narrow deterministic check: compare explicit Arabic cumulative
            # completion quantities with the linked structured observations.
            # Other wording/units still need human semantic review.
            quantities = {}
            if ids and ACTUAL.search(row["text"]) and not any(
                (records.get(key) or {}).get("kind") in {"progress", "output", "budget"} for key in ids
            ):
                issues.append("实际成果声明仅关联目标或决定，不能用计划记录证明实际发生")
            for key in ids:
                for measurement in (records.get(key) or {}).get("measurements", []):
                    if measurement.get("quantity") is not None and measurement.get("unit"):
                        quantities.setdefault(measurement["unit"], set()).add(measurement["quantity"])
            for unit, values in quantities.items():
                pattern = rf"(?:已完成|实际完成|完成了|完成|累计(?:完成)?)[^\d。]{{0,6}}(\d+(?:\.\d+)?)\s*{re.escape(unit)}"
                for match in re.finditer(pattern, row["text"]):
                    if re.search(r"计划|预期|拟|目标|原定|约定", row["text"][max(0, match.start()-8):match.start()]):
                        continue
                    if re.search(r"(?:不写成|不得写成|不能写成|避免写成|未|没有)$", row["text"][:match.start()]):
                        continue
                    if float(match.group(1)) not in values:
                        issues.append(f"正文数量 {match.group(1)}{unit} 与关联观测 {sorted(values)}{unit} 不一致")
            stale = bool(review and any(key not in records or records[key]["revision"] != revision
                                        for key, revision in review["record_versions"].items()))
            state = "sourced" if ids and not issues else "needs_evidence"
            if review:
                state = "needs_review" if stale or issues else review["verdict"]
            if not review and any(r["filename"] == filename and r["claim_id"] == row["claim_id"] for r in reviews):
                # An edit to any part of the draft invalidates old human sign-off.
                state = "needs_review"
                issues.append("草稿版本已变化，旧核查结论不自动沿用")
            row.update(state=state, record_ids=ids, issues=issues,
                       review=review, stale=stale,
                       source_summary=[{"record_id": key, "content": records[key]["content"],
                                        "sources": [{k: s.get(k, "") for k in ("kind", "key", "locator")}
                                                    for s in records[key]["sources"]]}
                                       for key in ids if key in records])
        return {"filename": filename, "draft_version": version, "claims": rows,
                "candidate_count": len(rows),
                "unresolved": sum(r["state"] not in {"sourced", "supported", "not_factual"} for r in rows),
                "limitation": "规则提取的是候选关键声明，可能漏检或误检；有来源不等于证据支持其含义，仍需人工通读。"}

    @author_write
    def review(self, body: ClaimReviewInput):
        if not body.filename.endswith(".md"):
            body = body.model_copy(update={"filename": body.filename + ".md"})
        text = self.ws.read_draft(body.filename)
        if draft_version(text) != body.draft_version:
            raise ValueError("Draft changed; run the check again before reviewing")
        claim = next((r for r in extract_claims(text) if r["claim_id"] == body.claim_id), None)
        if claim is None:
            raise ValueError("Claim not found in this draft version")
        ds = ProjectDossier(self.ws)
        records = {r["record_id"]: r for r in ds.active_records()}
        ids = list(dict.fromkeys(claim["record_ids"] + body.record_ids))
        if any(key not in records or records[key]["status"] != "confirmed" for key in ids):
            raise ValueError("Claim sources must be adopted active records in this project")
        if body.verdict == "supported" and (not ids or any(ds.source_issues(records[key]) for key in ids)):
            raise ValueError("Supported claims require available source records; repair sources first")
        row = {**body.model_dump(), "record_ids": ids,
               "record_versions": {key: records[key]["revision"] for key in ids},
               "claim_text": claim["text"], "actor": "user",
               "created_at": datetime.now(timezone.utc).isoformat()}
        if body.verdict == "supported":
            projected = next(r for r in self.audit(text, body.filename, row)["claims"] if r["claim_id"] == body.claim_id)
            if projected["issues"]:
                raise ValueError("Resolve source/quantity conflicts before supporting this claim: " + "; ".join(projected["issues"]))
        with state_lock(self.path):
            history = read_json(self.path, [])
            history.append(row)
            write_json(self.path, history)
        return row
