"""Agent Registry Service

Manages active Claude Code agent sessions with thread-safe access and auto-expiration.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class AgentSession:
    """Represents an active Claude Code agent session.

    Attributes:
        id: Unique agent session ID
        role: Agent role (e.g., "feature-dev", "debug", "review")
        name: Optional agent name
        project: Project being worked on (domain/project format)
        task: Current task description
        status: Agent status ("active", "waiting", "idle", "completed")
        progress: Progress percentage (0-100)
        current_task: Current task being worked on
        files_modified: List of files modified
        token_usage: Token usage statistics
        messages: Messages received/sent
        registered_at: When agent was registered
        last_activity: Last activity timestamp
    """

    id: str
    role: str
    project: str
    task: str
    name: str | None = None
    domain: str | None = None  # Domain for hierarchy (e.g., "codeswiftr-com")
    parent_id: str | None = None  # Parent agent ID for hierarchy tracking
    children: list[str] = field(default_factory=list)  # Child agent IDs
    tmux_session: str | None = None  # tmux session:window location
    skills: list[str] = field(default_factory=list)  # Attached skills
    status: str = "active"
    progress: int = 0
    current_task: str | None = None
    files_modified: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    registered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "domain": self.domain,
            "project": self.project,
            "task": self.task,
            "parent_id": self.parent_id,
            "children": self.children,
            "tmux_session": self.tmux_session,
            "skills": self.skills,
            "status": self.status,
            "progress": self.progress,
            "current_task": self.current_task,
            "files_modified": self.files_modified,
            "token_usage": self.token_usage,
            "messages_count": len(self.messages),
            "registered_at": self.registered_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "is_stale": self.is_stale(),
        }

    def is_expired(self, timeout_seconds: int = 300) -> bool:
        """Check if agent has expired (default 5 minutes)."""
        now = datetime.now(UTC)
        return (now - self.last_activity).total_seconds() > timeout_seconds

    def is_stale(self, heartbeat_timeout_seconds: int = 120) -> bool:
        """Check if agent is stale (no heartbeat for 2 minutes by default)."""
        now = datetime.now(UTC)
        return (now - self.last_activity).total_seconds() > heartbeat_timeout_seconds


class AgentRegistry:
    """In-memory registry for active agent sessions.

    Provides thread-safe access to agent sessions with auto-expiration.
    """

    def __init__(self, expiry_seconds: int = 300):
        """Initialize registry.

        Args:
            expiry_seconds: Seconds of inactivity before agent expires (default 5 min)
        """
        self._agents: dict[str, AgentSession] = {}
        self._lock = Lock()
        self._expiry_seconds = expiry_seconds

    def register(
        self,
        role: str,
        project: str,
        task: str,
        name: str | None = None,
        domain: str | None = None,
        parent_id: str | None = None,
        tmux_session: str | None = None,
        skills: list[str] | None = None,
    ) -> AgentSession:
        """Register a new agent session.

        Args:
            role: Agent role
            project: Project being worked on
            task: Initial task
            name: Optional agent name
            domain: Domain for hierarchy (e.g., "codeswiftr-com")
            parent_id: Parent agent ID for hierarchy tracking
            tmux_session: tmux session:window location
            skills: Attached skills

        Returns:
            Created AgentSession
        """
        with self._lock:
            agent_id = str(uuid.uuid4())[:8]
            agent = AgentSession(
                id=agent_id,
                role=role,
                project=project,
                task=task,
                name=name,
                domain=domain,
                parent_id=parent_id,
                tmux_session=tmux_session,
                skills=skills or [],
            )
            self._agents[agent_id] = agent

            # Update parent's children list if parent exists
            if parent_id and parent_id in self._agents:
                self._agents[parent_id].children.append(agent_id)
                logger.info(f"Agent {agent_id} added as child of {parent_id}")

            logger.info(f"Agent registered: {agent_id} ({role}) for {domain}/{project}")
            return agent

    def get(self, agent_id: str) -> AgentSession | None:
        """Get agent by ID.
        
        Supports lookup by:
        - UUID (full or short 8-char)
        - tmux_session format (e.g., "forge:opencode")
        """
        with self._lock:
            # First try direct lookup by ID
            agent = self._agents.get(agent_id)
            if agent:
                if agent.is_expired(self._expiry_seconds):
                    del self._agents[agent_id]
                    return None
                return agent
            
            # Try looking up by tmux_session (e.g., "forge:opencode")
            for ag in self._agents.values():
                if hasattr(ag, 'tmux_session') and ag.tmux_session == agent_id:
                    if ag.is_expired(self._expiry_seconds):
                        del self._agents[ag.id]
                        return None
                    return ag
            
            # Try looking up by name (case-insensitive)
            agent_id_lower = agent_id.lower()
            for ag in self._agents.values():
                if ag.name and ag.name.lower() == agent_id_lower:
                    if ag.is_expired(self._expiry_seconds):
                        del self._agents[ag.id]
                        return None
                    return ag
            
            return None

    def list_active(self) -> list[AgentSession]:
        """List all active (non-expired) agents."""
        with self._lock:
            self._cleanup_expired()
            return list(self._agents.values())

    def update_progress(
        self,
        agent_id: str,
        progress: int,
        current_task: str | None = None,
        files_modified: list[str] | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> AgentSession | None:
        """Update agent progress.

        Args:
            agent_id: Agent ID
            progress: Progress percentage (0-100)
            current_task: Current task description
            files_modified: Files modified (appended to list)
            token_usage: Token usage update (merged with existing)

        Returns:
            Updated agent or None if not found
        """
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return None
            agent.progress = progress
            agent.last_activity = datetime.now(UTC)
            if current_task:
                agent.current_task = current_task
            if files_modified:
                for f in files_modified:
                    if f not in agent.files_modified:
                        agent.files_modified.append(f)
            if token_usage:
                for k, v in token_usage.items():
                    agent.token_usage[k] = agent.token_usage.get(k, 0) + v
            return agent

    def complete(self, agent_id: str, summary: str | None = None) -> AgentSession | None:
        """Mark agent as completed.

        Args:
            agent_id: Agent ID
            summary: Optional completion summary

        Returns:
            Completed agent or None if not found
        """
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return None
            agent.status = "completed"
            agent.progress = 100
            agent.last_activity = datetime.now(UTC)
            if summary:
                agent.messages.append(
                    {
                        "type": "completion",
                        "content": summary,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            return agent

    def pause(self, agent_id: str) -> tuple[AgentSession | None, str | None]:
        """Pause an agent by setting its status to 'paused'.

        Returns (agent, previous_status) or (None, None) if not found.
        """
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return None, None
            previous_status = agent.status
            agent.status = "paused"
            agent.last_activity = datetime.now(UTC)
            return agent, previous_status

    def resume(self, agent_id: str) -> tuple[AgentSession | None, str | None]:
        """Resume an agent by setting its status back to 'active'.

        Returns (agent, previous_status) or (None, None) if not found.
        """
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return None, None
            previous_status = agent.status
            agent.status = "active"
            agent.last_activity = datetime.now(UTC)
            return agent, previous_status

    def kill(
        self, agent_id: str, reason: str | None = None
    ) -> tuple[AgentSession | None, str | None]:
        """Kill an agent by marking its status as 'failed'.

        The agent remains in the registry for visibility but is no longer active.

        Returns (agent, previous_status) or (None, None) if not found.
        """
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return None, None
            previous_status = agent.status
            agent.status = "failed"
            agent.last_activity = datetime.now(UTC)
            if reason:
                agent.messages.append(
                    {
                        "type": "kill",
                        "content": reason,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            return agent, previous_status

    def send_message(
        self,
        agent_id: str,
        message: dict[str, Any],
        sender_id: str = "system",
        use_queue: bool = True,
    ) -> tuple[AgentSession | None, str | None]:
        """Send a message to an agent.

        Args:
            agent_id: Target agent ID
            message: Message dict with type, content, etc.
            sender_id: ID of sending agent (default: "system")
            use_queue: If True, use message queue with ACK tracking

        Returns:
            Tuple of (agent, message_id) or (None, None) if agent not found
        """
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return None, None

            message_id = None
            if use_queue:
                # Import here to avoid circular import
                from .messaging import get_message_queue

                # Use message queue for tracking
                queue = get_message_queue()
                queued_msg = queue.enqueue(
                    sender_id=sender_id,
                    recipient_id=agent_id,
                    content=message.get("content", ""),
                    msg_type=message.get("type", "instruction"),
                    priority=message.get("priority", 1),
                    metadata=message.get("metadata", {}),
                )
                message_id = queued_msg.id

            # Also append to agent's messages list for backward compatibility
            message["timestamp"] = datetime.now(UTC).isoformat()
            if message_id:
                message["message_id"] = message_id
            agent.messages.append(message)

            return agent, message_id

    def broadcast(self, message: dict[str, Any]) -> int:
        """Broadcast message to all active agents.

        Args:
            message: Message dict

        Returns:
            Number of agents messaged
        """
        with self._lock:
            self._cleanup_expired()
            message["timestamp"] = datetime.now(UTC).isoformat()
            count = 0
            for agent in self._agents.values():
                agent.messages.append(message.copy())
                count += 1
            return count

    def _cleanup_expired(self) -> int:
        """Remove expired agents. Must be called with lock held."""
        expired = [aid for aid, a in self._agents.items() if a.is_expired(self._expiry_seconds)]
        for aid in expired:
            del self._agents[aid]
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired agents")
        return len(expired)


# Global agent registry
_agent_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Get or create global agent registry."""
    global _agent_registry
    if _agent_registry is None:
        _agent_registry = AgentRegistry()
    return _agent_registry
