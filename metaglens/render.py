"""Template rendering engine.

Loads the bundled shell templates, substitutes every ``{{PLACEHOLDER}}`` with a
concrete value derived from a :class:`~metaglens.config.Config`, and validates
the result (no leftover placeholders, optional ``bash -n`` syntax check).
"""

from __future__ import annotations

import re
import subprocess
from importlib import resources
from pathlib import Path
from typing import Dict, List

from .config import Config
from . import routes

_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")


class RenderError(Exception):
    pass


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def load_template(script_name: str) -> str:
    """Read a bundled template's raw text."""
    return resources.files("metaglens.templates").joinpath(script_name).read_text(
        encoding="utf-8"
    )


def copy_support_files(dest_dir: Path) -> None:
    """Copy pipeline_utils.sh and report_logo.b64 into the results root."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    utils = load_template("_pipeline_utils.sh")
    (dest_dir / "pipeline_utils.sh").write_text(utils, encoding="utf-8")
    try:
        logo = resources.files("metaglens.templates").joinpath(
            "report_logo.b64"
        ).read_text(encoding="utf-8")
        (dest_dir / "report_logo.b64").write_text(logo, encoding="utf-8")
    except Exception:
        pass  # report still renders with a text title if the logo is absent


def _env_for_group(cfg: Config, env_group: str) -> str:
    if env_group == "none":
        return cfg.conda_env
    if cfg.conda_mode == "create":
        return f"{cfg.conda_env}_{env_group}"
    return cfg.conda_env


def _db(cfg: Config, sub: str, override: str) -> str:
    return override or str(Path(cfg.resolved_db_dir()) / sub)


def build_global_values(cfg: Config, sample_ids: List[str]) -> Dict[str, str]:
    """Placeholders shared across templates."""
    route = cfg.route
    manifest = cfg.sample_manifest or str(cfg.results_dir / "samples.tsv")
    group_label = cfg.group_label or cfg.project_name
    parallel_jobs = cfg.parallel_jobs or max(1, min(len(sample_ids) or 1, cfg.total_threads))
    threads_per_job = cfg.threads_per_job or max(1, cfg.total_threads // parallel_jobs)
    # Absolute, so the generated scripts are runnable from any cwd and the
    # scheduler directives (#SBATCH --output=...) resolve regardless of the
    # directory the job is submitted from. Sample paths and the manifest are
    # already absolute, so this keeps the whole script self-consistent.
    work_dir = str(Path(cfg.work_dir).expanduser().resolve())

    return {
        # global / project
        "PROJECT_NAME": cfg.project_name,
        "WORK_DIR": work_dir,
        "RAW_DATA_DIR": cfg.raw_data_dir,
        "THREADS": str(cfg.total_threads),
        "MEMORY": cfg.memory,
        "SAMPLE_MANIFEST": manifest,
        "SAMPLE_LIST": " ".join(sample_ids),
        "SAMPLE_PATTERN": cfg.sample_pattern,
        # setup / conda
        "CONDA_MODE": cfg.conda_mode,
        "CONDA_ENV": cfg.conda_env,
        "CONDA_ORIGIN": cfg.conda_origin,
        "MISSING_TOOLS": " ".join(cfg.missing_tools),
        "UPDATE_TOOLS": " ".join(cfg.update_tools),
        "DB_DIR": cfg.resolved_db_dir(),
        "DOWNLOAD_DBS": _yn(cfg.download_dbs),
        # route / plan
        "ROUTE_NAME": route.name,
        "ANALYSIS_BASIS": route.analysis_basis,
        "BINNING_STRATEGY": route.binning_strategy,
        "SELECTED_STEPS": " ".join(route.steps),
        "EXEC_ENV": cfg.exec_env,
        "TOTAL_THREADS": str(cfg.total_threads),
        "PARALLEL_JOBS": str(parallel_jobs),
        "THREADS_PER_JOB": str(threads_per_job),
        "ASSEMBLY_STRATEGY": routes.assembly_strategy_for(route.binning_strategy),
        # stage 01 qc
        "QUALITY_THRESHOLD": str(cfg.quality_threshold),
        "MIN_LENGTH": str(cfg.min_length),
        "REMOVE_HOST": _yn(cfg.remove_host),
        "HOST_GENOME": cfg.host_genome,
        "REMOVE_PHIX": _yn(cfg.remove_phix),
        "PHIX_INDEX": cfg.phix_index,
        # stage 02 assembly
        "ASSEMBLER": cfg.assembler,
        "KMER_LIST": cfg.kmer_list,
        "MIN_CONTIG_LEN": str(cfg.min_contig_len),
        "MEGAHIT_PRESET": cfg.megahit_preset,
        # stage 03 mapping
        "ALIGN_TOOL": cfg.align_tool,
        "ALIGN_MODE": cfg.align_mode,
        "CALC_DEPTH": _yn(cfg.calc_depth),
        # stage 04 binning
        "MIN_CONTIG": str(cfg.min_contig),
        "USE_METABAT2": _yn(cfg.use_metabat2),
        "USE_MAXBIN2": _yn(cfg.use_maxbin2),
        "USE_CONCOCT": _yn(cfg.use_concoct),
        "USE_DAS_TOOL": _yn(cfg.use_das_tool),
        "GROUP_LABEL": group_label,
        # stage 05 checkm
        "BIN_EXTENSION": cfg.bin_extension,
        "COMPLETENESS_MIN": str(cfg.completeness_min),
        "CONTAMINATION_MAX": str(cfg.contamination_max),
        # stage 06 derep
        "ANI_THRESHOLD": str(cfg.ani_threshold),
        # stage 07 taxonomy
        "TAXONOMY_TOOL": cfg.taxonomy_tool,
        "MAG_EXTENSION": cfg.mag_extension,
        "KRAKEN2_CONFIDENCE": str(cfg.kraken2_confidence),
        "USE_BRACKEN": _yn(cfg.use_bracken),
        "BRACKEN_READ_LENGTH": str(cfg.bracken_read_length),
        # stage 08 annotation
        "USE_PROKKA": _yn(cfg.use_prokka),
        "PROKKA_KINGDOM": cfg.prokka_kingdom,
        "USE_EGGNOG": _yn(cfg.use_eggnog),
        # stage 09 contig
        "CONTIG_TAXONOMY": cfg.contig_taxonomy,
        # stage 10 community
        "TOP_LEVELS": cfg.top_levels,
        "TAX_LEVEL": cfg.tax_level,
        # stage 11 delivery
        "DO_TARBALL": _yn(cfg.do_tarball),
    }


def _step_overrides(cfg: Config, step_id: str) -> Dict[str, str]:
    """Placeholders whose value depends on the specific step."""
    step = routes.STEPS[step_id]
    results = cfg.results_dir
    ov: Dict[str, str] = {"CONDA_ENV": _env_for_group(cfg, step.env_group)}

    if step_id == "05_checkm":
        ov["BINS_DIR"] = str(results / "04_binning" / "all_bins")
        ov["CHECKM2_DB"] = _db(cfg, "checkm2", cfg.checkm2_db)
    elif step_id == "06_derep":
        ov["BINS_DIR"] = str(results / "05_checkm" / "filtered_bins")
    elif step_id == "07_taxonomy":
        ov["INPUT_PATH"] = str(results / "06_derep" / "dereplicated_genomes")
        default_sub = "gtdbtk" if cfg.taxonomy_tool == "gtdbtk" else "kraken2_standard"
        ov["DATABASE_PATH"] = _db(cfg, default_sub, cfg.taxonomy_db)
    elif step_id == "08_annotation":
        ov["MAGS_DIR"] = str(results / "06_derep" / "dereplicated_genomes")
        ov["EGGNOG_DB"] = _db(cfg, "eggnog", cfg.eggnog_db)
    elif step_id == "09_contig":
        ov["EGGNOG_DB"] = _db(cfg, "eggnog", cfg.eggnog_db)
        ov["KRAKEN2_DB"] = _db(cfg, "kraken2_standard", cfg.kraken2_db)
    return ov


def render_step(cfg: Config, step_id: str, sample_ids: List[str]) -> str:
    """Render one stage script to a string, validating no placeholder remains."""
    step = routes.STEPS[step_id]
    template = load_template(step.script)
    values = build_global_values(cfg, sample_ids)
    values.update(_step_overrides(cfg, step_id))

    def repl(match: "re.Match[str]") -> str:
        key = match.group(0)[2:-2].strip()
        if key not in values:
            raise RenderError(
                f"No value for placeholder {{{{{key}}}}} in {step.script}."
            )
        return values[key]

    rendered = _PLACEHOLDER_RE.sub(repl, template)
    leftover = _PLACEHOLDER_RE.findall(rendered)
    if leftover:
        raise RenderError(
            f"Unresolved placeholders in {step.script}: {', '.join(sorted(set(leftover)))}"
        )
    return rendered


def bash_syntax_ok(script_path: Path) -> None:
    """Run ``bash -n`` on a rendered script; raise RenderError on failure."""
    try:
        result = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return  # bash unavailable; skip silently
    if result.returncode != 0:
        raise RenderError(
            f"bash -n failed for {script_path.name}:\n{result.stderr.strip()}"
        )
