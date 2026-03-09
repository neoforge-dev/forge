"""
Tests for Command Center API client.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.command_center_client import (
    Agent,
    AgentStatus,
    Approval,
    ApprovalStatus,
    CommandCenterClient,
    Pattern,
    PortfolioProject,
    SSEEvent,
    Task,
    TaskPriority,
    TaskStatus,
    _parse_datetime,
)


class TestDataModels:
    """Test data model parsing."""

    def test_parse_datetime_valid(self):
        """Test parsing valid ISO datetime."""
        result = _parse_datetime("2026-01-25T12:00:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 25

    def test_parse_datetime_with_z(self):
        """Test parsing datetime with Z suffix."""
        result = _parse_datetime("2026-01-25T12:00:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_parse_datetime_none(self):
        """Test parsing None returns None."""
        assert _parse_datetime(None) is None

    def test_parse_datetime_invalid(self):
        """Test parsing invalid datetime returns None."""
        assert _parse_datetime("not-a-date") is None

    def test_agent_from_dict(self):
        """Test Agent.from_dict parsing."""
        data = {
            "id": "test-123",
            "role": "content-agent",
            "name": "Test Agent",
            "project": "/test-project",
            "task": "Generate content",
            "status": "active",
            "progress": 50,
            "current_task": "Writing blog post",
            "files_modified": ["file1.py", "file2.py"],
            "token_usage": {"input": 100, "output": 200},
            "messages_count": 5,
            "registered_at": "2026-01-25T12:00:00Z",
            "last_activity": "2026-01-25T12:30:00Z",
        }

        agent = Agent.from_dict(data)

        assert agent.id == "test-123"
        assert agent.role == "content-agent"
        assert agent.name == "Test Agent"
        assert agent.project == "/test-project"
        assert agent.task == "Generate content"
        assert agent.status == AgentStatus.ACTIVE
        assert agent.progress == 50
        assert agent.current_task == "Writing blog post"
        assert len(agent.files_modified) == 2
        assert agent.messages_count == 5
        assert agent.registered_at is not None

    def test_agent_from_dict_minimal(self):
        """Test Agent.from_dict with minimal data."""
        data = {"id": "test-123"}

        agent = Agent.from_dict(data)

        assert agent.id == "test-123"
        assert agent.role == "unknown"
        assert agent.status == AgentStatus.ACTIVE
        assert agent.progress == 0

    def test_approval_from_dict(self):
        """Test Approval.from_dict parsing."""
        data = {
            "id": "approval-123",
            "type": "deployment",
            "title": "Deploy to production",
            "description": "Ready for release",
            "domain": "codeswiftr-com",
            "project": "interview-simulator",
            "priority": "high",
            "status": "pending",
            "tier": "desktop",
            "context": {"version": "1.0.0"},
            "created_at": "2026-01-25T12:00:00Z",
        }

        approval = Approval.from_dict(data)

        assert approval.id == "approval-123"
        assert approval.type == "deployment"
        assert approval.title == "Deploy to production"
        assert approval.status == ApprovalStatus.PENDING
        assert approval.tier.value == "desktop"
        assert approval.context["version"] == "1.0.0"

    def test_pattern_from_dict(self):
        """Test Pattern.from_dict parsing."""
        data = {
            "id": "pattern-123",
            "name": "Test Pattern",
            "description": "A test pattern",
            "category": "testing",
            "success_rate": 0.85,
            "usage_count": 100,
            "created_at": "2026-01-25T12:00:00Z",
        }

        pattern = Pattern.from_dict(data)

        assert pattern.id == "pattern-123"
        assert pattern.name == "Test Pattern"
        assert pattern.category == "testing"
        assert pattern.success_rate == 0.85
        assert pattern.usage_count == 100

    def test_sse_event_from_line(self):
        """Test SSEEvent.from_line parsing."""
        line = 'data: {"type": "agent.registered", "data": {"id": "123", "role": "test"}}'

        event = SSEEvent.from_line(line)

        assert event is not None
        assert event.type == "agent.registered"
        assert event.data["id"] == "123"
        assert event.data["role"] == "test"

    def test_sse_event_from_line_invalid(self):
        """Test SSEEvent.from_line with invalid data."""
        assert SSEEvent.from_line("not-data-line") is None
        assert SSEEvent.from_line("data: not-json") is None


class TestPortfolioProjectDataModel:
    """Test PortfolioProject data model parsing."""

    def test_portfolio_project_from_dict(self):
        """Test PortfolioProject.from_dict parsing."""
        data = {
            "domain": "codeswiftr-com",
            "project": "interview-simulator",
            "status": "active",
            "description": "AI interview prep",
            "tech_stack": ["FastAPI", "React"],
            "last_activity": "2026-01-25T12:00:00Z",
        }

        project = PortfolioProject.from_dict(data)

        assert project.domain == "codeswiftr-com"
        assert project.project == "interview-simulator"
        assert project.status == "active"
        assert project.description == "AI interview prep"
        assert project.tech_stack == ["FastAPI", "React"]
        assert project.last_activity is not None


class TestCommandCenterClient:
    """Test CommandCenterClient."""

    def test_init_default(self):
        """Test default initialization."""
        client = CommandCenterClient()

        assert client.base_url == "http://localhost:8080"
        assert client.token is None
        assert client.timeout == 30.0

    def test_init_with_params(self):
        """Test initialization with parameters."""
        client = CommandCenterClient(
            base_url="http://example.com:9000",
            token="test-token",
            timeout=60.0,
        )

        assert client.base_url == "http://example.com:9000"
        assert client.token == "test-token"
        assert client.timeout == 60.0

    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from URL."""
        client = CommandCenterClient(base_url="http://localhost:8080/")

        assert client.base_url == "http://localhost:8080"

    @patch.dict(
        "os.environ", {"COMMAND_CENTER_URL": "http://test:9000", "FORGE_WEBHOOK_TOKEN": "env-token"}
    )
    def test_from_env(self):
        """Test creating client from environment."""
        client = CommandCenterClient.from_env()

        assert client.base_url == "http://test:9000"
        assert client.token == "env-token"

    def test_headers_without_token(self):
        """Test headers without token."""
        client = CommandCenterClient()
        headers = client._headers()

        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers

    def test_headers_with_token(self):
        """Test headers with token."""
        client = CommandCenterClient(token="test-token")
        headers = client._headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer test-token"


class TestCommandCenterClientAsync:
    """Test async methods of CommandCenterClient."""

    @pytest.mark.asyncio
    async def test_list_agents_success(self):
        """Test listing agents successfully."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "agents": [
                        {"id": "1", "role": "content-agent", "status": "active"},
                        {"id": "2", "role": "tech-agent", "status": "idle"},
                    ]
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            agents = await client.list_agents()

        assert len(agents) == 2
        assert agents[0].role == "content-agent"
        assert agents[1].role == "tech-agent"

    @pytest.mark.asyncio
    async def test_get_agent_success(self):
        """Test get_agent successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"id": "agent-1"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            agent = await client.get_agent("agent-1")

        assert agent is not None
        assert agent.id == "agent-1"

    @pytest.mark.asyncio
    async def test_register_agent_success(self):
        """Test registering an agent successfully."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "id": "new-agent-123",
                    "role": "content-agent",
                    "task": "Generate content",
                    "status": "active",
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            agent = await client.register_agent(
                role="content-agent",
                task="Generate content",
                project="/test",
            )

        assert agent is not None
        assert agent.id == "new-agent-123"
        assert agent.role == "content-agent"

    @pytest.mark.asyncio
    async def test_update_agent_progress_success(self):
        """Test update_agent_progress successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.update_agent_progress("agent-1", 75, current_task="Testing")

        assert result is True

    @pytest.mark.asyncio
    async def test_complete_agent_success(self):
        """Test complete_agent successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.complete_agent("agent-1", summary="Done")

        assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat_success(self):
        """Test heartbeat successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.heartbeat("agent-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """Test sending message to agent."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.send_message("agent-123", "Hello")

        assert result is True

    @pytest.mark.asyncio
    async def test_broadcast_message_success(self):
        """Test broadcast_message successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"sent_count": 5}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            count = await client.broadcast_message("Hello all")

        assert count == 5

    @pytest.mark.asyncio
    async def test_list_approvals_success(self):
        """Test list_approvals successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "success": True,
            "data": {"approvals": [{"id": "app-1", "type": "test", "title": "T1"}]}
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            approvals = await client.list_approvals(domain="test-domain")

        assert len(approvals) == 1
        assert approvals[0].id == "app-1"

    @pytest.mark.asyncio
    async def test_get_approval_success(self):
        """Test get_approval successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"id": "app-1", "type": "test", "title": "T1"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            approval = await client.get_approval("app-1")

        assert approval is not None
        assert approval.id == "app-1"

    @pytest.mark.asyncio
    async def test_approve_success(self):
        """Test approving a request."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.approve("request-123", notes="LGTM")

        assert result is True

    @pytest.mark.asyncio
    async def test_reject_success(self):
        """Test reject successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.reject("app-1", reason="No")

        assert result is True

    @pytest.mark.asyncio
    async def test_get_approval_count_success(self):
        """Test get_approval_count successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"count": 10}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            count = await client.get_approval_count()

        assert count == 10

    @pytest.mark.asyncio
    async def test_get_approval_stats_success(self):
        """Test get_approval_stats successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"total": 100}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            stats = await client.get_approval_stats()

        assert stats["total"] == 100

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """Test handling API errors."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.json = AsyncMock(return_value={"detail": "Not found"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            agent = await client.get_agent("nonexistent")

        assert agent is None

    @pytest.mark.asyncio
    async def test_request_error_status(self):
        """Test _request with error status code."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.json = AsyncMock(return_value={"detail": "Bad Request"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            response = await client._request("GET", "/test")

        assert response.success is False
        assert "Bad Request" in response.error

    @pytest.mark.asyncio
    async def test_request_exception(self):
        """Test _request with exception."""
        client = CommandCenterClient()

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(side_effect=Exception("Connection error"))

            response = await client._request("GET", "/test")

        assert response.success is False
        assert "Connection error" in response.error


class TestTaskDataModel:
    """Test Task data model parsing."""

    def test_task_from_dict(self):
        """Test Task.from_dict parsing."""
        data = {
            "id": "task-123",
            "subject": "Implement feature X",
            "description": "Add new feature to the codebase",
            "priority": "high",
            "status": "pending",
            "required_role": "opencode",
            "claimed_by": None,
            "created_at": "2026-01-25T12:00:00Z",
            "updated_at": "2026-01-25T12:30:00Z",
            "order": 1,
        }

        task = Task.from_dict(data)

        assert task.id == "task-123"
        assert task.subject == "Implement feature X"
        assert task.priority == TaskPriority.HIGH
        assert task.status == TaskStatus.PENDING
        assert task.required_role == "opencode"
        assert task.claimed_by is None
        assert task.order == 1

    def test_task_from_dict_minimal(self):
        """Test Task.from_dict with minimal data."""
        data = {"id": "task-123", "subject": "Test task", "description": "", "priority": "medium", "status": "pending"}

        task = Task.from_dict(data)

        assert task.id == "task-123"
        assert task.priority == TaskPriority.MEDIUM
        assert task.status == TaskStatus.PENDING
        assert task.required_role is None
        assert task.claimed_by is None

    def test_task_priority_enum(self):
        """Test TaskPriority enum values."""
        assert TaskPriority.LOW.value == "low"
        assert TaskPriority.MEDIUM.value == "medium"
        assert TaskPriority.HIGH.value == "high"
        assert TaskPriority.CRITICAL.value == "critical"

    def test_task_status_enum(self):
        """Test TaskStatus enum values."""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.ASSIGNED.value == "assigned"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.BLOCKED.value == "blocked"


class TestTaskOperations:
    """Test task operations."""

    @pytest.mark.asyncio
    async def test_list_tasks_success(self):
        """Test listing tasks successfully."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "tasks": [
                        {"id": "1", "subject": "Task 1", "description": "Desc 1", "priority": "high", "status": "pending"},
                        {"id": "2", "subject": "Task 2", "description": "Desc 2", "priority": "medium", "status": "assigned"},
                    ],
                    "count": 2,
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            tasks = await client.list_tasks(status="pending")

        assert len(tasks) == 2
        assert tasks[0]["subject"] == "Task 1"

    @pytest.mark.asyncio
    async def test_create_task_success(self):
        """Test creating a task successfully."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "id": "new-task-123",
                    "subject": "New Task",
                    "description": "Task description",
                    "priority": "high",
                    "status": "pending",
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.create_task(
                subject="New Task",
                description="Task description",
                priority="high",
            )

        assert result is not None
        assert result["id"] == "new-task-123"

    @pytest.mark.asyncio
    async def test_get_task_success(self):
        """Test getting a task by ID."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "id": "task-123",
                    "subject": "Test Task",
                    "description": "Description",
                    "priority": "medium",
                    "status": "pending",
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            task = await client.get_task("task-123")

        assert task is not None
        assert task["id"] == "task-123"

    @pytest.mark.asyncio
    async def test_claim_task_success(self):
        """Test claiming a task."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "id": "task-123",
                    "subject": "Test Task",
                    "status": "assigned",
                    "claimed_by": "agent-456",
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.claim_task("task-123", "agent-456")

        assert result is not None
        assert result["claimed_by"] == "agent-456"

    @pytest.mark.asyncio
    async def test_dispatch_task_success(self):
        """Test dispatching a task."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "id": "task-123",
                    "subject": "Test Task",
                    "status": "assigned",
                    "claimed_by": "agent-789",
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.dispatch_task("task-123", "agent-789")

        assert result is not None
        assert result["claimed_by"] == "agent-789"

    @pytest.mark.asyncio
    async def test_update_task_success(self):
        """Test updating a task."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "id": "task-123",
                    "subject": "Updated Task",
                    "description": "Updated description",
                    "priority": "high",
                    "status": "in_progress",
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.update_task(
                task_id="task-123",
                subject="Updated Task",
                status="in_progress",
            )

        assert result is not None
        assert result["subject"] == "Updated Task"

    @pytest.mark.asyncio
    async def test_delete_task_success(self):
        """Test deleting a task."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.delete_task("task-123")

        assert result is True

    @pytest.mark.asyncio
    async def test_get_task_stats_success(self):
        """Test getting task statistics."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "total": 100,
                    "pending": 50,
                    "in_progress": 20,
                    "completed": 30,
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            stats = await client.get_task_stats()

        assert stats["total"] == 100
        assert stats["pending"] == 50

    @pytest.mark.asyncio
    async def test_get_recommended_tasks_success(self):
        """Test getting recommended tasks."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "tasks": [
                        {"id": "1", "subject": "Recommended Task 1", "score": 0.95},
                        {"id": "2", "subject": "Recommended Task 2", "score": 0.90},
                    ],
                    "count": 2,
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            recommendations = await client.get_recommended_tasks(limit=10)

        assert len(recommendations) == 2
        assert recommendations[0]["score"] == 0.95

    @pytest.mark.asyncio
    async def test_reorder_tasks_success(self):
        """Test reordering tasks."""
        client = CommandCenterClient()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "updated": [
                        {"id": "1", "order": 0},
                        {"id": "2", "order": 1},
                    ],
                    "count": 2,
                },
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.reorder_tasks(["1", "2"])

        assert len(result) == 2


class TestPortfolioOperations:
    """Test portfolio operations."""

    @pytest.mark.asyncio
    async def test_list_portfolio_success(self):
        """Test list_portfolio successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": [{"domain": "d1"}]})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            portfolio = await client.list_portfolio()

        assert len(portfolio) == 1
        assert portfolio[0]["domain"] == "d1"

    @pytest.mark.asyncio
    async def test_get_domain_success(self):
        """Test get_domain successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"domain": "d1"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            domain = await client.get_domain("d1")

        assert domain is not None
        assert domain["domain"] == "d1"

    @pytest.mark.asyncio
    async def test_get_project_success(self):
        """Test get_project successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"project": "p1"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            project = await client.get_project("d1", "p1")

        assert project is not None
        assert project["project"] == "p1"


class TestPatternOperations:
    """Test pattern library operations."""

    @pytest.mark.asyncio
    async def test_list_patterns_success(self):
        """Test list_patterns successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "success": True,
            "data": {"patterns": [{"id": "pat-1", "name": "P1"}]}
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            patterns = await client.list_patterns()

        assert len(patterns) == 1
        assert patterns[0].id == "pat-1"

    @pytest.mark.asyncio
    async def test_get_pattern_success(self):
        """Test get_pattern successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"id": "pat-1", "name": "P1"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            pattern = await client.get_pattern("pat-1")

        assert pattern is not None
        assert pattern.id == "pat-1"

    @pytest.mark.asyncio
    async def test_create_pattern_success(self):
        """Test create_pattern successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True, "data": {"id": "pat-new", "name": "P-New"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            pattern = await client.create_pattern("P-New")

        assert pattern is not None
        assert pattern.id == "pat-new"

    @pytest.mark.asyncio
    async def test_record_outcome_success(self):
        """Test record_outcome successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.request = MagicMock(return_value=mock_response)

            result = await client.record_outcome("pat-1", True)

        assert result is True


class TestSSEOperations:
    """Test real-time SSE event operations."""

    @pytest.mark.asyncio
    async def test_subscribe_events_success(self):
        """Test subscribe_events successfully."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200

        # Mocking async iterator for resp.content
        mock_response.content = MagicMock()
        mock_response.content.__aiter__ = Mock(return_value=mock_response.content)
        mock_response.content.__anext__ = AsyncMock()
        mock_response.content.__anext__.side_effect = [
            b'data: {"type": "t1", "data": {"id": "1"}}\n',
            b': heartbeat\n',
            b'data: {"type": "t2", "data": {"id": "2"}}\n',
            StopAsyncIteration
        ]

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.get = MagicMock(return_value=mock_response)

            events = []
            async for event in client.subscribe_events():
                events.append(event)

        assert len(events) == 2
        assert events[0].type == "t1"
        assert events[1].type == "t2"

    @pytest.mark.asyncio
    async def test_subscribe_events_filtered(self):
        """Test subscribe_events with filtering."""
        client = CommandCenterClient()
        mock_response = MagicMock()
        mock_response.status = 200

        mock_response.content = MagicMock()
        mock_response.content.__aiter__ = Mock(return_value=mock_response.content)
        mock_response.content.__anext__ = AsyncMock()
        mock_response.content.__anext__.side_effect = [
            b'data: {"type": "keep", "data": {"id": "1"}}\n',
            b'data: {"type": "skip", "data": {"id": "2"}}\n',
            StopAsyncIteration
        ]

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_ensure_session", new_callable=AsyncMock):
            client._session = MagicMock()
            client._session.get = MagicMock(return_value=mock_response)

            events = []
            async for event in client.subscribe_events(event_types=["keep"]):
                events.append(event)

        assert len(events) == 1
        assert events[0].type == "keep"


class TestTaskSyncWrappers:
    """Test sync wrappers for task operations."""

    def test_sync_list_tasks(self):
        """Test sync wrapper for list_tasks."""
        client = CommandCenterClient()
        with patch.object(client, "list_tasks", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [{"id": "1", "subject": "Task"}]
            result = client.sync_list_tasks(status="pending")

        assert len(result) == 1

    def test_sync_create_task(self):
        """Test sync wrapper for create_task."""
        client = CommandCenterClient()
        with patch.object(client, "create_task", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {"id": "new", "subject": "New Task"}
            result = client.sync_create_task(subject="New Task")

        assert result["subject"] == "New Task"

    def test_sync_get_task(self):
        """Test sync wrapper for get_task."""
        client = CommandCenterClient()
        with patch.object(client, "get_task", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"id": "task-123"}
            result = client.sync_get_task("task-123")

        assert result["id"] == "task-123"

    def test_sync_claim_task(self):
        """Test sync wrapper for claim_task."""
        client = CommandCenterClient()
        with patch.object(client, "claim_task", new_callable=AsyncMock) as mock_claim:
            mock_claim.return_value = {"id": "task", "claimed_by": "agent"}
            result = client.sync_claim_task("task", "agent")

        assert result["claimed_by"] == "agent"

    def test_sync_dispatch_task(self):
        """Test sync wrapper for dispatch_task."""
        client = CommandCenterClient()
        with patch.object(client, "dispatch_task", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"id": "task", "status": "assigned"}
            result = client.sync_dispatch_task("task", "agent")

        assert result["status"] == "assigned"

    def test_sync_update_task(self):
        """Test sync wrapper for update_task."""
        client = CommandCenterClient()
        with patch.object(client, "update_task", new_callable=AsyncMock) as mock_update:
            mock_update.return_value = {"id": "task", "status": "completed"}
            result = client.sync_update_task("task", status="completed")

        assert result["status"] == "completed"

    def test_sync_delete_task(self):
        """Test sync wrapper for delete_task."""
        client = CommandCenterClient()
        with patch.object(client, "delete_task", new_callable=AsyncMock) as mock_delete:
            mock_delete.return_value = True
            result = client.sync_delete_task("task")

        assert result is True

    def test_sync_get_task_stats(self):
        """Test sync wrapper for get_task_stats."""
        client = CommandCenterClient()
        with patch.object(client, "get_task_stats", new_callable=AsyncMock) as mock_stats:
            mock_stats.return_value = {"total": 50}
            result = client.sync_get_task_stats()

        assert result["total"] == 50

    def test_sync_get_recommended_tasks(self):
        """Test sync wrapper for get_recommended_tasks."""
        client = CommandCenterClient()
        with patch.object(client, "get_recommended_tasks", new_callable=AsyncMock) as mock_recommend:
            mock_recommend.return_value = [{"id": "1"}]
            result = client.sync_get_recommended_tasks(limit=5)

        assert len(result) == 1

    def test_sync_reorder_tasks(self):
        """Test sync wrapper for reorder_tasks."""
        client = CommandCenterClient()
        with patch.object(client, "reorder_tasks", new_callable=AsyncMock) as mock_reorder:
            mock_reorder.return_value = [{"id": "1"}, {"id": "2"}]
            result = client.sync_reorder_tasks(["1", "2"])

        assert len(result) == 2
