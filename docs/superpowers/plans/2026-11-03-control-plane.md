# Hybrid Control Plane Implementation Plan

> For agentic workers: use executing-plans inline; one independent end review. User approved execution of the preceding architecture discussion.

**Goal:** 云端控制 API + 可恢复 worker + 本地受限执行器，贯穿身份、记忆、任务、授权和主动调度。
**Architecture:** 复用共享 Agent/工具/质量内核，保留旧 API 兼容；新任务由云端唯一拥有，本地只执行版本绑定的授权操作。身份模块与控制服务同部署，worker 独立进程。
**Tech Stack:** Python/FastAPI/SQLite/现有 JSONL 和 HTTP；本轮单主机 SQLite 事务/租约，不引入多机集群。
**Spec:** 本聊天已批准设计：统一身份、存储接口、任务归属、控制/执行分离、本地执行、主动调度、隔离验证。

## Constraints and rulings
- 不自动迁移或上传用户资料；默认未登录本地模式保持兼容。
- 账户配置经云端 /auth/me 验证后创建，按 origin+user 分目录；选择账户在进程启动时完成，运行中不动态改变环境变量。
- 云端会话及记忆接口必须先校验用户/空间归属，模型不能指定身份。
- 授权限定 space/device/material hash/actions/expiry；撤销在每个领取、执行结果和提交阶段生效。
- worker 不执行任意 shell，只派发 read_pdf、search_local、verify_citations、draft_propose；稿件提案须本地明确应用，后台不自动写稿。
- 共享 SQLite WAL + BEGIN IMMEDIATE + token fencing，单主机 API/worker 可并行；不声称跨主机 HA。旧请求式 advance 保留，新 managed 任务拒绝旧 advance 绕过。
- 无 Git：快照 .test-tmp/control-plane/backup，日志和决策写 docs/control_plane_verification.md，不初始化仓库。

## Review focus
跨用户 ID/记忆；撤销授权发生在模型运行中；租约过期旧 worker 回写；本地材料变更及重放；后台授权不扩大为作者写权限。

## Tasks
### 1. Identity and repositories
Files: src/paperagent/identity.py, workspace/repositories.py, workspace/workspace.py, api/control.py, conversation.py, tasks/session.py, cli.py; backend identity/storage modules and /auth/me.
- [x] Tests: 两账户同名项目/记忆互不读取；云记忆跨用户 404；过期会话推进租约冲突 409（见 ledger ruling）。
- [x] Implement immutable Identity, account root selection, FileSessionRepository, FileMemoryRepository, scoped cloud repositories. Integrate existing memory/context call sites.
- [x] Verify targeted tests RED→GREEN.

### 2. Durable control and worker
Files: backend control.py, worker.py, app.py, cli.py; shared task/operation protocol.
- [x] Tests: submit without model call, one claim, retry/backoff, stale lease, cancel, legacy compatibility.
- [x] Implement grants, managed job queue, events, memory context and fenced advance service; independent worker CLI.
- [x] Verify targeted tests RED→GREEN.

### 3. Local executor and task transport
Files: src/paperagent/tasks/cloud_tasks.py, workspace/operation_executor.py, api/v1/sync.py, cli.py; cloud operation result schemas, research/original_agent.py.
- [x] Tests: local PDF/read/search/check/proposal, changed hash reject, idempotent retries, explicit apply, unauthorized operation reject.
- [x] Implement local grant mapping, bounded results, scoped local queue/cache, pause/cancel controls; start/status/poll via API and CLI.
- [x] Verify complete model→local→cloud flow with injected model/HTTP fixtures.

### 4. Proactive scheduling
Files: backend control.py/worker.py; tasks/scheduler.py/cloud_tasks.py.
- [x] Tests: due schedules generate one job; revoked/expired grants stop; offline local pending; no autonomous author writes.
- [x] Implement periodic authorized cloud tasks and local poll integration. Public-information research uses existing source adapters where configured; private data remains local until explicitly granted.
- [x] Verify targeted tests.

### 5. Acceptance
- [x] Full pytest, offline demo, CLI validate, V6 browser, clean dual wheel installation.
- [x] Fresh independent review; fix important findings with RED→GREEN.
- [x] Document run commands, isolation boundaries, costs, recovery, compatibility; report actual limitations.

Acceptance: 469 passed / 1 skipped / 1 deselected; V6 browser, offline demo/validate, independent dual-wheel runtime smoke passed. See docs/control_plane_verification.md and docs/control_plane.md.
