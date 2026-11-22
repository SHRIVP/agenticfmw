"""The agent graph: agent -> policy -> approval (interrupt) -> tools -> agent.

    START -> agent --(tool calls?)--> policy --(any require_approval?)--> approval
               ^  `-(no)-> END          | no                                |
               `-------- tools <--------+-----------------------------------'

`policy` and `approval` are separate nodes because LangGraph re-runs an interrupted
node from its start on resume: Jev is called in `policy`, which has finished and is
checkpointed by the time `approval` interrupts, so a resume never calls Jev again.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from .gateway import SYSTEM_PROMPT
from .policy.engine import Decision, PolicyEngine, ToolInfo

RECENT_OUTPUT_LIMIT = 4000


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    pending: list[dict]  # decisions for the latest AI message's tool calls (replaced each turn)
    audit: Annotated[list[dict], operator.add]  # append-only decision records


def policy_context(messages: list[AnyMessage]) -> dict[str, Any]:
    """What the judge sees: every user message, and tool output since the last one."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    recent = [m.text for m in messages[last_human + 1 :] if isinstance(m, ToolMessage)]
    return {
        "user_messages": [m.content for m in messages if isinstance(m, HumanMessage)],
        "recent_tool_output": "\n\n".join(recent)[:RECENT_OUTPUT_LIMIT],
    }


def _resume_entry(answers: Any, call_id: str) -> tuple[bool, str]:
    """(approved, note) for one call. Anything missing or malformed counts as rejected."""
    entry = answers.get(call_id) if isinstance(answers, dict) else None
    if not isinstance(entry, dict):
        return False, ""
    return entry.get("approved") is True, str(entry.get("note") or "")


def build_graph(
    model, tools: list[BaseTool], info: dict[str, ToolInfo], engine: PolicyEngine, checkpointer
) -> CompiledStateGraph:
    bound = model.bind_tools(tools) if tools else model
    by_name = {t.name: t for t in tools}

    async def agent(state: AgentState) -> dict:
        reply = await bound.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    async def policy(state: AgentState) -> dict:
        calls = state["messages"][-1].tool_calls
        context = policy_context(state["messages"])
        pending = []
        for call in calls:
            tool = info.get(call["name"])
            if tool is None:
                d = Decision(call["id"], call["name"], call.get("args", {}), "deny", "unknown tool", "rule")
            else:
                d = await engine.decide(call, tool, context)
            pending.append(d.as_dict())
        return {"pending": pending, "audit": [dict(d) for d in pending]}

    def approval(state: AgentState) -> dict:
        # Only interrupt() and applying the answer: this node re-runs from the top on resume.
        asks = [d for d in state["pending"] if d["action"] == "require_approval"]
        answers = interrupt({"calls": asks})
        pending, audit = [], []
        for d in state["pending"]:
            if d["action"] == "require_approval":
                approved, note = _resume_entry(answers, d["tool_call_id"])
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
        decisions = {d["tool_call_id"]: d for d in state["pending"]}
        out = []
        for call in state["messages"][-1].tool_calls:  # in call order, one message per call
            d = decisions.get(call["id"])
            if d is None or d["action"] != "allow":
                reason = d["reason"] if d else "no decision"
                out.append(_error(call, f"Blocked by policy: {reason}"))
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
