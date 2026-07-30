"""Observe layer: runtime visibility (self-refreshing monitor page)."""

from __future__ import annotations

# Submodules first, so a re-exported function can never shadow its own module.
from . import monitor as monitor
from . import progress as progress
from . import resources as resources

from .monitor import collect, render_html, write_monitor
from .progress import Progress, parse_log, parse_stage
from .resources import Sample, sample

__all__ = [
    "monitor", "progress", "resources",
    "collect", "render_html", "write_monitor",
    "Progress", "parse_log", "parse_stage",
    "Sample", "sample",
]
