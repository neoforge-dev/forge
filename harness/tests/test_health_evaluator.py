"""
Tests for Health Evaluator Module
=================================

Comprehensive tests for the FORGE agent health evaluation system.
"""

from datetime import UTC, datetime, timedelta

import pytest

from forge_harness.health_evaluator import (
    # Data classes
    Action,
    ActionPriority,
    ActionType,
    AgentHeartbeat,
    EvaluationResult,
    EvaluatorConfig,
    FleetHealthSummary,
    # Main class
    HealthEvaluator,
    # Enums
    HealthStatus,
    # Factory functions
    create_evaluator,
    create_heartbeat,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def now():
    """Fixed timestamp for deterministic tests."""
    return datetime(2026, 1, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def evaluator():
    """Default health evaluator."""
    return HealthEvaluator()


@pytest.fixture
def custom_config():
    """Custom evaluator configuration."""
    return EvaluatorConfig(
        stale_warning_seconds=60,
        stale_critical_seconds=180,
        context_warning_percent=40,
        context_critical_percent=80,
    )


@pytest.fixture
def healthy_heartbeat(now):
    """Heartbeat for a healthy agent."""
    return AgentHeartbeat(
        agent_id="forge:tech",
        agent_type="claude",
        tmux_target="forge:tech",
        last_heartbeat=now,
        last_activity=now - timedelta(seconds=10),
        session_started=now - timedelta(hours=1),
        context_percentage=30,
        current_task="Implementing feature X",
    )


@pytest.fixture
def idle_heartbeat(now):
    """Heartbeat for an idle agent."""
    return AgentHeartbeat(
        agent_id="forge:product",
        agent_type="claude",
        last_heartbeat=now,
        last_activity=now - timedelta(seconds=90),
        context_percentage=20,
        current_task=None,  # No task = idle
    )


@pytest.fixture
def stale_heartbeat(now):
    """Heartbeat for a stale (warning) agent."""
    return AgentHeartbeat(
        agent_id="forge:qa",
        agent_type="claude",
        last_heartbeat=now - timedelta(seconds=150),
        last_activity=now - timedelta(seconds=150),
        context_percentage=25,
        current_task="Running tests",
    )


@pytest.fixture
def stuck_heartbeat(now):
    """Heartbeat for a stuck agent."""
    return AgentHeartbeat(
        agent_id="forge:content",
        agent_type="pi",
        last_heartbeat=now - timedelta(seconds=400),
        last_activity=now - timedelta(seconds=400),
        context_percentage=45,
        current_task="Writing blog post",
    )


@pytest.fixture
def context_warning_heartbeat(now):
    """Heartbeat for agent with context warning."""
    return AgentHeartbeat(
        agent_id="forge:debug",
        agent_type="claude",
        last_heartbeat=now,
        last_activity=now - timedelta(seconds=5),
        context_percentage=65,
        current_task="Debugging auth issue",
    )


@pytest.fixture
def exhausted_heartbeat(now):
    """Heartbeat for agent with exhausted context."""
    return AgentHeartbeat(
        agent_id="forge:builder",
        agent_type="amp",
        last_heartbeat=now,
        last_activity=now - timedelta(seconds=5),
        context_percentage=95,
        current_task="Complex refactoring",
    )


@pytest.fixture
def failed_heartbeat(now):
    """Heartbeat for a failed/crashed agent."""
    return AgentHeartbeat(
        agent_id="forge:crashed",
        agent_type="claude",
        last_heartbeat=now - timedelta(seconds=60),
        last_activity=now - timedelta(seconds=60),
        context_percentage=40,
        status=HealthStatus.FAILED,
        restart_count=1,
    )


# =============================================================================
# Test HealthStatus Enum
# =============================================================================


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_status_values(self):
        """Test all status values exist."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.IDLE.value == "idle"
        assert HealthStatus.WARNING.value == "warning"
        assert HealthStatus.STUCK.value == "stuck"
        assert HealthStatus.EXHAUSTED.value == "exhausted"
        assert HealthStatus.FAILED.value == "failed"
        assert HealthStatus.UNKNOWN.value == "unknown"

    def test_status_emoji(self):
        """Test emoji property for each status."""
        assert HealthStatus.HEALTHY.emoji == "✅"
        assert HealthStatus.IDLE.emoji == "💤"
        assert HealthStatus.WARNING.emoji == "⚠️"
        assert HealthStatus.STUCK.emoji == "🔴"
        assert HealthStatus.EXHAUSTED.emoji == "🆘"
        assert HealthStatus.FAILED.emoji == "💀"
        assert HealthStatus.UNKNOWN.emoji == "❓"

    def test_status_severity(self):
        """Test severity ordering."""
        assert HealthStatus.HEALTHY.severity < HealthStatus.IDLE.severity
        assert HealthStatus.IDLE.severity < HealthStatus.WARNING.severity
        assert HealthStatus.WARNING.severity < HealthStatus.STUCK.severity
        assert HealthStatus.STUCK.severity < HealthStatus.EXHAUSTED.severity
        assert HealthStatus.EXHAUSTED.severity < HealthStatus.FAILED.severity

    def test_needs_attention(self):
        """Test needs_attention property."""
        assert not HealthStatus.HEALTHY.needs_attention
        assert not HealthStatus.IDLE.needs_attention
        assert not HealthStatus.WARNING.needs_attention
        assert HealthStatus.STUCK.needs_attention
        assert HealthStatus.EXHAUSTED.needs_attention
        assert HealthStatus.FAILED.needs_attention

    def test_string_conversion(self):
        """Test string conversion."""
        assert str(HealthStatus.HEALTHY) == "healthy"
        assert str(HealthStatus.EXHAUSTED) == "exhausted"


# =============================================================================
# Test ActionType and ActionPriority Enums
# =============================================================================


class TestActionEnums:
    """Tests for action-related enums."""

    def test_action_types(self):
        """Test all action types."""
        assert ActionType.NUDGE.value == "nudge"
        assert ActionType.HANDOFF.value == "handoff"
        assert ActionType.HANDOFF_WARNING.value == "handoff_warning"
        assert ActionType.RESTART.value == "restart"
        assert ActionType.KILL.value == "kill"
        assert ActionType.NOTIFY.value == "notify"

    def test_action_priorities(self):
        """Test action priority weights."""
        assert ActionPriority.LOW.weight < ActionPriority.MEDIUM.weight
        assert ActionPriority.MEDIUM.weight < ActionPriority.HIGH.weight
        assert ActionPriority.HIGH.weight < ActionPriority.CRITICAL.weight


# =============================================================================
# Test Action Data Class
# =============================================================================


class TestAction:
    """Tests for Action data class."""

    def test_action_creation(self):
        """Test basic action creation."""
        action = Action(
            type=ActionType.NUDGE,
            priority=ActionPriority.HIGH,
            reason="Agent stuck",
        )
        assert action.type == ActionType.NUDGE
        assert action.priority == ActionPriority.HIGH
        assert action.reason == "Agent stuck"
        assert action.delay_seconds == 0
        assert action.metadata == {}

    def test_action_to_dict(self):
        """Test action serialization."""
        action = Action(
            type=ActionType.HANDOFF,
            priority=ActionPriority.CRITICAL,
            delay_seconds=30,
            reason="Context exhausted",
            metadata={"context": 95},
        )
        data = action.to_dict()

        assert data["type"] == "handoff"
        assert data["priority"] == "critical"
        assert data["delay_seconds"] == 30
        assert data["reason"] == "Context exhausted"
        assert data["metadata"] == {"context": 95}

    def test_action_from_dict(self):
        """Test action deserialization."""
        data = {
            "type": "restart",
            "priority": "medium",
            "delay_seconds": 60,
            "reason": "Agent unresponsive",
        }
        action = Action.from_dict(data)

        assert action.type == ActionType.RESTART
        assert action.priority == ActionPriority.MEDIUM
        assert action.delay_seconds == 60


# =============================================================================
# Test EvaluationResult Data Class
# =============================================================================


class TestEvaluationResult:
    """Tests for EvaluationResult data class."""

    def test_evaluation_result_creation(self):
        """Test basic result creation."""
        result = EvaluationResult(
            status=HealthStatus.WARNING,
            reason="Context approaching limit",
        )
        assert result.status == HealthStatus.WARNING
        assert result.reason == "Context approaching limit"
        assert result.actions == []
        assert result.confidence == 1.0

    def test_has_critical_actions(self):
        """Test critical action detection."""
        result_no_critical = EvaluationResult(
            status=HealthStatus.WARNING,
            actions=[Action(type=ActionType.NUDGE, priority=ActionPriority.LOW)],
        )
        assert not result_no_critical.has_critical_actions

        result_with_critical = EvaluationResult(
            status=HealthStatus.EXHAUSTED,
            actions=[Action(type=ActionType.HANDOFF, priority=ActionPriority.CRITICAL)],
        )
        assert result_with_critical.has_critical_actions

    def test_sorted_actions(self):
        """Test action sorting by priority."""
        result = EvaluationResult(
            status=HealthStatus.STUCK,
            actions=[
                Action(type=ActionType.NOTIFY, priority=ActionPriority.LOW),
                Action(type=ActionType.RESTART, priority=ActionPriority.CRITICAL),
                Action(type=ActionType.NUDGE, priority=ActionPriority.HIGH),
            ],
        )
        sorted_actions = result.sorted_actions

        assert sorted_actions[0].priority == ActionPriority.CRITICAL
        assert sorted_actions[1].priority == ActionPriority.HIGH
        assert sorted_actions[2].priority == ActionPriority.LOW

    def test_evaluation_result_to_dict(self):
        """Test result serialization."""
        result = EvaluationResult(
            status=HealthStatus.HEALTHY,
            actions=[],
            reason="All good",
            confidence=0.95,
        )
        data = result.to_dict()

        assert data["status"] == "healthy"
        assert data["reason"] == "All good"
        assert data["confidence"] == 0.95
        assert "evaluated_at" in data


# =============================================================================
# Test AgentHeartbeat Data Class
# =============================================================================


class TestAgentHeartbeat:
    """Tests for AgentHeartbeat data class."""

    def test_heartbeat_creation(self, now):
        """Test basic heartbeat creation."""
        heartbeat = AgentHeartbeat(
            agent_id="test:agent",
            agent_type="claude",
            context_percentage=50,
        )
        assert heartbeat.agent_id == "test:agent"
        assert heartbeat.agent_type == "claude"
        assert heartbeat.tmux_target == "test:agent"  # Auto-set
        assert heartbeat.context_percentage == 50
        assert heartbeat.status == HealthStatus.UNKNOWN

    def test_heartbeat_to_dict(self, healthy_heartbeat):
        """Test heartbeat serialization."""
        data = healthy_heartbeat.to_dict()

        assert data["agent_id"] == "forge:tech"
        assert data["agent_type"] == "claude"
        assert data["context_percentage"] == 30
        assert data["current_task"] == "Implementing feature X"
        assert "last_heartbeat" in data
        assert "last_activity" in data

    def test_heartbeat_from_dict(self, healthy_heartbeat):
        """Test heartbeat deserialization round-trip."""
        data = healthy_heartbeat.to_dict()
        restored = AgentHeartbeat.from_dict(data)

        assert restored.agent_id == healthy_heartbeat.agent_id
        assert restored.agent_type == healthy_heartbeat.agent_type
        assert restored.context_percentage == healthy_heartbeat.context_percentage
        assert restored.current_task == healthy_heartbeat.current_task

    def test_heartbeat_json_roundtrip(self, healthy_heartbeat):
        """Test JSON serialization round-trip."""
        json_str = healthy_heartbeat.to_json()
        restored = AgentHeartbeat.from_json(json_str)

        assert restored.agent_id == healthy_heartbeat.agent_id
        assert restored.context_percentage == healthy_heartbeat.context_percentage

    def test_update_stale_duration(self, now):
        """Test stale duration update."""
        heartbeat = AgentHeartbeat(
            agent_id="test:agent",
            last_activity=now - timedelta(seconds=120),
        )
        heartbeat.update_stale_duration(now)

        assert heartbeat.stale_duration_seconds == 120


# =============================================================================
# Test FleetHealthSummary Data Class
# =============================================================================


class TestFleetHealthSummary:
    """Tests for FleetHealthSummary data class."""

    def test_summary_creation(self):
        """Test basic summary creation."""
        summary = FleetHealthSummary(
            total_agents=5,
            by_status={
                "healthy": 3,
                "warning": 1,
                "stuck": 1,
            },
            critical_count=1,
        )
        assert summary.total_agents == 5
        assert summary.by_status["healthy"] == 3

    def test_healthy_percentage(self):
        """Test healthy percentage calculation."""
        summary = FleetHealthSummary(
            total_agents=10,
            by_status={"healthy": 7, "warning": 2, "stuck": 1},
        )
        assert summary.healthy_percentage == 70.0

        empty_summary = FleetHealthSummary(total_agents=0)
        assert empty_summary.healthy_percentage == 0.0

    def test_fleet_status(self):
        """Test overall fleet status determination."""
        # All healthy/idle
        healthy_fleet = FleetHealthSummary(
            total_agents=3,
            by_status={"healthy": 2, "idle": 1},
            critical_count=0,
        )
        assert healthy_fleet.fleet_status == HealthStatus.HEALTHY

        # Has critical
        critical_fleet = FleetHealthSummary(
            total_agents=3,
            by_status={"healthy": 2, "exhausted": 1},
            critical_count=1,
        )
        assert critical_fleet.fleet_status == HealthStatus.WARNING

        # Empty fleet
        empty_fleet = FleetHealthSummary(total_agents=0)
        assert empty_fleet.fleet_status == HealthStatus.UNKNOWN

    def test_summary_to_dict(self):
        """Test summary serialization."""
        summary = FleetHealthSummary(
            total_agents=5,
            by_status={"healthy": 5},
            average_context=35.5,
        )
        data = summary.to_dict()

        assert data["total_agents"] == 5
        assert data["average_context"] == 35.5
        assert "timestamp" in data


# =============================================================================
# Test EvaluatorConfig Data Class
# =============================================================================


class TestEvaluatorConfig:
    """Tests for EvaluatorConfig data class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = EvaluatorConfig()

        assert config.stale_warning_seconds == 120
        assert config.stale_critical_seconds == 300
        assert config.context_warning_percent == 50
        assert config.context_critical_percent == 90
        assert config.enable_auto_nudge is True
        assert config.enable_auto_handoff is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = EvaluatorConfig(
            stale_warning_seconds=60,
            context_critical_percent=80,
        )
        assert config.stale_warning_seconds == 60
        assert config.context_critical_percent == 80

    def test_config_roundtrip(self):
        """Test config serialization."""
        config = EvaluatorConfig(stale_warning_seconds=90)
        data = config.to_dict()
        restored = EvaluatorConfig.from_dict(data)

        assert restored.stale_warning_seconds == 90


# =============================================================================
# Test HealthEvaluator - Healthy Cases
# =============================================================================


class TestHealthEvaluatorHealthy:
    """Tests for healthy agent evaluation."""

    def test_evaluate_healthy_agent(self, evaluator, healthy_heartbeat, now):
        """Test evaluation of healthy agent."""
        result = evaluator.evaluate(healthy_heartbeat, now)

        assert result.status == HealthStatus.HEALTHY
        assert len(result.actions) == 0
        assert "operating normally" in result.reason.lower()

    def test_healthy_heartbeat_flags(self, evaluator, healthy_heartbeat, now):
        """Test flags are set correctly for healthy agent."""
        evaluator.evaluate(healthy_heartbeat, now)

        assert healthy_heartbeat.status == HealthStatus.HEALTHY
        assert not healthy_heartbeat.is_stale
        assert not healthy_heartbeat.needs_nudge
        assert not healthy_heartbeat.needs_handoff
        assert not healthy_heartbeat.needs_restart


# =============================================================================
# Test HealthEvaluator - Idle Cases
# =============================================================================


class TestHealthEvaluatorIdle:
    """Tests for idle agent evaluation."""

    def test_evaluate_idle_agent(self, evaluator, idle_heartbeat, now):
        """Test evaluation of idle agent."""
        result = evaluator.evaluate(idle_heartbeat, now)

        assert result.status == HealthStatus.IDLE
        assert len(result.actions) == 0
        assert "waiting for input" in result.reason.lower()

    def test_idle_with_task_is_not_idle(self, evaluator, now):
        """Agent with task but inactive is not idle, it's stale."""
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            current_task="Working on something",
            stale_seconds=90,
        )
        result = evaluator.evaluate(heartbeat, now)

        # Not idle because has a task
        assert result.status != HealthStatus.IDLE


# =============================================================================
# Test HealthEvaluator - Stale/Warning Cases
# =============================================================================


class TestHealthEvaluatorStale:
    """Tests for stale agent evaluation."""

    def test_evaluate_stale_warning(self, evaluator, stale_heartbeat, now):
        """Test evaluation of stale agent (warning level)."""
        result = evaluator.evaluate(stale_heartbeat, now)

        assert result.status == HealthStatus.WARNING
        assert any(a.type == ActionType.NUDGE for a in result.actions)

    def test_stale_nudge_priority(self, evaluator, stale_heartbeat, now):
        """Stale nudge should be low priority."""
        result = evaluator.evaluate(stale_heartbeat, now)

        nudge_action = next(a for a in result.actions if a.type == ActionType.NUDGE)
        assert nudge_action.priority == ActionPriority.LOW

    def test_stale_heartbeat_flags(self, evaluator, stale_heartbeat, now):
        """Test flags for stale agent."""
        evaluator.evaluate(stale_heartbeat, now)

        assert stale_heartbeat.is_stale is True
        assert stale_heartbeat.needs_nudge is True


# =============================================================================
# Test HealthEvaluator - Stuck Cases
# =============================================================================


class TestHealthEvaluatorStuck:
    """Tests for stuck agent evaluation."""

    def test_evaluate_stuck_agent(self, evaluator, stuck_heartbeat, now):
        """Test evaluation of stuck agent."""
        result = evaluator.evaluate(stuck_heartbeat, now)

        assert result.status == HealthStatus.STUCK
        assert any(a.type == ActionType.NUDGE for a in result.actions)
        assert any(a.type == ActionType.RESTART for a in result.actions)

    def test_stuck_nudge_high_priority(self, evaluator, stuck_heartbeat, now):
        """Stuck nudge should be high priority."""
        result = evaluator.evaluate(stuck_heartbeat, now)

        nudge_action = next(a for a in result.actions if a.type == ActionType.NUDGE)
        assert nudge_action.priority == ActionPriority.HIGH

    def test_stuck_restart_delayed(self, evaluator, stuck_heartbeat, now):
        """Restart action should be delayed."""
        result = evaluator.evaluate(stuck_heartbeat, now)

        restart_action = next(a for a in result.actions if a.type == ActionType.RESTART)
        assert restart_action.delay_seconds > 0

    def test_stuck_heartbeat_flags(self, evaluator, stuck_heartbeat, now):
        """Test flags for stuck agent."""
        evaluator.evaluate(stuck_heartbeat, now)

        assert stuck_heartbeat.needs_nudge is True
        assert stuck_heartbeat.needs_restart is True


# =============================================================================
# Test HealthEvaluator - Context Warning Cases
# =============================================================================


class TestHealthEvaluatorContextWarning:
    """Tests for context warning evaluation."""

    def test_evaluate_context_warning(self, evaluator, context_warning_heartbeat, now):
        """Test evaluation of agent with context warning."""
        result = evaluator.evaluate(context_warning_heartbeat, now)

        assert result.status == HealthStatus.WARNING
        assert any(a.type == ActionType.HANDOFF_WARNING for a in result.actions)
        assert "65%" in result.reason

    def test_context_warning_flags(self, evaluator, context_warning_heartbeat, now):
        """Test flags for context warning."""
        evaluator.evaluate(context_warning_heartbeat, now)

        assert context_warning_heartbeat.needs_handoff is True

    def test_context_50_percent_triggers_warning(self, evaluator, now):
        """Exactly 50% context triggers warning."""
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            context_percentage=50,
            current_task="Working",
            stale_seconds=5,
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.WARNING


# =============================================================================
# Test HealthEvaluator - Exhausted Cases
# =============================================================================


class TestHealthEvaluatorExhausted:
    """Tests for exhausted context evaluation."""

    def test_evaluate_exhausted(self, evaluator, exhausted_heartbeat, now):
        """Test evaluation of exhausted agent."""
        result = evaluator.evaluate(exhausted_heartbeat, now)

        assert result.status == HealthStatus.EXHAUSTED
        assert any(a.type == ActionType.HANDOFF for a in result.actions)
        assert any(a.priority == ActionPriority.CRITICAL for a in result.actions)

    def test_exhausted_immediate_handoff(self, evaluator, exhausted_heartbeat, now):
        """Exhausted agent gets immediate handoff action."""
        result = evaluator.evaluate(exhausted_heartbeat, now)

        handoff_action = next(a for a in result.actions if a.type == ActionType.HANDOFF)
        assert handoff_action.delay_seconds == 0
        assert handoff_action.priority == ActionPriority.CRITICAL

    def test_exhausted_notify_action(self, evaluator, exhausted_heartbeat, now):
        """Exhausted agent triggers notification."""
        result = evaluator.evaluate(exhausted_heartbeat, now)

        assert any(a.type == ActionType.NOTIFY for a in result.actions)

    def test_context_90_triggers_exhausted(self, evaluator, now):
        """Exactly 90% context triggers exhausted."""
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            context_percentage=90,
            current_task="Working",
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.EXHAUSTED


# =============================================================================
# Test HealthEvaluator - Failed Cases
# =============================================================================


class TestHealthEvaluatorFailed:
    """Tests for failed agent evaluation."""

    def test_evaluate_failed(self, evaluator, failed_heartbeat, now):
        """Test evaluation of failed agent."""
        result = evaluator.evaluate(failed_heartbeat, now)

        assert result.status == HealthStatus.FAILED
        assert any(a.type == ActionType.RESTART for a in result.actions)

    def test_failed_restart_count_respected(self, evaluator, now):
        """Max restart count prevents further restarts."""
        heartbeat = AgentHeartbeat(
            agent_id="test:agent",
            status=HealthStatus.FAILED,
            restart_count=3,  # At max
        )
        result = evaluator.evaluate(heartbeat, now)

        # Should notify instead of restart
        assert result.status == HealthStatus.FAILED
        assert not any(a.type == ActionType.RESTART for a in result.actions)
        assert any(a.type == ActionType.NOTIFY for a in result.actions)

    def test_failed_increment_restart(self, evaluator, failed_heartbeat, now):
        """Restart action mentions restart count."""
        result = evaluator.evaluate(failed_heartbeat, now)

        restart_action = next(a for a in result.actions if a.type == ActionType.RESTART)
        assert "2/3" in restart_action.reason  # restart_count=1, so next is 2


# =============================================================================
# Test HealthEvaluator - Custom Config
# =============================================================================


class TestHealthEvaluatorCustomConfig:
    """Tests for evaluator with custom configuration."""

    def test_custom_stale_thresholds(self, now):
        """Test custom stale thresholds."""
        config = EvaluatorConfig(
            stale_warning_seconds=30,
            stale_critical_seconds=90,
        )
        evaluator = HealthEvaluator(config)

        # 45 seconds should be warning with custom config
        # Create heartbeat directly to control last_activity precisely
        heartbeat = AgentHeartbeat(
            agent_id="test:agent",
            last_heartbeat=now - timedelta(seconds=45),
            last_activity=now - timedelta(seconds=45),
            current_task="Working",
            context_percentage=20,
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.WARNING

    def test_custom_context_thresholds(self, now):
        """Test custom context thresholds."""
        config = EvaluatorConfig(
            context_warning_percent=30,
            context_critical_percent=70,
        )
        evaluator = HealthEvaluator(config)

        # 35% should be warning with custom config
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            context_percentage=35,
            current_task="Working",
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.WARNING

    def test_disabled_auto_nudge(self, now):
        """Test disabling auto-nudge."""
        config = EvaluatorConfig(enable_auto_nudge=False)
        evaluator = HealthEvaluator(config)

        # Create heartbeat directly to ensure it's considered stuck
        heartbeat = AgentHeartbeat(
            agent_id="test:agent",
            last_heartbeat=now - timedelta(seconds=400),
            last_activity=now - timedelta(seconds=400),
            current_task="Working on something",  # Has a task, so not idle
            context_percentage=20,
        )
        result = evaluator.evaluate(heartbeat, now)

        # Stuck but no nudge action
        assert result.status == HealthStatus.STUCK
        assert not any(a.type == ActionType.NUDGE for a in result.actions)

    def test_disabled_auto_handoff(self, now):
        """Test disabling auto-handoff."""
        config = EvaluatorConfig(enable_auto_handoff=False)
        evaluator = HealthEvaluator(config)

        heartbeat = create_heartbeat(
            agent_id="test:agent",
            context_percentage=95,
        )
        result = evaluator.evaluate(heartbeat, now)

        # Exhausted but no handoff action
        assert result.status == HealthStatus.EXHAUSTED
        assert not any(a.type == ActionType.HANDOFF for a in result.actions)


# =============================================================================
# Test HealthEvaluator - Fleet Evaluation
# =============================================================================


class TestHealthEvaluatorFleet:
    """Tests for fleet-wide evaluation."""

    def test_evaluate_fleet_basic(
        self,
        evaluator,
        healthy_heartbeat,
        idle_heartbeat,
        context_warning_heartbeat,
        now,
    ):
        """Test basic fleet evaluation."""
        heartbeats = [healthy_heartbeat, idle_heartbeat, context_warning_heartbeat]
        summary = evaluator.evaluate_fleet(heartbeats, now)

        assert summary.total_agents == 3
        assert summary.by_status["healthy"] == 1
        assert summary.by_status["idle"] == 1
        assert summary.by_status["warning"] == 1

    def test_fleet_average_context(self, evaluator, now):
        """Test fleet average context calculation."""
        heartbeats = [
            create_heartbeat("a", context_percentage=20),
            create_heartbeat("b", context_percentage=40),
            create_heartbeat("c", context_percentage=60),
        ]
        summary = evaluator.evaluate_fleet(heartbeats, now)

        assert summary.average_context == 40.0

    def test_fleet_agents_needing_attention(
        self,
        evaluator,
        healthy_heartbeat,
        stuck_heartbeat,
        exhausted_heartbeat,
        now,
    ):
        """Test identification of agents needing attention."""
        heartbeats = [healthy_heartbeat, stuck_heartbeat, exhausted_heartbeat]
        summary = evaluator.evaluate_fleet(heartbeats, now)

        assert stuck_heartbeat.agent_id in summary.agents_needing_attention
        assert exhausted_heartbeat.agent_id in summary.agents_needing_attention
        assert healthy_heartbeat.agent_id not in summary.agents_needing_attention

    def test_fleet_recommended_actions(
        self,
        evaluator,
        stuck_heartbeat,
        exhausted_heartbeat,
        now,
    ):
        """Test fleet recommended actions aggregation."""
        heartbeats = [stuck_heartbeat, exhausted_heartbeat]
        summary = evaluator.evaluate_fleet(heartbeats, now)

        # Should have actions from both agents
        assert len(summary.recommended_actions) > 0

        # Critical actions should come first
        first_action = summary.recommended_actions[0]
        assert first_action["priority"] == "critical"

    def test_fleet_critical_count(
        self,
        evaluator,
        healthy_heartbeat,
        stuck_heartbeat,
        exhausted_heartbeat,
        now,
    ):
        """Test critical count tracking."""
        heartbeats = [healthy_heartbeat, stuck_heartbeat, exhausted_heartbeat]
        summary = evaluator.evaluate_fleet(heartbeats, now)

        assert summary.critical_count == 2  # stuck and exhausted

    def test_empty_fleet(self, evaluator, now):
        """Test evaluation of empty fleet."""
        summary = evaluator.evaluate_fleet([], now)

        assert summary.total_agents == 0
        assert summary.average_context == 0.0
        assert summary.fleet_status == HealthStatus.UNKNOWN


# =============================================================================
# Test Factory Functions
# =============================================================================


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_evaluator_defaults(self):
        """Test create_evaluator with defaults."""
        evaluator = create_evaluator()

        assert evaluator.config.stale_warning_seconds == 120
        assert evaluator.config.context_critical_percent == 90

    def test_create_evaluator_custom(self):
        """Test create_evaluator with custom values."""
        evaluator = create_evaluator(
            stale_warning_seconds=60,
            context_critical_percent=80,
        )

        assert evaluator.config.stale_warning_seconds == 60
        assert evaluator.config.context_critical_percent == 80

    def test_create_heartbeat_defaults(self):
        """Test create_heartbeat with defaults."""
        heartbeat = create_heartbeat(agent_id="test:agent")

        assert heartbeat.agent_id == "test:agent"
        assert heartbeat.agent_type == "claude"
        assert heartbeat.context_percentage == 0
        assert heartbeat.stale_duration_seconds == 0

    def test_create_heartbeat_stale(self):
        """Test create_heartbeat with stale_seconds."""
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            stale_seconds=300,
        )
        heartbeat.update_stale_duration()

        # Should be approximately 300 seconds stale
        assert heartbeat.stale_duration_seconds >= 299


# =============================================================================
# Test Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_evaluation_priority_exhausted_over_stuck(self, evaluator, now):
        """Exhausted context takes priority over stuck."""
        heartbeat = AgentHeartbeat(
            agent_id="test:agent",
            last_activity=now - timedelta(seconds=600),  # Very stuck
            context_percentage=95,  # Also exhausted
        )
        result = evaluator.evaluate(heartbeat, now)

        # Exhausted should win
        assert result.status == HealthStatus.EXHAUSTED

    def test_evaluation_priority_failed_over_exhausted(self, evaluator, now):
        """Failed status takes priority over exhausted."""
        heartbeat = AgentHeartbeat(
            agent_id="test:agent",
            status=HealthStatus.FAILED,
            context_percentage=95,
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.FAILED

    def test_context_boundary_49_percent(self, evaluator, now):
        """49% context is healthy, not warning."""
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            context_percentage=49,
            current_task="Working",
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.HEALTHY

    def test_context_boundary_89_percent(self, evaluator, now):
        """89% context is warning, not exhausted."""
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            context_percentage=89,
            current_task="Working",
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.WARNING

    def test_evaluation_count_increments(self, evaluator, healthy_heartbeat, now):
        """Test evaluation count tracking."""
        initial_count = evaluator.evaluation_count

        evaluator.evaluate(healthy_heartbeat, now)
        evaluator.evaluate(healthy_heartbeat, now)

        assert evaluator.evaluation_count == initial_count + 2

    def test_zero_stale_duration(self, evaluator, now):
        """Agent with 0 stale duration is healthy."""
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            stale_seconds=0,
            context_percentage=10,
            current_task="Just started",
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.HEALTHY

    def test_negative_values_handled(self, evaluator, now):
        """Negative values should not crash."""
        heartbeat = AgentHeartbeat(
            agent_id="test:agent",
            context_percentage=-1,  # Invalid but shouldn't crash
        )
        result = evaluator.evaluate(heartbeat, now)

        # Should still evaluate (as healthy due to low context)
        assert result.status in (HealthStatus.HEALTHY, HealthStatus.IDLE, HealthStatus.UNKNOWN)

    def test_context_over_100(self, evaluator, now):
        """Context over 100% treated as exhausted."""
        heartbeat = create_heartbeat(
            agent_id="test:agent",
            context_percentage=110,  # Over 100%
        )
        result = evaluator.evaluate(heartbeat, now)

        assert result.status == HealthStatus.EXHAUSTED


# =============================================================================
# Test Coverage Gap Closers
# =============================================================================


class TestCoverageGaps:
    """Tests targeting the remaining uncovered lines for 100% coverage."""

    def test_action_type_str_conversion(self):
        """Test ActionType __str__ returns the enum value string."""
        assert str(ActionType.NUDGE) == "nudge"
        assert str(ActionType.HANDOFF) == "handoff"
        assert str(ActionType.HANDOFF_WARNING) == "handoff_warning"
        assert str(ActionType.RESTART) == "restart"
        assert str(ActionType.KILL) == "kill"
        assert str(ActionType.NOTIFY) == "notify"

    def test_action_priority_str_conversion(self):
        """Test ActionPriority __str__ returns the enum value string."""
        assert str(ActionPriority.LOW) == "low"
        assert str(ActionPriority.MEDIUM) == "medium"
        assert str(ActionPriority.HIGH) == "high"
        assert str(ActionPriority.CRITICAL) == "critical"

    def test_fleet_summary_from_dict_roundtrip(self):
        """Test FleetHealthSummary from_dict round-trip serialization."""
        now = datetime(2026, 2, 22, 10, 0, 0, tzinfo=UTC)
        original = FleetHealthSummary(
            timestamp=now,
            total_agents=4,
            by_status={"healthy": 2, "warning": 1, "stuck": 1},
            agents_needing_attention=["forge:agent-a"],
            recommended_actions=[{"agent_id": "forge:agent-a", "type": "nudge"}],
            average_context=45.0,
            critical_count=1,
        )
        data = original.to_dict()
        restored = FleetHealthSummary.from_dict(data)

        assert restored.total_agents == 4
        assert restored.by_status == {"healthy": 2, "warning": 1, "stuck": 1}
        assert restored.agents_needing_attention == ["forge:agent-a"]
        assert restored.average_context == 45.0
        assert restored.critical_count == 1
        assert restored.timestamp == now

    def test_fleet_status_warning_when_mixed_but_no_critical(self):
        """Fleet status is WARNING when agents are in warning state but critical_count=0."""
        # No critical issues, but not all agents are healthy/idle — should still be WARNING
        summary = FleetHealthSummary(
            total_agents=3,
            by_status={"healthy": 1, "warning": 2},
            critical_count=0,  # No critical count, but not all healthy+idle
        )
        assert summary.fleet_status == HealthStatus.WARNING

    def test_fleet_summary_from_dict_with_minimal_data(self):
        """Test FleetHealthSummary.from_dict with only required fields."""
        now = datetime(2026, 2, 22, 10, 0, 0, tzinfo=UTC)
        data = {
            "timestamp": now.isoformat(),
            "total_agents": 2,
        }
        summary = FleetHealthSummary.from_dict(data)

        assert summary.total_agents == 2
        assert summary.by_status == {}
        assert summary.agents_needing_attention == []
        assert summary.recommended_actions == []
        assert summary.average_context == 0.0
        assert summary.critical_count == 0
