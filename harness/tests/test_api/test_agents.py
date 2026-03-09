"""Tests for /api/agents endpoints.

Tests cover:
- GET /api/agents - List all agents
- GET /api/agents/{agent_id} - Get agent details
- POST /api/agents/register - Register new agent
- POST /api/agents/{agent_id}/progress - Update agent progress
- POST /api/agents/{agent_id}/heartbeat - Agent heartbeat
- POST /api/agents/{agent_id}/complete - Complete agent
- POST /api/agents/{agent_id}/pause - Pause agent
- POST /api/agents/{agent_id}/resume - Resume agent
- POST /api/agents/{agent_id}/kill - Kill agent
- GET /api/agents/fleet/status - Fleet status
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestAgentsAPI:
    """Test class for /api/agents endpoints."""

    @pytest.fixture
    def mock_agent_registry(self):
        """Create a mock agent registry."""
        registry = Mock()

        # Create mock agents
        agent1 = Mock()
        agent1.id = "agent-001"
        agent1.session_id = "agent-001"  # Add session_id
        agent1.role = "backend-engineer"
        agent1.name = "Backend Agent"
        agent1.project = "codeswiftr-com/interview-simulator"
        agent1.task = "Implement API endpoints"
        agent1.status = "active"
        agent1.progress = 45
        agent1.current_task = "Creating user endpoints"
        agent1.files_modified = ["api.py", "models.py"]
        agent1.token_usage = {"input": 1000, "output": 500}
        agent1.messages_count = 25
        agent1.registered_at = datetime.now(UTC)
        agent1.last_activity = datetime.now(UTC)
        agent1.is_stale = False
        agent1.tmux_session = None
        agent1.domain = "codeswiftr-com"
        agent1.parent_id = None
        agent1.children = []
        agent1.skills = ["python", "fastapi"]
        # Add to_dict() method that returns a proper dict
        agent1.to_dict = Mock(return_value={
            "id": "agent-001",
            "session_id": "agent-001",
            "role": "backend-engineer",
            "name": "Backend Agent",
            "project": "codeswiftr-com/interview-simulator",
            "task": "Implement API endpoints",
            "status": "active",
            "progress": 45,
            "current_task": "Creating user endpoints",
            "files_modified": ["api.py", "models.py"],
            "token_usage": {"input": 1000, "output": 500},
            "messages_count": 25,
            "registered_at": agent1.registered_at,
            "last_activity": agent1.last_activity,
            "is_stale": False,
            "tmux_session": None,
            "domain": "codeswiftr-com",
            "parent_id": None,
            "children": [],
            "skills": ["python", "fastapi"],
        })

        agent2 = Mock()
        agent2.id = "agent-002"
        agent2.session_id = "agent-002"  # Add session_id
        agent2.role = "frontend-builder"
        agent2.name = "Frontend Agent"
        agent2.project = "codeswiftr-com/interview-simulator"
        agent2.task = "Build UI components"
        agent2.status = "idle"
        agent2.progress = 0
        agent2.current_task = None
        agent2.files_modified = []
        agent2.token_usage = {"input": 0, "output": 0}
        agent2.messages_count = 0
        agent2.registered_at = datetime.now(UTC)
        agent2.last_activity = datetime.now(UTC)
        agent2.is_stale = False
        agent2.tmux_session = None
        agent2.domain = "codeswiftr-com"
        agent2.parent_id = None
        agent2.children = []
        agent2.skills = ["react", "typescript"]
        # Add to_dict() method that returns a proper dict
        agent2.to_dict = Mock(return_value={
            "id": "agent-002",
            "session_id": "agent-002",
            "role": "frontend-builder",
            "name": "Frontend Agent",
            "project": "codeswiftr-com/interview-simulator",
            "task": "Build UI components",
            "status": "idle",
            "progress": 0,
            "current_task": None,
            "files_modified": [],
            "token_usage": {"input": 0, "output": 0},
            "messages_count": 0,
            "registered_at": agent2.registered_at,
            "last_activity": agent2.last_activity,
            "is_stale": False,
            "tmux_session": None,
            "domain": "codeswiftr-com",
            "parent_id": None,
            "children": [],
            "skills": ["react", "typescript"],
        })

        registry.list_active = Mock(return_value=[agent1, agent2])
        registry.get = Mock(side_effect=lambda x: {
            "agent-001": agent1,
            "agent-002": agent2,
        }.get(x))
        registry.register = Mock(return_value=agent1)
        registry.complete = Mock(return_value=True)
        registry.pause = Mock(return_value=True)
        registry.resume = Mock(return_value=True)
        registry.kill = Mock(return_value=True)

        return registry

    @pytest.fixture
    def mock_state_store(self):
        """Create a mock state store."""
        store = Mock()
        store.is_connected = Mock(return_value=False)
        return store

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = AsyncMock()
        bus.publish = AsyncMock()
        return bus

    @pytest.fixture
    def app_client(self, mock_agent_registry, mock_state_store, mock_event_bus):
        """Create a test client for the agents API."""
        # Create app with the router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server.api.agents import router
        app = FastAPI()
        app.include_router(router)

        # Patch dependencies
        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_state_store') as mock_store, \
             patch('forge_harness.webhook_server.api.agents.get_event_bus') as mock_bus:

            mock_reg.return_value = mock_agent_registry
            mock_store.return_value = mock_state_store
            mock_bus.return_value = mock_event_bus

            client = TestClient(app)
            yield client, mock_agent_registry, mock_event_bus

    async def test_list_agents_empty(self, mock_agent_registry):
        """Test listing agents when registry is empty."""
        mock_agent_registry.list_active.return_value = []

        from forge_harness.webhook_server.api.agents import list_agents

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg:
            mock_reg.return_value = mock_agent_registry

            result = await list_agents()

            # Check response structure
            body = result.body.decode()
            assert 'success' in body
            assert 'agents' in body
            assert 'total' in body

    async def test_list_agents_with_data(self, mock_agent_registry):
        """Test listing agents with active agents."""
        from forge_harness.webhook_server.api.agents import list_agents

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg:
            mock_reg.return_value = mock_agent_registry

            result = await list_agents()

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            assert 'agents' in body
            assert 'total' in body

    async def test_get_agent_found(self, mock_agent_registry):
        """Test getting an existing agent."""
        from forge_harness.webhook_server.api.agents import get_agent

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg:
            mock_reg.return_value = mock_agent_registry

            result = await get_agent("agent-001")

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            assert 'data' in body

    async def test_get_agent_not_found(self, mock_agent_registry):
        """Test getting a non-existent agent."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import get_agent

        mock_agent_registry.get.return_value = None

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg:
            mock_reg.return_value = mock_agent_registry

            with pytest.raises(HTTPException) as exc_info:
                await get_agent("non-existent")
            assert exc_info.value.status_code == 404

    async def test_get_agent_by_tmux_session(self):
        """Test getting agent by tmux session identifier."""
        from forge_harness.webhook_server.api.agents import get_agent

        # Create mock session from session tracker
        mock_session = Mock()
        mock_session.session_name = "forge:opencode"
        mock_session.window_name = "opencode"
        mock_session.agent_type = "opencode"
        mock_session.domain = None
        mock_session.project = ""
        mock_session.current_task = ""
        mock_session.status = "active"
        mock_session.started_at = None
        mock_session.last_activity = None

        mock_tracker = Mock()
        mock_tracker.get_session = Mock(return_value=mock_session)
        mock_tracker.get_all_sessions = Mock(return_value=[mock_session])

        mock_store = AsyncMock()
        mock_store.is_connected = Mock(return_value=False)

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_state_store', return_value=mock_store), \
             patch('forge_harness.session_tracker.get_session_tracker', return_value=mock_tracker):
            mock_reg.return_value = Mock()
            mock_reg.return_value.get.return_value = None
            mock_reg.return_value.list_active.return_value = []

            result = await get_agent("forge:opencode")

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body

    async def test_register_agent(self, mock_agent_registry, mock_state_store, mock_event_bus):
        """Test registering a new agent."""
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        request_body = AgentRegisterRequest(
            role="backend-engineer",
            project="codeswiftr-com/interview-simulator",
            task="Implement authentication",
            name="Test Agent",
            domain="codeswiftr-com",
            tmux_session="forge:test",
            skills=["python", "fastapi"]
        )

        # Create mock Request
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = None

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_state_store') as mock_store, \
             patch('forge_harness.webhook_server.api.agents.get_event_bus') as mock_bus, \
             patch('forge_harness.webhook_server.api.agents.get_audit_logger') as mock_audit:

            mock_reg.return_value = mock_agent_registry
            mock_store.return_value = mock_state_store
            mock_bus.return_value = mock_event_bus
            mock_audit.return_value = AsyncMock()

            result = await register_agent(request_body, mock_request)

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            mock_agent_registry.register.assert_called_once()
            mock_event_bus.publish.assert_called_once()

    async def test_agent_progress_update(self, mock_agent_registry):
        """Test updating agent progress."""
        from forge_harness.webhook_server.api.agents import (
            AgentProgressRequest,
            update_agent_progress,
        )

        request = AgentProgressRequest(
            progress=75,
            current_task="Implementing database schema",
            files_modified=["schema.py", "migrations.py"]
        )

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg:
            mock_reg.return_value = mock_agent_registry

            result = await update_agent_progress("agent-001", request)

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            assert mock_agent_registry.get.called

    async def test_agent_heartbeat(self, mock_agent_registry):
        """Test agent heartbeat endpoint."""
        from forge_harness.webhook_server.api.agents import agent_heartbeat

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg:
            mock_reg.return_value = mock_agent_registry

            result = await agent_heartbeat("agent-001")

            assert result.status_code == 200
            assert 'success' in result.body.decode()

    async def test_complete_agent(self, mock_agent_registry, mock_event_bus):
        """Test completing an agent."""
        from forge_harness.webhook_server.api.agents import complete_agent

        # Create mock Request
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = None

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_event_bus') as mock_bus, \
             patch('forge_harness.webhook_server.api.agents.get_audit_logger') as mock_audit:

            mock_reg.return_value = mock_agent_registry
            mock_bus.return_value = mock_event_bus
            mock_audit.return_value = AsyncMock()

            result = await complete_agent("agent-001", mock_request)

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            mock_agent_registry.complete.assert_called_once_with("agent-001")
            mock_event_bus.publish.assert_called_once()

    async def test_pause_agent(self, mock_agent_registry):
        """Test pausing an agent."""
        from forge_harness.webhook_server.api.agents import AgentPauseRequest, pause_agent

        request_body = AgentPauseRequest(reason="Waiting for input", duration_minutes=30)

        # Create mock Request
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = None

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_audit_logger') as mock_audit:
            mock_reg.return_value = mock_agent_registry
            mock_audit.return_value = AsyncMock()

            result = await pause_agent("agent-001", request_body, mock_request)

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            mock_agent_registry.pause.assert_called_once_with("agent-001")

    async def test_resume_agent(self, mock_agent_registry):
        """Test resuming a paused agent."""
        from forge_harness.webhook_server.api.agents import resume_agent

        # Create mock Request
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = None

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_audit_logger') as mock_audit:
            mock_reg.return_value = mock_agent_registry
            mock_audit.return_value = AsyncMock()

            result = await resume_agent("agent-001", mock_request)

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            mock_agent_registry.resume.assert_called_once_with("agent-001")

    async def test_kill_agent(self, mock_agent_registry):
        """Test killing an agent."""
        from forge_harness.webhook_server.api.agents import kill_agent

        # Create mock Request
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = None

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_audit_logger') as mock_audit:
            mock_reg.return_value = mock_agent_registry
            mock_audit.return_value = AsyncMock()

            result = await kill_agent("agent-001", mock_request)

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            mock_agent_registry.kill.assert_called_once_with("agent-001")

    async def test_fleet_status(self, mock_agent_registry):
        """Test getting fleet-wide status."""
        from forge_harness.webhook_server.api.agents import get_fleet_status

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg:
            mock_reg.return_value = mock_agent_registry

            result = await get_fleet_status()

            assert result.status_code == 200
            body = result.body.decode()
            assert 'success' in body
            assert 'total_agents' in body

    async def test_send_agent_message_success(self, mock_agent_registry, mock_event_bus):
        """Test sending a message to an agent via tmux."""
        from forge_harness.fleet.dispatch_client import DispatchResult
        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        # Update the existing agent from the fixture to have a tmux_session
        agent = mock_agent_registry.get("agent-001")
        agent.tmux_session = "forge:backend"

        request_body = AgentMessageRequest(message="Run tests")
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = None

        # Mock successful dispatch result
        mock_dispatch_result = DispatchResult(
            success=True,
            target="forge:backend",
            message_id="msg-123",
            delivery_time_ms=50.0,
            total_time_ms=100.0,
            attempts=1,
            verification_method="verified",
        )

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_event_bus') as mock_bus, \
             patch('forge_harness.webhook_server.api.agents.get_audit_logger') as mock_audit, \
             patch('forge_harness.fleet.dispatch_client.DispatchClient') as mock_client_class:

            mock_reg.return_value = mock_agent_registry
            mock_bus.return_value = mock_event_bus
            mock_audit.return_value = AsyncMock()

            # Mock DispatchClient instance and its send method
            mock_client = AsyncMock()
            mock_client.send = AsyncMock(return_value=mock_dispatch_result)
            mock_client_class.return_value = mock_client

            result = await send_agent_message("agent-001", request_body, mock_request)

            # Debug: print response body if not 200
            if result.status_code != 200:
                print(f"Response status: {result.status_code}")
                print(f"Response body: {result.body.decode()}")

            assert result.status_code == 200, f"Expected 200, got {result.status_code}: {result.body.decode()}"
            # Verify DispatchClient.send was called
            mock_client.send.assert_called_once()
            # Verify message was sent with correct target
            call_args = mock_client.send.call_args
            assert call_args[1]['target'] == "forge:backend"
            assert call_args[1]['message'] == "Run tests"

    async def test_send_agent_message_no_tmux_session(self, mock_agent_registry, mock_event_bus):
        """Test sending message to agent without tmux session."""
        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        # Create agent without tmux session
        agent = Mock()
        agent.id = "agent-001"
        agent.role = "backend-engineer"
        agent.tmux_session = None

        mock_agent_registry.get.return_value = agent

        request_body = AgentMessageRequest(message="Run tests")
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = None

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg, \
             patch('forge_harness.webhook_server.api.agents.get_event_bus') as mock_bus:

            mock_reg.return_value = mock_agent_registry
            mock_bus.return_value = mock_event_bus

            result = await send_agent_message("agent-001", request_body, mock_request)

            # Should return 400 error
            assert result.status_code == 400
            assert 'NO_TMUX_SESSION' in result.body.decode()

    async def test_send_agent_message_agent_not_found(self, mock_agent_registry, mock_event_bus):
        """Test sending message to non-existent agent."""
        from fastapi import HTTPException

        from forge_harness.webhook_server.api.agents import AgentMessageRequest, send_agent_message

        mock_agent_registry.get.return_value = None

        request_body = AgentMessageRequest(message="Run tests")
        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = None

        with patch('forge_harness.webhook_server.api.agents.get_agent_registry') as mock_reg:
            mock_reg.return_value = mock_agent_registry

            with pytest.raises(HTTPException) as exc_info:
                await send_agent_message("non-existent", request_body, mock_request)
            assert exc_info.value.status_code == 404
            assert "Agent not found" in str(exc_info.value.detail)


class TestAgentsAPIModels:
    """Test Pydantic models for agents API."""

    def test_agent_register_request_validation(self):
        """Test AgentRegisterRequest validation."""
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        # Valid request
        request = AgentRegisterRequest(
            role="backend-engineer",
            project="test-project",
            task="Implement feature"
        )
        assert request.role == "backend-engineer"
        assert request.project == "test-project"
        assert request.task == "Implement feature"

        # With optional fields
        request = AgentRegisterRequest(
            role="frontend",
            project="web-app",
            task="Build UI",
            name="Web Agent",
            domain="example-com",
            tmux_session="forge:web",
            skills=["react", "typescript"]
        )
        assert request.name == "Web Agent"
        assert request.domain == "example-com"
        assert request.tmux_session == "forge:web"
        assert request.skills == ["react", "typescript"]

    def test_agent_register_request_empty_validation(self):
        """Test that empty role/project/task raises validation error."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.agents import AgentRegisterRequest

        with pytest.raises(ValidationError):
            AgentRegisterRequest(
                role="",
                project="test",
                task="test"
            )

        with pytest.raises(ValidationError):
            AgentRegisterRequest(
                role="test",
                project="",
                task="test"
            )

    def test_agent_progress_request(self):
        """Test AgentProgressRequest model."""
        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        request = AgentProgressRequest(
            progress=50,
            current_task="Working on feature",
            files_modified=["file1.py", "file2.py"]
        )
        assert request.progress == 50
        assert request.current_task == "Working on feature"
        assert len(request.files_modified) == 2

    def test_agent_progress_request_bounds(self):
        """Test progress bounds validation."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.agents import AgentProgressRequest

        # Progress below 0 should fail
        with pytest.raises(ValidationError):
            AgentProgressRequest(progress=-1)

        # Progress above 100 should fail
        with pytest.raises(ValidationError):
            AgentProgressRequest(progress=101)

        # Valid progress
        request = AgentProgressRequest(progress=0)
        assert request.progress == 0

        request = AgentProgressRequest(progress=100)
        assert request.progress == 100

    def test_agent_pause_request(self):
        """Test AgentPauseRequest model."""
        from forge_harness.webhook_server.api.agents import AgentPauseRequest

        # Default values
        request = AgentPauseRequest()
        assert request.reason is None
        assert request.duration_minutes == 30

        # Custom values
        request = AgentPauseRequest(reason="Testing", duration_minutes=60)
        assert request.reason == "Testing"
        assert request.duration_minutes == 60

    def test_agent_pause_request_bounds(self):
        """Test pause duration bounds validation."""
        from pydantic import ValidationError

        from forge_harness.webhook_server.api.agents import AgentPauseRequest

        # Duration below 5 should fail
        with pytest.raises(ValidationError):
            AgentPauseRequest(duration_minutes=4)

        # Duration above 120 should fail
        with pytest.raises(ValidationError):
            AgentPauseRequest(duration_minutes=121)

        # Valid bounds
        request = AgentPauseRequest(duration_minutes=5)
        assert request.duration_minutes == 5

        request = AgentPauseRequest(duration_minutes=120)
        assert request.duration_minutes == 120


class TestAgentsAPIResponse:
    """Test API response formatting."""

    def test_api_response_success(self):
        """Test successful API response format."""
        from forge_harness.webhook_server.api.agents import api_response

        result = api_response(data={"key": "value"})

        assert result["success"] is True
        assert result["data"] == {"key": "value"}
        assert result["error"] is None
        assert "timestamp" in result

    def test_api_response_error(self):
        """Test error API response format."""
        from forge_harness.webhook_server.api.agents import api_response

        result = api_response(
            error_code="NOT_FOUND",
            error_message="Agent not found"
        )

        assert result["success"] is False
        assert result["data"] is None
        assert result["error"]["code"] == "NOT_FOUND"
        assert result["error"]["message"] == "Agent not found"
        assert "timestamp" in result

    def test_normalize_agent_dict(self):
        """Test agent dictionary normalization."""
        from forge_harness.webhook_server.api.agents import normalize_agent_dict

        # Test with id -> session_id normalization
        agent = {"id": "agent-123", "role": "test", "project": "proj"}
        normalized = normalize_agent_dict(agent)
        assert "session_id" in normalized
        assert "id" not in normalized

        # Test with missing optional fields
        agent = {"session_id": "agent-123"}
        normalized = normalize_agent_dict(agent)
        assert normalized["agent_role"] == ""
        assert normalized["project"] == ""
