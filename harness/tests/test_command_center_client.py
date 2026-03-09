"""Tests for command_center_client.py - Command Center API Client.

Tests cover:
- Data models (Agent, Approval, Pattern, Task, etc.)
- _parse_datetime helper
- CommandCenterClient initialization
- HTTP request handling
- Agent operations
- Approval operations
- Portfolio operations
- Pattern operations
- Task operations
- Sync wrappers
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.command_center_client import (
    Agent,
    AgentStatus,
    APIResponse,
    Approval,
    ApprovalStatus,
    CommandCenterClient,
    DecisionTier,
    Pattern,
    PortfolioProject,
    SSEEvent,
    Task,
    _parse_datetime,
)
from forge_harness.task_queue import TaskPriority, TaskStatus

# =============================================================================
# _parse_datetime Tests
# =============================================================================


class TestParseDatetime:
    """Tests for _parse_datetime helper function."""

    def test_parse_valid_iso(self) -> None:
        """Should parse valid ISO datetime."""
        dt = _parse_datetime("2024-01-15T10:30:00")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_parse_with_z(self) -> None:
        """Should parse ISO datetime with Z suffix."""
        dt = _parse_datetime("2024-01-15T10:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_parse_none(self) -> None:
        """Should return None for None input."""
        dt = _parse_datetime(None)
        assert dt is None

    def test_parse_empty_string(self) -> None:
        """Should return None for empty string."""
        dt = _parse_datetime("")
        assert dt is None

    def test_parse_invalid(self) -> None:
        """Should return None for invalid datetime."""
        dt = _parse_datetime("not-a-datetime")
        assert dt is None


# =============================================================================
# Agent Data Model Tests
# =============================================================================


class TestAgent:
    """Tests for Agent dataclass."""

    def test_from_dict_minimal(self) -> None:
        """Should create Agent from minimal dict."""
        data = {"id": "agent-1", "role": "dev"}
        agent = Agent.from_dict(data)

        assert agent.id == "agent-1"
        assert agent.role == "dev"
        assert agent.status == AgentStatus.ACTIVE
        assert agent.progress == 0
        assert agent.is_stale is False

    def test_from_dict_full(self) -> None:
        """Should create Agent from full dict."""
        now = datetime.now(UTC).isoformat()
        data = {
            "id": "agent-1",
            "role": "dev",
            "name": "Test Agent",
            "project": "test-project",
            "task": "Implement feature",
            "status": "idle",
            "progress": 50,
            "current_task": "Coding",
            "files_modified": ["file1.py", "file2.py"],
            "token_usage": {"prompt": 100, "completion": 50},
            "messages_count": 10,
            "registered_at": now,
            "last_activity": now,
            "is_stale": True,
        }
        agent = Agent.from_dict(data)

        assert agent.id == "agent-1"
        assert agent.name == "Test Agent"
        assert agent.project == "test-project"
        assert agent.status == AgentStatus.IDLE
        assert agent.progress == 50
        assert agent.is_stale is True
        assert agent.registered_at is not None


# =============================================================================
# Approval Data Model Tests
# =============================================================================


class TestApproval:
    """Tests for Approval dataclass."""

    def test_from_dict_minimal(self) -> None:
        """Should create Approval from minimal dict."""
        data = {"id": "approval-1"}
        approval = Approval.from_dict(data)

        assert approval.id == "approval-1"
        assert approval.title == "Untitled"
        assert approval.status == ApprovalStatus.PENDING
        assert approval.tier is None

    def test_from_dict_with_tier(self) -> None:
        """Should create Approval with tier."""
        data = {
            "id": "approval-1",
            "tier": "desktop",
            "status": "approved",
        }
        approval = Approval.from_dict(data)

        assert approval.tier == DecisionTier.DESKTOP
        assert approval.status == ApprovalStatus.APPROVED


# =============================================================================
# Pattern Data Model Tests
# =============================================================================


class TestPattern:
    """Tests for Pattern dataclass."""

    def test_from_dict_minimal(self) -> None:
        """Should create Pattern from minimal dict."""
        data = {"id": "pattern-1"}
        pattern = Pattern.from_dict(data)

        assert pattern.id == "pattern-1"
        assert pattern.name == ""
        assert pattern.category == "general"
        assert pattern.success_rate == 0.0

    def test_from_dict_full(self) -> None:
        """Should create Pattern from full dict."""
        data = {
            "id": "pattern-1",
            "name": "Test Pattern",
            "description": "A test pattern",
            "category": "auth",
            "success_rate": 0.85,
            "usage_count": 42,
        }
        pattern = Pattern.from_dict(data)

        assert pattern.name == "Test Pattern"
        assert pattern.category == "auth"
        assert pattern.success_rate == 0.85
        assert pattern.usage_count == 42


# =============================================================================
# Task Data Model Tests
# =============================================================================


class TestTask:
    """Tests for Task dataclass."""

    def test_from_dict_minimal(self) -> None:
        """Should create Task from minimal dict."""
        data = {"id": "task-1", "subject": "Test task"}
        task = Task.from_dict(data)

        assert task.id == "task-1"
        assert task.subject == "Test task"
        assert task.priority == TaskPriority.MEDIUM
        assert task.status == TaskStatus.PENDING

    def test_from_dict_full(self) -> None:
        """Should create Task from full dict."""
        now = datetime.now(UTC).isoformat()
        data = {
            "id": "task-1",
            "subject": "Test task",
            "description": "Description",
            "priority": "high",
            "status": "in_progress",
            "required_role": "dev",
            "claimed_by": "agent-1",
            "domain": "test-domain",
            "project": "test-project",
            "task_type": "feature",
            "complexity": "medium",
            "blocked_by": ["task-0"],
            "estimated_hours": 4.5,
            "created_at": now,
            "updated_at": now,
        }
        task = Task.from_dict(data)

        assert task.priority == TaskPriority.HIGH
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.domain == "test-domain"
        assert task.blocked_by == ["task-0"]
        assert task.estimated_hours == 4.5


# =============================================================================
# PortfolioProject Data Model Tests
# =============================================================================


class TestPortfolioProject:
    """Tests for PortfolioProject dataclass."""

    def test_from_dict(self) -> None:
        """Should create PortfolioProject from dict."""
        data = {
            "domain": "test-domain",
            "project": "test-project",
            "status": "active",
            "description": "Test project",
            "tech_stack": ["python", "fastapi"],
        }
        project = PortfolioProject.from_dict(data)

        assert project.domain == "test-domain"
        assert project.project == "test-project"
        assert project.status == "active"
        assert project.tech_stack == ["python", "fastapi"]


# =============================================================================
# SSEEvent Data Model Tests
# =============================================================================


class TestSSEEvent:
    """Tests for SSEEvent dataclass."""

    def test_from_line_valid(self) -> None:
        """Should parse valid SSE data line."""
        line = 'data: {"type": "agent_update", "data": {"id": "1"}}'
        event = SSEEvent.from_line(line)

        assert event is not None
        assert event.type == "agent_update"
        assert event.data == {"id": "1"}

    def test_from_line_invalid_prefix(self) -> None:
        """Should return None for non-data lines."""
        line = "event: message"
        event = SSEEvent.from_line(line)

        assert event is None

    def test_from_line_invalid_json(self) -> None:
        """Should return None for invalid JSON."""
        line = "data: not valid json"
        event = SSEEvent.from_line(line)

        assert event is None


# =============================================================================
# APIResponse Data Model Tests
# =============================================================================


class TestAPIResponse:
    """Tests for APIResponse dataclass."""

    def test_creation(self) -> None:
        """Should create APIResponse."""
        response = APIResponse(success=True, data={"key": "value"}, error=None)

        assert response.success is True
        assert response.data == {"key": "value"}
        assert response.error is None


# =============================================================================
# CommandCenterClient Initialization Tests
# =============================================================================


class TestCommandCenterClientInit:
    """Tests for CommandCenterClient initialization."""

    def test_init_defaults(self) -> None:
        """Should initialize with defaults."""
        client = CommandCenterClient()

        assert client.base_url == "http://localhost:8080"
        assert client.token is None
        assert client.timeout == 30.0
        assert client._session is None

    def test_init_custom(self) -> None:
        """Should initialize with custom values."""
        client = CommandCenterClient(
            base_url="http://api.example.com",
            token="test-token",
            timeout=60.0,
        )

        assert client.base_url == "http://api.example.com"
        assert client.token == "test-token"
        assert client.timeout == 60.0

    def test_init_strips_trailing_slash(self) -> None:
        """Should strip trailing slash from base_url."""
        client = CommandCenterClient(base_url="http://api.example.com/")

        assert client.base_url == "http://api.example.com"

    def test_from_env(self) -> None:
        """Should create client from environment variables."""
        env = {
            "COMMAND_CENTER_URL": "http://env-api.com",
            "FORGE_WEBHOOK_TOKEN": "env-token",
        }

        with patch.dict("os.environ", env, clear=True):
            client = CommandCenterClient.from_env()

            assert client.base_url == "http://env-api.com"
            assert client.token == "env-token"

    def test_from_env_defaults(self) -> None:
        """Should use defaults when env vars not set."""
        with patch.dict("os.environ", {}, clear=True):
            client = CommandCenterClient.from_env()

            assert client.base_url == "http://localhost:8080"
            assert client.token is None


# =============================================================================
# CommandCenterClient Session Management Tests
# =============================================================================


class TestSessionManagement:
    """Tests for session management."""

    @pytest.mark.asyncio
    async def test_ensure_session_creates_new(self) -> None:
        """Should create new session if none exists."""
        client = CommandCenterClient()

        mock_session = AsyncMock()
        mock_session_class = MagicMock(return_value=mock_session)

        with patch("aiohttp.ClientSession", mock_session_class):
            await client._ensure_session()

            assert client._session is not None
            mock_session_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_session_existing(self) -> None:
        """Should not create new session if one exists."""
        client = CommandCenterClient()
        client._session = AsyncMock()

        with patch("aiohttp.ClientSession") as mock_session_class:
            await client._ensure_session()

            mock_session_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_session(self) -> None:
        """Should close session."""
        client = CommandCenterClient()
        mock_session = AsyncMock()
        client._session = mock_session

        await client.close()

        mock_session.close.assert_called_once()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Should work as async context manager."""
        async with CommandCenterClient() as client:
            assert isinstance(client, CommandCenterClient)

    def test_headers_without_token(self) -> None:
        """Should return headers without auth when no token."""
        client = CommandCenterClient()
        headers = client._headers()

        assert headers == {"Content-Type": "application/json"}
        assert "Authorization" not in headers

    def test_headers_with_token(self) -> None:
        """Should include auth header when token present."""
        client = CommandCenterClient(token="test-token")
        headers = client._headers()

        assert headers["Authorization"] == "Bearer test-token"


# =============================================================================
# HTTP Request Tests
# =============================================================================


class TestHTTPRequests:
    """Tests for HTTP request handling."""

    @pytest.mark.asyncio
    async def test_request_success(self) -> None:
        """Should handle successful request."""
        client = CommandCenterClient()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "success": True,
            "data": {"agents": []},
        })

        # Setup async context manager
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_cm)

        client._session = mock_session

        response = await client._request("GET", "/api/agents")

        assert response.success is True
        assert response.data == {"agents": []}

    @pytest.mark.asyncio
    async def test_request_http_error(self) -> None:
        """Should handle HTTP error response."""
        client = CommandCenterClient()

        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.json = AsyncMock(return_value={"detail": "Not found"})

        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.request = MagicMock(return_value=mock_cm)

        client._session = mock_session

        response = await client._request("GET", "/api/agents/123")

        assert response.success is False
        assert response.error == "Not found"

    @pytest.mark.asyncio
    async def test_request_connection_error(self) -> None:
        """Should handle connection error."""
        client = CommandCenterClient()

        mock_session = AsyncMock()
        mock_session.request = AsyncMock(side_effect=ConnectionRefusedError())
        client._session = mock_session

        response = await client._request("GET", "/api/agents")

        assert response.success is False


# =============================================================================
# Agent Operations Tests
# =============================================================================


class TestAgentOperations:
    """Tests for agent operations."""

    @pytest.mark.asyncio
    async def test_list_agents_success(self) -> None:
        """Should list agents successfully."""
        client = CommandCenterClient()

        mock_response = APIResponse(
            success=True,
            data=[{"id": "agent-1", "role": "dev"}],
        )

        with patch.object(client, "_request", return_value=mock_response):
            agents = await client.list_agents()

            assert len(agents) == 1
            assert agents[0].id == "agent-1"

    @pytest.mark.asyncio
    async def test_list_agents_failure(self) -> None:
        """Should return empty list on failure."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=False, error="API Error")

        with patch.object(client, "_request", return_value=mock_response):
            agents = await client.list_agents()

            assert agents == []

    @pytest.mark.asyncio
    async def test_get_agent_success(self) -> None:
        """Should get agent by ID."""
        client = CommandCenterClient()

        mock_response = APIResponse(
            success=True,
            data={"id": "agent-1", "role": "dev"},
        )

        with patch.object(client, "_request", return_value=mock_response):
            agent = await client.get_agent("agent-1")

            assert agent is not None
            assert agent.id == "agent-1"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self) -> None:
        """Should return None when agent not found."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=False, error="Not found")

        with patch.object(client, "_request", return_value=mock_response):
            agent = await client.get_agent("agent-1")

            assert agent is None

    @pytest.mark.asyncio
    async def test_register_agent_success(self) -> None:
        """Should register new agent."""
        client = CommandCenterClient()

        mock_response = APIResponse(
            success=True,
            data={"id": "agent-1", "role": "dev", "task": "Test task"},
        )

        with patch.object(client, "_request", return_value=mock_response):
            agent = await client.register_agent("dev", "Test task")

            assert agent is not None
            assert agent.role == "dev"

    @pytest.mark.asyncio
    async def test_register_agent_failure(self) -> None:
        """Should return None on registration failure."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=False, error="Failed")

        with patch.object(client, "_request", return_value=mock_response):
            agent = await client.register_agent("dev", "Test task")

            assert agent is None

    @pytest.mark.asyncio
    async def test_update_agent_progress(self) -> None:
        """Should update agent progress."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.update_agent_progress("agent-1", 50)

            assert result is True

    @pytest.mark.asyncio
    async def test_complete_agent(self) -> None:
        """Should mark agent as completed."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.complete_agent("agent-1", "Task completed")

            assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat(self) -> None:
        """Should send heartbeat."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.heartbeat("agent-1")

            assert result is True

    @pytest.mark.asyncio
    async def test_send_message(self) -> None:
        """Should send message to agent."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.send_message("agent-1", "Hello")

            assert result is True

    @pytest.mark.asyncio
    async def test_broadcast_message(self) -> None:
        """Should broadcast message to all agents."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"sent_count": 3})

        with patch.object(client, "_request", return_value=mock_response):
            count = await client.broadcast_message("Hello all")

            assert count == 3


# =============================================================================
# Approval Operations Tests
# =============================================================================


class TestApprovalOperations:
    """Tests for approval operations."""

    @pytest.mark.asyncio
    async def test_list_approvals_success(self) -> None:
        """Should list approvals."""
        client = CommandCenterClient()

        mock_response = APIResponse(
            success=True,
            data=[{"id": "approval-1", "title": "Test"}],
        )

        with patch.object(client, "_request", return_value=mock_response):
            approvals = await client.list_approvals()

            assert len(approvals) == 1
            assert approvals[0].id == "approval-1"

    @pytest.mark.asyncio
    async def test_list_approvals_with_filters(self) -> None:
        """Should list approvals with filters."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data=[])

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.list_approvals(
                status=ApprovalStatus.PENDING,
                domain="test-domain",
                priority="high",
            )

            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[1]["params"]["status"] == "pending"
            assert call_args[1]["params"]["domain"] == "test-domain"
            assert call_args[1]["params"]["priority"] == "high"

    @pytest.mark.asyncio
    async def test_approve(self) -> None:
        """Should approve request."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.approve("approval-1", "Looks good")

            assert result is True

    @pytest.mark.asyncio
    async def test_reject(self) -> None:
        """Should reject request."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.reject("approval-1", "Needs work")

            assert result is True

    @pytest.mark.asyncio
    async def test_get_approval_count(self) -> None:
        """Should get approval count."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"count": 5})

        with patch.object(client, "_request", return_value=mock_response):
            count = await client.get_approval_count()

            assert count == 5

    @pytest.mark.asyncio
    async def test_get_approval_stats(self) -> None:
        """Should get approval stats."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"pending": 5, "approved": 10})

        with patch.object(client, "_request", return_value=mock_response):
            stats = await client.get_approval_stats()

            assert stats["pending"] == 5


# =============================================================================
# Portfolio Operations Tests
# =============================================================================


class TestPortfolioOperations:
    """Tests for portfolio operations."""

    @pytest.mark.asyncio
    async def test_list_portfolio(self) -> None:
        """Should list portfolio domains."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data=[{"domain": "test"}])

        with patch.object(client, "_request", return_value=mock_response):
            domains = await client.list_portfolio()

            assert len(domains) == 1

    @pytest.mark.asyncio
    async def test_get_domain(self) -> None:
        """Should get domain details."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"id": "test-domain"})

        with patch.object(client, "_request", return_value=mock_response):
            domain = await client.get_domain("test-domain")

            assert domain is not None
            assert domain["id"] == "test-domain"

    @pytest.mark.asyncio
    async def test_get_project(self) -> None:
        """Should get project details."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"name": "test-project"})

        with patch.object(client, "_request", return_value=mock_response):
            project = await client.get_project("test-domain", "test-project")

            assert project is not None
            assert project["name"] == "test-project"


# =============================================================================
# Pattern Operations Tests
# =============================================================================


class TestPatternOperations:
    """Tests for pattern operations."""

    @pytest.mark.asyncio
    async def test_list_patterns(self) -> None:
        """Should list patterns."""
        client = CommandCenterClient()

        mock_response = APIResponse(
            success=True,
            data=[{"id": "pattern-1", "name": "Test"}],
        )

        with patch.object(client, "_request", return_value=mock_response):
            patterns = await client.list_patterns()

            assert len(patterns) == 1
            assert patterns[0].id == "pattern-1"

    @pytest.mark.asyncio
    async def test_get_pattern(self) -> None:
        """Should get pattern by ID."""
        client = CommandCenterClient()

        mock_response = APIResponse(
            success=True,
            data={"id": "pattern-1", "name": "Test"},
        )

        with patch.object(client, "_request", return_value=mock_response):
            pattern = await client.get_pattern("pattern-1")

            assert pattern is not None
            assert pattern.id == "pattern-1"

    @pytest.mark.asyncio
    async def test_create_pattern(self) -> None:
        """Should create new pattern."""
        client = CommandCenterClient()

        mock_response = APIResponse(
            success=True,
            data={"id": "pattern-1", "name": "New Pattern"},
        )

        with patch.object(client, "_request", return_value=mock_response):
            pattern = await client.create_pattern("New Pattern", "Description", "auth")

            assert pattern is not None
            assert pattern.name == "New Pattern"

    @pytest.mark.asyncio
    async def test_record_outcome(self) -> None:
        """Should record pattern outcome."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.record_outcome("pattern-1", True)

            assert result is True


# =============================================================================
# Task Operations Tests
# =============================================================================


class TestTaskOperations:
    """Tests for task operations."""

    @pytest.mark.asyncio
    async def test_list_tasks(self) -> None:
        """Should list tasks."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"tasks": [{"id": "task-1"}]})

        with patch.object(client, "_request", return_value=mock_response):
            tasks = await client.list_tasks()

            assert len(tasks) == 1
            assert tasks[0]["id"] == "task-1"

    @pytest.mark.asyncio
    async def test_claim_task(self) -> None:
        """Should claim task."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"claimed": True})

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.claim_task("task-1", "agent-1")

            assert result is not None
            assert result["claimed"] is True

    @pytest.mark.asyncio
    async def test_dispatch_task(self) -> None:
        """Should dispatch task."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"dispatched": True})

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.dispatch_task("task-1", "agent-1")

            assert result is not None

    @pytest.mark.asyncio
    async def test_create_task(self) -> None:
        """Should create task."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"id": "task-1"})

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.create_task("Test task", "Description", "high")

            assert result is not None
            assert result["id"] == "task-1"

    @pytest.mark.asyncio
    async def test_get_task(self) -> None:
        """Should get task by ID."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"id": "task-1"})

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_task("task-1")

            assert result is not None

    @pytest.mark.asyncio
    async def test_get_recommended_tasks(self) -> None:
        """Should get recommended tasks."""
        client = CommandCenterClient()

        mock_response = APIResponse(
            success=True,
            data={"recommendations": [{"task": {"id": "task-1"}}]},
        )

        with patch.object(client, "_request", return_value=mock_response):
            tasks = await client.get_recommended_tasks()

            assert len(tasks) == 1

    @pytest.mark.asyncio
    async def test_update_task(self) -> None:
        """Should update task."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"id": "task-1", "status": "completed"})

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.update_task("task-1", status="completed")

            assert result is not None
            assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_delete_task(self) -> None:
        """Should delete task."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True)

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.delete_task("task-1")

            assert result is True

    @pytest.mark.asyncio
    async def test_get_task_stats(self) -> None:
        """Should get task stats."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"total": 10, "pending": 5})

        with patch.object(client, "_request", return_value=mock_response):
            stats = await client.get_task_stats()

            assert stats["total"] == 10

    @pytest.mark.asyncio
    async def test_reorder_tasks(self) -> None:
        """Should reorder tasks."""
        client = CommandCenterClient()

        mock_response = APIResponse(success=True, data={"updated": [{"id": "task-1"}]})

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.reorder_tasks(["task-1", "task-2"])

            assert len(result) == 1


# =============================================================================
# Sync Wrapper Tests
# =============================================================================


class TestSyncWrappers:
    """Tests for sync wrapper methods."""

    def test_sync_list_agents(self) -> None:
        """Should sync list agents."""
        client = CommandCenterClient()

        mock_agent = MagicMock()

        with patch.object(client, "_run_sync", return_value=[mock_agent]):
            agents = client.sync_list_agents()

            assert len(agents) == 1

    def test_sync_get_agent(self) -> None:
        """Should sync get agent."""
        client = CommandCenterClient()

        mock_agent = MagicMock()

        with patch.object(client, "_run_sync", return_value=mock_agent):
            agent = client.sync_get_agent("agent-1")

            assert agent is mock_agent

    def test_sync_heartbeat(self) -> None:
        """Should sync send heartbeat."""
        client = CommandCenterClient()

        with patch.object(client, "_run_sync", return_value=True):
            result = client.sync_heartbeat("agent-1")

            assert result is True

    def test_sync_approve(self) -> None:
        """Should sync approve request."""
        client = CommandCenterClient()

        with patch.object(client, "_run_sync", return_value=True):
            result = client.sync_approve("approval-1")

            assert result is True

    def test_sync_reject(self) -> None:
        """Should sync reject request."""
        client = CommandCenterClient()

        with patch.object(client, "_run_sync", return_value=True):
            result = client.sync_reject("approval-1")

            assert result is True

    def test_sync_list_patterns(self) -> None:
        """Should sync list patterns."""
        client = CommandCenterClient()

        mock_pattern = MagicMock()

        with patch.object(client, "_run_sync", return_value=[mock_pattern]):
            patterns = client.sync_list_patterns()

            assert len(patterns) == 1

    def test_sync_list_tasks(self) -> None:
        """Should sync list tasks."""
        client = CommandCenterClient()

        with patch.object(client, "_run_sync", return_value=[{"id": "task-1"}]):
            tasks = client.sync_list_tasks()

            assert len(tasks) == 1


# =============================================================================
# _run_sync Tests
# =============================================================================


class TestRunSync:
    """Tests for _run_sync method."""

    def test_run_sync_no_loop(self) -> None:
        """Should run coroutine with asyncio.run when no loop."""
        client = CommandCenterClient()

        async def test_coro():
            return "result"

        with patch("asyncio.get_running_loop", side_effect=RuntimeError()), \
             patch("asyncio.run", return_value="result") as mock_run:
            result = client._run_sync(test_coro())

            assert result == "result"
            mock_run.assert_called_once()


# =============================================================================
# SSE Event Tests
# =============================================================================


class TestSSEEvents:
    """Tests for SSE event subscription."""

    @pytest.mark.asyncio
    async def test_subscribe_events_success(self) -> None:
        """Should subscribe to SSE events."""
        client = CommandCenterClient(token="test-token")

        mock_response = AsyncMock()
        mock_response.status = 200

        # Simulate SSE lines using proper async iterator
        async def async_lines():
            lines = [
                b'data: {"type": "agent_update", "data": {"id": "1"}}',
            ]
            for line in lines:
                yield line

        mock_response.content = async_lines()

        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_cm)

        client._session = mock_session

        events = []
        async for event in client.subscribe_events():
            events.append(event)
            break  # Just get first event

        assert len(events) == 1
        assert events[0].type == "agent_update"

    @pytest.mark.asyncio
    async def test_subscribe_events_with_filter(self) -> None:
        """Should filter events by type."""
        client = CommandCenterClient()

        mock_response = AsyncMock()
        mock_response.status = 200

        # Simulate SSE lines using proper async iterator
        async def async_lines():
            lines = [
                b'data: {"type": "agent_update", "data": {}}',
                b'data: {"type": "other_event", "data": {}}',
            ]
            for line in lines:
                yield line

        mock_response.content = async_lines()

        mock_session = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_cm)

        client._session = mock_session

        events = []
        async for event in client.subscribe_events(event_types=["agent_update"]):
            events.append(event)
            if len(events) >= 1:
                break

        assert len(events) == 1
        assert events[0].type == "agent_update"
