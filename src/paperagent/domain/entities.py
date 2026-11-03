"""V2 Research Domain Layer: structured research state.

Spec rule: Structured Research State ≠ Memory. Facts that carry real
consequences when wrong (missed deadlines, wrong submission status)
live here; Memory only helps the assistant understand the user.

Entities: Task / Deadline / Notification / StandingIntent / Automation /
TriggerEvent / Watcher / Opportunity / RadarHit / Submission / MemoryRecord /
ResearchProfile.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Task(BaseModel):
    task_id: str
    title: str
    origin: Literal["user", "agent", "system"] = "user"
    status: Literal["todo", "in_progress", "done", "cancelled"] = "todo"
    priority: int = Field(default=2, ge=1, le=3)  # 1 high .. 3 low
    due_date: Optional[str] = None  # ISO datetime
    depends_on: list[str] = Field(default_factory=list)
    project_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    updated_at: str = Field(default_factory=lambda: iso(utcnow()))


TASK_TRANSITIONS: dict[str, set[str]] = {
    "todo": {"in_progress", "done", "cancelled"},
    "in_progress": {"done", "cancelled", "todo"},
    "done": set(),
    "cancelled": {"todo"},
}


class Deadline(BaseModel):
    deadline_id: str
    title: str
    when: str  # ISO datetime
    scope: Optional[str] = None  # project_id
    linked_entity: Optional[str] = None
    # hours before `when` to remind; [72, 24] => 3 days and 1 day before
    reminder_hours: list[int] = Field(default_factory=lambda: [72, 24])
    reminders_sent: list[str] = Field(default_factory=list)  # keys like "h72"
    overdue_notified: bool = False


class Notification(BaseModel):
    notification_id: str
    relay_project_id: str | None = None
    relay_target_device: str | None = None
    type: Literal["deadline", "radar", "task", "digest", "submission", "system"] = "system"
    title: str
    body: str = ""
    channel: Literal["in_app", "email"] = "in_app"
    read: bool = False
    dismissed: bool = False
    ref: Optional[str] = None  # e.g. radar hit id / task id
    dedup_key: str = ""
    priority: int = Field(default=2, ge=1, le=3)
    created_at: str = Field(default_factory=lambda: iso(utcnow()))


class StandingIntent(BaseModel):
    """Long-lived structured intent, e.g. "浙江省 AI 社科课题出现时提醒我".

    `topic` is kept as a compatibility/search shortcut. The V2 contract
    lives in structured_query / scope / notify_policy / dedup_key.
    """
    intent_id: str
    sync_id: str = ""
    sync_updated: int = 0
    sync_version: int = 0
    sync_origin: str = ""
    deleted: bool = False
    description: str
    type: Literal["literature", "grant", "journal"] = "literature"
    topic: str = ""  # search query derived from the intent
    structured_query: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)
    notify_policy: dict[str, Any] = Field(default_factory=lambda: {
        "channel": "in_app",
        "cadence": "asap",
        "min_score": 0.0,
    })
    dedup_key: str = ""
    enabled: bool = True
    created_at: str = Field(default_factory=lambda: iso(utcnow()))


class Automation(BaseModel):
    automation_id: str
    name: str
    trigger_type: Literal["schedule", "event", "deadline", "state_change", "external"] = "schedule"
    cadence: Literal["hourly", "daily", "weekly"] = "daily"
    action: Literal["radar_literature", "daily_digest"] = "radar_literature"
    enabled: bool = True
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    trigger_ref: Optional[str] = None
    action_spec: dict[str, Any] = Field(default_factory=dict)


class TriggerEvent(BaseModel):
    trigger_id: str
    type: Literal["schedule", "deadline", "state_change", "external"]
    source: str
    ref: Optional[str] = None
    action: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    handled: bool = False


class Watcher(BaseModel):
    watcher_id: str
    name: str
    watcher_type: Literal["literature", "grant", "journal", "submission", "deadline", "external"]
    source: str
    query: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    cursor: Optional[str] = None
    last_checked: Optional[str] = None
    created_at: str = Field(default_factory=lambda: iso(utcnow()))


AUTOMATION_INTERVALS: dict[str, int] = {"hourly": 3600, "daily": 86400, "weekly": 604800}


class RadarHit(BaseModel):
    hit_id: str
    radar_type: Literal["literature", "grant", "journal"] = "literature"
    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    source: str = "openalex"
    topic_query: str = ""
    matched: bool = False
    seen: bool = False
    created_at: str = Field(default_factory=lambda: iso(utcnow()))


class Opportunity(BaseModel):
    opportunity_id: str
    opportunity_type: Literal[
        "literature",
        "grant",
        "journal",
        "conference",
        "school_notice",
        "policy",
    ] = "literature"
    title: str
    source: str = "unknown"
    source_url: Optional[str] = None
    dedup_key: str
    topic_query: str = ""
    match_score: float = Field(default=0.0, ge=0.0, le=1.0)
    matched: bool = False
    ai_evaluation: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    seen: bool = False
    notified: bool = False
    feedback: Optional[Literal["interested", "not_interested", "saved"]] = None
    created_at: str = Field(default_factory=lambda: iso(utcnow()))


SubmissionStatus = Literal[
    "submitted",
    "with_editor",
    "under_review",
    "major_revision",
    "minor_revision",
    "accepted",
    "rejected",
    "published",
    "withdrawn",
]


class SubmissionStatusEntry(BaseModel):
    status: SubmissionStatus
    at: str = Field(default_factory=lambda: iso(utcnow()))
    source: str = "manual"
    note: str = ""


class Submission(BaseModel):
    submission_id: str
    paper_id: Optional[str] = None
    project_id: Optional[str] = None
    journal_id: Optional[str] = None
    journal_name: str = ""
    manuscript_id: str = ""
    title: str = ""
    submitted_at: Optional[str] = None
    previous_submission_id: Optional[str] = None
    revision_round: int = 0
    status: SubmissionStatus = "submitted"
    status_history: list[SubmissionStatusEntry] = Field(default_factory=list)
    last_update: str = Field(default_factory=lambda: iso(utcnow()))
    created_at: str = Field(default_factory=lambda: iso(utcnow()))


class MemoryRecord(BaseModel):
    trust: Literal["user_adopted", "distilled_candidate", "model_transient"] = "model_transient"
    memory_id: str
    layer: Literal["working", "project", "user", "episodic", "prospective"]
    content: str
    scope: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    importance: int = Field(default=2, ge=1, le=3)
    ref: Optional[str] = None
    kind: Literal["preference", "decision", "fact", "experience", "hypothesis", "note"] = "note"
    source: dict[str, Any] = Field(default_factory=dict)
    confirmation: Literal["provided", "supported", "inferred", "unconfirmed"] = "unconfirmed"
    status: Literal["active", "superseded", "deleted"] = "active"
    pinned: bool = False
    expires_at: Optional[datetime] = None
    supersedes: Optional[str] = None
    replaced_by: Optional[str] = None
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    updated_at: str = Field(default_factory=lambda: iso(utcnow()))


class LanguageProfile(BaseModel):
    """Language and style preferences for UI, writing, and terminology."""

    ui_language: Literal["zh", "en"] = "zh"
    writing_language: Literal["zh", "en", "bilingual"] = "zh"
    terminology_policy: Literal[
        "translate",
        "preserve",
        "bilingual_glossary",
    ] = "preserve"
    citation_style: Literal["apa", "gbt7714", "chicago", "ieee", "vancouver"] = "apa"
    writing_style: Literal["journal", "thesis", "grant", "conference", "plain"] = "journal"
    glossary: dict[str, str] = Field(default_factory=dict)


class DisciplineProfile(BaseModel):
    """Discipline-specific rules used to select skills, tools, and checks."""

    discipline_id: str
    name: str = ""
    keywords: list[str] = Field(default_factory=list)
    theories: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    instruments: list[str] = Field(default_factory=list)
    artifact_structures: dict[str, list[str]] = Field(default_factory=dict)
    terminology: dict[str, str] = Field(default_factory=dict)
    quality_checks: list[str] = Field(default_factory=list)
    figure_conventions: list[str] = Field(default_factory=list)
    formula_conventions: list[str] = Field(default_factory=list)


class ResearchProfile(BaseModel):
    """User-level research identity.

    The original V2-m1 fields stay at the top level for compatibility with
    radar/recommender code and existing profile.json files.
    """

    name: str = ""
    roles: list[str] = Field(default_factory=list)
    affiliations: list[str] = Field(default_factory=list)
    directions: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    institution: Optional[str] = None
    methods: list[str] = Field(default_factory=list)
    publications: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    target_journals: list[str] = Field(default_factory=list)
    target_conferences: list[str] = Field(default_factory=list)
    target_funders: list[str] = Field(default_factory=list)
    advisor_rules: list[str] = Field(default_factory=list)
    language: LanguageProfile = Field(default_factory=LanguageProfile)
    disciplines: list[DisciplineProfile] = Field(default_factory=list)
    notes: str = ""


class ProjectProfile(BaseModel):
    project_id: str
    project_type: Literal[
        "paper",
        "journal_article",
        "conference_paper",
        "grant",
        "grant_midterm",
        "thesis",
        "experiment",
        "general",
    ] = "general"
    title: str = ""
    language: Optional[LanguageProfile] = None
    discipline_ids: list[str] = Field(default_factory=list)
    target_venue: Optional[str] = None
    target_funder: Optional[str] = None
    target_school: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    status: Literal["active", "paused", "completed", "archived"] = "active"
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    updated_at: str = Field(default_factory=lambda: iso(utcnow()))


class ImportedFile(BaseModel):
    file_id: str
    path: str
    content_type: str = "application/octet-stream"
    size: int = 0
    mtime: Optional[str] = None
    sha256: str = ""
    source: Literal["local_folder", "manual", "zotero", "connector"] = "manual"
    project_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    imported_at: str = Field(default_factory=lambda: iso(utcnow()))


class ZoteroItem(BaseModel):
    item_id: str
    zotero_key: str
    item_type: str = "journalArticle"
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    collections: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    project_id: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)
    synced_at: str = Field(default_factory=lambda: iso(utcnow()))


def radar_dedup_key(title: str, doi: Optional[str]) -> str:
    if doi:
        return doi.lower()
    normalized = re.sub(r"\W+", "", title.lower())
    return "t:" + hashlib.sha1(normalized.encode()).hexdigest()[:16]
