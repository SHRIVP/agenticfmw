"""pcagent command line: `serve` the API, `chat` against it."""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from .config import get_settings

_LABEL = {"allow": "ALLOW", "require_approval": "ASK", "deny": "DENY"}


def _args(arguments) -> str:
    return json.dumps(arguments or {}, separators=(",", ":"), default=str)


def render(result: dict) -> str:
    lines: list[str] = []
    for d in result.get("decisions", []):
        label = _LABEL.get(d["action"], d["action"].upper())
        lines.append(f"→ {d['tool']}({_args(d.get('arguments'))})  [{label}] {d.get('reason', '')}")
        j = d.get("judgments")
        if j:
            lines.append(
                f"    intent {j['intent']:.2f} · grounded {j['grounded']:.2f} · "
                f"injected {j['injected']:.2f} · impact {j['impact']:.1f}"
            )
    ai_text: list[str] = []
    for m in result.get("messages", []):
        content = m.get("content")
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        if m.get("type") == "tool":
            lines.append(f"  ← {text[:200]}")
        elif m.get("type") == "ai" and text:
            ai_text.append(text)
    lines.extend(ai_text[-1:])
    return "\n".join(lines)


def _serve(args) -> None:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)


def _chat(args) -> None:
    api = (args.api or get_settings().api_url).rstrip("/")
    with httpx.Client(base_url=api, timeout=600) as http:
        thread = args.thread or http.post("/threads").json()["thread_id"]
        print(f"thread: {thread}")
        try:
            while True:
                try:
                    msg = input("you> ")
                except EOFError:
                    break
                if not msg.strip():
                    continue
                r = http.post(f"/threads/{thread}/runs", json={"message": msg})
                try:
                    body = r.json()
                except ValueError:
                    print(f"error {r.status_code}: {r.text[:200]}")
                    continue
                if r.status_code == 409 and body.get("detail") == "awaiting approval":
                    body = {"status": "awaiting_approval", "messages": [], "decisions": [],
                            "pending": body["pending"]}
                elif r.status_code != 200:
                    print(f"error {r.status_code}: {body}")
                    continue
                _drive(http, thread, body)
        except (KeyboardInterrupt, EOFError):
            pass


def _drive(http: httpx.Client, thread: str, body: dict) -> None:
    print(render(body))
    while body["status"] == "awaiting_approval":
        decisions = {}
        for p in body["pending"]:
            ans = input(f"Approve {p['tool']}({_args(p['arguments'])})? [y/N] ")
            decisions[p["tool_call_id"]] = {"approved": ans.strip().lower() in ("y", "yes"), "note": ""}
        r = http.post(f"/threads/{thread}/approvals", json={"decisions": decisions})
        if r.status_code != 200:
            print(f"error {r.status_code}: {r.text}")
            return
        body = r.json()
        print(render(body))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pcagent")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=_serve)
    c = sub.add_parser("chat")
    c.add_argument("--thread")
    c.add_argument("--api")
    c.set_defaults(fn=_chat)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
