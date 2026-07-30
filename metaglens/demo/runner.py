"""Offline end-to-end self-check (``metaglens demo``).

Creates a throwaway project with synthetic reads and a stub toolchain, renders
the real stage scripts, and runs the selected route to completion. What gets
exercised is the genuine article: stage control flow, the resumable status file,
product validation, and report/monitor generation — not just ``bash -n``.

Deliberate properties: no network, no conda, no reference databases, seconds to
run, and nothing is written outside the temporary directory (never ``~``, never
an existing project). **It produces no scientific results** — every number and
sequence comes from a stub.
"""

from __future__ import annotations

import gzip
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Config
from .. import pipeline, routes
from .stubs import install_stubs, stub_env

DEMO_ROUTES = ("mag_per_sample", "contig_based")

# Fake reference-database directories: stages check that the directory exists
# before invoking their tool, and the stubs ignore the contents.
_FAKE_DBS = ("checkm2", "gtdbtk", "eggnog", "kraken2_standard")


def _write_reads(raw_dir: Path, sample_ids: List[str], n_reads: int = 500) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for sid in sample_ids:
        for mate in (1, 2):
            path = raw_dir / f"{sid}_R{mate}.fastq.gz"
            with gzip.open(path, "wt") as handle:
                for i in range(n_reads):
                    handle.write(f"@{sid}_read{i}/{mate}\n")
                    handle.write("ACGTACGTACGTACGTACGTACGTACGTACGT\n")
                    handle.write("+\n")
                    handle.write("IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII\n")


def _make_config(root: Path, route: str, sample_ids: List[str]) -> Config:
    raw = root / "raw"
    _write_reads(raw, sample_ids)
    db_dir = root / "databases"
    for name in _FAKE_DBS:
        (db_dir / name).mkdir(parents=True, exist_ok=True)
    kwargs: Dict[str, Any] = dict(
        project_name="demo",
        work_dir=str(root / "work"),
        raw_data_dir=str(raw),
        db_dir=str(db_dir),
        route_name=route,
        exec_env="local",
        total_threads=2,
        parallel_jobs=1,
        threads_per_job=1,
        conda_mode="none",
        conda_env="none",
        download_dbs=False,
    )
    if route in ("contig_based", "mag_and_contig"):
        # A contig route needs a taxonomy source for 10_community (see §7-8).
        kwargs["contig_taxonomy"] = "kraken2"
    return Config(**kwargs)


def _expected_artefacts(cfg: Config) -> List[Path]:
    """Key products whose absence means the run only *looked* successful."""
    results = cfg.results_dir
    steps = set(cfg.route.steps)
    expected = [results / "samples.tsv", results / "pipeline_status.json"]
    if "01_qc" in steps:
        expected.append(results / "01_qc")
    if "02_assembly" in steps:
        expected.append(results / "02_assembly")
    if "05_checkm" in steps:
        expected.append(results / "05_checkm" / "quality_report.tsv")
    if "06_derep" in steps:
        expected.append(results / "06_derep" / "dereplicated_genomes")
    if "07_taxonomy" in steps:
        expected.append(results / "07_taxonomy" / "gtdbtk" / "gtdbtk.bac120.summary.tsv")
    if "10_community" in steps:
        expected.append(results / "10_community" / "community_matrix.tsv")
    if "11_delivery" in steps:
        expected.append(results / "delivery")
    return expected


def run_demo(route: str = "mag_per_sample", workdir: Optional[str] = None,
             keep: bool = False, sample_ids: Optional[List[str]] = None,
             verbose: bool = False) -> Dict[str, Any]:
    """Run the stub end-to-end check for ``route``. Returns a result dict."""
    if route not in routes.ROUTES:
        raise ValueError(f"unknown route '{route}'")
    sample_ids = sample_ids or ["demoA", "demoB"]

    root = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="metaglens_demo_"))
    root.mkdir(parents=True, exist_ok=True)
    cleanup = not keep and workdir is None

    result: Dict[str, Any] = {
        "route": route, "root": str(root), "ok": False,
        "stages": [], "missing": [], "errors": [],
        "report_html": "", "monitor_html": "",
        "note": "stub toolchain — no scientific results",
    }

    try:
        cfg = _make_config(root, route, sample_ids)
        errors = cfg.validate()
        if errors:
            result["errors"] = errors
            return result

        pipeline.materialize(cfg)
        bin_dir = install_stubs(root / "stubbin")
        env = stub_env(bin_dir)
        results_dir = cfg.results_dir

        for step_id in cfg.route.steps:
            script = results_dir / routes.STEPS[step_id].script
            proc = subprocess.run(
                ["bash", str(script)], cwd=str(results_dir), env=env,
                capture_output=not verbose, text=True, timeout=600,
            )
            status = pipeline.step_status(cfg, step_id)
            result["stages"].append({
                "step": step_id, "exit_code": proc.returncode, "status": status,
            })
            if proc.returncode != 0 or status != "completed":
                tail = ""
                if not verbose and proc.stdout:
                    tail = "\n".join(proc.stdout.strip().splitlines()[-25:])
                if not verbose and proc.stderr:
                    tail += "\n" + "\n".join(proc.stderr.strip().splitlines()[-25:])
                result["errors"].append(
                    f"{step_id}: exit {proc.returncode}, status '{status}'\n{tail}"
                )
                return result

        missing = [str(p) for p in _expected_artefacts(cfg) if not p.exists()]
        result["missing"] = missing

        # Both HTML surfaces must build from the results alone.
        from ..report import generate_report
        from ..observe import monitor as monitor_mod
        result["report_html"] = str(generate_report(results_dir,
                                                   raw_data_dir=cfg.raw_data_dir))
        result["monitor_html"] = str(monitor_mod.write_monitor(results_dir))

        result["ok"] = not missing and not result["errors"]
        return result
    finally:
        if cleanup and not result["ok"] and not keep:
            # Keep the tree when something failed so it can be inspected.
            result["root"] = str(root)
            result["kept_for_debug"] = True
        elif cleanup:
            shutil.rmtree(root, ignore_errors=True)
            result["root"] = "(cleaned up)"
