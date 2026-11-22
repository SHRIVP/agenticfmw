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
