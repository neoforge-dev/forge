"""
Heartbeat Coordinator for FORGE Agent Fleet
============================================

Unified heartbeat and health monitoring system for FORGE agents.
Consolidates monitoring from shell scripts, Python classes, and web dashboard.

Usage:
    from forge_harness.heartbeat_coordinator import (
        HeartbeatCoordinator,
        get_heartbeat_coordinator,
    )

    coordinator = get_heartbeat_coordinator()
    await coordinator.start()

    # Get all heartbeats
    heartbeats = coordinator.get_all_heartbeats()

    # Get fleet summary
    summary = coordinator.get_fleet_summary()

    # Execute action on agent
    result = await coordinator.execute_action("forge:tech", "nudge")
"""

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Health Status Enum
# =============================================================================


class HealthStatus(str, Enum):
    """Agent health status levels."""

    HEALTHY = "healthy"  # Working normally
    IDLE = "idle"  # Waiting for input, <50% context
    WARNING = "warning"  # Context >50% or stale >2 min
    STUCK = "stuck"  # No activity for >5 min
    EXHAUSTED = "exhausted"  # Context >90%
    FAILED = "failed"  # Crashed or unreachable
    UNKNOWN = "unknown"  # Cannot determine status


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class AgentHeartbeat:
    """Heartbeat data for a single agent."""

    agent_id: str
    agent_type: str  # claude, pi, amp, opencode, cursor
    tmux_target: str  # session:window

    # Timestamps
    last_heartbeat: datetime
    last_activity: datetime
    session_started: datetime

    # Context tracking
    context_percentage: int = 0  # 0-100
    context_tokens_used: int = 0
    context_tokens_budget: int = 200000  # Default Claude budget

    # Status
    status: HealthStatus = HealthStatus.UNKNOWN
    current_task: str | None = None
    last_output_preview: str | None = None  # Last 100 chars

    # Flags
    is_stale: bool = False
    needs_nudge: bool = False
    needs_handoff: bool = False
    needs_restart: bool = False

    # Metrics
    stale_duration_seconds: int = 0
    restart_count: int = 0
    error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
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
    def from_dict(cls, data: dict[str, Any]) -> "AgentHeartbeat":
        """Create from dictionary."""
        return cls(
            agent_id=data["agent_id"],
            agent_type=data["agent_type"],
            tmux_target=data["tmux_target"],
            last_heartbeat=datetime.fromisoformat(data["last_heartbeat"]),
            last_activity=datetime.fromisoformat(data["last_activity"]),
            session_started=datetime.fromisoformat(data["session_started"]),
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


@dataclass
class FleetHealthSummary:
    """Aggregated fleet health."""

    timestamp: datetime
    total_agents: int
    by_status: dict[str, int] = field(default_factory=dict)
    agents_needing_attention: list[str] = field(default_factory=list)
    recommended_actions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "total_agents": self.total_agents,
            "by_status": self.by_status,
            "agents_needing_attention": self.agents_needing_attention,
            "recommended_actions": self.recommended_actions,
        }


@dataclass
class ProbeResult:
    """Result from probing an agent."""

    output: str
    agent_type: str
    is_active: bool
    is_idle: bool
    context_percentage: int | None = None
    current_task: str | None = None
    error: str | None = None


@dataclass
class Action:
    """Corrective action to take on an agent."""

    type: str  # nudge, restart, handoff, handoff_warning, kill
    priority: str  # critical, high, medium, low
    delay: int = 0  # Seconds to wait before executing
    reason: str | None = None


@dataclass
class EvaluationResult:
    """Result of health evaluation."""

    status: HealthStatus
    actions: list[Action] = field(default_factory=list)
    reason: str | None = None


@dataclass
class ActionResult:
    """Result of executing an action."""

    success: bool
    action: str
    agent_id: str
    executed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "success": self.success,
            "action": self.action,
            "agent_id": self.agent_id,
            "executed_at": self.executed_at.isoformat(),
            "error": self.error,
        }


# =============================================================================
# Agent Probe
# =============================================================================


class AgentProbe:
    """Probe agent activity via terminal output."""

    ACTIVITY_PATTERNS = {
        "claude": [
            r"(thinking|nebuliz|roosting|cogitat|Pouncing|Sautéed|Infusing)",
            r"(working|Bash|Read|Write|Edit|Explore|Task|Glob|Grep)",
            r"(TaskCreate|TaskUpdate|TaskList|WebSearch|WebFetch)",
        ],
        "pi": [r"(Thinking|Working|→|⏳)"],
        "amp": [r"(Waiting for response|✓ Read|✓ Wrote|✓ Ran)"],
        "opencode": [r"(Processing|Analyzing|Generating)"],
        "cursor": [r"(Generating|Applying|Working)"],
    }

    IDLE_PATTERNS = {
        "claude": [r"^❯|INSERT|NORMAL|^\$"],
        "pi": [r"^~/|^[a-z]+-[0-9]"],
        "amp": [r"^amp>|waiting"],
        "opencode": [r"^opencode>"],
        "cursor": [r"idle"],
    }

    AGENT_TYPE_INDICATORS = {
        "claude": ["Claude", "claude", "Anthropic", "opus", "sonnet", "haiku"],
        "pi": ["pi ", "π", "pi>"],
        "amp": ["amp", "Amp", "AMP"],
        "opencode": ["opencode", "OpenCode"],
        "cursor": ["cursor", "Cursor"],
    }

    async def probe(self, tmux_target: str) -> ProbeResult:
        """Capture and analyze agent terminal output."""
        try:
            output = await self._capture_pane(tmux_target)
            if not output:
                return ProbeResult(
                    output="",
                    agent_type="unknown",
                    is_active=False,
                    is_idle=True,
                    error="Could not capture pane output",
                )

            agent_type = self._detect_agent_type(output)
            is_active = self._detect_activity(output, agent_type)
            is_idle = self._detect_idle(output, agent_type)
            context_percentage = self._extract_context_percentage(output, agent_type)
            current_task = self._extract_current_task(output)

            return ProbeResult(
                output=output[-500:] if len(output) > 500 else output,
                agent_type=agent_type,
                is_active=is_active,
                is_idle=is_idle and not is_active,
                context_percentage=context_percentage,
                current_task=current_task,
            )
        except Exception as e:
            logger.error(f"Error probing {tmux_target}: {e}")
            return ProbeResult(
                output="",
                agent_type="unknown",
                is_active=False,
                is_idle=True,
                error=str(e),
            )

    async def _capture_pane(self, tmux_target: str) -> str:
        """Capture tmux pane content."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "capture-pane",
                "-p",
                "-t",
                tmux_target,
                "-S",
                "-50",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"tmux capture failed for {tmux_target}: {stderr.decode()}")
                return ""
            return stdout.decode("utf-8", errors="replace")
        except Exception as e:
            logger.error(f"Error capturing pane {tmux_target}: {e}")
            return ""

    def _detect_agent_type(self, output: str) -> str:
        """Detect agent type from terminal output."""
        for agent_type, indicators in self.AGENT_TYPE_INDICATORS.items():
            for indicator in indicators:
                if indicator in output:
                    return agent_type
        return "claude"  # Default to claude

    def _detect_activity(self, output: str, agent_type: str) -> bool:
        """Detect if agent is actively working."""
        patterns = self.ACTIVITY_PATTERNS.get(agent_type, self.ACTIVITY_PATTERNS["claude"])
        for pattern in patterns:
            if re.search(pattern, output, re.IGNORECASE):
                return True
        return False

    def _detect_idle(self, output: str, agent_type: str) -> bool:
        """Detect if agent is idle/waiting."""
        patterns = self.IDLE_PATTERNS.get(agent_type, self.IDLE_PATTERNS["claude"])
        # Check last few lines for idle indicators
        last_lines = output.strip().split("\n")[-5:]
        for line in last_lines:
            for pattern in patterns:
                if re.search(pattern, line):
                    return True
        return False

    def _extract_context_percentage(self, output: str, agent_type: str) -> int | None:
        """Extract context percentage from output."""
        patterns = {
            "claude": [
                r"(\d+)%\s*context",
                r"Context[^0-9]*(\d+)%",
                r"context.*?(\d+)%",
            ],
            "pi": [r"(\d+)\.\d+%/\d+k"],
            "amp": [r"(\d+)% of \d+k"],
        }

        for pattern in patterns.get(agent_type, patterns["claude"]):
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue
        return None

    def _extract_current_task(self, output: str) -> str | None:
        """Extract current task description from output."""
        # Look for task indicators in output
        task_patterns = [
            r"(?:working on|implementing|fixing|building|creating|updating)\s+(.+?)(?:\n|$)",
            r"Task:\s*(.+?)(?:\n|$)",
            r"(?:Reading|Writing|Editing)\s+(.+?)(?:\n|$)",
        ]

        for pattern in task_patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                task = match.group(1).strip()
                if len(task) > 100:
                    task = task[:97] + "..."
                return task
        return None


# =============================================================================
# Health Evaluator
# =============================================================================


class HealthEvaluator:
    """Evaluate agent health and recommend actions."""

    # Thresholds (configurable)
    STALE_WARNING_SECONDS = 120  # 2 minutes
    STALE_CRITICAL_SECONDS = 300  # 5 minutes
    CONTEXT_WARNING = 50
    CONTEXT_CRITICAL = 90

    def __init__(
        self,
        stale_warning_seconds: int = 120,
        stale_critical_seconds: int = 300,
        context_warning: int = 50,
        context_critical: int = 90,
    ):
        self.STALE_WARNING_SECONDS = stale_warning_seconds
        self.STALE_CRITICAL_SECONDS = stale_critical_seconds
        self.CONTEXT_WARNING = context_warning
        self.CONTEXT_CRITICAL = context_critical

    def evaluate(self, heartbeat: AgentHeartbeat) -> EvaluationResult:
        """Evaluate health status and actions."""
        now = datetime.now(UTC)

        # Calculate stale duration
        stale_duration = (now - heartbeat.last_activity).total_seconds()
        heartbeat.stale_duration_seconds = int(stale_duration)

        # Check for exhausted context (highest priority)
        if heartbeat.context_percentage >= self.CONTEXT_CRITICAL:
            heartbeat.needs_handoff = True
            return EvaluationResult(
                status=HealthStatus.EXHAUSTED,
                actions=[Action(type="handoff", priority="critical", reason="Context exhausted")],
                reason=f"Context at {heartbeat.context_percentage}%",
            )

        # Check for stuck agent (no activity for >5 min)
        if stale_duration > self.STALE_CRITICAL_SECONDS * 2:
            heartbeat.needs_restart = True
            return EvaluationResult(
                status=HealthStatus.STUCK,
                actions=[
                    Action(type="nudge", priority="high"),
                    Action(type="restart", priority="medium", delay=60),
                ],
                reason=f"No activity for {int(stale_duration // 60)}+ minutes",
            )

        # Check for stale (may need nudge)
        if stale_duration > self.STALE_CRITICAL_SECONDS:
            heartbeat.is_stale = True
            heartbeat.needs_nudge = True
            return EvaluationResult(
                status=HealthStatus.STUCK,
                actions=[Action(type="nudge", priority="high")],
                reason=f"No activity for {int(stale_duration // 60)} minutes",
            )

        # Check for idle BEFORE stale warning - an agent waiting for input
        # shouldn't be flagged as stale if it has no current task
        if not heartbeat.current_task and heartbeat.context_percentage < self.CONTEXT_WARNING:
            idle_duration = stale_duration
            if idle_duration > 120:  # 2 minutes without task = idle
                return EvaluationResult(
                    status=HealthStatus.IDLE,
                    actions=[],
                    reason="Agent waiting for input",
                )

        if stale_duration > self.STALE_WARNING_SECONDS:
            heartbeat.is_stale = True
            return EvaluationResult(
                status=HealthStatus.WARNING,
                actions=[Action(type="nudge", priority="low")],
                reason="Agent has not sent heartbeat recently",
            )

        # Check for context warning
        if heartbeat.context_percentage >= self.CONTEXT_WARNING:
            heartbeat.needs_handoff = True
            return EvaluationResult(
                status=HealthStatus.WARNING,
                actions=[Action(type="handoff_warning", priority="medium")],
                reason=f"Context at {heartbeat.context_percentage}%",
            )

        # Healthy
        heartbeat.is_stale = False
        heartbeat.needs_nudge = False
        return EvaluationResult(status=HealthStatus.HEALTHY, actions=[])


# =============================================================================
# Action Engine
# =============================================================================


class ActionEngine:
    """Execute corrective actions on agents."""

    async def execute(self, action: Action, heartbeat: AgentHeartbeat) -> ActionResult:
        """Execute a corrective action."""
        try:
            match action.type:
                case "nudge":
                    return await self._nudge_agent(heartbeat)
                case "restart":
                    return await self._restart_agent(heartbeat)
                case "handoff":
                    return await self._trigger_handoff(heartbeat)
                case "handoff_warning":
                    return await self._send_handoff_warning(heartbeat)
                case "kill":
                    return await self._kill_agent(heartbeat)
                case _:
                    return ActionResult(
                        success=False,
                        action=action.type,
                        agent_id=heartbeat.agent_id,
                        error=f"Unknown action type: {action.type}",
                    )
        except Exception as e:
            logger.error(f"Error executing action {action.type} on {heartbeat.agent_id}: {e}")
            return ActionResult(
                success=False,
                action=action.type,
                agent_id=heartbeat.agent_id,
                error=str(e),
            )

    async def _nudge_agent(self, heartbeat: AgentHeartbeat) -> ActionResult:
        """Send Enter key to wake up agent."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "send-keys",
                "-t",
                heartbeat.tmux_target,
                "Enter",
            )
            await proc.wait()

            logger.info(f"Nudged agent {heartbeat.agent_id}")
            return ActionResult(
                success=proc.returncode == 0,
                action="nudge",
                agent_id=heartbeat.agent_id,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action="nudge",
                agent_id=heartbeat.agent_id,
                error=str(e),
            )

    async def _trigger_handoff(self, heartbeat: AgentHeartbeat) -> ActionResult:
        """Trigger /handoff command on agent."""
        try:
            # Send /handoff command via tmux
            proc1 = await asyncio.create_subprocess_exec(
                "tmux",
                "send-keys",
                "-t",
                heartbeat.tmux_target,
                "-l",
                "/handoff",
            )
            await proc1.wait()

            proc2 = await asyncio.create_subprocess_exec(
                "tmux",
                "send-keys",
                "-t",
                heartbeat.tmux_target,
                "Enter",
            )
            await proc2.wait()

            logger.info(f"Triggered handoff for agent {heartbeat.agent_id}")
            return ActionResult(
                success=True,
                action="handoff",
                agent_id=heartbeat.agent_id,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action="handoff",
                agent_id=heartbeat.agent_id,
                error=str(e),
            )

    async def _send_handoff_warning(self, heartbeat: AgentHeartbeat) -> ActionResult:
        """Send handoff warning notification."""
        logger.warning(
            f"Agent {heartbeat.agent_id} context at {heartbeat.context_percentage}% - "
            "consider triggering handoff"
        )
        return ActionResult(
            success=True,
            action="handoff_warning",
            agent_id=heartbeat.agent_id,
        )

    async def _restart_agent(self, heartbeat: AgentHeartbeat) -> ActionResult:
        """Restart agent via supervisor or tmux."""
        try:
            # First try to gracefully exit
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "send-keys",
                "-t",
                heartbeat.tmux_target,
                "C-c",
            )
            await proc.wait()

            await asyncio.sleep(2)

            # Start new claude session
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "send-keys",
                "-t",
                heartbeat.tmux_target,
                "claude",
            )
            await proc.wait()

            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "send-keys",
                "-t",
                heartbeat.tmux_target,
                "Enter",
            )
            await proc.wait()

            heartbeat.restart_count += 1
            logger.info(f"Restarted agent {heartbeat.agent_id} (count: {heartbeat.restart_count})")

            return ActionResult(
                success=True,
                action="restart",
                agent_id=heartbeat.agent_id,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action="restart",
                agent_id=heartbeat.agent_id,
                error=str(e),
            )

    async def _kill_agent(self, heartbeat: AgentHeartbeat) -> ActionResult:
        """Kill agent process."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "send-keys",
                "-t",
                heartbeat.tmux_target,
                "C-c",
            )
            await proc.wait()

            logger.info(f"Killed agent {heartbeat.agent_id}")
            return ActionResult(
                success=True,
                action="kill",
                agent_id=heartbeat.agent_id,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                action="kill",
                agent_id=heartbeat.agent_id,
                error=str(e),
            )


# =============================================================================
# State Persistence
# =============================================================================


class HeartbeatStateStore:
    """File-based state persistence for heartbeat data."""

    def __init__(self, state_dir: Path | None = None):
        if state_dir is None:
            state_dir = Path.cwd() / ".forge/fleet"
        self.state_dir = state_dir
        self.state_file = state_dir / "heartbeat_state.json"
        self._ensure_directory()

    def _ensure_directory(self):
        """Ensure state directory exists."""
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def save(self, heartbeats: dict[str, AgentHeartbeat], summary: FleetHealthSummary):
        """Save heartbeat state to file."""
        try:
            state = {
                "version": "1.0",
                "last_updated": datetime.now(UTC).isoformat(),
                "agents": {agent_id: hb.to_dict() for agent_id, hb in heartbeats.items()},
                "fleet_summary": summary.to_dict(),
            }

            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)

            logger.debug(f"Saved heartbeat state for {len(heartbeats)} agents")
        except Exception as e:
            logger.error(f"Failed to save heartbeat state: {e}")

    def load(self) -> tuple[dict[str, AgentHeartbeat], FleetHealthSummary | None]:
        """Load heartbeat state from file."""
        if not self.state_file.exists():
            return {}, None

        try:
            with open(self.state_file) as f:
                state = json.load(f)

            heartbeats = {
                agent_id: AgentHeartbeat.from_dict(data)
                for agent_id, data in state.get("agents", {}).items()
            }

            summary_data = state.get("fleet_summary")
            summary = None
            if summary_data:
                summary = FleetHealthSummary(
                    timestamp=datetime.fromisoformat(summary_data["timestamp"]),
                    total_agents=summary_data["total_agents"],
                    by_status=summary_data.get("by_status", {}),
                    agents_needing_attention=summary_data.get("agents_needing_attention", []),
                    recommended_actions=summary_data.get("recommended_actions", []),
                )

            logger.debug(f"Loaded heartbeat state for {len(heartbeats)} agents")
            return heartbeats, summary
        except Exception as e:
            logger.error(f"Failed to load heartbeat state: {e}")
            return {}, None


# =============================================================================
# Recovery State Manager
# =============================================================================


class RecoveryStateManager:
    """Manages shared recovery state between HeartbeatCoordinator and heartbeat-loop.sh.

    State file: .forge/heartbeat/coordinator_state.json
    Default state (file missing): no recovery in progress (safe fallback).
    Writes atomically via tmp + os.replace.
    """

    DEFAULT_STATE: dict[str, Any] = {
        "recovery_in_progress": False,
        "agent_under_recovery": None,
        "recovery_started_at": None,
    }

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file

    def read(self) -> dict[str, Any]:
        """Return current recovery state. Returns DEFAULT_STATE if file missing or invalid."""
        if not self.state_file.exists():
            return dict(self.DEFAULT_STATE)
        try:
            with open(self.state_file) as f:
                data = json.load(f)
            # Merge with defaults so callers always get all keys
            result = dict(self.DEFAULT_STATE)
            result.update(data)
            return result
        except Exception as e:
            logger.warning(f"RecoveryStateManager: failed to read {self.state_file}: {e}")
            return dict(self.DEFAULT_STATE)

    def set_recovery_started(self, agent_id: str) -> None:
        """Record that a recovery action has started for *agent_id*."""
        state = {
            "recovery_in_progress": True,
            "agent_under_recovery": agent_id,
            "recovery_started_at": datetime.now(UTC).isoformat(),
        }
        self._write_atomic(state)
        logger.info(f"RecoveryStateManager: recovery started for {agent_id}")

    def set_recovery_complete(self) -> None:
        """Clear the recovery-in-progress flag."""
        self._write_atomic(dict(self.DEFAULT_STATE))
        logger.info("RecoveryStateManager: recovery complete")

    def is_recovery_in_progress(self) -> bool:
        """Convenience accessor — True when a recovery action is active."""
        return bool(self.read().get("recovery_in_progress", False))

    def _write_atomic(self, state: dict[str, Any]) -> None:
        """Write *state* atomically using a temp file + os.replace."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=self.state_file.parent,
                prefix=".coordinator_state_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp_path, self.state_file)
            except Exception:
                # Clean up the temp file if the replace failed
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"RecoveryStateManager: failed to write {self.state_file}: {e}")
            raise


# =============================================================================
# Heartbeat Coordinator
# =============================================================================


class HeartbeatCoordinator:
    """Main coordinator for agent heartbeat monitoring."""

    # Actions that touch agent process lifecycle — require recovery state tracking.
    _RECOVERY_ACTIONS: frozenset[str] = frozenset({"restart", "handoff", "kill"})

    # Action cooldown periods in seconds — prevent action spam across monitoring cycles.
    ACTION_COOLDOWNS: dict[str, int] = {
        "restart": 300,           # 5 minutes between restarts
        "nudge": 120,             # 2 minutes between nudges
        "handoff": 300,           # 5 minutes between handoff triggers
        "handoff_warning": 300,   # 5 minutes between handoff warnings
        "kill": 600,              # 10 minutes between kill attempts
    }
    DEFAULT_COOLDOWN = 60  # 1 minute fallback for unknown action types

    def __init__(
        self,
        interval_seconds: int = 30,
        state_dir: Path | None = None,
        auto_actions: bool = True,
        recovery_state_file: Path | None = None,
    ):
        """Initialize coordinator.

        Args:
            interval_seconds: Heartbeat check interval
            state_dir: Directory for state persistence
            auto_actions: Whether to automatically execute corrective actions
            recovery_state_file: Path for coordinator_state.json; defaults to
                ``.forge/heartbeat/coordinator_state.json`` relative to the
                repository root discovered via :meth:`_default_recovery_state_file`.
        """
        self.interval_seconds = interval_seconds
        self.auto_actions = auto_actions

        self.probe = AgentProbe()
        self.evaluator = HealthEvaluator()
        self.action_engine = ActionEngine()
        self.state_store = HeartbeatStateStore(state_dir)

        # Recovery state shared with heartbeat-loop.sh
        resolved_recovery_file = (
            recovery_state_file
            if recovery_state_file is not None
            else self._default_recovery_state_file()
        )
        self.recovery_state = RecoveryStateManager(resolved_recovery_file)

        self._heartbeats: dict[str, AgentHeartbeat] = {}
        self._running = False
        self._task: asyncio.Task | None = None

        # Track last action execution time: {(agent_id, action_type): datetime}
        self._action_cooldowns: dict[tuple[str, str], datetime] = {}

        # Load previous state
        self._heartbeats, _ = self.state_store.load()

    @staticmethod
    def _default_recovery_state_file() -> Path:
        """Return the default path for coordinator_state.json.

        Walks up from this module's location to find the ``.forge`` directory,
        falling back to a path relative to cwd if not found.
        """
        here = Path(__file__).resolve().parent
        # Walk up looking for .forge directory
        candidate = here
        for _ in range(10):
            forge_dir = candidate / ".forge"
            if forge_dir.is_dir():
                return forge_dir / "heartbeat" / "coordinator_state.json"
            parent = candidate.parent
            if parent == candidate:
                break
            candidate = parent
        # Fallback: relative to cwd
        return Path(".forge") / "heartbeat" / "coordinator_state.json"

    def _is_action_on_cooldown(self, agent_id: str, action_type: str) -> bool:
        """Check if an action is still within its cooldown period.

        Returns True if the action was executed recently enough that it should
        be skipped this cycle, False if it is safe to execute.
        """
        key = (agent_id, action_type)
        last_executed = self._action_cooldowns.get(key)
        if last_executed is None:
            return False
        elapsed = (datetime.now(UTC) - last_executed).total_seconds()
        cooldown = self.ACTION_COOLDOWNS.get(action_type, self.DEFAULT_COOLDOWN)
        return elapsed < cooldown

    def _record_action_executed(self, agent_id: str, action_type: str) -> None:
        """Record that an action was just executed for cooldown tracking."""
        self._action_cooldowns[(agent_id, action_type)] = datetime.now(UTC)

    async def _execute_action_with_recovery_state(
        self, action: "Action", heartbeat: "AgentHeartbeat"
    ) -> "ActionResult":
        """Execute an action, wrapping lifecycle-altering actions with recovery state.

        For non-recovery actions (nudge, etc.) the call is passed straight
        through to :attr:`action_engine`.  For recovery actions (restart,
        handoff, kill) ``recovery_state`` is set before execution and cleared
        in a ``finally`` block so that ``heartbeat-loop.sh`` never sees a
        stale in-progress flag.
        """
        if action.type not in self._RECOVERY_ACTIONS:
            return await self.action_engine.execute(action, heartbeat)

        self.recovery_state.set_recovery_started(heartbeat.agent_id)
        try:
            return await self.action_engine.execute(action, heartbeat)
        finally:
            self.recovery_state.set_recovery_complete()

    async def start(self):
        """Start the monitoring loop."""
        if self._running:
            logger.warning("Heartbeat coordinator already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitoring_loop())
        logger.info(f"Heartbeat coordinator started (interval: {self.interval_seconds}s)")

    async def stop(self):
        """Stop the monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Heartbeat coordinator stopped")

    async def run(self, interval_seconds: int | None = None):
        """Run the monitoring loop blocking until cancelled or killed.

        Unlike ``start()``, which schedules a background task and returns
        immediately, this method runs the monitoring loop directly in the
        calling coroutine.  It is intended for standalone entry-point usage::

            asyncio.run(get_heartbeat_coordinator().run(interval_seconds=30))

        Each cycle calls ``_check_all_agents()``.  Exceptions raised by a
        single cycle are logged and swallowed so that one bad probe does not
        crash the entire loop.

        Args:
            interval_seconds: Seconds to sleep between cycles.  Defaults to
                ``self.interval_seconds`` when *None*.
        """
        sleep_for = interval_seconds if interval_seconds is not None else self.interval_seconds
        self._running = True
        logger.info(f"Heartbeat coordinator running (interval: {sleep_for}s) — blocking until killed")
        cycle = 0
        try:
            while self._running:
                cycle += 1
                logger.info(f"Heartbeat cycle {cycle} — checking all agents")
                try:
                    await self._check_all_agents()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Heartbeat cycle {cycle} error (continuing): {e}")
                await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            logger.info("Heartbeat coordinator run() received cancellation — shutting down")
        finally:
            self._running = False
            logger.info("Heartbeat coordinator run() exited")

    async def _monitoring_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_all_agents()
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(5)

    async def _check_all_agents(self):
        """Check all registered tmux sessions."""
        try:
            # Get list of tmux sessions
            sessions = await self._get_tmux_sessions()

            for session in sessions:
                await self._check_agent(session)

            # Update and save summary
            summary = self.get_fleet_summary()
            self.state_store.save(self._heartbeats, summary)

        except Exception as e:
            logger.error(f"Error checking agents: {e}")

    # Windows in the forge session that are actual agents (not infrastructure)
    AGENT_SESSIONS = {"forge"}
    # Infrastructure windows to never probe or nudge
    EXCLUDED_WINDOWS = frozenset({
        "backend", "frontend", "status", "tasks", "heartbeat",
        "hb-loop", "agent-health", "xnode",
    })

    async def _get_tmux_sessions(self) -> list[str]:
        """Get list of forge agent windows (excludes infrastructure windows)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "list-windows",
                "-a",
                "-F",
                "#{session_name}:#{window_name}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0:
                return []

            sessions = []
            for line in stdout.decode().strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":", 1)
                if len(parts) != 2:
                    continue
                session_name, window_name = parts
                # Only monitor windows in agent sessions, skip infrastructure
                if session_name in self.AGENT_SESSIONS and window_name not in self.EXCLUDED_WINDOWS:
                    sessions.append(line)

            return sessions
        except Exception as e:
            logger.error(f"Error getting tmux sessions: {e}")
            return []

    async def _check_agent(self, tmux_target: str):
        """Check a single agent's health."""
        now = datetime.now(UTC)

        # Probe the agent
        probe_result = await self.probe.probe(tmux_target)

        # Get or create heartbeat record
        if tmux_target in self._heartbeats:
            heartbeat = self._heartbeats[tmux_target]
            heartbeat.last_heartbeat = now
        else:
            heartbeat = AgentHeartbeat(
                agent_id=tmux_target,
                agent_type=probe_result.agent_type,
                tmux_target=tmux_target,
                last_heartbeat=now,
                last_activity=now,
                session_started=now,
            )
            self._heartbeats[tmux_target] = heartbeat

        # Update heartbeat with probe data
        heartbeat.agent_type = probe_result.agent_type
        heartbeat.last_output_preview = probe_result.output[-100:] if probe_result.output else None

        if probe_result.context_percentage is not None:
            heartbeat.context_percentage = probe_result.context_percentage

        if probe_result.current_task:
            heartbeat.current_task = probe_result.current_task

        if probe_result.is_active:
            heartbeat.last_activity = now

        # Evaluate health
        evaluation = self.evaluator.evaluate(heartbeat)
        heartbeat.status = evaluation.status

        # Execute auto actions if enabled
        if self.auto_actions and evaluation.actions:
            for action in evaluation.actions:
                if self._is_action_on_cooldown(heartbeat.agent_id, action.type):
                    logger.debug(
                        f"Skipping {action.type} for {heartbeat.agent_id} — on cooldown"
                    )
                    continue
                if action.delay == 0:
                    await self._execute_action_with_recovery_state(action, heartbeat)
                    self._record_action_executed(heartbeat.agent_id, action.type)
                else:
                    # Schedule delayed action
                    asyncio.create_task(self._delayed_action(action, heartbeat))

    async def _delayed_action(self, action: Action, heartbeat: AgentHeartbeat):
        """Execute a delayed action."""
        await asyncio.sleep(action.delay)
        # Re-check if action is still needed
        current_hb = self._heartbeats.get(heartbeat.agent_id)
        if current_hb and current_hb.needs_restart:
            if not self._is_action_on_cooldown(current_hb.agent_id, action.type):
                await self._execute_action_with_recovery_state(action, current_hb)
                self._record_action_executed(current_hb.agent_id, action.type)

    def register_agent(self, tmux_target: str, agent_type: str = "claude"):
        """Manually register an agent for monitoring."""
        now = datetime.now(UTC)
        heartbeat = AgentHeartbeat(
            agent_id=tmux_target,
            agent_type=agent_type,
            tmux_target=tmux_target,
            last_heartbeat=now,
            last_activity=now,
            session_started=now,
        )
        self._heartbeats[tmux_target] = heartbeat
        logger.info(f"Registered agent {tmux_target} ({agent_type})")

    def unregister_agent(self, agent_id: str):
        """Unregister an agent from monitoring."""
        if agent_id in self._heartbeats:
            del self._heartbeats[agent_id]
            logger.info(f"Unregistered agent {agent_id}")

    def get_heartbeat(self, agent_id: str) -> AgentHeartbeat | None:
        """Get heartbeat for a specific agent."""
        return self._heartbeats.get(agent_id)

    def get_all_heartbeats(self) -> list[AgentHeartbeat]:
        """Get all agent heartbeats."""
        return list(self._heartbeats.values())

    def get_fleet_summary(self) -> FleetHealthSummary:
        """Get aggregated fleet health summary."""
        now = datetime.now(UTC)

        by_status: dict[str, int] = {}
        agents_needing_attention: list[str] = []
        recommended_actions: list[dict] = []

        for agent_id, hb in self._heartbeats.items():
            status = hb.status.value
            by_status[status] = by_status.get(status, 0) + 1

            if hb.status in (HealthStatus.STUCK, HealthStatus.EXHAUSTED, HealthStatus.FAILED):
                agents_needing_attention.append(agent_id)

                if hb.needs_handoff:
                    recommended_actions.append(
                        {
                            "agent_id": agent_id,
                            "action": "handoff",
                            "reason": f"Context at {hb.context_percentage}%",
                        }
                    )
                elif hb.needs_restart:
                    recommended_actions.append(
                        {
                            "agent_id": agent_id,
                            "action": "restart",
                            "reason": f"Stuck for {hb.stale_duration_seconds // 60} minutes",
                        }
                    )
                elif hb.needs_nudge:
                    recommended_actions.append(
                        {
                            "agent_id": agent_id,
                            "action": "nudge",
                            "reason": "Agent appears stale",
                        }
                    )

        return FleetHealthSummary(
            timestamp=now,
            total_agents=len(self._heartbeats),
            by_status=by_status,
            agents_needing_attention=agents_needing_attention,
            recommended_actions=recommended_actions,
        )

    async def execute_action(self, agent_id: str, action_type: str) -> ActionResult:
        """Execute a manual action on an agent."""
        heartbeat = self._heartbeats.get(agent_id)
        if not heartbeat:
            return ActionResult(
                success=False,
                action=action_type,
                agent_id=agent_id,
                error=f"Agent {agent_id} not found",
            )

        action = Action(type=action_type, priority="manual")
        return await self._execute_action_with_recovery_state(action, heartbeat)


# =============================================================================
# Global Coordinator Instance
# =============================================================================

_heartbeat_coordinator: HeartbeatCoordinator | None = None


def get_heartbeat_coordinator() -> HeartbeatCoordinator:
    """Get or create global heartbeat coordinator instance."""
    global _heartbeat_coordinator
    if _heartbeat_coordinator is None:
        _heartbeat_coordinator = HeartbeatCoordinator()
    return _heartbeat_coordinator


def reset_heartbeat_coordinator():
    """Reset the global coordinator (for testing)."""
    global _heartbeat_coordinator
    _heartbeat_coordinator = None
