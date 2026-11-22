"""Eval suites shipped by packs, scored by pluggable scorers, gated for safety.

A pack's `evals/` directory holds suite files (`*.yaml`):

    suite: policy | tool_selection
    cases: <jsonl file next to it>
    scorers: [exact_action, "module:Class", ...]
    min_accuracy: 0.85

Policy suites always add the gates `missed_escalation` and `no_fallback`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ScoreResult:
    passed: bool
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Scorer(Protocol):
    name: str

    def score(self, case: dict, outcome: dict) -> ScoreResult: ...


@dataclass
class SuiteResult:
    name: str
    pack: str
    kind: str
    accuracy: float
    runs: int
    min_accuracy: float
    gates: dict[str, int] = field(default_factory=dict)
    summaries: dict[str, dict] = field(default_factory=dict)
    failures: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (self.accuracy >= self.min_accuracy and not any(self.gates.values())
                and all(s["passed"] for s in self.summaries.values()))


@dataclass
class EvalReport:
    suites: list[SuiteResult]

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.suites)


__all__ = ["ScoreResult", "Scorer", "SuiteResult", "EvalReport"]
