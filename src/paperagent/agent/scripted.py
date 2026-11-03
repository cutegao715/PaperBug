"""ScriptedAgent: a deterministic decision maker.

Not a product feature - it is the reference implementation of the
AgentAction protocol. It runs the full skeleton offline (tests, demo,
golden evals) with zero LLM calls, and it labels its own drafts as
scaffolds. Real writing comes from LLMAgent.
"""
from __future__ import annotations

import re
from typing import Optional

from paperagent.schemas.actions import (FinishAction, ReadPdfAction,
                                        SaveEvidenceAction, SaveReferenceAction,
                                        SearchAction, WriteDraftAction)


class ScriptedAgent:
    """Fixed policy: search -> save refs -> read pdf -> save evidence ->
    write scaffold draft -> finish."""

    def __init__(self, goal: str, provider: str = "offline_stub",
                 pdf_path: Optional[str] = None):
        self.goal = goal
        self.provider = provider
        self.pdf_path = pdf_path
        self.searched = False
        self.candidate_ids: list[str] = []
        self.saved: list[str] = []
        self.read_pdf_done = False
        self.evidence_saved = False
        self.draft_done = False
        self.pdf_page_text: str = ""

    def decide(self, obs: dict):
        if not self.searched:
            self.searched = True
            return SearchAction(query=self.goal, max_results=5,
                                provider=self.provider)

        if obs.get("last_action") == "search":
            for c in (obs["last_result"].get("candidates") or []):
                if c["ref_id"] not in self.candidate_ids:
                    self.candidate_ids.append(c["ref_id"])

        if len(self.saved) < min(3, len(self.candidate_ids)):
            nxt = self.candidate_ids[len(self.saved)]
            self.saved.append(nxt)
            return SaveReferenceAction(ref_id=nxt)

        if self.pdf_path and not self.read_pdf_done:
            self.read_pdf_done = True
            return ReadPdfAction(path=self.pdf_path, pages=[1])

        if self.pdf_path and not self.evidence_saved:
            pages = ((obs.get("last_result") or {}).get("pages") or [])
            if not pages:
                self.evidence_saved = True  # read failed; skip evidence
            else:
                self.evidence_saved = True
                text = pages[0]["text"]
                quote = self._pick_quote(text)
                if quote and self.saved:
                    return SaveEvidenceAction(
                        ref_id=self.saved[0], quote=quote, page=pages[0]["page"],
                        supports=f"sample evidence for the '{self.goal}' overview",
                        source_path=self.pdf_path)

        if not self.draft_done:
            self.draft_done = True
            return WriteDraftAction(filename="draft.md",
                                    content=self._draft(obs))
        return FinishAction(summary="offline scaffold draft written and validated")

    @staticmethod
    def _pick_quote(text: str) -> str:
        # deterministic: middle sentence of the page, capped
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if not sentences:
            return ""
        q = sentences[len(sentences) // 2]
        return q[:200]

    def _draft(self, obs: dict) -> str:
        refs = obs.get("saved_references") or []
        lines = [
            f"# Literature overview (scaffold): {self.goal}",
            "",
            "> Offline scaffold produced by ScriptedAgent for walking-skeleton",
            "> verification. Statements are placeholders pending real reading.",
            "",
            "## Overview",
            "",
        ]
        for r in refs:
            year = r.get("year") or "n.d."
            ph = "{{ref:" + r["ref_id"] + "}}"
            lines.append(f"- {ph} The literature landscape includes "
                         f"work on {r.get('title', 'this topic')} ({year}).")
        if not refs:
            lines.append("- No references were committed; nothing citable yet.")
        lines += ["", "## Next steps", "",
                  "- Replace scaffold statements with evidence-backed claims.",
                  "- Verify every quote against its source page.", ""]
        return "\n".join(lines)
