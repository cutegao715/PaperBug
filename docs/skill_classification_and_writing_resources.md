# Skill 分类与论文写作资源适配

> 2026-10-28 更新：当前为 10 个 Workflow、35 个 Skill。旧入口与旧 Skill 的迁移、版本确认和主动服务，以 [生命周期 V1 与 Skill V2 落地记录](lifecycle_v1_and_skills_v2.md) 为准；以下保留历史架构说明。

日期：2026-10-26。本文记录已经落地的分类与资源组织，完整业务规划仍以 research_assistant_system_design.md 为准。

## 1. 两个独立维度

判断方法是否依赖某阶段特有的业务目标和验收要求，而不是依据文件存放位置或名称。

| 维度 | 值 | 含义 |
| --- | --- | --- |
| 能力类型 capability_type | general | 可跨研究成果或生命周期复用的方法 |
| 能力类型 capability_type | lifecycle_specific | 依赖具体业务交付、阶段目标或验收规则 |
| 来源 source_kind | builtin / shared / workspace | 系统共享、用户共享扩展、研究空间私有 |

内置申请书、Rebuttal、投稿准备可以同时是“共享”与“生命周期专用”。空间自定义的论证组织可以是“私有”与“通用”。旧 layer 只保留兼容和原有候选排序，不推导能力类型。旧 YAML 和 SKILL.md 未提供 capability_type 时保持未分类，不改写存量定义。

能力分类仅用于理解、目录和展示，不新增调用门槛，不按类型硬分配 Workflow，也不改变同标识同版本的空间覆盖规则。分类随定义保存；私有覆盖可以声明不同分类，来源限定 key 仍能读取原共享定义。

学校盲审要求、导师意见、刊会指南先保留为带来源、适用范围和版本的资料或论文要求记录。形成可复用方法时才提升为 Skill。Skill 不保存研究事实、审稿状态或作者决定。导师意见处理是跨材料的反馈转化方法，归通用；审稿回应需理解正式评审交付约束，归专用。

## 2. 论文撰写的关系

研究空间通过对话和资料按需发现 Skill；paper_writing 提供十二项带 when 条件的推荐：scientific_figure、figure_review、citation_verification、research_design、literature_review、evidence_synthesis、methodology、experiment_design、academic_writing、english_academic_writing、paper_revision、paper_review。

它们没有执行顺序，均可跳过、回访或脱离 Workflow 独立使用。学科 Skill 根据研究对象另外发现，例如真实包含 ML 实验时才读取 computer_science_discipline 的 ml_reporting。未列入某个 Workflow 也不影响发现和读取。

引言、摘要压缩、图表与结果表述仍放在 english_academic_writing 内部资源；不新增 Introduction 或摘要 Workflow。NSFC 立项依据可作为申请书 Skill 的专项资源，当前不因目录分类自动导入。独立评审继续复用 paper_review，通用修订复用 paper_revision。

## 3. 上游资源落位

| 来源 | 适配位置 | 取舍 |
| --- | --- | --- |
| academic-writing-skills | academic_writing 的论证／证据指南，英文写作 introduction | 保留读者功能、证据边界、术语与变更影响；不导入另一套状态模板 |
| scientific-writing-skills | 通用 prose、英文 introduction | 吸收句意提取和章节功能；不采用固定段数、句长或强制写作顺序 |
| Scientific Agent Skills | citation_verification、通用证据指南、学科 ml_reporting | 区分文献存在、元数据、原文支持和发表状态；ML 方法留在学科能力；不引入整库依赖与源仓库台账 |
| sci-introduction | evidence_synthesis 的 thematic_synthesis | 实际偏研究现状而非通用引言；按主题综合、按证据比较情境，不强制国内外配额、固定对比与五步流程 |

源目录位于 E:/PaperAgent/github，均未修改。重复提供的 academic-writing-skills 路径作为同一来源；另找到 scientific-writing-skills-master 并阅读其原创方法和来源声明。许可证和映射见 src/paperagent/skills/library/references/general/PROVENANCE.md；未复制课程案例或例句。

## 4. 按需内部资源

| Skill | 本次资源 | 使用条件 |
| --- | --- | --- |
| academic_writing | argument、prose、evidence | 跨材料论证、语言修订或承重主张核查 |
| paper_revision | argument、prose | 复用同一资源文件，不复制通用方法 |
| evidence_synthesis | thematic_synthesis | 组织主题、矛盾、情境差异和研究缺口 |
| citation_verification | identity、evidence | 独立引文身份、支持和状态核查；复用证据指南 |
| english_academic_writing | introduction | 期刊、会议论文的引言功能；原八份资源保留 |
| computer_science_discipline | ml_reporting | 真实包含机器学习训练与评估时 |

前轮新增七份独立方法文件；research-skills 适配后共三十四份方法文件、四十项资源引用，新增内容见 [分阶段实施](research_skills_adaptation.md)。目录只给摘要与资源数量，读 Skill 后看到资源说明和条件，再读选定资源；分页、哈希和声明路径限制沿用既有实现。分类不会将所有指南一次塞入上下文。

## 5. 当前内置目录

三十四个顶层 Skill，二十个通用、十四个生命周期专用，当前都在系统共享库中。学科方法归通用，但须满足学科适用条件；通用不意味着无条件适用。

| Skill | 能力类型 | 资源引用数 |
| --- | --- | --- |
| `academic_writing` | 通用 | 3 |
| `citation_verification` | 通用 | 2 |
| `computer_science_discipline` | 通用 | 1 |
| `economics_discipline` | 通用 | 0 |
| `education_discipline` | 通用 | 0 |
| `management_discipline` | 通用 | 0 |
| `materials_discipline` | 通用 | 0 |
| `medicine_discipline` | 通用 | 0 |
| `advisor_feedback` | 通用 | 0 |
| `conference_submission` | 生命周期专用 | 0 |
| `english_academic_writing` | 生命周期专用 | 9 |
| `evidence_synthesis` | 通用 | 2 |
| `experiment_design` | 通用 | 0 |
| `figure_review` | 通用 | 1 |
| `grant_application` | 生命周期专用 | 0 |
| `grant_application_general` | 生命周期专用 | 0 |
| `grant_midterm_report` | 生命周期专用 | 0 |
| `grant_review` | 生命周期专用 | 3 |
| `grant_writing` | 生命周期专用 | 1 |
| `journal_recommendation` | 生命周期专用 | 0 |
| `journal_strategy` | 生命周期专用 | 0 |
| `literature_review` | 通用 | 2 |
| `methodology` | 通用 | 0 |
| `paper_review` | 通用 | 8 |
| `paper_revision` | 通用 | 2 |
| `project_evidence_review` | 生命周期专用 | 0 |
| `research_design` | 通用 | 0 |
| `research_radar` | 通用 | 0 |
| `reviewer_response` | 生命周期专用 | 0 |
| `scientific_figure` | 通用 | 2 |
| `scientific_presentation` | 通用 | 2 |
| `submission_preparation` | 生命周期专用 | 2 |
| `submission_tracking` | 生命周期专用 | 0 |
| `thesis_planning` | 生命周期专用 | 0 |

## 6. 上下文与续接

继续使用 ConversationManager、MemoryManager、ProjectDossier、ManuscriptContext 和 PaperReviews。方法文件不创建副本记忆；研究问题、术语、论证、刊会要求、来源定位、历史决定和检查点沿用已有记录。来源或稿件版本变化后复核受影响内容，作者采纳与 Agent 候选保持分离。

引用核查指南只提供方法，实际结论取决于取得的原文和执行结果；DOI 可解析不代表全文支持，网络失败不代表伪造。本次没有新建引文核查自动认证服务、常驻机会发现、投稿账号连接或答辩工具。
