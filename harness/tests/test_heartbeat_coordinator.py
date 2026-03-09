"""
Comprehensive tests for heartbeat_coordinator.py
=================================================

Tests covering all heartbeat coordination functions:
- HealthStatus enum
- AgentHeartbeat dataclass (to_dict, from_dict, edge cases)
- FleetHealthSummary dataclass (to_dict)
- ProbeResult, Action, EvaluationResult, ActionResult dataclasses
- AgentProbe: type detection, activity detection, idle detection,
  context extraction, task extraction, capture_pane
- HealthEvaluator: all status branches, threshold customization, edge cases
- ActionEngine: all action types, error handling, subprocess mocking
- HeartbeatStateStore: save, load, missing file, corrupted file, defaults
- HeartbeatCoordinator: lifecycle, registration, monitoring, execute_action
- Global singleton: get/reset
- Integration scenarios
"""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.heartbeat_coordinator import (
    Action,
    ActionEngine,
    ActionResult,
    AgentHeartbeat,
    AgentProbe,
    EvaluationResult,
    FleetHealthSummary,
    HealthEvaluator,
    HealthStatus,
    HeartbeatCoordinator,
    HeartbeatStateStore,
    ProbeResult,
    get_heartbeat_coordinator,
    reset_heartbeat_coordinator,
)

# =============================================================================
# Helpers
# =============================================================================


def make_heartbeat(
    agent_id: str = "forge:tech",
    agent_type: str = "claude",
    tmux_target: str | None = None,
    last_activity_offset_seconds: int = 0,
    context_percentage: int = 0,
    current_task: str | None = None,
    status: HealthStatus = HealthStatus.UNKNOWN,
    restart_count: int = 0,
    needs_restart: bool = False,
    needs_nudge: bool = False,
    needs_handoff: bool = False,
    is_stale: bool = False,
) -> AgentHeartbeat:
    """Build a test AgentHeartbeat with controllable staleness."""
    now = datetime.now(UTC)
    last_activity = now - timedelta(seconds=last_activity_offset_seconds)
    return AgentHeartbeat(
        agent_id=agent_id,
        agent_type=agent_type,
        tmux_target=tmux_target or agent_id,
        last_heartbeat=now,
        last_activity=last_activity,
        session_started=now - timedelta(minutes=30),
        context_percentage=context_percentage,
        current_task=current_task,
        status=status,
        restart_count=restart_count,
        needs_restart=needs_restart,
        needs_nudge=needs_nudge,
        needs_handoff=needs_handoff,
        is_stale=is_stale,
    )


def make_mock_proc(returncode: int = 0) -> AsyncMock:
    """Build an async mock subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.wait = AsyncMock()
    return proc


# =============================================================================
# Module-level fixtures
# =============================================================================


@pytest.fixture
def now():
    return datetime.now(UTC)


@pytest.fixture
def heartbeat(now):
    return AgentHeartbeat(
        agent_id="forge:tech",
        agent_type="claude",
        tmux_target="forge:tech",
        last_heartbeat=now,
        last_activity=now,
        session_started=now - timedelta(hours=1),
        context_percentage=25,
        status=HealthStatus.HEALTHY,
        current_task="Implementing feature X",
    )


@pytest.fixture
def stale_heartbeat(now):
    return AgentHeartbeat(
        agent_id="forge:game",
        agent_type="claude",
        tmux_target="forge:game",
        last_heartbeat=now - timedelta(minutes=10),
        last_activity=now - timedelta(minutes=10),
        session_started=now - timedelta(hours=2),
        context_percentage=40,
        status=HealthStatus.UNKNOWN,
    )


@pytest.fixture
def exhausted_heartbeat(now):
    return AgentHeartbeat(
        agent_id="forge:claude",
        agent_type="claude",
        tmux_target="forge:claude",
        last_heartbeat=now,
        last_activity=now - timedelta(minutes=1),
        session_started=now - timedelta(hours=3),
        context_percentage=95,
        status=HealthStatus.UNKNOWN,
    )


@pytest.fixture
def temp_state_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def coordinator(temp_state_dir):
    reset_heartbeat_coordinator()
    return HeartbeatCoordinator(
        interval_seconds=1,
        state_dir=temp_state_dir,
        auto_actions=False,
    )


# =============================================================================
# 1. HealthStatus Enum
# =============================================================================


class TestHealthStatus:
    def test_all_values_exist(self):
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.IDLE.value == "idle"
        assert HealthStatus.WARNING.value == "warning"
        assert HealthStatus.STUCK.value == "stuck"
        assert HealthStatus.EXHAUSTED.value == "exhausted"
        assert HealthStatus.FAILED.value == "failed"
        assert HealthStatus.UNKNOWN.value == "unknown"

    def test_is_str_subclass(self):
        assert isinstance(HealthStatus.HEALTHY, str)
        assert HealthStatus.HEALTHY == "healthy"

    def test_roundtrip_via_value(self):
        for status in HealthStatus:
            assert HealthStatus(status.value) == status

    def test_count_of_statuses(self):
        assert len(list(HealthStatus)) == 7


# =============================================================================
# 2. AgentHeartbeat Dataclass
# =============================================================================


class TestAgentHeartbeat:
    def test_creation(self, heartbeat):
        assert heartbeat.agent_id == "forge:tech"
        assert heartbeat.agent_type == "claude"
        assert heartbeat.context_percentage == 25
        assert heartbeat.status == HealthStatus.HEALTHY

    def test_to_dict_has_all_keys(self, heartbeat):
        d = heartbeat.to_dict()
        expected = {
            "agent_id", "agent_type", "tmux_target",
            "last_heartbeat", "last_activity", "session_started",
            "context_percentage", "context_tokens_used", "context_tokens_budget",
            "status", "current_task", "last_output_preview",
            "is_stale", "needs_nudge", "needs_handoff", "needs_restart",
            "stale_duration_seconds", "restart_count", "error_count",
        }
        assert expected == set(d.keys())

    def test_to_dict_values(self, heartbeat):
        d = heartbeat.to_dict()
        assert d["agent_id"] == "forge:tech"
        assert d["context_percentage"] == 25
        assert d["status"] == "healthy"

    def test_to_dict_timestamps_are_iso(self, heartbeat):
        d = heartbeat.to_dict()
        datetime.fromisoformat(d["last_heartbeat"])
        datetime.fromisoformat(d["last_activity"])
        datetime.fromisoformat(d["session_started"])

    def test_from_dict_roundtrip(self, heartbeat):
        d = heartbeat.to_dict()
        restored = AgentHeartbeat.from_dict(d)
        assert restored.agent_id == heartbeat.agent_id
        assert restored.context_percentage == heartbeat.context_percentage
        assert restored.status == heartbeat.status
        assert restored.current_task == heartbeat.current_task

    def test_from_dict_with_defaults(self):
        minimal = {
            "agent_id": "test",
            "agent_type": "claude",
            "tmux_target": "test:0",
            "last_heartbeat": datetime.now(UTC).isoformat(),
            "last_activity": datetime.now(UTC).isoformat(),
            "session_started": datetime.now(UTC).isoformat(),
        }
        hb = AgentHeartbeat.from_dict(minimal)
        assert hb.context_percentage == 0
        assert hb.context_tokens_budget == 200000
        assert hb.status == HealthStatus.UNKNOWN
        assert hb.is_stale is False
        assert hb.restart_count == 0

    def test_default_flags_are_false(self, now):
        hb = AgentHeartbeat(
            agent_id="x", agent_type="claude", tmux_target="x",
            last_heartbeat=now, last_activity=now, session_started=now,
        )
        assert hb.is_stale is False
        assert hb.needs_nudge is False
        assert hb.needs_handoff is False
        assert hb.needs_restart is False

    def test_status_serialized_as_string_value(self, heartbeat):
        heartbeat.status = HealthStatus.EXHAUSTED
        assert heartbeat.to_dict()["status"] == "exhausted"

    def test_from_dict_unknown_status_parses(self):
        d = {
            "agent_id": "x", "agent_type": "claude", "tmux_target": "x",
            "last_heartbeat": datetime.now(UTC).isoformat(),
            "last_activity": datetime.now(UTC).isoformat(),
            "session_started": datetime.now(UTC).isoformat(),
            "status": "unknown",
        }
        hb = AgentHeartbeat.from_dict(d)
        assert hb.status == HealthStatus.UNKNOWN


# =============================================================================
# 3. FleetHealthSummary Dataclass
# =============================================================================


class TestFleetHealthSummary:
    def test_creation(self, now):
        s = FleetHealthSummary(
            timestamp=now, total_agents=5,
            by_status={"healthy": 3, "warning": 2},
            agents_needing_attention=["forge:tech"],
        )
        assert s.total_agents == 5
        assert s.by_status["healthy"] == 3
        assert len(s.agents_needing_attention) == 1

    def test_to_dict_keys(self, now):
        s = FleetHealthSummary(timestamp=now, total_agents=0)
        d = s.to_dict()
        assert set(d.keys()) == {
            "timestamp", "total_agents", "by_status",
            "agents_needing_attention", "recommended_actions"
        }

    def test_to_dict_timestamp_is_iso(self, now):
        s = FleetHealthSummary(timestamp=now, total_agents=0)
        datetime.fromisoformat(s.to_dict()["timestamp"])

    def test_empty_defaults(self, now):
        s = FleetHealthSummary(timestamp=now, total_agents=0)
        d = s.to_dict()
        assert d["by_status"] == {}
        assert d["agents_needing_attention"] == []
        assert d["recommended_actions"] == []

    def test_to_dict_includes_recommended_actions(self, now):
        s = FleetHealthSummary(
            timestamp=now, total_agents=1,
            recommended_actions=[{"agent_id": "forge:tech", "action": "nudge"}],
        )
        d = s.to_dict()
        assert len(d["recommended_actions"]) == 1


# =============================================================================
# 4. ActionResult Dataclass
# =============================================================================


class TestActionResult:
    def test_to_dict_success(self):
        ar = ActionResult(success=True, action="nudge", agent_id="forge:tech")
        d = ar.to_dict()
        assert d["success"] is True
        assert d["action"] == "nudge"
        assert d["agent_id"] == "forge:tech"
        assert d["error"] is None
        datetime.fromisoformat(d["executed_at"])

    def test_to_dict_failure(self):
        ar = ActionResult(success=False, action="restart", agent_id="x", error="fail")
        d = ar.to_dict()
        assert d["success"] is False
        assert d["error"] == "fail"

    def test_executed_at_defaults_to_now(self):
        before = datetime.now(UTC)
        ar = ActionResult(success=True, action="nudge", agent_id="x")
        after = datetime.now(UTC)
        assert before <= ar.executed_at <= after


# =============================================================================
# 5. AgentProbe — Type Detection
# =============================================================================


class TestAgentProbeDetectType:
    def setup_method(self):
        self.probe = AgentProbe()

    def test_detects_claude(self):
        assert self.probe._detect_agent_type("Claude is running") == "claude"

    def test_detects_opencode(self):
        assert self.probe._detect_agent_type("opencode session active") == "opencode"

    def test_detects_amp(self):
        assert self.probe._detect_agent_type("Amp running task") == "amp"

    def test_detects_pi(self):
        assert self.probe._detect_agent_type("pi > ready") == "pi"

    def test_detects_cursor(self):
        assert self.probe._detect_agent_type("Cursor generating code") == "cursor"

    def test_defaults_to_claude_on_unknown(self):
        assert self.probe._detect_agent_type("some random terminal output") == "claude"

    def test_anthropic_keyword_maps_to_claude(self):
        assert self.probe._detect_agent_type("Anthropic session") == "claude"


# =============================================================================
# 6. AgentProbe — Activity Detection
# =============================================================================


class TestAgentProbeDetectActivity:
    def setup_method(self):
        self.probe = AgentProbe()

    def test_claude_working(self):
        assert self.probe._detect_activity("working on the task", "claude") is True

    def test_claude_bash(self):
        assert self.probe._detect_activity("Bash command running", "claude") is True

    def test_claude_thinking(self):
        assert self.probe._detect_activity("thinking...", "claude") is True

    def test_claude_read(self):
        assert self.probe._detect_activity("Read file.py", "claude") is True

    def test_amp_read(self):
        assert self.probe._detect_activity("✓ Read file.py", "amp") is True

    def test_opencode_processing(self):
        assert self.probe._detect_activity("Processing request", "opencode") is True

    def test_no_activity_returns_false(self):
        assert self.probe._detect_activity("   ", "claude") is False

    def test_unknown_type_falls_back_to_claude_patterns(self):
        assert self.probe._detect_activity("working on something", "unknown_type") is True


# =============================================================================
# 7. AgentProbe — Idle Detection
# =============================================================================


class TestAgentProbeDetectIdle:
    def setup_method(self):
        self.probe = AgentProbe()

    def test_claude_idle_prompt(self):
        assert self.probe._detect_idle("some output\n❯", "claude") is True

    def test_amp_idle(self):
        assert self.probe._detect_idle("some output\namp>", "amp") is True

    def test_opencode_idle(self):
        assert self.probe._detect_idle("result\nopencode>", "opencode") is True

    def test_not_idle_when_no_pattern(self):
        assert self.probe._detect_idle("Bash running command\nstill working", "claude") is False

    def test_idle_checks_last_lines_only(self):
        # First line has idle marker but last lines do not
        output = "❯\n" + "\n".join(["still working"] * 10)
        assert self.probe._detect_idle(output, "claude") is False


# =============================================================================
# 8. AgentProbe — Context Percentage
# =============================================================================


class TestAgentProbeContextPercentage:
    def setup_method(self):
        self.probe = AgentProbe()

    def test_percent_context_pattern(self):
        assert self.probe._extract_context_percentage("45% context used", "claude") == 45

    def test_context_prefix_pattern(self):
        assert self.probe._extract_context_percentage("Context: 72%", "claude") == 72

    def test_pi_pattern(self):
        assert self.probe._extract_context_percentage("12.3%/200k tokens", "pi") == 12

    def test_amp_pattern(self):
        assert self.probe._extract_context_percentage("38% of 100k", "amp") == 38

    def test_returns_none_on_no_match(self):
        assert self.probe._extract_context_percentage("no percentage here", "claude") is None

    def test_context_percentage_case_insensitive(self):
        assert self.probe._extract_context_percentage("CONTEXT: 55%", "claude") == 55


# =============================================================================
# 9. AgentProbe — Current Task Extraction
# =============================================================================


class TestAgentProbeCurrentTask:
    def setup_method(self):
        self.probe = AgentProbe()

    def test_working_on_pattern(self):
        task = self.probe._extract_current_task("working on the authentication module")
        assert task is not None
        assert "authentication module" in task

    def test_task_label(self):
        task = self.probe._extract_current_task("Task: implement user login")
        assert task is not None
        assert "implement user login" in task

    def test_reading_file(self):
        task = self.probe._extract_current_task("Reading config.py")
        assert task is not None

    def test_truncates_long_task(self):
        task = self.probe._extract_current_task("working on " + "x" * 200)
        assert task is not None
        assert len(task) <= 100

    def test_returns_none_on_no_match(self):
        assert self.probe._extract_current_task("   idle output   ") is None

    def test_implementing_pattern(self):
        task = self.probe._extract_current_task("implementing the new API")
        assert task is not None


# =============================================================================
# 10. AgentProbe — probe() and _capture_pane()
# =============================================================================


class TestAgentProbeProbe:
    def setup_method(self):
        self.probe = AgentProbe()

    @pytest.mark.asyncio
    async def test_probe_empty_pane(self):
        with patch.object(self.probe, "_capture_pane", new=AsyncMock(return_value="")):
            result = await self.probe.probe("forge:tech")
        assert result.is_active is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_probe_active_agent(self):
        output = "claude is working on implementing the feature\n❯"
        with patch.object(self.probe, "_capture_pane", new=AsyncMock(return_value=output)):
            result = await self.probe.probe("forge:tech")
        assert result.agent_type == "claude"
        assert result.is_active is True

    @pytest.mark.asyncio
    async def test_probe_truncates_output_to_500(self):
        output = "x" * 1000
        with patch.object(self.probe, "_capture_pane", new=AsyncMock(return_value=output)):
            result = await self.probe.probe("forge:tech")
        assert len(result.output) <= 500

    @pytest.mark.asyncio
    async def test_probe_exception_returns_error_result(self):
        with patch.object(self.probe, "_capture_pane", new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await self.probe.probe("forge:tech")
        assert result.is_active is False
        assert "boom" == result.error

    @pytest.mark.asyncio
    async def test_probe_idle_not_active(self):
        output = "opencode>"
        with patch.object(self.probe, "_capture_pane", new=AsyncMock(return_value=output)):
            result = await self.probe.probe("forge:tech")
        assert result.is_active is False
        assert result.is_idle is True

    @pytest.mark.asyncio
    async def test_probe_extracts_context_percentage(self):
        output = "claude running\nContext: 35% used"
        with patch.object(self.probe, "_capture_pane", new=AsyncMock(return_value=output)):
            result = await self.probe.probe("forge:tech")
        assert result.context_percentage == 35

    @pytest.mark.asyncio
    async def test_capture_pane_success(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"pane content\n", b""))
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            output = await self.probe._capture_pane("forge:tech")
        assert "pane content" in output

    @pytest.mark.asyncio
    async def test_capture_pane_returns_empty_on_nonzero_rc(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_proc.returncode = 1
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            output = await self.probe._capture_pane("forge:tech")
        assert output == ""

    @pytest.mark.asyncio
    async def test_capture_pane_returns_empty_on_exception(self):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no tmux")):
            output = await self.probe._capture_pane("forge:tech")
        assert output == ""


# =============================================================================
# 11. HealthEvaluator
# =============================================================================


class TestHealthEvaluator:
    def setup_method(self):
        self.evaluator = HealthEvaluator()

    def test_healthy_recent_activity(self, heartbeat):
        result = self.evaluator.evaluate(heartbeat)
        assert result.status == HealthStatus.HEALTHY
        assert heartbeat.is_stale is False
        assert heartbeat.needs_nudge is False

    def test_exhausted_at_90_percent(self):
        hb = make_heartbeat(context_percentage=90)
        result = self.evaluator.evaluate(hb)
        assert result.status == HealthStatus.EXHAUSTED
        assert hb.needs_handoff is True
        assert any(a.type == "handoff" for a in result.actions)
        assert result.actions[0].priority == "critical"

    def test_exhausted_above_90_percent(self):
        hb = make_heartbeat(context_percentage=99)
        assert self.evaluator.evaluate(hb).status == HealthStatus.EXHAUSTED

    def test_stuck_very_long_stale(self):
        hb = make_heartbeat(last_activity_offset_seconds=700, current_task="something")
        result = self.evaluator.evaluate(hb)
        assert result.status == HealthStatus.STUCK
        assert hb.needs_restart is True
        types = {a.type for a in result.actions}
        assert "nudge" in types
        assert "restart" in types

    def test_stuck_moderately_stale(self):
        hb = make_heartbeat(last_activity_offset_seconds=400, current_task="some task")
        result = self.evaluator.evaluate(hb)
        assert result.status == HealthStatus.STUCK
        assert hb.is_stale is True
        assert hb.needs_nudge is True
        assert any(a.type == "nudge" for a in result.actions)

    def test_idle_no_task_no_context(self):
        hb = make_heartbeat(last_activity_offset_seconds=150, context_percentage=10,
                             current_task=None)
        result = self.evaluator.evaluate(hb)
        assert result.status == HealthStatus.IDLE
        assert result.actions == []

    def test_idle_check_only_after_120s(self):
        hb = make_heartbeat(last_activity_offset_seconds=30, context_percentage=5,
                             current_task=None)
        result = self.evaluator.evaluate(hb)
        assert result.status == HealthStatus.HEALTHY

    def test_warning_stale_with_task(self):
        hb = make_heartbeat(last_activity_offset_seconds=200, current_task="coding",
                             context_percentage=10)
        result = self.evaluator.evaluate(hb)
        assert result.status == HealthStatus.WARNING
        assert hb.is_stale is True

    def test_warning_context_at_50(self):
        hb = make_heartbeat(context_percentage=50, last_activity_offset_seconds=5,
                             current_task="working")
        result = self.evaluator.evaluate(hb)
        assert result.status == HealthStatus.WARNING
        assert hb.needs_handoff is True
        assert any(a.type == "handoff_warning" for a in result.actions)

    def test_custom_thresholds_context_critical(self):
        evaluator = HealthEvaluator(context_critical=60)
        hb = make_heartbeat(context_percentage=65, last_activity_offset_seconds=5,
                             current_task="working")
        assert evaluator.evaluate(hb).status == HealthStatus.EXHAUSTED

    def test_custom_thresholds_stale_warning(self):
        evaluator = HealthEvaluator(stale_warning_seconds=10, stale_critical_seconds=20)
        hb = make_heartbeat(last_activity_offset_seconds=15, current_task="working")
        result = evaluator.evaluate(hb)
        assert result.status == HealthStatus.WARNING

    def test_stale_duration_is_set(self):
        hb = make_heartbeat(last_activity_offset_seconds=60, current_task="working")
        self.evaluator.evaluate(hb)
        assert hb.stale_duration_seconds >= 60

    def test_healthy_clears_stale_and_nudge_flags(self):
        hb = make_heartbeat(last_activity_offset_seconds=5, context_percentage=10,
                             current_task="working", is_stale=True, needs_nudge=True)
        self.evaluator.evaluate(hb)
        assert hb.is_stale is False
        assert hb.needs_nudge is False

    def test_evaluate_exhausted_context(self, exhausted_heartbeat):
        result = self.evaluator.evaluate(exhausted_heartbeat)
        assert result.status == HealthStatus.EXHAUSTED
        assert exhausted_heartbeat.needs_handoff is True

    def test_evaluate_stuck_stale(self, stale_heartbeat):
        result = self.evaluator.evaluate(stale_heartbeat)
        assert result.status == HealthStatus.STUCK
        assert stale_heartbeat.stale_duration_seconds > 0

    def test_custom_context_warning(self, heartbeat):
        evaluator = HealthEvaluator(context_warning=20, context_critical=80)
        heartbeat.context_percentage = 25
        result = evaluator.evaluate(heartbeat)
        assert result.status == HealthStatus.WARNING

    def test_idle_reason_set(self):
        hb = make_heartbeat(last_activity_offset_seconds=200, context_percentage=5,
                             current_task=None)
        result = self.evaluator.evaluate(hb)
        assert result.status == HealthStatus.IDLE
        assert result.reason is not None


# =============================================================================
# 12. ActionEngine
# =============================================================================


class TestActionEngine:
    def setup_method(self):
        self.engine = ActionEngine()
        self.hb = make_heartbeat()

    @pytest.mark.asyncio
    async def test_nudge_success(self):
        proc = make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await self.engine.execute(Action(type="nudge", priority="high"), self.hb)
        assert result.success is True
        assert result.action == "nudge"

    @pytest.mark.asyncio
    async def test_nudge_nonzero_rc(self):
        proc = make_mock_proc(returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await self.engine.execute(Action(type="nudge", priority="high"), self.hb)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_nudge_exception(self):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no tmux")):
            result = await self.engine.execute(Action(type="nudge", priority="high"), self.hb)
        assert result.success is False
        assert "no tmux" in result.error

    @pytest.mark.asyncio
    async def test_handoff_warning_always_succeeds(self):
        result = await self.engine.execute(Action(type="handoff_warning", priority="medium"), self.hb)
        assert result.success is True
        assert result.action == "handoff_warning"

    @pytest.mark.asyncio
    async def test_handoff_two_subprocess_calls(self):
        proc = make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await self.engine.execute(Action(type="handoff", priority="critical"), self.hb)
        assert result.success is True
        assert mock_exec.call_count == 2

    @pytest.mark.asyncio
    async def test_handoff_exception(self):
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("tmux gone")):
            result = await self.engine.execute(Action(type="handoff", priority="critical"), self.hb)
        assert result.success is False
        assert "tmux gone" in result.error

    @pytest.mark.asyncio
    async def test_restart_increments_count(self):
        proc = make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await self.engine.execute(Action(type="restart", priority="medium"), self.hb)
        assert result.success is True
        assert self.hb.restart_count == 1

    @pytest.mark.asyncio
    async def test_restart_exception(self):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("fail")):
            result = await self.engine.execute(Action(type="restart", priority="medium"), self.hb)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_kill_success(self):
        proc = make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await self.engine.execute(Action(type="kill", priority="critical"), self.hb)
        assert result.success is True
        assert result.action == "kill"

    @pytest.mark.asyncio
    async def test_kill_exception(self):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("fail")):
            result = await self.engine.execute(Action(type="kill", priority="critical"), self.hb)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unknown_action_type(self):
        result = await self.engine.execute(Action(type="teleport", priority="low"), self.hb)
        assert result.success is False
        assert "Unknown action type" in result.error

    @pytest.mark.asyncio
    async def test_execute_top_level_exception(self):
        with patch.object(self.engine, "_nudge_agent", side_effect=RuntimeError("unexpected")):
            result = await self.engine.execute(Action(type="nudge", priority="high"), self.hb)
        assert result.success is False
        assert "unexpected" in result.error

    @pytest.mark.asyncio
    async def test_nudge_agent_id_in_result(self):
        proc = make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await self.engine.execute(Action(type="nudge", priority="high"), self.hb)
        assert result.agent_id == self.hb.agent_id


# =============================================================================
# 13. HeartbeatStateStore
# =============================================================================


class TestHeartbeatStateStore:
    @pytest.fixture
    def store(self, temp_state_dir) -> HeartbeatStateStore:
        return HeartbeatStateStore(state_dir=temp_state_dir)

    @pytest.fixture
    def sample_heartbeats(self):
        return {
            "forge:tech": make_heartbeat(agent_id="forge:tech"),
            "forge:game": make_heartbeat(agent_id="forge:game", agent_type="opencode"),
        }

    @pytest.fixture
    def sample_summary(self, now) -> FleetHealthSummary:
        return FleetHealthSummary(timestamp=now, total_agents=2, by_status={"healthy": 2})

    def test_state_dir_is_created(self, temp_state_dir):
        new_dir = temp_state_dir / "nested" / "state"
        store = HeartbeatStateStore(state_dir=new_dir)
        assert new_dir.exists()

    def test_save_creates_file(self, store, sample_heartbeats, sample_summary):
        store.save(sample_heartbeats, sample_summary)
        assert store.state_file.exists()

    def test_save_and_load_roundtrip(self, store, sample_heartbeats, sample_summary):
        store.save(sample_heartbeats, sample_summary)
        loaded, loaded_summary = store.load()
        assert set(loaded.keys()) == {"forge:tech", "forge:game"}
        assert loaded["forge:tech"].agent_type == "claude"
        assert loaded["forge:game"].agent_type == "opencode"
        assert loaded_summary is not None
        assert loaded_summary.total_agents == 2

    def test_load_missing_file_returns_empty(self, temp_state_dir):
        store = HeartbeatStateStore(state_dir=temp_state_dir)
        hbs, summary = store.load()
        assert hbs == {}
        assert summary is None

    def test_load_corrupted_json(self, store):
        store.state_file.write_text("not valid json{{{")
        hbs, summary = store.load()
        assert hbs == {}
        assert summary is None

    def test_save_write_error_does_not_raise(self, store, sample_heartbeats, sample_summary):
        with patch("builtins.open", side_effect=OSError("disk full")):
            store.save(sample_heartbeats, sample_summary)  # should not raise

    def test_save_produces_valid_json(self, store, sample_heartbeats, sample_summary):
        store.save(sample_heartbeats, sample_summary)
        data = json.loads(store.state_file.read_text())
        assert data["version"] == "1.0"
        assert "agents" in data
        assert "fleet_summary" in data

    def test_load_state_without_fleet_summary(self, store, sample_heartbeats):
        state = {
            "version": "1.0",
            "last_updated": datetime.now(UTC).isoformat(),
            "agents": {k: v.to_dict() for k, v in sample_heartbeats.items()},
        }
        store.state_file.write_text(json.dumps(state))
        hbs, summary = store.load()
        assert len(hbs) == 2
        assert summary is None

    def test_default_state_dir_uses_forge_fleet(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("pathlib.Path.cwd", return_value=Path(tmp)):
                s = HeartbeatStateStore()
                assert ".forge/fleet" in str(s.state_dir)


# =============================================================================
# 14. HeartbeatCoordinator — Registration and Queries
# =============================================================================


class TestHeartbeatCoordinatorRegistration:
    def test_init_empty_heartbeats(self, coordinator):
        assert coordinator._heartbeats == {}
        assert coordinator._running is False

    def test_init_loads_previous_state(self, temp_state_dir, now):
        store = HeartbeatStateStore(state_dir=temp_state_dir)
        hb = make_heartbeat(agent_id="forge:tech")
        summary = FleetHealthSummary(timestamp=now, total_agents=1)
        store.save({"forge:tech": hb}, summary)
        coord = HeartbeatCoordinator(state_dir=temp_state_dir)
        assert "forge:tech" in coord._heartbeats

    def test_register_agent(self, coordinator):
        coordinator.register_agent("forge:tech", agent_type="claude")
        hb = coordinator.get_heartbeat("forge:tech")
        assert hb is not None
        assert hb.agent_type == "claude"
        assert hb.agent_id == "forge:tech"

    def test_register_agent_default_type(self, coordinator):
        coordinator.register_agent("forge:game")
        assert coordinator.get_heartbeat("forge:game").agent_type == "claude"

    def test_unregister_existing_agent(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator.unregister_agent("forge:tech")
        assert coordinator.get_heartbeat("forge:tech") is None

    def test_unregister_nonexistent_no_error(self, coordinator):
        coordinator.unregister_agent("nonexistent:agent")

    def test_get_heartbeat_missing_returns_none(self, coordinator):
        assert coordinator.get_heartbeat("missing:agent") is None

    def test_get_all_heartbeats_empty(self, coordinator):
        assert coordinator.get_all_heartbeats() == []

    def test_get_all_heartbeats_multiple(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator.register_agent("forge:game")
        assert len(coordinator.get_all_heartbeats()) == 2


# =============================================================================
# 15. HeartbeatCoordinator — Fleet Summary
# =============================================================================


class TestHeartbeatCoordinatorFleetSummary:
    def test_empty_fleet(self, coordinator):
        s = coordinator.get_fleet_summary()
        assert s.total_agents == 0
        assert s.by_status == {}
        assert s.agents_needing_attention == []

    def test_counts_by_status(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator.register_agent("forge:game")
        coordinator._heartbeats["forge:tech"].status = HealthStatus.HEALTHY
        coordinator._heartbeats["forge:game"].status = HealthStatus.STUCK
        s = coordinator.get_fleet_summary()
        assert s.by_status.get("healthy") == 1
        assert s.by_status.get("stuck") == 1

    def test_stuck_agent_in_attention(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator._heartbeats["forge:tech"].status = HealthStatus.STUCK
        coordinator._heartbeats["forge:tech"].needs_nudge = True
        s = coordinator.get_fleet_summary()
        assert "forge:tech" in s.agents_needing_attention
        assert any(a["action"] == "nudge" for a in s.recommended_actions)

    def test_exhausted_handoff_recommended(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator._heartbeats["forge:tech"].status = HealthStatus.EXHAUSTED
        coordinator._heartbeats["forge:tech"].needs_handoff = True
        coordinator._heartbeats["forge:tech"].context_percentage = 95
        s = coordinator.get_fleet_summary()
        assert any(a["action"] == "handoff" for a in s.recommended_actions)

    def test_restart_recommended(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator._heartbeats["forge:tech"].status = HealthStatus.STUCK
        coordinator._heartbeats["forge:tech"].needs_restart = True
        s = coordinator.get_fleet_summary()
        assert any(a["action"] == "restart" for a in s.recommended_actions)

    def test_failed_agent_in_attention(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator._heartbeats["forge:tech"].status = HealthStatus.FAILED
        s = coordinator.get_fleet_summary()
        assert "forge:tech" in s.agents_needing_attention


# =============================================================================
# 16. HeartbeatCoordinator — execute_action
# =============================================================================


class TestHeartbeatCoordinatorExecuteAction:
    @pytest.mark.asyncio
    async def test_execute_action_missing_agent(self, coordinator):
        result = await coordinator.execute_action("missing:agent", "nudge")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execute_action_registered_agent(self, coordinator):
        coordinator.register_agent("forge:tech")
        proc = make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await coordinator.execute_action("forge:tech", "nudge")
        assert result.action == "nudge"
        assert result.agent_id == "forge:tech"

    @pytest.mark.asyncio
    async def test_execute_action_delegates_to_engine(self, coordinator, heartbeat):
        coordinator._heartbeats[heartbeat.agent_id] = heartbeat
        mock_result = ActionResult(success=True, action="nudge", agent_id=heartbeat.agent_id)
        with patch.object(coordinator.action_engine, "execute", new=AsyncMock(return_value=mock_result)):
            result = await coordinator.execute_action(heartbeat.agent_id, "nudge")
        assert result.success is True


# =============================================================================
# 17. HeartbeatCoordinator — Lifecycle (start/stop)
# =============================================================================


class TestHeartbeatCoordinatorLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running(self, coordinator):
        await coordinator.start()
        assert coordinator._running is True
        assert coordinator._task is not None
        await coordinator.stop()

    @pytest.mark.asyncio
    async def test_start_twice_no_second_task(self, coordinator):
        await coordinator.start()
        task_before = coordinator._task
        await coordinator.start()  # second call should be no-op
        assert coordinator._task is task_before
        await coordinator.stop()

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, coordinator):
        await coordinator.start()
        await coordinator.stop()
        assert coordinator._running is False

    @pytest.mark.asyncio
    async def test_stop_with_no_task(self, coordinator):
        coordinator._running = True
        coordinator._task = None
        await coordinator.stop()
        assert coordinator._running is False


# =============================================================================
# 18. HeartbeatCoordinator — _get_tmux_sessions
# =============================================================================


class TestHeartbeatCoordinatorTmuxSessions:
    @pytest.mark.asyncio
    async def test_filters_forge_sessions(self, coordinator):
        # The source checks `"forge" in line.lower()`, so "forge:tech" matches.
        # Completely unrelated sessions (no forge/tech/game keyword) are excluded.
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"forge:tech\nunrelated:session\n", b""))
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            sessions = await coordinator._get_tmux_sessions()
        assert "forge:tech" in sessions
        assert "unrelated:session" not in sessions

    @pytest.mark.asyncio
    async def test_includes_tech_and_game(self, coordinator):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"session:tech\nsession:game\nsession:other\n", b""))
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            sessions = await coordinator._get_tmux_sessions()
        assert "session:tech" in sessions
        assert "session:game" in sessions

    @pytest.mark.asyncio
    async def test_returns_empty_on_tmux_error(self, coordinator):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no tmux")):
            sessions = await coordinator._get_tmux_sessions()
        assert sessions == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_nonzero_rc(self, coordinator):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_proc.returncode = 1
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            sessions = await coordinator._get_tmux_sessions()
        assert sessions == []


# =============================================================================
# 19. HeartbeatCoordinator — _check_agent
# =============================================================================


class TestHeartbeatCoordinatorCheckAgent:
    @pytest.mark.asyncio
    async def test_creates_new_heartbeat(self, coordinator):
        probe_result = ProbeResult(
            output="claude working", agent_type="claude",
            is_active=True, is_idle=False, context_percentage=20, current_task="coding",
        )
        with patch.object(coordinator.probe, "probe", new=AsyncMock(return_value=probe_result)), \
             patch.object(coordinator.evaluator, "evaluate",
                          return_value=EvaluationResult(status=HealthStatus.HEALTHY)):
            await coordinator._check_agent("forge:tech")

        hb = coordinator._heartbeats.get("forge:tech")
        assert hb is not None
        assert hb.agent_type == "claude"
        assert hb.context_percentage == 20
        assert hb.current_task == "coding"
        assert hb.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_updates_existing_heartbeat(self, coordinator):
        coordinator.register_agent("forge:tech", agent_type="claude")
        probe_result = ProbeResult(
            output="new output", agent_type="opencode",
            is_active=False, is_idle=True, context_percentage=None, current_task=None,
        )
        with patch.object(coordinator.probe, "probe", new=AsyncMock(return_value=probe_result)), \
             patch.object(coordinator.evaluator, "evaluate",
                          return_value=EvaluationResult(status=HealthStatus.IDLE)):
            await coordinator._check_agent("forge:tech")

        hb = coordinator._heartbeats["forge:tech"]
        assert hb.agent_type == "opencode"
        assert hb.status == HealthStatus.IDLE

    @pytest.mark.asyncio
    async def test_auto_actions_disabled(self, coordinator):
        probe_result = ProbeResult(output="", agent_type="claude", is_active=False, is_idle=True)
        action = Action(type="nudge", priority="high", delay=0)
        eval_result = EvaluationResult(status=HealthStatus.STUCK, actions=[action])

        with patch.object(coordinator.probe, "probe", new=AsyncMock(return_value=probe_result)), \
             patch.object(coordinator.evaluator, "evaluate", return_value=eval_result), \
             patch.object(coordinator.action_engine, "execute", new=AsyncMock()) as mock_exec:
            await coordinator._check_agent("forge:tech")

        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_actions_enabled_no_delay(self, temp_state_dir):
        coord = HeartbeatCoordinator(state_dir=temp_state_dir, auto_actions=True)
        coord.register_agent("forge:tech")

        probe_result = ProbeResult(output="", agent_type="claude", is_active=False, is_idle=True)
        action = Action(type="nudge", priority="high", delay=0)
        eval_result = EvaluationResult(status=HealthStatus.STUCK, actions=[action])

        proc = make_mock_proc(returncode=0)
        with patch.object(coord.probe, "probe", new=AsyncMock(return_value=probe_result)), \
             patch.object(coord.evaluator, "evaluate", return_value=eval_result), \
             patch("asyncio.create_subprocess_exec", return_value=proc):
            await coord._check_agent("forge:tech")
        # No assertion needed — just ensure no exception is raised


# =============================================================================
# 20. HeartbeatCoordinator — _delayed_action
# =============================================================================


class TestHeartbeatCoordinatorDelayedAction:
    @pytest.mark.asyncio
    async def test_executes_when_needs_restart(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator._heartbeats["forge:tech"].needs_restart = True
        action = Action(type="restart", priority="medium", delay=1)
        hb = coordinator._heartbeats["forge:tech"]
        proc = make_mock_proc(returncode=0)
        with patch("asyncio.sleep", new=AsyncMock()), \
             patch("asyncio.create_subprocess_exec", return_value=proc):
            await coordinator._delayed_action(action, hb)

    @pytest.mark.asyncio
    async def test_skips_when_needs_restart_false(self, coordinator):
        coordinator.register_agent("forge:tech")
        coordinator._heartbeats["forge:tech"].needs_restart = False
        action = Action(type="restart", priority="medium", delay=1)
        hb = coordinator._heartbeats["forge:tech"]
        with patch("asyncio.sleep", new=AsyncMock()), \
             patch.object(coordinator.action_engine, "execute", new=AsyncMock()) as mock_exec:
            await coordinator._delayed_action(action, hb)
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_agent_removed(self, coordinator):
        hb = make_heartbeat(agent_id="gone:agent", needs_restart=True)
        action = Action(type="restart", priority="medium", delay=1)
        with patch("asyncio.sleep", new=AsyncMock()), \
             patch.object(coordinator.action_engine, "execute", new=AsyncMock()) as mock_exec:
            await coordinator._delayed_action(action, hb)
        mock_exec.assert_not_called()


# =============================================================================
# 21. Global Singleton
# =============================================================================


class TestGlobalCoordinator:
    def setup_method(self):
        reset_heartbeat_coordinator()

    def teardown_method(self):
        reset_heartbeat_coordinator()

    def test_get_returns_coordinator(self):
        coord = get_heartbeat_coordinator()
        assert isinstance(coord, HeartbeatCoordinator)

    def test_get_returns_same_instance(self):
        c1 = get_heartbeat_coordinator()
        c2 = get_heartbeat_coordinator()
        assert c1 is c2

    def test_reset_creates_new_instance(self):
        c1 = get_heartbeat_coordinator()
        reset_heartbeat_coordinator()
        c2 = get_heartbeat_coordinator()
        assert c1 is not c2

    def test_reset_allows_fresh_singleton(self):
        get_heartbeat_coordinator()
        reset_heartbeat_coordinator()
        coord = get_heartbeat_coordinator()
        assert coord is not None


# =============================================================================
# 22. Integration Tests
# =============================================================================


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_monitoring_cycle(self, coordinator, temp_state_dir):
        coordinator.register_agent("forge:integration", "claude")

        with patch.object(coordinator, "_get_tmux_sessions",
                          new=AsyncMock(return_value=["forge:integration"])):
            with patch.object(coordinator.probe, "probe",
                              new=AsyncMock(return_value=ProbeResult(
                                  output="Working on task...",
                                  agent_type="claude",
                                  is_active=True,
                                  is_idle=False,
                                  context_percentage=25,
                              ))):
                await coordinator._check_all_agents()

        hb = coordinator.get_heartbeat("forge:integration")
        assert hb is not None
        assert hb.status == HealthStatus.HEALTHY
        assert (temp_state_dir / "heartbeat_state.json").exists()

    @pytest.mark.asyncio
    async def test_stale_agent_detection(self, coordinator, now):
        stale_time = now - timedelta(minutes=10)
        hb = AgentHeartbeat(
            agent_id="forge:stale",
            agent_type="claude",
            tmux_target="forge:stale",
            last_heartbeat=stale_time,
            last_activity=stale_time,
            session_started=now - timedelta(hours=1),
        )
        coordinator._heartbeats[hb.agent_id] = hb
        result = coordinator.evaluator.evaluate(hb)
        assert result.status == HealthStatus.STUCK
        assert hb.stale_duration_seconds > 300

    @pytest.mark.asyncio
    async def test_context_exhaustion_workflow(self, coordinator, now):
        hb = AgentHeartbeat(
            agent_id="forge:exhausted",
            agent_type="claude",
            tmux_target="forge:exhausted",
            last_heartbeat=now,
            last_activity=now,
            session_started=now - timedelta(hours=2),
            context_percentage=95,
        )
        coordinator._heartbeats[hb.agent_id] = hb
        result = coordinator.evaluator.evaluate(hb)
        assert result.status == HealthStatus.EXHAUSTED
        assert hb.needs_handoff is True
        assert any(a.type == "handoff" for a in result.actions)

    @pytest.mark.asyncio
    async def test_check_all_agents_handles_tmux_error(self, coordinator):
        with patch.object(coordinator, "_get_tmux_sessions",
                          new=AsyncMock(side_effect=RuntimeError("tmux unavailable"))):
            # Should not raise — exception is caught inside _check_all_agents
            await coordinator._check_all_agents()
