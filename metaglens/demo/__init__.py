"""Offline self-check with a stub toolchain (``metaglens demo``).

Produces **no scientific results**: every tool is a stub emitting the minimal
artefact the next stage reads. Its job is to prove the plumbing works — stage
control flow, status transitions, product validation, report and monitor
generation — with no network, no conda, and no reference databases.
"""

from __future__ import annotations

from .runner import DEMO_ROUTES, run_demo

__all__ = ["DEMO_ROUTES", "run_demo"]
