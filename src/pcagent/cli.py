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


def _post(http: httpx.Client, path: str, payload: dict | None = None) -> httpx.Response:
    try:
        return http.post(path, json=payload)
    except httpx.HTTPError as e:
        print(f"cannot reach pcagent API at {http.base_url}: {e or type(e).__name__}", file=sys.stderr)
        sys.exit(1)


def _result(r: httpx.Response) -> dict | None:
    """Normalise a run/approvals response to a result dict, or print the problem and return None."""
    try:
        body = r.json()
    except ValueError:
        print(f"error {r.status_code}: {r.text[:200]}")
        return None
    if not isinstance(body, dict):
        print(f"error {r.status_code}: {str(body)[:200]}")
        return None
    if r.status_code == 409:
        detail = body.get("detail")
        if detail == "awaiting approval" and body.get("pending"):
            return {"status": "awaiting_approval", "messages": [], "decisions": [],
                    "pending": body["pending"]}
        if detail == "thread busy":
            print("thread busy: another request is running on this thread; wait a moment and try again")
        else:
            print(f"error 409: {detail or body}")
        return None
    if r.status_code != 200:
        print(f"error {r.status_code}: {body}")
        return None
    return body


def _chat(args) -> None:
    api = (args.api or get_settings().api_url).rstrip("/")
    with httpx.Client(base_url=api, timeout=600) as http:
        thread = args.thread
        if not thread:
            r = _post(http, "/threads")
            try:
                thread = r.json()["thread_id"]
            except (ValueError, KeyError, TypeError):
                print(f"error {r.status_code}: could not create a thread: {r.text[:200]}")
                sys.exit(1)
        print(f"thread: {thread}")
        try:
            while True:
                try:
                    msg = input("you> ")
                except EOFError:
                    break
                if not msg.strip():
                    continue
                body = _result(_post(http, f"/threads/{thread}/runs", {"message": msg}))
                if body is not None and not _drive(http, thread, body):
                    break
        except KeyboardInterrupt:
            pass


def _drive(http: httpx.Client, thread: str, body: dict) -> bool:
    """Render a result and walk any approvals. Returns False if the user left mid-approval."""
    print(render(body))
    while body["status"] == "awaiting_approval":
        decisions = {}
        try:
            for p in body["pending"]:
                ans = input(f"Approve {p['tool']}({_args(p['arguments'])})? [y/N] ")
                decisions[p["tool_call_id"]] = {"approved": ans.strip().lower() in ("y", "yes"), "note": ""}
        except (EOFError, KeyboardInterrupt):
            print(f"\napproval still pending; resume with: pcagent chat --thread {thread}")
            return False
        body = _result(_post(http, f"/threads/{thread}/approvals", {"decisions": decisions}))
        if body is None:
            return True
        print(render(body))
    return True


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
