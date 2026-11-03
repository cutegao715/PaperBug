# Control plane implementation ledger

Plan: docs/superpowers/plans/2026-11-03-control-plane.md
Status: completed locally; not deployed.

Ruling: 用户已批准上一轮架构并要求落地，按既有 inline 流程执行，不重复请求设计批准。
Ruling: 无 Git，保留快照；部署范围单主机 SQLite API + worker，使用事务租约，不宣称多机 HA。
Ruling: 新 managed 任务复用现有模型决策和研究资源，旧请求式 API 保留兼容且不可推进 managed 任务。
Ruling: 本地账号根目录按云 origin/user 分离；离线旧根目录不会自动认领到任一云用户。
Ruling: 本轮落实架构与可用闭环，不凭架构变动宣称具备新的科学语义验证能力。

Preflight: cloud memory → context assembler 必须传经鉴权的 owner/space；task grant → local executor 保持 opaque file_ref/hash，不能把云端任意 path 解释为本地路径；scheduler 与 interactive 的操作白名单分别校验。

## Current evidence
- Baseline: 451 passed, 1 skipped, 1 deselected (`baseline.log`).
- Initial red: missing identity/control/executor modules; initial green: 14 passed including legacy cloud tests.
- Recovery red: local result / worker finalization race and terminal-step restart recovery failed; fixed → 14 passed.
- Proposal red: interrupted apply could not recover; journal-aware apply and guarded undo → 15 passed (`proposal-green.log`).
- End-to-end fixture proves model → local citation checker → cloud finish → deduplicated local notification, scoped memories, account mismatch upload prevention, readonly schedule, public-source dedup, revocation during model execution.

Ruling: 云会话不开放任意 save；CloudSessionRepository 提供 owner-scoped 快照及事件 revision，所有状态写入由 worker 租约 token 与事务串行化。原计划“会话版本冲突 409”用过期租约/旧推进请求 409 验证，不增加客户端改写云上下文的入口。
Ruling: 新云任务与监测使用独立明确授权和本机暂停开关，阶段同步 local_only 不撤销用户单独授权的云任务；界面明确说明，撤销 grant 或取消 task 才停止云端任务。
Ruling: 公开源监测采用查询元数据缓存及本机轮询通知；本机关闭时保留通知，不声称操作系统推送或远程唤醒。云端可以持续监测；私人检查等待本机上线。
Ruling: 保留本轮快照和验证日志，因为没有 Git 历史可用于恢复；不按通用 skill 清理唯一备份。

## Independent final review (fresh reviewer)
Reviewer: review_control_plane, gpt-6-astra/high, read-only; no follow-up reviewer.
- Important: expired lease could cancel via exception handler. RED reproduction then fence handler and reset by current/unexpired token.
- Important: citation verification read arbitrary evidence paths and leaked paths. RED reproduction then limit metadata to cited records, remove source paths and explicitly mark source verification not authorized. This grant does not certify PDF quotations.
- Important: bad material aborted all polling. RED two-operation reproduction then persist a bounded material_unavailable result, mark only that task failed and continue processing others.
- `review-red-all.log`: 3 failed, 15 passed → `review-green.log`: 18 passed.
- No Critical findings, no deferred Minor findings.

Final: Ruling: Review set aside live providers/cost, production TLS/deployment, multi-host SQLite, OS push. These are explicitly not deployed or claimed; deterministic fixtures verify protocol and isolation, not live model quality or production readiness. Production rollout must validate those separately.
Final: Ruling: Reviewer deferred full regression and browser assessment to implementer evidence; real V6 browser test covers new task authorization flow using a fixture transport, full HTTP integration tests cover server/local/model boundary. No real credentials or external paid calls used.

Acceptance so far:
- Initial full suite `full-first.log`: 466 passed, 1 skipped, 1 deselected, 3 warnings; 161.59s.
- V6 `browser-final.log`: V6_BROWSER_OK, including new authorization form.
- Offline demo and `validate --project demo`: passed=True, 3 citations/3 refs/1 evidence; two expected weak-grounding warnings for synthetic uncited evidence.
- Clean package staging (exclude egg-info/build caches): both wheels built, both installed in independent wheel-env.


## Final acceptance
- Full regression after review fixes: `full-final.log` — **469 passed, 1 skipped, 1 deselected, 3 warnings, 174.12s**. Network-marked test intentionally excluded by project default. Warnings include existing FastAPI lifespan deprecation, not test failures.
- `browser-final.log`: V6_BROWSER_OK; task consent form exercises POST body, existing desktop/mobile flows remain green.
- `installed-smoke.log`: INSTALLED_SMOKE_OK; both imports originate in independent wheel-env site-packages, real PDF read and local search work, identity roots differ, packaged V6 contains task UI.
- Independent installed-wheel integration: `installed-tests.log` — **18 passed, 3 warnings, 10.43s**, run outside the source tree with both packages from site-packages. pytest/httpx were initially absent; dependency download completed and the installed test run succeeded.
- `build.log` / `install.log`: clean staging builds and installs both core/cloud wheels.
- No deployment, external model calls, production data migration, Git commit or push performed.
- Tasks 1–5 complete under the documented rulings. Snapshot/logs retained for rollback because no Git repository exists.

Final: fixed expired-worker error writes — test_expired_worker_cannot_cancel_reclaimable_task RED→GREEN, full suite 469 passed.
Final: fixed unauthorized citation source reads/path disclosure — test_citation_grant_does_not_read_or_expose_unrelated_sources RED→GREEN, full suite 469 passed.
Final: fixed one invalid material blocking other operations — test_invalid_material_does_not_block_other_operations RED→GREEN, full suite 469 passed.
