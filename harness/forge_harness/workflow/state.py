"""Fleet State Manager - Manages agent state transitions and persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class AgentState(Enum):
    """Finite state machine states for agents."""

    IDLE = "idle"
    ASSIGNED = "assigned"
    WORKING = "working"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"
    RESTARTING = "restarting"
    TERMINATED = "terminated"


# Valid state transitions
VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {AgentState.ASSIGNED, AgentState.PAUSED, AgentState.TERMINATED},
    AgentState.ASSIGNED: {AgentState.WORKING, AgentState.IDLE, AgentState.FAILED},
    AgentState.WORKING: {AgentState.COMPLETED, AgentState.FAILED, AgentState.PAUSED, AgentState.RECOVERING},
    AgentState.PAUSED: {AgentState.WORKING, AgentState.IDLE, AgentState.TERMINATED},
    AgentState.COMPLETED: {AgentState.IDLE, AgentState.ASSIGNED},
    AgentState.FAILED: {AgentState.RECOVERING, AgentState.IDLE, AgentState.RESTARTING, AgentState.TERMINATED},
    AgentState.RECOVERING: {AgentState.IDLE, AgentState.WORKING, AgentState.FAILED},
    AgentState.RESTARTING: {AgentState.IDLE, AgentState.FAILED},
    AgentState.TERMINATED: set(),  # Terminal state
}


@dataclass
class AgentStateRecord:
    """Complete state record for an agent."""

    agent_id: str
    state: AgentState = AgentState.IDLE
    current_task: str | None = None
    current_workflow: str | None = None
    context_percentage: int = 0
    last_heartbeat: datetime | None = None
    last_state_change: datetime = field(default_factory=lambda: datetime.now(UTC))
    state_history: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def can_transition_to(self, new_state: AgentState) -> bool:
        """Check if transition to new_state is valid."""
        return new_state in VALID_TRANSITIONS.get(self.state, set())

    def transition(self, new_state: AgentState, reason: str | None = None) -> bool:
        """Attempt to transition to new state."""
        if not self.can_transition_to(new_state):
            logger.warning(
                "Invalid state transition: %s -> %s for agent %s",
                self.state.value,
                new_state.value,
                self.agent_id,
            )
            return False

        # Record state change
        old_state = self.state
        self.state_history.append({
            "from": old_state.value,
            "to": new_state.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "reason": reason,
        })

        self.state = new_state
        self.last_state_change = datetime.now(UTC)
        logger.info(
            "Agent %s transitioned: %s -> %s (reason: %s)",
            self.agent_id,
            old_state.value,
            new_state.value,
            reason or "unspecified",
        )
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "agent_id": self.agent_id,
            "state": self.state.value,
            "current_task": self.current_task,
            "current_workflow": self.current_workflow,
            "context_percentage": self.context_percentage,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "last_state_change": self.last_state_change.isoformat(),
            "state_history": self.state_history,
            "capabilities": self.capabilities,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentStateRecord:
        """Deserialize from dictionary."""
        record = cls(
            agent_id=data["agent_id"],
            state=AgentState(data.get("state", "idle")),
            current_task=data.get("current_task"),
            current_workflow=data.get("current_workflow"),
            context_percentage=data.get("context_percentage", 0),
            state_history=data.get("state_history", []),
            capabilities=data.get("capabilities", []),
            metadata=data.get("metadata", {}),
        )
        if data.get("last_heartbeat"):
            record.last_heartbeat = datetime.fromisoformat(data["last_heartbeat"])
        if data.get("last_state_change"):
            record.last_state_change = datetime.fromisoformat(data["last_state_change"])
        return record


class FleetStateManager:
    """Manages state for all agents in the fleet."""

    def __init__(self, state_dir: Path | None = None) -> None:
        """Initialize state manager.

        Args:
            state_dir: Directory for state persistence. If None, uses .forge/workflow/state/
        """
        if state_dir is None:
            # Navigate from forge_harness location to FORGE root
            forge_root = Path(__file__).resolve().parents[3]
            state_dir = forge_root / ".forge" / "workflow" / "state"

        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, AgentStateRecord] = {}
        self._load_all_states()

    def _get_state_path(self, agent_id: str) -> Path:
        """Get the file path for an agent's state."""
        return self.state_dir / f"{agent_id}.json"

    def _load_all_states(self) -> None:
        """Load all agent states from disk."""
        if not self.state_dir.exists():
            return

        for path in self.state_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                record = AgentStateRecord.from_dict(data)
                self._agents[record.agent_id] = record
                logger.debug("Loaded state for agent %s", record.agent_id)
            except Exception as exc:
                logger.warning("Failed to load state from %s: %s", path, exc)

    def _save_state(self, agent_id: str) -> None:
        """Persist agent state to disk."""
        if agent_id not in self._agents:
            return

        path = self._get_state_path(agent_id)
        try:
            path.write_text(json.dumps(self._agents[agent_id].to_dict(), indent=2))
        except Exception as exc:
            logger.error("Failed to save state for %s: %s", agent_id, exc)

    def register_agent(
        self,
        agent_id: str,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentStateRecord:
        """Register a new agent in the fleet."""
        if agent_id in self._agents:
            logger.debug("Agent %s already registered", agent_id)
            return self._agents[agent_id]

        record = AgentStateRecord(
            agent_id=agent_id,
            state=AgentState.IDLE,
            capabilities=capabilities or [],
            metadata=metadata or {},
        )
        self._agents[agent_id] = record
        self._save_state(agent_id)
        logger.info("Registered agent %s with capabilities: %s", agent_id, capabilities)
        return record

    def get_agent(self, agent_id: str) -> AgentStateRecord | None:
        """Get state record for an agent."""
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[AgentStateRecord]:
        """Get all agent state records."""
        return list(self._agents.values())

    def get_agents_by_state(self, state: AgentState) -> list[AgentStateRecord]:
        """Get all agents in a specific state."""
        return [a for a in self._agents.values() if a.state == state]

    def get_available_agents(self, capabilities: list[str] | None = None) -> list[AgentStateRecord]:
        """Get agents available for task assignment."""
        available: list[AgentStateRecord] = []
        for agent in self._agents.values():
            if agent.state not in (AgentState.IDLE, AgentState.COMPLETED):
                continue
            if capabilities:
                if not all(cap in agent.capabilities for cap in capabilities):
                    continue
            available.append(agent)
        return available

    def transition_agent(
        self,
        agent_id: str,
        new_state: AgentState,
        reason: str | None = None,
    ) -> bool:
        """Transition an agent to a new state."""
        agent = self._agents.get(agent_id)
        if not agent:
            logger.error("Agent %s not found", agent_id)
            return False

        success = agent.transition(new_state, reason)
        if success:
            self._save_state(agent_id)
        return success

    def assign_task(self, agent_id: str, task_id: str, workflow_id: str | None = None) -> bool:
        """Assign a task to an agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False

        if agent.state not in (AgentState.IDLE, AgentState.COMPLETED):
            logger.warning("Agent %s not available for assignment (state: %s)", agent_id, agent.state.value)
            return False

        agent.current_task = task_id
        agent.current_workflow = workflow_id
        return self.transition_agent(agent_id, AgentState.ASSIGNED, f"Assigned task {task_id}")

    def release_agent(self, agent_id: str, status: str = "completed") -> bool:
        """Release an agent from its current task."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False

        agent.current_task = None
        agent.current_workflow = None

        if status == "completed":
            return self.transition_agent(agent_id, AgentState.COMPLETED, "Task completed")
        elif status == "failed":
            return self.transition_agent(agent_id, AgentState.FAILED, "Task failed")
        else:
            return self.transition_agent(agent_id, AgentState.IDLE, "Task released")

    def update_heartbeat(self, agent_id: str, context_percentage: int | None = None) -> None:
        """Update agent heartbeat timestamp."""
        agent = self._agents.get(agent_id)
        if not agent:
            return

        agent.last_heartbeat = datetime.now(UTC)
        if context_percentage is not None:
            agent.context_percentage = context_percentage
        self._save_state(agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent from the fleet."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            path = self._get_state_path(agent_id)
            if path.exists():
                path.unlink()
            logger.info("Unregistered agent %s", agent_id)

    def get_fleet_summary(self) -> dict[str, Any]:
        """Get summary of fleet state."""
        by_state: dict[str, int] = {}
        for state in AgentState:
            by_state[state.value] = 0

        for agent in self._agents.values():
            by_state[agent.state.value] += 1

        return {
            "total_agents": len(self._agents),
            "by_state": by_state,
            "available": len(self.get_available_agents()),
            "working": len(self.get_agents_by_state(AgentState.WORKING)),
            "failed": len(self.get_agents_by_state(AgentState.FAILED)),
        }
