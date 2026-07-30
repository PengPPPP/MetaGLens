"""Pipeline materialization and execution.

Materialization writes the rendered stage scripts, the shared utilities, and the
sample manifest into ``<work_dir>/metaglens_results``. Execution runs the
selected stage scripts in order, honouring the resumable state recorded in
``pipeline_status.json`` by the scripts themselves.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from . import render, routes, samples as samples_mod


class PipelineError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Materialization
# --------------------------------------------------------------------------- #
def resolve_samples(cfg: Config) -> List[samples_mod.Sample]:
    """Return samples from an existing manifest, or by discovery."""
    manifest = cfg.sample_manifest
    if manifest and Path(manifest).is_file():
        return samples_mod.read_manifest(manifest)
    return samples_mod.discover(cfg.raw_data_dir)[0]


def materialize(cfg: Config, sample_list: Optional[List[samples_mod.Sample]] = None,
                validate_syntax: bool = True) -> List[Path]:
    """Write all rendered stage scripts + support files. Returns script paths."""
    errors = cfg.validate()
    if errors:
        raise PipelineError("Invalid configuration:\n  - " + "\n  - ".join(errors))

    if sample_list is None:
        sample_list = resolve_samples(cfg)
    sample_ids = [s.sample_id for s in sample_list]

    results_dir = cfg.results_dir
    results_dir.mkdir(parents=True, exist_ok=True)

    # samples.tsv is the canonical manifest consumed by the scripts.
    manifest_path = results_dir / "samples.tsv"
    samples_mod.write_manifest(sample_list, str(manifest_path))
    cfg.sample_manifest = str(manifest_path)

    render.copy_support_files(results_dir)

    written: List[Path] = []
    for step_id in cfg.route.steps:
        script_text = render.render_step(cfg, step_id, sample_ids)
        dest = results_dir / routes.STEPS[step_id].script
        dest.write_text(script_text, encoding="utf-8")
        dest.chmod(0o755)
        if validate_syntax:
            render.bash_syntax_ok(dest)
        written.append(dest)
    return written


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
def status_file_path(cfg: Config) -> Path:
    return cfg.results_dir / "pipeline_status.json"


def read_status(cfg: Config) -> Optional[Dict]:
    path = status_file_path(cfg)
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def step_status(cfg: Config, step_id: str) -> str:
    data = read_status(cfg)
    if not data:
        return "pending"
    return data.get("steps", {}).get(step_id, {}).get("status", "pending")


def first_incomplete_step(cfg: Config) -> Optional[str]:
    for step_id in cfg.route.steps:
        if step_status(cfg, step_id) != "completed":
            return step_id
    return None


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def write_step_status(cfg: Config, step_id: str, status: str,
                      extra: Optional[Dict] = None) -> None:
    """Patch one step's status in ``pipeline_status.json`` (best effort)."""
    path = status_file_path(cfg)
    if not path.is_file():
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return
    step = data.setdefault("steps", {}).setdefault(step_id, {})
    step["status"] = status
    if extra:
        step.update(extra)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
    except OSError:
        pass


def validate_step_products(cfg: Config, step_id: str):
    """Semantic product validation for one stage (see :mod:`metaglens.state`)."""
    from . import state
    return state.validate_stage(cfg.results_dir, step_id)


def run_step(cfg: Config, step_id: str, validate_products: bool = True) -> int:
    """Execute one stage script from the results dir; returns its exit code.

    When the script reports success, its **products** are re-checked before the
    result is accepted. A stage that exits 0 while producing nothing usable is
    demoted back to ``failed`` — the pipeline must never treat a header-only
    table as a result (see metaglens.state).
    """
    script = cfg.results_dir / routes.STEPS[step_id].script
    if not script.is_file():
        raise PipelineError(f"Script not found (run materialize first): {script}")
    proc = subprocess.run(["bash", str(script)], cwd=str(cfg.results_dir))
    rc = proc.returncode

    if rc == 0 and validate_products and step_status(cfg, step_id) == "completed":
        report = validate_step_products(cfg, step_id)
        if not report.ok:
            reasons = report.failures
            write_step_status(cfg, step_id, "failed", {
                "product_validation": {"ok": False, "failures": reasons},
            })
            print(f"[metaglens] {step_id}: the script reported success but its "
                  f"products did not pass validation:")
            for reason in reasons:
                print(f"[metaglens]   - {reason}")
            return 1
        write_step_status(cfg, step_id, "completed", {
            "product_validation": {"ok": True,
                                   "checks": len(report.checks)},
        })
    return rc


def select_steps(cfg: Config, only: Optional[List[str]] = None,
                 from_step: Optional[str] = None) -> List[str]:
    """Resolve the ordered steps for a run, validating the requested selection.

    ``only`` restricts execution to a subset; ``from_step`` resumes from a step.
    Both are validated against the selected route so a typo fails loudly instead
    of silently producing an empty run.
    """
    steps = cfg.route.steps
    from .express.suggest import suggest

    if only:
        unknown = [s for s in only if s not in steps]
        if unknown:
            hints = []
            for name in unknown:
                hint = suggest(name, steps)
                hints.append(f"'{name}'" + (f" — {hint}" if hint else ""))
            raise PipelineError(
                f"Step(s) not in route '{cfg.route.name}': {'; '.join(hints)}. "
                f"Route steps: {', '.join(steps)}."
            )
        return [s for s in steps if s in set(only)]
    if from_step:
        if from_step not in steps:
            hint = suggest(from_step, steps)
            raise PipelineError(
                f"Step '{from_step}' not in route '{cfg.route.name}'."
                + (f" {hint}" if hint else "")
                + f" Route steps: {', '.join(steps)}."
            )
        return steps[steps.index(from_step):]
    return list(steps)


def run(cfg: Config, only: Optional[List[str]] = None,
        from_step: Optional[str] = None) -> None:
    """Execute selected stage scripts in order, stopping on the first failure.

    Steps already marked completed are skipped.
    """
    run_list = select_steps(cfg, only=only, from_step=from_step)

    for step_id in run_list:
        if step_status(cfg, step_id) == "completed":
            print(f"[metaglens] {step_id}: already completed — skipping.")
            continue
        print(f"[metaglens] ==== running {step_id} "
              f"({routes.STEPS[step_id].script}) ====")
        rc = run_step(cfg, step_id)
        if rc != 0:
            raise PipelineError(
                f"Stage {step_id} failed with exit code {rc}. "
                f"See {cfg.results_dir}/reports/logs/ and pipeline_status.json."
            )
    print("[metaglens] pipeline finished.")


def resume(cfg: Config) -> None:
    start = first_incomplete_step(cfg)
    if start is None:
        print("[metaglens] all selected steps already completed.")
        return
    print(f"[metaglens] resuming from {start}.")
    run(cfg, from_step=start)
