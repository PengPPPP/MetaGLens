"""Methods text generated from what actually ran.

The one thing this has to get right is honesty about versions. The old command
simply printed a file the shell had written; here the text is assembled in
Python, which is the only place that knows both what the config asked for and
what ``reports/tool_versions.txt`` says is really installed.

Rules: only stages that actually completed are described, versions come from the
recorded file (anything missing is marked ``[provisional]`` rather than guessed),
and branches that did not run are not mentioned at all.

Output is English by contract — deliverables do not follow the interactive
language setting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_VERSION_LINE = re.compile(r"^([A-Za-z0-9_.+-]+)\s*:\s*(.+)$")


def read_tool_versions(results_dir: Path) -> Dict[str, str]:
    """Parse reports/tool_versions.txt into {tool: version}."""
    path = Path(results_dir) / "reports" / "tool_versions.txt"
    versions: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return versions
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _VERSION_LINE.match(line)
        if not match:
            continue
        tool, raw = match.group(1), match.group(2).strip()
        # Strip the bookkeeping tags the setup script appends.
        raw = re.sub(r"\[(new|reused|updated)\]\s*$", "", raw).strip()
        version = _extract_version(raw)
        if version:
            versions[tool.lower()] = version
    return versions


def _extract_version(text: str) -> str:
    """Pull a version number out of a tool's self-reported line.

    Returns "" when the line carries no version — passing the whole line off as
    a version would print things like "v[stub] fastp --version" into a Methods
    section, which is worse than admitting the version was not recorded.
    """
    if not text:
        return ""
    match = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?(?:[-\w.]*)?)", text)
    if match:
        return match.group(1)
    return ""


def _version(versions: Dict[str, str], tool: str) -> str:
    """Version string for a tool, or a provisional marker when unknown."""
    found = versions.get(tool.lower(), "")
    return f"v{found}" if found else "[provisional: version not recorded]"


def completed_stages(status: Dict[str, Any]) -> List[str]:
    """Stages that actually completed, in the recorded order."""
    steps = status.get("steps", {}) or {}
    order = status.get("selected_steps") or list(steps)
    return [s for s in order
            if (steps.get(s, {}) or {}).get("status") == "completed"]


# Each entry returns a paragraph, or None when the stage's options mean there is
# nothing truthful to say.
def _qc(cfg, ver) -> str:
    text = (f"Raw paired-end reads were quality-filtered with fastp "
            f"({_version(ver, 'fastp')}) using a minimum Phred score of "
            f"Q{cfg.quality_threshold} and a minimum retained read length of "
            f"{cfg.min_length} bp.")
    if cfg.remove_host:
        text += (f" Host-derived reads were removed by mapping against "
                 f"{cfg.host_genome or 'the supplied host reference'} with "
                 f"Bowtie2 ({_version(ver, 'bowtie2')}) and retaining unmapped "
                 f"pairs.")
    if cfg.remove_phix:
        text += (" PhiX control reads were removed by the same procedure.")
    return text


def _assembly(cfg, ver) -> str:
    if cfg.assembler == "megahit":
        tool = f"MEGAHIT ({_version(ver, 'megahit')})"
        params = (f"with the {cfg.megahit_preset} preset and k-mer list "
                  f"{cfg.kmer_list}")
    else:
        tool = f"metaSPAdes ({_version(ver, 'spades')})"
        params = f"with k-mer list {cfg.kmer_list}"
    basis = ("Reads from all samples were co-assembled"
             if cfg.route.binning_strategy == "co_binning"
             else "Quality-filtered reads were assembled per sample")
    return (f"{basis} using {tool} {params}. Contigs shorter than "
            f"{cfg.min_contig_len} bp were discarded with SeqKit "
            f"({_version(ver, 'seqkit')}).")


def _mapping(cfg, ver) -> str:
    if cfg.align_tool == "bowtie2":
        aligner = f"Bowtie2 ({_version(ver, 'bowtie2')}) in --{cfg.align_mode} mode"
    else:
        aligner = f"bwa-mem2 ({_version(ver, 'bwa-mem2')})"
    text = (f"Quality-filtered reads were mapped back to the assembled contigs "
            f"with {aligner}, and alignments were sorted and indexed with "
            f"samtools ({_version(ver, 'samtools')}).")
    if cfg.calc_depth:
        text += (" Per-contig coverage depth was computed with "
                 "jgi_summarize_bam_contig_depths.")
    return text


def _binning(cfg, ver) -> Optional[str]:
    binners = []
    if cfg.use_metabat2:
        binners.append(f"MetaBAT2 ({_version(ver, 'metabat2')})")
    if cfg.use_maxbin2:
        binners.append(f"MaxBin2 ({_version(ver, 'maxbin2')})")
    if cfg.use_concoct:
        binners.append(f"CONCOCT ({_version(ver, 'concoct')})")
    if not binners:
        return None
    text = (f"Contigs longer than {cfg.min_contig} bp were binned with "
            f"{', '.join(binners[:-1])}{' and ' if len(binners) > 1 else ''}"
            f"{binners[-1]}, using contig composition and coverage.")
    if cfg.use_das_tool and len(binners) >= 1:
        text += (f" The resulting bin sets were integrated into a "
                 f"non-redundant set with DAS Tool "
                 f"({_version(ver, 'das_tool')}).")
    return text


def _checkm(cfg, ver) -> str:
    return (f"Bin completeness and contamination were estimated with CheckM2 "
            f"({_version(ver, 'checkm2')}). Bins with completeness "
            f">= {cfg.completeness_min}% and contamination "
            f"<= {cfg.contamination_max}% were retained as "
            f"metagenome-assembled genomes (MAGs).")


def _derep(cfg, ver) -> str:
    return (f"Retained MAGs were dereplicated with dRep "
            f"({_version(ver, 'drep')}) at {cfg.ani_threshold}% average "
            f"nucleotide identity, and one representative genome was kept per "
            f"cluster.")


def _taxonomy(cfg, ver) -> str:
    if cfg.taxonomy_tool == "gtdbtk":
        return (f"Representative genomes were taxonomically classified with "
                f"GTDB-Tk ({_version(ver, 'gtdbtk')}) using the classify_wf "
                f"workflow against the GTDB reference taxonomy.")
    text = (f"Quality-filtered reads were taxonomically classified with Kraken2 "
            f"({_version(ver, 'kraken2')}) at a confidence threshold of "
            f"{cfg.kraken2_confidence}.")
    if cfg.use_bracken:
        text += (f" Species-level abundances were re-estimated with Bracken "
                 f"({_version(ver, 'bracken')}) for a read length of "
                 f"{cfg.bracken_read_length} bp.")
    return text


def _mag_abundance(cfg, ver) -> str:
    return ("Relative MAG abundance was estimated by mapping each sample's "
            "quality-filtered reads against the dereplicated representative "
            "genomes and aggregating per-contig coverage to the genome level.")


def _annotation(cfg, ver) -> Optional[str]:
    parts = []
    if cfg.use_prokka:
        parts.append(f"Genes were predicted and annotated in each "
                     f"representative genome with Prokka "
                     f"({_version(ver, 'prokka')}) using the "
                     f"{cfg.prokka_kingdom} database.")
    else:
        parts.append(f"Protein-coding genes were predicted with Prodigal "
                     f"({_version(ver, 'prodigal')}) in metagenomic mode.")
    if cfg.use_eggnog:
        parts.append(f"Predicted proteins were functionally annotated with "
                     f"eggNOG-mapper ({_version(ver, 'eggnog-mapper')}) in "
                     f"DIAMOND mode.")
    return " ".join(parts) if parts else None


def _contig(cfg, ver) -> str:
    text = (f"Protein-coding genes were predicted directly on assembled contigs "
            f"with Prodigal ({_version(ver, 'prodigal')}) in metagenomic mode.")
    if cfg.use_eggnog:
        text += (f" Functional annotation was performed with eggNOG-mapper "
                 f"({_version(ver, 'eggnog-mapper')}).")
    if cfg.contig_taxonomy == "kraken2":
        text += (f" Contigs were taxonomically classified with Kraken2 "
                 f"({_version(ver, 'kraken2')}).")
    return text


def _community(cfg, ver) -> str:
    return (f"A cross-sample community composition table was assembled from the "
            f"available abundance estimates, and the {cfg.top_levels.replace(' ', ' and ')} "
            f"most abundant taxa were tabulated at the "
            f"{cfg.tax_level} level.")


_SECTIONS = [
    ("01_qc", "Quality control and preprocessing", _qc),
    ("02_assembly", "Metagenome assembly", _assembly),
    ("03_mapping", "Read mapping and coverage estimation", _mapping),
    ("04_binning", "Genome binning", _binning),
    ("05_checkm", "MAG quality assessment", _checkm),
    ("06_derep", "Dereplication", _derep),
    ("07_taxonomy", "Taxonomic classification", _taxonomy),
    ("mag_abundance", "MAG abundance estimation", _mag_abundance),
    ("08_annotation", "Functional annotation", _annotation),
    ("09_contig", "Contig-level gene prediction and annotation", _contig),
    ("10_community", "Community composition summary", _community),
]


def generate(cfg, status: Optional[Dict[str, Any]] = None,
             results_dir: Optional[Path] = None) -> str:
    """Build the Methods text for the stages that actually completed."""
    results = Path(results_dir) if results_dir else cfg.results_dir
    if status is None:
        import json
        status_path = results / "pipeline_status.json"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = {}

    done = set(completed_stages(status))
    versions = read_tool_versions(results)

    lines: List[str] = ["# Methods", ""]
    if not done:
        lines.append("No stage has completed yet, so there are no methods to "
                     "report. Run the pipeline first.")
        return "\n".join(lines) + "\n"

    for step_id, heading, builder in _SECTIONS:
        if step_id not in done:
            continue          # never describe a branch that did not run
        try:
            paragraph = builder(cfg, versions)
        except Exception:
            paragraph = None
        if not paragraph:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(paragraph)
        lines.append("")

    provisional = [s for s in lines if "[provisional" in s]
    if provisional:
        lines.append("> Note: entries marked [provisional] had no version "
                     "recorded in reports/tool_versions.txt. Re-run "
                     "`metaglens methods` after a complete run, or fill the "
                     "versions in before submission.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write(cfg, results_dir: Optional[Path] = None) -> Path:
    """Write reports/methods.md and return its path."""
    results = Path(results_dir) if results_dir else cfg.results_dir
    text = generate(cfg, results_dir=results)
    out = results / "reports" / "methods.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
