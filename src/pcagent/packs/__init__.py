"""Packs: everything specific to one system (tools, rules, prompt, labels, evals, Jev hints).

Tools come from Python functions (`@pack.tool`) or MCP servers (`pack.mcp(...)`). Either
way they reach the agent as `<pack>__<tool>` and pass the same policy path.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langchain_core.tools.base import create_schema_from_function

from pcagent.mcp_bridge import ServerSpec
from pcagent.policy.engine import ToolInfo

PACK_NAME = re.compile(r"^[a-z0-9_]{1,20}$")
TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SEP = "__"


class PackError(ValueError):
    """A pack, or one of its tools, is defined wrongly. Raised at load time."""


def qualify(pack: str, tool: str) -> str:
    name = f"{pack}{SEP}{tool}"
    if not TOOL_NAME.match(name):
        raise PackError(f"pack {pack!r}: tool {tool!r} gives invalid tool name {name!r}")
    return name


@dataclass
class PythonTool:
    name: str
    fn: Callable[..., Any]
    description: str
    read_only: bool
    destructive: bool


@dataclass
class McpSource:
    name: str
    spec: ServerSpec
    include: list[str] | None = None
    exclude: list[str] | None = None


class Pack:
    def __init__(
        self,
        name: str,
        prompt: str = "",
        rules: str | Path | None = None,
        labels: dict[str, str] | None = None,
        evals: str | Path | None = None,
        judge_hints: list[str] | None = None,
        base_dir: str | Path | None = None,
    ):
        if not PACK_NAME.match(name or "") or SEP in name:
            raise PackError(f"invalid pack name {name!r}: use [a-z0-9_]{{1,20}} without '__'")
        self.name = name
        self.prompt = prompt
        self.labels = dict(labels or {})
        self.judge_hints = list(judge_hints or [])
        if base_dir is None:
            base_dir = Path(inspect.stack()[1].filename).resolve().parent
        self.base_dir = Path(base_dir)
        self._rules = rules
        self._evals = evals
        self.python_tools: dict[str, PythonTool] = {}
        self.mcp_sources: list[McpSource] = []

    @property
    def rules_path(self) -> Path | None:
        return self.base_dir / self._rules if self._rules else None

    @property
    def evals_path(self) -> Path | None:
        return self.base_dir / self._evals if self._evals else None

    def tool(self, *, read_only: bool = False, destructive: bool | None = None):
        if read_only and destructive:
            raise PackError(f"pack {self.name!r}: a tool cannot be both read_only and destructive")

        def register(fn):
            doc = inspect.getdoc(fn)
            if not doc:
                raise PackError(f"pack {self.name!r}: tool {fn.__name__!r} needs a docstring (its description)")
            qualify(self.name, fn.__name__)  # validates the name now, not at load
            self.python_tools[fn.__name__] = PythonTool(
                name=fn.__name__, fn=fn, description=doc, read_only=read_only,
                destructive=(not read_only) if destructive is None else destructive,
            )
            return fn

        return register

    def mcp(self, name: str, *, url: str | None = None, command: str | None = None,
            args: list[str] | tuple = (), env: dict[str, str] | None = None, cwd: str | None = None,
            include: list[str] | None = None, exclude: list[str] | None = None) -> None:
        if bool(url) == bool(command):
            raise PackError(f"pack {self.name!r}: MCP source {name!r} needs exactly one of url or command")
        spec = ServerSpec(name=f"{self.name}/{name}", url=url, command=command,
                          args=list(args), env=dict(env or {}), cwd=cwd)
        self.mcp_sources.append(McpSource(name, spec, include, exclude))


def python_langchain_tool(pack: Pack, pt: PythonTool) -> tuple[BaseTool, ToolInfo]:
    qualified = qualify(pack.name, pt.name)
    fn = pt.fn

    async def call(**kwargs) -> str:
        try:
            if inspect.iscoroutinefunction(fn):
                out = await fn(**kwargs)
            else:
                out = await asyncio.to_thread(fn, **kwargs)
        except ToolException:
            raise
        except Exception as e:  # any tool bug becomes an error result, never a crash
            raise ToolException(f"{type(e).__name__}: {e}") from e
        return out if isinstance(out, str) else str(out)

    tool = StructuredTool.from_function(
        coroutine=call, name=qualified, description=pt.description,
        args_schema=create_schema_from_function(qualified, fn),
    )
    info = ToolInfo(
        name=qualified, description=pt.description, read_only=pt.read_only,
        destructive=pt.destructive, pack=pack.name, short_name=pt.name, source="python",
        label=pack.labels.get(pt.name),
    )
    return tool, info


__all__ = ["Pack", "PackError", "PythonTool", "McpSource", "qualify", "python_langchain_tool"]
