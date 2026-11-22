# Layer 4: Checkpoints

## What it does
`open_checkpointer(database_url)` yields the saver the graph writes its state to.
With a URL it is an `AsyncPostgresSaver` (tables created by an idempotent `setup()`);
with `None` it is an `InMemorySaver`, used by tests and quick local runs.

A checkpoint is a snapshot of the whole graph state after each step: the message
list, the audit trail, the pending-approval decisions, and which node runs next
(`next == ("approval",)` when paused). Pending interrupts are stored with it.

The `thread_id` in `config["configurable"]` is the key. Every checkpoint for one
conversation shares a thread_id, and the latest one is what `aget_state` and
`Command(resume=...)` load. One thread is one conversation and one approval queue.

## Why resume survives a restart
Nothing about the paused run lives in the process. The graph object is stateless:
node functions, tools and policy are rebuilt from code, and the data is read back
from Postgres by thread_id. So a new process (or a new graph instance on a new
connection) sees `next == ("approval",)`, and resuming re-enters the approval node,
which receives the human's decision and carries on. The live test proves this by
closing the first connection entirely before the second graph opens its own.

## Why built this way
- A context manager, because the Postgres saver owns a connection that must close;
  the FastAPI lifespan (next task) opens it once for the server's life.
- `None` falls back to memory so offline tests need no database.
- The compose `db` uses the pgvector image so later layers can add embeddings
  without changing infrastructure.

## Notes
- The api image needs the `serve` subcommand (Task 6); `docker compose config` validates now.
- Run `docker compose up -d db` before `pytest -m live`.
- Test helpers (`setup`, `ai_call`, `ask`) moved from test_graph.py into tests/fakes.py
  so the Postgres test reuses the same scenario.
