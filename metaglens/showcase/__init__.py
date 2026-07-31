"""Showcase layer: a public-facing, read-only demo site.

Unlike :mod:`metaglens.express.webconfig` (loopback + one-time token, single
user), the showcase is meant to be reachable by hackathon judges, so it may bind
``0.0.0.0``. That makes its security posture the whole point: it exposes **only**
read-only demo endpoints. It never accepts a filesystem path or a command from a
request — every server-side artefact is reached through an opaque run id that
maps to a server-managed temporary directory, and the only thing it can ever run
is the fixed stub demo, never a command carried in the request.
"""

from __future__ import annotations

from .jobs import JobManager
from .server import build_app, serve, export_static

__all__ = ["JobManager", "build_app", "serve", "export_static"]
