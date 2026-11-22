# Layer 3: Graph

## What it does
`build_graph(model, tools, info, engine, checkpointer)` compiles a custom `StateGraph`:

```
START → agent ──(tool calls?)──▶ policy ──(any require_approval?)──▶ approval
          ▲  └─(no)─▶ END          │ no                                │
          └──────── tools ◀────────┴───────────────────────────────────┘
```

- **agent**: `SYSTEM_PROMPT` + messages into the model (`make_model` = `ChatLiteLLM(model=...)`).
- **policy**: one `PolicyEngine.decide` per tool call; a name missing from `info` is `deny`
  / `"unknown tool"` without asking Jev. Writes `pending` and appends to `audit`.
  Jev sees tool output since the last user message, capped at the newest 4000 chars (tail kept).
  If any call id is empty or repeated, every call in the turn is `deny` / `"ambiguous tool call ids"`.
  If `decide` itself raises, that call is `require_approval` / `fallback`, so the node never crashes.
- **approval**: `interrupt({"calls": [require_approval decisions]})`, resumed with
  `Command(resume={tool_call_id: {"approved": bool, "note": str}})`. Missing or malformed
  ids count as rejected; unknown ids are ignored. Each answer becomes `allow`/`deny`,
  `source="human"`, with an audit record carrying the `note`.
- **tools**: decisions are matched to calls by position (never by id) and must name the
  same tool and id, or the call is blocked. One `ToolMessage` per call, in call order. Allowed calls run (sequentially);
  any exception, including MCP's `ToolException`, becomes `status="error"`. Denied or
  rejected calls get `status="error"`, `"Blocked by policy: <reason>"`, so the model can explain.

## Why it's built this way
- **policy and approval are separate nodes.** On resume LangGraph re-runs the interrupted
  node from its first line. If Jev ran in `approval` it would be called again on every
  resume (and could answer differently). `policy` has completed and been checkpointed before
  `approval` interrupts, so Jev runs once. Pinned by `test_approval_interrupts_and_judge_called_once`.
- **A mixed turn waits.** If any call needs approval, none run until the person answers, so
  an allowed search never runs ahead of a rejected edit it was paired with.
- **Reducers.** `messages` uses `add_messages` (append, or replace by id); `audit` uses
  `operator.add`, so nodes return only new records; `pending` has no reducer and is replaced
  each turn, and `approval` returns the whole updated list.
- All state lives in the checkpointer, so a fresh graph built on the same saver can resume
  (`test_resume_on_new_graph_instance`, the restart case).

## What surprised us
- Keying decisions by `tool_call_id` was a bypass: a deny and an allow sharing id `"x"` let
  the denied bind run, and one "yes" approved every call with that id. Fixed by the rules above.
- `interrupt()` now takes `response_schema=`; unused here. `StateSnapshot` has `.interrupts`
  (no need to dig through `.tasks`), and `ainvoke` returns `"__interrupt__"` in its output.
- `StructuredTool` is a pydantic model, so test fakes can't set ad-hoc attributes;
  `RecordingTool(StructuredTool)` declares a `calls` field instead.
- Invoking a tool with a full `ToolCall` dict returns a ready `ToolMessage`, keeping ids and names.
