"""
Command Center API Client
=========================

Unified async client for interacting with the Command Center API.
Used by both CLI and TUI interfaces for consistent API access.

Usage:
    from forge_harness.command_center_client import CommandCenterClient

    async with CommandCenterClient() as client:
        agents = await client.list_agents()
        await client.send_message("agent-id", "Hello")

CLI Usage:
    client = CommandCenterClient.from_env()
    asyncio.run(client.list_agents())
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .approval_queue import ApprovalStatus
from .logging_config import get_logger
from .meta_learning.schemas import DecisionTier
from .task_queue import TaskPriority, TaskStatus

logger = get_logger(__name__)


# =============================================================================
# Data Models
# =============================================================================

# Re-export for public API
__all__ = [
    "TaskStatus",
    "TaskPriority",
    "ApprovalStatus",
    "DecisionTier",
]


class AgentStatus(str, Enum):
    """Agent status values."""

    ACTIVE = "active"
    IDLE = "idle"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class Agent:
    """Agent data model."""

    id: str
    role: str
    name: str | None
    project: str | None
    task: str | None
    status: AgentStatus
    progress: int
    current_task: str | None
    files_modified: list[str]
    token_usage: dict[str, int]
    messages_count: int
    registered_at: datetime | None
    last_activity: datetime | None
    is_stale: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent:
        """Create Agent from API response dict."""
        return cls(
            id=data.get("id", ""),
            role=data.get("role", "unknown"),
            name=data.get("name"),
            project=data.get("project"),
            task=data.get("task"),
            status=AgentStatus(data.get("status", "active")),
            progress=data.get("progress", 0),
            current_task=data.get("current_task"),
            files_modified=data.get("files_modified", []),
            token_usage=data.get("token_usage", {}),
            messages_count=data.get("messages_count", 0),
            registered_at=_parse_datetime(data.get("registered_at")),
            last_activity=_parse_datetime(data.get("last_activity")),
            is_stale=data.get("is_stale", False),
        )


@dataclass
class Approval:
    """Approval request data model."""

    id: str
    type: str
    title: str
    description: str | None
    domain: str | None
    project: str | None
    priority: str
    status: ApprovalStatus
    tier: DecisionTier | None
    context: dict[str, Any]
    created_at: datetime | None
    resolved_at: datetime | None
    resolved_by: str | None
    notes: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Approval:
        """Create Approval from API response dict."""
        tier_str = data.get("tier")
        tier = DecisionTier(tier_str) if tier_str else None

        return cls(
            id=data.get("id", ""),
            type=data.get("type", "unknown"),
            title=data.get("title", "Untitled"),
            description=data.get("description"),
            domain=data.get("domain"),
            project=data.get("project"),
            priority=data.get("priority", "normal"),
            status=ApprovalStatus(data.get("status", "pending")),
            tier=tier,
            context=data.get("context", {}),
            created_at=_parse_datetime(data.get("created_at")),
            resolved_at=_parse_datetime(data.get("resolved_at")),
            resolved_by=data.get("resolved_by"),
            notes=data.get("notes"),
        )


@dataclass
class Pattern:
    """Pattern data model."""

    id: str
    name: str
    description: str | None
    category: str
    success_rate: float
    usage_count: int
    created_at: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pattern:
        """Create Pattern from API response dict."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description"),
            category=data.get("category", "general"),
            success_rate=data.get("success_rate", 0.0),
            usage_count=data.get("usage_count", 0),
            created_at=_parse_datetime(data.get("created_at")),
        )


@dataclass
class Task:
    """Task data model."""

    id: str
    subject: str
    description: str
    priority: TaskPriority
    status: TaskStatus
    required_role: str | None
    claimed_by: str | None
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None
    order: int = 0
    domain: str | None = None
    project: str | None = None
    task_type: str | None = None
    complexity: str | None = None
    blocked_by: list[str] | None = None
    estimated_hours: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        """Create Task from API response dict."""
        return cls(
            id=data.get("id", ""),
            subject=data.get("subject", ""),
            description=data.get("description", ""),
            priority=TaskPriority(data.get("priority", "medium")),
            status=TaskStatus(data.get("status", "pending")),
            required_role=data.get("required_role"),
            claimed_by=data.get("claimed_by"),
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
            completed_at=_parse_datetime(data.get("completed_at")),
            order=data.get("order", 0),
            domain=data.get("domain"),
            project=data.get("project"),
            task_type=data.get("task_type"),
            complexity=data.get("complexity"),
            blocked_by=data.get("blocked_by"),
            estimated_hours=data.get("estimated_hours"),
        )


@dataclass
class PortfolioProject:
    """Portfolio project data model."""

    domain: str
    project: str
    status: str
    description: str | None
    tech_stack: list[str]
    last_activity: datetime | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortfolioProject:
        """Create PortfolioProject from API response dict."""
        return cls(
            domain=data.get("domain", ""),
            project=data.get("project", ""),
            status=data.get("status", "unknown"),
            description=data.get("description"),
            tech_stack=data.get("tech_stack", []),
            last_activity=_parse_datetime(data.get("last_activity")),
        )


@dataclass
class SSEEvent:
    """Server-Sent Event data model."""

    type: str
    data: dict[str, Any]
    timestamp: datetime | None = None

    @classmethod
    def from_line(cls, line: str) -> SSEEvent | None:
        """Parse SSE event from data line."""
        if not line.startswith("data: "):
            return None
        try:
            data = json.loads(line[6:])
            return cls(
                type=data.get("type", "unknown"),
                data=data.get("data", {}),
                timestamp=_parse_datetime(data.get("timestamp")),
            )
        except json.JSONDecodeError:
            return None


@dataclass
class APIResponse:
    """Generic API response wrapper."""

    success: bool
    data: Any = None
    error: str | None = None
    timestamp: datetime | None = None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO datetime string."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# =============================================================================
# API Client
# =============================================================================


class CommandCenterClient:
    """
    Async client for Command Center API.

    Provides methods for all Command Center operations:
    - Agent management
    - Approval queue
    - Portfolio queries
    - Pattern library
    - Real-time event streaming
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        token: str | None = None,
        timeout: float = 30.0,
    ):
        """
        Initialize client.

        Args:
            base_url: Command Center API base URL
            token: Authentication token (optional for localhost)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._session: Any = None  # aiohttp.ClientSession

    @classmethod
    def from_env(cls) -> CommandCenterClient:
        """Create client from environment variables."""
        return cls(
            base_url=os.environ.get("COMMAND_CENTER_URL", "http://localhost:8080"),
            token=os.environ.get("FORGE_WEBHOOK_TOKEN"),
        )

    async def __aenter__(self) -> CommandCenterClient:
        """Async context manager entry."""
        await self._ensure_session()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_session(self) -> None:
        """Ensure aiohttp session exists."""
        if self._session is None:
            try:
                import aiohttp

                timeout = aiohttp.ClientTimeout(total=self.timeout)
                self._session = aiohttp.ClientSession(timeout=timeout)
            except ImportError:
                raise ImportError(
                    "aiohttp is required for async operations. Install with: uv add aiohttp"
                )

    async def close(self) -> None:
        """Close the client session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _headers(self) -> dict[str, str]:
        """Get request headers."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> APIResponse:
        """Make HTTP request to API."""
        await self._ensure_session()

        url = f"{self.base_url}{path}"
        kwargs: dict[str, Any] = {"headers": self._headers()}

        if data:
            kwargs["json"] = data
        if params:
            kwargs["params"] = params

        try:
            async with self._session.request(method, url, **kwargs) as resp:
                body = await resp.json()

                if resp.status >= 400:
                    return APIResponse(
                        success=False,
                        error=body.get("detail", f"HTTP {resp.status}"),
                    )

                return APIResponse(
                    success=body.get("success", True),
                    data=body.get("data", body),
                    error=body.get("error"),
                    timestamp=_parse_datetime(body.get("timestamp")),
                )
        except Exception as e:
            # Connection errors are expected when API is not running - log as debug
            import aiohttp

            connection_errors = (
                ConnectionRefusedError,
                ConnectionError,
                OSError,
            )
            try:
                connection_errors = connection_errors + (
                    aiohttp.ClientConnectionError,
                    aiohttp.ServerTimeoutError,
                )
            except AttributeError:
                pass

            if isinstance(e, connection_errors):
                logger.debug(f"API request failed (API not available): {e}")
            else:
                logger.error(f"API request failed: {e}")
            return APIResponse(success=False, error=str(e))

    # =========================================================================
    # Agent Operations
    # =========================================================================

    async def list_agents(
        self,
        status: AgentStatus | None = None,
    ) -> list[Agent]:
        """
        List all registered agents.

        Args:
            status: Filter by status (optional)

        Returns:
            List of Agent objects
        """
        params = {}
        if status:
            params["status"] = status.value

        response = await self._request("GET", "/api/agents", params=params)
        if not response.success:
            logger.debug(f"Failed to list agents: {response.error}")
            return []

        agents_data = response.data
        if isinstance(agents_data, dict):
            agents_data = agents_data.get("agents", [])

        return [Agent.from_dict(a) for a in agents_data]

    async def get_agent(self, agent_id: str) -> Agent | None:
        """Get agent by ID."""
        response = await self._request("GET", f"/api/agents/{agent_id}")
        if not response.success:
            return None
        return Agent.from_dict(response.data)

    async def register_agent(
        self,
        role: str,
        task: str,
        project: str | None = None,
        name: str | None = None,
    ) -> Agent | None:
        """
        Register a new agent.

        Args:
            role: Agent role (e.g., "content-agent", "tech-agent")
            task: Current task description
            project: Project path (optional)
            name: Agent name (optional)

        Returns:
            Registered Agent or None on failure
        """
        data = {"role": role, "task": task}
        if project:
            data["project"] = project
        if name:
            data["name"] = name

        response = await self._request("POST", "/api/agents/register", data=data)
        if not response.success:
            logger.debug(f"Failed to register agent: {response.error}")
            return None
        return Agent.from_dict(response.data)

    async def update_agent_progress(
        self,
        agent_id: str,
        progress: int,
        current_task: str | None = None,
        files_modified: list[str] | None = None,
    ) -> bool:
        """Update agent progress."""
        data: dict[str, Any] = {"progress": progress}
        if current_task:
            data["current_task"] = current_task
        if files_modified:
            data["files_modified"] = files_modified

        response = await self._request("POST", f"/api/agents/{agent_id}/progress", data=data)
        return response.success

    async def complete_agent(
        self,
        agent_id: str,
        summary: str | None = None,
    ) -> bool:
        """Mark agent as completed."""
        data = {}
        if summary:
            data["summary"] = summary

        response = await self._request("POST", f"/api/agents/{agent_id}/complete", data=data)
        return response.success

    async def heartbeat(self, agent_id: str) -> bool:
        """Send heartbeat to indicate agent is still alive.

        Should be called periodically (e.g., every 30 seconds) to prevent
        the agent from being marked as stale.
        """
        response = await self._request("POST", f"/api/agents/{agent_id}/heartbeat")
        return response.success

    def sync_heartbeat(self, agent_id: str) -> bool:
        """Sync wrapper for heartbeat."""
        return self._run_sync(self.heartbeat(agent_id))

    async def send_message(
        self,
        agent_id: str,
        content: str,
        message_type: str = "instruction",
    ) -> bool:
        """
        Send a message to an agent.

        Args:
            agent_id: Target agent ID
            content: Message content
            message_type: Message type (instruction, query, etc.)

        Returns:
            True if sent successfully
        """
        response = await self._request(
            "POST",
            f"/api/agents/{agent_id}/message",
            data={"type": message_type, "content": content},
        )
        return response.success

    async def broadcast_message(
        self,
        content: str,
        message_type: str = "instruction",
    ) -> int:
        """
        Broadcast message to all agents.

        Args:
            content: Message content
            message_type: Message type

        Returns:
            Number of agents messaged
        """
        response = await self._request(
            "POST",
            "/api/agents/broadcast",
            data={"type": message_type, "content": content},
        )
        if response.success and isinstance(response.data, dict):
            return response.data.get("sent_count", 0)
        return 0

    async def pause_agent(self, agent_id: str) -> bool:
        """Pause a running agent.

        Args:
            agent_id: Agent identifier to pause.

        Returns:
            True if agent was successfully paused.
        """
        response = await self._request("POST", f"/api/agents/{agent_id}/pause")
        if not response.success:
            logger.debug(f"Failed to pause agent {agent_id}: {response.error}")
            return False
        data = response.data
        if isinstance(data, dict):
            return data.get("success", False)
        return response.success

    async def resume_agent(self, agent_id: str) -> bool:
        """Resume a paused agent.

        Args:
            agent_id: Agent identifier to resume.

        Returns:
            True if agent was successfully resumed.
        """
        response = await self._request("POST", f"/api/agents/{agent_id}/resume")
        if not response.success:
            logger.debug(f"Failed to resume agent {agent_id}: {response.error}")
            return False
        data = response.data
        if isinstance(data, dict):
            return data.get("success", False)
        return response.success

    async def kill_agent(self, agent_id: str) -> bool:
        """Forcefully terminate an agent.

        Args:
            agent_id: Agent identifier to kill.

        Returns:
            True if agent was successfully killed.
        """
        response = await self._request("POST", f"/api/agents/{agent_id}/kill")
        if not response.success:
            logger.debug(f"Failed to kill agent {agent_id}: {response.error}")
            return False
        return True

    async def fleet_pause(self) -> dict[str, Any]:
        """Pause all agents in the fleet.

        Returns:
            Dict with operation result including count of paused agents.
        """
        response = await self._request("POST", "/api/agents/fleet/pause")
        if not response.success:
            logger.debug(f"Failed to pause fleet: {response.error}")
            return {"success": False, "error": response.error}
        data = response.data
        if isinstance(data, dict):
            return data
        return {"success": True}

    async def fleet_resume(self) -> dict[str, Any]:
        """Resume all paused agents in the fleet.

        Returns:
            Dict with operation result including count of resumed agents.
        """
        response = await self._request("POST", "/api/agents/fleet/resume")
        if not response.success:
            logger.debug(f"Failed to resume fleet: {response.error}")
            return {"success": False, "error": response.error}
        data = response.data
        if isinstance(data, dict):
            return data
        return {"success": True}

    async def get_agent_logs(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch log entries for a specific agent.

        Args:
            agent_id: Agent identifier.
            limit: Maximum number of log entries to return (default 50).

        Returns:
            List of log entry dicts, empty list on failure.
        """
        response = await self._request(
            "GET",
            f"/api/agents/{agent_id}/logs",
            params={"limit": limit},
        )
        if not response.success:
            logger.debug(f"Failed to get logs for agent {agent_id}: {response.error}")
            return []
        data = response.data
        if isinstance(data, dict):
            return data.get("logs", [])
        if isinstance(data, list):
            return data
        return []

    # =========================================================================
    # Approval Operations
    # =========================================================================

    async def list_approvals(
        self,
        status: ApprovalStatus | None = None,
        domain: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[Approval]:
        """List approval requests."""
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status.value
        if domain:
            params["domain"] = domain
        if priority:
            params["priority"] = priority

        response = await self._request("GET", "/api/approvals", params=params)
        if not response.success:
            return []

        approvals_data = response.data
        if isinstance(approvals_data, dict):
            approvals_data = approvals_data.get("approvals", [])

        return [Approval.from_dict(a) for a in approvals_data]

    async def get_approval(self, request_id: str) -> Approval | None:
        """Get approval by ID."""
        response = await self._request("GET", f"/api/approvals/{request_id}")
        if not response.success:
            return None
        return Approval.from_dict(response.data)

    async def approve(self, request_id: str, notes: str = "") -> bool:
        """Approve a request."""
        response = await self._request(
            "POST",
            f"/api/approvals/{request_id}/approve",
            data={"notes": notes, "approved_by": "cli"},
        )
        return response.success

    async def reject(self, request_id: str, reason: str = "") -> bool:
        """Reject a request."""
        response = await self._request(
            "POST",
            f"/api/approvals/{request_id}/reject",
            data={"reason": reason, "rejected_by": "cli"},
        )
        return response.success

    async def get_approval_count(self) -> int:
        """Get count of pending approvals."""
        response = await self._request("GET", "/api/approvals/count")
        if response.success and isinstance(response.data, dict):
            return response.data.get("count", 0)
        return 0

    async def get_approval_stats(self) -> dict[str, Any]:
        """Get approval queue statistics."""
        response = await self._request("GET", "/api/approvals/stats")
        if response.success:
            return response.data or {}
        return {}

    # =========================================================================
    # Portfolio Operations
    # =========================================================================

    async def list_portfolio(self) -> list[dict[str, Any]]:
        """List all domains in portfolio."""
        response = await self._request("GET", "/api/portfolio")
        if not response.success:
            return []
        return response.data if isinstance(response.data, list) else []

    async def get_domain(self, domain: str) -> dict[str, Any] | None:
        """Get domain details."""
        response = await self._request("GET", f"/api/portfolio/{domain}")
        if not response.success:
            return None
        return response.data

    async def get_project(self, domain: str, project: str) -> dict[str, Any] | None:
        """Get project details."""
        response = await self._request("GET", f"/api/portfolio/{domain}/{project}")
        if not response.success:
            return None
        return response.data

    # =========================================================================
    # Pattern Operations
    # =========================================================================

    async def list_patterns(
        self,
        category: str | None = None,
        limit: int = 50,
    ) -> list[Pattern]:
        """List patterns."""
        params: dict[str, Any] = {"limit": limit}
        if category:
            params["category"] = category

        response = await self._request("GET", "/api/patterns", params=params)
        if not response.success:
            return []

        patterns_data = response.data
        if isinstance(patterns_data, dict):
            patterns_data = patterns_data.get("patterns", [])

        return [Pattern.from_dict(p) for p in patterns_data]

    async def get_pattern(self, pattern_id: str) -> Pattern | None:
        """Get pattern by ID."""
        response = await self._request("GET", f"/api/patterns/{pattern_id}")
        if not response.success:
            return None
        return Pattern.from_dict(response.data)

    async def create_pattern(
        self,
        name: str,
        description: str | None = None,
        category: str = "general",
    ) -> Pattern | None:
        """Create a new pattern."""
        data = {"name": name, "category": category}
        if description:
            data["description"] = description

        response = await self._request("POST", "/api/patterns", data=data)
        if not response.success:
            return None
        return Pattern.from_dict(response.data)

    async def record_outcome(
        self,
        pattern_id: str,
        success: bool,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Record pattern outcome."""
        data: dict[str, Any] = {"success": success}
        if context:
            data["context"] = context

        response = await self._request("POST", f"/api/patterns/{pattern_id}/outcome", data=data)
        return response.success

    # =========================================================================
    # Task Operations
    # =========================================================================

    async def list_tasks(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters."""
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if priority:
            params["priority"] = priority

        response = await self._request("GET", "/api/tasks", params=params)
        if not response.success:
            return []

        data = response.data
        if isinstance(data, dict):
            tasks_list = data.get("tasks", [])
        elif isinstance(data, list):
            tasks_list = data
        else:
            tasks_list = []
        return tasks_list

    async def claim_task(self, task_id: str, agent_id: str) -> dict[str, Any] | None:
        """Claim a task for an agent."""
        response = await self._request(
            "POST",
            f"/api/tasks/{task_id}/claim",
            data={"agent_id": agent_id},
        )
        if not response.success:
            return None
        return response.data

    async def dispatch_task(self, task_id: str, agent_id: str) -> dict[str, Any] | None:
        """Dispatch task to an agent."""
        response = await self._request(
            "POST",
            f"/api/tasks/{task_id}/dispatch",
            data={"agent_id": agent_id},
        )
        if not response.success:
            return None
        return response.data

    async def create_task(
        self,
        subject: str,
        description: str = "",
        priority: str = "medium",
        required_role: str | None = None,
        domain: str | None = None,
        project: str | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Create a new task."""
        data: dict[str, Any] = {
            "subject": subject,
            "description": description,
            "priority": priority,
        }
        if required_role:
            data["required_role"] = required_role
        if domain:
            data["domain"] = domain
        if project:
            data["project"] = project
        if depends_on:
            data["depends_on"] = depends_on

        response = await self._request("POST", "/api/tasks", data=data)
        if not response.success:
            logger.debug(f"Failed to create task: {response.error}")
            return None
        return response.data

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get task by ID."""
        response = await self._request("GET", f"/api/tasks/{task_id}")
        if not response.success:
            return None
        return response.data

    async def get_recommended_tasks(
        self,
        limit: int = 10,
        agent_id: str | None = None,
        include_blocked: bool = False,
        min_priority: str | None = None,
        task_type: str | None = None,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get recommended tasks for execution."""
        params: dict[str, Any] = {"limit": limit}
        if agent_id:
            params["agent_id"] = agent_id
        if include_blocked:
            params["include_blocked"] = "true"
        if min_priority:
            params["min_priority"] = min_priority
        if task_type:
            params["task_type"] = task_type
        if domain:
            params["domain"] = domain

        response = await self._request("GET", "/api/tasks/recommended", params=params)
        if not response.success:
            return []

        data = response.data
        if isinstance(data, dict):
            # API returns {recommendations: [{task: {...}, ...}], meta: {...}}
            recs = data.get("recommendations", [])
            if recs:
                return [r.get("task", r) for r in recs]
            return data.get("tasks", [])
        elif isinstance(data, list):
            return data
        return []

    async def update_task(
        self,
        task_id: str,
        subject: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        required_role: str | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Update a task."""
        updates: dict[str, Any] = {}
        if subject is not None:
            updates["subject"] = subject
        if description is not None:
            updates["description"] = description
        if priority is not None:
            updates["priority"] = priority
        if status is not None:
            updates["status"] = status
        if required_role is not None:
            updates["required_role"] = required_role
        if depends_on is not None:
            updates["depends_on"] = depends_on

        response = await self._request("PUT", f"/api/tasks/{task_id}", data=updates)
        if not response.success:
            return None
        return response.data

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        response = await self._request("DELETE", f"/api/tasks/{task_id}")
        return response.success

    async def get_task_stats(self) -> dict[str, Any]:
        """Get task queue statistics."""
        response = await self._request("GET", "/api/tasks/stats")
        if response.success:
            return response.data or {}
        return {}

    async def list_ready_tasks(
        self,
        priority: str | None = None,
        limit: int = 50,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """List tasks that are ready for execution (all dependencies met).

        Returns:
            Dict with "tasks", "count", and "blocked_count" keys.
        """
        params: dict[str, Any] = {"limit": limit}
        if priority:
            params["priority"] = priority
        if agent:
            params["agent"] = agent

        response = await self._request("GET", "/api/tasks/ready", params=params)
        if not response.success:
            return {"tasks": [], "count": 0, "blocked_count": 0}

        data = response.data
        if isinstance(data, dict):
            return {
                "tasks": data.get("tasks", []),
                "count": data.get("count", 0),
                "blocked_count": data.get("blocked_count", 0),
            }
        return {"tasks": [], "count": 0, "blocked_count": 0}

    async def list_active_leases(self) -> list[dict[str, Any]]:
        """Return tasks that currently hold an active lease.

        Fetches ``GET /api/tasks`` and filters to entries that have a
        non-empty ``lease`` sub-object, projecting each to the canonical
        active-lease shape:

            {
                "task_id":    str,
                "lease_state":  str,
                "lease_owner":  str,
                "lease_node":   str,
                "path_lock":    str,
                "expires_at":   str,
            }

        Returns:
            List of active-lease dicts.  Empty list when the API is
            unreachable or no leased tasks exist.
        """
        response = await self._request("GET", "/api/tasks")
        if not response.success:
            logger.debug(f"Failed to list tasks for lease extraction: {response.error}")
            return []

        data = response.data
        if isinstance(data, dict):
            tasks_list: list[dict[str, Any]] = data.get("tasks", [])
        elif isinstance(data, list):
            tasks_list = data
        else:
            tasks_list = []

        leases: list[dict[str, Any]] = []
        for task in tasks_list:
            if not isinstance(task, dict):
                continue
            lease = task.get("lease")
            if not isinstance(lease, dict) or not lease:
                continue
            # Only include leases that have an owner (truly active)
            owner_agent = lease.get("owner_agent", "") or ""
            owner_node = lease.get("owner_node", "") or ""
            expires_at = str(lease.get("lease_expires_at", "") or "")
            if not owner_agent and not owner_node:
                continue
            leases.append(
                {
                    "task_id": task.get("id", ""),
                    "lease_state": task.get("status", ""),
                    "lease_owner": owner_agent,
                    "lease_node": owner_node,
                    "path_lock": lease.get("path_lock", ""),
                    "expires_at": expires_at,
                }
            )
        return leases

    async def reorder_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        """Reorder tasks by task IDs."""
        response = await self._request("POST", "/api/tasks/reorder", data={"task_ids": task_ids})
        if not response.success:
            return []
        data = response.data
        if isinstance(data, dict):
            return data.get("updated", [])
        return []

    # =========================================================================
    # Real-time Events (SSE)
    # =========================================================================

    async def subscribe_events(
        self,
        event_types: list[str] | None = None,
    ) -> AsyncGenerator[SSEEvent, None]:
        """
        Subscribe to server-sent events.

        Args:
            event_types: Filter by event types (optional)

        Yields:
            SSEEvent objects as they arrive
        """
        await self._ensure_session()

        url = f"{self.base_url}/api/events"
        params = {}
        if self.token:
            params["token"] = self.token

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"SSE connection failed: HTTP {resp.status}")
                    return

                async for line in resp.content:
                    line_str = line.decode("utf-8").strip()
                    if not line_str or line_str.startswith(":"):
                        continue

                    event = SSEEvent.from_line(line_str)
                    if event:
                        if event_types is None or event.type in event_types:
                            yield event

        except Exception as e:
            logger.error(f"SSE subscription error: {e}")

    # =========================================================================
    # Sync Wrappers (for CLI)
    # =========================================================================

    def _run_sync(self, coro: Any) -> Any:
        """Run a coroutine synchronously, handling nested event loops."""
        try:
            # Check if there's already a running event loop
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop, use asyncio.run()
            return asyncio.run(self._sync_wrapper(coro))

        # There's a running loop - run in a thread to avoid conflicts
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, self._sync_wrapper(coro))
            return future.result(timeout=self.timeout)

    def sync_list_agents(self, status: AgentStatus | None = None) -> list[Agent]:
        """Sync wrapper for list_agents."""
        return self._run_sync(self.list_agents(status))

    def sync_get_agent(self, agent_id: str) -> Agent | None:
        """Sync wrapper for get_agent."""
        return self._run_sync(self.get_agent(agent_id))

    def sync_register_agent(self, role: str, task: str, project: str | None = None) -> Agent | None:
        """Sync wrapper for register_agent."""
        return self._run_sync(self.register_agent(role, task, project))

    def sync_send_message(self, agent_id: str, content: str) -> bool:
        """Sync wrapper for send_message."""
        return self._run_sync(self.send_message(agent_id, content))

    def sync_broadcast_message(self, content: str) -> int:
        """Sync wrapper for broadcast_message."""
        return self._run_sync(self.broadcast_message(content))

    def sync_pause_agent(self, agent_id: str) -> bool:
        """Sync wrapper for pause_agent."""
        return self._run_sync(self.pause_agent(agent_id))

    def sync_resume_agent(self, agent_id: str) -> bool:
        """Sync wrapper for resume_agent."""
        return self._run_sync(self.resume_agent(agent_id))

    def sync_kill_agent(self, agent_id: str) -> bool:
        """Sync wrapper for kill_agent."""
        return self._run_sync(self.kill_agent(agent_id))

    def sync_fleet_pause(self) -> dict[str, Any]:
        """Sync wrapper for fleet_pause."""
        return self._run_sync(self.fleet_pause())

    def sync_fleet_resume(self) -> dict[str, Any]:
        """Sync wrapper for fleet_resume."""
        return self._run_sync(self.fleet_resume())

    def sync_get_agent_logs(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Sync wrapper for get_agent_logs."""
        return self._run_sync(self.get_agent_logs(agent_id, limit))

    def sync_complete_agent(self, agent_id: str, summary: str | None = None) -> bool:
        """Sync wrapper for complete_agent."""
        return self._run_sync(self.complete_agent(agent_id, summary))

    def sync_list_approvals(
        self,
        status: ApprovalStatus | None = None,
        domain: str | None = None,
    ) -> list[Approval]:
        """Sync wrapper for list_approvals."""
        return self._run_sync(self.list_approvals(status, domain))

    def sync_approve(self, request_id: str, notes: str = "") -> bool:
        """Sync wrapper for approve."""
        return self._run_sync(self.approve(request_id, notes))

    def sync_reject(self, request_id: str, reason: str = "") -> bool:
        """Sync wrapper for reject."""
        return self._run_sync(self.reject(request_id, reason))

    def sync_list_portfolio(self) -> list[dict[str, Any]]:
        """Sync wrapper for list_portfolio."""
        return self._run_sync(self.list_portfolio())

    def sync_get_domain(self, domain: str) -> dict[str, Any] | None:
        """Sync wrapper for get_domain."""
        return self._run_sync(self.get_domain(domain))

    def sync_get_project(self, domain: str, project: str) -> dict[str, Any] | None:
        """Sync wrapper for get_project."""
        return self._run_sync(self.get_project(domain, project))

    def sync_list_patterns(self, category: str | None = None) -> list[Pattern]:
        """Sync wrapper for list_patterns."""
        return self._run_sync(self.list_patterns(category))

    def sync_list_tasks(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Sync wrapper for list_tasks."""
        return self._run_sync(self.list_tasks(status, priority, limit))

    def sync_claim_task(self, task_id: str, agent_id: str) -> dict[str, Any] | None:
        """Sync wrapper for claim_task."""
        return self._run_sync(self.claim_task(task_id, agent_id))

    def sync_dispatch_task(self, task_id: str, agent_id: str) -> dict[str, Any] | None:
        """Sync wrapper for dispatch_task."""
        return self._run_sync(self.dispatch_task(task_id, agent_id))

    def sync_create_task(
        self,
        subject: str,
        description: str = "",
        priority: str = "medium",
        required_role: str | None = None,
        domain: str | None = None,
        project: str | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Sync wrapper for create_task."""
        return self._run_sync(
            self.create_task(subject, description, priority, required_role, domain, project, depends_on)
        )

    def sync_get_task(self, task_id: str) -> dict[str, Any] | None:
        """Sync wrapper for get_task."""
        return self._run_sync(self.get_task(task_id))

    def sync_get_recommended_tasks(
        self,
        limit: int = 10,
        agent_id: str | None = None,
        include_blocked: bool = False,
        min_priority: str | None = None,
        task_type: str | None = None,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        """Sync wrapper for get_recommended_tasks."""
        return self._run_sync(
            self.get_recommended_tasks(
                limit, agent_id, include_blocked, min_priority, task_type, domain
            )
        )

    def sync_update_task(
        self,
        task_id: str,
        subject: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        required_role: str | None = None,
        depends_on: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Sync wrapper for update_task."""
        return self._run_sync(
            self.update_task(task_id, subject, description, priority, status, required_role, depends_on)
        )

    def sync_delete_task(self, task_id: str) -> bool:
        """Sync wrapper for delete_task."""
        return self._run_sync(self.delete_task(task_id))

    def sync_get_task_stats(self) -> dict[str, Any]:
        """Sync wrapper for get_task_stats."""
        return self._run_sync(self.get_task_stats())

    def sync_list_ready_tasks(
        self,
        priority: str | None = None,
        limit: int = 50,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Sync wrapper for list_ready_tasks."""
        return self._run_sync(self.list_ready_tasks(priority, limit, agent))

    def sync_reorder_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        """Sync wrapper for reorder_tasks."""
        return self._run_sync(self.reorder_tasks(task_ids))

    def sync_list_active_leases(self) -> list[dict[str, Any]]:
        """Sync wrapper for list_active_leases."""
        return self._run_sync(self.list_active_leases())

    # =========================================================================
    # Node Operations
    # =========================================================================

    async def list_nodes(self) -> list[dict[str, Any]]:
        """List all known nodes from the Command Center.

        Calls ``GET /api/nodes`` and returns the raw node dicts.  Each dict
        contains at minimum: ``node_id``, ``status``, ``agent_count``,
        ``cpu_load``, ``ram_usage``, ``last_heartbeat``.

        Returns:
            List of node dicts.  Empty list when the API is unreachable or
            returns a non-200 status.
        """
        response = await self._request("GET", "/api/nodes")
        if not response.success:
            logger.debug(f"Failed to list nodes: {response.error}")
            return []

        data = response.data
        if isinstance(data, dict):
            nodes_list: list[dict[str, Any]] = data.get("nodes", [])
        elif isinstance(data, list):
            nodes_list = data
        else:
            nodes_list = []

        return [n for n in nodes_list if isinstance(n, dict)]

    def sync_list_nodes(self) -> list[dict[str, Any]]:
        """Sync wrapper for list_nodes."""
        return self._run_sync(self.list_nodes())

    # =========================================================================
    # Decision Operations
    # =========================================================================

    async def list_decisions(
        self,
        domain: str | None = None,
        project: str | None = None,
        context: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List decisions from the meta-learning system.

        Args:
            domain: Filter by domain (optional)
            project: Filter by project — requires domain (optional)
            context: Filter by context signature (optional)
            limit: Maximum number of decisions to return

        Returns:
            Dict with 'decisions' list and 'count' integer keys, or empty
            dict on failure.
        """
        params: dict[str, Any] = {"limit": limit}
        if domain:
            params["domain"] = domain
        if project:
            params["project"] = project
        if context:
            params["context"] = context

        response = await self._request("GET", "/api/decisions", params=params)
        if not response.success:
            logger.debug(f"Failed to list decisions: {response.error}")
            return {"decisions": [], "count": 0}

        data = response.data
        if isinstance(data, dict):
            return data
        return {"decisions": [], "count": 0}

    async def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Get a specific decision by ID.

        Args:
            decision_id: Decision identifier

        Returns:
            Decision dict or None if not found / on error.
        """
        response = await self._request("GET", f"/api/decisions/{decision_id}")
        if not response.success:
            error_msg = response.error or ""
            if "404" in error_msg or "not found" in error_msg.lower():
                return None
            logger.debug(f"Failed to get decision {decision_id}: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    def sync_list_decisions(
        self,
        domain: str | None = None,
        project: str | None = None,
        context: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Sync wrapper for list_decisions."""
        return self._run_sync(self.list_decisions(domain, project, context, limit))

    def sync_get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Sync wrapper for get_decision."""
        return self._run_sync(self.get_decision(decision_id))

    # =========================================================================
    # Ralph Loop Operations
    # =========================================================================

    async def ralph_loop_status(self) -> dict[str, Any]:
        """Get Ralph Loop status from the CC backend.

        Returns:
            Dict with loop status fields or empty dict on failure.
        """
        response = await self._request("GET", "/api/ralph-loop/status")
        if not response.success:
            logger.debug(f"Failed to get Ralph Loop status: {response.error}")
            return {}
        data = response.data
        if isinstance(data, dict):
            return data
        return {}

    async def ralph_loop_decisions(self, limit: int = 50) -> dict[str, Any]:
        """Get Ralph Loop decision history.

        Args:
            limit: Maximum number of decisions to return.

        Returns:
            Dict with 'decisions' list and 'count' integer keys, or empty dict on failure.
        """
        response = await self._request(
            "GET", "/api/ralph-loop/decisions", params={"limit": limit}
        )
        if not response.success:
            logger.debug(f"Failed to get Ralph Loop decisions: {response.error}")
            return {"decisions": [], "count": 0}
        data = response.data
        if isinstance(data, dict):
            return data
        return {"decisions": [], "count": 0}

    async def ralph_loop_start(
        self,
        domain: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Start the Ralph Loop.

        Args:
            domain: Optional domain context for the loop.
            project: Optional project context for the loop.

        Returns:
            Dict with 'success' and 'status' fields, or empty dict on failure.
        """
        body: dict[str, Any] = {}
        if domain:
            body["domain"] = domain
        if project:
            body["project"] = project

        response = await self._request("POST", "/api/ralph-loop/start", data=body or None)
        if not response.success:
            logger.debug(f"Failed to start Ralph Loop: {response.error}")
            return {"success": False, "error": response.error}
        data = response.data
        if isinstance(data, dict):
            return data
        return {"success": True}

    async def ralph_loop_pause(self) -> dict[str, Any]:
        """Pause the Ralph Loop.

        Returns:
            Dict with 'success' and 'status' fields, or empty dict on failure.
        """
        response = await self._request("POST", "/api/ralph-loop/pause")
        if not response.success:
            logger.debug(f"Failed to pause Ralph Loop: {response.error}")
            return {"success": False, "error": response.error}
        data = response.data
        if isinstance(data, dict):
            return data
        return {"success": True}

    async def ralph_loop_stop(self) -> dict[str, Any]:
        """Stop the Ralph Loop.

        Returns:
            Dict with 'success' and 'status' fields, or empty dict on failure.
        """
        response = await self._request("POST", "/api/ralph-loop/stop")
        if not response.success:
            logger.debug(f"Failed to stop Ralph Loop: {response.error}")
            return {"success": False, "error": response.error}
        data = response.data
        if isinstance(data, dict):
            return data
        return {"success": True}

    # ---- Sync wrappers for Ralph Loop ----------------------------------------

    def sync_ralph_loop_status(self) -> dict[str, Any]:
        """Sync wrapper for ralph_loop_status."""
        return self._run_sync(self.ralph_loop_status())

    def sync_ralph_loop_decisions(self, limit: int = 50) -> dict[str, Any]:
        """Sync wrapper for ralph_loop_decisions."""
        return self._run_sync(self.ralph_loop_decisions(limit))

    def sync_ralph_loop_start(
        self,
        domain: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Sync wrapper for ralph_loop_start."""
        return self._run_sync(self.ralph_loop_start(domain, project))

    def sync_ralph_loop_pause(self) -> dict[str, Any]:
        """Sync wrapper for ralph_loop_pause."""
        return self._run_sync(self.ralph_loop_pause())

    def sync_ralph_loop_stop(self) -> dict[str, Any]:
        """Sync wrapper for ralph_loop_stop."""
        return self._run_sync(self.ralph_loop_stop())

    # =========================================================================
    # Handoff Operations
    # =========================================================================

    async def accept_handoff(self, handoff_id: str, agent_id: str = "") -> dict[str, Any]:
        """Accept a handoff.

        Args:
            handoff_id: Handoff identifier.
            agent_id: Accepting agent ID (optional).

        Returns:
            Response dict or empty dict on failure.
        """
        payload: dict[str, Any] = {}
        if agent_id:
            payload["agent_id"] = agent_id
        response = await self._request(
            "POST", f"/api/handoffs/{handoff_id}/accept", data=payload or None
        )
        if not response.success:
            logger.debug(f"Failed to accept handoff {handoff_id}: {response.error}")
            return {}
        return response.data if isinstance(response.data, dict) else {}

    async def reject_handoff(self, handoff_id: str, reason: str = "") -> dict[str, Any]:
        """Reject a handoff.

        Args:
            handoff_id: Handoff identifier.
            reason: Reason for rejection.

        Returns:
            Response dict or empty dict on failure.
        """
        response = await self._request(
            "POST", f"/api/handoffs/{handoff_id}/reject", data={"reason": reason}
        )
        if not response.success:
            logger.debug(f"Failed to reject handoff {handoff_id}: {response.error}")
            return {}
        return response.data if isinstance(response.data, dict) else {}

    async def complete_handoff(self, handoff_id: str) -> dict[str, Any]:
        """Mark a handoff as complete.

        Args:
            handoff_id: Handoff identifier.

        Returns:
            Response dict or empty dict on failure.
        """
        response = await self._request("POST", f"/api/handoffs/{handoff_id}/complete")
        if not response.success:
            logger.debug(f"Failed to complete handoff {handoff_id}: {response.error}")
            return {}
        return response.data if isinstance(response.data, dict) else {}

    # ---- Sync wrappers for Handoff Operations --------------------------------

    def sync_accept_handoff(self, handoff_id: str, agent_id: str = "") -> dict[str, Any]:
        """Sync wrapper for accept_handoff."""
        return self._run_sync(self.accept_handoff(handoff_id, agent_id))

    def sync_reject_handoff(self, handoff_id: str, reason: str = "") -> dict[str, Any]:
        """Sync wrapper for reject_handoff."""
        return self._run_sync(self.reject_handoff(handoff_id, reason))

    def sync_complete_handoff(self, handoff_id: str) -> dict[str, Any]:
        """Sync wrapper for complete_handoff."""
        return self._run_sync(self.complete_handoff(handoff_id))

    # =========================================================================
    # Feature Operations
    # =========================================================================

    async def list_features(
        self,
        status: str | None = None,
        domain: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List feature progress records with optional filters.

        Args:
            status:  Filter by status (planned, in_progress, testing, done).
            domain:  Filter to a specific domain.
            project: Filter to a specific project.

        Returns:
            Dict with 'features' list and 'count' integer keys, or empty
            dict on failure.
        """
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if domain:
            params["domain"] = domain
        if project:
            params["project"] = project

        response = await self._request("GET", "/api/features", params=params)
        if not response.success:
            logger.debug(f"Failed to list features: {response.error}")
            return {"features": [], "count": 0}

        data = response.data
        if isinstance(data, dict):
            return data
        return {"features": [], "count": 0}

    async def get_feature(self, feature_id: str) -> dict[str, Any] | None:
        """Get feature progress details by ID.

        Args:
            feature_id: The feature identifier.

        Returns:
            Feature dict or None if not found / on error.
        """
        response = await self._request("GET", f"/api/features/{feature_id}")
        if not response.success:
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def assign_feature(
        self, feature_id: str, agent_id: str
    ) -> dict[str, Any] | None:
        """Assign an agent to a feature.

        Args:
            feature_id: The feature identifier.
            agent_id:   Agent identifier to assign.

        Returns:
            Updated feature dict or None on error.
        """
        response = await self._request(
            "POST",
            f"/api/features/{feature_id}/assign",
            data={"agent_id": agent_id},
        )
        if not response.success:
            logger.debug(f"Failed to assign feature {feature_id}: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def link_feature_task(
        self, feature_id: str, task_id: str
    ) -> dict[str, Any] | None:
        """Link a task to a feature.

        Args:
            feature_id: The feature identifier.
            task_id:    Task identifier to link.

        Returns:
            Updated feature dict or None on error.
        """
        response = await self._request(
            "POST",
            f"/api/features/{feature_id}/link-task",
            data={"task_id": task_id},
        )
        if not response.success:
            logger.debug(f"Failed to link task to feature {feature_id}: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def get_feature_stats(self) -> dict[str, Any]:
        """Return aggregate feature statistics.

        Returns:
            Dict with 'total', 'by_status', and 'by_domain' keys, or empty
            dict on failure.
        """
        response = await self._request("GET", "/api/features/stats")
        if not response.success:
            logger.debug(f"Failed to get feature stats: {response.error}")
            return {}
        data = response.data
        if isinstance(data, dict):
            return data
        return {}

    # ---- Sync wrappers for Feature Operations --------------------------------

    def sync_list_features(
        self,
        status: str | None = None,
        domain: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Sync wrapper for list_features."""
        return self._run_sync(self.list_features(status, domain, project))

    def sync_get_feature(self, feature_id: str) -> dict[str, Any] | None:
        """Sync wrapper for get_feature."""
        return self._run_sync(self.get_feature(feature_id))

    def sync_assign_feature(
        self, feature_id: str, agent_id: str
    ) -> dict[str, Any] | None:
        """Sync wrapper for assign_feature."""
        return self._run_sync(self.assign_feature(feature_id, agent_id))

    def sync_link_feature_task(
        self, feature_id: str, task_id: str
    ) -> dict[str, Any] | None:
        """Sync wrapper for link_feature_task."""
        return self._run_sync(self.link_feature_task(feature_id, task_id))

    def sync_get_feature_stats(self) -> dict[str, Any]:
        """Sync wrapper for get_feature_stats."""
        return self._run_sync(self.get_feature_stats())

    # =========================================================================
    # Intake Operations
    # =========================================================================

    async def list_intake(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List intake queue items with optional filters.

        Args:
            status: Filter by item status (e.g. pending, processed).
            limit:  Maximum number of items to return.

        Returns:
            List of intake item dicts, empty list on failure.
        """
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status

        response = await self._request("GET", "/api/intake", params=params)
        if not response.success:
            logger.debug(f"Failed to list intake items: {response.error}")
            return []

        data = response.data
        if isinstance(data, dict):
            return data.get("items", data.get("intake", []))
        if isinstance(data, list):
            return data
        return []

    async def get_intake_item(self, item_id: str) -> dict[str, Any] | None:
        """Get a single intake item by ID.

        Args:
            item_id: Intake item identifier.

        Returns:
            Item dict or None if not found / on error.
        """
        response = await self._request("GET", f"/api/intake/{item_id}")
        if not response.success:
            logger.debug(f"Failed to get intake item {item_id}: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def create_intake_item(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        source: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Create a new intake queue item.

        Args:
            title:       Short human-readable title.
            description: Detailed description of the request.
            priority:    Priority level (low / medium / high / critical).
            source:      Originating system or requester (optional).
            tags:        List of tag strings for categorisation (optional).

        Returns:
            Created item dict or None on failure.
        """
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "priority": priority,
        }
        if source:
            payload["source"] = source
        if tags:
            payload["tags"] = tags

        response = await self._request("POST", "/api/intake", data=payload)
        if not response.success:
            logger.debug(f"Failed to create intake item: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def get_intake_stats(self) -> dict[str, Any]:
        """Get intake queue aggregate statistics.

        Returns:
            Dict with totals and breakdowns, or empty dict on failure.
        """
        response = await self._request("GET", "/api/intake/stats")
        if not response.success:
            logger.debug(f"Failed to get intake stats: {response.error}")
            return {}
        data = response.data
        if isinstance(data, dict):
            return data
        return {}

    # ---- Sync wrappers for Intake Operations ---------------------------------

    def sync_list_intake(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Sync wrapper for list_intake."""
        return self._run_sync(self.list_intake(status=status, limit=limit))

    def sync_get_intake_item(self, item_id: str) -> dict[str, Any] | None:
        """Sync wrapper for get_intake_item."""
        return self._run_sync(self.get_intake_item(item_id))

    def sync_create_intake_item(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        source: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Sync wrapper for create_intake_item."""
        return self._run_sync(
            self.create_intake_item(title, description, priority, source, tags)
        )

    def sync_get_intake_stats(self) -> dict[str, Any]:
        """Sync wrapper for get_intake_stats."""
        return self._run_sync(self.get_intake_stats())

    # =========================================================================
    # Balancer Operations
    # =========================================================================

    async def get_balancer_agents(self) -> list[dict[str, Any]]:
        """Get load information for all agents registered with the balancer.

        Returns:
            List of agent load dicts, empty list on failure.
        """
        response = await self._request("GET", "/api/balancer/agents")
        if not response.success:
            logger.debug(f"Failed to get balancer agents: {response.error}")
            return []

        data = response.data
        if isinstance(data, dict):
            return data.get("agents", [])
        if isinstance(data, list):
            return data
        return []

    async def register_balancer_agent(
        self,
        agent_id: str,
        model: str | None = None,
        capabilities: list[str] | None = None,
        capacity: int | None = None,
        node: str | None = None,
    ) -> dict[str, Any] | None:
        """Register an agent with the load balancer.

        Args:
            agent_id:     Unique agent identifier.
            model:        Model name or alias (optional).
            capabilities: List of capability strings (optional).
            capacity:     Maximum concurrent task capacity (optional).
            node:         Node hostname where the agent is running (optional).

        Returns:
            Registered agent dict or None on failure.
        """
        payload: dict[str, Any] = {"agent_id": agent_id}
        if model:
            payload["model"] = model
        if capabilities:
            payload["capabilities"] = capabilities
        if capacity is not None:
            payload["capacity"] = capacity
        if node:
            payload["node"] = node

        response = await self._request("POST", "/api/balancer/agents", data=payload)
        if not response.success:
            logger.debug(f"Failed to register agent with balancer: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def get_balancer_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get load details for a specific balancer agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Agent load dict or None if not found / on error.
        """
        response = await self._request("GET", f"/api/balancer/agents/{agent_id}")
        if not response.success:
            logger.debug(f"Failed to get balancer agent {agent_id}: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def update_balancer_agent_load(
        self,
        agent_id: str,
        load: float,
        active_tasks: int | None = None,
    ) -> dict[str, Any] | None:
        """Update the load value for a specific balancer agent.

        Args:
            agent_id:     Agent identifier.
            load:         Current load fraction (0.0 – 1.0).
            active_tasks: Number of currently active tasks (optional).

        Returns:
            Updated agent dict or None on failure.
        """
        payload: dict[str, Any] = {"load": load}
        if active_tasks is not None:
            payload["active_tasks"] = active_tasks

        response = await self._request(
            "PUT", f"/api/balancer/agents/{agent_id}/load", data=payload
        )
        if not response.success:
            logger.debug(f"Failed to update balancer agent load for {agent_id}: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def balancer_recommend(
        self,
        task_type: str | None = None,
        domain: str | None = None,
        count: int = 1,
    ) -> dict[str, Any]:
        """Get recommended agent(s) for a given task type.

        Args:
            task_type: Task type to match (e.g. backend, testing, content).
            domain:    Optional domain context for the recommendation.
            count:     Number of recommendations to return.

        Returns:
            Dict with 'recommendations' list (or single agent dict),
            or empty dict on failure.
        """
        payload: dict[str, Any] = {"count": count}
        if task_type:
            payload["task_type"] = task_type
        if domain:
            payload["domain"] = domain

        response = await self._request("POST", "/api/balancer/recommend", data=payload)
        if not response.success:
            logger.debug(f"Failed to get balancer recommendation: {response.error}")
            return {}
        data = response.data
        if isinstance(data, dict):
            return data
        return {}

    # ---- Sync wrappers for Balancer Operations -------------------------------

    def sync_get_balancer_agents(self) -> list[dict[str, Any]]:
        """Sync wrapper for get_balancer_agents."""
        return self._run_sync(self.get_balancer_agents())

    def sync_register_balancer_agent(
        self,
        agent_id: str,
        model: str | None = None,
        capabilities: list[str] | None = None,
        capacity: int | None = None,
        node: str | None = None,
    ) -> dict[str, Any] | None:
        """Sync wrapper for register_balancer_agent."""
        return self._run_sync(
            self.register_balancer_agent(agent_id, model, capabilities, capacity, node)
        )

    def sync_get_balancer_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Sync wrapper for get_balancer_agent."""
        return self._run_sync(self.get_balancer_agent(agent_id))

    def sync_update_balancer_agent_load(
        self,
        agent_id: str,
        load: float,
        active_tasks: int | None = None,
    ) -> dict[str, Any] | None:
        """Sync wrapper for update_balancer_agent_load."""
        return self._run_sync(self.update_balancer_agent_load(agent_id, load, active_tasks))

    def sync_balancer_recommend(
        self,
        task_type: str | None = None,
        domain: str | None = None,
        count: int = 1,
    ) -> dict[str, Any]:
        """Sync wrapper for balancer_recommend."""
        return self._run_sync(self.balancer_recommend(task_type, domain, count))

    # =========================================================================
    # Memory Operations
    # =========================================================================

    async def list_memories(self, limit: int = 50) -> list[dict[str, Any]]:
        """List agent memories/patterns from the Command Center.

        Args:
            limit: Maximum number of memory records to return.

        Returns:
            List of memory dicts, empty list on failure.
        """
        response = await self._request(
            "GET",
            "/api/memories",
            params={"limit": limit},
        )
        if not response.success:
            logger.debug(f"Failed to list memories: {response.error}")
            return []
        data = response.data
        if isinstance(data, dict):
            return data.get("memories", data.get("items", []))
        if isinstance(data, list):
            return data
        return []

    async def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Get a specific memory record by ID.

        Args:
            memory_id: Memory record identifier.

        Returns:
            Memory dict or None if not found / on error.
        """
        response = await self._request("GET", f"/api/memories/{memory_id}")
        if not response.success:
            logger.debug(f"Failed to get memory {memory_id}: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    def sync_list_memories(self, limit: int = 50) -> list[dict[str, Any]]:
        """Sync wrapper for list_memories."""
        return self._run_sync(self.list_memories(limit=limit))

    def sync_get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Sync wrapper for get_memory."""
        return self._run_sync(self.get_memory(memory_id))

    # =========================================================================
    # Decomposition Operations
    # =========================================================================

    async def list_decomposition_graphs(self, limit: int = 50) -> list[dict[str, Any]]:
        """List decomposition graphs from the Command Center.

        Args:
            limit: Maximum number of graphs to return.

        Returns:
            List of graph dicts, empty list on failure.
        """
        response = await self._request(
            "GET",
            "/api/decomposition/graphs",
            params={"limit": limit},
        )
        if not response.success:
            logger.debug(f"Failed to list decomposition graphs: {response.error}")
            return []
        data = response.data
        if isinstance(data, dict):
            return data.get("graphs", data.get("items", []))
        if isinstance(data, list):
            return data
        return []

    async def get_decomposition_graph(self, graph_id: str) -> dict[str, Any] | None:
        """Get a specific decomposition graph by ID.

        Args:
            graph_id: Decomposition graph identifier.

        Returns:
            Graph dict or None if not found / on error.
        """
        response = await self._request("GET", f"/api/decomposition/graphs/{graph_id}")
        if not response.success:
            logger.debug(f"Failed to get decomposition graph {graph_id}: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def create_decomposition_graph(
        self,
        task_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a new decomposition graph.

        Args:
            task_id: Optional task ID to associate with the graph.
            description: Optional description of the work to decompose.

        Returns:
            Created graph dict or None on failure.
        """
        payload: dict[str, Any] = {}
        if task_id:
            payload["task_id"] = task_id
        if description:
            payload["description"] = description

        response = await self._request(
            "POST",
            "/api/decomposition/graphs",
            data=payload or None,
        )
        if not response.success:
            logger.debug(f"Failed to create decomposition graph: {response.error}")
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def check_decomposition_ready(self, graph_id: str) -> dict[str, Any] | None:
        """Check if a decomposition graph is ready for execution.

        Args:
            graph_id: Decomposition graph identifier.

        Returns:
            Dict with 'ready' bool and optional 'reason' string,
            or None if the graph was not found.
        """
        response = await self._request(
            "GET",
            f"/api/decomposition/graphs/{graph_id}/ready",
        )
        if not response.success:
            logger.debug(
                f"Failed to check ready state for graph {graph_id}: {response.error}"
            )
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def create_decomposition_subtasks(
        self, graph_id: str
    ) -> dict[str, Any] | None:
        """Create subtasks from a decomposition graph.

        Args:
            graph_id: Decomposition graph identifier.

        Returns:
            Response dict (typically includes a 'subtasks' list) or None on failure.
        """
        response = await self._request(
            "POST",
            f"/api/decomposition/graphs/{graph_id}/subtasks",
        )
        if not response.success:
            logger.debug(
                f"Failed to create subtasks for graph {graph_id}: {response.error}"
            )
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    async def complete_decomposition_subtask(
        self, graph_id: str, subtask_id: str
    ) -> dict[str, Any] | None:
        """Mark a subtask as complete within a decomposition graph.

        Args:
            graph_id: Decomposition graph identifier.
            subtask_id: Subtask identifier to mark complete.

        Returns:
            Updated subtask dict or None if the graph/subtask was not found.
        """
        response = await self._request(
            "PUT",
            f"/api/decomposition/graphs/{graph_id}/subtasks/{subtask_id}/complete",
        )
        if not response.success:
            logger.debug(
                f"Failed to complete subtask {subtask_id} in graph {graph_id}: "
                f"{response.error}"
            )
            return None
        data = response.data
        if isinstance(data, dict):
            return data
        return None

    def sync_list_decomposition_graphs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Sync wrapper for list_decomposition_graphs."""
        return self._run_sync(self.list_decomposition_graphs(limit=limit))

    def sync_get_decomposition_graph(self, graph_id: str) -> dict[str, Any] | None:
        """Sync wrapper for get_decomposition_graph."""
        return self._run_sync(self.get_decomposition_graph(graph_id))

    def sync_create_decomposition_graph(
        self,
        task_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Sync wrapper for create_decomposition_graph."""
        return self._run_sync(self.create_decomposition_graph(task_id, description))

    def sync_check_decomposition_ready(self, graph_id: str) -> dict[str, Any] | None:
        """Sync wrapper for check_decomposition_ready."""
        return self._run_sync(self.check_decomposition_ready(graph_id))

    def sync_create_decomposition_subtasks(self, graph_id: str) -> dict[str, Any] | None:
        """Sync wrapper for create_decomposition_subtasks."""
        return self._run_sync(self.create_decomposition_subtasks(graph_id))

    def sync_complete_decomposition_subtask(
        self, graph_id: str, subtask_id: str
    ) -> dict[str, Any] | None:
        """Sync wrapper for complete_decomposition_subtask."""
        return self._run_sync(self.complete_decomposition_subtask(graph_id, subtask_id))

    async def _sync_wrapper(self, coro: Any) -> Any:
        """Wrapper to handle session lifecycle for sync calls."""
        await self._ensure_session()
        try:
            return await coro
        finally:
            await self.close()
