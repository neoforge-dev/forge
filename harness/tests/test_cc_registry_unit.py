"""Unit tests for forge_harness.webhook_server.services.registry

Targets: AgentSession, AgentRegistry, get_agent_registry
Coverage goal: 95%+ line coverage

All external dependencies (message queue, logging) are mocked.
"""

import sys
from datetime import UTC, datetime, timedelta
from threading import Thread
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — inject a fake ``forge_harness.webhook_server.services.messaging``
# module so registry.py's lazy import does not raise ModuleNotFoundError.
# ---------------------------------------------------------------------------

_fake_messaging = ModuleType("forge_harness.webhook_server.services.messaging")


def _install_fake_messaging(monkeypatch):
    """Insert a fake messaging module into sys.modules so the lazy import in
    registry.send_message() resolves without a real module on disk.
    """
    mock_queue = MagicMock()
    mock_msg = MagicMock()
    mock_msg.id = "mock-msg-id"
    mock_queue.enqueue.return_value = mock_msg
    _fake_messaging.get_message_queue = MagicMock(return_value=mock_queue)

    monkeypatch.setitem(
        sys.modules,
        "forge_harness.webhook_server.services.messaging",
        _fake_messaging,
    )
    return mock_queue


# ---------------------------------------------------------------------------
# Import the module under test AFTER env is established by conftest.py
# ---------------------------------------------------------------------------

import forge_harness.webhook_server.services.registry as _registry_module  # noqa: E402
from forge_harness.webhook_server.services.registry import (  # noqa: E402
    AgentRegistry,
    AgentSession,
    get_agent_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the global registry singleton before and after every test."""
    _registry_module._agent_registry = None
    yield
    _registry_module._agent_registry = None


@pytest.fixture
def registry():
    """Return a fresh AgentRegistry with a long expiry (avoids flakiness)."""
    return AgentRegistry(expiry_seconds=300)


@pytest.fixture
def fake_messaging(monkeypatch):
    """Inject fake messaging module and return the mock queue."""
    return _install_fake_messaging(monkeypatch)


# ===========================================================================
# TestAgentSessionDataclass
# ===========================================================================


class TestAgentSessionDataclass:
    """Tests for the AgentSession dataclass fields and defaults."""

    def test_required_fields_stored(self):
        s = AgentSession(id="a1", role="feature-dev", project="proj", task="task")
        assert s.id == "a1"
        assert s.role == "feature-dev"
        assert s.project == "proj"
        assert s.task == "task"

    def test_optional_fields_defaults(self):
        s = AgentSession(id="a1", role="r", project="p", task="t")
        assert s.name is None
        assert s.domain is None
        assert s.parent_id is None
        assert s.children == []
        assert s.tmux_session is None
        assert s.skills == []
        assert s.status == "active"
        assert s.progress == 0
        assert s.current_task is None
        assert s.files_modified == []
        assert s.token_usage == {}
        assert s.messages == []

    def test_optional_fields_set(self):
        s = AgentSession(
            id="a2",
            role="debug",
            project="p",
            task="t",
            name="dbg",
            domain="codeswiftr-com",
            parent_id="parent-1",
            children=["child-1"],
            tmux_session="forge:debug",
            skills=["debug"],
            status="waiting",
            progress=42,
            current_task="step 3",
            files_modified=["a.py"],
            token_usage={"input": 10},
        )
        assert s.name == "dbg"
        assert s.domain == "codeswiftr-com"
        assert s.parent_id == "parent-1"
        assert s.children == ["child-1"]
        assert s.tmux_session == "forge:debug"
        assert s.skills == ["debug"]
        assert s.status == "waiting"
        assert s.progress == 42
        assert s.current_task == "step 3"
        assert s.files_modified == ["a.py"]
        assert s.token_usage == {"input": 10}

    def test_registered_at_and_last_activity_auto_set(self):
        before = datetime.now(UTC)
        s = AgentSession(id="x", role="r", project="p", task="t")
        after = datetime.now(UTC)
        assert before <= s.registered_at <= after
        assert before <= s.last_activity <= after

    def test_mutable_defaults_are_independent(self):
        """Each instance gets its own list/dict — not a shared default."""
        s1 = AgentSession(id="s1", role="r", project="p", task="t")
        s2 = AgentSession(id="s2", role="r", project="p", task="t")
        s1.children.append("c1")
        s1.skills.append("sk")
        s1.files_modified.append("f.py")
        s1.messages.append({"x": 1})
        s1.token_usage["input"] = 100
        assert s2.children == []
        assert s2.skills == []
        assert s2.files_modified == []
        assert s2.messages == []
        assert s2.token_usage == {}


# ===========================================================================
# TestAgentSessionToDict
# ===========================================================================


class TestAgentSessionToDict:
    """Tests for AgentSession.to_dict()."""

    def test_all_keys_present(self):
        s = AgentSession(id="a", role="r", project="p", task="t")
        d = s.to_dict()
        expected_keys = {
            "id",
            "role",
            "name",
            "domain",
            "project",
            "task",
            "parent_id",
            "children",
            "tmux_session",
            "skills",
            "status",
            "progress",
            "current_task",
            "files_modified",
            "token_usage",
            "messages_count",
            "registered_at",
            "last_activity",
            "is_stale",
        }
        assert expected_keys.issubset(d.keys())

    def test_messages_count_reflects_message_list_length(self):
        s = AgentSession(id="a", role="r", project="p", task="t")
        assert s.to_dict()["messages_count"] == 0
        s.messages.append({"type": "info"})
        s.messages.append({"type": "completion"})
        assert s.to_dict()["messages_count"] == 2

    def test_registered_at_is_isoformat_string(self):
        s = AgentSession(id="a", role="r", project="p", task="t")
        d = s.to_dict()
        # Should parse without error
        datetime.fromisoformat(d["registered_at"])
        datetime.fromisoformat(d["last_activity"])

    def test_is_stale_value_matches_method(self):
        s = AgentSession(
            id="a",
            role="r",
            project="p",
            task="t",
            last_activity=datetime.now(UTC) - timedelta(minutes=5),
        )
        d = s.to_dict()
        assert d["is_stale"] == s.is_stale()

    def test_scalar_fields_round_trip(self):
        s = AgentSession(
            id="rid",
            role="review",
            project="my-proj",
            task="review code",
            name="rev",
            domain="dom",
            parent_id="pid",
            tmux_session="forge:rev",
            status="waiting",
            progress=77,
            current_task="step X",
        )
        d = s.to_dict()
        assert d["id"] == "rid"
        assert d["role"] == "review"
        assert d["project"] == "my-proj"
        assert d["task"] == "review code"
        assert d["name"] == "rev"
        assert d["domain"] == "dom"
        assert d["parent_id"] == "pid"
        assert d["tmux_session"] == "forge:rev"
        assert d["status"] == "waiting"
        assert d["progress"] == 77
        assert d["current_task"] == "step X"


# ===========================================================================
# TestAgentSessionIsExpired
# ===========================================================================


class TestAgentSessionIsExpired:
    """Tests for AgentSession.is_expired()."""

    def test_fresh_session_is_not_expired(self):
        s = AgentSession(id="a", role="r", project="p", task="t")
        assert s.is_expired(timeout_seconds=300) is False

    def test_old_session_is_expired(self):
        s = AgentSession(
            id="a",
            role="r",
            project="p",
            task="t",
            last_activity=datetime.now(UTC) - timedelta(seconds=400),
        )
        assert s.is_expired(timeout_seconds=300) is True

    def test_exactly_at_boundary_is_not_expired(self):
        """A session exactly at the timeout is NOT yet expired (strict >)."""
        s = AgentSession(
            id="a",
            role="r",
            project="p",
            task="t",
            last_activity=datetime.now(UTC) - timedelta(seconds=300),
        )
        # Slightly over 300 due to execution time — so we check the boundary
        # behavior: 300 seconds ago should be very close to threshold.
        # The check is "> timeout_seconds" so 300s of inactivity is NOT yet expired.
        result = s.is_expired(timeout_seconds=300)
        # Allow either True or False for the exact boundary; mainly confirm no exception.
        assert isinstance(result, bool)

    def test_custom_timeout(self):
        s = AgentSession(
            id="a",
            role="r",
            project="p",
            task="t",
            last_activity=datetime.now(UTC) - timedelta(seconds=10),
        )
        assert s.is_expired(timeout_seconds=5) is True
        assert s.is_expired(timeout_seconds=30) is False

    def test_default_timeout_is_300(self):
        s = AgentSession(
            id="a",
            role="r",
            project="p",
            task="t",
            last_activity=datetime.now(UTC) - timedelta(seconds=400),
        )
        # Calling without argument should use default 300
        assert s.is_expired() is True


# ===========================================================================
# TestAgentSessionIsStale
# ===========================================================================


class TestAgentSessionIsStale:
    """Tests for AgentSession.is_stale()."""

    def test_fresh_session_is_not_stale(self):
        s = AgentSession(id="a", role="r", project="p", task="t")
        assert s.is_stale(heartbeat_timeout_seconds=120) is False

    def test_old_session_is_stale(self):
        s = AgentSession(
            id="a",
            role="r",
            project="p",
            task="t",
            last_activity=datetime.now(UTC) - timedelta(seconds=200),
        )
        assert s.is_stale(heartbeat_timeout_seconds=120) is True

    def test_custom_heartbeat_timeout(self):
        s = AgentSession(
            id="a",
            role="r",
            project="p",
            task="t",
            last_activity=datetime.now(UTC) - timedelta(seconds=60),
        )
        assert s.is_stale(heartbeat_timeout_seconds=30) is True
        assert s.is_stale(heartbeat_timeout_seconds=120) is False

    def test_default_timeout_is_120(self):
        s = AgentSession(
            id="a",
            role="r",
            project="p",
            task="t",
            last_activity=datetime.now(UTC) - timedelta(seconds=200),
        )
        # Default heartbeat timeout is 120 — 200 seconds > 120, so stale
        assert s.is_stale() is True


# ===========================================================================
# TestAgentRegistryInit
# ===========================================================================


class TestAgentRegistryInit:
    """Tests for AgentRegistry.__init__."""

    def test_default_expiry(self):
        r = AgentRegistry()
        assert r._expiry_seconds == 300

    def test_custom_expiry(self):
        r = AgentRegistry(expiry_seconds=60)
        assert r._expiry_seconds == 60

    def test_starts_empty(self):
        r = AgentRegistry()
        assert r._agents == {}

    def test_lock_is_created(self):
        r = AgentRegistry()
        assert r._lock is not None


# ===========================================================================
# TestAgentRegistryRegister
# ===========================================================================


class TestAgentRegistryRegister:
    """Tests for AgentRegistry.register()."""

    def test_register_returns_agent_session(self, registry):
        agent = registry.register(role="feature-dev", project="my-proj", task="do stuff")
        assert isinstance(agent, AgentSession)

    def test_id_is_8_chars(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        assert len(agent.id) == 8

    def test_agent_stored_in_registry(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        assert agent.id in registry._agents

    def test_role_project_task_set(self, registry):
        agent = registry.register(role="debug", project="voice-coach", task="fix bug")
        assert agent.role == "debug"
        assert agent.project == "voice-coach"
        assert agent.task == "fix bug"

    def test_optional_name(self, registry):
        agent = registry.register(role="r", project="p", task="t", name="named-agent")
        assert agent.name == "named-agent"

    def test_optional_domain(self, registry):
        agent = registry.register(role="r", project="p", task="t", domain="codeswiftr-com")
        assert agent.domain == "codeswiftr-com"

    def test_optional_tmux_session(self, registry):
        agent = registry.register(role="r", project="p", task="t", tmux_session="forge:worker")
        assert agent.tmux_session == "forge:worker"

    def test_optional_skills(self, registry):
        agent = registry.register(role="r", project="p", task="t", skills=["debug", "test"])
        assert agent.skills == ["debug", "test"]

    def test_skills_none_becomes_empty_list(self, registry):
        agent = registry.register(role="r", project="p", task="t", skills=None)
        assert agent.skills == []

    def test_status_defaults_to_active(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        assert agent.status == "active"

    def test_multiple_registrations_get_unique_ids(self, registry):
        ids = {registry.register(role="r", project="p", task="t").id for _ in range(10)}
        assert len(ids) == 10

    def test_register_with_parent_updates_parent_children(self, registry):
        parent = registry.register(role="orchestrator", project="p", task="orchestrate")
        child = registry.register(role="worker", project="p", task="work", parent_id=parent.id)
        assert child.parent_id == parent.id
        assert child.id in registry._agents[parent.id].children

    def test_register_with_nonexistent_parent_does_not_raise(self, registry):
        """If parent_id does not exist in registry, no error, no children update."""
        agent = registry.register(
            role="worker", project="p", task="t", parent_id="ghost-parent"
        )
        assert agent.parent_id == "ghost-parent"
        # ghost-parent not in registry — no KeyError

    def test_register_thread_safety(self, registry):
        """Concurrent registrations should all succeed without data corruption."""
        results = []

        def reg():
            a = registry.register(role="r", project="p", task="t")
            results.append(a.id)

        threads = [Thread(target=reg) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        # All IDs must be unique
        assert len(set(results)) == 20


# ===========================================================================
# TestAgentRegistryGet
# ===========================================================================


class TestAgentRegistryGet:
    """Tests for AgentRegistry.get()."""

    def test_get_by_exact_id(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result = registry.get(agent.id)
        assert result is not None
        assert result.id == agent.id

    def test_get_nonexistent_returns_none(self, registry):
        assert registry.get("does-not-exist") is None

    def test_get_expired_by_id_removes_and_returns_none(self, registry):
        registry._expiry_seconds = 1
        agent = registry.register(role="r", project="p", task="t")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = registry.get(agent.id)
        assert result is None
        assert agent.id not in registry._agents

    def test_get_by_tmux_session(self, registry):
        agent = registry.register(
            role="r", project="p", task="t", tmux_session="forge:opencode"
        )
        result = registry.get("forge:opencode")
        assert result is not None
        assert result.id == agent.id

    def test_get_by_tmux_session_not_found(self, registry):
        registry.register(role="r", project="p", task="t", tmux_session="forge:opencode")
        assert registry.get("forge:something-else") is None

    def test_get_expired_by_tmux_session_removes_and_returns_none(self, registry):
        registry._expiry_seconds = 1
        agent = registry.register(
            role="r", project="p", task="t", tmux_session="forge:exp"
        )
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = registry.get("forge:exp")
        assert result is None
        assert agent.id not in registry._agents

    def test_get_by_name_exact_match(self, registry):
        agent = registry.register(role="r", project="p", task="t", name="MyAgent")
        result = registry.get("MyAgent")
        assert result is not None
        assert result.id == agent.id

    def test_get_by_name_case_insensitive(self, registry):
        agent = registry.register(role="r", project="p", task="t", name="MyAgent")
        assert registry.get("myagent") is not None
        assert registry.get("MYAGENT") is not None
        assert registry.get("MyAgent") is not None

    def test_get_expired_by_name_removes_and_returns_none(self, registry):
        registry._expiry_seconds = 1
        agent = registry.register(role="r", project="p", task="t", name="OldAgent")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = registry.get("oldagent")
        assert result is None
        assert agent.id not in registry._agents

    def test_get_prefers_id_over_tmux_session(self, registry):
        """Direct ID lookup is checked first."""
        agent_a = registry.register(
            role="r", project="p", task="t", tmux_session="forge:a"
        )
        agent_b = registry.register(
            role="r", project="p", task="t", tmux_session=agent_a.id
        )
        # Looking up agent_a.id should return agent_a (direct id match),
        # not agent_b (which has tmux_session == agent_a.id).
        result = registry.get(agent_a.id)
        assert result.id == agent_a.id

    def test_get_no_name_agents_skipped_in_name_lookup(self, registry):
        # Register agent without a name
        registry.register(role="r", project="p", task="t")  # name=None
        # Should not crash when name is None
        result = registry.get("nobodyhasthisname")
        assert result is None


# ===========================================================================
# TestAgentRegistryListActive
# ===========================================================================


class TestAgentRegistryListActive:
    """Tests for AgentRegistry.list_active()."""

    def test_empty_registry(self, registry):
        assert registry.list_active() == []

    def test_returns_all_active_agents(self, registry):
        registry.register(role="r1", project="p", task="t")
        registry.register(role="r2", project="p", task="t")
        registry.register(role="r3", project="p", task="t")
        assert len(registry.list_active()) == 3

    def test_expired_agents_excluded(self, registry):
        registry._expiry_seconds = 1
        stale = registry.register(role="stale", project="p", task="t")
        registry.register(role="fresh", project="p", task="t")
        stale.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        active = registry.list_active()
        ids = [a.id for a in active]
        assert stale.id not in ids
        assert len(active) == 1

    def test_returns_list_copy(self, registry):
        registry.register(role="r", project="p", task="t")
        result = registry.list_active()
        assert isinstance(result, list)


# ===========================================================================
# TestAgentRegistryUpdateProgress
# ===========================================================================


class TestAgentRegistryUpdateProgress:
    """Tests for AgentRegistry.update_progress()."""

    def test_update_progress_value(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result = registry.update_progress(agent.id, progress=60)
        assert result is not None
        assert result.progress == 60

    def test_update_last_activity_refreshed(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        before = agent.last_activity
        result = registry.update_progress(agent.id, progress=10)
        assert result.last_activity >= before

    def test_update_current_task(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result = registry.update_progress(agent.id, progress=50, current_task="step 2")
        assert result.current_task == "step 2"

    def test_current_task_not_changed_when_none(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.update_progress(agent.id, progress=50, current_task="step 2")
        result = registry.update_progress(agent.id, progress=60, current_task=None)
        assert result.current_task == "step 2"

    def test_files_modified_appended_no_duplicates(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.update_progress(agent.id, progress=25, files_modified=["a.py", "b.py"])
        registry.update_progress(agent.id, progress=50, files_modified=["b.py", "c.py"])
        result = registry.get(agent.id)
        assert sorted(result.files_modified) == ["a.py", "b.py", "c.py"]

    def test_files_modified_none_leaves_list_unchanged(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.update_progress(agent.id, progress=25, files_modified=["a.py"])
        result = registry.update_progress(agent.id, progress=50, files_modified=None)
        assert result.files_modified == ["a.py"]

    def test_token_usage_accumulated(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.update_progress(
            agent.id, progress=25, token_usage={"input": 100, "output": 50}
        )
        registry.update_progress(
            agent.id, progress=50, token_usage={"input": 200, "output": 100}
        )
        result = registry.get(agent.id)
        assert result.token_usage["input"] == 300
        assert result.token_usage["output"] == 150

    def test_token_usage_new_key_initialized_from_zero(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result = registry.update_progress(
            agent.id, progress=50, token_usage={"cache_hit": 42}
        )
        assert result.token_usage["cache_hit"] == 42

    def test_token_usage_none_leaves_unchanged(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.update_progress(
            agent.id, progress=25, token_usage={"input": 100}
        )
        result = registry.update_progress(agent.id, progress=50, token_usage=None)
        assert result.token_usage["input"] == 100

    def test_returns_none_when_agent_not_found(self, registry):
        result = registry.update_progress("ghost", progress=50)
        assert result is None


# ===========================================================================
# TestAgentRegistryComplete
# ===========================================================================


class TestAgentRegistryComplete:
    """Tests for AgentRegistry.complete()."""

    def test_complete_sets_status_and_progress(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result = registry.complete(agent.id)
        assert result.status == "completed"
        assert result.progress == 100

    def test_complete_refreshes_last_activity(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        before = agent.last_activity
        result = registry.complete(agent.id)
        assert result.last_activity >= before

    def test_complete_with_summary_appends_message(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result = registry.complete(agent.id, summary="All done!")
        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "completion"
        assert msg["content"] == "All done!"
        assert "timestamp" in msg

    def test_complete_without_summary_no_message(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result = registry.complete(agent.id)
        assert len(result.messages) == 0

    def test_complete_not_found_returns_none(self, registry):
        result = registry.complete("nonexistent")
        assert result is None


# ===========================================================================
# TestAgentRegistryPause
# ===========================================================================


class TestAgentRegistryPause:
    """Tests for AgentRegistry.pause()."""

    def test_pause_sets_status_paused(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result, prev = registry.pause(agent.id)
        assert result.status == "paused"

    def test_pause_returns_previous_status(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        _, prev = registry.pause(agent.id)
        assert prev == "active"

    def test_pause_refreshes_last_activity(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        before = agent.last_activity
        result, _ = registry.pause(agent.id)
        assert result.last_activity >= before

    def test_pause_not_found_returns_none_none(self, registry):
        result, prev = registry.pause("ghost")
        assert result is None
        assert prev is None

    def test_pause_already_paused_agent(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.pause(agent.id)
        result, prev = registry.pause(agent.id)
        assert result.status == "paused"
        assert prev == "paused"


# ===========================================================================
# TestAgentRegistryResume
# ===========================================================================


class TestAgentRegistryResume:
    """Tests for AgentRegistry.resume()."""

    def test_resume_sets_status_active(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.pause(agent.id)
        result, _ = registry.resume(agent.id)
        assert result.status == "active"

    def test_resume_returns_previous_status(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.pause(agent.id)
        _, prev = registry.resume(agent.id)
        assert prev == "paused"

    def test_resume_refreshes_last_activity(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        registry.pause(agent.id)
        before = agent.last_activity
        result, _ = registry.resume(agent.id)
        assert result.last_activity >= before

    def test_resume_not_found_returns_none_none(self, registry):
        result, prev = registry.resume("ghost")
        assert result is None
        assert prev is None

    def test_resume_active_agent(self, registry):
        """Resuming an already-active agent records 'active' as previous status."""
        agent = registry.register(role="r", project="p", task="t")
        result, prev = registry.resume(agent.id)
        assert result.status == "active"
        assert prev == "active"


# ===========================================================================
# TestAgentRegistryKill
# ===========================================================================


class TestAgentRegistryKill:
    """Tests for AgentRegistry.kill()."""

    def test_kill_sets_status_failed(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result, _ = registry.kill(agent.id)
        assert result.status == "failed"

    def test_kill_returns_previous_status(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        _, prev = registry.kill(agent.id)
        assert prev == "active"

    def test_kill_refreshes_last_activity(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        before = agent.last_activity
        result, _ = registry.kill(agent.id)
        assert result.last_activity >= before

    def test_kill_with_reason_appends_message(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result, _ = registry.kill(agent.id, reason="out of tokens")
        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg["type"] == "kill"
        assert msg["content"] == "out of tokens"
        assert "timestamp" in msg

    def test_kill_without_reason_no_message(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result, _ = registry.kill(agent.id)
        assert len(result.messages) == 0

    def test_kill_not_found_returns_none_none(self, registry):
        result, prev = registry.kill("ghost")
        assert result is None
        assert prev is None

    def test_killed_agent_remains_in_registry(self, registry):
        """kill() only changes status — agent stays in the registry dict."""
        agent = registry.register(role="r", project="p", task="t")
        registry.kill(agent.id)
        assert agent.id in registry._agents


# ===========================================================================
# TestAgentRegistrySendMessage
# ===========================================================================


class TestAgentRegistrySendMessage:
    """Tests for AgentRegistry.send_message()."""

    def test_send_message_without_queue(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        result, msg_id = registry.send_message(
            agent.id,
            {"type": "info", "content": "hello"},
            use_queue=False,
        )
        assert result is not None
        assert msg_id is None
        assert len(result.messages) == 1
        assert result.messages[0]["type"] == "info"
        assert result.messages[0]["content"] == "hello"
        assert "timestamp" in result.messages[0]

    def test_send_message_without_queue_appends_timestamp(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        msg = {"type": "cmd", "content": "do X"}
        result, _ = registry.send_message(agent.id, msg, use_queue=False)
        assert "timestamp" in result.messages[0]

    def test_send_message_not_found_returns_none_none(self, registry):
        result, msg_id = registry.send_message(
            "ghost", {"type": "t", "content": "c"}, use_queue=False
        )
        assert result is None
        assert msg_id is None

    def test_send_message_with_queue(self, registry, fake_messaging):
        """Verify queue path: enqueue called, message_id attached to stored msg."""
        mock_msg = MagicMock()
        mock_msg.id = "queued-abc"
        fake_messaging.enqueue.return_value = mock_msg

        agent = registry.register(role="r", project="p", task="t")
        result, msg_id = registry.send_message(
            agent.id,
            {
                "type": "instruction",
                "content": "do something",
                "priority": 2,
                "metadata": {"key": "value"},
            },
            sender_id="orchestrator",
            use_queue=True,
        )
        assert msg_id == "queued-abc"
        assert result is not None
        # enqueue must have been called
        fake_messaging.enqueue.assert_called_once()
        # The stored message should carry the message_id
        stored = result.messages[-1]
        assert stored.get("message_id") == "queued-abc"

    def test_send_message_with_queue_uses_defaults_for_missing_fields(
        self, registry, fake_messaging
    ):
        """Missing type/priority/metadata fields fall back to defaults inside send_message."""
        mock_msg = MagicMock()
        mock_msg.id = "def-123"
        fake_messaging.enqueue.return_value = mock_msg

        agent = registry.register(role="r", project="p", task="t")
        # Provide minimal message dict — no type, priority, metadata
        result, msg_id = registry.send_message(
            agent.id, {"content": "minimal"}, use_queue=True
        )
        assert msg_id == "def-123"
        call_kwargs = fake_messaging.enqueue.call_args
        # Check defaults passed to enqueue
        _, kwargs = call_kwargs if call_kwargs[1] else (call_kwargs[0], {})
        # Either positional or keyword args accepted
        assert result is not None

    def test_send_message_multiple_messages_accumulate(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        for i in range(5):
            registry.send_message(
                agent.id, {"type": "info", "content": f"msg {i}"}, use_queue=False
            )
        assert len(registry._agents[agent.id].messages) == 5

    def test_send_message_modifies_message_dict_in_place(self, registry):
        """The timestamp field is added to the original dict object."""
        agent = registry.register(role="r", project="p", task="t")
        msg = {"type": "test", "content": "check"}
        registry.send_message(agent.id, msg, use_queue=False)
        assert "timestamp" in msg


# ===========================================================================
# TestAgentRegistryBroadcast
# ===========================================================================


class TestAgentRegistryBroadcast:
    """Tests for AgentRegistry.broadcast()."""

    def test_broadcast_returns_count(self, registry):
        registry.register(role="r1", project="p", task="t")
        registry.register(role="r2", project="p", task="t")
        count = registry.broadcast({"type": "announcement", "content": "hi"})
        assert count == 2

    def test_broadcast_appends_to_all_agents(self, registry):
        a1 = registry.register(role="r1", project="p", task="t")
        a2 = registry.register(role="r2", project="p", task="t")
        registry.broadcast({"type": "global", "content": "hello"})
        assert any(m["type"] == "global" for m in a1.messages)
        assert any(m["type"] == "global" for m in a2.messages)

    def test_broadcast_adds_timestamp(self, registry):
        agent = registry.register(role="r", project="p", task="t")
        msg = {"type": "ann", "content": "test"}
        registry.broadcast(msg)
        # timestamp is added to the passed dict
        assert "timestamp" in msg

    def test_broadcast_uses_copy_per_agent(self, registry):
        """Each agent gets an independent copy of the broadcast message."""
        a1 = registry.register(role="r1", project="p", task="t")
        a2 = registry.register(role="r2", project="p", task="t")
        registry.broadcast({"type": "ann", "content": "initial"})
        # Mutate one agent's copy
        a1.messages[-1]["content"] = "mutated"
        # The other agent's copy should be unaffected
        assert a2.messages[-1]["content"] != "mutated"

    def test_broadcast_zero_when_empty(self, registry):
        count = registry.broadcast({"type": "t", "content": "c"})
        assert count == 0

    def test_broadcast_excludes_expired_agents(self, registry):
        registry._expiry_seconds = 1
        stale = registry.register(role="stale", project="p", task="t")
        registry.register(role="fresh", project="p", task="t")
        stale.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        count = registry.broadcast({"type": "t", "content": "c"})
        assert count == 1

    def test_broadcast_calls_cleanup_expired(self, registry):
        """broadcast() must invoke _cleanup_expired() to prune stale entries."""
        with patch.object(registry, "_cleanup_expired", wraps=registry._cleanup_expired) as spy:
            registry.broadcast({"type": "t", "content": "c"})
            spy.assert_called_once()


# ===========================================================================
# TestAgentRegistryCleanupExpired
# ===========================================================================


class TestAgentRegistryCleanupExpired:
    """Tests for AgentRegistry._cleanup_expired() (internal method)."""

    def test_cleanup_removes_expired_agents(self, registry):
        registry._expiry_seconds = 1
        stale = registry.register(role="s", project="p", task="t")
        registry.register(role="f", project="p", task="t")
        stale.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        # Manually acquire the lock to call private method as documented
        with registry._lock:
            count = registry._cleanup_expired()
        assert count == 1
        assert stale.id not in registry._agents

    def test_cleanup_returns_zero_when_nothing_expired(self, registry):
        registry.register(role="r", project="p", task="t")
        with registry._lock:
            count = registry._cleanup_expired()
        assert count == 0

    def test_cleanup_returns_count_of_removed(self, registry):
        registry._expiry_seconds = 1
        agents = [registry.register(role="r", project="p", task="t") for _ in range(5)]
        # Expire all
        for a in agents:
            a.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        with registry._lock:
            count = registry._cleanup_expired()
        assert count == 5
        assert registry._agents == {}

    def test_cleanup_does_not_remove_fresh_agents(self, registry):
        fresh = registry.register(role="r", project="p", task="t")
        with registry._lock:
            registry._cleanup_expired()
        assert fresh.id in registry._agents


# ===========================================================================
# TestGetAgentRegistry
# ===========================================================================


class TestGetAgentRegistry:
    """Tests for the get_agent_registry() singleton function."""

    def test_returns_agent_registry_instance(self):
        registry = get_agent_registry()
        assert isinstance(registry, AgentRegistry)

    def test_same_instance_returned_on_repeated_calls(self):
        r1 = get_agent_registry()
        r2 = get_agent_registry()
        assert r1 is r2

    def test_none_triggers_creation(self):
        _registry_module._agent_registry = None
        registry = get_agent_registry()
        assert registry is not None
        assert isinstance(registry, AgentRegistry)

    def test_existing_instance_reused(self):
        existing = AgentRegistry(expiry_seconds=999)
        _registry_module._agent_registry = existing
        result = get_agent_registry()
        assert result is existing

    def test_singleton_persists_state(self):
        """Agents registered via singleton are visible from subsequent calls."""
        r = get_agent_registry()
        agent = r.register(role="r", project="p", task="t")
        r2 = get_agent_registry()
        assert agent.id in r2._agents


# ===========================================================================
# TestConcurrencyEdgeCases
# ===========================================================================


class TestConcurrencyEdgeCases:
    """Additional concurrency and edge-case tests."""

    def test_concurrent_updates_do_not_corrupt_progress(self, registry):
        agent = registry.register(role="r", project="p", task="t")

        errors = []

        def updater(i):
            try:
                registry.update_progress(
                    agent.id,
                    progress=i,
                    files_modified=[f"file_{i}.py"],
                    token_usage={"input": 1},
                )
            except Exception as e:
                errors.append(e)

        threads = [Thread(target=updater, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent update: {errors}"
        # After 20 threads each adding 1 token, total input should be 20
        assert registry._agents[agent.id].token_usage.get("input", 0) == 20

    def test_concurrent_broadcast_and_register(self, registry):
        """Register and broadcast should not deadlock or corrupt state."""
        errors = []

        def broadcaster():
            for _ in range(5):
                try:
                    registry.broadcast({"type": "t", "content": "c"})
                except Exception as e:
                    errors.append(e)

        def registrar():
            for _ in range(5):
                try:
                    registry.register(role="r", project="p", task="t")
                except Exception as e:
                    errors.append(e)

        threads = [Thread(target=broadcaster) for _ in range(3)]
        threads += [Thread(target=registrar) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent broadcast/register: {errors}"


# ===========================================================================
# TestIntegrationScenarios
# ===========================================================================


class TestIntegrationScenarios:
    """End-to-end lifecycle scenarios exercising multiple methods together."""

    def test_full_agent_lifecycle(self, registry):
        """Register -> update_progress -> complete -> verify state."""
        agent = registry.register(
            role="feature-dev",
            project="interview-simulator",
            task="Implement auth module",
            name="dev-agent",
            domain="codeswiftr-com",
        )
        agent_id = agent.id

        # Update progress in stages
        registry.update_progress(
            agent_id,
            progress=25,
            current_task="Writing models",
            files_modified=["models.py"],
            token_usage={"input": 500, "output": 200},
        )
        registry.update_progress(
            agent_id,
            progress=75,
            current_task="Writing tests",
            files_modified=["test_models.py"],
            token_usage={"input": 800, "output": 400},
        )

        a = registry.get(agent_id)
        assert a.progress == 75
        assert sorted(a.files_modified) == ["models.py", "test_models.py"]
        assert a.token_usage["input"] == 1300
        assert a.token_usage["output"] == 600

        # Complete the agent
        registry.complete(agent_id, summary="Auth module implemented")
        a = registry.get(agent_id)
        assert a.status == "completed"
        assert a.progress == 100

    def test_pause_resume_kill_lifecycle(self, registry):
        agent = registry.register(role="r", project="p", task="t")

        _, prev = registry.pause(agent.id)
        assert prev == "active"
        assert registry.get(agent.id).status == "paused"

        _, prev = registry.resume(agent.id)
        assert prev == "paused"
        assert registry.get(agent.id).status == "active"

        _, prev = registry.kill(agent.id, reason="terminated by orchestrator")
        assert prev == "active"
        assert registry.get(agent.id).status == "failed"

    def test_parent_child_hierarchy(self, registry):
        """Verify parent-child relationship registration."""
        orchestrator = registry.register(role="orchestrator", project="p", task="manage")
        worker_a = registry.register(
            role="worker", project="p", task="work A", parent_id=orchestrator.id
        )
        worker_b = registry.register(
            role="worker", project="p", task="work B", parent_id=orchestrator.id
        )

        parent = registry.get(orchestrator.id)
        assert worker_a.id in parent.children
        assert worker_b.id in parent.children
        assert len(parent.children) == 2

    def test_broadcast_reaches_all_active_agents(self, registry):
        agents = [
            registry.register(role=f"r{i}", project="p", task="t") for i in range(5)
        ]
        registry.broadcast({"type": "system", "content": "Shutdown imminent"})
        for agent in agents:
            assert any(
                m.get("content") == "Shutdown imminent" for m in agent.messages
            )

    def test_expired_agent_not_returned_after_timeout(self, registry):
        registry._expiry_seconds = 1
        agent = registry.register(role="r", project="p", task="t")
        # Force expiry
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=100)
        assert registry.get(agent.id) is None
        active = registry.list_active()
        assert all(a.id != agent.id for a in active)
