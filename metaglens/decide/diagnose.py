"""Failure diagnosis: turn an exit code and a log tail into something actionable.

Before this, a failed stage produced one line — ``exit 137. Check reports/logs/``
— which tells a newcomer nothing. Signatures live in ``rules/failures.yaml`` so
the knowledge accumulates as configuration rather than code.

The contract that matters most: when nothing matches, say so. An honest "unknown
failure, here is the log and the last command" is far better than a confident
wrong cause, which would send someone down the wrong path for hours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_RULES_FILE = "failures.yaml"
_LOG_TAIL_LINES = 200

CLASS_LABELS = {
    "script_defect": "script defect",
    "environment": "environment / infrastructure",
    "data_config": "data or configuration",
    "unknown": "unknown",
}


@dataclass
class Diagnosis:
    stage: str
    rule_id: str                     # "" when nothing matched
    failure_class: str               # script_defect | environment | data_config | unknown
    title: str
    diagnosis: str
    evidence: List[str] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    exit_code: Optional[int] = None
    failed_command: str = ""
    log_file: str = ""
    log_line: str = ""
    matched: bool = False

    @property
    def class_label(self) -> str:
        return CLASS_LABELS.get(self.failure_class, self.failure_class)

    def human_actions(self) -> List[str]:
        return [a["text"] for a in self.actions if a.get("text")]

    def auto_actions(self) -> List[Dict[str, Any]]:
        """Resource-only changes the repair layer may consider (never science)."""
        return [a for a in self.actions if a.get("kind") == "auto"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage, "id": self.rule_id, "class": self.failure_class,
            "class_label": self.class_label, "title": self.title,
            "diagnosis": self.diagnosis, "evidence": self.evidence,
            "actions": self.actions, "exit_code": self.exit_code,
            "failed_command": self.failed_command, "log_file": self.log_file,
            "log_line": self.log_line, "matched": self.matched,
        }


def load_rules(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load failure signatures (empty list when unavailable)."""
    import yaml
    target = Path(path) if path else Path(__file__).with_name("rules") / _RULES_FILE
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or []
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _read_log_tail(log_path: Path, lines: int = _LOG_TAIL_LINES) -> List[str]:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return content.splitlines()[-lines:]


def find_log(results_dir: Path, stage: str) -> Optional[Path]:
    """Locate a stage's log, tolerating the scheduler's ``_%j`` suffixes."""
    log_dir = Path(results_dir) / "reports" / "logs"
    direct = log_dir / f"{stage}.log"
    if direct.is_file():
        return direct
    if log_dir.is_dir():
        matches = sorted(log_dir.glob(f"{stage}*.log"))
        if matches:
            return matches[-1]
    return None


def _matches(rule: Dict[str, Any], exit_code: Optional[int],
             log_lines: List[str]) -> Optional[str]:
    """Return the matching evidence line, or None. All given keys must hold."""
    match = rule.get("match") or {}
    if not match:
        return None

    if "exit_code" in match:
        if exit_code is None or int(match["exit_code"]) != int(exit_code):
            return None

    pattern = match.get("log_regex")
    if pattern:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return None
        for line in reversed(log_lines):
            if regex.search(line):
                return line.strip()
        return None

    # exit_code-only rule matched; cite the last log line as context.
    return log_lines[-1].strip() if log_lines else "(no log output)"


def diagnose(results_dir: Path, stage: str,
             exit_code: Optional[int] = None,
             log_lines: Optional[List[str]] = None,
             rules: Optional[List[Dict[str, Any]]] = None,
             status: Optional[Dict[str, Any]] = None) -> Diagnosis:
    """Diagnose ``stage``'s failure from the status file plus its log tail."""
    results = Path(results_dir)

    if status is None:
        status = {}
        status_path = results / "pipeline_status.json"
        if status_path.is_file():
            import json
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                status = {}

    step_info = (status.get("steps", {}) or {}).get(stage, {}) or {}
    last_failure = step_info.get("last_failure") or {}
    if not last_failure and (status.get("last_failure") or {}).get("stage") == stage:
        last_failure = status.get("last_failure") or {}

    if exit_code is None:
        raw = last_failure.get("exit_code", step_info.get("exit_code"))
        try:
            exit_code = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            exit_code = None

    failed_command = str(last_failure.get("command", "") or "")
    log_line = str(last_failure.get("line", "") or "")

    log_path = find_log(results, stage)
    if log_lines is None:
        log_lines = _read_log_tail(log_path) if log_path else []

    # Product validation failures are their own, precise story: prefer them over
    # pattern matching, because we already know exactly what was wrong.
    validation = step_info.get("product_validation") or {}
    if validation and validation.get("ok") is False:
        failures = [str(f) for f in validation.get("failures", [])]
        return Diagnosis(
            stage=stage, rule_id="products.invalid", failure_class="script_defect",
            title="The stage reported success but its products are unusable",
            diagnosis=("The script exited 0, yet the outputs the next stage needs "
                       "are missing or empty. This is caught deliberately: an "
                       "empty result must never be delivered as a real one."),
            evidence=failures,
            actions=[
                {"kind": "human",
                 "text": "Check the upstream stage that feeds this one: metaglens gate"},
                {"kind": "human",
                 "text": f"Read the full log: {log_path if log_path else 'reports/logs/'}"},
            ],
            exit_code=exit_code, failed_command=failed_command,
            log_file=str(log_path or ""), log_line=log_line, matched=True,
        )

    for rule in (rules if rules is not None else load_rules()):
        evidence = _matches(rule, exit_code, log_lines)
        if evidence is None:
            continue
        return Diagnosis(
            stage=stage,
            rule_id=str(rule.get("id", "")),
            failure_class=str(rule.get("class", "unknown")),
            title=str(rule.get("title", "")).strip(),
            diagnosis=" ".join(str(rule.get("diagnosis", "")).split()),
            evidence=[evidence] if evidence else [],
            actions=list(rule.get("actions") or []),
            exit_code=exit_code, failed_command=failed_command,
            log_file=str(log_path or ""), log_line=log_line, matched=True,
        )

    # Nothing matched. Say exactly that, and hand over the evidence — never
    # fabricate a cause.
    tail = [ln.strip() for ln in log_lines[-5:] if ln.strip()]
    return Diagnosis(
        stage=stage, rule_id="", failure_class="unknown",
        title="Unknown failure — no known signature matched",
        diagnosis=("This failure does not match any signature MetaGLens knows "
                   "about, so no cause is being guessed. The evidence below is "
                   "what the run recorded."),
        evidence=tail,
        actions=[
            {"kind": "human",
             "text": f"Read the log: {log_path if log_path else 'reports/logs/'}"},
            {"kind": "human", "text": "Check the environment: metaglens doctor"},
            {"kind": "human", "text": "Check stage outputs: metaglens gate"},
        ],
        exit_code=exit_code, failed_command=failed_command,
        log_file=str(log_path or ""), log_line=log_line, matched=False,
    )


def failed_stages(status: Dict[str, Any]) -> List[str]:
    """Stage ids currently marked failed, in the recorded step order."""
    steps = status.get("steps", {}) or {}
    order = status.get("selected_steps") or list(steps)
    return [s for s in order if (steps.get(s, {}) or {}).get("status") == "failed"]
