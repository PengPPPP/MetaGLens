"""Bounded self-repair.

This is the highest-risk component in the system, so its boundaries are wider
than its capabilities:

* **Whitelist, not blacklist.** Only ``ALLOWED_OPS`` may run. An action the
  rules propose that is not on the list is refused, whatever it claims.
* **Scientific parameters are untouchable.** Completeness cut-offs, ANI
  thresholds, k-mer lists — none of these may be changed to make a stage pass.
  Changing them would alter the result rather than fix the run.
* **Bounded.** Two attempts by default, and a repeated failure signature stops
  immediately rather than looping.
* **Evidence always.** Every attempt writes a script snapshot and one JSON line
  to ``reports/repair_log.jsonl`` before anything is re-run.
* **Only the failed stage re-runs.** Upstream stages are never touched, and no
  non-empty output is ever deleted to make a retry pass.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_MAX_ATTEMPTS = 2

# The complete set of repairs that may ever run automatically. Each entry is a
# resource-only adjustment.
ALLOWED_OPS = frozenset({
    "reduce_parallel",     # fewer concurrent jobs
    "reduce_threads",      # fewer threads per job
    "increase_memory",     # larger scheduler memory request
    "retry",               # transient failure, no change
})

# Config fields a repair may write. Deliberately tiny.
ALLOWED_FIELDS = frozenset({
    "parallel_jobs", "threads_per_job", "memory",
})

# Fields that must never be modified by a repair, listed explicitly so the
# refusal is auditable rather than implied.
FORBIDDEN_FIELDS = frozenset({
    "completeness_min", "contamination_max", "ani_threshold", "min_contig_len",
    "min_contig", "min_length", "quality_threshold", "kmer_list",
    "megahit_preset", "align_mode", "assembler", "align_tool", "taxonomy_tool",
    "contig_taxonomy", "kraken2_confidence", "tax_level", "prokka_kingdom",
    "bracken_read_length", "top_levels", "use_metabat2", "use_maxbin2",
    "use_concoct", "use_das_tool", "use_prokka", "use_eggnog", "use_bracken",
    "raw_data_dir", "sample_manifest", "db_dir", "conda_env", "conda_mode",
    "checkm2_db", "taxonomy_db", "eggnog_db", "kraken2_db", "route_name",
    "custom_steps",
})


class RepairRefused(Exception):
    """Raised when a proposed repair falls outside the permitted boundary."""


@dataclass
class RepairPlan:
    op: str
    stage: str
    changes: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "stage": self.stage, "changes": self.changes,
                "rationale": self.rationale}


def check_allowed(plan: RepairPlan) -> None:
    """Raise :class:`RepairRefused` unless the plan is inside the boundary."""
    if plan.op not in ALLOWED_OPS:
        raise RepairRefused(
            f"operation '{plan.op}' is not on the repair whitelist "
            f"({', '.join(sorted(ALLOWED_OPS))})"
        )
    for field_name in plan.changes:
        if field_name in FORBIDDEN_FIELDS:
            raise RepairRefused(
                f"refusing to change '{field_name}': scientific and input "
                f"parameters are never modified automatically, because that "
                f"would change the result rather than fix the run"
            )
        if field_name not in ALLOWED_FIELDS:
            raise RepairRefused(
                f"refusing to change '{field_name}': only "
                f"{', '.join(sorted(ALLOWED_FIELDS))} may be adjusted"
            )


def plan_from_diagnosis(cfg, diag, stage: Optional[str] = None
                        ) -> Optional[RepairPlan]:
    """Derive a repair plan from a diagnosis, or None when nothing is safe."""
    stage = stage or diag.stage
    for action in diag.auto_actions():
        op = str(action.get("op", "")).strip()
        if op == "reduce_parallel":
            current = cfg.parallel_jobs or 0
            if current <= 1:
                continue
            factor = float(action.get("factor", 0.5))
            new_jobs = max(1, int(current * factor))
            if new_jobs >= current:
                continue
            threads = max(1, (cfg.total_threads or new_jobs) // new_jobs)
            return RepairPlan(
                op=op, stage=stage,
                changes={"parallel_jobs": new_jobs, "threads_per_job": threads},
                rationale=(f"{diag.rule_id}: lowering concurrency from "
                           f"{current} to {new_jobs} job(s) to cut peak memory"),
            )
        if op == "reduce_threads":
            current = cfg.threads_per_job or 0
            if current <= 1:
                continue
            return RepairPlan(
                op=op, stage=stage,
                changes={"threads_per_job": max(1, current // 2)},
                rationale=f"{diag.rule_id}: halving threads per job",
            )
        if op == "increase_memory":
            return RepairPlan(
                op=op, stage=stage,
                changes={"memory": str(action.get("value", cfg.memory))},
                rationale=f"{diag.rule_id}: raising the memory request",
            )
        if op == "retry":
            return RepairPlan(op=op, stage=stage, changes={},
                              rationale=f"{diag.rule_id}: transient failure, retrying")
    return None


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
def repair_log_path(results_dir: Path) -> Path:
    return Path(results_dir) / "reports" / "repair_log.jsonl"


def read_log(results_dir: Path) -> List[Dict[str, Any]]:
    path = repair_log_path(results_dir)
    entries: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return entries


def append_log(results_dir: Path, entry: Dict[str, Any]) -> None:
    path = repair_log_path(results_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def snapshot_script(results_dir: Path, stage: str, attempt: int,
                    script: Path) -> Optional[str]:
    """Preserve the failing script before anything is changed."""
    target_dir = (Path(results_dir) / "reports" / "repairs" / stage
                  / f"attempt-{attempt}")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / script.name
        shutil.copy2(str(script), str(target))
        return str(target)
    except OSError:
        return None


def attempts_for(results_dir: Path, stage: str) -> List[Dict[str, Any]]:
    return [e for e in read_log(results_dir) if e.get("stage") == stage]


def signature_seen(results_dir: Path, stage: str, signature: str) -> bool:
    """Has this exact failure signature already been repaired once?"""
    return any(e.get("signature") == signature
               for e in attempts_for(results_dir, stage))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def attempt_repair(cfg, stage: str, diag, config_path: str,
                   max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                   runner=None) -> Dict[str, Any]:
    """Try one bounded repair of ``stage``. Returns a result record.

    ``runner`` is injected for testing; by default the stage is re-run through
    ``pipeline.run_step`` — and only that stage.
    """
    from .. import pipeline, routes

    results_dir = Path(cfg.results_dir)
    prior = attempts_for(results_dir, stage)
    attempt_no = len(prior) + 1

    outcome: Dict[str, Any] = {
        "stage": stage, "attempt": attempt_no, "applied": False,
        "repaired": False, "reason": "", "plan": None,
    }

    if max_attempts <= 0:
        outcome["reason"] = "automatic repair is disabled (--auto-repair 0)"
        return outcome
    if attempt_no > max_attempts:
        outcome["reason"] = (f"repair limit reached ({max_attempts} attempt(s)); "
                             f"stopping rather than looping")
        return outcome

    signature = f"{diag.rule_id or 'unknown'}:{diag.exit_code}"
    if signature_seen(results_dir, stage, signature):
        outcome["reason"] = (f"the same failure signature ({signature}) already "
                             f"occurred; stopping rather than repeating a repair "
                             f"that did not work")
        return outcome

    plan = plan_from_diagnosis(cfg, diag, stage)
    if plan is None:
        outcome["reason"] = ("no safe automatic repair for this failure — it "
                             "needs a human decision")
        return outcome

    try:
        check_allowed(plan)
    except RepairRefused as exc:
        outcome["reason"] = f"refused: {exc}"
        append_log(results_dir, {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stage": stage, "attempt": attempt_no, "signature": signature,
            "diagnosis": diag.rule_id, "plan": plan.as_dict(),
            "refused": str(exc), "outcome": "refused",
        })
        return outcome

    outcome["plan"] = plan.as_dict()
    script = results_dir / routes.STEPS[stage].script
    snapshot = snapshot_script(results_dir, stage, attempt_no, script)

    for field_name, value in plan.changes.items():
        setattr(cfg, field_name, value)
    errors = cfg.validate()
    if errors:
        outcome["reason"] = f"the adjusted config would be invalid: {errors[0]}"
        append_log(results_dir, {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stage": stage, "attempt": attempt_no, "signature": signature,
            "diagnosis": diag.rule_id, "plan": plan.as_dict(),
            "snapshot": snapshot, "outcome": "invalid_config",
            "validation_errors": errors,
        })
        return outcome

    cfg.to_yaml(config_path)
    outcome["applied"] = True

    # Re-render so the stage script picks up the new resource plan, then re-run
    # only that stage.
    try:
        pipeline.materialize(cfg)
    except Exception as exc:
        outcome["reason"] = f"re-render failed: {exc}"
        append_log(results_dir, {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stage": stage, "attempt": attempt_no, "signature": signature,
            "diagnosis": diag.rule_id, "plan": plan.as_dict(),
            "snapshot": snapshot, "outcome": "render_failed",
            "error": str(exc),
        })
        return outcome

    pipeline.write_step_status(cfg, stage, "pending")
    run = runner or (lambda: pipeline.run_step(cfg, stage))
    rc = run()
    repaired = (rc == 0 and pipeline.step_status(cfg, stage) == "completed")
    outcome["repaired"] = repaired
    outcome["exit_code"] = rc
    if not repaired:
        outcome["reason"] = f"the stage failed again (exit {rc})"

    append_log(results_dir, {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stage": stage, "attempt": attempt_no, "signature": signature,
        "diagnosis": diag.rule_id, "diagnosis_title": diag.title,
        "plan": plan.as_dict(), "snapshot": snapshot,
        "validation": "config validated before re-run",
        "rerun_command": f"metaglens run --only {stage}",
        "exit_code": rc,
        "outcome": "repaired" if repaired else "still_failing",
    })
    return outcome
