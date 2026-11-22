"""Everywire PAS as a pack: tools from the gwmcp MCP server plus the claimdocs test server."""

from __future__ import annotations

import sys
from pathlib import Path

from pcagent.packs import Pack

PROMPT = """\
Everywire PAS is a property and casualty policy administration system. Edits go into a \
draft policy change: start or reuse a policy change, make the edits there, and quote it so \
the user can review the premium. You never bind; binding is done by a person in the PAS \
view, so tell the user when the change is ready for them to bind."""

LABELS = {
    "search_policies": "Searched policies", "search_accounts": "Searched accounts",
    "search_jobs": "Searched jobs", "open_policy": "Opened the policy",
    "start_policy_change": "Started a policy change", "start_submission": "Started a submission",
    "create_account": "Created an account", "add_vehicle": "Added a vehicle",
    "remove_vehicle": "Removed a vehicle", "add_driver": "Added a driver",
    "set_home_details": "Set home details", "update_coverage": "Updated coverage",
    "quote_policy_change": "Quoted the change", "withdraw_policy_change": "Withdrew the change",
    "bind_policy_change": "Bind the change", "get_claim_notes": "Read the claim notes",
}

JUDGE_HINTS = [
    "Calling start_policy_change before editing a policy the user asked to change is the normal next step.",
    "Calling quote_policy_change after edits the user asked to quote is the normal next step.",
]


def make_pack(settings: dict) -> Pack:
    gw = settings.get("gwmcp") or {}
    pack = Pack("everywire", prompt=PROMPT, rules="rules.yaml", labels=LABELS, evals="evals",
                judge_hints=JUDGE_HINTS, base_dir=Path(__file__).parent)
    if gw.get("url"):
        pack.mcp("gwmcp", url=gw["url"])
    else:
        cwd = gw.get("cwd")
        pack.mcp("gwmcp", command=gw.get("command", "uv"), args=gw.get("args", []),
                 env=gw.get("env", {}), cwd=str(Path(cwd).resolve()) if cwd else None)
    pack.mcp("claimdocs", command=sys.executable, args=["-m", "pcagent.packs.everywire.claimdocs"])
    return pack
