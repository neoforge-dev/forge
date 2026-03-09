"""Tests for dashboard.py - Rich TUI Dashboard.

Tests cover:
- KeyboardHandler initialization and lifecycle
- ForgeDashboard initialization and configuration
- Data loading from files and API
- Rendering methods (header, panels, layout)
- Utility methods
"""

from __future__ import annotations

import asyncio
import json
import sys
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
    create_dashboard,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_rich_components():
    """Mock Rich library components."""
    with patch("forge_harness.dashboard.Console") as mock_console, \
         patch("forge_harness.dashboard.Layout") as mock_layout, \
         patch("forge_harness.dashboard.Panel") as mock_panel, \
         patch("forge_harness.dashboard.Table") as mock_table, \
         patch("forge_harness.dashboard.Text") as mock_text, \
         patch("forge_harness.dashboard.Live") as mock_live:

        mock_console_instance = MagicMock()
        mock_console.return_value = mock_console_instance

        mock_layout_instance = MagicMock()
        mock_layout.return_value = mock_layout_instance

        mock_panel_instance = MagicMock()
        mock_panel.return_value = mock_panel_instance

        mock_table_instance = MagicMock()
        mock_table.return_value = mock_table_instance

        mock_text_instance = MagicMock()
        mock_text.return_value = mock_text_instance

        yield {
            "Console": mock_console,
            "Layout": mock_layout,
            "Panel": mock_panel,
            "Table": mock_table,
            "Text": mock_text,
            "Live": mock_live,
            "console_instance": mock_console_instance,
            "layout_instance": mock_layout_instance,
            "panel_instance": mock_panel_instance,
            "table_instance": mock_table_instance,
            "text_instance": mock_text_instance,
        }


@pytest.fixture
def tmp_forge_root(tmp_path: Path) -> Path:
    """Create a temporary FORGE root directory structure."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()

    # Create subdirectories
    (forge_root / ".forge/approvals").mkdir()
    (forge_root / ".forge/orchestration_checkpoints").mkdir()
    (forge_root / ".forge/ralph_checkpoints").mkdir()
    (forge_root / ".forge/mvp_status").mkdir()

    return forge_root


@pytest.fixture
def dashboard(tmp_forge_root: Path, mock_rich_components) -> ForgeDashboard:
    """Create a ForgeDashboard instance with mocked dependencies."""
    with patch("forge_harness.dashboard.logger"):
        dashboard = ForgeDashboard(
            forge_root=tmp_forge_root,
            approval_storage_dir=tmp_forge_root / ".forge/approvals",
            checkpoint_dir=tmp_forge_root / ".forge/orchestration_checkpoints",
            ralph_checkpoint_dir=tmp_forge_root / ".forge/ralph_checkpoints",
        )
        return dashboard


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
        assert handler._old_settings is None

    def test_start_no_termios(self) -> None:
        """Should not start when termios is not available."""
        with patch("forge_harness.dashboard.HAS_TERMIOS", False):
            handler = KeyboardHandler()
            handler.start()

            assert handler._running is False
            assert handler._thread is None

    def test_stop_without_start(self) -> None:
        """Should handle stop without start gracefully."""
        handler = KeyboardHandler()
        handler.stop()  # Should not raise

        assert handler._running is False

    def test_get_key_empty_queue(self) -> None:
        """Should return None when queue is empty."""
        handler = KeyboardHandler()

        result = handler.get_key()

        assert result is None

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

    def test_init_default_paths(self, tmp_forge_root: Path, mock_rich_components) -> None:
        """Should initialize with default paths."""
        with patch("forge_harness.dashboard.logger"):
            dashboard = ForgeDashboard(forge_root=tmp_forge_root)

            assert dashboard.forge_root == tmp_forge_root
            assert dashboard.approval_storage_dir == tmp_forge_root / ".forge/approvals"
            assert dashboard.checkpoint_dir == tmp_forge_root / ".forge/orchestration_checkpoints"
            assert dashboard.ralph_checkpoint_dir == tmp_forge_root / ".forge/ralph_checkpoints"

    def test_init_custom_paths(self, tmp_path: Path, mock_rich_components) -> None:
        """Should initialize with custom paths."""
        custom_approvals = tmp_path / "custom_approvals"
        custom_checkpoints = tmp_path / "custom_checkpoints"

        with patch("forge_harness.dashboard.logger"):
            dashboard = ForgeDashboard(
                forge_root=tmp_path,
                approval_storage_dir=custom_approvals,
                checkpoint_dir=custom_checkpoints,
            )

            assert dashboard.approval_storage_dir == custom_approvals
            assert dashboard.checkpoint_dir == custom_checkpoints

    def test_init_api_configuration(self, tmp_path: Path, mock_rich_components) -> None:
        """Should configure API settings."""
        with patch("forge_harness.dashboard.logger"):
            dashboard = ForgeDashboard(
                forge_root=tmp_path,
                api_url="http://localhost:8080",
                api_token="test-token",
            )

            assert dashboard.api_url == "http://localhost:8080"
            assert dashboard.api_token == "test-token"

    def test_init_api_token_from_env(self, tmp_path: Path, mock_rich_components) -> None:
        """Should read API token from environment."""
        with patch("forge_harness.dashboard.logger"), \
             patch.dict("os.environ", {"FORGE_WEBHOOK_TOKEN": "env-token"}):
            dashboard = ForgeDashboard(
                forge_root=tmp_path,
                api_url="http://localhost:8080",
            )

            assert dashboard.api_token == "env-token"

    def test_init_default_state(self, tmp_path: Path, mock_rich_components) -> None:
        """Should initialize with correct default state."""
        with patch("forge_harness.dashboard.logger"):
            dashboard = ForgeDashboard(forge_root=tmp_path)

            assert dashboard._selected_approval_index == 0
            assert dashboard._cached_approvals == []
            assert dashboard._keyboard_handler is None
            assert dashboard._show_help_modal is False
            assert dashboard._time_filter == "all"
            assert dashboard._sound_alerts_enabled is True
            assert dashboard._detail_view_active is False


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling methods."""

    def test_add_error(self, dashboard: ForgeDashboard) -> None:
        """Should add error to buffer."""
        dashboard._add_error("Test error message")

        assert len(dashboard._recent_errors) == 1
        assert dashboard._recent_errors[0][1] == "Test error message"

    def test_add_error_max_buffer(self, dashboard: ForgeDashboard) -> None:
        """Should limit error buffer size."""
        dashboard._max_errors = 3

        for i in range(5):
            dashboard._add_error(f"Error {i}")

        assert len(dashboard._recent_errors) == 3
        # Should keep most recent errors
        assert dashboard._recent_errors[0][1] == "Error 2"
        assert dashboard._recent_errors[-1][1] == "Error 4"

    def test_set_status(self, dashboard: ForgeDashboard) -> None:
        """Should set status message with timestamp."""
        dashboard._set_status("Test status")

        assert dashboard._status_message == "Test status"
        assert dashboard._status_message_time is not None


# =============================================================================
# Data Loading Tests - Pipelines
# =============================================================================


class TestLoadActivePipelines:
    """Tests for _load_active_pipelines method."""

    def test_load_no_checkpoint_dir(self, dashboard: ForgeDashboard) -> None:
        """Should return empty list when checkpoint dir doesn't exist."""
        dashboard.checkpoint_dir = Path("/nonexistent")

        result = dashboard._load_active_pipelines()

        assert result == []

    def test_load_running_pipeline(self, dashboard: ForgeDashboard) -> None:
        """Should load running pipeline from checkpoint."""
        checkpoint = {
            "status": "running",
            "pipeline": {
                "name": "test-pipeline",
                "steps": [
                    {"name": "step1"},
                    {"name": "step2"},
                ],
            },
            "step_results": {"step1": "success"},
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = dashboard.checkpoint_dir / "test.json"
        checkpoint_file.write_text(json.dumps(checkpoint))

        result = dashboard._load_active_pipelines()

        assert len(result) == 1
        assert result[0].name == "test-pipeline"
        assert result[0].status == "running"
        assert result[0].current_step == "step2"

    def test_load_non_running_pipeline_filtered(self, dashboard: ForgeDashboard) -> None:
        """Should filter out non-running pipelines."""
        checkpoint = {
            "status": "completed",
            "pipeline": {"name": "completed-pipeline", "steps": []},
            "step_results": {},
        }

        checkpoint_file = dashboard.checkpoint_dir / "test.json"
        checkpoint_file.write_text(json.dumps(checkpoint))

        result = dashboard._load_active_pipelines()

        assert result == []

    def test_load_invalid_checkpoint(self, dashboard: ForgeDashboard) -> None:
        """Should handle invalid checkpoint files gracefully."""
        checkpoint_file = dashboard.checkpoint_dir / "invalid.json"
        checkpoint_file.write_text("not valid json")

        result = dashboard._load_active_pipelines()

        assert result == []


# =============================================================================
# Data Loading Tests - MVP Status
# =============================================================================


class TestLoadMVPStatus:
    """Tests for _load_mvp_status method."""

    def test_load_no_status_dir(self, dashboard: ForgeDashboard) -> None:
        """Should return empty list when status dir doesn't exist."""
        dashboard.mvp_status_dir = Path("/nonexistent")

        result = dashboard._load_mvp_status()

        assert result == []

    def test_load_mvp_status(self, dashboard: ForgeDashboard) -> None:
        """Should load MVP status from file."""
        status = {
            "domain": "test-domain",
            "project": "test-project",
            "status": "passed",
            "timestamp": datetime.now(UTC).isoformat(),
            "missing_env_vars": [],
        }

        status_file = dashboard.mvp_status_dir / "test.json"
        status_file.write_text(json.dumps(status))

        result = dashboard._load_mvp_status()

        assert len(result) == 1
        assert result[0].domain == "test-domain"
        assert result[0].status == "passed"

    def test_load_mvp_status_with_missing_env(self, dashboard: ForgeDashboard) -> None:
        """Should count missing env vars."""
        status = {
            "domain": "test-domain",
            "status": "failed",
            "missing_env_vars": ["VAR1", "VAR2"],
        }

        status_file = dashboard.mvp_status_dir / "test.json"
        status_file.write_text(json.dumps(status))

        result = dashboard._load_mvp_status()

        assert result[0].missing_env_vars == 2

    def test_load_multiple_sorted_by_time(self, dashboard: ForgeDashboard) -> None:
        """Should sort statuses by timestamp (newest first)."""
        now = datetime.now(UTC)

        for i, minutes in enumerate([30, 10, 20]):
            status = {
                "domain": f"domain-{i}",
                "timestamp": (now - timedelta(minutes=minutes)).isoformat(),
            }
            status_file = dashboard.mvp_status_dir / f"test{i}.json"
            status_file.write_text(json.dumps(status))

        result = dashboard._load_mvp_status()

        assert len(result) == 3
        # Should be sorted: 10 min ago (domain-1), 20 min ago (domain-2), 30 min ago (domain-0)
        assert result[0].domain == "domain-1"
        assert result[1].domain == "domain-2"
        assert result[2].domain == "domain-0"


# =============================================================================
# Data Loading Tests - Ralph Status
# =============================================================================


class TestLoadRalphStatus:
    """Tests for _load_ralph_status method."""

    def test_load_no_checkpoint_dir(self, dashboard: ForgeDashboard) -> None:
        """Should return default status when checkpoint dir doesn't exist."""
        dashboard.ralph_checkpoint_dir = Path("/nonexistent")

        result = dashboard._load_ralph_status()

        assert result.active is False
        assert result.state == "idle"
        assert result.iteration == 0

    def test_load_ralph_checkpoint(self, dashboard: ForgeDashboard) -> None:
        """Should load Ralph status from checkpoint."""
        checkpoint = {
            "state": "running",
            "current_feature": "feature-123",
            "iteration": 5,
            "max_iterations": 100,
            "failure_count": 2,
            "started_at": datetime.now(UTC).isoformat(),
        }

        checkpoint_file = dashboard.ralph_checkpoint_dir / "ralph.json"
        checkpoint_file.write_text(json.dumps(checkpoint))

        result = dashboard._load_ralph_status()

        assert result.active is True
        assert result.current_feature == "feature-123"
        assert result.iteration == 5
        assert result.failure_count == 2
        assert result.state == "running"

    def test_load_ralph_inactive(self, dashboard: ForgeDashboard) -> None:
        """Should detect inactive state."""
        checkpoint = {
            "state": "completed",
            "current_feature": None,
            "iteration": 100,
        }

        checkpoint_file = dashboard.ralph_checkpoint_dir / "ralph.json"
        checkpoint_file.write_text(json.dumps(checkpoint))

        result = dashboard._load_ralph_status()

        assert result.active is False
        assert result.state == "completed"


# =============================================================================
# Data Loading Tests - Pending Approvals
# =============================================================================


class TestLoadPendingApprovals:
    """Tests for _load_pending_approvals method."""

    def test_load_from_files(self, dashboard: ForgeDashboard) -> None:
        """Should load pending approvals from files."""
        approval = {
            "id": "approval-123",
            "type": "deployment",
            "title": "Deploy to production",
            "status": "pending",
            "domain": "test-domain",
            "priority": "high",
            "tier": "DESKTOP",
            "risk_score": 0.8,
            "created_at": datetime.now(UTC).isoformat(),
        }

        approval_file = dashboard.approval_storage_dir / "approval_123.json"
        approval_file.write_text(json.dumps(approval))

        result = dashboard._load_pending_approvals()

        assert len(result) == 1
        assert result[0].id == "approval-123"
        assert result[0].priority == "high"

    def test_load_filters_non_pending(self, dashboard: ForgeDashboard) -> None:
        """Should filter out non-pending approvals."""
        pending = {"id": "pending-1", "status": "pending"}
        approved = {"id": "approved-1", "status": "approved"}

        (dashboard.approval_storage_dir / "approval_pending.json").write_text(
            json.dumps(pending)
        )
        (dashboard.approval_storage_dir / "approval_approved.json").write_text(
            json.dumps(approved)
        )

        result = dashboard._load_pending_approvals()

        assert len(result) == 1
        assert result[0].id == "pending-1"

    def test_load_invalid_file(self, dashboard: ForgeDashboard) -> None:
        """Should handle invalid approval files gracefully."""
        (dashboard.approval_storage_dir / "invalid.json").write_text("not json")

        result = dashboard._load_pending_approvals()

        assert result == []

    def test_load_no_approvals_dir(self, dashboard: ForgeDashboard) -> None:
        """Should handle missing approvals directory."""
        dashboard.approval_storage_dir = Path("/nonexistent")

        result = dashboard._load_pending_approvals()

        assert result == []


# =============================================================================
# Data Loading Tests - Tasks
# =============================================================================


class TestLoadTasksFromAPI:
    """Tests for _load_tasks_from_api method."""

    def test_load_no_api_url(self, dashboard: ForgeDashboard) -> None:
        """Should return empty list when no API URL."""
        dashboard.api_url = None

        result = dashboard._load_tasks_from_api()

        assert result == []

    @pytest.mark.asyncio
    async def test_load_tasks_success(self, dashboard: ForgeDashboard) -> None:
        """Should load tasks from API."""
        dashboard.api_url = "http://localhost:8080"
        dashboard.api_token = "test-token"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "tasks": [
                    {"id": "task-1", "status": "pending"},
                    {"id": "task-2", "status": "assigned"},
                ]
            }
        }

        with patch("httpx.get", return_value=mock_response) as mock_get:
            result = dashboard._load_tasks_from_api()

            assert len(result) == 2
            mock_get.assert_called_once()
            # Check auth header
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer test-token"

    def test_load_tasks_api_error(self, dashboard: ForgeDashboard) -> None:
        """Should handle API errors gracefully."""
        dashboard.api_url = "http://localhost:8080"

        with patch("httpx.get", side_effect=Exception("Connection failed")):
            result = dashboard._load_tasks_from_api()

            assert result == []


# =============================================================================
# Data Loading Tests - Agents
# =============================================================================


class TestLoadAgentsFromAPI:
    """Tests for _load_agents_from_api method."""

    def test_load_no_api(self, dashboard: ForgeDashboard) -> None:
        """Should attempt to load from tmux when no API."""
        dashboard.api_url = None

        with patch("forge_harness.dashboard.logger"):
            result = dashboard._load_agents_from_api()

            assert isinstance(result, list)

    def test_load_with_api_client(self, dashboard: ForgeDashboard) -> None:
        """Should load agents from API client."""
        dashboard.api_url = "http://localhost:8080"

        mock_agent = MagicMock()
        mock_agent.id = "agent-1"
        mock_agent.role = "dev"
        mock_agent.project = "test-project"
        mock_agent.task = "Test task"
        mock_agent.status.value = "active"
        mock_agent.progress = 50
        mock_agent.last_activity = datetime.now(UTC)
        mock_agent.is_stale = False

        mock_client = MagicMock()
        mock_client.sync_list_agents.return_value = [mock_agent]
        dashboard._api_client = mock_client

        # Mock session tracker to avoid loading real tmux sessions
        mock_tracker = MagicMock()
        mock_tracker.get_all_sessions.return_value = []

        with patch("forge_harness.dashboard.logger"), \
             patch("forge_harness.session_tracker.get_session_tracker", return_value=mock_tracker):
            result = dashboard._load_agents_from_api()

            assert len(result) == 1
            assert result[0].id == "agent-1"
            assert result[0].role == "dev"


# =============================================================================
# Data Loading Tests - Portfolio
# =============================================================================


class TestLoadPortfolioFromAPI:
    """Tests for _load_portfolio_from_api method."""

    def test_load_no_api_url(self, dashboard: ForgeDashboard) -> None:
        """Should return None when no API URL."""
        dashboard.api_url = None

        result = dashboard._load_portfolio_from_api()

        assert result is None

    def test_load_portfolio_success(self, dashboard: ForgeDashboard) -> None:
        """Should load portfolio from API."""
        dashboard.api_url = "http://localhost:8080"
        dashboard.api_token = "token"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "domains": [{"id": "domain-1", "project_count": 3}],
                "total_projects": 3,
            }
        }

        with patch("httpx.get", return_value=mock_response):
            result = dashboard._load_portfolio_from_api()

            assert result is not None
            assert result["total_projects"] == 3

    def test_load_portfolio_error(self, dashboard: ForgeDashboard) -> None:
        """Should handle API errors gracefully."""
        dashboard.api_url = "http://localhost:8080"

        with patch("httpx.get", side_effect=Exception("Connection failed")):
            result = dashboard._load_portfolio_from_api()

            assert result is None


# =============================================================================
# Data Loading Tests - Patterns
# =============================================================================


class TestLoadPatternsFromAPI:
    """Tests for _load_patterns_from_api method."""

    def test_load_no_api_url(self, dashboard: ForgeDashboard) -> None:
        """Should return empty list when no API URL."""
        dashboard.api_url = None

        result = dashboard._load_patterns_from_api()

        assert result == []

    def test_load_patterns_success(self, dashboard: ForgeDashboard) -> None:
        """Should load patterns from API."""
        dashboard.api_url = "http://localhost:8080"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "patterns": [
                    {"name": "pattern-1", "category": "auth", "uses": 10},
                ]
            }
        }

        with patch("httpx.get", return_value=mock_response):
            result = dashboard._load_patterns_from_api()

            assert len(result) == 1
            assert result[0]["name"] == "pattern-1"

    def test_load_patterns_error(self, dashboard: ForgeDashboard) -> None:
        """Should handle API errors gracefully."""
        dashboard.api_url = "http://localhost:8080"

        with patch("httpx.get", side_effect=Exception("Failed")):
            result = dashboard._load_patterns_from_api()

            assert result == []


# =============================================================================
# Rendering Tests
# =============================================================================


class TestRendering:
    """Tests for rendering methods."""

    def test_render_header(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render header panel."""
        with patch("forge_harness.dashboard.Panel") as mock_panel:
            mock_panel.return_value = MagicMock()
            result = dashboard.render_header()

            mock_panel.assert_called_once()
            assert result is not None

    def test_render_pipelines_empty(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render empty pipelines panel."""
        with patch("forge_harness.dashboard.Panel") as mock_panel, \
             patch.object(dashboard, "_load_active_pipelines", return_value=[]):
            mock_panel.return_value = MagicMock()
            result = dashboard.render_pipelines()

            assert result is not None

    def test_render_approvals_empty(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render empty approvals panel."""
        with patch("forge_harness.dashboard.Panel") as mock_panel, \
             patch.object(dashboard, "_load_pending_approvals", return_value=[]):
            mock_panel.return_value = MagicMock()
            result = dashboard.render_approvals()

            assert result is not None

    def test_render_agents_no_api(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render agents panel when no API."""
        dashboard.api_url = None

        with patch("forge_harness.dashboard.Panel") as mock_panel:
            mock_panel.return_value = MagicMock()
            result = dashboard.render_agents()

            assert result is not None

    def test_render_ralph_idle(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render Ralph status when idle."""
        with patch("forge_harness.dashboard.Panel") as mock_panel, \
             patch.object(dashboard, "_load_ralph_status") as mock_load:
            mock_load.return_value = MagicMock(
                active=False,
                state="idle",
                current_feature=None,
                iteration=0,
                max_iterations=100,
                failure_count=0,
                started_at=None,
            )
            mock_panel.return_value = MagicMock()
            result = dashboard.render_ralph_status()

            assert result is not None

    def test_render_ralph_active(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render Ralph status when active."""
        with patch("forge_harness.dashboard.Panel") as mock_panel, \
             patch.object(dashboard, "_load_ralph_status") as mock_load:
            mock_load.return_value = MagicMock(
                active=True,
                state="running",
                current_feature="feature-1",
                iteration=5,
                max_iterations=100,
                failure_count=0,
                started_at=datetime.now(UTC),
            )
            mock_panel.return_value = MagicMock()
            result = dashboard.render_ralph_status()

            assert result is not None

    def test_render_mvp_empty(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render empty MVP status panel."""
        with patch("forge_harness.dashboard.Panel") as mock_panel, \
             patch.object(dashboard, "_load_mvp_status", return_value=[]):
            mock_panel.return_value = MagicMock()
            result = dashboard.render_mvp_status()

            assert result is not None

    def test_render_tasks_no_api(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render tasks panel when no API."""
        dashboard.api_url = None

        with patch("forge_harness.dashboard.Panel") as mock_panel:
            mock_panel.return_value = MagicMock()
            result = dashboard.render_tasks()

            assert result is not None

    def test_render_portfolio_no_api(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render portfolio panel when no API."""
        dashboard.api_url = None

        with patch("forge_harness.dashboard.Panel") as mock_panel:
            mock_panel.return_value = MagicMock()
            result = dashboard.render_portfolio()

            assert result is not None

    def test_render_patterns_no_api(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render patterns panel when no API."""
        dashboard.api_url = None

        with patch("forge_harness.dashboard.Panel") as mock_panel:
            mock_panel.return_value = MagicMock()
            result = dashboard.render_patterns()

            assert result is not None

    def test_render_errors_empty(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render empty errors panel."""
        with patch("forge_harness.dashboard.Panel") as mock_panel:
            mock_panel.return_value = MagicMock()
            result = dashboard.render_errors()

            assert result is not None

    def test_render_help_modal(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render help modal panel."""
        with patch("forge_harness.dashboard.Panel") as mock_panel:
            mock_panel.return_value = MagicMock()
            result = dashboard.render_help_modal()

            mock_panel.assert_called_once()
            assert result is not None

    def test_render_health_status_error(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render health status with error."""
        dashboard._health_check_error = "API error"

        with patch("forge_harness.dashboard.Panel") as mock_panel:
            mock_panel.return_value = MagicMock()
            result = dashboard.render_health_status()

            assert result is not None


# =============================================================================
# Progress Bar Tests
# =============================================================================


class TestProgressBar:
    """Tests for progress bar rendering."""

    def test_render_progress_bar_0(self, dashboard: ForgeDashboard) -> None:
        """Should render empty progress bar."""
        result = dashboard._render_progress_bar(0)

        assert "░" in result
        assert "█" not in result

    def test_render_progress_bar_50(self, dashboard: ForgeDashboard) -> None:
        """Should render half-filled progress bar."""
        result = dashboard._render_progress_bar(50)

        assert result.count("█") == 5
        assert result.count("░") == 5

    def test_render_progress_bar_100(self, dashboard: ForgeDashboard) -> None:
        """Should render full progress bar."""
        result = dashboard._render_progress_bar(100)

        assert "░" not in result
        assert result.count("█") == 10


# =============================================================================
# Layout Tests
# =============================================================================


class TestLayout:
    """Tests for layout creation and rendering."""

    def test_create_layout(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should create dashboard layout structure."""
        mock_layout = mock_rich_components["layout_instance"]

        result = dashboard.create_layout()

        assert result is not None
        # Should split into header, main, footer
        mock_layout.split.assert_called()

    def test_render_layout(self, dashboard: ForgeDashboard, mock_rich_components) -> None:
        """Should render full dashboard layout."""
        with patch.object(dashboard, "render_header"), \
             patch.object(dashboard, "render_pipelines"), \
             patch.object(dashboard, "render_tasks"), \
             patch.object(dashboard, "render_patterns"), \
             patch.object(dashboard, "render_errors"), \
             patch.object(dashboard, "render_health_status"), \
             patch.object(dashboard, "render_approvals"), \
             patch.object(dashboard, "render_agents"), \
             patch.object(dashboard, "render_portfolio"), \
             patch.object(dashboard, "render_mvp_status"):

            result = dashboard.render_layout()

            assert result is not None


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestCreateDashboard:
    """Tests for create_dashboard factory function."""

    def test_create_dashboard(self, tmp_path: Path, mock_rich_components) -> None:
        """Should create dashboard with factory function."""
        with patch("forge_harness.dashboard.logger"):
            dashboard = create_dashboard(
                forge_root=tmp_path,
                api_url="http://localhost:8080",
                api_token="token",
            )

            assert isinstance(dashboard, ForgeDashboard)
            assert dashboard.api_url == "http://localhost:8080"


# =============================================================================
# Sound Alert Tests
# =============================================================================


class TestSoundAlerts:
    """Tests for sound alert functionality."""

    def test_play_alert_sound_enabled(self, dashboard: ForgeDashboard) -> None:
        """Should play sound when enabled."""
        dashboard._sound_alerts_enabled = True

        with patch("forge_harness.dashboard.print") as mock_print:
            dashboard._play_alert_sound()

            mock_print.assert_called_once()

    def test_play_alert_sound_disabled(self, dashboard: ForgeDashboard) -> None:
        """Should not play sound when disabled."""
        dashboard._sound_alerts_enabled = False

        with patch("forge_harness.dashboard.print") as mock_print:
            dashboard._play_alert_sound()

            mock_print.assert_not_called()

    def test_check_new_approvals_triggers_sound(self, dashboard: ForgeDashboard) -> None:
        """Should play sound on new approvals."""
        dashboard._sound_alerts_enabled = True
        dashboard._last_approval_count = 1

        mock_approvals = [MagicMock(), MagicMock()]  # 2 approvals now

        with patch.object(dashboard, "_play_alert_sound") as mock_play:
            dashboard._check_new_approvals(mock_approvals)

            mock_play.assert_called_once()
            assert dashboard._last_approval_count == 2


# =============================================================================
# API Client Tests
# =============================================================================


class TestGetAPIClient:
    """Tests for _get_api_client method."""

    def test_get_client_creates_new(self, dashboard: ForgeDashboard) -> None:
        """Should create new client when none exists."""
        dashboard.api_url = "http://localhost:8080"
        dashboard.api_token = "token"
        dashboard._api_client = None

        mock_client_class = MagicMock()
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # CommandCenterClient is imported locally inside _get_api_client
        with patch("forge_harness.command_center_client.CommandCenterClient", mock_client_class):
            result = dashboard._get_api_client()

            assert result is mock_client
            mock_client_class.assert_called_once_with(
                base_url="http://localhost:8080",
                token="token",
            )

    def test_get_client_returns_existing(self, dashboard: ForgeDashboard) -> None:
        """Should return existing client."""
        existing = MagicMock()
        dashboard._api_client = existing

        result = dashboard._get_api_client()

        assert result is existing

    def test_get_client_no_url(self, dashboard: ForgeDashboard) -> None:
        """Should return None when no API URL."""
        dashboard.api_url = None
        dashboard._api_client = None

        result = dashboard._get_api_client()

        assert result is None


# =============================================================================
# Approval API Tests
# =============================================================================


class TestLoadApprovalsFromAPI:
    """Tests for _load_pending_approvals_from_api method."""

    def test_load_no_api_url(self, dashboard: ForgeDashboard) -> None:
        """Should return None when no API URL."""
        dashboard.api_url = None

        result = dashboard._load_pending_approvals_from_api()

        assert result is None

    def test_load_success(self, dashboard: ForgeDashboard) -> None:
        """Should load approvals from API."""
        dashboard.api_url = "http://localhost:8080"
        dashboard.api_token = "token"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "approvals": [
                    {
                        "id": "approval-1",
                        "type": "deployment",
                        "title": "Test",
                        "domain": "test",
                        "priority": "high",
                        "tier": "DESKTOP",
                        "risk_score": 0.8,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                ]
            }
        }

        with patch("httpx.get", return_value=mock_response):
            result = dashboard._load_pending_approvals_from_api()

            assert result is not None
            assert len(result) == 1
            assert result[0].id == "approval-1"

    def test_load_api_error(self, dashboard: ForgeDashboard) -> None:
        """Should return None on API error."""
        dashboard.api_url = "http://localhost:8080"

        with patch("httpx.get", side_effect=Exception("Failed")):
            result = dashboard._load_pending_approvals_from_api()

            assert result is None


# =============================================================================
# Snapshot Tests
# =============================================================================


class TestSnapshot:
    """Tests for snapshot method."""

    def test_snapshot_prints_all_panels(self, dashboard: ForgeDashboard) -> None:
        """Should print all dashboard panels."""
        with patch.object(dashboard, "render_header") as mock_header, \
             patch.object(dashboard, "render_pipelines") as mock_pipelines, \
             patch.object(dashboard, "render_approvals") as mock_approvals, \
             patch.object(dashboard, "render_agents") as mock_agents, \
             patch.object(dashboard, "render_portfolio") as mock_portfolio, \
             patch.object(dashboard, "render_patterns") as mock_patterns, \
             patch.object(dashboard, "render_mvp_status") as mock_mvp, \
             patch.object(dashboard, "render_errors") as mock_errors, \
             patch.object(dashboard, "render_health_status") as mock_health:

            dashboard.snapshot()

            mock_header.assert_called_once()
            mock_pipelines.assert_called_once()
            mock_approvals.assert_called_once()
            mock_agents.assert_called_once()
            mock_portfolio.assert_called_once()
            mock_patterns.assert_called_once()
            mock_mvp.assert_called_once()
            mock_errors.assert_called_once()
            mock_health.assert_called_once()
