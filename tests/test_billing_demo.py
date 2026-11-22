from importlib.metadata import entry_points

import pytest
from langchain_core.tools import ToolException

from pcagent.packs.loader import open_packs
from pcagent.policy.engine import PackPolicy, PolicySet, Thresholds


def test_entry_point_registered():
    assert "billing_demo" in {ep.name for ep in entry_points(group="pcagent.packs")}


def make():
    from billing_demo import make_pack
    return make_pack({})


async def test_tools_and_flags():
    async with open_packs([make()]) as loaded:
        assert loaded.info["billing__issue_refund"].destructive is True
        assert loaded.info["billing__get_balance"].read_only is True
        bal = next(t for t in loaded.tools if t.name == "billing__get_balance")
        assert "120.00" in await bal.ainvoke({"account_number": "ACC-10001"})


async def test_refund_validation():
    async with open_packs([make()]) as loaded:
        refund = next(t for t in loaded.tools if t.name == "billing__issue_refund")
        with pytest.raises(ToolException):
            await refund.ainvoke({"account_number": "ACC-10001", "amount": -5, "reason": "x"})
        with pytest.raises(ToolException):
            await refund.ainvoke({"account_number": "NOPE", "amount": 5, "reason": "x"})


async def test_policy_refund_needs_approval():
    p = make()
    async with open_packs([p]) as loaded:
        ps = PolicySet({}, {p.name: PackPolicy.load(p.rules_path, p.name)}, Thresholds())
        assert ps.rule_for(loaded.info["billing__issue_refund"])[0] == "require_approval"
        assert ps.rule_for(loaded.info["billing__get_balance"])[0] == "allow"


def test_seed_from_settings():
    from billing_demo import make_pack
    p = make_pack({"seed": {"ACC-1": 5.0}})
    assert p.python_tools["get_balance"].fn("ACC-1") == "ACC-1 balance: 5.00"
