"""Scientific quality gates.

Product validation (:mod:`metaglens.state`) answers "did this stage produce
anything usable". Gates answer the next question: "do the numbers look
plausible" — QC retention, bins recovered, MIMAG-quality MAGs.

Thresholds and their plain-language hints live in ``rules/gates.yaml`` so a
bioinformatician can tune them without touching Python. Gates warn by default;
only ``strict`` turns a warning into a stop, because a low-but-explicable metric
is a reason to look, not necessarily a reason to abort.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_RULES_FILE = "gates.yaml"

# Ranks used when combining several gate outcomes.
_SEVERITY_ORDER = {"pass": 0, "warn": 1, "block": 2, "unknown": -1}


@dataclass
class GateResult:
    gate_id: str
    stage: str
    label: str
    metric: str
    value: Optional[float]
    status: str            # pass | warn | block | unknown
    detail: str
    hint: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.gate_id, "stage": self.stage, "label": self.label,
            "metric": self.metric, "value": self.value, "status": self.status,
            "detail": self.detail, "hint": self.hint,
        }


def load_rules(path: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Load the externalised gate rules (empty dict when unavailable)."""
    import yaml
    target = Path(path) if path else Path(__file__).with_name("rules") / _RULES_FILE
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
# Metric extraction (read-only, tolerant of absent files)
# --------------------------------------------------------------------------- #
def _rows(path: Path, has_header: bool = True) -> List[List[str]]:
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8",
                                             errors="replace").splitlines()
                 if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []
    if has_header and lines:
        lines = lines[1:]
    return [ln.split("\t") for ln in lines]


def _retention_rate(results: Path, samples: List[str]) -> Optional[float]:
    """Aggregate QC retention from the per-sample .qcstats files."""
    before = after = 0
    found = False
    for stats in sorted((results / "01_qc").glob("*.qcstats")):
        try:
            parts = stats.read_text(encoding="utf-8").strip().split("\t")
            before += int(parts[0])
            after += int(parts[1])
            found = True
        except (OSError, ValueError, IndexError):
            continue
    if not found or before <= 0:
        return None
    return 100.0 * after / before


def _bins_per_sample(results: Path, samples: List[str]) -> Optional[float]:
    bins_dir = results / "04_binning" / "all_bins"
    if not bins_dir.is_dir():
        return None
    count = len([p for p in bins_dir.iterdir()
                 if p.is_file() and p.name.endswith((".fa", ".fna", ".fasta"))])
    denominator = max(1, len(samples))
    return count / denominator


def _checkm_rows(results: Path) -> List[List[str]]:
    return _rows(results / "05_checkm" / "quality_report.tsv")


def _mimag_hq_count(results: Path, samples: List[str]) -> Optional[float]:
    rows = _checkm_rows(results)
    if not rows:
        return None
    count = 0
    for row in rows:
        if len(row) < 3:
            continue
        try:
            completeness, contamination = float(row[1]), float(row[2])
        except ValueError:
            continue
        # MIMAG high quality: >=90% complete and <=5% contaminated.
        if completeness >= 90.0 and contamination <= 5.0:
            count += 1
    return float(count)


def _retained_fraction(results: Path, samples: List[str]) -> Optional[float]:
    total = _checkm_rows(results)
    if not total:
        return None
    filtered = _rows(results / "05_checkm" / "quality_report_filtered.tsv")
    if not filtered:
        filtered_dir = results / "05_checkm" / "filtered_bins"
        if filtered_dir.is_dir():
            kept = len([p for p in filtered_dir.iterdir() if p.is_file()])
        else:
            return None
    else:
        kept = len(filtered)
    return 100.0 * kept / len(total)


def _representative_count(results: Path, samples: List[str]) -> Optional[float]:
    rep = results / "06_derep" / "dereplicated_genomes"
    if not rep.is_dir():
        return None
    return float(len([p for p in rep.iterdir()
                      if p.is_file() and p.name.endswith((".fa", ".fna", ".fasta"))]))


def _taxa_count(results: Path, samples: List[str]) -> Optional[float]:
    matrix = results / "10_community" / "community_matrix.tsv"
    if not matrix.is_file():
        return None
    return float(len(_rows(matrix)))


METRICS = {
    "retention_rate": _retention_rate,
    "bins_per_sample": _bins_per_sample,
    "mimag_hq_count": _mimag_hq_count,
    "retained_fraction": _retained_fraction,
    "representative_count": _representative_count,
    "taxa_count": _taxa_count,
}


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def _judge(rule: Dict[str, Any], value: float) -> str:
    for key, cmp in (("block_below", lambda v, t: v < t),
                     ("block_above", lambda v, t: v > t)):
        threshold = rule.get(key)
        if threshold is not None and cmp(value, float(threshold)):
            return "block"
    for key, cmp in (("warn_below", lambda v, t: v < t),
                     ("warn_above", lambda v, t: v > t)):
        threshold = rule.get(key)
        if threshold is not None and cmp(value, float(threshold)):
            return "warn"
    return "pass"


def _thresholds(rule: Dict[str, Any]) -> str:
    parts = []
    for key, text in (("warn_below", "warn <"), ("block_below", "block <"),
                      ("warn_above", "warn >"), ("block_above", "block >")):
        if rule.get(key) is not None:
            parts.append(f"{text} {rule[key]}")
    return ", ".join(parts)


def evaluate(results_dir: Path, stages: Optional[List[str]] = None,
             samples: Optional[List[str]] = None,
             rules: Optional[Dict[str, List[Dict[str, Any]]]] = None
             ) -> List[GateResult]:
    """Evaluate gates for ``stages`` (default: every stage with rules)."""
    results = Path(results_dir)
    rule_map = rules if rules is not None else load_rules()
    if samples is None:
        from . import __name__ as _unused  # keep imports local and cheap
        from ..state import _sample_ids
        samples = _sample_ids(results)

    targets = stages if stages else list(rule_map)
    out: List[GateResult] = []
    for stage in targets:
        for rule in rule_map.get(stage, []):
            metric = rule.get("metric", "")
            extractor = METRICS.get(metric)
            label = rule.get("label", metric)
            unit = rule.get("unit", "")
            gate_id = rule.get("id", f"{stage}.{metric}")
            hint = (rule.get("hint") or "").strip()
            if extractor is None:
                out.append(GateResult(gate_id, stage, label, metric, None,
                                      "unknown", f"no extractor for '{metric}'",
                                      hint))
                continue
            try:
                value = extractor(results, samples)
            except Exception as exc:
                out.append(GateResult(gate_id, stage, label, metric, None,
                                      "unknown", f"metric error: {exc}", hint))
                continue
            if value is None:
                out.append(GateResult(gate_id, stage, label, metric, None,
                                      "unknown",
                                      "metric unavailable (stage not run yet?)",
                                      hint))
                continue
            status = _judge(rule, value)
            shown = f"{value:.1f}" if isinstance(value, float) else str(value)
            detail = f"{label}: {shown}{unit}"
            bounds = _thresholds(rule)
            if bounds and status != "pass":
                detail += f" ({bounds})"
            out.append(GateResult(gate_id, stage, label, metric, round(value, 3),
                                  status, detail, hint))
    return out


def summarise(gate_results: List[GateResult], strict: bool = False
              ) -> Dict[str, Any]:
    """Roll gate results up into a decision. ``strict`` promotes warn to block."""
    statuses = [g.status for g in gate_results]
    warn = [g for g in gate_results if g.status == "warn"]
    block = [g for g in gate_results if g.status == "block"]
    blocking = list(block) + (list(warn) if strict else [])
    worst = "pass"
    for status in statuses:
        if _SEVERITY_ORDER.get(status, -1) > _SEVERITY_ORDER.get(worst, 0):
            worst = status
    return {
        "gates": [g.as_dict() for g in gate_results],
        "counts": {
            "pass": statuses.count("pass"),
            "warn": len(warn),
            "block": len(block),
            "unknown": statuses.count("unknown"),
        },
        "worst": worst,
        "strict": strict,
        "blocking": [g.gate_id for g in blocking],
        "ok": not blocking,
    }
