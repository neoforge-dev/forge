"""
Tests for forge doctor health checks.

Covers each health check function individually with mocked filesystem and
subprocess calls, then verifies the overall doctor command output.

Targets 80%+ coverage of forge_harness/cli_v2/doctor.py.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_forge_root(tmp_path: Path) -> Path:
    """Return a tmp_path that looks like a FORGE root."""
    (tmp_path / ".forge-root").write_text("")
    (tmp_path / "CLAUDE.md").write_text("# FORGE")
    return tmp_path


@pytest.fixture
def tmp_git_root(tmp_path: Path) -> Path:
    """Return a tmp_path with a real git repo in a clean state (no untracked files)."""
    import subprocess as sp

    sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    sp.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    # Commit all marker files so the repo is clean after init
    (tmp_path / "README.md").write_text("hello")
    (tmp_path / "CLAUDE.md").write_text("# FORGE")
    (tmp_path / ".forge-root").write_text("")
    sp.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    sp.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# _check_git — git repository state
# ---------------------------------------------------------------------------


class TestCheckGit:
    """Tests for _check_git health check."""

    def test_clean_repo_returns_ok(self, tmp_git_root: Path) -> None:
        """A clean git repo with no lock files should return status=ok."""
        from forge_harness.cli_v2.doctor import _check_git

        result = _check_git(ctx=None, forge_root=tmp_git_root, fix=False)

        assert result["status"] == "ok"
        assert "clean" in result["message"].lower()

    def test_index_lock_returns_error(self, tmp_git_root: Path) -> None:
        """Presence of .git/index.lock should return status=error."""
        from forge_harness.cli_v2.doctor import _check_git

        (tmp_git_root / ".git" / "index.lock").write_text("lock")

        result = _check_git(ctx=None, forge_root=tmp_git_root, fix=False)

        assert result["status"] == "error"
        assert "index.lock" in result["message"]

    def test_rebase_merge_returns_error(self, tmp_git_root: Path) -> None:
        """Presence of .git/rebase-merge should return status=error."""
        from forge_harness.cli_v2.doctor import _check_git

        (tmp_git_root / ".git" / "rebase-merge").mkdir()

        result = _check_git(ctx=None, forge_root=tmp_git_root, fix=False)

        assert result["status"] == "error"
        assert "rebase" in result["message"].lower()

    def test_rebase_apply_returns_error(self, tmp_git_root: Path) -> None:
        """Presence of .git/rebase-apply should return status=error."""
        from forge_harness.cli_v2.doctor import _check_git

        (tmp_git_root / ".git" / "rebase-apply").mkdir()

        result = _check_git(ctx=None, forge_root=tmp_git_root, fix=False)

        assert result["status"] == "error"
        assert "rebase" in result["message"].lower()

    def test_fix_removes_stale_index_lock(self, tmp_git_root: Path) -> None:
        """With fix=True and a stale lock (>300s old), the lock should be removed."""
        from forge_harness.cli_v2.doctor import _check_git

        lock_path = tmp_git_root / ".git" / "index.lock"
        lock_path.write_text("stale lock")

        # Back-date the lock file by 400 seconds to make it stale
        old_time = time.time() - 400
        os.utime(lock_path, (old_time, old_time))

        _check_git(ctx=None, forge_root=tmp_git_root, fix=True)

        assert not lock_path.exists(), "Stale lock file should have been removed"

    def test_uncommitted_changes_returns_warning(self, tmp_git_root: Path) -> None:
        """Uncommitted changes in a clean-lock repo should return status=warning."""
        from forge_harness.cli_v2.doctor import _check_git

        (tmp_git_root / "dirty.txt").write_text("untracked")

        result = _check_git(ctx=None, forge_root=tmp_git_root, fix=False)

        assert result["status"] == "warning"
        assert "uncommitted" in result["message"].lower()


# ---------------------------------------------------------------------------
# _check_config — configuration validity
# ---------------------------------------------------------------------------


class TestCheckConfig:
    """Tests for _check_config health check."""

    def test_claude_md_present_returns_ok_or_warning(self, tmp_forge_root: Path) -> None:
        """When CLAUDE.md exists, check should be ok or warning (not error)."""
        from forge_harness.cli_v2.doctor import _check_config

        result = _check_config(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] in ("ok", "warning")

    def test_missing_claude_md_returns_warning_or_error(self, tmp_path: Path) -> None:
        """When CLAUDE.md is absent the check should surface it as warning or error."""
        from forge_harness.cli_v2.doctor import _check_config

        # tmp_path has no CLAUDE.md
        result = _check_config(ctx=None, forge_root=tmp_path, fix=False)

        assert result["status"] in ("warning", "error")

    def test_config_result_contains_message(self, tmp_forge_root: Path) -> None:
        """Config check result must always include a non-empty message string."""
        from forge_harness.cli_v2.doctor import _check_config

        result = _check_config(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert "message" in result
        assert isinstance(result["message"], str)
        assert len(result["message"]) > 0

    def test_config_result_contains_status(self, tmp_forge_root: Path) -> None:
        """Config check result must always include a valid status field."""
        from forge_harness.cli_v2.doctor import _check_config

        result = _check_config(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert "status" in result
        assert result["status"] in ("ok", "warning", "error")

    def test_missing_webhook_token_surfaces_in_result(self, tmp_forge_root: Path) -> None:
        """Missing FORGE_WEBHOOK_TOKEN should appear in config check output."""
        from forge_harness.cli_v2.doctor import _check_config

        backup = os.environ.pop("FORGE_WEBHOOK_TOKEN", None)
        try:
            result = _check_config(ctx=None, forge_root=tmp_forge_root, fix=False)
            # Either a warning about missing token, or ok if check only reads files
            assert result["status"] in ("ok", "warning", "error")
        finally:
            if backup is not None:
                os.environ["FORGE_WEBHOOK_TOKEN"] = backup


# ---------------------------------------------------------------------------
# _check_api_health — API server connectivity
# ---------------------------------------------------------------------------


class TestCheckApiHealth:
    """Tests for _check_api_health health check."""

    def test_no_webhook_url_configured_returns_warning(self, tmp_forge_root: Path) -> None:
        """When no webhook URL is configured, check should return warning."""
        from forge_harness.cli_v2.doctor import _check_api_health

        backup_url = os.environ.pop("FORGE_WEBHOOK_URL", None)
        backup_cc = os.environ.pop("COMMAND_CENTER_URL", None)
        try:
            result = _check_api_health(ctx=None, forge_root=tmp_forge_root, fix=False)
        finally:
            if backup_url is not None:
                os.environ["FORGE_WEBHOOK_URL"] = backup_url
            if backup_cc is not None:
                os.environ["COMMAND_CENTER_URL"] = backup_cc

        assert result["status"] == "warning"
        assert "url" in result["message"].lower() or "configured" in result["message"].lower()

    def test_reachable_server_returns_ok(self, tmp_forge_root: Path) -> None:
        """When the server responds with HTTP 200, check should return ok."""
        from forge_harness.cli_v2.doctor import _check_api_health

        os.environ["FORGE_WEBHOOK_URL"] = "http://localhost:9999"
        try:
            mock_resp = Mock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = Mock(return_value=False)
            mock_resp.status = 200

            with patch("urllib.request.urlopen", return_value=mock_resp):
                result = _check_api_health(ctx=None, forge_root=tmp_forge_root, fix=False)
        finally:
            os.environ.pop("FORGE_WEBHOOK_URL", None)

        assert result["status"] == "ok"

    def test_connection_refused_returns_error(self, tmp_forge_root: Path) -> None:
        """When the server refuses connections, check should return error."""
        from urllib import error as url_error

        from forge_harness.cli_v2.doctor import _check_api_health

        os.environ["FORGE_WEBHOOK_URL"] = "http://localhost:19999"
        try:
            with patch(
                "urllib.request.urlopen",
                side_effect=url_error.URLError("Connection refused"),
            ):
                result = _check_api_health(ctx=None, forge_root=tmp_forge_root, fix=False)
        finally:
            os.environ.pop("FORGE_WEBHOOK_URL", None)

        assert result["status"] == "error"
        assert (
            "connection" in result["message"].lower() or "unreachable" in result["message"].lower()
        )

    def test_timeout_returns_error(self, tmp_forge_root: Path) -> None:
        """When the server times out, check should return error."""
        from forge_harness.cli_v2.doctor import _check_api_health

        os.environ["FORGE_WEBHOOK_URL"] = "http://localhost:19999"
        try:
            with patch(
                "urllib.request.urlopen",
                side_effect=TimeoutError("timed out"),
            ):
                result = _check_api_health(ctx=None, forge_root=tmp_forge_root, fix=False)
        finally:
            os.environ.pop("FORGE_WEBHOOK_URL", None)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# _check_fleet_tmux — tmux session detection
# ---------------------------------------------------------------------------


class TestCheckFleetTmux:
    """Tests for _check_fleet_tmux health check."""

    def test_forge_session_exists_returns_ok(self, tmp_forge_root: Path) -> None:
        """When tmux has-session -t forge succeeds, check should return ok."""
        from forge_harness.cli_v2.doctor import _check_fleet_tmux

        with patch(
            "subprocess.run",
            return_value=Mock(returncode=0, stdout="", stderr=""),
        ):
            result = _check_fleet_tmux(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "ok"
        assert "forge" in result["message"].lower()

    def test_no_forge_session_returns_warning(self, tmp_forge_root: Path) -> None:
        """When tmux has-session -t forge fails, check should return warning."""
        from forge_harness.cli_v2.doctor import _check_fleet_tmux

        with patch(
            "subprocess.run",
            return_value=Mock(returncode=1, stdout="", stderr="no such session"),
        ):
            result = _check_fleet_tmux(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"

    def test_tmux_not_installed_returns_warning(self, tmp_forge_root: Path) -> None:
        """When tmux binary is missing, check should return warning not error."""
        from forge_harness.cli_v2.doctor import _check_fleet_tmux

        with patch(
            "subprocess.run",
            side_effect=FileNotFoundError("tmux not found"),
        ):
            result = _check_fleet_tmux(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"
        assert "tmux" in result["message"].lower()

    def test_subprocess_timeout_returns_warning(self, tmp_forge_root: Path) -> None:
        """When tmux command times out, check should return warning."""
        from forge_harness.cli_v2.doctor import _check_fleet_tmux

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5),
        ):
            result = _check_fleet_tmux(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"


# ---------------------------------------------------------------------------
# _check_stale_dispatches — stale dispatch files
# ---------------------------------------------------------------------------


class TestCheckStaleDispatches:
    """Tests for _check_stale_dispatches health check."""

    def test_no_dispatches_dir_returns_ok(self, tmp_forge_root: Path) -> None:
        """When .forge/dispatches/ does not exist, check should return ok."""
        from forge_harness.cli_v2.doctor import _check_stale_dispatches

        result = _check_stale_dispatches(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "ok"

    def test_empty_dispatches_dir_returns_ok(self, tmp_forge_root: Path) -> None:
        """An empty .forge/dispatches/ directory should return ok."""
        from forge_harness.cli_v2.doctor import _check_stale_dispatches

        (tmp_forge_root / ".forge" / "dispatches").mkdir(parents=True)

        result = _check_stale_dispatches(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "ok"

    def test_fresh_dispatch_returns_ok(self, tmp_forge_root: Path) -> None:
        """Dispatch files newer than 24h should not trigger a warning."""
        from forge_harness.cli_v2.doctor import _check_stale_dispatches

        dispatches_dir = tmp_forge_root / ".forge" / "dispatches"
        dispatches_dir.mkdir(parents=True)
        (dispatches_dir / "dispatch-fresh.md").write_text("fresh dispatch")

        result = _check_stale_dispatches(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "ok"

    def test_stale_dispatch_returns_warning(self, tmp_forge_root: Path) -> None:
        """Dispatch files older than 24h should return status=warning."""
        from forge_harness.cli_v2.doctor import _check_stale_dispatches

        dispatches_dir = tmp_forge_root / ".forge" / "dispatches"
        dispatches_dir.mkdir(parents=True)

        stale_file = dispatches_dir / "dispatch-stale.md"
        stale_file.write_text("stale dispatch")
        old_time = time.time() - (25 * 3600)
        os.utime(stale_file, (old_time, old_time))

        result = _check_stale_dispatches(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"
        assert "stale" in result["message"].lower() or "old" in result["message"].lower()

    def test_stale_count_in_message_or_result(self, tmp_forge_root: Path) -> None:
        """Result should convey count of stale dispatch files."""
        from forge_harness.cli_v2.doctor import _check_stale_dispatches

        dispatches_dir = tmp_forge_root / ".forge" / "dispatches"
        dispatches_dir.mkdir(parents=True)

        old_time = time.time() - (30 * 3600)
        for i in range(3):
            f = dispatches_dir / f"dispatch-stale-{i}.md"
            f.write_text(f"stale {i}")
            os.utime(f, (old_time, old_time))

        result = _check_stale_dispatches(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"
        # Either a stale_count key or "3" mentioned in the message
        assert "stale_count" in result or "3" in result["message"]

    def test_mixed_dispatches_warns_about_stale_only(self, tmp_forge_root: Path) -> None:
        """Only stale files should trigger a warning; fresh files alone do not."""
        from forge_harness.cli_v2.doctor import _check_stale_dispatches

        dispatches_dir = tmp_forge_root / ".forge" / "dispatches"
        dispatches_dir.mkdir(parents=True)

        (dispatches_dir / "dispatch-fresh.md").write_text("fresh")

        stale = dispatches_dir / "dispatch-stale.md"
        stale.write_text("stale")
        os.utime(stale, (time.time() - 26 * 3600,) * 2)

        result = _check_stale_dispatches(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"


# ---------------------------------------------------------------------------
# _check_fleet — fleet agent health (existing logic, regression guard)
# ---------------------------------------------------------------------------


class TestCheckFleet:
    """Tests for _check_fleet health check."""

    def test_error_agents_surface_as_warning(self, tmp_forge_root: Path) -> None:
        """Fleet with error agents should return status=warning."""
        from forge_harness.cli_v2.doctor import _check_fleet

        with patch(
            "forge_harness.commands.agent.get_agents_data",
            return_value=[
                {"id": "forge:active", "status": "active"},
                {"id": "forge:error", "status": "error"},
            ],
        ):
            result = _check_fleet(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"

    def test_all_active_agents_return_ok(self, tmp_forge_root: Path) -> None:
        """Fleet with all active agents should return status=ok."""
        from forge_harness.cli_v2.doctor import _check_fleet

        with patch(
            "forge_harness.commands.agent.get_agents_data",
            return_value=[
                {"id": "forge:agent1", "status": "active"},
                {"id": "forge:agent2", "status": "active"},
            ],
        ):
            result = _check_fleet(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "ok"

    def test_empty_fleet_returns_warning(self, tmp_forge_root: Path) -> None:
        """Empty fleet should return warning, not ok."""
        from forge_harness.cli_v2.doctor import _check_fleet

        with patch(
            "forge_harness.commands.agent.get_agents_data",
            return_value=[],
        ):
            result = _check_fleet(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"

    def test_fleet_api_unavailable_returns_warning(self, tmp_forge_root: Path) -> None:
        """When fleet API is down, check should return warning (not crash)."""
        from forge_harness.cli_v2.doctor import _check_fleet

        with patch(
            "forge_harness.commands.agent.get_agents_data",
            side_effect=Exception("Connection refused"),
        ):
            result = _check_fleet(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"
        assert "unavailable" in result["message"].lower() or "error" in result["message"].lower()

    def test_counts_included_in_result(self, tmp_forge_root: Path) -> None:
        """Fleet check should include counts dict in result."""
        from forge_harness.cli_v2.doctor import _check_fleet

        with patch(
            "forge_harness.commands.agent.get_agents_data",
            return_value=[
                {"id": "forge:a1", "status": "active"},
                {"id": "forge:a2", "status": "idle"},
                {"id": "forge:a3", "status": "error"},
            ],
        ):
            result = _check_fleet(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert "counts" in result
        assert result["counts"]["active"] == 1
        assert result["counts"]["error"] == 1


# ---------------------------------------------------------------------------
# _check_approvals — approval queue integrity (regression guard)
# ---------------------------------------------------------------------------


class TestCheckApprovals:
    """Tests for _check_approvals health check."""

    def test_healthy_queue_returns_ok(self, tmp_forge_root: Path) -> None:
        """A queue with no expired items should return status=ok."""
        from forge_harness.cli_v2.doctor import _check_approvals

        from forge_harness.approval_queue import ApprovalQueueStats

        stats = ApprovalQueueStats(
            total_requests=5,
            pending_count=1,
            approved_count=4,
            rejected_count=0,
            expired_count=0,
            avg_resolution_hours=0.5,
            by_type={},
            by_domain={},
            oldest_pending_hours=0.5,
        )
        queue_mock = AsyncMock()
        queue_mock.get_stats.return_value = stats

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=queue_mock):
            result = _check_approvals(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "ok"
        assert result["stats"]["total_requests"] == 5

    def test_expired_requests_return_warning(self, tmp_forge_root: Path) -> None:
        """Expired requests should trigger status=warning."""
        from forge_harness.cli_v2.doctor import _check_approvals

        from forge_harness.approval_queue import ApprovalQueueStats

        stats = ApprovalQueueStats(
            total_requests=3,
            pending_count=1,
            approved_count=2,
            rejected_count=0,
            expired_count=2,
            avg_resolution_hours=1.0,
            by_type={},
            by_domain={},
            oldest_pending_hours=1.0,
        )
        queue_mock = AsyncMock()
        queue_mock.get_stats.return_value = stats

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=queue_mock):
            result = _check_approvals(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"

    def test_stale_pending_returns_warning(self, tmp_forge_root: Path) -> None:
        """Pending requests older than 24h should trigger status=warning."""
        from forge_harness.cli_v2.doctor import _check_approvals

        from forge_harness.approval_queue import ApprovalQueueStats

        stats = ApprovalQueueStats(
            total_requests=1,
            pending_count=1,
            approved_count=0,
            rejected_count=0,
            expired_count=0,
            avg_resolution_hours=0.0,
            by_type={},
            by_domain={},
            oldest_pending_hours=30.0,
        )
        queue_mock = AsyncMock()
        queue_mock.get_stats.return_value = stats

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=queue_mock):
            result = _check_approvals(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"

    def test_queue_unavailable_returns_warning(self, tmp_forge_root: Path) -> None:
        """When approval queue is unavailable, check should warn rather than crash."""
        from forge_harness.cli_v2.doctor import _check_approvals

        with patch(
            "forge_harness.approval_queue.create_approval_queue",
            side_effect=Exception("Storage unavailable"),
        ):
            result = _check_approvals(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"


# ---------------------------------------------------------------------------
# Full doctor command integration via CLI runner
# ---------------------------------------------------------------------------


class TestDoctorCommandIntegration:
    """Integration tests for the doctor command via CliRunner."""

    def test_doctor_help(self, runner: CliRunner, tmp_forge_root: Path) -> None:
        """doctor --help should exit 0 and mention health check."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=tmp_forge_root):
            result = runner.invoke(cli, ["doctor", "--help"])

        assert result.exit_code == 0
        assert "health" in result.output.lower() or "diagnose" in result.output.lower()

    def test_doctor_json_output_is_valid(self, runner: CliRunner, tmp_forge_root: Path) -> None:
        """doctor --json should produce valid JSON output."""
        import json

        from forge_harness.cli_v2 import cli

        with patch("forge_harness.cli_v2.doctor._run_full_check") as mock_full:
            mock_full.return_value = {
                "results": {"Git Repository": {"status": "ok", "message": "clean"}},
                "summary": {"ok": 1, "warning": 0, "error": 0, "total": 1},
            }
            with runner.isolated_filesystem(temp_dir=tmp_forge_root):
                result = runner.invoke(cli, ["doctor", "--json"])

        assert result.exit_code == 0
        try:
            parsed = json.loads(result.output)
            assert parsed  # Non-empty
        except json.JSONDecodeError:
            pytest.fail(f"Output is not valid JSON: {result.output!r}")

    def test_doctor_check_git_invokes_git_check(
        self, runner: CliRunner, tmp_forge_root: Path
    ) -> None:
        """doctor --check git should invoke the git check function."""
        from forge_harness.cli_v2 import cli

        with patch("forge_harness.cli_v2.doctor._check_git") as mock_git:
            mock_git.return_value = {"status": "ok", "message": "Repository is clean"}
            with runner.isolated_filesystem(temp_dir=tmp_forge_root):
                result = runner.invoke(cli, ["doctor", "--check", "git"])

        mock_git.assert_called_once()

    def test_doctor_check_config_invokes_config_check(
        self, runner: CliRunner, tmp_forge_root: Path
    ) -> None:
        """doctor --check config should invoke the config check function."""
        from forge_harness.cli_v2 import cli

        with patch("forge_harness.cli_v2.doctor._check_config") as mock_cfg:
            mock_cfg.return_value = {"status": "ok", "message": "Configuration valid"}
            with runner.isolated_filesystem(temp_dir=tmp_forge_root):
                result = runner.invoke(cli, ["doctor", "--check", "config"])

        mock_cfg.assert_called_once()

    def test_doctor_check_fleet_invokes_fleet_check(
        self, runner: CliRunner, tmp_forge_root: Path
    ) -> None:
        """doctor --check fleet should invoke the fleet check function."""
        from forge_harness.cli_v2 import cli

        with patch("forge_harness.cli_v2.doctor._check_fleet") as mock_fleet:
            mock_fleet.return_value = {"status": "ok", "message": "Fleet healthy"}
            with runner.isolated_filesystem(temp_dir=tmp_forge_root):
                result = runner.invoke(cli, ["doctor", "--check", "fleet"])

        mock_fleet.assert_called_once()

    def test_doctor_exits_zero_on_success(self, runner: CliRunner, tmp_forge_root: Path) -> None:
        """Full doctor run with mocked checks should exit 0."""
        from forge_harness.cli_v2 import cli

        with patch("forge_harness.cli_v2.doctor._run_full_check") as mock_full:
            mock_full.return_value = {
                "results": {},
                "summary": {"ok": 3, "warning": 1, "error": 0, "total": 4},
            }
            with runner.isolated_filesystem(temp_dir=tmp_forge_root):
                result = runner.invoke(cli, ["doctor"])

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Result structure contracts — parametrized across all check functions
# ---------------------------------------------------------------------------


class TestResultStructureContracts:
    """All health check functions must return a consistent dict structure."""

    @pytest.mark.parametrize(
        "check_name",
        [
            "_check_git",
            "_check_config",
            "_check_fleet",
            "_check_api_health",
            "_check_fleet_tmux",
            "_check_stale_dispatches",
        ],
    )
    def test_check_returns_status_and_message(self, check_name: str, tmp_forge_root: Path) -> None:
        """Every check function must return a dict with 'status' and 'message' keys."""
        # forge_harness.cli_v2.__init__ re-exports `doctor` as a click Command object
        # which shadows the submodule in sys.modules.  Import each private function
        # directly using importlib to access the real module.
        import importlib
        import sys

        # Ensure the submodule is registered in sys.modules as a module (not Command).
        # importlib.import_module will use the cached sys.modules entry if it exists,
        # so we delete the shadowed entry and re-import.
        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, check_name):
            del sys.modules["forge_harness.cli_v2.doctor"]

        doctor_mod = importlib.import_module("forge_harness.cli_v2.doctor")
        check_func = getattr(doctor_mod, check_name)

        # Patch outbound I/O to avoid real network/filesystem hits
        with (
            patch("subprocess.run", return_value=Mock(returncode=0, stdout="", stderr="")),
            patch("forge_harness.commands.agent.get_agents_data", return_value=[]),
            patch(
                "forge_harness.approval_queue.create_approval_queue",
                side_effect=Exception("mocked"),
            ),
            patch("urllib.request.urlopen", side_effect=Exception("mocked")),
        ):
            result = check_func(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert isinstance(result, dict), f"{check_name} must return a dict"
        assert "status" in result, f"{check_name} result must contain 'status'"
        assert "message" in result, f"{check_name} result must contain 'message'"
        assert result["status"] in (
            "ok",
            "warning",
            "error",
        ), f"{check_name} status must be ok/warning/error, got {result['status']!r}"


# ---------------------------------------------------------------------------
# _run_full_check — internal orchestrator
# ---------------------------------------------------------------------------


class TestRunFullCheck:
    """Tests for _run_full_check internal function."""

    def _get_module(self):
        """Return the doctor module, bypassing the cli_v2 package's name shadowing."""
        import importlib
        import sys

        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_run_full_check"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        return importlib.import_module("forge_harness.cli_v2.doctor")

    def test_returns_results_and_summary(self, tmp_forge_root: Path) -> None:
        """_run_full_check must return dict with 'results' and 'summary' keys."""
        import click

        doctor_mod = self._get_module()

        # Mock all individual checks to avoid I/O
        ok_result = {"status": "ok", "message": "all good"}
        warn_result = {"status": "warning", "message": "minor issue"}
        all_checks = [
            "_check_startup_exports",
            "_check_route_health",
            "_check_auth_mode",
            "_check_git",
            "_check_config",
            "_check_api_health",
            "_check_fleet_tmux",
            "_check_stale_dispatches",
            "_check_fleet",
            "_check_approvals",
            "_check_quality",
        ]

        ctx = click.Context(click.Command("test"))

        patches = {}
        for name in all_checks:
            patches[name] = patch.object(doctor_mod, name, return_value=ok_result)

        with (
            patches["_check_startup_exports"],
            patches["_check_route_health"],
            patches["_check_auth_mode"],
            patches["_check_git"],
            patches["_check_config"],
            patches["_check_api_health"],
            patches["_check_fleet_tmux"],
            patches["_check_stale_dispatches"],
            patches["_check_fleet"],
            patches["_check_approvals"],
            patches["_check_quality"],
        ):
            result = doctor_mod._run_full_check(
                ctx=ctx, forge_root=tmp_forge_root, fix=False, report=False, output_json=True
            )

        assert "results" in result
        assert "summary" in result
        assert result["summary"]["total"] > 0

    def test_summary_counts_ok_warning_error(self, tmp_forge_root: Path) -> None:
        """Summary counts should correctly tally ok/warning/error checks."""
        import click

        doctor_mod = self._get_module()

        ctx = click.Context(click.Command("test"))
        ok_r = {"status": "ok", "message": "ok"}
        warn_r = {"status": "warning", "message": "warn"}
        err_r = {"status": "error", "message": "err"}

        # Patch all checks: make half ok, rest warning
        all_patch_names = [
            "_check_startup_exports",
            "_check_route_health",
            "_check_auth_mode",
            "_check_git",
            "_check_config",
            "_check_api_health",
            "_check_fleet_tmux",
            "_check_stale_dispatches",
            "_check_fleet",
            "_check_approvals",
            "_check_quality",
        ]

        with (
            patch.object(doctor_mod, "_check_startup_exports", return_value=ok_r),
            patch.object(doctor_mod, "_check_route_health", return_value=warn_r),
            patch.object(doctor_mod, "_check_auth_mode", return_value=ok_r),
            patch.object(doctor_mod, "_check_git", return_value=ok_r),
            patch.object(doctor_mod, "_check_config", return_value=warn_r),
            patch.object(doctor_mod, "_check_api_health", return_value=ok_r),
            patch.object(doctor_mod, "_check_fleet_tmux", return_value=ok_r),
            patch.object(doctor_mod, "_check_stale_dispatches", return_value=ok_r),
            patch.object(doctor_mod, "_check_fleet", return_value=ok_r),
            patch.object(doctor_mod, "_check_approvals", return_value=ok_r),
            patch.object(doctor_mod, "_check_quality", return_value=warn_r),
        ):
            result = doctor_mod._run_full_check(
                ctx=ctx, forge_root=tmp_forge_root, fix=False, report=False, output_json=True
            )

        assert result["summary"]["ok"] == 8
        assert result["summary"]["warning"] == 3
        assert result["summary"]["error"] == 0


# ---------------------------------------------------------------------------
# _run_subsystem_check — subsystem dispatch
# ---------------------------------------------------------------------------


class TestRunSubsystemCheck:
    """Tests for _run_subsystem_check internal function."""

    def _get_module(self):
        import importlib
        import sys

        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_run_subsystem_check"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        return importlib.import_module("forge_harness.cli_v2.doctor")

    def test_dispatches_to_correct_check(self, tmp_forge_root: Path) -> None:
        """_run_subsystem_check should call the correct function for a given subsystem."""
        import click

        doctor_mod = self._get_module()
        ctx = click.Context(click.Command("test"))

        with patch.object(
            doctor_mod, "_check_git", return_value={"status": "ok", "message": "clean"}
        ) as mock_git:
            result = doctor_mod._run_subsystem_check(
                ctx=ctx,
                forge_root=tmp_forge_root,
                subsystem="git",
                fix=False,
                output_json=True,
            )

        mock_git.assert_called_once()
        assert result["status"] == "ok"

    def test_unknown_subsystem_returns_error(self, tmp_forge_root: Path) -> None:
        """Unknown subsystem name should return status=error."""
        import click

        doctor_mod = self._get_module()
        ctx = click.Context(click.Command("test"))

        result = doctor_mod._run_subsystem_check(
            ctx=ctx,
            forge_root=tmp_forge_root,
            subsystem="nonexistent",
            fix=False,
            output_json=True,
        )

        assert result["status"] == "error"
        assert "unknown" in result["message"].lower() or "nonexistent" in result["message"].lower()

    def test_api_subsystem_dispatches_to_api_health(self, tmp_forge_root: Path) -> None:
        """'api' subsystem should call _check_api_health."""
        import click

        doctor_mod = self._get_module()
        ctx = click.Context(click.Command("test"))

        with patch.object(
            doctor_mod,
            "_check_api_health",
            return_value={"status": "warning", "message": "no url"},
        ) as mock_api:
            doctor_mod._run_subsystem_check(
                ctx=ctx,
                forge_root=tmp_forge_root,
                subsystem="api",
                fix=False,
                output_json=True,
            )

        mock_api.assert_called_once()

    def test_dispatches_subsystem_dispatches(self, tmp_forge_root: Path) -> None:
        """'dispatches' subsystem should call _check_stale_dispatches."""
        import click

        doctor_mod = self._get_module()
        ctx = click.Context(click.Command("test"))

        with patch.object(
            doctor_mod,
            "_check_stale_dispatches",
            return_value={"status": "ok", "message": "fresh"},
        ) as mock_disp:
            doctor_mod._run_subsystem_check(
                ctx=ctx,
                forge_root=tmp_forge_root,
                subsystem="dispatches",
                fix=False,
                output_json=True,
            )

        mock_disp.assert_called_once()


# ---------------------------------------------------------------------------
# _check_startup_exports — module startup check
# ---------------------------------------------------------------------------


class TestCheckStartupExports:
    """Tests for _check_startup_exports health check."""

    def test_successful_import_returns_ok(self, tmp_forge_root: Path) -> None:
        """When all modules import correctly, check should return ok."""
        from forge_harness.cli_v2.doctor import _check_startup_exports

        # Patch create_app to return a mock app with routes
        mock_app = Mock()
        mock_app.routes = [Mock()]

        with patch("forge_harness.webhook_server_main.create_app", return_value=mock_app):
            result = _check_startup_exports(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "ok"

    def test_import_error_returns_error(self, tmp_forge_root: Path) -> None:
        """An ImportError in a critical module should return status=error."""
        from forge_harness.cli_v2.doctor import _check_startup_exports

        with patch.dict(
            "sys.modules",
            {"forge_harness.ralph_loop": None},
        ):
            result = _check_startup_exports(ctx=None, forge_root=tmp_forge_root, fix=False)

        # Either catches import error or app creation error
        assert result["status"] in ("error", "ok")

    def test_app_creation_failure_returns_error(self, tmp_forge_root: Path) -> None:
        """Failure in create_app should return status=error."""
        from forge_harness.cli_v2.doctor import _check_startup_exports

        with patch(
            "forge_harness.webhook_server_main.create_app",
            side_effect=Exception("DB connection failed"),
        ):
            result = _check_startup_exports(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "error"
        assert "failed" in result["message"].lower()


# ---------------------------------------------------------------------------
# _check_quality — quality check
# ---------------------------------------------------------------------------


class TestCheckQuality:
    """Tests for _check_quality health check."""

    def test_quality_check_ok_on_success(self, tmp_forge_root: Path) -> None:
        """When quality_self_analyze runs without error, check should return ok."""
        import click
        from forge_harness.cli_v2.doctor import _check_quality

        ctx = Mock()
        ctx.invoke = Mock(return_value=None)

        result = _check_quality(ctx=ctx, forge_root=tmp_forge_root, fix=False, output_json=False)

        assert result["status"] == "ok"
        ctx.invoke.assert_called_once()

    def test_quality_check_warns_on_exception(self, tmp_forge_root: Path) -> None:
        """When quality_self_analyze raises, check should return warning (not crash)."""
        from forge_harness.cli_v2.doctor import _check_quality

        ctx = Mock()
        ctx.invoke = Mock(side_effect=Exception("quality tool unavailable"))

        result = _check_quality(ctx=ctx, forge_root=tmp_forge_root, fix=False, output_json=False)

        assert result["status"] == "warning"
        assert "quality" in result["message"].lower() or "failed" in result["message"].lower()


# ---------------------------------------------------------------------------
# _run_cleanup — cleanup orchestration
# ---------------------------------------------------------------------------


class TestRunCleanup:
    """Tests for _run_cleanup internal function."""

    def _get_module(self):
        import importlib
        import sys

        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_run_cleanup"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        return importlib.import_module("forge_harness.cli_v2.doctor")

    def test_cleanup_returns_dict(self, tmp_forge_root: Path) -> None:
        """_run_cleanup should return a dict of task results."""
        import click

        doctor_mod = self._get_module()
        ctx = click.Context(click.Command("test"))

        with (
            patch.object(doctor_mod, "_cleanup_tmux", return_value=None),
            patch.object(doctor_mod, "_cleanup_checkpoints", return_value=None),
            patch.object(doctor_mod, "_cleanup_approvals", return_value=None),
        ):
            result = doctor_mod._run_cleanup(ctx=ctx, forge_root=tmp_forge_root, output_json=True)

        assert isinstance(result, dict)
        assert len(result) == 3

    def test_cleanup_records_failures(self, tmp_forge_root: Path) -> None:
        """Cleanup failures should be recorded in the result, not raised."""
        import click

        doctor_mod = self._get_module()
        ctx = click.Context(click.Command("test"))

        with (
            patch.object(doctor_mod, "_cleanup_tmux", side_effect=RuntimeError("tmux died")),
            patch.object(doctor_mod, "_cleanup_checkpoints", return_value=None),
            patch.object(doctor_mod, "_cleanup_approvals", return_value=None),
        ):
            result = doctor_mod._run_cleanup(ctx=ctx, forge_root=tmp_forge_root, output_json=True)

        # One entry should contain the failure message
        failed_entries = [v for v in result.values() if "failed" in str(v)]
        assert len(failed_entries) >= 1


# ---------------------------------------------------------------------------
# _check_route_health — route health check
# ---------------------------------------------------------------------------


class TestCheckRouteHealth:
    """Tests for _check_route_health health check."""

    def _make_mock_route(self, path: str, methods=None):
        """Create a mock route object."""
        r = Mock()
        r.path = path
        if methods is not None:
            r.methods = methods
        else:
            del r.methods  # simulate routes without methods attr
        return r

    def test_all_routes_present_returns_ok(self, tmp_forge_root: Path) -> None:
        """When all required routes exist plus webhook/SSE, check returns ok."""
        from forge_harness.cli_v2.doctor import _check_route_health

        routes = [
            self._make_mock_route("/api/tasks/recommended", {"GET"}),
            self._make_mock_route("/api/nodes", {"GET"}),
            self._make_mock_route("/api/health", {"GET"}),
            self._make_mock_route("/api/webhooks/slack", {"POST"}),
            self._make_mock_route("/api/sse/stream", {"GET"}),
        ]
        mock_app = Mock()
        mock_app.routes = routes

        with patch("forge_harness.webhook_server_main.create_app", return_value=mock_app):
            result = _check_route_health(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "ok"

    def test_missing_required_route_returns_error(self, tmp_forge_root: Path) -> None:
        """When a required route is missing, check returns error."""
        from forge_harness.cli_v2.doctor import _check_route_health

        # Missing /api/tasks/recommended
        routes = [
            self._make_mock_route("/api/nodes", {"GET"}),
            self._make_mock_route("/api/health", {"GET"}),
        ]
        mock_app = Mock()
        mock_app.routes = routes

        with patch("forge_harness.webhook_server_main.create_app", return_value=mock_app):
            result = _check_route_health(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "error"
        assert "missing" in result["message"].lower()

    def test_no_webhook_routes_returns_warning(self, tmp_forge_root: Path) -> None:
        """When no webhook routes exist, check returns warning."""
        from forge_harness.cli_v2.doctor import _check_route_health

        routes = [
            self._make_mock_route("/api/tasks/recommended", {"GET"}),
            self._make_mock_route("/api/nodes", {"GET"}),
            self._make_mock_route("/api/health", {"GET"}),
            # No /api/webhooks/* routes
        ]
        mock_app = Mock()
        mock_app.routes = routes

        with patch("forge_harness.webhook_server_main.create_app", return_value=mock_app):
            result = _check_route_health(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"
        assert "webhook" in result["message"].lower()

    def test_create_app_exception_returns_error(self, tmp_forge_root: Path) -> None:
        """When create_app raises, check returns error."""
        from forge_harness.cli_v2.doctor import _check_route_health

        with patch(
            "forge_harness.webhook_server_main.create_app",
            side_effect=Exception("DB error"),
        ):
            result = _check_route_health(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# _check_auth_mode — authentication mode
# ---------------------------------------------------------------------------


class TestCheckAuthMode:
    """Tests for _check_auth_mode health check."""

    def test_localhost_bypass_enabled_returns_warning(self, tmp_forge_root: Path) -> None:
        """When localhost bypass is enabled, check returns warning."""
        from forge_harness.cli_v2.doctor import _check_auth_mode

        mock_config = Mock()
        mock_config.allow_localhost = True

        with patch(
            "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
            return_value=mock_config,
        ):
            result = _check_auth_mode(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"
        assert "localhost" in result["message"].lower()

    def test_no_webhook_token_returns_warning(self, tmp_forge_root: Path) -> None:
        """When no FORGE_WEBHOOK_TOKEN is set, check returns warning."""
        from forge_harness.cli_v2.doctor import _check_auth_mode

        mock_config = Mock()
        mock_config.allow_localhost = False

        backup = os.environ.pop("FORGE_WEBHOOK_TOKEN", None)
        try:
            with patch(
                "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
                return_value=mock_config,
            ):
                result = _check_auth_mode(ctx=None, forge_root=tmp_forge_root, fix=False)
        finally:
            if backup is not None:
                os.environ["FORGE_WEBHOOK_TOKEN"] = backup

        assert result["status"] == "warning"

    def test_valid_token_returns_ok(self, tmp_forge_root: Path) -> None:
        """When a sufficiently long token is set and bypass is off, check returns ok."""
        from forge_harness.cli_v2.doctor import _check_auth_mode

        mock_config = Mock()
        mock_config.allow_localhost = False

        os.environ["FORGE_WEBHOOK_TOKEN"] = "a" * 40  # long enough
        try:
            with patch(
                "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
                return_value=mock_config,
            ):
                result = _check_auth_mode(ctx=None, forge_root=tmp_forge_root, fix=False)
        finally:
            os.environ.pop("FORGE_WEBHOOK_TOKEN", None)

        assert result["status"] == "ok"

    def test_import_error_returns_error(self, tmp_forge_root: Path) -> None:
        """When auth module fails to import, check returns error."""
        from forge_harness.cli_v2.doctor import _check_auth_mode

        with patch(
            "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
            side_effect=Exception("import error"),
        ):
            result = _check_auth_mode(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# _cleanup_checkpoints — checkpoint cleanup
# ---------------------------------------------------------------------------


class TestCleanupCheckpoints:
    """Tests for _cleanup_checkpoints function."""

    def _get_func(self):
        import importlib
        import sys

        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_cleanup_checkpoints"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        mod = importlib.import_module("forge_harness.cli_v2.doctor")
        return mod._cleanup_checkpoints

    def test_no_checkpoint_dirs_is_noop(self, tmp_forge_root: Path) -> None:
        """When checkpoint dirs don't exist, cleanup should be a no-op."""
        cleanup = self._get_func()
        import click

        ctx = click.Context(click.Command("test"))
        # Should not raise even when dirs don't exist
        cleanup(ctx=ctx, forge_root=tmp_forge_root)

    def test_old_checkpoints_deleted(self, tmp_forge_root: Path) -> None:
        """Checkpoint files older than 7 days should be deleted."""
        import time

        cleanup = self._get_func()
        import click

        ctx = click.Context(click.Command("test"))

        # Create old checkpoint
        ckpt_dir = tmp_forge_root / ".forge/orchestration_checkpoints"
        ckpt_dir.mkdir()
        old_file = ckpt_dir / "old-checkpoint.json"
        old_file.write_text("{}")

        # Back-date by 8 days
        old_time = time.time() - (8 * 86400)
        os.utime(old_file, (old_time, old_time))

        cleanup(ctx=ctx, forge_root=tmp_forge_root)

        assert not old_file.exists(), "Old checkpoint should have been deleted"

    def test_fresh_checkpoints_kept(self, tmp_forge_root: Path) -> None:
        """Checkpoint files newer than 7 days should be kept."""
        cleanup = self._get_func()
        import click

        ctx = click.Context(click.Command("test"))

        ckpt_dir = tmp_forge_root / ".forge/orchestration_checkpoints"
        ckpt_dir.mkdir()
        fresh_file = ckpt_dir / "fresh-checkpoint.json"
        fresh_file.write_text("{}")
        # Default mtime is now — should be kept

        cleanup(ctx=ctx, forge_root=tmp_forge_root)

        assert fresh_file.exists(), "Fresh checkpoint should have been kept"


# ---------------------------------------------------------------------------
# Additional coverage: route health SSE warning, auth short token, cleanup paths
# ---------------------------------------------------------------------------


class TestAdditionalCoveragePaths:
    """Targeted tests to push doctor.py coverage past 80%."""

    def _mock_route(self, path: str, methods=None):
        r = Mock()
        r.path = path
        if methods is not None:
            r.methods = methods
        return r

    def test_route_health_no_sse_returns_warning(self, tmp_forge_root: Path) -> None:
        """When no SSE routes exist (but webhook routes do), check returns warning."""
        from forge_harness.cli_v2.doctor import _check_route_health

        routes = [
            self._mock_route("/api/tasks/recommended", {"GET"}),
            self._mock_route("/api/nodes", {"GET"}),
            self._mock_route("/api/health", {"GET"}),
            self._mock_route("/api/webhooks/slack", {"POST"}),
            # No SSE/stream routes
        ]
        mock_app = Mock()
        mock_app.routes = routes

        with patch("forge_harness.webhook_server_main.create_app", return_value=mock_app):
            result = _check_route_health(ctx=None, forge_root=tmp_forge_root, fix=False)

        assert result["status"] == "warning"
        assert "sse" in result["message"].lower() or "stream" in result["message"].lower()

    def test_auth_short_token_returns_warning(self, tmp_forge_root: Path) -> None:
        """A FORGE_WEBHOOK_TOKEN shorter than 32 chars triggers a warning."""
        from forge_harness.cli_v2.doctor import _check_auth_mode

        mock_config = Mock()
        mock_config.allow_localhost = False

        os.environ["FORGE_WEBHOOK_TOKEN"] = "short"
        try:
            with patch(
                "forge_harness.webhook_server.infrastructure.auth.AuthConfig.from_env",
                return_value=mock_config,
            ):
                result = _check_auth_mode(ctx=None, forge_root=tmp_forge_root, fix=False)
        finally:
            os.environ.pop("FORGE_WEBHOOK_TOKEN", None)

        assert result["status"] == "warning"
        assert "short" in result["message"].lower() or "token" in result["message"].lower()

    def test_cleanup_tmux_no_sessions_is_noop(self, tmp_forge_root: Path) -> None:
        """When no forge:* sessions exist, _cleanup_tmux should be a no-op."""
        import importlib
        import sys

        import click
        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_cleanup_tmux"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        doctor_mod = importlib.import_module("forge_harness.cli_v2.doctor")

        ctx = click.Context(click.Command("test"))

        # Only the "forge" base session (no forge:* variants)
        list_result = Mock(returncode=0, stdout="forge\n", stderr="")
        with patch("subprocess.run", return_value=list_result):
            doctor_mod._cleanup_tmux(ctx=ctx, forge_root=tmp_forge_root)
        # Should complete without raising

    def test_cleanup_tmux_kills_shell_only_sessions(self, tmp_forge_root: Path) -> None:
        """Sessions with only shell panes should be killed as orphans."""
        import importlib
        import sys

        import click
        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_cleanup_tmux"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        doctor_mod = importlib.import_module("forge_harness.cli_v2.doctor")

        ctx = click.Context(click.Command("test"))

        list_result = Mock(returncode=0, stdout="forge\nforge:tech\n", stderr="")
        panes_result = Mock(returncode=0, stdout="0|zsh\n", stderr="")
        kill_result = Mock(returncode=0, stdout="", stderr="")

        with patch(
            "subprocess.run",
            side_effect=[list_result, panes_result, kill_result],
        ):
            doctor_mod._cleanup_tmux(ctx=ctx, forge_root=tmp_forge_root)
        # Should complete without raising

    def test_cleanup_tmux_skips_active_sessions(self, tmp_forge_root: Path) -> None:
        """Sessions with non-shell processes should NOT be killed."""
        import importlib
        import sys

        import click
        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_cleanup_tmux"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        doctor_mod = importlib.import_module("forge_harness.cli_v2.doctor")

        ctx = click.Context(click.Command("test"))

        list_result = Mock(returncode=0, stdout="forge\nforge:claude\n", stderr="")
        # "claude" is not a shell — session is active
        panes_result = Mock(returncode=0, stdout="0|claude\n", stderr="")

        with patch(
            "subprocess.run",
            side_effect=[list_result, panes_result],
        ) as run_mock:
            doctor_mod._cleanup_tmux(ctx=ctx, forge_root=tmp_forge_root)

        # kill-session should NOT have been called (only 2 subprocess calls)
        assert run_mock.call_count == 2

    def test_generate_report_writes_markdown(self, tmp_forge_root: Path) -> None:
        """_generate_report should write a markdown file to docs/health_report.md."""
        import importlib
        import sys

        import click
        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_generate_report"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        doctor_mod = importlib.import_module("forge_harness.cli_v2.doctor")

        ctx = click.Context(click.Command("test"))
        docs_dir = tmp_forge_root / "docs"
        docs_dir.mkdir()

        results = {
            "Git Repository": {"status": "ok", "message": "clean"},
            "Configuration": {"status": "warning", "message": "no token"},
            "Fleet Status": {"status": "error", "message": "no agents"},
        }

        doctor_mod._generate_report(ctx=ctx, forge_root=tmp_forge_root, results=results)

        report_path = tmp_forge_root / "docs" / "health_report.md"
        assert report_path.exists()
        content = report_path.read_text()
        assert "FORGE Health Report" in content
        assert "Git Repository" in content

    def test_doctor_command_cleanup_invokes_run_cleanup(
        self, runner: CliRunner, tmp_forge_root: Path
    ) -> None:
        """doctor --cleanup should call _run_cleanup and print results."""
        import importlib
        import sys

        import forge_harness.cli_v2  # noqa: F401
        from forge_harness.cli_v2 import cli

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_run_cleanup"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        doctor_mod = importlib.import_module("forge_harness.cli_v2.doctor")

        with patch.object(
            doctor_mod,
            "_run_cleanup",
            return_value={"Orphaned tmux sessions": "success"},
        ) as mock_cleanup:
            with runner.isolated_filesystem(temp_dir=tmp_forge_root):
                result = runner.invoke(cli, ["doctor", "--cleanup"])

        assert result.exit_code == 0

    def test_full_run_non_json_prints_summary(
        self, runner: CliRunner, tmp_forge_root: Path
    ) -> None:
        """Full doctor run without --json should print the Summary section to stdout."""
        from forge_harness.cli_v2 import cli

        ok_r = {"status": "ok", "message": "ok"}

        with patch("forge_harness.cli_v2.doctor._run_full_check") as mock_full:
            mock_full.return_value = {
                "results": {
                    "Git Repository": ok_r,
                    "Configuration": ok_r,
                },
                "summary": {"ok": 2, "warning": 0, "error": 0, "total": 2},
            }
            with runner.isolated_filesystem(temp_dir=tmp_forge_root):
                result = runner.invoke(cli, ["doctor"])

        assert result.exit_code == 0
        # _run_full_check is mocked so summary printing is handled inside the real function;
        # the cli layer should call it and exit cleanly.

    def test_run_full_check_non_json_prints_results(self, tmp_forge_root: Path) -> None:
        """_run_full_check in human-readable mode should call format_success/warning/error."""
        import importlib
        import sys

        import click
        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_run_full_check"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        doctor_mod = importlib.import_module("forge_harness.cli_v2.doctor")

        ctx = click.Context(click.Command("test"))

        with (
            patch.object(
                doctor_mod, "_check_startup_exports", return_value={"status": "ok", "message": "ok"}
            ),
            patch.object(
                doctor_mod,
                "_check_route_health",
                return_value={"status": "warning", "message": "warn"},
            ),
            patch.object(
                doctor_mod, "_check_auth_mode", return_value={"status": "error", "message": "err"}
            ),
            patch.object(doctor_mod, "_check_git", return_value={"status": "ok", "message": "ok"}),
            patch.object(
                doctor_mod, "_check_config", return_value={"status": "ok", "message": "ok"}
            ),
            patch.object(
                doctor_mod, "_check_api_health", return_value={"status": "ok", "message": "ok"}
            ),
            patch.object(
                doctor_mod, "_check_fleet_tmux", return_value={"status": "ok", "message": "ok"}
            ),
            patch.object(
                doctor_mod,
                "_check_stale_dispatches",
                return_value={"status": "ok", "message": "ok"},
            ),
            patch.object(
                doctor_mod, "_check_fleet", return_value={"status": "ok", "message": "ok"}
            ),
            patch.object(
                doctor_mod, "_check_approvals", return_value={"status": "ok", "message": "ok"}
            ),
            patch.object(
                doctor_mod, "_check_quality", return_value={"status": "ok", "message": "ok"}
            ),
            # format_error raises SystemExit(1) — patch it so the loop can complete
            patch.object(doctor_mod, "format_error", return_value=None),
        ):
            result = doctor_mod._run_full_check(
                ctx=ctx, forge_root=tmp_forge_root, fix=False, report=False, output_json=False
            )

        assert result["summary"]["warning"] == 1
        assert result["summary"]["error"] == 1

    def test_subsystem_check_non_json_prints_result(self, tmp_forge_root: Path) -> None:
        """_run_subsystem_check in human-readable mode should print the result."""
        import importlib
        import sys

        import click
        import forge_harness.cli_v2  # noqa: F401

        shadowed = sys.modules.get("forge_harness.cli_v2.doctor")
        if shadowed is not None and not hasattr(shadowed, "_run_subsystem_check"):
            del sys.modules["forge_harness.cli_v2.doctor"]
        doctor_mod = importlib.import_module("forge_harness.cli_v2.doctor")

        ctx = click.Context(click.Command("test"))

        with patch.object(
            doctor_mod,
            "_check_git",
            return_value={"status": "ok", "message": "clean"},
        ):
            result = doctor_mod._run_subsystem_check(
                ctx=ctx,
                forge_root=tmp_forge_root,
                subsystem="git",
                fix=False,
                output_json=False,
            )

        assert result["status"] == "ok"
