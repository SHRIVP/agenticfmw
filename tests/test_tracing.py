from pcagent.tracing import callbacks


def test_no_keys_no_callbacks(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    assert callbacks() == []


def test_keys_give_handler(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-fake")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-fake")
    monkeypatch.setenv("LANGFUSE_HOST", "http://127.0.0.1:9")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    assert len(callbacks()) == 1


def test_misconfigured_langfuse_logs_and_returns_empty(monkeypatch, caplog):
    import sys
    import types

    broken = types.ModuleType("langfuse.langchain")

    class CallbackHandler:
        def __init__(self, *a, **k):
            raise ValueError("bad host sk-lf-should-not-be-logged")

    broken.CallbackHandler = CallbackHandler
    monkeypatch.setitem(sys.modules, "langfuse.langchain", broken)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-fake")
    with caplog.at_level("WARNING"):
        assert callbacks() == []
    assert "tracing disabled" in caplog.text
    assert "sk-lf" not in caplog.text  # only the exception type is logged
