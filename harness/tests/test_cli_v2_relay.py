"""Tests for forge CLI v2 relay commands."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# forge_harness.cli_v2.__init__ re-exports the Click Group named 'relay' as a
# package-level attribute, so `import forge_harness.cli_v2.relay as X` gives
# us the Group object, not the module.  The workaround is to trigger the
# module import first and then retrieve the actual module from sys.modules.
import forge_harness.cli_v2  # noqa: F401 - ensure package is loaded
import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.relay import relay  # the Click Group

_relay_module = sys.modules["forge_harness.cli_v2.relay"]

# run_relay_worker and RelayWorker are imported lazily inside command
# functions via `from forge_harness.relay_worker import ...`.  Patch them
# at the source module so the local import picks up the mock.
_RUN_RELAY_WORKER = "forge_harness.relay_worker.run_relay_worker"
_RELAY_WORKER_CLS = "forge_harness.relay_worker.RelayWorker"


runner = CliRunner()


@pytest.fixture
def mock_forge_root(tmp_path):
    """Create a mock forge root directory."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    (forge_root / ".forge-root").touch()
    return forge_root


@pytest.fixture
def clean_pid_file(tmp_path, monkeypatch):
    """Ensure the relay PID file path is isolated per test.

    Patches RELAY_PID_FILE on the real module object so that the running
    CLI commands read from the isolated temp path rather than /tmp.
    """
    pid_path = tmp_path / "forge-relay-worker.pid"
    monkeypatch.setattr(_relay_module, "RELAY_PID_FILE", pid_path)
    return pid_path


# ---------------------------------------------------------------------------
# relay --help
# ---------------------------------------------------------------------------


class TestRelayHelp:
    def test_help_exits_zero(self):
        result = runner.invoke(relay, ["--help"])
        assert result.exit_code == 0

    def test_help_lists_subcommands(self):
        result = runner.invoke(relay, ["--help"])
        assert "start" in result.output
        assert "status" in result.output
        assert "stop" in result.output


# ---------------------------------------------------------------------------
# relay start
# ---------------------------------------------------------------------------


class TestRelayStart:
    def test_start_once_invokes_run_relay_worker(self, clean_pid_file):
        with patch(_RUN_RELAY_WORKER) as mock_run:
            result = runner.invoke(relay, ["start", "--once"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_start_once_passes_dry_run_flag(self, clean_pid_file):
        with patch(_RUN_RELAY_WORKER) as mock_run:
            result = runner.invoke(relay, ["start", "--once", "--dry-run"])
        assert result.exit_code == 0
        _, kwargs = mock_run.call_args
        assert kwargs.get("dry_run") is True

    def test_start_once_passes_custom_interval(self, clean_pid_file):
        with patch(_RUN_RELAY_WORKER) as mock_run:
            result = runner.invoke(relay, ["start", "--once", "--interval", "60"])
        assert result.exit_code == 0
        _, kwargs = mock_run.call_args
        assert kwargs.get("poll_interval") == 60

    def test_start_background_writes_pid_file(self, clean_pid_file):
        fake_proc = MagicMock()
        fake_proc.pid = 12345
        with patch("subprocess.Popen", return_value=fake_proc):
            result = runner.invoke(relay, ["start"])
        assert result.exit_code == 0
        assert clean_pid_file.exists()
        assert clean_pid_file.read_text().strip() == "12345"

    def test_start_background_includes_dry_run_flag_in_cmd(self, clean_pid_file):
        fake_proc = MagicMock()
        fake_proc.pid = 9999
        with patch("subprocess.Popen", return_value=fake_proc) as mock_popen:
            result = runner.invoke(relay, ["start", "--dry-run"])
        assert result.exit_code == 0
        cmd = mock_popen.call_args[0][0]
        assert "--dry-run" in cmd

    def test_start_fails_when_already_running(self, clean_pid_file):
        clean_pid_file.write_text("42")
        with patch("os.kill", return_value=None):  # process exists, no exception
            result = runner.invoke(relay, ["start", "--once"])
        assert result.exit_code != 0

    def test_start_removes_stale_pid_file_and_proceeds(self, clean_pid_file):
        clean_pid_file.write_text("99999")
        with patch("os.kill", side_effect=ProcessLookupError):
            with patch(_RUN_RELAY_WORKER) as mock_run:
                result = runner.invoke(relay, ["start", "--once"])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_start_background_popen_failure_exits_nonzero(self, clean_pid_file):
        with patch("subprocess.Popen", side_effect=OSError("no such file")):
            result = runner.invoke(relay, ["start"])
        assert result.exit_code != 0

    def test_start_background_passes_poll_interval_in_cmd(self, clean_pid_file):
        fake_proc = MagicMock()
        fake_proc.pid = 777
        with patch("subprocess.Popen", return_value=fake_proc) as mock_popen:
            result = runner.invoke(relay, ["start", "--interval", "120"])
        assert result.exit_code == 0
        cmd = mock_popen.call_args[0][0]
        assert "120" in cmd


# ---------------------------------------------------------------------------
# relay status
# ---------------------------------------------------------------------------


class TestRelayStatus:
    def _make_worker(self, tasks=None, agents=None):
        mock_worker = MagicMock()
        mock_worker.scan_pending_tasks.return_value = tasks or []
        mock_worker.get_available_agents.return_value = agents or []
        mock_worker.get_agent_status.return_value = {"status": "idle"}
        return mock_worker

    def test_status_shows_not_running_when_no_pid_file(self, mock_forge_root, clean_pid_file):
        mock_worker = self._make_worker()
        with patch("forge_harness.cli_v2.relay.require_forge_root", return_value=mock_forge_root):
            with patch(_RELAY_WORKER_CLS, return_value=mock_worker):
                result = runner.invoke(relay, ["status"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_status_shows_running_pid_when_process_live(self, mock_forge_root, clean_pid_file):
        clean_pid_file.write_text("5555")
        mock_worker = self._make_worker()
        with patch("forge_harness.cli_v2.relay.require_forge_root", return_value=mock_forge_root):
            with patch(_RELAY_WORKER_CLS, return_value=mock_worker):
                with patch("os.kill", return_value=None):
                    result = runner.invoke(relay, ["status"])
        assert result.exit_code == 0
        assert "5555" in result.output

    def test_status_reports_stale_pid_and_removes_file(self, mock_forge_root, clean_pid_file):
        clean_pid_file.write_text("11111")
        mock_worker = self._make_worker()
        with patch("forge_harness.cli_v2.relay.require_forge_root", return_value=mock_forge_root):
            with patch(_RELAY_WORKER_CLS, return_value=mock_worker):
                with patch("os.kill", side_effect=ProcessLookupError):
                    result = runner.invoke(relay, ["status"])
        assert result.exit_code == 0
        assert not clean_pid_file.exists()

    def test_status_shows_pending_task_count(self, mock_forge_root, clean_pid_file):
        tasks = [
            {"id": "t1", "subject": "Task One"},
            {"id": "t2", "subject": "Task Two"},
        ]
        mock_worker = self._make_worker(tasks=tasks)
        with patch("forge_harness.cli_v2.relay.require_forge_root", return_value=mock_forge_root):
            with patch(_RELAY_WORKER_CLS, return_value=mock_worker):
                result = runner.invoke(relay, ["status"])
        assert result.exit_code == 0
        assert "2" in result.output

    def test_status_lists_available_agents(self, mock_forge_root, clean_pid_file):
        mock_worker = self._make_worker(agents=["forge:tech"])
        with patch("forge_harness.cli_v2.relay.require_forge_root", return_value=mock_forge_root):
            with patch(_RELAY_WORKER_CLS, return_value=mock_worker):
                result = runner.invoke(relay, ["status"])
        assert result.exit_code == 0
        assert "forge:tech" in result.output

    def test_status_shows_no_pending_tasks_message(self, mock_forge_root, clean_pid_file):
        mock_worker = self._make_worker()
        with patch("forge_harness.cli_v2.relay.require_forge_root", return_value=mock_forge_root):
            with patch(_RELAY_WORKER_CLS, return_value=mock_worker):
                result = runner.invoke(relay, ["status"])
        assert result.exit_code == 0
        assert "pending" in result.output.lower()

    def test_status_truncates_task_list_beyond_five(self, mock_forge_root, clean_pid_file):
        tasks = [{"id": f"t{i}", "subject": f"Task {i}"} for i in range(8)]
        mock_worker = self._make_worker(tasks=tasks)
        with patch("forge_harness.cli_v2.relay.require_forge_root", return_value=mock_forge_root):
            with patch(_RELAY_WORKER_CLS, return_value=mock_worker):
                result = runner.invoke(relay, ["status"])
        assert result.exit_code == 0
        assert "3 more" in result.output


# ---------------------------------------------------------------------------
# relay stop
# ---------------------------------------------------------------------------


class TestRelayStop:
    def test_stop_warns_when_not_running(self, clean_pid_file):
        result = runner.invoke(relay, ["stop"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_stop_sends_sigterm_and_removes_pid_file(self, clean_pid_file):
        clean_pid_file.write_text("2222")
        with patch("os.kill") as mock_kill:
            # First call is SIGTERM, second raises ProcessLookupError (stopped)
            mock_kill.side_effect = [None, ProcessLookupError]
            with patch("time.sleep"):
                result = runner.invoke(relay, ["stop"])
        assert result.exit_code == 0
        assert not clean_pid_file.exists()

    def test_stop_reports_error_when_process_survives(self, clean_pid_file):
        clean_pid_file.write_text("3333")
        with patch("os.kill", return_value=None):
            with patch("time.sleep"):
                result = runner.invoke(relay, ["stop"])
        assert result.exit_code != 0

    def test_stop_handles_invalid_pid_file(self, clean_pid_file):
        clean_pid_file.write_text("not_a_number")
        result = runner.invoke(relay, ["stop"])
        assert result.exit_code == 0
        assert not clean_pid_file.exists()
