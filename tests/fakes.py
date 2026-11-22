"""Shared test doubles: a scripted chat model, a counting judge and recording tools."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from pydantic import Field

from langgraph.checkpoint.memory import InMemorySaver

from pcagent.graph import build_graph
from pcagent.policy.engine import Policy, PolicyEngine, ToolInfo
from pcagent.policy.jev import Judgments

GOOD = Judgments(0.97, 0.91, 0.02, 1.0, 1)  # every Jev check passes

# The v1 Everywire rules over unqualified names, so graph/API tests keep their tool names.
V1_RULES = {
    "search_*": "allow", "open_policy": "allow", "bind_policy_change": "deny",
    "withdraw_policy_change": "require_approval", "create_account": "require_approval",
    "quote_policy_change": "require_approval",
}
BAD = Judgments(0.04, 0.94, 0.97, 3.0, 3)   # injected, not asked for, binding


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted AIMessages in order, one per call."""

    responses: list[AIMessage]
    seen: list[list] = Field(default_factory=list)  # the messages each call received

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


class FakeJudge:
    """Returns `result`, or raises it if it is an exception. Counts calls."""

    def __init__(self, result: Judgments | Exception):
        self.result, self.calls = result, 0

    async def judge(self, state: dict[str, Any]) -> Judgments:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RecordingTool(StructuredTool):
    calls: list[dict] = Field(default_factory=list)


def fake_tool(name: str, result: Any | Exception = "ok") -> RecordingTool:
    """A tool taking free-form kwargs. `tool.calls` lists the args of each call."""
    calls: list[dict] = []

    async def run(**kwargs):
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    tool = RecordingTool.from_function(
        coroutine=run,
        name=name,
        description=f"fake {name}",
        args_schema={"type": "object", "properties": {}, "additionalProperties": True},
    )
    tool.calls = calls
    return tool


def ai_call(*calls):
    """An AIMessage with tool calls given as (id, name, args)."""
    return AIMessage("", tool_calls=[{"id": i, "name": n, "args": a} for i, n, a in calls])


def setup(responses, tools, judge=None, saver=None):
    model = ScriptedChatModel(responses=responses)
    info = {t.name: ToolInfo(t.name, description=t.description) for t in tools}
    engine = PolicyEngine(Policy(rules=dict(V1_RULES)), judge or FakeJudge(GOOD))
    saver = saver or InMemorySaver()
    graph = build_graph(model, tools, info, engine, saver)
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    return graph, cfg, model, info, engine, saver


def ask(text="Find policy PA-100001-01"):
    return {"messages": [HumanMessage(text)]}
