from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy
from paperagent.skills.loader import SkillLoader
from paperagent.tools.registry import REGISTRY, describe_tools
from paperagent.workspace.workspace import Workspace


def test_web_tools_registered_with_read_permissions():
    tool_ids = {tool["tool_id"] for tool in describe_tools()}

    assert {"web_search", "page_fetch", "html_extract"} <= tool_ids
    assert REGISTRY["web_search"].permission == "network_read"
    assert REGISTRY["page_fetch"].permission == "network_read"
    assert REGISTRY["html_extract"].permission == "local_read"


def test_web_search_offline_stub_executes_through_tool_executor(tmp_path):
    ws = Workspace("web", root=tmp_path).init("grant deadline radar")
    ex = ToolExecutor(ResearchPolicy(), ws)

    result = ex.execute(ToolCall(
        tool_id="web_search",
        input={"query": "NSFC management science deadline", "provider": "offline_stub"},
    ))

    assert result.status == "ok"
    assert result.data["provider"] == "offline_stub"
    assert result.data["results"][0]["url"].startswith("https://example.test/")


def test_html_extract_returns_readable_text_links_and_snapshot(tmp_path):
    ws = Workspace("html", root=tmp_path).init("author guidelines")
    ex = ToolExecutor(ResearchPolicy(), ws)
    html = """
    <html>
      <head>
        <title>Journal Guidelines</title>
        <meta name="date" content="2026-09-30">
        <style>.hidden { display: none; }</style>
      </head>
      <body>
        <main>
          <h1>Author Guidelines</h1>
          <p>Submit empirical articles under 8000 words.</p>
          <a href="/ethics">Ethics policy</a>
          <a href="/ethics">Ethics policy duplicate</a>
        </main>
        <script>doNotInclude()</script>
      </body>
    </html>
    """

    result = ex.execute(ToolCall(
        tool_id="html_extract",
        input={"html": html, "url": "https://journal.example/guidelines"},
    ))

    assert result.status == "ok"
    assert result.data["title"] == "Journal Guidelines"
    assert "Submit empirical articles" in result.data["text"]
    assert "doNotInclude" not in result.data["text"]
    assert result.data["published_at"] == "2026-09-30"
    assert result.data["links"][0]["url"] == "https://journal.example/ethics"
    assert result.data["snapshot"]["dedup_key"]


def test_recent_research_skills_are_selectable(tmp_path):
    loader = SkillLoader(tmp_path)
    selected = loader.select(
        goal="respond to reviewer comments and strengthen methodology",
        task_type="revision",
        limit=10,
    )
    ids = {skill.id for skill in selected}

    assert "reviewer_response" in ids
    assert "methodology" in ids
