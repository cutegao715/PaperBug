# M2 实施记录

状态：**M2-12、M2-13、M2-14、M2-15 已完成**。基线 M1-11：398 passed, 1 skipped。备份 `.test-tmp/m2/backup` 与 before.json。

- M2-13：先实现鉴权、设备挑战、显式项目绑定、本地/云同步开关；默认 local_only，网络前本地检查；主动 LLM/检索不受后台同步开关影响。
- M2-12：本地事件源与兼容快照，恢复队列；云端原子追加与单调序号；先推离线队列再按服务器序号投影。
- M2-14：云端实际模型请求前原子预留，响应 usage 结算，兼容返回值；不存在的上传/通知只提供计量基础接口。
- M2-15：保留云端执行状态与全部续接校验，验证本地离线恢复。

Ruling: 无 Git，保留快照不创建仓库或提交 — 与前轮一致 — 回滚需使用备份。

## 实施与定向验证

M2-13：云端绑定挑战须用户鉴权及活跃设备凭据，一次消费，10 分钟过期；本地绑定记录位于 `projects/<id>/meta.json`，校验 project_id、绝对目录、云地址、space_id/device_id。目录复制后不得静默沿用绑定。后台同步前检查本地模式，然后检查云空间模式，云端追加时再次检查绑定与模式。

M2-12：lifecycle_events.jsonl 为本地追加事件源，以临时文件 + flush/fsync + replace 原子发布，lifecycle.json 为可重建兼容投影；历史快照首次读取惰性建立种子事件，不批量迁移。lifecycle_sync.json 缓存已确认服务器事件；待发集合由本地事件减去已确认 key 推导，因此不需要独立队列写入事务，回包丢失会安全重试。GET 按服务器单调序号分页，cursor 只越过连续序号，避免本地推送 ACK 跳过其他设备历史；冲突保留且在 UI 标注。

M2-14：usage_events 带 user/kind/request_key 唯一约束；BEGIN IMMEDIATE 下原子预留，用量 + 未结算预留共同受限；每次真实网络尝试均预留。现有云对话与云 Agent 接入，模型返回业务 JSON 不变；OpenAI/Anthropic usage 通过可选回调及 last_usage 暴露。无 usage 或网络结果不确定时按预留上限保守估算，明示 estimated；确定拒绝请求释放额度。重启将旧进程留下的 reserved 幂等结算为 estimated 消费，不自动释放以免漏记；管理员可核对 provider 账单后处理。storage_bytes 为所有月份当前存量，负 delta 释放，禁止释放超过库存。未提供用户任意修改用量或配额的公开接口。

M2-15：新增执行边界文档、云端职责说明和本地断网续接/云端约束测试，不删减 agent_context。

定向记录：sync-red 初始 2 项失败后通过；生命周期恢复与复制新增用例先失败后通过；usage-red 初始缺模块失败，接入后通过。扩展 M2 三组测试目前 11 passed（包括双设备、丢 ACK、scheduler 清队列、迁移幂等、并发预留、429 零模型调用、本地/云端续接边界）。V6 浏览器验证通过，覆盖新同步入口与阶段历史以及既有桌面/移动端主链路。

## 配置与范围

进程环境配置 `PAPERAGENT_CLOUD_URL`、`PAPERAGENT_CLOUD_TOKEN`、`PAPERAGENT_CLOUD_DEVICE_TOKEN`。URL 只接受 HTTPS origin（本机 loopback 可 HTTP），凭据仅发给此 origin，禁止重定向。UI 从生命周期面板打开“云同步设置与阶段历史”，填写对应云端空间/设备 ID 后挑战确认；再次确认后才开启 cloud_sync。meta.json 不含令牌。此轮没有上传用户项目到真实云端。

Ruling: 待发队列采用事件差集而非另一份待发文件 — 避免事件/快照/队列三写崩溃窗口 — 队列查询需扫描本项目事件，较大历史未来可建索引。
Ruling: 云端没有 Radar 通知和文件上传接口，仅提供用量/存量计量基础函数，不增加虚构业务端点 — 遵从修订范围 — 对应功能未来接入时须再接计量。
Ruling: 供应商不返回 usage 或响应不确定时保守计入预留额度 — 避免付费后漏记 — 可能高估，summary 明示 estimated，管理员需按账单核对。

## 独立审查与修复

独立 reviewer 确认三个问题，均已修复：

1. 绑定前 legacy_history 丢失：在事件中同步迁移历史，投影从事件重建；新设备保留绑定前记录，不重复导入。
2. 重启遗留预留悬挂：启动时幂等结算为 estimated 用量，同时恢复消息/任务；不把可能已付费的请求当作免费。
3. 降低配额后不能释放存量：所有减少库存的合法 delta 仍允许，库存不得为负。

额外检查修复：存储预留跨月份仍占用当前容量；关闭同步先持久化本机禁用，再通知云端关闭，离线失败显示待确认并允许用户重试（本机后台始终不出网）；云端事件未提供时间戳时，动态默认值不参与幂等内容比较，展示采用已保存的服务器时间；Anthropic 缓存读/写输入计入输入总量。

审查复现 `review-red.log`：4 failed, 9 passed；修复 `review-green.log`：15 passed。默认时间戳、缓存计量各有独立 RED→GREEN 记录。最终 M2 定向套件 `final-focused.log`：17 passed。

Anthropic 输入计量依据：[官方缓存用量说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)，输入总量为非缓存输入、缓存读和缓存创建之和。计量是 token 数，不宣称折算为供应商账单金额。

## 最终验收

- 首轮完整回归：406 passed, 1 skipped；最终完整回归：**415 passed, 1 skipped, 3 warnings，166.72s**，日志 `.test-tmp/m2/final-regression.log`。跳过项为既有条件跳过；警告为明文 .env 迁移提示和两个既有 FastAPI 生命周期弃用提示。
- V6 浏览器回归通过：既有真实创建/上传/编辑/刷新/对话/桌面/移动端链路；新同步历史入口使用真实本地服务；绑定/启用/关闭 UI 使用隔离控制 API fixture，并实际点击确认弹窗。真实云端行为由 TestClient 双设备集成测试验证；未连接真实云。
- core + cloud 两个 wheel 构建与独立虚拟环境安装通过；导入路径均来自 site-packages；重复迁移、重启预留结算、本地事件保存及未绑定零网络同步均通过。
- 源码与安装版离线 demo 均通过。
- M3 未推进。无真实云数据上传，无用户凭据修改，无数据库手工迁移用户历史。

Ruling: 关闭同步先停本机，再尽力关闭云空间；离线时不恢复本机同步 — 保证当前设备停止上传 — 其他设备可能仍同步，UI 显示云端关闭待确认，须显式重试。
Ruling: 启动恢复把未知付费结果记为 estimated，不自动释放 — 避免服务崩溃绕过配额 — 可能高估本月用量，需要供应商账单核对。

Final: fixed 独立审查三项问题及附加边界修复 — 复现测试 RED→GREEN，最终完整回归 415 passed, 1 skipped。无延期 minor。

源码备份与验证日志保留在 `.test-tmp/m2/`，当前无 Git，未提交或推送。

