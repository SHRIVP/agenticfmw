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


async def test_rejection_through_api():
    app, add = paused_app()
    async with app.router.lifespan_context(app), await client_for(app) as c:
        tid = await new_thread(c)
        await c.post(f"/threads/{tid}/runs", json={"message": "add a vehicle"})
        r = await c.post(
            f"/threads/{tid}/approvals", json={"decisions": {"c1": {"approved": False, "note": "no"}}}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "completed"
        assert add.calls == []
        assert any(d["source"] == "human" for d in body["decisions"])


async def test_unknown_id_through_api():
    app, add = paused_app()
    async with app.router.lifespan_context(app), await client_for(app) as c:
        tid = await new_thread(c)
        await c.post(f"/threads/{tid}/runs", json={"message": "add a vehicle"})
        r = await c.post(
            f"/threads/{tid}/approvals", json={"decisions": {"bogus": {"approved": True}}}
        )
        assert r.status_code == 200
        assert add.calls == []


async def test_concurrent_requests_one_wins():
    import asyncio

    from langchain_core.tools import StructuredTool

    started, release, calls = asyncio.Event(), asyncio.Event(), []

    async def slow(**kwargs):
        calls.append(kwargs)
        started.set()
        await release.wait()
        return "added"

    tool = StructuredTool.from_function(
        coroutine=slow,
        name="add_vehicle",
        description="slow",
        args_schema={"type": "object", "properties": {}, "additionalProperties": True},
    )
    app = make_app(
        [ai_call(("c1", "add_vehicle", {"vin": "X"})), AIMessage("Added.")], [tool], FakeJudge(BAD)
    )
    body = {"decisions": {"c1": {"approved": True}}}
    async with app.router.lifespan_context(app), await client_for(app) as c:
        tid = await new_thread(c)
        await c.post(f"/threads/{tid}/runs", json={"message": "add"})
        first = asyncio.create_task(c.post(f"/threads/{tid}/approvals", json=body))
        await asyncio.wait_for(started.wait(), 5)  # first approval is now in flight
        second = await c.post(f"/threads/{tid}/approvals", json=body)
        third = await c.post(f"/threads/{tid}/runs", json={"message": "hi"})
        release.set()
        r1 = await first
    assert (r1.status_code, second.status_code, third.status_code) == (200, 409, 409)
    assert second.json()["detail"] == "thread busy" and third.json()["detail"] == "thread busy"
    assert len(calls) == 1


async def test_interrupted_run_returns_409():
    # The model fails after a tool ran, so the checkpoint stops mid-run (next == ("agent",)).
    search = fake_tool("search_policies", "found")
    app = make_app([ai_call(("c1", "search_policies", {"q": "P"}))], [search])
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=ASGITransport(app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        tid = await new_thread(c)
        r = await c.post(f"/threads/{tid}/runs", json={"message": "find it"})
        assert r.status_code == 500
        r = await c.post(f"/threads/{tid}/runs", json={"message": "try again"})
        assert r.status_code == 409
        assert r.json() == {"detail": "interrupted run"}


# --- GET /threads and the web page ---------------------------------------


async def test_list_threads_empty():
    app = make_app([], [])
    async with app.router.lifespan_context(app), await client_for(app) as c:
        r = await c.get("/threads")
        assert r.status_code == 200
        assert r.json() == []


async def test_list_threads_titles_and_order():
    app = make_app([AIMessage("One."), AIMessage("Two.")], [])
    async with app.router.lifespan_context(app), await client_for(app) as c:
        a = await new_thread(c)
        b = await new_thread(c)
        await c.post(f"/threads/{a}/runs", json={"message": "Find policy PA-200001-01"})
        await c.post(f"/threads/{b}/runs", json={"message": "x" * 80})
        rows = (await c.get("/threads")).json()
        assert [r["thread_id"] for r in rows] == [b, a]
        assert rows[1]["title"] == "Find policy PA-200001-01"
        assert len(rows[0]["title"]) == 61 and rows[0]["title"].endswith("…")
        assert all(r["updated_at"] for r in rows)


async def test_list_threads_skips_unused():
    app = make_app([], [])
    async with app.router.lifespan_context(app), await client_for(app) as c:
        tid = await new_thread(c)
        assert tid not in [r["thread_id"] for r in (await c.get("/threads")).json()]


async def test_root_serves_page():
    app = make_app([], [])
    async with app.router.lifespan_context(app), await client_for(app) as c:
        r = await c.get("/")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "pcagent" in r.text


async def test_static_served():
    app = make_app([], [])
    async with app.router.lifespan_context(app), await client_for(app) as c:
        assert (await c.get("/static/index.html")).status_code == 200
