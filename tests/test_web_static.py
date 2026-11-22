"""Offline checks on the web UI's static files (no browser)."""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "pcagent" / "web"


def read(name: str) -> str:
    return (WEB / name).read_text()


def test_no_innerhtml_with_tool_data():
    # Every innerHTML assignment must be sanitized Markdown or a string literal.
    for n, line in enumerate(read("app.js").splitlines(), 1):
        m = re.search(r"\.innerHTML\s*=\s*(.+)", line)
        if not m:
            continue
        rhs = m.group(1).strip()
        literal = re.fullmatch(r"""(['"`])[^${}]*\1;?""", rhs)
        assert "DOMPurify.sanitize" in rhs or literal, f"app.js:{n} unsafe innerHTML: {line.strip()}"


def test_cdn_scripts_pinned():
    html = read("index.html")
    assert "marked/12.0.2" in html
    assert "dompurify/3.1.6" in html
    assert "@latest" not in html


def test_no_anthropic_branding():
    for name in ("index.html", "app.js", "styles.css"):
        text = read(name).lower()
        assert "anthropic" not in text, name
        assert "claude" not in text, name


def test_page_title():
    assert re.search(r"<title>[^<]*pcagent[^<]*</title>", read("index.html"))
