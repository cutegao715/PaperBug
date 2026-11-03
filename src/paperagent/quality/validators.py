"""Deterministic validators for domain state."""
from __future__ import annotations

from paperagent.domain.entities import SubmissionStatus


SUBMISSION_TRANSITIONS: dict[str, set[str]] = {
    "submitted": {"with_editor", "under_review", "withdrawn", "rejected"},
    "with_editor": {"under_review", "major_revision", "minor_revision",
                    "accepted", "rejected", "withdrawn"},
    "under_review": {"major_revision", "minor_revision", "accepted",
                     "rejected", "withdrawn"},
    "major_revision": {"submitted", "with_editor", "under_review",
                       "accepted", "rejected", "withdrawn"},
    "minor_revision": {"submitted", "with_editor", "under_review",
                       "accepted", "rejected", "withdrawn"},
    "accepted": {"published"},
    "rejected": set(),
    "published": set(),
    "withdrawn": set(),
}


class InvalidSubmissionTransition(ValueError):
    pass


def validate_submission_transition(before: SubmissionStatus,
                                   after: SubmissionStatus) -> None:
    if before == after:
        return
    allowed = SUBMISSION_TRANSITIONS[before]
    if after not in allowed:
        raise InvalidSubmissionTransition(
            f"invalid submission transition: {before} -> {after}")
