"""Recovery Manager - Handles agent recovery and health monitoring."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from forge_harness.logging_config import get_logger

from .state import AgentState, FleetStateManager

logger = get_logger(__name__)


@dataclass
class RecoveryConfig:
    """Configuration for recovery behavior."""

    # Staleness detection
    stale_threshold_minutes: int = 30
    context_overload_threshold: int = 60

    # Auto-restart settings
    auto_restart_stuck: bool = True
    auto_restart_overloaded: bool = False
    max_restarts_per_hour: int = 5

    # Health check interval
    health_check_interval_seconds: int = 60

    # Recovery actions
    nudge_before_restart: bool = True
    nudge_timeout_seconds: int = 30


@dataclass
class HealthIncident:
    """Record of a health incident."""

    agent_id: str
    incident_type: str  # stale, overloaded, crashed, etc.
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolution: str | None = None
    auto_resolved: bool = False


class RecoveryManager:
    """Manages agent health monitoring and recovery actions."""

    def __init__(
        self,
        state_manager: FleetStateManager,
        config: RecoveryConfig | None = None,
    ) -> None:
        self.state_manager = state_manager
        self.config = config or RecoveryConfig()
        self._incidents: list[HealthIncident] = []
        self._restart_counts: dict[str, list[datetime]] = {}  # agent_id -> list of restart times
        self._running = False
        self._health_check_task: asyncio.Task | None = None
        self._nudge_handlers: list[Callable[[str], asyncio.Future[bool]]] = []
        self._restart_handlers: list[Callable[[str], asyncio.Future[bool]]] = []

    def register_nudge_handler(
        self,
        handler: Callable[[str], asyncio.Future[bool]],
    ) -> None:
        """Register a handler for nudging agents."""
        self._nudge_handlers.append(handler)

    def register_restart_handler(
        self,
        handler: Callable[[str], asyncio.Future[bool]],
    ) -> None:
        """Register a handler for restarting agents."""
        self._restart_handlers.append(handler)

    async def start_monitoring(self) -> None:
        """Start the health monitoring loop."""
        if self._running:
            return

        self._running = True
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info("Started recovery manager monitoring")

    async def stop_monitoring(self) -> None:
        """Stop the health monitoring loop."""
        self._running = False
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        logger.info("Stopped recovery manager monitoring")

    async def _health_check_loop(self) -> None:
        """Main health check loop."""
        while self._running:
            try:
                await self._check_all_agents()
            except Exception as exc:
                logger.error("Health check loop error: %s", exc)

            await asyncio.sleep(self.config.health_check_interval_seconds)

    async def _check_all_agents(self) -> None:
        """Check health of all agents and take action if needed."""
        agents = self.state_manager.get_all_agents()

        for agent in agents:
            # Check for stale agents
            if self._is_stale(agent.last_heartbeat):
                await self._handle_stale_agent(agent.agent_id)
                continue

            # Check for overloaded agents
            if agent.context_percentage > self.config.context_overload_threshold:
                await self._handle_overloaded_agent(agent.agent_id)
                continue

            # Check for stuck agents (in working state too long)
            if agent.state == AgentState.WORKING:
                time_in_state = datetime.now(UTC) - agent.last_state_change
                if time_in_state > timedelta(minutes=self.config.stale_threshold_minutes):
                    await self._handle_stuck_agent(agent.agent_id)

    def _is_stale(self, last_heartbeat: datetime | None) -> bool:
        """Check if an agent is stale based on last heartbeat."""
        if not last_heartbeat:
            return True

        threshold = timedelta(minutes=self.config.stale_threshold_minutes)
        return datetime.now(UTC) - last_heartbeat > threshold

    async def _handle_stale_agent(self, agent_id: str) -> None:
        """Handle a stale agent."""
        logger.warning("Detected stale agent: %s", agent_id)

        # Record incident
        incident = HealthIncident(
            agent_id=agent_id,
            incident_type="stale",
        )
        self._incidents.append(incident)

        # Update agent state
        self.state_manager.transition_agent(
            agent_id,
            AgentState.RECOVERING,
            "Detected stale heartbeat",
        )

        if self.config.auto_restart_stuck and await self._can_restart(agent_id):
            success = await self._restart_agent(agent_id)
            incident.resolved_at = datetime.now(UTC)
            incident.resolution = "restart" if success else "failed"
            incident.auto_resolved = success

    async def _handle_overloaded_agent(self, agent_id: str) -> None:
        """Handle an overloaded agent."""
        logger.warning("Detected overloaded agent: %s", agent_id)

        incident = HealthIncident(
            agent_id=agent_id,
            incident_type="overloaded",
        )
        self._incidents.append(incident)

        self.state_manager.transition_agent(
            agent_id,
            AgentState.RECOVERING,
            "Context overloaded",
        )

        if self.config.auto_restart_overloaded and await self._can_restart(agent_id):
            success = await self._restart_agent(agent_id)
            incident.resolved_at = datetime.now(UTC)
            incident.resolution = "restart" if success else "failed"
            incident.auto_resolved = success

    async def _handle_stuck_agent(self, agent_id: str) -> None:
        """Handle a stuck agent (working too long)."""
        logger.warning("Detected stuck agent: %s", agent_id)

        incident = HealthIncident(
            agent_id=agent_id,
            incident_type="stuck",
        )
        self._incidents.append(incident)

        self.state_manager.transition_agent(
            agent_id,
            AgentState.RECOVERING,
            "Stuck in working state",
        )

        # Try nudge first if enabled
        if self.config.nudge_before_restart:
            nudge_success = await self._nudge_agent(agent_id)
            if nudge_success:
                logger.info("Nudge succeeded for agent %s", agent_id)
                self.state_manager.transition_agent(agent_id, AgentState.WORKING, "Nudge succeeded")
                incident.resolved_at = datetime.now(UTC)
                incident.resolution = "nudge"
                incident.auto_resolved = True
                return

        if self.config.auto_restart_stuck and await self._can_restart(agent_id):
            success = await self._restart_agent(agent_id)
            incident.resolved_at = datetime.now(UTC)
            incident.resolution = "restart" if success else "failed"
            incident.auto_resolved = success

    async def _can_restart(self, agent_id: str) -> bool:
        """Check if agent can be restarted (rate limiting)."""
        now = datetime.now(UTC)
        hour_ago = now - timedelta(hours=1)

        # Get restart times in the last hour
        restarts = self._restart_counts.get(agent_id, [])
        recent_restarts = [t for t in restarts if t > hour_ago]

        if len(recent_restarts) >= self.config.max_restarts_per_hour:
            logger.warning(
                "Agent %s has exceeded max restarts per hour (%d)",
                agent_id,
                self.config.max_restarts_per_hour,
            )
            return False

        return True

    async def _nudge_agent(self, agent_id: str) -> bool:
        """Send a nudge to an agent."""
        logger.info("Nudging agent: %s", agent_id)

        for handler in self._nudge_handlers:
            try:
                result = await asyncio.wait_for(
                    handler(agent_id),
                    timeout=self.config.nudge_timeout_seconds,
                )
                if result:
                    return True
            except TimeoutError:
                logger.warning("Nudge timed out for agent %s", agent_id)
            except Exception as exc:
                logger.error("Nudge failed for agent %s: %s", agent_id, exc)

        return False

    async def _restart_agent(self, agent_id: str) -> bool:
        """Restart an agent."""
        logger.info("Restarting agent: %s", agent_id)

        # Record restart time
        if agent_id not in self._restart_counts:
            self._restart_counts[agent_id] = []
        self._restart_counts[agent_id].append(datetime.now(UTC))

        # Update state
        self.state_manager.transition_agent(agent_id, AgentState.RESTARTING, "Auto-restart")

        for handler in self._restart_handlers:
            try:
                result = await handler(agent_id)
                if result:
                    self.state_manager.transition_agent(agent_id, AgentState.IDLE, "Restart successful")
                    return True
            except Exception as exc:
                logger.error("Restart failed for agent %s: %s", agent_id, exc)

        self.state_manager.transition_agent(agent_id, AgentState.FAILED, "Restart failed")
        return False

    def get_incidents(
        self,
        agent_id: str | None = None,
        incident_type: str | None = None,
        limit: int = 100,
    ) -> list[HealthIncident]:
        """Get health incidents, optionally filtered."""
        incidents = self._incidents

        if agent_id:
            incidents = [i for i in incidents if i.agent_id == agent_id]
        if incident_type:
            incidents = [i for i in incidents if i.incident_type == incident_type]

        # Sort by detected_at descending
        incidents = sorted(incidents, key=lambda i: i.detected_at, reverse=True)

        return incidents[:limit]

    def get_recovery_summary(self) -> dict[str, Any]:
        """Get summary of recovery status."""
        # Clean up old restart counts
        now = datetime.now(UTC)
        hour_ago = now - timedelta(hours=1)

        for agent_id in list(self._restart_counts.keys()):
            self._restart_counts[agent_id] = [
                t for t in self._restart_counts[agent_id] if t > hour_ago
            ]

        unresolved = [i for i in self._incidents if i.resolved_at is None]

        return {
            "total_incidents": len(self._incidents),
            "unresolved_incidents": len(unresolved),
            "recent_restarts": {
                agent_id: len(times) for agent_id, times in self._restart_counts.items()
            },
            "monitoring_active": self._running,
            "config": {
                "stale_threshold_minutes": self.config.stale_threshold_minutes,
                "auto_restart_stuck": self.config.auto_restart_stuck,
                "max_restarts_per_hour": self.config.max_restarts_per_hour,
            },
        }

    async def manual_recover(self, agent_id: str) -> bool:
        """Manually trigger recovery for an agent."""
        logger.info("Manual recovery triggered for agent: %s", agent_id)

        agent = self.state_manager.get_agent(agent_id)
        if not agent:
            logger.error("Agent %s not found", agent_id)
            return False

        # Force transition to recovering
        self.state_manager.transition_agent(agent_id, AgentState.RECOVERING, "Manual recovery")

        # Try restart
        return await self._restart_agent(agent_id)
