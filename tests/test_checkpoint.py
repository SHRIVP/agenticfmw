import os

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from fakes import BAD, FakeJudge, ai_call, ask, fake_tool, setup
from pcagent.checkpoint import open_checkpointer
from pcagent.graph import build_graph

LOCAL_URL = "postgresql://pcagent:pcagent@localhost:5432/pcagent"


async def test_memory_when_no_url():
    async with open_checkpointer(None) as saver:
        assert isinstance(saver, InMemorySaver)


async def test_memory_when_url_is_empty_string():
    # `cp .env.example .env` used to leave DATABASE_URL="" and try Postgres with it.
    async with open_checkpointer("") as saver:
        assert isinstance(saver, InMemorySaver)


def test_load_env_puts_dotenv_keys_in_environ(tmp_path, monkeypatch):
    from pcagent.config import load_env

    (tmp_path / ".env").write_text("PCAGENT_TEST_KEY=from-dotenv\nPCAGENT_TEST_SET=from-file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PCAGENT_TEST_KEY", raising=False)
    monkeypatch.setenv("PCAGENT_TEST_SET", "from-shell")
    load_env()
    assert os.environ["PCAGENT_TEST_KEY"] == "from-dotenv"
    assert os.environ["PCAGENT_TEST_SET"] == "from-shell"  # the shell wins
    monkeypatch.delenv("PCAGENT_TEST_KEY")


async def test_default_runtime_loads_env_first(monkeypatch):
    from pcagent import runtime

    class Loaded(Exception):
        pass

    def boom():
        raise Loaded

    monkeypatch.setattr(runtime, "load_env", boom)
    with pytest.raises(Loaded):
        async with runtime.default_runtime(None):
            pass


@pytest.mark.live
async def test_postgres_resume_across_connections():
    url = os.environ.get("DATABASE_URL") or LOCAL_URL
    add = fake_tool("add_vehicle", "added")
    async with open_checkpointer(url) as saver_a:
        graph_a, cfg, model, info, engine, _ = setup(
            [ai_call(("c1", "add_vehicle", {"vin": "X"})), AIMessage("Added.")], [add], FakeJudge(BAD), saver_a
        )
        await graph_a.ainvoke(ask("add a vehicle"), cfg)
    # first connection is closed; a fresh one (a "restart") must find the paused run
    async with open_checkpointer(url) as saver_b:
        graph_b = build_graph(model, [add], info, engine, saver_b)
        assert (await graph_b.aget_state(cfg)).next == ("approval",)
        assert add.calls == []
        out = await graph_b.ainvoke(Command(resume={"c1": {"approved": True, "note": ""}}), cfg)
    assert add.calls == [{"vin": "X"}]
    assert out["messages"][-1].content == "Added."
