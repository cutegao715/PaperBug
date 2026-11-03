import json
import sys
import time
import pytest
from paperagent.tools.mcp_bridge import Bridge, close_all, tool_id
from paperagent.tools.registry import describe_tools
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy
from paperagent.workspace.workspace import Workspace


@pytest.fixture
def configured(tmp_path):
    pytest.importorskip("mcp")
    server = tmp_path / "echo.py"
    server.write_text('''import asyncio
from mcp.server import MCPServer
app = MCPServer("test")
@app.tool()
def echo(text: str) -> str:
    return text
@app.tool()
async def slow() -> str:
    await asyncio.sleep(20)
    return "late"
@app.tool()
def forbidden() -> str:
    return "no"
app.run(transport="stdio")
''')
    path = tmp_path / ".paperagent" / "mcp.json"
    path.parent.mkdir()
    data = {"servers": {"test": {"command": sys.executable, "args": [str(server)],
        "timeout_s": 3, "allow_tools": {"echo": "network_read", "slow": "network_read"}}}}
    path.write_text(json.dumps(data))
    yield tmp_path, path, data
    close_all()


def test_no_config_no_sdk_import(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."): raise AssertionError("SDK imported")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert describe_tools(tmp_path)
    assert tool_id("a_b", "c") != tool_id("a", "b_c")


def test_discovery_execution_schema_and_revocation(configured):
    root, path, data = configured
    from paperagent.tools.mcp_bridge import get_bridge
    bridge = get_bridge(root)
    descriptors = bridge.discover()
    ident = tool_id("test", "echo")
    assert ident in descriptors
    assert tool_id("test", "forbidden") not in descriptors
    session = bridge.sessions["test"]
    assert bridge.discover() == descriptors and bridge.sessions["test"] is session
    ws = Workspace("p", root).init("test")
    executor = ToolExecutor(ResearchPolicy(), ws)
    result = executor.execute(ToolCall(tool_id=ident, input={"text": "hello"}))
    assert result.status == "ok", result
    assert "hello" in str(result.data) and "mcp:test" in result.provenance
    bad = executor.execute(ToolCall(tool_id=ident, input={"text": 5}))
    assert bad.status == "error"
    data["servers"]["test"]["allow_tools"] = {}
    path.write_text(json.dumps(data))
    assert executor.execute(ToolCall(tool_id=ident, input={"text": "x"})).status == "error"
    assert not session.thread.is_alive()


def test_timeout_closes_session(configured):
    root, _, _ = configured
    bridge = Bridge(root)
    try:
        assert bridge.discover()
        session = bridge.sessions["test"]
        start = time.monotonic()
        with pytest.raises(RuntimeError): bridge.call(tool_id("test", "slow"), {})
        assert time.monotonic() - start < 12
        assert not session.thread.is_alive()
        assert bridge.discover()
        assert "recovered" in str(bridge.call(tool_id("test", "echo"), {"text": "recovered"}))
    finally:
        bridge.close()


@pytest.mark.parametrize("content", ["[]", "null", "1", '{"servers": []}'])
def test_invalid_config_isolated(tmp_path, content):
    p = tmp_path / ".paperagent" / "mcp.json"
    p.parent.mkdir()
    p.write_text(content)
    assert Bridge(tmp_path).discover() == {}


def test_failed_server_isolated(tmp_path):
    p = tmp_path / ".paperagent" / "mcp.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"servers": {"bad": {"command": "missing-paperagent-mcp-executable", "allow_tools": {"echo": "network_read"}}}}))
    bridge = Bridge(tmp_path)
    try:
        assert bridge.discover() == {}
        assert describe_tools(tmp_path)
    finally:
        bridge.close()
