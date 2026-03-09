"""Tests for `python -m forge_harness.cli_v2` entrypoint behavior."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner
from forge_harness.cli_v2 import cli


def _extract_help_commands(help_text: str) -> list[str]:
    """Extract command names from Click help output."""
    commands: list[str] = []
    in_commands = False

    for raw_line in help_text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if stripped == "Commands:":
            in_commands = True
            continue

        if not in_commands:
            continue

        if not stripped:
            continue

        # Click command rows are indented: "  command   Description"
        if not line.startswith("  "):
            break

        command_name = stripped.split()[0]
        commands.append(command_name)

    return commands


def test_module_entrypoint_help_exits_zero() -> None:
    """`python -m forge_harness.cli_v2 --help` should run successfully."""
    harness_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "forge_harness.cli_v2", "--help"],
        cwd=harness_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
    assert "Commands:" in result.stdout


def test_module_entrypoint_command_set_matches_click_cli() -> None:
    """Module entrypoint command discovery should match `forge` CLI registration."""
    harness_root = Path(__file__).resolve().parents[1]
    module_result = subprocess.run(
        [sys.executable, "-m", "forge_harness.cli_v2", "--help"],
        cwd=harness_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert module_result.returncode == 0, module_result.stderr

    runner = CliRunner()
    click_result = runner.invoke(cli, ["--help"])
    assert click_result.exit_code == 0, click_result.output

    module_commands = _extract_help_commands(module_result.stdout)
    click_commands = _extract_help_commands(click_result.output)

    assert module_commands
    assert module_commands == click_commands
