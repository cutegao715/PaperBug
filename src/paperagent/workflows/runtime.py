"""Durable, version-bound workflow cases; independent of real-world stages.

Agent writes are proposals. Only the local user API can approve decisions and
formal versions. Resuming a case never replays tool calls or submissions.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from paperagent.domain.entities import iso, utcnow
from paperagent.workflows.catalog import workflow_spec
from paperagent.workflows.design import contract
from paperagent.workspace.state_store import read_json, state_lock, write_json


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class WorkflowRuntime:
    def __init__(self, ws):
        self.ws = ws
        self.directory = ws.dir / "workflow_cases"

    def _path(self, case_id):
        if not re.fullmatch(r"wf_[a-f0-9]{16}", case_id):
            raise ValueError("Invalid workflow case ID")
        return self.directory / f"{case_id}.json"

    def list(self):
        return [self.get(p.stem) for p in sorted(self.directory.glob("wf_*.json"), reverse=True)]

    def snapshot(self, filename, *, capture=False):
        local = self.ws.dir / filename
        path = local if not Path(filename).is_absolute() and local.is_file() else self.ws.resolve_path(filename)
        if not self.ws.is_path_allowed(path):
            raise ValueError("File is outside this project's allowed roots")
        if not path.is_file():
            raise ValueError(f"File not found: {filename}")
        raw = path.read_bytes()
        row = {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}
        if capture:
            version = self.ws.dir / "workflow_versions" / (row["sha256"] + path.suffix)
            version.parent.mkdir(parents=True, exist_ok=True)
            if not version.exists():
                version.write_bytes(raw)
            row["version_path"] = str(version)
        return row

    def _fingerprint(self, case):
        content = {k: case[k] for k in ("inputs", "outputs", "decisions", "quality", "required_deliverables")}
        if case.get("submission_id"):
            from paperagent.domain.store import DomainStore
            sub = next((s for s in DomainStore(self.ws.root).list_submissions()
                        if s.submission_id == case["submission_id"] and s.project_id == self.ws.project_id), None)
            content["submission"] = sub.model_dump() if sub else None
        return digest(content)

    def _view(self, case):
        changed = []
        for row in [*case["inputs"], *case["outputs"]]:
            try:
                current = self.snapshot(row["path"])["sha256"]
            except (ValueError, OSError):
                current = None
            if current != row["sha256"]:
                changed.append(row["path"])
        fingerprint = self._fingerprint(case)
        approvals = case["approvals"]
        pending = [key for key, item in case["decisions"].items()
                   if item["required"] and not any(a["subject"] == key and a["fingerprint"] == digest(item) for a in approvals)]
        final = any(a["subject"] == "final_delivery" and a["fingerprint"] == fingerprint for a in approvals)
        return {**case, "changed_sources": changed, "pending_confirmations": pending,
                "decision_fingerprints": {k: digest(v) for k, v in case["decisions"].items()},
                "final_approved": final and not changed, "fingerprint": fingerprint,
                "needs_review": bool(changed) or (case["status"] == "completed" and not final)}

    def get(self, case_id):
        path = self._path(case_id)
        if not path.exists():
            raise KeyError(case_id)
        return self._view(read_json(path))

    def start(self, workflow_id, *, title="", required_deliverables=None, inputs=None, scope="full"):
        spec = workflow_spec(workflow_id)
        if not spec or not spec.get("code"):
            raise ValueError("Select one of the ten lifecycle workflows")
        from paperagent.domain.lifecycle import LifecycleService
        saved = LifecycleService(self.ws).get()
        if saved and saved.lifecycle_type not in spec["lifecycle_types"]:
            raise ValueError("Workflow belongs to a different lifecycle")
        identifier = f"wf_{uuid.uuid4().hex[:16]}"
        required = spec["deliverables"] if required_deliverables is None else required_deliverables
        if not required or any(not isinstance(s, str) or not s.strip() for s in required):
            raise ValueError("At least one requested deliverable is required")
        if scope not in {"full", "partial"}:
            raise ValueError("Unknown workflow scope")
        checkpoints = [p for p in spec["confirmation_points"] if p != "大纲"] if scope == "full" else []
        case = {"schema_version": 1, "case_id": identifier, "workflow_id": spec["id"],
                "code": spec["code"], "title": title or spec["name"], "status": "active", "revision": 1,
                "contract": contract(spec), "scope": scope, "required_deliverables": list(dict.fromkeys(required)),
                "steps": {s["id"]: {"status": "pending", "note": ""} for s in spec["steps"]},
                "inputs": [self.snapshot(p, capture=True) for p in (inputs or [])], "outputs": [],
                "decisions": {p: {"content": "", "required": True} for p in checkpoints},
                "approvals": [], "quality": [], "quality_reviewed": False, "submission_id": None,
                "events": [{"action": "start", "actor": "user", "at": iso(utcnow())}]}
        write_json(self._path(identifier), case)
        return self._view(case)

    def update(self, case_id, operation, data=None, *, actor="agent", expected_revision=None):
        data = data or {}
        path = self._path(case_id)
        with state_lock(path):
            case = self.get(case_id)
            if expected_revision is not None and expected_revision != case["revision"]:
                raise ValueError("Workflow changed; reload before updating")
            # Derived fields must never be persisted as authoritative state.
            for key in ("changed_sources", "pending_confirmations", "final_approved", "fingerprint", "needs_review", "decision_fingerprints"):
                case.pop(key, None)
            if operation == "status":
                status = data.get("status")
                if status not in {"active", "paused", "cancelled"}:
                    raise ValueError("Use complete for formal completion")
                if actor != "user":
                    raise ValueError("Only the user can pause, resume or cancel a workflow")
                case["status"] = status
            elif case["status"] in {"paused", "cancelled", "completed"}:
                raise ValueError("Resume this workflow before changing it")
            elif operation == "submission":
                from paperagent.domain.store import DomainStore
                sub = next((s for s in DomainStore(self.ws.root).list_submissions()
                            if s.submission_id == data.get("submission_id") and s.project_id == self.ws.project_id), None)
                if actor != "user" or case["code"] != "P3" or sub is None:
                    raise ValueError("Choose a submission in this research space for P3")
                case["submission_id"] = sub.submission_id
                case["approvals"] = [a for a in case["approvals"] if a["subject"] != "final_delivery"]
            elif operation == "step":
                step = data.get("step_id")
                if step not in case["steps"] or data.get("status") not in {"pending", "active", "done", "skipped"}:
                    raise ValueError("Unknown step or step status")
                case["steps"][step] = {"status": data["status"], "note": str(data.get("note", ""))}
            elif operation == "decision":
                subject = str(data.get("subject", "")).strip()
                content = str(data.get("content", "")).strip()
                if not subject or not content or subject == "final_delivery":
                    raise ValueError("A named decision with concrete content is required")
                # Outline and innovation may be advisory; substantive subjects cannot opt out.
                case["decisions"][subject] = {"content": content,
                    "required": subject not in {"outline", "innovation", "大纲", "创新点"} or bool(data.get("required", False))}
            elif operation in {"inputs", "output"}:
                if operation == "inputs":
                    case["inputs"] = [self.snapshot(p, capture=True) for p in data.get("paths", [])]
                else:
                    label = data.get("label")
                    if label not in case["required_deliverables"]:
                        raise ValueError("Output must identify a requested deliverable")
                    row = {**self.snapshot(data["path"], capture=True), "label": label}
                    if not Path(row["path"]).is_relative_to(self.ws.dir.resolve()):
                        raise ValueError("Output must belong to the current research space; reuse sources as inputs")
                    case["outputs"] = [r for r in case["outputs"] if r["label"] != label] + [row]
                case["quality_reviewed"] = False
            elif operation == "quality":
                issues = data.get("issues", [])
                if any(i.get("level") not in {"ERROR", "WARNING", "SUGGESTION"} or not i.get("message") for i in issues):
                    raise ValueError("Quality issues require a severity and a message")
                case["quality"] = issues
                case["quality_reviewed"] = True
            elif operation == "approve":
                if actor != "user":
                    raise ValueError("Agent proposals cannot approve user decisions")
                view = self._view(case)
                subject = data.get("subject")
                if subject == "final_delivery":
                    if view["changed_sources"]:
                        raise ValueError("Sources changed; rebind and review before approval")
                    fingerprint = view["fingerprint"]
                elif subject in case["decisions"]:
                    if not case["decisions"][subject]["content"].strip():
                        raise ValueError("Write a concrete decision before approving it")
                    fingerprint = digest(case["decisions"][subject])
                else:
                    raise ValueError("Unknown confirmation subject")
                if data.get("fingerprint") != fingerprint:
                    raise ValueError("Confirmation must target the displayed current version")
                case["approvals"].append({"subject": subject, "fingerprint": fingerprint, "actor": "user", "at": iso(utcnow())})
            elif operation == "complete":
                view = self._view(case)
                missing = set(case["required_deliverables"]) - {r["label"] for r in case["outputs"]}
                if missing or view["changed_sources"] or view["pending_confirmations"]:
                    raise ValueError("Missing deliverables, changed sources or unconfirmed decisions")
                if not case["quality_reviewed"] or any(i["level"] == "ERROR" for i in case["quality"]):
                    raise ValueError("Quality review required; unresolved ERROR blocks completion")
                if not view["final_approved"]:
                    raise ValueError("User confirmation of the current formal version is required")
                case["status"] = "completed"
            else:
                raise ValueError("Unknown workflow operation")
            case["revision"] += 1
            case["events"].append({"action": operation, "actor": actor, "data": data, "at": iso(utcnow())})
            write_json(path, case)
        return self._view(case)

    def summary(self):
        return [{k: c[k] for k in ("case_id", "workflow_id", "code", "title", "status", "revision", "pending_confirmations", "final_approved", "needs_review")}
                for c in self.list()]
