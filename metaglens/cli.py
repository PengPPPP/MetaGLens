"""Command-line interface for MetaGLens.

Uses Typer + Rich to provide a UX consistent with the GVLens family:
same hexagonal-aperture motif, block-letter banner, colour-coded progress,
interactive numbered menus, and Rich tables for status output.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from metaglens import __version__, routes
from metaglens.config import Config
from metaglens import pipeline, render, conda_setup
from metaglens.pipeline import PipelineError
from metaglens.render import RenderError

console = Console()

app = typer.Typer(
    name="metaglens",
    help="Reproducible shotgun-metagenomics pipeline: reads → MAGs → annotation.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

# ─── Brand ───────────────────────────────────────────────────────────────────
_BRAND_COLOR = "#38A8F0"
_AUTHOR = "PengPPPP"
_AFFILIATION = "Fudan University"

_BANNER_WORDMARK = (
    "██   ██  ███████  ███████   █████    ██████  ██       ███████  ██   ██   ██████\n"
    "███ ███  ██         ██     ██   ██  ██       ██       ██       ███  ██  ██\n"
    "██ █ ██  █████      ██     ███████  ██  ███  ██       █████    ██ █ ██   █████\n"
    "██   ██  ██         ██     ██   ██  ██   ██  ██       ██       ██  ███       ██\n"
    "██   ██  ███████    ██     ██   ██   ██████  ███████  ███████  ██   ██  ██████"
)


def _render_banner() -> Text:
    banner = Text()
    banner.append("◜◜◜ ⬡ ◝◝◝\n", style=_BRAND_COLOR)
    banner.append(_BANNER_WORDMARK, style=f"bold {_BRAND_COLOR}")
    banner.append("\n")
    banner.append("Metagenomics", style=f"bold {_BRAND_COLOR}")
    banner.append(f" · Shotgun pipeline orchestrator · v{__version__}", style="dim")
    banner.append("\n")
    banner.append("Developed by ", style="dim")
    banner.append(_AUTHOR, style=_BRAND_COLOR)
    banner.append(f" (GitHub), {_AFFILIATION}", style="dim")
    banner.append("\n")
    return banner


def print_banner() -> None:
    console.print(_render_banner())


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


DEFAULT_CONFIG = "metaglens.yaml"

ConfigOpt = typer.Option(DEFAULT_CONFIG, "-c", "--config",
                         help="Path to the project config YAML.")


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _load_config(path: str) -> Config:
    if not Path(path).is_file():
        console.print(
            f"[bold red]Config not found:[/bold red] {path}\n"
            "Run [cyan]metaglens init[/cyan] to create one."
        )
        raise typer.Exit(code=2)
    return Config.from_yaml(path)


def _section(title: str) -> None:
    console.print(f"\n[bold cyan]━━━ {title} ━━━[/bold cyan]\n")


def _success(msg: str) -> None:
    console.print(f"[green]✓[/green] {msg}")


def _fail(msg: str) -> None:
    console.print(f"[bold red]✗[/bold red] {msg}")


# ─── Callback ────────────────────────────────────────────────────────────────
def _version_callback(value: bool) -> None:
    if value:
        console.print(f"MetaGLens {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version_flag: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Reproducible shotgun-metagenomics pipeline orchestrator."""
    if ctx.invoked_subcommand is None:
        print_banner()
        console.print(ctx.get_help())
        raise typer.Exit()


# ─── version ─────────────────────────────────────────────────────────────────
@app.command()
def version() -> None:
    """Show the installed MetaGLens version."""
    console.print(f"MetaGLens {__version__}")


# ─── init ────────────────────────────────────────────────────────────────────
@app.command()
def init(
    config: str = ConfigOpt,
    non_interactive: bool = typer.Option(False, "--non-interactive",
                                         help="Write a template config without prompting."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config."),
    project: Optional[str] = typer.Option(None, help="Project name (non-interactive)."),
    raw_data_dir: Optional[str] = typer.Option(None, "--raw-data-dir",
                                                help="Raw-data directory."),
    work_dir: Optional[str] = typer.Option(None, "--work-dir", help="Work directory."),
) -> None:
    """Create a project config via interactive wizard or template."""
    print_banner()
    if Path(config).exists() and not force:
        console.print(
            f"[bold red]Config already exists:[/bold red] {config}. "
            "Use [cyan]--force[/cyan] to overwrite."
        )
        raise typer.Exit(code=2)

    if non_interactive:
        cfg = Config(
            project_name=project or "my_project",
            raw_data_dir=raw_data_dir or "",
            work_dir=work_dir or f"./{project or 'my_project'}",
        )
        cfg.to_yaml(config)
        _success(f"Template config written to {config}.")
        console.print("[dim]Edit it before running.[/dim]")
        return

    # Offer terminal wizard (default) or the local web config page. Both write
    # the same metaglens.yaml through Config.validate(), so the choice is only
    # about the entry surface.
    use_web = False
    if _is_interactive():
        console.print("\n ? How would you like to configure this project?")
        console.print("   [1] Terminal wizard (default)")
        console.print("   [2] Web page (opens a local browser form)")
        choice = typer.prompt("\n >", default="1")
        use_web = str(choice).strip() == "2"

    if use_web:
        from metaglens.express import webconfig
        console.print(
            "\n[cyan]Starting the local web config...[/cyan] "
            "(loopback only; a one-time token is in the URL)."
        )
        console.print("[dim]No GUI? Port-forward the printed URL. Ctrl-C when done.[/dim]")
        webconfig.serve(config_path=config, open_browser=True)
        if Path(config).is_file():
            _success(f"Configuration saved to {config}.")
        return

    from metaglens.wizard import run_wizard
    cfg = run_wizard(console)
    cfg.to_yaml(config)
    _success(f"Configuration written to {config}.")
    console.print(
        f"\nNext: [cyan]metaglens validate -c {config}[/cyan], "
        f"then [cyan]metaglens run -c {config}[/cyan]."
    )


# ─── configure ─────────────────────────────────────────────────────────────
@app.command()
def configure(
    config: str = ConfigOpt,
    lang: str = typer.Option("zh", "--lang", help="UI language: zh or en."),
    no_browser: bool = typer.Option(False, "--no-browser",
                                     help="Do not auto-open a browser (headless)."),
) -> None:
    """Configure the project in a local web page (loopback + one-time token)."""
    print_banner()
    _section("Web configuration")
    from metaglens.express import webconfig
    console.print(
        "[cyan]Starting local config server...[/cyan] "
        "bound to 127.0.0.1 with a one-time token."
    )
    if no_browser:
        console.print("[dim]--no-browser: port-forward the printed URL, then open it.[/dim]")
    webconfig.serve(config_path=config, lang=lang, open_browser=not no_browser)
    if Path(config).is_file():
        _success(f"Configuration available at {config}.")


# ─── doctor ──────────────────────────────────────────────────────────────────
@app.command()
def doctor(
    config: str = ConfigOpt,
    env: Optional[str] = typer.Option(None, "--env", help="Conda env to inspect."),
    fix: bool = typer.Option(False, "--fix",
                             help="Install missing required tools (never upgrades)."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Check tools, databases, and hardware against what this route needs."""
    import json as _json
    from metaglens.sense import doctor as doctor_mod

    cfg = _load_config(config)
    report = doctor_mod.build_report(cfg, env=env)

    if as_json:
        console.print_json(_json.dumps(report, ensure_ascii=False))
        raise typer.Exit(code=0 if report["ok"] else 2)

    print_banner()
    conda = report["conda"]
    _section(f"Environment — {conda['env'] or '(none)'}")
    if conda["executable"]:
        console.print(f"[dim]conda: {conda['executable']}[/dim]")
    else:
        console.print("[yellow]conda not found; checking the current PATH only.[/yellow]")
    if conda["error"]:
        console.print(f"[yellow]{conda['error']}[/yellow]")

    table = Table(title="Tools")
    table.add_column("Tool", style="cyan")
    table.add_column("Group")
    table.add_column("Version")
    table.add_column("Status")
    table.add_column("Why")
    marks = {
        "ok": "[green]✓ ok[/green]",
        "package_only": "[yellow]⚠ not on PATH[/yellow]",
        "missing": "[bold red]✗ missing[/bold red]",
        "not_needed": "[dim]· not needed[/dim]",
    }
    for row in report["tools"]:
        why = row["reason"] if row["required"] else "not needed by this route"
        table.add_row(row["tool"], row["group"], row["version"] or "—",
                      marks.get(row["status"], row["status"]),
                      f"[dim]{why}[/dim]" if not row["required"] else why)
    console.print(table)

    if report["databases"]:
        db_table = Table(title="Databases (required by this route)")
        db_table.add_column("Database", style="cyan")
        db_table.add_column("State")
        db_table.add_column("Version")
        db_table.add_column("Path / hint")
        db_marks = {"ready": "[green]✓ ready[/green]",
                    "wrong_path": "[bold red]✗ wrong path[/bold red]",
                    "missing": "[bold red]✗ missing[/bold red]"}
        for name, row in report["databases"].items():
            db_table.add_row(name, db_marks.get(row["state"], row["state"]),
                             row["version"] or "—", row["path"] or row["detail"])
        console.print(db_table)

    hw = report["hardware"]
    console.print(f"\n[bold]Hardware:[/bold] {hw['summary']}")

    for warning in report["warnings"]:
        console.print(f"[yellow]⚠[/yellow] {warning}")
    for problem in report["problems"]:
        _fail(problem)

    if not report["problems"]:
        _success("No blocking problems for this route.")

    if fix:
        missing = doctor_mod.missing_required_tools(report)
        if not missing:
            console.print("\n[dim]--fix: nothing to install.[/dim]")
        else:
            target = conda["env"]
            if not target:
                _fail("--fix needs a conda environment (set conda_env or use --env).")
                raise typer.Exit(code=2)
            console.print(
                f"\n[bold]--fix will install into '{target}':[/bold] {', '.join(missing)}"
            )
            console.print("[dim]Only missing packages are installed; "
                          "nothing already present is upgraded.[/dim]")
            if not typer.confirm("Proceed?", default=False):
                console.print("[dim]Aborted; nothing was changed.[/dim]")
                raise typer.Exit(code=1)
            from metaglens import conda_env as ce
            exe = ce.find_conda() or "conda"
            argv = [exe, "install", "-n", target, "-y",
                    *conda_setup.CHANNELS, *missing]
            console.print(f"[dim]+ {' '.join(argv)}[/dim]")
            import subprocess
            rc = subprocess.run(argv).returncode
            if rc != 0:
                _fail(f"conda install failed (exit {rc}).")
                raise typer.Exit(code=2)
            _success("Installed. Re-run 'metaglens doctor' to confirm.")

    raise typer.Exit(code=0 if report["ok"] else 2)


# ─── db ──────────────────────────────────────────────────────────────────────
db_app = typer.Typer(help="Inspect and prepare reference databases.")
app.add_typer(db_app, name="db")


@db_app.command("list")
def db_list(
    config: str = ConfigOpt,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show the databases this route needs and whether they are ready."""
    import json as _json
    from metaglens.sense import database as db
    cfg = _load_config(config)
    out = {}
    for name, reason in db.required_databases(cfg).items():
        st = db.discover(name, cfg)
        out[name] = {"reason": reason, "state": st.state, "path": st.path,
                     "version": st.version, "source": st.source,
                     "detail": st.detail}
    if as_json:
        console.print_json(_json.dumps(out, ensure_ascii=False))
        return
    if not out:
        console.print("[dim]This route needs no reference databases.[/dim]")
        return
    table = Table(title=f"Databases — {cfg.route_name}")
    table.add_column("Database", style="cyan")
    table.add_column("State")
    table.add_column("Version")
    table.add_column("Found via")
    table.add_column("Path / hint")
    marks = {"ready": "[green]✓ ready[/green]",
             "wrong_path": "[bold red]✗ wrong path[/bold red]",
             "missing": "[bold red]✗ missing[/bold red]"}
    for name, row in out.items():
        table.add_row(name, marks.get(row["state"], row["state"]),
                      row["version"] or "—", row["source"] or "—",
                      row["path"] or row["detail"])
    console.print(table)


@db_app.command("status")
def db_status(
    config: str = ConfigOpt,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Alias for 'db list'."""
    db_list(config=config, as_json=as_json)


@db_app.command("where")
def db_where(
    name: str = typer.Argument(..., help="Database name (checkm2/gtdbtk/kraken2/eggnog)."),
    config: str = ConfigOpt,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Print the full resolution chain and which level provides the path."""
    import json as _json
    from metaglens.sense import database as db
    if name not in db.REGISTRY:
        _fail(f"Unknown database '{name}'. Known: {', '.join(db.REGISTRY)}.")
        raise typer.Exit(code=2)
    cfg = _load_config(config)
    chain = db.resolution_chain(name, cfg)
    if as_json:
        console.print_json(_json.dumps(
            {"name": name, "chain": chain}, ensure_ascii=False))
        return
    table = Table(title=f"Resolution chain — {name}")
    table.add_column("Order", justify="right")
    table.add_column("Level", style="cyan")
    table.add_column("Candidate")
    table.add_column("Result")
    for i, link in enumerate(chain, 1):
        table.add_row(str(i), link["level"], link["candidate"],
                      "[green]← used[/green]" if link["hit"]
                      else f"[dim]{link['detail']}[/dim]")
    console.print(table)


@db_app.command("verify")
def db_verify(
    name: str = typer.Argument(..., help="Database name."),
    path: Optional[str] = typer.Argument(None, help="Directory to verify."),
    config: str = ConfigOpt,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Read-only check that a directory really is this database."""
    import json as _json
    from metaglens.sense import database as db
    if name not in db.REGISTRY:
        _fail(f"Unknown database '{name}'. Known: {', '.join(db.REGISTRY)}.")
        raise typer.Exit(code=2)
    if path:
        ok, detail = db.validate(name, path)
        target = path
    else:
        cfg = _load_config(config)
        st = db.discover(name, cfg)
        ok, detail, target = st.state == "ready", st.detail, st.path
    if as_json:
        console.print_json(_json.dumps(
            {"name": name, "path": target, "ok": ok, "detail": detail},
            ensure_ascii=False))
    elif ok:
        _success(f"{name}: {detail} ({target})")
    else:
        _fail(f"{name}: {detail}")
    raise typer.Exit(code=0 if ok else 2)


@db_app.command("get")
def db_get(
    name: str = typer.Argument(..., help="Database name."),
    dest: str = typer.Argument(..., help="Explicit destination directory (required)."),
    config: str = ConfigOpt,
    as_json: bool = typer.Option(False, "--json", help="Preflight only, as JSON."),
) -> None:
    """Preflight and (after confirmation) fetch a database into DEST."""
    import json as _json
    from metaglens.sense import database as db
    if name not in db.REGISTRY:
        _fail(f"Unknown database '{name}'. Known: {', '.join(db.REGISTRY)}.")
        raise typer.Exit(code=2)
    cfg = _load_config(config)
    pre = db.plan_get(name, dest, cfg)

    if as_json:
        console.print_json(_json.dumps(pre, ensure_ascii=False))
        raise typer.Exit(code=0 if pre["enough_space"] else 2)

    _section(f"Database download preflight — {name}")
    console.print(f"Destination : {pre['dest']}")
    console.print(f"Size (approx): ~{pre['size_hint_gb']:.0f} GB")
    console.print(
        f"Space needed : ~{pre['required_gb']:.0f} GB "
        f"(includes a {pre['margin']}x extraction margin)"
    )
    console.print(f"Free on that filesystem: {pre['free_gb']:.0f} GB")
    if not pre["enough_space"]:
        _fail("Not enough free space — choose a destination on a larger filesystem.")
        raise typer.Exit(code=2)
    _success("Space check passed.")

    if not pre["command"]:
        console.print(
            f"\n[yellow]No automated fetch for '{name}'.[/yellow] Do this manually:"
        )
        console.print(f"  {pre['download_hint']}")
        console.print(
            f"\n[dim]Then: metaglens db verify {name} {pre['dest']}[/dim]")
        return

    console.print(f"\n[bold]Command:[/bold] {pre['command']}")
    console.print("[dim]This is a large download; nothing runs without your "
                  "confirmation.[/dim]")
    if not typer.confirm("Download now?", default=False):
        console.print("[dim]Aborted; nothing was downloaded.[/dim]")
        raise typer.Exit(code=1)
    Path(pre["dest"]).mkdir(parents=True, exist_ok=True)
    import shlex
    import subprocess
    rc = subprocess.run(shlex.split(pre["command"])).returncode
    if rc != 0:
        _fail(f"Download failed (exit {rc}). Nothing was verified.")
        raise typer.Exit(code=2)
    ok, detail = db.validate(name, pre["dest"])
    if ok:
        _success(f"{name} ready: {detail}")
    else:
        _fail(f"Download finished but validation failed: {detail}")
        raise typer.Exit(code=2)


# ─── gate ────────────────────────────────────────────────────────────────────
@app.command()
def gate(
    config: str = ConfigOpt,
    stage: Optional[str] = typer.Option(None, "--stage",
                                        help="Comma-separated stage ids (default: all)."),
    strict: bool = typer.Option(False, "--strict",
                                help="Treat warnings as blocking."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Check scientific quality metrics (QC retention, bins, MIMAG MAGs...)."""
    import json as _json
    from metaglens.decide import gates as gates_mod

    cfg = _load_config(config)
    results = cfg.results_dir
    if not results.is_dir():
        _fail("Results directory not found. Run the pipeline first.")
        raise typer.Exit(code=2)

    stages = [s.strip() for s in stage.split(",") if s.strip()] if stage else None
    gate_results = gates_mod.evaluate(results, stages=stages)
    summary = gates_mod.summarise(gate_results, strict=strict)

    # Persist so the report and later inspection see the same verdict.
    status_path = results / "pipeline_status.json"
    if status_path.is_file():
        try:
            with open(status_path, "r", encoding="utf-8") as handle:
                data = _json.load(handle)
            data["gates"] = summary
            with open(status_path, "w", encoding="utf-8") as handle:
                _json.dump(data, handle, indent=2, ensure_ascii=False)
        except (OSError, ValueError):
            pass

    if as_json:
        console.print_json(_json.dumps(summary, ensure_ascii=False))
        raise typer.Exit(code=0 if summary["ok"] else 2)

    print_banner()
    _section(f"Quality gates — {cfg.project_name}"
             + (" (strict)" if strict else ""))
    if not gate_results:
        console.print("[dim]No gate rules apply to the selected stage(s).[/dim]")
        raise typer.Exit(code=0)

    marks = {"pass": "[green]✓ pass[/green]",
             "warn": "[yellow]⚠ warn[/yellow]",
             "block": "[bold red]✗ block[/bold red]",
             "unknown": "[dim]· n/a[/dim]"}
    table = Table(title="Gates")
    table.add_column("Gate", style="cyan")
    table.add_column("Stage")
    table.add_column("Value")
    table.add_column("Status")
    for res in gate_results:
        table.add_row(res.gate_id, res.stage, res.detail,
                      marks.get(res.status, res.status))
    console.print(table)

    for res in gate_results:
        if res.status in ("warn", "block") and res.hint:
            console.print(f"\n[bold]{res.gate_id}[/bold] — {res.detail}")
            console.print(f"  [dim]{res.hint}[/dim]")

    counts = summary["counts"]
    console.print(
        f"\n{counts['pass']} passed · {counts['warn']} warning(s) · "
        f"{counts['block']} blocking · {counts['unknown']} not applicable"
    )
    if summary["ok"]:
        if counts["warn"] and not strict:
            console.print("[yellow]Warnings present but not blocking.[/yellow] "
                          "[dim]Use --strict to treat them as errors.[/dim]")
        else:
            _success("All applicable gates passed.")
    else:
        _fail(f"Blocking gates: {', '.join(summary['blocking'])}")
    raise typer.Exit(code=0 if summary["ok"] else 2)


# ─── demo ────────────────────────────────────────────────────────────────────
@app.command()
def demo(
    route: str = typer.Option("all", "--route",
                              help="Route to check, or 'all' for every demo route."),
    keep: bool = typer.Option(False, "--keep", help="Keep the temporary directory."),
    verbose: bool = typer.Option(False, "--verbose", help="Stream stub output."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Offline self-check: run the pipeline end-to-end with stub tools."""
    import json as _json
    from metaglens.demo import DEMO_ROUTES, run_demo

    targets = list(DEMO_ROUTES) if route == "all" else [route]
    if not as_json:
        print_banner()
        _section("Offline self-check (stub toolchain)")
        console.print(
            "[dim]Stub tools stand in for fastp/MEGAHIT/CheckM2/... — this checks "
            "the plumbing only and produces NO scientific results.[/dim]\n"
        )

    results = []
    for target in targets:
        if not as_json:
            console.print(f"[cyan]▶ {target}[/cyan]")
        try:
            res = run_demo(target, keep=keep, verbose=verbose)
        except ValueError as exc:
            _fail(str(exc))
            raise typer.Exit(code=2)
        results.append(res)
        if as_json:
            continue
        for stage in res["stages"]:
            icon = "[green]✓[/green]" if stage["status"] == "completed" else "[bold red]✗[/bold red]"
            console.print(f"   {icon} {stage['step']} "
                          f"[dim](exit {stage['exit_code']}, {stage['status']})[/dim]")
        if res["ok"]:
            _success(f"{target}: all stages completed, artefacts present.")
        else:
            for err in res["errors"]:
                _fail(err)
            for missing in res["missing"]:
                _fail(f"expected artefact missing: {missing}")
            console.print(f"[dim]Left for inspection: {res['root']}[/dim]")

    ok = all(r["ok"] for r in results)
    if as_json:
        console.print_json(_json.dumps({"ok": ok, "runs": results},
                                       ensure_ascii=False))
    else:
        console.print()
        if ok:
            _success(f"Self-check passed for {len(results)} route(s). "
                     "MetaGLens is wired up correctly on this machine.")
            console.print("[dim]Reminder: stub tools — no scientific output.[/dim]")
        else:
            _fail("Self-check failed. See the messages above.")
    raise typer.Exit(code=0 if ok else 2)


# ─── plan ────────────────────────────────────────────────────────────────────
@app.command()
def plan(
    config: str = ConfigOpt,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    plain: bool = typer.Option(False, "--plain",
                               help="Paste-able plain text (for resource requests)."),
) -> None:
    """Show what will run, rough time/RAM/disk, and anything that would block it."""
    import json as _json
    from metaglens.decide import plan as plan_mod

    cfg = _load_config(config)
    data = plan_mod.build_plan(cfg)

    if as_json:
        console.print_json(_json.dumps(data, ensure_ascii=False))
        raise typer.Exit(code=0 if data["ok"] else 2)

    if plain:
        # print() rather than console.print(): keep it copy-paste clean.
        print(plan_mod.render_plain(data), end="")
        raise typer.Exit(code=0 if data["ok"] else 2)

    print_banner()
    par = data["parallel"]
    _section(
        f"Plan — {data['project']} · {data['route']} · "
        f"{data['samples']} sample(s) · {par['jobs']}x{par['threads_per_job']}"
    )

    table = Table(title="Stages")
    table.add_column("Stage", style="cyan")
    table.add_column("Mode")
    table.add_column("Est. time", justify="right")
    table.add_column("Peak RAM", justify="right")
    table.add_column("Disk Δ", justify="right")
    for stage in data["stages"]:
        table.add_row(stage["step"], stage["mode"],
                      plan_mod._hm(stage["minutes"]),
                      f"{stage['peak_ram_gb']:.0f} GB",
                      f"{stage['disk_gb']:.0f} GB")
    totals = data["totals"]
    table.add_row("[bold]TOTAL[/bold]", "",
                  f"[bold]{plan_mod._hm(totals['minutes'])}[/bold]",
                  f"[bold]{totals['peak_ram_gb']:.0f} GB[/bold]",
                  f"[bold]{totals['disk_gb']:.0f} GB[/bold]")
    console.print(table)

    band = int(data["estimate"]["band"] * 100)
    console.print(
        f"[dim]Estimates are coarse (±{band}%), based on "
        f"{data['estimate']['reference']}. Real runtime depends heavily on the "
        f"data.[/dim]"
    )
    console.print(f"[dim]Host: {data['hardware']['summary']}[/dim]")
    console.print(f"[dim]Parallel: {par['reason']}[/dim]")

    if data["databases"]:
        db_table = Table(title="Required databases")
        db_table.add_column("Database", style="cyan")
        db_table.add_column("State")
        db_table.add_column("Version")
        db_table.add_column("Path / hint")
        marks = {"ready": "[green]✓ ready[/green]",
                 "wrong_path": "[bold red]✗ wrong path[/bold red]",
                 "missing": "[bold red]✗ missing[/bold red]"}
        for name, row in data["databases"].items():
            db_table.add_row(name, marks.get(row["state"], row["state"]),
                             row["version"] or "—", row["path"] or row["detail"])
        console.print(db_table)

    for warning in data["db_warnings"] + data["resource_warnings"]:
        _fail(warning)
    if data["ok"]:
        _success("Nothing blocking — ready to run.")
    else:
        console.print("\n[dim]Resolve the items above, then re-run "
                      "[cyan]metaglens plan[/cyan].[/dim]")

    console.print("\n[dim]Need a summary to send to an admin? "
                  "[cyan]metaglens plan --plain[/cyan][/dim]")
    raise typer.Exit(code=0 if data["ok"] else 2)


# ─── validate ────────────────────────────────────────────────────────────────
@app.command()
def validate(config: str = ConfigOpt) -> None:
    """Validate configuration and dry-render every stage script."""
    cfg = _load_config(config)
    errors = cfg.validate()
    if errors:
        _section("Validation errors")
        for e in errors:
            _fail(e)
        raise typer.Exit(code=2)
    try:
        sample_ids = [s.sample_id for s in pipeline.resolve_samples(cfg)]
    except Exception as exc:
        _fail(f"Sample resolution failed: {exc}")
        raise typer.Exit(code=2)

    _section("Dry render")
    for step_id in cfg.route.steps:
        try:
            render.render_step(cfg, step_id, sample_ids)
            _success(f"{step_id} ({routes.STEPS[step_id].script})")
        except RenderError as exc:
            _fail(f"{step_id}: {exc}")
            raise typer.Exit(code=2)
    console.print(
        f"\n[bold green]OK:[/bold green] {len(cfg.route.steps)} scripts render "
        f"cleanly for {len(sample_ids)} sample(s)."
    )


# ─── run ─────────────────────────────────────────────────────────────────────
@app.command()
def run(
    config: str = ConfigOpt,
    dry_run: bool = typer.Option(False, "--dry-run",
                                  help="Render and syntax-check only."),
    only: Optional[str] = typer.Option(None, help="Comma-separated steps to run."),
    from_step: Optional[str] = typer.Option(None, "--from",
                                             help="Resume from this step."),
    strict_gates: bool = typer.Option(False, "--strict-gates",
                                       help="Stop on quality-gate warnings too."),
) -> None:
    """Materialize scripts and execute the pipeline."""
    print_banner()
    cfg = _load_config(config)
    _section("Materialize")
    try:
        scripts = pipeline.materialize(cfg)
    except (PipelineError, RenderError) as exc:
        _fail(str(exc))
        raise typer.Exit(code=2)
    _success(f"{len(scripts)} scripts rendered → {cfg.results_dir}")

    if dry_run:
        console.print("\n[dim]--dry-run: nothing executed.[/dim]")
        raise typer.Exit()

    _section("Execute pipeline")
    only_list = [s.strip() for s in only.split(",") if s.strip()] if only else None
    try:
        step_list = pipeline.select_steps(cfg, only=only_list, from_step=from_step)
    except PipelineError as exc:
        _fail(str(exc))
        raise typer.Exit(code=2)

    from metaglens.decide import gates as gates_mod

    for step_id in step_list:
        if pipeline.step_status(cfg, step_id) == "completed":
            console.print(f"  [dim]{step_id} — already completed, skipping.[/dim]")
            continue
        console.print(f"  [cyan]{step_id}[/cyan] ({routes.STEPS[step_id].script})")
        with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                      TimeElapsedColumn(), console=console) as progress:
            task = progress.add_task(f"Running {step_id}...", total=None)
            rc = pipeline.run_step(cfg, step_id)
            progress.update(task, completed=1, total=1)
        if rc != 0:
            _fail(f"{step_id} failed (exit {rc}). Check reports/logs/.")
            raise typer.Exit(code=2)
        _success(f"{step_id} completed.")

        # Scientific gates for the stage that just finished.
        stage_gates = gates_mod.evaluate(cfg.results_dir, stages=[step_id])
        summary = gates_mod.summarise(stage_gates, strict=strict_gates)
        for res in stage_gates:
            if res.status == "warn":
                console.print(f"    [yellow]⚠[/yellow] {res.detail}")
                if res.hint:
                    console.print(f"      [dim]{res.hint}[/dim]")
            elif res.status == "block":
                _fail(f"    {res.detail}")
                if res.hint:
                    console.print(f"      [dim]{res.hint}[/dim]")
        if not summary["ok"]:
            _fail(f"{step_id}: quality gate(s) blocked the run "
                  f"({', '.join(summary['blocking'])}).")
            console.print("[dim]Inspect with [cyan]metaglens gate[/cyan]; "
                          "gates are configured in decide/rules/gates.yaml.[/dim]")
            raise typer.Exit(code=2)

    console.print("\n[bold green]Pipeline finished.[/bold green]")


# ─── resume ──────────────────────────────────────────────────────────────────
@app.command()
def resume(config: str = ConfigOpt) -> None:
    """Resume from the first incomplete step."""
    cfg = _load_config(config)
    start = pipeline.first_incomplete_step(cfg)
    if start is None:
        _success("All selected steps already completed.")
        raise typer.Exit()
    console.print(f"Resuming from [cyan]{start}[/cyan].\n")
    try:
        pipeline.materialize(cfg)
        pipeline.run(cfg, from_step=start)
    except (PipelineError, RenderError) as exc:
        _fail(str(exc))
        raise typer.Exit(code=2)
    console.print("\n[bold green]Pipeline finished.[/bold green]")


# ─── status ──────────────────────────────────────────────────────────────────
@app.command()
def status(config: str = ConfigOpt) -> None:
    """Show pipeline stage progress."""
    cfg = _load_config(config)
    data = pipeline.read_status(cfg)
    route = cfg.route

    table = Table(title=f"{cfg.project_name} — {route.name}")
    table.add_column("Stage", style="cyan")
    table.add_column("Script")
    table.add_column("Status")
    table.add_column("Attempts", justify="right")

    icons = {"completed": "[green]✓ done[/green]",
             "running": "[yellow]⟳ running[/yellow]",
             "failed": "[bold red]✗ FAILED[/bold red]",
             "pending": "[dim]· pending[/dim]"}

    steps = data.get("steps", {}) if data else {}
    for step_id in route.steps:
        st = steps.get(step_id, {}).get("status", "pending")
        attempts = str(steps.get(step_id, {}).get("attempts", 0) or "")
        table.add_row(step_id, routes.STEPS[step_id].script, icons.get(st, st), attempts)

    console.print(table)
    if data:
        lf = data.get("last_failure")
        if lf and steps.get(lf.get("stage"), {}).get("status") == "failed":
            console.print(
                f"\n[bold red]Last failure:[/bold red] {lf.get('stage')} — "
                f"{lf.get('command')} (exit {lf.get('exit_code')}, line {lf.get('line')})"
            )


# ─── monitor ─────────────────────────────────────────────────────────────────
@app.command()
def monitor(
    config: str = ConfigOpt,
    interval: int = typer.Option(5, "--interval", help="Refresh seconds."),
    once: bool = typer.Option(False, "--once", help="Write one snapshot and exit."),
) -> None:
    """Write a self-refreshing monitor.html (open it with file://; side-car)."""
    from metaglens.observe import monitor as monitor_mod
    cfg = _load_config(config)
    results = cfg.results_dir
    if not results.is_dir():
        _fail("Results directory not found. Run the pipeline first.")
        raise typer.Exit(code=2)
    out = monitor_mod.write_monitor(results, refresh=interval)
    console.print(f"[green]✓[/green] Monitor page: file://{out}")
    if once:
        return
    console.print(f"[dim]Rewriting every {interval}s. Ctrl-C to stop.[/dim]")
    import time
    try:
        while True:
            time.sleep(interval)
            monitor_mod.write_monitor(results, refresh=interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Monitor stopped (monitor.html keeps its last state).[/dim]")


# ─── report ──────────────────────────────────────────────────────────────────
@app.command()
def report(config: str = ConfigOpt) -> None:
    """(Re)build the interactive delivery report."""
    from metaglens.report import generate_report
    cfg = _load_config(config)
    results = cfg.results_dir
    if not results.is_dir():
        _fail("Results directory not found. Run the pipeline first.")
        raise typer.Exit(code=2)
    _section("Report generation")
    console.print("[cyan]Building delivery report...[/cyan]")
    try:
        out = generate_report(results, raw_data_dir=cfg.raw_data_dir)
        _success(f"Report written: {out}")
    except Exception as exc:
        _fail(f"Report generation failed: {exc}")
        raise typer.Exit(code=2)


# ─── methods ─────────────────────────────────────────────────────────────────
@app.command()
def methods(config: str = ConfigOpt) -> None:
    """Print the generated Methods text."""
    cfg = _load_config(config)
    path = cfg.results_dir / "reports" / "methods.md"
    if not path.is_file():
        console.print("[yellow]methods.md not yet available (produced during the run).[/yellow]")
        raise typer.Exit(code=1)
    console.print(path.read_text(encoding="utf-8"))


# ─── routes ──────────────────────────────────────────────────────────────────
@app.command("routes")
def list_routes() -> None:
    """List available analysis routes and their steps."""
    table = Table(title="MetaGLens routes")
    table.add_column("Route", style="cyan")
    table.add_column("Basis")
    table.add_column("Binning")
    table.add_column("Steps")

    for name, r in routes.ROUTES.items():
        table.add_row(name, r.analysis_basis, r.binning_strategy,
                      " → ".join(r.steps))
    table.add_row("custom", "derived", "derived",
                  "[dim]user-selected subset[/dim]")
    console.print(table)


# ─── setup-env ───────────────────────────────────────────────────────────────
@app.command("setup-env")
def setup_env(
    config: str = ConfigOpt,
    name: Optional[str] = typer.Option(None, "-n", "--name",
                                        help="Environment base name."),
    single: bool = typer.Option(False, "--single",
                                 help="One environment instead of 3 groups."),
    groups: Optional[str] = typer.Option(None, help="Comma-separated groups."),
    dry_run: bool = typer.Option(False, "--dry-run",
                                  help="Preview commands without executing."),
) -> None:
    """One-shot creation of conda environments for the pipeline."""
    print_banner()
    _section("Conda environment setup")

    if not name:
        if not _is_interactive():
            _fail("--name is required in non-interactive mode.")
            raise typer.Exit(code=2)
        console.print(" ? Enter an environment base name (e.g. metaglens):")
        name = typer.prompt("\n >", default="metaglens")
    if not name:
        _fail("A name is required.")
        raise typer.Exit(code=2)

    if not single and not groups and _is_interactive() and not dry_run:
        console.print("\n ? How should tools be organized?")
        console.print("   [1] 3-group split (recommended — avoids dependency conflicts)")
        console.print("   [2] Single environment (all tools together)")
        choice = typer.prompt("\n >", default="1")
        if choice == "2":
            single = True

    grp_list = [g.strip() for g in groups.split(",") if g.strip()] if groups else None
    mode_label = "single" if single else "3-group"
    console.print(f"\n[cyan]Creating environments (base={name}, {mode_label})...[/cyan]\n")

    try:
        created = conda_setup.create_environments(
            base=name, groups=grp_list, single=single, dry_run=dry_run
        )
    except conda_setup.CondaSetupError as exc:
        _fail(str(exc))
        raise typer.Exit(code=2)

    if dry_run:
        console.print(f"\n[dim]Would create: {', '.join(created)}[/dim]")
    else:
        _success(f"Created: {', '.join(created)}")
        if Path(config).is_file():
            cfg = Config.from_yaml(config)
            cfg.conda_env = name
            cfg.conda_mode = "reuse" if single else "create"
            cfg.conda_origin = "metaglens setup-env"
            cfg.to_yaml(config)
            console.print(
                f"\n[dim]Updated {config}: conda_env={name}, "
                f"conda_mode={'reuse' if single else 'create'}.[/dim]"
            )
        else:
            console.print(
                f"\n[dim]Set conda_env: {name} and conda_mode: "
                f"{'reuse' if single else 'create'} in your config.[/dim]"
            )


# ─── Entry point ─────────────────────────────────────────────────────────────
def main() -> None:
    app()


if __name__ == "__main__":
    main()
