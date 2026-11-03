"""Decision Loop: observe -> decide -> act -> observe.

The loop owns execution and safety (budget, kernel gates, trace). The
agent only ever chooses the next AgentAction. Workspace ops (draft,
evidence) are loop-native; external capabilities go through ToolExecutor.
"""
from __future__ import annotations
from paperagent.quality.policies import role_run

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
import uuid

from paperagent.harness.context_assembler import (assemble_observation,
                                                  compact_result)
from paperagent.kernel.application_completeness import (
    check_application_completeness,
    should_check_application,
)
from paperagent.kernel.grant_writing_quality import check_grant_writing_quality
from paperagent.harness.tool_executor import BudgetExceeded, ToolExecutor
from paperagent.kernel.citation_integrity import build_quality_report
from paperagent.schemas.actions import (AgentAction, AskUserAction,
                                        AppendDraftAction,
                                        ChatReplyAction, FinishAction,
                                        ImportFileAction, ImportFolderAction,
                                        ReadDraftAction, ReadPdfAction,
                                        ReadContextAction, UpdateTaskAction, RecordProjectAction, RecordManuscriptAction,
                                        SavePaperReviewAction, ReviewIssueAction,
                                        ReplaceDraftSectionAction,
                                        SaveEvidenceAction,
                                        SaveReferenceAction, SearchAction,
                                        ToolCallAction, UpdateDraftPlanAction,
                                        WriteDraftAction)
from paperagent.schemas.artifacts import QualityReport, ToolCall
from paperagent.schemas.evidence import Evidence
from paperagent.schemas.references import ReferenceCandidate, make_ref_id
from paperagent.tools.importers import run_file_import
from paperagent.workspace.export import (
    export_draft_docx,
    export_draft_docx_with_template,
)
from paperagent.workspace.workspace import Workspace

MAX_CONSECUTIVE_ERRORS = 3
MAX_GRANT_WRITE_CHARS = 12000
MAX_GRANT_SECTION_CHARS = 4500


@dataclass
class RunOutcome:
    status: str  # finished | budget_exhausted | too_many_errors | error
    iterations: int
    summary: str = ""
    draft: Optional[str] = None
    quality_report: Optional[QualityReport] = None
    steps: list[dict] = field(default_factory=list)


class DecisionLoop:
    def __init__(self, ws: Workspace, executor: ToolExecutor, agent,
                 ask_user_fn: Optional[Callable[[str], str]] = None,
                 log: Callable[[str], None] = print,
                 on_step: Optional[Callable[[dict], None]] = None,
                 cancel_event: Optional[threading.Event] = None,
                 run_id: Optional[str] = None,
                 store=None,
                 permission_gate=None,
                 task_id: str | None = None,
                 workflow_id: str | None = None,
                 resume: bool = False,
                 workflow_mode: str | None = None,
                 workflow_options: dict | None = None, role=None):
        from paperagent.quality.policies import effective_role
        self.role = effective_role(role or getattr(executor, "role", None))
        executor.role = self.role
        if self.role == 'proactive':
            if task_id:
                raise ValueError('Proactive execution cannot resume an author task')
            import copy
            ws = copy.copy(ws)
            internal = ws.dir / '.proactive' / (run_id or uuid.uuid4().hex)
            ws.run_state_path = internal / 'run_state.json'
            ws.project_memory_path = internal / 'project_memory.json'
        self.ws = ws
        self.executor = executor
        self.agent = agent
        self.ask_user_fn = ask_user_fn or (lambda q: "[non-interactive mode: question logged]")
        self.log = log
        self.on_step = on_step or (lambda step: None)
        self.cancel_event = cancel_event
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.task_id = task_id
        self.workflow_id = workflow_id
        self.resume = resume
        self.workflow_mode = workflow_mode
        self.workflow_options = workflow_options
        from paperagent.tasks.session import SessionManager
        from paperagent.conversation import ConversationManager
        self.sessions = SessionManager(ws) if task_id else ConversationManager(ws)
        if self.role == "proactive":
            self.sessions.dir = internal / "conversations"
        self.checkpoint_id = task_id or "main"
        self.candidates: dict[str, ReferenceCandidate] = {}
        self.candidate_queries: dict[str, str] = {}
        self.last_draft: Optional[str] = None
        self.last_report: Optional[QualityReport] = None
        self.store = store  # DomainStore: feeds prospective memory into context
        from paperagent.quality.policies import PermissionGate
        self.gate = permission_gate or PermissionGate()

    # ---------------------------------------------------------------- run

    @role_run
    def run(self, goal: str) -> RunOutcome:
        if self.task_id:
            self.sessions.get(self.task_id)  # reject a missing resume target
            session = self.sessions.begin(goal, self.run_id, task_id=self.task_id,
                                          workflow_id=self.workflow_id)
        else:
            session = self.sessions.begin(goal, self.run_id, resume=self.resume,
                                          workflow_id=self.workflow_id, workflow_mode=self.workflow_mode,
                                          workflow_options=self.workflow_options)
        if session.target:
            self.last_draft = session.target["filename"]
        steps: list[dict] = []
        consecutive_errors = 0
        last_action: Optional[str] = None
        last_compact: Optional[dict] = None
        last_error: str = ""
        outcome = RunOutcome(status="budget_exhausted", iterations=0)
        self.ws.update_run_state(
            run_id=self.run_id,
            goal=goal,
            status="running",
            phase="deciding",
            iteration=0,
            last_error="",
            recovery=None,
            task_id=self.task_id,
            conversation_id=None if self.task_id else "main",
            workflow=None,
            draft_plan=session.draft_plan,
            draft=self.last_draft,
            summary="",
            exports=[],
        )

        for i in range(1, self.executor.policy.max_iterations + 1):
            if self.cancel_event is not None and self.cancel_event.is_set():
                outcome.status = "cancelled"
                self.log("[loop] cancelled by request")
                self.ws.update_run_state(status="cancelled", phase="cancelled",
                                         iteration=i)
                break
            outcome.iterations = i
            self.ws.update_run_state(status="running", phase="deciding",
                                     iteration=i, last_action=last_action,
                                     last_result=last_compact)
            try:
                obs = assemble_observation(
                    self.ws, goal, i,
                    iterations_left=self.executor.policy.max_iterations - i,
                    tool_calls_left=self.executor.budget_remaining,
                    last_action=last_action, last_result_compact=last_compact,
                    notes=[], store=self.store)
            except ValueError as e:
                outcome.status = "error"
                outcome.summary = str(e)
                break
            if obs.get("workflow"):
                self.ws.update_run_state(workflow=obs["workflow"])
            try:
                action = self.agent.decide(obs)
            except Exception as e:
                consecutive_errors += 1
                last_error = str(e)
                self.log(f"[loop] agent error ({consecutive_errors}): {e}")
                step = {"iteration": i, "error": str(e)}
                steps.append(step)
                self._emit_step(step)
                recovery = self._recovery_advice(str(e), phase="decide")
                last_compact = {"status": "error",
                                "errors": [f"invalid action: {e}"],
                                "recovery": recovery}
                self.ws.update_run_state(
                    status="running",
                    phase="recovering",
                    iteration=i,
                    last_error=str(e),
                    recovery=recovery,
                    consecutive_errors=consecutive_errors,
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    outcome.status = "too_many_errors"
                    outcome.summary = self._failure_summary(last_error, recovery)
                    self.ws.update_run_state(status=outcome.status,
                                             phase="failed",
                                             last_error=last_error,
                                             recovery=recovery)
                    break
                continue

            if self.cancel_event is not None and self.cancel_event.is_set():
                outcome.status = "cancelled"
                self.log("[loop] cancelled by request")
                self.ws.update_run_state(status="cancelled", phase="cancelled",
                                         iteration=i)
                break

            try:
                self.ws.update_run_state(
                    status="running",
                    phase="acting",
                    iteration=i,
                    current_action=action.model_dump(exclude_none=True),
                )
                result = self._execute(action, goal)
            except BudgetExceeded as e:
                self.log(f"[loop] {e}")
                outcome.status = "budget_exhausted"
                steps.append({"iteration": i, "action": action.action,
                              "note": str(e)})
                self.ws.update_run_state(
                    status=outcome.status,
                    phase="failed",
                    last_error=str(e),
                    recovery=self._recovery_advice(str(e), phase="budget"),
                )
                break

            except Exception as e:
                result = {"status": "error", "errors": [f"{type(e).__name__}: {e}"],
                          "recovery": self._recovery_advice(str(e), phase="act")}

            step = {"iteration": i,
                    "action": action.model_dump(exclude_none=True),
                    "result": result}
            steps.append(step)
            self._emit_step(step)
            last_action = action.action
            last_compact = result

            if result.get("done"):
                outcome.status = "finished"
                outcome.summary = result.get("chat_reply") or (
                    action.summary if isinstance(action, FinishAction)
                    else "")
                outcome.draft = self.last_draft
                outcome.quality_report = self.last_report
                break
            if result.get("status") == "error":
                last_error = "; ".join(str(e) for e in result.get("errors", []))
                recovery = result.get("recovery") or self._recovery_advice(
                    last_error, phase="act")
                self.ws.update_run_state(
                    status="running",
                    phase="recovering",
                    iteration=i,
                    last_error=last_error,
                    recovery=recovery,
                    consecutive_errors=consecutive_errors + 1,
                )
                if result.get("fatal"):
                    outcome.status = "too_many_errors"
                    outcome.summary = self._failure_summary(last_error, recovery)
                    break
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    outcome.status = "too_many_errors"
                    outcome.summary = self._failure_summary(last_error, recovery)
                    break
            else:
                consecutive_errors = 0
        else:
            outcome.status = "budget_exhausted"

        outcome.draft = outcome.draft or self.last_draft
        outcome.quality_report = outcome.quality_report or self.last_report
        outcome.steps = steps
        trace = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "conversation_id": None if self.task_id else "main",
            "goal": goal,
            "status": outcome.status,
            "iterations": outcome.iterations,
            "steps": steps,
            "quality_report": (outcome.quality_report.model_dump()
                               if outcome.quality_report else None),
        }
        trace_path = self.ws.write_run_trace(trace)
        final_phase = "completed" if outcome.status == "finished" else (
            "cancelled" if outcome.status == "cancelled" else "failed")
        self.ws.update_run_state(
            status=outcome.status,
            phase=final_phase,
            iterations=outcome.iterations,
            summary=outcome.summary,
            draft=outcome.draft,
            trace_path=str(trace_path),
        )
        self.log(f"[loop] trace -> {trace_path}")
        self.sessions.checkpoint(self.checkpoint_id, status=outcome.status,
                                 summary=outcome.summary)
        return outcome

    def _emit_step(self, step: dict):
        action = step.get("action") or {}
        changed = isinstance(action, dict) and action.get("action") in {
            "write_draft", "append_draft", "replace_draft_section", "import_file", "import_folder"}
        self.sessions.checkpoint(self.checkpoint_id, step={**step, "run_id": self.run_id},
                                 draft=self.last_draft if changed else None)
        self.on_step(step)

    # ----------------------------------------------------------- execution

    def _execute(self, action: AgentAction, goal: str) -> dict:
        if hasattr(self.agent, "check_action"):
            self.agent.check_action(action.model_dump(exclude_none=True))
        decision = self.gate.check_loop_action(action.action, role=self.role)
        if decision != "allow":
            return {"status": "error",
                    "fatal": True,
                    "errors": [f"permission gate: {action.action} -> {decision}"]}
        if isinstance(action, SearchAction):
            return self._search(action)
        if isinstance(action, ReadPdfAction):
            return self._read_pdf(action)
        if isinstance(action, ImportFileAction):
            return self._import_file(action)
        if isinstance(action, ImportFolderAction):
            return self._import_folder(action)
        if isinstance(action, ToolCallAction):
            return self._tool_call(action)
        if isinstance(action, SaveReferenceAction):
            return self._save_reference(action)
        if isinstance(action, SaveEvidenceAction):
            return self._save_evidence(action)
        if isinstance(action, WriteDraftAction):
            return self._write_draft(action)
        if isinstance(action, AppendDraftAction):
            return self._append_draft(action)
        if isinstance(action, ReadDraftAction):
            return self._read_draft(action)
        if isinstance(action, ReadContextAction):
            return self._read_context(action)
        if isinstance(action, UpdateTaskAction):
            self.sessions.update(self.checkpoint_id, plan=action.plan,
                                 open_questions=action.open_questions, decisions=action.decisions,
                                 workflow_options=action.workflow_options,
                                 workflow_options_actor="agent")
            return {"status": "ok", "task_id": self.task_id,
                    "conversation_id": None if self.task_id else "main"}
        if isinstance(action, RecordProjectAction):
            from paperagent.domain.project_dossier import ProjectDossier, ProjectRecordInput
            body = ProjectRecordInput.model_validate(action.model_dump(exclude={"action"}))
            row = ProjectDossier(self.ws).record(body, actor="agent", task_id=self.task_id)
            return {"status": "ok", "record": {k: row[k] for k in ("record_id", "revision", "status")},
                    "hint": "Candidate only. Read project_dossier for details. User adoption is required; do not claim verified progress."}
        if isinstance(action, RecordManuscriptAction):
            from paperagent.domain.manuscript import ManuscriptContext, ManuscriptRecordInput
            body = ManuscriptRecordInput.model_validate(action.model_dump(exclude={"action"}))
            row = ManuscriptContext(self.ws).record(body, actor="agent", run_id=self.run_id)
            return {"status": "ok", "record_id": row["record_id"], "revision": row["revision"],
                    "confirmation": "proposed", "hint": "Proposal only; never overwrite confirmed terms or claim source verification."}
        if isinstance(action, SavePaperReviewAction):
            from paperagent.domain.paper_review import PaperReviewInput, PaperReviews
            row = PaperReviews(self.ws).create(PaperReviewInput.model_validate(action.model_dump(exclude={"action"})), actor="agent", run_id=self.run_id)
            return {"status": "ok", "report_id": row["report_id"], "report_sha256": row["report_sha256"],
                    "hint": "Review assessment saved; manuscript unchanged. Findings are not confirmed scientific facts."}
        if isinstance(action, ReviewIssueAction):
            from paperagent.domain.paper_review import PaperReviews, ReviewFollowupInput
            row = PaperReviews(self.ws).followup(action.report_id, action.issue_id,
                ReviewFollowupInput.model_validate(action.model_dump(exclude={"action", "report_id", "issue_id"})),
                actor="agent", run_id=self.run_id)
            return {"status": "ok", "followup_id": row["followup_id"], "revision": row["revision"],
                    "adoption": "proposed", "hint": "Follow-up proposal only. User adoption is separate from evidence verification."}
        if isinstance(action, ReplaceDraftSectionAction):
            return self._replace_draft_section(action)
        if isinstance(action, UpdateDraftPlanAction):
            return self._update_draft_plan(action)
        if isinstance(action, AskUserAction):
            return self._ask_user(action)
        if isinstance(action, ChatReplyAction):
            return self._chat_reply(action)
        if isinstance(action, FinishAction):
            return self._finish(action, goal)
        return {"status": "error", "errors": [f"unhandled action {action!r}"]}

    def _search(self, action: SearchAction) -> dict:
        cap = self.executor.policy.max_search_results
        search_input = {"query": action.query,
                        "max_results": min(action.max_results, cap)}
        if action.provider:
            search_input["provider"] = action.provider
        call = ToolCall(tool_id="literature_search", input=search_input)
        result = self.executor.execute(call)
        compact = compact_result(result)
        if result.status == "ok":
            for c in (result.data.get("candidates") or []):
                cand = ReferenceCandidate.model_validate(c)
                self.candidates[cand.ref_id] = cand
                self.candidate_queries[cand.ref_id] = action.query
            compact["hint"] = "save_reference the useful ones before citing"
        return compact

    def _read_pdf(self, action: ReadPdfAction) -> dict:
        path = self._resolve_project_read_path(action.path)
        if path and not self.ws.is_path_allowed(path):
            return {
                "status": "error",
                "errors": [f"filesystem permission denied for {path}"],
                "recovery": self._recovery_advice(
                    "filesystem permission denied", phase="permission"),
            }
        if path and path.suffix.lower() in {".md", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            return {
                "status": "ok",
                "path": str(path),
                "total_pages": 1,
                "pages": [{"page": 1, "text": text[:3500],
                           "truncated": len(text) > 3500}],
                "hint": "This is a text draft/material, not a PDF. Use the text directly or save evidence/write a draft.",
            }
        call = ToolCall(tool_id="pdf_reader",
                        input={"path": action.path, "pages": action.pages})
        result = self.executor.execute(call)
        return compact_result(result)

    def _resolve_project_read_path(self, path: str) -> Path | None:
        p = Path(path)
        candidates = [p] if p.is_absolute() else [
            self.ws.dir / path,
            self.ws.drafts_dir / path,
            self.ws.root / path,
        ]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if resolved.exists() and resolved.is_file():
                return resolved
        return None

    def _tool_call(self, action: ToolCallAction) -> dict:
        result = self.executor.execute(ToolCall(tool_id=action.tool_id, input=action.input))
        if result.status == "error":
            return {"status": "error", "errors": result.errors}
        data = result.data or {}
        for c in data.get("candidates") or []:
            cand = ReferenceCandidate.model_validate(c)
            self.candidates[cand.ref_id] = cand
            self.candidate_queries[cand.ref_id] = str(action.input.get("query") or action.input.get("title") or "")
        return {
            "status": "ok",
            "tool_id": result.tool_id,
            "data": self._compact_tool_data(data),
            "provenance": result.provenance,
        }

    @staticmethod
    def _compact_tool_data(data: dict) -> dict:
        compact = {}
        for key, value in data.items():
            if isinstance(value, str):
                compact[key] = value[:1800]
            elif isinstance(value, list):
                compact[key] = value[:10]
            elif isinstance(value, dict):
                compact[key] = {k: (v[:800] if isinstance(v, str) else v)
                                for k, v in list(value.items())[:20]}
            else:
                compact[key] = value
        return compact

    def _import_file(self, action: ImportFileAction) -> dict:
        original = self.ws.resolve_path(action.path)
        self.ws.authorize_root(original)
        self.ws.upsert_material(str(original), "processing")
        result = run_file_import(
            {"path": action.path, "conversion_dir": str(self.ws.dir / "converted")},
            resolve=self.ws.resolve_path,
            timeout=self.executor.policy.tool_timeout_s,
        )
        if result.get("error"):
            suffix = original.suffix.lower()
            self.ws.upsert_material(
                str(original),
                "pending_conversion" if suffix in {".doc", ".wps"} else "error",
                error=result["error"],
                supported=suffix in {".doc", ".wps"},
            )
            return {"status": "error", "errors": [result["error"]]}
        committed = self._commit_import_result(result)
        self.ws.upsert_material(
            result.get("path", str(original)),
            "converted" if result.get("converted_path") else "imported",
            kind=result.get("kind"),
            content_type=result.get("content_type"),
            size=result.get("size"),
            sha256=result.get("sha256"),
            converted_path=result.get("converted_path"),
            references=len(committed.get("references", [])),
            evidence=len(committed.get("evidence", [])),
            drafts=list(committed.get("drafts", [])),
        )
        return {"status": "ok", **committed}

    def _import_folder(self, action: ImportFolderAction) -> dict:
        from paperagent.connectors.local_folder import (
            SUPPORTED_SUFFIXES,
            split_supported_files,
            summarize_unsupported_files,
        )
        try:
            self.ws.authorize_root(self.ws.resolve_path(action.folder))
            root, supported, unsupported_paths = split_supported_files(
                self.ws.resolve_path(action.folder),
                recursive=action.recursive,
            )
        except NotADirectoryError as e:
            return {
                "status": "error",
                "errors": [f"not a folder: {e}"],
                "recovery": self._recovery_advice("not a folder", phase="import_folder"),
            }
        imported = []
        errors = []
        for path in supported:
            result = run_file_import(
                {"path": str(path), "conversion_dir": str(self.ws.dir / "converted")},
                resolve=lambda p: Path(p).resolve(),
                timeout=self.executor.policy.tool_timeout_s,
            )
            if result.get("error"):
                suffix = path.suffix.lower()
                self.ws.upsert_material(
                    str(path),
                    "pending_conversion" if suffix in {".doc", ".wps"} else "error",
                    error=result["error"],
                    supported=suffix in {".doc", ".wps"},
                )
                errors.append({"path": str(path), "error": result["error"]})
                continue
            committed = self._commit_import_result(result)
            self.ws.upsert_material(
                result.get("path", str(path)),
                "converted" if result.get("converted_path") else "imported",
                kind=result.get("kind"),
                content_type=result.get("content_type"),
                size=result.get("size"),
                sha256=result.get("sha256"),
                converted_path=result.get("converted_path"),
                references=len(committed.get("references", [])),
                evidence=len(committed.get("evidence", [])),
                drafts=list(committed.get("drafts", [])),
            )
            imported.append(committed)
        unsupported = summarize_unsupported_files(unsupported_paths)
        for item in unsupported:
            suffix = item.get("suffix")
            self.ws.upsert_material(
                item["path"],
                "unsupported",
                error=f"unsupported file type: {suffix}",
                supported=False,
            )
        if imported:
            status = "ok"
            done = False
            chat_reply = ""
        elif errors:
            status = "error"
            done = False
            chat_reply = ""
        elif unsupported_paths:
            status = "ok"
            done = True
            suffixes = sorted({p.suffix.lower() or "(无后缀)" for p in unsupported_paths})
            chat_reply = (
                f"我找到了这个目录，但里面的文件类型当前还不能直接解析：{', '.join(suffixes)}。"
                "请先把需要读取的材料另存为 PDF、DOCX、Markdown、TXT、BibTeX 或 RIS 后再导入。"
            )
        else:
            status = "ok"
            done = True
            chat_reply = "我找到了这个目录，但里面没有可导入的文件。"
        out = {
            "status": status,
            "folder": str(root),
            "done": done,
            "chat_reply": chat_reply,
            "imported_count": len(imported),
            "errors": errors,
            "imported": imported[:20],
            "unsupported_count": len(unsupported_paths),
            "unsupported": unsupported,
            "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        }
        if errors:
            out["recovery"] = self._recovery_advice(str(errors[0].get("error", "")),
                                                    phase="import_folder")
        return out

    def _commit_import_result(self, result: dict) -> dict:
        imported_refs = []
        imported_evidence = []
        imported_drafts = []
        if result.get("references"):
            for item in result["references"]:
                ref = self.ws.save_reference(
                    ReferenceCandidate.model_validate(item),
                    source_query=f"import:{result['filename']}",
                )
                imported_refs.append({"ref_id": ref.ref_id, "title": ref.title})
        else:
            title = Path(result["filename"]).stem.replace("_", " ").replace("-", " ")
            first = title.split()[0] if title.split() else "file"
            ref = self.ws.save_reference(ReferenceCandidate(
                ref_id=make_ref_id("local", None, first),
                title=title,
                authors=[],
                year=None,
                venue="Local import",
                url=Path(result["path"]).as_uri(),
                source="local_file",
            ), source_query=f"import:{result['filename']}")
            imported_refs.append({"ref_id": ref.ref_id, "title": ref.title})
            text, page = self._first_import_text(result)
            if text:
                quote = " ".join(text.split())[:600]
                ev = self.ws.save_evidence(Evidence(
                    evidence_id=f"ev_{result.get('sha256', uuid.uuid4().hex)[:12]}",
                    ref_id=ref.ref_id,
                    quote=quote,
                    page=page,
                    section="imported excerpt",
                    supports=f"Imported material from {result['filename']}",
                    source_path=result["path"],
                ))
                imported_evidence.append({"evidence_id": ev.evidence_id, "ref_id": ev.ref_id})
            if result.get("kind") in {"text", "docx"} and text:
                filename = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", Path(result["filename"]).stem).strip("-") or "imported"
                draft_path = self.ws.write_draft(f"imported-{filename}.md", text)
                imported_drafts.append(draft_path.name)
                self.last_draft = draft_path.name
        return {
            "path": result.get("path"),
            "filename": result.get("filename"),
            "kind": result.get("kind"),
            "references": imported_refs,
            "evidence": imported_evidence,
            "drafts": imported_drafts,
        }

    @staticmethod
    def _first_import_text(result: dict) -> tuple[str, Optional[int]]:
        if result.get("pages"):
            page = result["pages"][0]
            return str(page.get("text") or "").strip(), page.get("page")
        return str(result.get("text") or "").strip(), None

    def _save_reference(self, action: SaveReferenceAction) -> dict:
        if action.ref_id in self.candidates:
            cand = self.candidates[action.ref_id]
            self.ws.save_reference(cand, source_query=self.candidate_queries.get(action.ref_id))
            return {"status": "ok", "saved": action.ref_id}
        if self.ws.get_reference(action.ref_id):
            return {"status": "ok", "saved": action.ref_id, "note": "already in DB"}
        return {"status": "error",
                "errors": [f"ref_id '{action.ref_id}' unknown - search first, "
                           f"then save one of the returned candidates"]}

    def _save_evidence(self, action: SaveEvidenceAction) -> dict:
        if not self.ws.get_reference(action.ref_id):
            return {"status": "error",
                    "errors": [f"ref_id '{action.ref_id}' not saved - "
                               f"save_reference before save_evidence"]}
        source_sha256 = None
        if action.source_path:
            import hashlib
            path = self.ws.resolve_path(action.source_path)
            if not self.ws.is_path_allowed(path):
                return {"status": "error", "errors": ["Evidence source is outside allowed roots"]}
            if not path.is_file():
                return {"status": "error", "errors": ["Evidence source file is unavailable"]}
            source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        ev = Evidence(
            evidence_id=f"ev{len(self.ws.list_evidence()) + 1:03d}",
            ref_id=action.ref_id,
            quote=action.quote,
            page=action.page,
            section=action.section,
            supports=action.supports,
            source_path=action.source_path,
            source_sha256=source_sha256,
            **action.model_dump(include={"source_coverage", "relation", "themes", "method", "population", "finding", "limitations"}),
        )
        self.ws.save_evidence(ev)
        return {"status": "ok", "evidence_id": ev.evidence_id,
                "note": "quote will be verified verbatim against the source"}

    def _write_draft(self, action: WriteDraftAction) -> dict:
        workflow = self.ws.read_run_state().get("workflow")
        too_large = self._long_grant_chunk_error(
            action.content, workflow, phase="write_draft",
            limit=MAX_GRANT_WRITE_CHARS,
        )
        if too_large:
            return too_large
        self.ws.write_draft(action.filename, action.content)
        self.last_draft = action.filename
        self.last_report = build_quality_report(
            action.content, self.ws.list_references(), self.ws.list_evidence(),
            resolve=self.ws.resolve_path)
        if workflow and should_check_application(action.filename, action.content, workflow):
            self.last_report.application_completeness = check_application_completeness(
                action.content, action.filename, workflow)
        self._attach_grant_writing_quality(action.content, workflow)
        report = self.last_report.citation_integrity
        out = {"status": "ok", "draft": action.filename,
               "kernel": {
                   "errors": report.errors,
                   "warnings": report.warnings,
               }}
        app_report = self.last_report.application_completeness
        if app_report is not None:
            out["application_kernel"] = {
                "errors": app_report.errors,
                "warnings": app_report.warnings,
                "stats": app_report.stats,
            }
        self._add_grant_quality_to_result(out)
        all_errors = list(report.errors)
        if app_report is not None:
            if self._application_kernel_should_block(workflow, app_report):
                all_errors.extend(app_report.errors)
            elif app_report.errors:
                out["application_kernel"]["warnings"] = (
                    out["application_kernel"]["warnings"]
                    + [f"non-final draft completeness issue: {e}" for e in app_report.errors]
                )
                out["recovery"] = {
                    "kind": "application_completeness_warning",
                    "phase": "write_draft",
                    "retryable": True,
                    "next_step": "This is a non-final grant-writing stage. Continue revising or append missing sections before final delivery.",
                }
        if all_errors:
            out["status"] = "error"
            out["errors"] = [f"kernel rejected draft: {e}" for e in all_errors]
            if app_report is not None and app_report.errors:
                out["recovery"] = {
                    "kind": "application_completeness",
                    "phase": "write_draft",
                    "retryable": True,
                    "next_step": "Continue drafting section by section. Expand missing or short sections until the application completeness kernel passes.",
                }
        return out

    def _append_draft(self, action: AppendDraftAction) -> dict:
        workflow = self.ws.read_run_state().get("workflow")
        too_large = self._long_grant_chunk_error(
            action.content, workflow, phase="append_draft",
            limit=MAX_GRANT_SECTION_CHARS,
        )
        if too_large:
            return too_large
        path = self.ws.append_draft(action.filename, action.content)
        self.last_draft = path.name
        text = self.ws.read_draft(path.name)
        self.last_report = build_quality_report(
            text, self.ws.list_references(), self.ws.list_evidence(),
            resolve=self.ws.resolve_path)
        self._attach_grant_writing_quality(text, workflow)
        report = self.last_report.citation_integrity
        out = {
            "status": "ok",
            "draft": path.name,
            "chars": len(text),
            "kernel": {"errors": report.errors, "warnings": report.warnings},
            "hint": "Continue appending remaining sections, then finish when the draft is complete.",
        }
        self._add_grant_quality_to_result(out)
        if report.errors:
            out["status"] = "error"
            out["errors"] = [f"kernel rejected draft: {e}" for e in report.errors]
        return out

    def _read_draft(self, action: ReadDraftAction) -> dict:
        try:
            filename = action.filename if action.filename.endswith(".md") else action.filename + ".md"
            snapshot = self.ws.snapshot_draft(filename)
            text = Path(snapshot["snapshot_path"]).read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"status": "error",
                    "errors": [f"draft not found: {action.filename}"]}
        headings = re.findall(r"^\s*#{1,6}\s+(.+?)\s*$", text, flags=re.M)
        if action.heading:
            match = re.search(rf"^\s*#{{1,6}}\s+{re.escape(action.heading)}\s*$", text, re.M)
            if not match:
                return {"status": "error", "errors": ["heading not found"], "headings": headings}
            remaining = text[match.end():]
            end = re.search(r"^\s*#{1,6}\s+", remaining, re.M)
            text = text[match.start():match.end() + end.start()] if end else text[match.start():]
        return {
            "status": "ok",
            "draft": action.filename,
            "version": snapshot["version"],
            "chars": len(text),
            "headings": headings[:40],
            "content": text[action.offset:action.offset + action.max_chars],
            "offset": action.offset,
            "next_offset": action.offset + action.max_chars if len(text) > action.offset + action.max_chars else None,
            "truncated": len(text) > action.offset + action.max_chars,
            "hint": "Use replace_draft_section for targeted revisions, or append_draft for the next missing section.",
        }

    def _read_context(self, action: ReadContextAction) -> dict:
        from paperagent.skills.loader import SkillLoader
        if action.kind == "skill_resource":
            try:
                return SkillLoader(self.ws.root).read_resource(
                    action.key, action.resource_id, self.ws.project_id, offset=action.offset,
                    max_chars=action.max_chars, expected_sha256=action.expected_sha256)
            except (ValueError, OSError) as exc:
                return {"status": "error", "errors": [str(exc)]}
        if action.kind == "project_acceptance":
            from paperagent.domain.project_acceptance import objective_matrix
            contract = (self.ws.read_run_state().get("workflow") or {}).get("project_contract", {})
            data = objective_matrix(self.ws, action.key or contract.get("stage", "midterm"), contract.get("report_date"))
            source = "derived:project_dossier:objective_matrix"
        elif action.kind == "project_claims":
            from paperagent.domain.project_claims import ProjectClaims
            filename = action.key or self.last_draft
            if not filename:
                raise ValueError("Specify a draft filename for claim audit")
            data = ProjectClaims(self.ws).audit(self.ws.read_draft(filename), filename)
            source = "derived:project_claim_reviews:" + filename
        elif action.kind in {"project_dossier", "project_source"}:
            from paperagent.domain.project_dossier import ProjectDossier
            dossier = ProjectDossier(self.ws)
            source = str(dossier.path)
            if action.kind == "project_source":
                data = dossier.source_text(action.key)
                source += "#" + action.key
            elif action.key:
                data = [r for r in dossier.read()["records"] if r["record_id"] == action.key]
                if not data:
                    raise ValueError("Project record not found")
            else:
                data = {"records": dossier.active_records(), "bindings": dossier.bindings(),
                        "revision": dossier.read()["revision"]}
        elif action.kind in {"paper_reviews", "paper_review_source"}:
            from paperagent.domain.paper_review import PaperReviews
            service = PaperReviews(self.ws)
            try:
                if action.kind == "paper_review_source":
                    data = service.source_text(action.key, action.resource_id)
                else:
                    data = service.view()
                    if action.key:
                        report, _ = service._find(data, action.key)
                        data = {"report": report, "followups": [f for f in data["followups"] if f["report_id"] == action.key]}
                source = str(service.path) + ("#" + action.key if action.key else "")
            except (ValueError, OSError) as exc:
                return {"status": "error", "errors": [str(exc)]}
        elif action.kind == "manuscript":
            from paperagent.domain.manuscript import ManuscriptContext
            service = ManuscriptContext(self.ws)
            view = service.view()
            if action.key:
                history = [r for r in view["records"] if r["record_id"] == action.key]
                if not history:
                    return {"status": "error", "errors": ["Manuscript record not found in this workspace"]}
                data = {"current": next((r for r in view["active_records"] if r["record_id"] == action.key), history[-1]),
                        "history": history}
            else:
                data = view
            source = str(service.path)
        elif action.kind == "workflow_contract":
            from paperagent.workflows.catalog import workflow_spec, workflow_skill_recommendations
            from paperagent.workflows.design import contract
            spec = workflow_spec(action.key)
            if not spec or not spec.get("code"):
                return {"status": "error", "errors": ["Unknown workflow contract"]}
            data = {**contract(spec), "skill_recommendations": workflow_skill_recommendations(spec["id"])}
            from paperagent.workflows.registry import select_workflow
            data["current_delivery_context"] = select_workflow(self.ws, self.ws.read_run_state().get("goal", ""), spec["id"],
                                                               self.ws.read_run_state().get("workflow_options"))
            source = "workflow_contract:" + spec["id"]
        elif action.kind == "skill_catalog":
            loader = SkillLoader(self.ws.root)
            skills = loader.select(goal=action.key, project_id=self.ws.project_id, limit=100) if action.key else loader.load_all(self.ws.project_id)
            data = [s.metadata_view() for s in skills]
            source = "skill_catalog:" + self.ws.project_id
        elif action.kind == "skill":
            try:
                skill = SkillLoader(self.ws.root).resolve(action.key, self.ws.project_id)
            except ValueError as exc:
                return {"status": "error", "errors": [str(exc)]}
            data, source = skill.context_view(), skill.source_path
        elif action.kind == "messages":
            data, source = self.ws.list_messages(), str(self.ws.messages_path)
        elif action.kind == "evidence":
            data = [e.model_dump() for e in self.ws.list_evidence()
                    if not action.key or e.evidence_id == action.key or e.ref_id == action.key]
            source = str(self.ws.evidence_path)
        elif action.kind == "references":
            data = [r.model_dump() for r in self.ws.list_references()
                    if not action.key or r.ref_id == action.key]
            source = str(self.ws.references_path)
        elif action.kind == "materials":
            data, source = self.ws.list_materials(), str(self.ws.materials_path)
        elif action.kind in {"task", "conversation"}:
            source = str(self.sessions.path(self.checkpoint_id))
            data = self.sessions.context(self.checkpoint_id)
            if action.kind == "conversation" and action.key:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", action.key):
                    return {"status":"error", "errors":["Invalid run ID"]}
                path = self.ws.dir / "conversations" / "runs" / f"{action.key}.json"
                if not path.exists():
                    return {"status":"error", "errors":["Conversation run not found"]}
                data, source = json.loads(path.read_text(encoding="utf-8")), str(path)
        else:
            path = self.sessions.dir / f"{self.checkpoint_id}.events.jsonl"
            data = path.read_text(encoding="utf-8") if path.exists() else ""
            if action.key.startswith("event:"):
                try:
                    index = int(action.key.split(":", 1)[1]) - 1
                    lines = data.splitlines()
                    if index < 0 or index >= len(lines):
                        raise ValueError("event index out of range")
                    data = lines[index]
                except ValueError as e:
                    return {"status": "error", "errors": [str(e)]}
            elif action.key == "last" and data:
                # Skip reads themselves so pagination points to a stable source event.
                events = [json.loads(line) for line in data.splitlines() if line.strip()]
                data = next((json.dumps(event, ensure_ascii=False) for event in reversed(events)
                             if not isinstance(event.get("action"), dict)
                             or event["action"].get("action") != "read_context"), "")
            source = str(path)
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        end = action.offset + action.max_chars
        return {"status": "ok", "source": source, "content": text[action.offset:end],
                "offset": action.offset, "next_offset": end if end < len(text) else None,
                "total_chars": len(text), "content_role": "source_material"}

    def _replace_draft_section(self, action: ReplaceDraftSectionAction) -> dict:
        workflow = self.ws.read_run_state().get("workflow")
        too_large = self._long_grant_chunk_error(
            action.content, workflow, phase="replace_draft_section",
            limit=MAX_GRANT_SECTION_CHARS,
        )
        if too_large:
            return too_large
        path = self.ws.replace_draft_section(
            action.filename, action.heading, action.content)
        self.last_draft = path.name
        text = self.ws.read_draft(path.name)
        self.last_report = build_quality_report(
            text, self.ws.list_references(), self.ws.list_evidence(),
            resolve=self.ws.resolve_path)
        self._attach_grant_writing_quality(text, workflow)
        report = self.last_report.citation_integrity
        out = {
            "status": "ok",
            "draft": path.name,
            "heading": action.heading,
            "chars": len(text),
            "kernel": {"errors": report.errors, "warnings": report.warnings},
            "hint": "Continue replacing or appending sections until the draft plan is complete.",
        }
        self._add_grant_quality_to_result(out)
        if report.errors:
            out["status"] = "error"
            out["errors"] = [f"kernel rejected draft: {e}" for e in report.errors]
        return out

    def _update_draft_plan(self, action: UpdateDraftPlanAction) -> dict:
        plan = {
            "filename": action.filename,
            "sections": action.sections,
            "next_section": action.next_section,
            "notes": action.notes,
        }
        self.ws.update_run_state(draft_plan=plan)
        return {
            "status": "ok",
            "draft_plan": plan,
            "hint": "Follow draft_plan.next_section with append_draft or replace_draft_section.",
        }

    def _ask_user(self, action: AskUserAction) -> dict:
        answer = self.ask_user_fn(action.question)
        if answer is None:
            # Non-interactive surface (web): the question itself is this
            # turn's reply. End the run; the user answers in the chat and
            # their next message starts a fresh run over the same state.
            return {"status": "ok", "done": True,
                    "chat_reply": action.question}
        return {"status": "ok", "question": action.question, "answer": answer}

    def _chat_reply(self, action: ChatReplyAction) -> dict:
        # Terminal: conversation needs no draft, so finish's kernel gate
        # does not apply. The reply rides the step result and run summary.
        return {"status": "ok", "done": True, "chat_reply": action.text}

    def _finish(self, action: FinishAction, goal: str) -> dict:
        if not self.last_draft:
            return {"status": "error",
                    "errors": ["cannot finish: no draft written yet"]}
        from paperagent.kernel.external_source_check import verify_workspace
        verify_workspace(self.ws)
        text = self.ws.read_draft(self.last_draft)
        workflow = self.ws.read_run_state().get("workflow")
        self.last_report = build_quality_report(
            text, self.ws.list_references(), self.ws.list_evidence(),
            resolve=self.ws.resolve_path)
        if workflow and should_check_application(self.last_draft, text, workflow):
            self.last_report.application_completeness = check_application_completeness(
                text, self.last_draft, workflow)
        self._attach_grant_writing_quality(text, workflow)
        report = self.last_report.citation_integrity
        errors = list(report.errors)
        from paperagent.kernel.project_delivery import check_project_delivery
        project_report = check_project_delivery(self.ws, text, workflow, self.last_draft)
        self.last_report.project_delivery = project_report
        if project_report:
            errors.extend(project_report.errors)
        app_report = self.last_report.application_completeness
        if app_report is not None:
            if self._application_kernel_should_block(workflow, app_report):
                errors.extend(app_report.errors)
        short_blockers = self._workflow_short_draft_blockers(workflow, app_report)
        errors.extend(short_blockers)
        case_delivery = None
        if workflow:
            from paperagent.workflows.runtime import WorkflowRuntime
            from paperagent.workflows.catalog import canonical_workflow_id
            runtime = WorkflowRuntime(self.ws)
            cases = [c for c in runtime.list() if c["status"] == "active" and
                     c["workflow_id"] == canonical_workflow_id(workflow["workflow_id"])]
            if workflow.get("case_id"):
                cases = [c for c in cases if c["case_id"] == workflow["case_id"]]
            if len(cases) == 1:
                case = cases[0]
                case = runtime.update(case["case_id"], "output", {
                    "label": case["required_deliverables"][0], "path": str(self.ws.drafts_dir / self.last_draft)}, actor="agent")
                issues = [{"level": "ERROR", "message": str(e)} for e in errors]
                issues += [{"level": "WARNING", "message": str(e)} for e in report.warnings]
                if project_report:
                    issues += [{"level": "WARNING", "message": str(e)} for e in project_report.warnings]
                runtime.update(case["case_id"], "quality", {"issues": issues}, actor="agent")
                case_delivery = {"case_id": case["case_id"], "status": "awaiting_user_review", "auto_completed": False}
        if errors and self.executor.policy.block_finish_on_kernel_errors:
            return {"status": "error",
                    "errors": [f"finish blocked by kernel: {e}" for e in errors],
                    "recovery": {
                        "kind": "project_contract_repair" if project_report and project_report.errors else "draft_expansion_required",
                        "phase": "finish",
                        "retryable": True,
                        "next_step": "Resolve reported issues within the requested scope: read project sources, correct facts or complete applicable sections, then finish again.",
                    },
                    "hint": "Resolve the specific kernel errors before delivery",
                    "project_kernel": project_report.model_dump() if project_report else None}
        grant_report = self.last_report.grant_writing_quality
        review_summary = self._finish_review_summary(
            workflow, report, app_report, grant_report)
        if project_report and project_report.warnings:
            review_summary += "\n项目核查提示：" + "；".join(project_report.warnings[:6])
        delivery = None
        exports = self._auto_export_final(workflow)
        if project_report:
            from paperagent.domain.project_dossier import ProjectDossier
            from paperagent.domain.project_assessment import save_assessment
            exports.extend(save_assessment(self.ws, self.last_draft, workflow))
            delivery = ProjectDossier(self.ws).delivery(self.last_draft, workflow, project_report, exports)
            self.ws.update_run_state(exports=exports)
        chat_reply = action.summary
        if exports:
            export_lines = [
                f"已生成文档（请按质量提示核查）：{item['path']}"
                for item in exports
                if item.get("status") == "ok" and item.get("path")
            ]
            if export_lines:
                chat_reply = chat_reply + "\n\n" + "\n".join(export_lines)
        if review_summary:
            chat_reply = f"{action.summary}\n\n{review_summary}"
            if exports:
                export_lines = [
                    f"已生成文档（请按质量提示核查）：{item['path']}"
                    for item in exports
                    if item.get("status") == "ok" and item.get("path")
                ]
                if export_lines:
                    chat_reply = chat_reply + "\n" + "\n".join(export_lines)
        failed_exports = [item.get("error", "unknown") for item in exports if item.get("status") == "error"]
        if failed_exports:
            chat_reply += "\n文档导出失败，Markdown 草稿已保留：" + "；".join(failed_exports)
        return {"status": "ok", "done": True,
                "chat_reply": chat_reply,
                "exports": exports,
                "project_kernel": project_report.model_dump() if project_report else None,
                "project_delivery": delivery,
                "workflow_case": case_delivery,
                "kernel": {"errors": report.errors, "warnings": report.warnings},
                "application_kernel": (
                    {"errors": app_report.errors, "warnings": app_report.warnings,
                     "stats": app_report.stats}
                    if app_report is not None else None),
                "grant_quality_kernel": (
                    {"errors": grant_report.errors, "warnings": grant_report.warnings,
                     "stats": grant_report.stats}
                    if grant_report is not None else None)}

    def _attach_grant_writing_quality(self, text: str, workflow: dict | None) -> None:
        if self.last_report is None or not workflow:
            return
        if (workflow or {}).get("workflow_id") == "grant_application" or should_check_application(
            self.last_draft or "", text, workflow
        ):
            self.last_report.grant_writing_quality = check_grant_writing_quality(
                text, workflow)

    def _add_grant_quality_to_result(self, out: dict) -> None:
        if self.last_report is None or self.last_report.grant_writing_quality is None:
            return
        report = self.last_report.grant_writing_quality
        out["grant_quality_kernel"] = {
            "errors": report.errors,
            "warnings": report.warnings,
            "stats": report.stats,
        }

    @staticmethod
    def _long_grant_chunk_error(content: str, workflow: dict | None,
                                *, phase: str, limit: int) -> dict | None:
        if (workflow or {}).get("workflow_id") != "grant_application":
            return None
        chars = len(content)
        if chars <= limit:
            return None
        return {
            "status": "error",
            "errors": [
                f"{phase} content too long for reliable JSON action: {chars} chars; limit {limit}.",
            ],
            "recovery": {
                "kind": "long_document_chunk_required",
                "phase": phase,
                "retryable": True,
                "next_step": (
                    "Use update_draft_plan, then append_draft or replace_draft_section "
                    "for only the next official section. Keep each chunk under "
                    f"{limit} Chinese characters and continue section by section."
                ),
            },
            "hint": "write only the next template section, not the full application",
        }

    def _auto_export_final(self, workflow: dict | None) -> list[dict]:
        if not self.last_draft or (workflow or {}).get("workflow_id") not in {
            "grant_application", "project_midterm_report", "project_final_report"
        }:
            return []
        exports: list[dict] = []
        # A bound Markdown template supplies headings. Do not substitute an unrelated DOCX.
        template = self._find_application_template() if (
            workflow.get("workflow_id") == "grant_application"
            and not workflow.get("project_contract", {}).get("template_binding_id")) else None
        try:
            if workflow.get("project_contract"):
                # The complete rendered document is always delivered; template field
                # matching can omit unmapped text and is only an additional output.
                path = export_draft_docx(self.ws, self.last_draft)
                exports.append({"status": "ok", "format": "docx", "path": str(path),
                                "template_path": None, "note": "complete document with project provenance"})
            if template:
                path = export_draft_docx_with_template(self.ws, self.last_draft, template)
                exports.append({
                    "status": "ok",
                    "format": "template_docx",
                    "path": str(path),
                    "template_path": str(template),
                })
            elif not exports:
                path = export_draft_docx(self.ws, self.last_draft)
                exports.append({
                    "status": "ok",
                    "format": "docx",
                    "path": str(path),
                    "template_path": None,
                    "note": "no DOCX template detected; exported plain DOCX",
                })
        except Exception as e:
            exports.append({
                "status": "error",
                "format": "docx",
                "error": str(e),
            })
        if (workflow or {}).get("project_contract"):
            # Keep each generated delivery file; later revisions cannot overwrite it.
            from paperagent.domain.project_dossier import ProjectDossier
            version = self.ws.snapshot_draft(self.last_draft)["version"][:12]
            revision = ProjectDossier(self.ws).read()["revision"]
            for item in exports:
                if item.get("status") == "ok":
                    original = Path(item["path"])
                    target = original.with_name(f"{original.stem}-{version}-r{revision}{original.suffix}")
                    try:
                        target.write_bytes(original.read_bytes())
                        item["path"] = str(target)
                    except OSError as exc:
                        item.update(status="error", error=f"Could not preserve delivery version: {exc}")
        self.ws.update_run_state(exports=exports)
        return exports

    def _find_application_template(self) -> Path | None:
        candidates = []
        for material in self.ws.list_materials():
            name = material.get("filename") or ""
            if not any(key in name for key in ("申请书", "申报书", "模板")):
                continue
            path = material.get("converted_path") or material.get("path")
            if not path:
                continue
            p = Path(path)
            if p.suffix.lower() == ".docx" and p.exists():
                candidates.append(p)
        if candidates:
            return candidates[0]
        return None

    @staticmethod
    def _finish_review_summary(workflow: dict | None, citation_report,
                               app_report, grant_report=None) -> str:
        if not workflow and app_report is None and grant_report is None:
            return ""
        lines = []
        if workflow:
            stage = (workflow.get("state") or {}).get("stage")
            if stage:
                lines.append(f"当前阶段：{stage}")
            missing = workflow.get("missing_information") or []
            if missing:
                lines.append("待确认信息：" + "；".join(missing[:3]))
        warnings = []
        if citation_report is not None:
            warnings.extend(citation_report.warnings[:3])
        if app_report is not None:
            warnings.extend(app_report.warnings[:5])
        if grant_report is not None:
            warnings.extend(grant_report.errors[:3])
            warnings.extend(grant_report.warnings[:8])
        if warnings:
            lines.append("质量提示：" + "；".join(warnings[:6]))
        return "\n".join(lines)

    @staticmethod
    def _application_kernel_should_block(workflow: dict | None, app_report) -> bool:
        contract = (workflow or {}).get("project_contract")
        if contract:
            return bool(app_report and app_report.errors and contract["scope"] == "full"
                        and contract["delivery"] == "ready")
        if workflow and workflow.get("execution_policy", {}).get("scope") == "partial":
            return False
        if app_report is None or not app_report.errors:
            return False
        if not workflow:
            return True
        stage = ((workflow.get("state") or {}).get("stage") or "")
        document_mode = ((workflow.get("state") or {}).get("document_mode") or "")
        final_stages = {
            "full_application",
            "argument_sheet",
            "export_delivery",
        }
        if stage in final_stages or document_mode == "argument_sheet":
            return True
        return False

    @staticmethod
    def _workflow_short_draft_blockers(workflow: dict | None, app_report) -> list[str]:
        if (workflow or {}).get("project_contract"):
            return []  # Length is editorial guidance, not a project delivery invariant.
        if workflow and workflow.get("execution_policy", {}).get("scope") == "partial":
            return []
        if not workflow or app_report is None:
            return []
        stage = ((workflow.get("state") or {}).get("stage") or "")
        if stage not in {"full_application", "argument_sheet"}:
            return []
        warnings = app_report.warnings or []
        blockers = [w for w in warnings if "too short" in w]
        if not blockers:
            return []
        return [
            "workflow requires a fuller draft before final delivery: "
            + blockers[0]
        ]

    @staticmethod
    def _recovery_advice(error: str, phase: str) -> dict:
        text = error.lower()
        if "truncated json object" in text:
            return {
                "kind": "llm_output_truncated",
                "phase": phase,
                "retryable": True,
                "next_step": "The model tried to return too much draft text in one JSON action. Retry with append_draft for only the next section, keep the chunk under 2500 Chinese characters, and continue section by section.",
            }
        if "llm returned no text content" in text or "json" in text or "invalid action" in text:
            return {
                "kind": "llm_action_parse",
                "phase": phase,
                "retryable": True,
                "next_step": "Retry with a stricter JSON-only instruction, or switch this run to offline mode if the provider keeps returning non-action content.",
            }
        if "permission denied" in text:
            return {
                "kind": "filesystem_permission",
                "phase": phase,
                "retryable": True,
                "next_step": "Ask the user to import or authorize the folder, then retry the same tool call.",
            }
        if "malformed ref placeholder" in text:
            return {
                "kind": "citation_placeholder",
                "phase": phase,
                "retryable": True,
                "next_step": "Rewrite the draft with citation placeholders that exactly match saved reference ids, using {{ref:<ref_id>}}.",
            }
        if "not a folder" in text:
            return {
                "kind": "bad_path",
                "phase": phase,
                "retryable": True,
                "next_step": "Ask for a valid folder path or import a specific file instead.",
            }
        if "timeout" in text:
            return {
                "kind": "timeout",
                "phase": phase,
                "retryable": True,
                "next_step": "Retry once, then continue with partial results and mark the slow file as pending.",
            }
        return {
            "kind": "generic",
            "phase": phase,
            "retryable": False,
            "next_step": "Summarize the failing step, keep completed artifacts, and ask for user input only if no safe fallback remains.",
        }

    @staticmethod
    def _failure_summary(error: str, recovery: dict | None) -> str:
        if not recovery:
            return f"Agent 连续失败：{error}"
        return (
            f"Agent 连续失败：{error}\n"
            f"恢复建议：{recovery.get('next_step', '请检查上一轮工具调用。')}"
        )
