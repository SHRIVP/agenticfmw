# 11 · Telemetry (OpenTelemetry)

**What it does.** pcagent emits its own spans: `pcagent.run` (thread, kind, status), one per graph
node, `pcagent.policy.decision` (tool, pack, action, source, reason, Jev scores), `pcagent.tool.call`
(tool, pack, source, error) and `pcagent.llm` (model, input and output tokens, finish reason).
`tracing.exporter` picks `none`, `console` or `otlp`.

**Why it was built this way.**
- OTLP reaches any backend, Langfuse included, so the Langfuse-specific callback went away.
- The policy decision is its own span with the Jev scores as attributes: the one thing an
  auditor asks for is one query away.
- Prompts and completions can contain customer data, so they are recorded only with
  `capture_content: true`.
- Tracing must never fail a run: every span call is guarded, and setup errors log only the
  exception type, never the headers.

**What surprised us.** LangGraph raises `GraphInterrupt` to pause for approval. The node span
treats it as a normal exit, not an error.
