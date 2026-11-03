from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from paperagent.workspace.workspace import Workspace


@dataclass
class WorkflowMatch:
    workflow_id: str
    name: str
    confidence: float
    reason: str
    state: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] | None = None
    next_actions: list[str] = field(default_factory=list)
    quality_gates: list[str] = field(default_factory=list)
    drafting_contract: dict[str, Any] = field(default_factory=dict)
    playbook: dict[str, Any] = field(default_factory=dict)
    deliverable_contracts: dict[str, Any] = field(default_factory=dict)
    quality_review_levels: dict[str, list[str]] = field(default_factory=dict)
    interaction_policy: dict[str, Any] = field(default_factory=dict)
    missing_information: list[str] = field(default_factory=list)

    def to_observation(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "confidence": self.confidence,
            "reason": self.reason,
            "state": self.state,
            "lifecycle": self.lifecycle,
            "next_actions": self.next_actions,
            "quality_gates": self.quality_gates,
            "drafting_contract": self.drafting_contract,
            "playbook": self.playbook,
            "deliverable_contracts": self.deliverable_contracts,
            "quality_review_levels": self.quality_review_levels,
            "interaction_policy": self.interaction_policy,
            "missing_information": self.missing_information,
        }


class Workflow(Protocol):
    workflow_id: str
    name: str

    def match(self, ws: Workspace, goal: str, lifecycle: dict | None = None) -> WorkflowMatch | None:
        ...
