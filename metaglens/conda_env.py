"""Conda environment discovery and inspection.

Used by the setup wizard (and, later, ``metaglens doctor``) to report which
pipeline tools are installed in a candidate environment, without ever mutating
an environment.

Two design points matter here:

* **Locating conda is not just ``which conda``.** With a standard ``conda init``
  setup, ``conda`` is a *shell function*, so it is absent from a
  non-interactive subprocess PATH even though conda is installed. Falling back
  to ``$CONDA_EXE``/``$CONDA_PREFIX`` and the common install directories keeps
  environment detection working from cron, systemd, or a fresh non-login shell.

* **Failure modes stay distinguishable.** "conda cannot be run"
  (:class:`CondaUnavailable`) and "that environment does not exist"
  (:class:`EnvNotFound`) are separate errors. Collapsing both into an empty
  package dict — as an earlier version did — made a mistyped environment name
  look like a real environment with every tool missing, which in turn made the
  wizard offer to install 18 packages into an environment that did not exist.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# Conda package names for every tool the pipeline may use.
PIPELINE_TOOLS: List[str] = [
    "fastp", "megahit", "spades", "bowtie2", "bwa-mem2", "samtools", "seqkit",
    "metabat2", "maxbin2", "concoct", "das_tool",
    "checkm2", "drep", "gtdbtk", "kraken2", "bracken", "prokka", "eggnog-mapper",
]

# Distribution directory names to probe under $HOME and /opt, in order.
_DISTRO_DIRS = (
    "miniconda3", "anaconda3", "miniforge3", "mambaforge", "conda", "miniconda",
    "anaconda",
)


class CondaError(Exception):
    """Base class for conda interaction problems."""


class CondaUnavailable(CondaError):
    """conda could not be located or could not be executed."""


class EnvNotFound(CondaError):
    """The requested conda environment does not exist."""


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def find_conda() -> Optional[str]:
    """Return a path to the conda executable, or ``None``.

    Resolution order:

    1. ``conda`` on ``PATH``
    2. ``$CONDA_EXE`` (exported by ``conda init``)
    3. ``$CONDA_PREFIX``/bin/conda, plus the base install two levels up
       (so this works while a child environment is active)
    4. common install directories under ``$HOME`` and ``/opt``
    """
    on_path = shutil.which("conda")
    if on_path:
        return on_path

    exe = os.environ.get("CONDA_EXE", "")
    if exe and Path(exe).is_file():
        return exe

    prefix = os.environ.get("CONDA_PREFIX", "")
    if prefix:
        base = Path(prefix)
        # <prefix>/bin/conda covers base; <prefix>/../../bin/conda covers
        # "<base>/envs/<name>" when a child environment is active.
        for candidate in (base / "bin" / "conda",
                          base.parent.parent / "bin" / "conda"):
            if candidate.is_file():
                return str(candidate)

    for root in (Path.home(), Path("/opt")):
        for name in _DISTRO_DIRS:
            candidate = root / name / "bin" / "conda"
            if candidate.is_file():
                return str(candidate)
    return None


def conda_available() -> bool:
    """True when a conda executable can be located."""
    return find_conda() is not None


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #
def _env_selector(env: str) -> List[str]:
    """conda takes ``-p`` for a prefix path and ``-n`` for a named environment."""
    if os.sep in env:
        return ["-p", env]
    return ["-n", env]


def _run_conda(args: Sequence[str], timeout: int = 60) -> subprocess.CompletedProcess:
    exe = find_conda()
    if exe is None:
        raise CondaUnavailable(
            "conda executable not found. Looked on PATH, in $CONDA_EXE and "
            "$CONDA_PREFIX, and in the usual install directories under "
            "$HOME and /opt."
        )
    try:
        return subprocess.run(
            [exe, *args], capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CondaUnavailable(f"could not run '{exe}': {exc}") from exc


def _env_name(prefix: str) -> str:
    """Display name for an environment prefix.

    conda reports the base environment as the installation root (e.g.
    ``/home/u/miniconda3``), whose directory name would otherwise be shown as
    ``miniconda3``. Named environments live under ``<root>/envs/<name>``.
    """
    path = Path(prefix)
    if path.parent.name == "envs":
        return path.name
    return "base"


def list_envs() -> List[str]:
    """Environment names, best effort. Empty list when conda is unusable."""
    try:
        proc = _run_conda(["env", "list", "--json"], timeout=30)
    except CondaUnavailable:
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return []
    return [name for name in (_env_name(p) for p in data.get("envs", [])) if name]


def env_prefixes() -> Dict[str, str]:
    """Map environment name -> prefix path, best effort (empty when unusable).

    Lets callers check that a tool's executable really exists under
    ``<prefix>/bin`` — ``conda list`` reporting a package is not the same thing
    as the command being runnable.
    """
    try:
        proc = _run_conda(["env", "list", "--json"], timeout=30)
    except CondaUnavailable:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {}
    out: Dict[str, str] = {}
    for prefix in data.get("envs", []):
        name = _env_name(prefix)
        if name:
            out.setdefault(name, prefix)
    return out


def env_exists(env: str) -> bool:
    """True when ``env`` names an existing environment or is an env prefix path."""
    if not env or env == "none":
        return False
    if os.sep in env:
        return (Path(env) / "conda-meta").is_dir()
    return env in list_envs()


def installed_packages(env: str) -> Dict[str, str]:
    """Map package name -> version for ``env``.

    An empty dict unambiguously means "the environment exists but has no
    packages". Problems are raised instead of being swallowed:

    :raises CondaUnavailable: conda could not be located or executed.
    :raises EnvNotFound: the environment does not exist.
    :raises CondaError: conda ran but its output could not be used.
    """
    proc = _run_conda(["list", "--json", *_env_selector(env)])
    if proc.returncode != 0:
        # Only now pay for the extra lookup, to report the precise cause.
        if not env_exists(env):
            raise EnvNotFound(f"conda environment not found: {env!r}")
        raise CondaError(
            f"'conda list' failed for {env!r} (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip() or 'no stderr'}"
        )
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise CondaError(f"could not parse 'conda list' output for {env!r}: {exc}") from exc
    return {
        pkg["name"]: pkg.get("version", "")
        for pkg in payload
        if isinstance(pkg, dict) and "name" in pkg
    }


def missing_tools(env: str, tools: Optional[Sequence[str]] = None) -> List[str]:
    """Tools from ``tools`` that are absent from ``env``.

    Propagates :class:`CondaError` subclasses; callers must decide how to
    surface "conda unusable" and "environment missing", which are not the same
    thing as "nothing installed".
    """
    tools = tools or PIPELINE_TOOLS
    present = installed_packages(env)
    return [t for t in tools if t not in present]


def inventory(env: str, tools: Optional[Sequence[str]] = None) -> Dict[str, str]:
    """Return ``{tool: version-or-'missing'}`` for an installed/missing view."""
    tools = tools or PIPELINE_TOOLS
    present = installed_packages(env)
    return {t: present.get(t, "missing") for t in tools}
