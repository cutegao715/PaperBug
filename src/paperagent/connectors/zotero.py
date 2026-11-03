"""Zotero connector.

V1 is intentionally credential-free: it accepts Zotero Web API-like item
payloads or exported JSON and normalizes them into PaperAgent domain state.
OAuth/API sync can later feed the same ``sync_items`` method.
"""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from paperagent.domain.store import DomainStore
from paperagent.gateway.events import AssistantEvent
from paperagent.schemas.references import ReferenceCandidate, make_ref_id
from paperagent.tasks.watcher import WatcherManager


class ZoteroRecord(BaseModel):
    zotero_key: str
    item_type: str = "journalArticle"
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None
    collections: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    project_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def to_candidate(self) -> ReferenceCandidate:
        first = self.authors[0].split()[-1] if self.authors else "anon"
        first_word = (self.title.split() or ["untitled"])[0]
        return ReferenceCandidate(
            ref_id=make_ref_id(first, self.year, first_word),
            title=self.title or "Untitled",
            authors=self.authors,
            year=self.year,
            venue=self.venue,
            doi=self.doi,
            url=self.url,
            source="zotero",
        )


class ZoteroConnector:
    connector_id = "zotero"

    def __init__(self, store: DomainStore):
        self.store = store
        self.watchers = WatcherManager(store)

    def normalize(self, payload: dict[str, Any]) -> ZoteroRecord:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        key = str(payload.get("key") or data.get("key") or data.get("itemKey") or "").strip()
        if not key:
            key = _stable_key(data)
        title = str(data.get("title") or "").strip()
        creators = data.get("creators") or data.get("authors") or []
        return ZoteroRecord(
            zotero_key=key,
            item_type=str(data.get("itemType") or data.get("item_type") or "journalArticle"),
            title=title,
            authors=_authors(creators),
            year=_year(data),
            venue=(data.get("publicationTitle") or data.get("conferenceName")
                   or data.get("proceedingsTitle") or data.get("journalAbbreviation")
                   or data.get("venue") or None),
            doi=(data.get("DOI") or data.get("doi") or None),
            url=(data.get("url") or data.get("URL") or None),
            collections=_strings(data.get("collections")),
            tags=_tags(data.get("tags")),
            notes=_notes(data),
            attachments=_attachments(data),
            project_id=payload.get("project_id"),
            raw=payload,
        )

    def to_event(self, normalized: ZoteroRecord,
                 *, project_id: str | None = None) -> AssistantEvent:
        return AssistantEvent(
            event_id=f"zotero:{normalized.zotero_key}",
            type="connector_event",
            source="connector:zotero",
            project_id=project_id or normalized.project_id,
            payload=normalized.model_dump(),
        )

    def sync_items(self, items: list[dict[str, Any]], *,
                   watcher_id: str | None = None,
                   project_id: str | None = None) -> dict[str, Any]:
        synced = []
        references = []
        events = []
        imported_files = []
        for payload in items:
            payload = dict(payload)
            payload.setdefault("project_id", project_id)
            record = self.normalize(payload)
            item = self.store.upsert_zotero_item(
                zotero_key=record.zotero_key,
                item_type=record.item_type,
                title=record.title,
                authors=record.authors,
                year=record.year,
                venue=record.venue,
                doi=record.doi,
                url=record.url,
                collections=record.collections,
                tags=record.tags,
                notes=record.notes,
                attachments=record.attachments,
                project_id=project_id,
                raw=record.raw,
            )
            synced.append(item)
            if record.title:
                references.append(record.to_candidate())
            imported_files.extend(self._record_attachments(record, project_id=project_id))
            event = self.to_event(record, project_id=project_id)
            trigger = None
            if watcher_id:
                trigger = self.watchers.ingest_change(
                    watcher_id,
                    {
                        "action": "zotero_item_synced",
                        "zotero_key": record.zotero_key,
                        "title": record.title,
                        "cursor": record.zotero_key,
                    },
                )
            events.append({"event": event, "trigger": trigger})
        return {
            "synced": synced,
            "references": references,
            "imported_files": imported_files,
            "events": events,
        }

    def _record_attachments(self, record: ZoteroRecord,
                            *, project_id: str | None = None):
        out = []
        known = self.store.imported_file_dedup_keys()
        for attachment in record.attachments:
            path_value = attachment.get("path") or attachment.get("local_path")
            if not path_value:
                continue
            path = Path(path_value).expanduser().resolve()
            if not path.exists() or not path.is_file():
                continue
            sha = _sha256(path)
            if sha in known:
                continue
            known.add(sha)
            content_type, _ = mimetypes.guess_type(str(path))
            stat = path.stat()
            out.append(self.store.add_imported_file(
                path=str(path),
                content_type=content_type or "application/octet-stream",
                size=stat.st_size,
                mtime=_mtime_iso(stat.st_mtime),
                sha256=sha,
                source="zotero",
                project_id=project_id,
                metadata={
                    "zotero_key": record.zotero_key,
                    "title": attachment.get("title") or path.name,
                    "suffix": path.suffix.lower(),
                    "filename": path.name,
                },
            ))
        return out


def _authors(creators: Any) -> list[str]:
    if not isinstance(creators, list):
        return []
    out = []
    for creator in creators:
        if isinstance(creator, str):
            name = creator.strip()
        elif isinstance(creator, dict):
            name = creator.get("name") or " ".join(
                p for p in [creator.get("firstName"), creator.get("lastName")] if p)
        else:
            name = ""
        if name:
            out.append(name)
    return out


def _year(data: dict[str, Any]) -> int | None:
    value = str(data.get("date") or data.get("year") or "")
    import re
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return int(match.group(0)) if match else None


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for tag in value:
        if isinstance(tag, dict):
            text = tag.get("tag")
        else:
            text = tag
        if str(text or "").strip():
            out.append(str(text).strip())
    return out


def _notes(data: dict[str, Any]) -> list[str]:
    notes = data.get("notes") or []
    if isinstance(notes, str):
        return [notes]
    if isinstance(notes, list):
        out = []
        for note in notes:
            if isinstance(note, dict):
                text = note.get("note") or note.get("text")
            else:
                text = note
            if str(text or "").strip():
                out.append(str(text).strip())
        return out
    return []


def _attachments(data: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = data.get("attachments") or []
    return [a for a in attachments if isinstance(a, dict)]


def _stable_key(data: dict[str, Any]) -> str:
    raw = "|".join(str(data.get(k) or "") for k in ("title", "DOI", "doi", "url"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8].upper()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _mtime_iso(timestamp: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")
