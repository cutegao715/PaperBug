# 混合控制面：实现与运行

本轮是单主机可运行的架构落地，未部署生产服务。Control API 与身份、设备、配额共用 cloud 服务，worker 是独立进程；不需要每个用户一个服务，也没有额外拆出用户管理微服务。

## 主入口更新（2026-11-03）

主对话现在默认使用云端模型决策，对话内完成逐轮授权，本机执行工具与验证，不必单独创建云端任务。完整职责、传输和恢复边界见 [统一对话说明](unified_chat.md)。下文的 opaque file_ref、提案后应用与 24 小时授权描述的是保留兼容的旧 managed-task 协议，不是新主对话协议。

## 旧 managed-task 协议职责

| 能力 | 本地 | 云端 |
|---|---|---|
| 普通离线研究会话 | 保留现有决策循环、会话和工具 | 不参与 |
| 新建云端研究任务 | 明确授权，轮询执行允许的操作 | 持久入队、模型决策、上下文、任务状态、重试 |
| 原始材料 | 文件留本机；路径不进入新操作协议 | 只持有 opaque file_ref、SHA256、授权、按需结果 |
| 记忆 | 账户目录下的用户/项目记忆，默认不上传 | 明确共享的用户/项目记忆，按 owner 和 space 查询 |
| 验证 | 读取授权文件、局部检索、引用标记/记录核对 | 接收有来源标识的设备报告，不当作服务器认证事实 |
| 改稿 | 保存提案、用户审阅后应用、条件式撤回 | 生成提案，不能直接写本地稿件 |
| 主动服务 | 私人检查执行、结果缓存、上线接收通知 | 公开文献监测、只读周期任务、去重通知保存 |

新任务唯一执行归属为 cloud；旧的请求式 agent API 保留兼容，但无法推进 managed 任务。任务通常执行十步以内，超出步数不会无限消耗模型。

## 身份和记忆

`paperagent account-connect --root ./workspace` 使用配置的云地址和登录令牌调用 `/api/v1/auth/me`。只保存 origin/user_id，不保存令牌；打印的 profile 填入 `PAPERAGENT_ACCOUNT`，重启本地服务后生效。目录为 `workspace/accounts/<origin+user hash>/`。切换账户必须重新启动进程，不能在运行中改环境变量。旧 `workspace/` 数据不自动迁移，也不自动认领为某个用户。相同 OS 用户可访问自身磁盘，目录隔离不是操作系统安全沙箱。

云端身份来自 bearer token；项目、任务、设备、记忆查询均校验 owner。当前为个人空间，不包含团队成员/RBAC/组织权限。新管理 API 不允许客户端写任意会话快照：`CloudSessionRepository` 提供归属校验的快照和事件 revision，worker 用租约 token + 事务写入；旧 token 不能提交或取消任务。

`FileSessionRepository`、`FileMemoryRepository` 是本地存储边界；云端记忆以 `CloudMemoryRepository` 接入现有相关性检索。`share-memory` 必须明确确认，按 user/project 作用域复制到云；这不是自动双向同步，删除本地记忆不会自动删除云副本。云记忆可通过 DELETE `/api/v1/control/spaces/{space}/memories/{id}` 删除。任务只有授权 `include_memories` 时才检索云记忆。

## 启动

先安装 core 与 backend 包；所有示例在自己的测试/部署环境运行，本文不包含凭据。

```powershell
python -m pip install . ./backend
# API 和 worker 必须使用相同绝对数据库路径、模型配置、产品配额配置
$env:PAPERAGENT_CLOUD_DB = 'E:/PaperBug/cloud-data/cloud.sqlite3'
paperagent-cloud
# 在第二个终端配置同一环境后：
python -m paperagent_cloud.worker
# 单次调度可使用 python -m paperagent_cloud.worker --once
```

本地配置 `PAPERAGENT_CLOUD_URL`、`PAPERAGENT_CLOUD_TOKEN`、`PAPERAGENT_CLOUD_DEVICE_TOKEN`；云地址只接受 HTTPS 或回环 HTTP。注册/登录、空间和设备仍使用现有 `/api/v1/auth/*`、`/spaces`、`/devices` API。令牌过期需重新登录配置，不会自动刷新。

```powershell
paperagent account-connect --root ./workspace
$env:PAPERAGENT_ACCOUNT = '<命令返回的 profile>'
paperagent serve
```

在 V6 项目“云同步设置与阶段历史”完成设备/空间绑定，然后直接在主对话提出需求并授权。旧云端任务仍可通过 CLI 创建，默认授权 24 小时，授权对象为当前文件版本。提交目标、按需文本片段和检查结果会上传云端；引用检查可读取本项目被引用的参考/证据元数据，但不会读取未单独授权的源 PDF，也不会上传源文件路径。该检查明确返回 `source_verification=not_authorized`，不能据此宣称引文原文已经核验。

CLI 对应入口：

```powershell
paperagent cloud-task --project my-paper start --goal '检查引用标记和记录' --file drafts/paper.md --confirm
paperagent cloud-task --project my-paper poll
paperagent cloud-task --project my-paper status --id <task-id>
paperagent cloud-task --project my-paper cancel --id <task-id>
paperagent cloud-task --project my-paper revoke --id <grant-id>
paperagent cloud-task --project my-paper share-memory --id <memory-id> --confirm
paperagent cloud-task --project my-paper proposals
paperagent cloud-task --project my-paper apply --id <operation-id> --confirm
paperagent cloud-task --project my-paper undo --id <operation-id> --confirm
paperagent cloud-task --project my-paper monitor --goal 'graph learning' --confirm
paperagent cloud-task --project my-paper stop-monitor --id <monitor-id>
```

需要生成提案时，start 的 `--actions` 显式加入 `draft_propose`。应用提案校验文件版本，先持久化原文再写入；重启后可恢复中断应用。撤回要求当前稿件仍等于提案，避免覆盖后续人工修改。没有任意 shell/路径执行入口。

## 主动服务和离线恢复

- 私人材料周期任务：start 加 `--interval 3600`，仅允许只读授权。云端定时入队，本机关机时等待，授权过期后不再执行。文件改变需新授权；旧任务收到明确失败，不阻塞其他任务。
- 公开监测：云 worker 轮询 arXiv RSS/OpenAlex/已有检索适配器，记录来源覆盖范围与游标。RSS 只代表近期 feed，不能声称完整历史扫描。检索主题会发给所选公开源。
- 公开元数据缓存 15 分钟，不缓存私人稿件；通知按用户/空间保存、去重、游标拉取。本地调度器开启时自动执行；也可手动 poll。没有实现 OS 推送或唤醒关机设备。
- 本机“暂停执行与接收”只停本地轮询；云监测需“停止监测”，云任务需“取消任务/撤销授权”。阶段同步开关与新任务授权相互独立。
- 操作结果 outbox 持久保存、租约过期重新领取；重复结果校验一致性。模型异常退避，五次失败后结束；worker 崩溃可在租约到期后接管。任务取消/授权撤销在领取、结果提交、模型回写均校验。

## 长文本和成本边界

本地单材料上限 20 MB、PDF 500 页；读取结果每次最多 5 页、每页 3500 字符，局部检索最多 20 段。不会每步上传整个 PDF。当前 read_pdf 返回前五页，长文其他位置使用授权材料内 search_local；尚无完整分块索引/任意页导航。上下文有预算裁剪并标明省略，不能把摘要当作全文覆盖。已存结果可保留在云任务状态，不等于全部进入每次模型输入。

沿用云端模型 usage 计量/配额，监测记 radar_runs。实际账单取决于模型和文献供应商；本轮没有外网负载测试或价格承诺。当前 SQLite WAL/事务适用于单主机，需用进程管理器运行 API 与 worker；多机队列、PostgreSQL/RLS、独立对象存储、保留期/备份策略、团队权限、自动凭据刷新属于后续生产化工作。

旧桌面客户端、旧对话 API 并未整体迁成新控制协议。本轮可用入口是 V6、本地 API/CLI 和 cloud API/worker。原论文优化路线的完成状态不等于这套架构已经生产部署。
