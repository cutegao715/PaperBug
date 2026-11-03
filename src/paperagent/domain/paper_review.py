"""Version-bound review artifacts and optional issue follow-ups, not a workflow."""
from __future__ import annotations
from paperagent.quality.policies import author_write

import hashlib
import json
from pathlib import Path
from typing import Literal
import uuid

from pydantic import Field, field_validator, model_validator

from paperagent.domain.manuscript import ManuscriptContext, StrictModel
from paperagent.domain.project_dossier import ProjectDossier, ProjectSource, now
from paperagent.workspace.state_store import read_json, state_lock, write_json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def draft_name(value):
    return value if value.endswith(".md") else value + ".md"


class ReviewArtifact(StrictModel):
    filename: str = Field(min_length=1, max_length=200)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["manuscript", "companion", "prior_comments", "response"] = "manuscript"
    _filename = field_validator("filename")(draft_name)


class ReviewCheck(StrictModel):
    dimension: str = Field(min_length=1, max_length=200)
    coverage: Literal["checked", "not_applicable", "outside_scope", "unavailable"]
    basis: str = Field(min_length=1, max_length=3000)


class ReviewFinding(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=1000)
    problem: str = Field(min_length=1, max_length=5000)
    basis: str = Field(min_length=1, max_length=4000)
    action_needed: str = Field(min_length=1, max_length=3000)
    severity: Literal["critical", "major", "minor", "note"] = "minor"
    evidence_status: Literal["supported", "tentative", "unverified"] = "unverified"
    classification: Literal["validity", "reporting", "venue", "preference", "prose"] = "reporting"
    affected_artifacts: list[str] = Field(min_length=1, max_length=20)

    @field_validator("affected_artifacts")
    @classmethod
    def filenames(cls, values):
        return [draft_name(v) for v in values]


class PaperReviewInput(StrictModel):
    review_kind: Literal["paper", "grant"] = "paper"
    review_mode: Literal["scientific", "readability", "criteria"] = "scientific"
    review_profile: str = Field(default="", max_length=500)
    title: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=3000)
    round_label: str = Field(default="developmental", max_length=100)
    modules: list[str] = Field(default_factory=list, max_length=30)
    artifacts: list[ReviewArtifact] = Field(min_length=1, max_length=20)
    knowledge_ids: list[str] = Field(default_factory=list, max_length=50)
    sources: list[ProjectSource] = Field(default_factory=list, max_length=30)
    checks: list[ReviewCheck] = Field(min_length=1, max_length=40)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=80)
    summary: str = Field(min_length=1, max_length=6000)
    prior_report_id: str | None = None

    @model_validator(mode="after")
    def artifact_membership(self):
        names = [a.filename for a in self.artifacts]
        if len(names) != len(set(names)):
            raise ValueError("Review artifact filenames must be unique")
        if not any(a.role == "manuscript" for a in self.artifacts):
            raise ValueError("A review needs a manuscript artifact")
        if any(set(f.affected_artifacts) - set(names) for f in self.findings):
            raise ValueError("Findings must identify artifacts in this review")
        return self


class ReviewVerification(StrictModel):
    filename: str = Field(min_length=1, max_length=200)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    locator: str = Field(min_length=1, max_length=1000)
    quote: str = Field(min_length=1, max_length=2000)
    _filename = field_validator("filename")(draft_name)


class ReviewFollowupInput(StrictModel):
    expected_revision: int = Field(ge=1)
    status: Literal["open", "partial", "resolved", "regressed", "waived", "not_verifiable"]
    reason: str = Field(min_length=1, max_length=3000)
    sources: list[ProjectSource] = Field(default_factory=list, max_length=20)
    verifications: list[ReviewVerification] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def resolution_evidence(self):
        if self.status == "resolved" and not self.verifications:
            raise ValueError("Resolved requires located evidence in the current manuscript or companion artifact")
        return self


class PaperReviews:
    def __init__(self, ws):
        self.ws = ws
        self.path = ws.dir / "paper_reviews.json"
        self.dossier = ProjectDossier(ws)
        self.manuscript = ManuscriptContext(ws)

    def read(self):
        return read_json(self.path, {"schema_version": 1, "revision": 0, "reports": [], "followups": []})

    def _save(self, data):
        data["revision"] += 1
        write_json(self.path, data)

    def basis(self, filename):
        """Reuse content-addressed draft snapshots, including for local reviews."""
        if not filename.endswith(".md"):
            filename += ".md"
        return self.ws.snapshot_draft(filename)

    def _artifact(self, value):
        snap = self.basis(value.filename)
        if snap["version"] != value.expected_sha256:
            raise ValueError("Review artifact changed; read the current version before saving")
        return {**snap, "role": getattr(value, "role", "companion")}

    def _knowledge(self, ids):
        view = self.manuscript.view()
        rows = {r["record_id"]: r for r in view["active_records"]}
        return {"profile": view["profile"], "records": {key: rows.get(key) for key in ids}}

    @author_write
    def create(self, body: PaperReviewInput, *, actor="user", run_id=None):
        if body.review_kind == "grant" and body.review_mode == "criteria":
            if not body.review_profile.strip() or not any(s.kind != "user_note" for s in body.sources):
                raise ValueError("Criteria-based grant review requires a profile and a sourced guideline artifact")
        with state_lock(self.path):
            data = self.read()
            if body.prior_report_id and not any(r["report_id"] == body.prior_report_id for r in data["reports"]):
                raise ValueError("Prior report must belong to this workspace")
            knowledge = self._knowledge(body.knowledge_ids)
            if any(r is None for r in knowledge["records"].values()):
                raise ValueError("Knowledge links must identify active records in this workspace")
            row = {**body.model_dump(mode="json", exclude={"artifacts", "sources", "findings"}),
                   "report_id": "prv_" + uuid.uuid4().hex[:16], "created_at": now(),
                   "actor": actor, "run_id": run_id,
                   "artifacts": [self._artifact(a) for a in body.artifacts],
                   "sources": [self.dossier.capture_source(s) for s in body.sources],
                   "knowledge_basis": knowledge,
                   "findings": [{**f.model_dump(), "issue_id": "pri_" + uuid.uuid4().hex[:16]} for f in body.findings],
                   "interpretation": "Reviewer assessment only; saving does not verify scientific validity or venue compliance."}
            report = self._render(row)
            folder = self.ws.dir / "reviews" / "paper"
            folder.mkdir(parents=True, exist_ok=True)
            # A unique report is immutable. Follow-up decisions never overwrite it.
            path = folder / (row["report_id"] + ".md")
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(report)
            row["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            data["reports"].append(row)
            self._save(data)
            return row

    @staticmethod
    def _find(data, report_id, issue_id=None):
        report = next((r for r in data["reports"] if r["report_id"] == report_id), None)
        if not report:
            raise ValueError("Review report not found in this workspace")
        issue = next((i for i in report["findings"] if i["issue_id"] == issue_id), None) if issue_id else None
        if issue_id and not issue:
            raise ValueError("Issue not found in this report")
        return report, issue

    def _freshness(self, basis):
        issues = self.dossier.source_issues({"sources": [*basis.get("sources", []),
            *[{"kind": "draft", "snapshot": a} for a in basis.get("artifacts", [])]]})
        for a in basis.get("artifacts", []):
            try:
                path = self.ws._draft_path(a["filename"])
                version = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError):
                version = None
            if version != a["version"]:
                issues.append("稿件版本已变化：" + a["filename"])
        for source in basis.get("sources", []):
            if source["kind"] == "draft":
                a = source["snapshot"]
                try:
                    current = hashlib.sha256(self.ws._draft_path(a["filename"]).read_bytes()).hexdigest()
                except (OSError, ValueError):
                    current = None
                if current != a["version"]:
                    issues.append("评审来源稿件已变化：" + a["filename"])
        if fingerprint(basis["knowledge_basis"]) != fingerprint(self._knowledge(basis["knowledge_ids"])):
            issues.append("关联论文知识或目标刊会已变化，请复核")
        return list(dict.fromkeys(issues))

    @author_write
    def followup(self, report_id, issue_id, body: ReviewFollowupInput, *, actor="user", run_id=None):
        with state_lock(self.path):
            data = self.read()
            report, issue = self._find(data, report_id, issue_id)
            history = [f for f in data["followups"] if f["issue_id"] == issue_id]
            if body.expected_revision != len(history) + 1:
                raise ValueError("Issue changed; reload before saving follow-up")
            roles = {a["filename"]: a["role"] for a in report["artifacts"]}
            artifacts = {a["filename"]: a for a in report["artifacts"] if a["filename"] in issue["affected_artifacts"]}
            # Even an open/waived assessment has a candidate basis. Missing files
            # remain visible as unavailable instead of preventing that assessment.
            for filename, old in list(artifacts.items()):
                try:
                    artifacts[filename] = {**self.basis(filename), "role": old["role"]}
                except OSError:
                    pass
            for verification in body.verifications:
                if roles.get(verification.filename) not in {"manuscript", "companion"}:
                    raise ValueError("Response letters and comment threads cannot verify a manuscript repair")
                if verification.filename not in issue["affected_artifacts"]:
                    raise ValueError("Verification must address an affected manuscript artifact")
                snapshot = self._artifact(verification)
                text = Path(snapshot["snapshot_path"]).read_text(encoding="utf-8-sig")
                if verification.quote not in text:
                    raise ValueError("Verification quote is absent from the current artifact")
                artifacts[verification.filename] = {**snapshot, "role": roles[verification.filename]}
            row = {**body.model_dump(mode="json", exclude={"expected_revision", "sources"}),
                   "followup_id": "prf_" + uuid.uuid4().hex[:16], "report_id": report_id, "issue_id": issue_id,
                   "revision": len(history) + 2, "actor": actor, "run_id": run_id, "created_at": now(),
                   "adoption": "accepted" if actor == "user" else "proposed",
                   "sources": [self.dossier.capture_source(s) for s in body.sources], "artifacts": list(artifacts.values()),
                   "knowledge_ids": report["knowledge_ids"], "knowledge_basis": self._knowledge(report["knowledge_ids"])}
            data["followups"].append(row)
            self._save(data)
            return row

    @author_write
    def adopt(self, report_id, issue_id, *, expected_revision, followup_id, reason):
        with state_lock(self.path):
            data = self.read()
            self._find(data, report_id, issue_id)
            history = [f for f in data["followups"] if f["issue_id"] == issue_id]
            if expected_revision != len(history) + 1 or not reason.strip():
                raise ValueError("Issue changed or reason missing; reload before adoption")
            proposal = next((f for f in history if f["followup_id"] == followup_id and f["adoption"] == "proposed"), None)
            if not proposal:
                raise ValueError("Follow-up proposal not found")
            if any(f.get("adopted_from") == followup_id for f in history):
                raise ValueError("Follow-up proposal was already adopted")
            if self._freshness(proposal):
                raise ValueError("Proposal basis changed; re-evaluate the current manuscript before adoption")
            row = {**proposal, "followup_id": "prf_" + uuid.uuid4().hex[:16], "revision": len(history) + 2,
                   "actor": "user", "adoption": "accepted", "reason": reason, "created_at": now(),
                   "adopted_from": followup_id}
            data["followups"].append(row)
            self._save(data)
            return row

    def view(self):
        data = self.read()
        reports = []
        for report in data["reports"]:
            findings = []
            for issue in report["findings"]:
                history = [f for f in data["followups"] if f["issue_id"] == issue["issue_id"]]
                effective = next((f for f in reversed(history) if f["adoption"] == "accepted"), None)
                review_issues = self._freshness(effective or report)
                adopted = {f.get("adopted_from") for f in history if f["adoption"] == "accepted"}
                proposals = [{**f, "needs_review": bool(self._freshness(f))} for f in history
                             if f["adoption"] == "proposed" and f["followup_id"] not in adopted]
                findings.append({**issue, "revision": len(history) + 1,
                                 "recorded_status": effective["status"] if effective else "open",
                                 "status": "not_verifiable" if review_issues and effective and effective["status"] == "resolved"
                                           else (effective["status"] if effective else "open"),
                                 "needs_review": bool(review_issues), "review_issues": review_issues,
                                 "effective_followup": effective, "proposals": proposals})
            issues = self._freshness(report)
            reports.append({**report, "needs_review": bool(issues) or any(
                f["needs_review"] or any(p["needs_review"] for p in f["proposals"]) for f in findings),
                            "review_issues": issues, "findings": findings,
                            "report_url": f"/api/v1/projects/{self.ws.project_id}/paper-reviews/{report['report_id']}/report"})
        return {**data, "reports": reports}

    def context(self):
        if not self.path.exists():
            return {}
        reports = self.view()["reports"]
        return {"total": len(reports), "omitted": max(0, len(reports) - 5),
                "pending_followups": sum(len(f["proposals"]) for r in reports for f in r["findings"]),
                "reports": [{"report_id": r["report_id"], "title": r["title"], "scope": r["scope"][:400],
                             "review_kind": r.get("review_kind", "paper"),
                             "review_mode": r.get("review_mode", "scientific"),
                             "round_label": r["round_label"], "needs_review": r["needs_review"],
                             "issue_count": len(r["findings"]),
                             "read_with": {"action": "read_context", "kind": "paper_reviews", "key": r["report_id"]}}
                            for r in reversed(reports[-5:])],
                "read_with": {"action": "read_context", "kind": "paper_reviews"}}

    def report_text(self, report_id):
        row, _ = self._find(self.read(), report_id)
        path = self.ws.dir / "reviews" / "paper" / (row["report_id"] + ".md")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["report_sha256"]:
            raise ValueError("Saved review report changed; use stored review records to inspect its original basis")
        return raw.decode("utf-8")

    def source_text(self, report_id, filename):
        report, _ = self._find(self.read(), report_id)
        snapshot = next((a for a in report["artifacts"] if a["filename"] == filename), None)
        if not snapshot:
            raise ValueError("Artifact not found in this review")
        path = Path(snapshot["snapshot_path"])
        if not path.resolve().is_relative_to((self.ws.dir / "versions").resolve()):
            raise ValueError("Invalid review snapshot")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != snapshot["version"]:
            raise ValueError("Review source snapshot changed")
        return raw.decode("utf-8-sig")

    @staticmethod
    def _render(row):
        lines = ["# " + row["title"], "", "Review ID: " + row["report_id"], "Created: " + row["created_at"],
                 "Round: " + row["round_label"], "Scope: " + row["scope"], "Modules: " + ", ".join(row["modules"]),
                 "", "This is an immutable reviewer assessment, not scientific certification. Follow-ups are recorded separately.",
                 "", "## Source basis", ""]
        lines += [f"- {a['filename']} ({a['role']}), SHA256 {a['version']}" for a in row["artifacts"]]
        lines += [f"- {s['kind']}: {s['key']} {s.get('locator', '')}" for s in row["sources"]]
        lines += ["", "## Linked paper knowledge", "",
                  "Target profile: " + json.dumps(row["knowledge_basis"]["profile"], ensure_ascii=False)]
        lines += [f"- {key}: {record['title']} / revision {record['revision']} / {record['status']}"
                  for key, record in row["knowledge_basis"]["records"].items()]
        lines += ["", "## Coverage", ""]
        lines += [f"- {c['dimension']}: {c['coverage']} — {c['basis']}" for c in row["checks"]]
        lines += ["", "## Findings", ""]
        if not row["findings"]:
            lines.append("No findings recorded for the declared scope; coverage and unavailable evidence remain as stated above.")
        for finding in row["findings"]:
            lines += ["### " + finding["title"], "", "ID: " + finding["issue_id"],
                      f"{finding['severity']} / {finding['evidence_status']} / {finding['classification']}",
                      "Location: " + finding["location"], finding["problem"], "Evidence: " + finding["basis"],
                      "Suggested action: " + finding["action_needed"], ""]
        lines += ["", "Review kind: " + row.get("review_kind", "paper"), "Review mode: " + row.get("review_mode", "scientific"), "Review profile: " + row.get("review_profile", ""), "", "## Assessment and limits", "", row["summary"], ""]
        return "\n".join(lines)
