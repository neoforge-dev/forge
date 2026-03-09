"""
Health Evaluator for FORGE Agents
=================================

Evaluates agent health status based on heartbeat data and recommends
corrective actions. Part of the unified heartbeat monitoring system.

Usage:
    from forge_harness.health_evaluator import (
        HealthStatus,
        AgentHeartbeat,
        HealthEvaluator,
        FleetHealthSummary,
    )

    # Create heartbeat data
    heartbeat = AgentHeartbeat(
        agent_id="forge:tech",
        agent_type="claude",
        tmux_target="forge:tech",
        last_heartbeat=datetime.now(timezone.utc),
        last_activity=datetime.now(timezone.utc),
        session_started=datetime.now(timezone.utc),
        context_percentage=45,
    )

    # Evaluate health
    evaluator = HealthEvaluator()
    result = evaluator.evaluate(heartbeat)

    print(result.status)  # HealthStatus.HEALTHY
    print(result.actions)  # []
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class HealthStatus(str, Enum):
    """Agent health status levels.

    Status progression typically follows:
    HEALTHY -> IDLE -> WARNING -> STUCK -> FAILED

    EXHAUSTED can occur at any point when context threshold is breached.
    """

    HEALTHY = "healthy"  # Working normally, context <50%
    IDLE = "idle"  # Waiting for input, <50% context, <2 min inactive
    WARNING = "warning"  # Context 50-90% OR stale 2-5 min
    STUCK = "stuck"  # No activity for >5 min
    EXHAUSTED = "exhausted"  # Context >90%, needs immediate handoff
    FAILED = "failed"  # Crashed or unreachable
    UNKNOWN = "unknown"  # Cannot determine status

    def __str__(self) -> str:
        return self.value

    @property
    def emoji(self) -> str:
        """Return emoji representation of status."""
        return {
            HealthStatus.HEALTHY: "✅",
            HealthStatus.IDLE: "💤",
            HealthStatus.WARNING: "⚠️",
            HealthStatus.STUCK: "🔴",
            HealthStatus.EXHAUSTED: "🆘",
            HealthStatus.FAILED: "💀",
            HealthStatus.UNKNOWN: "❓",
        }.get(self, "❓")

    @property
    def severity(self) -> int:
        """Return numeric severity (higher = more critical)."""
        return {
            HealthStatus.HEALTHY: 0,
            HealthStatus.IDLE: 1,
            HealthStatus.WARNING: 2,
            HealthStatus.STUCK: 3,
            HealthStatus.EXHAUSTED: 4,
            HealthStatus.FAILED: 5,
            HealthStatus.UNKNOWN: 1,
        }.get(self, 0)

    @property
    def needs_attention(self) -> bool:
        """Return True if status requires human attention."""
        return self in (
            HealthStatus.STUCK,
            HealthStatus.EXHAUSTED,
            HealthStatus.FAILED,
        )


class ActionType(str, Enum):
    """Types of corrective actions."""

    NUDGE = "nudge"  # Send Enter key to wake agent
    HANDOFF = "handoff"  # Trigger /handoff command
    HANDOFF_WARNING = "handoff_warning"  # Warn about approaching handoff
    RESTART = "restart"  # Restart the agent
    KILL = "kill"  # Force kill agent process
    NOTIFY = "notify"  # Send notification (no agent action)

    def __str__(self) -> str:
        return self.value


class ActionPriority(str, Enum):
    """Priority levels for actions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    def __str__(self) -> str:
        return self.value

    @property
    def weight(self) -> int:
        """Return numeric weight for sorting."""
        return {
            ActionPriority.LOW: 1,
            ActionPriority.MEDIUM: 2,
            ActionPriority.HIGH: 3,
            ActionPriority.CRITICAL: 4,
        }.get(self, 0)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class Action:
    """A corrective action to take on an agent.

    Attributes:
        type: Type of action (nudge, handoff, restart, etc.)
        priority: Priority level for execution
        delay_seconds: Optional delay before executing
        reason: Human-readable reason for action
        metadata: Additional action-specific data
    """

    type: ActionType
    priority: ActionPriority = ActionPriority.MEDIUM
    delay_seconds: int = 0
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "type": self.type.value,
            "priority": self.priority.value,
            "delay_seconds": self.delay_seconds,
            "reason": self.reason,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Action:
        """Create from dictionary."""
        return cls(
            type=ActionType(data["type"]),
            priority=ActionPriority(data.get("priority", "medium")),
            delay_seconds=data.get("delay_seconds", 0),
            reason=data.get("reason", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class EvaluationResult:
    """Result of health evaluation.

    Attributes:
        status: Determined health status
        actions: Recommended corrective actions
        reason: Human-readable explanation
        confidence: Confidence in the evaluation (0.0-1.0)
        evaluated_at: When evaluation was performed
    """

    status: HealthStatus
    actions: list[Action] = field(default_factory=list)
    reason: str = ""
    confidence: float = 1.0
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "actions": [a.to_dict() for a in self.actions],
            "reason": self.reason,
            "confidence": self.confidence,
            "evaluated_at": self.evaluated_at.isoformat(),
        }

    @property
    def has_critical_actions(self) -> bool:
        """Return True if any actions are critical priority."""
        return any(a.priority == ActionPriority.CRITICAL for a in self.actions)

    @property
    def sorted_actions(self) -> list[Action]:
        """Return actions sorted by priority (highest first)."""
        return sorted(self.actions, key=lambda a: a.priority.weight, reverse=True)


@dataclass
class AgentHeartbeat:
    """Heartbeat data for a single agent.

    Attributes:
        agent_id: Unique agent identifier (e.g., "forge:tech")
        agent_type: Type of agent (claude, pi, amp, opencode, cursor)
        tmux_target: tmux session:window target
        last_heartbeat: When last heartbeat was received
        last_activity: When agent last showed activity
        session_started: When agent session started
        context_percentage: Current context usage (0-100)
        context_tokens_used: Tokens used (estimated)
        context_tokens_budget: Total token budget
        status: Current evaluated health status
        current_task: Description of current task
        last_output_preview: Last 100 chars of output
        is_stale: Whether heartbeat is stale
        needs_nudge: Whether agent needs a nudge
        needs_handoff: Whether handoff is needed
        needs_restart: Whether restart is needed
        stale_duration_seconds: How long since last activity
        restart_count: Number of restarts in this session
        error_count: Number of errors encountered
    """

    # Identity
    agent_id: str
    agent_type: str = "claude"
    tmux_target: str = ""

    # Timestamps
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))
    session_started: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Context tracking
    context_percentage: int = 0
    context_tokens_used: int = 0
    context_tokens_budget: int = 200000  # Default 200k for Claude

    # Status (evaluated externally or set directly)
    status: HealthStatus = HealthStatus.UNKNOWN
    current_task: str | None = None
    last_output_preview: str | None = None

    # Flags (computed by evaluator)
    is_stale: bool = False
    needs_nudge: bool = False
    needs_handoff: bool = False
    needs_restart: bool = False

    # Metrics
    stale_duration_seconds: int = 0
    restart_count: int = 0
    error_count: int = 0

    def __post_init__(self):
        """Ensure tmux_target is set."""
        if not self.tmux_target:
            self.tmux_target = self.agent_id

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "tmux_target": self.tmux_target,
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "session_started": self.session_started.isoformat(),
            "context_percentage": self.context_percentage,
            "context_tokens_used": self.context_tokens_used,
            "context_tokens_budget": self.context_tokens_budget,
            "status": self.status.value,
            "current_task": self.current_task,
            "last_output_preview": self.last_output_preview,
            "is_stale": self.is_stale,
            "needs_nudge": self.needs_nudge,
            "needs_handoff": self.needs_handoff,
            "needs_restart": self.needs_restart,
            "stale_duration_seconds": self.stale_duration_seconds,
            "restart_count": self.restart_count,
            "error_count": self.error_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentHeartbeat:
        """Create from dictionary."""
        return cls(
            agent_id=data["agent_id"],
            agent_type=data.get("agent_type", "claude"),
            tmux_target=data.get("tmux_target", data["agent_id"]),
            last_heartbeat=datetime.fromisoformat(data["last_heartbeat"]),
            last_activity=datetime.fromisoformat(data["last_activity"]),
            session_started=datetime.fromisoformat(
                data.get("session_started", data["last_heartbeat"])
            ),
            context_percentage=data.get("context_percentage", 0),
            context_tokens_used=data.get("context_tokens_used", 0),
            context_tokens_budget=data.get("context_tokens_budget", 200000),
            status=HealthStatus(data.get("status", "unknown")),
            current_task=data.get("current_task"),
            last_output_preview=data.get("last_output_preview"),
            is_stale=data.get("is_stale", False),
            needs_nudge=data.get("needs_nudge", False),
            needs_handoff=data.get("needs_handoff", False),
            needs_restart=data.get("needs_restart", False),
            stale_duration_seconds=data.get("stale_duration_seconds", 0),
            restart_count=data.get("restart_count", 0),
            error_count=data.get("error_count", 0),
        )

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> AgentHeartbeat:
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def update_stale_duration(self, now: datetime | None = None) -> None:
        """Update stale duration based on current time."""
        now = now or datetime.now(UTC)
        delta = now - self.last_activity
        self.stale_duration_seconds = int(delta.total_seconds())


@dataclass
class FleetHealthSummary:
    """Aggregated fleet health summary.

    Attributes:
        timestamp: When summary was generated
        total_agents: Total number of agents
        by_status: Count of agents by status
        agents_needing_attention: IDs of agents needing attention
        recommended_actions: Aggregated recommended actions
        average_context: Average context percentage across fleet
        critical_count: Number of agents in critical states
    """

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_agents: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    agents_needing_attention: list[str] = field(default_factory=list)
    recommended_actions: list[dict[str, Any]] = field(default_factory=list)
    average_context: float = 0.0
    critical_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "total_agents": self.total_agents,
            "by_status": self.by_status,
            "agents_needing_attention": self.agents_needing_attention,
            "recommended_actions": self.recommended_actions,
            "average_context": self.average_context,
            "critical_count": self.critical_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FleetHealthSummary:
        """Create from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            total_agents=data["total_agents"],
            by_status=data.get("by_status", {}),
            agents_needing_attention=data.get("agents_needing_attention", []),
            recommended_actions=data.get("recommended_actions", []),
            average_context=data.get("average_context", 0.0),
            critical_count=data.get("critical_count", 0),
        )

    @property
    def healthy_percentage(self) -> float:
        """Return percentage of healthy agents."""
        if self.total_agents == 0:
            return 0.0
        healthy = self.by_status.get(HealthStatus.HEALTHY.value, 0)
        return (healthy / self.total_agents) * 100

    @property
    def fleet_status(self) -> HealthStatus:
        """Return overall fleet health status."""
        if self.critical_count > 0:
            return HealthStatus.WARNING
        if self.total_agents == 0:
            return HealthStatus.UNKNOWN

        healthy = self.by_status.get(HealthStatus.HEALTHY.value, 0)
        idle = self.by_status.get(HealthStatus.IDLE.value, 0)

        if healthy + idle == self.total_agents:
            return HealthStatus.HEALTHY
        return HealthStatus.WARNING


# =============================================================================
# Health Evaluator Configuration
# =============================================================================


@dataclass
class EvaluatorConfig:
    """Configuration for health evaluator.

    Attributes:
        stale_warning_seconds: Seconds until stale warning (default 120)
        stale_critical_seconds: Seconds until stuck status (default 300)
        context_warning_percent: Context % for warning (default 50)
        context_critical_percent: Context % for exhausted (default 90)
        idle_threshold_seconds: Seconds of inactivity for idle (default 60)
        enable_auto_nudge: Auto-generate nudge actions (default True)
        enable_auto_handoff: Auto-generate handoff actions (default True)
        max_restart_count: Max restarts before giving up (default 3)
    """

    stale_warning_seconds: int = 120  # 2 minutes
    stale_critical_seconds: int = 300  # 5 minutes
    context_warning_percent: int = 50
    context_critical_percent: int = 90
    idle_threshold_seconds: int = 60  # 1 minute
    enable_auto_nudge: bool = True
    enable_auto_handoff: bool = True
    max_restart_count: int = 3

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "stale_warning_seconds": self.stale_warning_seconds,
            "stale_critical_seconds": self.stale_critical_seconds,
            "context_warning_percent": self.context_warning_percent,
            "context_critical_percent": self.context_critical_percent,
            "idle_threshold_seconds": self.idle_threshold_seconds,
            "enable_auto_nudge": self.enable_auto_nudge,
            "enable_auto_handoff": self.enable_auto_handoff,
            "max_restart_count": self.max_restart_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluatorConfig:
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Health Evaluator
# =============================================================================


class HealthEvaluator:
    """Evaluates agent health and recommends corrective actions.

    The evaluator applies a priority-based evaluation:
    1. Failed state (process crashed) - highest priority
    2. Exhausted context (>90%) - requires immediate handoff
    3. Stuck (>5 min inactive) - needs nudge then restart
    4. Warning (stale or context >50%)
    5. Idle (waiting for input)
    6. Healthy (default)

    Usage:
        evaluator = HealthEvaluator()
        result = evaluator.evaluate(heartbeat)

        if result.status == HealthStatus.EXHAUSTED:
            # Trigger handoff
            pass
    """

    def __init__(self, config: EvaluatorConfig | None = None):
        """Initialize evaluator with optional config.

        Args:
            config: Evaluator configuration (uses defaults if None)
        """
        self.config = config or EvaluatorConfig()
        self._evaluation_count = 0

    def evaluate(
        self,
        heartbeat: AgentHeartbeat,
        now: datetime | None = None,
    ) -> EvaluationResult:
        """Evaluate agent health and recommend actions.

        Args:
            heartbeat: Agent heartbeat data
            now: Current time (uses UTC now if None)

        Returns:
            EvaluationResult with status and recommended actions
        """
        now = now or datetime.now(UTC)
        self._evaluation_count += 1

        # Update stale duration
        heartbeat.update_stale_duration(now)

        # Evaluate in priority order
        result = self._evaluate_failed(heartbeat)
        if result:
            return self._finalize_result(heartbeat, result)

        result = self._evaluate_exhausted(heartbeat)
        if result:
            return self._finalize_result(heartbeat, result)

        result = self._evaluate_stuck(heartbeat)
        if result:
            return self._finalize_result(heartbeat, result)

        result = self._evaluate_stale_warning(heartbeat)
        if result:
            return self._finalize_result(heartbeat, result)

        result = self._evaluate_context_warning(heartbeat)
        if result:
            return self._finalize_result(heartbeat, result)

        result = self._evaluate_idle(heartbeat)
        if result:
            return self._finalize_result(heartbeat, result)

        # Default: Healthy
        return self._finalize_result(
            heartbeat,
            EvaluationResult(
                status=HealthStatus.HEALTHY,
                reason="Agent operating normally",
            ),
        )

    def _finalize_result(
        self,
        heartbeat: AgentHeartbeat,
        result: EvaluationResult,
    ) -> EvaluationResult:
        """Update heartbeat flags based on evaluation result."""
        heartbeat.status = result.status
        heartbeat.is_stale = heartbeat.stale_duration_seconds > self.config.stale_warning_seconds
        heartbeat.needs_nudge = any(a.type == ActionType.NUDGE for a in result.actions)
        heartbeat.needs_handoff = any(
            a.type in (ActionType.HANDOFF, ActionType.HANDOFF_WARNING) for a in result.actions
        )
        heartbeat.needs_restart = any(a.type == ActionType.RESTART for a in result.actions)
        return result

    def _evaluate_failed(self, heartbeat: AgentHeartbeat) -> EvaluationResult | None:
        """Check for failed/crashed state."""
        # Failed is typically set externally when process is not found
        if heartbeat.status == HealthStatus.FAILED:
            actions = []
            if heartbeat.restart_count < self.config.max_restart_count:
                actions.append(
                    Action(
                        type=ActionType.RESTART,
                        priority=ActionPriority.CRITICAL,
                        reason=f"Agent crashed (restart {heartbeat.restart_count + 1}/{self.config.max_restart_count})",
                    )
                )
            else:
                actions.append(
                    Action(
                        type=ActionType.NOTIFY,
                        priority=ActionPriority.CRITICAL,
                        reason="Agent crashed, max restarts exceeded",
                        metadata={"notify_type": "critical"},
                    )
                )

            return EvaluationResult(
                status=HealthStatus.FAILED,
                actions=actions,
                reason=f"Agent process crashed (restarts: {heartbeat.restart_count})",
            )
        return None

    def _evaluate_exhausted(self, heartbeat: AgentHeartbeat) -> EvaluationResult | None:
        """Check for exhausted context (>90%)."""
        if heartbeat.context_percentage >= self.config.context_critical_percent:
            actions = []
            if self.config.enable_auto_handoff:
                actions.append(
                    Action(
                        type=ActionType.HANDOFF,
                        priority=ActionPriority.CRITICAL,
                        reason=f"Context at {heartbeat.context_percentage}%",
                    )
                )
            actions.append(
                Action(
                    type=ActionType.NOTIFY,
                    priority=ActionPriority.CRITICAL,
                    reason=f"Context exhausted at {heartbeat.context_percentage}%",
                    metadata={"notify_type": "critical"},
                )
            )

            return EvaluationResult(
                status=HealthStatus.EXHAUSTED,
                actions=actions,
                reason=f"Context at {heartbeat.context_percentage}%, immediate handoff required",
            )
        return None

    def _evaluate_stuck(self, heartbeat: AgentHeartbeat) -> EvaluationResult | None:
        """Check for stuck agent (>5 min inactive)."""
        if heartbeat.stale_duration_seconds > self.config.stale_critical_seconds:
            actions = []
            minutes = heartbeat.stale_duration_seconds // 60

            if self.config.enable_auto_nudge:
                actions.append(
                    Action(
                        type=ActionType.NUDGE,
                        priority=ActionPriority.HIGH,
                        reason=f"No activity for {minutes}+ minutes",
                    )
                )

            # Schedule restart if nudge doesn't work
            if heartbeat.restart_count < self.config.max_restart_count:
                actions.append(
                    Action(
                        type=ActionType.RESTART,
                        priority=ActionPriority.MEDIUM,
                        delay_seconds=60,  # Wait 1 min after nudge
                        reason="Restart if nudge fails",
                    )
                )

            return EvaluationResult(
                status=HealthStatus.STUCK,
                actions=actions,
                reason=f"No activity for {minutes}+ minutes, agent may be stuck",
            )
        return None

    def _evaluate_stale_warning(self, heartbeat: AgentHeartbeat) -> EvaluationResult | None:
        """Check for stale heartbeat (2-5 min inactive)."""
        if (
            heartbeat.stale_duration_seconds > self.config.stale_warning_seconds
            and heartbeat.stale_duration_seconds <= self.config.stale_critical_seconds
        ):
            actions = []
            minutes = heartbeat.stale_duration_seconds // 60

            if self.config.enable_auto_nudge:
                actions.append(
                    Action(
                        type=ActionType.NUDGE,
                        priority=ActionPriority.LOW,
                        reason=f"Agent stale for {minutes}+ minutes",
                    )
                )

            return EvaluationResult(
                status=HealthStatus.WARNING,
                actions=actions,
                reason=f"No heartbeat for {minutes}+ minutes",
                confidence=0.8,  # Could be legitimate delay
            )
        return None

    def _evaluate_context_warning(self, heartbeat: AgentHeartbeat) -> EvaluationResult | None:
        """Check for context warning (50-90%)."""
        if (
            heartbeat.context_percentage >= self.config.context_warning_percent
            and heartbeat.context_percentage < self.config.context_critical_percent
        ):
            actions = []

            if self.config.enable_auto_handoff:
                actions.append(
                    Action(
                        type=ActionType.HANDOFF_WARNING,
                        priority=ActionPriority.MEDIUM,
                        reason=f"Context at {heartbeat.context_percentage}%",
                    )
                )

            return EvaluationResult(
                status=HealthStatus.WARNING,
                actions=actions,
                reason=f"Context at {heartbeat.context_percentage}%, plan handoff soon",
            )
        return None

    def _evaluate_idle(self, heartbeat: AgentHeartbeat) -> EvaluationResult | None:
        """Check for idle state (waiting for input)."""
        if not heartbeat.current_task:
            if heartbeat.stale_duration_seconds > self.config.idle_threshold_seconds:
                return EvaluationResult(
                    status=HealthStatus.IDLE,
                    actions=[],
                    reason="Agent waiting for input",
                    confidence=0.9,
                )
        return None

    def evaluate_fleet(
        self,
        heartbeats: list[AgentHeartbeat],
        now: datetime | None = None,
    ) -> FleetHealthSummary:
        """Evaluate health of entire fleet.

        Args:
            heartbeats: List of agent heartbeats
            now: Current time (uses UTC now if None)

        Returns:
            FleetHealthSummary with aggregated health data
        """
        now = now or datetime.now(UTC)

        summary = FleetHealthSummary(
            timestamp=now,
            total_agents=len(heartbeats),
        )

        if not heartbeats:
            return summary

        # Evaluate each agent and aggregate
        status_counts: dict[str, int] = {}
        total_context = 0
        all_actions: list[dict[str, Any]] = []

        for heartbeat in heartbeats:
            result = self.evaluate(heartbeat, now)

            # Count by status
            status_key = result.status.value
            status_counts[status_key] = status_counts.get(status_key, 0) + 1

            # Track context
            total_context += heartbeat.context_percentage

            # Collect agents needing attention
            if result.status.needs_attention:
                summary.agents_needing_attention.append(heartbeat.agent_id)
                summary.critical_count += 1

            # Collect critical actions
            for action in result.actions:
                if action.priority in (ActionPriority.HIGH, ActionPriority.CRITICAL):
                    all_actions.append(
                        {
                            "agent_id": heartbeat.agent_id,
                            **action.to_dict(),
                        }
                    )

        summary.by_status = status_counts
        summary.average_context = total_context / len(heartbeats) if heartbeats else 0.0
        summary.recommended_actions = sorted(
            all_actions,
            key=lambda a: ActionPriority(a["priority"]).weight,
            reverse=True,
        )

        return summary

    @property
    def evaluation_count(self) -> int:
        """Return total number of evaluations performed."""
        return self._evaluation_count


# =============================================================================
# Factory Functions
# =============================================================================


def create_evaluator(
    stale_warning_seconds: int = 120,
    stale_critical_seconds: int = 300,
    context_warning_percent: int = 50,
    context_critical_percent: int = 90,
    **kwargs: Any,
) -> HealthEvaluator:
    """Create a configured health evaluator.

    Args:
        stale_warning_seconds: Seconds until stale warning
        stale_critical_seconds: Seconds until stuck status
        context_warning_percent: Context % for warning
        context_critical_percent: Context % for exhausted
        **kwargs: Additional config options

    Returns:
        Configured HealthEvaluator instance
    """
    config = EvaluatorConfig(
        stale_warning_seconds=stale_warning_seconds,
        stale_critical_seconds=stale_critical_seconds,
        context_warning_percent=context_warning_percent,
        context_critical_percent=context_critical_percent,
        **kwargs,
    )
    return HealthEvaluator(config)


def create_heartbeat(
    agent_id: str,
    agent_type: str = "claude",
    context_percentage: int = 0,
    current_task: str | None = None,
    stale_seconds: int = 0,
    **kwargs: Any,
) -> AgentHeartbeat:
    """Create an agent heartbeat with sensible defaults.

    Args:
        agent_id: Unique agent identifier
        agent_type: Type of agent (claude, pi, amp, etc.)
        context_percentage: Current context usage (0-100)
        current_task: Description of current task
        stale_seconds: How many seconds since last activity
        **kwargs: Additional heartbeat fields

    Returns:
        Configured AgentHeartbeat instance
    """
    now = datetime.now(UTC)
    last_activity = now - timedelta(seconds=stale_seconds)

    return AgentHeartbeat(
        agent_id=agent_id,
        agent_type=agent_type,
        tmux_target=kwargs.get("tmux_target", agent_id),
        last_heartbeat=now,
        last_activity=last_activity,
        session_started=kwargs.get("session_started", now - timedelta(hours=1)),
        context_percentage=context_percentage,
        current_task=current_task,
        **{k: v for k, v in kwargs.items() if k in AgentHeartbeat.__dataclass_fields__},
    )
