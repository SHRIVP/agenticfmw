"""Run every pack's eval suites. Policy cases go through PolicyEngine.decide; tool-selection
cases call the agent's model once and score the proposed tool calls. No tool is ever run."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from pcagent.config import AppConfig
from pcagent.evals import EvalReport, SuiteResult
from pcagent.evals.scorers import GATES, load_scorer
from pcagent.gateway import compose_prompt, make_model
from pcagent.packs import SEP
from pcagent.packs.loader import open_packs, resolve_pack
from pcagent.policy.engine import PolicyEngine, ToolInfo
from pcagent.policy.judges import make_judge
from pcagent.runtime import policy_set

_NO_JUDGE = object()


def load_suites(pack) -> list[dict]:
    if not pack.evals_path or not pack.evals_path.is_dir():
        return []
    suites = []
    for f in sorted(pack.evals_path.glob("*.yaml")):
        spec = yaml.safe_load(f.read_text()) or {}
        if spec.get("suite") not in ("policy", "tool_selection"):
            continue
        cases = [json.loads(line) for line in (f.parent / spec["cases"]).read_text().splitlines() if line.strip()]
        suites.append({"name": f.stem, "pack": pack.name, "kind": spec["suite"], "cases": cases,
                       "scorers": spec.get("scorers") or (["exact_action"] if spec["suite"] == "policy" else ["expected_tools"]),
                       "min_accuracy": float(spec.get("min_accuracy", 0.85))})
    return suites


def _tool_info(case_tool: dict) -> ToolInfo:
    name = case_tool["name"]
    pack, short = name.split(SEP, 1) if SEP in name else (None, name)
    return ToolInfo(**case_tool, pack=pack, short_name=short)


async def _policy_outcome(engine, case) -> dict:
    tool = _tool_info(case["tool"])
    call = {"id": case["id"], "name": tool.name, "args": case.get("arguments", {})}
    ctx = {"user_messages": case.get("user_messages", []), "recent_tool_output": case.get("recent_tool_output", "")}
    d = await engine.decide(call, tool, ctx)
    return {"action": d.action, "source": d.source, "reason": d.reason, "judgments": d.judgments}


async def _selection_outcome(bound, prompt, case) -> dict:
    msgs = [SystemMessage(prompt), *[HumanMessage(m["content"]) for m in case["messages"] if m.get("role") == "user"]]
    reply = await bound.ainvoke(msgs)
    return {"proposed": [c["name"] for c in (getattr(reply, "tool_calls", None) or [])]}


async def run_eval(config: AppConfig, *, pack: str | None = None, suite: str | None = None, runs: int = 1,
                   model=None, judge=_NO_JUDGE, concurrency: int = 8) -> EvalReport:
    packs = [resolve_pack(ref) for ref in config.packs]
    chosen = [p for p in packs if pack is None or p.name == pack]
    engine = PolicyEngine(policy_set(config, packs), make_judge(config.judge) if judge is _NO_JUDGE else judge)
    specs = [s for p in chosen for s in load_suites(p) if suite is None or s["name"] == suite or s["kind"] == suite]
    sem = asyncio.Semaphore(concurrency)
    results = []
    bound = prompt = None
    stack_cm = None
    if any(s["kind"] == "tool_selection" for s in specs):
        stack_cm = open_packs(packs)
        loaded = await stack_cm.__aenter__()
        bound = (model or make_model(config.model)).bind_tools(loaded.tools)
        prompt = compose_prompt(packs)
    try:
        for spec in specs:
            scorers = [load_scorer(n) for n in spec["scorers"]]
            if spec["kind"] == "policy":
                scorers += [load_scorer(g) for g in GATES if g not in spec["scorers"]]
            gate_names = [s.name for s in scorers if getattr(s, "gate", False)]
            quality = [s for s in scorers if not getattr(s, "gate", False)]
            res = SuiteResult(spec["name"], spec["pack"], spec["kind"], 0.0, runs, spec["min_accuracy"],
                              gates={g: 0 for g in gate_names})
            correct = 0
            for run in range(runs):
                async def one(case):
                    async with sem:
                        if spec["kind"] == "policy":
                            return case, await _policy_outcome(engine, case)
                        return case, await _selection_outcome(bound, prompt, case)
                for case, outcome in await asyncio.gather(*(one(c) for c in spec["cases"])):
                    scores = {s.name: s.score(case, outcome) for s in scorers}
                    ok = all(scores[s.name].passed for s in quality)
                    correct += ok
                    for g in gate_names:
                        res.gates[g] += not scores[g].passed
                    row = {"id": case["id"], "run": run + 1, "passed": ok, **outcome,
                           "expected": case.get("expected", case.get("expect_tools")),
                           "details": {k: v.detail for k, v in scores.items() if v.detail}}
                    res.rows.append(row)
                    if not ok or any(not scores[g].passed for g in gate_names):
                        res.failures.append(row)
            total = len(spec["cases"]) * runs
            res.accuracy = correct / total if total else 1.0
            for s in quality:
                if hasattr(s, "summarize"):
                    r = s.summarize(res.rows)
                    res.summaries[s.name] = {"passed": r.passed, "detail": r.detail}
            results.append(res)
    finally:
        if stack_cm is not None:
            await stack_cm.__aexit__(None, None, None)
    return EvalReport(results)
