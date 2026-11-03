from paperagent.harness.context_assembler import assemble_observation
from paperagent.kernel.application_completeness import check_application_completeness
from paperagent.workflows import detect_lifecycle, select_workflow
from paperagent.workspace.workspace import Workspace


SHANDONG_APPLICATION_TEMPLATE = """# imported

四、课题设计论证
1. [选题说明]  选题所研究的具体问题、研究视角和核心概念（300字以内）。
2. [选题依据]  国内外相关研究的学术史梳理及研究进展。
3. [研究内容]  项目研究的主要目标、重点难点、整体框架、研究计划及其可行性等。
4．[创新之处]  在学术观点、研究方法等方面的特色和创新。
5．[预期成果]  成果形式、宣传转化及预期学术价值和社会效益等。
6．[参考文献]  开展本项目研究的主要中外参考文献。

五、研究基础和条件保障
1．[学术简历]
2．[前期成果]
3．[承担项目]
"""


SHANDONG_ARGUMENT_TEMPLATE = """# imported

山东省教育教学研究课题论证活页
1. [选题说明]  选题所研究的具体问题、研究视角和核心概念（300字以内）。
2. [选题依据]  国内外相关研究的学术史梳理及研究进展。
3. [研究内容]  项目研究的主要目标、重点难点、整体框架、研究计划及其可行性等。
4．[创新之处]  在学术观点、研究方法等方面的特色和创新。
5．[预期成果]  成果形式、宣传转化及预期学术价值和社会效益等。
6．[研究基础]  综述前期相关研究的核心观点。
7．[参考文献]  开展本项目研究的主要中外参考文献。
"""


def test_grant_application_workflow_matches_project_materials(tmp_path):
    ws = Workspace("grant", root=tmp_path).init("山东省教育教学研究课题")
    ws.upsert_material(str(tmp_path / "申请书.doc"), "converted")
    ws.upsert_material(str(tmp_path / "论证活页.doc"), "converted")
    ws.upsert_material(str(tmp_path / "选题指南.wps"), "converted")
    ws.write_draft("imported-3-山东省教育教学研究课题申请书.md", SHANDONG_APPLICATION_TEMPLATE)
    ws.write_draft("imported-4-山东省教育教学研究课题论证活页.md", SHANDONG_ARGUMENT_TEMPLATE)

    workflow = select_workflow(ws, "帮我完成课题申请书")

    assert workflow["workflow_id"] == "grant_application"
    assert workflow["lifecycle"]["lifecycle_type"] == "project"
    assert workflow["lifecycle"]["stage"] == "application"
    assert workflow["confidence"] > 0.8
    assert "application_completeness" in workflow["quality_gates"]
    assert "grant_writing_quality" in workflow["quality_gates"]
    assert workflow["drafting_contract"]["do_not_finish_with_outline_only"] is False
    assert workflow["project_contract"]["delivery"] == "draft"
    assert workflow["state"]["rule_priority"][0] == "user_explicit_requirements"
    assert workflow["state"]["stage"] == "full_application"
    assert "课题名称字数限制" in workflow["drafting_contract"]["hard_rule_extraction"]["fields"]
    assert "必须" in workflow["drafting_contract"]["hard_rule_extraction"]["keywords"]
    assert "选题" in workflow["playbook"]["stages"]
    assert "topic_selection" in workflow["deliverable_contracts"]
    assert "outline" in workflow["deliverable_contracts"]
    assert "扩写扩充" in workflow["playbook"]["stages"]
    assert workflow["playbook"]["writing_pipeline"][:3] == [
        "选题", "大纲", "每个章节写核心内容",
    ]
    assert "国内外研究现状" in workflow["playbook"]["default_application_framework"]
    assert workflow["playbook"]["logic_chain"][:3] == [
        "现实/科学问题", "现有研究不足", "研究问题",
    ]
    assert "选题依据" in workflow["drafting_contract"]["section_generation_order"]
    assert workflow["drafting_contract"]["required_application_sections"] == [
        "选题说明",
        "选题依据",
        "研究内容",
        "创新之处",
        "预期成果",
        "参考文献",
    ]
    assert "通用申请书结构只作理解参考" in workflow["drafting_contract"]["official_template_note"]
    assert "宏观背景与现实需求" in workflow["drafting_contract"]["selection_basis_paragraphs"]
    assert any("section by section" in action for action in workflow["next_actions"])
    assert "ERROR" in workflow["quality_review_levels"]
    assert any("文献综述" in item for item in workflow["quality_review_levels"]["WARNING"])
    assert workflow["interaction_policy"]["principle"].startswith("能先做就先做")


def test_lifecycle_detection_for_three_top_level_systems(tmp_path):
    project_ws = Workspace("life-project", root=tmp_path).init("省级课题")
    paper_ws = Workspace("life-paper", root=tmp_path).init("conference paper")
    thesis_ws = Workspace("life-thesis", root=tmp_path).init("硕士毕业论文")

    project_lifecycle = detect_lifecycle(project_ws, "帮我写项目中期进展")
    assert project_lifecycle["lifecycle_type"] == "project"
    assert project_lifecycle["stage"] == "midterm"
    assert detect_lifecycle(paper_ws, "准备会议论文投稿")["lifecycle_type"] == "paper_conference"
    assert detect_lifecycle(thesis_ws, "帮我写开题报告")["lifecycle_type"] == "thesis"


def test_project_midterm_and_final_workflows_share_lifecycle(tmp_path):
    ws = Workspace("project-reports", root=tmp_path).init("教育课题")
    ws.write_draft("申请书.md", "# 申请书\n\n## 研究目标\n\n目标。")
    ws.update_run_state(draft="申请书.md")

    midterm = select_workflow(ws, "帮我写项目中期进展报告")
    final = select_workflow(ws, "帮我写项目结题报告")

    assert midterm["workflow_id"] == "project_midterm_report"
    assert midterm["lifecycle"]["lifecycle_type"] == "project"
    assert midterm["lifecycle"]["stage"] == "midterm"
    assert midterm["drafting_contract"]["stage"] == "midterm"
    assert "completed_work" in midterm["drafting_contract"]["required_sections"]
    assert final["workflow_id"] == "project_final_report"
    assert final["drafting_contract"]["stage"] == "final"
    assert "objective_completion" in final["drafting_contract"]["required_sections"]


def test_generic_lifecycle_workflow_for_paper_and_thesis(tmp_path):
    paper_ws = Workspace("paper-life", root=tmp_path).init("论文投稿")
    thesis_ws = Workspace("thesis-life", root=tmp_path).init("毕业论文")

    paper = select_workflow(paper_ws, "帮我处理会议论文返修意见")
    thesis = select_workflow(thesis_ws, "帮我准备毕业论文答辩PPT")

    assert paper["workflow_id"] == "paper_conference_revision"
    assert paper["lifecycle"]["lifecycle_type"] == "paper_conference"
    assert paper["lifecycle"]["stage"] == "revision"
    assert thesis["workflow_id"] == "thesis_defense"
    assert thesis["lifecycle"]["lifecycle_type"] == "thesis"
    assert thesis["lifecycle"]["stage"] == "defense"


def test_workflow_is_in_observation(tmp_path):
    ws = Workspace("grant-obs", root=tmp_path).init("课题申报")
    ws.upsert_material(str(tmp_path / "申请书.doc"), "converted")

    obs = assemble_observation(ws, "帮我写申请书", 1, 10, 5, None, None, [])

    assert obs["workflow"]["workflow_id"] == "grant_application"
    assert obs["assistant_state"]["current_context"]["workflow"]["workflow_id"] == "grant_application"


def test_grant_workflow_detects_stages(tmp_path):
    ws = Workspace("grant-stage", root=tmp_path).init("课题申报")
    ws.upsert_material(str(tmp_path / "申请书.doc"), "converted")
    assert select_workflow(ws, "帮我做选题")["state"]["stage"] == "topic_selection"
    assert select_workflow(ws, "帮我写论证活页")["state"]["stage"] == "argument_sheet"
    assert select_workflow(ws, "帮我检查这份申请书")["state"]["stage"] == "review_check"
    assert select_workflow(ws, "导出最终wps")["state"]["stage"] == "export_delivery"
    assert select_workflow(ws, "先给我申请书大纲")["state"]["stage"] == "outline"
    assert select_workflow(ws, "每个章节先写点内容")["state"]["stage"] == "section_seed"
    assert select_workflow(ws, "在这个基础上扩写扩充")["state"]["stage"] == "expansion"
    assert select_workflow(ws, "整体润色一下")["state"]["stage"] == "polishing"
    assert select_workflow(ws, "生成技术路线图和公式")["state"]["stage"] == "visual_formula"
    assert select_workflow(ws, "再帮我优化一下内容")["state"]["stage"] == "revision"


def test_application_completeness_rejects_short_application():
    text = "# 山东省教育教学研究课题申请书\n\n## 选题依据\n\n太短。\n\n## 研究目标\n\n太短。"

    report = check_application_completeness(text, "申请书.md")

    assert not report.passed
    assert any("too short" in w for w in report.warnings)
    assert any("missing required section" in e for e in report.errors)


def test_application_completeness_accepts_full_skeleton():
    section_body = "本部分围绕高职人工智能赋能教学与就业指导展开，强调问题导向、实践验证、数据支撑和推广价值。" * 45
    text = "# 山东省教育教学研究课题申请书\n\n课题名称：人工智能赋能高职教学就业协同育人研究\n\n"
    for title in [
        "课题基本信息", "选题依据", "研究目标", "研究内容", "研究方法",
        "实施步骤", "创新之处", "预期成果", "研究基础", "保障条件",
    ]:
        text += f"## {title}\n\n{section_body}\n\n"

    report = check_application_completeness(text, "申请书.md")

    assert report.passed


def test_application_completeness_warns_about_generic_topic_and_overclaim():
    section_body = "本部分围绕课题展开，强调问题导向、实践验证、数据支撑和推广价值。" * 45
    text = "# 山东省教育教学研究课题申请书\n\n课题名称：人工智能赋能教育研究\n\n"
    for title in [
        "课题基本信息", "选题依据", "研究目标", "研究内容", "研究方法",
        "实施步骤", "创新之处", "预期成果", "研究基础", "保障条件",
    ]:
        text += f"## {title}\n\n{section_body}\n\n"
    text += "本课题填补国内空白。"

    report = check_application_completeness(text, "申请书.md")

    assert report.passed
    assert any("过大或过泛" in w for w in report.warnings)
    assert any("创新性表述可能过度" in w for w in report.warnings)


def test_shandong_template_sections_override_generic_application_sections(tmp_path):
    ws = Workspace("grant-shandong", root=tmp_path).init("山东省教育教学研究课题")
    ws.upsert_material(str(tmp_path / "3.山东省教育教学研究课题申请书.doc"), "converted")
    ws.upsert_material(str(tmp_path / "4.山东省教育教学研究课题论证活页.doc"), "converted")
    ws.upsert_material(str(tmp_path / "1.2026年度山东省教育教学研究课题选题指南.wps"), "converted")
    ws.write_draft("imported-3-山东省教育教学研究课题申请书.md", SHANDONG_APPLICATION_TEMPLATE)
    ws.write_draft("imported-4-山东省教育教学研究课题论证活页.md", SHANDONG_ARGUMENT_TEMPLATE)

    workflow = select_workflow(ws, "帮我完成课题申请书", workflow_options={"delivery": "ready"})
    generic_text = "# 山东省教育教学研究课题申请书\n\n"
    for title in [
        "课题基本信息", "选题依据", "研究目标", "研究内容", "研究方法",
        "实施步骤", "创新之处", "预期成果", "研究基础", "保障条件",
    ]:
        generic_text += f"## {title}\n\n" + ("本部分用于测试。" * 450) + "\n\n"

    report = check_application_completeness(generic_text, "申请书.md", workflow)

    assert not report.passed
    assert report.stats["required_section_count"] == 6
    assert any("选题说明" in w and "参考文献" in w for w in report.warnings)
    assert any("选题说明" in e and "参考文献" in e for e in report.errors)


def test_shandong_template_sections_accept_official_design_argument(tmp_path):
    ws = Workspace("grant-shandong-ok", root=tmp_path).init("山东省教育教学研究课题")
    ws.upsert_material(str(tmp_path / "3.山东省教育教学研究课题申请书.doc"), "converted")
    ws.write_draft("imported-3-山东省教育教学研究课题申请书.md", SHANDONG_APPLICATION_TEMPLATE)
    workflow = select_workflow(ws, "帮我完成课题申请书")

    body = "本部分围绕高职人工智能赋能教学与就业指导展开，说明问题、证据、方案、价值和可行性。" * 260
    official_text = "# 山东省教育教学研究课题申请书\n\n"
    for title in ["选题说明", "选题依据", "研究内容", "创新之处", "预期成果", "参考文献"]:
        official_text += f"## {title}\n\n{body}\n\n"

    report = check_application_completeness(official_text, "申请书.md", workflow)

    assert report.passed


def test_material_template_sections_are_extracted_generically(tmp_path):
    ws = Workspace("grant-generic-template", root=tmp_path).init("市级科研项目")
    ws.upsert_material(str(tmp_path / "项目申报书.docx"), "converted")
    ws.write_draft(
        "imported-项目申报书.md",
        """# 项目申报书

三、项目论证
1. [项目背景] 说明现实依据。
2. [核心问题] 说明拟解决的问题。
3. [研究方案] 说明研究内容、技术路线和方法。
4. [进度安排] 说明年度计划。
5. [成果形式] 说明预期成果。
""",
    )

    workflow = select_workflow(ws, "帮我完成项目申报书")

    assert workflow["drafting_contract"]["required_application_sections"] == [
        "项目背景",
        "核心问题",
        "研究方案",
        "进度安排",
        "成果形式",
    ]
    assert "选题依据" not in workflow["drafting_contract"]["required_application_sections"]


def test_general_grant_skill_selected(tmp_path):
    from paperagent.skills.loader import SkillLoader

    skills = SkillLoader(tmp_path).select(goal="帮我完成课题申请书", limit=8)
    ids = {s.id for s in skills}
    grant_skill = next(s for s in skills if s.id == "grant_application")

    assert "grant_application" in ids
    assert "selection_basis" in grant_skill.section_writing_guide
    assert "research_content" in grant_skill.section_writing_guide
    assert grant_skill.document_logic_chain[:3] == [
        "realistic/scientific problem",
        "existing research/practice gap",
        "research question",
    ]
    assert any("Technical route" in item for item in grant_skill.reviewer_risk_points)
    assert "writing_norm_review" in grant_skill.context_view()
    assert "academic_quality_review" in grant_skill.context_view()
