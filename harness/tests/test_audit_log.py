"""
Tests for Audit Log - Control action event emission

Asserts that dispatch, pause, resume, complete emit audit events.
Per docs/research/ARCHITECTURE_STRATEGY_TDD_IMPLEMENTATION_PLAN.md R5c.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_audit_logger():
    """Mock audit logger that captures log calls."""
    logger = AsyncMock()
    return logger


@pytest.fixture
def app_with_mock_audit():
    """Create app with mocked audit logger."""
    with patch("forge_harness.webhook_server.api.agents.get_audit_logger") as mock_get:
        mock_logger = AsyncMock()
        mock_get.return_value = mock_logger

        from forge_harness.webhook_server import AuthConfig, create_app

        app = create_app(auth_config=AuthConfig(require_auth=False))
        yield app, mock_logger


class TestAuditLogControlActions:
    """Assert dispatch, pause, resume, complete emit audit events."""

    @pytest.mark.asyncio
    async def test_complete_emits_audit_event(self):
        """POST /api/agents/{id}/complete should call audit_logger.log with action=complete."""
        from unittest.mock import Mock

        from forge_harness.webhook_server.api.agents import complete_agent

        mock_registry = Mock()
        mock_registry.complete.return_value = True
        mock_bus = AsyncMock()
        mock_audit = AsyncMock()

        with patch("forge_harness.webhook_server.api.agents.get_agent_registry", AsyncMock(return_value=mock_registry)), \
             patch("forge_harness.webhook_server.api.agents.get_event_bus", AsyncMock(return_value=mock_bus)), \
             patch("forge_harness.webhook_server.api.agents.get_audit_logger", return_value=mock_audit):

            from fastapi.responses import JSONResponse

            resp = await complete_agent("agent-001")
            assert isinstance(resp, JSONResponse)
            mock_audit.log.assert_called_once()
            call_kw = mock_audit.log.call_args[1]
            assert call_kw["action"] == "complete"
            assert call_kw.get("target", {}).get("id") == "agent-001"
            assert call_kw.get("source") == "webhook_api"

    @pytest.mark.asyncio
    async def test_pause_emits_audit_event(self):
        """POST /api/agents/{id}/pause should call audit_logger.log with action=pause."""
        from unittest.mock import Mock

        from forge_harness.webhook_server.api.agents import AgentPauseRequest, pause_agent

        mock_registry = Mock()
        mock_registry.pause.return_value = True
        mock_audit = AsyncMock()

        with patch("forge_harness.webhook_server.api.agents.get_agent_registry", AsyncMock(return_value=mock_registry)), \
             patch("forge_harness.webhook_server.api.agents.get_audit_logger", return_value=mock_audit):

            body = AgentPauseRequest(reason="test", duration_minutes=30)
            resp = await pause_agent("agent-001", body)
            mock_audit.log.assert_called_once()
            call_kw = mock_audit.log.call_args[1]
            assert call_kw["action"] == "pause"

    @pytest.mark.asyncio
    async def test_resume_emits_audit_event(self):
        """POST /api/agents/{id}/resume should call audit_logger.log with action=resume."""
        from unittest.mock import Mock

        from forge_harness.webhook_server.api.agents import resume_agent

        mock_registry = Mock()
        mock_registry.resume.return_value = True
        mock_audit = AsyncMock()

        with patch("forge_harness.webhook_server.api.agents.get_agent_registry", AsyncMock(return_value=mock_registry)), \
             patch("forge_harness.webhook_server.api.agents.get_audit_logger", return_value=mock_audit):

            resp = await resume_agent("agent-001")
            mock_audit.log.assert_called_once()
            call_kw = mock_audit.log.call_args[1]
            assert call_kw["action"] == "resume"

    @pytest.mark.asyncio
    async def test_dispatch_emits_audit_event(self):
        """Node dispatch should call audit_logger.log with action=dispatch."""
        import os
        from unittest.mock import Mock

        from forge_harness.webhook_server.api.agents import NodeDispatchRequest, dispatch_to_node

        mock_registry = Mock()
        mock_registry.list_active.return_value = [
            type("A", (), {"id": "a1", "to_dict": lambda self: {"id": "a1", "status": "idle"}})()
        ]
        mock_audit = AsyncMock()

        with patch.dict(os.environ, {"FORGE_NODE_ID": "local-node"}, clear=False), \
             patch("forge_harness.webhook_server.api.agents.get_agent_registry", AsyncMock(return_value=mock_registry)), \
             patch("forge_harness.webhook_server.api.agents.get_audit_logger", return_value=mock_audit), \
             patch("forge_harness.webhook_server.api.agents.get_event_bus", AsyncMock()):

            body = NodeDispatchRequest(agent_type="cursor", task="test task")
            resp = await dispatch_to_node("local-node", body)
            mock_audit.log.assert_called_once()
            call_kw = mock_audit.log.call_args[1]
            assert call_kw["action"] == "dispatch"


class TestAuditLogService:
    """Tests for AuditLogger and MemoryAuditBackend."""

    @pytest.mark.asyncio
    async def test_memory_backend_captures_events(self):
        """MemoryAuditBackend should append events."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        backend = MemoryAuditBackend()
        logger = AuditLogger(backend=backend)

        await logger.log(action="test", actor={"id": "sys"}, target={"id": "t1"})
        assert len(backend.events) == 1
        assert backend.events[0]["action"] == "test"
        assert "timestamp" in backend.events[0]
        assert "event_id" in backend.events[0]

    @pytest.mark.asyncio
    async def test_log_includes_source_when_provided(self):
        """AuditLogger should include source in event when provided."""
        from forge_harness.webhook_server.services.audit import AuditLogger, MemoryAuditBackend

        backend = MemoryAuditBackend()
        logger = AuditLogger(backend=backend)
        await logger.log(
            action="dispatch",
            actor={"id": "cli", "type": "system"},
            target={"id": "t1", "type": "task"},
            source="cli",
        )
        assert len(backend.events) == 1
        assert backend.events[0]["source"] == "cli"
        assert backend.events[0]["action"] == "dispatch"
