"""Project configuration model and YAML (de)serialization.

The :class:`Config` dataclass captures every parameter needed to render the
bundled shell templates. Defaults mirror the original skill's default tables so
that a minimal config still produces a runnable MAG pipeline.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

from . import routes


@dataclass
class Config:
    # --- Project ---
    project_name: str = ""
    work_dir: str = ""
    raw_data_dir: str = ""
    db_dir: str = ""
    download_dbs: bool = False

    # --- Route / execution plan ---
    route_name: str = "mag_per_sample"
    custom_steps: List[str] = field(default_factory=list)
    exec_env: str = "local"           # local | slurm | sge
    total_threads: int = 16
    parallel_jobs: int = 0            # 0 => auto-derive from sample count
    threads_per_job: int = 0          # 0 => auto-derive
    memory: str = "64G"               # SLURM --mem request (comment for local)

    # --- Conda ---
    conda_mode: str = "none"          # create | reuse | reuse_and_update | none
    conda_env: str = "none"
    conda_origin: str = "user-specified"
    missing_tools: List[str] = field(default_factory=list)
    update_tools: List[str] = field(default_factory=list)

    # --- Samples ---
    sample_manifest: str = ""
    sample_pattern: str = "auto"

    # --- Stage 01 QC ---
    quality_threshold: int = 15
    min_length: int = 75
    remove_host: bool = False
    host_genome: str = ""
    remove_phix: bool = False
    phix_index: str = ""

    # --- Stage 02 assembly ---
    assembler: str = "megahit"        # megahit | metaspades
    kmer_list: str = "21,29,39,59,79,99,121,141"
    min_contig_len: int = 1000
    megahit_preset: str = "meta-sensitive"

    # --- Stage 03 mapping ---
    align_tool: str = "bowtie2"       # bowtie2 | bwa-mem2
    align_mode: str = "very-sensitive"
    calc_depth: bool = True

    # --- Stage 04 binning ---
    min_contig: int = 1500
    use_metabat2: bool = True
    use_maxbin2: bool = True
    use_concoct: bool = True
    use_das_tool: bool = True
    group_label: str = ""             # blank => project_name

    # --- Stage 05 checkm ---
    bin_extension: str = "fa"
    checkm2_db: str = ""              # blank => {db_dir}/checkm2
    completeness_min: int = 50
    contamination_max: int = 10

    # --- Stage 06 derep ---
    ani_threshold: str = "95"

    # --- Stage 07 taxonomy ---
    taxonomy_tool: str = "gtdbtk"     # gtdbtk | kraken2
    taxonomy_db: str = ""             # blank => gtdbtk or kraken2_standard by tool
    mag_extension: str = "fa"
    kraken2_confidence: str = "0"
    use_bracken: bool = False
    bracken_read_length: int = 150

    # --- Stage 08 annotation ---
    use_prokka: bool = True
    prokka_kingdom: str = "Bacteria"  # Bacteria | Archaea | Viruses
    use_eggnog: bool = True
    eggnog_db: str = ""               # blank => {db_dir}/eggnog

    # --- Stage 09 contig ---
    contig_taxonomy: str = "none"     # kraken2 | none
    kraken2_db: str = ""              # blank => {db_dir}/kraken2_standard

    # --- Stage 10 community ---
    top_levels: str = "10 15"
    tax_level: str = "S"

    # --- Stage 11 delivery ---
    do_tarball: bool = False

    # ------------------------------------------------------------------ #
    # Derived helpers
    # ------------------------------------------------------------------ #
    @property
    def results_dir(self) -> Path:
        return Path(self.work_dir) / "metaglens_results"

    @property
    def route(self) -> routes.Route:
        return routes.resolve_route(self.route_name, self.custom_steps)

    def resolved_db_dir(self) -> str:
        return self.db_dir or str(Path(self.work_dir) / "databases")

    # ------------------------------------------------------------------ #
    # YAML round-trip
    # ------------------------------------------------------------------ #
    def to_yaml(self, path: str) -> None:
        data = dataclasses.asdict(self)
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(
                data, handle, sort_keys=False, allow_unicode=True, default_flow_style=False
            )

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        return cls(**data)

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self) -> List[str]:
        """Return a list of human-readable problems; empty means valid."""
        errors: List[str] = []
        if not self.project_name:
            errors.append("project_name is required.")
        if not self.work_dir:
            errors.append("work_dir is required.")
        if not self.raw_data_dir:
            errors.append("raw_data_dir is required.")
        elif not Path(self.raw_data_dir).is_dir():
            errors.append(f"raw_data_dir does not exist: {self.raw_data_dir}")
        if self.route_name not in routes.ROUTE_NAMES:
            errors.append(
                f"route_name must be one of {routes.ROUTE_NAMES}, got '{self.route_name}'."
            )
        if self.route_name == "custom":
            if not self.custom_steps:
                errors.append("route_name is 'custom' but custom_steps is empty.")
            unknown_steps = [s for s in self.custom_steps if s not in routes.STEPS]
            if unknown_steps:
                errors.append(
                    f"custom_steps contains unknown step id(s): {', '.join(unknown_steps)}. "
                    f"Valid steps: {', '.join(routes.STEPS)}."
                )
        if self.exec_env not in ("local", "slurm", "sge"):
            errors.append("exec_env must be local, slurm, or sge.")
        if self.conda_mode not in ("create", "reuse", "reuse_and_update", "none"):
            errors.append("conda_mode must be create, reuse, reuse_and_update, or none.")
        if self.total_threads < 1:
            errors.append("total_threads must be >= 1.")
        if self.assembler not in ("megahit", "metaspades"):
            errors.append("assembler must be megahit or metaspades.")
        if self.align_tool not in ("bowtie2", "bwa-mem2"):
            errors.append("align_tool must be bowtie2 or bwa-mem2.")
        if self.taxonomy_tool not in ("gtdbtk", "kraken2"):
            errors.append("taxonomy_tool must be gtdbtk or kraken2.")
        if self.contig_taxonomy not in ("kraken2", "none"):
            errors.append("contig_taxonomy must be kraken2 or none.")
        if self.prokka_kingdom not in ("Bacteria", "Archaea", "Viruses"):
            errors.append("prokka_kingdom must be Bacteria, Archaea, or Viruses.")

        # Cross-field consistency: the community stage (10_community) needs at
        # least one taxonomy/abundance source. If it is selected but nothing can
        # feed it, fail here rather than producing an empty table at runtime
        # (see 10_community_summary.sh source-selection chain).
        if self.route_name in routes.ROUTES or (
            self.route_name == "custom" and self.custom_steps
        ):
            try:
                steps = self.route.steps
            except Exception:
                steps = []
            if "10_community" in steps:
                has_mag_taxonomy = "07_taxonomy" in steps
                has_contig_source = (
                    "09_contig" in steps and self.contig_taxonomy == "kraken2"
                )
                if not has_mag_taxonomy and not has_contig_source:
                    errors.append(
                        "10_community is selected but no taxonomy source will be "
                        "produced: this route has no 07_taxonomy stage and "
                        "contig_taxonomy is 'none'. Set contig_taxonomy=kraken2 "
                        "(requires a Kraken2 database) so 09_contig emits contig "
                        "taxonomy, or drop 10_community from the route."
                    )
        return errors
