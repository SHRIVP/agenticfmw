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
