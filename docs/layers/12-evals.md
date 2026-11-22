# 12 · Evals

**What it does.** `pcagent eval` finds every pack's suites (`evals/*.yaml`) and runs them:

- **policy**: each case goes through `PolicyEngine.decide` with the real rules and judge.
- **tool_selection**: the agent's model is called once with the packs' tool schemas; the proposed
  tool calls are scored. No tool ever runs, so it is safe against real systems.

Scorers are pluggable (`exact_action`, `expected_tools`, or any `module:Class`). Policy suites
always add two gates: **no missed escalations** and **no judge fallbacks**, so a Jev outage
fails the eval instead of looking clean. `--runs N` averages accuracy; a miss in any run fails.
Reports go to `reports/` as Markdown and JSON, and the exit code fails CI.

**Why it was built this way.** The v1 eval was a pytest file hard-wired to one PAS. Moving cases
into packs means a carrier's pack ships its own evidence, and the gates stay the same for everyone.

**Status.** The Everywire cases were renamed to qualified tool names and Jev's `injected`
wording became system-neutral (pack hints replace Everywire's examples). The live re-run of the
suites was not done in this build; [docs/evals/latest.md](../evals/latest.md) is the last live run,
from before the change.
