"""Live terminal dashboard.

Reads the shared collection layer (:mod:`metaglens.observe.monitor`) so the
terminal view and the self-refreshing HTML page can never disagree.

Two behaviours matter more than the layout:

* **Quiet is not stalled.** A stage with no recent output is shown with its
  heartbeat ("no output for 12m — normal for assemblers"), never as an error.
* **Leaving the view never touches the run.** ``watch`` attaches read-only;
  quitting closes the display and nothing else. A dashboard that could kill a
  twelve-hour assembly by accident would be worse than no dashboard.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

_STATUS_STYLE = {
    "completed": ("green", "✓"),
    "running": ("yellow", "⟳"),
    "failed": ("bold red", "✗"),
    "pending": ("dim", "·"),
}

_BAR_WIDTH = 24


def _bar(fraction: Optional[float], status: str) -> str:
    """A text progress bar; indeterminate stages get a dashed track."""
    if status == "completed":
        return "█" * _BAR_WIDTH
    if status in ("pending",):
        return "░" * _BAR_WIDTH
    if fraction is None:
        return "▒" * _BAR_WIDTH          # running, unknown fraction
    filled = max(0, min(_BAR_WIDTH, int(round(fraction * _BAR_WIDTH))))
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def render(data: Dict[str, Any], quit_hint: bool = True):
    """Build the renderable dashboard (a Rich group)."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    header = Text()
    header.append(f"{data.get('project') or 'MetaGLens'}", style="bold")
    header.append(f"  {data.get('route', '')}", style="dim")
    header.append(f"   stages {data.get('completed', 0)}/"
                  f"{data.get('total_steps', 0)}", style="dim")

    progress = data.get("progress") or {}
    current = data.get("current")

    table = Table.grid(padding=(0, 1))
    table.add_column(width=3)
    table.add_column(min_width=14)
    table.add_column(width=_BAR_WIDTH)
    table.add_column()
    for step in data.get("steps", []):
        style, icon = _STATUS_STYLE.get(step["status"], ("white", "?"))
        fraction = (progress.get("fraction")
                    if step["step"] == current else None)
        detail = ""
        if step["step"] == current:
            detail = str(progress.get("detail", "") or step["status"])
        elif step["status"] == "completed" and step.get("finished"):
            detail = f"finished {step['finished']}"
        elif step["status"] == "failed":
            detail = "failed"
        table.add_row(Text(icon, style=style),
                      Text(step["step"], style=style),
                      Text(_bar(fraction, step["status"]), style=style),
                      Text(detail, style="dim"))

    blocks = [header, table]

    active = progress.get("active") or []
    if active:
        blocks.append(Text(f"in flight: {', '.join(str(a) for a in active)}",
                           style="dim"))

    # Heartbeat, phrased so silence never reads as a failure.
    heartbeat = progress.get("heartbeat")
    if heartbeat:
        blocks.append(Text(str(heartbeat), style="dim"))

    resources = data.get("resources") or {}
    if resources:
        parts = []
        if resources.get("cpu_percent") is not None:
            parts.append(f"CPU ~{float(resources['cpu_percent']):.0f}%")
        if resources.get("ram_total_gb"):
            parts.append(f"RAM {float(resources.get('ram_used_gb') or 0):.0f}/"
                         f"{float(resources['ram_total_gb']):.0f} GB")
        if resources.get("disk_free_gb") is not None:
            parts.append(f"disk free {float(resources['disk_free_gb']):.0f} GB")
        if parts:
            blocks.append(Text(" · ".join(parts), style="cyan"))

    failure = data.get("last_failure") or {}
    if failure.get("stage"):
        blocks.append(Text(
            f"last failure: {failure.get('stage')} — {failure.get('command','')} "
            f"(exit {failure.get('exit_code','?')})", style="bold red"))

    log_tail = (data.get("log_tail") or "").strip()
    if log_tail:
        lines = log_tail.splitlines()[-8:]
        blocks.append(Panel(Text("\n".join(lines), style="dim"),
                            title=data.get("log_file") or "log",
                            border_style="dim"))

    if quit_hint:
        blocks.append(Text(
            "Ctrl-C leaves this view — the pipeline keeps running.", style="dim"))

    return Group(*blocks)


def watch(results_dir: Path, interval: float = 2.0,
          console=None, once: bool = False,
          max_iterations: Optional[int] = None) -> Dict[str, Any]:
    """Attach to a run and display it live. Read-only; never signals the run.

    Returns the last collected snapshot. ``Ctrl-C`` (or ``q`` in a terminal that
    delivers it as an interrupt) exits the display only — no process is touched.
    """
    from rich.console import Console
    from rich.live import Live
    from ..observe.monitor import collect

    console = console or Console()
    results_dir = Path(results_dir)

    data = collect(results_dir)
    if once:
        console.print(render(data, quit_hint=False))
        return data

    iterations = 0
    try:
        with Live(render(data), console=console, refresh_per_second=4,
                  transient=False) as live:
            while True:
                time.sleep(interval)
                iterations += 1
                data = collect(results_dir)
                live.update(render(data))
                if max_iterations is not None and iterations >= max_iterations:
                    break
                if data.get("current") is None and data.get("completed") \
                        and data["completed"] == data.get("total_steps"):
                    break
    except KeyboardInterrupt:
        # Leaving the view must never affect the run.
        console.print("[dim]Left the monitor view; the pipeline is unaffected.[/dim]")
    return data
