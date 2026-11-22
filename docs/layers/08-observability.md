> **Superseded** by [11 · Telemetry](11-telemetry.md): Langfuse is now reached over OpenTelemetry.

# Layer: observability (Langfuse tracing)

## What it does
`pcagent.tracing.callbacks()` returns `[langfuse CallbackHandler()]` when
`LANGFUSE_PUBLIC_KEY` is set, and `[]` otherwise. The API passes it to `run_turn`
and `resume`, which put it in the graph config, so each run becomes one trace
(agent -> policy -> approval -> tools). Set `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` to turn it on.

## Why this way
- Tracing is opt-in. With no keys nothing is imported or sent, so tests and
  local runs are unaffected.
- The import is lazy, and `callbacks()` runs per request inside a try: if the handler
  can't be built (bad keys or host), it logs a warning with the exception type only and
  returns `[]`, so a misconfigured Langfuse turns tracing off instead of failing runs.
- Verified against langfuse 5.2.0: `from langfuse.langchain import CallbackHandler`;
  constructor is `CallbackHandler(*, public_key=None, trace_context=None)`.
  It reads keys from the environment.
- Policy decisions as span metadata: not done. The 5.x handler has no simple
  per-node metadata hook, so we rely on the graph state. Decisions are in
  `pending`/`audit` and appear in the policy span's output in the trace.

## What surprised us
- Constructing the handler does not block on the network; the client exports in
  the background. The test still sets `LANGFUSE_TRACING_ENABLED=false` and an
  unroutable host to be safe.
- Only the server needs the keys; the CLI itself is not traced.

## Not done
- Manual trace check and `docs/img/trace.png` were skipped: no Langfuse keys or
  account in this environment. Run with keys to see traces.
