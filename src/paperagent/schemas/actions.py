"""AgentAction protocol: every decision the agent makes is one of these.

The decision loop never free-runs; it asks the agent for the next
AgentAction, executes it, observes, and repeats. Discriminated by
``action`` so validation is deterministic.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


class SearchAction(BaseModel):
    action: Literal["search"] = "search"
    query: str
    max_results: int = Field(default=5, ge=1, le=20)
    # "semantic_scholar" (default) or "offline_stub" for tests/demo
    provider: Optional[str] = None


class ReadPdfAction(BaseModel):
    action: Literal["read_pdf"] = "read_pdf"
    path: str  # absolute, or relative to workspace root
    pages: Optional[list[int]] = None  # 1-based; None = first 5 pages


class ImportFileAction(BaseModel):
    action: Literal["import_file"] = "import_file"
    path: str  # absolute, or relative to workspace root


class ImportFolderAction(BaseModel):
    action: Literal["import_folder"] = "import_folder"
    folder: str  # absolute, or relative to workspace root
    recursive: bool = True


class ToolCallAction(BaseModel):
    action: Literal["tool_call"] = "tool_call"
    tool_id: str
    input: dict[str, Any] = Field(default_factory=dict)


class SaveReferenceAction(BaseModel):
    action: Literal["save_reference"] = "save_reference"
    ref_id: str  # must be a ref_id previously returned by search


class SaveEvidenceAction(BaseModel):
    action: Literal["save_evidence"] = "save_evidence"
    ref_id: str
    quote: str
    page: Optional[int] = None
    section: Optional[str] = None
    supports: Optional[str] = None
    source_path: Optional[str] = None
    source_coverage: Literal["full_text", "partial", "abstract_only", "unknown"] = "unknown"
    relation: Literal["supports", "contradicts", "context"] = "supports"
    themes: list[str] = Field(default_factory=list, max_length=30)
    method: str = ""
    population: str = ""
    finding: str = ""
    limitations: list[str] = Field(default_factory=list, max_length=30)


class WriteDraftAction(BaseModel):
    action: Literal["write_draft"] = "write_draft"
    filename: str = "draft.md"
    content: str  # markdown with {{ref:<id>}} placeholders


class AppendDraftAction(BaseModel):
    action: Literal["append_draft"] = "append_draft"
    filename: str = "draft.md"
    content: str  # markdown to append; use stable {{ref:<id>}} placeholders


class ReadDraftAction(BaseModel):
    action: Literal["read_draft"] = "read_draft"
    filename: str = "draft.md"
    max_chars: int = Field(default=5000, ge=500, le=20000)
    offset: int = Field(default=0, ge=0)
    heading: Optional[str] = None


class ReadContextAction(BaseModel):
    action: Literal["read_context"] = "read_context"
    kind: Literal["workflow_contract", "skill", "skill_resource", "skill_catalog", "manuscript", "paper_reviews", "paper_review_source", "messages", "evidence", "references", "materials", "task", "task_events", "conversation", "conversation_events", "project_dossier", "project_source", "project_acceptance", "project_claims"]
    key: str = ""
    resource_id: str = ""
    expected_sha256: str = ""
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=6000, ge=500, le=20000)


class UpdateTaskAction(BaseModel):
    action: Literal["update_task_context", "update_conversation_context"] = "update_task_context"
    plan: Optional[dict[str, Any]] = None
    open_questions: Optional[list[str]] = None
    decisions: Optional[list[str]] = None
    workflow_options: Optional[dict[str, Any]] = None


from paperagent.domain.project_dossier import ProjectRecordInput


class RecordProjectAction(ProjectRecordInput):
    action: Literal["record_project_context"] = "record_project_context"


from paperagent.domain.manuscript import ManuscriptRecordInput


class RecordManuscriptAction(ManuscriptRecordInput):
    action: Literal["record_manuscript_context"] = "record_manuscript_context"


from paperagent.domain.paper_review import PaperReviewInput, ReviewFollowupInput


class SavePaperReviewAction(PaperReviewInput):
    action: Literal["save_paper_review"] = "save_paper_review"


class ReviewIssueAction(ReviewFollowupInput):
    action: Literal["record_review_followup"] = "record_review_followup"
    report_id: str
    issue_id: str


class ReplaceDraftSectionAction(BaseModel):
    action: Literal["replace_draft_section"] = "replace_draft_section"
    filename: str = "draft.md"
    heading: str
    content: str


class UpdateDraftPlanAction(BaseModel):
    action: Literal["update_draft_plan"] = "update_draft_plan"
    filename: str = "draft.md"
    sections: list[dict[str, Any]] = Field(default_factory=list)
    next_section: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class AskUserAction(BaseModel):
    action: Literal["ask_user"] = "ask_user"
    question: str


class ChatReplyAction(BaseModel):
    action: Literal["chat_reply"] = "chat_reply"
    text: str  # conversational reply; terminal, no draft required


class FinishAction(BaseModel):
    action: Literal["finish"] = "finish"
    summary: str


AgentAction = Union[
    SearchAction,
    ReadPdfAction,
    ImportFileAction,
    ImportFolderAction,
    ToolCallAction,
    SaveReferenceAction,
    SaveEvidenceAction,
    WriteDraftAction,
    AppendDraftAction,
    ReadDraftAction,
    ReadContextAction,
    UpdateTaskAction,
    RecordProjectAction,
    RecordManuscriptAction,
    SavePaperReviewAction,
    ReviewIssueAction,
    ReplaceDraftSectionAction,
    UpdateDraftPlanAction,
    AskUserAction,
    ChatReplyAction,
    FinishAction,
]
