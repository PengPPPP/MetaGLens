"""Execution plan: what will run, roughly how long, and what is missing.

Answers the three questions a newcomer has before committing hours of compute:
how long, how much disk, and will it fail on a missing database. Everything
here is deliberately an order-of-magnitude estimate — the numbers carry an
explicit +/-50% band rather than pretending to be precise, because
bioinformatics runtimes depend heavily on data characteristics.

``render_plain`` produces a paste-able plain-text summary intended for
requesting compute resources from a supervisor or admin; it also states
explicitly that running MetaGLens incurs no metered cost.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .. import routes
from ..sense import database as database_mod
from ..sense import hardware as hardware_mod
from .planner import recommend_parallel

# Coarse per-stage cost model. Reference point: ~40M read pairs (2x150 bp) per
# sample with 8 threads per job. These are order-of-magnitude figures used only
# for preflight sizing; every rendering states the +/-50% band.
ESTIMATE_REFERENCE = "~40M read pairs (2x150bp) per sample at 8 threads/job"
ESTIMATE_BAND = 0.5
_REFERENCE_THREADS = 8.0

# step -> (mode, minutes at reference, peak RAM GB, disk GB)
# mode: "per_sample" scales with the sample count; "single" runs once.
STAGE_COST: Dict[str, Dict[str, Any]] = {
    "00_setup":      {"mode": "single", "minutes": 1, "ram_gb": 1, "disk_gb": 0.1},
    "01_qc":         {"mode": "per_sample", "minutes": 8, "ram_gb": 4, "disk_gb": 5},
    "02_assembly":   {"mode": "per_sample", "minutes": 90, "ram_gb": 24, "disk_gb": 2},
    "03_mapping":    {"mode": "per_sample", "minutes": 20, "ram_gb": 8, "disk_gb": 9},
    "04_binning":    {"mode": "per_sample", "minutes": 25, "ram_gb": 16, "disk_gb": 1.5},
    "05_checkm":     {"mode": "single", "minutes": 30, "ram_gb": 12, "disk_gb": 1},
    "06_derep":      {"mode": "single", "minutes": 20, "ram_gb": 8, "disk_gb": 1},
    "07_taxonomy":   {"mode": "single", "minutes": 45, "ram_gb": 64, "disk_gb": 1},
    "mag_abundance": {"mode": "per_sample", "minutes": 15, "ram_gb": 8, "disk_gb": 4},
    "08_annotation": {"mode": "single", "minutes": 40, "ram_gb": 16, "disk_gb": 2},
    "09_contig":     {"mode": "per_sample", "minutes": 35, "ram_gb": 16, "disk_gb": 3},
    "10_community":  {"mode": "single", "minutes": 2, "ram_gb": 2, "disk_gb": 0.1},
    "11_delivery":   {"mode": "single", "minutes": 5, "ram_gb": 2, "disk_gb": 2},
}

_DEFAULT_COST = {"mode": "single", "minutes": 10, "ram_gb": 4, "disk_gb": 1}


def _sample_count(cfg) -> int:
    from .. import samples as samples_mod
    if cfg.sample_manifest:
        try:
            return len(samples_mod.read_manifest(cfg.sample_manifest))
        except Exception:
            pass
    try:
        return len(samples_mod.discover(cfg.raw_data_dir).samples)
    except Exception:
        return 0


def build_plan(cfg, n_samples: Optional[int] = None) -> Dict[str, Any]:
    """Assemble the execution plan for ``cfg`` as a plain dict."""
    samples = _sample_count(cfg) if n_samples is None else int(n_samples)
    effective_samples = max(1, samples)

    hw = hardware_mod.probe(cfg.work_dir or ".")
    plan = recommend_parallel(cfg.total_threads or hw.cores, hw.ram_gb,
                              effective_samples)
    jobs, threads_per_job = plan.jobs, plan.threads_per_job
    thread_factor = _REFERENCE_THREADS / max(1, threads_per_job)

    stages: List[Dict[str, Any]] = []
    total_minutes = 0.0
    total_disk = 0.0
    peak_ram = 0.0
    for step_id in cfg.route.steps:
        cost = STAGE_COST.get(step_id, _DEFAULT_COST)
        if cost["mode"] == "per_sample":
            waves = math.ceil(effective_samples / max(1, jobs))
            minutes = cost["minutes"] * waves * thread_factor
            ram = cost["ram_gb"] * jobs
            disk = cost["disk_gb"] * effective_samples
            mode = f"{min(jobs, effective_samples)} parallel"
        else:
            minutes = cost["minutes"] * thread_factor
            ram = cost["ram_gb"]
            disk = cost["disk_gb"]
            mode = "single"
        stages.append({
            "step": step_id,
            "script": routes.STEPS[step_id].script,
            "mode": mode,
            "minutes": round(minutes, 1),
            "peak_ram_gb": round(ram, 1),
            "disk_gb": round(disk, 1),
        })
        total_minutes += minutes
        total_disk += disk
        peak_ram = max(peak_ram, ram)

    db_rows: Dict[str, Any] = {}
    db_warnings: List[str] = []
    for name, reason in database_mod.required_databases(cfg).items():
        st = database_mod.discover(name, cfg)
        db_rows[name] = {"reason": reason, "state": st.state, "path": st.path,
                         "version": st.version, "detail": st.detail}
        if st.state != "ready":
            spec = database_mod.REGISTRY[name]
            db_warnings.append(
                f"{name} is not ready ({st.state}); the stage that needs it will "
                f"fail. Prepare it with: metaglens db get {name} "
                f"<dir>   (~{spec.size_hint_gb:.0f} GB)"
            )

    resource_warnings: List[str] = []
    if hw.ram_gb and peak_ram > hw.ram_gb:
        resource_warnings.append(
            f"estimated peak memory ~{peak_ram:.0f} GB exceeds available "
            f"{hw.ram_gb:.0f} GB — lower parallel_jobs or the route will OOM"
        )
    if hw.disk_free_gb and total_disk > hw.disk_free_gb:
        resource_warnings.append(
            f"estimated disk growth ~{total_disk:.0f} GB exceeds free "
            f"{hw.disk_free_gb:.0f} GB"
        )

    return {
        "project": cfg.project_name,
        "route": cfg.route_name,
        "analysis_basis": cfg.route.analysis_basis,
        "samples": samples,
        "parallel": {"jobs": jobs, "threads_per_job": threads_per_job,
                     "reason": plan.reason, "memory_capped": plan.memory_capped},
        "hardware": {"cores": hw.cores, "ram_gb": round(hw.ram_gb, 1),
                     "disk_free_gb": round(hw.disk_free_gb, 1),
                     "summary": hw.summary()},
        "stages": stages,
        "totals": {"minutes": round(total_minutes, 1),
                   "hours": round(total_minutes / 60.0, 1),
                   "peak_ram_gb": round(peak_ram, 1),
                   "disk_gb": round(total_disk, 1)},
        "estimate": {"reference": ESTIMATE_REFERENCE, "band": ESTIMATE_BAND,
                     "note": "coarse estimates, +/-50%"},
        "databases": db_rows,
        "db_warnings": db_warnings,
        "resource_warnings": resource_warnings,
        "ok": not db_warnings and not resource_warnings,
    }


def _hm(minutes: float) -> str:
    hours, mins = divmod(int(round(minutes)), 60)
    return f"{hours}h{mins:02d}m" if hours else f"{mins}m"


def render_plain(plan: Dict[str, Any]) -> str:
    """Paste-able plain-text summary (resource requests, zero-cost statement)."""
    band = int(plan["estimate"]["band"] * 100)
    lines: List[str] = []
    lines.append("MetaGLens execution plan")
    lines.append("=" * 60)
    lines.append(f"Project        : {plan['project']}")
    lines.append(f"Route          : {plan['route']} (basis: {plan['analysis_basis']})")
    lines.append(f"Samples        : {plan['samples']}")
    lines.append(f"Parallel plan  : {plan['parallel']['jobs']} job(s) x "
                 f"{plan['parallel']['threads_per_job']} thread(s)")
    lines.append(f"Host           : {plan['hardware']['summary']}")
    lines.append("")
    lines.append(f"{'Stage':<16}{'Mode':<14}{'Time':>9}{'Peak RAM':>11}{'Disk':>9}")
    lines.append("-" * 60)
    for s in plan["stages"]:
        lines.append(f"{s['step']:<16}{s['mode']:<14}{_hm(s['minutes']):>9}"
                     f"{s['peak_ram_gb']:>9.0f} GB{s['disk_gb']:>7.0f} GB")
    lines.append("-" * 60)
    t = plan["totals"]
    lines.append(f"{'TOTAL':<16}{'':<14}{_hm(t['minutes']):>9}"
                 f"{t['peak_ram_gb']:>9.0f} GB{t['disk_gb']:>7.0f} GB")
    lines.append("")
    lines.append(f"Estimates are COARSE (+/-{band}%), based on "
                 f"{plan['estimate']['reference']}.")
    lines.append("Actual runtime depends strongly on data characteristics.")
    lines.append("")

    if plan["databases"]:
        lines.append("Reference databases required:")
        for name, row in plan["databases"].items():
            state = row["state"]
            extra = f" ({row['version']})" if row["version"] else ""
            where = row["path"] or row["detail"]
            lines.append(f"  - {name:<9} {state:<11}{extra} {where}")
        lines.append("")

    if plan["db_warnings"] or plan["resource_warnings"]:
        lines.append("Blocking issues to resolve before running:")
        for w in plan["db_warnings"] + plan["resource_warnings"]:
            lines.append(f"  ! {w}")
        lines.append("")

    lines.append("Cost note: MetaGLens runs entirely locally. It requires no API")
    lines.append("key, makes no outbound calls during analysis, and incurs no")
    lines.append("per-use or metered charges. The only resources needed are the")
    lines.append("CPU, memory, and disk listed above (plus a one-off database")
    lines.append("download if those are not already present on the system).")
    return "\n".join(lines) + "\n"
