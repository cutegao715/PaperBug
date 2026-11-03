# PaperBug 架构修复清单 v1（代码核对修订版）

> 初次核对记录（2026-10-30）：当时仅修订计划；下文“现状证据”为实施前基线。后续已逐阶段实施，当前进度及验证见各阶段执行记录，不应将历史基线解读为当前代码状态。

> **当前状态（2026-11-01）**：M0～M3 及并行轨 #18～#20 全部实施完成（按修订范围与下述能力边界）。最近全量回归 **451 passed、1 skipped、1 network deselected**。#18～#20 的审查、安装、demo、浏览器和真实 RSS 探测记录见 `docs/remaining_verification.md`；配置见 `docs/reference_and_radar.md`。触发式 backlog 仍不排期，不计为已实现。

> 本文档是写给执行 agent 的工程规格。每一项包含：现状证据（文件/行为）、改动文件、
> 实施步骤、验收标准、禁止事项。执行前必须通读第 0 节守则。

---

## 0. 执行守则（每项开工前重读）

1. **回归红线**：每完成一项，以下三条必须全部通过才允许 commit：
   ```bash
   python -m pytest -q                    # 单元 + 端到端
   paperagent demo                        # 离线全链路
   python -c "import paperagent; import paperagent_cloud"   # 双包可导入（M0-1 之后）
   ```
2. **范围纪律**：只修改该项"改动文件"清单中列出的文件。发现必须连带修改的其它文件时，
   先补充本计划该项的文件范围与理由，再修改；提交时记录原因。未列完整文件范围的条目，先完成调用点清单。
3. **兼容原则**：不批量迁移历史用户数据；旧路径/旧键/旧 API 保留一个版本窗口；
   数据结构变更只加字段（带默认值），不删除、不改名已有字段。
4. **提交粒度**：一项一个 commit，标题格式 `[M0-1] 抽取共享内核，消灭 original/ 副本`。
5. **依赖顺序**（严格遵守，其余项之间可并行）：
   ```
   M0-1 → M0-2 → M0-3
   M1-4..11 在 M0-3 后开展；M1-9 依赖 M1-8；M1-8 接入预算依赖 M1-6
   M0-3 → M2-13（绑定及同步基础设施）→ M2-12（生命周期同步）
   M2-14、M2-15 依赖 M0-1 和 M0-3；M3-17 依赖 M2-13
   M3-16 可在 M0-3 后独立开展
   #18 依赖 M0-1；#19 基础语料随时，外环用例依赖 #18；#20 不强依赖 MCP
   ```
6. **探查优先**：标注"先读"的步骤，动手前必须先读完该文件，禁止凭本文档想象其实现。

---

## M0-1 抽取共享内核，消灭 original/ 副本

**代码核对与实施约束（2026-10-30）**：核对结果：云端 LLM 与本地实现字节一致；original 下 12 个源文件中 5 个字节一致，7 个有差异（含导入路径差异），实施时逐个审查，不能将字节差异直接当作功能分叉。范围补齐两包元数据、所有旧导入方和安装测试。

**M0-1 实际文件范围**：`backend/pyproject.toml`、`backend/src/paperagent_cloud/app.py`、`research/original_agent.py`、删除 `backend/src/paperagent_cloud/original/` 与 `llm.py`、`src/paperagent/cloud/app.py`、新增 `tests/test_shared_kernel.py`；安装说明与执行记录写入 `docs/m0_1_verification.md`。这些变更用于依赖声明、共享导入、旧入口兼容和独立安装验证。M0-2 的研究技能库保持不变。

**现状证据**：
- `backend/src/paperagent_cloud/original/` 复制了 `agent/llm_agent.py`、`agent/prompts.py`、
  `domain/manuscript.py`、`domain/paper_review.py`、`domain/project_dossier.py` 等本地模块；
- `backend/src/paperagent_cloud/llm.py` 与 `src/paperagent/harness/llm.py` 同构，读同名环境变量
  `PAPERAGENT_LLM_BASE_URL / _API_KEY / _MODEL`；
- `src/paperagent/cloud/app.py` 用 `sys.path.insert` hack 引 backend，属于反向依赖。

**目标**：单一事实来源 = `paperagent` 包本身。`paperagent_cloud` 成为 `paperagent` 的依赖方，
不再持有任何研究语义代码副本。

**步骤**：
1. 修改已有 `backend/pyproject.toml`，声明与本地包版本匹配的 `paperagent` 依赖。开发安装使用 `python -m pip install -e . -e ./backend`；不把未定义的 `${PROJECT_ROOT}` 或相对 file URL 写入发行元数据。验收另含干净环境中的双 wheel 安装与导入。
2. 全局搜索 `backend/src/paperagent_cloud/original/` 与 `backend/src/paperagent_cloud/llm.py`
   的所有 import 点，逐个改为：
   - `paperagent_cloud.original.agent.llm_agent` → `paperagent.agent.llm_agent`
   - `paperagent_cloud.original.domain.manuscript` → `paperagent.domain.manuscript`（依此类推）
   - `paperagent_cloud.llm` → `paperagent.harness.llm`
3. **先读** `backend/src/paperagent_cloud/original/` 下每个文件，与本地同名模块 diff：
   - 若完全一致 → 直接删除，改 import；
   - 若有分叉（云版本改动）→ 把云端特有的改动以最小 diff 合并回 `paperagent` 对应模块
     （加参数或子类，不允许第二份实现），再删除。
4. 保留 `src/paperagent/cloud/` 旧导入入口一个兼容窗口，删除 sys.path hack，转发到正常安装的云包；本地核心不能强制依赖云包，云包缺失时仅旧云入口给出安装提示。当前 `server.py` 未发现该 shim 导入，不预设它是引用方。
5. 跑守则三条回归。

**验收**：
```bash
rg -n "paperagent_cloud\.original" backend/src --glob "*.py"   # 旧模块导入零命中；original_agent 名称不在禁止范围
grep -rn "sys.path.insert" src/ backend/src/                                # 零命中
python -m pytest -q && paperagent demo
```

**禁止**：借机重命名 `paperagent` 包；借机"顺手优化"被合并模块的实现。

---

## M0-2 Skill 库三处归一

**代码核对与实施约束（2026-10-30）**：核对结果：两份 loader 字节一致，library 的 82 个文件全部字节一致。应直接复用共享 loader；catalog/design/capabilities 与本地 workflows 的重叠也须检查，不能仅搬 YAML。补齐两包 package-data，验证 wheel 中 catalog、别名与引用资源可读取。技能市场按 id 集合与资源解析验收，不只比数量。

**M0-2 实际文件范围**：共享 `skills/skill_catalog.yaml`、`workflows/catalog.py`；云端 research 的 loader/catalog/design 兼容转发、删除云端 library 和旧 catalog YAML；两包 pyproject.toml；`scripts/install_project_skills_v2.py`（修正生成目的地）；新增 `tests/test_shared_skills.py`、验证记录。云端 legacy_aliases.json 暂留一个兼容窗口，但运行时统一读取共享映射。保留全部旧 Python 导入入口。

**现状证据**：skill 物理副本存在于三处：
- `src/paperagent/skills/library/`（系统共享库）
- `<workspace_root>/skills`、`<workspace_root>/projects/<id>/skills`（用户扩展，保留）
- `backend/src/paperagent_cloud/research/library/` + `skill_catalog.yaml` + `legacy_aliases.json`（云端副本）

**步骤**：
1. **先读** `backend/src/paperagent_cloud/research/loader.py` 与 `src/paperagent/skills/`
   的加载器，确认两边的目录约定与 catalog 格式差异。
2. 云端 `research/library/` 若与本地 `skills/library/` 内容有分叉：以本地为基准合并
   （本地缺的 skill 移入本地库），云端只保留 `loader.py` 中纯编排逻辑。
3. `skill_catalog.yaml` 移入 `src/paperagent/skills/` 随包分发；云端 loader 改从
   `paperagent.skills` 读取。
4. 本地已有 `skills/legacy_aliases.json`。核对两份映射后统一读取本地资源，旧云端读取接口保留兼容窗口；淘汰条件写在文档或 Python 注释中，不在 JSON 文件内添加注释。
5. 加一条 pytest：断言 skill 定义文件在仓库中只存在一个物理位置
   （按内置 YAML 的 `id` 检查生产加载路径；排除 `skills包/`、`github/`、导出快照与 build 目录，保留用户覆盖机制）。

**验收**：该去重测试通过；`paperagent serve` 中技能市场显示数量不变。

---

## M0-3 API 分层：/api/v1 与 /internal

**代码核对与实施约束（2026-10-30）**：范围补齐云端 app 路由和 frontend API 客户端：本地 server 版本化不会自动产生云端 /api/v1/spaces 路径。两服务分别挂载兼容路由，不能把已有 /api 路径再次前缀拼成 /api/v1/api。保持鉴权、状态码和 SSE；/internal 端点逐条分类并保留必要旧路径兼容，不能顺便暴露调试状态。

**M0-3 实际文件范围与边界**：新增 `api/control.py`（原服务的运行状态及业务处理，保持行为）、`api/v1/` 领域路由工厂、`api/compat.py`；精简 `server.py` 为组装与兼容导出；原 `*_api.py` 保留兼容入口。云端 app 同时挂载版本化和旧路径。前端调用、相关测试、README 与验证记录同步更新。上下文预览 `/projects/{id}/context` 定为内部诊断，保留旧 `/api` 别名；其他已被 UI 使用的配置、任务、SSE 端点保持稳定公开 API。桌面 `/local/*` 是另一个有来源校验的本机协议，不改其路径。业务逻辑不借此重写。范围另含 domain/project_assessment.py、domain/paper_review.py 的返回链接前缀：客户端会跟随这些链接，必须一起迁移；字段结构不变。

**现状证据**：`src/paperagent/server.py` 当前有 73 条直接 `@app.*` 装饰器，另有注册模块路由；并非全部路由均在单文件中。路由主要使用 `/api/*`。

**步骤**：
1. 新建 `src/paperagent/api/v1/` 包，按领域拆 router：
   `project.py`（project_api 的路由）、`lifecycle.py`、`manuscript.py`、`review.py`、
   `research.py`、`connections.py`、`conversation.py`、`runtime.py`（today/tasks/deadlines/
   radar/notifications/automations/scheduler）。
   现有 `*_api.py` 文件里的 `register_*_routes(app, ...)` 改为返回 `APIRouter`。
2. `server.py` 只保留：app 组装、SSE、静态文件、CORS，以及两次挂载：
   ```python
   app.include_router(v1_router, prefix="/api/v1")      # 稳定面，语义化版本承诺
   app.include_router(v1_router, prefix="/api")         # 旧路径，兼容窗口，响应加
                                                        # Deprecation 头
   ```
   内部实现细节端点（调试、诊断、临时状态）移到 `/internal/*`，不承诺兼容。
3. **先读** `src/paperagent/web/`（含 `v6/`）与 `frontend/` 中全部 fetch/axios 调用点，
   统一改到 `/api/v1`。
4. 更新 README 的 API 表格；`tests/` 中涉及路径的断言同步更新。

**验收**：`paperagent serve` 后 `curl 127.0.0.1:8000/openapi.json` 显示双前缀；
前端全功能手工点一遍（对话、证据、技能市场、生命周期入口）无 404。

**禁止**：趁机改任何端点的请求/响应结构（纯路径搬迁）。

---

## M1-4 证据预览按 goal 相关性选取

**代码核对与实施约束（2026-10-30）**：关联 reference 标题需要显式索引或传参，原二参数函数不能凭空获得标题。排序稳定且零命中保留原顺序；预算裁剪当前从列表头 pop(0)，相关性降序后会先删最佳证据，必须同时修改 context_budget.py 为移除最低分项并补联动测试。

**现状证据**：`src/paperagent/harness/context_assembler.py` 约 123–125 行：
`evidence_preview` 取 `evidence[-10:]`、`quote[:80]`——纯时间序，与当前任务无关。

**改动文件**：`context_assembler.py`、`context_budget.py`、新增 `tests/test_evidence_relevance.py`、执行记录。预览的 relevance_score 仅在有命中时作为新增字段提供；无命中保持旧形状和顺序，不写回证据记录。

**步骤**：
1. 新增函数：
   ```python
   def _evidence_relevance(evidence, goal_tokens: set[str]) -> int:
       # 复用 paperagent.tasks.memory_manager.tokens()
       # 命中字段：evidence.quote + 关联 reference 的 title（若有）
   ```
2. 实际在 `assemble_assistant_state` 构造预览处实现（`assemble_observation` 是包装层），使用续接解析后的 `selection_goal`：score = 命中 token 数，按 (score 降序, 添加时间降序) 排序取前 10；
   score 全为 0 时回退现行为（最近 10 条）。
3. 单测：构造 20 张证据卡，其中 5 张含目标主题词，断言这 5 张必进 preview 前 10。

**验收**：单测通过；`paperagent demo` 输出不变（demo 证据少，回退路径覆盖）。

---

## 本轮实施说明（2026-10-31，M1-5 至 M1-11）

M1-5 至 M1-11 已完成，最终完整回归 398 passed、1 skipped；离线 demo、双包导入、独立 wheel 安装与无 MCP extras 启动检查通过。执行记录见 `docs/m1_5_11_verification.md`。此处仅记录当时 M1 批次；后续阶段进度见下文。
连带范围补充：M1-7 的用户采纳/撤回在 `api/v1/project.py`、`api/v1/manuscript.py` 同步关联记忆；M1-8 的服务接入点已随 M0-3 移至 `api/control.py`；M1-10 增加 `harness/context_assembler.py` 工作区传递和 `server.py`、`cli.py` 关闭连接。各项新增定向回归测试，已有技能上下文断言适配分段预算，并验证裁剪诊断；不改变技能库内容。

## M1-5 记忆召回打分升级（IDF 权重）

**代码核对与实施约束（2026-10-30）**：现有 importance 按升序参与排序，保留该方向和 updated_at 的稳定次序。IDF 的 N/df 仅从通过状态、过期及 project/task scope 过滤后的本层候选计算，不能混入其他项目。200 条是规划阈值，不是已验证的性能瓶颈。

**现状证据**：`src/paperagent/tasks/memory_manager.py` `relevant()` L105：
`score = len(query & tokens(...))`——无权重，常见词（"研究/方法/the"）与主题词同分。

**改动文件**：`memory_manager.py`、单测。

**步骤**：
1. 打分改为 IDF 加权：对当前 layer 的候选集先统计每个 token 的文档频率 df，
   `score = Σ_{t∈命中} log(1 + N/(1+df(t)))`。
2. 其余逻辑不动：pinned/importance 排序、每层限额（`LAYER_LIMITS`）、过期过滤、
   替换链剔除全部保留。
3. 单测：候选记忆里"方法"出现在 80% 条目、"贝叶斯网络"只出现在 2 条，
   goal 含两者时，含"贝叶斯网络"者排序靠前。
4. **决策记录**：FTS5/向量检索推迟——当前 working/project/user/episodic 各 8 条、prospective 为 10 条、单项目记忆量级下收益不成立；
   触发条件：单层活跃记忆 > 200 条时再引入 SQLite FTS5。

---

## M1-6 上下文预算分段

**代码核对与实施约束（2026-10-30）**：比例之和为 0.95，但余下 5% 不能保证容纳所有保护字段和 ACTION_PROTOCOL；先计算保护内容与协议/诊断开销，再对可裁剪内容分配上限。project_memory 只能裁剪现有可重建库存字段；memories 保护项不得按分段比例硬删。把 recent_tool_calls、last_result 以及未分段字段明确计入全局预算。

**现状证据**：`src/paperagent/harness/context_budget.py` `fit_context()` 单池 24000，
逐级驱逐（workflow→playbook→recommendations→connector/evidence/references→memories→
messages→last_result→project_memory），无分段上限，单类可占满。

**改动文件**：`context_budget.py`、单测。

**步骤**：
1. 定义分段占比（默认值，进 product.json 后可配，见 M1-8）：
   ```python
   SECTION_RATIOS = {
       "workflow": 0.12, "skills": 0.08, "memories": 0.15,
       "evidence_preview": 0.10, "saved_references": 0.07,
       "recent_messages": 0.28, "project_memory": 0.12, "connector_state": 0.03,
       # 剩余为保护性字段（当前请求、决定、checkpoint）的保留区
   }
   ```
实际分配先扣除保护状态、工具目录、协议与诊断开销，再对可分配预算应用比例；上表的 5% 不能作为保护字段硬上限。

2. 在现有全局驱逐循环**之前**，先按各段上限裁剪（裁剪方式沿用各段现有的
   `read_context` 回读提示模式，不静默丢）；全局循环保留作为最后防线。
3. `context_diagnostics` 增加 `per_section` 字段：各段估算 token 与上限。
4. 单测：构造 evidence_preview 超大（正常会挤爆全局）的状态，断言其余段不受影响、
   诊断正确报告；保护字段超限仍抛 `ContextBudgetExceeded`（现行为不变）。

---

## M1-7 记忆信任级字段

**代码核对与实施约束（2026-10-30）**：范围增加 context_budget.py、server.py 的记忆 API 与相应采纳 API。confirmation 是现有独立维度，不能替代 trust。采纳目前修改领域记录，不自动生成 MemoryRecord；只有明确建立来源 ref 的记忆才提升 trust，需幂等避免重复。模型自行替换已采纳内容不得继承 user_adopted：拒绝替换或创建未采纳候选，只有用户确认才可保留该等级。

**改动文件**：`src/paperagent/domain/entities.py`（MemoryRecord）、
`memory_manager.py`、采纳动作所在 API、`context_assembler.py`。

**步骤**：
1. `MemoryRecord` 加字段：
   ```python
   trust: Literal["user_adopted", "distilled_candidate", "model_transient"] = "model_transient"
   ```
   （只加不改，历史记录自动填充默认值。）
2. `remember()` 加关键字参数 `trust`，默认 `model_transient`；用户确认的 `replace()` 继承被替换记录 trust；模型替换不得自动继承已采纳等级。
3. **先读** manuscript/project API 中用户"采纳"动作的实现（README 提及"助手建议不会
   覆盖作者已采纳记录"），在该动作处写入 `trust="user_adopted"`。
4. `relevant()` 排序键升级：`(not pinned, trust_rank, -score, importance)`，
   其中 `user_adopted=0 < distilled_candidate=1 < model_transient=2`。
5. `fit_context` 的记忆驱逐顺序保持"从尾部、跳过 pinned/decision/preference"，
   并增加规则：`user_adopted` 与 pinned/decision/preference 同级，不自动驱逐；保护内容超预算继续抛错。不得把“最后驱逐”解释为可删除保护内容。
6. 单测：预算紧张先驱逐 model_transient，user_adopted 保留；保护内容本身超限时显式报错。

---

## M1-8 product.json 外置产品配置

**代码核对与实施约束（2026-10-30）**：范围增加 harness/context_budget.py、harness/tool_executor.py 和 pyproject.toml package-data。禁用工具不仅从 describe_tools 隐藏，还须在执行入口拒绝。配置应校验预算及比例；保留 LLMClient 显式构造参数优先，并保留 anthropic 协议支持。product.json 不保存密钥。

**改动文件**：新增 `src/paperagent/product.json`、`src/paperagent/product_config.py`；
接入点 `registry.py`、`harness/llm.py`、`server.py`。

**步骤**：
1. `product.json` 初版结构（保持 <200 行）：
   ```json
   {
     "product": "PaperAgent",
     "models": {
       "primary": {"protocol": "openai", "base_url": "", "model": ""},
       "fast": {"protocol": "openai", "base_url": "", "model": ""}
     },
     "tools": {"disabled": []},
     "features": {"radar_cloud_notify": false},
     "budget": {"context_input_tokens": 24000, "section_ratios": {}}
   }
   ```
2. `product_config.py`：`load()` → 合并优先级 **环境变量 > product.json > 代码默认值**
   （现有 `PAPERAGENT_*` 环境变量全部继续生效且优先）。
3. 接入：`describe_tools()` 过滤 `tools.disabled`；`llm.py` 默认值来自 `models.primary`；
   `fit_context` 预算与分段比例从 `budget` 读。
4. 单测：配置禁用某工具后 `describe_tools()` 不含它；环境变量覆盖 product.json 值。

---

## M1-9 双档模型路由（fast / primary）

**代码核对与实施约束（2026-10-30）**：fast 的协议、端点、模型与凭据来源应整体解析，不能向不同端点盲目发送 primary 密钥。未配置 fast 整体回退 primary；半配置明确报错。tier 环境覆盖需单独命名，避免现有 primary 环境变量把 fast model 覆盖掉。

**依赖**：M1-8。

**改动文件**：`harness/llm.py`、调用点若干。

**步骤**：
1. `harness/llm.py` 增加：
   ```python
   @classmethod
   def for_tier(cls, tier: Literal["fast", "primary"]) -> "LLMClient":
       # 读 product.json models[tier]；fast 未配置时返回 primary 客户端
   ```
2. **先读**以下调用点，把"压缩/摘要/打分"类路径切到 `for_tier("fast")`：
   - 当前 `conversation.py` 只存储 summary/checkpoint，没有 LLM 摘要调用；本项不虚构调用点，也不新增摘要功能。
   - Radar 的 advisory relevance（`quality/evaluators.py` 中模型参与处，若当前纯规则则
     预留接口）；
   - 后续 M2/L2 蒸馏统一走 fast。
   决策循环（`agent/llm_agent.py`）与写作/评审保持 primary。
3. 单测直接验证 tier 客户端请求参数与缺省回退；实际摘要或蒸馏调用点未来出现时再补集成测试。当前 Radar 默认是规则 evaluator，保持不调用模型。

---

## M1-10 MCP 客户端接入

**代码核对与实施约束（2026-10-30）**：补充 MCP 客户端生命周期与关闭、异步 SDK 到同步 executor 的边界、注册表查找及输入 schema 校验。describe_tools 当前不接 workspace，需增加显式上下文或注入注册表，不在每次观察组装时重复拉起进程。验证未安装 extras 时仍可启动、工具 ID 无碰撞、超时关闭子进程。实施时已核对官方 SDK v2 文档与本机 2.1.1 签名；使用 params=PaginatedRequestParams 分页。

**改动文件**：`pyproject.toml`（extras）、新增 `src/paperagent/tools/mcp_bridge.py`、
`tools/registry.py`、`harness/tool_executor.py`。

**步骤**：
1. `pyproject.toml` 加 extras：`mcp = ["mcp>=2.1,<3", "jsonschema>=4.23"]`（官方 Python SDK v2，支持 Python >=3.10）。
2. `mcp_bridge.py`：
   - 配置文件 `<workspace_root>/.paperagent/mcp.json`：
     ```json
     {"servers": {"arxiv": {"command": "...", "args": [], "env": {}, "allow_tools": {"search": "network_read"}, "timeout_s": 30}}}
     ```
   - 懒启动 stdio client；`discover(server)` → 把远端工具转为
     `ToolDescriptor(tool_id="mcp_" + server.encode().hex() + "_" + name.encode().hex(), permission=本地白名单指定权限, timeout_s=30)`；
   - `call(tool_id, args)` → 带超时调用，返回结构与现有 ToolResult 对齐；
   - **失败隔离**：某 server 启动失败只记录日志，其工具不出现在注册表，进程不崩；
     未配置文件时行为与现状完全一致。
3. `registry.py`：`describe_tools()` 合并 MCP 发现的工具；`ToolExecutor` 分派时
   `tool_id.startswith("mcp_")` 走 bridge，provenance 记录 server 名与耗时。
4. 权限映射：首版由本地配置显式允许 server/tool 白名单，未列入者不注册；远端元数据仅作提示，不能通过默认映射将写操作伪装为 `network_read`。白名单工具按本地审核结果赋权，并在执行端再次校验。
5. 单测：用 fake stdio server（测试内起一个 echo MCP）验证 discover + call + 超时。

---

## M1-11 凭据入系统凭据库

**代码核对与实施约束（2026-10-30）**：范围补齐 pyproject.toml、harness/llm.py 与测试。secrets list 使用应用维护的键名目录，不假设 keyring 支持枚举；禁止返回值。一次性提示不得输出密钥。

**改动文件**：新增 `src/paperagent/secrets.py`、`env.py`、`cli.py`、`.env.example`。

**步骤**：
1. `secrets.py`：
   ```python
   def get_secret(name: str) -> str | None:
       # 1) keyring.get_password("paperagent", name)；2) 回退 os.environ[f"PAPERAGENT_{name}"]；此凭据专用优先级是 M1-8 通用配置优先级的明确例外
   ```
   依赖 `keyring`（加入主依赖；无桌面环境的 Linux 上 keyring 可能不可用，异常时回退 env 并告警）。
2. `env.py` / `harness/llm.py`：API key 一律经 `secrets.get_secret("LLM_API_KEY")`；
   base_url/model 等非敏感项仍走环境变量/product.json。
3. CLI：`paperagent secrets set LLM_API_KEY`（隐藏输入，写 keyring）、`secrets list`（只列名字）。
4. `.env.example` 更新说明：推荐 keyring，`.env` 保留作回退。
5. 启动时若检测到 `.env` 中存在明文 key，打印一次性迁移提示（不自动写 keyring）。
6. 验收：删除 `.env` 中的 key、`paperagent secrets set` 后 `paperagent serve` 正常；
   测试使用隔离环境和测试凭据；不读取输出或自动删除用户真实 `.env` 密钥。

---

## M2 本轮实施范围（2026-10-31）

已按 M2-13 → M2-12 → M2-14 → M2-15 完成。最终回归 **415 passed、1 skipped**；双包 wheel、独立安装、离线 demo 与浏览器验证通过。验收记录 `docs/m2_verification.md`。
范围补齐：云端新增 `sync_api.py`（绑定、事件 API）、`usage.py`（原子配额）；本地新增 `tasks/cloud_sync.py` 和 `api/v1/sync.py`，接线 scheduler、v1 路由以及 V6 app.js 设置/历史。生命周期模块增加事件源、服务确认缓存和投影恢复。LLM 增加可选请求级计量钩子，兼容 chat_json 返回值；云端两个实际模型入口接入。新增专项测试与执行模式文档，`.env.example` 补充可选同步配置说明。绑定放实际项目目录 meta.json，包含 project_id 及规范化本地路径，绝不写工作区根目录；凭据不放 meta.json。
不存在的云端文件上传和 Radar 通知不会伪造接入；存量配额/雷达计量接口独立验证，待对应能力出现时接入。此处仅记录当时 M2 批次；M3 进度见下文。

## M2-12 生命周期事件上云（append-only）

**代码核对与实施约束（2026-10-30）**：先完成 M2-13 的受控同步传输。本地当前是每项目 lifecycle.json，包含 focus_stages/status/source/history，不可只保留 stage。事件新增完整必要字段；按项目惰性兼容旧快照，不批量改历史文件。保留旧 API 和读路径，定义快照/事件/待发队列崩溃恢复；schema 迁移可重复执行。幂等键相同但内容不同返回冲突。

**依赖**：M0-1。**改动文件**：`backend/.../app.py`（schema+API）、
`src/paperagent/domain/lifecycle.py`、同步任务、前端生命周期面板。

**步骤**：
1. 云端 schema（沿用现有 SQLite 风格）：
   ```sql
   ALTER TABLE spaces ADD COLUMN lifecycle_type TEXT;
   ALTER TABLE spaces ADD COLUMN stage TEXT;   -- 冗余当前值，由事件推导维护
   CREATE TABLE lifecycle_events(
     id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id),
     lifecycle_type TEXT, stage_from TEXT, stage_to TEXT NOT NULL,
     reason TEXT, actor TEXT, idempotency_key TEXT NOT NULL, server_seq INTEGER NOT NULL, created INTEGER, UNIQUE(space_id,idempotency_key), UNIQUE(space_id,server_seq));
   ```
2. API：`POST /api/v1/spaces/{id}/lifecycle/events`，body 含 `stage_to/reason/
   idempotency_key`；命中 idempotency_key 返回既有事件（200 而非重复插入）；
   `GET .../lifecycle/events` 按 server_seq 升序分页返回（客户端 created 仅展示）。**禁止提供 PATCH/UPDATE**——阶段历史
   不可改写。
3. 本地 `LifecycleService`：在项目目录新增 `lifecycle_events.jsonl` 事件源，保留兼容快照 lifecycle.json + 未推送
   队列（离线可推进，联网后按 idempotency_key 幂等推送）；当前 stage = 事件流末值。
4. 冲突语义：事件全量保留、不覆盖历史；服务器为每个 space 分配单调序号，按该序号投影当前值，客户端时间只用于展示。同序、时钟偏差不能影响收敛；事件与 stage 投影同事务更新。此投影仍是后接收事件优先，不宣称自动解决业务冲突；前端展示冲突历史。
5. 测试：模拟双端各推两条 + 一条重复 key，断言合并后事件流确定、无重复、stage 正确；
   断网推进阶段 → 恢复后队列自动清空。

---

## M2-13 workspace↔space 显式绑定 + 同步开关

**代码核对与实施约束（2026-10-30）**：当前未发现可直接复用的通用 workspace-space 同步客户端，需新增而非仅“接入”。项目与 space 的粒度先固定：绑定记录放在实际项目目录并包含 project_id，不能让一个根目录绑定混淆多个项目。挑战必须鉴权、一次性消费且校验设备；所有后台同步上传在网络调用前检查绑定与开关。local_only 不禁止用户主动的 LLM/文献查询，否则与现有产品冲突。

**改动文件**：云端 app.py、本地 workspace 元数据、同步客户端、前端设置页。

**步骤**：
1. 云端：`POST /api/v1/spaces/{id}/bind_challenge` → 返回 `{challenge, expires_in:600}`；
   `POST /api/v1/spaces/{id}/bind_confirm`（device_id + challenge）→ 记录绑定。
2. 本地：实际项目目录 `projects/<project_id>/meta.json`：`{"space_id": "...", "device_id": "...", "bound_at": ...}`。
   同步客户端启动时**必须**校验该文件存在且 space_id 匹配，否则拒绝同步并要求用户显式
   走绑定流程（UI 弹窗确认——防绑错目录导致未发表数据上传）。
3. per-space 同步开关：spaces 表加 `sync_mode TEXT DEFAULT 'local_only'`
   （`local_only` | `cloud_sync`）；仅 `cloud_sync` 空间参与上传；前端设置页提供切换，
   切到 cloud_sync 时二次确认提示。
4. 测试：未绑定目录发起同步 → 拒绝；local_only 空间任何上传路径均不出网（可用
   monkeypatch 网络层断言零调用）。

---

## M2-14 用量记账与配额

**代码核对与实施约束（2026-10-30）**：现有 LLMClient.chat_json 返回业务 JSON，不暴露计费用量；需加兼容的 usage 回调/元数据出口。未实现的云通知、上传计量点只能随对应能力接入，不得当作已有。usage_events 增加请求幂等字段及唯一约束；并发配额采用原子预留/结算，LLM 超限不能仅在付费调用完成后检查。storage_bytes 明确为当前存量，删除应释放，不能把累计上传量误当存储配额。

**改动文件**：云端 app.py（或新模块 usage.py）、计量点、测试。

**步骤**：
1. 表：
   ```sql
   CREATE TABLE usage_events(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
     kind TEXT NOT NULL, quantity REAL NOT NULL, meta TEXT, created INTEGER);
   CREATE TABLE quotas(user_id TEXT, kind TEXT, monthly_limit REAL, PRIMARY KEY(user_id, kind));
   ```
   kind 初版：`llm_input_tokens / llm_output_tokens / radar_runs / storage_bytes`。
2. 计量点：云端 LLM 代理转发处（读 response usage）；Radar 云端通知触发处；文件上传处。
   统一经 `record_usage(user_id, kind, qty)`，附 idempotency（请求级 key）防重。
3. `GET /api/v1/usage/summary?month=YYYY-MM` 返回分类合计；超 quota 时计量点返回 429
   （quota 行不存在 = 不限）。
4. 测试：注入计量 → 汇总正确；设置小 quota → 超限 429。

---

## M2-15 区分本地续接与云端任务状态

**核对证据**：本地 `conversation.py` 使用本地会话状态；云端 `app.py:advance_agent_task` 实际读取 `agent_context.research_json/steps` 构造 observation，并用文件摘要、device_id、workflow_id、skill_id 校验续接。该表目前并非纯展示锚点。

**修订目标**：本地会话以本地 checkpoint/事件日志为真相源；现有云端任务以云端任务及 operation 状态为真相源。两种执行模式显式区分，不把云端运行状态直接当作本地 checkpoint。

**改动文件**：云端 `app.py` 文档字符串、模式边界文档、相关本地/云端续接测试；仅在发现本地恢复误读云表时修改本地恢复代码。

**步骤与验收**：
1. 保留云端 advance 对研究上下文、步骤预算、设备和材料的校验，补充职责说明；不能仅为“唯一化”删除这些执行依赖。
2. 测试本地恢复无需云连接；云端恢复仍受身份、材料哈希、workflow/skill 和步骤限制约束。
3. 若未来要把所有执行统一迁往本地，应另列迁移规格，涵盖 advance/claim/result 协议、在途任务与前端兼容；不混入本项。

---

## M3 实施范围（2026-10-31）

M3-16、M3-17 已完成。最终回归 **430 passed、1 skipped**；双包独立安装、离线 demo、浏览器回归通过。验收记录 `docs/m3_verification.md`。本轮仅 M3-16、M3-17。范围补齐：`domain/entities.py`、`domain/store.py` 的意图稳定同步标识/版本/墓碑与内部写入边界；`workspace/workspace.py` 防止主动直调写稿；`radar/pipeline.py` 角色传递；新增云端 `proactive_api.py` 与本地 `tasks/proactive_sync.py`，共享意图冲突模型；`tasks/cloud_sync.py`、`api/v1/sync.py`、V6 设置接线独立授权开关。新增专项回归与浏览器用例。范围同时补齐 lifecycle/project_dossier/manuscript/research_links/paper_review/project_claims 的作者写入边界和 runtime 意图修改/删除 API。全局 profile 意图单独确认同步，项目意图按绑定隔离，不能把根目录所有意图隐式上传。日志见 `docs/m3_verification.md`。

## M3-16 主动链路权限降级

**代码核对与实施约束（2026-10-30）**：当前 proactive 直接调用工具函数并写 DomainStore，未统一经过 executor；只加 role 参数不会生效，必须覆盖直调链。禁止的是自主修改作者文件/领域决定；运行日志、watcher 游标、通知、缓存及待审批队列属于范围明确的内部持久化，否则禁止一切写盘会使主动链路自身失效。当前 gate 返回决策字符串，沿用这一契约。

**改动文件**：`quality/policies.py`、`agent/decision_loop.py`、`harness/tool_executor.py`、`tasks/scheduler.py`、`tasks/proactive.py`、相关直接调用路径与测试。

**步骤**：
1. **先读** `quality/policies.py` 中 PermissionGate 的实现与 action kind 集合（已知约定：
   `local_read / local_write / notify_in_app` 等）。
2. Gate 检查函数增加 `role: Literal["interactive", "proactive"]` 参数（默认 interactive，
   现行为不变）。
3. 调度链路（scheduler tick → ProactiveRuntime → radar/digest/watcher）调用处统一传
   `role="proactive"`。策略：proactive 允许 `local_read/network_read/notify_in_app`，
   **拒绝 `local_write`**（Gate 继续返回现有 `deny` 决策字符串，由调用方写 trace 并终止该任务；不要求不存在的 PermissionDenied 异常）。
4. 需要写盘的主动产物（如 digest 草稿）改为：写入操作队列（复用 agent_operations 模式）
   或降级为通知内嵌内容。
5. 测试：proactive run 尝试 `draft_render`（local_write）→ 拒绝且 trace 有记录；
   interactive 同调用 → 正常。

---

## M3-17 调度分工落位（intents 云同步 + 通知路由）

**代码核对与实施约束（2026-10-30）**：先落地 M2-13 的同步基础设施与开关约束。意图目前在本地 store；云表需要按用户隔离、版本/稳定冲突决胜与删除墓碑，防删除复活。补齐待发队列持久化、退避和批次上限；通知仅在显式允许云转发时上传。

**改动文件**：云端 app.py、`tasks/notification_service.py`、`tasks/standing_intent.py`、测试。

**步骤**：
1. 云端表 `standing_intents(id, user_id, payload TEXT, updated INTEGER)`；
   本地 DomainStore 的 intents 与之双向同步：推送本地变更、拉取远端，冲突用
   `updated` 时间戳 LWW，同时间按版本/来源/规范化内容稳定决胜；同 ID 删除墓碑终结，重建或跨范围编辑使用新 ID（**配置类数据 LWW 可接受**，与 lifecycle 的事件语义不同）。
2. `NotificationService` 增加云端投递通道：`POST /api/v1/notifications/relay`
   （带 idempotency key，目标设备可选）；投递失败**不重试阻塞**，落本地待发队列，
   下次 tick 补投。本地通知照常生成——云端只是转发层。
3. 断路测试：云端宕机 24h 模拟（mock 全部 HTTP 失败）→ tick 正常、命中照常落本地 store、
   通知进待发队列；云端恢复 → 队列按 key 幂等补投，无重复。

---

## 并行轨 #18 引文外环核验（DOI/元数据真实性）

**状态：已完成**；Crossref 匹配结果、缓存、保存/render/finish/CLI 接线及严格策略回归已落地。

**实施范围补齐（2026-11-01）**：新增 `kernel/external_source_check.py`，修改 schemas/references、workspace/workspace 与 render、tools/references、kernel/citation_integrity、agent/decision_loop、cli；测试和配置说明同步补齐。默认离线 unchecked，显式 `PAPERAGENT_REFERENCE_PROVIDER=crossref` 开启外部核验，`PAPERAGENT_REFERENCE_STRICT=1` 开启严格策略；缓存与结果绑定元数据指纹，保存不会信任导入记录自带的 verified。记录见 `docs/remaining_verification.md`。

**代码核对与实施约束（2026-10-30）**：补齐 schemas/references.py、workspace 引文持久化、导入/保存入口和 finish/CLI 校验接线。resolver 对 HTTP 错误会 raise，必须区分 404、429、超时及 provider 覆盖不足；离线 stub 生成 synthetic 元数据，不能作为核验真值。缓存键包括作者/年份/provider/策略版本，避免修改年份后复用旧 verified。Crossref 查无仅表示该 provider 查无，不等于文献虚构；标题搜索查无默认 warning，只有明确 DOI 查无且策略要求严格核验时阻断。年份差异允许解释线上/印刷版，作者缺失不得伪造 verified。

**依赖**：M0-1。**现状**：`tools/references.py` 已有 `reference_resolve`（Crossref provider），
`kernel/citation_integrity.py` 只做内环（草稿↔库↔证据卡），不核验库本身真伪。

**步骤**：
1. 新增 `kernel/external_source_check.py`：
   - 输入：references.jsonl 的条目集合；
   - 对每条：有 DOI → `reference_resolve(doi=...)`；无 DOI → `reference_resolve(title=...)`；
   - 比对：归一化标题 token Jaccard ≥ 0.85 且年份一致（±0）且第一作者姓氏一致 → `verified`；
     找到但字段不符 → `mismatch`；查无 → `not_found`；离线/超限 → `unchecked`。
2. 写回 reference 记录新字段 `verification = {status, checked_at, provider, diff?}`
   （只加字段）。结果缓存：`workspace/.verification_cache.json`，key = hash(归一化 DOI + title + authors + year + provider + 策略版本)，
   TTL 30 天，限速 1 req/s（离线桩 provider 返回 `unchecked`，绝不伪造通过）。
3. 触发点：① 文献保存/导入路径；② `draft_render` 与 kernel finish 检查之前批量跑。
4. `citation_integrity.py` 集成：被草稿引用且 DOI 明确查无、启用严格核验策略 → **error（阻断 finish）**；仅标题/provider 查无 → warning；
   `mismatch` → warning；`unchecked` 不罚。
5. 测试（对应 #19 语料的种子用例）：正确条目 → verified；fixture 中 DOI 改一位并明确返回 404 → not_found，严格核验策略下 finish 被拦；年份改错 → mismatch 警告。

---

## 并行轨 #19 验证器 mutation 测试语料

**状态：已完成**；默认离线执行，包含干净基线、DOI、quote、证据删除、年份 ±1 与数字 ±0.1；数字语义检测保留为明确能力缺口。

**实施范围**：`tests/mutations/` 的临时基线与参数化变异测试，fixture 网络替身；复用 demo PDF，真实元数据由确定性 fixture 提供。

**代码核对与实施约束（2026-10-30）**：真实 HTTP 全部替换为确定性 fixture，不调用生产离线 synthetic resolver 冒充核验结果。用临时工作区复制干净基线，不能变异用户 workspace；外环用例依赖 #18，数字语义检测暂不承诺。

**步骤**：
1. `tests/mutations/`：以 demo 工作区为干净基线，写程序化变异器：
   - DOI 单字符变异；引文 quote 单词替换；正文数值 ±0.1；删除证据卡但保留引用；
     参考文献年份 ±1。
2. 参数化 pytest：每个变异明确预期严重级：可验证 PDF 的 quote 不匹配为 error；删除证据卡当前是 warning；年份不匹配依 #18 为 warning；DOI 明确查无依 #18 为 error。正文数字 ±0.1 当前无通用检测能力，只有建立明确证据映射的用例才要求检测，其余列为能力缺口，不能用虚假断言宣称覆盖。定位断言采用当前报告支持的 ref/evidence 标识；行号需另增实现。干净基线零 error。
3. 该测试进默认 `pytest -q` 集合（离线、无网络、无模型）。

---

## 并行轨 #20 Radar 真实数据源

**状态：已完成**；可配置多源聚合、arXiv RSS、OpenAlex 日期/游标分页、离线源、故障隔离、覆盖窗口与独立限速已落地。默认兼容旧数据源；实际 arXiv RSS 探测通过。

**实施范围补齐（2026-11-01）**：新增 `radar/sources.py`，修改 radar/pipeline、tools/web 的有界 robots 读取、pyproject 默认网络测试排除；新增源协议/分页/故障隔离测试和配置说明。接口扩为 `fetch(topic, since, cursor)`，返回候选、下一页与覆盖窗口；通过 PAPERAGENT_RADAR_SOURCES 配置多源，默认保留既有 literature_search。RSS 地址以 arXiv 官方说明为准，OpenAlex 深分页使用可选 API key，不在日志或游标文件保存凭据。

**代码核对与实施约束（2026-10-30）**：现有 literature_search 已接 Semantic Scholar/OpenAlex，标题应理解为多源增量增强而非从无到有。接口需传入 intent/topic、时间游标和分页状态，不能只传 since。RSS 不是历史增量接口，需记录覆盖窗口；发布日期过滤不等同于更新时间增量。真实网络测试除标记外还需 pytest 默认排除配置，单独标记不会自动跳过。

**依赖**：建议在 M1-10 后（若 arXiv MCP 已接入，此处只补 OpenAlex 增量与期刊源）。

**步骤**：
1. 新增 `radar/sources.py`：`class RadarSource` 接口 `fetch(since) -> list[RawItem]`。
   实现：`arxiv_rss`（intent 主题→分类映射，读 经实施时核实的 HTTPS RSS 地址（本次未联网验证地址和服务政策））、
   `openalex_incremental`（`from_publication_date=since` + intent 关键词过滤）、
   `offline_stub`（测试）。
2. 接入 `radar/pipeline.py`：Source Fetch 阶段改为多源聚合（现有 literature_search 轮询
   降级为其中一个 source，保持兼容）；Normalize 之后的去重/硬过滤/规则匹配链路不动。
3. 限速与 robots：复用 `tools/web.py` 的 min_delay/robots 约定；每源独立限速。
4. 测试：offline_stub 源跑全管道；真实源测试标记 `@pytest.mark.network` 默认跳过。

---

## 触发式 backlog（不排期，写明触发条件）

| 项目 | 触发条件 | 到时第一步 |
|---|---|---|
| daemon 分离 + ACP 协议 | 第二个客户端（IDE/Zotero 插件/CLI）出现 | 读 agentclientprotocol.com 规范，SSE 层下加协议适配 |
| 遥测聚合表 | 付费用户 > 10 | 在 kernel 拦截/skill 调用统计点接本地事件队列 |
| 课题组 RBAC | 首个团队空间需求 | spaces 加 membership 表，三角色 Owner/Editor/Viewer |
| skills 市场版本化 | 外部技能贡献者出现 | git tag + catalog 版本字段（勿自建服务器） |
| OIDC/大学 SSO | 进校采购谈判 | 评估简单 SAML/OIDC 网关，替换自建登录入口 |
| SQLite FTS5 记忆检索 | 单层活跃记忆 > 200 条 | 见 M1-5 决策记录 |

## 永久不做

沙箱套件、Electron 重客户端、模型矩阵聚合、IM 通道、多部署变体。

---

## 附：全局回归清单（每项完成必跑）

```bash
python -m pytest -q                       # 含 mutation 语料（#19 落地后）
paperagent demo                           # 离线全链路
paperagent validate --project demo --draft draft.md   # 引用内核
paperagent serve --port 8000  # 在独立终端前台运行；退出后关闭服务            # 起服务后手工清单：
#   对话一轮 / 证据面板 / 技能市场 / 生命周期入口 / today+radar 页
python tests/verify_manuscript_ui.py      # 需 Playwright 时
```


## 初次核对边界（历史记录，2026-10-30）

初次核对仅静态阅读并修订此文档，当时没有运行回归或修改代码。后续实施与验证记录见各阶段 verification 文档；外部 SDK 和数据源的核实也记录在对应阶段。所有测试使用临时工作区，不迁移用户历史数据。目录未见 .git，保留修改前快照，不擅自初始化仓库。


## 后续混合控制面（2026-11-03）

用户随后批准的新架构单独记录在 [control_plane.md](control_plane.md)，实施计划和验证记录分别见 `superpowers/plans/2026-11-03-control-plane.md`、`control_plane_verification.md`。本轮增加云端 managed 任务/worker、本地受限执行器、账户隔离、显式记忆共享和主动监测；保留本清单已有优化与旧接口兼容，不将其描述为生产部署完成。
