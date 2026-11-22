# Layer 2: MCP bridge

## What it does
Packs declare MCP servers with `pack.mcp(...)` (originally `servers.yaml` listed them; see [10 · Packs](10-packs.md)) (Streamable HTTP `url`, or stdio `command`). `MCPToolbox`
opens each in one `AsyncExitStack`, and exposes `.tools` (LangChain `StructuredTool`s) and
`.info` (`name -> ToolInfo`) for the policy engine. `${VAR}` is expanded in YAML values and a
relative `cwd` resolves against the YAML file's directory. `servers.local.yaml` runs the
`../gwmcp` mock instead of the deployed PAS. `claimdocs` is a tiny demo server whose notes
for `PA-200001-01` carry a planted prompt injection.

## Why it's built this way
- No `langchain-mcp-adapters`: it needs `mcp<2`, while `litellm` needs `mcp>=2.3`. The bridge is ~100 lines.
- Annotations feed the policy: `read_only_hint` becomes `ToolInfo.read_only` (default allow),
  `destructive_hint` becomes `ToolInfo.destructive` (never auto-allowed). As in the MCP spec,
  a tool that is not read-only is destructive unless it says `destructive_hint: false`, so a
  tool with no annotations goes to a person (unless a rule denies it). gwmcp sets both hints.
- Fail loudly: a server that won't start raises `ToolboxError` naming the server; duplicate
  tool names across servers are rejected, since the policy is keyed by name.
- Tool errors: the coroutine raises `ToolException` and the tool does not swallow it
  (no `handle_tool_error`), so the graph node can turn it into a `status="error"` ToolMessage.

## What surprised us
- mcp 2.x renamed fields to snake_case: `inputSchema` -> `input_schema`, `isError` -> `is_error`,
  `readOnlyHint` -> `read_only_hint`, `destructiveHint` -> `destructive_hint`. `annotations` may be None.
- `streamable_http_client` yields a tuple; use only `streams[0]` and `streams[1]`.
- Server side, `FastMCP` is now `mcp.server.MCPServer` (`@server.tool(annotations=ToolAnnotations(...))`,
  `server.run("stdio")`). The lowlevel `Server` still exists (gwmcp uses it).
- The stdio subprocess needs `PATH` or `uv`/`python` aren't found. It gets MCP's
  `get_default_environment()` (PATH, HOME, USER, ...) plus the spec's `env`, not the whole
  `os.environ`, so API keys don't leak to servers; pass one explicitly as `env: {K: "${K}"}`.
- The Streamable HTTP session doesn't reconnect: if the deployed PAS restarts, restart pcagent.
