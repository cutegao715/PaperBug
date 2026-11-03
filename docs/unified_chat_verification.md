# Unified chat ledger
Plan: docs/superpowers/plans/2026-11-03-unified-chat.md
Status: complete for the scoped unified V6 main-chat implementation; limitations below.
Ruling: User explicitly approved the unified architecture and requested implementation; continue without repeating design approval. Reuse deterministic local execution and cloud full-protocol decisions to preserve tools/workflows; do not claim the entire execution harness moved to cloud.
Ruling: No Git detected in preceding work; retain scoped pre-change snapshot and logs instead of initializing Git or creating a phantom worktree.
Ruling: auto/cloud default cloud; explicit llm/offline retained for compatibility, never used as automatic fallback.
Preflight: observation -> cloud model uses assistant_state once; action -> existing local executor must be scoped; inline approval must bind exact pending request and owner before send_message can start.

- Baseline full suite: 469 passed / 1 skipped / 1 deselected, 132.52s.
- Cloud protocol RED: missing conversation_api (3 failed) → owner/device/sequence/lease tests pass. Missing device credential expected401 (existing verify_device contract), test expectation corrected.
- Local adapter RED: missing CloudConversation (3 failed) → inline no-upload, full existing read/write/reply loop, permission scope and personal filter pass.
- Routing RED: default local path and missing authorization API (2 failed) → main cloud consent, duplicate approval single startup, workflow options retained; 30 targeted passed.
- Completion outbox RED: missing durable pending_finish → fixed and 27 targeted (new9 + previous18) passed.
Ruling: Per-run authorization (1 hour), optional write/external roots/personal context; automatic root access is narrowed to the approved project. Existing explicit local/offline modes remain available. Cloud can't expand roots by calling import_folder.
Ruling: Local process restart requires a new continuation rather than automatic replay of uncertain filesystem writes; cloud model leases can recover independently. UI says interrupted and retains checkpoints.

- Main HTTP integration (real local DecisionLoop, injected cloud model + HTTP transport): read/write and followup complete over SSE, local LLM construction is an assertion failure; 10 tests passed before review fixes.
- First full regression: 477 passed / 1 skipped / 1 deselected, 140.80s (before completion/review tests).
- Fresh independent review: review_unified_chat, gpt-6-astra/high, read-only. Three Important findings reproduced: readonly saving tools, unapproved request leaking through history, conversion output outside project.
- Review RED: 6 failing cases / 11 passing. Fix payload-aware side-effect classifier, consent-tagged messages filtered at ScopedWorkspace (including read_context), separate output-path validation. GREEN: 35 passed including prior control tests.
Ruling: Dynamic MCP scopes cannot be inferred from arbitrary tool schemas. Default cloud consent refuses MCP; a separate unchecked MCP option explicitly authorizes the existing configured external scope (not project-limited), and also requires author-write consent. Existing tool-level permission gates continue to apply.
Final: minor (deferred): completion reconciliation cannot upload after authorization expiry. Local result stays saved; cloud terminal state may remain pending until a later authorized reconciliation capability is implemented. Do not silently extend consent to fix it.
Final: Ruling: Reviewer set aside live model/browser/Office/deployment and final full tests. We run browser + live model + suite here; actual Office conversion availability and production deployment remain environment-dependent and unclaimed.

- Final full regression: 486 passed / 1 skipped / 1 deselected, 152.37s (`.test-tmp/unified-chat/full-final2.log`). Existing three warnings remain; optional network test excluded by project defaults.
- CSS baseline: asserted app.css retains the entire original byte prefix; only consent styles appended. Updated protected hash for this intentional UI feature; other protected artifacts unchanged.
- Clean core/backend wheel build and force install into independent wheel-env; both imports verified under site-packages. Source-external targeted tests: 35 passed, 17.42s (`installed-tests.log`).
- Legacy browser regression V6_BROWSER_OK. Live browser verified send -> consent -> reject; new request -> refresh -> inline write consent -> real cloud model -> local read/edit/kernel validation -> same-chat final reply. Cloud status finished, 4 acknowledged actions. Cancelled text absent from all observations.
- Test services remain on 8766 (local) and 8765 (cloud API), independent worker running. Synthetic test workspace and evidence are recorded in docs/unified_chat_live_test.md.
- No second independent review. All Important findings resolved with regression evidence; disclosed minor expiry/reconciliation limit remains deferred.
