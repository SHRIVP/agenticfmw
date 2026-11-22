"""Checkpointer factory: Postgres when a URL is configured, in-memory otherwise."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@asynccontextmanager
async def open_checkpointer(database_url: str | None) -> AsyncIterator[BaseCheckpointSaver]:
    """Yield a saver. Postgres tables are created on first use (`setup()` is idempotent)."""
    if not database_url:  # None or "" (an empty DATABASE_URL= line in .env)
        yield InMemorySaver()
        return
    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        await saver.setup()
        yield saver
