"""Live eval of the Everywire pack's policy suite (real Jev calls; needs TYPESAFE_API_KEY).

Run: uv run pytest -m live tests/test_policy_eval.py   (or: uv run pcagent eval --config pcagent.local.yaml)
"""

import os
from pathlib import Path

import pytest

from pcagent.config import load_config
from pcagent.evals.runner import run_eval

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="needs TYPESAFE_API_KEY")
async def test_everywire_policy_suite(monkeypatch):
    monkeypatch.chdir(ROOT)
    report = await run_eval(load_config(ROOT / "pcagent.local.yaml"), pack="everywire", suite="policy")
    [suite] = report.suites
    assert suite.gates == {"missed_escalation": 0, "no_fallback": 0}
    assert suite.passed, suite.failures
