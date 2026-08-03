"""Stub toolchain for the offline end-to-end self-check.

Each stub is a tiny shell script that accepts the flags the real tool is called
with and emits the **minimal artefact the next stage actually reads** — a fastp
JSON with a ``summary`` block, a contigs FASTA, a CheckM2 ``quality_report.tsv``
with the columns stage 05 filters on, and so on.

The point is to execute the real stage scripts: their control flow, status-file
transitions, product validation, and report/monitor generation all run for real.
Nothing here produces scientific output — the sequences and numbers are
placeholders, which is why ``demo`` says so loudly.

Every stub announces itself on stderr so a failing run is easy to trace.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Dict

_TRACE = 'echo "[stub] $(basename "$0") $*" >&2'

# A couple of short contigs, reused wherever a FASTA is needed.
_FASTA_BODY = (
    '>contig_1 len=2000\\n'
    'ACGTACGTAC' * 4 + '\\n'
    '>contig_2 len=1500\\n'
    'TTGACCAGTT' * 4 + '\\n'
)

_PROTEIN_BODY = (
    '>gene_1 # 1 # 300 # 1 # stub\\n'
    'MKVLATTLLA' * 3 + '\\n'
    '>gene_2 # 320 # 700 # -1 # stub\\n'
    'MSTQPARDIL' * 3 + '\\n'
)

STUBS: Dict[str, str] = {}


def _stub(name: str, body: str) -> None:
    STUBS[name] = "#!/usr/bin/env bash\nset -u\n" + _TRACE + "\n" + body


# --------------------------------------------------------------------------- #
# 01 QC
# --------------------------------------------------------------------------- #
_stub("fastp", r'''
R1=""; R2=""; O1=""; O2=""; JSON=""; HTML=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) echo "fastp 0.0.0-stub"; exit 0;;
    -i) R1="$2"; shift 2;;
    -I) R2="$2"; shift 2;;
    -o) O1="$2"; shift 2;;
    -O) O2="$2"; shift 2;;
    --json) JSON="$2"; shift 2;;
    --html) HTML="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$O1" && -n "$R1" ]] && cp "$R1" "$O1"
[[ -n "$O2" && -n "$R2" ]] && cp "$R2" "$O2"
if [[ -n "$JSON" ]]; then
cat > "$JSON" <<'EOF'
{"summary":{"before_filtering":{"total_reads":200,"total_bases":30000},
"after_filtering":{"total_reads":180,"total_bases":27000,"q30_rate":0.93,"gc_content":0.45}},
"adapter_cutting":{"adapter_trimmed_reads":12}}
EOF
fi
[[ -n "$HTML" ]] && echo "<html><body>stub fastp report</body></html>" > "$HTML"
exit 0
''')

# --------------------------------------------------------------------------- #
# 02 assembly
# --------------------------------------------------------------------------- #
_stub("megahit", r'''
OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) echo "MEGAHIT v1.2.9-stub"; exit 0;;
    -o) OUT="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
mkdir -p "$OUT"
python3 "$MG_STUBDATA" contigs "$OUT/final.contigs.fa" 48
exit 0
''')

_stub("metaspades.py", r'''
OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) echo "SPAdes v3.15-stub"; exit 0;;
    -o) OUT="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
mkdir -p "$OUT"
python3 "$MG_STUBDATA" contigs "$OUT/contigs.fasta" 48
exit 0
''')

_stub("seqkit", r'''
SUB="${1:-}"
case "$SUB" in
  version) echo "seqkit v0.0.0-stub"; exit 0;;
  seq)
    shift
    IN=""; OUT=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -m) shift 2;;
        -o) OUT="$2"; shift 2;;
        -*) shift;;
        *) IN="$1"; shift;;
      esac
    done
    if [[ -n "$OUT" ]]; then cp "$IN" "$OUT"; else cat "$IN"; fi
    ;;
  stats)
    shift
    printf 'file\tformat\ttype\tnum_seqs\tsum_len\tmin_len\tavg_len\tmax_len\n'
    printf 'stub\tFASTA\tDNA\t2\t3500\t1500\t1750\t2000\n'
    ;;
  *) ;;
esac
exit 0
''')

# --------------------------------------------------------------------------- #
# 03 mapping / mag_abundance
# --------------------------------------------------------------------------- #
_stub("bowtie2-build", r'''
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --threads) shift 2;;
    -*) shift;;
    *) ARGS+=("$1"); shift;;
  esac
done
REF="${ARGS[0]:-}"
PREFIX="${ARGS[-1]:-}"
[[ -n "$PREFIX" ]] || exit 1
for i in 1 2 3 4; do : > "${PREFIX}.${i}.bt2"; done
: > "${PREFIX}.rev.1.bt2"
# Remember which sequences this index was built from. The aligner replays them
# as @SQ lines so per-contig aggregation downstream (mag_abundance builds a
# combined reference with "<mag>|<contig>" names) sees the real identifiers.
if [[ -f "$REF" ]]; then
  grep '^>' "$REF" | sed 's/^>//; s/[[:space:]].*$//' > "${PREFIX}.stubrefs"
fi
exit 0
''')

_stub("bowtie2", r'''
UNCONC=""; IDX=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) echo "bowtie2-align version 0.0.0-stub"; exit 0;;
    --un-conc-gz) UNCONC="$2"; shift 2;;
    -x) IDX="$2"; shift 2;;
    *) shift;;
  esac
done
if [[ -n "$UNCONC" ]]; then
  # bowtie2 substitutes %; emit both mates so host/PhiX removal has inputs.
  for m in 1 2; do
    printf '@r1\nACGT\n+\nIIII\n' | gzip -c > "${UNCONC/\%/$m}"
  done
fi
REFS=()
if [[ -n "$IDX" && -f "${IDX}.stubrefs" ]]; then
  mapfile -t REFS < "${IDX}.stubrefs"
fi
[[ ${#REFS[@]} -gt 0 ]] || REFS=(contig_1 contig_2)
printf '@HD\tVN:1.6\tSO:unsorted\n'
for r in "${REFS[@]}"; do printf '@SQ\tSN:%s\tLN:2000\n' "$r"; done
i=0
for r in "${REFS[@]}"; do
  i=$((i+1))
  printf 'r%d\t0\t%s\t1\t42\t4M\t*\t0\t0\tACGT\tIIII\n' "$i" "$r"
done
exit 0
''')

_stub("bwa-mem2", r'''
SUB="${1:-}"
case "$SUB" in
  index)
    REF="${2:-}"
    if [[ -f "$REF" ]]; then
      grep '^>' "$REF" | sed 's/^>//; s/[[:space:]].*$//' > "${REF}.stubrefs"
    fi
    ;;
  mem)
    shift
    REF=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -t) shift 2;;
        -*) shift;;
        *) [[ -z "$REF" ]] && REF="$1"; shift;;
      esac
    done
    REFS=()
    [[ -n "$REF" && -f "${REF}.stubrefs" ]] && mapfile -t REFS < "${REF}.stubrefs"
    [[ ${#REFS[@]} -gt 0 ]] || REFS=(contig_1 contig_2)
    printf '@HD\tVN:1.6\tSO:unsorted\n'
    for r in "${REFS[@]}"; do printf '@SQ\tSN:%s\tLN:2000\n' "$r"; done
    i=0
    for r in "${REFS[@]}"; do
      i=$((i+1))
      printf 'r%d\t0\t%s\t1\t42\t4M\t*\t0\t0\tACGT\tIIII\n' "$i" "$r"
    done
    ;;
  *) ;;
esac
exit 0
''')

_stub("samtools", r'''
SUB="${1:-}"
shift || true
case "$SUB" in
  --version|version) echo "samtools 0.0.0-stub"; exit 0;;
  sort)
    OUT=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -o) OUT="$2"; shift 2;;
        -@) shift 2;;
        *) shift;;
      esac
    done
    if [[ -n "$OUT" ]]; then cat > "$OUT"; else cat > /dev/null; fi
    ;;
  index)
    BAM=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -@) shift 2;;
        -*) shift;;
        *) BAM="$1"; shift;;
      esac
    done
    [[ -n "$BAM" ]] && : > "${BAM}.bai"
    ;;
  view)
    # -c counts; -F 4 counts mapped. Numbers are placeholders.
    MAPPED=0
    for a in "$@"; do [[ "$a" == "4" ]] && MAPPED=1; done
    if [[ "$MAPPED" == "1" ]]; then echo 90; else echo 100; fi
    ;;
  flagstat) echo "100 + 0 in total (QC-passed reads + QC-failed reads)";;
  coverage)
    # mag_abundance aggregates per-contig meandepth from this table, so the
    # reference names must be the real ones: read them back from the @SQ lines
    # the aligner stub wrote into this "BAM", and give each MAG a varied depth.
    BAM=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -*) shift;;
        *) BAM="$1"; shift;;
      esac
    done
    if [[ -n "$BAM" && -f "$BAM" ]]; then
      SAMPLE="$(basename "$BAM")"; SAMPLE="${SAMPLE%%.*}"
      grep '^@SQ' "$BAM" 2>/dev/null | sed 's/.*SN:\([^\t]*\).*/\1/' \
        | python3 "$MG_STUBDATA" coverage /dev/stdout "$SAMPLE"
    else
      printf '#rname\tstartpos\tendpos\tnumreads\tcovbases\tcoverage\tmeandepth\tmeanbaseq\tmeanmapq\n'
    fi
    ;;
  faidx) ;;
  *) ;;
esac
exit 0
''')

_stub("jgi_summarize_bam_contig_depths", r'''
OUT=""
BAMS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --outputDepth) OUT="$2"; shift 2;;
    -*) shift;;
    *) BAMS+=("$1"); shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
{
  printf 'contigName\tcontigLen\ttotalAvgDepth'
  for b in "${BAMS[@]}"; do printf '\t%s\t%s-var' "$(basename "$b")" "$(basename "$b")"; done
  printf '\n'
  for c in contig_1:2000 contig_2:1500; do
    name="${c%%:*}"; len="${c##*:}"
    printf '%s\t%s\t12.5' "$name" "$len"
    for b in "${BAMS[@]}"; do printf '\t12.5\t1.2'; done
    printf '\n'
  done
} > "$OUT"
exit 0
''')

# --------------------------------------------------------------------------- #
# 04 binning
# --------------------------------------------------------------------------- #
_stub("metabat2", r'''
IN=""; OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) echo "MetaBAT 2 (version 2.15-stub)"; exit 0;;
    -i) IN="$2"; shift 2;;
    -o) OUT="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
mkdir -p "$(dirname "$OUT")"
if [[ -n "$IN" && -f "$IN" ]]; then
  python3 "$MG_STUBDATA" partition "$IN" "$OUT" 12
else
  printf '%b' ">contig_1 len=2000\nACGTACGTACACGTACGTAC\n" > "${OUT}.1.fa"
fi
exit 0
''')

_stub("run_MaxBin.pl", r'''
IN=""; OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -version|-v) echo "MaxBin 2.2.7-stub"; exit 0;;
    -contig) IN="$2"; shift 2;;
    -out) OUT="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
mkdir -p "$(dirname "$OUT")"
if [[ -n "$IN" && -f "$IN" ]]; then
  python3 "$MG_STUBDATA" partition "$IN" "${OUT}.tmp" 12
  for f in "${OUT}.tmp".*.fa; do
    [[ -e "$f" ]] || continue
    n="${f##*.tmp.}"; n="${n%.fa}"
    mv "$f" "${OUT}.$(printf '%03d' "$n").fasta"
  done
else
  printf '%b' ">contig_1 len=2000\nACGTACGTACACGTACGTAC\n" > "${OUT}.001.fasta"
fi
exit 0
''')

_stub("cut_up_fasta.py", r'''
IN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|-o) shift 2;;
    --merge_last) shift;;
    -*) shift;;
    *) IN="$1"; shift;;
  esac
done
[[ -n "$IN" ]] && cat "$IN"
exit 0
''')

_stub("concoct_coverage_table.py", r'''
printf 'contig\tsample\n'
printf 'contig_1\t12.5\n'
printf 'contig_2\t9.5\n'
exit 0
''')

_stub("concoct", r'''
BASE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) echo "concoct 0.0.0-stub"; exit 0;;
    -b) BASE="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$BASE" ]] || exit 1
mkdir -p "$BASE"
printf 'contig_id,cluster_id\ncontig_1,0\ncontig_2,0\n' > "${BASE%/}/clustering_gt1000.csv"
exit 0
''')

_stub("merge_cutup_clustering.py", r'''
[[ -f "${1:-}" ]] && cat "$1" || printf 'contig_id,cluster_id\ncontig_1,0\n'
exit 0
''')

_stub("extract_fasta_bins.py", r'''
OUTDIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output_path) OUTDIR="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUTDIR" ]] || exit 1
mkdir -p "$OUTDIR"
printf '%b' ">contig_1 len=2000\nACGTACGTACACGTACGTAC\n" > "${OUTDIR}/0.fa"
exit 0
''')

_stub("Fasta_to_Contigs2Bin.sh", r'''
DIR=""; EXT="fa"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -i) DIR="$2"; shift 2;;
    -e) EXT="$2"; shift 2;;
    *) shift;;
  esac
done
shopt -s nullglob
for f in "${DIR%/}"/*."${EXT}"; do
  bin="$(basename "${f%.*}")"
  # contig<TAB>bin, as DAS Tool expects.
  grep '^>' "$f" | sed 's/^>//; s/[[:space:]].*$//' | while read -r c; do
    printf '%s\t%s\n' "$c" "$bin"
  done
done
exit 0
''')

_stub("DAS_Tool", r'''
OUT=""; CONTIGS=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) echo "DAS Tool 1.1.6-stub"; exit 0;;
    -o) OUT="$2"; shift 2;;
    -c) CONTIGS="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
BINS="${OUT}_DASTool_bins"
mkdir -p "$BINS"
if [[ -n "$CONTIGS" && -f "$CONTIGS" ]]; then
  python3 "$MG_STUBDATA" partition "$CONTIGS" "${BINS}/das_bin" 12
else
  printf '%b' ">contig_1 len=2000\nACGTACGTACACGTACGTAC\n" > "${BINS}/das_bin_1.fa"
  printf '%b' ">contig_2 len=1500\nTTGACCAGTTTTGACCAGTT\n" > "${BINS}/das_bin_2.fa"
fi
exit 0
''')

# --------------------------------------------------------------------------- #
# 05 CheckM2 / 06 dRep / 07 GTDB-Tk
# --------------------------------------------------------------------------- #
_stub("checkm2", r'''
if [[ "${1:-}" == "--version" ]]; then echo "1.0.0-stub"; exit 0; fi
SUB="${1:-}"; shift || true
OUT=""; IN=""; EXT="fa"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-directory) OUT="$2"; shift 2;;
    --input) IN="$2"; shift 2;;
    --extension) EXT="$2"; shift 2;;
    *) shift;;
  esac
done
[[ "$SUB" == "predict" ]] || exit 0
[[ -n "$OUT" ]] || exit 1
mkdir -p "$OUT"
python3 "$MG_STUBDATA" checkm "${IN%/}" "$EXT" "${OUT%/}/quality_report.tsv"
exit 0
''')

_stub("dRep", r'''
if [[ "${1:-}" == "--version" ]]; then echo "dRep v0.0.0-stub"; exit 0; fi
SUB="${1:-}"; shift || true
OUT=""; GENOMES=()
COLLECT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -g) COLLECT=1; shift;;
    -p|-sa|--S_algorithm|--genomeInfo) COLLECT=0; shift 2;;
    -*) COLLECT=0; shift;;
    *)
      if [[ "$COLLECT" == "1" ]]; then GENOMES+=("$1"); elif [[ -z "$OUT" ]]; then OUT="$1"; fi
      shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
REP="${OUT%/}/dereplicated_genomes"
mkdir -p "$REP" "${OUT%/}/data_tables"
if [[ ${#GENOMES[@]} -gt 0 ]]; then
  for g in "${GENOMES[@]}"; do [[ -f "$g" ]] && cp "$g" "$REP/$(basename "$g")"; done
else
  printf '%b' ">contig_1\nACGTACGTAC\n" > "$REP/stub_mag.fa"
fi
printf 'genome,cluster\n' > "${OUT%/}/data_tables/Cdb.csv"
exit 0
''')

_stub("gtdbtk", r'''
if [[ "${1:-}" == "--version" || "${1:-}" == "-v" ]]; then echo "gtdbtk 2.4-stub"; exit 0; fi
SUB="${1:-}"; shift || true
OUT=""; GDIR=""; EXT="fa"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out_dir) OUT="$2"; shift 2;;
    --genome_dir) GDIR="$2"; shift 2;;
    --extension) EXT="$2"; shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
mkdir -p "$OUT"
python3 "$MG_STUBDATA" gtdbtk "${GDIR%/}" "$EXT" "${OUT%/}/gtdbtk.bac120.summary.tsv"
exit 0
''')

# --------------------------------------------------------------------------- #
# 08 / 09 annotation
# --------------------------------------------------------------------------- #
_stub("prodigal", r'''
if [[ "${1:-}" == "-v" || "${1:-}" == "--version" ]]; then echo "Prodigal V0.0-stub"; exit 0; fi
FAA=""; FNA=""; GFF=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -a) FAA="$2"; shift 2;;
    -d) FNA="$2"; shift 2;;
    -o) GFF="$2"; shift 2;;
    -i|-p|-f) shift 2;;
    *) shift;;
  esac
done
[[ -n "$FAA" ]] && { mkdir -p "$(dirname "$FAA")"; printf '%b' "__PROT__" > "$FAA"; }
[[ -n "$FNA" ]] && { mkdir -p "$(dirname "$FNA")"; printf '%b' ">gene_1\nACGTACGTAC\n" > "$FNA"; }
[[ -n "$GFF" ]] && { mkdir -p "$(dirname "$GFF")"; printf '##gff-version 3\ncontig_1\tstub\tCDS\t1\t300\t.\t+\t0\tID=1\n' > "$GFF"; }
exit 0
'''.replace("__PROT__", _PROTEIN_BODY))

_stub("prokka", r'''
if [[ "${1:-}" == "--version" ]]; then echo "prokka 0.0.0-stub"; exit 0; fi
OUT=""; PREFIX="stub"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --outdir) OUT="$2"; shift 2;;
    --prefix) PREFIX="$2"; shift 2;;
    --kingdom|--cpus) shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUT" ]] || exit 1
mkdir -p "$OUT"
printf '%b' "__PROT__" > "${OUT%/}/${PREFIX}.faa"
printf '##gff-version 3\ncontig_1\tprokka\tCDS\t1\t300\t.\t+\t0\tID=1\n' > "${OUT%/}/${PREFIX}.gff"
printf 'CDS: 2\n' > "${OUT%/}/${PREFIX}.txt"
exit 0
'''.replace("__PROT__", _PROTEIN_BODY))

_stub("emapper.py", r'''
if [[ "${1:-}" == "--version" ]]; then echo "emapper-0.0.0-stub"; exit 0; fi
OUTDIR=""; NAME="eggnog_results"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output_dir) OUTDIR="$2"; shift 2;;
    --output) NAME="$2"; shift 2;;
    -i|--data_dir|-m|--cpu) shift 2;;
    *) shift;;
  esac
done
[[ -n "$OUTDIR" ]] || exit 1
mkdir -p "$OUTDIR"
{
  printf '## stub emapper annotations\n'
  printf '#query\tseed_ortholog\tevalue\tCOG_category\tDescription\tKEGG_ko\n'
  printf 'gene_1\tstub\t1e-50\tJ\tstub description\tko:K00001\n'
  printf 'gene_2\tstub\t1e-40\tE\tstub description\tko:K00002\n'
} > "${OUTDIR%/}/${NAME}.emapper.annotations"
exit 0
''')

_stub("kraken2", r'''
if [[ "${1:-}" == "--version" ]]; then echo "Kraken version 0.0.0-stub"; exit 0; fi
OUT=""; REP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) OUT="$2"; shift 2;;
    --report) REP="$2"; shift 2;;
    --db|--confidence|--threads) shift 2;;
    --paired|--gzip-compressed) shift;;
    *) shift;;
  esac
done
if [[ -n "$OUT" ]]; then
  mkdir -p "$(dirname "$OUT")"
  printf 'C\tr1\t1423\t150\t1423:1\nU\tr2\t0\t150\t0:1\n' > "$OUT"
fi
if [[ -n "$REP" ]]; then
  mkdir -p "$(dirname "$REP")"
  python3 "$MG_STUBDATA" kraken_report "$REP"
fi
exit 0
''')

_stub("bracken", r'''
if [[ "${1:-}" == "-v" ]]; then echo "Bracken 2.9-stub"; exit 0; fi
OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) OUT="$2"; shift 2;;
    -d|-i|-r|-l) shift 2;;
    *) shift;;
  esac
done
if [[ -n "$OUT" ]]; then
  python3 "$MG_STUBDATA" bracken "$OUT"
fi
exit 0
''')


def install_stubs(bin_dir: Path) -> Path:
    """Write every stub as an executable into ``bin_dir``. Returns the path.

    Also installs the deterministic data generator as ``_mg_stubdata.py`` so the
    stubs can call ``python3 "$MG_STUBDATA" <command>`` to emit realistic data.
    """
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in STUBS.items():
        target = bin_dir / name
        target.write_text(body, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # Ship the data generator next to the stubs.
    import importlib.util
    spec = importlib.util.find_spec("metaglens.demo.stubdata")
    if spec is not None and spec.origin:
        src = Path(spec.origin).read_text(encoding="utf-8")
        (bin_dir / "_mg_stubdata.py").write_text(src, encoding="utf-8")
    return bin_dir


def stub_env(bin_dir: Path, base_env: Dict[str, str] = None) -> Dict[str, str]:
    """Environment with the stub directory first on PATH and MG_STUBDATA set."""
    env = dict(base_env if base_env is not None else os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["MG_STUBDATA"] = str(Path(bin_dir) / "_mg_stubdata.py")
    return env
