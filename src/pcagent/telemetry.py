"""OpenTelemetry tracing: pcagent's own spans, exported to any OTLP backend (or the console).

Spans: pcagent.run, pcagent.node.<name>, pcagent.policy.decision, pcagent.tool.call and
pcagent.llm (OTel GenAI attributes). Prompt and completion text are recorded only when
`tracing.capture_content` is true. Tracing never fails a run.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace import NoOpTracer, Status, StatusCode

from pcagent.config import TracingConfig

log = logging.getLogger(__name__)

_provider: TracerProvider | None = None
_capture = False


def setup_tracing(cfg: TracingConfig, exporter=None) -> None:
    """Install pcagent's tracer provider. `exporter` overrides the configured one (tests)."""
    global _provider, _capture
    reset_tracing()
    if cfg.exporter == "none" and exporter is None:
        return
    try:
        provider = TracerProvider(resource=Resource.create({"service.name": "pcagent"}))
        if exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        elif cfg.exporter == "console":
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        else:  # otlp, HTTP/protobuf
            endpoint = (cfg.endpoint or "http://localhost:4318").rstrip("/") + "/v1/traces"
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=cfg.headers)))
    except Exception as e:  # never log the config itself: headers may carry credentials
        log.warning("tracing disabled: exporter setup failed (%s)", type(e).__name__)
        return
    _provider, _capture = provider, cfg.capture_content


def reset_tracing() -> None:
    global _provider, _capture
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:
            pass
    _provider, _capture = None, False


def enabled() -> bool:
    return _provider is not None


def capture_content() -> bool:
    return _capture


def tracer():
    return _provider.get_tracer("pcagent") if _provider else NoOpTracer()


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attrs.items() if v is not None}


@contextmanager
def span(name: str, **attrs):
    """A span that can never break the code it wraps."""
    try:
        cm = tracer().start_as_current_span(name, attributes=_clean(attrs))
        s = cm.__enter__()
    except Exception:
        yield None
        return
    try:
        yield s
    except BaseException as e:
        try:
            if isinstance(e, Exception) and type(e).__name__ != "GraphInterrupt":
                s.set_status(Status(StatusCode.ERROR, type(e).__name__))
        finally:
            _safe_exit(cm, e)
        raise
    else:
        _safe_exit(cm, None)


def _safe_exit(cm, exc):
    try:
        cm.__exit__(type(exc) if exc else None, exc, exc.__traceback__ if exc else None)
    except Exception:
        pass


def set_attrs(s, **attrs) -> None:
    if s is None:
        return
    try:
        s.set_attributes(_clean(attrs))
    except Exception:
        pass


def decision_span(d: dict, tool_info) -> None:
    j = d.get("judgments") or {}
    with span("pcagent.policy.decision", tool=d["tool"], pack=getattr(tool_info, "pack", None),
              action=d["action"], source=d["source"], reason=d["reason"],
              **{f"jev.{k}": j.get(k) for k in ("intent", "grounded", "injected", "impact")}):
        pass


class LLMSpanHandler(BaseCallbackHandler):
    """One pcagent.llm span per model call, with OTel GenAI attributes."""

    def __init__(self):
        self._spans: dict[Any, Any] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, invocation_params=None, **kw):
        try:
            params = invocation_params or {}
            model = params.get("model") or params.get("model_name") or (serialized or {}).get("name")
            s = tracer().start_span("pcagent.llm", attributes=_clean({"gen_ai.request.model": model}))
            if _capture:
                s.set_attribute("gen_ai.prompt", "\n\n".join(str(m.content) for batch in messages for m in batch))
            self._spans[run_id] = s
        except Exception:
            pass

    def on_llm_end(self, response, *, run_id, **kw):
        s = self._spans.pop(run_id, None)
        if s is None:
            return
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            usage = getattr(msg, "usage_metadata", None) or {}
            reason = (getattr(msg, "response_metadata", None) or {}).get("finish_reason") or \
                (getattr(msg, "response_metadata", None) or {}).get("stop_reason")
            set_attrs(s, **{"gen_ai.usage.input_tokens": usage.get("input_tokens"),
                            "gen_ai.usage.output_tokens": usage.get("output_tokens"),
                            "gen_ai.response.finish_reasons": [reason] if reason else None})
            if _capture:
                s.set_attribute("gen_ai.completion", str(getattr(msg, "content", gen.text)))
        finally:
            try:
                s.end()
            except Exception:
                pass

    def on_llm_error(self, error, *, run_id, **kw):
        s = self._spans.pop(run_id, None)
        if s is not None:
            try:
                s.set_status(Status(StatusCode.ERROR, type(error).__name__))
                s.end()
            except Exception:
                pass


def llm_callbacks(cfg: TracingConfig) -> list:
    return [] if cfg.exporter == "none" or not enabled() else [LLMSpanHandler()]
