"""Versioned project commitments and observations, separate from derived memory.

Confirmation means the user adopted a record; it is never external approval.
All replacements remain auditable and bindings always address immutable bytes.
"""
from __future__ import annotations
from paperagent.quality.policies import author_write

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from paperagent.workspace.state_store import read_json, state_lock, write_json

ProjectStage = Literal["application", "midterm", "final"]
ArtifactRole = Literal["application", "baseline", "midterm", "final",
                       "template_application", "template_midterm", "template_final"]


class ProjectSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["draft", "material", "evidence", "user_note"]
    key: str = Field(min_length=1, max_length=8000)
    locator: str = Field(default="", max_length=1000)


class ObjectiveCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    output_type: str = Field(default="", max_length=200)
    quantity: float | None = Field(default=None, ge=0)
    unit: str = Field(default="", max_length=40)
    due_date: date | None = None
    acceptance: str = Field(default="", max_length=2000)
    evidence_requirement: Literal["any_source", "documented"] = "any_source"

    @model_validator(mode="after")
    def check_unit(self):
        if self.quantity is not None and not self.unit:
            raise ValueError("Quantitative targets require a unit")
        return self


class ObjectiveMeasurement(BaseModel):
    """One cumulative observation per goal and reporting date, never an increment."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    objective_id: str
    as_of: date
    quantity: float | None = Field(default=None, ge=0)
    unit: str = Field(default="", max_length=40)
    completion: Literal["not_started", "in_progress", "completed", "not_completed", "not_applicable"]
    explanation: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_observation(self):
        if self.quantity is not None and not self.unit:
            raise ValueError("Observed quantities require a unit")
        if self.completion in {"not_completed", "not_applicable"} and not self.explanation:
            raise ValueError("Incomplete/not-applicable observations require an explanation")
        return self


class ProjectRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["objective", "progress", "output", "decision", "issue", "next_plan", "budget"]
    stage: ProjectStage
    content: str = Field(min_length=1, max_length=8000)
    sources: list[ProjectSource] = Field(min_length=1, max_length=20)
    relates_to: list[str] = Field(default_factory=list, max_length=100)
    record_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    criteria: ObjectiveCriteria | None = None
    measurements: list[ObjectiveMeasurement] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_structured_fields(self):
        if self.criteria is not None and self.kind != "objective":
            raise ValueError("Criteria belong to objective records")
        if self.measurements and self.kind not in {"progress", "output"}:
            raise ValueError("Measurements belong to progress/output records")
        ids = [m.objective_id for m in self.measurements]
        if len(set(ids)) != len(ids) or any(key not in self.relates_to for key in ids):
            raise ValueError("Each measured objective must be linked once in relates_to")
        return self


class ProjectBindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: ArtifactRole
    filename: str
    reason: str = Field(min_length=1, max_length=2000)
    required_sections: list[str] = Field(default_factory=list, max_length=100)


def now():
    return datetime.now(timezone.utc).isoformat()


class ProjectDossier:
    def __init__(self, ws):
        self.ws = ws
        self.path = ws.dir / "project_dossier.json"

    def read(self):
        return read_json(self.path, {"schema_version": 1, "revision": 0,
                                    "records": [], "bindings": [], "deliveries": []})

    def _save(self, data):
        data["revision"] += 1
        data["updated_at"] = now()
        write_json(self.path, data)

    @staticmethod
    def latest_records(data):
        latest = {}
        for record in data["records"]:
            latest[record["record_id"]] = record
        return list(latest.values())

    def active_records(self):
        return [r for r in self.latest_records(self.read()) if r["status"] != "withdrawn"]

    def _source(self, source: ProjectSource):
        result = source.model_dump()
        if source.kind == "draft":
            result["snapshot"] = self.ws.snapshot_draft(source.key)
        elif source.kind == "material":
            item = next((m for m in self.ws.list_materials()
                         if source.key in (m.get("path"), m.get("material_id"))), None)
            if not item:
                raise ValueError("Source must reference a material imported into this project")
            # Preserve registry metadata, not an arbitrary caller-supplied path.
            result["registered_material"] = item
            from pathlib import Path
            path = Path(item["path"])
            if not path.is_file():
                raise ValueError("Imported material source is missing; choose an available source")
            with path.open("rb") as stream:
                result["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        elif source.kind == "evidence":
            item = next((e for e in self.ws.list_evidence() if e.evidence_id == source.key), None)
            if not item:
                raise ValueError("Evidence source not found in this project")
            result["evidence"] = item.model_dump(mode="json")
        else:
            result["attribution"] = "user-provided note"  # not independent verification
        return result

    def capture_source(self, source: ProjectSource):
        """Reuse workspace source/version validation without creating project records."""
        return self._source(source)

    @author_write
    def record(self, body: ProjectRecordInput, *, actor: str = "user", task_id=None):
        with state_lock(self.path):
            data = self.read()
            latest = {r["record_id"]: r for r in self.latest_records(data)}
            old = latest.get(body.record_id) if body.record_id else None
            if body.record_id and old is None:
                raise ValueError("Record not found")
            if old and body.expected_revision != old["revision"]:
                raise ValueError("Record changed; reload its current revision before editing")
            if old and actor != "user" and old["status"] != "proposed":
                raise ValueError("Agent cannot replace confirmed/withdrawn facts; propose a new correction")
            if old and (old["kind"] != body.kind or old["stage"] != body.stage):
                raise ValueError("Keep record kind/stage stable; create a new record for another purpose")
            if any(key not in latest or latest[key]["status"] == "withdrawn"
                   or key == body.record_id for key in body.relates_to):
                raise ValueError("Related records must be active records in this project, without self-links")
            if any(latest[m.objective_id]["kind"] != "objective" for m in body.measurements):
                raise ValueError("Measurements must reference objective records")
            row = {**body.model_dump(mode="json", exclude={"expected_revision"}),
                   "record_id": body.record_id or f"pr_{uuid.uuid4().hex[:16]}",
                   "revision": old["revision"] + 1 if old else 1,
                   "status": "confirmed" if actor == "user" else "proposed",
                   "actor": actor, "task_id": task_id, "created_at": now(),
                   "sources": [self._source(s) for s in body.sources],
                   "related_versions": {key: latest[key]["revision"] for key in body.relates_to},
                   "baseline_binding_id": (self.bindings().get("baseline") or {}).get("binding_id")}
            data["records"].append(row)
            self._save(data)
            return row

    @author_write
    def review(self, record_id, *, expected_revision: int, status: str, reason: str):
        if status not in {"confirmed", "withdrawn"} or not reason.strip():
            raise ValueError("Review requires confirmed/withdrawn status and a reason")
        with state_lock(self.path):
            data = self.read()
            old = next((r for r in self.latest_records(data) if r["record_id"] == record_id), None)
            if not old or old["revision"] != expected_revision:
                raise ValueError("Record missing or changed; reload before reviewing")
            row = {**old, "revision": old["revision"] + 1, "status": status,
                   "actor": "user", "reason": reason, "created_at": now()}
            data["records"].append(row)
            self._save(data)
            return row

    @author_write
    def bind(self, body: ProjectBindingInput):
        # Only the explicit API/UI can adopt bindings. Generating text never does.
        if not body.role.startswith("template_") and body.required_sections:
            raise ValueError("Required headings belong to a stage template binding")
        if any(not s.strip() or len(s) > 300 for s in body.required_sections):
            raise ValueError("Template headings must be nonempty and at most 300 characters")
        snapshot = self.ws.snapshot_draft(body.filename)
        with state_lock(self.path):
            data = self.read()
            row = {**body.model_dump(), **snapshot, "binding_id": f"pb_{uuid.uuid4().hex[:16]}",
                   "actor": "user", "created_at": now()}
            data["bindings"].append(row)
            self._save(data)
            return row

    def bindings(self):
        result = {}
        for binding in self.read()["bindings"]:
            if binding.get("status") == "released":
                result.pop(binding["role"], None)
            else:
                result[binding["role"]] = binding
        for role, binding in list(result.items()):
            path = self.ws.drafts_dir / binding["filename"]
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            result[role] = {**binding, "working_copy_changed": current != binding["version"]}
        return result

    @author_write
    def release_binding(self, role, expected_binding_id, reason):
        if not reason.strip():
            raise ValueError("A release reason is required")
        with state_lock(self.path):
            data = self.read()
            current = self.bindings().get(role)
            if not current or current["binding_id"] != expected_binding_id:
                raise ValueError("Binding changed or missing; reload before releasing")
            row = {"role": role, "status": "released", "binding_id": f"pb_{uuid.uuid4().hex[:16]}",
                   "previous_binding_id": expected_binding_id, "actor": "user", "reason": reason,
                   "created_at": now()}
            data["bindings"].append(row)
            self._save(data)
            return row

    def source_text(self, binding_id):
        binding = next((b for b in self.read()["bindings"] if b["binding_id"] == binding_id), None)
        if not binding or binding.get("status") == "released":
            raise ValueError("Binding not found in this project")
        from pathlib import Path
        path = Path(binding["snapshot_path"])
        if not path.is_absolute():
            path = path.resolve()
        if not path.is_relative_to((self.ws.dir / "versions").resolve()):
            raise ValueError("Snapshot is outside this project's version store")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding["version"]:
            raise ValueError("Snapshot integrity check failed")
        return raw.decode("utf-8")

    def source_issues(self, record):
        from pathlib import Path
        issues = []
        for source in record["sources"]:
            if source["kind"] == "material":
                path = Path(source["registered_material"]["path"])
                try:
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if digest != source["sha256"]:
                        issues.append("材料内容已变化，需重新读取并核对")
                except OSError:
                    issues.append("材料来源已不可访问")
            elif source["kind"] == "draft":
                snapshot = source["snapshot"]
                try:
                    path = Path(snapshot["snapshot_path"])
                    if not path.resolve().is_relative_to((self.ws.dir / "versions").resolve()):
                        raise ValueError("invalid snapshot")
                    if hashlib.sha256(path.read_bytes()).hexdigest() != snapshot["version"]:
                        issues.append("草稿源版本校验失败")
                except (OSError, ValueError):
                    issues.append("草稿源版本不可用")
        return issues

    def context(self, stage):
        data = self.read()
        records = self.active_records()
        # Decisions survive stage/keyword changes. Full records/history are paged on demand.
        records.sort(key=lambda r: (r["kind"] not in {"decision", "objective"}, r["stage"] != stage))
        preview = [{k: r[k] for k in ("record_id", "revision", "kind", "stage", "status", "relates_to")}
                   | {"preview": r["content"][:220]} for r in records[:24]]
        from paperagent.domain.project_acceptance import objective_matrix
        matrix = objective_matrix(self.ws, stage)
        return {"revision": data["revision"], "stage": stage, "bindings": self.bindings(),
                "acceptance_summary": matrix["summary"],
                "read_acceptance": {"action": "read_context", "kind": "project_acceptance", "key": stage},
                "records": preview, "record_count": len(records), "omitted_records": max(0, len(records)-24),
                "read_with": {"action": "read_context", "kind": "project_dossier", "key": ""},
                "source": str(self.path),
                "rule": "Read full records and bound snapshots before asserting facts. Confirmed means user-adopted, not independently verified or externally approved."}

    def delivery(self, filename, workflow, report, exports=None):
        snapshot = self.ws.snapshot_draft(filename)
        with state_lock(self.path):
            data = self.read()
            row = {"delivery_id": f"pd_{uuid.uuid4().hex[:16]}", **snapshot,
                   "stage": workflow["project_contract"]["stage"],
                   "contract": workflow["project_contract"], "dossier_revision": data["revision"],
                   "quality": report.model_dump(), "created_at": now(),
                   "exports": exports or [],
                   "bindings": {k: v["binding_id"] for k, v in self.bindings().items()},
                   "record_versions": {r["record_id"]: r["revision"] for r in self.active_records()}}
            data["deliveries"].append(row)
            self._save(data)
            return row
