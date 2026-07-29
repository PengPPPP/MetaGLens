"""Sense layer: read-only environment probing.

Every module here answers "what is the current situation" without mutating
anything. All functions are pure stdlib and degrade gracefully when optional
enhancements (e.g. psutil) are unavailable, so the layer works offline on a
bare interpreter.
"""

from __future__ import annotations

from .hardware import HardwareInfo, probe
from .database import (
    DbSpec,
    DbStatus,
    REGISTRY,
    discover,
    required_databases,
    validate,
)

__all__ = [
    "HardwareInfo",
    "probe",
    "DbSpec",
    "DbStatus",
    "REGISTRY",
    "discover",
    "required_databases",
    "validate",
]
