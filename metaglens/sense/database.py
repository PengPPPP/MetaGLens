"""Database registry, discovery, and validation (read-only).

Answers three questions without ever mutating a database directory:

  * ``required_databases(cfg)`` — which reference databases *this* run needs,
    derived from the route + config switches (unused ones are not flagged).
  * ``discover(name, cfg)`` — where a database is, following a fixed priority:
    explicit config path -> environment variable -> filesystem scan -> default.
  * ``validate(name, path)`` — is a given directory really this database, and
    what version, judged by a sentinel file. Strictly read-only.

Pure stdlib. Aligns with the existing ``Config`` fields and the default
sub-directory names used by ``render._db`` so the two never disagree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class DbSpec:
    name: str
    env_var: str
    sentinel: str                     # file that proves the dir is this database
    default_subdir: str               # under resolved_db_dir()
    size_hint_gb: float
    download_hint: str                # text only; never executed
    # ("relative/file", "KEY") — read KEY=value from that file for the version.
    version_file: Optional[Tuple[str, str]] = None
    # glob patterns (relative to each scan root) to locate the db directory.
    scan_names: Tuple[str, ...] = field(default_factory=tuple)


# Sentinels and version files are calibrated against real installations
# (e.g. GTDB-Tk r232 carries taxonomy/gtdb_taxonomy.tsv and
# metadata/metadata.txt with a VERSION_DATA= line). size_hint_gb are
# order-of-magnitude figures for preflight, not exact.
REGISTRY: Dict[str, DbSpec] = {
    "checkm2": DbSpec(
        name="checkm2",
        env_var="CHECKM2DB",
        sentinel="CheckM2_database/uniref100.KO.1.dmnd",
        default_subdir="checkm2",
        size_hint_gb=3.0,
        download_hint="checkm2 database --download --path <dir>",
        scan_names=("checkm2*", "CheckM2*", "*checkm2*"),
    ),
    "gtdbtk": DbSpec(
        name="gtdbtk",
        env_var="GTDBTK_DATA_PATH",
        sentinel="taxonomy/gtdb_taxonomy.tsv",
        default_subdir="gtdbtk",
        size_hint_gb=110.0,
        download_hint=(
            "download the GTDB-Tk reference package from "
            "https://ecogenomics.github.io/GTDBTk/ and extract it to <dir>"
        ),
        version_file=("metadata/metadata.txt", "VERSION_DATA"),
        scan_names=("gtdbtk*", "gtdbtk*/*", "*gtdb*", "*gtdb*/*", "release*"),
    ),
    "kraken2": DbSpec(
        name="kraken2",
        env_var="KRAKEN2_DB_PATH",
        sentinel="hash.k2d",
        default_subdir="kraken2_standard",
        size_hint_gb=100.0,
        download_hint=(
            "download a prebuilt Kraken2 index from "
            "https://benlangmead.github.io/aws-indexes/k2 (pick a variant that "
            "fits available RAM), or build one with kraken2-build --standard"
        ),
        scan_names=("kraken2*", "*kraken*", "k2_*"),
    ),
    "eggnog": DbSpec(
        name="eggnog",
        env_var="EGGNOG_DATA_DIR",
        sentinel="eggnog.db",
        default_subdir="eggnog",
        size_hint_gb=50.0,
        download_hint="download_eggnog_data.py -y --data_dir <dir>",
        scan_names=("eggnog*", "*eggnog*"),
    ),
}


@dataclass
class DbStatus:
    name: str
    state: str                 # "ready" | "wrong_path" | "missing"
    path: str                  # resolved path (empty when missing)
    version: Optional[str]     # read from version_file when available
    source: Optional[str]      # "config" | "env" | "scan" | "default" | None
    detail: str                # human explanation / download hint


# --------------------------------------------------------------------------- #
# Validation (read-only)
# --------------------------------------------------------------------------- #
def _read_version(spec: DbSpec, root: Path) -> Optional[str]:
    if not spec.version_file:
        return None
    rel, key = spec.version_file
    vf = root / rel
    try:
        with open(vf, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


def _check(spec: DbSpec, path: str) -> Tuple[bool, Optional[str], str]:
    """(ok, version, detail). Read-only; never writes to the directory."""
    p = Path(path).expanduser()
    if not p.is_dir():
        return False, None, f"not a directory: {path}"
    if not (p / spec.sentinel).exists():
        return False, None, (
            f"directory exists but does not look like the {spec.name} database "
            f"(expected to find '{spec.sentinel}')"
        )
    version = _read_version(spec, p)
    detail = "ready" + (f" (version {version})" if version else "")
    return True, version, detail


def validate(name: str, path: str) -> Tuple[bool, str]:
    """Public: is ``path`` a valid ``name`` database? Read-only."""
    spec = REGISTRY[name]
    ok, _version, detail = _check(spec, path)
    return ok, detail


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def _configured_path(name: str, cfg) -> str:
    """Explicit path from config, mirroring render._step_overrides mapping."""
    if name == "checkm2":
        return cfg.checkm2_db or ""
    if name == "gtdbtk":
        return cfg.taxonomy_db if cfg.taxonomy_tool == "gtdbtk" else ""
    if name == "kraken2":
        # taxonomy_db when kraken2 is the taxonomy tool; else the contig kraken2 db.
        if cfg.taxonomy_tool == "kraken2" and cfg.taxonomy_db:
            return cfg.taxonomy_db
        return cfg.kraken2_db or ""
    if name == "eggnog":
        return cfg.eggnog_db or ""
    return ""


def _default_scan_roots(cfg) -> List[Path]:
    home = Path.home()
    roots = [
        home,
        home / "databases",
        Path(cfg.resolved_db_dir()),
        Path("/shared"),
        Path("/opt"),
        Path("/data"),
    ]
    seen: set = set()
    unique: List[Path] = []
    for r in roots:
        rs = str(r)
        if rs not in seen:
            seen.add(rs)
            unique.append(r)
    return unique


def _scan_candidates(spec: DbSpec, roots: List[Path]) -> List[Path]:
    cands: List[Path] = []
    seen: set = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        probes = [root]
        for pattern in spec.scan_names:
            try:
                probes.extend(sorted(root.glob(pattern)))
            except OSError:
                continue
        for c in probes:
            cs = str(c)
            if cs in seen:
                continue
            seen.add(cs)
            try:
                if c.is_dir():
                    cands.append(c)
            except OSError:
                continue
    return cands


def discover(name: str, cfg, scan_roots: Optional[List[Path]] = None) -> DbStatus:
    """Locate database ``name`` for ``cfg`` by the fixed priority chain."""
    spec = REGISTRY[name]

    # 1. explicit config path
    explicit = _configured_path(name, cfg)
    if explicit:
        ok, version, detail = _check(spec, explicit)
        return DbStatus(name, "ready" if ok else "wrong_path",
                        str(Path(explicit).expanduser()), version, "config", detail)

    # 2. environment variable
    env_val = os.environ.get(spec.env_var, "").strip()
    if env_val:
        ok, version, detail = _check(spec, env_val)
        return DbStatus(name, "ready" if ok else "wrong_path",
                        str(Path(env_val).expanduser()), version, "env", detail)

    # 3. filesystem scan
    for cand in _scan_candidates(spec, scan_roots or _default_scan_roots(cfg)):
        ok, version, detail = _check(spec, str(cand))
        if ok:
            return DbStatus(name, "ready", str(cand), version, "scan", detail)

    # 4. default location
    default = Path(cfg.resolved_db_dir()) / spec.default_subdir
    ok, version, detail = _check(spec, str(default))
    if ok:
        return DbStatus(name, "ready", str(default), version, "default", detail)

    return DbStatus(
        name, "missing", "", None, None,
        f"not found (~{spec.size_hint_gb:.0f} GB). Get it: {spec.download_hint}",
    )


# --------------------------------------------------------------------------- #
# Requirement derivation (shared base for doctor / plan / web)
# --------------------------------------------------------------------------- #
def required_databases(cfg) -> Dict[str, str]:
    """Databases this run actually needs, keyed name -> reason.

    Derived from the resolved route steps + config switches. Databases the
    selected route never touches are omitted (not reported as missing).
    """
    try:
        steps = set(cfg.route.steps)
    except Exception:
        steps = set()
    needed: Dict[str, str] = {}

    if "05_checkm" in steps:
        needed["checkm2"] = "05_checkm assesses MAG quality with CheckM2"

    if "07_taxonomy" in steps:
        if cfg.taxonomy_tool == "gtdbtk":
            needed["gtdbtk"] = "07_taxonomy classifies MAGs with GTDB-Tk"
        elif cfg.taxonomy_tool == "kraken2":
            needed["kraken2"] = "07_taxonomy profiles reads with Kraken2"

    if "09_contig" in steps and cfg.contig_taxonomy == "kraken2":
        needed["kraken2"] = (needed.get("kraken2", "")
                             + "; 09_contig classifies contigs with Kraken2").lstrip("; ")

    if cfg.use_eggnog and ("08_annotation" in steps or "09_contig" in steps):
        where = []
        if "08_annotation" in steps:
            where.append("08_annotation")
        if "09_contig" in steps:
            where.append("09_contig")
        needed["eggnog"] = f"{'/'.join(where)} functional annotation with eggNOG-mapper"

    return needed
