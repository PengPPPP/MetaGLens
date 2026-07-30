"""Resource sampling for the live views.

stdlib first: ``/proc`` on Linux covers CPU and memory, and ``shutil.disk_usage``
covers disk. ``psutil`` is consulted when present but is never required, because
the deployment target cannot be assumed to have extra packages installed.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

_BYTES_PER_GB = 1024 ** 3
_KB_PER_GB = 1024 ** 2


@dataclass
class Sample:
    cpu_percent: Optional[float]      # None when it cannot be determined
    load1: Optional[float]
    cores: int
    ram_used_gb: Optional[float]
    ram_total_gb: Optional[float]
    disk_used_gb: Optional[float]     # size of the watched directory tree
    disk_free_gb: Optional[float]
    timestamp: float

    @property
    def ram_percent(self) -> Optional[float]:
        if not self.ram_total_gb:
            return None
        return 100.0 * (self.ram_used_gb or 0.0) / self.ram_total_gb

    def as_dict(self) -> Dict[str, object]:
        return {
            "cpu_percent": self.cpu_percent, "load1": self.load1,
            "cores": self.cores, "ram_used_gb": self.ram_used_gb,
            "ram_total_gb": self.ram_total_gb, "ram_percent": self.ram_percent,
            "disk_used_gb": self.disk_used_gb, "disk_free_gb": self.disk_free_gb,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        parts = []
        if self.cpu_percent is not None:
            parts.append(f"CPU {self.cpu_percent:.0f}%")
        elif self.load1 is not None:
            parts.append(f"load {self.load1:.1f}/{self.cores}")
        if self.ram_total_gb:
            parts.append(f"RAM {self.ram_used_gb:.0f}/{self.ram_total_gb:.0f} GB")
        if self.disk_used_gb is not None:
            parts.append(f"disk +{self.disk_used_gb:.1f} GB")
        if self.disk_free_gb is not None:
            parts.append(f"free {self.disk_free_gb:.0f} GB")
        return " · ".join(parts) or "resource data unavailable"


def _meminfo() -> Dict[str, int]:
    values: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    try:
                        values[key] = int(parts[1])
                    except ValueError:
                        continue
    except OSError:
        return {}
    return values


def _ram_used_total_gb() -> tuple:
    info = _meminfo()
    total = info.get("MemTotal")
    available = info.get("MemAvailable")
    if total:
        total_gb = total / _KB_PER_GB
        if available is not None:
            return (total - available) / _KB_PER_GB, total_gb
        return None, total_gb
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        return vm.used / _BYTES_PER_GB, vm.total / _BYTES_PER_GB
    except Exception:
        return None, None


def _load1() -> Optional[float]:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


def _cpu_percent_from_load(load: Optional[float], cores: int) -> Optional[float]:
    """Approximate utilisation from load average.

    Load is not instantaneous CPU%, but it needs no sampling interval and no
    extra dependency; the dashboards label it accordingly.
    """
    if load is None or cores <= 0:
        return None
    return min(100.0, 100.0 * load / cores)


def dir_size_gb(path: Path, max_entries: int = 200_000) -> Optional[float]:
    """Total size of a directory tree, bounded so a huge tree cannot stall a UI."""
    root = Path(path)
    if not root.is_dir():
        return None
    total = 0
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        for name in filenames:
            seen += 1
            if seen > max_entries:
                return total / _BYTES_PER_GB
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total / _BYTES_PER_GB


def sample(watch_dir: Optional[Path] = None,
           measure_disk: bool = True) -> Sample:
    """Take one resource sample. Every field degrades to None on failure."""
    cores = os.cpu_count() or 1
    load = _load1()
    used, total = _ram_used_total_gb()

    disk_used = None
    disk_free = None
    if watch_dir is not None:
        target = Path(watch_dir)
        if measure_disk:
            disk_used = dir_size_gb(target)
        probe = target
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            disk_free = shutil.disk_usage(str(probe)).free / _BYTES_PER_GB
        except OSError:
            disk_free = None

    return Sample(
        cpu_percent=_cpu_percent_from_load(load, cores),
        load1=load, cores=cores,
        ram_used_gb=used, ram_total_gb=total,
        disk_used_gb=disk_used, disk_free_gb=disk_free,
        timestamp=time.time(),
    )
