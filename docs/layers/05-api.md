# Layer 5: API

## What it does
`create_app()` wraps the compiled graph in a small FastAPI service. A **thread** is
one conversation (and one approval queue, keyed by `thread_id` in the checkpointer).
A **run** sends one user message and drives the graph until it finishes or pauses.
An **approval** answers the paused run's pending tool calls and resumes it.

| Route | Purpose |
|---|---|
| `POST /threads` | new `thread_id` (a uuid; no state is stored until the first run) |
| `POST /threads/{id}/runs` `{"message"}` | run result |
| `POST /threads/{id}/approvals` `{"decisions": {call_id: {"approved", "note"}}}` | run result |
| `GET /threads/{id}` | `thread_id, messages, pending, audit`; 404 if no checkpoint |
| `GET /health` | `{"ok": true}` |

A run result is `status` (`completed` or `awaiting_approval`), `messages` and
`decisions` added by this call only, and `pending` (calls waiting on a person).
The helpers live in `runs.py` and take optional `callbacks` (for tracing, later).

## Why a busy thread is a 409
A paused thread has a tool call that is half-decided. A new user message would
append after an AI message with unanswered tool calls, which breaks the model's
message contract and lets a user talk past a pending approval. So `run_turn`
raises `ThreadBusy` and the API returns 409 with the `pending` list, so the client
can show what to approve. Likewise approving a thread that is not paused is a 409
(`NotAwaiting`), which makes a double-submitted approval harmless.

The graph is built once in the lifespan (`app.state.graph`), because it holds the
MCP sessions and the checkpointer connection. `default_runtime` wires them.

## Example
    curl -s -X POST localhost:8000/threads                     # {"thread_id": "T"}
    curl -s -X POST localhost:8000/threads/T/runs -H 'content-type: application/json' \
      -d '{"message": "Add a 2021 Civic to PA-100001-01"}'     # awaiting_approval
    curl -s -X POST localhost:8000/threads/T/approvals -H 'content-type: application/json' \
      -d '{"decisions": {"<tool_call_id>": {"approved": true, "note": "ok"}}}'
    curl -s localhost:8000/threads/T

## Surprises
Tests run the lifespan explicitly with `app.router.lifespan_context(app)`;
`ASGITransport` does not. Unknown-thread detection checks for an empty state.
