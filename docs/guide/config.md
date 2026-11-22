# Configuration reference

pcagent reads one YAML file: `$PCAGENT_CONFIG`, or `./pcagent.yaml`. `uv run pcagent serve --config FILE`
sets it for one run. `.env` in the working directory is loaded first (shell values win).

```yaml
packs:
  - use: pcagent.packs.everywire:make_pack     # "module:attr", or an entry-point name
    settings:                                   # passed to the pack's factory
      gwmcp: {url: https://pas.example/mcp}
  - use: billing_demo
model: anthropic/claude-opus-5-5                # any LiteLLM model string; $PCAGENT_MODEL overrides
judge:
  type: jev                                     # jev | none | "module:ClassName"
  thresholds: {intent_min: 0.7, grounded_min: 0.6, injected_max: 0.3, impact_max: 1.5}
rules:                                          # global rules: deny or require_approval only
  "*__bind_*": deny
tracing:
  exporter: none                                # none | console | otlp
  endpoint: http://localhost:4318               # otlp: base URL; /v1/traces is added
  headers: {}                                   # otlp: e.g. auth headers; use ${ENV}
  capture_content: false                        # record prompts and completions in spans
checkpointer: ${DATABASE_URL:-}                 # Postgres URL; empty means in-memory
```

| Key | Default | Notes |
|---|---|---|
| `packs` | `[]` | Loaded in order. Pack names must be unique, and so must the qualified tool names. |
| `model` | `anthropic/claude-opus-5-5` | `$PCAGENT_MODEL` wins over the file. |
| `judge.type` | `jev` | `none` sends every `evaluate` call to a person. A custom class needs an async `judge(state)`. |
| `judge.thresholds` | the values above | Global only; packs cannot change them. |
| `rules` | `{}` | Matched on qualified names (`pack__tool`), exact first, then the longest glob. They can only tighten a pack's rules: the stricter result wins. |
| `tracing.*` | off | See below. |
| `checkpointer` | in-memory | Set it to keep paused runs across restarts. |

## Environment variables in the file

`${VAR}` is replaced from the environment, and an unset variable is an error.
`${VAR:-default}` uses the default when `VAR` is unset; `${VAR:-}` means empty.

## Tracing

pcagent emits OpenTelemetry spans: `pcagent.run`, `pcagent.node.*`, `pcagent.policy.decision`,
`pcagent.tool.call` and `pcagent.llm` (with GenAI token attributes). With `exporter: otlp` they go
to any OTLP/HTTP collector: Grafana Tempo, Honeycomb, Datadog, or Langfuse, which accepts OTLP
traces. Endpoint paths and auth headers differ by vendor and change over time, so check your
backend's current OTLP documentation for the exact URL and header. A broken exporter only logs a
warning; it never fails a run.

## Migrating from servers.yaml

Earlier versions read `servers.yaml`, `PCAGENT_SERVERS` and `PCAGENT_POLICY`. Those are gone:

- Put MCP servers inside a pack: Everywire's settings take `gwmcp: {url: ...}` or
  `gwmcp: {command, args, cwd, env}`.
- Rules live in each pack's `rules.yaml`. Global tightening rules and Jev thresholds live in `pcagent.yaml`.
- Langfuse keys (`LANGFUSE_*`) are no longer read; use `tracing.exporter: otlp`.
- Conversations saved by the older version use unprefixed tool names. The web UI marks them as
  legacy; start a new conversation.
