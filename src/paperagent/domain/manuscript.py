"""Paper-specific knowledge; conversations, memory, files and versions stay shared."""
from __future__ import annotations
from paperagent.quality.policies import author_write

import hashlib
import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from paperagent.domain.project_dossier import ProjectDossier, ProjectSource, now
from paperagent.workspace.state_store import read_json, state_lock, write_json


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ManuscriptProfile(StrictModel):
    language: str = "en"
    article_type: str = ""
    study_designs: list[str] = Field(default_factory=list, max_length=20)
    venue_type: Literal["unspecified", "journal", "conference"] = "unspecified"
    venue_name: str = ""
    track: str = ""
    publication_stage: str = "draft"


class TermDetails(StrictModel):
    preferred: str = Field(min_length=1, max_length=200)
    definition: str = Field(default="", max_length=3000)
    allowed_variants: list[str] = Field(default_factory=list, max_length=50)
    discouraged_variants: list[str] = Field(default_factory=list, max_length=50)


class ArgumentDetails(StrictModel):
    question: str = Field(default="", max_length=3000)
    method: str = Field(default="", max_length=3000)
    evidence: str = Field(default="", max_length=3000)
    result: str = Field(default="", max_length=3000)
    interpretation: str = Field(default="", max_length=3000)
    boundary: str = Field(default="", max_length=3000)
    contribution: str = Field(default="", max_length=3000)
    nonclaims: list[str] = Field(default_factory=list, max_length=30)


class VenueDetails(StrictModel):
    classification: Literal["official_requirement", "example", "preference"]
    source_url: str = Field(default="", max_length=2000)
    checked_on: date | None = None
    verification: Literal["unverified", "checked"] = "unverified"
    applies_to: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def verified_source(self):
        if self.source_url and not self.source_url.startswith(("https://", "http://")):
            raise ValueError("Venue source URL must be http(s)")
        if self.verification == "checked" and not self.checked_on:
            raise ValueError("Checked venue rules require a check date")
        if self.checked_on and self.checked_on > date.today():
            raise ValueError("Check date cannot be in the future")
        return self


class ManuscriptRecordInput(StrictModel):
    kind: Literal["term", "argument", "venue_rule"]
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=6000)
    term: TermDetails | None = None
    argument: ArgumentDetails | None = None
    venue_rule: VenueDetails | None = None
    sources: list[ProjectSource] = Field(min_length=1, max_length=20)
    artifact_names: list[str] = Field(default_factory=list, max_length=30)
    relates_to: list[str] = Field(default_factory=list, max_length=50)
    record_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def kind_details(self):
        if any((getattr(self, k) is not None) != (self.kind == k) for k in ("term", "argument", "venue_rule")):
            raise ValueError("Provide only the details matching the record kind")
        if self.kind == "venue_rule" and not self.content:
            raise ValueError("A venue rule needs its requirement or preference text")
        if (self.venue_rule and self.venue_rule.verification == "checked"
                and not self.venue_rule.source_url
                and not any(s.kind in {"material", "evidence"} for s in self.sources)):
            raise ValueError("Checked venue rules require a source URL or imported material/evidence")
        return self


class ManuscriptContext:
    def __init__(self, ws):
        self.ws = ws
        self.path = ws.dir / "manuscript.json"
        self.sources = ProjectDossier(ws)

    def read(self):
        return read_json(self.path, {"schema_version": 1, "revision": 0,
            "profile_revision": 0, "profile": ManuscriptProfile().model_dump(),
            "profile_history": [], "records": []})

    @staticmethod
    def latest(data):
        return list({r["record_id"]: r for r in data["records"]}.values())

    def _save(self, data):
        data["revision"] += 1
        data["updated_at"] = now()
        write_json(self.path, data)

    @author_write
    def set_profile(self, profile: ManuscriptProfile, expected_revision: int, reason: str):
        with state_lock(self.path):
            data = self.read()
            if data["revision"] != expected_revision or not reason.strip():
                raise ValueError("Profile changed or reason missing; reload before saving")
            data["profile_history"].append({"profile": data["profile"], "revision": data["profile_revision"],
                                            "reason": reason, "at": now()})
            data["profile"] = profile.model_dump()
            data["profile_revision"] += 1
            self._save(data)
            return data

    @author_write
    def record(self, body: ManuscriptRecordInput, *, actor="user", run_id=None):
        with state_lock(self.path):
            data = self.read()
            latest = {r["record_id"]: r for r in self.latest(data)}
            old = latest.get(body.record_id)
            if body.record_id and (not old or old["revision"] != body.expected_revision):
                raise ValueError("Record missing or changed; reload before editing")
            if old and (old["kind"] != body.kind or (actor != "user" and old["status"] != "proposed")):
                raise ValueError("Agent cannot replace adopted records; propose a new correction")
            if any(k not in latest or latest[k]["status"] == "withdrawn" or k == body.record_id for k in body.relates_to):
                raise ValueError("Links must refer to active records in this workspace")
            for filename in body.artifact_names:
                self.ws.snapshot_draft(filename)  # validate current workspace membership
            if body.kind == "venue_rule" and not data["profile"]["venue_name"]:
                raise ValueError("Set the target venue before recording venue-specific rules")
            row = {**body.model_dump(mode="json", exclude={"expected_revision"}),
                "record_id": body.record_id or "mr_" + uuid.uuid4().hex[:16],
                "revision": old["revision"] + 1 if old else 1,
                "status": "confirmed" if actor == "user" else "proposed",
                "actor": actor, "run_id": run_id, "created_at": now(),
                "sources": [self.sources.capture_source(s) for s in body.sources],
                "related_versions": {k: latest[k]["revision"] for k in body.relates_to},
                "venue_profile": data["profile"] if body.kind == "venue_rule" else None,
                "profile_revision": data["profile_revision"] if body.kind == "venue_rule" else None}
            # The agent can propose a checked-source claim, but cannot certify it.
            if actor != "user" and row.get("venue_rule"):
                row["venue_rule"]["verification"] = "unverified"
            data["records"].append(row)
            self._save(data)
            return row

    @author_write
    def review(self, record_id, *, expected_revision, status, reason):
        if status not in {"confirmed", "withdrawn"} or not reason.strip():
            raise ValueError("Adoption/withdrawal needs a reason")
        with state_lock(self.path):
            data = self.read()
            old = next((r for r in self.latest(data) if r["record_id"] == record_id), None)
            if not old or old["revision"] != expected_revision:
                raise ValueError("Record missing or changed; reload before reviewing")
            row = {**old, "revision": old["revision"] + 1, "status": status,
                   "actor": "user", "reason": reason, "created_at": now()}
            data["records"].append(row)
            self._save(data)
            return row

    def view(self):
        data = self.read()
        latest = {r["record_id"]: r for r in self.latest(data)}
        active = []
        for row in latest.values():
            if row["status"] == "withdrawn":
                continue
            issues = self.sources.source_issues(row)
            for source in row["sources"]:
                if source["kind"] == "draft":
                    snapshot = source["snapshot"]
                    path = self.ws.drafts_dir / snapshot["filename"]
                    current = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                    if current != snapshot["version"]:
                        issues.append("来源稿件的当前版本已变化；原快照仍保留，请核对本条记录")
            if row["kind"] == "venue_rule" and row["profile_revision"] != data["profile_revision"]:
                issues.append("目标刊会或稿件配置已变化，请重新核对本条要求")
            for key, revision in row["related_versions"].items():
                current = latest.get(key)
                if not current or current["status"] != "confirmed" or current["revision"] != revision:
                    issues.append("关联记录已变化或尚未确认：" + key)
            if row["kind"] == "term" and row["status"] == "confirmed":
                if any(other["record_id"] != row["record_id"] and other["kind"] == "term"
                       and other["status"] == "confirmed" and other["title"].casefold() == row["title"].casefold()
                       and (not other["artifact_names"] or not row["artifact_names"]
                            or set(other["artifact_names"]) & set(row["artifact_names"]))
                       and other["term"] != row["term"] for other in latest.values()):
                    issues.append("同一概念存在冲突的已确认术语；不能自动按时间覆盖")
            active.append({**row, "needs_review": bool(issues), "issues": issues})
        # A stale source also affects dependent arguments even before the author
        # revises the source record. Propagate flags, never mutate saved facts.
        by_id = {r["record_id"]: r for r in active}
        for _ in range(len(active)):
            changed = False
            for row in active:
                for key in row["related_versions"]:
                    if by_id.get(key, {}).get("needs_review"):
                        issue = "关联记录需要复核：" + key
                        if issue not in row["issues"]:
                            row["issues"].append(issue)
                            row["needs_review"] = True
                            changed = True
            if not changed:
                break
        return {**data, "active_records": active}

    def context(self):
        if not self.path.exists():
            return {}
        data = self.view()
        rows = sorted(data["active_records"], key=lambda r: (r["status"] != "confirmed", r["kind"], r["record_id"]))
        previews = [{"record_id": r["record_id"], "revision": r["revision"], "kind": r["kind"],
                     "title": r["title"], "status": r["status"], "needs_review": r["needs_review"],
                     "read_with": {"action": "read_context", "kind": "manuscript", "key": r["record_id"]}}
                    for r in rows[:12]]
        return {"revision": data["revision"], "profile": data["profile"], "records": previews,
                "total": len(rows), "omitted": max(0, len(rows) - 12),
                "read_with": {"action": "read_context", "kind": "manuscript"},
                "note": "Adoption is not scientific verification; read full records and sources before using."}

    def inspect_text(self, filename: str):
        """Literal diagnostics only; never certify scientific/venue readiness."""
        if not filename.endswith(".md"):
            filename += ".md"
        candidate = self.ws.snapshot_draft(filename)
        text = Path(candidate["snapshot_path"]).read_bytes().decode("utf-8-sig")
        data = self.view()
        findings = []
        applicable = [r for r in data["active_records"] if not r["artifact_names"] or filename in r["artifact_names"]]
        for row in applicable:
            if row["status"] != "confirmed":
                continue
            for issue in row["issues"]:
                findings.append({"record_id": row["record_id"], "code": "context_needs_review", "message": issue})
            if row["kind"] == "term":
                term = row["term"]
                allowed = {v.casefold() for v in [term["preferred"], *term["allowed_variants"]]}
                for variant in term["discouraged_variants"]:
                    if not variant or variant.casefold() in allowed:
                        continue
                    for match in re.finditer(r"(?<!\w)" + re.escape(variant) + r"(?!\w)", text, re.IGNORECASE):
                        findings.append({"record_id": row["record_id"], "code": "term_variant",
                            "message": f"核对 {variant} 是否应为 {term['preferred']}；引文或专名可能例外",
                            "offset": match.start(), "line": text.count("\n", 0, match.start()) + 1})
            elif row["kind"] == "venue_rule" and row["venue_rule"]["verification"] != "checked":
                findings.append({"record_id": row["record_id"], "code": "venue_unverified", "message": "来源尚未核对，不能据此宣称满足刊会要求"})
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {"status": "diagnostics_only", "filename": filename,
                "candidate_sha256": candidate["version"], "context_sha256": fingerprint,
                "context_revision": data["revision"], "findings": findings,
                "checks": {"registered_term_patterns": "checked", "context_freshness": "checked",
                           "argument_validity": "not_checked", "citation_support": "not_checked",
                           "venue_compliance": "not_checked", "rendered_layout": "not_checked"}}
