"""Tests for AgentRegistry service.

Tests agent session management and lifecycle.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.services.agent_registry import (
    AgentRegistry,
    AgentSession,
    get_agent_registry,
)


class TestAgentSession:
    """Tests for AgentSession dataclass."""

    def test_create_session_defaults(self):
        """Test creating session with defaults."""
        session = AgentSession(
            id="agent-1",
            role="feature-dev",
            project="interview-simulator",
            task="Implement auth",
        )
        assert session.id == "agent-1"
        assert session.role == "feature-dev"
        assert session.project == "interview-simulator"
        assert session.task == "Implement auth"
        assert session.status == "active"
        assert session.progress == 0
        assert session.name is None
        assert session.domain is None
        assert session.parent_id is None
        assert session.children == []
        assert session.skills == []
        assert session.files_modified == []
        assert session.messages == []

    def test_create_session_full(self):
        """Test creating session with all fields."""
        session = AgentSession(
            id="agent-2",
            role="debug",
            project="voice-coach",
            task="Fix bug",
            name="debugger-1",
            domain="brandfocus-ai",
            parent_id="parent-123",
            children=["child-1", "child-2"],
            tmux_session="forge:debug",
            skills=["debug", "test"],
            status="waiting",
            progress=50,
            current_task="Analyzing stacktrace",
            files_modified=["app.py", "tests.py"],
            token_usage={"input": 1000, "output": 500},
        )
        assert session.name == "debugger-1"
        assert session.domain == "brandfocus-ai"
        assert session.parent_id == "parent-123"
        assert session.children == ["child-1", "child-2"]
        assert session.tmux_session == "forge:debug"
        assert session.skills == ["debug", "test"]
        assert session.status == "waiting"
        assert session.progress == 50

    def test_to_dict(self):
        """Test converting session to dictionary."""
        session = AgentSession(
            id="agent-3",
            role="review",
            project="test-project",
            task="Review code",
            name="reviewer",
            progress=75,
        )
        session.messages.append({"type": "info", "content": "Starting"})

        result = session.to_dict()

        assert result["id"] == "agent-3"
        assert result["role"] == "review"
        assert result["project"] == "test-project"
        assert result["task"] == "Review code"
        assert result["name"] == "reviewer"
        assert result["progress"] == 75
        assert result["messages_count"] == 1
        assert "registered_at" in result
        assert "last_activity" in result
        assert "is_stale" in result

    def test_is_expired_false(self):
        """Test is_expired returns False for recent activity."""
        session = AgentSession(
            id="test",
            role="test",
            project="test",
            task="test",
            last_activity=datetime.now(UTC),
        )
        assert session.is_expired(timeout_seconds=300) is False

    def test_is_expired_true(self):
        """Test is_expired returns True for old activity."""
        session = AgentSession(
            id="test",
            role="test",
            project="test",
            task="test",
            last_activity=datetime.now(UTC) - timedelta(minutes=10),
        )
        assert session.is_expired(timeout_seconds=300) is True

    def test_is_stale_false(self):
        """Test is_stale returns False for recent activity."""
        session = AgentSession(
            id="test",
            role="test",
            project="test",
            task="test",
            last_activity=datetime.now(UTC),
        )
        assert session.is_stale(heartbeat_timeout_seconds=120) is False

    def test_is_stale_true(self):
        """Test is_stale returns True for old activity."""
        session = AgentSession(
            id="test",
            role="test",
            project="test",
            task="test",
            last_activity=datetime.now(UTC) - timedelta(minutes=5),
        )
        assert session.is_stale(heartbeat_timeout_seconds=120) is True


class TestAgentRegistry:
    """Tests for AgentRegistry class."""

    @pytest.fixture
    def registry(self):
        """Create fresh registry."""
        return AgentRegistry(expiry_seconds=300)

    def test_init(self, registry):
        """Test registry initialization."""
        assert registry._agents == {}
        assert registry._expiry_seconds == 300

    def test_register_basic(self, registry):
        """Test basic agent registration."""
        agent = registry.register(
            role="feature-dev",
            project="test-project",
            task="Implement feature",
        )

        assert agent.id is not None
        assert len(agent.id) == 8
        assert agent.role == "feature-dev"
        assert agent.project == "test-project"
        assert agent.task == "Implement feature"
        assert agent.status == "active"

    def test_register_with_all_options(self, registry):
        """Test registration with all options."""
        agent = registry.register(
            role="debug",
            project="my-project",
            task="Fix bug",
            name="debugger",
            domain="codeswiftr-com",
            tmux_session="forge:debug",
            skills=["debug", "trace"],
        )

        assert agent.name == "debugger"
        assert agent.domain == "codeswiftr-com"
        assert agent.tmux_session == "forge:debug"
        assert agent.skills == ["debug", "trace"]

    def test_register_with_parent(self, registry):
        """Test registration with parent agent."""
        parent = registry.register(
            role="orchestrator",
            project="project",
            task="Orchestrate",
        )

        child = registry.register(
            role="worker",
            project="project",
            task="Work",
            parent_id=parent.id,
        )

        assert child.parent_id == parent.id
        # Parent should have child in children list
        updated_parent = registry.get(parent.id)
        assert child.id in updated_parent.children

    def test_get_by_id(self, registry):
        """Test getting agent by ID."""
        agent = registry.register("role", "project", "task")

        result = registry.get(agent.id)

        assert result is not None
        assert result.id == agent.id

    def test_get_by_tmux_session(self, registry):
        """Test getting agent by tmux session."""
        agent = registry.register(
            role="test",
            project="project",
            task="task",
            tmux_session="forge:opencode",
        )

        result = registry.get("forge:opencode")

        assert result is not None
        assert result.id == agent.id

    def test_get_by_name(self, registry):
        """Test getting agent by name (case-insensitive)."""
        agent = registry.register(
            role="test",
            project="project",
            task="task",
            name="MyAgent",
        )

        result = registry.get("myagent")

        assert result is not None
        assert result.id == agent.id

    def test_get_not_found(self, registry):
        """Test getting non-existent agent."""
        result = registry.get("nonexistent")
        assert result is None

    def test_get_expired_agent_returns_none(self, registry):
        """Test that expired agents are removed on get."""
        registry._expiry_seconds = 1
        agent = registry.register("role", "project", "task")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)

        result = registry.get(agent.id)

        assert result is None
        assert agent.id not in registry._agents

    def test_get_expired_agent_by_tmux_session(self, registry):
        """Test that expired agents are removed when looked up by tmux session."""
        registry._expiry_seconds = 1
        agent = registry.register(
            role="test",
            project="project",
            task="task",
            tmux_session="forge:expired",
        )
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)

        result = registry.get("forge:expired")

        assert result is None
        assert agent.id not in registry._agents

    def test_get_expired_agent_by_name(self, registry):
        """Test that expired agents are removed when looked up by name."""
        registry._expiry_seconds = 1
        agent = registry.register(
            role="test",
            project="project",
            task="task",
            name="ExpiredAgent",
        )
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)

        result = registry.get("expiredagent")

        assert result is None
        assert agent.id not in registry._agents

    def test_list_active(self, registry):
        """Test listing active agents."""
        registry.register("role1", "project1", "task1")
        registry.register("role2", "project2", "task2")
        registry.register("role3", "project3", "task3")

        agents = registry.list_active()

        assert len(agents) == 3

    def test_list_active_excludes_expired(self, registry):
        """Test that list_active excludes expired agents."""
        registry._expiry_seconds = 1
        agent1 = registry.register("role1", "project1", "task1")
        registry.register("role2", "project2", "task2")

        # Expire agent1
        agent1.last_activity = datetime.now(UTC) - timedelta(seconds=10)

        agents = registry.list_active()

        assert len(agents) == 1

    def test_update_progress(self, registry):
        """Test updating agent progress."""
        agent = registry.register("role", "project", "task")

        result = registry.update_progress(
            agent.id,
            progress=50,
            current_task="Working on step 2",
        )

        assert result is not None
        assert result.progress == 50
        assert result.current_task == "Working on step 2"

    def test_update_progress_files_modified(self, registry):
        """Test that files_modified are appended."""
        agent = registry.register("role", "project", "task")

        registry.update_progress(agent.id, progress=25, files_modified=["a.py", "b.py"])
        registry.update_progress(agent.id, progress=50, files_modified=["b.py", "c.py"])

        result = registry.get(agent.id)
        # Should have a.py, b.py, c.py (no duplicates)
        assert set(result.files_modified) == {"a.py", "b.py", "c.py"}

    def test_update_progress_token_usage(self, registry):
        """Test that token_usage is accumulated."""
        agent = registry.register("role", "project", "task")

        registry.update_progress(agent.id, progress=25, token_usage={"input": 100, "output": 50})
        registry.update_progress(agent.id, progress=50, token_usage={"input": 200, "output": 100})

        result = registry.get(agent.id)
        assert result.token_usage["input"] == 300
        assert result.token_usage["output"] == 150

    def test_update_progress_not_found(self, registry):
        """Test updating non-existent agent."""
        result = registry.update_progress("nonexistent", progress=50)
        assert result is None

    def test_complete(self, registry):
        """Test marking agent as completed."""
        agent = registry.register("role", "project", "task")

        result = registry.complete(agent.id, summary="Done!")

        assert result is not None
        assert result.status == "completed"
        assert result.progress == 100
        assert len(result.messages) == 1
        assert result.messages[0]["type"] == "completion"
        assert result.messages[0]["content"] == "Done!"

    def test_complete_no_summary(self, registry):
        """Test completing without summary."""
        agent = registry.register("role", "project", "task")

        result = registry.complete(agent.id)

        assert result.status == "completed"
        assert result.progress == 100
        assert len(result.messages) == 0

    def test_complete_not_found(self, registry):
        """Test completing non-existent agent."""
        result = registry.complete("nonexistent")
        assert result is None

    def test_pause(self, registry):
        """Test pausing agent."""
        agent = registry.register("role", "project", "task")
        assert agent.status == "active"

        result, prev_status = registry.pause(agent.id)

        assert result is not None
        assert result.status == "paused"
        assert prev_status == "active"

    def test_pause_not_found(self, registry):
        """Test pausing non-existent agent."""
        result, prev_status = registry.pause("nonexistent")
        assert result is None
        assert prev_status is None

    def test_resume(self, registry):
        """Test resuming agent."""
        agent = registry.register("role", "project", "task")
        registry.pause(agent.id)

        result, prev_status = registry.resume(agent.id)

        assert result is not None
        assert result.status == "active"
        assert prev_status == "paused"

    def test_resume_not_found(self, registry):
        """Test resuming non-existent agent."""
        result, prev_status = registry.resume("nonexistent")
        assert result is None
        assert prev_status is None

    def test_kill(self, registry):
        """Test killing agent."""
        agent = registry.register("role", "project", "task")

        result, prev_status = registry.kill(agent.id, reason="Out of tokens")

        assert result is not None
        assert result.status == "failed"
        assert prev_status == "active"
        assert len(result.messages) == 1
        assert result.messages[0]["type"] == "kill"
        assert result.messages[0]["content"] == "Out of tokens"

    def test_kill_no_reason(self, registry):
        """Test killing without reason."""
        agent = registry.register("role", "project", "task")

        result, _ = registry.kill(agent.id)

        assert result.status == "failed"
        assert len(result.messages) == 0

    def test_kill_not_found(self, registry):
        """Test killing non-existent agent."""
        result, prev_status = registry.kill("nonexistent")
        assert result is None
        assert prev_status is None

    def test_send_message_with_queue(self, registry):
        """Test sending message using queue."""
        agent = registry.register("role", "project", "task")

        with patch("forge_harness.webhook_server.services.agent_registry.get_message_queue") as mock_get_queue:
            mock_queue = MagicMock()
            mock_msg = MagicMock()
            mock_msg.id = "msg-123"
            mock_queue.enqueue.return_value = mock_msg
            mock_get_queue.return_value = mock_queue

            result, msg_id = registry.send_message(
                agent.id,
                {"type": "instruction", "content": "Do something"},
                sender_id="orchestrator",
                use_queue=True,
            )

            assert result is not None
            assert msg_id == "msg-123"
            assert len(result.messages) == 1
            assert result.messages[0]["message_id"] == "msg-123"

    def test_send_message_without_queue(self, registry):
        """Test sending message without queue."""
        agent = registry.register("role", "project", "task")

        result, msg_id = registry.send_message(
            agent.id,
            {"type": "info", "content": "Status update"},
            use_queue=False,
        )

        assert result is not None
        assert msg_id is None
        assert len(result.messages) == 1
        assert "timestamp" in result.messages[0]

    def test_send_message_not_found(self, registry):
        """Test sending to non-existent agent."""
        result, msg_id = registry.send_message(
            "nonexistent",
            {"type": "test", "content": "test"},
        )
        assert result is None
        assert msg_id is None

    def test_broadcast(self, registry):
        """Test broadcasting to all agents."""
        registry.register("role1", "project1", "task1")
        registry.register("role2", "project2", "task2")
        registry.register("role3", "project3", "task3")

        count = registry.broadcast({"type": "announcement", "content": "Hello all"})

        assert count == 3
        for agent in registry.list_active():
            assert len(agent.messages) == 1
            assert agent.messages[0]["type"] == "announcement"

    def test_broadcast_excludes_expired(self, registry):
        """Test broadcast excludes expired agents."""
        registry._expiry_seconds = 1
        agent1 = registry.register("role1", "project1", "task1")
        registry.register("role2", "project2", "task2")

        # Expire agent1
        agent1.last_activity = datetime.now(UTC) - timedelta(seconds=10)

        count = registry.broadcast({"type": "test", "content": "test"})

        assert count == 1

    def test_cleanup_expired(self, registry):
        """Test cleanup of expired agents."""
        registry._expiry_seconds = 1
        agent1 = registry.register("role1", "project1", "task1")
        registry.register("role2", "project2", "task2")

        # Expire agent1
        agent1.last_activity = datetime.now(UTC) - timedelta(seconds=10)

        cleaned = registry._cleanup_expired()

        assert cleaned == 1
        assert len(registry._agents) == 1


class TestGetAgentRegistry:
    """Tests for get_agent_registry function."""

    def test_returns_registry(self):
        """Test that get_agent_registry returns an AgentRegistry."""
        import forge_harness.webhook_server.services.agent_registry as ar_module
        ar_module._agent_registry = None

        registry = get_agent_registry()

        assert isinstance(registry, AgentRegistry)

    def test_returns_same_instance(self):
        """Test that get_agent_registry returns singleton."""
        import forge_harness.webhook_server.services.agent_registry as ar_module
        ar_module._agent_registry = None

        registry1 = get_agent_registry()
        registry2 = get_agent_registry()

        assert registry1 is registry2
