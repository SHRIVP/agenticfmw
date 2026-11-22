"""HTTP API: threads, runs and approvals over the compiled graph."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import get_settings
from .runs import NotAwaiting, ThreadBusy, resume, run_turn, thread_view
from .runtime import default_runtime


class RunRequest(BaseModel):
    message: str


class Approval(BaseModel):
    approved: bool
    note: str = ""


class ApprovalsRequest(BaseModel):
    decisions: dict[str, Approval]


def create_app(runtime: Callable[[], AbstractAsyncContextManager] | None = None) -> FastAPI:
    runtime = runtime or (lambda: default_runtime(get_settings()))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with runtime() as graph:
            app.state.graph = graph
            yield

    app = FastAPI(title="pcagent", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/threads")
    async def create_thread():
        return {"thread_id": str(uuid.uuid4())}

    @app.post("/threads/{thread_id}/runs")
    async def post_run(thread_id: str, body: RunRequest, request: Request):
        try:
            return await run_turn(request.app.state.graph, thread_id, body.message)
        except ThreadBusy as busy:
            return JSONResponse(
                {"detail": "awaiting approval", "pending": busy.pending}, status_code=409
            )

    @app.post("/threads/{thread_id}/approvals")
    async def post_approvals(thread_id: str, body: ApprovalsRequest, request: Request):
        decisions = {k: v.model_dump() for k, v in body.decisions.items()}
        try:
            return await resume(request.app.state.graph, thread_id, decisions)
        except NotAwaiting:
            raise HTTPException(409, "not awaiting approval")

    @app.get("/threads/{thread_id}")
    async def get_thread(thread_id: str, request: Request):
        view = await thread_view(request.app.state.graph, thread_id)
        if not view["messages"] and not view["audit"]:
            raise HTTPException(404, "unknown thread")
        return view

    return app
