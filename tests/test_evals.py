import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from fakes import BAD, GOOD, FakeJudge, ScriptedChatModel, ai_call
from pcagent.cli import main
from pcagent.config import AppConfig, PackRef
from pcagent.evals import ScoreResult
from pcagent.evals.runner import run_eval

ROOT = Path(__file__).resolve().parents[1]


def write_pack(tmp_path, monkeypatch, policy_cases, ts_cases=(), scorers=("exact_action",), name="demo"):
    """A Python pack module with an evals/ dir, importable as `<name>_pack:make_pack`."""
    pkg = tmp_path / f"{name}_pack"
    (pkg / "evals").mkdir(parents=True)
    calls_file = tmp_path / "calls.json"
    (pkg / "__init__.py").write_text(f'''
from pathlib import Path
import json
from pcagent.packs import Pack

def make_pack(settings):
    p = Pack("{name}", evals="evals", base_dir=Path(__file__).parent)

    @p.tool()
    def act(x: str) -> str:
        """Do the thing."""
        Path({str(calls_file)!r}).write_text("called")
        return "done"

    @p.tool(read_only=True)
    def look(x: str) -> str:
        """Look something up."""
        return "seen"
    return p
''')
    (pkg / "evals" / "policy.yaml").write_text(
        f"suite: policy\ncases: policy_cases.jsonl\nscorers: {json.dumps(list(scorers))}\n")
    (pkg / "evals" / "policy_cases.jsonl").write_text("\n".join(json.dumps(c) for c in policy_cases) + "\n")
    if ts_cases:
        (pkg / "evals" / "tool_selection.yaml").write_text(
            "suite: tool_selection\ncases: ts.jsonl\nscorers: [expected_tools]\nmin_accuracy: 0.5\n")
        (pkg / "evals" / "ts.jsonl").write_text("\n".join(json.dumps(c) for c in ts_cases) + "\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, f"{name}_pack", raising=False)
    return AppConfig(packs=[PackRef(use=f"{name}_pack:make_pack")]), calls_file


def pcase(cid, short, expected, pack="demo"):
    return {"id": cid, "note": "", "tool": {"name": f"{pack}__{short}", "description": "d",
            "read_only": short == "look", "destructive": False},
            "arguments": {"x": "1"}, "user_messages": ["do it"], "recent_tool_output": "", "expected": expected}


async def test_policy_suite_gates_always_on(tmp_path, monkeypatch):
    cfg, _ = write_pack(tmp_path, monkeypatch, [pcase("a-1", "look", "allow"), pcase("a-2", "act", "allow")])
    report = await run_eval(cfg, judge=FakeJudge(GOOD))
    [s] = report.suites
    assert set(s.gates) == {"missed_escalation", "no_fallback"}
    assert s.accuracy == 1.0 and report.passed


async def test_missed_escalation_fails_report(tmp_path, monkeypatch):
    cfg, _ = write_pack(tmp_path, monkeypatch, [pcase("a-1", "act", "require_approval")] + [
        pcase(f"b-{i}", "look", "allow") for i in range(9)])
    report = await run_eval(cfg, judge=FakeJudge(GOOD))  # Jev passes the call: a miss
    assert report.suites[0].gates["missed_escalation"] == 1
    assert report.passed is False


async def test_fallback_fails_report(tmp_path, monkeypatch):
    cfg, _ = write_pack(tmp_path, monkeypatch, [pcase("a-1", "act", "require_approval")])
    report = await run_eval(cfg, judge=FakeJudge(TimeoutError()))
    assert report.suites[0].gates["no_fallback"] == 1 and report.passed is False


async def test_runs_average_and_any_miss_fails(tmp_path, monkeypatch):
    cfg, _ = write_pack(tmp_path, monkeypatch, [pcase("a-1", "act", "require_approval")])

    class Flaky:
        n = 0

        async def judge(self, state):
            Flaky.n += 1
            return BAD if Flaky.n == 1 else GOOD  # run 1 escalates, run 2 misses

    report = await run_eval(cfg, judge=Flaky(), runs=2)
    s = report.suites[0]
    assert s.runs == 2 and s.accuracy == 0.5
    assert s.gates["missed_escalation"] == 1 and report.passed is False


async def test_tool_selection_never_invokes_tools(tmp_path, monkeypatch):
    ts = [{"id": "t-1", "messages": [{"role": "user", "content": "do it"}],
           "expect_tools": ["demo__act"], "forbid_tools": ["demo__look"]}]
    cfg, calls_file = write_pack(tmp_path, monkeypatch, [pcase("a-1", "look", "allow")], ts)
    model = ScriptedChatModel(responses=[ai_call(("c1", "demo__act", {"x": "1"}))])
    report = await run_eval(cfg, suite="tool_selection", model=model)
    [s] = report.suites
    assert s.kind == "tool_selection" and s.accuracy == 1.0
    assert s.rows[0]["proposed"] == ["demo__act"]
    assert not calls_file.exists()  # proposed, never run


async def test_expected_and_forbidden_tools(tmp_path, monkeypatch):
    ts = [{"id": "t-1", "messages": [{"role": "user", "content": "x"}], "expect_tools": ["demo__act"], "forbid_tools": []},
          {"id": "t-2", "messages": [{"role": "user", "content": "y"}], "expect_tools": [], "forbid_tools": ["demo__act"]}]
    cfg, _ = write_pack(tmp_path, monkeypatch, [pcase("a-1", "look", "allow")], ts)
    model = ScriptedChatModel(responses=[AIMessage("no tools"), ai_call(("c", "demo__act", {"x": "1"}))])
    s = (await run_eval(cfg, suite="tool_selection", model=model)).suites[0]
    assert [r["passed"] for r in s.rows] == [False, False]


class AlwaysFail:
    name = "always_fail"

    def score(self, case, outcome):
        return ScoreResult(False, "nope")


async def test_custom_scorer_by_import_path(tmp_path, monkeypatch):
    cfg, _ = write_pack(tmp_path, monkeypatch, [pcase("a-1", "look", "allow")],
                        scorers=("exact_action", "test_evals:AlwaysFail"))
    s = (await run_eval(cfg, judge=FakeJudge(GOOD))).suites[0]
    assert s.accuracy == 0.0 and "nope" in json.dumps(s.rows)


def test_cli_eval_exit_code(tmp_path, monkeypatch):
    write_pack(tmp_path, monkeypatch, [pcase("a-1", "act", "require_approval")])
    cfg_file = tmp_path / "pcagent.yaml"
    cfg_file.write_text("packs:\n  - use: demo_pack:make_pack\njudge:\n  type: none\n")
    out = tmp_path / "reports"
    # judge none → evaluate calls go to fallback → the no_fallback gate fails
    with pytest.raises(SystemExit) as e:
        main(["eval", "--config", str(cfg_file), "--out", str(out)])
    assert e.value.code == 1
    assert list(out.glob("eval-*.md")) and list(out.glob("eval-*.json"))


def test_everywire_cases_use_qualified_names():
    for f in (ROOT / "src/pcagent/packs/everywire/evals").glob("*.jsonl"):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            names = [c["tool"]["name"]] if "tool" in c else c["expect_tools"] + c["forbid_tools"]
            assert all(n.startswith("everywire__") for n in names), (f.name, c["id"])
