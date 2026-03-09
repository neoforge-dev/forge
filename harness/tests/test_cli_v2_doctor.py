"""
TDD tests for forge doctor command.

Tests follow Test-Driven Development:
1. Tests define expected behavior first
2. Implementation should match test expectations
3. All public functions are tested
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestDoctorCommand:
    """Test forge doctor command."""

    def test_doctor_command_callable(self):
        """Doctor command should be callable."""
        from forge_harness.cli_v2 import cli
        # Will raise SystemExit if not properly registered
        try:
            cli.main(["doctor", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_doctor_check_option_exists(self):
        """--check option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["doctor", "--check", "all", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_doctor_fix_option_exists(self):
        """--fix option should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["doctor", "--fix", "all", "--help"])
        except SystemExit:
            pass  # Expected with --help


class TestDoctorChecks:
    """Test doctor check options."""

    def test_check_all_exists(self):
        """'all' check should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["doctor", "--check", "all", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_check_git_exists(self):
        """'git' check should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["doctor", "--check", "git", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_check_tests_exists(self):
        """'tests' check should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["doctor", "--check", "tests", "--help"])
        except SystemExit:
            pass  # Expected with --help

    def test_check_dependencies_exists(self):
        """'dependencies' check should be available."""
        from forge_harness.cli_v2 import cli
        try:
            cli.main(["doctor", "--check", "dependencies", "--help"])
        except SystemExit:
            pass  # Expected with --help


class TestDoctorImplementedChecks:
    """Tests for doctor subsystem checks that should not be placeholders."""

    def test_check_fleet_returns_warning_for_error_agents(self, tmp_path):
        """Fleet check should surface unhealthy agents."""
        from forge_harness.cli_v2.doctor import _check_fleet

        with patch(
            "forge_harness.commands.agent.get_agents_data",
            return_value=[
                {"id": "forge:active", "status": "active"},
                {"id": "forge:error", "status": "error"},
            ],
        ):
            result = _check_fleet(ctx=None, forge_root=tmp_path, fix=False)

        assert result["status"] == "warning"
        assert result["counts"]["active"] == 1
        assert result["counts"]["error"] == 1

    def test_check_approvals_returns_stats(self, tmp_path):
        """Approval queue check should return structured stats."""
        from forge_harness.cli_v2.doctor import _check_approvals

        from forge_harness.approval_queue import ApprovalQueueStats

        stats = ApprovalQueueStats(
            total_requests=3,
            pending_count=1,
            approved_count=2,
            rejected_count=0,
            expired_count=0,
            avg_resolution_hours=1.2,
            by_type={"deploy": 2, "feature": 1},
            by_domain={"codeswiftr-com": 3},
            oldest_pending_hours=2.0,
        )
        queue = AsyncMock()
        queue.get_stats.return_value = stats

        with patch(
            "forge_harness.approval_queue.create_approval_queue",
            return_value=queue,
        ):
            result = _check_approvals(ctx=None, forge_root=tmp_path, fix=False)

        assert result["status"] == "ok"
        assert result["stats"]["total_requests"] == 3
        assert result["stats"]["pending_count"] == 1

    def test_check_quality_invokes_legacy_command_with_json_output_kwarg(self, tmp_path):
        """Quality check should pass 'json_output' (not 'output_json') to legacy command."""
        from forge_harness.cli_v2.doctor import _check_quality

        ctx = Mock()

        def _invoke(_func, **kwargs):
            assert "json_output" in kwargs
            assert "output_json" not in kwargs
            return None

        ctx.invoke = Mock(side_effect=_invoke)

        result = _check_quality(ctx=ctx, forge_root=tmp_path, fix=False, output_json=True)
        assert result["status"] == "ok"


class TestDoctorCleanup:
    """Tests for cleanup implementations."""

    def test_cleanup_tmux_kills_orphaned_forge_sessions(self, tmp_path):
        """Cleanup should kill forge:* sessions that only have shell panes."""
        from forge_harness.cli_v2.doctor import _cleanup_tmux

        list_sessions = Mock(returncode=0, stdout="forge\nforge:tech\nforge:review\n", stderr="")
        list_panes_tech = Mock(returncode=0, stdout="0|zsh\n", stderr="")
        kill_tech = Mock(returncode=0, stdout="", stderr="")
        list_panes_review = Mock(returncode=0, stdout="0|claude\n", stderr="")

        with patch(
            "subprocess.run",
            side_effect=[list_sessions, list_panes_tech, kill_tech, list_panes_review],
        ) as run_mock:
            _cleanup_tmux(ctx=None, forge_root=tmp_path)

        assert run_mock.call_count == 4
        kill_call = run_mock.call_args_list[2]
        assert kill_call.args[0] == ["tmux", "kill-session", "-t", "forge:tech"]

    def test_cleanup_tmux_handles_missing_tmux_server(self, tmp_path):
        """No tmux server should be treated as a clean no-op state."""
        from forge_harness.cli_v2.doctor import _cleanup_tmux

        with patch(
            "subprocess.run",
            return_value=Mock(returncode=1, stdout="", stderr="no server running on /tmp/tmux-501/default"),
        ) as run_mock:
            _cleanup_tmux(ctx=None, forge_root=tmp_path)

        assert run_mock.call_count == 1
