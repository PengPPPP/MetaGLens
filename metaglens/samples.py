"""Paired-end sample discovery and manifest generation.

Scans a raw-data directory for FASTQ files, pairs R1/R2 mates using common
naming conventions, and writes a validated ``samples.tsv`` manifest with the
columns ``sample_id``, ``r1``, ``r2``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Match, Optional, Tuple

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


def _list_fastqs(raw_dir: Path) -> List[Path]:
    files: List[Path] = []
    for entry in sorted(raw_dir.iterdir()):
        if entry.is_file() and entry.name.endswith(_FASTQ_SUFFIXES):
            files.append(entry)
    return files


def discover(raw_data_dir: str) -> Tuple[List[Sample], str]:
    """Discover paired samples. Returns (samples, detected_pattern_label)."""
    raw_dir = Path(raw_data_dir)
    if not raw_dir.is_dir():
        raise SampleDiscoveryError(f"raw_data_dir does not exist: {raw_data_dir}")

    fastqs = _list_fastqs(raw_dir)
    if not fastqs:
        raise SampleDiscoveryError(f"No FASTQ files found under {raw_data_dir}.")

    names = {p.name: p for p in fastqs}
    for label, r1_re, r2_of in _PATTERNS:
        samples: List[Sample] = []
        used: set = set()
        seen_ids: set = set()
        ok = True
        for name in sorted(names):
            m = r1_re.match(name)
            if not m:
                continue
            sample_id = m.group("id")
            r2_name = r2_of(m)
            if r2_name not in names:
                continue
            if sample_id in seen_ids:
                ok = False
                break
            r1_path = names[name].resolve()
            r2_path = names[r2_name].resolve()
            samples.append(Sample(sample_id, str(r1_path), str(r2_path)))
            used.add(name)
            used.add(r2_name)
            seen_ids.add(sample_id)
        if samples and ok:
            _validate(samples)
            return samples, label

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
