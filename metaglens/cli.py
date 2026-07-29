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
