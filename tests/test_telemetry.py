import logging

import pytest
from langchain_core.messages import AIMessage
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fakes import ask, ai_call, fake_tool, setup
from pcagent import telemetry
from pcagent.config import TracingConfig
from pcagent.runs import run_turn


@pytest.fixture
def mem():
    exp = InMemorySpanExporter()
    telemetry.setup_tracing(TracingConfig(exporter="console"), exporter=exp)
    yield exp
    telemetry.reset_tracing()


def names(exp):
    return [s.name for s in exp.get_finished_spans()]


def attrs(exp, name):
    return [dict(s.attributes) for s in exp.get_finished_spans() if s.name == name]


async def run(graph, cbs=None, thread="t1"):
    return await run_turn(graph, thread, "find it", callbacks=cbs)


def test_none_installs_nothing():
    telemetry.reset_tracing()
    telemetry.setup_tracing(TracingConfig(exporter="none"))
    assert telemetry.enabled() is False
    assert telemetry.llm_callbacks(TracingConfig(exporter="none")) == []


async def test_spans_for_allowed_call(mem):
    t = fake_tool("search_policies", "found")
    graph = setup([ai_call(("c1", "search_policies", {"q": "x"})), AIMessage("Done.")], [t])[0]
    await run(graph)
    n = names(mem)
    assert "pcagent.run" in n and "pcagent.node.agent" in n and "pcagent.tool.call" in n
    [d] = attrs(mem, "pcagent.policy.decision")
    assert d["action"] == "allow" and d["tool"] == "search_policies" and d["source"] == "rule"
    [r] = attrs(mem, "pcagent.run")
    assert r["thread_id"] == "t1" and r["status"] == "completed" and r["kind"] == "run"


async def test_denied_call_has_decision_span_no_tool_span(mem):
    t = fake_tool("bind_policy_change", "bound")
    graph = setup([ai_call(("c1", "bind_policy_change", {})), AIMessage("Blocked.")], [t])[0]
    await run(graph)
    assert attrs(mem, "pcagent.policy.decision")[0]["action"] == "deny"
    assert "pcagent.tool.call" not in names(mem)


async def test_llm_span_without_content(mem):
    reply = AIMessage("Hello.", usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14})
    graph = setup([reply], [])[0]
    await run(graph, telemetry.llm_callbacks(TracingConfig(exporter="console")))
    [s] = attrs(mem, "pcagent.llm")
    assert s["gen_ai.usage.input_tokens"] == 11 and s["gen_ai.usage.output_tokens"] == 3
    assert not any(k.startswith("gen_ai.prompt") or k.startswith("gen_ai.completion") for k in s)


async def test_capture_content_on():
    exp = InMemorySpanExporter()
    cfg = TracingConfig(exporter="console", capture_content=True)
    telemetry.setup_tracing(cfg, exporter=exp)
    try:
        graph = setup([AIMessage("Hello there.")], [])[0]
        await run(graph, telemetry.llm_callbacks(cfg))
        [s] = attrs(exp, "pcagent.llm")
        assert "Hello there." in s["gen_ai.completion"]
    finally:
        telemetry.reset_tracing()


class Broken(SpanExporter):
    def export(self, spans):
        raise RuntimeError("collector down")

    def shutdown(self):
        pass


async def test_broken_exporter_does_not_fail_run():
    telemetry.setup_tracing(TracingConfig(exporter="console"), exporter=Broken())
    try:
        graph = setup([AIMessage("Fine.")], [])[0]
        assert (await run(graph))["status"] == "completed"
    finally:
        telemetry.reset_tracing()


def test_setup_failure_logs_without_secrets(monkeypatch, caplog):
    def boom(**kw):
        raise ValueError(f"bad endpoint {kw}")
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", boom)
    cfg = TracingConfig(exporter="otlp", endpoint="http://x:4318", headers={"Authorization": "Basic SECRET"})
    with caplog.at_level(logging.WARNING):
        telemetry.setup_tracing(cfg)
    assert telemetry.enabled() is False
    assert "SECRET" not in caplog.text and "ValueError" in caplog.text
    telemetry.reset_tracing()
