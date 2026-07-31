"""The "attack it yourself" panel — backed by the real security check.

Every result here comes from calling :func:`metaglens.decide.repair.check_allowed`
for real; nothing is faked. The canonical cases are the ones the reviewer used
to probe the boundary (change a scientific parameter, smuggle an illegal field
alongside a legal one, invoke a non-whitelisted operation), plus one legal
operation that is correctly allowed.

For the live server this runs on demand — a judge can even submit their own
field name and watch it be refused, because ``check_allowed`` only inspects and
raises, it never executes anything. For the static export the canonical results
are baked in, so the page shows the same real messages with no backend.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..decide import repair

# Preset probes shown as buttons. Each is a real RepairPlan spec.
CANONICAL: List[Dict[str, Any]] = [
    {"key": "sci_min_contig",
     "label_en": "Make it change min_contig_len",
     "label_zh": "试着让它改 min_contig_len",
     "op": "reduce_parallel", "stage": "04_binning",
     "changes": {"min_contig_len": 200}},
    {"key": "sci_completeness",
     "label_en": "Make it relax completeness_min",
     "label_zh": "试着放宽 completeness_min",
     "op": "reduce_parallel", "stage": "05_checkm",
     "changes": {"completeness_min": 10}},
    {"key": "smuggle",
     "label_en": "Smuggle an illegal field (parallel_jobs + min_length)",
     "label_zh": "夹带非法字段(parallel_jobs + min_length)",
     "op": "reduce_parallel", "stage": "02_assembly",
     "changes": {"parallel_jobs": 2, "min_length": 30}},
    {"key": "bad_op",
     "label_en": "Use a non-whitelisted operation (delete_outputs)",
     "label_zh": "用非白名单操作 delete_outputs",
     "op": "delete_outputs", "stage": "02_assembly", "changes": {}},
    {"key": "legal",
     "label_en": "Legal operation: lower concurrency",
     "label_zh": "合法操作:降并发",
     "op": "reduce_parallel", "stage": "02_assembly",
     "changes": {"parallel_jobs": 4, "threads_per_job": 8}},
]

# Bounds for judge-supplied probes on the live endpoint.
_MAX_CHANGES = 8


def evaluate(op: str, stage: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Run the real repair boundary check and report exactly what it said.

    Safe by construction: ``check_allowed`` inspects the plan and raises; it
    never runs anything. The worst an input can do is get refused.
    """
    op = str(op or "")[:64]
    stage = str(stage or "")[:64]
    safe_changes: Dict[str, Any] = {}
    for i, (field, value) in enumerate(dict(changes or {}).items()):
        if i >= _MAX_CHANGES:
            break
        # Coerce to a scalar; the check only cares about the field name.
        if isinstance(value, (str, int, float, bool)):
            safe_changes[str(field)[:64]] = value
        else:
            safe_changes[str(field)[:64]] = str(value)[:64]
    plan = repair.RepairPlan(op=op, stage=stage, changes=safe_changes)
    try:
        repair.check_allowed(plan)
        return {"op": op, "changes": safe_changes, "refused": False,
                "message": "Allowed — this is a resource-only change the repair "
                           "layer may apply.",
                "verdict": "ALLOWED"}
    except repair.RepairRefused as exc:
        return {"op": op, "changes": safe_changes, "refused": True,
                "message": str(exc), "verdict": "REFUSED"}


def run_canonical() -> List[Dict[str, Any]]:
    """Evaluate every preset probe for real. Used live and baked into export."""
    out = []
    for case in CANONICAL:
        result = evaluate(case["op"], case["stage"], case["changes"])
        out.append({
            "key": case["key"],
            "label_en": case["label_en"],
            "label_zh": case["label_zh"],
            "op": case["op"],
            "changes": case["changes"],
            "refused": result["refused"],
            "verdict": result["verdict"],
            "message": result["message"],
        })
    return out
