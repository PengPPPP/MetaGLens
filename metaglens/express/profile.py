"""User-level profile: remember answers across projects.

Running a second project should not mean re-answering how many cores the machine
has or where the databases live. The profile supplies **defaults only** — an
explicit config value or CLI flag always wins, because silently overriding what
someone wrote down would be worse than asking again.

Follows ``XDG_CONFIG_HOME``. Every read degrades silently: a corrupt or
unreadable profile must never block a run.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

# Only settings that are genuinely machine- or user-level. Project-specific
# values (project_name, work_dir, raw_data_dir, route) are deliberately absent.
REMEMBERED_KEYS = ("total_threads", "db_dir", "conda_env", "conda_mode", "lang")

_FILENAME = "profile.yaml"


def config_home() -> Path:
    """Base config directory, honouring XDG_CONFIG_HOME."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "metaglens"


def profile_path() -> Path:
    return config_home() / _FILENAME


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the profile. Any problem yields an empty dict — never raises."""
    target = Path(path) if path else profile_path()
    try:
        import yaml
        with open(target, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in REMEMBERED_KEYS}


def save(values: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """Merge ``values`` into the profile. Returns True on success."""
    target = Path(path) if path else profile_path()
    keep = {k: v for k, v in values.items()
            if k in REMEMBERED_KEYS and v not in (None, "", [])}
    if not keep:
        return False
    merged = load(target)
    merged.update(keep)
    try:
        import yaml
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            yaml.safe_dump(merged, handle, sort_keys=True, allow_unicode=True,
                           default_flow_style=False)
        return True
    except Exception:
        return False


def defaults_for(explicit: Optional[Dict[str, Any]] = None,
                 path: Optional[Path] = None) -> Dict[str, Any]:
    """Profile values that do **not** collide with anything explicitly given.

    The asymmetry is the point: the profile fills gaps, it never overwrites.
    """
    explicit = explicit or {}
    stored = load(path)
    return {k: v for k, v in stored.items()
            if explicit.get(k) in (None, "", 0, [])}


def remember_from_config(cfg, path: Optional[Path] = None) -> bool:
    """Persist the reusable parts of a finished config."""
    return save({
        "total_threads": getattr(cfg, "total_threads", None),
        "db_dir": getattr(cfg, "db_dir", None),
        "conda_env": getattr(cfg, "conda_env", None),
        "conda_mode": getattr(cfg, "conda_mode", None),
    }, path)
