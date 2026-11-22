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
