"""End-to-end offline loop: ScriptedAgent walks the whole skeleton."""
from paperagent.agent.decision_loop import DecisionLoop
from paperagent.agent.scripted import ScriptedAgent
from paperagent.demo_assets import make_sample_pdf
from paperagent.harness.tool_executor import BudgetExceeded, ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy
from paperagent.workspace.workspace import Workspace


def test_scripted_full_run(tmp_path):
    ws = Workspace("e2e", root=tmp_path).init("overview of research agents")
    pdf = make_sample_pdf(tmp_path)
    agent = ScriptedAgent(goal=ws.goal, provider="offline_stub", pdf_path=str(pdf))
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), agent,
                        log=lambda m: None)
    outcome = loop.run(ws.goal)

    assert outcome.status == "finished"
    assert len(ws.list_references()) == 3
    assert len(ws.list_evidence()) == 1
    assert outcome.quality_report is not None
    assert outcome.quality_report.passed, outcome.quality_report
    # trace file written
    assert list(ws.runs_dir.glob("*.json"))


def test_chat_reply_ends_run_without_draft(tmp_path):
    """A greeting is answered conversationally: 1 iteration, no research,
    and finish's draft requirement does not apply."""
    from paperagent.schemas.actions import ChatReplyAction

    ws = Workspace("chat", root=tmp_path).init("文献综述")

    class Greeter:
        def decide(self, obs):
            return ChatReplyAction(text="你好！想研究什么课题？")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), Greeter(),
                        log=lambda m: None)
    outcome = loop.run("你好")

    assert outcome.status == "finished"
    assert outcome.iterations == 1
    assert outcome.summary == "你好！想研究什么课题？"
    assert ws.list_references() == []
    assert not list(ws.drafts_dir.glob("*.md"))


def test_observation_includes_recent_messages(tmp_path):
    from paperagent.harness.context_assembler import assemble_observation

    ws = Workspace("chat-context", root=tmp_path).init("文献综述")
    ws.append_message("user", "刚才那个课题继续")
    ws.append_message("assistant", "我们刚才在讨论教育课题。")

    obs = assemble_observation(
        ws, "继续", 1, iterations_left=5, tool_calls_left=5,
        last_action=None, last_result_compact=None, notes=[],
    )

    assert obs["recent_messages"][-1]["content"] == "我们刚才在讨论教育课题。"
    assert obs["assistant_state"]["current_context"]["recent_messages"]


def test_ask_user_without_answer_channel_ends_turn(tmp_path):
    """Web mode: ask_user_fn returns None -> the question itself ends the
    run (no stub answer, no forced research after asking)."""
    from paperagent.schemas.actions import AskUserAction

    ws = Workspace("askweb", root=tmp_path).init("goal")

    class Asker:
        def decide(self, obs):
            return AskUserAction(question="想研究哪个课题？")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), Asker(),
                        ask_user_fn=lambda q: None, log=lambda m: None)
    outcome = loop.run("你好")

    assert outcome.status == "finished"
    assert outcome.iterations == 1
    assert outcome.summary == "想研究哪个课题？"
    assert ws.list_references() == []


def test_repeated_agent_errors_have_user_visible_summary(tmp_path):
    ws = Workspace("errors", root=tmp_path).init("goal")

    class BrokenAgent:
        def decide(self, obs):
            raise RuntimeError("provider unavailable")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), BrokenAgent(),
                        log=lambda m: None)
    outcome = loop.run("hello")

    assert outcome.status == "too_many_errors"
    assert "provider unavailable" in outcome.summary


def test_action_union_parses_chat_reply():
    from paperagent.agent.llm_agent import _action_adapter
    action = _action_adapter.validate_python(
        {"action": "chat_reply", "text": "hi"})
    assert action.action == "chat_reply"
    assert action.text == "hi"


def test_action_union_parses_tool_call():
    from paperagent.agent.llm_agent import _action_adapter
    action = _action_adapter.validate_python(
        {"action": "tool_call", "tool_id": "reference_resolve",
         "input": {"title": "x", "provider": "offline_stub"}})
    assert action.action == "tool_call"
    assert action.tool_id == "reference_resolve"


def test_action_union_parses_append_draft():
    from paperagent.agent.llm_agent import _action_adapter
    action = _action_adapter.validate_python(
        {"action": "append_draft", "filename": "draft.md", "content": "## A\n\nText"})
    assert action.action == "append_draft"
    assert action.filename == "draft.md"


def test_action_union_parses_codex_style_draft_editing_actions():
    from paperagent.agent.llm_agent import _action_adapter

    assert _action_adapter.validate_python(
        {"action": "read_draft", "filename": "draft.md"}).action == "read_draft"
    assert _action_adapter.validate_python({
        "action": "replace_draft_section",
        "filename": "draft.md",
        "heading": "研究目标",
        "content": "## 研究目标\n\n目标内容",
    }).action == "replace_draft_section"
    assert _action_adapter.validate_python({
        "action": "update_draft_plan",
        "filename": "draft.md",
        "sections": [{"heading": "研究目标", "status": "todo"}],
        "next_section": "研究目标",
    }).action == "update_draft_plan"


def test_tool_call_candidates_can_be_saved(tmp_path):
    from paperagent.schemas.actions import ChatReplyAction, SaveReferenceAction, ToolCallAction

    ws = Workspace("tool-call", root=tmp_path).init("resolve reference")

    class ToolAgent:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return ToolCallAction(
                    tool_id="reference_resolve",
                    input={"title": "agentic workflows", "provider": "offline_stub"},
                )
            if self.step == 2:
                candidate = obs["last_result"]["data"]["candidates"][0]
                return SaveReferenceAction(ref_id=candidate["ref_id"])
            return ChatReplyAction(text="reference resolved")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), ToolAgent(),
                        log=lambda m: None)
    outcome = loop.run("resolve a reference")

    assert outcome.status == "finished"
    assert ws.list_references()


def test_import_folder_action_imports_project_materials(tmp_path):
    from paperagent.schemas.actions import FinishAction, ImportFolderAction

    folder = tmp_path / "materials"
    folder.mkdir()
    (folder / "notes.md").write_text("# Notes\nImportant local context.", encoding="utf-8")
    (folder / "refs.bib").write_text(
        '@article{a, title={Real Project Path Reading}, author={Doe, Jane}, year={2024}, journal={Demo Journal}}',
        encoding="utf-8",
    )

    ws = Workspace("import-folder", root=tmp_path).init("read project folder")

    class FolderAgent:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return ImportFolderAction(folder=str(folder), recursive=True)
            return FinishAction(summary="folder imported")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), FolderAgent(),
                        log=lambda m: None)
    outcome = loop.run("读取这个项目目录")

    assert outcome.status == "finished"
    assert len(ws.list_references()) >= 2
    assert any(p.name.startswith("imported-notes") for p in ws.drafts_dir.glob("*.md"))
    assert any(str(folder.resolve()) == root for root in ws.load_permissions()["allowed_roots"])
    state = ws.read_run_state()
    assert state["status"] == "finished"
    assert state["phase"] == "completed"


def test_tool_executor_records_protocol_calls(tmp_path):
    ws = Workspace("tool-log", root=tmp_path).init("goal")
    ex = ToolExecutor(ResearchPolicy(), ws, parent_run_id="run123")

    result = ex.execute(ToolCall(
        tool_id="literature_search",
        input={"query": "agent runtime", "provider": "offline_stub"},
    ))

    assert result.status == "ok"
    calls = ws.list_tool_calls()
    assert calls[-1]["parent_run_id"] == "run123"
    assert calls[-1]["tool_id"] == "literature_search"
    assert calls[-1]["status"] == "ok"
    assert calls[-1]["permission"] == "local_read"


def test_error_recovery_state_is_persisted(tmp_path):
    ws = Workspace("recover", root=tmp_path).init("goal")

    class BrokenAgent:
        def decide(self, obs):
            raise ValueError("LLM returned no text content")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), BrokenAgent(),
                        log=lambda m: None, run_id="run-recover")
    outcome = loop.run("goal")

    assert outcome.status == "too_many_errors"
    state = ws.read_run_state()
    assert state["status"] == "too_many_errors"
    assert state["recovery"]["kind"] == "llm_action_parse"
    assert "恢复建议" in outcome.summary


def test_read_pdf_action_can_read_project_markdown_draft(tmp_path):
    from paperagent.schemas.actions import ChatReplyAction, ReadPdfAction

    ws = Workspace("read-md", root=tmp_path).init("read project draft")
    ws.write_draft("imported-material.md", "Project material text.")

    class ReaderAgent:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return ReadPdfAction(path="imported-material.md", pages=[1])
            text = obs["last_result"]["pages"][0]["text"]
            return ChatReplyAction(text=f"read: {text}")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), ReaderAgent(),
                        log=lambda m: None)
    outcome = loop.run("read markdown")

    assert outcome.status == "finished"
    assert "Project material text" in outcome.summary


def test_import_folder_action_reports_unsupported_files_without_retry(tmp_path):
    from paperagent.schemas.actions import ImportFolderAction

    folder = tmp_path / "materials"
    folder.mkdir()
    (folder / "proposal.abc").write_bytes(b"abc placeholder")
    (folder / "unknown.xyz").write_bytes(b"unknown placeholder")

    ws = Workspace("unsupported-folder", root=tmp_path).init("read project folder")

    class FolderAgent:
        def __init__(self):
            self.calls = 0

        def decide(self, obs):
            self.calls += 1
            return ImportFolderAction(folder=str(folder), recursive=True)

    agent = FolderAgent()
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), agent,
                        log=lambda m: None)
    outcome = loop.run("读取这个项目目录")

    assert outcome.status == "finished"
    assert agent.calls == 1
    assert ".abc" in outcome.summary
    assert outcome.steps[0]["result"]["unsupported_count"] == 2
    assert ws.list_references() == []


def test_kernel_blocks_finish_after_bad_write(tmp_path):
    """A fabricating agent cannot finish: kernel gate rejects the draft."""
    ws = Workspace("fabricator", root=tmp_path).init("goal")

    class Fabricator:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            from paperagent.schemas.actions import FinishAction, WriteDraftAction
            self.step += 1
            if self.step == 1:
                return WriteDraftAction(
                    filename="draft.md",
                    content="Fabricated claim {{ref:ghost2099}}.")
            return FinishAction(summary="done")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), Fabricator(),
                        log=lambda m: None)
    outcome = loop.run("goal")
    assert outcome.status != "finished"
    assert any("ghost2099" in e for e in
               outcome.quality_report.citation_integrity.errors)


def test_application_workflow_warns_on_short_working_draft(tmp_path):
    from paperagent.schemas.actions import WriteDraftAction

    ws = Workspace("short-app", root=tmp_path).init("课题申请书")
    ws.upsert_material(str(tmp_path / "申请书.doc"), "converted")

    class ShortWriter:
        def decide(self, obs):
            return WriteDraftAction(
                filename="申请书.md",
                content="# 山东省教育教学研究课题申请书\n\n## 选题依据\n\n太短。",
            )

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=1), ws),
                        ShortWriter(), log=lambda m: None)
    outcome = loop.run("帮我写申请书")

    assert outcome.status == "budget_exhausted"
    assert outcome.steps[0]["result"]["status"] == "ok"
    assert "application_kernel" in outcome.steps[0]["result"]
    assert not outcome.quality_report.application_completeness.errors
    assert any("too short" in w for w in outcome.quality_report.application_completeness.warnings)


def test_revision_does_not_block_on_missing_full_application_sections(tmp_path):
    from paperagent.schemas.actions import FinishAction, WriteDraftAction

    ws = Workspace("revision-app", root=tmp_path).init("课题申请书")
    ws.upsert_material(str(tmp_path / "申请书.doc"), "converted")

    class RevisionWriter:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return WriteDraftAction(
                    filename="申请书.md",
                    content="# 山东省教育教学研究课题申请书\n\n## 选题依据\n\n这是优化后的局部内容。",
                )
            return FinishAction(summary="已完成局部优化")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws),
                        RevisionWriter(), log=lambda m: None)
    outcome = loop.run("再帮我优化一下内容")

    assert outcome.status == "finished"
    assert outcome.steps[0]["result"]["status"] == "ok"
    assert ws.read_run_state()["workflow"] is None
    assert outcome.quality_report.application_completeness is None


def test_append_draft_allows_section_by_section_writing(tmp_path):
    from paperagent.schemas.actions import AppendDraftAction, ChatReplyAction

    ws = Workspace("append", root=tmp_path).init("long draft")

    class SectionWriter:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return AppendDraftAction(filename="draft.md", content="# Draft\n\n## A\n\nOne")
            if self.step == 2:
                return AppendDraftAction(filename="draft.md", content="## B\n\nTwo")
            return ChatReplyAction(text="done")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), SectionWriter(),
                        log=lambda m: None)
    outcome = loop.run("write long draft")

    assert outcome.status == "finished"
    assert "## A" in ws.read_draft("draft.md")
    assert "## B" in ws.read_draft("draft.md")


def test_codex_style_long_draft_read_plan_and_replace_section(tmp_path):
    from paperagent.harness.context_assembler import assemble_observation
    from paperagent.schemas.actions import (
        ChatReplyAction,
        ReadDraftAction,
        ReplaceDraftSectionAction,
        UpdateDraftPlanAction,
        WriteDraftAction,
    )

    ws = Workspace("codex-style", root=tmp_path).init("课题申请书")

    class DraftEditor:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return UpdateDraftPlanAction(
                    filename="申请书.md",
                    sections=[
                        {"heading": "选题依据", "status": "drafted"},
                        {"heading": "研究目标", "status": "todo"},
                    ],
                    next_section="研究目标",
                )
            if self.step == 2:
                return WriteDraftAction(
                    filename="申请书.md",
                    content="# 申请书\n\n## 选题依据\n\n旧内容\n\n## 研究目标\n\n太短",
                )
            if self.step == 3:
                return ReadDraftAction(filename="申请书.md", max_chars=1000)
            if self.step == 4:
                return ReplaceDraftSectionAction(
                    filename="申请书.md",
                    heading="研究目标",
                    content="## 研究目标\n\n优化后的目标内容。",
                )
            return ChatReplyAction(text="done")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), DraftEditor(),
                        log=lambda m: None)
    outcome = loop.run("按 Codex 方式写长申请书")

    assert outcome.status == "finished"
    assert "优化后的目标内容" in ws.read_draft("申请书.md")
    assert ws.read_run_state()["draft_plan"]["next_section"] == "研究目标"

    obs = assemble_observation(
        ws, "继续", 1, iterations_left=5, tool_calls_left=5,
        last_action=None, last_result_compact=None, notes=[],
    )
    assert obs["draft_plan"]["next_section"] == "研究目标"
    assert obs["project_memory"]["draft_plan"]["next_section"] == "研究目标"


def test_long_grant_write_draft_is_rejected_with_section_recovery(tmp_path):
    from paperagent.schemas.actions import ChatReplyAction, WriteDraftAction

    ws = Workspace("long-grant-write", root=tmp_path).init("课题申请书")
    ws.upsert_material(str(tmp_path / "申请书.doc"), "converted")
    content = "# 申请书\n\n## 选题依据\n\n" + ("很长的正文。" * 5000)

    class LongWriter:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return WriteDraftAction(filename="申请书.md", content=content)
            return ChatReplyAction(text="done")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=1), ws),
                        LongWriter(), log=lambda m: None)
    outcome = loop.run("帮我完成课题申请书")

    assert outcome.steps[0]["result"]["status"] == "error"
    assert outcome.steps[0]["result"]["recovery"]["kind"] == "long_document_chunk_required"


def test_finish_auto_exports_docx_for_grant_application(tmp_path):
    from docx import Document
    from paperagent.schemas.actions import FinishAction, WriteDraftAction

    template = tmp_path / "申请书模板.docx"
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "选题依据"
    table.cell(0, 1).text = ""
    doc.save(template)

    ws = Workspace("auto-export", root=tmp_path).init("课题申请书")
    ws.upsert_material(str(template), "imported", kind="docx")
    body = "本部分围绕项目真实问题展开论证，说明背景、问题、依据、价值和实施基础。" * 250
    content = "# 申请书\n\n## 选题依据\n\n" + body

    class ExportWriter:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return WriteDraftAction(filename="申请书.md", content=content)
            return FinishAction(summary="完成")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws),
                        ExportWriter(), workflow_id="grant_application", log=lambda m: None)
    outcome = loop.run("再帮我优化一下内容")

    assert outcome.status == "finished"
    exports = outcome.steps[-1]["result"]["exports"]
    assert exports and exports[0]["status"] == "ok"
    assert exports[0]["path"].endswith(".docx")
    assert (ws.dir / "project_memory.json").exists()


def test_finish_summary_includes_workflow_quality_review(tmp_path):
    from paperagent.schemas.actions import FinishAction, WriteDraftAction

    ws = Workspace("finish-review", root=tmp_path).init("课题申请书")
    ws.upsert_material(str(tmp_path / "申请书.doc"), "converted")
    section_body = "本部分围绕高职人工智能赋能教学与就业指导展开，强调问题导向、实践验证、数据支撑和推广价值。" * 25
    content = "# 山东省教育教学研究课题申请书\n\n课题名称：人工智能赋能教育研究\n\n"
    for title in [
        "课题基本信息", "选题依据", "研究目标", "研究内容", "研究方法",
        "实施步骤", "创新之处", "预期成果", "研究基础", "保障条件",
    ]:
        content += f"## {title}\n\n{section_body}\n\n"

    class AppWriter:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return WriteDraftAction(filename="申请书.md", content=content)
            return FinishAction(summary="申请书初稿已完成。")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), AppWriter(),
                        log=lambda m: None)
    outcome = loop.run("帮我完成课题申请书")

    assert outcome.status == "finished"
    assert "当前阶段" in outcome.summary
    assert "质量提示" in outcome.summary


def test_full_application_working_draft_can_finish_with_length_warning(tmp_path):
    from paperagent.schemas.actions import FinishAction, WriteDraftAction

    ws = Workspace("short-finish", root=tmp_path).init("课题申请书")
    ws.upsert_material(str(tmp_path / "申请书.doc"), "converted")
    section_body = "本部分内容较短。" * 10
    content = "# 山东省教育教学研究课题申请书\n\n课题名称：人工智能赋能高职教学就业协同育人研究\n\n"
    for title in [
        "课题基本信息", "选题依据", "研究目标", "研究内容", "研究方法",
        "实施步骤", "创新之处", "预期成果", "研究基础", "保障条件",
    ]:
        content += f"## {title}\n\n{section_body}\n\n"

    class ShortFinisher:
        def __init__(self):
            self.step = 0

        def decide(self, obs):
            self.step += 1
            if self.step == 1:
                return WriteDraftAction(filename="申请书.md", content=content)
            return FinishAction(summary="申请书完成。")

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=2), ws),
                        ShortFinisher(), log=lambda m: None)
    outcome = loop.run("帮我完成课题申请书")

    assert outcome.status == "finished"
    assert any("too short" in w for w in outcome.quality_report.application_completeness.warnings)
    assert outcome.steps[-1]["result"]["project_delivery"]["contract"]["delivery"] == "draft"


def test_tool_budget_enforced(tmp_path):
    ws = Workspace("budget", root=tmp_path).init("goal")
    ex = ToolExecutor(ResearchPolicy(max_tool_calls=1), ws)
    ok = ex.execute(ToolCall(tool_id="literature_search",
                             input={"query": "q", "provider": "offline_stub"}))
    assert ok.status == "ok"
    try:
        ex.execute(ToolCall(tool_id="literature_search",
                            input={"query": "q", "provider": "offline_stub"}))
        raise AssertionError("BudgetExceeded expected")
    except BudgetExceeded:
        pass


def test_cancel_event_stops_loop(tmp_path):
    import threading

    class ForeverAgent:
        def __init__(self):
            self.calls = 0

        def decide(self, obs):
            from paperagent.schemas.actions import SearchAction
            self.calls += 1
            return SearchAction(query="q", max_results=1,
                                provider="offline_stub")

    ws = Workspace("cancel", root=tmp_path).init("goal")
    agent = ForeverAgent()
    cancel = threading.Event()
    cancel.set()  # cancel before first iteration

    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), agent,
                        log=lambda m: None, cancel_event=cancel)
    outcome = loop.run("goal")
    assert outcome.status == "cancelled"
    assert agent.calls == 0


def test_unknown_tool_fails_cleanly(tmp_path):
    ws = Workspace("u", root=tmp_path).init("goal")
    ex = ToolExecutor(ResearchPolicy(), ws)
    result = ex.execute(ToolCall(tool_id="nope", input={}))
    assert result.status == "error"
    assert "unknown tool" in result.errors[0]


def test_pdf_reader_missing_file(tmp_path):
    ws = Workspace("m", root=tmp_path).init("goal")
    ex = ToolExecutor(ResearchPolicy(), ws)
    result = ex.execute(ToolCall(tool_id="pdf_reader",
                                 input={"path": "missing.pdf"}))
    assert result.status == "error"
