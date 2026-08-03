"""Deterministic, realistic-looking data for the stub toolchain.

The demo must be reproducible, so every "random" value comes from a fixed seed.
The data is deliberately realistic — a plausible gut-microbiome community with
recognisable genera, varied MAG quality and abundance — but it is **synthetic**.
Nothing here is a real analysis result; the showcase says so.

A single module owns the generators so every stub draws from the same, consistent
world: a MAG's species and its abundance are derived from its name by the same
functions everywhere, so the GTDB-Tk summary, the coverage table and the
community matrix all agree.

Installed alongside the stubs as ``_mg_stubdata.py`` and invoked as
``python3 "$MG_STUBDATA" <command> ...``.
"""

from __future__ import annotations

import random
import sys
import zlib

SEED = 20260731

# A plausible gut community: 10 species across 8 genera, with realistic GTDB
# lineages and relative-abundance weights (a couple of dominants + a long tail).
LINEAGES = [
    ("Bacteroides vulgatus",
     "d__Bacteria;p__Bacteroidota;c__Bacteroidia;o__Bacteroidales;f__Bacteroidaceae;g__Bacteroides;s__Bacteroides vulgatus",
     32.0),
    ("Faecalibacterium prausnitzii",
     "d__Bacteria;p__Bacillota;c__Clostridia;o__Eubacteriales;f__Oscillospiraceae;g__Faecalibacterium;s__Faecalibacterium prausnitzii",
     26.0),
    ("Bacteroides fragilis",
     "d__Bacteria;p__Bacteroidota;c__Bacteroidia;o__Bacteroidales;f__Bacteroidaceae;g__Bacteroides;s__Bacteroides fragilis",
     14.0),
    ("Prevotella copri",
     "d__Bacteria;p__Bacteroidota;c__Bacteroidia;o__Bacteroidales;f__Prevotellaceae;g__Prevotella;s__Prevotella copri",
     10.0),
    ("Roseburia intestinalis",
     "d__Bacteria;p__Bacillota;c__Clostridia;o__Eubacteriales;f__Lachnospiraceae;g__Roseburia;s__Roseburia intestinalis",
     7.0),
    ("Akkermansia muciniphila",
     "d__Bacteria;p__Verrucomicrobiota;c__Verrucomicrobiae;o__Verrucomicrobiales;f__Akkermansiaceae;g__Akkermansia;s__Akkermansia muciniphila",
     5.0),
    ("Blautia wexlerae",
     "d__Bacteria;p__Bacillota;c__Clostridia;o__Eubacteriales;f__Lachnospiraceae;g__Blautia;s__Blautia wexlerae",
     3.0),
    ("Ruminococcus bromii",
     "d__Bacteria;p__Bacillota;c__Clostridia;o__Eubacteriales;f__Oscillospiraceae;g__Ruminococcus;s__Ruminococcus bromii",
     2.0),
    ("Bifidobacterium longum",
     "d__Bacteria;p__Actinomycetota;c__Actinomycetia;o__Bifidobacteriales;f__Bifidobacteriaceae;g__Bifidobacterium;s__Bifidobacterium longum",
     1.5),
    ("Bacteroides thetaiotaomicron",
     "d__Bacteria;p__Bacteroidota;c__Bacteroidia;o__Bacteroidales;f__Bacteroidaceae;g__Bacteroides;s__Bacteroides thetaiotaomicron",
     1.0),
]

_NUC = "ACGT"


def _stable_index(name: str, modulus: int) -> int:
    """A name-keyed stable index (independent of iteration order)."""
    return zlib.crc32(name.encode("utf-8")) % modulus


def species_for(mag_name: str):
    """(species, lineage, weight) for a MAG, consistent everywhere."""
    return LINEAGES[_stable_index(mag_name, len(LINEAGES))]


def _rng(name: str) -> random.Random:
    return random.Random(SEED ^ zlib.crc32(name.encode("utf-8")))


def _random_seq(r: random.Random, length: int) -> str:
    return "".join(_NUC[r.randint(0, 3)] for _ in range(length))


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #
def gen_contigs(out: str, n: int = 48) -> None:
    """Write ``n`` contigs with realistic lengths (>=1000 bp) and megahit names."""
    r = random.Random(SEED)
    with open(out, "w") as fh:
        for i in range(1, n + 1):
            length = r.randint(1000, 8000)
            # Round to a multiple of 4 so the sequence is tidy.
            length -= length % 4
            fh.write(f">k141_{i} length={length} cov={r.uniform(2, 60):.1f}\n")
            seq = _random_seq(r, length)
            for j in range(0, length, 70):
                fh.write(seq[j:j + 70] + "\n")


def partition_bins(in_fasta: str, out_prefix: str, n_bins: int = 12) -> None:
    """Split a contig FASTA into ``n_bins`` bin FASTAs (deterministic round-robin).

    Writes ``<out_prefix>.<k>.fa`` for k in 1..n_bins.
    """
    contigs = []  # (header, [seq lines])
    header, seq = None, []
    with open(in_fasta) as fh:
        for line in fh:
            if line.startswith(">"):
                if header is not None:
                    contigs.append((header, seq))
                header = line.rstrip("\n")
                seq = []
            else:
                seq.append(line.rstrip("\n"))
    if header is not None:
        contigs.append((header, seq))

    bins = [[] for _ in range(n_bins)]
    for idx, item in enumerate(contigs):
        bins[idx % n_bins].append(item)

    for k in range(n_bins):
        if not bins[k]:
            continue
        with open(f"{out_prefix}.{k + 1}.fa", "w") as fh:
            for header, seq in bins[k]:
                fh.write(header + "\n")
                for s in seq:
                    fh.write(s + "\n")


def gen_checkm(bins_dir: str, ext: str, out: str) -> None:
    """Quality report with varied completeness/contamination per bin."""
    import glob
    import os
    names = sorted(
        os.path.basename(p)[: -(len(ext) + 1)]
        for p in glob.glob(os.path.join(bins_dir, f"*.{ext}"))
    )
    with open(out, "w") as fh:
        fh.write("Name\tCompleteness\tContamination\tCompleteness_Model_Used\n")
        for name in names:
            r = _rng("checkm:" + name)
            completeness = round(r.uniform(50.0, 99.0), 1)
            contamination = round(r.uniform(0.5, 8.0), 1)
            fh.write(f"{name}\t{completeness}\t{contamination}\tstub\n")


def gen_gtdbtk(genomes_dir: str, ext: str, out: str) -> None:
    """Classify each genome with a consistent, realistic gut lineage."""
    import glob
    import os
    names = sorted(
        os.path.basename(p)[: -(len(ext) + 1)]
        for p in glob.glob(os.path.join(genomes_dir, f"*.{ext}"))
    )
    with open(out, "w") as fh:
        fh.write("user_genome\tclassification\tfastani_reference\n")
        for name in names:
            species, lineage, _w = species_for(name)
            fh.write(f"{name}\t{lineage}\tGCF_stub_{species.replace(' ', '_')}\n")


def mag_depth(mag_name: str) -> float:
    """Mean depth for a MAG: its species' weight scaled by per-MAG noise."""
    _species, _lineage, weight = species_for(mag_name)
    r = _rng("depth:" + mag_name)
    return weight * r.uniform(0.6, 1.4)


def gen_coverage(rnames, out: str, sample: str = "") -> None:
    """Coverage rows for ``<mag>|<contig>`` rnames with per-MAG varied depth.

    When ``sample`` is given, a deterministic per-(sample, MAG) multiplier is
    applied so different samples show different abundances.
    """
    with open(out, "w") as fh:
        fh.write("#rname\tstartpos\tendpos\tnumreads\tcovbases\tcoverage\tmeandepth\tmeanbaseq\tmeanmapq\n")
        for rname in rnames:
            if not rname:
                continue
            mag = rname.split("|", 1)[0]
            depth = mag_depth(mag)
            if sample:
                depth *= _rng(f"sample:{sample}:{mag}").uniform(0.4, 1.6)
            r = _rng("cov:" + rname)
            length = r.randint(1000, 8000)
            depth_here = depth * r.uniform(0.7, 1.3)
            covbases = int(length * r.uniform(0.85, 1.0))
            coverage = 100.0 * covbases / length
            numreads = int(depth_here * length / 150)
            fh.write(f"{rname}\t1\t{length}\t{numreads}\t{covbases}\t"
                     f"{coverage:.2f}\t{depth_here:.3f}\t35.0\t42.0\n")


def gen_kraken_report(out: str) -> None:
    """A multi-taxon Kraken2 report (rank-coded), species rows dominate."""
    r = random.Random(SEED)
    rows = []
    # Species-level rows (rank S) — these feed the community table.
    for species, lineage, weight in LINEAGES:
        taxid = 1000 + _stable_index(species, 9000)
        reads = int(weight * r.uniform(800, 1200))
        genus = lineage.split(";")[-1].replace("s__", "")
        rows.append((weight, reads, taxid, "S", species))
    total = sum(w for w, *_ in rows) or 1.0
    with open(out, "w") as fh:
        # A couple of higher-rank summary rows for realism.
        fh.write(f"100.00\t{int(total*1000)}\t0\tD\t2\tBacteria\n")
        for weight, reads, taxid, rank, name in sorted(rows, key=lambda x: -x[0]):
            pct = 100.0 * weight / total
            fh.write(f"{pct:.2f}\t{int(weight*1000)}\t{reads}\t{rank}\t{taxid}\t{name}\n")


def gen_bracken(out: str) -> None:
    """Multi-taxon Bracken abundance output."""
    r = random.Random(SEED)
    with open(out, "w") as fh:
        fh.write("name\ttaxonomy_id\ttaxonomy_lvl\tkraken_assigned_reads\t"
                 "added_reads\tnew_est_reads\tfraction_total_reads\n")
        weights = [(sp, w * r.uniform(0.9, 1.1)) for sp, _lin, w in LINEAGES]
        total = sum(w for _, w in weights)
        for species, weight in sorted(weights, key=lambda x: -x[1]):
            taxid = 1000 + _stable_index(species, 9000)
            reads = int(weight * 1000)
            frac = weight / total
            fh.write(f"{species}\t{taxid}\tS\t{reads}\t0\t{reads}\t{frac:.4f}\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv) -> int:
    if len(argv) < 2:
        print("usage: stubdata <command> ...", file=sys.stderr)
        return 2
    cmd = argv[1]
    args = argv[2:]
    if cmd == "contigs":
        gen_contigs(args[0], int(args[1]) if len(args) > 1 else 48)
    elif cmd == "partition":
        partition_bins(args[0], args[1], int(args[2]) if len(args) > 2 else 12)
    elif cmd == "checkm":
        gen_checkm(args[0], args[1], args[2])
    elif cmd == "gtdbtk":
        gen_gtdbtk(args[0], args[1], args[2])
    elif cmd == "coverage":
        # rnames on stdin, one per line; optional sample name as arg[1]
        gen_coverage([ln.strip() for ln in sys.stdin], args[0],
                     args[1] if len(args) > 1 else "")
    elif cmd == "kraken_report":
        gen_kraken_report(args[0])
    elif cmd == "bracken":
        gen_bracken(args[0])
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
