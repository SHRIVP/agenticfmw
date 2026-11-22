"""Policy engine: decide allow / require_approval / deny for each proposed tool call.

Two layers, in order:

1. Rules (code, from policy.yaml). Exact and glob tool-name rules, plus MCP tool
   annotations. A `deny` or `allow` rule is final and Jev is not asked. Destructive
   tools are never auto-allowed.
2. Jev judgments, for tools whose rule is `evaluate`. Jev can only keep a call
   automatic or escalate it to a person. It never makes a call more permissive than
   the rule, and if Jev is unavailable the call goes to a person (fail closed).
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .jev import Judge, Judgments

log = logging.getLogger(__name__)

Action = Literal["allow", "require_approval", "deny"]
RuleAction = Literal["allow", "require_approval", "deny", "evaluate"]
DEFAULT_POLICY = Path(__file__).with_name("policy.yaml")


@dataclass
class ToolInfo:
    name: str
    description: str = ""
    read_only: bool = False
    destructive: bool = False


@dataclass
class Thresholds:
    intent_min: float = 0.7
    grounded_min: float = 0.6
    injected_max: float = 0.3
    # Highest expected impact level (see jev.IMPACT_LEVELS) that may run unattended.
    impact_max: float = 1.5


@dataclass
class Policy:
    rules: dict[str, RuleAction] = field(default_factory=dict)
    default: RuleAction = "evaluate"
    read_only_default: RuleAction = "allow"
    thresholds: Thresholds = field(default_factory=Thresholds)
    use_jev: bool = True

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Policy":
        data = yaml.safe_load(Path(path or DEFAULT_POLICY).read_text()) or {}
        return cls(
            rules=data.get("rules", {}),
            default=data.get("default", "evaluate"),
            read_only_default=data.get("read_only_default", "allow"),
            thresholds=Thresholds(**data.get("jev", {}).get("thresholds", {})),
            use_jev=data.get("jev", {}).get("enabled", True),
        )

    def rule_for(self, tool: ToolInfo) -> tuple[RuleAction, str]:
        if tool.name in self.rules:
            return self.rules[tool.name], f"rule '{tool.name}'"
        # Longest glob wins, so `search_*` can be overridden by `search_claims_*`.
        for pattern in sorted((p for p in self.rules if any(c in p for c in "*?[")), key=len, reverse=True):
            if fnmatch.fnmatchcase(tool.name, pattern):
                return self.rules[pattern], f"rule '{pattern}'"
        if tool.read_only and not tool.destructive:
            return self.read_only_default, "read-only tool"
        return self.default, "default rule"


@dataclass
class Decision:
    tool_call_id: str
    tool: str
    arguments: dict[str, Any]
    action: Action
    reason: str
    source: Literal["rule", "jev", "fallback", "human"]
    judgments: dict[str, float] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def combine(j: Judgments, t: Thresholds) -> tuple[Action, str]:
    """Explicit policy over raw judgments. Every failed check is listed."""
    problems = []
    if j.injected > t.injected_max:
        problems.append(f"may follow instructions from tool output (injected={j.injected:.2f})")
    if j.intent < t.intent_min:
        problems.append(f"user may not have asked for this (intent={j.intent:.2f})")
    if j.grounded < t.grounded_min:
        problems.append(f"arguments may be invented (grounded={j.grounded:.2f})")
    if j.impact > t.impact_max:
        problems.append(f"high impact (level {j.impact:.1f})")
    if problems:
        return "require_approval", "; ".join(problems)
    return "allow", f"Jev checks passed (intent={j.intent:.2f}, grounded={j.grounded:.2f}, impact={j.impact:.1f})"


class PolicyEngine:
    def __init__(self, policy: Policy, judge: Judge | None):
        self.policy = policy
        self.judge = judge

    async def decide(self, call: dict[str, Any], tool: ToolInfo, context: dict[str, Any]) -> Decision:
        base = dict(tool_call_id=call["id"], tool=call["name"], arguments=call.get("args", {}))
        rule, why = self.policy.rule_for(tool)

        if rule == "deny":
            return Decision(**base, action="deny", reason=f"denied by {why}", source="rule")
        if tool.destructive:  # never auto-allowed, and Jev is not asked
            rule, why = "require_approval", f"destructive tool ({why})"
        if rule in ("allow", "require_approval"):
            return Decision(**base, action=rule, reason=why, source="rule")

        # rule == "evaluate"
        if not (self.policy.use_jev and self.judge):
            return Decision(**base, action="require_approval", reason="no judge configured", source="fallback")
        state = {
            "user_messages": context.get("user_messages", []),
            "recent_tool_output": context.get("recent_tool_output", ""),
            "proposed_call": {
                "tool": {"name": tool.name, "description": tool.description, "read_only": tool.read_only},
                "arguments": base["arguments"],
            },
        }
        try:
            j = (await self.judge.judge(state)).check()  # NaN, None or out-of-range values raise
            action, reason = combine(j, self.policy.thresholds)
        except Exception as exc:  # missing key, network, rate limit, bad values
            log.warning("Jev unavailable for %s: %r", tool.name, exc)
            return Decision(**base, action="require_approval", reason=f"Jev unavailable ({exc.__class__.__name__})", source="fallback")
        return Decision(**base, action=action, reason=reason, source="jev", judgments=j.as_dict())
