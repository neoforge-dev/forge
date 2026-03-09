"""
Tests for Comprehensive Audit Logging

Tests audit logging for sensitive operations:
- Agent registration/deregistration
- Task creation/deletion/assignment
- Approval decisions
- Handoff operations
- Fleet control actions
- Configuration changes
- Authentication events
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import Request


@pytest.fixture
def mock_audit_logger():
    """Mock audit logger that captures log calls."""
    logger = AsyncMock()
    return logger


@pytest.fixture
def mock_request():
    """Mock FastAPI Request with IP and user agent."""
    request = Mock(spec=Request)
    request.client = Mock()
    request.client.host = "192.168.1.100"
    request.headers = {"User-Agent": "forge-cli/2.0"}
    return request


class TestAuditLoggerConvenienceMethods:
    """Test convenience methods on AuditLogger."""

    @pytest.mark.asyncio
    async def test_log_agent_registration(self, mock_audit_logger):
        """Test log_agent_registration convenience method."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        logger = AuditLogger(backend=MemoryAuditBackend())
        await logger.log_agent_registration(
            agent_id="agent-001",
            actor={"id": "api", "type": "system"},
            context={"role": "backend-engineer"},
            ip_address="192.168.1.100",
            user_agent="forge-cli/2.0",
        )

        assert len(logger._backend.events) == 1
        event = logger._backend.events[0]
        assert event["action"] == "agent_registration"
        assert event["target"]["id"] == "agent-001"
        assert event["ip_address"] == "192.168.1.100"
        assert event["user_agent"] == "forge-cli/2.0"

    @pytest.mark.asyncio
    async def test_log_task_assignment(self, mock_audit_logger):
        """Test log_task_assignment convenience method."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        logger = AuditLogger(backend=MemoryAuditBackend())
        await logger.log_task_assignment(
            task_id="task-001",
            agent_id="agent-001",
            actor={"id": "api", "type": "system"},
            ip_address="192.168.1.100",
            user_agent="forge-cli/2.0",
        )

        assert len(logger._backend.events) == 1
        event = logger._backend.events[0]
        assert event["action"] == "task_assignment"
        assert event["target"]["id"] == "task-001"
        assert event["context"]["agent_id"] == "agent-001"

    @pytest.mark.asyncio
    async def test_log_approval_decision(self, mock_audit_logger):
        """Test log_approval_decision convenience method."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        logger = AuditLogger(backend=MemoryAuditBackend())
        await logger.log_approval_decision(
            approval_id="approval-001",
            decision="approved",
            actor={"id": "user-001", "type": "human"},
            context={"comment": "Looks good"},
            ip_address="192.168.1.100",
            user_agent="forge-cli/2.0",
        )

        assert len(logger._backend.events) == 1
        event = logger._backend.events[0]
        assert event["action"] == "approval_approve"
        assert event["target"]["id"] == "approval-001"
        assert event["context"]["decision"] == "approved"

    @pytest.mark.asyncio
    async def test_log_handoff_operation(self, mock_audit_logger):
        """Test log_handoff_operation convenience method."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        logger = AuditLogger(backend=MemoryAuditBackend())
        await logger.log_handoff_operation(
            handoff_id="handoff-001",
            actor={"id": "api", "type": "system"},
            context={"from_agent": "agent-001", "to_agent": "agent-002"},
            ip_address="192.168.1.100",
            user_agent="forge-cli/2.0",
        )

        assert len(logger._backend.events) == 1
        event = logger._backend.events[0]
        assert event["action"] == "handoff_operation"
        assert event["target"]["id"] == "handoff-001"

    @pytest.mark.asyncio
    async def test_log_fleet_control(self, mock_audit_logger):
        """Test log_fleet_control convenience method."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        logger = AuditLogger(backend=MemoryAuditBackend())
        await logger.log_fleet_control(
            action_type="pause",
            actor={"id": "api", "type": "system"},
            target_id="agent-001",
            context={"reason": "maintenance"},
            ip_address="192.168.1.100",
            user_agent="forge-cli/2.0",
        )

        assert len(logger._backend.events) == 1
        event = logger._backend.events[0]
        assert event["action"] == "fleet_pause"
        assert event["target"]["id"] == "agent-001"

    @pytest.mark.asyncio
    async def test_log_configuration_change(self, mock_audit_logger):
        """Test log_configuration_change convenience method."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        logger = AuditLogger(backend=MemoryAuditBackend())
        await logger.log_configuration_change(
            config_key="llm",
            actor={"id": "api", "type": "system"},
            context={"changes": {"model": "claude-sonnet-4"}},
            ip_address="192.168.1.100",
            user_agent="forge-cli/2.0",
        )

        assert len(logger._backend.events) == 1
        event = logger._backend.events[0]
        assert event["action"] == "configuration_change"
        assert event["target"]["id"] == "llm"

    @pytest.mark.asyncio
    async def test_log_authentication_event(self, mock_audit_logger):
        """Test log_authentication_event convenience method."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        logger = AuditLogger(backend=MemoryAuditBackend())
        await logger.log_authentication_event(
            event_type="refresh",
            actor={"id": "user-001", "type": "user"},
            context={"token_id": "token-001"},
            ip_address="192.168.1.100",
            user_agent="forge-cli/2.0",
        )

        assert len(logger._backend.events) == 1
        event = logger._backend.events[0]
        assert event["action"] == "auth_refresh"
        assert event["target"]["id"] == "user-001"


class TestAuditLoggingEndpoints:
    """Test audit logging integration in API endpoints."""

    @pytest.mark.asyncio
    async def test_register_agent_logs_audit(self, mock_request):
        """Test that register_agent logs audit event."""
        from forge_harness.webhook_server.api.agents import AgentRegisterRequest, register_agent

        mock_registry = Mock()
        mock_agent = Mock()
        mock_agent.id = "agent-001"
        mock_agent.to_dict.return_value = {"id": "agent-001", "role": "backend-engineer"}
        mock_registry.register.return_value = mock_agent
        mock_audit = AsyncMock()

        with patch("forge_harness.webhook_server.api.agents.get_agent_registry", AsyncMock(return_value=mock_registry)), \
             patch("forge_harness.webhook_server.api.agents.get_state_store", AsyncMock(return_value=Mock(is_connected=lambda: False))), \
             patch("forge_harness.webhook_server.api.agents.get_event_bus", AsyncMock(return_value=AsyncMock())), \
             patch("forge_harness.webhook_server.api.agents.get_audit_logger", return_value=mock_audit), \
             patch("forge_harness.webhook_server.api.agents.verify_auth", return_value=None):

            body = AgentRegisterRequest(
                role="backend-engineer",
                project="interview-simulator",
                task="Implement feature",
            )
            await register_agent(body, mock_request, None)

            mock_audit.log_agent_registration.assert_called_once()
            call_kw = mock_audit.log_agent_registration.call_args[1]
            assert call_kw["agent_id"] == "agent-001"
            assert call_kw["ip_address"] == "192.168.1.100"
            assert call_kw["user_agent"] == "forge-cli/2.0"

    @pytest.mark.asyncio
    async def test_create_task_logs_audit(self, mock_request):
        """Test that create_task logs audit event."""
        from forge_harness.webhook_server.api.tasks import CreateTaskRequest, create_task

        mock_handler = AsyncMock()
        mock_handler.create_task.return_value = {"id": "task-001", "subject": "Test task"}
        mock_audit = AsyncMock()

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler", AsyncMock(return_value=mock_handler)), \
             patch("forge_harness.webhook_server.api.tasks.get_event_bus", AsyncMock(return_value=AsyncMock())), \
             patch("forge_harness.webhook_server.api.tasks.get_audit_logger", return_value=mock_audit), \
             patch("forge_harness.webhook_server.api.tasks.verify_auth", return_value=None):

            body = CreateTaskRequest(
                subject="Test task",
                description="Test description",
                priority="high",
            )
            await create_task(body, mock_request, None)

            mock_audit.log_task_creation.assert_called_once()
            call_kw = mock_audit.log_task_creation.call_args[1]
            assert call_kw["task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_delete_task_logs_audit(self, mock_request):
        """Test that delete_task logs audit event."""
        from forge_harness.webhook_server.api.tasks import delete_task

        mock_handler = AsyncMock()
        mock_handler.delete_task.return_value = True
        mock_audit = AsyncMock()

        with patch("forge_harness.webhook_server.api.tasks.get_task_handler", AsyncMock(return_value=mock_handler)), \
             patch("forge_harness.webhook_server.api.tasks.get_event_bus", AsyncMock(return_value=AsyncMock())), \
             patch("forge_harness.webhook_server.api.tasks.get_audit_logger", return_value=mock_audit), \
             patch("forge_harness.webhook_server.api.tasks.verify_auth", return_value=None):

            await delete_task("task-001", mock_request, None)

            mock_audit.log_task_deletion.assert_called_once()
            call_kw = mock_audit.log_task_deletion.call_args[1]
            assert call_kw["task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_approve_request_logs_audit(self, mock_request):
        """Test that approve_request logs audit event."""
        from forge_harness.webhook_server.api.approvals import ApproveRequest, approve_request

        mock_handler = AsyncMock()
        mock_handler.approve_request.return_value = {"success": True}
        mock_audit = AsyncMock()

        with patch("forge_harness.webhook_server.api.approvals.get_approval_handler", AsyncMock(return_value=mock_handler)), \
             patch("forge_harness.webhook_server.api.approvals.get_event_bus", AsyncMock(return_value=AsyncMock())), \
             patch("forge_harness.webhook_server.api.approvals.get_audit_logger", return_value=mock_audit), \
             patch("forge_harness.webhook_server.api.approvals.verify_auth", return_value=None):

            body = ApproveRequest(approver="user-001", comment="Approved")
            await approve_request("approval-001", body, mock_request, None)

            mock_audit.log_approval_decision.assert_called_once()
            call_kw = mock_audit.log_approval_decision.call_args[1]
            assert call_kw["approval_id"] == "approval-001"
            assert call_kw["decision"] == "approved"

    @pytest.mark.asyncio
    async def test_create_handoff_logs_audit(self, mock_request):
        """Test that create_handoff logs audit event."""
        from forge_harness.webhook_server.api.handoffs import HandoffRequest, create_handoff

        mock_handler = AsyncMock()
        mock_handler.create_handoff.return_value = {"id": "handoff-001", "from_agent": "agent-001"}
        mock_audit = AsyncMock()

        with patch("forge_harness.webhook_server.api.handoffs.get_handoff_handler", AsyncMock(return_value=mock_handler)), \
             patch("forge_harness.webhook_server.api.handoffs.get_event_bus", AsyncMock(return_value=AsyncMock())), \
             patch("forge_harness.webhook_server.api.handoffs.get_audit_logger", return_value=mock_audit), \
             patch("forge_harness.webhook_server.api.handoffs.verify_auth", return_value=None):

            body = HandoffRequest(
                from_agent="agent-001",
                to_agent="agent-002",
                task_description="Transfer work",
            )
            await create_handoff(body, mock_request, None)

            mock_audit.log_handoff_operation.assert_called_once()
            call_kw = mock_audit.log_handoff_operation.call_args[1]
            assert "handoff-001" in str(call_kw["handoff_id"])
