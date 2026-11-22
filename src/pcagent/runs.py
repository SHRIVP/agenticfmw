"""Run helpers: drive the graph for one thread and shape results as plain dicts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from . import telemetry


class ThreadBusy(Exception):
    """The thread is awaiting approval and cannot take a new message."""

    def __init__(self, pending: list[dict]):
        super().__init__("awaiting approval")
        self.pending = pending


class NotAwaiting(Exception):
    """The thread has no paused run to resume."""


class InterruptedRun(Exception):
    """An earlier run on this thread crashed part-way (e.g. the model or a node raised).

    Its checkpoint may hold tool calls with no answer, which providers reject, so the
    thread takes no new messages. Recovery: start a new thread.
    """


class ThreadLocked(Exception):
    """Another request is already driving this thread."""


# Per process only: with several replicas this would need a database lock.
_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def _thread_lock(thread_id: str):
    """Hold the thread for check-plus-invoke; fail at once (no queueing) if it is held."""
    lock = _locks.setdefault(thread_id, asyncio.Lock())
    if lock.locked():
        raise ThreadLocked(thread_id)
    await lock.acquire()  # a free lock is taken without suspending, so no interleaving
    try:
        yield
    finally:
        lock.release()
        _locks.pop(thread_id, None)


def serialize(msg) -> dict:
    out: dict[str, Any] = {"type": msg.type, "content": msg.content}
    if getattr(msg, "tool_calls", None):
        out["tool_calls"] = msg.tool_calls
    if getattr(msg, "tool_call_id", None):
        out["tool_call_id"] = msg.tool_call_id
    if getattr(msg, "status", None):
        out["status"] = msg.status
    return out


def _cfg(thread_id: str, callbacks: list | None = None) -> dict:
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if callbacks:
        cfg["callbacks"] = callbacks
    return cfg


def _awaiting(snap) -> bool:
    return tuple(snap.next) == ("approval",)


def _waiting(snap) -> list[dict]:
    """Decisions still waiting on a person (empty unless paused)."""
    if not _awaiting(snap):
        return []
    return [d for d in snap.values.get("pending", []) if d["action"] == "require_approval"]


async def _finish(graph, cfg: dict, n_messages: int, n_audit: int) -> dict:
    snap = await graph.aget_state(cfg)
    values = snap.values
    return {
        "status": "awaiting_approval" if _awaiting(snap) else "completed",
        "messages": [serialize(m) for m in values.get("messages", [])[n_messages:]],
        "pending": _waiting(snap),
        "decisions": list(values.get("audit", []))[n_audit:],
    }


async def _counts(graph, cfg: dict):
    snap = await graph.aget_state(cfg)
    return snap, len(snap.values.get("messages", [])), len(snap.values.get("audit", []))


async def run_turn(graph, thread_id: str, message: str, callbacks: list | None = None) -> dict:
    cfg = _cfg(thread_id, callbacks)
    async with _thread_lock(thread_id):
        snap, n_msgs, n_audit = await _counts(graph, cfg)
        if _awaiting(snap):
            raise ThreadBusy(_waiting(snap))
        if snap.next:  # stopped mid-run, not at the approval interrupt
            raise InterruptedRun(thread_id)
        with telemetry.span("pcagent.run", thread_id=thread_id, kind="run") as s:
            await graph.ainvoke({"messages": [HumanMessage(message)]}, cfg)
            result = await _finish(graph, cfg, n_msgs, n_audit)
            telemetry.set_attrs(s, status=result["status"])
        return result


async def resume(graph, thread_id: str, decisions: dict, callbacks: list | None = None) -> dict:
    cfg = _cfg(thread_id, callbacks)
    async with _thread_lock(thread_id):
        snap, n_msgs, n_audit = await _counts(graph, cfg)
        if not _awaiting(snap):
            raise NotAwaiting(thread_id)
        with telemetry.span("pcagent.run", thread_id=thread_id, kind="resume") as s:
            await graph.ainvoke(Command(resume=decisions), cfg)
            result = await _finish(graph, cfg, n_msgs, n_audit)
            telemetry.set_attrs(s, status=result["status"])
        return result


async def thread_view(graph, thread_id: str) -> dict:
    snap = await graph.aget_state(_cfg(thread_id))
    values = snap.values
    return {
        "thread_id": thread_id,
        "messages": [serialize(m) for m in values.get("messages", [])],
        "pending": _waiting(snap),
        "audit": list(values.get("audit", [])),
        # Tool names without a pack prefix come from before packs (v1): such a thread can't continue.
        "legacy": any("__" not in c["name"] for m in values.get("messages", [])
                      for c in (getattr(m, "tool_calls", None) or [])),
    }


TITLE_LIMIT = 60


def _title(messages) -> str | None:
    for m in messages:
        if m.type == "human":
            text = " ".join(str(m.text if hasattr(m, "text") else m.content).split())
            return text if len(text) <= TITLE_LIMIT else text[:TITLE_LIMIT] + "…"
    return None


async def list_threads(graph) -> list[dict]:
    """Conversations in the checkpointer, newest first; threads with no user message are skipped."""
    latest: dict[str, str] = {}
    async for tup in graph.checkpointer.alist(None):
        tid = tup.config["configurable"]["thread_id"]
        ts = tup.checkpoint["ts"]
        if ts > latest.get(tid, ""):
            latest[tid] = ts
    rows = []
    for tid, ts in latest.items():
        snap = await graph.aget_state(_cfg(tid))
        title = _title(snap.values.get("messages", []))
        if title:
            rows.append({"thread_id": tid, "title": title, "updated_at": ts})
    return sorted(rows, key=lambda r: r["updated_at"], reverse=True)
