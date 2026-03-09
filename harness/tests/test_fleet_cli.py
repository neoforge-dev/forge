"""Comprehensive pytest tests for forge_harness/fleet_cli.py.

Tests cover:
- _find_forge_root() discovery logic
- All fleet sub-commands: save, restore, generate-handoffs, show-handoff,
  list-handoffs, archive-handoffs, dashboard, check, restart, auto-recover,
  dispatch-loop, status, tasks
- Error paths and edge cases

Patch targets (resolved via import-inside-function pattern):
  create_handoff_generator  -> forge_harness.handoff_generator.create_handoff_generator
  AgentRestarter            -> forge_harness.fleet.AgentRestarter
  auto_recover_all          -> forge_harness.fleet.auto_recover_all
  get_idle_agents           -> forge_harness.fleet.priority_dispatch.get_idle_agents
  AGENT_CAPABILITIES        -> forge_harness.fleet.priority_dispatch.AGENT_CAPABILITIES
  get_pending_tasks         -> forge_harness.fleet.priority_dispatch.get_pending_tasks
  asyncio.run               -> asyncio.run (top-level stdlib)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cli():
    """Build a minimal Click group and register fleet commands onto it."""
    import click
    from forge_harness.fleet_cli import register_fleet_commands

    @click.group()
    def cli():
        pass

    register_fleet_commands(cli)
    return cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli():
    return _make_cli()


@pytest.fixture
def forge_root(tmp_path):
    """A temp directory that looks like a FORGE root (has .forge-root marker)."""
    (tmp_path / ".forge-root").touch()
    return tmp_path


@pytest.fixture
def forge_root_with_scripts(forge_root):
    """A forge root that also has the harness/scripts directory."""
    scripts_dir = forge_root / "harness" / "scripts"
    scripts_dir.mkdir(parents=True)
    return forge_root


# ---------------------------------------------------------------------------
# _find_forge_root tests
# ---------------------------------------------------------------------------

class TestFindForgeRoot:
    """Tests for the _find_forge_root helper."""

    def test_finds_forge_root_marker(self, tmp_path):
        from forge_harness.fleet_cli import _find_forge_root
        (tmp_path / ".forge-root").touch()
        with patch("forge_harness.fleet_cli.Path.cwd", return_value=tmp_path):
            result = _find_forge_root()
        assert result == tmp_path

    def test_finds_domains_yaml_marker(self, tmp_path):
        from forge_harness.fleet_cli import _find_forge_root
        (tmp_path / "domains.yaml").touch()
        with patch("forge_harness.fleet_cli.Path.cwd", return_value=tmp_path):
            result = _find_forge_root()
        assert result == tmp_path

    def test_finds_git_with_hyphen_subdirs(self, tmp_path):
        from forge_harness.fleet_cli import _find_forge_root
        (tmp_path / ".git").mkdir()
        (tmp_path / "my-domain").mkdir()
        with patch("forge_harness.fleet_cli.Path.cwd", return_value=tmp_path):
            result = _find_forge_root()
        assert result == tmp_path

    def test_git_no_hyphen_subdir_does_not_match(self, tmp_path):
        from forge_harness.fleet_cli import _find_forge_root
        (tmp_path / ".git").mkdir()
        (tmp_path / "nodomain").mkdir()
        with patch("forge_harness.fleet_cli.Path.cwd", return_value=tmp_path):
            result = _find_forge_root()
        assert result is None

    def test_returns_none_when_not_found(self, tmp_path):
        from forge_harness.fleet_cli import _find_forge_root
        with patch("forge_harness.fleet_cli.Path.cwd", return_value=tmp_path):
            result = _find_forge_root()
        assert result is None

    def test_traverses_parent_directories(self, tmp_path):
        from forge_harness.fleet_cli import _find_forge_root
        (tmp_path / ".forge-root").touch()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        with patch("forge_harness.fleet_cli.Path.cwd", return_value=nested):
            result = _find_forge_root()
        assert result == tmp_path


# ---------------------------------------------------------------------------
# fleet save command
# ---------------------------------------------------------------------------

class TestFleetSave:
    """Tests for 'fleet save' command."""

    def test_save_no_forge_root(self, runner, cli):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=None):
            result = runner.invoke(cli, ["fleet", "save"])
        assert result.exit_code != 0
        assert "Could not find FORGE repository root" in result.output

    def test_save_script_not_found(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root):
            result = runner.invoke(cli, ["fleet", "save"])
        assert result.exit_code != 0
        assert "Fleet save script not found" in result.output

    def test_save_subprocess_failure(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-save.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 1
        mock_proc.stderr = "some error"

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc):
            result = runner.invoke(cli, ["fleet", "save"])
        assert result.exit_code != 0

    def test_save_success_no_handoff(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-save.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Saved OK"

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc):
            result = runner.invoke(cli, ["fleet", "save", "--no-handoff"])
        assert result.exit_code == 0

    def test_save_verbose_flag_passes_arg(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-save.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "verbose output"

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = runner.invoke(cli, ["fleet", "save", "--verbose", "--no-handoff"])
        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert "--verbose" in call_args

    def test_save_no_verbose_flag_not_passed(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-save.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = runner.invoke(cli, ["fleet", "save", "--no-handoff"])
        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert "--verbose" not in call_args

    def test_save_with_handoff_success(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-save.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""

        handoff_path = forge_root_with_scripts / ".forge/handoffs" / "forge_tech.md"
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.touch()

        mock_generator = Mock()
        mock_generator.generate_all_handoffs.return_value = {"forge:tech": handoff_path}
        mock_generator.update_fleet_state_with_handoffs = Mock()

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc), \
             patch("forge_harness.handoff_generator.create_handoff_generator", return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "save", "--handoff"])
        assert result.exit_code == 0

    def test_save_with_handoff_none_results(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-save.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""

        mock_generator = Mock()
        mock_generator.generate_all_handoffs.return_value = {}
        mock_generator.update_fleet_state_with_handoffs = Mock()

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc), \
             patch("forge_harness.handoff_generator.create_handoff_generator", return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "save"])
        assert result.exit_code == 0

    def test_save_exception_raises_system_exit(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-save.sh"
        script_path.touch()

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", side_effect=RuntimeError("unexpected")):
            result = runner.invoke(cli, ["fleet", "save", "--no-handoff"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# fleet restore command
# ---------------------------------------------------------------------------

class TestFleetRestore:
    """Tests for 'fleet restore' command."""

    def test_restore_no_forge_root(self, runner, cli):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=None):
            result = runner.invoke(cli, ["fleet", "restore"])
        assert result.exit_code != 0
        assert "Could not find FORGE repository root" in result.output

    def test_restore_script_not_found(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root):
            result = runner.invoke(cli, ["fleet", "restore"])
        assert result.exit_code != 0
        assert "Fleet restore script not found" in result.output

    def test_restore_subprocess_failure(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-restore.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 1
        mock_proc.stderr = "restore error"

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc):
            result = runner.invoke(cli, ["fleet", "restore"])
        assert result.exit_code != 0

    def test_restore_success(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-restore.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Fleet restored."

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc):
            result = runner.invoke(cli, ["fleet", "restore"])
        assert result.exit_code == 0

    def test_restore_verbose_passes_flag(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-restore.sh"
        script_path.touch()

        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "verbose restore"

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = runner.invoke(cli, ["fleet", "restore", "--verbose"])
        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert "--verbose" in call_args

    def test_restore_exception_exits(self, runner, cli, forge_root_with_scripts):
        script_path = forge_root_with_scripts / "harness" / "scripts" / "fleet-restore.sh"
        script_path.touch()

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root_with_scripts), \
             patch("subprocess.run", side_effect=OSError("disk error")):
            result = runner.invoke(cli, ["fleet", "restore"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# fleet generate-handoffs command
# ---------------------------------------------------------------------------

class TestFleetGenerateHandoffs:
    """Tests for 'fleet generate-handoffs' command."""

    _PATCH = "forge_harness.handoff_generator.create_handoff_generator"

    def test_no_forge_root(self, runner, cli):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=None):
            result = runner.invoke(cli, ["fleet", "generate-handoffs"])
        assert result.exit_code != 0

    def test_generate_all_no_agents_needing_handoff(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.generate_all_handoffs.return_value = {}
        mock_generator.update_fleet_state_with_handoffs = Mock()

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "generate-handoffs"])
        assert result.exit_code == 0
        assert "No agents require handoff" in result.output

    def test_generate_all_with_results(self, runner, cli, forge_root):
        handoff_path = forge_root / ".forge/handoffs" / "forge_tech.md"
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.touch()

        mock_generator = Mock()
        mock_generator.generate_all_handoffs.return_value = {"forge:tech": handoff_path}
        mock_generator.update_fleet_state_with_handoffs = Mock()

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "generate-handoffs"])
        assert result.exit_code == 0
        mock_generator.update_fleet_state_with_handoffs.assert_called_once()

    def test_generate_specific_agent_not_found(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.load_fleet_state.return_value = {"agents": {}}

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "generate-handoffs", "--agent", "forge:unknown"])
        assert result.exit_code != 0

    def test_generate_specific_agent_success(self, runner, cli, forge_root):
        handoff_path = forge_root / ".forge/handoffs" / "forge_tech.md"
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.touch()

        agent_data = {"context_percent": "75", "session": "forge", "window": "tech"}
        mock_generator = Mock()
        mock_generator.load_fleet_state.return_value = {"agents": {"forge:tech": agent_data}}
        mock_generator.generate_handoff.return_value = handoff_path
        mock_generator.context_threshold = 50

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "generate-handoffs", "--agent", "forge:tech"])
        assert result.exit_code == 0

    def test_generate_specific_agent_no_handoff_needed(self, runner, cli, forge_root):
        agent_data = {"context_percent": "10"}
        mock_generator = Mock()
        mock_generator.load_fleet_state.return_value = {"agents": {"forge:tech": agent_data}}
        mock_generator.generate_handoff.return_value = None
        mock_generator.context_threshold = 50

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "generate-handoffs", "--agent", "forge:tech"])
        assert result.exit_code == 0
        assert "does not need handoff" in result.output

    def test_generate_with_force_resets_threshold(self, runner, cli, forge_root):
        handoff_path = forge_root / ".forge/handoffs" / "forge_tech.md"
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.touch()

        agent_data = {"context_percent": "10"}
        mock_generator = Mock()
        mock_generator.load_fleet_state.return_value = {"agents": {"forge:tech": agent_data}}
        mock_generator.generate_handoff.return_value = handoff_path
        mock_generator.context_threshold = 50

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(
                cli, ["fleet", "generate-handoffs", "--agent", "forge:tech", "--force"]
            )
        assert result.exit_code == 0
        # Threshold should be restored to original after force generation
        assert mock_generator.context_threshold == 50

    def test_generate_with_threshold_option(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.generate_all_handoffs.return_value = {}
        mock_generator.update_fleet_state_with_handoffs = Mock()

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator) as mock_create:
            result = runner.invoke(cli, ["fleet", "generate-handoffs", "--threshold", "60"])
        assert result.exit_code == 0
        mock_create.assert_called_once_with(forge_root=forge_root, context_threshold=60)

    def test_file_not_found_error_exits(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.load_fleet_state.side_effect = FileNotFoundError("state file missing")

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "generate-handoffs", "--agent", "forge:tech"])
        assert result.exit_code != 0
        assert "Run 'forge-harness fleet save' first" in result.output

    def test_unexpected_exception_exits(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.generate_all_handoffs.side_effect = RuntimeError("boom")

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "generate-handoffs"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# fleet show-handoff command
# ---------------------------------------------------------------------------

class TestFleetShowHandoff:
    """Tests for 'fleet show-handoff' command."""

    _PATCH = "forge_harness.handoff_generator.create_handoff_generator"

    def test_no_forge_root(self, runner, cli):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=None):
            result = runner.invoke(cli, ["fleet", "show-handoff", "forge:tech"])
        assert result.exit_code != 0

    def test_missing_colon_in_agent(self, runner, cli, forge_root):
        mock_generator = Mock()
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "show-handoff", "forgeatech"])
        assert result.exit_code != 0
        assert "session:window" in result.output

    def test_handoff_not_found(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.get_handoff_for_agent.return_value = None

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "show-handoff", "forge:tech"])
        assert result.exit_code != 0
        assert "No handoff found" in result.output

    def test_shows_handoff_content(self, runner, cli, forge_root):
        handoff_file = forge_root / ".forge/handoffs" / "forge_tech.md"
        handoff_file.parent.mkdir(parents=True, exist_ok=True)
        handoff_file.write_text("# Handoff for tech\n\nResume task X.")

        mock_generator = Mock()
        mock_generator.get_handoff_for_agent.return_value = handoff_file

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "show-handoff", "forge:tech"])
        assert result.exit_code == 0

    def test_calls_get_handoff_with_session_and_window(self, runner, cli, forge_root):
        handoff_file = forge_root / ".forge/handoffs" / "forge_tech.md"
        handoff_file.parent.mkdir(parents=True, exist_ok=True)
        handoff_file.write_text("content")

        mock_generator = Mock()
        mock_generator.get_handoff_for_agent.return_value = handoff_file

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "show-handoff", "myses:mywin"])
        mock_generator.get_handoff_for_agent.assert_called_once_with("myses", "mywin")

    def test_exception_exits(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.get_handoff_for_agent.side_effect = OSError("read error")

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "show-handoff", "forge:tech"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# fleet list-handoffs command
# ---------------------------------------------------------------------------

class TestFleetListHandoffs:
    """Tests for 'fleet list-handoffs' command."""

    _PATCH = "forge_harness.handoff_generator.create_handoff_generator"

    def test_no_forge_root(self, runner, cli):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=None):
            result = runner.invoke(cli, ["fleet", "list-handoffs"])
        assert result.exit_code != 0

    def test_no_handoffs(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.list_handoffs.return_value = []

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "list-handoffs"])
        assert result.exit_code == 0
        assert "No handoff files found" in result.output

    def test_lists_handoffs(self, runner, cli, forge_root):
        handoff_file = forge_root / ".forge/handoffs" / "forge_tech.md"
        handoff_file.parent.mkdir(parents=True, exist_ok=True)
        handoff_file.write_text("content")

        mock_generator = Mock()
        mock_generator.list_handoffs.return_value = [handoff_file]

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "list-handoffs"])
        assert result.exit_code == 0

    def test_limit_option_restricts_display(self, runner, cli, forge_root):
        handoff_dir = forge_root / ".forge/handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(5):
            f = handoff_dir / f"forge_agent{i}.md"
            f.write_text(f"content {i}")
            files.append(f)

        mock_generator = Mock()
        mock_generator.list_handoffs.return_value = files

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "list-handoffs", "--limit", "2"])
        assert result.exit_code == 0

    def test_shows_ellipsis_when_more_than_limit(self, runner, cli, forge_root):
        handoff_dir = forge_root / ".forge/handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(25):
            f = handoff_dir / f"forge_agent{i}.md"
            f.write_text(f"content {i}")
            files.append(f)

        mock_generator = Mock()
        mock_generator.list_handoffs.return_value = files

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "list-handoffs", "--limit", "20"])
        assert result.exit_code == 0
        assert "more" in result.output

    def test_single_part_filename_uses_stem_as_agent(self, runner, cli, forge_root):
        """Files without underscore in name should use full stem as agent."""
        handoff_dir = forge_root / ".forge/handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        f = handoff_dir / "simplehandoff.md"
        f.write_text("content")

        mock_generator = Mock()
        mock_generator.list_handoffs.return_value = [f]

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "list-handoffs"])
        assert result.exit_code == 0

    def test_exception_exits(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.list_handoffs.side_effect = OSError("disk error")

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "list-handoffs"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# fleet archive-handoffs command
# ---------------------------------------------------------------------------

class TestFleetArchiveHandoffs:
    """Tests for 'fleet archive-handoffs' command."""

    _PATCH = "forge_harness.handoff_generator.create_handoff_generator"

    def test_no_forge_root(self, runner, cli):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=None):
            result = runner.invoke(cli, ["fleet", "archive-handoffs"])
        assert result.exit_code != 0

    def test_no_old_handoffs(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.archive_old_handoffs.return_value = []

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "archive-handoffs"])
        assert result.exit_code == 0
        assert "No handoffs older than" in result.output

    def test_archives_files(self, runner, cli, forge_root):
        archive_dir = forge_root / ".forge/handoffs" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived = [archive_dir / f"forge_tech_{i}.md" for i in range(3)]
        for f in archived:
            f.touch()

        mock_generator = Mock()
        mock_generator.archive_old_handoffs.return_value = archived

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "archive-handoffs"])
        assert result.exit_code == 0
        assert "Archived" in result.output

    def test_shows_ellipsis_for_large_archive(self, runner, cli, forge_root):
        archive_dir = forge_root / ".forge/handoffs" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived = [archive_dir / f"f_{i}.md" for i in range(15)]
        for f in archived:
            f.touch()

        mock_generator = Mock()
        mock_generator.archive_old_handoffs.return_value = archived

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "archive-handoffs"])
        assert result.exit_code == 0
        assert "more" in result.output

    def test_custom_days_option_passed_through(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.archive_old_handoffs.return_value = []

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "archive-handoffs", "--days", "14"])
        assert result.exit_code == 0
        mock_generator.archive_old_handoffs.assert_called_once_with(days=14)

    def test_exception_exits(self, runner, cli, forge_root):
        mock_generator = Mock()
        mock_generator.archive_old_handoffs.side_effect = OSError("permission denied")

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=mock_generator):
            result = runner.invoke(cli, ["fleet", "archive-handoffs"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# fleet dashboard command
# ---------------------------------------------------------------------------

class TestFleetDashboard:
    """Tests for 'fleet dashboard' command."""

    def test_dashboard_exits_cleanly_on_zero(self, runner, cli):
        import sys as _sys
        with patch("forge_harness.fleet.__main__.main", return_value=0):
            result = runner.invoke(cli, ["fleet", "dashboard"])
        assert result.exit_code == 0

    def test_dashboard_keyboard_interrupt_exits_zero(self, runner, cli):
        with patch("forge_harness.fleet.__main__.main", side_effect=KeyboardInterrupt):
            result = runner.invoke(cli, ["fleet", "dashboard"])
        assert result.exit_code == 0
        assert "Dashboard closed" in result.output

    def test_dashboard_exception_exits_nonzero(self, runner, cli):
        with patch("forge_harness.fleet.__main__.main", side_effect=RuntimeError("crash")):
            result = runner.invoke(cli, ["fleet", "dashboard"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# fleet check command
# ---------------------------------------------------------------------------

class TestFleetCheck:
    """Tests for 'fleet check' command."""

    _PATCH = "forge_harness.fleet.AgentRestarter"

    def _make_restarter(self, is_stale=False, is_crashed=False):
        mock_restarter = Mock()
        mock_restarter.is_stale.return_value = is_stale
        mock_restarter.is_crashed.return_value = is_crashed
        return mock_restarter

    def test_agent_healthy(self, runner, cli):
        mock_restarter = self._make_restarter(is_stale=False, is_crashed=False)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "check", "tech"])
        assert result.exit_code == 0
        assert "healthy" in result.output

    def test_agent_crashed(self, runner, cli):
        mock_restarter = self._make_restarter(is_crashed=True)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "check", "tech"])
        assert result.exit_code != 0
        assert "crashed" in result.output

    def test_agent_stale(self, runner, cli):
        mock_restarter = self._make_restarter(is_stale=True)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "check", "tech"])
        assert result.exit_code != 0
        assert "stale" in result.output

    def test_custom_session_passed_to_restarter(self, runner, cli):
        mock_restarter = self._make_restarter()
        with patch(self._PATCH, return_value=mock_restarter) as mock_cls:
            result = runner.invoke(cli, ["fleet", "check", "tech", "--session", "my-session"])
        mock_cls.assert_called_once_with(session="my-session")

    def test_custom_threshold_passed_to_is_stale(self, runner, cli):
        mock_restarter = self._make_restarter()
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "check", "tech", "--threshold", "45"])
        mock_restarter.is_stale.assert_called_once_with("tech", threshold_minutes=45)

    def test_exception_exits_nonzero(self, runner, cli):
        with patch(self._PATCH, side_effect=RuntimeError("boom")):
            result = runner.invoke(cli, ["fleet", "check", "tech"])
        assert result.exit_code != 0

    def test_default_session_is_forge(self, runner, cli):
        mock_restarter = self._make_restarter()
        with patch(self._PATCH, return_value=mock_restarter) as mock_cls:
            result = runner.invoke(cli, ["fleet", "check", "tech"])
        mock_cls.assert_called_once_with(session="forge")


# ---------------------------------------------------------------------------
# fleet restart command
# ---------------------------------------------------------------------------

class TestFleetRestart:
    """Tests for 'fleet restart' command."""

    _PATCH = "forge_harness.fleet.AgentRestarter"

    def _make_restarter(self, task_state=None, restart_success=True):
        mock_restarter = Mock()
        mock_restarter.recover_task_state.return_value = task_state
        mock_restarter.restart_agent.return_value = restart_success
        return mock_restarter

    def test_restart_success_no_recover(self, runner, cli):
        mock_restarter = self._make_restarter(restart_success=True)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "restart", "tech", "--no-recover"])
        assert result.exit_code == 0
        assert "restarted successfully" in result.output

    def test_restart_success_with_task_state(self, runner, cli):
        task_state = Mock()
        task_state.working_directory = "/some/dir"
        task_state.prompt = "Continue implementing feature X and write tests..."
        task_state.files_modified = ["file1.py", "file2.py"]

        mock_restarter = self._make_restarter(task_state=task_state, restart_success=True)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "restart", "tech", "--recover"])
        assert result.exit_code == 0
        assert "Recovered task state" in result.output

    def test_restart_with_no_task_state_recovered(self, runner, cli):
        mock_restarter = self._make_restarter(task_state=None, restart_success=True)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "restart", "tech", "--recover"])
        assert result.exit_code == 0
        assert "No task state recovered" in result.output

    def test_restart_failure_exits_nonzero(self, runner, cli):
        mock_restarter = self._make_restarter(restart_success=False)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "restart", "tech", "--no-recover"])
        assert result.exit_code != 0
        assert "Failed to restart" in result.output

    def test_restart_task_state_no_prompt(self, runner, cli):
        task_state = Mock()
        task_state.working_directory = "/dir"
        task_state.prompt = None
        task_state.files_modified = []

        mock_restarter = self._make_restarter(task_state=task_state, restart_success=True)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "restart", "tech", "--recover"])
        assert result.exit_code == 0

    def test_restart_custom_session(self, runner, cli):
        mock_restarter = self._make_restarter(restart_success=True)
        with patch(self._PATCH, return_value=mock_restarter) as mock_cls:
            result = runner.invoke(cli, ["fleet", "restart", "tech", "--session", "custom"])
        mock_cls.assert_called_once_with(session="custom")

    def test_restart_exception_exits(self, runner, cli):
        with patch(self._PATCH, side_effect=RuntimeError("crash")):
            result = runner.invoke(cli, ["fleet", "restart", "tech"])
        assert result.exit_code != 0

    def test_restart_task_state_with_files(self, runner, cli):
        task_state = Mock()
        task_state.working_directory = "/dir"
        task_state.prompt = "A short prompt"
        task_state.files_modified = ["a.py", "b.py", "c.py"]

        mock_restarter = self._make_restarter(task_state=task_state, restart_success=True)
        with patch(self._PATCH, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "restart", "tech", "--recover"])
        assert result.exit_code == 0
        assert "Files:" in result.output


# ---------------------------------------------------------------------------
# fleet auto-recover command
# ---------------------------------------------------------------------------

class TestFleetAutoRecover:
    """Tests for 'fleet auto-recover' command."""

    _PATCH_RESTARTER = "forge_harness.fleet.AgentRestarter"
    _PATCH_AUTO_ALL = "forge_harness.fleet.auto_recover_all"

    def _healthy_result(self):
        r = Mock()
        r.previous_status = "healthy"
        r.success = True
        r.task_recovered = False
        r.task_state = None
        r.error = None
        r.window_name = "tech"
        return r

    def _recovered_result(self, success=True, status="stale", task=None):
        r = Mock()
        r.previous_status = status
        r.success = success
        r.task_recovered = task is not None
        r.task_state = task
        r.error = None if success else "recovery failed"
        r.window_name = "tech"
        return r

    def test_no_agent_no_all_flag(self, runner, cli):
        result = runner.invoke(cli, ["fleet", "auto-recover"])
        assert result.exit_code != 0
        assert "Must specify agent" in result.output

    def test_specific_agent_already_healthy(self, runner, cli):
        mock_restarter = Mock()
        mock_restarter.auto_recover.return_value = self._healthy_result()

        with patch(self._PATCH_RESTARTER, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "auto-recover", "tech"])
        assert result.exit_code == 0
        assert "already healthy" in result.output

    def test_specific_agent_recovery_success(self, runner, cli):
        mock_restarter = Mock()
        mock_restarter.auto_recover.return_value = self._recovered_result(success=True)

        with patch(self._PATCH_RESTARTER, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "auto-recover", "tech"])
        assert result.exit_code == 0
        assert "recovered successfully" in result.output

    def test_specific_agent_recovery_failure(self, runner, cli):
        mock_restarter = Mock()
        mock_restarter.auto_recover.return_value = self._recovered_result(success=False)

        with patch(self._PATCH_RESTARTER, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "auto-recover", "tech"])
        assert result.exit_code != 0

    def test_specific_agent_with_task_state(self, runner, cli):
        task = Mock()
        task.working_directory = "/work/dir"
        task.prompt = "Continue implementing the feature with detailed instructions..."
        task.files_modified = ["a.py"]

        mock_restarter = Mock()
        mock_restarter.auto_recover.return_value = self._recovered_result(success=True, task=task)

        with patch(self._PATCH_RESTARTER, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "auto-recover", "tech"])
        assert result.exit_code == 0
        assert "Recovered task state" in result.output

    def test_specific_agent_task_state_no_files(self, runner, cli):
        task = Mock()
        task.working_directory = "/work/dir"
        task.prompt = "Short prompt"
        task.files_modified = []

        mock_restarter = Mock()
        mock_restarter.auto_recover.return_value = self._recovered_result(success=True, task=task)

        with patch(self._PATCH_RESTARTER, return_value=mock_restarter):
            result = runner.invoke(cli, ["fleet", "auto-recover", "tech"])
        assert result.exit_code == 0

    def test_all_flag_all_healthy(self, runner, cli):
        with patch(self._PATCH_AUTO_ALL, return_value=[]):
            result = runner.invoke(cli, ["fleet", "auto-recover", "--all"])
        assert result.exit_code == 0
        assert "All agents are healthy" in result.output

    def test_all_flag_with_results(self, runner, cli):
        r1 = Mock()
        r1.previous_status = "stale"
        r1.success = True
        r1.task_recovered = True
        r1.window_name = "tech"

        r2 = Mock()
        r2.previous_status = "crashed"
        r2.success = False
        r2.task_recovered = False
        r2.window_name = "qa"

        with patch(self._PATCH_AUTO_ALL, return_value=[r1, r2]):
            result = runner.invoke(cli, ["fleet", "auto-recover", "--all"])
        assert result.exit_code == 0
        assert "Recovered" in result.output

    def test_all_flag_unknown_status_style(self, runner, cli):
        r1 = Mock()
        r1.previous_status = "unknown_status"
        r1.success = True
        r1.task_recovered = False
        r1.window_name = "tech"

        with patch(self._PATCH_AUTO_ALL, return_value=[r1]):
            result = runner.invoke(cli, ["fleet", "auto-recover", "--all"])
        assert result.exit_code == 0

    def test_exception_exits(self, runner, cli):
        with patch(self._PATCH_RESTARTER, side_effect=RuntimeError("err")):
            result = runner.invoke(cli, ["fleet", "auto-recover", "tech"])
        assert result.exit_code != 0

    def test_custom_session_for_specific_agent(self, runner, cli):
        mock_restarter = Mock()
        mock_restarter.auto_recover.return_value = self._healthy_result()

        with patch(self._PATCH_RESTARTER, return_value=mock_restarter) as mock_cls:
            result = runner.invoke(cli, ["fleet", "auto-recover", "tech", "--session", "custom"])
        mock_cls.assert_called_once_with(session="custom")

    def test_all_flag_with_custom_session(self, runner, cli):
        with patch(self._PATCH_AUTO_ALL, return_value=[]) as mock_fn:
            result = runner.invoke(cli, ["fleet", "auto-recover", "--all", "--session", "alt"])
        mock_fn.assert_called_once_with(session="alt")


# ---------------------------------------------------------------------------
# fleet dispatch-loop command
# ---------------------------------------------------------------------------

class TestFleetDispatchLoop:
    """Tests for 'fleet dispatch-loop' command."""

    def test_no_forge_root_exits_nonzero(self, runner, cli):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=None):
            result = runner.invoke(cli, ["fleet", "dispatch-loop"])
        assert result.exit_code != 0
        assert "Could not find FORGE repository root" in result.output

    def test_dry_run_exits_cleanly(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root):
            result = runner.invoke(cli, ["fleet", "dispatch-loop", "--dry-run"])
        assert result.exit_code == 0
        assert "DRY RUN" in result.output

    def test_shows_interval_in_output(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch("asyncio.run", side_effect=KeyboardInterrupt):
            result = runner.invoke(cli, ["fleet", "dispatch-loop", "--interval", "30"])
        assert "Interval: 30s" in result.output

    def test_shows_max_iterations_in_output(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch("asyncio.run", side_effect=KeyboardInterrupt):
            result = runner.invoke(cli, ["fleet", "dispatch-loop", "--max-iterations", "5"])
        assert "Max Iterations: 5" in result.output

    def test_max_iterations_zero_shows_infinite(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch("asyncio.run", side_effect=KeyboardInterrupt):
            result = runner.invoke(cli, ["fleet", "dispatch-loop", "--max-iterations", "0"])
        assert "infinite" in result.output

    def test_keyboard_interrupt_exits_zero(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch("asyncio.run", side_effect=KeyboardInterrupt):
            result = runner.invoke(cli, ["fleet", "dispatch-loop"])
        assert result.exit_code == 0
        assert "Shutting down" in result.output

    def test_runtime_exception_exits_nonzero(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch("asyncio.run", side_effect=RuntimeError("async error")):
            result = runner.invoke(cli, ["fleet", "dispatch-loop"])
        assert result.exit_code != 0

    def test_asyncio_run_is_called(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch("asyncio.run") as mock_run:
            mock_run.return_value = None
            result = runner.invoke(cli, ["fleet", "dispatch-loop", "--max-iterations", "1"])
        assert mock_run.called

    def test_live_mode_shown_in_output(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch("asyncio.run", side_effect=KeyboardInterrupt):
            result = runner.invoke(cli, ["fleet", "dispatch-loop"])
        assert "LIVE" in result.output

    def test_forge_root_auto_detected_when_not_specified(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root) as mock_find, \
             patch("asyncio.run", side_effect=KeyboardInterrupt):
            result = runner.invoke(cli, ["fleet", "dispatch-loop"])
        mock_find.assert_called_once()


# ---------------------------------------------------------------------------
# fleet status command
# ---------------------------------------------------------------------------

class TestFleetStatus:
    """Tests for 'fleet status' command."""

    _PATCH_IDLE = "forge_harness.fleet.priority_dispatch.get_idle_agents"
    _PATCH_CAPS = "forge_harness.fleet.priority_dispatch.AGENT_CAPABILITIES"

    def _make_idle_agent(self, name):
        agent = Mock()
        agent.name = name
        return agent

    def test_status_human_readable_output(self, runner, cli):
        mock_idle = [self._make_idle_agent("tech")]
        mock_caps = {
            "tech": {"domains": ["harness"], "keywords": ["backend", "api", "db"]},
            "qa": {"domains": ["*"], "keywords": ["test", "quality"]},
        }

        with patch(self._PATCH_IDLE, return_value=mock_idle), \
             patch(self._PATCH_CAPS, mock_caps):
            result = runner.invoke(cli, ["fleet", "status"])
        assert result.exit_code == 0
        assert "FORGE Fleet Status" in result.output

    def test_status_json_output_structure(self, runner, cli):
        mock_idle = [self._make_idle_agent("tech")]
        mock_caps = {
            "tech": {"domains": ["harness"], "keywords": ["backend"]},
        }

        with patch(self._PATCH_IDLE, return_value=mock_idle), \
             patch(self._PATCH_CAPS, mock_caps):
            result = runner.invoke(cli, ["fleet", "status", "--json"])
        assert result.exit_code == 0
        # Output includes a header line before JSON; extract the JSON block
        json_str = result.output[result.output.index("{"):]
        data = json.loads(json_str)
        assert "total_agents" in data
        assert "idle_agents" in data
        assert "busy_agents" in data
        assert "agents" in data

    def test_status_shows_idle_agent(self, runner, cli):
        mock_idle = [self._make_idle_agent("tech")]
        mock_caps = {
            "tech": {"domains": ["harness"], "keywords": ["backend"]},
        }

        with patch(self._PATCH_IDLE, return_value=mock_idle), \
             patch(self._PATCH_CAPS, mock_caps):
            result = runner.invoke(cli, ["fleet", "status"])
        assert "IDLE" in result.output

    def test_status_shows_busy_agent(self, runner, cli):
        mock_caps = {
            "tech": {"domains": ["harness"], "keywords": ["backend"]},
        }

        with patch(self._PATCH_IDLE, return_value=[]), \
             patch(self._PATCH_CAPS, mock_caps):
            result = runner.invoke(cli, ["fleet", "status"])
        assert "BUSY" in result.output

    def test_status_json_counts_correct(self, runner, cli):
        mock_idle = [self._make_idle_agent("tech")]
        mock_caps = {
            "tech": {"domains": ["harness"], "keywords": ["backend"]},
            "qa": {"domains": ["*"], "keywords": ["test"]},
        }

        with patch(self._PATCH_IDLE, return_value=mock_idle), \
             patch(self._PATCH_CAPS, mock_caps):
            result = runner.invoke(cli, ["fleet", "status", "--json"])
        json_str = result.output[result.output.index("{"):]
        data = json.loads(json_str)
        assert data["total_agents"] == 2
        assert data["idle_agents"] == 1
        assert data["busy_agents"] == 1

    def test_status_json_agent_entries(self, runner, cli):
        mock_idle = [self._make_idle_agent("tech")]
        mock_caps = {
            "tech": {"domains": ["harness"], "keywords": ["backend"]},
        }

        with patch(self._PATCH_IDLE, return_value=mock_idle), \
             patch(self._PATCH_CAPS, mock_caps):
            result = runner.invoke(cli, ["fleet", "status", "--json"])
        json_str = result.output[result.output.index("{"):]
        data = json.loads(json_str)
        assert data["agents"][0]["name"] == "tech"
        assert data["agents"][0]["status"] == "idle"
        assert data["agents"][0]["capabilities"] == {"domains": ["harness"], "keywords": ["backend"]}

    def test_status_empty_capabilities(self, runner, cli):
        mock_caps = {
            "tech": {"domains": [], "keywords": []},
        }

        with patch(self._PATCH_IDLE, return_value=[]), \
             patch(self._PATCH_CAPS, mock_caps):
            result = runner.invoke(cli, ["fleet", "status"])
        assert result.exit_code == 0

    def test_status_truncates_keywords_to_three(self, runner, cli):
        """Only first 3 keywords should be displayed in human-readable mode."""
        mock_caps = {
            "tech": {"domains": ["harness", "neoforge"], "keywords": ["a", "b", "c", "d", "e"]},
        }

        with patch(self._PATCH_IDLE, return_value=[]), \
             patch(self._PATCH_CAPS, mock_caps):
            result = runner.invoke(cli, ["fleet", "status"])
        assert result.exit_code == 0
        # "d" and "e" should not appear in keywords line since only first 3 are shown
        assert "d, e" not in result.output


# ---------------------------------------------------------------------------
# fleet tasks command
# ---------------------------------------------------------------------------

class TestFleetTasks:
    """Tests for 'fleet tasks' command."""

    _PATCH = "forge_harness.fleet.priority_dispatch.get_pending_tasks"

    def _make_task(self, feature_id="IS-001", title="Test task", score=9.5):
        task = Mock()
        task.feature_id = feature_id
        task.title = title
        task.score = score
        task.impact = 8.0
        task.urgency = 7.5
        task.effort = 3.0
        task.metadata = {"domain": "codeswiftr-com", "priority": "high"}
        task.to_dict.return_value = {
            "feature_id": feature_id,
            "title": title,
            "score": score,
        }
        return task

    def test_no_forge_root_exits_nonzero(self, runner, cli):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=None):
            result = runner.invoke(cli, ["fleet", "tasks"])
        assert result.exit_code != 0

    def test_no_pending_tasks(self, runner, cli, forge_root):
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=[]):
            result = runner.invoke(cli, ["fleet", "tasks"])
        assert result.exit_code == 0
        assert "No pending tasks" in result.output

    def test_human_readable_output(self, runner, cli, forge_root):
        tasks = [self._make_task("IS-001", "Implement auth", 9.5)]
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=tasks):
            result = runner.invoke(cli, ["fleet", "tasks"])
        assert result.exit_code == 0
        assert "Pending Tasks" in result.output
        assert "IS-001" in result.output
        assert "Implement auth" in result.output

    def test_json_output_structure(self, runner, cli, forge_root):
        tasks = [self._make_task("IS-001", "Implement auth", 9.5)]
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=tasks):
            result = runner.invoke(cli, ["fleet", "tasks", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total_pending" in data
        assert "tasks" in data
        assert len(data["tasks"]) == 1

    def test_limit_option_caps_output(self, runner, cli, forge_root):
        tasks = [self._make_task(f"IS-{i:03d}", f"Task {i}", float(10 - i)) for i in range(10)]
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=tasks):
            result = runner.invoke(cli, ["fleet", "tasks", "--limit", "3"])
        assert result.exit_code == 0
        assert "IS-000" in result.output
        # IS-003 and beyond should be sliced off
        assert "IS-003" not in result.output

    def test_uses_provided_forge_root_directly(self, runner, cli, forge_root):
        tasks = [self._make_task()]
        with patch(self._PATCH, return_value=tasks) as mock_gpt:
            result = runner.invoke(cli, ["fleet", "tasks", "--forge-root", str(forge_root)])
        mock_gpt.assert_called_once_with(forge_root)

    def test_task_missing_metadata_keys_uses_defaults(self, runner, cli, forge_root):
        task = Mock()
        task.feature_id = "IS-001"
        task.title = "Task with no meta"
        task.score = 5.0
        task.impact = 3.0
        task.urgency = 3.0
        task.effort = 3.0
        task.metadata = {}  # empty — should use .get() defaults
        task.to_dict.return_value = {"feature_id": "IS-001"}

        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=[task]):
            result = runner.invoke(cli, ["fleet", "tasks"])
        assert result.exit_code == 0
        assert "unknown" in result.output  # default domain
        assert "medium" in result.output   # default priority

    def test_human_output_shows_scores(self, runner, cli, forge_root):
        tasks = [self._make_task("IS-001", "Test", 8.3)]
        with patch("forge_harness.fleet_cli._find_forge_root", return_value=forge_root), \
             patch(self._PATCH, return_value=tasks):
            result = runner.invoke(cli, ["fleet", "tasks"])
        assert "Score: 8.3" in result.output


# ---------------------------------------------------------------------------
# fleet group registration completeness
# ---------------------------------------------------------------------------

class TestFleetGroupRegistration:
    """Ensure all sub-commands are properly registered."""

    @pytest.mark.parametrize("subcmd", [
        "save",
        "restore",
        "generate-handoffs",
        "show-handoff",
        "list-handoffs",
        "archive-handoffs",
        "dashboard",
        "check",
        "restart",
        "auto-recover",
        "dispatch-loop",
        "status",
        "tasks",
    ])
    def test_subcommand_help_succeeds(self, runner, cli, subcmd):
        """Every sub-command should accept --help without errors (exit 0)."""
        result = runner.invoke(cli, ["fleet", subcmd, "--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output

    def test_fleet_group_help(self, runner, cli):
        result = runner.invoke(cli, ["fleet", "--help"])
        assert result.exit_code == 0
        assert "fleet" in result.output.lower()
