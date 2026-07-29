"""Parallel-plan recommendation.

Builds on the baseline derivation already used in ``render.build_global_values``
(``jobs = min(n_samples, cores)``; ``threads_per_job = cores // jobs``) and adds
the memory constraint that baseline ignores: per-sample peak RAM times the
number of concurrent jobs must not exceed available RAM, or memory-heavy stages
(assembly especially) OOM instead of merely running slowly.

Only resource knobs are decided here. The output always explains itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Coarse per-job peak-RAM figure, dominated by the assembly stage on complex
# metagenomes. Deliberately a single rough constant (order-of-magnitude), not a
# precise model — it is used only to avoid oversubscribing memory, and the
# rationale string always flags it as an estimate.
PEAK_MEM_GB_PER_JOB = 24.0


@dataclass
class Plan:
    jobs: int                  # samples processed concurrently
    threads_per_job: int       # threads each concurrent job gets
    reason: str                # human explanation of the choice
    memory_capped: bool        # True when RAM (not cores) limited the jobs


def recommend_parallel(cores: int, ram_gb: float, n_samples: int,
                       peak_mem_gb_per_job: float = PEAK_MEM_GB_PER_JOB) -> Plan:
    """Recommend ``jobs`` x ``threads_per_job`` for per-sample stages.

    Guarantees ``jobs * threads_per_job <= cores`` and, when ``ram_gb`` is
    known (> 0), ``jobs * peak_mem_gb_per_job <= ram_gb``.
    """
    cores = max(1, int(cores))
    samples = max(1, int(n_samples))

    baseline_jobs = min(samples, cores)

    if ram_gb and ram_gb > 0 and peak_mem_gb_per_job > 0:
        mem_cap = max(1, int(math.floor(ram_gb / peak_mem_gb_per_job)))
    else:
        mem_cap = baseline_jobs  # RAM unknown -> do not constrain on memory

    jobs = max(1, min(baseline_jobs, mem_cap))
    threads_per_job = max(1, cores // jobs)
    memory_capped = jobs < baseline_jobs

    if memory_capped:
        reason = (
            f"{samples} sample(s) could run {baseline_jobs} at a time on "
            f"{cores} cores, but assembly peaks around ~{peak_mem_gb_per_job:.0f} "
            f"GB/job (rough estimate); {baseline_jobs} jobs would need "
            f"~{baseline_jobs * peak_mem_gb_per_job:.0f} GB > {ram_gb:.0f} GB "
            f"available. Capped to {jobs} job(s) x {threads_per_job} thread(s) "
            f"(~{jobs * peak_mem_gb_per_job:.0f} GB) to avoid OOM."
        )
    elif ram_gb and ram_gb > 0:
        reason = (
            f"{samples} sample(s) on {cores} cores / {ram_gb:.0f} GB RAM: "
            f"{jobs} job(s) x {threads_per_job} thread(s). Memory headroom is "
            f"sufficient (~{jobs * peak_mem_gb_per_job:.0f} GB estimated peak)."
        )
    else:
        reason = (
            f"{samples} sample(s) on {cores} cores: {jobs} job(s) x "
            f"{threads_per_job} thread(s). RAM unknown, so the memory ceiling "
            f"was not applied — verify assembly does not exhaust memory."
        )

    return Plan(jobs=jobs, threads_per_job=threads_per_job,
                reason=reason, memory_capped=memory_capped)
