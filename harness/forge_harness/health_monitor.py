"""
FORGE Agent Health Monitor
==========================

Detects agent health status (context level, activity) and determines
whether agents can receive new task dispatches.

Prevents context exhaustion emergencies by integrating with the
orchestrator loop to skip EXHAUSTED agents.

Usage:
    from forge_harness.health_monitor import HealthMonitor, HealthStatus

    monitor = HealthMonitor()
    health = monitor.check("forge:claude")
    if health.can_dispatch:
        dispatch_task(agent, task)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    IDLE = "IDLE"
    WARNING = "WARNING"
    EXHAUSTED = "EXHAUSTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class AgentHealth:
    agent: str
    status: HealthStatus
    context_pct: int  # -1 = unknown
    activity: str
    can_dispatch: bool
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class FleetHealth:
    agents: list[AgentHealth]
    total: int = 0
    healthy: int = 0
    exhausted: int = 0
    warning: int = 0
    failed: int = 0

    def to_dict(self) -> dict:
        return {
            "agents": [a.to_dict() for a in self.agents],
            "summary": {
                "total": self.total,
                "healthy": self.healthy,
                "exhausted": self.exhausted,
                "warning": self.warning,
                "failed": self.failed,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class HealthMonitor:
    """Agent health monitor using tmux pane inspection."""

    def __init__(self, forge_root: Path | None = None):
        self.forge_root = forge_root or Path.cwd()
        self._script = self.forge_root / ".forge" / "scripts" / "check-agent-health.sh"

    def check(self, agent: str) -> AgentHealth:
        """Check health of a single agent.

        Args:
            agent: tmux target (e.g., "forge:claude")

        Returns:
            AgentHealth with status, context, activity, and dispatch eligibility.
        """
        if not self._script.exists():
            return AgentHealth(
                agent=agent,
                status=HealthStatus.UNKNOWN,
                context_pct=-1,
                activity="unknown",
                can_dispatch=True,
                reason="health script not found",
            )

        try:
            result = subprocess.run(
                [str(self._script), agent],
                capture_output=True,
                text=True,
                timeout=10,
            )
            data = json.loads(result.stdout.strip())
            return AgentHealth(
                agent=data.get("agent", agent),
                status=HealthStatus(data.get("status", "UNKNOWN")),
                context_pct=data.get("context_pct", -1),
                activity=data.get("activity", "unknown"),
                can_dispatch=data.get("can_dispatch", True),
                reason=data.get("reason", ""),
            )
        except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError) as e:
            return AgentHealth(
                agent=agent,
                status=HealthStatus.UNKNOWN,
                context_pct=-1,
                activity="unknown",
                can_dispatch=True,
                reason=str(e),
            )

    def check_all(self) -> FleetHealth:
        """Check health of all agents in the forge tmux session.

        Returns:
            FleetHealth with all agent statuses and summary counts.
        """
        if not self._script.exists():
            return FleetHealth(agents=[])

        try:
            result = subprocess.run(
                [str(self._script), "--all"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            data = json.loads(result.stdout.strip())

            agents = []
            for agent_data in data.get("agents", []):
                agents.append(
                    AgentHealth(
                        agent=agent_data.get("agent", "unknown"),
                        status=HealthStatus(agent_data.get("status", "UNKNOWN")),
                        context_pct=agent_data.get("context_pct", -1),
                        activity=agent_data.get("activity", "unknown"),
                        can_dispatch=agent_data.get("can_dispatch", True),
                        reason=agent_data.get("reason", ""),
                    )
                )

            summary = data.get("summary", {})
            return FleetHealth(
                agents=agents,
                total=summary.get("total", len(agents)),
                healthy=summary.get("healthy", 0),
                exhausted=summary.get("exhausted", 0),
                warning=summary.get("warning", 0),
                failed=summary.get("failed", 0),
            )
        except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError):
            return FleetHealth(agents=[])

    def should_dispatch(self, agent: str) -> bool:
        """Quick check: can this agent receive a new task?

        Args:
            agent: tmux target (e.g., "forge:claude")

        Returns:
            True if agent is healthy enough for dispatch.
        """
        health = self.check(agent)
        return health.can_dispatch
