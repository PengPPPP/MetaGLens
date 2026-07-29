"""Interactive setup wizard with Rich-styled prompts.

Matches the GVLens family UX: numbered menus, colour-coded messages, and
``typer.prompt`` for user input.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import Config
from . import conda_env, routes, samples as samples_mod


def _ask(console: Console, prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = typer.prompt(f" {prompt}{suffix}", default=default or "")
        value = value.strip()
        if value:
            return value
        if default is not None:
            return default
        console.print("  [yellow]A value is required.[/yellow]")


def _ask_int(console: Console, prompt: str, default: int) -> int:
    while True:
        raw = _ask(console, prompt, str(default))
        try:
            return int(raw)
        except ValueError:
            console.print("  [yellow]Please enter a whole number.[/yellow]")


def _menu(console: Console, question: str, choices: List[str],
           default: str = "1") -> str:
    console.print(f"\n ? {question}")
    for i, choice in enumerate(choices, 1):
        console.print(f"   [{i}] {choice}")
    raw = typer.prompt("\n >", default=default)
    if raw.isdigit() and 1 <= int(raw) <= len(choices):
        return choices[int(raw) - 1]
    if raw in choices:
        return raw
    return choices[int(default) - 1] if default.isdigit() else choices[0]


def run_wizard(console: Console) -> Config:
    """Run the interactive project setup wizard."""
    console.print("[bold cyan]━━━ Project setup ━━━[/bold cyan]\n")
    cfg = Config()

    cfg.project_name = _ask(console, "Project name")
    cfg.raw_data_dir = _ask(console, "Raw-data directory (paired FASTQ files)")
    cfg.work_dir = _ask(console, "Work directory", f"./{cfg.project_name}")

    # ─── Sample discovery ────────────────────────────────────────────────
    console.print("\n[cyan]Discovering paired samples...[/cyan]")
    try:
        found, pattern = samples_mod.discover(cfg.raw_data_dir)
        cfg.sample_pattern = pattern

        table = Table(title=f"Discovered {len(found)} paired sample(s) — convention: {pattern}")
        table.add_column("#", justify="right")
        table.add_column("Sample ID", style="cyan")
        table.add_column("R1")
        table.add_column("R2")
        for i, s in enumerate(found[:10], 1):
            table.add_row(str(i), s.sample_id,
                          Path(s.r1).name, Path(s.r2).name)
        if len(found) > 10:
            table.add_row("...", f"+{len(found)-10} more", "", "")
        console.print(table)

        use = _menu(console, "Use these samples?", ["Yes", "No, I will provide a manifest"],
                    default="1")
        if use.startswith("No"):
            manifest = _ask(console, "Path to samples.tsv manifest", "")
            cfg.sample_manifest = manifest
    except samples_mod.SampleDiscoveryError as exc:
        console.print(f"[yellow]Sample discovery:[/yellow] {exc}")
        manifest = _ask(console, "Path to a samples.tsv manifest (or leave blank)", "")
        cfg.sample_manifest = manifest

    # ─── Route ───────────────────────────────────────────────────────────
    route_choices = [
        "mag_per_sample — per-sample MAG reconstruction",
        "mag_co_binning — co-assembly with multi-sample depth",
        "contig_based — contig-level analysis, no binning",
        "mag_and_contig — both branches",
        "custom — select steps manually",
    ]
    selected = _menu(console, "Analysis route:", route_choices, default="1")
    cfg.route_name = selected.split(" —")[0].strip()

    if cfg.route_name == "custom":
        catalogue = [s for s in routes.STEPS if s != "00_setup"]
        console.print(f"  Available steps: [dim]{', '.join(catalogue)}[/dim]")
        chosen = _ask(console, "Steps (comma-separated)", "")
        cfg.custom_steps = [s.strip() for s in chosen.split(",") if s.strip()]

    # ─── Execution ───────────────────────────────────────────────────────
    env_choices = ["local", "slurm", "sge"]
    cfg.exec_env = _menu(console, "Execution environment:", env_choices, default="1")
    cfg.total_threads = _ask_int(console, "Total available threads", 16)
    if cfg.exec_env in ("slurm", "sge"):
        cfg.memory = _ask(console, "Memory request per job (e.g. 64G)", cfg.memory)

    # ─── Conda ───────────────────────────────────────────────────────────
    console.print("")
    if conda_env.conda_available():
        envs = conda_env.list_envs()
        if envs:
            console.print(f"  [dim]Detected conda envs: {', '.join(envs[:15])}[/dim]")
    else:
        console.print(
            "  [yellow]conda not found[/yellow] — environment inspection is "
            "unavailable. Choose 'none' to rely on tools already on PATH."
        )

    conda_choices = [
        "reuse — use an existing environment",
        "create — build 3 grouped environments",
        "reuse_and_update — reuse, update selected tools, install missing ones",
        "none — rely on tools already on PATH",
    ]
    mode_sel = _menu(console, "Conda mode:", conda_choices, default="1")
    cfg.conda_mode = mode_sel.split(" —")[0].strip()

    if cfg.conda_mode == "none":
        cfg.conda_env = "none"
    else:
        cfg.conda_env = _ask(console, "Conda environment base name", cfg.project_name)
        if cfg.conda_mode in ("reuse", "reuse_and_update"):
            try:
                missing = conda_env.missing_tools(cfg.conda_env)
            except conda_env.EnvNotFound:
                # Distinct from "nothing installed": never offer to install into
                # an environment that does not exist.
                console.print(
                    f"  [bold red]Environment '{cfg.conda_env}' does not exist.[/bold red]"
                )
                console.print(
                    "  [dim]Create it first with "
                    f"[cyan]metaglens setup-env -n {cfg.conda_env}[/cyan], "
                    "or re-run init and pick an existing environment.[/dim]"
                )
                missing = []
            except conda_env.CondaError as exc:
                console.print(f"  [yellow]Could not inspect environment:[/yellow] {exc}")
                missing = []
            if missing:
                console.print(f"  [yellow]Missing:[/yellow] {', '.join(missing)}")
                record = _menu(console, "Record these for installation at setup?",
                               ["Yes", "No"], default="1")
                if record == "Yes":
                    cfg.missing_tools = missing
        if cfg.conda_mode == "reuse_and_update":
            # Only tools the user names are updated; a blanket update can
            # destabilize unrelated packages in a shared environment.
            try:
                installed = [t for t, v in conda_env.inventory(cfg.conda_env).items()
                             if v != "missing"]
            except conda_env.CondaError:
                installed = []
            if installed:
                console.print(f"  [dim]Installed tools: {', '.join(installed)}[/dim]")
                chosen = _ask(console,
                              "Tools to update (comma-separated, blank = none)", "")
                wanted = [t.strip() for t in chosen.split(",") if t.strip()]
                unknown = [t for t in wanted if t not in installed]
                if unknown:
                    console.print(
                        f"  [yellow]Not detected in {cfg.conda_env}:[/yellow] "
                        f"{', '.join(unknown)} (kept anyway; verify the names)."
                    )
                cfg.update_tools = wanted
            else:
                console.print(
                    "  [yellow]No pipeline tools detected in that environment; "
                    "nothing to update.[/yellow]"
                )

    # ─── Databases ───────────────────────────────────────────────────────
    cfg.db_dir = _ask(console, "Database directory",
                      str(Path(cfg.work_dir) / "databases"))
    dl = _menu(console,
               "Auto-download reference databases at setup? (can exceed 200 GB)",
               ["No (I will prepare them manually)", "Yes"], default="1")
    cfg.download_dbs = dl == "Yes"

    console.print(
        "\n[dim]Advanced stage parameters use sensible defaults; "
        "edit config.yaml to change assembler, thresholds, DB paths, etc.[/dim]"
    )
    return cfg
