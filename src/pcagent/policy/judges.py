"""Judge selection from config: jev (default), none (rules only), or a class by import path."""

from __future__ import annotations

import dataclasses
import importlib
import inspect

from pcagent.config import JudgeConfig
from pcagent.policy.engine import PolicyError, Thresholds
from pcagent.policy.jev import Judge, JevJudge


def make_judge(cfg: JudgeConfig) -> Judge | None:
    if cfg.type == "jev":
        return JevJudge()
    if cfg.type == "none":
        return None  # every `evaluate` call then goes to a person
    if ":" not in cfg.type:
        raise PolicyError(f"judge {cfg.type!r}: use jev, none, or 'module:ClassName'")
    module, attr = cfg.type.split(":", 1)
    try:
        judge = getattr(importlib.import_module(module), attr)()
    except Exception as e:
        raise PolicyError(f"judge {cfg.type!r} could not be created: {e}") from e
    if not inspect.iscoroutinefunction(getattr(judge, "judge", None)):
        raise PolicyError(f"judge {cfg.type!r} has no async judge(state) method")
    return judge


def thresholds_from(cfg: JudgeConfig) -> Thresholds:
    known = {f.name for f in dataclasses.fields(Thresholds)}
    unknown = set(cfg.thresholds) - known
    if unknown:
        raise PolicyError(f"unknown judge thresholds {sorted(unknown)}; known: {sorted(known)}")
    return Thresholds(**cfg.thresholds)
