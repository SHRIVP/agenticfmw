"""Resolve packs from config and open their tools (Python and MCP) under qualified names."""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from importlib.metadata import entry_points

from langchain_core.tools import BaseTool

from pcagent.config import PackRef
from pcagent.mcp_bridge import open_session, to_langchain_tool, tool_info
from pcagent.packs import Pack, PackError, python_langchain_tool, qualify
from pcagent.policy.engine import ToolInfo

GROUP = "pcagent.packs"


def _load_target(use: str):
    if ":" in use:
        module, attr = use.split(":", 1)
        try:
            return getattr(importlib.import_module(module), attr)
        except (ImportError, AttributeError) as e:
            raise PackError(f"cannot load pack {use!r}: {e}") from e
    for ep in entry_points(group=GROUP):
        if ep.name == use:
            return ep.load()
    raise PackError(f"no pack {use!r}: not 'module:attr' and no entry point in group {GROUP!r}")


def resolve_pack(ref: PackRef) -> Pack:
    target = _load_target(ref.use)
    if isinstance(target, Pack):
        if ref.settings:
            raise PackError(f"pack {ref.use!r} is a Pack instance and takes no settings; expose a factory")
        return target
    if callable(target):
        pack = target(ref.settings)
        if not isinstance(pack, Pack):
            raise PackError(f"pack factory {ref.use!r} returned {type(pack).__name__}, not Pack")
        return pack
    raise PackError(f"pack {ref.use!r} is neither a Pack nor a factory")


@dataclass
class LoadedPacks:
    packs: list[Pack]
    tools: list[BaseTool] = field(default_factory=list)
    info: dict[str, ToolInfo] = field(default_factory=dict)


async def _list_tools(session):
    return (await session.list_tools()).tools


@asynccontextmanager
async def open_packs(packs: list[Pack]) -> AsyncIterator[LoadedPacks]:
    names = [p.name for p in packs]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise PackError(f"duplicate pack names: {sorted(dupes)}")
    loaded = LoadedPacks(packs=list(packs))

    def add(tool: BaseTool, info: ToolInfo) -> None:
        if info.name in loaded.info:
            raise PackError(f"duplicate tool name {info.name!r}")
        loaded.info[info.name] = info
        loaded.tools.append(tool)

    stack = AsyncExitStack()
    try:
        for pack in packs:
            for pt in pack.python_tools.values():
                add(*python_langchain_tool(pack, pt))
            for src in pack.mcp_sources:
                try:
                    session = await open_session(stack, src.spec)
                    listed = await _list_tools(session)
                except Exception as e:
                    raise PackError(f"pack {pack.name!r}: MCP source {src.name!r} failed: {e}") from e
                for t in listed:
                    if src.include is not None and t.name not in src.include:
                        continue
                    if src.exclude and t.name in src.exclude:
                        continue
                    name = qualify(pack.name, t.name)
                    info = replace(tool_info(t), name=name, pack=pack.name, short_name=t.name,
                                   source="mcp", label=pack.labels.get(t.name))
                    add(to_langchain_tool(session, t, name=name), info)
    except BaseException as e:
        # Close connections first, then raise the load error itself (not the task group
        # wrapper that MCP's transports add while shutting down).
        try:
            await stack.aclose()
        except BaseException:
            pass
        raise e
    async with stack:
        yield loaded
