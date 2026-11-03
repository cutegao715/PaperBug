# PaperAgent Skill 重组评估（2026-10-28）

## 一、整理结论

原始 34 个 Skill（20 通用 + 14 生命周期专用）。合并 6 组，净减少 7 个；针对 10 个 Workflow 的空白补充 8 个原生 YAML Skill，最终 **35 个**。目录继续使用当前工程 `SkillLoader` 兼容的 `skills/library/*.yaml`；通过 `skill_catalog.yaml` 做逻辑归类，避免移动已有 `references` 路径。

### 6 组合并（保留全部原始文件快照）
| 合并保留的 Skill ID | 纳入原 Skill | 原因 |
|---|---|---|
| `grant_application` | `grant_application`, `grant_application_general`, `grant_writing` | 统一选题、申报论证、分栏撰写打法；高密度写作指南没有删除 |
| `journal_strategy` | `journal_strategy`, `journal_recommendation` | 合并选刊评价、推荐排序、转投策略 |
| `paper_revision` | `paper_revision`, `advisor_feedback` | 统一修改、导师意见分解和反馈闭环 |
| `scientific_figure` | `scientific_figure`, `figure_review` | 保持制图与图表审查两个工作模式，而不是两个重叠 Skill |
| `submission_preparation` | `submission_preparation`, `conference_submission` | 统一期刊/会议材料准备，区别具体要求 |
| `academic_writing` | `academic_writing`, `english_academic_writing` | 统一循证写作原则，按英语场景选择英文 reference（完整保留） |

未合并：`grant_review` 保留独立评审视角；`project_evidence_review` 保留目标—成果证据核查；`methodology`/`experiment_design` 保留方法论与实验设计的边界；`paper_review` 独立于 `paper_revision`，避免审稿即修改。

### 8 个新 Skill
| Skill ID | 核心缺口 | 典型场景 |
|---|---|---|
| `requirements_interpretation` | 材料硬约束提取、来源锚定 | 申报通知/投稿指南/学校模板 |
| `research_question_formulation` | 深度设计与评估研究问题 | G1、P1、T1 |
| `grant_closeout_report` | 结题闭环 | G3 |
| `thesis_midterm_report` | 毕业论文中期报告 | T2 |
| `thesis_blind_review_defense` | 盲审与答辩闭环 | T4 |
| `systematic_review_prisma` | 系统综述方法 | 指定系统综述/Meta 分析时 |
| `reproducibility_reporting` | 可复现性与透明披露 | 稿件投稿、学位论文终稿 |
| `abstract_title` | 标题、摘要、关键词、Highlights | 论文写作、投稿适配 |

此外：`submission_preparation` 吸收编辑信（Cover Letter）专项规则；`research_radar` 补充常态关注与去重推荐；`journal_strategy` 补充权威信息确认要求。

## 二、10 个 Workflow 的主 Skill 路由

具体配置见 `skill_catalog.yaml`，包含 `core`、`support`、`confirm` 三类；以下是核心：

| Workflow | Core Skill |
|---|---|
| G1 项目申请 | `requirements_interpretation` + `grant_application` + `research_question_formulation` + `grant_review` |
| G2 项目中期 | `requirements_interpretation` + `grant_midterm_report` + `project_evidence_review` |
| G3 项目结题 | `requirements_interpretation` + `grant_closeout_report` + `project_evidence_review` |
| P1 论文写作 | `research_question_formulation` + `research_design` + `literature_review` + `academic_writing` + `paper_review` |
| P2 投稿机会 | `journal_strategy` + `requirements_interpretation` + `submission_preparation` |
| P3 投稿跟踪 | `submission_tracking` + `reviewer_response` + `paper_revision` |
| T1 开题 | `thesis_planning` + `requirements_interpretation` + `research_question_formulation` + `research_design` |
| T2 毕业论文中期 | `thesis_midterm_report` + `requirements_interpretation` |
| T3 论文终稿 | `thesis_planning` + `academic_writing` + `paper_revision` + `paper_review` |
| T4 盲审答辩 | `thesis_blind_review_defense` + `paper_review` + `paper_revision` + `scientific_presentation` |

每个 Workflow 不是一定把表内所有 Skill 同时加载；推荐先挂载 1—3 个核心 Skill，按需追加。`research_radar` 与期限/邮箱监听属于跨生命周期触发能力，不能封死在某个 Workflow 内。学科 Skill 仅命中学科后才叠加。

## 三、GitHub 调研与引入选择

已通过 GitHub 仓库资料和原始 SKILL.md 核查：

1. [KalarisLabs/research-agent-skills](https://github.com/KalarisLabs/research-agent-skills)：`systematic-review-prisma`、`reproducibility-statement`、`abstract-and-title`、`cover-letter-to-editor`。适合补足方法学与投稿材料空白。已将可复用方法**重述并适配**成原生 YAML，保持 PaperAgent 原有结构；未引入外部 Python 脚本。
2. [K-Dense-AI/claude-scientific-writer](https://github.com/K-Dense-AI/claude-scientific-writer)：`research-grants`。包含美方 NSF/NIH 等申报经验，当前系统已有更适合中文申报的详细 Skill，只做参考，没有照搬国外基金要求。
3. [wanshuiyin/Auto-claude-code-research-in-sleep](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep)：`grant-proposal`。支持多资助机构申报，值得后续开发专项 Overlay，但其中用户资格/资助类型信息应以申报当年的权威指南核实；其固定的子 Skill 链不符合现有非刚性 Playbook 设计，没有直接引入。
4. [DELONG-L/Academic-Paper-Skills](https://github.com/DELONG-L/Academic-Paper-Skills)：四个协作 Skill 的写作/证据审查模式可借鉴，但与现有写作、论文评审、图表 Skill 重叠，暂不整体引入。

**来源透明性：** 本次 GitHub 连通主要通过检索/读取工具，代码容器无法直连 GitHub。因此 GitHub 中的 `SKILL.md` **没有逐字下载进交付包**，交付包中新增的是审核后编写的 PaperAgent 原生适配稿。已随包附 `fetch_upstream.py`，可在有联网权限的环境下载核查的原始 4 份 SKILL.md，并使用 Git blob SHA 对下载内容进行校验。详细记录见 `external_sources.yaml`。不要将适配稿误认为上游官方版本。

## 四、兼容性与部署注意

- `skills/loader.py` 增加**生命周期路由隔离**：项目结题/中期 Skill 不再因为相同“中期”词误命中学位论文场景；生命周期只为已有内容匹配的 Skill 加分，防止误加载整类。
- 六组被合并的旧 ID 中有 7 个被删除：需要迁移已有 Workflow 的 `skill_recommendations`、记录的 `skill_id`、展示配置；映射文件见 `migration_mapping.csv`，默认不自动替换。
- 当前只对给定 `skills.zip` 做了本地 Loader 和路由测试，**没有接入用户实际的整个后端工作区**。如需覆盖正式环境，还应做：Workflow 查找、上下文截断、资源访问、用户确认策略、任务中断恢复、质量审查门禁的端到端测试。
- GitHub 引入的脚本、权限、网络调用均未执行。源仓库版权/开源声明与已有库参考资料的 provenance 需要继续保留。
