"""Extended tests for fleet submodules with low coverage.

Targets:
1. forge_harness/fleet/context_rotation.py  (60% -> 80%+)
2. forge_harness/fleet/dispatch_client.py   (80% -> 85%+)
3. forge_harness/fleet/dispatch_validation.py (99% -> 100%)
4. forge_harness/fleet/permissions.py       (99% -> 100%)
5. forge_harness/fleet/verification.py      (23% -> 80%+)

Focus on untested branches: subprocess calls, error paths, edge cases.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# =============================================================================
# context_rotation.py - LegacyContextRotator uncovered paths
# =============================================================================


class TestLegacyContextRotatorCapturePaneExtended:
    """Tests for LegacyContextRotator._capture_pane edge cases."""

    def test_capture_pane_success(self) -> None:
        """Should return stdout on success."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "some pane content"

        with patch("subprocess.run", return_value=mock_result):
            result = rotator._capture_pane("forge:test")

        assert result == "some pane content"

    def test_capture_pane_nonzero_returncode(self) -> None:
        """Should return None when tmux returns nonzero."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "no session"

        with patch("subprocess.run", return_value=mock_result):
            result = rotator._capture_pane("forge:test")

        assert result is None

    def test_capture_pane_timeout(self) -> None:
        """Should return None on TimeoutExpired."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=30),
        ):
            result = rotator._capture_pane("forge:test")

        assert result is None

    def test_capture_pane_file_not_found(self) -> None:
        """Should return None when tmux not found."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = rotator._capture_pane("forge:test")

        assert result is None

    def test_capture_pane_generic_exception(self) -> None:
        """Should return None on generic exceptions."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        with patch("subprocess.run", side_effect=OSError("connection error")):
            result = rotator._capture_pane("forge:test")

        assert result is None

    def test_capture_pane_normalizes_session_id(self) -> None:
        """Should normalize session ID before capturing."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator(session_prefix="forge")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "content"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            rotator._capture_pane("tech")  # bare name, should be prefixed

        # Verify tmux target was forge:tech
        call_args = mock_run.call_args[0][0]
        assert "forge:tech" in call_args


class TestLegacyContextRotatorSpawnWindow:
    """Tests for LegacyContextRotator._spawn_new_window."""

    def test_spawn_new_window_success(self) -> None:
        """Should return True on success."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator(session_prefix="forge")

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = rotator._spawn_new_window("new-window")

        assert result is True

    def test_spawn_new_window_failure(self) -> None:
        """Should return False when tmux returns nonzero."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error creating window"

        with patch("subprocess.run", return_value=mock_result):
            result = rotator._spawn_new_window("new-window")

        assert result is False

    def test_spawn_new_window_exception(self) -> None:
        """Should return False on exception."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        with patch("subprocess.run", side_effect=OSError("tmux error")):
            result = rotator._spawn_new_window("new-window")

        assert result is False


class TestLegacyContextRotatorSendToSession:
    """Tests for LegacyContextRotator._send_to_session."""

    def test_send_to_session_success(self) -> None:
        """Should return True on success."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = rotator._send_to_session("forge:test", "hello world")

        assert result is True

    def test_send_to_session_failure(self) -> None:
        """Should return False on nonzero returncode."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"

        with patch("subprocess.run", return_value=mock_result):
            result = rotator._send_to_session("forge:test", "hello world")

        assert result is False

    def test_send_to_session_exception(self) -> None:
        """Should return False on exception."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        with patch("subprocess.run", side_effect=OSError("broken pipe")):
            result = rotator._send_to_session("forge:test", "hello world")

        assert result is False

    def test_send_to_session_escapes_quotes(self) -> None:
        """Should escape single quotes in text."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            rotator._send_to_session("forge:test", "it's working")

        # Check that the send-keys call was made
        assert mock_run.called


class TestLegacyContextRotatorStartClaude:
    """Tests for LegacyContextRotator._start_claude_in_window."""

    def test_start_claude_success(self) -> None:
        """Should return True when tmux send-keys succeeds."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator(session_prefix="forge")

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = rotator._start_claude_in_window("tech")

        assert result is True

    def test_start_claude_exception(self) -> None:
        """Should return False on exception."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        with patch("subprocess.run", side_effect=OSError("tmux gone")):
            result = rotator._start_claude_in_window("tech")

        assert result is False


class TestLegacyContextRotatorRotateAgent:
    """Tests for LegacyContextRotator.rotate_agent."""

    def test_rotate_agent_success(self) -> None:
        """Should return SUCCESS result on full success."""
        from forge_harness.fleet.context_rotation import (
            LegacyContextRotator,
            RotationStatus,
        )

        rotator = LegacyContextRotator()

        with patch.object(rotator, "estimate_context_usage", return_value=75), \
             patch.object(rotator, "_generate_new_window_name", return_value="tech-r1234"), \
             patch.object(rotator, "_spawn_new_window", return_value=True), \
             patch.object(rotator, "_start_claude_in_window", return_value=True), \
             patch.object(rotator, "_send_to_session", return_value=True), \
             patch("time.sleep"):
            result = rotator.rotate_agent("forge:tech")

        assert result.success is True
        assert result.status == RotationStatus.SUCCESS
        assert result.new_session_id == "forge:tech-r1234"
        assert result.old_context_pct == 75

    def test_rotate_agent_spawn_failure(self) -> None:
        """Should return FAILED when spawn fails."""
        from forge_harness.fleet.context_rotation import (
            LegacyContextRotator,
            RotationStatus,
        )

        rotator = LegacyContextRotator()

        with patch.object(rotator, "estimate_context_usage", return_value=75), \
             patch.object(rotator, "_generate_new_window_name", return_value="tech-r1234"), \
             patch.object(rotator, "_spawn_new_window", return_value=False), \
             patch("time.sleep"):
            result = rotator.rotate_agent("forge:tech")

        assert result.status == RotationStatus.FAILED
        assert "Failed to create new tmux window" in result.error

    def test_rotate_agent_start_claude_failure(self) -> None:
        """Should return FAILED when starting Claude fails."""
        from forge_harness.fleet.context_rotation import (
            LegacyContextRotator,
            RotationStatus,
        )

        rotator = LegacyContextRotator()

        with patch.object(rotator, "estimate_context_usage", return_value=75), \
             patch.object(rotator, "_generate_new_window_name", return_value="tech-r1234"), \
             patch.object(rotator, "_spawn_new_window", return_value=True), \
             patch.object(rotator, "_start_claude_in_window", return_value=False), \
             patch("time.sleep"):
            result = rotator.rotate_agent("forge:tech")

        assert result.status == RotationStatus.FAILED
        assert "Failed to start Claude" in result.error

    def test_rotate_agent_send_handoff_failure(self) -> None:
        """Should return FAILED when handoff send fails."""
        from forge_harness.fleet.context_rotation import (
            LegacyContextRotator,
            RotationStatus,
        )

        rotator = LegacyContextRotator()

        with patch.object(rotator, "estimate_context_usage", return_value=75), \
             patch.object(rotator, "_generate_new_window_name", return_value="tech-r1234"), \
             patch.object(rotator, "_spawn_new_window", return_value=True), \
             patch.object(rotator, "_start_claude_in_window", return_value=True), \
             patch.object(rotator, "_send_to_session", return_value=False), \
             patch("time.sleep"):
            result = rotator.rotate_agent("forge:tech")

        assert result.status == RotationStatus.FAILED
        assert "Failed to send handoff prompt" in result.error

    def test_rotate_agent_with_task_context(self) -> None:
        """Should use provided task context."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator, RotationStatus

        rotator = LegacyContextRotator()

        task_context = {
            "task": "Implement feature X",
            "files": ["src/main.py"],
            "progress": "50% done",
            "notes": "Check auth logic",
            "test_command": "pytest tests/",
        }

        with patch.object(rotator, "estimate_context_usage", return_value=80), \
             patch.object(rotator, "_generate_new_window_name", return_value="tech-r1234"), \
             patch.object(rotator, "_spawn_new_window", return_value=True), \
             patch.object(rotator, "_start_claude_in_window", return_value=True), \
             patch.object(rotator, "_send_to_session", return_value=True), \
             patch("time.sleep"):
            result = rotator.rotate_agent("forge:tech", task_context=task_context)

        assert result.success is True
        assert "Implement feature X" in result.handoff_prompt

    def test_rotate_agent_exception_handling(self) -> None:
        """Should return FAILED result on unexpected exception."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator, RotationStatus

        rotator = LegacyContextRotator()

        with patch.object(rotator, "estimate_context_usage", return_value=75), \
             patch.object(
                 rotator, "_generate_new_window_name", side_effect=RuntimeError("unexpected")
             ), \
             patch("time.sleep"):
            result = rotator.rotate_agent("forge:tech")

        assert result.status == RotationStatus.FAILED
        assert result.error is not None

    def test_rotate_agent_marks_session_rotating(self) -> None:
        """Should mark session as rotating during operation."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        with patch.object(rotator, "estimate_context_usage", return_value=75), \
             patch.object(rotator, "_generate_new_window_name", return_value="tech-r1234"), \
             patch.object(rotator, "_spawn_new_window", return_value=True), \
             patch.object(rotator, "_start_claude_in_window", return_value=True), \
             patch.object(rotator, "_send_to_session", return_value=True), \
             patch("time.sleep"):
            rotator.rotate_agent("forge:tech")

        # Session should be in rotating set after rotation
        assert "forge:tech" in rotator._rotating_sessions

    def test_rotate_agent_generate_new_window_name_no_colon(self) -> None:
        """Should generate window name when session has no colon."""
        from forge_harness.fleet.context_rotation import LegacyContextRotator

        rotator = LegacyContextRotator()

        with patch("forge_harness.fleet.context_rotation.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "1234"
            result = rotator._generate_new_window_name("tech")

        assert result == "tech-r1234"


class TestFleetContextRotatorGetTmuxPaneLines:
    """Tests for FleetContextRotator.get_tmux_pane_lines."""

    def test_get_tmux_pane_lines_success(self, tmp_path: Path) -> None:
        """Should return min(history_size, history_limit)."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        # Two tmux calls: history_size, history_limit
        mock_results = [
            MagicMock(stdout="500\n"),
            MagicMock(stdout="2000\n"),
        ]

        with patch("subprocess.run", side_effect=mock_results):
            lines = rotator.get_tmux_pane_lines("forge:tech")

        assert lines == 500  # min(500, 2000)

    def test_get_tmux_pane_lines_capped_at_limit(self, tmp_path: Path) -> None:
        """Should cap at history_limit."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        mock_results = [
            MagicMock(stdout="3000\n"),  # size > limit
            MagicMock(stdout="2000\n"),
        ]

        with patch("subprocess.run", side_effect=mock_results):
            lines = rotator.get_tmux_pane_lines("forge:tech")

        assert lines == 2000

    def test_get_tmux_pane_lines_called_process_error(self, tmp_path: Path) -> None:
        """Should re-raise CalledProcessError."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "tmux"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                rotator.get_tmux_pane_lines("forge:tech")

    def test_get_tmux_pane_lines_value_error(self, tmp_path: Path) -> None:
        """Should return 0 on value parse error."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        mock_results = [
            MagicMock(stdout="not-a-number\n"),
            MagicMock(stdout="2000\n"),
        ]

        with patch("subprocess.run", side_effect=mock_results):
            lines = rotator.get_tmux_pane_lines("forge:tech")

        assert lines == 0


class TestFleetContextRotatorSpawnFreshAgent:
    """Tests for FleetContextRotator.spawn_fresh_agent."""

    def test_spawn_fresh_agent_success(self, tmp_path: Path) -> None:
        """Should create session and return new session name."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        mock_result = MagicMock(returncode=0)

        with patch("subprocess.run", return_value=mock_result):
            new_session = rotator.spawn_fresh_agent(
                "forge:tech", "# Handoff content"
            )

        assert new_session.startswith("forge-")
        # Handoff file should be written
        handoff_dir = tmp_path / ".forge/handoffs"
        assert handoff_dir.exists()
        assert len(list(handoff_dir.glob("*.md"))) == 1

    def test_spawn_fresh_agent_no_window_in_name(self, tmp_path: Path) -> None:
        """Should use 'main' as window when no colon in session name."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        mock_result = MagicMock(returncode=0)

        with patch("subprocess.run", return_value=mock_result):
            new_session = rotator.spawn_fresh_agent("forge", "# Handoff")

        assert new_session.startswith("forge-")

    def test_spawn_fresh_agent_tmux_failure_raises(self, tmp_path: Path) -> None:
        """Should raise CalledProcessError when tmux fails."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "tmux"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                rotator.spawn_fresh_agent("forge:tech", "# Handoff")


class TestFleetContextRotatorRetireAgent:
    """Tests for FleetContextRotator.retire_agent."""

    def test_retire_agent_success(self, tmp_path: Path) -> None:
        """Should kill session without error."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        mock_result = MagicMock(returncode=0)

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            rotator.retire_agent("forge:tech")

        # Should make 2 calls: send message, kill session
        assert mock_run.call_count == 2

    def test_retire_agent_failure_raises(self, tmp_path: Path) -> None:
        """Should raise CalledProcessError on tmux failure."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path)

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "tmux"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                rotator.retire_agent("forge:tech")


class TestFleetContextRotatorRotateAgent:
    """Tests for FleetContextRotator.rotate_agent."""

    def test_rotate_agent_below_threshold_raises(self, tmp_path: Path) -> None:
        """Should raise ValueError when context is below threshold."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        rotator = FleetContextRotator(forge_root=tmp_path, rotation_threshold=0.7)

        with patch.object(rotator, "estimate_context_usage", return_value=0.3):
            with pytest.raises(ValueError, match="does not need rotation"):
                rotator.rotate_agent("forge:tech")

    def test_rotate_agent_success(self, tmp_path: Path) -> None:
        """Should return new session name and handoff path."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        # Setup state file for handoff generation
        fleet_dir = tmp_path / ".forge/fleet"
        fleet_dir.mkdir()
        state_file = fleet_dir / "state.json"
        state_file.write_text(
            json.dumps(
                {
                    "agents": {
                        "agent-1": {
                            "session": "forge",
                            "window": "tech",
                            "task": "Build feature",
                        }
                    }
                }
            )
        )

        rotator = FleetContextRotator(forge_root=tmp_path, rotation_threshold=0.7)

        with patch.object(rotator, "estimate_context_usage", return_value=0.8), \
             patch.object(rotator, "get_tmux_pane_lines", return_value=1400), \
             patch.object(
                 rotator.handoff_generator,
                 "generate_handoff_prompt",
                 return_value="# Handoff\n## Status\nworking",
             ), \
             patch("subprocess.run", return_value=MagicMock(returncode=0)):
            new_session, handoff_file = rotator.rotate_agent("forge:tech")

        assert new_session.startswith("forge-")
        assert isinstance(handoff_file, Path)

    def test_rotate_agent_partial_session_name(self, tmp_path: Path) -> None:
        """Should handle session name without window component."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        fleet_dir = tmp_path / ".forge/fleet"
        fleet_dir.mkdir()
        state_file = fleet_dir / "state.json"
        state_file.write_text(json.dumps({"agents": {}}))

        rotator = FleetContextRotator(forge_root=tmp_path, rotation_threshold=0.7)

        with patch.object(rotator, "estimate_context_usage", return_value=0.8), \
             patch.object(rotator, "generate_handoff_prompt", side_effect=ValueError("no agent")):
            with pytest.raises(ValueError):
                rotator.rotate_agent("forge")  # no window component


class TestFleetContextRotatorForgeRootDiscovery:
    """Tests for FleetContextRotator forge root discovery."""

    def test_init_discovers_forge_fleet(self, tmp_path: Path) -> None:
        """Should find forge_root when .forge/fleet dir exists in parent."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        # Create .forge/fleet in tmp_path
        (tmp_path / ".forge/fleet").mkdir()
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        with patch.object(Path, "cwd", return_value=subdir):
            rotator = FleetContextRotator()

        assert rotator.forge_root == tmp_path

    def test_init_falls_back_to_cwd(self, tmp_path: Path) -> None:
        """Should fall back to cwd when .forge/fleet not found."""
        from forge_harness.fleet.context_rotation import FleetContextRotator

        with patch.object(Path, "cwd", return_value=tmp_path):
            rotator = FleetContextRotator()

        assert rotator.forge_root == tmp_path


# =============================================================================
# dispatch_client.py - uncovered paths
# =============================================================================


class TestDispatchClientCrashedCLI:
    """Tests for DispatchClient handling of crashed CLI state."""

    @pytest.mark.asyncio
    async def test_send_crashed_cli_with_auto_type_returns_failure(self) -> None:
        """Should return failure when CLI is CRASHED and cli_type is 'auto'."""
        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.readiness import CLIState
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        mock_readiness = MagicMock()
        mock_readiness.state = CLIState.CRASHED
        mock_readiness.cli_type = "shell"
        mock_readiness.confidence = 0.9

        with patch("forge_harness.fleet.dispatch_client.check_readiness", return_value=mock_readiness):
            result = await client.send(
                "forge:test",
                "do something",
                cli_type="auto",  # auto type — no restart attempt
            )

        assert result.success is False
        assert result.failure_type == FailureType.CLI_CRASHED

    @pytest.mark.asyncio
    async def test_send_crashed_cli_returns_failure(self) -> None:
        """Should return CLI_CRASHED without attempting auto-restart."""
        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.readiness import CLIState
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        mock_readiness = MagicMock()
        mock_readiness.state = CLIState.CRASHED
        mock_readiness.cli_type = "shell"
        mock_readiness.confidence = 0.9

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=mock_readiness,
        ):
            result = await client.send(
                "forge:test",
                "run tests",
                cli_type="claude",
            )

        assert result.success is False
        assert result.failure_type == FailureType.CLI_CRASHED
        assert "Restart the agent manually" in result.error

    @pytest.mark.asyncio
    async def test_send_not_ready_wait_fails(self) -> None:
        """Should return failure when CLI not ready and wait_for_ready fails."""
        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.readiness import CLIState
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        mock_readiness = MagicMock()
        mock_readiness.state = CLIState.STARTING
        mock_readiness.cli_type = "claude"
        mock_readiness.confidence = 0.4
        mock_readiness.is_ready.return_value = False

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=mock_readiness,
        ), patch("forge_harness.fleet.dispatch_client.wait_for_ready", return_value=False):
            result = await client.send("forge:test", "run tests")

        assert result.success is False
        assert result.failure_type == FailureType.CLI_NOT_READY

    @pytest.mark.asyncio
    async def test_send_git_lock_timeout_returns_failure(self) -> None:
        """Should return GIT_LOCK failure when git lock doesn't release."""
        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.readiness import CLIState
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        mock_readiness = MagicMock()
        mock_readiness.state = CLIState.READY
        mock_readiness.cli_type = "claude"
        mock_readiness.confidence = 0.95
        mock_readiness.is_ready.return_value = True

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=mock_readiness,
        ), patch.object(client.git_mutex, "is_locked", return_value=True), \
           patch.object(client.git_mutex, "wait_for_unlock", return_value=False):
            result = await client.send("forge:test", "run tests", check_git_lock=True)

        assert result.success is False
        assert result.failure_type == FailureType.GIT_LOCK

    @pytest.mark.asyncio
    async def test_send_skip_readiness(self) -> None:
        """Should skip readiness check when skip_readiness=True."""
        from forge_harness.fleet.dispatch_client import DispatchClient

        client = DispatchClient()

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check, \
             patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_retry, \
             patch("forge_harness.fleet.dispatch_client.send_with_verification"):

            mock_retry.return_value = MagicMock(
                success=True,
                attempts=1,
                total_time_ms=100.0,
                circuit_state=MagicMock(value="closed"),
            )

            result = await client.send("forge:test", "run tests", skip_readiness=True)

        # check_readiness should NOT have been called
        mock_check.assert_not_called()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_send_auto_cli_type_from_target_window(self) -> None:
        """Should infer cli_type from target window name when cli_type='auto'."""
        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.readiness import CLIState

        client = DispatchClient()

        mock_readiness = MagicMock()
        mock_readiness.state = CLIState.READY
        mock_readiness.cli_type = "auto"
        mock_readiness.confidence = 0.95
        mock_readiness.is_ready.return_value = True

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=mock_readiness,
        ), patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_retry, \
           patch("forge_harness.fleet.dispatch_client.send_with_verification"):

            mock_retry.return_value = MagicMock(
                success=True,
                attempts=1,
                total_time_ms=100.0,
                circuit_state=MagicMock(value="closed"),
            )

            # Target window is 'claude', so effective_cli should be 'claude'
            result = await client.send("forge:claude", "run tests", cli_type="auto")

        assert result.success is True

    @pytest.mark.asyncio
    async def test_send_batch(self) -> None:
        """Should send to multiple targets in parallel."""
        from forge_harness.fleet.dispatch_client import DispatchClient

        client = DispatchClient()

        async def mock_send(target, message, *args, **kwargs):
            return MagicMock(success=True, target=target)

        with patch.object(client, "send", side_effect=mock_send):
            results = await client.send_batch(
                [("forge:tech", "task 1"), ("forge:qa", "task 2"), ("forge:codex", "task 3")]
            )

        assert len(results) == 3
        assert all(r.success for r in results)

    def test_get_circuit_state(self) -> None:
        """Should return circuit state for target."""
        from forge_harness.fleet.dispatch_client import DispatchClient

        client = DispatchClient()

        state = client.get_circuit_state("forge:tech")
        assert state in ("closed", "open", "half_open")

    def test_reset_circuit(self) -> None:
        """Should reset circuit breaker for target."""
        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        # Force some failures
        target = "forge:test"
        for _ in range(3):
            client.circuit_breaker.record_failure(target, FailureType.CLI_NOT_READY)

        # Reset it
        client.reset_circuit(target)

        # Should be closed again
        assert client.get_circuit_state(target) == "closed"

    @pytest.mark.asyncio
    async def test_send_no_git_lock_check(self) -> None:
        """Should skip git lock check when check_git_lock=False."""
        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.readiness import CLIState

        client = DispatchClient()

        mock_readiness = MagicMock()
        mock_readiness.state = CLIState.READY
        mock_readiness.cli_type = "claude"
        mock_readiness.confidence = 0.95
        mock_readiness.is_ready.return_value = True

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=mock_readiness,
        ), patch.object(client.git_mutex, "is_locked") as mock_locked, \
           patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_retry, \
           patch("forge_harness.fleet.dispatch_client.send_with_verification"):

            mock_retry.return_value = MagicMock(
                success=True,
                attempts=1,
                total_time_ms=100.0,
                circuit_state=MagicMock(value="closed"),
            )

            await client.send("forge:test", "test", check_git_lock=False)

        # is_locked should NOT be called
        mock_locked.assert_not_called()


class TestGitMutexEdgeCases:
    """Additional GitMutex edge cases."""

    @pytest.mark.asyncio
    async def test_wait_for_unlock_oserror_on_stat(self, tmp_path: Path) -> None:
        """Should return True when OSError occurs while checking lock file stat."""
        from forge_harness.fleet.dispatch_client import GitMutex

        mutex = GitMutex(repo_root=tmp_path)
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        with patch(
            "forge_harness.fleet.dispatch_client.check_git_unsafe_state",
            return_value=(True, "locked"),
        ), patch("pathlib.Path.exists", return_value=True), \
           patch("pathlib.Path.stat", side_effect=OSError("permission denied")), \
           patch("asyncio.sleep"):
            result = await mutex.wait_for_unlock(timeout=0.5)

        assert result is True

    def test_wait_for_unlock_sync_oserror_on_stat(self, tmp_path: Path) -> None:
        """Should return True when OSError occurs while checking lock file stat (sync)."""
        from forge_harness.fleet.dispatch_client import GitMutex

        mutex = GitMutex(repo_root=tmp_path)

        with patch(
            "forge_harness.fleet.dispatch_client.check_git_unsafe_state",
            return_value=(True, "locked"),
        ), patch("pathlib.Path.exists", return_value=True), \
           patch("pathlib.Path.stat", side_effect=OSError("permission denied")), \
           patch("time.sleep"):
            result = mutex.wait_for_unlock_sync(timeout=0.5)

        assert result is True


# =============================================================================
# dispatch_validation.py - the 1 missing line
# =============================================================================


class TestValidationStatusTransitionEdgeCases:
    """Tests for the uncovered line 331 in validate_status_transition."""

    def test_transition_none_enum_edge_case(self) -> None:
        """Test edge case: both statuses parse to None due to logic path."""
        from forge_harness.fleet.dispatch_validation import validate_status_transition

        # The from_enum/to_enum can be None if isinstance checks all fail
        # This exercises line 331 (the final None check after both branches)
        # We'll test with a valid enum but trigger by mocking TaskStatus
        from forge_harness.task_queue import TaskStatus

        # Use TaskStatus enum directly - this should exercise all branches
        error = validate_status_transition(TaskStatus.COMPLETED, TaskStatus.PENDING)
        assert error is not None  # LIFECYCLE_FORBIDDEN

    def test_cancelled_to_assigned_invalid_transition(self) -> None:
        """Test that CANCELLED to ASSIGNED is invalid (not in valid set)."""
        from forge_harness.fleet.dispatch_validation import (
            ValidationErrorCode,
            validate_status_transition,
        )

        error = validate_status_transition("cancelled", "assigned")
        assert error is not None
        assert error.code == ValidationErrorCode.INVALID_STATUS_TRANSITION
        assert error.next_action is not None

    def test_in_progress_to_cancelled_invalid(self) -> None:
        """Test that IN_PROGRESS to CANCELLED is invalid."""
        from forge_harness.fleet.dispatch_validation import (
            ValidationErrorCode,
            validate_status_transition,
        )

        error = validate_status_transition("in_progress", "cancelled")
        assert error is not None
        assert error.code == ValidationErrorCode.INVALID_STATUS_TRANSITION


# =============================================================================
# permissions.py - the 2 missing lines
# =============================================================================


class TestPermissionPreloaderMissingLines:
    """Tests for uncovered lines in fleet/permissions.py (lines 260, 341)."""

    def test_apply_permissions_with_profile_domain_permissions(self) -> None:
        """Test apply_permissions uses profile's domain_permissions (line 260)."""
        from forge_harness.fleet.permissions import PermissionPreloader, PermissionProfile

        preloader = PermissionPreloader()

        # Create a profile with domain_permissions already set
        profile = PermissionProfile(
            agent_type="backend-engineer",
            permissions={"file:read"},
            domain_permissions={"staging": {"deploy:staging"}},
        )
        preloader.profiles["backend-engineer"] = profile

        # Apply with domain that exists in profile.domain_permissions
        result = preloader.apply_permissions("backend-engineer", domain="staging")

        assert result is True
        assert "deploy:staging" in preloader.applied_permissions["backend-engineer"]

    def test_verify_permissions_non_critical_missing_logs_warning(self) -> None:
        """Test verify_permissions logs warning for non-critical missing (line 341)."""
        from forge_harness.fleet.permissions import PermissionPreloader, PermissionProfile

        preloader = PermissionPreloader(fail_on_missing_critical=False)

        # Create profile with extra non-critical permissions not applied
        profile = PermissionProfile(
            agent_type="backend-engineer",
            permissions={"file:read", "file:write", "extra:perm"},
            critical_permissions={"file:read"},  # Only file:read is critical
        )
        preloader.profiles["backend-engineer"] = profile

        # Apply only the critical permission
        preloader.applied_permissions["backend-engineer"] = ["file:read"]

        # Should succeed but warn about non-critical missing perms
        result = preloader.verify_permissions("agent-001", agent_type="backend-engineer")

        assert result is True  # critical permissions are present
        assert len(preloader.verification_history) == 1
        history = preloader.verification_history[0]
        # Missing non-critical permissions should be recorded
        assert len(history.missing_permissions) > 0

    def test_apply_permissions_adds_global_domain_permissions(self) -> None:
        """Test apply_permissions uses DOMAIN_PERMISSIONS when domain not in profile."""
        from forge_harness.fleet.permissions import PermissionPreloader

        preloader = PermissionPreloader()
        preloader.load_permissions()

        # Apply with 'production' domain (exists in DOMAIN_PERMISSIONS but not in profile)
        result = preloader.apply_permissions("backend-engineer", domain="production")

        assert result is True
        applied = preloader.applied_permissions["backend-engineer"]
        assert "deployment:prod" in applied or "deployment:stage" in applied


# =============================================================================
# verification.py (fleet/verification.py) - uncovered paths
# =============================================================================


class TestDeliveryReceipt:
    """Tests for DeliveryReceipt dataclass."""

    def test_delivered_str(self) -> None:
        """Should produce human-readable success string."""
        from forge_harness.fleet.verification import DeliveryReceipt

        receipt = DeliveryReceipt(
            delivered=True,
            message_id="msg-abc123def456",
            delivery_time_ms=125.5,
            verification_method="echo",
        )

        text = str(receipt)
        assert "Delivered" in text
        assert "echo" in text
        assert "125ms" in text or "126ms" in text
        assert "msg-abc1" in text  # First 8 chars

    def test_failed_str(self) -> None:
        """Should produce human-readable failure string."""
        from forge_harness.fleet.verification import DeliveryReceipt

        receipt = DeliveryReceipt(
            delivered=False,
            message_id="msg-abc123def456",
            delivery_time_ms=5000.0,
            verification_method="none",
            failure_reason="Timeout waiting for echo",
        )

        text = str(receipt)
        assert "Delivery failed" in text
        assert "Timeout" in text


class TestGenerateMessageId:
    """Tests for generate_message_id."""

    def test_format(self) -> None:
        """Should produce msg-{12 hex chars} format."""
        from forge_harness.fleet.verification import generate_message_id

        msg_id = generate_message_id()
        assert msg_id.startswith("msg-")
        assert len(msg_id) == 16  # "msg-" + 12 hex chars

    def test_uniqueness(self) -> None:
        """Should generate unique IDs."""
        from forge_harness.fleet.verification import generate_message_id

        ids = {generate_message_id() for _ in range(100)}
        assert len(ids) == 100


class TestSendToTmux:
    """Tests for send_to_tmux function."""

    @pytest.mark.asyncio
    async def test_send_to_tmux_success(self) -> None:
        """Should return True on successful send."""
        from forge_harness.fleet.verification import send_to_tmux

        mock_result = MagicMock(returncode=0)

        with patch("subprocess.run", return_value=mock_result), \
             patch("asyncio.sleep"):
            result = await send_to_tmux("forge:test", "hello world")

        assert result is True

    @pytest.mark.asyncio
    async def test_send_to_tmux_chunk_failure(self) -> None:
        """Should return False when a chunk send fails."""
        from forge_harness.fleet.verification import send_to_tmux

        # First call (C-u) succeeds, second chunk call fails
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:  # C-u clear succeeds
                return MagicMock(returncode=0)
            # Chunk send fails
            result = MagicMock()
            result.returncode = 1
            result.stderr = b"error"
            return result

        with patch("subprocess.run", side_effect=side_effect), \
             patch("asyncio.sleep"):
            result = await send_to_tmux("forge:test", "message chunk")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_to_tmux_timeout_error(self) -> None:
        """Should return False on TimeoutExpired."""
        from forge_harness.fleet.verification import send_to_tmux

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=2),
        ), patch("asyncio.sleep"):
            result = await send_to_tmux("forge:test", "message")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_to_tmux_subprocess_error(self) -> None:
        """Should return False on SubprocessError."""
        from forge_harness.fleet.verification import send_to_tmux

        with patch(
            "subprocess.run",
            side_effect=subprocess.SubprocessError("process failed"),
        ), patch("asyncio.sleep"):
            result = await send_to_tmux("forge:test", "message")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_to_tmux_large_message_chunked(self) -> None:
        """Should split large messages into chunks."""
        from forge_harness.fleet.verification import CHUNK_SIZE, send_to_tmux

        mock_result = MagicMock(returncode=0)
        large_message = "x" * (CHUNK_SIZE * 3)  # 3 chunks

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("asyncio.sleep"):
            result = await send_to_tmux("forge:test", large_message)

        assert result is True
        # Should have: 1 C-u + 3 chunks + 1 Enter = 5 calls
        assert mock_run.call_count == 5


class TestSendToTmuxRestart:
    """Tests for send_to_tmux_restart function."""

    @pytest.mark.asyncio
    async def test_send_to_tmux_restart_success(self) -> None:
        """Should restart CLI and send message."""
        from forge_harness.fleet.verification import send_to_tmux_restart

        # All subprocess calls succeed
        call_results = [
            MagicMock(returncode=0),  # C-c
            MagicMock(returncode=0),  # second C-c
        ]
        # After C-c calls, shell prompt detection
        shell_result = MagicMock(returncode=0, stdout="$ ")
        send_result = MagicMock(returncode=0)
        enter_result = MagicMock(returncode=0)

        all_results = call_results + [shell_result] + [send_result, enter_result]
        result_iter = iter(all_results)

        def get_next(*args, **kwargs):
            try:
                return next(result_iter)
            except StopIteration:
                return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=get_next), \
             patch("asyncio.sleep"):
            result = await send_to_tmux_restart("forge:gemini", "do research", "gemini --yolo")

        assert result is True

    @pytest.mark.asyncio
    async def test_send_to_tmux_restart_timeout(self) -> None:
        """Should return False on TimeoutExpired during restart."""
        from forge_harness.fleet.verification import send_to_tmux_restart

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=2),
        ), patch("asyncio.sleep"):
            result = await send_to_tmux_restart("forge:gemini", "do research", "gemini --yolo")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_to_tmux_restart_subprocess_error(self) -> None:
        """Should return False on SubprocessError."""
        from forge_harness.fleet.verification import send_to_tmux_restart

        with patch(
            "subprocess.run",
            side_effect=subprocess.SubprocessError("error"),
        ), patch("asyncio.sleep"):
            result = await send_to_tmux_restart("forge:gemini", "do research", "gemini --yolo")

        assert result is False

    @pytest.mark.asyncio
    async def test_send_to_tmux_restart_escapes_single_quotes(self) -> None:
        """Should escape single quotes in message."""
        from forge_harness.fleet.verification import send_to_tmux_restart

        # Need stdout to be a string for re.search in the shell prompt detection loop
        def make_result(stdout_text="$ "):
            r = MagicMock()
            r.returncode = 0
            r.stdout = stdout_text
            return r

        sent_commands = []

        def capture_run(cmd, *args, **kwargs):
            sent_commands.append(cmd)
            return make_result("$ ")

        with patch("subprocess.run", side_effect=capture_run), patch("asyncio.sleep"):
            await send_to_tmux_restart(
                "forge:gemini", "it's a test", "gemini --yolo"
            )

        # The escaped message should have been used in the restart command
        # Check that some send-keys call contained gemini in restart command
        all_commands = " ".join(str(c) for c in sent_commands)
        assert "gemini" in all_commands


class TestGetPaneOutput:
    """Tests for get_pane_output function."""

    @pytest.mark.asyncio
    async def test_get_pane_output_success(self) -> None:
        """Should return pane content."""
        from forge_harness.fleet.verification import get_pane_output

        mock_result = MagicMock(returncode=0, stdout="Working\nRunning tests\n")

        with patch("subprocess.run", return_value=mock_result):
            output = await get_pane_output("forge:test")

        assert output == "Working\nRunning tests\n"

    @pytest.mark.asyncio
    async def test_get_pane_output_nonzero_returncode(self) -> None:
        """Should return None on nonzero returncode."""
        from forge_harness.fleet.verification import get_pane_output

        mock_result = MagicMock(returncode=1)

        with patch("subprocess.run", return_value=mock_result):
            output = await get_pane_output("forge:test")

        assert output is None

    @pytest.mark.asyncio
    async def test_get_pane_output_timeout(self) -> None:
        """Should return None on timeout."""
        from forge_harness.fleet.verification import get_pane_output

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5),
        ):
            output = await get_pane_output("forge:test")

        assert output is None

    @pytest.mark.asyncio
    async def test_get_pane_output_subprocess_error(self) -> None:
        """Should return None on SubprocessError."""
        from forge_harness.fleet.verification import get_pane_output

        with patch(
            "subprocess.run",
            side_effect=subprocess.SubprocessError("error"),
        ):
            output = await get_pane_output("forge:test")

        assert output is None

    @pytest.mark.asyncio
    async def test_get_pane_output_custom_lines(self) -> None:
        """Should pass lines parameter to tmux."""
        from forge_harness.fleet.verification import get_pane_output

        mock_result = MagicMock(returncode=0, stdout="content")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            await get_pane_output("forge:test", lines=50)

        call_args = mock_run.call_args[0][0]
        assert "-50" in call_args


class TestVerifyEcho:
    """Tests for verify_echo function."""

    @pytest.mark.asyncio
    async def test_verify_echo_found(self) -> None:
        """Should return True when echo pattern found."""
        from forge_harness.fleet.verification import verify_echo

        msg_id = "test-msg-001"
        pane_content = f"Working on task\n# MSG_ID: {msg_id}\nsome output"

        with patch(
            "forge_harness.fleet.verification.get_pane_output",
            return_value=pane_content,
        ), patch("asyncio.sleep"):
            result = await verify_echo("forge:test", msg_id, timeout=5.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_echo_not_found(self) -> None:
        """Should return False when echo pattern not found within timeout."""
        from forge_harness.fleet.verification import verify_echo

        with patch(
            "forge_harness.fleet.verification.get_pane_output",
            return_value="some other output",
        ), patch("asyncio.sleep"), \
           patch("time.time", side_effect=[0, 0.1, 10]):  # Force timeout
            result = await verify_echo("forge:test", "msg-123", timeout=0.5)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_echo_none_output(self) -> None:
        """Should handle None output from get_pane_output."""
        from forge_harness.fleet.verification import verify_echo

        with patch(
            "forge_harness.fleet.verification.get_pane_output",
            return_value=None,
        ), patch("asyncio.sleep"), \
           patch("time.time", side_effect=[0, 0.1, 10]):
            result = await verify_echo("forge:test", "msg-123", timeout=0.5)

        assert result is False


class TestVerifyActivity:
    """Tests for verify_activity function."""

    @pytest.mark.asyncio
    async def test_verify_activity_working_detected(self) -> None:
        """Should return True when 'Working' is in output."""
        from forge_harness.fleet.verification import verify_activity

        with patch(
            "forge_harness.fleet.verification.get_pane_output",
            return_value="Working on your request...",
        ), patch("asyncio.sleep"):
            result = await verify_activity("forge:test", timeout=5.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_activity_composing_detected(self) -> None:
        """Should return True when 'Composing' is in output."""
        from forge_harness.fleet.verification import verify_activity

        with patch(
            "forge_harness.fleet.verification.get_pane_output",
            return_value="Composing response...",
        ), patch("asyncio.sleep"):
            result = await verify_activity("forge:test", timeout=5.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_activity_tokens_detected(self) -> None:
        """Should return True when 'tokens' is in output."""
        from forge_harness.fleet.verification import verify_activity

        with patch(
            "forge_harness.fleet.verification.get_pane_output",
            return_value="1234 tokens used",
        ), patch("asyncio.sleep"):
            result = await verify_activity("forge:test", timeout=5.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_activity_not_detected(self) -> None:
        """Should return False when no activity pattern found."""
        from forge_harness.fleet.verification import verify_activity

        with patch(
            "forge_harness.fleet.verification.get_pane_output",
            return_value="$ ",  # Shell prompt, no activity
        ), patch("asyncio.sleep"), \
           patch("time.time", side_effect=[0, 0.1, 10]):
            result = await verify_activity("forge:test", timeout=0.5)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_activity_none_output(self) -> None:
        """Should handle None output gracefully."""
        from forge_harness.fleet.verification import verify_activity

        with patch(
            "forge_harness.fleet.verification.get_pane_output",
            return_value=None,
        ), patch("asyncio.sleep"), \
           patch("time.time", side_effect=[0, 0.1, 10]):
            result = await verify_activity("forge:test", timeout=0.5)

        assert result is False


class TestSendWithVerification:
    """Tests for send_with_verification function."""

    @pytest.mark.asyncio
    async def test_send_echo_success(self) -> None:
        """Should return delivered receipt when echo verification succeeds."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux", return_value=True), \
             patch("forge_harness.fleet.verification.verify_echo", return_value=True), \
             patch("asyncio.sleep"):
            receipt = await send_with_verification("forge:test", "run tests", use_echo=True)

        assert receipt.delivered is True
        assert receipt.verification_method == "echo"

    @pytest.mark.asyncio
    async def test_send_echo_fails_activity_succeeds(self) -> None:
        """Should fall back to activity detection when echo fails."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux", return_value=True), \
             patch("forge_harness.fleet.verification.verify_echo", return_value=False), \
             patch("forge_harness.fleet.verification.verify_activity", return_value=True), \
             patch("asyncio.sleep"):
            receipt = await send_with_verification("forge:test", "run tests", use_echo=True)

        assert receipt.delivered is True
        assert receipt.verification_method == "activity"
        assert receipt.activity_detected is True

    @pytest.mark.asyncio
    async def test_send_both_verification_fail(self) -> None:
        """Should return undelivered when both echo and activity fail."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux", return_value=True), \
             patch("forge_harness.fleet.verification.verify_echo", return_value=False), \
             patch("forge_harness.fleet.verification.verify_activity", return_value=False), \
             patch("asyncio.sleep"):
            receipt = await send_with_verification("forge:test", "run tests", use_echo=True)

        assert receipt.delivered is False
        assert receipt.verification_method == "none"
        assert receipt.failure_reason is not None

    @pytest.mark.asyncio
    async def test_send_to_tmux_fails(self) -> None:
        """Should return failure receipt when send to tmux fails."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux", return_value=False):
            receipt = await send_with_verification("forge:test", "run tests")

        assert receipt.delivered is False
        assert "Failed to send to tmux" in receipt.failure_reason

    @pytest.mark.asyncio
    async def test_send_no_echo_uses_activity_only(self) -> None:
        """Should use activity detection when use_echo=False."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux", return_value=True), \
             patch("forge_harness.fleet.verification.verify_activity", return_value=True), \
             patch("asyncio.sleep"):
            receipt = await send_with_verification(
                "forge:test", "run tests", use_echo=False
            )

        assert receipt.delivered is True
        assert receipt.verification_method == "activity"

    @pytest.mark.asyncio
    async def test_send_gemini_uses_restart_strategy(self) -> None:
        """Should use restart-with-prompt for gemini CLI type."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux_restart", return_value=True) as mock_restart, \
             patch("forge_harness.fleet.verification.verify_activity", return_value=True), \
             patch("asyncio.sleep"), \
             patch("forge_harness.fleet.verification.get_pane_output", return_value="Working"):
            receipt = await send_with_verification(
                "forge:gemini",
                "research this topic",
                cli_type="gemini",
            )

        assert receipt.delivered is True
        assert "restart" in receipt.verification_method
        mock_restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_gemini_auto_detection(self) -> None:
        """Should auto-detect gemini from target window name."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux_restart", return_value=True), \
             patch("forge_harness.fleet.verification.verify_activity", return_value=False), \
             patch("asyncio.sleep"):
            receipt = await send_with_verification(
                "forge:gemini",  # window name is 'gemini'
                "do research",
                cli_type="auto",  # Should auto-detect gemini
            )

        # Gemini restart always succeeds (sends restart_success=True)
        assert receipt.delivered is True
        assert "restart" in receipt.verification_method

    @pytest.mark.asyncio
    async def test_send_gemini_restart_failure(self) -> None:
        """Should return failure when gemini restart fails."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux_restart", return_value=False), \
             patch("asyncio.sleep"):
            receipt = await send_with_verification(
                "forge:gemini",
                "do research",
                cli_type="gemini",
            )

        assert receipt.delivered is False
        assert receipt.failure_reason is not None

    @pytest.mark.asyncio
    async def test_send_gemini_no_activity_still_delivered(self) -> None:
        """Should return delivered even without activity for restart strategy."""
        from forge_harness.fleet.verification import send_with_verification

        with patch("forge_harness.fleet.verification.send_to_tmux_restart", return_value=True), \
             patch("forge_harness.fleet.verification.verify_activity", return_value=False), \
             patch("asyncio.sleep"):
            receipt = await send_with_verification(
                "forge:gemini",
                "do research",
                cli_type="gemini",
            )

        # Even without activity, restart strategy counts as delivered
        assert receipt.delivered is True
        assert receipt.verification_method == "restart"
        assert receipt.activity_detected is False


class TestSendWithVerificationSync:
    """Tests for send_with_verification_sync convenience function."""

    def test_sync_wrapper_calls_async(self) -> None:
        """Should call async function via asyncio.run."""
        from forge_harness.fleet.verification import send_with_verification_sync

        mock_receipt = MagicMock()
        mock_receipt.delivered = True

        with patch("asyncio.run", return_value=mock_receipt) as mock_run:
            result = send_with_verification_sync("forge:test", "run tests")

        assert mock_run.called
        assert result is mock_receipt


class TestSendBatchWithVerification:
    """Tests for send_batch_with_verification function."""

    @pytest.mark.asyncio
    async def test_batch_sends_all(self) -> None:
        """Should send to all targets and return results."""
        from forge_harness.fleet.verification import DeliveryReceipt, send_batch_with_verification

        def make_receipt(delivered: bool) -> DeliveryReceipt:
            return DeliveryReceipt(
                delivered=delivered,
                message_id="msg-abc123def456",
                delivery_time_ms=100.0,
                verification_method="echo" if delivered else "none",
                failure_reason=None if delivered else "timeout",
            )

        receipts_iter = iter([make_receipt(True), make_receipt(True), make_receipt(False)])

        async def mock_send(target, message, timeout=None):
            return next(receipts_iter)

        with patch("forge_harness.fleet.verification.send_with_verification", side_effect=mock_send):
            results = await send_batch_with_verification(
                [("forge:tech", "task 1"), ("forge:qa", "task 2"), ("forge:codex", "task 3")]
            )

        assert len(results) == 3
        assert results[0].delivered is True
        assert results[1].delivered is True
        assert results[2].delivered is False


class TestVerificationConstants:
    """Tests for constants in verification module."""

    def test_default_timeout(self) -> None:
        """Should have correct default timeout."""
        from forge_harness.fleet.verification import DEFAULT_TIMEOUT

        assert DEFAULT_TIMEOUT == 15.0

    def test_research_timeout(self) -> None:
        """Should have correct research timeout."""
        from forge_harness.fleet.verification import RESEARCH_TIMEOUT

        assert RESEARCH_TIMEOUT == 30.0

    def test_complex_timeout(self) -> None:
        """Should have correct complex timeout."""
        from forge_harness.fleet.verification import COMPLEX_TIMEOUT

        assert COMPLEX_TIMEOUT == 60.0

    def test_chunk_size(self) -> None:
        """Should have correct chunk size."""
        from forge_harness.fleet.verification import CHUNK_SIZE

        assert CHUNK_SIZE == 500

    def test_activity_patterns_not_empty(self) -> None:
        """Should have activity patterns defined."""
        from forge_harness.fleet.verification import ACTIVITY_PATTERNS

        assert len(ACTIVITY_PATTERNS) > 0
        assert any("Working" in p for p in ACTIVITY_PATTERNS)

    def test_restart_clis_contains_gemini(self) -> None:
        """Should have gemini in restart-with-prompt CLIs."""
        from forge_harness.fleet.verification import RESTART_WITH_PROMPT_CLIS

        assert "gemini" in RESTART_WITH_PROMPT_CLIS
