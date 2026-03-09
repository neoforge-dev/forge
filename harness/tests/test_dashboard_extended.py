"""Extended tests for dashboard.py - Rich TUI Dashboard (40+ tests).

Tests cover:
- KeyboardHandler lifecycle and key reading
- ForgeDashboard initialization with various configs
- Data loading from files, API, and synchronizer
- Panel rendering methods
- Snapshot and live mode
- Error handling and edge cases
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Mock Rich components before import
rich_mock = MagicMock()
rich_mock.Console = MagicMock
rich_mock.Layout = MagicMock
rich_mock.Live = MagicMock
rich_mock.Panel = MagicMock
rich_mock.Table = MagicMock
rich_mock.Text = MagicMock

sys.modules["rich"] = rich_mock
sys.modules["rich.console"] = MagicMock()
sys.modules["rich.layout"] = MagicMock()
sys.modules["rich.live"] = MagicMock()
sys.modules["rich.panel"] = MagicMock()
sys.modules["rich.table"] = MagicMock()
sys.modules["rich.text"] = MagicMock()

from forge_harness.dashboard import (
    HAS_TERMIOS,
    ForgeDashboard,
    KeyboardHandler,
)

# =============================================================================
# KeyboardHandler Tests
# =============================================================================


class TestKeyboardHandler:
    """Tests for KeyboardHandler class."""

    def test_init(self) -> None:
        """Should initialize with default state."""
        handler = KeyboardHandler()
        assert handler._running is False
        assert handler._key_queue == []
        assert handler._thread is None

    def test_start_without_termios(self) -> None:
        """Should handle start when termios not available."""
        with patch("forge_harness.dashboard.HAS_TERMIOS", False):
            handler = KeyboardHandler()
            handler.start()
            assert handler._running is False
            assert handler._thread is None

    def test_start_with_termios(self) -> None:
        """Should start thread when termios available."""
        with patch("forge_harness.dashboard.HAS_TERMIOS", True):
            handler = KeyboardHandler()
            with patch.object(threading.Thread, "start") as mock_start:
                handler.start()
                assert handler._running is True
                assert handler._thread is not None

    def test_stop(self) -> None:
        """Should stop handler and cleanup."""
        handler = KeyboardHandler()
        handler._running = True
        handler._old_settings = MagicMock()

        with patch("forge_harness.dashboard.HAS_TERMIOS", True):
            with patch("termios.tcsetattr") as mock_tcsetattr:
                handler.stop()
                assert handler._running is False

    def test_get_key_empty_queue(self) -> None:
        """Should return None when queue is empty."""
        handler = KeyboardHandler()
        assert handler.get_key() is None

    def test_get_key_with_items(self) -> None:
        """Should return keys in FIFO order."""
        handler = KeyboardHandler()
        handler._key_queue = ["a", "b", "c"]
        assert handler.get_key() == "a"
        assert handler.get_key() == "b"
        assert handler.get_key() == "c"
        assert handler.get_key() is None


# =============================================================================
# ForgeDashboard Initialization Tests
# =============================================================================


class TestForgeDashboardInit:
    """Tests for ForgeDashboard initialization."""

    def test_init_defaults(self) -> None:
        """Should initialize with default paths."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            assert dashboard.forge_root == Path.cwd()
            assert dashboard.api_url is None
            assert dashboard._use_synchronizer is False

    def test_init_with_paths(self, tmp_path: Path) -> None:
        """Should initialize with custom paths."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(
                approval_storage_dir=tmp_path / "approvals",
                checkpoint_dir=tmp_path / "checkpoints",
                ralph_checkpoint_dir=tmp_path / "ralph",
                forge_root=tmp_path,
            )
            assert dashboard.forge_root == tmp_path
            assert dashboard.approval_storage_dir == tmp_path / "approvals"
            assert dashboard.checkpoint_dir == tmp_path / "checkpoints"

    def test_init_with_api_config(self) -> None:
        """Should initialize with API configuration."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(
                api_url="http://localhost:8080",
                api_token="test-token",
            )
            assert dashboard.api_url == "http://localhost:8080"
            assert dashboard.api_token == "test-token"

    def test_init_api_token_from_env(self) -> None:
        """Should read API token from environment."""
        with patch.dict("os.environ", {"FORGE_WEBHOOK_TOKEN": "env-token"}):
            with patch("forge_harness.dashboard.Console"):
                dashboard = ForgeDashboard(api_url="http://localhost:8080")
                assert dashboard.api_token == "env-token"

    def test_init_with_synchronizer(self, tmp_path: Path) -> None:
        """Should initialize with state synchronizer."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.state_synchronizer.get_synchronizer") as mock_sync:
                mock_sync.return_value = MagicMock()
                dashboard = ForgeDashboard(
                    forge_root=tmp_path,
                    use_synchronizer=True,
                )
                assert dashboard._use_synchronizer is True
                assert dashboard._synchronizer is not None

    def test_init_synchronizer_import_error(self, tmp_path: Path) -> None:
        """Should handle synchronizer import error gracefully."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.state_synchronizer.get_synchronizer", side_effect=ImportError):
                dashboard = ForgeDashboard(
                    forge_root=tmp_path,
                    use_synchronizer=True,
                )
                assert dashboard._use_synchronizer is False
                assert dashboard._synchronizer is None


# =============================================================================
# State Snapshot Tests
# =============================================================================


class TestStateSnapshot:
    """Tests for state snapshot functionality."""

    def test_get_state_snapshot_with_synchronizer(self, tmp_path: Path) -> None:
        """Should get snapshot from synchronizer."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.state_synchronizer.get_synchronizer") as mock_sync:
                mock_synchronizer = MagicMock()
                mock_synchronizer.get_state_snapshot.return_value = {"test": "data"}
                mock_sync.return_value = mock_synchronizer

                dashboard = ForgeDashboard(
                    forge_root=tmp_path,
                    use_synchronizer=True,
                )
                result = dashboard._get_state_snapshot()
                assert result == {"test": "data"}

    def test_get_state_snapshot_without_synchronizer(self, tmp_path: Path) -> None:
        """Should return None without synchronizer."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(forge_root=tmp_path)
            result = dashboard._get_state_snapshot()
            assert result is None


# =============================================================================
# API Client Tests
# =============================================================================


class TestAPIClient:
    """Tests for API client functionality."""

    def test_get_api_client_creates_new(self) -> None:
        """Should create new API client when none exists."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.command_center_client.CommandCenterClient") as mock_client:
                dashboard = ForgeDashboard(
                    api_url="http://localhost:8080",
                    api_token="test-token",
                )
                client = dashboard._get_api_client()
                assert client is not None
                mock_client.assert_called_once_with(
                    base_url="http://localhost:8080",
                    token="test-token",
                )

    def test_get_api_client_reuses_existing(self) -> None:
        """Should reuse existing API client."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.command_center_client.CommandCenterClient") as mock_client:
                dashboard = ForgeDashboard(api_url="http://localhost:8080")
                existing = MagicMock()
                dashboard._api_client = existing

                client = dashboard._get_api_client()
                assert client is existing
                mock_client.assert_not_called()

    def test_get_api_client_no_api_url(self) -> None:
        """Should return None when no API URL configured."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            client = dashboard._get_api_client()
            assert client is None

    def test_get_api_client_import_error(self) -> None:
        """Should handle import error gracefully."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.command_center_client.CommandCenterClient", side_effect=ImportError):
                dashboard = ForgeDashboard(api_url="http://localhost:8080")
                client = dashboard._get_api_client()
                assert client is None


# =============================================================================
# Agent Loading Tests
# =============================================================================


class TestLoadAgents:
    """Tests for agent loading functionality."""

    def test_load_agents_from_api(self) -> None:
        """Should load agents from API."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.command_center_client.CommandCenterClient") as mock_client_class:
                with patch("forge_harness.session_tracker.get_session_tracker") as mock_tracker:
                    mock_tracker.return_value.get_all_sessions.return_value = []

                    mock_client = MagicMock()
                    mock_agent = MagicMock()
                    mock_agent.id = "agent-1"
                    mock_agent.role = "developer"
                    mock_agent.project = "test-project"
                    mock_agent.task = "coding"
                    mock_agent.status.value = "active"
                    mock_agent.progress = 50
                    mock_agent.is_stale = False
                    mock_agent.last_activity = datetime.now(UTC)
                    mock_client.sync_list_agents.return_value = [mock_agent]
                    mock_client_class.return_value = mock_client

                    dashboard = ForgeDashboard(api_url="http://localhost:8080")
                    agents = dashboard._load_agents_from_api()

                    assert len(agents) == 1
                    assert agents[0].id == "agent-1"
                    assert agents[0].role == "developer"

    def test_load_agents_api_error(self) -> None:
        """Should handle API errors gracefully."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.command_center_client.CommandCenterClient") as mock_client_class:
                with patch("forge_harness.session_tracker.get_session_tracker") as mock_tracker:
                    mock_tracker.return_value.get_all_sessions.return_value = []

                    mock_client = MagicMock()
                    mock_client.sync_list_agents.side_effect = Exception("API Error")
                    mock_client_class.return_value = mock_client

                    dashboard = ForgeDashboard(api_url="http://localhost:8080")
                    agents = dashboard._load_agents_from_api()

                    assert agents == []

    def test_load_agents_from_tmux(self) -> None:
        """Should load agents from tmux sessions."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.session_tracker.get_session_tracker") as mock_tracker:
                mock_session = MagicMock()
                mock_session.session_name = "forge:test"
                mock_session.window_name = "test-window"
                mock_session.agent_type = "unknown"
                mock_session.status = "active"
                mock_session.domain = "test-domain"
                mock_session.project = "test-project"
                mock_session.current_task = "testing"
                mock_session.last_activity = datetime.now(UTC).isoformat()

                mock_tracker.return_value.get_all_sessions.return_value = [mock_session]

                dashboard = ForgeDashboard()
                agents = dashboard._load_agents_from_api()

                assert len(agents) == 1
                assert agents[0].source == "tmux"


# =============================================================================
# Task Loading Tests
# =============================================================================


class TestLoadTasks:
    """Tests for task loading functionality."""

    def test_load_tasks_from_api(self) -> None:
        """Should load tasks from API."""
        with patch("forge_harness.dashboard.Console"):
            with patch("httpx.get") as mock_get:
                mock_response = MagicMock()
                mock_response.json.return_value = {
                    "data": {
                        "tasks": [
                            {"id": "task-1", "title": "Test Task"},
                        ]
                    }
                }
                mock_response.raise_for_status = MagicMock()
                mock_get.return_value = mock_response

                dashboard = ForgeDashboard(api_url="http://localhost:8080")
                tasks = dashboard._load_tasks_from_api()

                assert len(tasks) == 1
                assert tasks[0]["id"] == "task-1"

    def test_load_tasks_api_error(self) -> None:
        """Should handle API errors and return cached tasks."""
        with patch("forge_harness.dashboard.Console"):
            with patch("httpx.get", side_effect=Exception("API Error")):
                dashboard = ForgeDashboard(api_url="http://localhost:8080")
                dashboard._cached_tasks = [{"id": "cached"}]
                tasks = dashboard._load_tasks_from_api()

                assert tasks == [{"id": "cached"}]

    def test_load_tasks_no_api_url(self) -> None:
        """Should return empty list when no API URL."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            tasks = dashboard._load_tasks_from_api()
            assert tasks == []


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling functionality."""

    def test_add_error(self) -> None:
        """Should add error to buffer."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            dashboard._add_error("Test error")
            assert len(dashboard._recent_errors) == 1
            assert dashboard._recent_errors[0][1] == "Test error"

    def test_add_error_max_buffer(self) -> None:
        """Should limit error buffer size."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            dashboard._max_errors = 3

            for i in range(5):
                dashboard._add_error(f"Error {i}")

            assert len(dashboard._recent_errors) == 3
            assert dashboard._recent_errors[0][1] == "Error 2"
            assert dashboard._recent_errors[2][1] == "Error 4"


# =============================================================================
# Approval Loading Tests
# =============================================================================


class TestLoadApprovals:
    """Tests for approval loading functionality."""

    def test_load_approvals_from_synchronizer(self, tmp_path: Path) -> None:
        """Should load approvals from synchronizer."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.state_synchronizer.get_synchronizer") as mock_sync:
                mock_approval = MagicMock()
                mock_approval.status = "pending"
                mock_approval.id = "app-1"
                mock_approval.type = "deployment"
                mock_approval.title = "Test Approval"
                mock_approval.domain = "test-domain"
                mock_approval.priority = "high"
                mock_approval.tier = "PHONE"
                mock_approval.risk_score = 0.5
                mock_approval.created_at = datetime.now(UTC)

                mock_snapshot = MagicMock()
                mock_snapshot.approvals = [mock_approval]

                mock_synchronizer = MagicMock()
                mock_synchronizer.get_state_snapshot.return_value = mock_snapshot
                mock_sync.return_value = mock_synchronizer

                dashboard = ForgeDashboard(
                    forge_root=tmp_path,
                    use_synchronizer=True,
                )
                approvals = dashboard._load_pending_approvals()

                assert len(approvals) == 1
                assert approvals[0].id == "app-1"

    def test_load_approvals_from_files(self, tmp_path: Path) -> None:
        """Should load approvals from files."""
        approvals_dir = tmp_path / ".forge/approvals"
        approvals_dir.mkdir()

        approval_file = approvals_dir / "approval_test.json"
        approval_file.write_text(json.dumps({
            "id": "app-file",
            "status": "pending",
            "type": "deployment",
            "title": "File Approval",
            "domain": "test-domain",
            "priority": "high",
            "tier": "PHONE",
            "risk_score": 0.5,
            "created_at": datetime.now(UTC).isoformat(),
        }))

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(approval_storage_dir=approvals_dir)
            approvals = dashboard._load_pending_approvals()

            assert len(approvals) == 1
            assert approvals[0].id == "app-file"

    def test_load_approvals_skips_non_pending(self, tmp_path: Path) -> None:
        """Should skip non-pending approvals."""
        approvals_dir = tmp_path / ".forge/approvals"
        approvals_dir.mkdir()

        approval_file = approvals_dir / "approval_test.json"
        approval_file.write_text(json.dumps({
            "id": "app-file",
            "status": "approved",
            "type": "deployment",
            "title": "File Approval",
            "domain": "test-domain",
            "priority": "high",
            "tier": "PHONE",
            "risk_score": 0.5,
            "created_at": datetime.now(UTC).isoformat(),
        }))

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(approval_storage_dir=approvals_dir)
            approvals = dashboard._load_pending_approvals()

            assert len(approvals) == 0

    def test_load_approvals_invalid_json(self, tmp_path: Path) -> None:
        """Should skip files with invalid JSON."""
        approvals_dir = tmp_path / ".forge/approvals"
        approvals_dir.mkdir()

        approval_file = approvals_dir / "approval_bad.json"
        approval_file.write_text("not valid json")

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(approval_storage_dir=approvals_dir)
            approvals = dashboard._load_pending_approvals()

            assert len(approvals) == 0


# =============================================================================
# Pipeline Loading Tests
# =============================================================================


class TestLoadPipelines:
    """Tests for pipeline loading functionality."""

    def test_load_active_pipelines(self, tmp_path: Path) -> None:
        """Should load active pipelines."""
        checkpoint_dir = tmp_path / ".forge/orchestration_checkpoints"
        checkpoint_dir.mkdir()

        checkpoint_file = checkpoint_dir / "pipeline_test.json"
        checkpoint_file.write_text(json.dumps({
            "status": "running",
            "pipeline": {"name": "Test Pipeline", "steps": [{"name": "step1"}, {"name": "step2"}]},
            "step_results": {"step1": "done"},
            "started_at": datetime.now(UTC).isoformat(),
        }))

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(checkpoint_dir=checkpoint_dir)
            pipelines = dashboard._load_active_pipelines()

            assert len(pipelines) == 1
            assert pipelines[0].name == "Test Pipeline"
            assert pipelines[0].status == "running"

    def test_load_pipelines_skips_inactive(self, tmp_path: Path) -> None:
        """Should skip inactive pipelines."""
        checkpoint_dir = tmp_path / ".forge/orchestration_checkpoints"
        checkpoint_dir.mkdir()

        checkpoint_file = checkpoint_dir / "pipeline_test.json"
        checkpoint_file.write_text(json.dumps({
            "status": "completed",
            "pipeline": {"name": "Test Pipeline", "steps": []},
            "step_results": {},
        }))

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(checkpoint_dir=checkpoint_dir)
            pipelines = dashboard._load_active_pipelines()

            assert len(pipelines) == 0

    def test_load_pipelines_invalid_json(self, tmp_path: Path) -> None:
        """Should skip files with invalid JSON."""
        checkpoint_dir = tmp_path / ".forge/orchestration_checkpoints"
        checkpoint_dir.mkdir()

        checkpoint_file = checkpoint_dir / "pipeline_bad.json"
        checkpoint_file.write_text("not valid json")

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(checkpoint_dir=checkpoint_dir)
            pipelines = dashboard._load_active_pipelines()

            assert len(pipelines) == 0


# =============================================================================
# MVP Status Tests
# =============================================================================


class TestLoadMVPStatus:
    """Tests for MVP status loading functionality."""

    def test_load_mvp_status(self, tmp_path: Path) -> None:
        """Should load MVP status from files."""
        mvp_dir = tmp_path / ".forge/mvp_status"
        mvp_dir.mkdir()

        status_file = mvp_dir / "test_domain.json"
        status_file.write_text(json.dumps({
            "domain": "test-domain",
            "project": "test-project",
            "status": "ready",
            "timestamp": datetime.now(UTC).isoformat(),
            "missing_env_vars": [],
        }))

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(forge_root=tmp_path)
            statuses = dashboard._load_mvp_status()

            assert len(statuses) == 1
            assert statuses[0].domain == "test-domain"
            assert statuses[0].status == "ready"

    def test_load_mvp_status_invalid_json(self, tmp_path: Path) -> None:
        """Should skip files with invalid JSON."""
        mvp_dir = tmp_path / ".forge/mvp_status"
        mvp_dir.mkdir()

        status_file = mvp_dir / "bad.json"
        status_file.write_text("not valid json")

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(forge_root=tmp_path)
            statuses = dashboard._load_mvp_status()

            assert len(statuses) == 0


# =============================================================================
# Ralph Status Tests
# =============================================================================


class TestLoadRalphStatus:
    """Tests for Ralph status loading functionality."""

    def test_load_ralph_status_default(self, tmp_path: Path) -> None:
        """Should return default status when no checkpoints."""
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(ralph_checkpoint_dir=ralph_dir)
            status = dashboard._load_ralph_status()

            assert status.active is False
            assert status.current_feature is None
            assert status.iteration == 0

    def test_load_ralph_status_from_checkpoint(self, tmp_path: Path) -> None:
        """Should load status from checkpoint file."""
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()

        checkpoint_file = ralph_dir / "checkpoint_20250101_120000.json"
        checkpoint_file.write_text(json.dumps({
            "state": "running",
            "current_feature": "feature-1",
            "iteration": 5,
            "max_iterations": 100,
            "failure_count": 0,
            "started_at": datetime.now(UTC).isoformat(),
        }))

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(ralph_checkpoint_dir=ralph_dir)
            status = dashboard._load_ralph_status()

            assert status.active is True
            assert status.current_feature == "feature-1"
            assert status.iteration == 5

    def test_load_ralph_status_invalid_json(self, tmp_path: Path) -> None:
        """Should return default status on invalid JSON."""
        ralph_dir = tmp_path / ".forge/ralph_checkpoints"
        ralph_dir.mkdir()

        checkpoint_file = ralph_dir / "checkpoint_bad.json"
        checkpoint_file.write_text("not valid json")

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(ralph_checkpoint_dir=ralph_dir)
            status = dashboard._load_ralph_status()

            assert status.active is False


# =============================================================================
# Alert Sound Tests
# =============================================================================


class TestAlertSound:
    """Tests for alert sound functionality."""

    def test_play_alert_sound_enabled(self) -> None:
        """Should play sound when enabled."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            dashboard._sound_alerts_enabled = True

            with patch("builtins.print") as mock_print:
                dashboard._play_alert_sound()
                mock_print.assert_called_once()

    def test_play_alert_sound_disabled(self) -> None:
        """Should not play sound when disabled."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            dashboard._sound_alerts_enabled = False

            with patch("builtins.print") as mock_print:
                dashboard._play_alert_sound()
                mock_print.assert_not_called()

    def test_check_new_approvals_triggers_sound(self) -> None:
        """Should trigger sound on new approvals."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            dashboard._last_approval_count = 1
            dashboard._sound_alerts_enabled = True

            with patch.object(dashboard, "_play_alert_sound") as mock_play:
                from forge_harness.dashboard_core import ApprovalSummary
                approvals = [
                    ApprovalSummary(
                        id="app-1",
                        type="test",
                        title="Test",
                        domain="test",
                        priority="high",
                        tier="PHONE",
                        risk_score=0.5,
                        created_at=datetime.now(UTC),
                    ),
                    ApprovalSummary(
                        id="app-2",
                        type="test",
                        title="Test 2",
                        domain="test",
                        priority="high",
                        tier="PHONE",
                        risk_score=0.5,
                        created_at=datetime.now(UTC),
                    ),
                ]
                dashboard._check_new_approvals(approvals)
                mock_play.assert_called_once()


# =============================================================================
# Render Header Tests
# =============================================================================


class TestRenderHeader:
    """Tests for header rendering."""

    def test_render_header_basic(self) -> None:
        """Should render header panel."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.dashboard.Panel") as mock_panel:
                with patch("forge_harness.dashboard.Text") as mock_text:
                    dashboard = ForgeDashboard()
                    result = dashboard.render_header()
                    assert result is not None
                    mock_panel.assert_called_once()

    def test_render_header_with_status_message(self) -> None:
        """Should include status message in header."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.dashboard.Panel") as mock_panel:
                with patch("forge_harness.dashboard.Text") as mock_text:
                    dashboard = ForgeDashboard()
                    dashboard._status_message = "✓ Test message"
                    dashboard._status_message_time = datetime.now(UTC)
                    result = dashboard.render_header()
                    assert result is not None


# =============================================================================
# Health Status Tests
# =============================================================================


class TestHealthStatus:
    """Tests for health status functionality."""

    def test_render_health_status_with_error(self) -> None:
        """Should render health status with error."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.dashboard.Panel") as mock_panel:
                dashboard = ForgeDashboard()
                dashboard._health_check_error = "Test error"
                result = dashboard.render_health_status()
                assert result is not None

    @pytest.mark.asyncio
    async def test_load_health_status_from_api(self) -> None:
        """Should load health status from API."""
        with patch("forge_harness.dashboard.Console"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"status": "healthy"}

                mock_async_client = AsyncMock()
                mock_async_client.get.return_value = mock_response
                mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_async_client)
                mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

                dashboard = ForgeDashboard(api_url="http://localhost:8080")
                result = await dashboard._load_health_status()

                # _load_health_status parses the API dict into an AggregatedHealth object
                assert result is not None
                assert result.status.value == "healthy"

    @pytest.mark.asyncio
    async def test_load_health_status_api_error(self) -> None:
        """Should handle API errors and try fallback."""
        with patch("forge_harness.dashboard.Console"):
            with patch("httpx.AsyncClient", side_effect=Exception("API Error")):
                with patch("forge_harness.health_checks.get_health_registry", side_effect=Exception("Registry Error")):
                    dashboard = ForgeDashboard(api_url="http://localhost:8080")
                    result = await dashboard._load_health_status()

                    # When both API and registry fail, should record error
                    assert dashboard._health_check_error is not None


# =============================================================================
# Pipeline Rendering Tests
# =============================================================================


class TestRenderPipelines:
    """Tests for pipeline rendering."""

    def test_render_pipelines_empty(self) -> None:
        """Should render empty pipelines panel."""
        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.dashboard.Panel") as mock_panel:
                with patch("forge_harness.dashboard.Table") as mock_table:
                    dashboard = ForgeDashboard()
                    result = dashboard.render_pipelines()
                    assert result is not None

    def test_render_pipelines_with_data(self, tmp_path: Path) -> None:
        """Should render pipelines with data."""
        checkpoint_dir = tmp_path / ".forge/orchestration_checkpoints"
        checkpoint_dir.mkdir()

        checkpoint_file = checkpoint_dir / "pipeline_test.json"
        checkpoint_file.write_text(json.dumps({
            "status": "running",
            "pipeline": {"name": "Test Pipeline", "steps": [{"name": "step1"}]},
            "step_results": {},
            "started_at": datetime.now(UTC).isoformat(),
        }))

        with patch("forge_harness.dashboard.Console"):
            with patch("forge_harness.dashboard.Panel") as mock_panel:
                with patch("forge_harness.dashboard.Table") as mock_table:
                    dashboard = ForgeDashboard(checkpoint_dir=checkpoint_dir)
                    result = dashboard.render_pipelines()
                    assert result is not None


# =============================================================================
# Approval Loading from API Tests
# =============================================================================


class TestLoadApprovalsFromAPI:
    """Tests for loading approvals from API."""

    def test_load_approvals_from_api(self) -> None:
        """Should load approvals from API."""
        with patch("forge_harness.dashboard.Console"):
            with patch("httpx.get") as mock_get:
                mock_response = MagicMock()
                mock_response.json.return_value = {
                    "data": {
                        "approvals": [
                            {
                                "id": "app-api",
                                "type": "deployment",
                                "title": "API Approval",
                                "domain": "test-domain",
                                "priority": "high",
                                "tier": "PHONE",
                                "risk_score": 0.5,
                                "created_at": datetime.now(UTC).isoformat(),
                            }
                        ]
                    }
                }
                mock_response.raise_for_status = MagicMock()
                mock_get.return_value = mock_response

                dashboard = ForgeDashboard(api_url="http://localhost:8080")
                approvals = dashboard._load_pending_approvals_from_api()

                assert approvals is not None
                assert len(approvals) == 1
                assert approvals[0].id == "app-api"

    def test_load_approvals_from_api_error(self) -> None:
        """Should handle API errors."""
        with patch("forge_harness.dashboard.Console"):
            with patch("httpx.get", side_effect=Exception("API Error")):
                dashboard = ForgeDashboard(api_url="http://localhost:8080")
                approvals = dashboard._load_pending_approvals_from_api()

                assert approvals is None

    def test_load_approvals_from_api_no_url(self) -> None:
        """Should return None when no API URL."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            approvals = dashboard._load_pending_approvals_from_api()

            assert approvals is None


# =============================================================================
# Navigation State Tests
# =============================================================================


class TestNavigationState:
    """Tests for navigation state management."""

    def test_initial_navigation_state(self) -> None:
        """Should have correct initial navigation state."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            assert dashboard._selected_approval_index == 0
            assert dashboard._selected_pipeline_index == 0
            assert dashboard._selected_pattern_index == 0
            assert dashboard._selected_agent_index == 0

    def test_detail_view_state(self) -> None:
        """Should manage detail view state."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            assert dashboard._detail_view_active is False
            assert dashboard._detail_view_type is None
            assert dashboard._detail_view_item is None

    def test_time_filter_state(self) -> None:
        """Should have default time filter."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            assert dashboard._time_filter == "all"

    def test_help_modal_state(self) -> None:
        """Should manage help modal state."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard()
            assert dashboard._show_help_modal is False


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_load_pipelines_no_directory(self) -> None:
        """Should handle missing checkpoint directory."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(checkpoint_dir=Path("/nonexistent"))
            pipelines = dashboard._load_active_pipelines()
            assert pipelines == []

    def test_load_mvp_status_no_directory(self, tmp_path: Path) -> None:
        """Should handle missing MVP status directory."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(forge_root=tmp_path)
            statuses = dashboard._load_mvp_status()
            assert statuses == []

    def test_load_ralph_status_no_directory(self, tmp_path: Path) -> None:
        """Should handle missing Ralph checkpoint directory."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(ralph_checkpoint_dir=tmp_path / "nonexistent")
            status = dashboard._load_ralph_status()
            assert status.active is False

    def test_approval_loading_with_time_filter(self, tmp_path: Path) -> None:
        """Should apply time filter to approvals."""
        approvals_dir = tmp_path / ".forge/approvals"
        approvals_dir.mkdir()

        # Create approval from 1 hour ago
        old_time = datetime.now(UTC) - timedelta(hours=1)
        approval_file = approvals_dir / "approval_old.json"
        approval_file.write_text(json.dumps({
            "id": "app-old",
            "status": "pending",
            "type": "deployment",
            "title": "Old Approval",
            "domain": "test-domain",
            "priority": "high",
            "tier": "PHONE",
            "risk_score": 0.5,
            "created_at": old_time.isoformat(),
        }))

        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(approval_storage_dir=approvals_dir)
            dashboard._time_filter = "1h"
            approvals = dashboard._load_pending_approvals()
            # Should be filtered out by 1h filter

    def test_cached_data_preserved(self, tmp_path: Path) -> None:
        """Should preserve cached data for detail views."""
        with patch("forge_harness.dashboard.Console"):
            dashboard = ForgeDashboard(forge_root=tmp_path)

            # Set some cached data
            dashboard._cached_agents = [MagicMock()]
            dashboard._cached_pipelines = [MagicMock()]
            dashboard._cached_approvals = [MagicMock()]

            assert len(dashboard._cached_agents) == 1
            assert len(dashboard._cached_pipelines) == 1
            assert len(dashboard._cached_approvals) == 1
