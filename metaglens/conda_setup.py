"""One-shot conda environment provisioning for the MetaGLens toolchain.

Creates the bioinformatics environments the pipeline needs. Tools are split into
three stage groups because mixing gtdbtk/checkm2/concoct/prokka in a single
environment frequently causes dependency-solver conflicts. The user chooses the
base name; the grouped environments become ``{base}_qc``, ``{base}_binning`` and
``{base}_mag`` (or a single ``{base}`` environment with ``single=True``).
"""

from __future__ import annotations

import subprocess
from typing import Dict, List, Tuple

from .conda_env import find_conda

# Stage-grouped conda package names. Mirrors the grouping used by 00_setup.sh,
# plus bwa-mem2 so the alternative aligner is available when selected, and
# prodigal, which 09_contig_analysis.sh calls directly for gene prediction (and
# 08_annotation.sh calls when Prokka is disabled) but which was missing from
# every group, leaving contig routes with a requirement nothing could install.
ENV_GROUPS: Dict[str, List[str]] = {
    "qc": ["fastp", "megahit", "spades", "bowtie2", "bwa-mem2", "samtools", "seqkit"],
    "binning": ["metabat2", "maxbin2", "concoct", "das_tool"],
    "mag": ["checkm2", "drep", "gtdbtk", "kraken2", "bracken", "prokka",
            "prodigal", "eggnog-mapper"],
}

CHANNELS = ["-c", "conda-forge", "-c", "bioconda"]


class CondaSetupError(Exception):
    pass


def conda_available() -> bool:
    return find_conda() is not None


def _create_cmd(env_name: str, tools: List[str]) -> List[str]:
    # Use the resolved executable: with `conda init`, `conda` is a shell
    # function and is not on a subprocess PATH.
    exe = find_conda() or "conda"
    return [exe, "create", "-n", env_name, "-y", *CHANNELS, *tools]


def build_commands(base: str, groups: List[str], single: bool) -> List[Tuple[str, List[str]]]:
    """Return a list of (env_name, argv) for the requested provisioning plan."""
    unknown = [g for g in groups if g not in ENV_GROUPS]
    if unknown:
        raise CondaSetupError(
            f"Unknown env group(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(ENV_GROUPS)}."
        )
    if single:
        tools = sorted({t for g in groups for t in ENV_GROUPS[g]})
        return [(base, _create_cmd(base, tools))]
    return [(f"{base}_{g}", _create_cmd(f"{base}_{g}", ENV_GROUPS[g])) for g in groups]


def create_environments(base: str, groups: List[str] = None, single: bool = False,
                        dry_run: bool = False) -> List[str]:
    """Create the conda environments. Returns the list of environment names.

    With ``dry_run=True`` the commands are printed but not executed.
    """
    if not base:
        raise CondaSetupError("An environment base name is required.")
    groups = groups or list(ENV_GROUPS)
    plan = build_commands(base, groups, single)

    if not dry_run and not conda_available():
        raise CondaSetupError(
            "conda was not found. Install Miniconda/Mambaforge first, "
            "or re-run with --dry-run to preview the commands."
        )

    created: List[str] = []
    for env_name, argv in plan:
        print(f"+ {' '.join(argv)}")
        if dry_run:
            created.append(env_name)
            continue
        rc = subprocess.run(argv).returncode
        if rc != 0:
            raise CondaSetupError(
                f"Failed to create environment '{env_name}' (conda exit {rc})."
            )
        created.append(env_name)
    return created
