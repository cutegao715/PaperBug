"""Standing Intent normalization.

This is a deterministic V2-m1 parser: it preserves explicit structured
fields when provided and derives a simple search query / dedup key when
the user supplies only natural language plus an optional topic.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


DEFAULT_NOTIFY_POLICY = {
    "channel": "in_app",
    "cadence": "asap",
    "min_score": 0.0,
}


def normalize_intent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    description = (out.get("description") or "").strip()
    intent_type = out.get("type") or "literature"
    topic = (out.get("topic") or "").strip()
    structured = dict(out.get("structured_query") or {})
    if topic and "query" not in structured:
        structured["query"] = topic
    if description and "description" not in structured:
        structured["description"] = description
    structured.setdefault("type", intent_type)
    out["description"] = description
    out["type"] = intent_type
    out["topic"] = topic or _topic_from_structured(structured) or description
    out["structured_query"] = structured
    out["scope"] = dict(out.get("scope") or {"profile": True})
    out["notify_policy"] = {
        **DEFAULT_NOTIFY_POLICY,
        **dict(out.get("notify_policy") or {}),
    }
    out["dedup_key"] = out.get("dedup_key") or dedup_key(
        intent_type, structured, out["scope"])
    return out


def dedup_key(intent_type: str, structured_query: dict[str, Any],
              scope: dict[str, Any]) -> str:
    raw = json.dumps({
        "type": intent_type,
        "structured_query": _stable(structured_query),
        "scope": _stable(scope),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _topic_from_structured(structured_query: dict[str, Any]) -> str:
    for key in ("query", "topic", "keywords"):
        value = structured_query.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = [str(v).strip() for v in value if str(v).strip()]
            if parts:
                return " ".join(parts)
    return ""


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def stamp_intent(store, item):
    import time, uuid
    from paperagent.workspace.state_store import read_json, write_json
    path = store.dir / 'intent_origin.json'
    origin = read_json(path).get('origin')
    if not origin:
        origin = uuid.uuid4().hex
        write_json(path, {'origin':origin})
    item.sync_id = item.sync_id or uuid.uuid4().hex
    item.sync_updated = max(time.time_ns(),item.sync_updated+1)
    item.sync_version += 1
    item.sync_origin = origin
    return item


def persist_intents(store, items):
    # Internal replication persistence; called only under the intent-file lock.
    import uuid, os
    path = store._path('standing_intents')
    temp = path.with_name('.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as stream:
            for item in items:
                stream.write(item.model_dump_json()+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
