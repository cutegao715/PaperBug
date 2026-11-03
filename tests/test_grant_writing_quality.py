from paperagent.kernel.grant_writing_quality import check_grant_writing_quality


def test_grant_writing_quality_detects_norm_and_academic_risks():
    text = """
# 课题申请书

## 选题依据

根据2026年度山东省教育教学研究课题选题指南，本课题具有鲜明的时代性与紧迫性。
根据选题指南，本课题符合指南要求。根据申报指南，本课题有重要意义。
高校毕业生规模持续处于高位，就业结构性矛盾日益突出。教学与就业指导“两张皮”，
就业指导往往“底数不清”，资源投放“大水漫灌”，服务好不好缺少评价。

## 国内外研究现状

国外研究较早关注人工智能在职业发展与就业指导中的应用。现有研究多停留在理念与框架层面。

## 研究方法

本研究采用文献研究法、问卷调查法、访谈法、案例研究法、准实验法和数据分析法。

## 创新点

本研究提出“三向匹配”“全链条服务机制”“动态反馈闭环”“人机协同”“学情画像—职业画像—岗位画像”。

## 研究基础

[待补充：团队成果]
"""

    report = check_grant_writing_quality(
        text, {"workflow_id": "grant_application"})

    assert report.errors
    assert any("生成/占位标记" in e for e in report.errors)
    assert any("口语化" in w for w in report.warnings)
    assert any("申报指南" in w for w in report.warnings)
    assert any("文献综述" in w for w in report.warnings)
    assert any("准实验设计" in w for w in report.warnings)
    assert any("概念集中堆砌" in w for w in report.warnings)


def test_grant_writing_quality_warns_on_single_application_material_title():
    text = """
# 课题申请书

## 选题依据

本课题依据《2026年度山东省教育教学研究课题选题指南》展开研究，具有现实价值。
"""

    report = check_grant_writing_quality(
        text, {"workflow_id": "grant_application"})

    assert any("申报材料/模板名称" in w for w in report.warnings)

