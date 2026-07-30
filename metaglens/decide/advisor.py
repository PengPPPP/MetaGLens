"""Parameter recommendations with an explicit rationale.

Two boundaries define this module:

* **Every recommendation explains itself.** A value without a reason is an
  unexplained order; the reason is part of the output, not a nicety.
* **Scientific parameters are advisory only.** Rules with ``scope: science`` may
  warn but carry no ``advise`` payload, so ``--apply`` can never rewrite a
  completeness cut-off or an ANI threshold. Only resource knobs are applicable.

Rules live in ``rules/advice.yaml``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_RULES_FILE = "advice.yaml"

# Same coarse per-job figure the planner uses; kept in one place conceptually by
# importing it rather than re-declaring a second, divergent constant.
from .planner import PEAK_MEM_GB_PER_JOB

# Config fields that may be changed automatically. Anything absent from this set
# is advisory only, whatever a rule claims.
APPLICABLE_FIELDS = frozenset({
    "assembler", "align_tool", "parallel_jobs", "threads_per_job",
    "total_threads", "memory",
})

# Fields that must never be auto-changed, even if a rule mistakenly lists them.
SCIENTIFIC_FIELDS = frozenset({
    "completeness_min", "contamination_max", "ani_threshold", "min_contig_len",
    "min_contig", "min_length", "quality_threshold", "kmer_list",
    "megahit_preset", "align_mode", "kraken2_confidence", "tax_level",
    "prokka_kingdom", "bracken_read_length", "top_levels",
})


@dataclass
class Advice:
    rule_id: str
    severity: str                 # info | warn
    scope: str                    # resource | science
    field: str                    # "" when the rule only warns
    current: Any
    suggested: Any
    reason: str
    applicable: bool              # may --apply change it?
    citation: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.rule_id, "severity": self.severity, "scope": self.scope,
            "field": self.field, "current": self.current,
            "suggested": self.suggested, "reason": self.reason,
            "applicable": self.applicable, "citation": self.citation,
        }


def load_rules(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    import yaml
    target = Path(path) if path else Path(__file__).with_name("rules") / _RULES_FILE
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or []
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _context(cfg, cores: int, ram_gb: float, n_samples: int) -> Dict[str, Any]:
    jobs = cfg.parallel_jobs or max(1, min(max(1, n_samples), cores))
    threads = cfg.threads_per_job or max(1, cores // max(1, jobs))
    return {
        "cores": cores,
        "ram_gb": ram_gb,
        "n_samples": n_samples,
        "parallel_jobs": jobs,
        "threads_per_job": threads,
        "assembler": cfg.assembler,
        "peak_mem_estimate": jobs * PEAK_MEM_GB_PER_JOB,
        "utilisation": (jobs * threads) / cores if cores else 1.0,
    }


def _condition_holds(key: str, expected: Any, cfg, ctx: Dict[str, Any]) -> bool:
    """Evaluate one `when` clause. Unknown keys are treated as not matching."""
    if key.endswith("_below"):
        name = key[: -len("_below")]
        value = ctx.get(name, getattr(cfg, name, None))
        return value is not None and float(value) < float(expected)
    if key.endswith("_above"):
        name = key[: -len("_above")]
        value = ctx.get(name, getattr(cfg, name, None))
        return value is not None and float(value) > float(expected)
    if key.endswith("_at_least"):
        name = key[: -len("_at_least")]
        value = ctx.get(name, getattr(cfg, name, None))
        return value is not None and float(value) >= float(expected)
    if key == "peak_memory_exceeds_ram":
        exceeds = bool(ctx["ram_gb"]) and ctx["peak_mem_estimate"] > ctx["ram_gb"]
        return exceeds is bool(expected)
    if key == "read_profiling_kraken2":
        uses = (cfg.taxonomy_tool == "kraken2"
                or cfg.contig_taxonomy == "kraken2")
        return uses is bool(expected)
    # Plain equality against a config attribute.
    actual = ctx.get(key, getattr(cfg, key, None))
    return actual == expected


_ALLOWED_FUNCS = {"max": max, "min": min, "int": int, "round": round,
                  "ceil": math.ceil, "floor": math.floor}


def _eval_node(node, names: Dict[str, Any]):
    """Evaluate one AST node from the tiny expression language."""
    import ast

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, names)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("only numeric literals are allowed")
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise ValueError(f"unknown name '{node.id}'")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_node(node.operand, names)
        return +value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, names)
        right = _eval_node(node.right, names)
        for op_type, func in ((ast.Add, lambda a, b: a + b),
                              (ast.Sub, lambda a, b: a - b),
                              (ast.Mult, lambda a, b: a * b),
                              (ast.Div, lambda a, b: a / b),
                              (ast.FloorDiv, lambda a, b: a // b),
                              (ast.Mod, lambda a, b: a % b)):
            if isinstance(node.op, op_type):
                return func(left, right)
        raise ValueError("unsupported operator")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError("only max/min/int/round/ceil/floor may be called")
        if node.keywords:
            raise ValueError("keyword arguments are not supported")
        args = [_eval_node(a, names) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)
    raise ValueError("unsupported expression")


def _resolve(expression: Any, ctx: Dict[str, Any]) -> Any:
    """Evaluate a small arithmetic suggestion like 'max(1, cores // 4)'.

    Rules are user-editable YAML, so this is an allowlisted AST walk rather than
    eval(): a rules file must never be able to execute arbitrary code.
    """
    if not isinstance(expression, str):
        return expression
    import ast

    names = {k: v for k, v in ctx.items() if isinstance(v, (int, float))}
    try:
        tree = ast.parse(expression, mode="eval")
        return _eval_node(tree, names)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError):
        return expression


def recommend(cfg, cores: Optional[int] = None, ram_gb: Optional[float] = None,
              n_samples: Optional[int] = None,
              rules: Optional[List[Dict[str, Any]]] = None) -> List[Advice]:
    """Evaluate advice rules against ``cfg`` and the host."""
    if cores is None or ram_gb is None:
        from ..sense import hardware
        info = hardware.probe(cfg.work_dir or ".")
        cores = cores if cores is not None else info.cores
        ram_gb = ram_gb if ram_gb is not None else info.ram_gb
    if n_samples is None:
        try:
            from .. import samples as samples_mod
            n_samples = len(samples_mod.discover(cfg.raw_data_dir).samples)
        except Exception:
            n_samples = 1

    ctx = _context(cfg, cores, ram_gb, n_samples)
    out: List[Advice] = []

    for rule in (rules if rules is not None else load_rules()):
        conditions = rule.get("when") or {}
        if not conditions:
            continue
        if not all(_condition_holds(k, v, cfg, ctx)
                   for k, v in conditions.items()):
            continue

        advise = rule.get("advise") or {}
        scope = str(rule.get("scope", "resource"))
        field = next(iter(advise), "")
        suggested = _resolve(advise.get(field), ctx) if field else None
        current = getattr(cfg, field, None) if field else None

        # The guard that matters: a scientific field is never applicable, no
        # matter what the rule says.
        applicable = bool(field) and field in APPLICABLE_FIELDS \
            and field not in SCIENTIFIC_FIELDS and scope != "science" \
            and suggested is not None and suggested != current

        reason = " ".join(str(rule.get("reason", "")).split())
        fmt = dict(ctx)
        fmt["value"] = current if current is not None else ""
        try:
            reason = reason.format(**{k: (f"{v:.0f}" if isinstance(v, float) else v)
                                      for k, v in fmt.items()})
        except (KeyError, IndexError, ValueError):
            pass

        out.append(Advice(
            rule_id=str(rule.get("id", "")),
            severity=str(rule.get("severity", "info")),
            scope=scope, field=field, current=current, suggested=suggested,
            reason=reason, applicable=applicable,
            citation=str(rule.get("citation", "")),
        ))
    return out


def applicable_changes(advice: List[Advice]) -> Dict[str, Any]:
    """The subset ``--apply`` may write: resource fields only."""
    changes: Dict[str, Any] = {}
    for item in advice:
        if item.applicable and item.field:
            changes[item.field] = item.suggested
    return changes


def diff_lines(cfg, changes: Dict[str, Any]) -> List[str]:
    """A YAML-ish diff shown before anything is written."""
    lines = []
    for field, value in sorted(changes.items()):
        lines.append(f"- {field}: {getattr(cfg, field, None)}")
        lines.append(f"+ {field}: {value}")
    return lines
