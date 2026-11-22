import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from langchain_core.messages import AIMessage

from fakes import ai_call, fake_tool, setup
from pcagent.api import create_app
from pcagent.config import load_config
from pcagent.gateway import CORE_PROMPT, compose_prompt
from pcagent.packs import Pack
from pcagent.packs.everywire import make_pack
from pcagent.policy.engine import PackPolicy
from pcagent.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


def test_core_prompt_has_no_everywire():
    assert "Everywire" not in CORE_PROMPT


def test_compose_prompt_sections():
    text = compose_prompt([Pack("billing", prompt="Billing things."), Pack("empty")])
    assert text.startswith(CORE_PROMPT)
    assert "\n\n## billing\nBilling things." in text
    assert "## empty" not in text


def app_with(graph, packs=()):
    @asynccontextmanager
    async def runtime():
        yield Runtime(graph, list(packs))
    return create_app(runtime)


async def test_get_packs_endpoint():
    graph = setup([], [])[0]
    packs = [{"name": "billing", "tools": [{"name": "billing__x", "short_name": "x", "label": "X",
                                             "read_only": True, "destructive": False, "source": "python"}]}]
    app = app_with(graph, packs)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=ASGITransport(app), base_url="http://t"
    ) as c:
        assert (await c.get("/packs")).json() == packs


async def test_thread_view_legacy_flag():
    old = fake_tool("add_vehicle", "ok")
    new = fake_tool("demo__x", "ok")
    graph = setup([ai_call(("c1", "add_vehicle", {})), AIMessage("a"),
                   ai_call(("c2", "demo__x", {})), AIMessage("b")], [old, new])[0]
    app = app_with(graph)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=ASGITransport(app), base_url="http://t"
    ) as c:
        await c.post("/threads/old/runs", json={"message": "go"})
        await c.post("/threads/new/runs", json={"message": "go"})
        assert (await c.get("/threads/old")).json()["legacy"] is True
        assert (await c.get("/threads/new")).json()["legacy"] is False


def test_everywire_make_pack_url_and_command():
    p = make_pack({"gwmcp": {"url": "https://pas.example/mcp"}})
    assert p.name == "everywire"
    gw = next(s for s in p.mcp_sources if s.name == "gwmcp")
    assert gw.spec.url == "https://pas.example/mcp"
    docs = next(s for s in p.mcp_sources if s.name == "claimdocs")
    assert docs.spec.command == sys.executable
    p2 = make_pack({"gwmcp": {"command": "uv", "args": ["run", "server.py"], "cwd": "../gwmcp",
                              "env": {"GW_MOCK": "true"}}})
    gw2 = next(s for s in p2.mcp_sources if s.name == "gwmcp")
    assert gw2.spec.command == "uv" and Path(gw2.spec.cwd).is_absolute()
    assert p.judge_hints and p.labels["quote_policy_change"]


def test_everywire_rules_load():
    p = make_pack({"gwmcp": {"url": "https://x"}})
    rules = PackPolicy.load(p.rules_path, "everywire").rules
    assert rules["bind_policy_change"] == "deny"
    assert rules["quote_policy_change"] == "require_approval"


def test_shipped_configs_parse():
    for name in ("pcagent.yaml", "pcagent.local.yaml"):
        cfg = load_config(ROOT / name)
        assert cfg.packs[0].use.startswith("pcagent.packs.everywire")


@pytest.mark.live
async def test_default_runtime_local(monkeypatch):
    from pcagent.runtime import default_runtime
    monkeypatch.chdir(ROOT)
    async with default_runtime(load_config(ROOT / "pcagent.local.yaml")) as rt:
        names = {t["name"] for p in rt.packs for t in p["tools"]}
        assert {"everywire__search_policies", "everywire__get_claim_notes"} <= names
