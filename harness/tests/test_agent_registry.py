"""
Agent Registry Service Tests
===========================

Tests for AgentRegistry and AgentSession classes.
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from forge_harness.webhook_server.services.agent_registry import (
    AgentRegistry,
    AgentSession,
    get_agent_registry,
)


class TestAgentSession:
    """Test class for AgentSession dataclass."""

    def test_agent_session_creation(self):
        """Test creating an AgentSession."""
        agent = AgentSession(
            id="test-123",
            role="backend-engineer",
            project="test-project",
            task="Implement feature",
        )

        assert agent.id == "test-123"
        assert agent.role == "backend-engineer"
        assert agent.project == "test-project"
        assert agent.task == "Implement feature"
        assert agent.status == "active"
        assert agent.progress == 0

    def test_agent_session_to_dict(self):
        """Test AgentSession.to_dict() method."""
        agent = AgentSession(
            id="test-456",
            role="frontend-dev",
            project="web-app",
            task="Build UI",
            name="Frontend Agent",
            domain="codeswiftr-com",
        )

        d = agent.to_dict()

        assert d["id"] == "test-456"
        assert d["role"] == "frontend-dev"
        assert d["project"] == "web-app"
        assert d["name"] == "Frontend Agent"
        assert d["domain"] == "codeswiftr-com"
        assert d["status"] == "active"
        assert "registered_at" in d
        assert "last_activity" in d

    def test_is_expired(self):
        """Test agent expiration check."""
        agent = AgentSession(
            id="test-789",
            role="test",
            project="test",
            task="test",
        )

        # Should not be expired (just created)
        assert not agent.is_expired(timeout_seconds=300)

    def test_is_expired_old_agent(self):
        """Test agent expiration with old timestamp."""
        agent = AgentSession(
            id="test-000",
            role="test",
            project="test",
            task="test",
            last_activity=datetime.now(UTC) - timedelta(seconds=400),
        )

        # Should be expired (400 seconds ago > 300 second timeout)
        assert agent.is_expired(timeout_seconds=300)

    def test_is_stale(self):
        """Test stale agent detection."""
        agent = AgentSession(
            id="test-111",
            role="test",
            project="test",
            task="test",
        )

        # Should not be stale (just created)
        assert not agent.is_stale(heartbeat_timeout_seconds=120)

    def test_is_stale_old_agent(self):
        """Test stale detection with old heartbeat."""
        agent = AgentSession(
            id="test-222",
            role="test",
            project="test",
            task="test",
            last_activity=datetime.now(UTC) - timedelta(seconds=150),
        )

        # Should be stale (150 seconds ago > 120 second timeout)
        assert agent.is_stale(heartbeat_timeout_seconds=120)


class TestAgentRegistry:
    """Test class for AgentRegistry."""

    @pytest.fixture
    def registry(self):
        """Create a fresh AgentRegistry for each test."""
        return AgentRegistry(expiry_seconds=300)

    def test_register_agent(self, registry):
        """Test registering a new agent."""
        agent = registry.register(
            role="backend-engineer",
            project="interview-simulator",
            task="Implement auth",
            name="Backend Agent",
            domain="codeswiftr-com",
        )

        assert agent is not None
        assert agent.role == "backend-engineer"
        assert agent.project == "interview-simulator"
        assert agent.name == "Backend Agent"
        assert agent.domain == "codeswiftr-com"

    def test_get_agent(self, registry):
        """Test getting an agent by ID."""
        created = registry.register(
            role="test",
            project="test",
            task="test",
        )
        agent_id = created.id

        retrieved = registry.get(agent_id)

        assert retrieved is not None
        assert retrieved.id == agent_id

    def test_get_agent_not_found(self, registry):
        """Test getting non-existent agent returns None."""
        result = registry.get("non-existent-id")
        assert result is None

    def test_list_active(self, registry):
        """Test listing all active agents."""
        registry.register(role="a", project="p", task="t", name="Agent A")
        registry.register(role="b", project="p", task="t", name="Agent B")
        registry.register(role="c", project="p", task="t", name="Agent C")

        agents = registry.list_active()

        assert len(agents) == 3

    def test_update_progress(self, registry):
        """Test updating agent progress."""
        agent = registry.register(role="dev", project="p", task="t")
        agent_id = agent.id

        updated = registry.update_progress(
            agent_id=agent_id,
            progress=50,
            current_task="Working on feature X",
            files_modified=["file1.py", "file2.py"],
        )

        assert updated is not None
        assert updated.progress == 50
        assert updated.current_task == "Working on feature X"

    def test_update_progress_not_found(self, registry):
        """Test updating non-existent agent returns None."""
        result = registry.update_progress(
            agent_id="non-existent",
            progress=75,
        )
        assert result is None

    def test_complete_agent(self, registry):
        """Test marking agent as completed."""
        agent = registry.register(role="dev", project="p", task="t")
        agent_id = agent.id

        completed = registry.complete(agent_id, summary="All done!")

        assert completed is not None
        assert completed.status == "completed"
        assert completed.progress == 100

    def test_pause_agent(self, registry):
        """Test pausing an agent."""
        agent = registry.register(role="dev", project="p", task="t")
        agent_id = agent.id

        result, prev = registry.pause(agent_id)

        assert result is not None
        assert result.status == "paused"
        assert prev == "active"

    def test_resume_agent(self, registry):
        """Test resuming a paused agent."""
        agent = registry.register(role="dev", project="p", task="t")
        agent_id = agent.id

        # Pause first
        registry.pause(agent_id)

        # Then resume
        result, prev = registry.resume(agent_id)

        assert result is not None
        assert result.status == "active"
        assert prev == "paused"

    def test_kill_agent(self, registry):
        """Test killing an agent."""
        agent = registry.register(role="dev", project="p", task="t")
        agent_id = agent.id

        result, prev = registry.kill(agent_id, reason="Memory limit exceeded")

        assert result is not None
        assert result.status == "failed"
        assert len(result.messages) == 1
        assert result.messages[0]["type"] == "kill"

    def test_send_message(self, registry):
        """Test sending a message to an agent."""
        agent = registry.register(role="dev", project="p", task="t")
        agent_id = agent.id

        result, msg_id = registry.send_message(
            agent_id=agent_id,
            message={"type": "instruction", "content": "Fix the bug"},
            use_queue=False,
        )

        assert result is not None
        assert len(result.messages) == 1
        assert result.messages[0]["content"] == "Fix the bug"

    def test_broadcast(self, registry):
        """Test broadcasting message to all agents."""
        registry.register(role="a", project="p", task="t")
        registry.register(role="b", project="p", task="t")

        count = registry.broadcast({"type": "system", "content": "Rebooting soon"})

        assert count == 2


class TestGetAgentRegistry:
    """Test class for get_agent_registry singleton."""

    def test_get_agent_registry_singleton(self):
        """Test that get_agent_registry returns singleton."""
        # Reset the global registry
        import forge_harness.webhook_server.services.agent_registry as ar_module

        original = ar_module._agent_registry
        ar_module._agent_registry = None

        try:
            registry1 = get_agent_registry()
            registry2 = get_agent_registry()

            # Should be the same instance
            assert registry1 is registry2
        finally:
            # Restore
            ar_module._agent_registry = original
