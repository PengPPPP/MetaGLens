"""Decide layer: rule-based recommendations with explicit rationale.

Every recommendation must be able to answer "why this value" — the returned
objects always carry a human-readable ``reason``. Recommendations here are
limited to *resource* choices (parallelism, threads); scientific parameters are
never touched (design principle: science params are not auto-changed).
"""

from __future__ import annotations

from .planner import Plan, recommend_parallel
from .plan import build_plan, render_plain
from .gates import GateResult, evaluate, load_rules, summarise

__all__ = [
    "Plan", "recommend_parallel", "build_plan", "render_plain",
    "GateResult", "evaluate", "load_rules", "summarise",
]
