# 科研生命周期 V1 与 Skill V2 落地记录

> 2026-11-03 清理更新：一次性 V2 安装源包和脚本已移出工程，当前定义在 `src/paperagent/skills/`，下文安装路径仅为历史记录。

> 后续前端连接已完成：文件选择、局部范围、投稿记录与产物下载见 [前端功能连接记录](frontend_function_connections.md)。本文件以下部分保留首轮实现记录。

日期：2026-10-28。以用户提供的《PaperAgent 科研生命周期与 Workflow 设计 V1》及《REORGANIZATION_REPORT.md》为本次实现依据。本文件取代旧文档中“8 个业务入口、34 个 Skill”的当前状态描述；旧文档保留为历史记录。

## 生命周期与入口

| 代码 | 当前 Workflow ID | 名称 |
| --- | --- | --- |
| G1 | `grant_application` | 项目申请 |
| G2 | `project_midterm_report` | 项目中期进展 |
| G3 | `project_final_report` | 项目结题 |
| P1 | `paper_writing` | 论文编写 |
| P2 | `venue_matching` | 投稿机会与期刊匹配 |
| P3 | `submission_tracking` | 投稿与追踪 |
| T1 | `thesis_proposal` | 论文开题 |
| T2 | `thesis_midterm_report` | 毕业论文中期 |
| T3 | `thesis_writing` | 毕业论文终稿编写 |
| T4 | `thesis_defense` | 盲审与答辩 |

`src/paperagent/workflows/design.py` 保存十份输入、可回访步骤、交付物、完成条件、用户确认点与特殊规则。API 接受 G1—T4 代码和 V2 手册中的完整代码别名。旧阶段 ID 和 `publication_management` 仍可续接，返回原 ID 及新的 `canonical_workflow_id`；不改写历史执行证据。原项目机会入口只作为兼容入口保留，常态机会发现归入 A2。

所有步骤可跳过、回访、回退；现实阶段继续独立保存在 `lifecycle.json`，写作或流程完成均不自动推进现实阶段。P2 从选题到返修阶段均可进入。普通交流、局部写作和独立 Skill 使用无需建立流程记录。

## 持久化与确认

每份交付记录保存在研究空间的 `workflow_cases/wf_*.json`，独立维护步骤状态、输入与交付文件、质量意见、作者决定、确认历史和修订号。文件固定到 SHA256，原始字节另保存在 `workflow_versions/`；文件变化使正式版本确认失效，并提示复核。暂停、恢复和重开修订不会重放工具操作。

完整流程自动登记附件中的实质确认点；大纲保留建议确认。作者必须先看到具体决定内容，再确认其指纹。核心决定或正式版本变更后旧确认不能用于新版本。局部流程可通过创建接口设置 `scope=partial` 和 `required_deliverables`，仅维护本次范围的交付。Agent 工具只能提出决定、更新步骤和登记交付／检查，不能代替作者批准、暂停、恢复或取消。

完成条件为：本次约定交付物已绑定版本、需要确认的决定均已确认、输入和输出未发生变化、已登记质量检查、无未解决 ERROR、当前正式版本经用户确认。WARNING 和 SUGGESTION 保留展示。确认本身不覆盖 ERROR。内容真实性和论证正确性依然需要实质审查，空问题清单不等于科学认证。

原申请书与项目中期／结题的确定性检查、模板适配、目标证据对照继续使用。`finish` 保存稿件，在恰有一个同类型活动交付记录时登记主文档和检查结果，仍等待作者审阅；它不等于整个业务流程已经完成。

前端研究空间新增“生命周期与交付流程”，支持选择入口、建立记录、回访步骤、登记交付文件、填写并确认决定、人工检查、正式版本确认、完成、暂停和恢复。原高级设置仍可使用。

## 跨生命周期复用

用户通过来源空间和草稿文件名显式建立 `research_links.json` 关系，复制固定版本到本空间 `reused_sources/` 并登记为资料；复用正文实际引用的文献，冲突 ID 报错。来源稿件变化后提示复核，保留本地原版本。原空间的审核、批准、现实阶段和交付状态不会转移。本机制可将同一研究关联到基金、论文和学位论文。

## 主动服务

A1 Research Radar、A2 Opportunity Radar、A3 Progress & Feedback Watcher 在 `tasks/proactive.py` 中由 Scheduler 调用，与十个 Workflow 分离。启用范围、主题／来源、间隔、游标及上次检查时间保存在现有 Watcher 中。研究空间可以启用、更新或停用；同空间同服务更新配置，不重复创建订阅。

- A1 复用文献 Radar、研究档案与关注主题，执行检索和去重推荐。
- A2 按配置的查询和至多五个来源页面发现申报、期刊与会议候选；保存来源快照、抽取结果及核对时间，按内容去重，只对新候选通知。抽取的资格、费用和日期作为待核对候选，不自动成为权威期限。
- A3 消费本空间的状态变化与反馈事件，按内容去重；截止日期提醒继续使用现有 Scheduler。

每个来源失败单独记录并在下一周期重试，调度器不因单个来源失败终止。后台停用时界面明确显示“配置已保存，启动调度器后生效”。用户主动启用之前不创建真实监控。

现有投稿状态机、邮件文本接入和领域记录继续提供 P3 的状态依据。邮件状态应用要求稿件编号，按研究空间和刊会匹配，歧义不自动更新。真实邮箱 OAuth／IMAP 账号适配器没有凭空创建；当前可使用本地邮件连接器或已有外部接入发送的事件。没有执行真实投稿或邮件发送，现有 PermissionGate 继续阻止未经授权的对外操作。

## Skill 安装结果

活动库仅为 `src/paperagent/skills/library/`：共 **35 个**原生 YAML Skill，23 个通用、12 个生命周期专用，39 项资源引用。六个学科方法按实际学科线索匹配；目录分类与来源分开。

按照手册将 `PaperAgent_Skills_V2/skills/library/` 安装到工程，保持扁平结构与 references 相对路径。全部声明资源已通过真实 Loader 读取校验。上游许可证、provenance 和 V2 外部来源登记完整保留；未执行附带的外部下载脚本，也未将适配稿称为上游官方原文。

| 已移除的活动 ID | 当前 ID |
| --- | --- |
| `grant_application_general`、`grant_writing` | `grant_application` |
| `journal_recommendation` | `journal_strategy` |
| `advisor_feedback` | `paper_revision` |
| `figure_review` | `scientific_figure` |
| `conference_submission` | `submission_preparation` |
| `english_academic_writing` | `academic_writing` |

完整旧库先做字节校验备份，存放于 `exports/skill_migrations/20261028T173207525785/library_before/`。该位置不在活动发现目录中。V2 输入包及其原始快照保留，不涉及 Codex 全局技能或其它工程。

`scripts/install_project_skills_v2.py` 提供可重复安装流程和带 SHA256 的安装记录。`skills/legacy_aliases.json` 保留七个旧读取入口与旧资源 ID 到新资源的兼容映射；别名不是额外可发现 Skill，不将空间私有精确 key 指向系统库。检查了当前两个工作区的活动对话与运行状态，未发现需要改写的旧 `skill_id`；历史日志保持原样。

`workflows/skill_catalog.yaml` 是运行时 core/support 路由配置；`docs/skills_v2/` 保存重组手册、迁移表和来源登记。运行时默认最多提供五个候选，流程推荐中的 core 最多三个、support 最多两个，完整目录仍可独立读取。生命周期隔离避免项目中期方法误入学位论文；显式读取仍可使用目录中的其它方法。

## API 与模型访问

| 接口／工具 | 用途 |
| --- | --- |
| `GET /api/lifecycles` | 三类生命周期、10 个契约、A1/A2/A3 和共用规则 |
| `POST /api/projects/{id}/workflows/preview` | 只读预览，含 core/support 推荐 |
| `GET/POST /api/projects/{id}/workflow-cases` | 读取或建立独立交付记录 |
| `GET/PATCH /api/projects/{id}/workflow-cases/{case}` | 状态、步骤、决定、输入、输出、质量、确认与完成 |
| `GET/POST /api/projects/{id}/research-links` | 显式关联并复用来源版本 |
| `GET/POST /api/projects/{id}/proactive` | 查看服务与调度状态，保存关注配置 |
| `DELETE /api/projects/{id}/proactive/{watcher}` | 停用本空间关注 |
| `workflow_case` | Agent 读取和提出流程记录更新 |
| `read_context(kind=workflow_contract)` | 分页读取完整契约和当前交付配置 |

状态修改支持 `expected_revision`；确认还要求当前内容指纹。完整流程方法按需进入模型上下文；预算紧张时延后可重建的 playbook 和支持推荐，保留用户请求、决定与实际要求。

`presentation_build` 现同时生成可编辑 PPTX、HTML 预览、讲稿、slides JSON 和来源清单。SVG 图表包含在 HTML 与配套 assets 中，PPTX 的图表插入和最终版式仍需检查，不声明自动视觉验收。

## 验证

基线：297 项通过、1 项跳过。本次回归：320 项通过、1 项跳过。新增验收涵盖十入口的跨阶段进入、跳过／回退／暂停／恢复、状态隔离、正式交付门禁、Agent 无权批准、版本变更、旧 Skill 资源读取、学科和生命周期路由、跨空间复用、主动服务去重与失败隔离。

`python tests/verify_lifecycle_v1_ui.py` 完成真实浏览器的 T2 决定填写／确认、交付文件绑定、检查、正式版本批准、完成、刷新续接、跨空间复用与 A2 配置，检查无前端异常及移动端横向溢出。截图位于 `exports/lifecycle-v1/desktop.png` 和 `mobile.png`。测试使用临时工作区、合成验收材料，无真实模型或对外提交。

已知边界：后台调度和外部来源需要部署环境实际开启／可达；账号型邮箱仍需接入授权适配器；科学有效性、真实申报资格和最终 Office 版式需要按实际材料核查。

打包验证：生成 `exports/lifecycle-v1/package/paperagent-0.1.0-py3-none-any.whl`，核对包含 35 个活动 Skill、生命周期路由及兼容映射。自动审批拒绝了临时 `build/` 目录清理，目录保留，不在运行时发现路径中。
