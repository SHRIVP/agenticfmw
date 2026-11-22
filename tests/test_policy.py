from types import SimpleNamespace as NS

import pytest

from pcagent.policy.engine import Policy, PolicyEngine, Thresholds, ToolInfo, combine
from pcagent.policy.jev import Judgments, to_judgments

GOOD = Judgments(0.97, 0.91, 0.02, 1.0, 1)


class FakeJudge:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def judge(self, state):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def call(name):
    return {"id": "c1", "name": name, "args": {"x": 1}}


def test_exact_rule_beats_glob():
    p = Policy(rules={"search_*": "allow", "search_policies": "deny"})
    assert p.rule_for(ToolInfo("search_policies")) == ("deny", "rule 'search_policies'")
    shipped = Policy.load()
    assert shipped.rule_for(ToolInfo("search_policies")) == ("allow", "rule 'search_*'")
    assert shipped.rule_for(ToolInfo("bind_policy_change"))[0] == "deny"


def test_longest_glob_wins():
    p = Policy(rules={"search_*": "allow", "search_claims_*": "deny"})
    assert p.rule_for(ToolInfo("search_claims_x"))[0] == "deny"
    assert p.rule_for(ToolInfo("search_other"))[0] == "allow"


def test_read_only_default_and_unknown_default():
    p = Policy()
    assert p.rule_for(ToolInfo("foo", read_only=True))[0] == "allow"
    assert p.rule_for(ToolInfo("foo", read_only=False))[0] == "evaluate"


async def test_destructive_never_auto_allowed():
    engine = PolicyEngine(Policy(rules={"drop": "allow"}), None)
    d = await engine.decide(call("drop"), ToolInfo("drop", destructive=True), {})
    assert d.action == "require_approval"
    assert "destructive tool" in d.reason


async def test_destructive_evaluate_not_sent_to_jev():
    judge = FakeJudge(GOOD)
    d = await PolicyEngine(Policy(), judge).decide(call("drop"), ToolInfo("drop", destructive=True), {})
    assert d.action == "require_approval"
    assert d.source == "rule"
    assert "destructive tool" in d.reason
    assert judge.calls == 0


async def test_deny_skips_judge():
    judge = FakeJudge(GOOD)
    d = await PolicyEngine(Policy.load(), judge).decide(call("bind_policy_change"), ToolInfo("bind_policy_change"), {})
    assert d.action == "deny" and d.source == "rule"
    assert judge.calls == 0


async def test_jev_pass_allows():
    d = await PolicyEngine(Policy.load(), FakeJudge(GOOD)).decide(call("add_vehicle"), ToolInfo("add_vehicle"), {})
    assert d.action == "allow" and d.source == "jev"
    assert d.judgments == GOOD.as_dict()
    assert d.as_dict()["tool_call_id"] == "c1"


@pytest.mark.parametrize(
    "bad,word",
    [
        (Judgments(0.5, 0.91, 0.02, 1.0, 1), "intent"),
        (Judgments(0.97, 0.01, 0.02, 1.0, 1), "grounded"),
        (Judgments(0.97, 0.91, 0.97, 1.0, 1), "injected"),
        (Judgments(0.97, 0.91, 0.02, 3.0, 3), "impact"),
    ],
)
async def test_jev_each_failure_escalates(bad, word):
    d = await PolicyEngine(Policy.load(), FakeJudge(bad)).decide(call("add_vehicle"), ToolInfo("add_vehicle"), {})
    assert d.action == "require_approval"
    assert word in d.reason


async def test_judge_error_fails_closed():
    d = await PolicyEngine(Policy.load(), FakeJudge(error=TimeoutError())).decide(
        call("add_vehicle"), ToolInfo("add_vehicle"), {}
    )
    assert d.action == "require_approval" and d.source == "fallback"
    assert "TimeoutError" in d.reason


async def test_no_judge_fails_closed():
    d = await PolicyEngine(Policy.load(), None).decide(call("add_vehicle"), ToolInfo("add_vehicle"), {})
    assert d.action == "require_approval"


def test_combine_lists_every_failure():
    action, reason = combine(Judgments(0.1, 0.1, 0.9, 3.0, 3), Thresholds())
    assert action == "require_approval"
    assert all(w in reason for w in ("intent", "grounded", "injected", "impact"))


def test_to_judgments_maps_response():
    resp = NS(
        nouls={"intent": NS(noul=0.9), "grounded": NS(noul=0.8), "injected": NS(noul=0.1)},
        scores={"impact": NS(score=2.99, probabilities={0: 0, 1: 0, 2: 0.01, 3: 0.99})},
    )
    j = to_judgments(resp)
    assert (j.intent, j.grounded, j.injected) == (0.9, 0.8, 0.1)
    assert j.impact == 2.99 and j.impact_level == 3


def test_shipped_policy_yaml_loads():
    p = Policy.load()
    assert p.rules["bind_policy_change"] == "deny"
    assert p.thresholds.impact_max == 1.5
