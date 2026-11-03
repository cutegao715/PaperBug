"""Submission tracking: status machine and state-change triggers."""
from __future__ import annotations

from paperagent.domain.entities import (Submission, SubmissionStatus,
                                        SubmissionStatusEntry, iso, utcnow)
from paperagent.domain.store import DomainStore
from paperagent.quality.validators import validate_submission_transition
from paperagent.tasks.notification_service import NotificationService
from paperagent.tasks.submission_parser import (ParsedSubmissionEmail,
                                                SubmissionEmailParser)
from paperagent.tasks.trigger_engine import TriggerEngine


class SubmissionManager:
    def __init__(self, store: DomainStore,
                 trigger_engine: TriggerEngine | None = None):
        self.store = store
        self.trigger_engine = trigger_engine or TriggerEngine(store)

    def create(self, *, title: str, journal_name: str = "",
               manuscript_id: str = "", project_id: str | None = None,
               paper_id: str | None = None,
               journal_id: str | None = None,
               status: SubmissionStatus = "submitted",
               source: str = "manual", submitted_at: str | None = None,
               previous_submission_id: str | None = None) -> Submission:
        if submitted_at:
            from datetime import datetime
            datetime.fromisoformat(submitted_at)
        if previous_submission_id:
            previous = self._get(previous_submission_id)
            if previous.project_id != project_id or previous.status not in {"rejected", "withdrawn"}:
                raise ValueError("Retargeting requires a rejected or withdrawn submission in this research space")
        entry = SubmissionStatusEntry(status=status, source=source)
        return self.store.add_submission(
            title=title.strip(),
            journal_name=journal_name.strip(),
            manuscript_id=manuscript_id.strip(),
            project_id=project_id,
            paper_id=paper_id,
            journal_id=journal_id,
            status=status,
            submitted_at=submitted_at,
            previous_submission_id=previous_submission_id,
            status_history=[entry],
            last_update=entry.at,
        )

    def transition(self, submission_id: str, status: SubmissionStatus,
                   *, source: str = "manual", note: str = "") -> Submission:
        sub = self._get(submission_id)
        before = sub.status
        validate_submission_transition(before, status)
        if before == status:
            return sub
        entry = SubmissionStatusEntry(status=status, source=source, note=note)
        history = [*sub.status_history, entry]
        updated = self.store.update_submission(
            submission_id,
            status=status,
            status_history=history,
            last_update=entry.at,
            revision_round=sub.revision_round + int(status in {"major_revision", "minor_revision"} and before not in {"major_revision", "minor_revision"}),
        )
        assert updated is not None
        self.trigger_engine.record_state_change(
            source="submission",
            ref=submission_id,
            before=before,
            after=status,
            payload={"action": "submission_status_changed",
                     "title": updated.title,
                     "journal_name": updated.journal_name,
                     "manuscript_id": updated.manuscript_id,
                     "project_id": updated.project_id,
                     "note": note},
        )
        NotificationService(self.store).notify(
            type="submission",
            title=f"投稿状态更新：{updated.title or updated.manuscript_id}",
            body=f"{before} -> {status}",
            ref=submission_id,
            dedup_key=f"submission:{submission_id}:{status}:{entry.at}",
        )
        return updated

    def apply_email(self, text: str, subject: str = "",
                    *, source: str = "email", project_id: str | None = None) -> Submission:
        parsed = SubmissionEmailParser().parse(text, subject=subject)
        return self.apply_parsed_email(parsed, source=source, project_id=project_id)

    def apply_parsed_email(self, parsed: ParsedSubmissionEmail,
                           *, source: str = "email", project_id: str | None = None) -> Submission:
        if parsed.status is None:
            raise ValueError("email did not contain a recognizable submission status")
        if not parsed.manuscript_id.strip():
            raise ValueError("A manuscript ID is required before applying an email status")
        sub = self._find_by_manuscript(parsed.manuscript_id, project_id=project_id, journal_name=parsed.journal_name)
        if sub is None:
            return self.create(
                title=parsed.title or parsed.manuscript_id,
                journal_name=parsed.journal_name,
                manuscript_id=parsed.manuscript_id,
                status=parsed.status,
                source=source,
                project_id=project_id,
            )
        updates = {}
        if parsed.title and not sub.title:
            updates["title"] = parsed.title
        if parsed.journal_name and not sub.journal_name:
            updates["journal_name"] = parsed.journal_name
        if updates:
            sub = self.store.update_submission(sub.submission_id, **updates) or sub
        return self.transition(sub.submission_id, parsed.status,
                               source=source, note=parsed.note)

    def _get(self, submission_id: str) -> Submission:
        for sub in self.store.list_submissions():
            if sub.submission_id == submission_id:
                return sub
        raise KeyError(submission_id)

    def _find_by_manuscript(self, manuscript_id: str, *, project_id=None, journal_name="") -> Submission | None:
        if not manuscript_id:
            return None
        target = manuscript_id.strip().lower()
        matches = [sub for sub in self.store.list_submissions()
                   if sub.manuscript_id.strip().lower() == target
                   and (project_id is None or sub.project_id == project_id)
                   and (not journal_name or not sub.journal_name or sub.journal_name.casefold() == journal_name.casefold())]
        if len(matches) > 1:
            raise ValueError("Ambiguous manuscript ID; identify its journal and research space")
        return matches[0] if matches else None
