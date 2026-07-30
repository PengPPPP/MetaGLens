"""Semantic product validation.

The reliability gap this closes: the Python side used to trust the status flag a
stage script wrote, never re-checking what the stage actually produced. §7-8 is
the cautionary tale — stage 10 wrote a header-only table, exited 0, and was
marked ``completed``.

So validation here is deliberately **semantic**, not "the file exists and is
non-empty": a header line alone makes a file non-empty. Every check states a
decidable lower bound — at least one data row, at least one FASTA record, at
least one bin — and each stage's expectations are declared explicitly.

Pure stdlib; every check is read-only.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

_FASTA_SUFFIXES = (".fa", ".fna", ".fasta")


@dataclass
class CheckResult:
    ok: bool
    detail: str


@dataclass
class ValidationReport:
    stage: str
    ok: bool
    checks: List[Dict[str, object]] = field(default_factory=list)

    @property
    def failures(self) -> List[str]:
        return [str(c["detail"]) for c in self.checks if not c["ok"]]

    def as_dict(self) -> Dict[str, object]:
        return {"stage": self.stage, "ok": self.ok, "checks": self.checks}


# --------------------------------------------------------------------------- #
# Primitive predicates
# --------------------------------------------------------------------------- #
def _data_rows(path: Path, has_header: bool = True,
               comment_prefixes: tuple = ("#",)) -> int:
    """Count rows that carry data, excluding the header and comment lines."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = [ln for ln in handle.read().splitlines() if ln.strip()]
    except OSError:
        return -1
    lines = [ln for ln in lines if not ln.startswith(comment_prefixes)]
    if has_header and lines:
        lines = lines[1:]
    return len(lines)


def table_has_rows(path: Path, minimum: int = 1,
                   has_header: bool = True) -> CheckResult:
    """A table must carry data rows — a header alone is not success."""
    if not path.is_file():
        return CheckResult(False, f"missing table: {path}")
    rows = _data_rows(path, has_header=has_header)
    if rows < 0:
        return CheckResult(False, f"unreadable table: {path}")
    if rows < minimum:
        return CheckResult(
            False,
            f"{path.name} has {rows} data row(s), expected >= {minimum} "
            f"(a header line alone is not a result)",
        )
    return CheckResult(True, f"{path.name}: {rows} data row(s)")


def fasta_has_records(path: Path, minimum: int = 1) -> CheckResult:
    if not path.is_file():
        return CheckResult(False, f"missing FASTA: {path}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return CheckResult(False, f"unreadable FASTA: {path}")
    count = text.count(">")
    if count < minimum:
        return CheckResult(
            False, f"{path.name} holds {count} sequence(s), expected >= {minimum}")
    return CheckResult(True, f"{path.name}: {count} sequence(s)")


def dir_has_fastas(directory: Path, minimum: int = 1) -> CheckResult:
    if not directory.is_dir():
        return CheckResult(False, f"missing directory: {directory}")
    found = [p for p in sorted(directory.iterdir())
             if p.is_file() and p.name.endswith(_FASTA_SUFFIXES)]
    if len(found) < minimum:
        return CheckResult(
            False,
            f"{directory.name}/ holds {len(found)} FASTA file(s), "
            f"expected >= {minimum}",
        )
    return CheckResult(True, f"{directory.name}/: {len(found)} FASTA file(s)")


def file_non_empty(path: Path) -> CheckResult:
    if not path.is_file():
        return CheckResult(False, f"missing file: {path}")
    try:
        size = path.stat().st_size
    except OSError:
        return CheckResult(False, f"unreadable file: {path}")
    if size <= 0:
        return CheckResult(False, f"{path.name} is empty")
    return CheckResult(True, f"{path.name}: {size} bytes")


def gzip_has_reads(path: Path) -> CheckResult:
    """A gzipped FASTQ must contain at least one record, not just be non-empty."""
    if not path.is_file():
        return CheckResult(False, f"missing reads: {path}")
    try:
        with gzip.open(path, "rt", errors="replace") as handle:
            first = handle.readline()
    except OSError:
        return CheckResult(False, f"unreadable gzip: {path}")
    except Exception:
        return CheckResult(False, f"corrupt gzip: {path}")
    if not first.startswith("@"):
        return CheckResult(False, f"{path.name} holds no FASTQ records")
    return CheckResult(True, f"{path.name}: reads present")


# --------------------------------------------------------------------------- #
# Per-stage expectations
# --------------------------------------------------------------------------- #
def _qc(results: Path, samples: List[str]) -> List[CheckResult]:
    out = []
    for sample in samples:
        for mate in (1, 2):
            out.append(gzip_has_reads(
                results / "01_qc" / f"{sample}_clean_R{mate}.fastq.gz"))
    return out


def _assembly(results: Path, samples: List[str]) -> List[CheckResult]:
    base = results / "02_assembly"
    units = samples if (base / (samples[0] if samples else "")).is_dir() \
        else ["coassembly"]
    out = []
    for unit in units:
        candidates = [base / unit / "final.contigs_filtered.fa",
                      base / unit / "final.contigs.fa",
                      base / unit / "contigs.fasta"]
        found = next((c for c in candidates if c.is_file()), None)
        if found is None:
            out.append(CheckResult(False, f"no contigs FASTA for '{unit}'"))
        else:
            out.append(fasta_has_records(found))
    return out


def _mapping(results: Path, samples: List[str]) -> List[CheckResult]:
    out = []
    for sample in samples:
        out.append(file_non_empty(
            results / "03_mapping" / sample / f"{sample}.sorted.bam"))
    return out


def _binning(results: Path, samples: List[str]) -> List[CheckResult]:
    return [dir_has_fastas(results / "04_binning" / "all_bins")]


def _checkm(results: Path, samples: List[str]) -> List[CheckResult]:
    return [table_has_rows(results / "05_checkm" / "quality_report.tsv")]


def _derep(results: Path, samples: List[str]) -> List[CheckResult]:
    return [dir_has_fastas(results / "06_derep" / "dereplicated_genomes")]


def _taxonomy(results: Path, samples: List[str]) -> List[CheckResult]:
    gtdb = results / "07_taxonomy" / "gtdbtk"
    if gtdb.is_dir():
        summaries = sorted(gtdb.glob("gtdbtk.*.summary.tsv"))
        if not summaries:
            return [CheckResult(False, "no GTDB-Tk summary table produced")]
        return [table_has_rows(s) for s in summaries]
    kraken = results / "07_taxonomy" / "kraken2"
    if kraken.is_dir():
        reports = sorted(kraken.glob("*_report.txt"))
        if not reports:
            return [CheckResult(False, "no Kraken2 report produced")]
        return [table_has_rows(r, has_header=False) for r in reports]
    return [CheckResult(False, "07_taxonomy produced neither GTDB-Tk nor Kraken2 output")]


def _mag_abundance(results: Path, samples: List[str]) -> List[CheckResult]:
    return [table_has_rows(
        results / "mag_abundance" / "mag_relative_abundance.tsv")]


def _annotation(results: Path, samples: List[str]) -> List[CheckResult]:
    base = results / "08_annotation"
    prokka = base / "prokka"
    eggnog = base / "eggnog"
    out: List[CheckResult] = []
    if prokka.is_dir():
        faas = sorted(prokka.glob("*/*.faa"))
        if not faas:
            out.append(CheckResult(False, "Prokka produced no protein FASTA"))
        else:
            out.append(CheckResult(True, f"prokka: {len(faas)} annotated MAG(s)"))
    annot = eggnog / "eggnog_results.emapper.annotations"
    if eggnog.is_dir() and annot.is_file():
        out.append(table_has_rows(annot, has_header=False))
    if not out:
        out.append(CheckResult(False, "08_annotation produced no annotations"))
    return out


def _contig(results: Path, samples: List[str]) -> List[CheckResult]:
    genes = results / "09_contig" / "genes"
    out: List[CheckResult] = []
    faas = sorted(genes.glob("*_proteins.faa")) if genes.is_dir() else []
    if not faas:
        out.append(CheckResult(False, "09_contig produced no predicted proteins"))
    else:
        out.extend(fasta_has_records(f) for f in faas)
        gffs = sorted(genes.glob("*_genes.gff"))
        if not gffs:
            out.append(CheckResult(False, "09_contig produced no GFF"))
        else:
            out.extend(file_non_empty(g) for g in gffs)
    return out


def _community(results: Path, samples: List[str]) -> List[CheckResult]:
    return [table_has_rows(results / "10_community" / "community_matrix.tsv")]


def _delivery(results: Path, samples: List[str]) -> List[CheckResult]:
    delivery = results / "delivery"
    if not delivery.is_dir():
        return [CheckResult(False, "delivery/ was not created")]
    out = [CheckResult(True, "delivery/ present")]
    dictionary = delivery / "DATA_DICTIONARY.md"
    out.append(file_non_empty(dictionary) if dictionary.exists()
               else CheckResult(False, "delivery/DATA_DICTIONARY.md missing"))
    populated = [p for p in delivery.iterdir() if p.is_dir() and any(p.iterdir())]
    if not populated:
        out.append(CheckResult(False, "delivery/ has no populated subdirectory"))
    else:
        out.append(CheckResult(
            True, f"delivery/: {len(populated)} populated subdirectory(ies)"))
    return out


def _setup(results: Path, samples: List[str]) -> List[CheckResult]:
    return [
        table_has_rows(results / "samples.tsv"),
        file_non_empty(results / "pipeline_status.json"),
    ]


# step_id -> checker. Stages absent from this map are not product-validated.
VALIDATORS: Dict[str, Callable[[Path, List[str]], List[CheckResult]]] = {
    "00_setup": _setup,
    "01_qc": _qc,
    "02_assembly": _assembly,
    "03_mapping": _mapping,
    "04_binning": _binning,
    "05_checkm": _checkm,
    "06_derep": _derep,
    "07_taxonomy": _taxonomy,
    "mag_abundance": _mag_abundance,
    "08_annotation": _annotation,
    "09_contig": _contig,
    "10_community": _community,
    "11_delivery": _delivery,
}


def _sample_ids(results: Path) -> List[str]:
    manifest = results / "samples.tsv"
    ids: List[str] = []
    try:
        with open(manifest, "r", encoding="utf-8") as handle:
            for i, line in enumerate(handle):
                parts = line.rstrip("\n").split("\t")
                if i == 0 and parts and parts[0] == "sample_id":
                    continue
                if parts and parts[0]:
                    ids.append(parts[0])
    except OSError:
        return []
    return ids


def validate_stage(results_dir: Path, step_id: str,
                   samples: Optional[List[str]] = None) -> ValidationReport:
    """Validate one stage's products. Unknown stages pass vacuously."""
    results = Path(results_dir)
    checker = VALIDATORS.get(step_id)
    if checker is None:
        return ValidationReport(step_id, True,
                                [{"ok": True, "detail": "no product contract"}])
    if samples is None:
        samples = _sample_ids(results)
    try:
        checks = checker(results, samples)
    except Exception as exc:  # a broken check must not mask the stage result
        return ValidationReport(step_id, False,
                                [{"ok": False,
                                  "detail": f"validation error: {exc}"}])
    if not checks:
        checks = [CheckResult(True, "nothing to validate")]
    return ValidationReport(
        step_id,
        all(c.ok for c in checks),
        [{"ok": c.ok, "detail": c.detail} for c in checks],
    )
