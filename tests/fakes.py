"""Shared test doubles: a scripted chat model, a counting judge and recording tools."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from pydantic import Field

from pcagent.policy.jev import Judgments

GOOD = Judgments(0.97, 0.91, 0.02, 1.0, 1)  # every Jev check passes
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
