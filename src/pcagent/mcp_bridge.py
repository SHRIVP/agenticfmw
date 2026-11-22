"""MCP servers -> LangChain tools plus ToolInfo for the policy engine."""

import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

import mcp.types as mt
import yaml
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from pcagent.policy.engine import ToolInfo


class ToolboxError(RuntimeError):
    pass


@dataclass
class ServerSpec:
    name: str
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None


def _expand(value):
    return os.path.expandvars(value) if isinstance(value, str) else value


def load_servers(path: Path) -> list[ServerSpec]:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    specs = []
    for name, cfg in (raw.get("servers") or {}).items():
        cfg = cfg or {}
        if bool(cfg.get("url")) == bool(cfg.get("command")):
            raise ToolboxError(f"server {name!r}: set exactly one of url or command")
        cwd = _expand(cfg.get("cwd"))
        if cwd:
            cwd = str((path.parent / cwd).resolve()) if not Path(cwd).is_absolute() else cwd
        specs.append(
            ServerSpec(
                name=name,
                url=_expand(cfg.get("url")),
                command=_expand(cfg.get("command")),
                args=[_expand(a) for a in cfg.get("args", [])],
                env={k: _expand(str(v)) for k, v in (cfg.get("env") or {}).items()},
                cwd=cwd,
            )
        )
    return specs


def tool_info(tool: mt.Tool) -> ToolInfo:
    a = tool.annotations
    return ToolInfo(
        name=tool.name,
        description=tool.description or "",
        read_only=bool(a and a.read_only_hint),
        destructive=bool(a and a.destructive_hint),
    )


def to_langchain_tool(session: ClientSession, tool: mt.Tool) -> BaseTool:
    async def call(**kwargs) -> str:
        res = await session.call_tool(tool.name, kwargs)
        text = "\n".join(c.text for c in res.content if c.type == "text")
        if res.is_error:
            raise ToolException(text)
        return text

    return StructuredTool(
        name=tool.name,
        description=tool.description or tool.name,
        args_schema=tool.input_schema,
        coroutine=call,
        handle_tool_error=True,
    )


class MCPToolbox:
    def __init__(self, specs: list[ServerSpec]):
        self.specs = specs
        self.tools: list[BaseTool] = []
        self.info: dict[str, ToolInfo] = {}
        self._stack = AsyncExitStack()

    async def _connect(self, spec: ServerSpec) -> ClientSession:
        if spec.url:
            streams = await self._stack.enter_async_context(streamable_http_client(spec.url))
            read, write = streams[0], streams[1]
        else:
            params = StdioServerParameters(
                command=spec.command,
                args=spec.args,
                env={**os.environ, **spec.env},
                cwd=spec.cwd,
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def __aenter__(self) -> "MCPToolbox":
        try:
            for spec in self.specs:
                try:
                    session = await self._connect(spec)
                    listed = await session.list_tools()
                except Exception as e:
                    raise ToolboxError(f"server {spec.name!r} failed: {e}") from e
                for t in listed.tools:
                    if t.name in self.info:
                        raise ToolboxError(
                            f"duplicate tool name {t.name!r} (server {spec.name!r})"
                        )
                    self.info[t.name] = tool_info(t)
                    self.tools.append(to_langchain_tool(session, t))
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()
