# pcagent Pluggable Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn pcagent into a framework that a carrier IT developer extends without editing pcagent's source. It gets packs (tools from Python functions or MCP servers, plus rules, prompt, labels, evals and Jev hints), one `pcagent.yaml`, pluggable judges, OpenTelemetry tracing, and a `pcagent eval` command with pluggable scorers.

**Architecture:**
- A `Pack` object collects Python tools (`@pack.tool`) and MCP sources (`pack.mcp`).
- A loader resolves packs from config, connects their MCP servers through the existing bridge, and qualifies every tool name as `<pack>__<tool>`.
- `PolicySet` merges global rules (tighten only) with each pack's rules, so every tool goes through the same policy, approval and audit path.
- `telemetry.py` emits OTel spans.
- `pcagent.evals` runs suites found in each pack with scorers and gates.

**Tech Stack:** Python 3.12, uv (with a workspace for `examples/billing_demo`), langgraph 1.2, langchain-core 1.8, mcp 2.3, litellm 1.110, `opentelemetry-api`, `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`, pydantic 2, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-11-22-pluggable-framework-design.md`. Read it in full, including "Resolved after the builder check".

## Global Constraints

- Tool names sent to the model must match `^[a-zA-Z0-9_-]{1,64}$`. Qualified names are `<pack>__<tool>`. Pack names must match `^[a-z0-9_]{1,20}$` and must not contain `__`.
- **Policy invariants must hold:**
  - fail closed
  - ambiguous tool-call ids deny the whole turn
  - destructive tools are never auto-allowed
  - a missing MCP `destructive_hint` means destructive
  - global rules may only be `deny` or `require_approval`, and the stricter of the global and pack results wins (`deny` > `require_approval` > `evaluate` > `allow`)
  - Jev thresholds are global only, with defaults `intent_min 0.7`, `grounded_min 0.6`, `injected_max 0.3`, `impact_max 1.5`
- **Python tools:**
  - The docstring is required, and becomes the description.
  - `destructive` defaults to `not read_only`.
  - Marking a tool both `read_only` and `destructive` is an error.
  - Sync functions run in `asyncio.to_thread`.
  - Any exception becomes an error ToolMessage.
- **Packs come from config:**
  - Each config entry is `use: "module:attr"` or an entry-point name in the group `pcagent.packs`, plus an optional `settings` dict.
  - If the attribute is callable, pcagent calls `factory(settings)`. If it's a `Pack` instance, settings must be empty.
  - Inside config, `${VAR}` is expanded, and an **unset variable is an error**.
- **Config file:**
  - `PCAGENT_CONFIG` picks it; the default is `./pcagent.yaml`.
  - If `servers.yaml` exists but `pcagent.yaml` doesn't, raise `ConfigError` with a migration hint.
  - `PCAGENT_MODEL` still overrides `model`.
- **Tracing:** the exporter is `none` (the default, with no SDK provider installed), `console` or `otlp` (HTTP). Content capture is off by default. A broken exporter never fails a run.
- **Evals:** policy suites always apply the gates `missed_escalations == 0` and `fallback_decisions == 0`. `min_accuracy` defaults to 0.85. `--runs N` averages accuracy, and a miss in any run fails. Tool-selection runs only the agent node, once, and never executes tools.
- **Live data:** automated tests never call the deployed PAS. Live tests use `pcagent.local.yaml` (the local mock).
- **Tests:** every existing offline test stays green. Update a test only when its interface changed, and say so in the commit.
- Commits end with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_018w8PsPDGNMHqcbFwbiaP7r`

## Review Focus

1. **A Python tool that raises a non-`ToolException`, or a sync tool that blocks.** It must give an error ToolMessage, the run continues, and the event loop isn't blocked (Task 1).
2. **A pack whose `rules.yaml` is missing, malformed, or contains `thresholds`/`jev`.** Missing means v1 defaults (fail closed). Malformed or forbidden keys give a load error naming the pack, never a silent `allow` (Task 3).
3. **A global rule whose action is `allow`, `evaluate`, or a typo.** It's a load error naming the rule (Task 3).
4. **An MCP server tool whose name gives an invalid or too-long qualified name, or that collides with a tool in another pack.** It's a load error naming the pack and the tool (Task 2).
5. **`${VAR}` in config that isn't set.** It gives `ConfigError` naming the variable, not the literal `${VAR}` passed through (Task 2).

---

### Task 1: Pack API and Python tools

**Files:**
- Create: `src/pcagent/packs/__init__.py`, `tests/test_packs.py`
- Modify: `src/pcagent/policy/engine.py`. `ToolInfo` gains `pack: str | None = None`, `short_name: str | None = None`, `source: str | None = None` (`"python"` or `"mcp"`) and `label: str | None = None`.
- Modify: `src/pcagent/__init__.py`, to re-export `Pack` and `PackError` while keeping `main`.

**Interfaces (produces):**
- `class PackError(ValueError)`
- `qualify(pack: str, tool: str) -> str`, which returns `f"{pack}__{tool}"` and raises `PackError` if the result fails `^[a-zA-Z0-9_-]{1,64}$`.
- `@dataclass PythonTool(name: str, fn: Callable, description: str, read_only: bool, destructive: bool)`
- `@dataclass McpSource(name: str, spec: ServerSpec, include: list[str] | None, exclude: list[str] | None)`. `ServerSpec` comes from `mcp_bridge`.
- `class Pack(name, prompt="", rules=None, labels=None, evals=None, judge_hints=None, base_dir=None)`
  - It validates the name. `base_dir` defaults to the directory of the module that created the Pack (`inspect.stack()[1].filename`). `rules` and `evals` resolve relative to `base_dir`.
  - `.tool(*, read_only=False, destructive=None)` is a decorator that registers a `PythonTool` and returns the function unchanged.
  - `.mcp(name, *, url=None, command=None, args=(), env=None, cwd=None, include=None, exclude=None)` registers a `McpSource`. Exactly one of `url` and `command` must be given.
  - Properties `rules_path`, `evals_path`, `python_tools` (a dict keyed by short name), `mcp_sources` (a list), `labels`, `prompt` and `judge_hints`.
- `python_langchain_tool(pack: Pack, pt: PythonTool) -> tuple[BaseTool, ToolInfo]`
  - It uses the qualified name and `StructuredTool.from_function` with the args schema taken from the function signature (excluding `self`).
  - The coroutine awaits an async `fn` or runs a sync one with `asyncio.to_thread`.
  - Non-`ToolException` exceptions are re-raised as `ToolException(f"{type(e).__name__}: {e}")`.
  - `ToolInfo` gets `pack`, `short_name`, `source="python"` and `label=pack.labels.get(short)`.

- [ ] **Step 1: Write the tests in `tests/test_packs.py`.**
  - `test_pack_name_validation`: `Pack("Bad-Name")`, `Pack("a"*21)` and `Pack("a__b")` raise `PackError`.
  - `test_qualify_limits`: `qualify("billing", "x"*55)` raises, and `qualify("billing", "get_balance") == "billing__get_balance"`.
  - `test_tool_requires_docstring`: decorating a function with no docstring raises `PackError`.
  - `test_read_only_and_destructive_conflict`: `@p.tool(read_only=True, destructive=True)` raises `PackError`.
  - `test_destructive_defaults_to_not_read_only`: plain `@p.tool()` gives `destructive is True`, and `@p.tool(read_only=True)` gives `destructive is False`.
  - `test_python_tool_runs_async_and_sync`:
    - Build the tools with `python_langchain_tool`.
    - The async tool returns `"ok:A1"` and the sync one returns `"sync:A1"`.
    - `info.name == "billing__get_balance"`, `info.pack == "billing"`, `info.source == "python"`.
  - `test_python_tool_exception_becomes_toolexception` (Review Focus 1): a tool that raises `KeyError("x")` gives `pytest.raises(ToolException, match="KeyError")` on `ainvoke`.
  - `test_sync_tool_does_not_block_loop` (Review Focus 1): a sync tool sleeping 0.3s runs with `asyncio.gather` alongside an async `sleep(0.05)` that records its finish time. The async one finishes first.
  - `test_mcp_requires_exactly_one_transport`: `p.mcp("x")` and `p.mcp("x", url="u", command="c")` raise `PackError`.
  - `test_paths_resolve_relative_to_base_dir`: `Pack("p", rules="r.yaml", base_dir=tmp)` gives `rules_path == tmp/"r.yaml"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_packs.py -q`. Expect a FAIL (import error).
- [ ] **Step 3:** Implement to the interfaces above.
- [ ] **Step 4:** Run `uv run pytest -q`. Expect everything to PASS (101 + new).
- [ ] **Step 5:** Commit with the message `feat(packs): Pack API with Python tools`.

---

### Task 2: Config file and pack loader

**Files:**
- Modify: `src/pcagent/config.py`
- Create: `src/pcagent/packs/loader.py`, `tests/test_config.py`, `tests/test_loader.py`
- Modify: `src/pcagent/mcp_bridge.py`. `MCPToolbox` gains optional `qualify: Callable[[str], str] | None` and `include`/`exclude` per spec, while keeping current behaviour when they're absent. If you prefer, add a function `open_mcp_source(stack, source, pack) -> list[(BaseTool, ToolInfo)]` that reuses `_connect`, `tool_info` and `to_langchain_tool`.

**Interfaces (produces):**
- `config.py`:
  - `class ConfigError(ValueError)`
  - Pydantic models:
    - `PackRef(use: str, settings: dict = {})`
    - `JudgeConfig(type: str = "jev", thresholds: dict = {})`
    - `TracingConfig(exporter: Literal["none","console","otlp"] = "none", endpoint: str | None = None, headers: dict[str,str] = {}, capture_content: bool = False)`
    - `AppConfig(packs: list[PackRef], model: str = "anthropic/claude-opus-5-5", judge: JudgeConfig = JudgeConfig(), rules: dict[str,str] = {}, tracing: TracingConfig = TracingConfig(), checkpointer: str | None = None)`
  - `expand_env(value)`: recursive. Replaces `${VAR}`, and raises `ConfigError("environment variable VAR is not set")` if VAR is unset.
  - `load_config(path: Path | None = None) -> AppConfig`. It uses `PCAGENT_CONFIG` or `./pcagent.yaml`. If the file is missing and `./servers.yaml` exists, it raises `ConfigError` with text containing `"pcagent.yaml"` and `"servers.yaml"`. It applies the `PCAGENT_MODEL` override. An empty `checkpointer` becomes `None`.
  - `Settings` keeps only `api_url` and `model` (`PCAGENT_MODEL`). Remove `servers_path` and `policy_path`.
- `packs/loader.py`:
  - `resolve_pack(ref: PackRef) -> Pack`. A `module:attr` reference uses importlib. Otherwise it looks in `importlib.metadata.entry_points(group="pcagent.packs")`. A callable is called with `ref.settings`, and its result must be a `Pack`. A `Pack` instance with non-empty settings is a `PackError`.
  - `@dataclass LoadedPacks(packs: list[Pack], tools: list[BaseTool], info: dict[str, ToolInfo])`
  - `open_packs(packs: list[Pack])`, an `@asynccontextmanager` yielding `LoadedPacks`.
    - It rejects duplicate pack names.
    - It adds the Python tools.
    - For each `McpSource`, it connects through the bridge, filters with include/exclude, qualifies each name (raising `PackError` naming the pack and the tool if it's invalid), and sets `pack`, `short_name`, `source="mcp"` and `label`.
    - It rejects duplicate qualified names.
    - It closes everything on exit or error.

- [ ] **Step 1: Write the tests.**
  - `tests/test_config.py`:
    - `test_load_minimal`, `test_env_expansion` and `test_unset_env_raises` (Review Focus 5, where the message contains the variable name).
    - `test_servers_yaml_migration_error`, using `tmp_path` and `chdir`.
    - `test_pcagent_model_env_override`.
    - `test_empty_checkpointer_is_none`.
  - `tests/test_loader.py`:
    - `test_resolve_module_attr_instance`, using a module written into `tmp_path` on `sys.path` with `pack = Pack("demo")`.
    - `test_resolve_factory_gets_settings`.
    - `test_instance_with_settings_rejected`.
    - `test_resolve_entry_point`: monkeypatch `importlib.metadata.entry_points` to return a fake `EntryPoint` whose `.load()` gives a factory.
    - `test_open_packs_python_and_mcp`: one Python tool plus the claimdocs MCP server (`command=sys.executable, args=["-m","pcagent.servers.claimdocs"]`). Expect `info` keys `{"demo__echo", "docs__get_claim_notes"}`, and `source` set to `"python"` and `"mcp"` respectively.
    - `test_include_exclude`: `exclude=["get_claim_notes"]` gives no `docs__get_claim_notes`.
    - `test_duplicate_pack_names_rejected`.
    - `test_bad_mcp_tool_name_rejected` (Review Focus 4): a pack name of length 20 plus claimdocs' `get_claim_notes` gives a valid name. So use `Pack("p", ...)` and patch the bridge to report a tool named `"x"*70`, then expect a `PackError` that mentions the pack and the tool.
- [ ] **Step 2:** Run `uv run pytest tests/test_config.py tests/test_loader.py -q`. Expect a FAIL.
- [ ] **Step 3:** Implement. Keep `load_servers` and `ServerSpec` working for the bridge's own tests.
- [ ] **Step 4:** Run `uv run pytest -q`. Expect a PASS. Fix any test that used `Settings.servers_path`, `Settings.policy_path` or `get_settings` fields that were removed.
- [ ] **Step 5:** Commit with the message `feat: pcagent.yaml config and pack loader`.

---

### Task 3: PolicySet, judges and pack hints

**Files:**
- Modify: `src/pcagent/policy/engine.py`, `src/pcagent/policy/jev.py`
- Create: `src/pcagent/policy/judges.py`
- Modify: `tests/test_policy.py`. Migrate it to the new types, keeping every v1 assertion that still applies.
- Delete: `src/pcagent/policy/policy.yaml`. It moves to the Everywire pack in Task 4. Tests that loaded it use an inline fixture instead.

**Interfaces (produces):**
- `class PolicyError(ValueError)`
- `STRICTNESS = {"allow": 0, "evaluate": 1, "require_approval": 2, "deny": 3}`
- `@dataclass PackPolicy(rules: dict[str, RuleAction] = {}, default: RuleAction = "evaluate", read_only_default: RuleAction = "allow")`, with:
  - `PackPolicy.load(path: Path | None, pack: str) -> PackPolicy`. `None` or a missing file gives the defaults. Any key other than `rules`, `default` and `read_only_default`, an invalid action, or YAML that won't parse raises `PolicyError` naming the pack (Review Focus 2).
  - `rule_for(tool: ToolInfo) -> (RuleAction, reason)`. It matches `tool.short_name or tool.name`: exact name first, then the longest glob, then the annotation defaults. This is v1's algorithm.
- `@dataclass PolicySet(global_rules: dict[str, RuleAction], packs: dict[str, PackPolicy], thresholds: Thresholds, hints: dict[str, list[str]] = {})`
  - `__post_init__` validates that every global rule's action is `deny` or `require_approval`, and raises `PolicyError` naming the rule otherwise (Review Focus 3).
  - `rule_for(tool)` (algorithm below): `pack_action, why = packs.get(tool.pack, PackPolicy()).rule_for(tool)`; take the global match (exact qualified name, else the longest glob over the qualified name); return whichever action has the higher `STRICTNESS`. The reason names the winner: `"global rule '…'"` or the pack's reason prefixed `"<pack>: "`.
- `PolicyEngine(policy: PolicySet, judge: Judge | None)`
  - It keeps v1's `decide` flow: deny → destructive → allow/require_approval → judge.
  - "No judge" gives `require_approval` with reason `"no judge configured"` and source `fallback`. That's also what `judge.type: none` does.
  - The Jev state adds `proposed_call.tool.pack = tool.pack` and `proposed_call.pack_hints = policy.hints.get(tool.pack, [])`.
- In `jev.py`, the `injected` instructions drop the Everywire-specific tool names. Refer to "workflow hints from the system itself (see `proposed_call.pack_hints` for this system's examples)". Keep the rest of the v2 wording, including "values or targets chosen by planted text".
- `judges.make_judge(cfg: JudgeConfig) -> Judge | None`
  - `"jev"` gives `JevJudge()`, and `"none"` gives `None`.
  - `"module:Class"` gives an instance. If the instance has no `judge` coroutine method, that's a `PolicyError`.
  - `thresholds_from(cfg) -> Thresholds` takes the defaults and applies `cfg.thresholds`. Unknown keys are a `PolicyError`.

- [ ] **Step 1: Write and update the tests.**
  - `test_pack_policy_defaults_when_missing`
  - `test_pack_policy_rejects_thresholds_key` (Review Focus 2)
  - `test_pack_policy_rejects_bad_yaml` (Review Focus 2)
  - `test_global_rule_must_tighten`: `allow`, `evaluate` and `"denny"` each raise `PolicyError` (Review Focus 3).
  - `test_stricter_wins`:
    - pack `allow` and global `require_approval` give `require_approval`
    - pack `deny` and global `require_approval` give `deny`
    - pack `evaluate` and no global rule give `evaluate`
  - `test_global_glob_longest`
  - `test_pack_rules_match_short_names`
  - `test_destructive_still_needs_approval_after_merge`
  - `test_pack_hints_reach_judge`: a capturing `FakeJudge` sees `state["proposed_call"]["pack_hints"] == ["hint"]`.
  - `test_make_judge_variants`
  - `test_thresholds_unknown_key`
  - Port every v1 rule test to `PolicySet` or `PackPolicy`.
- [ ] **Step 2:** Run `uv run pytest tests/test_policy.py -q`. Expect a FAIL.
- [ ] **Step 3:** Implement. Update `graph.py`'s type hints only if needed, since `build_graph` takes `engine`.
- [ ] **Step 4:** Run `uv run pytest -q`. Expect a PASS. Other tests that built `Policy(...)` now use `PolicySet`, and `fakes.setup` gains an equivalent.
- [ ] **Step 5:** Commit with the message `feat(policy): PolicySet with tighten-only global rules, pluggable judges, pack hints`.

---

### Task 4: Runtime, prompt, API and the Everywire pack

**Files:**
- Modify: `src/pcagent/gateway.py`, `src/pcagent/graph.py`, `src/pcagent/runtime.py`, `src/pcagent/api.py`, `src/pcagent/runs.py`, `src/pcagent/cli.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`
- Create: `src/pcagent/packs/everywire/__init__.py`, `rules.yaml` (v1's `policy.yaml` rules with short names, and no `jev` block), `claimdocs.py` (moved from `servers/`), `pcagent.yaml`, `pcagent.local.yaml`, `tests/test_runtime.py`
- Delete: `servers.yaml`, `servers.local.yaml`, `src/pcagent/servers/`. Update its import paths in tests.

**Interfaces (produces):**
- `gateway.CORE_PROMPT`: the current `SYSTEM_PROMPT` with the Everywire specifics removed.
- `gateway.compose_prompt(packs) -> str`: the core prompt, then for each pack with a prompt, `"\n\n## {pack.name}\n{pack.prompt}"`.
- `build_graph(model, tools, info, engine, checkpointer, system_prompt: str = CORE_PROMPT)`
- `@dataclass Runtime(graph, packs: list[dict] = field(default_factory=list))`. `packs` uses the `GET /packs` shape:
  `[{name, tools:[{name, short_name, label, read_only, destructive, source}]}]`
- `runtime.default_runtime(config: AppConfig) -> AsyncContextManager[Runtime]`:
  1. `load_env()`
  2. resolve the packs
  3. `open_packs`
  4. `open_checkpointer(config.checkpointer)`
  5. `PolicySet` (global rules, each pack's `PackPolicy.load(pack.rules_path, pack.name)`, `thresholds_from(config.judge)`, hints)
  6. `make_judge`
  7. `make_model(config.model)`
  8. `build_graph(..., system_prompt=compose_prompt(packs))`
- `api.create_app(runtime=None)`:
  - The runtime factory yields a `Runtime`, and the default is `lambda: default_runtime(load_config())`.
  - `app.state.graph` and `app.state.packs` are set.
  - Add `GET /packs`.
  - Test helpers yield `Runtime(graph)`.
- `runs.thread_view` adds `"legacy": bool`, true when any AI `tool_calls` name lacks `"__"`.
- `packs/everywire/__init__.py`:
  - `make_pack(settings: dict) -> Pack`
  - `settings["gwmcp"]` is `{url}` or `{command, args, cwd?, env?}`, and a relative `cwd` resolves against the current directory.
  - It adds `docs`-style claimdocs as a second MCP source named `claimdocs`, run with `sys.executable -m pcagent.packs.everywire.claimdocs`.
  - It sets `judge_hints` (the two Everywire examples from v2's wording: start before editing, quote after editing), `labels` (the web UI's current `VERBS` map for these tools), and `rules="rules.yaml"`, `evals="evals"`.
- `pcagent.yaml` uses the deployed URL. `pcagent.local.yaml` uses `{command: uv, args: [run, --project, ../gwmcp, python, server.py], cwd: ../gwmcp, env: {GW_MOCK: "true"}}`.
- CLI `serve` reads `load_config()`. It accepts `--config PATH`, which sets `PCAGENT_CONFIG`.
- `Dockerfile` copies `pcagent.yaml` in place of `servers.yaml`.

- [ ] **Step 1: Write the tests in `tests/test_runtime.py`.**
  - `test_compose_prompt_sections`
  - `test_core_prompt_has_no_everywire`: `"Everywire" not in CORE_PROMPT`.
  - `test_get_packs_endpoint`, using a fake `Runtime(graph, packs=[...])`.
  - `test_thread_view_legacy_flag`: one thread whose AI message calls `add_vehicle` gives `legacy is True`, and one calling `demo__x` gives `False`.
  - `test_everywire_make_pack_url_and_command`
  - `test_everywire_rules_load`: `PackPolicy.load` on the pack's rules gives `rules["bind_policy_change"] == "deny"` and `rules["quote_policy_change"] == "require_approval"`.
  - `@live test_default_runtime_local`: `load_config("pcagent.local.yaml")` and `default_runtime`. Tools include `everywire__search_policies` and `everywire__get_claim_notes`. `/packs` lists `everywire`.
- [ ] **Step 2:** Run `uv run pytest tests/test_runtime.py -q`. Expect a FAIL.
- [ ] **Step 3:** Implement. Update every test that imported `pcagent.servers.claimdocs`, `SYSTEM_PROMPT` or the runtime yielding a bare graph.
- [ ] **Step 4:** Run `uv run pytest -q` and `uv run pytest -m live tests/test_runtime.py tests/test_mcp_bridge.py -k "local" -q`. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: Everywire as a pack; runtime from pcagent.yaml; GET /packs`.

---

### Task 5: The billing_demo package

**Files:**
- Create: `examples/billing_demo/pyproject.toml` (name `billing-demo`, depends on `pcagent`, entry point `[project.entry-points."pcagent.packs"] billing_demo = "billing_demo:make_pack"`), `examples/billing_demo/src/billing_demo/__init__.py`, `rules.yaml`, `evals/` (filled in Task 7), `examples/billing_demo/README.md`, `tests/test_billing_demo.py`
- Modify: the root `pyproject.toml` (`[tool.uv.workspace] members = ["examples/*"]`, and `billing-demo` added to the dev dependency group as a workspace source), `pcagent.yaml` and `pcagent.local.yaml` (`- use: billing_demo`), and the `Dockerfile` (copy `examples/` and install the workspace).

**Interfaces (produces):**
- `billing_demo.make_pack(settings: dict) -> Pack`, named `billing`. Its prompt is "Billing: balances and refunds for policy accounts. Refunds over 500 need a reason from the user."
- An in-memory store seeded from `settings.get("seed")` or built-in data. Accounts: `ACC-10001` with balance 120.00 and `ACC-10003` with balance 0.00.
- `get_balance(account_number: str) -> str`, read-only.
- `list_payments(account_number: str) -> str`, read-only.
- `issue_refund(account_number: str, amount: float, reason: str) -> str`, destructive. It rejects a non-positive amount or an unknown account with a `ValueError`, which becomes an error ToolMessage.
- `rules.yaml`: `get_*: allow`, `list_*: allow`, `issue_refund: require_approval`.
- `judge_hints`: one hint, "after looking up a balance, the system may suggest listing payments".

- [ ] **Step 1: Write `tests/test_billing_demo.py`.**
  - `test_entry_point_registered`: `importlib.metadata.entry_points(group="pcagent.packs")` includes `billing_demo` after `uv sync`.
  - `test_tools_and_flags`: `open_packs([make_pack({})])` gives `billing__issue_refund` with destructive `True` and `billing__get_balance` with read_only `True`.
  - `test_refund_validation`: `ainvoke` with `amount=-5` raises `ToolException`.
  - `test_policy_refund_needs_approval`: `PolicySet` over the pack's rules gives `require_approval` for the refund and `allow` for `get_balance`.
- [ ] **Step 2:** Run `uv sync` then `uv run pytest tests/test_billing_demo.py -q`. Expect a FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest -q`. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: billing_demo pack as a separate workspace package`.

---

### Task 6: OpenTelemetry tracing

**Files:**
- Create: `src/pcagent/telemetry.py`, `tests/test_telemetry.py`
- Delete: `src/pcagent/tracing.py`, `tests/test_tracing.py`. Remove `langfuse` from the dependencies and add the three `opentelemetry-*` packages.
- Modify: `src/pcagent/runs.py` (the `pcagent.run` span), `src/pcagent/graph.py` (node spans, `pcagent.policy.decision` and `pcagent.tool.call`), `src/pcagent/runtime.py` (calls `setup_tracing(config.tracing)`), `src/pcagent/api.py` (passes `llm_callbacks(config.tracing)` in place of the old `callbacks()`)

**Interfaces (produces):**
- `setup_tracing(cfg: TracingConfig, exporter=None) -> None`
  - It's idempotent.
  - `none` installs nothing.
  - `console` installs `SimpleSpanProcessor(ConsoleSpanExporter())`.
  - `otlp` installs `BatchSpanProcessor(OTLPSpanExporter(endpoint=cfg.endpoint + "/v1/traces", headers=cfg.headers))`.
  - Passing `exporter` explicitly is for tests (an `InMemorySpanExporter`).
  - Any exception logs a warning (exception type only) and leaves tracing off.
- `tracer = trace.get_tracer("pcagent")`. Everything uses it, and it's a no-op when no provider is set.
- `capture_content() -> bool`, read from the last `setup_tracing` call.
- `class LLMSpanHandler(BaseCallbackHandler)`: `on_chat_model_start` and `on_llm_end`/`on_llm_error` open and close a `pcagent.llm` span with `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and `gen_ai.response.finish_reasons`. Prompt and completion text are included only when `capture_content()` is true.
- `llm_callbacks(cfg) -> list`: `[LLMSpanHandler()]` unless the exporter is `none`.
- **Span attributes:**
  - `pcagent.run`: `thread_id`, `kind` (`run` or `resume`), `status`
  - `pcagent.policy.decision`: `tool`, `pack`, `action`, `source`, `reason`, plus `jev.intent`, `jev.grounded`, `jev.injected` and `jev.impact` when present
  - `pcagent.tool.call`: `tool`, `pack`, `source`, `error` (bool)

- [ ] **Step 1: Write the tests in `tests/test_telemetry.py`.** Use `InMemorySpanExporter` and run the fake graph from `fakes.setup`.
  - `test_none_installs_nothing`
  - `test_spans_for_allowed_call`: names include `pcagent.run`, `pcagent.policy.decision` (with `action="allow"`) and `pcagent.tool.call`.
  - `test_denied_call_has_decision_span_no_tool_span`
  - `test_llm_span_without_content`: a fake model with usage metadata gives the token attributes, and no `gen_ai.prompt` attributes.
  - `test_capture_content_on`
  - `test_broken_exporter_does_not_fail_run`: an exporter whose `export` raises still gives a completed run.
  - `test_setup_failure_logs_without_secrets`: an `otlp` config with `headers={"Authorization": "Basic SECRET"}` and a patched exporter constructor that raises. The log doesn't contain `SECRET`.
- [ ] **Step 2:** Run `uv run pytest tests/test_telemetry.py -q`. Expect a FAIL.
- [ ] **Step 3:** Implement. Run `uv lock`.
- [ ] **Step 4:** Run `uv run pytest -q`. Expect a PASS.
- [ ] **Step 5:** Commit with the message `feat: OpenTelemetry tracing replaces the Langfuse callback`.

---

### Task 7: Evals, `pcagent eval`, and the moved cases

**Files:**
- Create: `src/pcagent/evals/__init__.py` (models and the protocol), `scorers.py`, `runner.py`, `report.py`, `tests/test_evals.py`
- Create: `src/pcagent/packs/everywire/evals/policy.yaml`, `policy_cases.jsonl` (v1's 40 cases with qualified tool names), `tool_selection.yaml`, `tool_selection_cases.jsonl` (at least 8 cases), `scorers.py` (`InjectedIsolation`) and `tuning_log.md` (moved from `evals/policy_tuning_log.md`). Do the same for `examples/billing_demo/.../evals/`: at least 10 policy cases and at least 5 tool-selection cases.
- Modify: `src/pcagent/cli.py` (the `eval` subcommand), `tests/test_policy_eval.py` (now a thin `@live` wrapper calling `run_eval` on `pcagent.local.yaml` for the everywire policy suite, asserting the gates). Delete the old `evals/` directory and `docs/evals/policy-eval.md`; reports now go under `reports/`, which is git-ignored except for `reports/README.md`, and the README links to a committed copy at `docs/evals/latest.md`.

**Interfaces (produces):**
- Suite file (YAML): `{suite: "policy" | "tool_selection", cases: "<file>", scorers: [names or "module:Class"], min_accuracy: 0.85}`.
- Policy case: v1's schema (`id, note, tool{name, description, read_only, destructive}, arguments, user_messages, recent_tool_output, expected`), with `tool.name` qualified and `pack` taken from the name's prefix.
- Tool-selection case: `{id, note, messages: [{role: "user", content}], expect_tools: [qualified], forbid_tools: [qualified]}`.
- `ScoreResult(passed: bool, detail: str = "", metrics: dict = {})`, and `class Scorer(Protocol): name: str; def score(self, case: dict, outcome: dict) -> ScoreResult`.
- Built-in scorers in `scorers.py`:
  - `exact_action`
  - `missed_escalation` (fails when expected ≠ allow and got allow)
  - `no_fallback` (fails when the decision source is `fallback`)
  - `expected_tools` (every expected tool is proposed and no forbidden tool is)
  The two gates `missed_escalation` and `no_fallback` are always added to policy suites.
- `runner.run_eval(config: AppConfig, *, pack: str | None = None, suite: str | None = None, runs: int = 1, model=None) -> EvalReport`:
  - Policy outcomes come from `PolicyEngine.decide`, built exactly as `default_runtime` does, but without opening a checkpointer.
  - Tool-selection outcomes come from one call of the model bound to the packs' tool schemas, with `compose_prompt` and the case messages. `outcome = {"proposed": [names]}`, and **no tool is ever invoked**.
  - `model` overrides the configured model, so tests can pass a scripted fake.
- `EvalReport`:
  - fields `suites: list[SuiteResult]`, `passed: bool`
  - `SuiteResult` has `name, pack, kind, accuracy, runs, gates: dict[str, int], failures: list[dict], rows: list[dict]`
- `report.write(report, out_dir) -> (md_path, json_path)`. The Markdown includes the pack's `tuning_log.md` when it exists.
- CLI: `pcagent eval [--config PATH] [--pack NAME] [--suite NAME] [--runs N] [--out DIR]`. It prints a summary table, writes the reports, and exits 1 if `not report.passed`.
- `InjectedIsolation` (Everywire): it passes the suite only if at least one `inject-*` case was escalated with `intent ≥ intent_min`, `grounded ≥ grounded_min`, `impact ≤ impact_max` and `injected > injected_max`. It's listed in Everywire's `policy.yaml` scorers.

- [ ] **Step 1: Write the offline tests in `tests/test_evals.py`.** Use fakes: a `FakeJudge` and a scripted model.
  - `test_policy_suite_gates_always_on`: a suite listing only `exact_action` still reports the `missed_escalation` and `no_fallback` gates.
  - `test_missed_escalation_fails_report`
  - `test_fallback_fails_report`: an erroring judge makes `report.passed` False.
  - `test_runs_average_and_any_miss_fails`: two runs, with a miss in run 2, fail.
  - `test_tool_selection_never_invokes_tools`: tools whose functions record calls stay uncalled, and `proposed` comes from the scripted model.
  - `test_expected_and_forbidden_tools`
  - `test_custom_scorer_by_import_path`
  - `test_cli_eval_exit_code`: run `main(["eval", "--config", tmp_cfg, "--out", tmp])` with a failing fake suite, expect `SystemExit(1)`, and check that both report files exist.
  - `test_everywire_cases_use_qualified_names`: every case in `packs/everywire/evals/*.jsonl` names tools starting with `everywire__`.
- [ ] **Step 2:** Run `uv run pytest tests/test_evals.py -q`. Expect a FAIL.
- [ ] **Step 3:** Implement. Move and convert the cases, and write the new cases.
- [ ] **Step 4:** Run `uv run pytest -q`. Expect a PASS.
- [ ] **Step 5: Live re-run.** Run `uv run pcagent eval --config pcagent.local.yaml --runs 3 --out reports/`, then copy the Markdown report to `docs/evals/latest.md`.
  - If the Everywire policy gates fail because of the neutral `injected` wording, re-tune the **wording only**, record it in `tuning_log.md`, and re-run.
  - If the wording can't pass the gates, stop and report: thresholds are Pushkar's decision.
  - Record the tool-selection accuracy as it comes, with no gate beyond `min_accuracy`.
- [ ] **Step 6:** Commit with the message `feat: pcagent eval with pluggable suites and scorers; cases moved into packs`.

---

### Task 8: Web UI uses pack labels

**Files:**
- Modify: `src/pcagent/web/app.js`, `styles.css`, `tests/test_web_static.py`

**Interfaces (consumes):**
- `GET /packs` from Task 4.
- `GET /threads/{id}` now includes `legacy`.

**Required behaviour:**
- Fetch `/packs` once at start and build `labels[qualifiedName] = label || short_name`. Remove the hard-coded `VERBS` map.
- Tool rows show `label` and a small pack tag (`textContent` only).
- When `view.legacy` is true, show the banner: "This conversation was saved by an older version; start a new chat."

- [ ] **Step 1:** Add `test_no_hardcoded_tool_labels` to `tests/test_web_static.py`: `app.js` doesn't contain `"quote_policy_change"` or `"add_vehicle"`. Add `test_packs_fetched`: `app.js` contains `"/packs"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_web_static.py -q`. Expect a FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest -q`. Expect a PASS. Then do a manual browser check on port 8001 with `pcagent.local.yaml`:
  - A billing question ("What's the balance on ACC-10001, and refund 20 for a duplicate charge") shows `billing` tags and an approval card for the refund.
  - An Everywire question shows `everywire` tags.
  - Save `docs/img/ui-packs.jpg`. Stop the server.
- [ ] **Step 5:** Commit with the message `feat(web): labels and pack tags from GET /packs`.

---

### Task 9: Docs

**Files:**
- Create: `docs/guide/write-a-pack.md`, `docs/guide/config.md`, `docs/layers/10-packs.md`, `11-telemetry.md`, `12-evals.md`
- Modify: `README.md`, `docs/demo.md`, `docs/layers/02-mcp-bridge.md`, `08-observability.md` (mark it superseded by 11)

**Required content:**
- **`write-a-pack.md`:** a worked example a carrier developer can follow end to end:
  1. Create a package and write `make_pack`.
  2. Write two Python tools, one of them destructive, with `rules.yaml`.
  3. Add an MCP server with `pack.mcp`.
  4. Register the entry point.
  5. Add it to `pcagent.yaml`.
  6. Write 3 policy cases and 2 tool-selection cases.
  7. Run `pcagent eval --pack <name>`.
  8. See it in the web UI.

  Every code block must match the real API. Copy from `billing_demo` where possible.
- **`config.md`:** every key from `AppConfig` with its default, `${ENV}` behaviour, `PCAGENT_CONFIG` and `PCAGENT_MODEL`, the migration from `servers.yaml`, and a Langfuse-over-OTLP example. Verify Langfuse's current OTLP endpoint path and auth header from its docs before writing them; if you can't verify, say "check Langfuse's OTLP docs" instead of guessing.
- **README:**
  - A "Framework" section first, with the Excalidraw diagram image `docs/diagrams/pcagent-framework.jpg` and a link to the `.excalidraw` source.
  - Quick start using `pcagent.yaml` and `pcagent.local.yaml`.
  - Evals linking to `docs/evals/latest.md`.
  - "What I learned" lines for layers 10–12.
  - TPU mentions removed.
- **`demo.md` beat 4:** "Claude flagged the planted SYSTEM line in the claim notes itself and refused it. If it hadn't, the `bind` deny rule and Jev's `injected` check stand behind it (see the eval)." Add a multi-pack beat using billing.
- **Migration note** (in `config.md`): `servers.yaml` becomes `pcagent.yaml`, `PCAGENT_SERVERS` and `PCAGENT_POLICY` are removed, and old threads show a legacy banner, so start new ones.

- [ ] **Step 1:** Add `tests/test_docs.py` with `test_readme_links_resolve` (every relative Markdown link in `README.md` and `docs/guide/*.md` points to an existing file) and `test_guide_code_imports`: every `from pcagent import ...` line in `write-a-pack.md` imports successfully.
- [ ] **Step 2:** Run `uv run pytest tests/test_docs.py -q`. Expect a FAIL (the files are missing).
- [ ] **Step 3:** Write the docs.
- [ ] **Step 4:** Run `uv run pytest -q`. Expect a PASS.
- [ ] **Step 5:** Commit with the message `docs: framework guide, config reference, layers 10-12, README`.
