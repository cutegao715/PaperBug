# Unified Chat Implementation Plan

> Execute inline with executing-plans; one independent fresh-context final review.

**Goal:** Default main chat to cloud decisions with full local tools and inline authorization.
**Architecture:** Durable cloud decision queue + RemoteAgent adapter + existing deterministic execution harness and unified transcript.
**Tech Stack:** Existing Python/FastAPI/SQLite/HTML/JS; no new runtime dependency.
**Spec:** docs/superpowers/specs/2026-11-03-unified-chat-design.md

## Constraints
No silent local fallback; no pre-consent uploads; retain explicit offline/local and legacy cloud endpoints. Single-host SQLite. No Git: snapshot .test-tmp/unified-chat/backup. Never log secrets.

## Review focus
- Wrong account/device/space and replayed or changed approval payload.
- Worker expiry/revocation during model inference and poll cancellation.
- Cloud action escaping approved project paths or escalating readonly grant.
- Personal/other-project data leaking through assembled context.
- Legacy workflows, quality checks, followup and interruption behavior.

## Tasks
- [x] 1. Cloud conversation queue (backend conversation_api.py, worker.py, app.py): owned grants/runs/decisions/results; full LLMAgent protocol; token fencing and retries. Test tests/test_unified_chat.py RED→GREEN.
- [x] 2. Local authorization and execution adapter (tasks/cloud_conversation.py): persistent cards, bounded scope, remote decisions, no local model, cancellation, local permissions and result reports. Test authorization/path/privacy and end-to-end actions RED→GREEN.
- [x] 3. Main message routing and V6 inline cards (api/control.py, api/v1/conversation.py, web/v6/app.js): preserve task/workflow/resume and SSE, default cloud, explicit legacy modes; browser acceptance.
- [x] 4. Independent final review, failure reproduction fixes, full pytest, browser, clean dual wheels, documentation, restart test services and live unified-chat roundtrip.
