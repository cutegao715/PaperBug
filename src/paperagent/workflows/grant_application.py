from __future__ import annotations

import re
from pathlib import Path

from paperagent.workflows.base import WorkflowMatch
from paperagent.workflows.lifecycle import detect_lifecycle
from paperagent.workflows.rules import (
    DELIVERABLE_CONTRACTS,
    GRANT_APPLICATION_PLAYBOOK,
    HARD_RULE_FIELDS,
    HARD_RULE_KEYWORDS,
    INTERACTION_POLICY,
    MISSING_INFO_GROUPS,
    QUALITY_REVIEW_LEVELS,
    RULE_PRIORITY,
)
from paperagent.workspace.workspace import Workspace


class GrantApplicationWorkflow:
    workflow_id = "grant_application"
    name = "课题申请书工作流"

    _TRIGGERS = (
        "申请书", "申报书", "论证活页", "课题", "选题", "立项",
        "申报材料", "grant", "proposal",
    )

    def match(self, ws: Workspace, goal: str, lifecycle: dict | None = None) -> WorkflowMatch | None:
        lifecycle = lifecycle or detect_lifecycle(ws, goal)
        if lifecycle and (lifecycle["lifecycle_type"] != "project"
                          or lifecycle["stage"] not in {"application", "topic"}):
            return None
        goal_text = goal.lower()
        project_text = " ".join([
            ws.goal,
            goal,
            " ".join(m.get("filename", "") for m in ws.list_materials()),
            " ".join(p.name for p in ws.drafts_dir.glob("*.md"))
            if ws.drafts_dir.exists() else "",
        ])
        has_trigger = any(t.lower() in goal_text or t in project_text
                          for t in self._TRIGGERS)
        has_template = self._has_any(project_text, ["申请书", "申报书", "论证活页"])
        has_guide = self._has_any(project_text, ["指南", "通知", "选题"])
        if not (has_trigger or has_template or (lifecycle and lifecycle["stage"] == "application")):
            return None

        materials = self._material_state(ws)
        drafts = sorted(p.name for p in ws.drafts_dir.glob("*.md")) if ws.drafts_dir.exists() else []
        stage = self._stage(goal, drafts)
        missing = self._missing_information(goal, drafts)
        next_actions = self._next_actions(materials, drafts, missing, goal, stage)
        official = self._official_sections(ws, materials, goal)
        required_application_sections = (
            official["application_sections"]
            or [
                "课题基本信息", "选题依据", "研究目标", "研究内容",
                "研究方法", "实施步骤", "创新之处", "预期成果",
                "研究基础", "保障条件",
            ]
        )
        section_generation_order = (
            official["generation_order"]
            or [
                "选题依据",
                "国内外研究现状",
                "研究内容",
                "研究目标",
                "研究重点与难点",
                "研究方法/研究方案/技术路线",
                "创新点",
                "年度研究计划及预期研究结果",
                "研究基础与工作条件",
            ]
        )

        confidence = 0.55
        if has_trigger:
            confidence += 0.2
        if has_template:
            confidence += 0.15
        if has_guide:
            confidence += 0.1

        return WorkflowMatch(
            workflow_id=self.workflow_id,
            name=self.name,
            confidence=min(confidence, 0.98),
            reason="The project/message matches a structured grant or teaching-research application task.",
            lifecycle=lifecycle or {
                "lifecycle_type": "project",
                "stage": stage,
                "confidence": min(confidence, 0.98),
                "reason": "Grant application is part of the project lifecycle.",
            },
            state={
                "lifecycle_type": "project",
                "has_application_template": materials["has_application_template"],
                "has_argument_sheet": materials["has_argument_sheet"],
                "has_topic_guide": materials["has_topic_guide"],
                "drafts": drafts[-12:],
                "document_mode": self._document_mode(goal),
                "stage": stage,
                "rule_priority": RULE_PRIORITY,
                "material_required_sections": official,
            },
            next_actions=next_actions,
            quality_gates=[
                "citation_integrity",
                "application_completeness",
                "grant_writing_quality",
                "placeholder_check",
                "section_length_review",
                "anonymous_review_check_for_argument_sheet",
            ],
            drafting_contract={
                "do_not_finish_with_outline_only": True,
                "write_section_by_section": True,
                "ask_for_missing_personal_or_school_facts": True,
                "target_lengths": {
                    "application": {
                        "min_chars": 9000,
                        "selection_basis": "1200-1800 Chinese chars",
                        "research_content": "1200-1800 Chinese chars",
                        "methods_and_steps": "800-1200 Chinese chars",
                        "innovation": "500-800 Chinese chars",
                        "research_foundation": "600-1000 Chinese chars",
                    },
                    "argument_sheet": {
                        "min_chars": 6500,
                        "basis": "1400-2200 Chinese chars",
                        "content": "1200-1800 Chinese chars",
                        "methods": "800-1200 Chinese chars",
                    },
                },
                "required_application_sections": required_application_sections,
                "required_argument_sheet_sections": official["argument_sheet_sections"],
                "section_generation_order": section_generation_order,
                "official_template_note": official["note"],
                "selection_basis_paragraphs": [
                    "宏观背景与现实需求",
                    "具体研究问题与关键矛盾",
                    "现有研究或实践不足",
                    "本课题价值与自然过渡",
                ],
                "alignment_requirements": [
                    "前文提出的问题必须在研究内容中回应。",
                    "研究目标必须与研究内容、方法和成果对应。",
                    "创新点必须从研究缺口中推出。",
                    "研究基础必须说明支撑本项目哪一部分，而不是罗列成果。",
                ],
                "topic_quality": {
                    "good_topic_signals": [
                        "问题真实，来源于明确实践场景",
                        "对象清楚，研究范围不过大",
                        "有一定新意但不过度追新",
                        "与政策导向、行业转型需求和申报指南关注议题形成可解释关联",
                        "与申报指南、学科方向高度匹配",
                        "团队具备现实基础和实施条件",
                        "能形成明确成果而非停留在观点讨论",
                    ],
                    "low_quality_topic_signals": [
                        "题目过大",
                        "只有热点没有具体问题",
                        "堆砌概念",
                        "只有现状调查没有解决方案",
                        "对象、场景、变量不明确",
                        "题目创新但实际内容仍是传统做法",
                    ],
                },
                "selection_basis_logic": [
                    "政策/行业背景",
                    "现实问题",
                    "现有研究或实践不足",
                    "本项目切入点",
                    "研究价值",
                ],
                "hard_rule_extraction": {
                    "fields": HARD_RULE_FIELDS,
                    "keywords": HARD_RULE_KEYWORDS,
                    "priority": "Extracted hard rules from current materials override general grant-writing heuristics.",
                },
                "missing_information_groups": MISSING_INFO_GROUPS,
            },
            playbook=GRANT_APPLICATION_PLAYBOOK,
            deliverable_contracts=DELIVERABLE_CONTRACTS,
            quality_review_levels=QUALITY_REVIEW_LEVELS,
            interaction_policy=INTERACTION_POLICY,
            missing_information=missing,
        )

    @staticmethod
    def _has_any(text: str, needles: list[str]) -> bool:
        return any(n in text for n in needles)

    @staticmethod
    def _document_mode(goal: str) -> str:
        if "活页" in goal:
            return "argument_sheet"
        if "选题" in goal and "申请书" not in goal and "申报书" not in goal:
            return "topic_selection"
        return "application"

    @staticmethod
    def _stage(goal: str, drafts: list[str]) -> str:
        if any(word in goal for word in ["图", "公式", "技术路线", "框架图", "流程图", "指标表"]):
            return "visual_formula"
        if any(word in goal for word in ["题目优化", "优化题目", "改题目"]):
            return "title_optimization"
        if any(word in goal for word in ["润色", " polish", "语言优化"]):
            return "polishing"
        if any(word in goal for word in ["扩写", "扩充", "展开", "写长", "补充内容"]):
            return "expansion"
        if any(word in goal for word in ["章节", "每一章", "每个章节", "分节"]):
            return "section_seed"
        if any(word in goal for word in ["大纲", "框架", "提纲"]):
            return "outline"
        if any(word in goal for word in ["导出", "交付", "下载", "docx", "wps"]):
            return "export_delivery"
        if any(word in goal for word in ["检查", "评审", "把关", "诊断"]):
            return "review_check"
        if any(word in goal for word in ["修改", "优化", "改写"]):
            return "revision"
        if "活页" in goal:
            return "argument_sheet"
        if any(word in goal for word in ["申请书", "申报书", "完整"]):
            return "full_application"
        if "选题" in goal or not any("选题方案" in d for d in drafts):
            return "topic_selection"
        return "topic_development"

    @staticmethod
    def _material_state(ws: Workspace) -> dict:
        names = " ".join(
            [m.get("filename", "") for m in ws.list_materials()]
            + [p.name for p in ws.drafts_dir.glob("*.md")]
            if ws.drafts_dir.exists() else []
        )
        return {
            "has_application_template": "申请书" in names or "申报书" in names,
            "has_argument_sheet": "论证活页" in names or "活页" in names,
            "has_topic_guide": "指南" in names or "选题" in names,
        }

    @staticmethod
    def _official_sections(ws: Workspace, materials: dict, goal: str) -> dict:
        """Return material-derived section constraints from imported forms.

        The application form can contain many administrative fields. When an
        imported template exposes bracketed writing columns, those columns
        override the generic grant-writing framework. The generic framework is
        only a fallback when no material-specific structure can be inferred.
        """
        application_sections: list[str] = []
        argument_sheet_sections: list[str] = []
        if ws.drafts_dir.exists():
            for path in ws.drafts_dir.glob("imported-*.md"):
                try:
                    content = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                name = path.name
                if any(key in name for key in ("论证活页", "活页")):
                    argument_sheet_sections = (
                        argument_sheet_sections
                        or GrantApplicationWorkflow._extract_template_sections(content)
                    )
                elif any(key in name for key in ("申请书", "申报书")):
                    application_sections = (
                        application_sections
                        or GrantApplicationWorkflow._extract_application_design_sections(content)
                    )

        if not application_sections and argument_sheet_sections:
            application_sections = [
                section for section in argument_sheet_sections
                if section not in {"研究基础"}
            ]
        if not argument_sheet_sections and application_sections:
            argument_sheet_sections = list(application_sections)
        if not application_sections and not argument_sheet_sections:
            return {
                "application_sections": [],
                "argument_sheet_sections": [],
                "generation_order": [],
                "note": "",
            }

        generation_order = (
            argument_sheet_sections
            if "活页" in goal
            else application_sections
        )
        source = "申请书/申报书"
        if "活页" in goal or (not application_sections and argument_sheet_sections):
            source = "论证活页"
        return {
            "application_sections": application_sections,
            "argument_sheet_sections": argument_sheet_sections,
            "generation_order": generation_order,
            "note": (
                f"已从导入的{source}模板识别栏目结构；正文必须按"
                "材料栏目生成，通用申请书结构只作理解参考。"
            ),
        }

    @staticmethod
    def _extract_application_design_sections(text: str) -> list[str]:
        block = text
        start = text.find("课题设计论证")
        if start >= 0:
            block = text[start:]
            next_part = re.search(
                r"\n[五六七八九十]、|\n[五六七八九十]\s",
                block[1:],
            )
            if next_part:
                block = block[: next_part.start() + 1]
        return GrantApplicationWorkflow._extract_template_sections(block)

    @staticmethod
    def _extract_template_sections(text: str) -> list[str]:
        compact = re.sub(r"\s+", "", text)
        candidates = re.findall(
            r"(?:\d+[\.．、]?)?[［\[]([^［\]\[\]【】]{2,16})[］\]]",
            compact,
        )
        if not candidates:
            candidates = re.findall(
                r"(?:\d+[\.．、]?)?[【]([^［\]\[\]【】]{2,16})[】]",
                compact,
            )
        excluded = {
            "待补充", "待核实", "姓名", "单位", "电话", "邮箱",
            "课题名称", "关键词", "学科分类", "课题类别",
        }
        sections: list[str] = []
        for candidate in candidates:
            section = candidate.strip(" ：:;；，,。")
            if section in excluded:
                continue
            if any(bad in section for bad in ("待补", "说明：", "例如")):
                continue
            if section not in sections:
                sections.append(section)
        return sections[:12]

    @staticmethod
    def _missing_information(goal: str, drafts: list[str]) -> list[str]:
        missing = []
        joined = "\n".join(drafts) + "\n" + goal
        checks = [
            ("身份信息：姓名、单位、职称、专业", r"申请人|姓名|职称|职务|单位|专业"),
            ("事实性材料：已有成果、已承担项目、团队成员、合作单位、实际数据、实践基础", r"研究基础|已有成果|前期|项目|成员|合作单位|数据|实践基础"),
            ("申报决策：申报类别、重点/一般/青年、经费预算、研究周期、是否使用真实案例", r"课题类别|申报类别|重点|一般|青年|经费|预算|周期|真实案例"),
        ]
        for label, pattern in checks:
            if not re.search(pattern, joined):
                missing.append(label)
        return missing

    @staticmethod
    def _next_actions(materials: dict, drafts: list[str], missing: list[str],
                      goal: str, stage: str) -> list[str]:
        actions = []
        if not any(materials.values()):
            actions.append("Import or locate the grant notice, topic guide, application template, and argument-sheet template.")
        if not materials["has_application_template"]:
            actions.append("Ask for or import the application form/template before writing the final application.")
        if not materials["has_topic_guide"]:
            actions.append("Read the topic guide or notice and align the title with its topic category/code.")
        if missing and stage in {"full_application", "export_delivery"}:
            actions.append("Ask the user for missing factual fields instead of inventing personal, school, platform, or budget details.")
        if stage == "topic_selection":
            actions.append("Produce topic options first, with feasibility and fit analysis.")
        elif stage == "outline":
            actions.append("Create the application/argument-sheet outline before writing long prose. Map each section to evidence, user facts, and missing information.")
        elif stage == "section_seed":
            actions.append("Write concise core content for each section first. Do not polish or over-expand yet.")
        elif stage == "expansion":
            actions.append("Use append_draft to expand each section from the seed draft, adding argument depth, methods, implementation details, outputs, and risk notes.")
        elif stage == "polishing":
            actions.append("Polish the expanded draft: remove AI-style repetition, unify terminology, and improve reviewer-facing clarity without inventing facts.")
        elif stage == "visual_formula":
            actions.append("Generate research framework/technical route/indicator table suggestions based on the completed narrative, and only add formulas when the research design truly needs them.")
        elif stage == "title_optimization":
            actions.append("Diagnose the original title, provide three optimized versions, and recommend one.")
        elif stage == "argument_sheet":
            actions.append("Draft the argument sheet with attention to anonymous-review risk.")
        elif stage == "full_application":
            actions.append("Draft the application section by section and keep expanding until the completeness kernel passes.")
        elif stage == "review_check":
            actions.append("Review the existing draft first, classify issues by severity, then revise only the necessary parts.")
        elif stage == "export_delivery":
            actions.append("Before export, summarize unresolved fields and quality risks, then render/export the latest accepted draft.")
        else:
            actions.append("Continue from the selected topic and prepare the next required grant document.")
        return actions
