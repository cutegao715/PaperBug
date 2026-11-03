"""Opt-in stdio MCP bridge. One owning coroutine per server session."""
import asyncio
import atexit
from concurrent.futures import Future
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    allow_tools: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=30, gt=0, le=120)


def tool_id(server, name):
    # Hex components cannot contain separators: names cannot collide.
    return "mcp_" + server.encode().hex() + "_" + name.encode().hex()


class Session:
    def __init__(self, name, config):
        self.name, self.config = name, config
        self.ready = Future()
        self.requests = queue.Queue()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException:
            if not self.ready.done():
                self.ready.set_exception(RuntimeError("MCP server unavailable"))
            log.warning("MCP session ended: %s", self.name)
        finally:
            self.stopping.set()
            while not self.requests.empty():
                _, _, future = self.requests.get_nowait()
                if not future.done():
                    future.set_exception(RuntimeError("MCP session closed"))

    async def _serve(self):
        # Optional dependencies stay out of the base application's import graph.
        from mcp import ClientSession
        from mcp.types import PaginatedRequestParams
        from mcp.client.stdio import stdio_client, StdioServerParameters
        config = self.config
        params = StdioServerParameters(command=config.command, args=config.args, env=config.env or None)
        with open(os.devnull, "w") as stderr:
            async with stdio_client(params, errlog=stderr) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=config.timeout_s) as client:
                    await asyncio.wait_for(client.initialize(), config.timeout_s)
                    tools = []
                    cursor = None
                    for _ in range(100):
                        page = await asyncio.wait_for(client.list_tools(params=PaginatedRequestParams(cursor=cursor)), config.timeout_s)
                        tools.extend(page.tools)
                        cursor = page.next_cursor
                        if not cursor:
                            break
                    self.ready.set_result(tools)
                    while not self.stopping.is_set():
                        try:
                            name, args, future = self.requests.get_nowait()
                        except queue.Empty:
                            await asyncio.sleep(.02)
                            continue
                        try:
                            result = await asyncio.wait_for(client.call_tool(name, args), config.timeout_s)
                            future.set_result(result.model_dump(mode="json", by_alias=True))
                        except BaseException:
                            future.set_exception(RuntimeError("MCP call failed or timed out"))
                            break

    def call(self, name, args):
        if self.stopping.is_set():
            raise RuntimeError("MCP session closed")
        future = Future()
        self.requests.put((name, args, future))
        try:
            return future.result(timeout=self.config.timeout_s + 5)
        except Exception:
            self.close()
            raise RuntimeError("MCP call failed or timed out") from None

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=self.config.timeout_s + 5)


class Bridge:
    def __init__(self, root):
        self.path = Path(root) / ".paperagent" / "mcp.json"
        self.fingerprint = None
        self.sessions = {}
        self.descriptors = {}
        self.targets = {}
        self.lock = threading.RLock()

    def discover(self):
        from paperagent.tools.registry import ToolDescriptor
        from paperagent.quality.policies import DEFAULT_POLICY
        with self.lock:
            try:
                text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
            except (OSError, UnicodeError):
                self.close()
                log.warning("Cannot read MCP configuration")
                return {}
            if text == self.fingerprint and all(not session.stopping.is_set() for session in self.sessions.values()):
                return dict(self.descriptors)
            self.close()
            self.fingerprint = text
            if not text:
                return {}
            try:
                servers = json.loads(text)["servers"]
                if not isinstance(servers, dict):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                log.warning("Invalid MCP configuration")
                return {}
            for name, raw in servers.items():
                session = None
                try:
                    config = ServerConfig.model_validate(raw)
                    if not config.allow_tools:
                        continue
                    session = Session(name, config)
                    tools = session.ready.result(timeout=config.timeout_s + 5)
                    self.sessions[name] = session
                    for tool in tools:
                        permission = config.allow_tools.get(tool.name)
                        if permission not in DEFAULT_POLICY:
                            continue
                        ident = tool_id(name, tool.name)
                        self.descriptors[ident] = ToolDescriptor(
                            tool_id=ident, description=tool.description or tool.name,
                            input_schema=tool.input_schema, permission=permission,
                            timeout_s=config.timeout_s)
                        self.targets[ident] = (name, tool.name)
                except Exception:
                    if session:
                        session.close()
                    log.warning("MCP discovery failed: %s", name)
            return dict(self.descriptors)

    def call(self, ident, args):
        from jsonschema import validate
        from paperagent.quality.policies import ActionSpec, PermissionGate
        from paperagent.product_config import load
        with self.lock:
            descriptor = self.discover().get(ident)
            if descriptor is None or ident in load().tools.disabled:
                raise ValueError("MCP tool not allowed")
            if PermissionGate().check(ActionSpec(descriptor.permission)) != "allow":
                raise ValueError("MCP permission denied")
            try:
                validate(args, descriptor.input_schema)
            except Exception:
                raise ValueError("MCP input schema validation failed") from None
            server, name = self.targets[ident]
            started = time.perf_counter()
            data = self.sessions[server].call(name, args)
            return {"content": data.get("content", []),
                    "structured_content": data.get("structuredContent"),
                    "errors": ["MCP tool reported failure"] if data.get("isError") else [],
                    "provenance": f"mcp:{server}; elapsed_s={time.perf_counter()-started:.3f}"}

    def close(self):
        self.fingerprint = None
        for session in self.sessions.values():
            session.close()
        self.sessions.clear()
        self.descriptors.clear()
        self.targets.clear()


_bridges = {}
_lock = threading.Lock()


def get_bridge(root):
    key = str(Path(root).resolve())
    with _lock:
        if key not in _bridges:
            _bridges[key] = Bridge(key)
        return _bridges[key]


def close_all():
    with _lock:
        for bridge in _bridges.values():
            bridge.close()
        _bridges.clear()


atexit.register(close_all)
