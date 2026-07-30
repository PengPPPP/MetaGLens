"""Environment health check backing ``metaglens doctor``.

Builds a structured, read-only report: tool availability (per conda env *and*
whether the command is actually runnable), database readiness, and hardware
headroom. Rendering lives in the CLI; everything here is a plain dict so
``--json`` and the table view share one source of truth.

Ruling D-2: tools the selected route never invokes are still listed, labelled
``not_needed``, and never counted as problems. Only entries in
:func:`metaglens.sense.tools.required_tools` can make the report fail.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import conda_env
from . import database, hardware, tools as tools_mod


def _command_available(command: str, env_prefix: Optional[str]) -> Dict[str, Any]:
    """Where the executable can be found: the env's bin dir and/or current PATH."""
    on_path = shutil.which(command)
    in_env = None
    if env_prefix:
        candidate = Path(env_prefix) / "bin" / command
        if candidate.exists() and os.access(str(candidate), os.X_OK):
            in_env = str(candidate)
    return {"on_path": on_path, "in_env": in_env,
            "runnable": bool(on_path or in_env)}


def build_report(cfg, env: Optional[str] = None) -> Dict[str, Any]:
    """Collect the doctor report for ``cfg`` (optionally overriding the env)."""
    env_name = env or cfg.conda_env or ""
    if env_name == "none":
        env_name = ""

    conda_exe = conda_env.find_conda()
    conda_info: Dict[str, Any] = {
        "available": conda_exe is not None,
        "executable": conda_exe or "",
        "env": env_name,
        "env_exists": None,
        "error": "",
    }

    packages: Dict[str, str] = {}
    prefix: Optional[str] = None
    if conda_exe and env_name:
        conda_info["env_exists"] = conda_env.env_exists(env_name)
        if conda_info["env_exists"]:
            prefix = conda_env.env_prefixes().get(env_name)
            try:
                packages = conda_env.installed_packages(env_name)
            except conda_env.CondaError as exc:
                conda_info["error"] = str(exc)
        else:
            conda_info["error"] = (
                f"conda environment not found: '{env_name}' — create it with "
                f"'metaglens setup-env -n {env_name}' or pick another with --env"
            )
    elif not conda_exe:
        conda_info["error"] = (
            "conda was not found; tool checks fall back to the current PATH"
        )

    required = tools_mod.required_tools(cfg)

    tool_rows: List[Dict[str, Any]] = []
    for spec in tools_mod.TOOL_SPECS:
        version = packages.get(spec.tool, "")
        avail = _command_available(spec.command, prefix)
        is_required = spec.tool in required
        if not is_required:
            status = "not_needed"
        elif avail["runnable"] or version:
            status = "ok" if avail["runnable"] else "package_only"
        else:
            status = "missing"
        tool_rows.append({
            "tool": spec.tool,
            "command": spec.command,
            "group": spec.group,
            "required": is_required,
            "reason": required.get(spec.tool, "not needed by this route"),
            "version": version,
            "on_path": avail["on_path"] or "",
            "in_env": avail["in_env"] or "",
            "status": status,
        })

    db_rows: Dict[str, Any] = {}
    for name, reason in database.required_databases(cfg).items():
        st = database.discover(name, cfg)
        db_rows[name] = {
            "reason": reason,
            "state": st.state,
            "path": st.path,
            "version": st.version,
            "source": st.source,
            "detail": st.detail,
        }

    hw = hardware.probe(cfg.work_dir or ".")

    problems: List[str] = []
    warnings: List[str] = []
    for row in tool_rows:
        if row["status"] == "missing":
            problems.append(
                f"{row['tool']} is required ({row['reason']}) but neither the "
                f"package nor the '{row['command']}' command was found"
            )
        elif row["status"] == "package_only":
            warnings.append(
                f"{row['tool']} {row['version']} is installed in "
                f"'{env_name}' but '{row['command']}' is not on the current "
                f"PATH — activate the environment before running"
            )
    for name, row in db_rows.items():
        if row["state"] == "missing":
            problems.append(f"database '{name}' not found — {row['detail']}")
        elif row["state"] == "wrong_path":
            problems.append(f"database '{name}' path looks wrong — {row['detail']}")
    if conda_info["error"] and conda_info["env_exists"] is False:
        problems.append(conda_info["error"])

    return {
        "project": cfg.project_name,
        "route": cfg.route_name,
        "conda": conda_info,
        "tools": tool_rows,
        "databases": db_rows,
        "hardware": {
            "cores": hw.cores,
            "ram_gb": round(hw.ram_gb, 1),
            "disk_free_gb": round(hw.disk_free_gb, 1),
            "in_container": hw.in_container,
            "summary": hw.summary(),
        },
        "problems": problems,
        "warnings": warnings,
        "ok": not problems,
    }


def missing_required_tools(report: Dict[str, Any]) -> List[str]:
    """Tool names that ``--fix`` may install (required and genuinely absent)."""
    return [r["tool"] for r in report["tools"] if r["status"] == "missing"]
