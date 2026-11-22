# 10 · Packs

**What it does.** A pack bundles one system's tools, rules, prompt, labels, evals and Jev hints.
Tools come from Python functions (`@pack.tool`) or MCP servers (`pack.mcp`), and both become
`<pack>__<tool>` LangChain tools plus a `ToolInfo` the policy reads. `pcagent.yaml` lists packs by
`module:attr` or entry-point name; a factory gets the pack's `settings`, so dev and prod differ by
config only. One agent loads several packs; Everywire and `billing_demo` ship as examples.

**Why it was built this way.**
- Carrier IT teams are often Java or .NET, so MCP stays the language-neutral path; Python tools
  are the fast path for a thin REST wrapper.
- Both sources produce the same `ToolInfo`, so policy, approvals, audit, UI and evals don't care
  where a tool came from. The safety story doesn't fork.
- Pack rules use short names; global rules use qualified names and may only tighten. An operator
  can't accidentally loosen a pack.
- Model providers only accept `[a-zA-Z0-9_-]{1,64}` tool names, hence `__` rather than a dot.

**What surprised us.**
- MCP's stdio transport wraps errors raised during shutdown in an `ExceptionGroup`; the loader closes
  connections first and re-raises the original `PackError`.
- `billing__` plus 55 characters is exactly 64: the plan's own test had the limit off by one.
