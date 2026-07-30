"""Which command-line tools *this* run actually needs.

Symmetric with :func:`metaglens.sense.database.required_databases`. The flat
``conda_env.PIPELINE_TOOLS`` constant cannot answer "what does this route with
these switches need", which is exactly what ``doctor`` and ``plan`` require:
a tool the selected route never invokes must not be reported as a problem
(ruling D-2 — show it, label it "not needed by this route", do not fail).

Requirements are grounded in what the stage templates actually invoke, not in
the coarse ``ENV_GROUPS`` package lists. Two dependencies are deliberately
encoded because a group-based derivation would get them wrong:

* ``03_read_mapping.sh`` calls ``jgi_summarize_bam_contig_depths``, which ships
  with **metabat2** — so depth calculation needs a "binning"-group tool even on
  routes that never bin.
* ``prodigal`` is invoked by ``09_contig_analysis.sh`` and by
  ``08_annotation.sh`` when eggNOG runs without Prokka, yet it is not listed in
  ``ENV_GROUPS`` at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class ToolSpec:
    tool: str                 # conda package name (matches ENV_GROUPS entries)
    command: str              # executable that must be runnable on PATH
    group: str                # conda env group: qc | binning | mag
    steps: Tuple[str, ...]    # stages that may invoke it
    # (cfg, selected_steps) -> reason string, or None when not needed
    needed: Callable[[object, Set[str]], Optional[str]]


def _plain(reason: str) -> Callable[[object, Set[str]], Optional[str]]:
    """Needed whenever one of the tool's steps is selected."""
    return lambda cfg, steps: reason


def _mapping_steps(steps: Set[str]) -> List[str]:
    return [s for s in ("03_mapping", "mag_abundance") if s in steps]


def _bowtie2_needed(cfg, steps: Set[str]) -> Optional[str]:
    reasons = []
    if "01_qc" in steps and (cfg.remove_host or cfg.remove_phix):
        which = " and ".join(
            w for w, on in (("host", cfg.remove_host), ("PhiX", cfg.remove_phix)) if on
        )
        reasons.append(f"01_qc builds a Bowtie2 index for {which} removal")
    mapped = _mapping_steps(steps)
    if mapped and cfg.align_tool == "bowtie2":
        reasons.append(f"{'/'.join(mapped)} aligns reads with Bowtie2")
    return "; ".join(reasons) or None


def _bwa_needed(cfg, steps: Set[str]) -> Optional[str]:
    mapped = _mapping_steps(steps)
    if mapped and cfg.align_tool == "bwa-mem2":
        return f"{'/'.join(mapped)} aligns reads with bwa-mem2"
    return None


def _metabat2_needed(cfg, steps: Set[str]) -> Optional[str]:
    reasons = []
    if "04_binning" in steps and cfg.use_metabat2:
        reasons.append("04_binning runs MetaBAT2")
    # jgi_summarize_bam_contig_depths ships with the metabat2 package.
    if "03_mapping" in steps and cfg.calc_depth:
        reasons.append(
            "03_mapping computes contig depth with "
            "jgi_summarize_bam_contig_depths (ships with metabat2)"
        )
    return "; ".join(reasons) or None


def _kraken2_needed(cfg, steps: Set[str]) -> Optional[str]:
    reasons = []
    if "07_taxonomy" in steps and cfg.taxonomy_tool == "kraken2":
        reasons.append("07_taxonomy profiles reads with Kraken2")
    if "09_contig" in steps and cfg.contig_taxonomy == "kraken2":
        reasons.append("09_contig classifies contigs with Kraken2")
    return "; ".join(reasons) or None


def _prodigal_needed(cfg, steps: Set[str]) -> Optional[str]:
    reasons = []
    if "09_contig" in steps:
        reasons.append("09_contig predicts genes with Prodigal")
    if "08_annotation" in steps and cfg.use_eggnog and not cfg.use_prokka:
        reasons.append("08_annotation predicts genes with Prodigal (Prokka disabled)")
    return "; ".join(reasons) or None


def _eggnog_needed(cfg, steps: Set[str]) -> Optional[str]:
    if not cfg.use_eggnog:
        return None
    where = [s for s in ("08_annotation", "09_contig") if s in steps]
    if not where:
        return None
    return f"{'/'.join(where)} annotates with eggNOG-mapper"


TOOL_SPECS: Tuple[ToolSpec, ...] = (
    ToolSpec("fastp", "fastp", "qc", ("01_qc",),
             _plain("01_qc filters reads with fastp")),
    ToolSpec("megahit", "megahit", "qc", ("02_assembly",),
             lambda cfg, steps: ("02_assembly assembles with MEGAHIT"
                                 if cfg.assembler == "megahit" else None)),
    ToolSpec("spades", "metaspades.py", "qc", ("02_assembly",),
             lambda cfg, steps: ("02_assembly assembles with metaSPAdes"
                                 if cfg.assembler == "metaspades" else None)),
    ToolSpec("seqkit", "seqkit", "qc", ("02_assembly",),
             _plain("02_assembly filters/summarises contigs with SeqKit")),
    ToolSpec("bowtie2", "bowtie2", "qc", ("01_qc", "03_mapping", "mag_abundance"),
             _bowtie2_needed),
    ToolSpec("bwa-mem2", "bwa-mem2", "qc", ("03_mapping", "mag_abundance"),
             _bwa_needed),
    ToolSpec("samtools", "samtools", "qc", ("03_mapping", "mag_abundance"),
             _plain("read mapping sorts/indexes BAMs with samtools")),
    ToolSpec("metabat2", "metabat2", "binning", ("03_mapping", "04_binning"),
             _metabat2_needed),
    ToolSpec("maxbin2", "run_MaxBin.pl", "binning", ("04_binning",),
             lambda cfg, steps: ("04_binning runs MaxBin2"
                                 if cfg.use_maxbin2 else None)),
    ToolSpec("concoct", "concoct", "binning", ("04_binning",),
             lambda cfg, steps: ("04_binning runs CONCOCT"
                                 if cfg.use_concoct else None)),
    ToolSpec("das_tool", "DAS_Tool", "binning", ("04_binning",),
             lambda cfg, steps: ("04_binning refines bins with DAS Tool"
                                 if cfg.use_das_tool else None)),
    ToolSpec("checkm2", "checkm2", "mag", ("05_checkm",),
             _plain("05_checkm assesses MAG quality with CheckM2")),
    ToolSpec("drep", "dRep", "mag", ("06_derep",),
             _plain("06_derep dereplicates MAGs with dRep")),
    ToolSpec("gtdbtk", "gtdbtk", "mag", ("07_taxonomy",),
             lambda cfg, steps: ("07_taxonomy classifies MAGs with GTDB-Tk"
                                 if cfg.taxonomy_tool == "gtdbtk" else None)),
    ToolSpec("kraken2", "kraken2", "mag", ("07_taxonomy", "09_contig"),
             _kraken2_needed),
    ToolSpec("bracken", "bracken", "mag", ("07_taxonomy",),
             lambda cfg, steps: (
                 "07_taxonomy estimates abundance with Bracken"
                 if cfg.taxonomy_tool == "kraken2" and cfg.use_bracken else None)),
    ToolSpec("prokka", "prokka", "mag", ("08_annotation",),
             lambda cfg, steps: ("08_annotation annotates MAGs with Prokka"
                                 if cfg.use_prokka else None)),
    ToolSpec("prodigal", "prodigal", "mag", ("08_annotation", "09_contig"),
             _prodigal_needed),
    ToolSpec("eggnog-mapper", "emapper.py", "mag",
             ("08_annotation", "09_contig"), _eggnog_needed),
)

_BY_TOOL: Dict[str, ToolSpec] = {s.tool: s for s in TOOL_SPECS}


def all_known_tools() -> List[str]:
    """Every tool MetaGLens may use, in a stable display order."""
    return [s.tool for s in TOOL_SPECS]


def tool_spec(tool: str) -> Optional[ToolSpec]:
    return _BY_TOOL.get(tool)


def required_tools(cfg) -> Dict[str, str]:
    """Tools this run actually needs, keyed tool -> reason.

    A tool whose stages are not in the selected route, or whose enabling switch
    is off, is simply absent from the result (it is not a missing dependency).
    """
    try:
        steps = set(cfg.route.steps)
    except Exception:
        steps = set()
    needed: Dict[str, str] = {}
    for spec in TOOL_SPECS:
        if not steps.intersection(spec.steps):
            continue
        reason = spec.needed(cfg, steps)
        if reason:
            needed[spec.tool] = reason
    return needed
