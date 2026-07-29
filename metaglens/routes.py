"""Route and step definitions for the MetaGLens pipeline.

A *step* is one analytical stage backed by a bundled shell template. A *route*
is a named, ordered subset of steps with a fixed analysis basis and binning
strategy. These mirror the routes described in the original skill orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Step:
    """One pipeline stage."""

    step_id: str          # canonical id used as the key in pipeline_status.json
    script: str           # template file name under metaglens/templates/
    prerequisite: Optional[str]  # step_id that must be completed first, or None
    env_group: str        # conda env group: qc | binning | mag | none


# Canonical catalogue of every step, keyed by step_id. `prerequisite` reflects
# the check_prerequisite call inside each template; route-aware skipping is
# applied at run time when the prerequisite is not part of selected_steps.
STEPS: Dict[str, Step] = {
    "00_setup":       Step("00_setup", "00_setup.sh", None, "none"),
    "01_qc":          Step("01_qc", "01_quality_control.sh", "00_setup", "qc"),
    "02_assembly":    Step("02_assembly", "02_assembly.sh", "01_qc", "qc"),
    "03_mapping":     Step("03_mapping", "03_read_mapping.sh", "02_assembly", "qc"),
    "04_binning":     Step("04_binning", "04_binning.sh", "03_mapping", "binning"),
    "05_checkm":      Step("05_checkm", "05_bin_evaluation.sh", "04_binning", "mag"),
    "06_derep":       Step("06_derep", "06_dereplication.sh", "05_checkm", "mag"),
    "07_taxonomy":    Step("07_taxonomy", "07_taxonomy.sh", "06_derep", "mag"),
    "mag_abundance":  Step("mag_abundance", "mag_abundance.sh", "06_derep", "qc"),
    "08_annotation":  Step("08_annotation", "08_annotation.sh", "07_taxonomy", "mag"),
    "09_contig":      Step("09_contig", "09_contig_analysis.sh", "03_mapping", "mag"),
    "10_community":   Step("10_community", "10_community_summary.sh", None, "none"),
    "11_delivery":    Step("11_delivery", "11_delivery.sh", None, "none"),
}


@dataclass(frozen=True)
class Route:
    name: str
    analysis_basis: str      # mag | contig | both
    binning_strategy: str    # per_sample | co_binning | none
    steps: List[str] = field(default_factory=list)  # ordered, includes 00_setup


_MAG_STEPS = [
    "00_setup", "01_qc", "02_assembly", "03_mapping", "04_binning",
    "05_checkm", "06_derep", "07_taxonomy", "mag_abundance", "08_annotation",
    "10_community", "11_delivery",
]

_CONTIG_STEPS = [
    "00_setup", "01_qc", "02_assembly", "03_mapping", "09_contig",
    "10_community", "11_delivery",
]

_BOTH_STEPS = [
    "00_setup", "01_qc", "02_assembly", "03_mapping", "04_binning",
    "05_checkm", "06_derep", "07_taxonomy", "mag_abundance", "08_annotation",
    "09_contig", "10_community", "11_delivery",
]

ROUTES: Dict[str, Route] = {
    "mag_per_sample": Route("mag_per_sample", "mag", "per_sample", list(_MAG_STEPS)),
    "mag_co_binning": Route("mag_co_binning", "mag", "co_binning", list(_MAG_STEPS)),
    "contig_based":   Route("contig_based", "contig", "none", list(_CONTIG_STEPS)),
    "mag_and_contig": Route("mag_and_contig", "both", "per_sample", list(_BOTH_STEPS)),
}

ROUTE_NAMES = list(ROUTES) + ["custom"]


def assembly_strategy_for(binning_strategy: str) -> str:
    """Map the binning strategy to the assembly-strategy token used by templates."""
    return "co-assembly" if binning_strategy == "co_binning" else "per-sample"


def build_selected_steps(step_ids: List[str]) -> List[str]:
    """Order an arbitrary subset by the canonical catalogue and ensure 00_setup."""
    order = list(STEPS)
    chosen = set(step_ids)
    chosen.add("00_setup")
    return [s for s in order if s in chosen]


def resolve_route(route_name: str, custom_steps: Optional[List[str]] = None) -> Route:
    """Return a Route object for a preset name, or build a custom one."""
    if route_name == "custom":
        steps = build_selected_steps(custom_steps or [])
        basis, binning = _infer_basis(steps)
        return Route("custom", basis, binning, steps)
    if route_name not in ROUTES:
        raise ValueError(
            f"Unknown route '{route_name}'. Choose one of: {', '.join(ROUTE_NAMES)}"
        )
    return ROUTES[route_name]


def _infer_basis(steps: List[str]):
    has_mag = "04_binning" in steps or "05_checkm" in steps
    has_contig = "09_contig" in steps
    if has_mag and has_contig:
        return "both", "per_sample"
    if has_contig and not has_mag:
        return "contig", "none"
    if has_mag:
        return "mag", "per_sample"
    return "mag", "none"
