# Unified cloud conversation

User-approved intent: 主对话默认云端 Agent，本地工具执行与验证，授权在对话中完成，不另建云任务，保留现有功能并完整回归。

Chosen implementation: reuse the complete AgentAction protocol and deterministic local DecisionLoop execution/quality/checkpoint machinery, but replace its decision provider with a cloud-owned durable worker queue. The local loop must never instantiate a model client in cloud mode. Each observation/action pair is a persisted cloud decision with owner/device/run identity, idempotent sequence and leased commit. Local execution reports and final status join the same cloud run. Existing conversation/Workflow/session and SSE remain the UI contract.

Alternatives rejected: simply redirect messages to the restricted single-material task (loses tools/workflows); move the full filesystem-dependent loop to a server mirror (uploads excessive data and duplicates sources). This choice retains local safety and working context checkpoints; cloud owns model decisions, local owns tool completion truth. Local process crash is explicitly interrupted, never silently replays uncertain writes; next user continuation assembles persisted context in a new cloud run.

Authorization: each main-chat request first creates a local pending card, with no network/model call and no context upload. Approve binds the exact request/options and account/origin/device, project scope, optional existing external read roots, optional personal context, and author-write permission to a bounded run. Rejection is visible in the same transcript. Missing account setup is shown in conversation, not silently routed to a local model. Local llm/offline modes remain explicit compatibility options. Existing manual cloud-task APIs remain compatible but are no longer the primary entry.

Permissions: no arbitrary paths, unknown actions, or tool bypass; preserve current tool and workflow policies. Cloud local workspace resolves only project and explicitly approved read roots; write permission is separate. No private upload before authorization; account mismatch/revocation/expiry/cancellation checked before steps and cloud commits. Context compacted by existing budget; personal context excluded unless chosen.

UI: main message API returns pending authorization or run_id. Cards persist across reload; approve/decline inline; progress stays SSE; response and validation results remain ordinary assistant messages. Settings label default cloud explicitly. Cloud monitoring remains separate settings; no manual task creation for normal conversation.

Tests: unbound default, pre-consent no network, immutable/replayed consent, cross-owner/device, stale worker, revocation midmodel, cancel while waiting, full shared actions and workflows, local writes blocked by read-only grant, personal-context filtering, readonly tool paths, normal cloud read/write/check/chat, followup/resume, SSE, reload/reject, complete prior suite and clean wheels. No actual external communications or production deploy.
