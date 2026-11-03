"""Versioned V1 lifecycle contracts. Steps are revisitable, never a pipeline."""
from copy import deepcopy


CONFIRMATION_POLICY = {
    "required": ["核心选题与研究问题", "重大研究方案调整", "真实事实和研究数据", "最终正式交付版本", "投稿、邮件发送等对外操作"],
    "recommended": ["大纲", "创新点", "评审修改方案"],
    "routine": ["文献检索", "常规资料整理", "引用格式修正", "非实质性语言润色", "自动质量检查"],
    "first_topic_question": "是否已有意向题目？",
    "invalidate_on_material_change": True,
}
QUALITY_LEVELS = {
    "ERROR": ["虚构事实", "错误引用", "违反官方强制规则", "关键材料缺失", "匿名信息泄露"],
    "WARNING": ["内容不完整", "论证不足", "研究设计不一致", "缺少必要证据"],
    "SUGGESTION": ["题目优化", "论证深化", "创新表达", "语言和结构优化"],
}


def _spec(code, identifier, name, kind, stage, stages, inputs, steps, outputs, confirmations, rules=()):
    return {
        "code": code, "id": identifier, "name": name, "lifecycle_types": [kind],
        "default_stage": stage, "suggested_stages": stages,
        "mode": "management" if code == "P3" else "writing",
        "implementation": "delivery" if code.startswith("G") else "coordination",
        "description": "；".join(outputs[:2]), "inputs": inputs,
        "steps": [{"id": f"{code.lower()}_{i}", "name": title} for i, title in enumerate(steps, 1)],
        "deliverables": outputs, "confirmation_points": confirmations,
        "completion_conditions": ["本次约定交付物绑定到具体文件版本", "无未解决 ERROR", "正式交付版本经用户确认"],
        "rules": list(rules), "ordered": False,
        "available_capabilities": ["按需 Skill 与内部资源", "版本化交付与确认", "可暂停、恢复、跳过、回退", "资料与证据复用"],
        "planned_capabilities": [], "planned_skill_ids": [],
    }


WORKFLOWS = [
    _spec("G1", "grant_application", "项目申请", "project", "application", ["topic", "application"],
          ["申报通知", "指南", "模板", "用户研究背景"],
          ["材料解析：资格、栏目、字数、格式及截止日期", "选题确认：已有题目或推荐 3—5 个候选", "研究设计：问题、目标、内容、方法与创新", "按申报模板生成大纲", "检索证据并逐章节写作", "模拟项目评审", "修改并交付申请书与附件"],
          ["申请书", "论证活页", "参考文献", "评审报告", "待补事实清单"],
          ["选题", "研究设计", "大纲", "最终正文及重要评审修改"]),
    _spec("G2", "project_midterm_report", "项目中期进展", "project", "midterm", ["execution", "midterm"],
          ["立项申请书", "任务书", "中期报告模板", "实际进度与阶段成果"],
          ["读取申请书、任务书和模板", "提取原定目标、任务、进度与预期成果", "收集实际进度、成果、调研及实验材料", "对照已完成、进行中、延期和调整事项", "按模板生成大纲并逐章撰写", "检查进展与申请书对应关系", "评审修正与后续工作建议"],
          ["中期报告", "任务完成度对照表", "后续研究计划", "风险提示"],
          ["实际进展", "计划调整", "阶段成果", "最终报告"]),
    _spec("G3", "project_final_report", "项目结题", "project", "final", ["final", "archive"],
          ["申请书", "任务书", "中期报告", "结题要求", "成果证明材料"],
          ["读取历史报告与结题要求", "提取目标、考核指标与成果承诺", "收集实际成果及证明", "建立承诺成果—实际成果—证明材料对应关系", "识别未完成、变更和缺证事项", "按结题模板逐章撰写", "模拟结题评审、修正并整理附件"],
          ["结题报告", "成果清单", "证明材料目录", "结题风险报告"],
          ["实际成果", "未完成事项及说明", "结题材料"],
          ["不得把未完成成果描述为已完成；承诺、实际完成与证明必须分别记录"]),
    _spec("P1", "paper_writing", "论文编写", "paper_conference", "drafting", ["topic", "literature_review", "research", "drafting"],
          ["研究背景", "已有课题与论文", "真实数据或研究计划", "目标刊会要求（可选）"],
          ["选题发现：先询问已有题目，否则推荐候选", "深入设计研究问题、缺口、可验证性与贡献", "研究设计：理论、方法、数据及验证方案", "深度文献调研并建立证据库", "按学科、研究类型和刊会要求生成大纲", "按适用章节写作", "质量评审：创新、逻辑、方法、证据与投稿竞争力", "修改迭代并交付候选稿"],
          ["论文正文", "参考文献", "研究问题说明", "方法与数据说明", "质量评审报告"],
          ["选题", "研究问题", "研究方案", "大纲", "重大修改"],
          ["按学科动态叠加方法，不强制统一章节结构", "区分已完成实验与计划；无真实数据时只写设计或结果框架，不编造结果"]),
    _spec("P2", "venue_matching", "投稿机会与期刊匹配", "paper_conference", "submission", ["topic", "literature_review", "research", "drafting", "submission", "revision"],
          ["论文或研究构想", "作者偏好与费用约束", "当前刊会、专题征稿和投稿指南"],
          ["分析主题、创新、成熟度与质量", "检索期刊、会议、Special Issue 与 CFP", "比较范围、层次、周期、费用、期限和录用风险", "推荐并排序 3—5 个投稿目标，说明依据与风险", "用户选择目标", "读取当前投稿指南、模板和作者须知", "适配格式、语言、字数、引用并准备 Cover Letter", "检查投稿完整性"],
          ["投稿推荐报告", "目标期刊适配稿", "Cover Letter", "投稿检查清单"],
          ["目标期刊或会议", "投稿版本"],
          ["目标可在写作前、中、后确定；无需先完成论文", "费用、周期、截止日期引用当前权威来源；未知信息不猜测"]),
    _spec("P3", "submission_tracking", "投稿与追踪", "paper_conference", "submission", ["submission", "revision", "production", "archive"],
          ["用户投稿信息或已授权邮箱", "稿件编号与投稿日期", "可靠编辑决定与审稿意见"],
          ["接收投稿信息或授权邮箱事件", "建立 Submission 记录", "依据可靠来源追踪邮件与状态", "识别决定、意见、修改要求及截止日期", "分类意见并生成策略与逐条回复草稿", "用户确认后修改论文与回复信", "追踪重新提交及再审", "按录用、拒稿或撤稿结果分支"],
          ["投稿状态记录", "审稿意见分析", "修改稿", "回复信", "录用记录"],
          ["审稿修改方案", "回复信", "重新投稿及任何对外操作"],
          ["录用为主要完成节点；出版跟踪可继续", "拒稿返回 P2；返修允许再审；撤稿结束该轮", "未连接邮箱时只处理用户提供的信息，不声称正在收信"]),
    _spec("T1", "thesis_proposal", "论文开题", "thesis", "proposal", ["topic", "proposal"],
          ["学历层次", "学校规范与导师要求", "已有论文、课题与积累"],
          ["识别学历、学科、学校与导师要求", "读取已有研究积累", "询问已有毕业论文题目", "无题目时结合积累、前沿与完成条件推荐", "论证问题、价值、可行性与创新", "检索文献并组织研究综述", "确定目标、内容、方法、技术路线与计划", "按学校模板生成开题报告", "模拟开题评审并修改"],
          ["开题报告", "文献综述", "技术路线", "研究计划", "开题答辩材料"],
          ["选题", "核心研究问题", "研究方案", "开题报告"]),
    _spec("T2", "thesis_midterm_report", "毕业论文中期", "thesis", "midterm", ["research", "midterm"],
          ["开题报告", "研究计划", "中期检查要求", "已完成工作、数据、实验和草稿"],
          ["读取开题报告、计划和检查要求", "提取原定目标、任务与阶段节点", "收集实际工作、数据、实验与草稿", "对照开题计划评估进展", "识别偏差、困难与方案调整", "生成中期检查报告", "评审并规划后续任务"],
          ["中期检查报告", "进度分析", "后续任务计划"],
          ["实际进度", "重大研究调整", "最终中期报告"]),
    _spec("T3", "thesis_writing", "毕业论文终稿编写", "thesis", "drafting", ["drafting", "advisor_review", "final"],
          ["开题报告", "中期报告", "导师意见", "已发表论文", "现有草稿", "学校规范"],
          ["读取历史研究材料与导师意见", "依据学校规范建立整体结构", "梳理核心问题与全文逻辑主线", "确定章节、材料和证据需求", "逐章写作并整理引用与图表", "核对逻辑递进、术语、问题、方法和结论", "检查整体贡献与证据支持", "按导师反馈修订", "格式检查与提交前评审"],
          ["毕业论文终稿", "参考文献", "图表目录", "格式检查报告", "待修改问题清单"],
          ["整体框架", "研究贡献", "重大章节调整", "最终版本"],
          ["复用成果时核对引用、版权与学术规范", "检查章节关联和整体贡献；按学历层次调整设计与评审深度"]),
    _spec("T4", "thesis_defense", "盲审与答辩", "thesis", "defense", ["blind_review", "defense", "final"],
          ["学校盲审、答辩与学位要求", "当前论文", "真实盲审及答辩意见"],
          ["读取学校盲审、答辩与学位授予要求", "模拟专家评审", "检查匿名化、引用、格式和提交材料", "分类真实盲审意见并制定修改方案", "修改论文并生成修改说明", "制作答辩 PPT、讲稿与专家问答", "模拟答辩并优化贡献表达", "按正式答辩意见修改归档版", "确认提交和流程状态"],
          ["盲审修改稿", "修改说明", "答辩 PPT", "讲稿与问答材料", "最终归档论文"],
          ["盲审修改方案", "答辩材料", "最终归档版本"]),
]

CODE_IDS = {w["code"]: w["id"] for w in WORKFLOWS}
CATALOG_KEYS = dict(zip(CODE_IDS, ["G1_grant_application", "G2_grant_midterm", "G3_grant_closeout", "P1_paper_writing", "P2_venue_matching", "P3_submission_tracking", "T1_thesis_proposal", "T2_thesis_midterm", "T3_thesis_writing", "T4_thesis_defense"]))
ALIASES = {**CODE_IDS, **{CATALOG_KEYS[c]: i for c, i in CODE_IDS.items()},
           "publication_management": "submission_tracking", "project_topic": "project_opportunity_discovery"}

PROACTIVE_SERVICES = [
    {"code": "A1", "id": "research_radar", "name": "Research Radar", "triggers": ["定期检索", "重要文献更新", "研究兴趣变化"], "outputs": ["科研简报", "选题建议", "文献推荐"]},
    {"code": "A2", "id": "opportunity_radar", "name": "Opportunity Radar", "triggers": ["新申报或征稿机会", "截止日期临近", "用户条件变化"], "outputs": ["机会推荐", "适配度分析", "行动建议"]},
    {"code": "A3", "id": "progress_watcher", "name": "Progress & Feedback Watcher", "triggers": ["邮件事件", "截止日期", "任务状态变化", "导师或审稿反馈"], "outputs": ["进度提醒", "反馈摘要", "下一步建议"]},
]


def contract(spec):
    return {**deepcopy(spec), "confirmation_policy": deepcopy(CONFIRMATION_POLICY),
            "quality_review_levels": deepcopy(QUALITY_LEVELS),
            "execution_policy": {"ordered_steps": False, "allow_skip": True, "allow_revisit": True,
                                 "allow_replan": True, "allow_pause": True, "allow_resume": True,
                                 "auto_advance_lifecycle": False, "allow_other_skills": True}}
