# PaperAgent · V2-m1 Research Copilot

迁移遗留清理已统一 Python 导入到正式包，工程目录与保留范围见 [清理说明](docs/cleanup_2026-11-03.md)。

当前主对话默认使用云端 Agent 决策，在对话内授权，本机执行工具和验证，无需单独创建云端任务。详见 [统一对话架构与使用说明](docs/unified_chat.md)。

## 云端后台与本地工作空间（第一阶段）

新增独立的 [云端后台工程](backend/README.md) 和[本地工作空间工程](frontend/README.md)，分别安装运行，通过 FastAPI 通信。原研究空间继续保留。启动方法、原型边界与已有云端数据库的使用方式见[第一阶段说明](docs/cloud_workspace_phase1.md)。

## 前端功能连接（2026-10-28）

保持产品原型、页面 HTML 和 CSS 不变，补齐文件选择、局部任务范围、对话对应交付记录、
投稿与返修／改投记录、正式版本下载、投稿 ZIP 和答辩 PPTX／讲稿下载。
输入保留、请求失败和稿件焦点同步一并修复。说明与验证见
[前端功能连接记录](docs/frontend_function_connections.md)。

## 科研生命周期 V1 与 Skill V2（2026-10-28）

当前已按生命周期附件提供 **3 类生命周期、10 个 Workflow、独立的 A1/A2/A3 主动服务**。
交付记录支持暂停、恢复、跳过、回退、版本化输入输出、作者确认及 ERROR 完成门禁；
研究空间可显式复用其它空间的来源版本，审核与现实阶段独立。

工程技能库已按重组手册替换为 **35 个原生 Skill**，七个旧活动定义已移除；
旧读取键和资源键保留兼容，原库有完整备份。此安装仅作用于本工程。

界面入口位于研究空间的“生命周期与交付流程”。实际邮箱账号连接与对外提交仍需授权接入，
后台关注只有在启用服务和调度器运行时生效。详细实现、接口、备份与边界见
[本次落地记录](docs/lifecycle_v1_and_skills_v2.md)，分类与去向见 [V2 重组手册](docs/skills_v2/REORGANIZATION_REPORT.md)。

以下按日期保留旧版本记录；如数量或入口描述冲突，以本节和本次落地记录为准。

## 文献证据、图表与科研产物（2026-10-26）

已增强文献证据与综合矩阵、基本 SVG 图表和检查，新增科研演示、投稿准备、专项申请评审能力。
研究空间可查看证据、下载 HTML 演示／讲稿和 DOCX／Markdown／ZIP 投稿包，并查看来源变化提示。
新增 5 个 Skill，当前共 34 个；Workflow 仍只提供条件推荐，资料与记忆不另建副本。
演示当前为 HTML；PPTX、自动视觉验收、完整刊会合规与真实账号同步仍待后续实现。
具体接口、交付范围和来源说明见 [research-skills 分阶段实施](docs/research_skills_adaptation.md)。

## Skill 分类与写作方法（2026-10-26）

能力类型（通用／生命周期专用）与来源（共享／空间私有）分开展示。前轮形成 29 个 Skill，
新增通用引用核查，以及论证、修订、主题综合、引言与 ML 报告的按需资源。
Workflow 仍只提供条件推荐，研究事实与记忆沿用原有上下文。
设计与完整映射见 [Skill 分类与写作资源](docs/skill_classification_and_writing_resources.md)。

## 八个业务 Workflow 入口（2026-10-26）

三个生命周期现在分别提供：

- 期刊／会议论文：论文撰写、投稿与评审管理。
- 科研项目：项目机会寻找、项目申请、中期报告、结题交付。
- 毕业／学位论文：论文撰写、答辩相关事宜。

新增入口共用原有对话、Skill、资料和论文知识，可从局部开始，不自动推进现实阶段。
选择器显示当前可用与后续接入能力；推荐 Skill 可以跳过、换用或独立使用。
界面可查看全部流程推荐，运行时仍按预算提供有限候选并按需读取正文。
旧阶段 Workflow ID 保留兼容及中断续接，不批量改写历史数据。

本次完成入口、协调配置和推荐关系；持续网站发现、真实邮箱／投稿账号同步、
完整投稿轮次、改投策略和答辩 PPTX 专业交付仍待后续实现。
当前状态与完整方案见 [系统设计](docs/research_assistant_system_design.md)。

## 独立论文评审（2026-10-20）

可以直接要求“评审这篇论文”“只看方法与证据”或“复核上次的修改”，
按实际研究方法读取评审 Skill 内部资源。评审、授权修改、回复审稿人各有独立入口，
不要求启用 Workflow，也没有固定轮次或通读顺序。

保存的报告位于当前空间 `reviews/paper/`，绑定稿件版本、资料来源、检查范围与论文知识。
在“论文知识 → 论文评审与复核”可查看报告、采纳助手提出的复核建议或保存人工判断。
“已复核解决”需要正文中的定位片段；稿件或相关依据变化后会提示重新核对。
报告与复核是评审判断，片段匹配不等于科学有效性或投稿合规认证。

架构和接口见 [架构文档第 20 节](docs/lifecycle_context_architecture.md#20-独立论文评审与版本复核)。

## 英文学术写作与论文知识（2026-10-19）

论文空间可以直接对话写作，也可在“相关专业能力”中查看“英文学术论文写作”，
再按需读取论证、英文表达、图表、刊会要求等内部指南。YAML 与 SKILL.md 技能包
均支持资源目录和独立分页读取；不要求启用 Workflow 或按固定顺序调用。

论文/会议与毕业论文空间的“论文知识”面板支持：

- 维护语言、文章类型、研究设计、目标刊会/届次、Track 和出版阶段。
- 保存、采纳、更正、撤回术语、论证关联和刊会要求，保留来源与历史版本。
- 来源、关联决定或刊会目标变化时提示复核；助手建议不会覆盖作者已采纳记录。
- 对当前稿件核对登记术语和记录状态；该检查不等于科学论证、引用或投稿合规验收。

沿用现有对话、记忆、资料、证据与稿件快照；论文记录进入同一上下文并支持中断续接。
后台刷新保留未提交表单；点击“刷新记录”查看最新候选和稿件列表。
架构、资源格式和接口说明见 [架构文档](docs/lifecycle_context_architecture.md)。

验证：`python -m pytest -q`；可选 `python tests/verify_manuscript_ui.py`
验证论文界面（需 Playwright Chromium，临时空间、无真实模型调用）。

## 共享与空间专用 Skill（2026-10-19）

Skill 以系统共享库和 `<workspace_root>/skills` 共享扩展为主，当前研究空间可在
`<workspace_root>/projects/<id>/skills` 添加专用 YAML 方法。来源与专业分类分开；
同标识同版本采用空间专用、共享扩展、系统共享的优先级，并展示被替代来源。

研究空间背景、当前请求和可选 Workflow 一起推荐能力；Workflow 只引用方法与适用条件，
不固定调用顺序，也不要求调用所有推荐 Skill。关闭流程后仍可直接使用专业能力。
界面可查看来源与方法；模型先看摘要，通过 `read_context(skill)` 按需读取全文，
通过 `read_context(skill_catalog)` 发现其他可用能力。项目事实和记忆继续留在研究空间。
详细约定见 [架构文档第 4 节](docs/lifecycle_context_architecture.md#4-skill-与按需-workflow)。

## 对话、Skill 与资料中心（2026-10-14）

默认直接在研究空间对话，不再创建或管理独立 Task。界面提供相关专业能力、当前稿件、
资料和可选交付流程。日常分析、评审与局部写作不因生命周期阶段自动套用 Workflow。

当前稿件、工作笔记和执行检查点持久保存；输入“继续”或点击中断后的继续按钮恢复上次工作。
换话题会归档旧计划，项目记忆和资料继续共享。稿件版本变化会提示重新读取。
旧 Task 数据和 API 保留兼容，新对话不写入 tasks 目录。

第一阶段支持一个研究空间一条持续对话。Skill 显式选择、要求作用范围、多对话和历史分支恢复
按后续阶段实施，见 [当前架构与路线](docs/lifecycle_context_architecture.md#11-分阶段落地)。

## 项目申请、中期进展与结题（第二阶段）

目标记录现可按需设置数量、单位、期限和验收说明；实际进展记录累计观测，生成目标对照表。
文档检查新增关键声明清单，可选择仅提示、有来源或逐条人工核查。核查结论绑定草稿和来源版本，
修改后提示复核；核查清单可保存为 Markdown/JSON。详见 [架构文档第 16 节](docs/lifecycle_context_architecture.md#16-项目目标对照与关键事实核查)。

三个项目 Workflow 已接入运行时、资料管理界面、适用范围检查和 DOCX 交付。
在科研房间的「研究背景与资料」中按需选择交付流程、局部/全文范围和草稿/提交准备要求；
展开「申请、中期与结题的衔接资料」绑定申请基线、设置阶段模板章节、记录及更正实际进展。

目标、决定、进展和成果带来源及修订历史，跨阶段重新读取；Agent 提取的内容先作为候选，
不会覆盖用户已采纳事实。采用的材料固定到具体版本，修改草稿不会悄悄替换基线。
可直接进入中期或结题、回退和重排步骤；完成文档不会推进现实阶段。
正式交付检查仍需人工核验真实性与业务合规，标准 DOCX 不等于已完成外部申报。

资料接口位于 `/api/v1/projects/{id}/dossier`。具体数据模型、检查规则、API 和操作路径见
[架构文档第 15 节](docs/lifecycle_context_architecture.md#15-项目申请中期进展项目结题第二阶段实现)。

## 三类生命周期与记忆底座（2026-10-13）

完整设计与分阶段验收见 [三类生命周期与上下文记忆架构](docs/lifecycle_context_architecture.md)。
当前已接入科研项目、期刊与会议论文、毕业与学位论文的生命周期目录和显式状态，
Workflow 可跨阶段选择、回退和调整步骤。现实阶段独立于任务执行，不随写作完成自动推进。

以下为首轮底座能力，独立 Task 交互已由上述对话架构取代：

- 创建窗口选择生命周期；科研房间设置现实阶段、管理资料和按需使用 Workflow。
- 对话工作计划、决定、待解问题和稿件版本持久化；恢复时重新观察，不重放工具副作用。
- 项目记忆可记录、更正、移出；保留来源与替代关系，过期或失效内容不再召回。
- 项目隔离与中文片段检索、技能按需读取、长消息完整保留、上下文预算诊断。
- 原始材料按需读取，草稿保留内容哈希快照，检测续接目标是否已被修改。

运行 `python -m pytest -q` 验证后端；可选运行 `python tests/verify_lifecycle_ui.py`
验证界面（需要 Playwright Chromium，使用临时工作空间且不调用真实模型）。
`PAPERAGENT_CONTEXT_INPUT_TOKENS` 配置含系统提示的保守输入预算，默认 24000；
这是文本估算，不是提供商精确计数，也不代表模型总上下文或输出额度。

新增 API：

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/lifecycles` | 三类生命周期与建议工作流目录 |
| `GET/POST /api/v1/projects/{id}/lifecycle` | 显式现实阶段和变更历史 |
| `POST /api/v1/projects/{id}/workflows/preview` | 按目标和可选 workflow_id 预览策略 |
| `GET /api/v1/projects/{id}/sessions` | 持久任务列表 |
| `GET/PATCH /api/v1/projects/{id}/sessions/{task_id}` | 续接状态与可调整计划 |
| `GET /internal/projects/{id}/context` | 上下文与预算诊断，可带 goal 和 task_id |
| `GET /api/v1/projects/{id}/memories` | 项目记忆管理视图 |
| `PATCH/DELETE /api/v1/memories/{memory_id}` | 更正或移出记忆 |

创建项目可传 `lifecycle_type`（`project`、`paper_conference`、`thesis`）和 `stage`。
发送消息可传 `task_id` 续接；不传则建立独立任务；可传 `workflow_id` 明确选择策略。
旧接口调用和已有工作空间继续兼容，不批量迁移用户数据。

此轮完成基础机制与交互接入。专业模板与检查器、投稿轮次的版本绑定界面、
自动语义压缩、自动记忆蒸馏和全文/向量检索仍按架构文档后续阶段实施。

## 既有 Research Copilot 能力

按 V2 蓝图开始升级的科研写作助理骨架：
**Interactive Runtime + Proactive Runtime + Domain State + Gateway + Permission Gate**。
V0 的单 Agent 决策、Harness 约束、确定性引用内核和文件系统 Workspace 仍保留为
交互式写作主链路；V2-m1 增加任务、截止日期、长期关注主题、Literature Radar、
通知和自动化调度。

## 安装

```bash
pip install -e ".[dev]"
```

## 快速开始（离线，无需任何 API key）

```bash
paperagent demo
```

跑通完整回路：离线文献检索（合成桩）→ 保存文献 → 读 PDF → 存证据卡（逐字引文）
→ 写草稿（`{{ref:id}}` 占位符）→ Citation Kernel 校验 → 渲染编号引用 + 文献表。

输出在 `workspace/projects/demo/`：
- `references.jsonl` 文献库（引用的唯一事实来源）
- `evidence.jsonl` 证据卡
- `drafts/draft.md` 草稿（占位符形式）与 `draft.rendered.md`（渲染后）
- `runs/*.json` 完整决策轨迹（provenance）

## Web UI（Research Copilot 界面）

```bash
paperagent serve --port 8000     # 打开 http://127.0.0.1:8000
```

按产品交互原型实现的三栏界面（工作空间 / 对话 / 证据面板）+ Agent Runtime 状态条：

- 每条消息启动一次 Agent Run，执行步骤通过 SSE 实时流入对话区的执行卡片
  （动态决策轨迹，非固定流程假进度）
- 右侧「证据」面板 = 文献库 + 证据卡；「产物」= Citation Kernel 报告 + 草稿；
  「变更」= 全部决策步骤
- 「文档」页查看渲染后的草稿（编号引用 + 自动生成的文献表）
- 停止按钮 → `/cancel`，Agent 在当前迭代边界安全退出
- 底部 Runtime 条 → 双击展开「可观察执行环境」抽屉（目标 / 模式 / 上下文 / 执行日志）
- 未配置 LLM 时自动使用离线演示模式；配置后可切换 Deep Research

## 使用真实 LLM（OpenAI 兼容接口）

```bash
cp .env.example .env  # 填入 base_url / api_key / model
paperagent run --goal "写一段关于 LLM 科研助手的文献综述"
paperagent chat --project mypaper
paperagent serve --port 8000   # Web UI 会自动显示 Deep Research 就绪
```

`.env` 会在启动时自动加载（已存在的环境变量优先）。API 报错会透传服务商的
错误码（如 GLM `1113 余额不足`），检查 `.env` 与账户额度。

## 其他命令

```bash
paperagent init --project mypaper --goal "..."   # 建项目
paperagent validate --project demo --draft draft.md  # 单独跑引用内核
paperagent render --project demo --draft draft.md    # 占位符 → [n] + 文献表
paperagent radar --provider offline_stub             # 手动跑一次 Literature Radar
paperagent tick                                      # 手动跑一次主动调度
paperagent eval          # 跑 evals/golden 离线评测（10 个任务）
pytest                   # 单元 + 端到端测试
```

## 蓝图 → 代码映射

| 蓝图层 | 当前实现 | 说明 |
|---|---|---|
| Research Agent Runtime | `src/paperagent/agent/` | `decision_loop.py` observe→decide→act（含 on_step 事件流与取消点）；`scripted.py` 离线确定性决策器；`llm_agent.py` 真实 LLM 决策器 |
| Agent Harness | `src/paperagent/harness/` | `tool_executor.py` 是唯一强制点（schema/超时/预算/provenance）；`context_assembler.py` 压缩观测，全文不进上下文；`llm.py` OpenAI 兼容客户端 |
| Client / Control Service | `src/paperagent/server.py` + `web/index.html` | FastAPI + SSE 流式推送 + 取消端点；单页三栏 Chat UI（原型视觉还原，真实数据） |
| Domain State | `src/paperagent/domain/` | 结构化保存任务、截止日期、通知、长期关注、自动化、Radar 命中和研究画像 |
| Proactive Runtime | `src/paperagent/tasks/` + `src/paperagent/radar/` | scheduler 处理 deadline reminders / digest / radar automation；Literature Radar 做抓取、去重、规则匹配和通知 |
| Gateway / Permission Gate | `src/paperagent/gateway/` + `src/paperagent/quality/` | AssistantEvent 路由到 interactive/proactive runtime；loop action 先过 PermissionGate |
| Tool Registry | `src/paperagent/tools/registry.py` | V0 静态表；health/SLA/灰度是 V1+ |
| Research Capabilities | `src/paperagent/tools/` | `literature_search.py`（Semantic Scholar + 离线桩）、`pdf_reader.py` |
| Research Kernel | `src/paperagent/kernel/citation_integrity.py` | 确定性校验：占位符合法性、引用存在性、引文逐字核验 |
| Memory / Workspace | `src/paperagent/workspace/` | 文件系统持久化 + `render.py` 引用渲染 |

## 架构决策（评审时定下的）

1. **引用编号永不落盘**：草稿只存 `{{ref:<id>}}`，编号在渲染时按首次出现顺序从
   Reference DB 重算 —— section 级改写不会破坏全文引用。
2. **证据卡压缩**：PDF 读完即压缩为逐字引文 + 页码锚点的 Evidence 对象，
   原文不进上下文（token 策略）。
3. **Kernel 分两档**：errors 阻断（虚构引用、引文与原文不符），warnings 仅提示
   （未引用文献、缺证据卡）。`finish` 被 kernel errors 拦截。
4. **Executor 是唯一强制点**：Policy 只给参数；预算、超时、provenance 全在
   `tool_executor.py` 一处执行。
5. **离线桩绝不伪装真实**：离线检索返回 `[SYNTHETIC STUB]` 标记的合成文献，
   渲染时保留标记 —— 虚构文献不能看起来像真的。
6. **结构化 Domain State 不是 Memory**：会影响真实后果的状态（deadline、任务状态、
   automation、通知）落在 `domain/`，供 Proactive Runtime 和 context assembler 读取。
7. **Permission Gate 先于执行**：loop action 被映射为 local_read/local_write/notify 等
   action kind；`deny` 会立即终止本轮运行并写入 trace，避免被后续 action 掩盖。

## V2-m1 HTTP 面

- `GET /api/v1/today`：今日科研简报、近期截止、开放任务、未读通知、Radar 命中
- `POST /api/v1/tasks`、`POST /api/v1/tasks/{id}/status`
- `POST /api/v1/deadlines`
- `POST /api/v1/standing-intents`
- `POST /api/v1/profile`
- `POST /api/v1/radar/run`
- `GET /api/v1/notifications`、`POST /api/v1/notifications/{id}/read`
- `GET /api/v1/automations`、`POST /api/v1/automations`
- `POST /api/v1/scheduler/tick`

## 下一步（V2 后续）

- Memory 检索（L1 working → L2 project 蒸馏）、更多 kernel（结构/方法一致性）、
  Skill Loader（`skill.yaml` 四层）、Radar 的 AI relevance evaluator，以及 Web UI 中
  Today / Tasks / Radar / Notifications 的完整操作面。


## API 版本与兼容窗口

本地研究服务与云端服务分别提供 `/api/v1/*` 稳定接口。现有 `/api/*` 路径在兼容窗口内继续处理相同请求，响应附 `Deprecation` 头；客户端应使用新前缀。旧接口无重定向，POST、SSE、鉴权及错误响应保持原行为。

上下文与预算诊断使用 `/internal/projects/{project_id}/context`，不承诺稳定版本；旧 `/api/projects/{project_id}/context` 暂保留。桌面本机材料授权协议 `/local/*` 保持原路径和来源校验。未新增调试接口或放宽权限。

本地 `server.py` 负责 app 组装；`api/v1/` 按领域提供 APIRouter；`api/control.py` 承载既有运行状态和业务处理。旧 `*_api.register_*_routes(app, ...)` 导入仍可用并返回 router。内部测试替换运行实现时应针对 `paperagent.api.control`，而非对 `server` 的兼容导出重新赋值。
