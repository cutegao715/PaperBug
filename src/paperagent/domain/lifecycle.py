"""Explicit real-world lifecycle state, independent from task completion."""
from __future__ import annotations
from paperagent.quality.policies import author_write

from typing import Literal
from pydantic import BaseModel, Field, model_validator

from paperagent.domain.entities import iso, utcnow
from paperagent.workflows.catalog import CATALOG
from paperagent.workspace.state_store import read_json, state_lock, write_json

LifecycleType = Literal["project", "paper_conference", "thesis"]


class LifecycleState(BaseModel):
    schema_version: int = 1
    lifecycle_type: LifecycleType
    stage: str
    focus_stages: list[str] = Field(default_factory=list)
    status: Literal["active", "paused", "completed", "archived"] = "active"
    source: str = "user"
    history: list[dict] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: iso(utcnow()))

    @model_validator(mode="after")
    def valid_stages(self):
        stages = CATALOG[self.lifecycle_type]["stages"]
        if self.stage not in stages or any(s not in stages for s in self.focus_stages):
            raise ValueError("Stage does not belong to this lifecycle")
        return self


class LifecycleService:
    def __init__(self, ws):
        self.path = ws.dir / "lifecycle.json"
        self.events_path = ws.dir / "lifecycle_events.jsonl"
        self.remote_path = ws.dir / "lifecycle_sync.json"

    def events(self):
        import json
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _append(self, event):
        import json, os, uuid
        rows = self.events() + [event]
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.events_path.with_name("." + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("w", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(self.events_path)
        finally:
            temp.unlink(missing_ok=True)

    def _event(self, state, previous, reason):
        import uuid
        return {"idempotency_key":uuid.uuid4().hex,
                "stage_from":previous.stage if previous else None,
                "state":state.model_dump(mode="json", exclude={"history"}),
                "reason":reason, "actor":state.source}

    def pending_events(self):
        with state_lock(self.path):
            remote = read_json(self.remote_path, {"events":[]})["events"]
            known = {r["idempotency_key"] for r in remote}
            return [r for r in self.events() if r["idempotency_key"] not in known]

    def remote_cursor(self):
        with state_lock(self.path):
            seqs = {r["server_seq"] for r in read_json(self.remote_path, {"events":[]})["events"]}
            cursor = 0
            while cursor + 1 in seqs:
                cursor += 1
            return cursor

    def accept_remote(self, rows):
        with state_lock(self.path):
            cached = read_json(self.remote_path, {"events":[]})["events"]
            by_seq = {r["server_seq"]:r for r in cached}
            keys = {r["idempotency_key"]:r["server_seq"] for r in cached}
            for row in rows:
                LifecycleState.model_validate(row["state"])
                seq = row["server_seq"]
                if not isinstance(seq,int) or seq < 1:
                    raise ValueError("Invalid server sequence")
                if seq in by_seq and by_seq[seq] != row:
                    raise ValueError("Conflicting server sequence")
                if row["idempotency_key"] in keys and keys[row["idempotency_key"]] != seq:
                    raise ValueError("Conflicting event identity")
                by_seq[seq] = row
                keys[row["idempotency_key"]] = seq
            write_json(self.remote_path, {"events":[by_seq[n] for n in sorted(by_seq)]})

    def get(self) -> LifecycleState | None:
        with state_lock(self.path):
            local = self.events()
            remote = read_json(self.remote_path, {"events":[]})["events"]
            if not local and not remote and self.path.exists():
                legacy = LifecycleState.model_validate(read_json(self.path))
                event = self._event(legacy, None, "Legacy lifecycle snapshot imported")
                event["legacy_history"] = legacy.history
                self._append(event)
                local = [event]
            pending = self.pending_events()
            ordered = sorted(remote, key=lambda r:r["server_seq"]) + pending
            if not ordered:
                return None
            state = LifecycleState.model_validate(ordered[-1]["state"])
            history = []
            previous = None
            for row in ordered:
                history.extend(row.get("legacy_history", []))
                history.append({"before":previous,"after":row["state"],"reason":row["reason"],
                                "source":row["actor"],"at":row["state"]["updated_at"],
                                "server_seq":row.get("server_seq"),"conflict":row.get("conflict",False)})
                previous = row["state"]
            state.history = history
            write_json(self.path, state.model_dump())
            return state

    @author_write
    def save(self, *, lifecycle_type: str, stage: str, reason: str,
             focus_stages: list[str] | None = None, status: str = "active",
             source: str = "user") -> LifecycleState:
        if not reason.strip() or len(reason) > 2000:
            raise ValueError("Lifecycle change requires a reason (at most 2000 characters)")
        with state_lock(self.path):
            previous = self.get()
            state = LifecycleState(lifecycle_type=lifecycle_type, stage=stage,
                                   focus_stages=focus_stages or [], status=status, source=source)
            self._append(self._event(state, previous, reason))
            return self.get()
