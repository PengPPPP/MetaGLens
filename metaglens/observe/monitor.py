"""Live monitor page (approach S: self-refreshing static HTML).

No server. ``collect`` reads ``pipeline_status.json`` plus the tail of the
current stage's log; ``render_html`` produces a self-contained page that reuses
the shared visual identity (``_theme``) and refreshes itself with a
``<meta http-equiv="refresh">``. The user opens it with ``file://`` and it keeps
working after the run ends or crashes — the file always reflects the last
written state.

The terminal ``status`` view is unaffected; this is an additive surface.
"""

from __future__ import annotations

import html as _html
import json
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional

from .._theme import REPORT_CSS, LENS_SVG

_STATUS_COLOR = {
    "completed": "var(--good)",
    "running": "var(--warn)",
    "failed": "var(--bad)",
    "pending": "var(--line)",
}
_STATUS_WIDTH = {"completed": "100%", "running": "50%", "failed": "100%", "pending": "0%"}


def _log_tail(results_dir: Path, step_id: str, max_lines: int = 40) -> str:
    """Return the last ``max_lines`` of the given stage's log (best effort)."""
    if not step_id:
        return ""
    log_dir = results_dir / "reports" / "logs"
    candidate = log_dir / f"{step_id}.log"
    path: Optional[Path] = candidate if candidate.is_file() else None
    if path is None and log_dir.is_dir():
        matches = sorted(log_dir.glob(f"{step_id}*.log"))
        if matches:
            path = matches[-1]
    if path is None:
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def collect(results_dir: Path, max_log_lines: int = 40,
            with_resources: bool = True,
            measure_disk: bool = False) -> Dict[str, Any]:
    """Gather monitor data from pipeline_status.json + current stage log.

    This is the single collection layer: the self-refreshing HTML page and the
    terminal dashboard both read it, so the two views cannot drift apart.
    """
    results_dir = Path(results_dir)
    status_path = results_dir / "pipeline_status.json"
    status: Dict[str, Any] = {}
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = {}

    steps_info = status.get("steps", {}) or {}
    order: List[str] = status.get("selected_steps") or list(steps_info.keys())

    steps: List[Dict[str, Any]] = []
    for step_id in order:
        info = steps_info.get(step_id, {})
        steps.append({
            "step": step_id,
            "status": info.get("status", "pending"),
            "started": info.get("started", ""),
            "finished": info.get("finished", ""),
            "attempts": info.get("attempts", 0),
        })

    current = None
    for s in steps:
        if s["status"] == "running":
            current = s["step"]
            break
    if current is None:
        for s in steps:
            if s["status"] == "failed":
                current = s["step"]
                break

    log_tail = _log_tail(results_dir, current, max_log_lines) if current else ""

    last_failure = status.get("last_failure") or {}

    # Progress for the active stage, and a resource snapshot.
    progress: Dict[str, Any] = {}
    if current:
        try:
            from .progress import parse_stage
            progress = parse_stage(results_dir, current).as_dict()
        except Exception:
            progress = {}

    resources: Dict[str, Any] = {}
    if with_resources:
        try:
            from .resources import sample as sample_resources
            resources = sample_resources(results_dir,
                                         measure_disk=measure_disk).as_dict()
        except Exception:
            resources = {}

    completed = sum(1 for s in steps if s["status"] == "completed")

    return {
        "project": status.get("project_name", ""),
        "route": status.get("route_name", ""),
        "basis": status.get("analysis_basis", ""),
        "steps": steps,
        "current": current,
        "completed": completed,
        "total_steps": len(steps),
        "log_file": f"{current}.log" if current else "",
        "log_tail": log_tail,
        "last_failure": last_failure,
        "progress": progress,
        "resources": resources,
        "gates": status.get("gates", {}),
    }


def _load_logo_b64(results_dir: Path) -> str:
    local = results_dir / "report_logo.b64"
    if local.is_file():
        try:
            return local.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    try:
        return resources.files("metaglens.templates").joinpath(
            "report_logo.b64").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def render_html(data: Dict[str, Any], refresh: int = 5, logo_b64: str = "") -> str:
    """Render the self-contained, self-refreshing monitor page."""
    e = _html.escape
    logo_src = f"data:image/png;base64,{logo_b64}" if logo_b64 else ""
    logo_html = (f'<img class="logo" src="{logo_src}" alt="MetaGLens"/>'
                 if logo_src else '<span style="font-size:30px;font-weight:700;'
                 'color:var(--brand)">MetaGLens</span>')

    rows = []
    for s in data["steps"]:
        color = _STATUS_COLOR.get(s["status"], "var(--line)")
        width = _STATUS_WIDTH.get(s["status"], "0%")
        meta = f'{e(s["started"] or "—")} → {e(s["finished"] or "—")} (×{s["attempts"]})'
        rows.append(
            '<div class="tl-row">'
            f'<div class="tl-step">{e(s["step"])}</div>'
            f'<div class="tl-bar"><div class="tl-fill" style="width:{width};'
            f'background:{color}"></div></div>'
            f'<div class="tl-meta">{e(s["status"])} · {meta}</div></div>'
        )
    timeline = "\n".join(rows) or '<div class="empty">No pipeline state yet.</div>'

    current = data.get("current")
    cur_html = (f'<b>{e(current)}</b>' if current
                else '<span class="empty">no active stage</span>')

    progress = data.get("progress") or {}
    progress_html = ""
    if progress:
        bits = []
        if progress.get("determinate") and progress.get("fraction") is not None:
            pct = float(progress["fraction"]) * 100.0
            bits.append(
                f'<div class="tl-bar" style="margin:8px 0"><div class="tl-fill" '
                f'style="width:{pct:.0f}%;background:var(--blue)"></div></div>'
            )
        if progress.get("detail"):
            bits.append(f'<div>{e(str(progress["detail"]))}</div>')
        active = progress.get("active") or []
        if active:
            bits.append(f'<div class="empty">in flight: '
                        f'{e(", ".join(str(a) for a in active))}</div>')
        if progress.get("heartbeat"):
            # Silence is information, not a verdict.
            bits.append(f'<div class="empty">{e(str(progress["heartbeat"]))}</div>')
        progress_html = "".join(bits)

    resources = data.get("resources") or {}
    resource_html = ""
    if resources:
        parts = []
        if resources.get("cpu_percent") is not None:
            parts.append(f'CPU ~{float(resources["cpu_percent"]):.0f}%')
        if resources.get("ram_total_gb"):
            used = resources.get("ram_used_gb") or 0.0
            parts.append(f'RAM {float(used):.0f}/'
                         f'{float(resources["ram_total_gb"]):.0f} GB')
        if resources.get("disk_free_gb") is not None:
            parts.append(f'disk free {float(resources["disk_free_gb"]):.0f} GB')
        if parts:
            resource_html = ('<div class="chip">' + e(" · ".join(parts))
                             + "</div>")

    fail = data.get("last_failure") or {}
    fail_html = ""
    if fail and fail.get("stage"):
        fail_html = (
            '<div class="source-note" style="border-color:var(--bad)">'
            f'<b>Last failure:</b> {e(str(fail.get("stage","")))} — '
            f'{e(str(fail.get("command","")))} '
            f'(exit {e(str(fail.get("exit_code","")))}, line {e(str(fail.get("line","")))})'
            '</div>'
        )

    log_block = e(data.get("log_tail", "")) or "(no log output yet)"

    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8"/>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0"/>\n'
        f'<meta http-equiv="refresh" content="{int(refresh)}"/>\n'
        f'<title>{e(data.get("project",""))} — MetaGLens Monitor</title>\n'
        f'<style>{REPORT_CSS}\n'
        'pre.logtail{background:#0d1b2a;color:#cfe0f6;padding:14px;border-radius:10px;'
        'overflow-x:auto;font-family:monospace;font-size:13px;max-height:360px;}'
        '</style>\n</head>\n<body>\n'
        f'{LENS_SVG}\n'
        '<header>\n'
        f'{logo_html}\n'
        '<div class="headline"><div class="t">MetaGLens Monitor</div>'
        f'<div class="d">refreshes every {int(refresh)}s</div></div>\n</header>\n'
        '<div class="meta">\n'
        f'<div class="chip">Project: <b>{e(data.get("project",""))}</b></div>\n'
        f'<div class="chip">Route: <b>{e(data.get("route",""))}</b></div>\n'
        f'<div class="chip">Stages: <b>{data.get("completed", 0)}/'
        f'{data.get("total_steps", 0)}</b></div>\n'
        f'<div class="chip">Current stage: {cur_html}</div>\n'
        f'{resource_html}\n'
        '</div>\n<main>\n'
        f'{fail_html}\n'
        '<div class="card">\n'
        '<h2>Pipeline</h2>\n'
        f'{progress_html}\n'
        f'{timeline}\n</div>\n'
        '<div class="card">\n'
        f'<h2>Log — {e(data.get("log_file","") or "(none)")}</h2>\n'
        f'<pre class="logtail">{log_block}</pre>\n</div>\n'
        '</main>\n'
        '<footer>MetaGLens · Self-refreshing monitor · Reflects the last written state.</footer>\n'
        '</body>\n</html>\n'
    )


def write_monitor(results_dir: Path, refresh: int = 5) -> Path:
    """Collect state and (re)write ``<results_dir>/monitor.html``. Returns path."""
    results_dir = Path(results_dir)
    data = collect(results_dir)
    logo = _load_logo_b64(results_dir)
    html = render_html(data, refresh=refresh, logo_b64=logo)
    out = results_dir / "monitor.html"
    out.write_text(html, encoding="utf-8")
    return out
