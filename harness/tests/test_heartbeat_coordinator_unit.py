"""
Unit tests for heartbeat_coordinator.py
========================================

Focused unit tests for four core areas:

1. TestTmuxSessionFiltering  — _get_tmux_sessions() window filtering logic
2. TestHealthEvaluator       — HealthEvaluator threshold-driven status/actions
3. TestAgentProbe            — detection helper methods (no tmux required)
4. TestAgentHeartbeat        — to_dict / from_dict serialization roundtrip
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.heartbeat_coordinator import (
    Action,
    AgentHeartbeat,
    AgentProbe,
    EvaluationResult,
    HealthEvaluator,
    HealthStatus,
    HeartbeatCoordinator,
    ProbeResult,
)

# =============================================================================
# Helpers
# =============================================================================


def make_heartbeat(
    *,
    agent_id: str = "forge:minimax",
    agent_type: str = "claude",
    last_activity_offset_seconds: int = 0,
    context_percentage: int = 10,
    current_task: str | None = "some task",
) -> AgentHeartbeat:
    """Return a minimal AgentHeartbeat with controllable staleness."""
    now = datetime.now(UTC)
    return AgentHeartbeat(
        agent_id=agent_id,
        agent_type=agent_type,
        tmux_target=agent_id,
        last_heartbeat=now,
        last_activity=now - timedelta(seconds=last_activity_offset_seconds),
        session_started=now - timedelta(hours=1),
        context_percentage=context_percentage,
        current_task=current_task,
    )


def _make_async_proc(returncode: int = 0, stdout: bytes = b"") -> AsyncMock:
    """Build an AsyncMock that behaves like asyncio.create_subprocess_exec result."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.wait = AsyncMock(return_value=returncode)
    return proc


# =============================================================================
# 1. Tmux Session Filtering
# =============================================================================


class TestTmuxSessionFiltering:
    """Tests for HeartbeatCoordinator._get_tmux_sessions() window filtering."""

    @pytest.mark.asyncio
    async def test_agent_windows_are_returned(self):
        """forge:minimax and forge:glm are valid agent windows and must be included."""
        tmux_output = b"forge:minimax\nforge:glm\n"
        proc = _make_async_proc(returncode=0, stdout=tmux_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            coordinator = HeartbeatCoordinator(state_dir=None)
            # Prevent file I/O side effects from __init__ by bypassing state load
            coordinator._heartbeats = {}
            sessions = await coordinator._get_tmux_sessions()

        assert "forge:minimax" in sessions
        assert "forge:glm" in sessions

    @pytest.mark.asyncio
    async def test_excluded_infrastructure_windows_are_removed(self):
        """backend, frontend, status, tasks, heartbeat, hb-loop, agent-health,
        and xnode windows must all be filtered out."""
        excluded = [
            "backend", "frontend", "status", "tasks",
            "heartbeat", "hb-loop", "agent-health", "xnode",
        ]
        lines = "\n".join(f"forge:{w}" for w in excluded) + "\n"
        proc = _make_async_proc(returncode=0, stdout=lines.encode())

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            coordinator = HeartbeatCoordinator(state_dir=None)
            coordinator._heartbeats = {}
            sessions = await coordinator._get_tmux_sessions()

        for window in excluded:
            assert f"forge:{window}" not in sessions, f"forge:{window} should be excluded"

    @pytest.mark.asyncio
    async def test_mixed_output_only_returns_agent_windows(self):
        """When output contains both agent and infra windows, only agents survive."""
        tmux_output = (
            b"forge:minimax\n"
            b"forge:glm\n"
            b"forge:backend\n"
            b"forge:frontend\n"
            b"forge:status\n"
        )
        proc = _make_async_proc(returncode=0, stdout=tmux_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            coordinator = HeartbeatCoordinator(state_dir=None)
            coordinator._heartbeats = {}
            sessions = await coordinator._get_tmux_sessions()

        assert set(sessions) == {"forge:minimax", "forge:glm"}

    @pytest.mark.asyncio
    async def test_forge_monitor_session_excluded(self):
        """Windows from forge-monitor (different session) must not appear."""
        tmux_output = (
            b"forge:minimax\n"
            b"forge-monitor:status\n"
            b"forge-monitor:heartbeat\n"
            b"forge-monitor:relay\n"
        )
        proc = _make_async_proc(returncode=0, stdout=tmux_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            coordinator = HeartbeatCoordinator(state_dir=None)
            coordinator._heartbeats = {}
            sessions = await coordinator._get_tmux_sessions()

        assert sessions == ["forge:minimax"]
        for s in sessions:
            assert not s.startswith("forge-monitor:")

    @pytest.mark.asyncio
    async def test_empty_tmux_output_returns_empty_list(self):
        """Empty subprocess output produces an empty session list."""
        proc = _make_async_proc(returncode=0, stdout=b"")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            coordinator = HeartbeatCoordinator(state_dir=None)
            coordinator._heartbeats = {}
            sessions = await coordinator._get_tmux_sessions()

        assert sessions == []

    @pytest.mark.asyncio
    async def test_tmux_nonzero_exit_returns_empty_list(self):
        """Non-zero tmux exit code must return an empty list, not raise."""
        proc = _make_async_proc(returncode=1, stdout=b"")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            coordinator = HeartbeatCoordinator(state_dir=None)
            coordinator._heartbeats = {}
            sessions = await coordinator._get_tmux_sessions()

        assert sessions == []

    @pytest.mark.asyncio
    async def test_only_forge_session_is_monitored(self):
        """Sessions other than 'forge' (e.g. 'work', 'dev') are ignored."""
        tmux_output = (
            b"work:minimax\n"
            b"dev:glm\n"
            b"forge:minimax\n"
        )
        proc = _make_async_proc(returncode=0, stdout=tmux_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            coordinator = HeartbeatCoordinator(state_dir=None)
            coordinator._heartbeats = {}
            sessions = await coordinator._get_tmux_sessions()

        assert sessions == ["forge:minimax"]

    @pytest.mark.asyncio
    async def test_whitespace_only_lines_ignored(self):
        """Lines that are empty or whitespace-only do not produce entries."""
        tmux_output = b"\n\n  \nforge:minimax\n\n"
        proc = _make_async_proc(returncode=0, stdout=tmux_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            coordinator = HeartbeatCoordinator(state_dir=None)
            coordinator._heartbeats = {}
            sessions = await coordinator._get_tmux_sessions()

        # Only the valid agent window survives
        assert "forge:minimax" in sessions
        assert len(sessions) == 1


# =============================================================================
# 2. Health Evaluator Thresholds
# =============================================================================


class TestHealthEvaluator:
    """Tests for HealthEvaluator.evaluate() threshold-driven status/action logic."""

    def setup_method(self):
        self.evaluator = HealthEvaluator()

    # ------------------------------------------------------------------
    # Healthy path
    # ------------------------------------------------------------------

    def test_healthy_recent_activity_low_context(self):
        """Agent with recent activity and low context should be HEALTHY."""
        hb = make_heartbeat(last_activity_offset_seconds=10, context_percentage=20)
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.HEALTHY
        assert result.actions == []

    # ------------------------------------------------------------------
    # Context paths
    # ------------------------------------------------------------------

    def test_context_warning_at_threshold(self):
        """context_percentage == CONTEXT_WARNING (50) triggers WARNING + handoff_warning."""
        # Need no staleness so we don't hit stale branches first.
        hb = make_heartbeat(last_activity_offset_seconds=5, context_percentage=55)
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.WARNING
        action_types = [a.type for a in result.actions]
        assert "handoff_warning" in action_types

    def test_context_warning_action_priority_is_medium(self):
        """handoff_warning action carries medium priority."""
        hb = make_heartbeat(last_activity_offset_seconds=5, context_percentage=60)
        result = self.evaluator.evaluate(hb)

        handoff_warnings = [a for a in result.actions if a.type == "handoff_warning"]
        assert handoff_warnings, "Expected at least one handoff_warning action"
        assert handoff_warnings[0].priority == "medium"

    def test_context_exhausted_returns_exhausted_status(self):
        """context_percentage >= CONTEXT_CRITICAL (90) → EXHAUSTED + critical handoff."""
        hb = make_heartbeat(last_activity_offset_seconds=5, context_percentage=95)
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.EXHAUSTED
        action_types = [a.type for a in result.actions]
        assert "handoff" in action_types

    def test_context_exhausted_action_priority_is_critical(self):
        """Handoff action for exhausted context carries critical priority."""
        hb = make_heartbeat(last_activity_offset_seconds=5, context_percentage=95)
        result = self.evaluator.evaluate(hb)

        handoffs = [a for a in result.actions if a.type == "handoff"]
        assert handoffs, "Expected a handoff action"
        assert handoffs[0].priority == "critical"

    def test_context_exhausted_sets_needs_handoff_flag(self):
        """Evaluating exhausted context must flip heartbeat.needs_handoff."""
        hb = make_heartbeat(last_activity_offset_seconds=5, context_percentage=95)
        self.evaluator.evaluate(hb)

        assert hb.needs_handoff is True

    def test_context_exactly_at_critical_boundary(self):
        """context_percentage == 90 (CONTEXT_CRITICAL) triggers EXHAUSTED."""
        hb = make_heartbeat(last_activity_offset_seconds=5, context_percentage=90)
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.EXHAUSTED

    # ------------------------------------------------------------------
    # Stale / stuck paths
    # ------------------------------------------------------------------

    def test_stale_warning_3_minutes(self):
        """Activity 3 minutes ago (> STALE_WARNING=120s, < STALE_CRITICAL=300s)
        should be WARNING with a low-priority nudge."""
        hb = make_heartbeat(last_activity_offset_seconds=180, context_percentage=20)
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.WARNING
        action_types = [a.type for a in result.actions]
        assert "nudge" in action_types

    def test_stale_warning_nudge_priority_is_low(self):
        """Nudge action for stale-warning carries low priority."""
        hb = make_heartbeat(last_activity_offset_seconds=180, context_percentage=20)
        result = self.evaluator.evaluate(hb)

        nudges = [a for a in result.actions if a.type == "nudge"]
        assert nudges
        assert nudges[0].priority == "low"

    def test_stuck_6_minutes(self):
        """Activity 6 minutes ago (> STALE_CRITICAL=300s) → STUCK + high nudge."""
        hb = make_heartbeat(last_activity_offset_seconds=360, context_percentage=20)
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.STUCK
        action_types = [a.type for a in result.actions]
        assert "nudge" in action_types
        nudges = [a for a in result.actions if a.type == "nudge"]
        assert nudges[0].priority == "high"

    def test_very_stuck_11_minutes_includes_restart(self):
        """Activity 11+ minutes ago (> STALE_CRITICAL * 2 = 600s) → STUCK +
        nudge + restart."""
        hb = make_heartbeat(last_activity_offset_seconds=700, context_percentage=20)
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.STUCK
        action_types = [a.type for a in result.actions]
        assert "nudge" in action_types
        assert "restart" in action_types

    def test_very_stuck_sets_needs_restart_flag(self):
        """Very stuck evaluation must flip heartbeat.needs_restart."""
        hb = make_heartbeat(last_activity_offset_seconds=700, context_percentage=20)
        self.evaluator.evaluate(hb)

        assert hb.needs_restart is True

    def test_stale_sets_is_stale_flag(self):
        """Evaluating a stale agent must set heartbeat.is_stale."""
        hb = make_heartbeat(last_activity_offset_seconds=360, context_percentage=20)
        self.evaluator.evaluate(hb)

        assert hb.is_stale is True

    def test_stale_duration_seconds_is_populated(self):
        """After evaluation, stale_duration_seconds should reflect elapsed time."""
        offset = 300
        hb = make_heartbeat(last_activity_offset_seconds=offset, context_percentage=20)
        self.evaluator.evaluate(hb)

        # Allow ±5 s for test execution time
        assert abs(hb.stale_duration_seconds - offset) <= 5

    # ------------------------------------------------------------------
    # Idle path
    # ------------------------------------------------------------------

    def test_idle_no_task_low_context_over_2_minutes(self):
        """Agent with no current_task, low context, and >2 min inactivity → IDLE."""
        hb = make_heartbeat(
            last_activity_offset_seconds=150,
            context_percentage=20,
            current_task=None,
        )
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.IDLE
        assert result.actions == []

    def test_idle_no_actions(self):
        """IDLE evaluation returns an empty action list."""
        hb = make_heartbeat(
            last_activity_offset_seconds=150,
            context_percentage=10,
            current_task=None,
        )
        result = self.evaluator.evaluate(hb)

        assert result.actions == []

    # ------------------------------------------------------------------
    # Custom threshold constructor
    # ------------------------------------------------------------------

    def test_custom_thresholds_honoured(self):
        """HealthEvaluator should use constructor-provided thresholds."""
        evaluator = HealthEvaluator(
            stale_warning_seconds=10,
            stale_critical_seconds=20,
            context_warning=30,
            context_critical=60,
        )
        # 15 s stale — above custom warning (10), below custom critical (20)
        hb = make_heartbeat(last_activity_offset_seconds=15, context_percentage=5)
        result = evaluator.evaluate(hb)

        assert result.status == HealthStatus.WARNING

    def test_healthy_is_not_stale(self):
        """HEALTHY evaluation must clear is_stale and needs_nudge flags."""
        hb = make_heartbeat(last_activity_offset_seconds=5, context_percentage=10)
        hb.is_stale = True
        hb.needs_nudge = True
        result = self.evaluator.evaluate(hb)

        assert result.status == HealthStatus.HEALTHY
        assert hb.is_stale is False
        assert hb.needs_nudge is False


# =============================================================================
# 3. Agent Probe — Detection Helpers
# =============================================================================


class TestAgentProbe:
    """Tests for AgentProbe detection methods without invoking tmux."""

    def setup_method(self):
        self.probe = AgentProbe()

    # ------------------------------------------------------------------
    # _detect_agent_type
    # ------------------------------------------------------------------

    def test_detect_agent_type_claude_from_claude_keyword(self):
        assert self.probe._detect_agent_type("Claude is thinking...") == "claude"

    def test_detect_agent_type_claude_from_sonnet(self):
        assert self.probe._detect_agent_type("claude-sonnet-4-6") == "claude"

    def test_detect_agent_type_opencode(self):
        assert self.probe._detect_agent_type("opencode v0.1.2 ready") == "opencode"

    def test_detect_agent_type_amp(self):
        assert self.probe._detect_agent_type("AMP session started") == "amp"

    def test_detect_agent_type_defaults_to_claude(self):
        """Unknown output must fall back to 'claude'."""
        assert self.probe._detect_agent_type("some random terminal output") == "claude"

    def test_detect_agent_type_pi(self):
        """'pi ' in output should resolve to pi type."""
        assert self.probe._detect_agent_type("pi  is working") == "pi"

    # ------------------------------------------------------------------
    # _detect_activity
    # ------------------------------------------------------------------

    def test_detect_activity_bash_tool_is_active(self):
        """Claude 'Bash' tool invocation signals activity."""
        assert self.probe._detect_activity("  Bash tool running...", "claude") is True

    def test_detect_activity_read_tool_is_active(self):
        assert self.probe._detect_activity("  Read /path/to/file", "claude") is True

    def test_detect_activity_task_create_is_active(self):
        assert self.probe._detect_activity("  TaskCreate in progress", "claude") is True

    def test_detect_activity_random_text_is_not_active(self):
        assert self.probe._detect_activity("some idle output line", "claude") is False

    def test_detect_activity_empty_string_is_not_active(self):
        assert self.probe._detect_activity("", "claude") is False

    def test_detect_activity_amp_check_mark(self):
        """AMP activity pattern: '✓ Read'."""
        assert self.probe._detect_activity("✓ Read config.json", "amp") is True

    def test_detect_activity_opencode_processing(self):
        assert self.probe._detect_activity("Processing request...", "opencode") is True

    # ------------------------------------------------------------------
    # _detect_idle
    # ------------------------------------------------------------------

    def test_detect_idle_prompt_symbol(self):
        """A trailing '❯' prompt in the last lines signals idle."""
        output = "some output\n❯ "
        assert self.probe._detect_idle(output, "claude") is True

    def test_detect_idle_working_line_is_not_idle(self):
        """A line containing 'Working' should not be classified idle."""
        output = "Working on the task...\nStill going"
        assert self.probe._detect_idle(output, "claude") is False

    def test_detect_idle_empty_output_is_not_idle(self):
        assert self.probe._detect_idle("", "claude") is False

    def test_detect_idle_amp_prompt(self):
        """AMP idle pattern: output ending with 'amp>'."""
        output = "previous output\namp>"
        assert self.probe._detect_idle(output, "amp") is True

    def test_detect_idle_opencode_prompt(self):
        output = "some result\nopencode>"
        assert self.probe._detect_idle(output, "opencode") is True

    # ------------------------------------------------------------------
    # _extract_context_percentage
    # ------------------------------------------------------------------

    def test_extract_context_percentage_pattern_pct_context(self):
        """'45% context' → 45."""
        result = self.probe._extract_context_percentage("45% context used", "claude")
        assert result == 45

    def test_extract_context_percentage_pattern_context_colon(self):
        """'Context: 78%' → 78."""
        result = self.probe._extract_context_percentage("Context: 78%", "claude")
        assert result == 78

    def test_extract_context_percentage_no_match_returns_none(self):
        """When no pattern matches, the method returns None."""
        result = self.probe._extract_context_percentage("nothing here", "claude")
        assert result is None

    def test_extract_context_percentage_zero(self):
        result = self.probe._extract_context_percentage("0% context", "claude")
        assert result == 0

    def test_extract_context_percentage_hundred(self):
        result = self.probe._extract_context_percentage("100% context exhausted", "claude")
        assert result == 100

    def test_extract_context_percentage_context_inline(self):
        """Pattern: 'context…NNN%' — value after context keyword."""
        result = self.probe._extract_context_percentage("context window at 33%", "claude")
        assert result == 33

    # ------------------------------------------------------------------
    # _extract_current_task
    # ------------------------------------------------------------------

    def test_extract_current_task_working_on(self):
        """'working on relay tests' → task fragment extracted."""
        output = "I am working on relay tests now"
        result = self.probe._extract_current_task(output)
        assert result is not None
        assert "relay tests" in result

    def test_extract_current_task_task_label(self):
        """'Task: fix auth bug' → 'fix auth bug'."""
        output = "Task: fix auth bug\nSome more text"
        result = self.probe._extract_current_task(output)
        assert result is not None
        assert "fix auth bug" in result

    def test_extract_current_task_no_match_returns_none(self):
        result = self.probe._extract_current_task("random terminal output")
        assert result is None

    def test_extract_current_task_truncates_long_descriptions(self):
        """Tasks longer than 100 chars should be truncated with '...'."""
        long_task = "a" * 120
        output = f"working on {long_task}"
        result = self.probe._extract_current_task(output)
        assert result is not None
        assert len(result) <= 100
        assert result.endswith("...")

    def test_extract_current_task_reading_file(self):
        """'Reading /some/path' is captured as a task."""
        output = "Reading /home/user/project/config.py"
        result = self.probe._extract_current_task(output)
        assert result is not None


# =============================================================================
# 4. AgentHeartbeat Serialization
# =============================================================================


class TestAgentHeartbeat:
    """Tests for AgentHeartbeat.to_dict() and from_dict() roundtrip."""

    def _make_full_heartbeat(self) -> AgentHeartbeat:
        now = datetime.now(UTC)
        return AgentHeartbeat(
            agent_id="forge:glm",
            agent_type="claude",
            tmux_target="forge:glm",
            last_heartbeat=now,
            last_activity=now - timedelta(minutes=2),
            session_started=now - timedelta(hours=3),
            context_percentage=42,
            context_tokens_used=84000,
            context_tokens_budget=200000,
            status=HealthStatus.WARNING,
            current_task="building auth module",
            last_output_preview="last 100 chars of output",
            is_stale=True,
            needs_nudge=True,
            needs_handoff=False,
            needs_restart=False,
            stale_duration_seconds=130,
            restart_count=2,
            error_count=1,
        )

    def test_to_dict_contains_all_fields(self):
        hb = self._make_full_heartbeat()
        d = hb.to_dict()

        expected_keys = {
            "agent_id", "agent_type", "tmux_target",
            "last_heartbeat", "last_activity", "session_started",
            "context_percentage", "context_tokens_used", "context_tokens_budget",
            "status", "current_task", "last_output_preview",
            "is_stale", "needs_nudge", "needs_handoff", "needs_restart",
            "stale_duration_seconds", "restart_count", "error_count",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_status_is_string(self):
        """status field in dict must be the enum value string, not the enum object."""
        hb = self._make_full_heartbeat()
        d = hb.to_dict()
        assert isinstance(d["status"], str)
        assert d["status"] == "warning"

    def test_to_dict_timestamps_are_iso_strings(self):
        hb = self._make_full_heartbeat()
        d = hb.to_dict()
        for key in ("last_heartbeat", "last_activity", "session_started"):
            assert isinstance(d[key], str)
            # Must be parseable back to datetime
            parsed = datetime.fromisoformat(d[key])
            assert parsed.tzinfo is not None

    def test_roundtrip_preserves_all_fields(self):
        """from_dict(to_dict(hb)) must reproduce the original heartbeat exactly."""
        original = self._make_full_heartbeat()
        reconstructed = AgentHeartbeat.from_dict(original.to_dict())

        assert reconstructed.agent_id == original.agent_id
        assert reconstructed.agent_type == original.agent_type
        assert reconstructed.tmux_target == original.tmux_target
        assert reconstructed.context_percentage == original.context_percentage
        assert reconstructed.context_tokens_used == original.context_tokens_used
        assert reconstructed.context_tokens_budget == original.context_tokens_budget
        assert reconstructed.status == original.status
        assert reconstructed.current_task == original.current_task
        assert reconstructed.last_output_preview == original.last_output_preview
        assert reconstructed.is_stale == original.is_stale
        assert reconstructed.needs_nudge == original.needs_nudge
        assert reconstructed.needs_handoff == original.needs_handoff
        assert reconstructed.needs_restart == original.needs_restart
        assert reconstructed.stale_duration_seconds == original.stale_duration_seconds
        assert reconstructed.restart_count == original.restart_count
        assert reconstructed.error_count == original.error_count

    def test_roundtrip_preserves_timestamps(self):
        original = self._make_full_heartbeat()
        reconstructed = AgentHeartbeat.from_dict(original.to_dict())

        # Compare as ISO strings to avoid sub-microsecond precision drift
        assert reconstructed.last_heartbeat.isoformat() == original.last_heartbeat.isoformat()
        assert reconstructed.last_activity.isoformat() == original.last_activity.isoformat()
        assert reconstructed.session_started.isoformat() == original.session_started.isoformat()

    def test_from_dict_uses_defaults_for_optional_fields(self):
        """from_dict must supply sensible defaults for missing optional keys."""
        minimal = {
            "agent_id": "forge:test",
            "agent_type": "claude",
            "tmux_target": "forge:test",
            "last_heartbeat": datetime.now(UTC).isoformat(),
            "last_activity": datetime.now(UTC).isoformat(),
            "session_started": datetime.now(UTC).isoformat(),
        }
        hb = AgentHeartbeat.from_dict(minimal)

        assert hb.context_percentage == 0
        assert hb.context_tokens_used == 0
        assert hb.context_tokens_budget == 200000
        assert hb.status == HealthStatus.UNKNOWN
        assert hb.current_task is None
        assert hb.last_output_preview is None
        assert hb.is_stale is False
        assert hb.needs_nudge is False
        assert hb.needs_handoff is False
        assert hb.needs_restart is False
        assert hb.stale_duration_seconds == 0
        assert hb.restart_count == 0
        assert hb.error_count == 0

    def test_from_dict_restores_health_status_enum(self):
        """status string in dict is converted back to HealthStatus enum."""
        original = self._make_full_heartbeat()
        reconstructed = AgentHeartbeat.from_dict(original.to_dict())

        assert isinstance(reconstructed.status, HealthStatus)
        assert reconstructed.status == HealthStatus.WARNING

    def test_to_dict_none_fields_serialise_as_none(self):
        """None-valued optional fields serialize as JSON null (Python None)."""
        hb = self._make_full_heartbeat()
        hb.current_task = None
        hb.last_output_preview = None
        d = hb.to_dict()

        assert d["current_task"] is None
        assert d["last_output_preview"] is None


# =============================================================================
# 5. Action Cooldowns
# =============================================================================


class TestActionCooldowns:
    """Tests for HeartbeatCoordinator per-agent, per-action-type cooldown logic."""

    def setup_method(self):
        """Create a coordinator with state_dir=None to avoid file I/O."""
        self.coordinator = HeartbeatCoordinator(state_dir=None)
        # Clear any state loaded from disk during __init__
        self.coordinator._heartbeats = {}
        self.coordinator._action_cooldowns = {}

    # ------------------------------------------------------------------
    # _is_action_on_cooldown / _record_action_executed unit tests
    # ------------------------------------------------------------------

    def test_first_action_not_on_cooldown(self):
        """A fresh agent with no history should not be on cooldown."""
        result = self.coordinator._is_action_on_cooldown("forge:minimax", "restart")
        assert result is False

    def test_action_on_cooldown_is_skipped(self):
        """Same action within the cooldown window must return True (on cooldown)."""
        self.coordinator._record_action_executed("forge:minimax", "restart")
        # Immediately check — well within 300s restart cooldown
        result = self.coordinator._is_action_on_cooldown("forge:minimax", "restart")
        assert result is True

    def test_action_after_cooldown_expires_executes(self):
        """After the cooldown period has elapsed the action should be allowed."""
        # Backdate the recorded timestamp so it appears old enough
        key = ("forge:minimax", "nudge")
        # nudge cooldown is 120s — record 121s in the past
        self.coordinator._action_cooldowns[key] = datetime.now(UTC) - timedelta(seconds=121)
        result = self.coordinator._is_action_on_cooldown("forge:minimax", "nudge")
        assert result is False

    def test_different_action_types_have_independent_cooldowns(self):
        """Nudge cooldown must not affect the restart cooldown for the same agent."""
        self.coordinator._record_action_executed("forge:glm", "nudge")
        # restart has not been executed yet — should NOT be on cooldown
        restart_on_cooldown = self.coordinator._is_action_on_cooldown("forge:glm", "restart")
        nudge_on_cooldown = self.coordinator._is_action_on_cooldown("forge:glm", "nudge")

        assert nudge_on_cooldown is True
        assert restart_on_cooldown is False

    def test_different_agents_have_independent_cooldowns(self):
        """Agent A's cooldown must not affect agent B."""
        self.coordinator._record_action_executed("forge:minimax", "restart")
        result = self.coordinator._is_action_on_cooldown("forge:glm", "restart")
        assert result is False

    def test_cooldown_recording_updates_timestamp(self):
        """Calling _record_action_executed twice overwrites the previous timestamp."""
        agent_id = "forge:kimi"
        action_type = "nudge"
        key = (agent_id, action_type)

        # Record an old timestamp
        old_ts = datetime.now(UTC) - timedelta(seconds=200)
        self.coordinator._action_cooldowns[key] = old_ts

        # Record again — should overwrite with a fresh timestamp
        self.coordinator._record_action_executed(agent_id, action_type)
        new_ts = self.coordinator._action_cooldowns[key]

        assert new_ts > old_ts
        # The new timestamp must be very recent (within 2 seconds)
        elapsed = (datetime.now(UTC) - new_ts).total_seconds()
        assert elapsed < 2

    def test_default_cooldown_used_for_unknown_action_type(self):
        """An action type not in ACTION_COOLDOWNS falls back to DEFAULT_COOLDOWN."""
        agent_id = "forge:minimax"
        action_type = "unknown_action"

        # Record action and check it is on cooldown immediately
        self.coordinator._record_action_executed(agent_id, action_type)
        assert self.coordinator._is_action_on_cooldown(agent_id, action_type) is True

        # Backdate by DEFAULT_COOLDOWN + 1 second — should no longer be on cooldown
        key = (agent_id, action_type)
        self.coordinator._action_cooldowns[key] = datetime.now(UTC) - timedelta(
            seconds=self.coordinator.DEFAULT_COOLDOWN + 1
        )
        assert self.coordinator._is_action_on_cooldown(agent_id, action_type) is False

    # ------------------------------------------------------------------
    # Integration: _check_agent honours cooldowns
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_check_agent_skips_action_on_cooldown(self):
        """_check_agent must not execute an action that is within its cooldown period."""
        agent_id = "forge:minimax"

        # Pre-record a restart action so it appears on cooldown
        self.coordinator._record_action_executed(agent_id, "restart")

        # Build a probe result that triggers a restart evaluation
        probe_result = MagicMock()
        probe_result.agent_type = "claude"
        probe_result.output = "some output"
        probe_result.context_percentage = None
        probe_result.current_task = None
        probe_result.is_active = False

        # Build an evaluator that always recommends a restart action
        mock_action = Action(type="restart", priority="high", delay=0)
        mock_evaluation = EvaluationResult(
            status=HealthStatus.STUCK, actions=[mock_action]
        )
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = mock_evaluation

        # Track whether the action engine was called
        mock_action_engine = AsyncMock()
        mock_action_engine.execute = AsyncMock(
            return_value=MagicMock(success=True)
        )

        self.coordinator.evaluator = mock_evaluator
        self.coordinator.action_engine = mock_action_engine
        self.coordinator.auto_actions = True

        # Stub probe
        with patch.object(self.coordinator.probe, "probe", return_value=probe_result):
            await self.coordinator._check_agent(agent_id)

        # The action engine must NOT have been called because restart is on cooldown
        mock_action_engine.execute.assert_not_called()
