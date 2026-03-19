"""
FORGE CLI v2 — ios.py only (ADR-040 complete)
==============================================

All fleet operations use the Go binary: `forge` (cmd/forge/)
Only iOS development commands remain here (Python-only, no Go equivalent).

Fleet operations: use `forge` (cmd/forge/ Go binary) instead.
"""

import click

_ios_import_error: str | None = None
try:
    from typer.main import get_command

    from .ios import app as ios_app
except ModuleNotFoundError as exc:
    ios_app = None
    get_command = None
    _ios_import_error = str(exc)


@click.group()
@click.version_option(version="2.0.0", prog_name="forge")
@click.option("--legacy", is_flag=True, help="Access legacy forge-harness commands")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx: click.Context, legacy: bool, verbose: bool) -> None:
    """FORGE - Autonomous MVP Development.

    iOS commands only. All fleet operations: use `forge` (Go CLI).
    Run 'forge COMMAND --help' for more information on each command.
    """
    import logging

    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    if verbose:
        logging.getLogger("forge_harness").setLevel(logging.DEBUG)

    if legacy:
        from rich.console import Console
        console = Console()
        console.print(
            "[yellow]The legacy forge-harness CLI has been removed.[/yellow]"
        )
        console.print(
            "[dim]Fleet operations: `forge` (Go CLI). iOS: `forge ios`.[/dim]"
        )
        raise SystemExit(0)


if ios_app is not None and get_command is not None:
    cli.add_command(get_command(ios_app), name="ios")
else:

    @click.command("ios")
    def ios_unavailable() -> None:
        """iOS commands unavailable in this environment."""
        reason = _ios_import_error or "missing optional dependency"
        raise click.ClickException(f"ios command unavailable: {reason}")

    cli.add_command(ios_unavailable)


def main() -> None:
    """Entry point for the forge CLI (iOS commands only)."""
    import logging

    logging.getLogger("forge_harness.circuit_breaker").setLevel(logging.WARNING)
    cli()


if __name__ == "__main__":
    main()
