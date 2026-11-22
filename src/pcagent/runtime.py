"""Production wiring: MCP tools + checkpointer + policy engine + model -> compiled graph."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.graph.state import CompiledStateGraph

from .checkpoint import open_checkpointer
from .config import load_env
from .gateway import make_model
from .graph import build_graph
from .mcp_bridge import MCPToolbox, load_servers
from .policy.engine import Policy, PolicyEngine
from .policy.jev import JevJudge


@asynccontextmanager
async def default_runtime(settings) -> AsyncIterator[CompiledStateGraph]:
    load_env()  # explicitly, rather than relying on litellm's import side effect
    async with MCPToolbox(load_servers(settings.servers_path)) as toolbox:
        async with open_checkpointer(settings.database_url) as saver:
            engine = PolicyEngine(Policy.load(settings.policy_path), JevJudge())
            model = make_model(settings.model)
            yield build_graph(model, toolbox.tools, toolbox.info, engine, saver)
