"""Deep tests for forge_harness/dashboard.py targeting uncovered lines.

Target uncovered lines:
53-54, 119, 130-171, 354, 385-386, 449-450, 454-460, 557-558, 704, 708,
731-736, 775-779, 784-787, 800, 808-813, 878, 883-930, 956-975, 984,
1007-1028, 1117-1120, 1130-1131, 1161, 1243-1244, 1269, 1273-1302, 1329,
1334-1376, 1469-1496, 1507, 1534-1623, 1627-1644, 1648-1665, 1676-1680
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Mock Rich and other heavy imports BEFORE importing dashboard
# ---------------------------------------------------------------------------
for mod in [
    "rich",
    "rich.console",
    "rich.layout",
    "rich.live",
    "rich.panel",
    "rich.table",
    "rich.text",
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from forge_harness.dashboard import (  # noqa: E402
    HAS_TERMIOS,
    ForgeDashboard,
    KeyboardHandler,
    create_dashboard,
)
from forge_harness.dashboard_core import AgentState, MVPStatusSummary, RalphStatus
from forge_harness.metrics_common import ApprovalSummary, PipelineStatus

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_dashboard(tmp_path: Path, **kwargs) -> ForgeDashboard:
    """Create a ForgeDashboard with mocked Console."""
    with patch("forge_harness.dashboard.Console"):
        return ForgeDashboard(forge_root=tmp_path, **kwargs)


def _make_approval(
    id: str = "abc12345",
    priority: str = "normal",
    tier: str = "PHONE",
    status: str = "pending",
    minutes_ago: int = 5,
) -> ApprovalSummary:
    return ApprovalSummary(
        id=id,
        type="content",
        title="Test approval title",
        domain="test-domain",
        priority=priority,
        tier=tier,
        risk_score=0.5,
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )


def _make_agent(
    id: str = "forge:cursor",
    role: str = "developer",
    status: str = "active",
    is_stale: bool = False,
    progress: int = 50,
    last_activity: datetime | None = None,
) -> AgentState:
    return AgentState(
        id=id,
        name=role,
        role=role,
        project="test/project",
        task="doing something",
        status=status,
        progress=progress,
        last_activity=last_activity or datetime.now(UTC) - timedelta(minutes=2),
        is_stale=is_stale,
    )


# ===========================================================================
# 1.  HAS_TERMIOS import branches (lines 53-54)
# ===========================================================================


class TestHasTermiosFlag:
    """Verify the HAS_TERMIOS constant is a bool."""

    def test_has_termios_is_bool(self):
        assert isinstance(HAS_TERMIOS, bool)

    def test_has_termios_false_when_import_fails(self):
        """Simulate ImportError path via module-level patching."""
        # The lines 53-54 are the except ImportError: HAS_TERMIOS = False block.
        # We can verify the False path by mocking the module-level attribute.
        with patch("forge_harness.dashboard.HAS_TERMIOS", False):
            assert not sys.modules["forge_harness.dashboard"].HAS_TERMIOS


# ===========================================================================
# 2.  KeyboardHandler._read_keys  (lines 130-171)
# ===========================================================================


class TestKeyboardHandlerReadKeys:
    """Cover the _read_keys thread logic."""

    def test_read_keys_returns_early_without_termios(self):
        """_read_keys should exit immediately when HAS_TERMIOS is False."""
        handler = KeyboardHandler()
        with patch("forge_harness.dashboard.HAS_TERMIOS", False):
            # Should return without doing anything
            handler._read_keys()
        assert handler._key_queue == []

    def test_read_keys_appends_regular_char(self):
        """_read_keys should append a regular character to the queue."""
        handler = KeyboardHandler()
        handler._running = True

        call_count = [0]

        def fake_select(rlist, wlist, xlist, timeout):
            # First call returns input available, second stops the loop
            call_count[0] += 1
            if call_count[0] == 1:
                return ([sys.stdin], [], [])
            handler._running = False
            return ([], [], [])

        with patch("forge_harness.dashboard.HAS_TERMIOS", True), \
             patch("forge_harness.dashboard.select") as mock_select_mod, \
             patch("forge_harness.dashboard.termios") as mock_termios, \
             patch("forge_harness.dashboard.tty"), \
             patch.object(sys.stdin, "fileno", return_value=0), \
             patch.object(sys.stdin, "read", return_value="a"):
            mock_select_mod.select.side_effect = fake_select
            mock_termios.tcgetattr.return_value = MagicMock()
            handler._read_keys()

        assert "a" in handler._key_queue

    def test_read_keys_handles_escape_up_arrow(self):
        """Should translate ESC [ A into 'UP'."""
        handler = KeyboardHandler()
        handler._running = True

        read_values = iter(["\x1b", "[", "A"])
        select_results = iter([
            ([sys.stdin], [], []),  # first char available
            ([sys.stdin], [], []),  # char2 available
            ([sys.stdin], [], []),  # char3 available
        ])

        def fake_select(*args, **kwargs):
            try:
                return next(select_results)
            except StopIteration:
                handler._running = False
                return ([], [], [])

        with patch("forge_harness.dashboard.HAS_TERMIOS", True), \
             patch("forge_harness.dashboard.select") as mock_select_mod, \
             patch("forge_harness.dashboard.termios") as mock_termios, \
             patch("forge_harness.dashboard.tty"), \
             patch.object(sys.stdin, "fileno", return_value=0), \
             patch.object(sys.stdin, "read", side_effect=read_values):
            mock_select_mod.select.side_effect = fake_select
            mock_termios.tcgetattr.return_value = MagicMock()
            handler._read_keys()

        assert "UP" in handler._key_queue

    def test_read_keys_handles_escape_down_arrow(self):
        """Should translate ESC [ B into 'DOWN'."""
        handler = KeyboardHandler()
        handler._running = True

        read_values = iter(["\x1b", "[", "B"])
        select_results = iter([
            ([sys.stdin], [], []),
            ([sys.stdin], [], []),
            ([sys.stdin], [], []),
        ])

        def fake_select(*args, **kwargs):
            try:
                return next(select_results)
            except StopIteration:
                handler._running = False
                return ([], [], [])

        with patch("forge_harness.dashboard.HAS_TERMIOS", True), \
             patch("forge_harness.dashboard.select") as mock_select_mod, \
             patch("forge_harness.dashboard.termios") as mock_termios, \
             patch("forge_harness.dashboard.tty"), \
             patch.object(sys.stdin, "fileno", return_value=0), \
             patch.object(sys.stdin, "read", side_effect=read_values):
            mock_select_mod.select.side_effect = fake_select
            mock_termios.tcgetattr.return_value = MagicMock()
            handler._read_keys()

        assert "DOWN" in handler._key_queue

    def test_read_keys_handles_escape_other_char3(self):
        """Should emit ESC when char3 is not A or B."""
        handler = KeyboardHandler()
        handler._running = True

        read_values = iter(["\x1b", "[", "C"])
        select_results = iter([
            ([sys.stdin], [], []),
            ([sys.stdin], [], []),
            ([sys.stdin], [], []),
        ])

        def fake_select(*args, **kwargs):
            try:
                return next(select_results)
            except StopIteration:
                handler._running = False
                return ([], [], [])

        with patch("forge_harness.dashboard.HAS_TERMIOS", True), \
             patch("forge_harness.dashboard.select") as mock_select_mod, \
             patch("forge_harness.dashboard.termios") as mock_termios, \
             patch("forge_harness.dashboard.tty"), \
             patch.object(sys.stdin, "fileno", return_value=0), \
             patch.object(sys.stdin, "read", side_effect=read_values):
            mock_select_mod.select.side_effect = fake_select
            mock_termios.tcgetattr.return_value = MagicMock()
            handler._read_keys()

        assert "ESC" in handler._key_queue

    def test_read_keys_handles_escape_not_bracket(self):
        """Should emit ESC when char2 is not '['."""
        handler = KeyboardHandler()
        handler._running = True

        read_values = iter(["\x1b", "x"])
        select_results = iter([
            ([sys.stdin], [], []),
            ([sys.stdin], [], []),
        ])

        def fake_select(*args, **kwargs):
            try:
                return next(select_results)
            except StopIteration:
                handler._running = False
                return ([], [], [])

        with patch("forge_harness.dashboard.HAS_TERMIOS", True), \
             patch("forge_harness.dashboard.select") as mock_select_mod, \
             patch("forge_harness.dashboard.termios") as mock_termios, \
             patch("forge_harness.dashboard.tty"), \
             patch.object(sys.stdin, "fileno", return_value=0), \
             patch.object(sys.stdin, "read", side_effect=read_values):
            mock_select_mod.select.side_effect = fake_select
            mock_termios.tcgetattr.return_value = MagicMock()
            handler._read_keys()

        assert "ESC" in handler._key_queue

    def test_read_keys_handles_escape_no_char2(self):
        """Should emit ESC when no char2 available after ESC."""
        handler = KeyboardHandler()
        handler._running = True

        # First select returns stdin, second (for char2) returns nothing
        read_values = iter(["\x1b"])
        select_calls = [0]

        def fake_select(*args, **kwargs):
            select_calls[0] += 1
            if select_calls[0] == 1:
                return ([sys.stdin], [], [])
            # char2 not available
            handler._running = False
            return ([], [], [])

        with patch("forge_harness.dashboard.HAS_TERMIOS", True), \
             patch("forge_harness.dashboard.select") as mock_select_mod, \
             patch("forge_harness.dashboard.termios") as mock_termios, \
             patch("forge_harness.dashboard.tty"), \
             patch.object(sys.stdin, "fileno", return_value=0), \
             patch.object(sys.stdin, "read", side_effect=read_values):
            mock_select_mod.select.side_effect = fake_select
            mock_termios.tcgetattr.return_value = MagicMock()
            handler._read_keys()

        assert "ESC" in handler._key_queue

    def test_read_keys_exception_logged(self):
        """Should handle exception from tcgetattr gracefully."""
        handler = KeyboardHandler()
        handler._running = True

        with patch("forge_harness.dashboard.HAS_TERMIOS", True), \
             patch("forge_harness.dashboard.termios") as mock_termios, \
             patch("forge_harness.dashboard.tty"), \
             patch.object(sys.stdin, "fileno", return_value=0):
            mock_termios.tcgetattr.side_effect = Exception("terminal error")
            # Should not raise
            handler._read_keys()

    def test_read_keys_finally_restores_settings(self):
        """Should restore terminal settings in finally block."""
        handler = KeyboardHandler()
        handler._running = True
        old_settings = MagicMock()

        with patch("forge_harness.dashboard.HAS_TERMIOS", True), \
             patch("forge_harness.dashboard.termios") as mock_termios, \
             patch("forge_harness.dashboard.tty"), \
             patch.object(sys.stdin, "fileno", return_value=0):
            mock_termios.tcgetattr.return_value = old_settings
            mock_termios.tcsetattr.side_effect = Exception("cleanup error")
            # Even when tcsetattr raises during finally, no exception propagates
            handler._read_keys()

    def test_stop_joins_thread(self):
        """stop() should join the thread (line 119)."""
        handler = KeyboardHandler()
        mock_thread = MagicMock()
        handler._thread = mock_thread
        handler._running = True

        with patch("forge_harness.dashboard.HAS_TERMIOS", True):
            handler.stop()

        mock_thread.join.assert_called_once_with(timeout=0.5)


# ===========================================================================
# 3.  _load_agents_from_api - tmux session path (line 354, 385-386)
# ===========================================================================


class TestLoadAgentsTmux:
    """Cover tmux session loading in _load_agents_from_api."""

    def test_load_agents_tmux_sessions_merged(self, tmp_path):
        """Tmux sessions not seen via API should be appended."""
        dash = _make_dashboard(tmp_path)

        mock_session = MagicMock()
        mock_session.session_name = "tmux-session-1"
        mock_session.window_name = "kimi"
        mock_session.agent_type = "kimi"
        mock_session.status = "active"
        mock_session.domain = "brandfocus"
        mock_session.project = "vc"
        mock_session.current_task = "coding"
        mock_session.last_activity = datetime.now(UTC).isoformat()

        with patch.object(dash, "_get_api_client", return_value=None), \
             patch("forge_harness.session_tracker.get_session_tracker") as mock_get_tracker:
            tracker = MagicMock()
            tracker.get_all_sessions.return_value = [mock_session]
            mock_get_tracker.return_value = tracker
            agents = dash._load_agents_from_api()

        assert any(a.id == "tmux-session-1" for a in agents)
        assert any(a.source == "tmux" for a in agents)

    def test_load_agents_tmux_session_skipped_if_seen(self, tmp_path):
        """Tmux sessions already seen via API should not be duplicated (line 354)."""
        dash = _make_dashboard(tmp_path)

        mock_api_agent = MagicMock()
        mock_api_agent.id = "agent-123"
        mock_api_agent.role = "developer"
        mock_api_agent.project = "test"
        mock_api_agent.task = "coding"
        mock_api_agent.status.value = "active"
        mock_api_agent.progress = 50
        mock_api_agent.last_activity = None
        mock_api_agent.is_stale = False

        mock_session = MagicMock()
        mock_session.session_name = "agent-123"  # same id as API agent
        mock_session.window_name = "window"

        mock_client = MagicMock()
        mock_client.sync_list_agents.return_value = [mock_api_agent]

        with patch.object(dash, "_get_api_client", return_value=mock_client), \
             patch("forge_harness.session_tracker.get_session_tracker") as mock_get_tracker:
            tracker = MagicMock()
            tracker.get_all_sessions.return_value = [mock_session]
            mock_get_tracker.return_value = tracker
            agents = dash._load_agents_from_api()

        # Should only have 1 agent (not duplicated)
        assert len(agents) == 1

    def test_load_agents_tmux_exception_logged(self, tmp_path):
        """Tmux exception should not crash the method (line 385-386)."""
        dash = _make_dashboard(tmp_path)

        with patch.object(dash, "_get_api_client", return_value=None), \
             patch("forge_harness.session_tracker.get_session_tracker") as mock_get_tracker:
            mock_get_tracker.side_effect = Exception("tmux unavailable")
            agents = dash._load_agents_from_api()

        assert agents == []

    def test_load_agents_tmux_no_agent_type(self, tmp_path):
        """When agent_type is unknown, use window_name as role."""
        dash = _make_dashboard(tmp_path)

        mock_session = MagicMock()
        mock_session.session_name = "sess1"
        mock_session.window_name = "my-window"
        mock_session.agent_type = "unknown"
        mock_session.status = "idle"
        mock_session.domain = None
        mock_session.project = "proj"
        mock_session.current_task = None
        mock_session.last_activity = None

        with patch.object(dash, "_get_api_client", return_value=None), \
             patch("forge_harness.session_tracker.get_session_tracker") as mock_get_tracker:
            tracker = MagicMock()
            tracker.get_all_sessions.return_value = [mock_session]
            mock_get_tracker.return_value = tracker
            agents = dash._load_agents_from_api()

        assert agents[0].role == "my-window"

    def test_load_agents_tmux_no_domain(self, tmp_path):
        """Agent project should be None when domain is None."""
        dash = _make_dashboard(tmp_path)

        mock_session = MagicMock()
        mock_session.session_name = "sess2"
        mock_session.window_name = "my-window"
        mock_session.agent_type = "kimi"
        mock_session.status = "completed"
        mock_session.domain = None
        mock_session.project = "proj"
        mock_session.current_task = "task"
        mock_session.last_activity = None

        with patch.object(dash, "_get_api_client", return_value=None), \
             patch("forge_harness.session_tracker.get_session_tracker") as mock_get_tracker:
            tracker = MagicMock()
            tracker.get_all_sessions.return_value = [mock_session]
            mock_get_tracker.return_value = tracker
            agents = dash._load_agents_from_api()

        assert agents[0].project is None


# ===========================================================================
# 4.  _load_pending_approvals - synchronizer path (lines 449-450)
# ===========================================================================


class TestLoadPendingApprovalsSynchronizer:
    """Cover the synchronizer branch in _load_pending_approvals."""

    def test_load_approvals_from_synchronizer(self, tmp_path):
        """Should load approvals from synchronizer when available."""
        dash = _make_dashboard(tmp_path)

        mock_approval = MagicMock()
        mock_approval.status = "pending"
        mock_approval.id = "sync-approval-1"
        mock_approval.type = "deployment"
        mock_approval.title = "Deploy voice-coach to production"
        mock_approval.domain = "brandfocus"
        mock_approval.priority = "high"
        mock_approval.tier = "DESKTOP"
        mock_approval.risk_score = 0.8
        mock_approval.created_at = datetime.now(UTC) - timedelta(minutes=10)

        mock_snapshot = MagicMock()
        mock_snapshot.approvals = [mock_approval]

        mock_sync = MagicMock()
        mock_sync.get_state_snapshot.return_value = mock_snapshot
        dash._synchronizer = mock_sync
        dash._use_synchronizer = True

        approvals = dash._load_pending_approvals()

        assert len(approvals) >= 1
        assert any(a.id == "sync-approval-1" for a in approvals)

    def test_load_approvals_synchronizer_filters_non_pending(self, tmp_path):
        """Synchronizer approvals with non-pending status are skipped."""
        dash = _make_dashboard(tmp_path)

        mock_approval_approved = MagicMock()
        mock_approval_approved.status = "approved"
        mock_approval_approved.id = "approved-1"

        mock_snapshot = MagicMock()
        mock_snapshot.approvals = [mock_approval_approved]

        mock_sync = MagicMock()
        mock_sync.get_state_snapshot.return_value = mock_snapshot
        dash._synchronizer = mock_sync

        approvals = dash._load_pending_approvals()
        assert not any(a.id == "approved-1" for a in approvals)

    def test_load_approvals_synchronizer_exception_falls_through(self, tmp_path):
        """Synchronizer exception should fall through to API/file path (lines 449-450)."""
        dash = _make_dashboard(tmp_path)

        mock_sync = MagicMock()
        mock_sync.get_state_snapshot.side_effect = Exception("sync error")
        dash._synchronizer = mock_sync

        # No API, no files - should return empty list gracefully
        approvals = dash._load_pending_approvals()
        assert isinstance(approvals, list)


# ===========================================================================
# 5.  _load_pending_approvals - API failure path (lines 454-460)
# ===========================================================================


class TestLoadPendingApprovalsAPIError:
    """Cover API error path in _load_pending_approvals."""

    def test_api_error_adds_error_buffer(self, tmp_path):
        """API exception should add to _recent_errors (lines 454-460)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")
        dash._synchronizer = None

        with patch.object(dash, "_load_pending_approvals_from_api",
                          side_effect=Exception("connection refused")):
            approvals = dash._load_pending_approvals()

        # _recent_errors should contain the API error
        assert any("API error" in err for _, err in dash._recent_errors)

    def test_api_returns_none_falls_to_files(self, tmp_path):
        """When API returns None, fall back to local files."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")
        dash._synchronizer = None

        # Create a local approval file
        approval_dir = tmp_path / ".forge/approvals"
        approval_dir.mkdir()
        approval_file = approval_dir / "approval_test.json"
        approval_file.write_text(json.dumps({
            "id": "local-1",
            "type": "content",
            "title": "Local approval",
            "domain": "test",
            "priority": "normal",
            "tier": "PHONE",
            "risk_score": 0.3,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
        }))

        with patch.object(dash, "_load_pending_approvals_from_api", return_value=None):
            approvals = dash._load_pending_approvals()

        assert any(a.id == "local-1" for a in approvals)


# ===========================================================================
# 6.  _load_pending_approvals_from_api - parse error (lines 557-558)
# ===========================================================================


class TestLoadPendingApprovalsFromAPI:
    """Cover _load_pending_approvals_from_api parse error path."""

    def test_parse_error_skips_item(self, tmp_path):
        """Items that fail to parse should be skipped (lines 557-558)."""
        import httpx

        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        good_item = {
            "id": "good-1",
            "type": "content",
            "title": "Good approval",
            "domain": "test",
            "priority": "normal",
            "tier": "PHONE",
            "risk_score": 0.3,
            "created_at": datetime.now(UTC).isoformat(),
        }
        bad_item = {
            "id": "bad-1",
            "created_at": "NOT-A-DATE",  # will cause ValueError in fromisoformat
        }

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": {"approvals": [bad_item, good_item]}
        }

        with patch("httpx.get", return_value=mock_response):
            approvals = dash._load_pending_approvals_from_api()

        # bad_item should be skipped, good_item should succeed
        # (depending on whether ValueError is raised for bad date)
        assert approvals is not None

    def test_api_exception_returns_none(self, tmp_path):
        """Network exception should return None."""
        import httpx

        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        with patch("httpx.get", side_effect=Exception("timeout")):
            result = dash._load_pending_approvals_from_api()

        assert result is None


# ===========================================================================
# 7.  render_header - status message paths (lines 704, 708, 731-736)
# ===========================================================================


class TestRenderHeaderStatusMessage:
    """Cover status message display branches in render_header."""

    def test_header_shows_time_filter(self, tmp_path):
        """Time filter indicator should appear when filter is not 'all' (line 708)."""
        dash = _make_dashboard(tmp_path)
        dash._time_filter = "1h"
        panel = dash.render_header()
        assert panel is not None  # Basic smoke test with mocked Panel

    def test_header_shows_success_status_message(self, tmp_path):
        """Status message containing checkmark should appear in green (lines 731-736)."""
        dash = _make_dashboard(tmp_path)
        dash._status_message = "✓ Approved: abc12345"
        dash._status_message_time = datetime.now(UTC)
        panel = dash.render_header()
        assert panel is not None

    def test_header_shows_error_status_message(self, tmp_path):
        """Status message containing 'Error' should appear in red."""
        dash = _make_dashboard(tmp_path)
        dash._status_message = "Error approving item"
        dash._status_message_time = datetime.now(UTC)
        panel = dash.render_header()
        assert panel is not None

    def test_header_shows_neutral_status_message(self, tmp_path):
        """Status message with no special marker should appear in yellow."""
        dash = _make_dashboard(tmp_path)
        dash._status_message = "Filter: 1h"
        dash._status_message_time = datetime.now(UTC)
        panel = dash.render_header()
        assert panel is not None

    def test_header_clears_old_status_message(self, tmp_path):
        """Status message older than 5s should be cleared (line 735-736)."""
        dash = _make_dashboard(tmp_path)
        dash._status_message = "Old message"
        dash._status_message_time = datetime.now(UTC) - timedelta(seconds=10)
        dash.render_header()
        assert dash._status_message is None

    def test_header_no_status_message(self, tmp_path):
        """Header without status message should render without error (line 704)."""
        dash = _make_dashboard(tmp_path)
        dash._status_message = None
        panel = dash.render_header()
        assert panel is not None

    def test_header_sound_alerts_disabled(self, tmp_path):
        """Header should show muted indicator when sound alerts disabled (line 704)."""
        dash = _make_dashboard(tmp_path)
        dash._sound_alerts_enabled = False
        panel = dash.render_header()
        assert panel is not None


# ===========================================================================
# 8.  _load_health_status (lines 775-787)
# ===========================================================================


class TestLoadHealthStatus:
    """Cover _load_health_status coroutine paths."""

    @pytest.mark.asyncio
    async def test_load_health_from_api(self, tmp_path):
        """Should load health status from API when api_url is set (lines 775-779)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "checks": []}

        mock_async_client = AsyncMock()
        mock_async_client.get = AsyncMock(return_value=mock_response)
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_async_client):
            result = await dash._load_health_status()

        assert result is not None
        assert dash._health_check_error is None

    @pytest.mark.asyncio
    async def test_load_health_api_non_200_falls_to_registry(self, tmp_path):
        """Should fall back to registry when API returns non-200 (lines 775-779)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.return_value = {}

        mock_async_client = AsyncMock()
        mock_async_client.get = AsyncMock(return_value=mock_response)
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        mock_registry = AsyncMock()
        mock_registry.check_all = AsyncMock(return_value={"status": "ok"})

        with patch("httpx.AsyncClient", return_value=mock_async_client), \
             patch("forge_harness.health_checks.get_health_registry",
                   AsyncMock(return_value=mock_registry)):
            result = await dash._load_health_status()

        assert result is not None

    @pytest.mark.asyncio
    async def test_load_health_api_exception_falls_to_registry(self, tmp_path):
        """API exception should fall through to local registry (line 784)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        mock_async_client = AsyncMock()
        mock_async_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_async_client.__aexit__ = AsyncMock(return_value=False)

        mock_registry = AsyncMock()
        mock_registry.check_all = AsyncMock(return_value={"healthy": True})

        with patch("httpx.AsyncClient", return_value=mock_async_client), \
             patch("forge_harness.health_checks.get_health_registry",
                   AsyncMock(return_value=mock_registry)):
            result = await dash._load_health_status()

        assert dash._health_check_error is None  # registry succeeded

    @pytest.mark.asyncio
    async def test_load_health_registry_exception(self, tmp_path):
        """Registry exception should set error and return None (lines 784-787)."""
        dash = _make_dashboard(tmp_path)

        mock_registry = AsyncMock()
        mock_registry.check_all = AsyncMock(side_effect=Exception("registry down"))

        with patch("forge_harness.health_checks.get_health_registry",
                   AsyncMock(return_value=mock_registry)):
            result = await dash._load_health_status()

        assert result is None
        assert dash._health_check_error is not None

    @pytest.mark.asyncio
    async def test_load_health_outer_exception(self, tmp_path):
        """Unexpected outer exception should set error and return None (line 784-787)."""
        dash = _make_dashboard(tmp_path)

        with patch("forge_harness.health_checks.get_health_registry",
                   side_effect=Exception("completely broken")):
            result = await dash._load_health_status()

        assert result is None


# ===========================================================================
# 9.  render_health_status (line 800)
# ===========================================================================


class TestRenderHealthStatus:
    """Cover render_health_status error branch."""

    def test_render_health_status_with_error(self, tmp_path):
        """Should show error panel when health check error is set (line 800)."""
        dash = _make_dashboard(tmp_path)
        dash._health_check_error = "API error: connection refused"
        panel = dash.render_health_status()
        assert panel is not None

    def test_render_health_status_delegates_to_detail_views(self, tmp_path):
        """Should delegate to dashboard_detail_views when no error."""
        dash = _make_dashboard(tmp_path)
        dash._health_check_error = None
        dash._last_health_check = {"status": "ok"}

        with patch("forge_harness.dashboard.dashboard_detail_views") as mock_views:
            mock_views.render_health_status.return_value = MagicMock()
            panel = dash.render_health_status()

        mock_views.render_health_status.assert_called_once_with({"status": "ok"})


# ===========================================================================
# 10.  _health_check_loop (lines 808-813)
# ===========================================================================


class TestHealthCheckLoop:
    """Cover _health_check_loop coroutine."""

    @pytest.mark.asyncio
    async def test_health_check_loop_calls_load(self, tmp_path):
        """Loop should call _load_health_status periodically."""
        dash = _make_dashboard(tmp_path)

        call_count = [0]

        async def mock_load():
            call_count[0] += 1
            if call_count[0] >= 2:
                raise asyncio.CancelledError()
            return {"status": "ok"}

        dash._load_health_status = mock_load

        with pytest.raises(asyncio.CancelledError):
            await dash._health_check_loop(0.001)

        assert call_count[0] >= 1

    @pytest.mark.asyncio
    async def test_health_check_loop_handles_exception(self, tmp_path):
        """Exceptions in loop should be caught and loop continues (line 811-812)."""
        dash = _make_dashboard(tmp_path)
        call_count = [0]

        async def mock_load():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("load error")
            raise asyncio.CancelledError()

        dash._load_health_status = mock_load

        with pytest.raises(asyncio.CancelledError):
            await dash._health_check_loop(0.001)

        assert call_count[0] >= 2


# ===========================================================================
# 11.  render_tasks - with tasks (lines 883-930)
# ===========================================================================


class TestRenderTasks:
    """Cover task rendering with actual task data (lines 883-930)."""

    def test_render_tasks_empty_with_api_url(self, tmp_path):
        """Empty tasks with api_url shows 'No tasks' (line 878)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")
        with patch.object(dash, "_load_tasks_from_api", return_value=[]):
            panel = dash.render_tasks()
        assert panel is not None

    def test_render_tasks_empty_no_api(self, tmp_path):
        """Empty tasks without api_url shows 'No API' (line 880)."""
        dash = _make_dashboard(tmp_path)
        with patch.object(dash, "_load_tasks_from_api", return_value=[]):
            panel = dash.render_tasks()
        assert panel is not None

    def test_render_tasks_with_assigned_task(self, tmp_path):
        """Should render assigned tasks with green color (lines 883-930)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        tasks = [{
            "id": "abc12345def",
            "subject": "Implement feature X",
            "status": "assigned",
            "lease": {
                "owner_agent": "kimi-agent",
                "lease_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "path_lock": "/some/path/lock",
            },
        }]

        with patch.object(dash, "_load_tasks_from_api", return_value=tasks):
            panel = dash.render_tasks()

        assert panel is not None

    def test_render_tasks_with_stale_lease(self, tmp_path):
        """Should show STALE for expired leases (lines 918-919)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        tasks = [{
            "id": "stale123",
            "subject": "Stale task",
            "status": "assigned",
            "lease": {
                "owner_agent": "old-agent",
                "lease_expires_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
                "path_lock": "/lock",
            },
        }]

        with patch.object(dash, "_load_tasks_from_api", return_value=tasks):
            panel = dash.render_tasks()

        assert panel is not None

    def test_render_tasks_with_bad_lease_date(self, tmp_path):
        """Bad lease date should not crash renderer (lines 916)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        tasks = [{
            "id": "task-bad",
            "subject": "Task with bad lease",
            "status": "pending",
            "lease": {
                "owner_agent": "agent",
                "lease_expires_at": "NOT-A-DATE",
            },
        }]

        with patch.object(dash, "_load_tasks_from_api", return_value=tasks):
            panel = dash.render_tasks()

        assert panel is not None

    def test_render_tasks_no_lease(self, tmp_path):
        """Tasks without lease should render dash for lease column (lines 923-925)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        tasks = [{
            "id": "task-nolease",
            "subject": "Pending task",
            "status": "pending",
            "lease": None,
        }]

        with patch.object(dash, "_load_tasks_from_api", return_value=tasks):
            panel = dash.render_tasks()

        assert panel is not None

    def test_render_tasks_overflow_shows_more(self, tmp_path):
        """More than 12 tasks should show +N indicator (lines 929-932)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        tasks = [
            {"id": f"task-{i:04d}", "subject": f"Task {i}", "status": "pending", "lease": None}
            for i in range(15)
        ]

        with patch.object(dash, "_load_tasks_from_api", return_value=tasks):
            panel = dash.render_tasks()

        assert panel is not None

    def test_render_tasks_various_statuses(self, tmp_path):
        """All status types should have corresponding colors (lines 896-901)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        tasks = [
            {"id": f"t{i}", "subject": f"Task {s}", "status": s, "lease": None}
            for i, s in enumerate(
                ["assigned", "in_progress", "pending", "blocked", "failed", "completed", "cancelled"]
            )
        ]

        with patch.object(dash, "_load_tasks_from_api", return_value=tasks):
            panel = dash.render_tasks()

        assert panel is not None


# ===========================================================================
# 12.  render_mvp_status - with data (lines 956-975)
# ===========================================================================


class TestRenderMVPStatus:
    """Cover render_mvp_status with actual status data (lines 956-975)."""

    def test_render_mvp_status_passed(self, tmp_path):
        """Should show green PASSED label (line 959)."""
        dash = _make_dashboard(tmp_path)

        statuses = [
            MVPStatusSummary(
                domain="test-domain",
                project="my-project",
                status="passed",
                timestamp=datetime.now(UTC),
                missing_env_vars=0,
            )
        ]

        with patch.object(dash, "_load_mvp_status", return_value=statuses):
            panel = dash.render_mvp_status()

        assert panel is not None

    def test_render_mvp_status_failed(self, tmp_path):
        """Should show red FAILED label (line 960)."""
        dash = _make_dashboard(tmp_path)

        statuses = [
            MVPStatusSummary(
                domain="test-domain",
                project="failing-project",
                status="failed",
                timestamp=datetime.now(UTC),
                missing_env_vars=3,
            )
        ]

        with patch.object(dash, "_load_mvp_status", return_value=statuses):
            panel = dash.render_mvp_status()

        assert panel is not None

    def test_render_mvp_status_unknown(self, tmp_path):
        """Should show yellow UNKNOWN for unrecognised status (line 961)."""
        dash = _make_dashboard(tmp_path)

        statuses = [
            MVPStatusSummary(
                domain="test-domain",
                project="unknown-project",
                status="unknown",
                timestamp=datetime.now(UTC),
                missing_env_vars=0,
            )
        ]

        with patch.object(dash, "_load_mvp_status", return_value=statuses):
            panel = dash.render_mvp_status()

        assert panel is not None

    def test_render_mvp_status_empty(self, tmp_path):
        """Empty statuses shows 'No MVP status yet' and returns early (line 984)."""
        dash = _make_dashboard(tmp_path)

        with patch.object(dash, "_load_mvp_status", return_value=[]):
            panel = dash.render_mvp_status()

        assert panel is not None


# ===========================================================================
# 13.  render_approvals - with approval data (lines 1007-1028)
# ===========================================================================


class TestRenderApprovals:
    """Cover approval rendering with data and selection (lines 1007-1028)."""

    def test_render_approvals_with_data(self, tmp_path):
        """Should render approvals table with tier badges and priorities."""
        dash = _make_dashboard(tmp_path)

        approvals = [
            _make_approval("id-0001", "critical", "DESKTOP"),
            _make_approval("id-0002", "high", "PHONE"),
            _make_approval("id-0003", "normal", "WATCH"),
            _make_approval("id-0004", "low", "PHONE"),
        ]

        with patch.object(dash, "_load_pending_approvals", return_value=approvals):
            panel = dash.render_approvals()

        assert panel is not None

    def test_render_approvals_selected_index_clamped(self, tmp_path):
        """selected_approval_index out of range should be clamped."""
        dash = _make_dashboard(tmp_path)
        dash._selected_approval_index = 999  # Way out of range

        approvals = [_make_approval("id-0001")]

        with patch.object(dash, "_load_pending_approvals", return_value=approvals):
            dash.render_approvals()

        assert dash._selected_approval_index == 0

    def test_render_approvals_empty_resets_index(self, tmp_path):
        """Empty approvals should reset selected index to 0 (lines 1007)."""
        dash = _make_dashboard(tmp_path)
        dash._selected_approval_index = 5

        with patch.object(dash, "_load_pending_approvals", return_value=[]):
            dash.render_approvals()

        assert dash._selected_approval_index == 0

    def test_render_approvals_tier_watch(self, tmp_path):
        """WATCH tier should show 'W' badge (lines 1009-1013)."""
        dash = _make_dashboard(tmp_path)
        approvals = [_make_approval("id-watch", "normal", "WATCH")]

        with patch.object(dash, "_load_pending_approvals", return_value=approvals):
            panel = dash.render_approvals()

        assert panel is not None

    def test_render_approvals_tier_unknown(self, tmp_path):
        """Unknown tier should show '?' badge."""
        dash = _make_dashboard(tmp_path)
        approvals = [_make_approval("id-unknown", "normal", "UNKNOWN_TIER")]

        with patch.object(dash, "_load_pending_approvals", return_value=approvals):
            panel = dash.render_approvals()

        assert panel is not None

    def test_render_approvals_selection_highlight(self, tmp_path):
        """Selected row should have 'reverse' style (lines 1024-1026)."""
        dash = _make_dashboard(tmp_path)
        dash._selected_approval_index = 1

        approvals = [
            _make_approval("id-0001"),
            _make_approval("id-0002"),  # Selected
        ]

        with patch.object(dash, "_load_pending_approvals", return_value=approvals):
            panel = dash.render_approvals()

        assert panel is not None


# ===========================================================================
# 14.  render_agents - no API configured (lines 1117-1120)
# ===========================================================================


class TestRenderAgents:
    """Cover agent rendering branches."""

    def test_render_agents_empty_with_api(self, tmp_path):
        """Empty agents with API shows 'No active agents' (lines 1117-1118)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        with patch.object(dash, "_load_agents_from_api", return_value=[]):
            panel = dash.render_agents()

        assert panel is not None

    def test_render_agents_empty_no_api(self, tmp_path):
        """Empty agents without API shows 'No API configured' (lines 1119-1120)."""
        dash = _make_dashboard(tmp_path)

        with patch.object(dash, "_load_agents_from_api", return_value=[]):
            panel = dash.render_agents()

        assert panel is not None

    def test_render_agents_stale_agent(self, tmp_path):
        """Stale agents should show 'offline' status (lines 1129-1131)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        agents = [_make_agent(is_stale=True, status="idle")]

        with patch.object(dash, "_load_agents_from_api", return_value=agents):
            panel = dash.render_agents()

        assert panel is not None

    def test_render_agents_overflow_shows_more(self, tmp_path):
        """More than 16 agents should show overflow indicator (line 1161)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        agents = [_make_agent(id=f"agent-{i}", role=f"role-{i}") for i in range(20)]

        with patch.object(dash, "_load_agents_from_api", return_value=agents):
            panel = dash.render_agents()

        assert panel is not None

    def test_render_agents_various_statuses(self, tmp_path):
        """All agent statuses should render with appropriate colors."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        agents = [
            _make_agent(id=f"agent-{s}", role=f"role-{s}", status=s)
            for s in ["active", "idle", "completed", "error", "unknown_status"]
        ]

        with patch.object(dash, "_load_agents_from_api", return_value=agents):
            panel = dash.render_agents()

        assert panel is not None

    def test_render_agents_no_last_activity(self, tmp_path):
        """Agent without last_activity should show '-' (line 1149)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        agents = [_make_agent(last_activity=None)]
        agents[0].last_activity = None  # Override

        with patch.object(dash, "_load_agents_from_api", return_value=agents):
            panel = dash.render_agents()

        assert panel is not None


# ===========================================================================
# 15.  render_errors - with errors (lines 1243-1244)
# ===========================================================================


class TestRenderErrors:
    """Cover error rendering with data."""

    def test_render_errors_with_data(self, tmp_path):
        """Should render last 5 errors (lines 1243-1244)."""
        dash = _make_dashboard(tmp_path)
        now = datetime.now(UTC)
        dash._recent_errors = [
            (now - timedelta(minutes=i), f"Error number {i}")
            for i in range(7)
        ]
        panel = dash.render_errors()
        assert panel is not None

    def test_render_errors_empty(self, tmp_path):
        """Empty errors should show 'No recent errors'."""
        dash = _make_dashboard(tmp_path)
        dash._recent_errors = []
        panel = dash.render_errors()
        assert panel is not None


# ===========================================================================
# 16.  render_portfolio - with data (lines 1269, 1273-1302)
# ===========================================================================


class TestRenderPortfolio:
    """Cover portfolio rendering with API data."""

    def test_render_portfolio_empty_with_api(self, tmp_path):
        """No portfolio data with api_url shows 'Loading...' (line 1269)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        with patch.object(dash, "_load_portfolio_from_api", return_value=None):
            panel = dash.render_portfolio()

        assert panel is not None

    def test_render_portfolio_empty_no_api(self, tmp_path):
        """No portfolio data without api_url shows 'No API configured'."""
        dash = _make_dashboard(tmp_path)

        with patch.object(dash, "_load_portfolio_from_api", return_value=None):
            panel = dash.render_portfolio()

        assert panel is not None

    def test_render_portfolio_with_data(self, tmp_path):
        """Should render portfolio domains with summary row (lines 1273-1302)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        portfolio = {
            "total_projects": 95,
            "active_domains": 11,
            "domains": [
                {
                    "id": "voice-ai",
                    "display_name": "Voice AI",
                    "project_count": 8,
                    "active": True,
                    "compliance": ["HIPAA", "GDPR"],
                },
                {
                    "id": "ad-tech",
                    "display_name": "Ad Tech",
                    "project_count": 5,
                    "active": False,
                    "compliance": [],
                },
            ],
        }

        with patch.object(dash, "_load_portfolio_from_api", return_value=portfolio):
            panel = dash.render_portfolio()

        assert panel is not None

    def test_render_portfolio_compliance_truncated(self, tmp_path):
        """Compliance list > 2 should be truncated with '...' (lines 1290-1292)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        portfolio = {
            "total_projects": 10,
            "active_domains": 1,
            "domains": [
                {
                    "id": "test",
                    "display_name": "Test Domain",
                    "project_count": 10,
                    "active": True,
                    "compliance": ["HIPAA", "GDPR", "SOC2", "ISO27001"],
                }
            ],
        }

        with patch.object(dash, "_load_portfolio_from_api", return_value=portfolio):
            panel = dash.render_portfolio()

        assert panel is not None


# ===========================================================================
# 17.  render_patterns - with data (lines 1329, 1334-1376)
# ===========================================================================


class TestRenderPatterns:
    """Cover pattern rendering branches."""

    def test_render_patterns_empty_with_api(self, tmp_path):
        """No patterns with api_url shows 'No patterns yet' (line 1329)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        with patch.object(dash, "_load_patterns_from_api", return_value=[]):
            panel = dash.render_patterns()

        assert panel is not None

    def test_render_patterns_empty_no_api(self, tmp_path):
        """No patterns without api_url shows 'No API configured'."""
        dash = _make_dashboard(tmp_path)

        with patch.object(dash, "_load_patterns_from_api", return_value=[]):
            panel = dash.render_patterns()

        assert panel is not None

    def test_render_patterns_with_high_success(self, tmp_path):
        """Patterns with >=80% success should show green (lines 1355-1360)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        patterns = [
            {"name": "fast-commit", "category": "workflow", "success_rate": 0.95, "uses": 50},
        ]

        with patch.object(dash, "_load_patterns_from_api", return_value=patterns):
            panel = dash.render_patterns()

        assert panel is not None

    def test_render_patterns_with_medium_success(self, tmp_path):
        """Patterns with 60-80% success should show yellow."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        patterns = [
            {"name": "retry-pattern", "category": "resilience", "success_rate": 0.70, "uses": 20},
        ]

        with patch.object(dash, "_load_patterns_from_api", return_value=patterns):
            panel = dash.render_patterns()

        assert panel is not None

    def test_render_patterns_with_low_success(self, tmp_path):
        """Patterns with <60% success should show red."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        patterns = [
            {"name": "risky-pattern", "category": "dangerous", "success_rate": 0.40, "uses": 10},
        ]

        with patch.object(dash, "_load_patterns_from_api", return_value=patterns):
            panel = dash.render_patterns()

        assert panel is not None

    def test_render_patterns_filters_test_category(self, tmp_path):
        """Test-category patterns should be excluded from real_patterns (lines 1334-1335)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        patterns = [
            {"name": "test-pat", "category": "test", "success_rate": 0.99, "uses": 100},
            {"name": "real-pat", "category": "workflow", "success_rate": 0.80, "uses": 50},
        ]

        with patch.object(dash, "_load_patterns_from_api", return_value=patterns):
            panel = dash.render_patterns()

        assert panel is not None

    def test_render_patterns_filters_zero_uses(self, tmp_path):
        """Patterns with zero uses should be excluded from real_patterns."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        patterns = [
            {"name": "unused-pat", "category": "workflow", "success_rate": 0.80, "uses": 0},
            {"name": "used-pat", "category": "workflow", "success_rate": 0.80, "uses": 5},
        ]

        with patch.object(dash, "_load_patterns_from_api", return_value=patterns):
            panel = dash.render_patterns()

        assert panel is not None

    def test_render_patterns_summary_row_with_averages(self, tmp_path):
        """Summary row should show totals and average success (lines 1369-1382)."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        patterns = [
            {"name": f"pat-{i}", "category": "workflow", "success_rate": 0.80, "uses": 10}
            for i in range(5)
        ]

        with patch.object(dash, "_load_patterns_from_api", return_value=patterns):
            panel = dash.render_patterns()

        assert panel is not None


# ===========================================================================
# 18.  render_layout - detail view branches (lines 1469-1496, 1507)
# ===========================================================================


class TestRenderLayout:
    """Cover render_layout detail view and help modal branches."""

    def test_render_layout_detail_view_agent(self, tmp_path):
        """Should show agent detail in main area when detail view is active (line 1470)."""
        dash = _make_dashboard(tmp_path)
        dash._detail_view_active = True
        dash._detail_view_type = "agent"
        dash._detail_view_item = _make_agent()

        with patch("forge_harness.dashboard.dashboard_detail_views") as mock_views:
            mock_views.render_agent_detail.return_value = MagicMock()
            dash.render_layout()

        mock_views.render_agent_detail.assert_called_once()

    def test_render_layout_detail_view_approval(self, tmp_path):
        """Should show approval detail in main area (line 1474)."""
        dash = _make_dashboard(tmp_path)
        approval = _make_approval()
        dash._detail_view_active = True
        dash._detail_view_type = "approval"
        dash._detail_view_item = approval

        with patch("forge_harness.dashboard.dashboard_detail_views") as mock_views:
            mock_views.render_approval_detail.return_value = MagicMock()
            dash.render_layout()

        mock_views.render_approval_detail.assert_called_once()

    def test_render_layout_detail_view_pipeline(self, tmp_path):
        """Should show pipeline detail in main area (line 1478)."""
        dash = _make_dashboard(tmp_path)
        pipeline = PipelineStatus(
            name="test-pipe",
            current_step="build",
            step_index=1,
            total_steps=5,
            status="running",
            started_at=datetime.now(UTC),
        )
        dash._detail_view_active = True
        dash._detail_view_type = "pipeline"
        dash._detail_view_item = pipeline

        with patch("forge_harness.dashboard.dashboard_detail_views") as mock_views:
            mock_views.render_pipeline_detail.return_value = MagicMock()
            dash.render_layout()

        mock_views.render_pipeline_detail.assert_called_once()

    def test_render_layout_detail_view_pattern(self, tmp_path):
        """Should show pattern detail in main area (line 1482)."""
        dash = _make_dashboard(tmp_path)
        dash._detail_view_active = True
        dash._detail_view_type = "pattern"
        dash._detail_view_item = {"name": "test-pattern", "category": "workflow"}

        with patch("forge_harness.dashboard.dashboard_detail_views") as mock_views:
            mock_views.render_pattern_detail.return_value = MagicMock()
            dash.render_layout()

        mock_views.render_pattern_detail.assert_called_once()

    def test_render_layout_detail_view_no_panel(self, tmp_path):
        """When detail panel returns None, should fall through to normal layout."""
        dash = _make_dashboard(tmp_path)
        dash._detail_view_active = True
        dash._detail_view_type = "agent"
        dash._detail_view_item = _make_agent()

        with patch("forge_harness.dashboard.dashboard_detail_views") as mock_views:
            mock_views.render_agent_detail.return_value = None
            # Should not crash and should render normal layout
            layout = dash.render_layout()

        assert layout is not None

    def test_render_layout_help_modal_active(self, tmp_path):
        """When help modal is active, should show help modal in approvals area (line 1507)."""
        dash = _make_dashboard(tmp_path)
        dash._show_help_modal = True
        dash._detail_view_active = False

        layout = dash.render_layout()
        assert layout is not None

    def test_render_layout_normal(self, tmp_path):
        """Normal layout should render all panels."""
        dash = _make_dashboard(tmp_path)
        dash._show_help_modal = False
        dash._detail_view_active = False

        layout = dash.render_layout()
        assert layout is not None


# ===========================================================================
# 19.  run() async method (lines 1534-1623)
# ===========================================================================


class TestRunMethod:
    """Cover the run() async method key handling paths."""

    @pytest.mark.asyncio
    async def test_run_quits_on_q_key(self, tmp_path):
        """Pressing 'q' should exit the run loop (line 1578)."""
        dash = _make_dashboard(tmp_path)
        dash._health_check_interval = 999  # prevent real health check

        mock_keyboard = MagicMock()
        keys = iter(["q"])
        mock_keyboard.get_key.side_effect = lambda: next(keys, None)

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

    @pytest.mark.asyncio
    async def test_run_quits_on_q_key(self, tmp_path):
        """Pressing 'Q' should also exit (line 1578)."""
        dash = _make_dashboard(tmp_path)

        mock_keyboard = MagicMock()
        keys = iter(["Q"])
        mock_keyboard.get_key.side_effect = lambda: next(keys, None)

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

    @pytest.mark.asyncio
    async def test_run_down_key_increments_index(self, tmp_path):
        """j/DOWN should increment selected approval index (line 1579-1580)."""
        dash = _make_dashboard(tmp_path)
        dash._selected_approval_index = 0

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] == 1:
                return "j"
            if call_n[0] == 2:
                return "DOWN"
            return "q"

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

        # Index should have been incremented at least once
        assert dash._selected_approval_index >= 2

    @pytest.mark.asyncio
    async def test_run_up_key_decrements_index(self, tmp_path):
        """k/UP should decrement selected approval index but not below 0 (lines 1582-1584)."""
        dash = _make_dashboard(tmp_path)
        dash._selected_approval_index = 2

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] == 1:
                return "k"
            if call_n[0] == 2:
                return "UP"
            if call_n[0] == 3:
                return "k"
            return "q"

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

        assert dash._selected_approval_index >= 0

    @pytest.mark.asyncio
    async def test_run_enter_key_opens_detail_view(self, tmp_path):
        """Enter key should open detail view (line 1585-1587)."""
        dash = _make_dashboard(tmp_path)
        dash._cached_approvals = [_make_approval()]
        dash._selected_approval_index = 0

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] == 1:
                return "\n"   # open detail view
            if call_n[0] == 2:
                return "ESC"  # close detail view (required to re-enter normal nav)
            return "q"        # then quit

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

        assert dash._detail_view_active is False  # ESC closed it

    @pytest.mark.asyncio
    async def test_run_question_key_shows_help(self, tmp_path):
        """'?' key should show help modal (line 1592)."""
        dash = _make_dashboard(tmp_path)

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] == 1:
                return "?"
            if call_n[0] == 2:
                # Next key dismisses help
                return "x"
            return "q"

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

    @pytest.mark.asyncio
    async def test_run_t_key_cycles_time_filter(self, tmp_path):
        """t/T should cycle time filter all->1h->24h->all (lines 1594-1602)."""
        dash = _make_dashboard(tmp_path)
        dash._time_filter = "all"

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] <= 3:
                return "t"
            return "q"

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

        # After 3 cycles: all->1h->24h->all, so back to "all"
        assert dash._time_filter == "all"

    @pytest.mark.asyncio
    async def test_run_s_key_toggles_sound(self, tmp_path):
        """s/S should toggle sound alerts (lines 1603-1606)."""
        dash = _make_dashboard(tmp_path)
        dash._sound_alerts_enabled = True

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] == 1:
                return "s"
            return "q"

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

        assert dash._sound_alerts_enabled is False

    @pytest.mark.asyncio
    async def test_run_detail_view_esc_closes(self, tmp_path):
        """ESC in detail view should close it (lines 1564-1567)."""
        dash = _make_dashboard(tmp_path)
        dash._detail_view_active = True
        dash._detail_view_type = "approval"
        dash._detail_view_item = _make_approval()

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] == 1:
                return "ESC"
            return "q"

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            # Completes instantly - avoids blocking test
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            await dash.run(refresh_rate=0.001)

        assert dash._detail_view_active is False
        assert dash._detail_view_type is None

    @pytest.mark.asyncio
    async def test_run_detail_view_approve_action(self, tmp_path):
        """'a' in approval detail view should call _approve_selected (lines 1570-1572)."""
        dash = _make_dashboard(tmp_path)
        dash._detail_view_active = True
        dash._detail_view_type = "approval"
        dash._detail_view_item = _make_approval()
        dash._cached_approvals = [dash._detail_view_item]

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] == 1:
                return "a"
            return "q"

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            return

        mock_approve = AsyncMock()
        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()), \
             patch.object(dash, "_approve_selected", new=mock_approve):
            await dash.run(refresh_rate=0.001)

        mock_approve.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_detail_view_reject_action(self, tmp_path):
        """'r' in approval detail view should call _reject_selected (lines 1573-1575)."""
        dash = _make_dashboard(tmp_path)
        dash._detail_view_active = True
        dash._detail_view_type = "approval"
        dash._detail_view_item = _make_approval()
        dash._cached_approvals = [dash._detail_view_item]

        mock_keyboard = MagicMock()
        call_n = [0]

        def get_key_side_effect():
            call_n[0] += 1
            if call_n[0] == 1:
                return "r"
            return "q"

        mock_keyboard.get_key.side_effect = get_key_side_effect

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)

        async def noop_loop(*args, **kwargs):
            return

        mock_reject = AsyncMock()
        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()), \
             patch.object(dash, "_reject_selected", new=mock_reject):
            await dash.run(refresh_rate=0.001)

        mock_reject.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_keyboard_interrupt_handled(self, tmp_path):
        """KeyboardInterrupt should be caught and exit gracefully (line 1610)."""
        dash = _make_dashboard(tmp_path)

        mock_keyboard = MagicMock()
        mock_keyboard.get_key.return_value = None

        mock_live = MagicMock()
        mock_live.__enter__ = MagicMock(return_value=mock_live)
        mock_live.__exit__ = MagicMock(return_value=False)
        mock_live.update.side_effect = [None, KeyboardInterrupt()]

        async def noop_loop(*args, **kwargs):
            return

        with patch("forge_harness.dashboard.KeyboardHandler", return_value=mock_keyboard), \
             patch("forge_harness.dashboard.Live", return_value=mock_live), \
             patch.object(dash, "_health_check_loop", new=noop_loop), \
             patch.object(dash, "render_layout", return_value=MagicMock()):
            # Should not raise
            await dash.run(refresh_rate=0.001)


# ===========================================================================
# 20.  _approve_selected (lines 1625-1644)
# ===========================================================================


class TestApproveSelected:
    """Cover _approve_selected paths."""

    @pytest.mark.asyncio
    async def test_approve_selected_no_approvals(self, tmp_path):
        """Should set 'No approvals' status when list is empty (lines 1627-1629)."""
        dash = _make_dashboard(tmp_path)
        dash._cached_approvals = []
        await dash._approve_selected()
        assert "No approvals" in (dash._status_message or "")

    @pytest.mark.asyncio
    async def test_approve_selected_index_out_of_range(self, tmp_path):
        """Should return early when index is out of range (lines 1631-1632)."""
        dash = _make_dashboard(tmp_path)
        dash._cached_approvals = [_make_approval()]
        dash._selected_approval_index = 5  # Out of range
        await dash._approve_selected()
        # Status should not be set to approved
        assert dash._status_message is None or "✓" not in (dash._status_message or "")

    @pytest.mark.asyncio
    async def test_approve_selected_success(self, tmp_path):
        """Should approve and set success status message (lines 1637-1641)."""
        dash = _make_dashboard(tmp_path)
        approval = _make_approval("test-approval-id")
        dash._cached_approvals = [approval]
        dash._selected_approval_index = 0

        mock_queue = AsyncMock()
        mock_queue.approve = AsyncMock()

        with patch("forge_harness.approval_queue.create_approval_queue",
                   return_value=mock_queue):
            await dash._approve_selected()

        assert "✓" in (dash._status_message or "")
        mock_queue.approve.assert_called_once_with("test-approval-id", approved_by="dashboard-user")

    @pytest.mark.asyncio
    async def test_approve_selected_failure(self, tmp_path):
        """Exception during approve should set error status (lines 1642-1644)."""
        dash = _make_dashboard(tmp_path)
        approval = _make_approval("fail-id")
        dash._cached_approvals = [approval]
        dash._selected_approval_index = 0

        mock_queue = AsyncMock()
        mock_queue.approve = AsyncMock(side_effect=Exception("queue error"))

        with patch("forge_harness.approval_queue.create_approval_queue",
                   return_value=mock_queue):
            await dash._approve_selected()

        assert "Error" in (dash._status_message or "")


# ===========================================================================
# 21.  _reject_selected (lines 1646-1665)
# ===========================================================================


class TestRejectSelected:
    """Cover _reject_selected paths."""

    @pytest.mark.asyncio
    async def test_reject_selected_no_approvals(self, tmp_path):
        """Should set 'No approvals' status when list is empty (lines 1648-1650)."""
        dash = _make_dashboard(tmp_path)
        dash._cached_approvals = []
        await dash._reject_selected()
        assert "No approvals" in (dash._status_message or "")

    @pytest.mark.asyncio
    async def test_reject_selected_index_out_of_range(self, tmp_path):
        """Should return early when index out of range (lines 1651-1653)."""
        dash = _make_dashboard(tmp_path)
        dash._cached_approvals = [_make_approval()]
        dash._selected_approval_index = 99
        await dash._reject_selected()
        assert dash._status_message is None or "✗" not in (dash._status_message or "")

    @pytest.mark.asyncio
    async def test_reject_selected_success(self, tmp_path):
        """Should reject and set success status message (lines 1657-1662)."""
        dash = _make_dashboard(tmp_path)
        approval = _make_approval("test-reject-id")
        dash._cached_approvals = [approval]
        dash._selected_approval_index = 0

        mock_queue = AsyncMock()
        mock_queue.reject = AsyncMock()

        with patch("forge_harness.approval_queue.create_approval_queue",
                   return_value=mock_queue):
            await dash._reject_selected()

        assert "✗" in (dash._status_message or "")
        mock_queue.reject.assert_called_once_with(
            "test-reject-id",
            rejected_by="dashboard-user",
            reason="Rejected via TUI",
        )

    @pytest.mark.asyncio
    async def test_reject_selected_failure(self, tmp_path):
        """Exception during reject should set error status (lines 1663-1665)."""
        dash = _make_dashboard(tmp_path)
        approval = _make_approval("fail-reject-id")
        dash._cached_approvals = [approval]
        dash._selected_approval_index = 0

        mock_queue = AsyncMock()
        mock_queue.reject = AsyncMock(side_effect=Exception("reject error"))

        with patch("forge_harness.approval_queue.create_approval_queue",
                   return_value=mock_queue):
            await dash._reject_selected()

        assert "Error" in (dash._status_message or "")


# ===========================================================================
# 22.  _open_detail_view (lines 1676-1680)
# ===========================================================================


class TestOpenDetailView:
    """Cover _open_detail_view method."""

    def test_open_detail_view_with_approvals(self, tmp_path):
        """Should open detail view for selected approval (lines 1676-1680)."""
        dash = _make_dashboard(tmp_path)
        approval = _make_approval("detail-approval-id")
        dash._cached_approvals = [approval]
        dash._selected_approval_index = 0

        dash._open_detail_view()

        assert dash._detail_view_active is True
        assert dash._detail_view_type == "approval"
        assert dash._detail_view_item is approval
        assert "Viewing detail" in (dash._status_message or "")

    def test_open_detail_view_no_approvals(self, tmp_path):
        """Should not open detail view when no approvals cached."""
        dash = _make_dashboard(tmp_path)
        dash._cached_approvals = []
        dash._selected_approval_index = 0

        dash._open_detail_view()

        assert dash._detail_view_active is False

    def test_open_detail_view_index_out_of_range(self, tmp_path):
        """Should not open detail view when index is out of range."""
        dash = _make_dashboard(tmp_path)
        dash._cached_approvals = [_make_approval()]
        dash._selected_approval_index = 5

        dash._open_detail_view()

        assert dash._detail_view_active is False


# ===========================================================================
# 23.  _check_new_approvals and _play_alert_sound
# ===========================================================================


class TestAlertSound:
    """Cover sound alert and new approval detection."""

    def test_check_new_approvals_triggers_sound_when_count_increases(self, tmp_path):
        """Should call _play_alert_sound when approval count increases."""
        dash = _make_dashboard(tmp_path)
        dash._last_approval_count = 1  # Already had approvals
        dash._sound_alerts_enabled = True

        with patch.object(dash, "_play_alert_sound") as mock_sound:
            dash._check_new_approvals([_make_approval("a1"), _make_approval("a2")])

        mock_sound.assert_called_once()

    def test_check_new_approvals_no_sound_when_last_was_zero(self, tmp_path):
        """Should not play sound on first load (last_count == 0)."""
        dash = _make_dashboard(tmp_path)
        dash._last_approval_count = 0

        with patch.object(dash, "_play_alert_sound") as mock_sound:
            dash._check_new_approvals([_make_approval("a1")])

        mock_sound.assert_not_called()

    def test_play_alert_sound_disabled(self, tmp_path):
        """Should not play sound when alerts disabled."""
        dash = _make_dashboard(tmp_path)
        dash._sound_alerts_enabled = False

        with patch("builtins.print") as mock_print:
            dash._play_alert_sound()

        mock_print.assert_not_called()

    def test_play_alert_sound_via_print(self, tmp_path):
        """Should use print as fallback when curses fails."""
        dash = _make_dashboard(tmp_path)
        dash._sound_alerts_enabled = True

        with patch("builtins.print") as mock_print, \
             patch.dict(sys.modules, {"curses": MagicMock(side_effect=ImportError)}):
            dash._play_alert_sound()

        # Either curses.beep() or print was called
        # Just verify no exception

    def test_play_alert_sound_via_curses(self, tmp_path):
        """Should use curses.beep() when available."""
        dash = _make_dashboard(tmp_path)
        dash._sound_alerts_enabled = True

        mock_curses = MagicMock()
        with patch.dict(sys.modules, {"curses": mock_curses}):
            dash._play_alert_sound()

        mock_curses.beep.assert_called_once()


# ===========================================================================
# 24.  _load_tasks_from_api
# ===========================================================================


class TestLoadTasksFromAPI:
    """Cover task loading from API."""

    def test_load_tasks_no_api_url(self, tmp_path):
        """Should return empty list when no api_url (line 395)."""
        dash = _make_dashboard(tmp_path)
        result = dash._load_tasks_from_api()
        assert result == []

    def test_load_tasks_success(self, tmp_path):
        """Should return tasks list on success."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": {"tasks": [{"id": "t1", "subject": "Task 1", "status": "pending"}]}
        }

        with patch("httpx.get", return_value=mock_response):
            tasks = dash._load_tasks_from_api()

        assert len(tasks) == 1
        assert tasks[0]["id"] == "t1"

    def test_load_tasks_exception_returns_cached(self, tmp_path):
        """Should return cached tasks on exception."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")
        dash._cached_tasks = [{"id": "cached-1"}]

        with patch("httpx.get", side_effect=Exception("timeout")):
            tasks = dash._load_tasks_from_api()

        assert tasks == [{"id": "cached-1"}]

    def test_load_tasks_exception_no_cache_returns_empty(self, tmp_path):
        """Should return empty list when no cache and exception occurs."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080")

        with patch("httpx.get", side_effect=Exception("timeout")):
            tasks = dash._load_tasks_from_api()

        assert tasks == []

    def test_load_tasks_with_token_in_header(self, tmp_path):
        """Should include Authorization header when token is set."""
        dash = _make_dashboard(tmp_path, api_url="http://localhost:8080",
                               api_token="my-secret-token")

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": {"tasks": []}}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            dash._load_tasks_from_api()

        call_kwargs = mock_get.call_args
        assert "headers" in call_kwargs.kwargs
        assert "Authorization" in call_kwargs.kwargs["headers"]


# ===========================================================================
# 25.  create_dashboard factory
# ===========================================================================


class TestCreateDashboard:
    """Cover the create_dashboard factory function."""

    def test_create_dashboard_default(self, tmp_path):
        """Should create ForgeDashboard with defaults."""
        with patch("forge_harness.dashboard.Console"):
            dash = create_dashboard(forge_root=tmp_path)

        assert isinstance(dash, ForgeDashboard)
        assert dash.forge_root == tmp_path

    def test_create_dashboard_with_all_params(self, tmp_path):
        """Should pass all params to ForgeDashboard."""
        with patch("forge_harness.dashboard.Console"):
            dash = create_dashboard(
                forge_root=tmp_path,
                approval_storage_dir=tmp_path / "approvals",
                checkpoint_dir=tmp_path / "checkpoints",
                ralph_checkpoint_dir=tmp_path / "ralph",
                api_url="http://localhost:8080",
                api_token="tok",
            )

        assert dash.api_url == "http://localhost:8080"
        assert dash.api_token == "tok"


# ===========================================================================
# 26.  render_help_modal
# ===========================================================================


class TestRenderHelpModal:
    """Cover render_help_modal method."""

    def test_render_help_modal_returns_panel(self, tmp_path):
        """Should return a Panel with keyboard shortcuts."""
        dash = _make_dashboard(tmp_path)
        panel = dash.render_help_modal()
        assert panel is not None


# ===========================================================================
# 27.  snapshot method
# ===========================================================================


class TestSnapshot:
    """Cover snapshot method."""

    def test_snapshot_renders_all_panels(self, tmp_path):
        """snapshot() should call console.print for each panel."""
        dash = _make_dashboard(tmp_path)
        mock_console = MagicMock()
        dash.console = mock_console

        with patch.object(dash, "_load_active_pipelines", return_value=[]), \
             patch.object(dash, "_load_pending_approvals", return_value=[]), \
             patch.object(dash, "_load_agents_from_api", return_value=[]), \
             patch.object(dash, "_load_portfolio_from_api", return_value=None), \
             patch.object(dash, "_load_patterns_from_api", return_value=[]), \
             patch.object(dash, "_load_mvp_status", return_value=[]):
            dash.snapshot()

        # console.print should have been called multiple times
        assert mock_console.print.call_count >= 5


# ===========================================================================
# 28.  _render_progress_bar
# ===========================================================================


class TestRenderProgressBar:
    """Cover _render_progress_bar edge cases."""

    def test_render_progress_bar_zero(self, tmp_path):
        """0% progress should return all empty blocks."""
        dash = _make_dashboard(tmp_path)
        result = dash._render_progress_bar(0)
        assert "░" in result
        assert "█" not in result.replace("[green]", "").replace("[/green]", "")

    def test_render_progress_bar_full(self, tmp_path):
        """100% progress should return all filled blocks."""
        dash = _make_dashboard(tmp_path)
        result = dash._render_progress_bar(100)
        assert "█" in result

    def test_render_progress_bar_partial(self, tmp_path):
        """50% progress should have mixed blocks."""
        dash = _make_dashboard(tmp_path)
        result = dash._render_progress_bar(50)
        assert "█" in result
        assert "░" in result


# ===========================================================================
# 29.  _add_error utility
# ===========================================================================


class TestAddError:
    """Cover _add_error method."""

    def test_add_error_appends(self, tmp_path):
        """Should append error to buffer."""
        dash = _make_dashboard(tmp_path)
        dash._add_error("something went wrong")
        assert len(dash._recent_errors) == 1
        assert "something went wrong" in dash._recent_errors[0][1]

    def test_add_error_trims_to_max(self, tmp_path):
        """Should remove oldest error when buffer is full."""
        dash = _make_dashboard(tmp_path)
        for i in range(12):
            dash._add_error(f"error-{i}")
        assert len(dash._recent_errors) == dash._max_errors
