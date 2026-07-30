"""Progress estimation from stage logs.

Two rules govern this module, both from hard-won operational experience:

1. **A quiet log is not a hung job.** Assemblers routinely run for tens of
   minutes without printing anything. Silence is reported as "no output for N",
   never as a failure or a stall.
2. **Parsing failure degrades, it never raises.** When a log does not match any
   known pattern the result is an indeterminate progress plus a heartbeat from
   the file's mtime — still useful, never wrong.

Patterns are anchored on what the stage scripts actually log (``Processing
sample:``, ``Assembling sample:``, ``Annotating:``) plus the tools' own output.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Progress:
    stage: str
    determinate: bool                  # False => unknown fraction, show a spinner
    fraction: Optional[float] = None   # 0.0-1.0 when determinate
    done: int = 0
    total: int = 0
    detail: str = ""                   # human summary, e.g. "3/7 samples"
    units: List[str] = field(default_factory=list)   # finished unit names
    active: List[str] = field(default_factory=list)  # started but unfinished
    last_line: str = ""
    seconds_since_output: Optional[float] = None
    heartbeat: str = ""                # "quiet for 12m — assemblers do this"

    def as_dict(self) -> Dict[str, object]:
        return {
            "stage": self.stage, "determinate": self.determinate,
            "fraction": self.fraction, "done": self.done, "total": self.total,
            "detail": self.detail, "units": self.units, "active": self.active,
            "last_line": self.last_line,
            "seconds_since_output": self.seconds_since_output,
            "heartbeat": self.heartbeat,
        }


# Per-stage markers: (started_regex, finished_regex). Group 1 is the unit name.
# Anchored on what the stage scripts actually log, verified against real runs.
_UNIT_MARKERS: Dict[str, tuple] = {
    "01_qc": (r"Processing sample:\s*(\S+)", r"\[(\S+)\]\s+QC completed"),
    "02_assembly": (r"Assembling sample:\s*(\S+)",
                    r"\[(\S+)\]\s+(?:assembly completed|Contig stats)"),
    "03_mapping": (r"Mapping sample:\s*(\S+)", r"\[(\S+)\]\s+mapping completed"),
    "04_binning": (r"(?:Binning|Processing) unit:\s*(\S+)",
                   r"\[(\S+)\]\s+binning completed"),
    "mag_abundance": (r"MAG coverage:\s*(\S+)", r"\[(\S+)\]\s+coverage table written"),
    # 08 logs only starts per MAG; completion is a single summary line, so the
    # start-count fallback below is what drives its progress.
    "08_annotation": (r"Annotating:\s*(\S+)", r"^\Z(?!x)x"),
    "09_contig": (r"(?:Predicting genes for|Processing unit):\s*(\S+)",
                  r"\[(\S+)\]\s+(?:genes predicted|completed)"),
}

# Declared totals, e.g. "MAGs to annotate: 4".
_TOTAL_PATTERNS = (
    r"(?:MAGs to annotate|Genomes to process|Units to process)\s*:\s*(\d+)",
    r"Processing (\d+) sample\(s\)",
    r"Mapping (\d+) sample\(s\)",
    r"Estimating abundance for (\d+) sample\(s\)",
    r"Classifying contigs for (\d+) unit\(s\)",
)

# Tool-specific hints shown when unit counting yields nothing.
_TOOL_HINTS = (
    (re.compile(r"--- \[k = (\d+)", re.I), "MEGAHIT k={}"),
    (re.compile(r"\[k=(\d+)\]", re.I), "MEGAHIT k={}"),
    (re.compile(r"(\d+(?:\.\d+)?)% overall alignment rate", re.I),
     "Bowtie2 {}% aligned"),
    (re.compile(r"Processing genome (\d+) of (\d+)", re.I), "genome {} of {}"),
    (re.compile(r"Step (\d+)/(\d+)", re.I), "step {}/{}"),
)

# Assemblers and database searches can be silent for a long time; only past this
# do we bother mentioning it, and even then only as information.
_QUIET_NOTICE_SECONDS = 120


def _humanise(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _tool_hint(lines: List[str]) -> str:
    for line in reversed(lines[-80:]):
        for pattern, template in _TOOL_HINTS:
            match = pattern.search(line)
            if match:
                try:
                    return template.format(*match.groups())
                except (IndexError, KeyError):
                    return template
    return ""


def parse_log(stage: str, lines: List[str],
              declared_total: Optional[int] = None) -> Progress:
    """Estimate progress for ``stage`` from its log lines. Never raises."""
    lines = [ln for ln in lines if ln.strip()]
    last_line = lines[-1].strip() if lines else ""

    total = declared_total or 0
    if not total:
        for pattern in _TOTAL_PATTERNS:
            for line in reversed(lines):
                match = re.search(pattern, line, re.I)
                if match:
                    try:
                        total = int(match.group(1))
                    except (TypeError, ValueError):
                        total = 0
                    break
            if total:
                break

    started: List[str] = []
    finished: List[str] = []
    markers = _UNIT_MARKERS.get(stage)
    if markers:
        start_re, done_re = (re.compile(markers[0]), re.compile(markers[1]))
        for line in lines:
            m = start_re.search(line)
            if m and m.group(1) not in started:
                started.append(m.group(1))
            m = done_re.search(line)
            if m:
                name = next((g for g in m.groups() if g), None)
                if name and name not in finished:
                    finished.append(name)

    if not total and started:
        total = len(started)

    if total and finished:
        done = min(len(finished), total)
        progress = Progress(
            stage=stage, determinate=True, fraction=done / total,
            done=done, total=total,
            detail=f"{done}/{total} unit(s)",
            units=finished,
            active=[u for u in started if u not in finished],
            last_line=last_line,
        )
    elif total and started:
        # Some stages log only starts (08_annotation). Everything started before
        # the newest one has necessarily finished, so count those.
        done = max(0, min(len(started) - 1, total))
        progress = Progress(
            stage=stage, determinate=True, fraction=done / total,
            done=done, total=total,
            detail=f"{done}/{total} unit(s), {started[-1]} in flight",
            units=started[:-1],
            active=[started[-1]],
            last_line=last_line,
        )
    else:
        hint = _tool_hint(lines)
        detail = hint or (f"{len(started)} unit(s) started" if started
                          else "running")
        progress = Progress(
            stage=stage, determinate=False, fraction=None,
            done=len(finished), total=total,
            detail=detail, units=finished,
            active=[u for u in started if u not in finished],
            last_line=last_line,
        )
    return progress


def parse_stage(results_dir: Path, stage: str,
                now: Optional[float] = None) -> Progress:
    """Parse a stage's log file, adding an mtime heartbeat."""
    log_dir = Path(results_dir) / "reports" / "logs"
    path = log_dir / f"{stage}.log"
    if not path.is_file():
        matches = sorted(log_dir.glob(f"{stage}*.log")) if log_dir.is_dir() else []
        path = matches[-1] if matches else None

    if path is None:
        progress = Progress(stage=stage, determinate=False,
                            detail="no log yet", heartbeat="waiting for output")
        return progress

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return Progress(stage=stage, determinate=False, detail="log unreadable",
                        heartbeat="log could not be read")

    progress = parse_log(stage, lines)

    # Heartbeat: silence is information, never a verdict.
    try:
        mtime = path.stat().st_mtime
        quiet = max(0.0, (now if now is not None else time.time()) - mtime)
        progress.seconds_since_output = quiet
        if quiet >= _QUIET_NOTICE_SECONDS:
            # Deliberately avoids words like "stalled"/"hung" even in the
            # negative: a glancing reader takes the alarming word, not the "not".
            progress.heartbeat = (
                f"no output for {_humanise(quiet)} — normal for assemblers and "
                f"database searches, which work silently for long stretches"
            )
        else:
            progress.heartbeat = f"output {_humanise(quiet)} ago"
    except OSError:
        pass
    return progress
