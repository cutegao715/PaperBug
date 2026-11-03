"""Local folder connector for research materials.

This connector is read-only. It records supported files as ImportedFile domain
objects and can emit watcher trigger events for downstream import/index steps.
"""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from paperagent.domain.entities import iso, utcnow
from paperagent.domain.store import DomainStore
from paperagent.gateway.events import AssistantEvent
from paperagent.tasks.watcher import WatcherManager

SUPPORTED_SUFFIXES = {".pdf", ".doc", ".docx", ".wps", ".md", ".txt", ".bib", ".ris"}


def split_supported_files(
    folder: str | Path,
    *,
    recursive: bool = True,
) -> tuple[Path, list[Path], list[Path]]:
    root = Path(folder).resolve()
    if not root.exists() or not root.is_dir():
        raise NotADirectoryError(root)
    paths = root.rglob("*") if recursive else root.glob("*")
    files = sorted(p for p in paths if p.is_file())
    supported = [p for p in files if p.suffix.lower() in SUPPORTED_SUFFIXES]
    unsupported = [p for p in files if p.suffix.lower() not in SUPPORTED_SUFFIXES]
    return root, supported, unsupported


def summarize_unsupported_files(paths: list[Path], *, limit: int = 20) -> list[dict[str, Any]]:
    return [
        {"path": str(path), "filename": path.name, "suffix": path.suffix.lower() or "(none)"}
        for path in paths[:limit]
    ]


class LocalFileRecord(BaseModel):
    path: str
    content_type: str = "application/octet-stream"
    size: int = 0
    mtime: str = ""
    sha256: str = ""
    project_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocalFolderConnector:
    connector_id = "local_folder"

    def __init__(self, store: DomainStore):
        self.store = store
        self.watchers = WatcherManager(store)

    def normalize(self, payload: dict[str, Any]) -> LocalFileRecord:
        path = Path(str(payload.get("path") or "")).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        stat = path.stat()
        content_type, _ = mimetypes.guess_type(str(path))
        return LocalFileRecord(
            path=str(path),
            content_type=content_type or "application/octet-stream",
            size=stat.st_size,
            mtime=iso(utcnow()) if not stat.st_mtime else _mtime_iso(stat.st_mtime),
            sha256=_sha256(path),
            project_id=payload.get("project_id"),
            metadata={"suffix": path.suffix.lower(), "filename": path.name},
        )

    def to_event(self, normalized: LocalFileRecord,
                 *, project_id: str | None = None) -> AssistantEvent:
        return AssistantEvent(
            event_id=f"local_file:{normalized.sha256[:16]}",
            type="connector_event",
            source="connector:local_folder",
            project_id=project_id or normalized.project_id,
            payload=normalized.model_dump(),
        )

    def scan(self, folder: str | Path, *, watcher_id: str | None = None,
             project_id: str | None = None, recursive: bool = True) -> dict[str, Any]:
        root = Path(folder).resolve()
        if not root.exists() or not root.is_dir():
            raise NotADirectoryError(root)
        _, supported, _ = split_supported_files(root, recursive=recursive)
        known = self.store.imported_file_dedup_keys()
        imported = []
        events = []
        for path in supported:
            record = self.normalize({"path": str(path), "project_id": project_id})
            if record.sha256 in known:
                continue
            known.add(record.sha256)
            imported_file = self.store.add_imported_file(
                path=record.path,
                content_type=record.content_type,
                size=record.size,
                mtime=record.mtime,
                sha256=record.sha256,
                source="local_folder",
                project_id=project_id,
                metadata=record.metadata,
            )
            imported.append(imported_file)
            assistant_event = self.to_event(record, project_id=project_id)
            trigger = None
            if watcher_id:
                trigger = self.watchers.ingest_change(
                    watcher_id,
                    {
                        "action": "local_file_imported",
                        "path": record.path,
                        "sha256": record.sha256,
                        "cursor": record.sha256,
                    },
                )
            events.append({"event": assistant_event, "trigger": trigger})
        return {
            "folder": str(root),
            "imported": imported,
            "events": events,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _mtime_iso(timestamp: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")
