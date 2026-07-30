"""Decide layer: rule-based recommendations with explicit rationale.

Every recommendation must be able to answer "why this value" — the returned
objects always carry a human-readable ``reason``. Recommendations here are
limited to *resource* choices (parallelism, threads); scientific parameters are
never touched (design principle: science params are not auto-changed).
"""

from __future__ import annotations

# Import the submodules first so `from metaglens.decide import diagnose` yields
# the module, not a same-named function that would shadow it.
from . import advisor as advisor
from . import diagnose as diagnose
from . import gates as gates
from . import plan as plan
from . import planner as planner
from . import repair as repair

from .planner import Plan, recommend_parallel
from .plan import build_plan, render_plain
from .gates import GateResult, evaluate, load_rules, summarise
from .diagnose import Diagnosis, failed_stages
from .diagnose import diagnose as diagnose_failure

__all__ = [
    "advisor", "diagnose", "gates", "plan", "planner", "repair",
    "Plan", "recommend_parallel", "build_plan", "render_plain",
    "GateResult", "evaluate", "load_rules", "summarise",
    "Diagnosis", "diagnose_failure", "failed_stages",
]
