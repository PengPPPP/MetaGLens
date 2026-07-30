"""Paired-end sample discovery and manifest generation.

Scans a raw-data directory for FASTQ files, pairs R1/R2 mates using common
naming conventions, and writes a validated ``samples.tsv`` manifest with the
columns ``sample_id``, ``r1``, ``r2``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Match, NamedTuple, Optional, Tuple

_FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")

# Ordered R1/R2 conventions. Each entry: (label, r1_regex, r2_marker_builder).
# The regex must capture the sample id in group 1 and match an R1 file; the
# builder produces the expected R2 filename from an R1 filename.
_PATTERNS: List[Tuple[str, "re.Pattern[str]", Callable[["Match[str]"], str]]] = [
    ("_R1_001/_R2_001",
     re.compile(r"^(?P<id>.+?)_R1_001(?P<suf>\.f(?:ast)?q(?:\.gz)?)$"),
     lambda m: f"{m.group('id')}_R2_001{m.group('suf')}"),
    ("_R1/_R2",
     re.compile(r"^(?P<id>.+?)_R1(?P<suf>\.f(?:ast)?q(?:\.gz)?)$"),
     lambda m: f"{m.group('id')}_R2{m.group('suf')}"),
    ("_1/_2",
     re.compile(r"^(?P<id>.+?)_1(?P<suf>\.f(?:ast)?q(?:\.gz)?)$"),
     lambda m: f"{m.group('id')}_2{m.group('suf')}"),
    (".1/.2",
     re.compile(r"^(?P<id>.+?)\.1(?P<suf>\.f(?:ast)?q(?:\.gz)?)$"),
     lambda m: f"{m.group('id')}.2{m.group('suf')}"),
]

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass
class Sample:
    sample_id: str
    r1: str
    r2: str


class SampleDiscoveryError(Exception):
    pass


# Maximum directory levels descended below raw_data_dir.
_MAX_DEPTH = 3


class Discovery(NamedTuple):
    """Result of :func:`discover`.

    ``layout`` and ``id_source`` exist so the wizard / web UI can show *how*
    the samples were determined rather than presenting a bare list.
    """

    samples: List[Sample]
    pattern: str          # naming convention label, e.g. "_R1/_R2"
    layout: str           # "flat" | "nested"
    id_source: str        # "filename" | "dirname"


def _walk_fastqs(raw_dir: Path, max_depth: int = _MAX_DEPTH) -> List[Path]:
    """Recursively collect FASTQ files, depth-limited and symlink-loop safe.

    Hidden directories are skipped. Directories are de-duplicated by resolved
    path, so a symlink pointing back at an ancestor cannot cause a loop.
    """
    found: List[Path] = []
    visited: set = set()
    stack: List[Tuple[Path, int]] = [(raw_dir, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            key = str(directory.resolve())
        except OSError:
            continue
        if key in visited:
            continue
        visited.add(key)
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if entry.name.startswith("."):
                    continue
                if depth < max_depth:
                    stack.append((entry, depth + 1))
            elif entry.is_file() and entry.name.endswith(_FASTQ_SUFFIXES):
                found.append(entry)
    return sorted(found)


def _pair_in_group(names: Dict[str, Path], r1_re, r2_of):
    """Pair R1/R2 **within one directory**. Returns None if ids collide here.

    Returning None mirrors the previous flat behaviour: a duplicate id means
    this naming convention does not apply, so the caller tries the next one.
    """
    pairs: List[Tuple[str, Path, Path]] = []
    seen: set = set()
    for name in sorted(names):
        match = r1_re.match(name)
        if not match:
            continue
        file_id = match.group("id")
        r2_name = r2_of(match)
        if r2_name not in names:
            continue
        if file_id in seen:
            return None
        seen.add(file_id)
        pairs.append((file_id, names[name], names[r2_name]))
    return pairs


def discover(raw_data_dir: str, max_depth: int = _MAX_DEPTH) -> Discovery:
    """Discover paired samples, including nested per-sample sub-directories."""
    raw_dir = Path(raw_data_dir)
    if not raw_dir.is_dir():
        raise SampleDiscoveryError(f"raw_data_dir does not exist: {raw_data_dir}")

    fastqs = _walk_fastqs(raw_dir, max_depth)
    if not fastqs:
        raise SampleDiscoveryError(f"No FASTQ files found under {raw_data_dir}.")

    # Group by resolved parent directory. Pairing happens ONLY inside a group,
    # which structurally prevents mating an R1 from one directory with an R2
    # from another (the worst failure mode: samples silently swapped).
    groups: Dict[str, Dict[str, Path]] = {}
    group_dir: Dict[str, Path] = {}
    for path in fastqs:
        try:
            gkey = str(path.parent.resolve())
        except OSError:
            gkey = str(path.parent)
        groups.setdefault(gkey, {})[path.name] = path
        group_dir.setdefault(gkey, path.parent)

    try:
        raw_key = str(raw_dir.resolve())
    except OSError:
        raw_key = str(raw_dir)
    layout = "flat" if set(groups) <= {raw_key} else "nested"

    for label, r1_re, r2_of in _PATTERNS:
        # (file_id, dir_name, r1, r2) across every group
        collected: List[Tuple[str, str, Path, Path]] = []
        convention_ok = True
        for gkey in sorted(groups):
            paired = _pair_in_group(groups[gkey], r1_re, r2_of)
            if paired is None:
                convention_ok = False
                break
            dir_name = group_dir[gkey].name
            for file_id, r1, r2 in paired:
                collected.append((file_id, dir_name, r1, r2))
        if not convention_ok or not collected:
            continue

        file_ids = [c[0] for c in collected]
        dir_names = [c[1] for c in collected]
        if len(set(file_ids)) == len(file_ids):
            ids, id_source = file_ids, "filename"
        elif len(set(dir_names)) == len(dir_names):
            # Layout 2: generic filenames inside per-sample directories.
            ids, id_source = dir_names, "dirname"
        else:
            raise SampleDiscoveryError(
                "Sample ids are ambiguous: file names collide across directories "
                f"({sorted(set(i for i in file_ids if file_ids.count(i) > 1))}) and "
                "the parent directory names are not unique either. Provide an "
                "explicit samples.tsv manifest (columns: sample_id, r1, r2)."
            )

        samples = [
            Sample(sample_id, str(r1.resolve()), str(r2.resolve()))
            for sample_id, (_fid, _dir, r1, r2) in zip(ids, collected)
        ]
        _validate(samples)
        return Discovery(samples, label, layout, id_source)

    raise SampleDiscoveryError(
        "Could not pair any FASTQ files. Supported conventions: "
        "_R1_001/_R2_001, _R1/_R2, _1/_2, .1/.2. "
        "Provide a samples.tsv manifest instead."
    )


def _validate(samples: List[Sample]) -> None:
    ids = [s.sample_id for s in samples]
    if len(ids) != len(set(ids)):
        raise SampleDiscoveryError("Duplicate sample identifiers detected.")
    seen_files: Dict[str, str] = {}
    for s in samples:
        if not _SAFE_ID.match(s.sample_id):
            raise SampleDiscoveryError(f"Unsafe sample id: {s.sample_id}")
        for f in (s.r1, s.r2):
            if not Path(f).is_file():
                raise SampleDiscoveryError(f"File not readable: {f}")
            if f in seen_files:
                raise SampleDiscoveryError(
                    f"File {f} assigned to both {seen_files[f]} and {s.sample_id}."
                )
            seen_files[f] = s.sample_id


def write_manifest(samples: List[Sample], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("sample_id\tr1\tr2\n")
        for s in samples:
            handle.write(f"{s.sample_id}\t{s.r1}\t{s.r2}\n")


def read_manifest(path: str) -> List[Sample]:
    samples: List[Sample] = []
    with open(path, "r", encoding="utf-8") as handle:
        for i, line in enumerate(handle):
            parts = line.rstrip("\n").split("\t")
            if i == 0 and parts and parts[0] == "sample_id":
                continue
            if len(parts) != 3 or not parts[0]:
                continue
            samples.append(Sample(parts[0], parts[1], parts[2]))
    if not samples:
        raise SampleDiscoveryError(f"No samples parsed from manifest {path}.")
    _validate(samples)
    return samples
