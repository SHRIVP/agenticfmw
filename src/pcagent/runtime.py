"""Production wiring: pcagent.yaml -> packs + policy + judge + model + checkpointer -> graph."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from .checkpoint import open_checkpointer
from . import telemetry
from .config import AppConfig, TracingConfig, load_env
from .gateway import compose_prompt, make_model
from .graph import build_graph
from .packs.loader import open_packs, resolve_pack
from .policy.engine import PackPolicy, PolicyEngine, PolicySet
from .policy.judges import make_judge, thresholds_from


@dataclass
class Runtime:
    graph: Any
    packs: list[dict] = field(default_factory=list)  # the GET /packs view
    tracing: TracingConfig = field(default_factory=TracingConfig)


def packs_view(packs, info) -> list[dict]:
    return [
        {"name": p.name, "tools": [
            {"name": i.name, "short_name": i.short_name, "label": i.label or i.short_name,
             "read_only": i.read_only, "destructive": i.destructive, "source": i.source}
            for i in info.values() if i.pack == p.name
        ]}
        for p in packs
    ]


def policy_set(config: AppConfig, packs) -> PolicySet:
    return PolicySet(
        global_rules=dict(config.rules),
        packs={p.name: PackPolicy.load(p.rules_path, p.name) for p in packs},
        thresholds=thresholds_from(config.judge),
        hints={p.name: p.judge_hints for p in packs},
    )


@asynccontextmanager
async def default_runtime(config: AppConfig) -> AsyncIterator[Runtime]:
    load_env()  # explicitly, rather than relying on litellm's import side effect
    telemetry.setup_tracing(config.tracing)
    packs = [resolve_pack(ref) for ref in config.packs]
    policies = policy_set(config, packs)  # rules errors surface before any server starts
    async with open_packs(packs) as loaded:
        async with open_checkpointer(config.checkpointer) as saver:
            engine = PolicyEngine(policies, make_judge(config.judge))
            graph = build_graph(make_model(config.model), loaded.tools, loaded.info, engine, saver,
                                system_prompt=compose_prompt(packs))
            yield Runtime(graph, packs_view(packs, loaded.info), config.tracing)
