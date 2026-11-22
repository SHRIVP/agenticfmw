# Layer 6: CLI and demo

**What it does.** `pcagent serve` runs the API with uvicorn; `pcagent chat` is a REPL over HTTP.
`render()` turns a run result into text: one line per decision (`[ALLOW]`/`[ASK]`/`[DENY]`),
Jev scores indented under it, tool results cut to 200 characters, the AI text last. On
`awaiting_approval` it asks `Approve tool(args)? [y/N]` for each pending call and posts the answers.

**Why.** The CLI is a thin client, so the demo exercises the same HTTP path as any other client.
`render` is pure so it is testable without a server.

**What surprised us.**
- Superseded: Jev's `injected` question was reworded in Task 7 (see `07-eval.md`), and the quote now pauses by rule (`quote_policy_change: require_approval`). Original finding:
  Jev set `injected` at 0.5-0.6 for the start and quote calls (threshold 0.3), so they paused
  instead of auto-running. Beat 2 as written needs a lower `injected` or a tuned prompt.
- Resume across a server restart worked, but `chat --thread` skipped a blank first line, so the
  demo's "press Enter" never showed the pending call. Now `chat --thread <id>` calls
  `GET /threads/{id}` first and, if `pending` is not empty, goes straight to the approval prompt.
- A thread whose earlier run crashed part-way answers 409 `interrupted run`; the CLI says to start a new thread.
- qwen3:8b via Ollama stalled with 600 s timeouts and never attempted the bind in three tries.
- The CLI now survives EOF on stdin and non-JSON error bodies (500s).
