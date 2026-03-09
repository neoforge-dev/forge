"""
TmuxDBSyncService - Synchronize tmux agent state to database.
=============================================================

Polls tmux sessions and syncs agent state to the state store.
Publishes SSE events when agent states change.

Usage:
    from forge_harness.sync.agent_sync import TmuxDBSyncService

    sync_service = TmuxDBSyncService(forge_root=Path("/path/to/forge"))
    await sync_service.start(poll_interval=5.0)

    # Later...
    await sync_service.stop()
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class EventBusProtocol(Protocol):
    """Protocol for event bus implementations."""

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to subscribers."""
        ...


@dataclass
class AgentState:
    """State of an agent session from tmux."""

    session_id: str
    agent_name: str
    agent_role: str
    domain: str | None
    project: str | None
    status: str  # active, idle, offline
    current_task: str | None
    context_percentage: float
    last_activity: datetime
    pane_output: str | None = None


@dataclass
class SyncResult:
    """Result of a sync operation."""

    agents_found: int = 0
    agents_updated: int = 0
    agents_unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class TmuxDBSyncService:
    """
    Synchronize tmux agent sessions to the database/state store.

    Polls tmux sessions periodically, extracts agent state, and syncs
to state_store. Publishes SSE events when state changes are detected.
    """

    def __init__(
        self,
        forge_root: Path,
        state_store: Any | None = None,
        event_bus: EventBusProtocol | None = None,
    ):
        """
        Initialize TmuxDBSyncService.

        Args:
            forge_root: FORGE project root directory
            state_store: Optional state store instance
            event_bus: Optional event bus for publishing events
        """
        self.forge_root = Path(forge_root)
        self._state_store = state_store
        self._event_bus = event_bus

        # Running state
        self._running = False
        self._sync_task: asyncio.Task | None = None
        self._poll_interval: float = 5.0

        # Track previous state to detect changes
        self._previous_states: dict[str, AgentState] = {}

        # Statistics
        self._sync_count = 0
        self._last_sync_result: SyncResult | None = None

    async def start(self, poll_interval: float = 5.0) -> None:
        """
        Start background synchronization loop.

        Args:
            poll_interval: Seconds between sync cycles
        """
        if self._running:
            logger.warning("TmuxDBSyncService already running")
            return

        self._running = True
        self._poll_interval = poll_interval

        logger.info(f"Starting TmuxDBSyncService with poll_interval={poll_interval}s")

        self._sync_task = asyncio.create_task(
            self._sync_loop(),
            name="tmux_db_sync_loop",
        )

    async def stop(self) -> None:
        """Stop synchronization loop."""
        self._running = False

        if self._sync_task is not None:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        logger.info("TmuxDBSyncService stopped")

    async def _sync_loop(self) -> None:
        """Background sync loop."""
        while self._running:
            try:
                result = await self.sync_from_tmux()
                self._last_sync_result = result
                self._sync_count += 1

                if result.errors:
                    logger.warning(f"Sync completed with errors: {result.errors}")

            except Exception as e:
                logger.error(f"Sync error: {e}")

            await asyncio.sleep(self._poll_interval)

    async def sync_from_tmux(self) -> SyncResult:
        """
        Sync agent state from tmux to database.

        Returns:
            SyncResult with sync statistics
        """
        result = SyncResult()

        try:
            # Get current tmux sessions
            agents = await self._get_tmux_agents()
            result.agents_found = len(agents)

            # Detect changes
            current_states: dict[str, AgentState] = {}

            for agent in agents:
                session_id = agent.session_id
                current_states[session_id] = agent

                # Check if state changed
                previous = self._previous_states.get(session_id)
                if previous is None:
                    # New agent
                    await self._on_agent_added(agent)
                    result.agents_updated += 1
                elif self._state_changed(previous, agent):
                    # State changed
                    await self._on_agent_updated(agent, previous)
                    result.agents_updated += 1
                else:
                    result.agents_unchanged += 1

                # Update state store
                await self._update_state_store(agent)

            # Check for removed agents
            for session_id, previous in self._previous_states.items():
                if session_id not in current_states:
                    await self._on_agent_removed(previous)
                    result.agents_updated += 1

            # Update previous states
            self._previous_states = current_states

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to sync from tmux: {error_msg}")
            result.errors.append(error_msg)

        return result

    async def _get_tmux_agents(self) -> list[AgentState]:
        """Get agent states from tmux sessions."""
        return await asyncio.to_thread(self._get_tmux_agents_blocking)

    def _get_tmux_agents_blocking(self) -> list[AgentState]:
        """Get agent states from tmux sessions (blocking — runs in thread)."""
        agents: list[AgentState] = []

        try:
            # List all tmux sessions
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                # No tmux server running
                return agents

            sessions = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]

            for session_name in sessions:
                # Only process forge sessions
                if not session_name.startswith("forge:"):
                    continue

                agent = self._parse_agent_session_blocking(session_name)
                if agent:
                    agents.append(agent)

        except subprocess.TimeoutExpired:
            logger.warning("Tmux command timed out")
        except FileNotFoundError:
            logger.debug("Tmux not available")
        except Exception as e:
            logger.error(f"Error getting tmux agents: {e}")

        return agents

    async def _parse_agent_session(self, session_name: str) -> AgentState | None:
        """Parse agent state from tmux session (async wrapper)."""
        return await asyncio.to_thread(self._parse_agent_session_blocking, session_name)

    def _parse_agent_session_blocking(self, session_name: str) -> AgentState | None:
        """Parse agent state from tmux session (blocking — runs in thread)."""
        try:
            # Get pane output
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", session_name, "-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            pane_output = result.stdout if result.returncode == 0 else ""

            # Parse agent info from session name (format: forge:domain-project-role or forge:role)
            parts = session_name.split(":")
            agent_name = parts[1] if len(parts) > 1 else session_name

            # Default role from name
            role = "developer"
            domain = None
            project = None

            # Try to extract role/domain/project from name
            if "-" in agent_name:
                name_parts = agent_name.split("-")
                if len(name_parts) >= 2:
                    # Could be domain-project or just role
                    if any(x in agent_name for x in ["tech", "claude", "cursor", "gemini", "opencode", "kimi"]):
                        role = name_parts[-1] if name_parts[-1] else "developer"
                    else:
                        domain = name_parts[0]
                        if len(name_parts) > 1:
                            project = "-".join(name_parts[1:])

            # Determine status from pane output
            status = "active"  # Default
            current_task = None
            context_percentage = 50.0  # Default estimate

            if pane_output:
                # Check for idle indicators
                if any(x in pane_output.lower() for x in ["waiting", "idle", "prompt", "$", ">"]):
                    # Check if it's actually idle vs processing
                    if not any(x in pane_output.lower() for x in ["thinking", "working", "processing"]):
                        status = "idle"

                # Try to extract current task from output
                lines = pane_output.strip().split("\n")
                for line in reversed(lines[-20:]):  # Check last 20 lines
                    if any(x in line.lower() for x in ["task:", "working on", "implementing", "fixing"]):
                        current_task = line.strip()[:100]  # Limit length
                        break

                # Estimate context from output length
                output_len = len(pane_output)
                if output_len > 50000:
                    context_percentage = 90.0
                elif output_len > 30000:
                    context_percentage = 70.0
                elif output_len > 10000:
                    context_percentage = 50.0
                else:
                    context_percentage = 30.0

            return AgentState(
                session_id=session_name,
                agent_name=agent_name,
                agent_role=role,
                domain=domain,
                project=project,
                status=status,
                current_task=current_task,
                context_percentage=context_percentage,
                last_activity=datetime.now(UTC),
                pane_output=pane_output[:500] if pane_output else None,  # Limit stored output
            )

        except Exception as e:
            logger.error(f"Error parsing session {session_name}: {e}")
            return None

    def _state_changed(self, previous: AgentState, current: AgentState) -> bool:
        """Check if agent state has meaningfully changed."""
        # Check key fields
        if previous.status != current.status:
            return True
        if previous.current_task != current.current_task:
            return True
        # Context percentage change > 10%
        if abs(previous.context_percentage - current.context_percentage) > 10:
            return True
        return False

    async def _on_agent_added(self, agent: AgentState) -> None:
        """Handle new agent detected."""
        logger.info(f"New agent detected: {agent.session_id}")

        await self._publish_event(
            "agent.added",
            {
                "session_id": agent.session_id,
                "agent_name": agent.agent_name,
                "agent_role": agent.agent_role,
                "status": agent.status,
                "domain": agent.domain,
                "project": agent.project,
            },
        )

    async def _on_agent_updated(self, agent: AgentState, previous: AgentState) -> None:
        """Handle agent state update."""
        logger.debug(f"Agent updated: {agent.session_id}")

        await self._publish_event(
            "agent.updated",
            {
                "session_id": agent.session_id,
                "agent_name": agent.agent_name,
                "status": agent.status,
                "previous_status": previous.status,
                "current_task": agent.current_task,
                "context_percentage": agent.context_percentage,
                "domain": agent.domain,
                "project": agent.project,
            },
        )

    async def _on_agent_removed(self, agent: AgentState) -> None:
        """Handle agent removal."""
        logger.info(f"Agent removed: {agent.session_id}")

        await self._publish_event(
            "agent.removed",
            {
                "session_id": agent.session_id,
                "agent_name": agent.agent_name,
            },
        )

    async def _update_state_store(self, agent: AgentState) -> None:
        """Update agent state in state store."""
        if self._state_store is None:
            return

        try:
            # Convert to state store format
            agent_data = {
                "session_id": agent.session_id,
                "agent_name": agent.agent_name,
                "agent_role": agent.agent_role,
                "domain": agent.domain,
                "project": agent.project,
                "status": agent.status,
                "current_task": agent.current_task,
                "context_used_percentage": agent.context_percentage,
                "last_heartbeat": agent.last_activity.isoformat(),
            }

            # Update in state store
            if hasattr(self._state_store, "update_agent"):
                self._state_store.update_agent(agent.session_id, agent_data)
            elif hasattr(self._state_store, "set"):
                self._state_store.set(f"agent:{agent.session_id}", json.dumps(agent_data))

        except Exception as e:
            logger.error(f"Failed to update state store: {e}")

    async def _publish_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish event to event bus."""
        if self._event_bus is not None:
            try:
                await self._event_bus.publish(event_type, data)
            except Exception as e:
                logger.error(f"Failed to publish event: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Get sync service statistics."""
        return {
            "running": self._running,
            "sync_count": self._sync_count,
            "poll_interval": self._poll_interval,
            "tracked_agents": len(self._previous_states),
            "last_sync": self._last_sync_result.timestamp.isoformat() if self._last_sync_result else None,
        }
