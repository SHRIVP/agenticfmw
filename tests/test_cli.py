from pcagent.cli import render


def _dec(tool, action, source, judgments=None, reason="r"):
    return {"tool_call_id": "c", "tool": tool, "arguments": {"a": 1}, "action": action,
            "reason": reason, "source": source, "judgments": judgments}


def test_render_shows_decisions_and_scores():
    result = {
        "status": "completed",
        "messages": [
            {"type": "tool", "content": "ok result", "tool_call_id": "c"},
            {"type": "ai", "content": "All done."},
        ],
        "pending": [],
        "decisions": [
            _dec("add_vehicle", "allow", "jev",
                 {"intent": 0.97, "grounded": 0.91, "injected": 0.02, "impact": 1.0}),
            _dec("bind_policy_change", "deny", "rule"),
        ],
    }
    out = render(result)
    assert "[ALLOW]" in out and "[DENY]" in out
    assert "injected 0.02" in out
    assert "intent 0.97 · grounded 0.91 · injected 0.02 · impact 1.0" in out
    assert "  ← ok result" in out
    assert out.rstrip().endswith("All done.")


def test_render_marks_ask():
    out = render({"status": "awaiting_approval", "messages": [], "pending": [],
                  "decisions": [_dec("quote_policy_change", "require_approval", "rule")]})
    assert "[ASK]" in out


# --- chat error handling -------------------------------------------------
import builtins

import httpx
import pytest

from pcagent import cli

PENDING = {"detail": "awaiting approval", "pending": [
    {"tool_call_id": "c1", "tool": "quote_policy_change", "arguments": {"p": 1}}]}


def _chat(monkeypatch, handler, inputs, args=("--thread", "T")):
    real = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    it = iter(inputs)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    cli.main(["chat", "--api", "http://x", *args])


def test_chat_server_unreachable(monkeypatch, capsys):
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(SystemExit) as e:
        _chat(monkeypatch, handler, [], args=())
    assert e.value.code == 1
    assert "cannot reach pcagent API at http://x" in capsys.readouterr().err


def test_approvals_thread_busy(monkeypatch, capsys):
    def handler(request):
        if request.url.path.endswith("/runs"):
            return httpx.Response(409, json=PENDING)
        return httpx.Response(409, json={"detail": "thread busy"})

    _chat(monkeypatch, handler, ["hi", "y"])
    assert "thread busy" in capsys.readouterr().out


def test_approvals_non_json_500(monkeypatch, capsys):
    def handler(request):
        if request.url.path.endswith("/runs"):
            return httpx.Response(409, json=PENDING)
        return httpx.Response(500, text="Internal Server Error")

    _chat(monkeypatch, handler, ["hi", "y"])
    assert "error 500: Internal Server Error" in capsys.readouterr().out


def test_eof_at_approval_prompt_prints_resume_hint(monkeypatch, capsys):
    posted = []

    def handler(request):
        posted.append(request.url.path)
        return httpx.Response(409, json=PENDING)

    _chat(monkeypatch, handler, ["hi"])
    out = capsys.readouterr().out
    assert "approval still pending" in out
    assert "pcagent chat --thread T" in out
    assert not any(p.endswith("/approvals") for p in posted)
