import pytest

from fakes import FakeJudge
from pcagent.config import JudgeConfig
from pcagent.policy.engine import (
    PackPolicy, PolicyEngine, PolicyError, PolicySet, Thresholds, ToolInfo,
)
from pcagent.policy.jev import JevJudge, Judgments
from pcagent.policy.judges import make_judge, thresholds_from

GOOD = Judgments(0.97, 0.91, 0.02, 1.0, 1)


def tool(short, pack="billing", **kw):
    return ToolInfo(name=f"{pack}__{short}", short_name=short, pack=pack, **kw)


def test_pack_policy_defaults_when_missing(tmp_path):
    p = PackPolicy.load(tmp_path / "nope.yaml", "billing")
    assert (p.rules, p.default, p.read_only_default) == ({}, "evaluate", "allow")
    assert PackPolicy.load(None, "billing").default == "evaluate"


def test_pack_policy_rejects_thresholds_key(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("rules: {}\njev: {thresholds: {injected_max: 1.0}}\n")
    with pytest.raises(PolicyError, match="billing"):
        PackPolicy.load(f, "billing")


def test_pack_policy_rejects_bad_yaml_and_actions(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("rules: [unclosed\n")
    with pytest.raises(PolicyError, match="billing"):
        PackPolicy.load(f, "billing")
    f.write_text("rules: {get_balance: allw}\n")
    with pytest.raises(PolicyError, match="allw"):
        PackPolicy.load(f, "billing")


@pytest.mark.parametrize("action", ["allow", "evaluate", "denny"])
def test_global_rule_must_tighten(action):
    with pytest.raises(PolicyError, match="x__y"):
        PolicySet(global_rules={"x__y": action}, packs={}, thresholds=Thresholds())


def test_stricter_wins():
    packs = {"billing": PackPolicy(rules={"get_balance": "allow", "issue_refund": "deny", "x": "evaluate"})}
    ps = PolicySet({"billing__get_balance": "require_approval", "billing__issue_refund": "require_approval"},
                   packs, Thresholds())
    assert ps.rule_for(tool("get_balance"))[0] == "require_approval"
    assert ps.rule_for(tool("issue_refund"))[0] == "deny"
    assert ps.rule_for(tool("x"))[0] == "evaluate"


def test_global_glob_longest():
    ps = PolicySet({"*__bind_*": "require_approval", "everywire__bind_*": "deny"}, {}, Thresholds())
    action, why = ps.rule_for(tool("bind_policy_change", pack="everywire"))
    assert action == "deny" and "everywire__bind_*" in why


def test_pack_rules_match_short_names():
    ps = PolicySet({}, {"billing": PackPolicy(rules={"get_*": "allow"})}, Thresholds())
    action, why = ps.rule_for(tool("get_balance"))
    assert action == "allow" and why.startswith("billing: ")


async def test_destructive_still_needs_approval_after_merge():
    ps = PolicySet({}, {"billing": PackPolicy(rules={"issue_refund": "allow"})}, Thresholds())
    judge = FakeJudge(GOOD)
    d = await PolicyEngine(ps, judge).decide(
        {"id": "c", "name": "billing__issue_refund", "args": {}}, tool("issue_refund", destructive=True), {})
    assert d.action == "require_approval" and judge.calls == 0


async def test_pack_hints_reach_judge():
    seen = {}

    class Capture:
        async def judge(self, state):
            seen.update(state)
            return GOOD

    ps = PolicySet({}, {}, Thresholds(), hints={"billing": ["hint"]})
    d = await PolicyEngine(ps, Capture()).decide(
        {"id": "c", "name": "billing__x", "args": {}}, tool("x"), {})
    assert d.source == "jev"
    assert seen["proposed_call"]["pack_hints"] == ["hint"]
    assert seen["proposed_call"]["tool"]["pack"] == "billing"


async def test_no_judge_fails_closed():
    d = await PolicyEngine(PolicySet({}, {}, Thresholds()), None).decide(
        {"id": "c", "name": "billing__x", "args": {}}, tool("x"), {})
    assert (d.action, d.source) == ("require_approval", "fallback")


class MyJudge:
    async def judge(self, state):
        return GOOD


class NotAJudge:
    pass


def test_make_judge_variants():
    assert isinstance(make_judge(JudgeConfig(type="jev")), JevJudge)
    assert make_judge(JudgeConfig(type="none")) is None
    assert isinstance(make_judge(JudgeConfig(type="test_policyset:MyJudge")), MyJudge)
    with pytest.raises(PolicyError):
        make_judge(JudgeConfig(type="test_policyset:NotAJudge"))


def test_thresholds_from():
    assert thresholds_from(JudgeConfig()) == Thresholds()
    assert thresholds_from(JudgeConfig(thresholds={"intent_min": 0.8})).intent_min == 0.8
    with pytest.raises(PolicyError):
        thresholds_from(JudgeConfig(thresholds={"bogus": 1}))
