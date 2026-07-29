"""Hardware probing with stdlib fallbacks.

Answers "how many cores, how much RAM, how much free disk" using only the
standard library. ``psutil`` is consulted opportunistically for cross-checking
but is never required — the module returns a complete result without it, which
is mandatory for the offline / no-extra-dependency deployment target.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_MEMINFO = "/proc/meminfo"
_BYTES_PER_GB = 1024 ** 3
_KB_PER_GB = 1024 ** 2


@dataclass
class HardwareInfo:
    cores: int
    ram_gb: float
    disk_free_gb: float
    in_container: bool

    def summary(self) -> str:
        """One-line human summary (used by plan/doctor text output)."""
        return (
            f"{self.cores} cores / {self.ram_gb:.0f} GB RAM / "
            f"{self.disk_free_gb:.0f} GB free"
            + (" (container)" if self.in_container else "")
        )


def _read_meminfo_kb(meminfo_path: str = _MEMINFO) -> Optional[int]:
    """Return MemTotal in kB from /proc/meminfo, or None if unreadable."""
    try:
        with open(meminfo_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    # Format: "MemTotal:       1048576 kB"
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except (OSError, ValueError):
        return None
    return None


def _ram_gb(meminfo_path: str = _MEMINFO) -> float:
    """Total RAM in GB. Prefer /proc/meminfo; fall back to sysconf; else 0."""
    kb = _read_meminfo_kb(meminfo_path)
    if kb is not None:
        return kb / _KB_PER_GB
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        if page_size > 0 and phys_pages > 0:
            return (page_size * phys_pages) / _BYTES_PER_GB
    except (ValueError, OSError, AttributeError):
        pass
    return 0.0


def _disk_free_gb(path: str) -> float:
    """Free bytes on the filesystem holding ``path``, in GB (0 on failure)."""
    try:
        return shutil.disk_usage(path).free / _BYTES_PER_GB
    except (OSError, ValueError):
        return 0.0


def _in_container() -> bool:
    """Best-effort container detection; never raises."""
    if Path("/.dockerenv").exists():
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as handle:
            content = handle.read()
        return any(tok in content for tok in ("docker", "kubepods", "containerd", "lxc"))
    except OSError:
        return False


def probe(path: str = ".", meminfo_path: str = _MEMINFO) -> HardwareInfo:
    """Probe the host. ``path`` selects the filesystem for the free-disk figure.

    Optional ``psutil`` is used only to cross-check RAM when the stdlib figure
    came back as 0 (rare); it is never a hard dependency.
    """
    cores = os.cpu_count() or 1
    ram = _ram_gb(meminfo_path)
    if ram <= 0:
        ram = _psutil_ram_gb()
    disk = _disk_free_gb(path)
    return HardwareInfo(
        cores=cores,
        ram_gb=ram,
        disk_free_gb=disk,
        in_container=_in_container(),
    )


def _psutil_ram_gb() -> float:
    """Optional psutil fallback for RAM; returns 0 if psutil is absent."""
    try:
        import psutil  # type: ignore
    except Exception:
        return 0.0
    try:
        return psutil.virtual_memory().total / _BYTES_PER_GB
    except Exception:
        return 0.0
