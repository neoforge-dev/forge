"""Regression tests for CLI command registration.

Guards against the raw Typer injection bug where adding a ``typer.Typer``
application object directly to a Click group (instead of converting it first
with ``typer.main.get_command``) caused ``forge --help`` to crash with::

    AttributeError: 'Typer' object has no attribute 'hidden'

The fix: every Typer-based sub-app must be converted via ``get_command``
before being passed to ``cli.add_command``.  These tests verify that invariant
holds for all currently registered commands and that the help surface works
end-to-end.
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

typer = pytest.importorskip("typer", reason="typer not installed")

from forge_harness.cli_v2 import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_COMMANDS = [
    "approve",
    "claims",
    "contract",
    "dispatch",
    "doctor",
    "fleet",
    "git",
    "handoff",
    "health",
    "ios",
    "lead",
    "loop",
    "nodes",
    "orchestrator",
    "plan",
    "ship",
    "status",
    "submodules",
    "tasks",
    "work",
    "xnode",
]


def _is_click_compatible(cmd: object) -> bool:
    """Return True if *cmd* is a proper Click command (not a raw Typer app).

    A raw ``typer.Typer`` instance is NOT a ``click.Command`` subclass and
    lacks the ``hidden`` attribute that Click interrogates when rendering help.
    After ``get_command`` conversion the result IS a ``click.Command`` subclass
    (specifically ``typer.core.TyperGroup`` or ``typer.core.TyperCommand``).
    """
    return isinstance(cmd, click.Command)


def _is_raw_typer_app(cmd: object) -> bool:
    """Return True if *cmd* is an unconverted ``typer.Typer`` application.

    This is the exact condition that triggers the ``'Typer' object has no
    attribute 'hidden'`` crash.
    """
    return isinstance(cmd, typer.Typer)


# ---------------------------------------------------------------------------
# Registration shape tests
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    """All expected commands must be present and Click-compatible."""

    def test_all_expected_commands_are_registered(self) -> None:
        """Every command in the canonical list must be registered on the CLI."""
        registered = set(cli.commands.keys())
        missing = set(_EXPECTED_COMMANDS) - registered
        assert not missing, (
            f"Missing commands: {sorted(missing)}.  "
            "Update _EXPECTED_COMMANDS if a command was intentionally removed."
        )

    def test_no_raw_typer_app_injected(self) -> None:
        """No registered command may be a raw ``typer.Typer`` instance.

        This is the direct regression guard for the bug that caused
        ``forge --help`` to crash with AttributeError.
        """
        offenders: list[str] = []
        for name, cmd in cli.commands.items():
            if _is_raw_typer_app(cmd):
                offenders.append(name)

        assert not offenders, (
            f"Commands registered as raw Typer apps (not converted via "
            f"get_command): {offenders}.  "
            f"Wrap them with `typer.main.get_command(app)` before calling "
            f"`cli.add_command()`."
        )

    def test_all_commands_are_click_compatible(self) -> None:
        """Every registered command must be a ``click.Command`` subclass.

        Click inspects ``.hidden`` and other attributes during help rendering;
        only proper ``click.Command`` subclasses expose these.
        """
        incompatible: list[str] = []
        for name, cmd in cli.commands.items():
            if not _is_click_compatible(cmd):
                incompatible.append(f"{name} ({type(cmd).__name__})")

        assert not incompatible, (
            f"Non-Click-compatible commands found: {incompatible}.  "
            f"Convert Typer apps with `typer.main.get_command()` before "
            f"registering."
        )

    def test_all_commands_have_hidden_attribute(self) -> None:
        """Every registered command must expose a ``hidden`` attribute.

        Click reads ``cmd.hidden`` during ``format_commands`` when building
        the help page.  A raw ``typer.Typer`` object lacks this attribute,
        which is the root cause of the original crash.
        """
        missing_hidden: list[str] = []
        for name, cmd in cli.commands.items():
            if not hasattr(cmd, "hidden"):
                missing_hidden.append(name)

        assert not missing_hidden, (
            f"Commands without 'hidden' attribute: {missing_hidden}.  "
            f"These will crash when Click renders --help."
        )


# ---------------------------------------------------------------------------
# Help invocation tests (end-to-end behavioural tests)
# ---------------------------------------------------------------------------


class TestRootHelp:
    """``forge --help`` must succeed and list every registered command."""

    def test_root_help_exits_zero(self) -> None:
        """``forge --help`` must return exit code 0."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, (
            f"forge --help crashed (exit={result.exit_code}).\nOutput:\n{result.output}"
        )

    def test_root_help_contains_usage_header(self) -> None:
        """Help output must begin with a Usage line."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output

    def test_root_help_contains_commands_section(self) -> None:
        """Help output must include a Commands section."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "Commands:" in result.output

    @pytest.mark.parametrize("command_name", _EXPECTED_COMMANDS)
    def test_root_help_lists_each_expected_command(self, command_name: str) -> None:
        """Every expected command name must appear in ``forge --help`` output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert command_name in result.output, (
            f"Command '{command_name}' not found in forge --help output.  Output:\n{result.output}"
        )


# ---------------------------------------------------------------------------
# Per-command --help tests
# ---------------------------------------------------------------------------


class TestSubcommandHelp:
    """Each registered subcommand must respond to ``--help`` with exit code 0.

    This is the tightest regression guard: if any command is a raw Typer app
    or otherwise broken, ``forge <cmd> --help`` will crash rather than print
    help.
    """

    @pytest.mark.parametrize("command_name", _EXPECTED_COMMANDS)
    def test_subcommand_help_exits_zero(self, command_name: str) -> None:
        """``forge <command> --help`` must exit 0 for every registered command."""
        runner = CliRunner()
        result = runner.invoke(cli, [command_name, "--help"])
        assert result.exit_code == 0, (
            f"forge {command_name} --help crashed "
            f"(exit={result.exit_code}).\n"
            f"Output:\n{result.output}"
        )

    @pytest.mark.parametrize("command_name", _EXPECTED_COMMANDS)
    def test_subcommand_help_contains_usage(self, command_name: str) -> None:
        """Each subcommand help must include a Usage line."""
        runner = CliRunner()
        result = runner.invoke(cli, [command_name, "--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output, (
            f"forge {command_name} --help produced no 'Usage:' line.\nOutput:\n{result.output}"
        )


# ---------------------------------------------------------------------------
# Typer conversion contract test
# ---------------------------------------------------------------------------


class TestTyperConversionContract:
    """Explicit contract: Typer apps must be converted before registration.

    This test documents and enforces the conversion pattern used for the
    ``ios`` command and any future Typer-based sub-apps.
    """

    def test_ios_command_is_not_raw_typer_app(self) -> None:
        """The ios command must not be the raw ``typer.Typer`` app object."""
        ios_cmd = cli.commands["ios"]
        assert not _is_raw_typer_app(ios_cmd), (
            "ios command is a raw typer.Typer app.  "
            "Use `typer.main.get_command(ios_app)` before registering."
        )

    def test_ios_command_is_click_compatible(self) -> None:
        """The ios command must be a Click-compatible command after conversion."""
        ios_cmd = cli.commands["ios"]
        assert _is_click_compatible(ios_cmd), (
            f"ios command has type {type(ios_cmd).__name__} which is not a "
            f"click.Command subclass.  Ensure get_command() conversion."
        )

    def test_ios_command_has_subcommands(self) -> None:
        """The ios command must expose its Typer subcommands after conversion."""
        ios_cmd = cli.commands["ios"]
        ctx = click.Context(ios_cmd)  # type: ignore[arg-type]
        subcommands = ios_cmd.list_commands(ctx)  # type: ignore[union-attr]
        assert subcommands, "ios command has no subcommands after conversion"
        # These are the subcommands defined in cli_v2/ios.py
        expected_ios_subcommands = {
            "bootstrap",
            "build",
            "test",
            "sim",
            "testflight",
            "status",
        }
        assert expected_ios_subcommands.issubset(set(subcommands)), (
            f"Expected ios subcommands {sorted(expected_ios_subcommands)} "
            f"but got {sorted(subcommands)}"
        )

    def test_get_command_conversion_produces_click_compatible_type(
        self,
    ) -> None:
        """Verify that ``typer.main.get_command`` produces a Click command.

        This is a unit-level guard on the conversion helper itself: it confirms
        that the pattern used in cli_v2/__init__.py is correct and sufficient.
        """
        from typer.main import get_command

        # Create a minimal Typer app (mirrors how ios.py is structured)
        raw_app = typer.Typer(name="test-app", help="A test Typer app")

        @raw_app.command()
        def ping() -> None:
            """Ping command."""
            print("pong")

        # Raw app must NOT be Click-compatible
        assert not _is_click_compatible(raw_app), (
            "typer.Typer was unexpectedly a click.Command subclass; "
            "this test assumption is no longer valid."
        )
        assert not hasattr(raw_app, "hidden"), (
            "typer.Typer unexpectedly gained a 'hidden' attribute; "
            "this test assumption is no longer valid."
        )

        # After conversion it MUST be Click-compatible
        converted = get_command(raw_app)
        assert _is_click_compatible(converted), (
            f"get_command() returned {type(converted).__name__} which is not "
            f"a click.Command subclass.  Typer version may have changed."
        )
        assert hasattr(converted, "hidden"), (
            "get_command() result lacks 'hidden' attribute; help rendering would crash."
        )

    def test_raw_typer_app_crashes_root_help(self) -> None:
        """Demonstrate that raw Typer injection reproduces the original crash.

        This is a canary test that documents the exact failure mode.  If this
        test stops failing (i.e., raw Typer apps become Click-compatible), the
        rest of the suite may need to be updated to reflect a Typer API change.
        """
        raw_app = typer.Typer(name="canary", help="canary app")

        @raw_app.command()
        def canary_cmd() -> None:
            """Canary subcommand."""

        crash_cli = click.Group("crash-test")

        # Intentionally inject the raw Typer app without conversion
        # ``click.Group.add_command`` accepts any object without validation
        crash_cli.add_command(raw_app, name="canary")  # type: ignore[arg-type]

        runner = CliRunner()
        result = runner.invoke(crash_cli, ["--help"])

        # The raw injection MUST crash with non-zero exit and AttributeError
        assert result.exit_code != 0, (
            "Expected forge --help to crash with a raw Typer injection, "
            "but it exited cleanly.  Typer may now be inherently Click-compatible; "
            "review the conversion guard tests."
        )
        assert result.exception is not None
        assert isinstance(result.exception, AttributeError), (
            f"Expected AttributeError from raw Typer injection but got "
            f"{type(result.exception).__name__}: {result.exception}"
        )
