from contextlib import asynccontextmanager

import httpx
from httpx import ASGITransport
from langchain_core.messages import AIMessage

from fakes import BAD, FakeJudge, ai_call, fake_tool, setup
from pcagent.api import create_app


def make_app(responses, tools, judge=None):
    graph = setup(responses, tools, judge)[0]

    @asynccontextmanager
    async def runtime():
        yield graph

    return create_app(runtime)


async def client_for(app):
    return httpx.AsyncClient(transport=ASGITransport(app), base_url="http://t")


async def new_thread(c) -> str:
    r = await c.post("/threads")
    assert r.status_code == 200
    return r.json()["thread_id"]


async def test_create_thread_and_complete_run():
    app = make_app([AIMessage("Hello.")], [])
    async with app.router.lifespan_context(app), await client_for(app) as c:
        assert (await c.get("/health")).json() == {"ok": True}
        tid = await new_thread(c)
        r = await c.post(f"/threads/{tid}/runs", json={"message": "hi"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "completed"
        assert body["pending"] == []
        assert body["messages"][-1]["type"] == "ai"
        assert [m["type"] for m in body["messages"]] == ["human", "ai"]


def paused_app():
    add = fake_tool("add_vehicle", "added")
    app = make_app(
        [ai_call(("c1", "add_vehicle", {"vin": "X"})), AIMessage("Added.")], [add], FakeJudge(BAD)
    )
    return app, add


async def test_run_pauses_then_approval_completes():
    app, add = paused_app()
    async with app.router.lifespan_context(app), await client_for(app) as c:
        tid = await new_thread(c)
        r = await c.post(f"/threads/{tid}/runs", json={"message": "add a vehicle"})
        body = r.json()
        assert body["status"] == "awaiting_approval"
        assert len(body["pending"]) == 1
        assert add.calls == []

        r = await c.post(
            f"/threads/{tid}/approvals", json={"decisions": {"c1": {"approved": True}}}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "completed"
        assert body["pending"] == []
        assert any(d["source"] == "human" for d in body["decisions"])
        assert body["messages"][-1]["content"] == "Added."
        assert "human" not in [m["type"] for m in body["messages"]]
        assert add.calls == [{"vin": "X"}]

        view = (await c.get(f"/threads/{tid}")).json()
        assert set(view) == {"thread_id", "messages", "pending", "audit"}
        assert view["pending"] == []


async def test_message_while_awaiting_returns_409():
    app, add = paused_app()
    async with app.router.lifespan_context(app), await client_for(app) as c:
        tid = await new_thread(c)
        await c.post(f"/threads/{tid}/runs", json={"message": "add a vehicle"})
        r = await c.post(f"/threads/{tid}/runs", json={"message": "hello?"})
        assert r.status_code == 409
        assert r.json()["detail"] == "awaiting approval"
        assert len(r.json()["pending"]) == 1


async def test_approval_when_not_awaiting_returns_409():
    app = make_app([AIMessage("Hello.")], [])
    async with app.router.lifespan_context(app), await client_for(app) as c:
        tid = await new_thread(c)
        await c.post(f"/threads/{tid}/runs", json={"message": "hi"})
        r = await c.post(f"/threads/{tid}/approvals", json={"decisions": {"c1": {"approved": True}}})
        assert r.status_code == 409


async def test_unknown_thread_404():
    app = make_app([], [])
    async with app.router.lifespan_context(app), await client_for(app) as c:
        assert (await c.get("/threads/nope")).status_code == 404
