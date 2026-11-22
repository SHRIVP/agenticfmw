"""HTTP API: threads, runs and approvals over the compiled graph."""

from __future__ import annotations

import uuid
from pathlib import Path
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import get_settings
from .runs import InterruptedRun, NotAwaiting, ThreadBusy, ThreadLocked, list_threads, resume, run_turn, thread_view
from .runtime import default_runtime
from .tracing import callbacks


WEB_DIR = Path(__file__).with_name("web")


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

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/threads")
    async def get_threads(request: Request):
        return await list_threads(request.app.state.graph)

    @app.post("/threads")
    async def create_thread():
        return {"thread_id": str(uuid.uuid4())}

    @app.post("/threads/{thread_id}/runs")
    async def post_run(thread_id: str, body: RunRequest, request: Request):
        try:
            return await run_turn(
                request.app.state.graph, thread_id, body.message, callbacks=callbacks()
            )
        except ThreadBusy as busy:
            return JSONResponse(
                {"detail": "awaiting approval", "pending": busy.pending}, status_code=409
            )
        except ThreadLocked:
            return JSONResponse({"detail": "thread busy"}, status_code=409)
        except InterruptedRun:  # recovery: start a new thread
            return JSONResponse({"detail": "interrupted run"}, status_code=409)

    @app.post("/threads/{thread_id}/approvals")
    async def post_approvals(thread_id: str, body: ApprovalsRequest, request: Request):
        decisions = {k: v.model_dump() for k, v in body.decisions.items()}
        try:
            return await resume(
                request.app.state.graph, thread_id, decisions, callbacks=callbacks()
            )
        except NotAwaiting:
            raise HTTPException(409, "not awaiting approval")
        except ThreadLocked:
            raise HTTPException(409, "thread busy")

    @app.get("/threads/{thread_id}")
    async def get_thread(thread_id: str, request: Request):
        view = await thread_view(request.app.state.graph, thread_id)
        if not view["messages"] and not view["audit"]:
            raise HTTPException(404, "unknown thread")
        return view

    return app
