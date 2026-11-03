# research-skills 能力适配与分阶段实施

日期：2026-10-26。依据用户指定顺序，先增强文献证据与图表，再接科研演示与投稿产物，最后补专项申请评审。三个生命周期与八个可选 Workflow 保持不变；没有新建 Task 板块或插件安装系统。

## 1. 已落地范围

| 阶段 | Skill／资源 | 工程能力 | 当前边界 |
| --- | --- | --- | --- |
| 文献证据组织 | literature_review 新增 corpus／matrix；evidence_synthesis 复用矩阵指南 | Evidence 扩展方法、对象、发现、局限、主题、关系、阅读覆盖与可选来源哈希；literature_matrix 按页派生视图 | 标注不是已验证结论；来源哈希相同不代表原文支持；没有自动完整系统综述认证 |
| 科研图表 | scientific_figure、figure_review | figure_style 可选空间默认与单图覆盖；柱／折线／散点 SVG 的负值、坐标和有限数据校验；SVG 明确文字、字号、结构与安全内容检查 | 尚无完整多面板编辑器、栅格 OCR、自动重叠／裁切检查或视觉评分；Mermaid 仍是结构输出 |
| 科研演示 | scientific_presentation，叙事／交付资源 | presentation_build 生成离线 HTML、slides.json、讲稿、SVG 嵌入和来源 manifest | 尚不生成 PPTX；结构检查不等于生产环境自动版式验收或计时演练 |
| 投稿产物 | submission_preparation | submission_package 导出已有草稿 Markdown／DOCX、选择的 SVG 和图注、分发清单与 ZIP | 不生成不存在的声明或 Cover Letter；不连接或提交账号；刊会合规与视觉排版仍需复核 |
| 专项申请评审 | grant_review 的科学／可读性／资助类型指南；grant_writing 复用类型资源 | 原 PaperReviewInput 增加 review_kind、review_mode、review_profile；沿用同一报告存储、快照、来源、复核与采纳机制 | 按标准评审需提供 profile 和非 user_note 来源；不自动认证其官方有效性、不预测获批、不声称运行独立评审团 |

新增 5 个 Skill，全部共 34 个，20 通用、14 生命周期专用；共 23 个工具。新增 11 份方法资源，所有 Skill 共引用 34 份独立方法文件、40 项资源引用。新增能力仍可独立使用，不要求启用 Workflow。

## 2. 调用与续接

### 文献证据

原 save_evidence action 保持兼容，新增可选字段：source_coverage（full_text／partial／abstract_only／unknown）、relation（supports／contradicts／context）、themes、method、population、finding、limitations。原 quote、ref_id、page、section、supports、source_path 保留。提供来源路径时运行时按授权检查文件并计算 source_sha256；旧证据无哈希保持 untracked。

literature_matrix 接受 ref_ids、theme、offset、limit，可选 evidence_limit。它只读 references.jsonl、evidence.jsonl 和必要来源，展示 current／changed／unavailable／untracked。默认每篇预览五项证据，长引文预览截断并给原 read_context 指针。coverage=metadata_only 表示没有对应证据卡，不自动推断阅读状态。

### 图表

figure_style 的 get 不创建配置；set 保存空间默认，单图 style 可覆盖。样式不是调用前置要求。chart_generate 拒绝空数据、非有限数、未知类型与不支持尺寸；数值坐标覆盖负数，数字型折线 X 按实际间距绘制。SVG 输出名限制在 figures 目录，不允许穿越。

figure_quality_check 可检查传入 spec 或授权本地 SVG。若有 image_path，读取当前文件；结果 sha256 对应实际检查内容。expected_text、直接声明字号和 XML 检查，与 unavailable 的几何／视觉检查分开。字号以 SVG 用户单位报告，不冒充最终印刷点数。passed 仅表示已执行确定性检查没有错误。

### 科研演示

presentation_build 输入示例：

```json
{"title":"Research update","slides":[{"title":"Key finding","points":["State the measured result with its boundary."],"notes":"Explain the limitations.","sources":[{"kind":"draft","key":"paper.md","locator":"Results"}],"figure_id":"已保存的SVG图表ID"}]}
```

sources 使用原 ProjectSource（draft／material／evidence／user_note），由原 ProjectDossier.capture_source 获取来源快照；不用新记忆数据库。每次生成创建 exports/research/presentation_<id>，HTML 离线使用，JSON 可继续编辑，讲稿可展开。

### 投稿包

submission_package 接受 filename，可选 companions、figure_ids、requirement_sources。正文和附件必须是已有草稿；先运行现有引用完整性检查，未知引文等确定性错误阻止成包，警告保留。DOCX 复用原导出器，没有新增自动模板合规判断。

每次生成独立 submission_<id>。外部 ZIP 含正文、附件、SVG、图注及 package-manifest.json，只列分发文件和哈希；内部 manifest.json 保留来源快照及路径，留在空间内，不打入 ZIP。内部清单另记录 ZIP 哈希，避免归档改动被误认为版本未变。

research_output_check 接受 manifest_path，核对原始材料、当前草稿、证据标注及来源、图表内容和记录元数据、输出文件和 ZIP。只标记 needs_review，不修改旧产物或自动推进阶段。原始输出保留，需修改时创建新交付。

### 专项申请评审

save_paper_review 增加 review_kind=grant；review_mode 可选 scientific／readability／criteria；review_profile 记录本次指南或标准。criteria 模式必须提供 profile 与至少一个 draft／material／evidence 来源，readability 不给科学优劣评分。该要求仅保证结构上提供依据，不证明来源官方有效或标准适用。

仍写入原 paper_reviews.json 和不可变报告目录。稿件和指南变化沿用原 needs_review 与复核机制；旧报告缺新增字段默认按论文科学评审处理。项目空间也可在原有界面查看申请报告。角色名称 manuscript 表示被审阅主稿，兼容原数据协议，不新增申请评审状态机。

## 3. 界面与接口

研究空间增加可折叠的“文献证据与科研产物”，按需读取、分页展示矩阵与产物。可以刷新、下载和查看来源变化。没有编辑完整流程的表单要求。

- GET /api/projects/{project_id}/literature-matrix：分页派生证据视图。
- GET /api/projects/{project_id}/research-outputs：分页列出本地交付及当前性。
- GET /api/projects/{project_id}/research-outputs/{output_id}/file?name=...：空间内受限附件下载；HTML 也按附件返回。
- 原 paper-reviews 接口和 Agent action 复用新增申请评审字段。

产物生成经原 ToolExecutor、权限、预算和工具调用记录执行；只读查看不创建 Task、运行或新资料台账。

## 4. 当前 Workflow 的条件推荐

| Workflow | 当前推荐 Skill |
| --- | --- |
| `paper_writing` | `scientific_figure`、`figure_review`、`citation_verification`、`research_design`、`literature_review`、`evidence_synthesis`、`methodology`、`experiment_design`、`academic_writing`、`english_academic_writing`、`paper_revision`、`paper_review` |
| `publication_management` | `submission_preparation`、`research_radar`、`journal_recommendation`、`journal_strategy`、`conference_submission`、`submission_tracking`、`paper_review`、`reviewer_response`、`paper_revision`、`english_academic_writing` |
| `project_opportunity_discovery` | `research_radar`、`grant_application`、`grant_writing`、`research_design`、`grant_application_general` |
| `grant_application` | `grant_review`、`scientific_figure`、`grant_application_general`、`research_design`、`academic_writing` |
| `project_midterm_report` | `scientific_figure`、`grant_midterm_report`、`project_evidence_review`、`academic_writing` |
| `project_final_report` | `scientific_presentation`、`project_evidence_review`、`academic_writing` |
| `thesis_writing` | `scientific_figure`、`thesis_planning`、`research_design`、`literature_review`、`evidence_synthesis`、`methodology`、`experiment_design`、`academic_writing`、`english_academic_writing`、`advisor_feedback`、`paper_review`、`paper_revision` |
| `thesis_defense` | `scientific_presentation`、`figure_review`、`thesis_planning`、`academic_writing`、`paper_review`、`paper_revision` |

以上只表达适用关系，不是调用顺序；普通对话仍可直接发现其他 Skill，关闭 Workflow 不关闭能力。跨阶段继续使用同一空间资料、论文论证、项目档案、记忆、对话和运行检查点。

## 5. 来源与许可

参考 github/research-skills-main 的 lit-review、figures、presentation-builder、manuscript-formatting、grant-writing／grant-review。上游目录保持原样。方法以中文适配，工具独立实现；没有安装上游 CLI 或复制其工程 Epic／Sprint 和模型编排。

根许可证为 BSD-3-Clause；原版权、许可全文及文件映射保留在 src/paperagent/skills/library/references/research/。humanizer 另有 MIT 来源，本次没有复制它的正文或案例。NIH／NSF 具体规则未直接转换为 NSFC 标准，当前指南另以空间资料读取。

## 6. 验证与后续

新增测试覆盖：旧证据兼容、矩阵筛选和分页、反例与来源变化、图表真实坐标／样式／异常数据、SVG 检查范围和输出路径、演示转义与来源保留、稿件变更、ZIP 及引用错误、申请评审字段／指南要求、接口下载边界。

离线浏览器验证矩阵、产物下载、来源变化、申请评审与实际 HTML／SVG 渲染；原生命周期和稿件界面回归保留。示例通过不等于所有生产稿件自动排版验收。

验证结果：全量 pytest **294 通过、1 跳过**（既有 Windows 符号链接权限），保留 4 条 FastAPI 弃用告警；三套离线浏览器验证和 JavaScript 语法检查通过。隔离副本 wheel 包含全部 81 个 Skill 定义、资源与来源／许可文件，以及新增界面和工具。

下一步可逐项补：复杂科研图表与栅格检查、PPTX 及视觉验收、完整刊会规则检查、真实账号同步与投稿轮次管理。轻量 Plugin 清单仍是独立架构选项，本次没有额外引入安装系统。
