"""AssistantState: structured decision state for the V2 assistant runtime.

The decision loop still passes a plain observation dict to agents for
backwards compatibility. This module defines the stable contract inside
that dict, so future gateway, memory, radar, and submission work can
attach to a real state object instead of adding unrelated top-level keys.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class AssistantBudget(BaseModel):
    iterations_left: int = 0
    tool_calls_left: int = 0


class CurrentContext(BaseModel):
    lifecycle: Optional[dict[str, Any]] = None
    task_checkpoint: dict[str, Any] = Field(default_factory=dict)
    conversation: dict[str, Any] = Field(default_factory=dict)
    context_diagnostics: dict[str, Any] = Field(default_factory=dict)
    saved_references: list[dict[str, Any]] = Field(default_factory=list)
    evidence_preview: list[dict[str, Any]] = Field(default_factory=list)
    drafts: list[str] = Field(default_factory=list)
    draft_plan: dict[str, Any] = Field(default_factory=dict)
    project_memory: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)
    memories: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    manuscript: dict[str, Any] = Field(default_factory=dict)
    paper_reviews: dict[str, Any] = Field(default_factory=dict)
    workflow: Optional[dict[str, Any]] = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    filesystem: dict[str, Any] = Field(default_factory=dict)
    recent_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    connector_state: list[dict[str, Any]] = Field(default_factory=list)
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    last_action: Optional[str] = None
    last_result: Optional[dict[str, Any]] = None
    notes: list[str] = Field(default_factory=list)


class EvidenceState(BaseModel):
    evidence_count: int = 0
    citation_errors: list[str] = Field(default_factory=list)
    citation_warnings: list[str] = Field(default_factory=list)


class ResearchState(BaseModel):
    upcoming_deadlines: list[dict[str, Any]] = Field(default_factory=list)
    pending_tasks: list[dict[str, Any]] = Field(default_factory=list)
    standing_intents: list[dict[str, Any]] = Field(default_factory=list)
    recent_radar_hits: list[dict[str, Any]] = Field(default_factory=list)
    unread_notifications: int = 0


class PendingAction(BaseModel):
    kind: Literal["task", "deadline", "intent", "notification", "budget"]
    title: str
    ref: Optional[str] = None
    priority: int = 2
    due_at: Optional[str] = None


class AssistantState(BaseModel):
    goal: str
    intent: str = ""
    project: Optional[dict[str, Any]] = None
    task: Optional[dict[str, Any]] = None
    current_context: CurrentContext = Field(default_factory=CurrentContext)
    evidence_state: EvidenceState = Field(default_factory=EvidenceState)
    research_state: ResearchState = Field(default_factory=ResearchState)
    pending_actions: list[PendingAction] = Field(default_factory=list)
    quality_risk: Literal["low", "medium", "high"] = "low"
    deadline: Optional[dict[str, Any]] = None
    budget: AssistantBudget = Field(default_factory=AssistantBudget)
    stop_condition: Optional[str] = None

    def to_observation(self) -> dict[str, Any]:
        """Compatibility view consumed by the current agents.

        The structured state is present under ``assistant_state`` while
        legacy top-level keys remain available during migration.
        """
        obs = {
            "goal": self.goal,
            "lifecycle": self.current_context.lifecycle,
            "task_checkpoint": self.current_context.task_checkpoint,
            "conversation": self.current_context.conversation,
            "context_diagnostics": self.current_context.context_diagnostics,
            "intent": self.intent,
            "project": self.project,
            "task": self.task,
            "budget": self.budget.model_dump(),
            "saved_references": self.current_context.saved_references,
            "evidence_count": self.evidence_state.evidence_count,
            "evidence_preview": self.current_context.evidence_preview,
            "drafts": self.current_context.drafts,
            "draft_plan": self.current_context.draft_plan,
            "project_memory": self.current_context.project_memory,
            "profile": self.current_context.profile,
            "memories": self.current_context.memories,
            "skills": self.current_context.skills,
            "manuscript": self.current_context.manuscript,
            "paper_reviews": self.current_context.paper_reviews,
            "workflow": self.current_context.workflow,
            "tools": self.current_context.tools,
            "filesystem": self.current_context.filesystem,
            "recent_tool_calls": self.current_context.recent_tool_calls,
            "connector_state": self.current_context.connector_state,
            "recent_messages": self.current_context.recent_messages,
            "last_action": self.current_context.last_action,
            "last_result": self.current_context.last_result,
            "notes": self.current_context.notes,
            "quality_risk": self.quality_risk,
            "pending_actions": [a.model_dump(exclude_none=True)
                                for a in self.pending_actions],
            "assistant_state": self.model_dump(exclude_none=True),
        }
        if self.research_state.pending_tasks:
            obs["pending_tasks"] = self.research_state.pending_tasks
        if self.research_state.upcoming_deadlines:
            obs["upcoming_deadlines"] = self.research_state.upcoming_deadlines
        if self.research_state.standing_intents:
            obs["standing_intents"] = self.research_state.standing_intents
        if self.research_state.recent_radar_hits:
            obs["recent_radar_hits"] = self.research_state.recent_radar_hits
        if self.research_state.unread_notifications:
            obs["unread_notifications"] = self.research_state.unread_notifications
        if self.deadline:
            obs["deadline"] = self.deadline
        if self.stop_condition:
            obs["stop_condition"] = self.stop_condition
        return obs
