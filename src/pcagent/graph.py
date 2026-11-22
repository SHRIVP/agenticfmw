"""The agent graph: agent -> policy -> approval (interrupt) -> tools -> agent.

    START -> agent --(tool calls?)--> policy --(any require_approval?)--> approval
               ^  `-(no)-> END          | no                                |
               `-------- tools <--------+-----------------------------------'

`policy` and `approval` are separate nodes because LangGraph re-runs an interrupted
node from its start on resume: Jev is called in `policy`, which has finished and is
checkpointed by the time `approval` interrupts, so a resume never calls Jev again.
"""

from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from .gateway import CORE_PROMPT
from .policy.engine import Decision, PolicyEngine, ToolInfo

log = logging.getLogger(__name__)

RECENT_OUTPUT_LIMIT = 4000
AMBIGUOUS_IDS = "ambiguous tool call ids"


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    pending: list[dict]  # decisions for the latest AI message's tool calls (replaced each turn)
    audit: Annotated[list[dict], operator.add]  # append-only decision records


def policy_context(messages: list[AnyMessage]) -> dict[str, Any]:
    """What the judge sees: every user message, and the newest 4000 chars of tool output since the last one."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    recent = [m.text for m in messages[last_human + 1 :] if isinstance(m, ToolMessage)]
    return {
        "user_messages": [m.content for m in messages if isinstance(m, HumanMessage)],
        # Keep the newest output: a later note driving this call (and any planted
        # instruction in it) must not be pushed out by an earlier long lookup.
        "recent_tool_output": "\n\n".join(recent)[-RECENT_OUTPUT_LIMIT:],
    }


def ids_ambiguous(ids: list[Any]) -> bool:
    """True when any id is empty or repeated: decisions and approvals could then reach the wrong call."""
    return any(not i for i in ids) or len(set(ids)) != len(ids)


def _resume_entry(answers: Any, call_id: str) -> tuple[bool, str]:
    """(approved, note) for one call. Anything missing or malformed counts as rejected."""
    entry = answers.get(call_id) if isinstance(answers, dict) else None
    if not isinstance(entry, dict):
        return False, ""
    return entry.get("approved") is True, str(entry.get("note") or "")


def build_graph(
    model, tools: list[BaseTool], info: dict[str, ToolInfo], engine: PolicyEngine, checkpointer,
    system_prompt: str = CORE_PROMPT,
) -> CompiledStateGraph:
    bound = model.bind_tools(tools) if tools else model
    by_name = {t.name: t for t in tools}

    async def agent(state: AgentState) -> dict:
        reply = await bound.ainvoke([SystemMessage(system_prompt), *state["messages"]])
        return {"messages": [reply]}

    async def policy(state: AgentState) -> dict:
        calls = state["messages"][-1].tool_calls
        context = policy_context(state["messages"])
        pending = []
        ambiguous = ids_ambiguous([c.get("id") for c in calls])
        for call in calls:
            tool = info.get(call["name"])
            if ambiguous:  # deny the whole turn; nothing is judged
                d = Decision(call.get("id") or "", call["name"], call.get("args", {}), "deny", AMBIGUOUS_IDS, "rule")
            elif tool is None:
                d = Decision(call["id"], call["name"], call.get("args", {}), "deny", "unknown tool", "rule")
            else:
                try:
                    d = await engine.decide(call, tool, context)
                except Exception as exc:  # a crash here would strand the AI message's tool calls
                    log.warning("policy engine failed for %s: %r", call["name"], exc)
                    reason = f"policy engine error ({exc.__class__.__name__})"
                    d = Decision(call["id"], call["name"], call.get("args", {}), "require_approval", reason, "fallback")
            pending.append(d.as_dict())
        return {"pending": pending, "audit": [dict(d) for d in pending]}

    def approval(state: AgentState) -> dict:
        # Only interrupt() and applying the answer: this node re-runs from the top on resume.
        asks = [d for d in state["pending"] if d["action"] == "require_approval"]
        answers = interrupt({"calls": asks})
        # Answers are keyed by id, so with ambiguous ids no answer can count as approval.
        ambiguous = ids_ambiguous([d["tool_call_id"] for d in state["pending"]])
        pending, audit = [], []
        for d in state["pending"]:
            if d["action"] == "require_approval":
                approved, note = (False, AMBIGUOUS_IDS) if ambiguous else _resume_entry(answers, d["tool_call_id"])
                verdict = "approved by a person" if approved else "rejected by a person"
                d = {
                    **d,
                    "action": "allow" if approved else "deny",
                    "reason": f"{verdict}: {note}" if note else verdict,
                    "source": "human",
                }
                audit.append({**d, "note": note})
            pending.append(d)
        return {"pending": pending, "audit": audit}

    async def run_tools(state: AgentState) -> dict:
        calls = state["messages"][-1].tool_calls
        pending = state["pending"]
        # Decisions are matched to calls by position, never by id (ids can repeat).
        if ids_ambiguous([c.get("id") for c in calls]):
            blocked = AMBIGUOUS_IDS
        elif len(pending) != len(calls):
            blocked = "no decision"
        else:
            blocked = None
        out = []
        for i, call in enumerate(calls):  # in call order, one message per call
            d = None if blocked else pending[i]
            if d is not None and (d["tool"] != call["name"] or d["tool_call_id"] != call["id"]):
                d = {**d, "action": "deny", "reason": "decision does not match the call"}
            if d is None or d["action"] != "allow":
                out.append(_error(call, f"Blocked by policy: {d['reason'] if d else blocked}"))
                continue
            try:
                msg = await by_name[call["name"]].ainvoke({**call, "type": "tool_call"})
            except Exception as exc:  # ToolException from MCP, or anything else
                out.append(_error(call, f"Error: {exc}"))
                continue
            out.append(msg)
        return {"messages": out}

    def after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        return "policy" if isinstance(last, AIMessage) and last.tool_calls else END

    def after_policy(state: AgentState) -> str:
        return "approval" if any(d["action"] == "require_approval" for d in state["pending"]) else "tools"

    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("policy", policy)
    g.add_node("approval", approval)
    g.add_node("tools", run_tools)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", after_agent, ["policy", END])
    g.add_conditional_edges("policy", after_policy, ["approval", "tools"])
    g.add_edge("approval", "tools")
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=checkpointer)


def _error(call: dict, text: str) -> ToolMessage:
    return ToolMessage(text, tool_call_id=call["id"], name=call["name"], status="error")
