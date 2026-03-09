"""Regression tests for top-level forge CLI command registration/help output."""

from __future__ import annotations

from click.testing import CliRunner
from forge_harness.cli_v2 import cli


def test_root_help_renders_successfully() -> None:
    """`forge --help` should exit cleanly and list registered commands."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "Usage: " in result.output
    assert "Commands:" in result.output
    assert "ios" in result.output


def test_ios_command_registered_as_click_command() -> None:
    """Guard against registering raw Typer objects into a Click command tree."""
    assert "ios" in cli.commands
    ios_cmd = cli.commands["ios"]
    assert hasattr(ios_cmd, "hidden"), "ios command must be Click-compatible"

