# pcagent as a pluggable framework (design)

Date: 2026-11-22. Status: awaiting Pushkar's approval. It builds on the v1 design (`2026-11-22-pcagent-design.md`) and the web UI design (`2026-11-22-web-ui-design.md`).

## Why

Pushkar calls pcagent a **framework**. A framework has to let a carrier's own IT developers connect *their* systems (any PAS, billing, claims or document system), *their* monitoring and *their* evals without editing pcagent's code. Today the core is general, but the edges are hard-wired:
- one PAS (Everywire, through `gwmcp`), named in `policy.yaml`, the system prompt and the web UI labels
- one tracer (Langfuse, through a LangChain callback)
- one eval (a pytest suite of gwmcp policy cases)

It's still a learning and portfolio project, so this design should show clean extension points a hiring manager can read, not breadth.

## Who it's for

**A developer on a P&C carrier's IT team.** They `pip install pcagent` (or check it out), write a small Python package or an MCP server for each of their systems, and wire everything together in one config file. They don't edit pcagent's source.

## Goals

1. **Packs.** A pack bundles everything specific to one system:
   - its tools
   - its policy rules
   - a prompt fragment
   - display labels for the UI
   - its eval cases

   One agent loads **several packs at once**, for example PAS, billing and claims.
2. **Two ways to add tools, one policy path.**
   - A pack's tools come from Python functions (`@pack.tool`), from MCP servers (`pack.mcp(...)`), or both.
   - Every tool, from any source, goes through the same policy, approval, audit, UI and eval path.
   - The guarantee "every tool call passes policy, failing closed" is unchanged.
3. **Config-driven wiring.** One `pcagent.yaml` chooses the packs, the model, the judge, tracing, the checkpointer and the API settings. Packs are found by Python import path or by package entry point.
4. **Pluggable judges.** `jev` (default), `none` (rules only, so every `evaluate` call goes to a person), or any class that implements the existing `Judge` protocol.
5. **OpenTelemetry tracing.** pcagent emits its own spans and the user picks an exporter: `otlp` (which reaches Langfuse, Datadog, Grafana Tempo, Honeycomb and others), `console` or `none`.
6. **Pluggable evals.** `pcagent eval` runs each pack's eval suites with pluggable scorers and writes Markdown and JSON reports. It ships with:
   - the existing **policy-decision** suite
   - a second suite type, **tool selection**: given a user message, does the agent propose the expected tool calls? It uses a live or scripted model.
7. **Examples that prove the extension points.**
   - **Everywire** becomes `packs/everywire`, an MCP pack that also includes `claimdocs`.
   - A new **Python pack**, `packs/billing_demo`, wraps a small in-memory billing system (`get_balance` read-only, `issue_refund` destructive). The demo can then show one agent working across two systems added in two different ways.
8. **Docs for the carrier developer:** a "Write your first pack" guide, the pack API reference in docstrings, and the config file reference.

## Non-goals

- Pack marketplace, versioned pack registry, hot reload.
- Sandboxing Python tools. They run in-process, and the docs say so: "use an MCP server for isolation".
- SDKs in other languages. MCP is the language-neutral path.
- Integrations with eval platforms such as Braintrust or promptfoo. The JSON report is the hand-off point.
- Login, multi-tenancy, hosting. These are unchanged from v1.
- The TPU or self-hosted-model work. Pushkar dropped it.

## Anti-goals

- **An extension point with only one implementation.** Each one ships with at least two:
  - tools: Python and MCP
  - judges: Jev and none
  - tracing: OTLP and console
  - evals: policy and tool-selection suites
- **Any way to reach a tool that skips policy.** That includes a Python tool registered later, or a tool from a pack whose rules file is missing.
- **Breaking v1 users silently.** A config in the old style either still works or fails with a clear error that tells the user how to migrate.

## Constraints

- Python 3.12 and uv. Library versions as in v1: langgraph 1.2, langchain-core 1.8, mcp 2.3, litellm 1.110. No `langchain-mcp-adapters`.
- Tool names sent to model providers must match `^[a-zA-Z0-9_-]{1,64}$`. **No dots.** Namespaced names use `<pack>__<tool>`, for example `everywire__add_vehicle`. That allows pack names of up to 20 characters and tool names of up to 42.
- Existing behaviour that must hold:
  - fail closed
  - ambiguous tool-call ids deny the whole turn
  - destructive tools are never auto-allowed, and a missing MCP `destructive_hint` means destructive
  - per-thread lock
  - 409 semantics
  - web UI safety rules
  - the 101 offline tests stay green (updated, not deleted)
- The deployed Everywire PAS (`https://gwmcp.ashenlabs.ca/mcp`) is shared data. Automated tests use the local mock and in-memory fakes only.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| B | MCP-only packs: every integration is an MCP server, and packs are pure YAML | Works in any language and keeps pcagent small, but even a 3-line REST call needs a server process, and Python teams get no fast path |
| **C** | **Packs whose tools come from Python functions or MCP servers** | Fastest start for Python teams and still open to any language through MCP, but two tool sources to build and test, and Python tools share pcagent's process |
| – | Packs built on LangChain toolkits | Dropped: it ties carriers to LangChain's churn, and v1's own bridge showed we don't need it |

**Pushkar chose C.**

## Design

### Pack API (`pcagent.packs`)

```python
from pcagent import Pack

billing = Pack(
    name="billing",                      # [a-z0-9_]{1,20}; becomes the tool-name prefix
    prompt="Billing: balances and refunds for policy accounts.",
    rules="rules.yaml",                   # relative to the pack module's directory; optional
    labels={"issue_refund": "Issued a refund"},
    evals="evals/",                       # directory of suite files; optional
)

@billing.tool(read_only=True)
async def get_balance(account_number: str) -> str:
    """Current balance for an account."""   # docstring = tool description (required)
    ...

@billing.tool(destructive=True)
async def issue_refund(account_number: str, amount: float, reason: str) -> str:
    """Refund an amount to the account."""
    ...

everywire = Pack(name="everywire", prompt="…", rules="rules.yaml")
everywire.mcp("gwmcp", url="https://gwmcp.ashenlabs.ca/mcp")
everywire.mcp("claimdocs", command="python", args=["-m", "pcagent.packs.everywire.claimdocs"])
```

**Python tools:**
- Arguments come from the type hints, and the description from the docstring. A tool with no docstring is a load error.
- Sync and async functions are both accepted.
- **Flags:**
  - `read_only` defaults to `False`.
  - `destructive` defaults to `not read_only`, which is the same default as MCP.
  - A tool marked both `read_only` and `destructive` is a load error.
- **Errors:** an exception becomes an error result, the same as an MCP `is_error`.

**MCP tools:**
- They come from the existing bridge (stdio or Streamable HTTP) and keep their annotations.
- **Selecting tools:** `pack.mcp(..., include=[...])` or `exclude=[...]` picks which of a server's tools the pack exposes, for example to drop app-only tools.

**Namespacing:**
- The model, the policy, the audit and the UI see `<pack>__<tool>`.
- A pack's own rules refer to its tools by their **short** names, and pcagent qualifies them.
- Two packs with the same name, or a tool name that is still too long after qualifying, is a load error naming both.

**Prompt:**
- The system prompt is pcagent's **core prompt**, followed by each pack's `prompt` under a `## <pack>` heading.
- The core prompt is the current `SYSTEM_PROMPT` minus everything Everywire-specific.
- The rules about data versus instructions, never guessing values, and explaining blocked calls stay in the core.

### Policy

- **Order of evaluation:**
  1. **Global rules** from `pcagent.yaml`.
  2. **The tool's own pack rules.**
  3. Annotation defaults.
  4. The judge.
- **When rules conflict:** for a given tool, a global rule overrides a pack rule only when the global rule is **stricter**. The order of strictness is `deny` > `require_approval` > `evaluate` > `allow`. A global rule can therefore never loosen what a pack asks for.
- **Rule matching:** global rules match qualified names and support globs (`billing__*`). Pack rules match short names.
- **No rules file:** a pack without a rules file gets `default: evaluate` and `read_only_default: allow`. That's the v1 default, so it still fails closed.
- **Jev questions:**
  - They receive each tool's **qualified name, description and pack name**.
  - The four questions' wording stays the same.
  - The Everywire-specific examples in the v2 `injected` question move into an `examples` field that the pack provides. The core wording becomes neutral: "the system's own workflow hints".
  - That changes Jev's input, so the policy eval must be re-run (see Evals).

### Config (`pcagent.yaml`)

```yaml
packs:
  - use: pcagent.packs.everywire:make_pack   # "module:factory", or an entry-point name in group "pcagent.packs"
    settings:                                 # passed to the factory; ${ENV} expanded
      gwmcp: {url: https://gwmcp.ashenlabs.ca/mcp}
  - use: billing_demo
model: anthropic/claude-opus-5-5
judge:
  type: jev                               # jev | none | "module:ClassName"
  thresholds: {intent_min: 0.7, grounded_min: 0.6, injected_max: 0.3, impact_max: 1.5}
rules:                                    # global rules: may only tighten (deny | require_approval)
  "*__bind_*": deny
tracing:
  exporter: none                        # none | console | otlp
  endpoint: http://localhost:4318       # otlp only; protocol http/protobuf
  headers: {}                           # e.g. Langfuse basic auth; values may use ${ENV}
checkpointer: ${DATABASE_URL}           # empty → in-memory
```

- `PCAGENT_CONFIG` selects the file, and the default is `./pcagent.yaml`. `${VAR}` expansion is the same as `servers.yaml` today.
- **v1 migration:**
  - If there's no `pcagent.yaml` but there is a `servers.yaml`, pcagent fails with a message that shows the equivalent `pcagent.yaml`.
  - The repo ships a `pcagent.yaml`, plus `pcagent.local.yaml` for the local mock.
  - `PCAGENT_SERVERS` and `PCAGENT_POLICY` are removed, and their removal is documented.

### Tracing (`pcagent.telemetry`)

- Use `opentelemetry-api` and `-sdk` directly, with pcagent's own spans:
  - `pcagent.run`, one per run or resume, with attributes `thread_id`, `model` and `status`
  - `pcagent.node.<name>` for agent, policy, approval and tools
  - `pcagent.policy.decision`, one per call, with attributes `tool`, `pack`, `action`, `source`, `reason` and the four scores
  - `pcagent.tool.call`, one per execution, with attributes `tool`, `pack`, `source` (python or mcp) and `error`
- **Payload privacy:** prompts and tool payloads are **not** recorded by default. `tracing.capture_content: true` turns that on.
- **Exporters:**
  - `none` uses no SDK provider, so there's no cost.
  - `console` uses `ConsoleSpanExporter`.
  - `otlp` uses the OTLP HTTP exporter, with the endpoint and headers from config.
- The Langfuse LangChain callback is **removed**. Langfuse is reached as an OTLP endpoint, and the docs show the header setup. The builder verifies Langfuse's current OTLP ingest path and auth when writing that doc.
- A broken exporter must never fail a run. Log a warning, with no secrets in it, and carry on.

### Evals (`pcagent.evals`)

**Suite files:** a pack's `evals/` directory holds suite files, `*.yaml` or `*.jsonl` with a header line:

```yaml
suite: policy            # or: tool_selection
cases: cases.jsonl
scorers: [exact_action]  # policy default; tool_selection default: [expected_tools]
```

**The `Scorer` protocol:** `score(case, outcome) -> ScoreResult(passed: bool, detail: str, metrics: dict)`.
- Built-in scorers:
  - `exact_action` (policy)
  - `missed_escalation` (policy; the hard gate, which must be 0)
  - `expected_tools` (tool selection: the expected tool names are a subset of the proposed ones, and no forbidden tool appears)
- Custom scorers are loaded by `module:Class`.

**Suite types:**
- **policy:** each case is a `PolicyEngine.decide` call with the case's context, the same as v1's eval.
- **tool_selection:**
  - Each case is `{messages, expect_tools, forbid_tools}`.
  - The runner builds the agent with the configured model and the packs' tool *schemas*.
  - It runs **only the agent node, once**. No tools execute, so it's safe against real systems.
  - It then scores the proposed tool calls.

**The command:**
- `pcagent eval [--pack NAME] [--suite NAME] [--runs N]` writes `reports/eval-<timestamp>.md` and `.json`.
- It exits non-zero if any gate fails. The gates are any missed escalation, or accuracy below a suite's `min_accuracy`, which defaults to 0.85.

**What moves where:**
- v1's 40 policy cases move to `packs/everywire/evals/`, with qualified tool names.
- `tests/test_policy_eval.py` becomes a thin `@live` wrapper around the runner.
- The eval is re-run live because Jev's input changed (see Policy). The tuning log records the result.

**New cases:**
- `packs/billing_demo/evals/` gets at least 10 policy cases and at least 5 tool-selection cases.
- `packs/everywire/evals/` gets at least 8 tool-selection cases.

### API and web UI

- **New endpoint `GET /packs`:** returns `[{name, tools: [{name, short_name, label, read_only, destructive, source}]}]`.
- The web UI fetches it once and uses its labels in place of the hard-coded `VERBS` map, falling back to the short name.
- Tool rows show the pack as a small tag.
- No other API changes.

### Package layout

```
src/pcagent/
  core/      graph.py, runs.py, policy/, gateway.py (core prompt), checkpoint.py
  packs/     __init__.py (Pack, tool decorator, loader), everywire/ (pack, rules, claimdocs, evals), billing_demo/
  telemetry.py   evals/   api.py   cli.py   web/   config.py
```

- Moving the existing modules is allowed. Public imports that tests use today must keep working, or the tests are updated in the same task.
- `pcagent` re-exports `Pack` and the `Judge` and `Scorer` protocols.

### Docs

- `docs/guide/write-a-pack.md`: install, write a Python pack with two tools and a rules file, add an MCP server, add eval cases, run `pcagent eval`, then see it in the web UI.
- `docs/guide/config.md`: every config key.
- `docs/layers/10-packs.md`, `11-telemetry.md` and `12-evals.md`.
- README:
  - "Framework" section first.
  - The Everywire demo becomes "the example pack".
  - The TPU beat is removed. Beat 4 becomes what really happened: Claude flagged the planted note itself, and the deny rule and Jev stand behind it.

## Resolved after the builder check (Claude's calls)

1. **Settings per environment.**
   - A pack is a **factory**, `def make_pack(settings: dict) -> Pack`, and `pcagent.yaml` passes each pack its `settings` block, with `${ENV}` expanded.
   - Everywire's factory reads either `gwmcp: {url: ...}` or `gwmcp: {command, args, cwd, env}`. `pcagent.local.yaml` gives the local-mock command, and `pcagent.yaml` gives the deployed URL.
   - Secrets come in only through `${ENV}`.
   - A pack with no settings may be a plain `Pack` attribute instead of a factory.
2. **How global and pack rules combine:**
   1. Work out the **pack decision** first: the pack's explicit rule (an exact name beats a glob, and the longest glob wins), otherwise annotations plus the pack's `default` and `read_only_default`.
   2. Work out the **global match**: the longest matching global glob or exact name.
   3. The result is the **stricter** of the two (`deny` > `require_approval` > `evaluate` > `allow`).
   4. Global rules may only be `deny` or `require_approval`. Any other value is a load error, because they exist to tighten.
   5. An operator who wants to relax an over-cautious pack edits that pack's rules, which is their own code.
   6. The destructive rule still applies after the merge.
3. **Thresholds and judge switch:**
   - Jev thresholds are **global only**, set under `judge.thresholds` in `pcagent.yaml`, and default to the v1 values.
   - Pack rules files contain only `default`, `read_only_default` and `rules`. If one contains `thresholds` or `jev`, that's a load error.
   - `jev.enabled` is replaced by `judge.type: none`.
4. **Pack examples for Jev:**
   - A pack declares workflow-hint examples as `Pack(judge_hints=[...])`. They travel in the judged **state** as `proposed_call.pack.hints`, so the four questions' instructions stay fixed and the `Judge` protocol doesn't change.
   - If the re-run eval fails after the wording becomes neutral, re-tuning the **wording** is in scope and goes in the tuning log. **Thresholds are not** in scope: if the gate can't be passed by wording alone, stop and ask Pushkar.
5. **Tool schemas for the tool-selection eval:**
   - `pcagent eval` builds the packs from the config file given with `--config`. The repo's documented eval command uses `pcagent.local.yaml`, the local mock. Listing tools is read-only, but tool-selection evals still never execute tools.
   - The scripted model exists only for offline tests of the runner. It isn't a supported eval mode.
6. **Eval gates:**
   - Policy suites always apply two gates, whatever `scorers` lists:
     - **missed escalations == 0**
     - **no `fallback` decisions** (a judge outage fails the eval)
   - "At least one injection is caught by `injected` alone" becomes an Everywire custom scorer.
   - With `--runs N`, accuracy is the mean across runs, and a missed escalation in any run fails the eval.
   - The exit code is non-zero if any gate fails.
7. **Threads saved before the upgrade:**
   - They aren't migrated. Their unqualified tool names are already denied as "unknown tool", which fails closed.
   - The migration note says to start new threads or reset the database.
   - `GET /threads/{id}` adds `legacy: true` when a thread contains unqualified tool calls, and the UI shows a one-line banner, so the break isn't silent.
8. **`billing_demo` is a separate package:**
   - `examples/billing_demo/` has its own `pyproject.toml`, is a uv workspace member, and registers the entry point `pcagent.packs: billing_demo = billing_demo:make_pack`.
   - That's how a carrier would ship a pack. The Docker image installs the workspace.
9. **Model-call spans:**
   - Each LLM call emits a `pcagent.llm` span with the OTel GenAI attributes: `gen_ai.request.model`, `gen_ai.usage.input_tokens` and `output_tokens`, and the finish reason.
   - Prompt and completion text are recorded only when `capture_content: true`.
   - The spans come from a LangChain callback inside pcagent, so Langfuse users keep a generation view through OTLP.

## Decided by Pushkar

- The user is a carrier IT developer who writes adapters (approach C).
- One agent loads several packs.
- TPU work dropped.

## Calls made by Claude (check these)

- `<pack>__<tool>` namespacing, and the "only stricter" rule for global versus pack rules.
- The judge `none` means rules only, sending `evaluate` to a person.
- OTel with its own spans replaces the Langfuse callback, and content capture is off by default.
- The second eval type is tool selection (agent node only, no tool execution).
- `billing_demo` as the Python example pack.
- `pcagent.yaml` replaces `servers.yaml` with a clear migration error rather than silent compatibility.

## Left to the builder

The loader internals, the module split details, exact span names and attributes beyond those listed, the report formatting, the case wording, and the billing demo's fake data.
