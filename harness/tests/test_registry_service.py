"""Comprehensive tests for the AgentRegistry service.

Covers:
- AgentSession dataclass and all its methods
- AgentRegistry: register, get, list_active, update_progress, complete,
  pause, resume, kill, send_message, broadcast, _cleanup_expired
- Singleton pattern via get_agent_registry()
- Thread-safety under concurrent access
- Edge cases: duplicate registration, missing entries, expired agents,
  lookup by tmux_session / name, parent-child hierarchy, message queue integration
"""

from __future__ import annotations

import sys
import threading
import time
import types
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.services.registry import (
    AgentRegistry,
    AgentSession,
    get_agent_registry,
)

# ---------------------------------------------------------------------------
# Helpers for patching the lazy `from .messaging import get_message_queue`
# ---------------------------------------------------------------------------


def _make_messaging_mock(queue_mock: MagicMock) -> types.ModuleType:
    """Create a fake 'messaging' module exposing get_message_queue -> queue_mock."""
    mod = types.ModuleType("forge_harness.webhook_server.services.messaging")
    mod.get_message_queue = lambda: queue_mock  # type: ignore[attr-defined]
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_registry(expiry_seconds: int = 300) -> AgentRegistry:
    """Return a fresh, isolated AgentRegistry instance."""
    return AgentRegistry(expiry_seconds=expiry_seconds)


def register_agent(
    registry: AgentRegistry,
    role: str = "backend-engineer",
    project: str = "test-project",
    task: str = "test task",
    name: str | None = None,
    domain: str | None = None,
    parent_id: str | None = None,
    tmux_session: str | None = None,
    skills: list[str] | None = None,
) -> AgentSession:
    return registry.register(
        role=role,
        project=project,
        task=task,
        name=name,
        domain=domain,
        parent_id=parent_id,
        tmux_session=tmux_session,
        skills=skills,
    )


# ---------------------------------------------------------------------------
# AgentSession unit tests
# ---------------------------------------------------------------------------


class TestAgentSessionDataclass:
    """Tests for the AgentSession dataclass."""

    def test_creation_with_required_fields(self):
        session = AgentSession(id="abc12345", role="debug", project="my-proj", task="fix bug")

        assert session.id == "abc12345"
        assert session.role == "debug"
        assert session.project == "my-proj"
        assert session.task == "fix bug"
        assert session.status == "active"
        assert session.progress == 0
        assert session.name is None
        assert session.domain is None
        assert session.parent_id is None
        assert session.children == []
        assert session.tmux_session is None
        assert session.skills == []
        assert session.current_task is None
        assert session.files_modified == []
        assert session.token_usage == {}
        assert session.messages == []
        # Timestamps must be timezone-aware and close to now
        assert session.registered_at.tzinfo is not None
        assert (datetime.now(UTC) - session.registered_at).total_seconds() < 5
        assert session.last_activity.tzinfo is not None

    def test_default_mutable_fields_are_independent(self):
        s1 = AgentSession(id="a1", role="r", project="p", task="t")
        s2 = AgentSession(id="a2", role="r", project="p", task="t")
        s1.children.append("child-id")
        s1.skills.append("python")
        s1.files_modified.append("main.py")

        assert s2.children == []
        assert s2.skills == []
        assert s2.files_modified == []

    def test_is_expired_false_when_fresh(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        assert session.is_expired(timeout_seconds=300) is False

    def test_is_expired_true_when_old(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        session.last_activity = datetime.now(UTC) - timedelta(seconds=400)
        assert session.is_expired(timeout_seconds=300) is True

    def test_is_expired_boundary_just_past_timeout(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        # 300s + 1ms ago: should be expired (> not >=)
        session.last_activity = datetime.now(UTC) - timedelta(seconds=300, milliseconds=1)
        assert session.is_expired(timeout_seconds=300) is True

    def test_is_stale_false_when_fresh(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        assert session.is_stale(heartbeat_timeout_seconds=120) is False

    def test_is_stale_true_when_old(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        session.last_activity = datetime.now(UTC) - timedelta(seconds=200)
        assert session.is_stale(heartbeat_timeout_seconds=120) is True

    def test_is_stale_uses_different_default_than_is_expired(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        # Between 120s and 300s: stale but not expired
        session.last_activity = datetime.now(UTC) - timedelta(seconds=150)
        assert session.is_stale() is True
        assert session.is_expired() is False

    def test_to_dict_contains_all_required_keys(self):
        session = AgentSession(
            id="abc12345",
            role="review",
            project="domain/proj",
            task="do review",
            name="reviewer",
            domain="test-domain",
        )
        d = session.to_dict()

        expected_keys = {
            "id", "role", "name", "domain", "project", "task",
            "parent_id", "children", "tmux_session", "skills",
            "status", "progress", "current_task", "files_modified",
            "token_usage", "messages_count", "registered_at",
            "last_activity", "is_stale",
        }
        assert expected_keys.issubset(d.keys())

    def test_to_dict_messages_count_not_messages_list(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        session.messages.append({"type": "completion", "content": "done"})
        d = session.to_dict()
        assert d["messages_count"] == 1
        assert "messages" not in d

    def test_to_dict_registered_at_is_iso_string(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        d = session.to_dict()
        parsed = datetime.fromisoformat(d["registered_at"])
        assert parsed.tzinfo is not None

    def test_to_dict_is_stale_reflects_staleness(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        session.last_activity = datetime.now(UTC) - timedelta(seconds=200)
        d = session.to_dict()
        assert d["is_stale"] is True

    def test_to_dict_is_stale_false_when_fresh(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        d = session.to_dict()
        assert d["is_stale"] is False

    def test_to_dict_optional_fields_none_when_not_set(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        d = session.to_dict()
        assert d["name"] is None
        assert d["domain"] is None
        assert d["parent_id"] is None
        assert d["tmux_session"] is None
        assert d["current_task"] is None

    def test_to_dict_messages_count_zero_for_empty(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        assert session.to_dict()["messages_count"] == 0

    def test_to_dict_includes_skills_list(self):
        session = AgentSession(id="x", role="r", project="p", task="t", skills=["fastapi"])
        d = session.to_dict()
        assert d["skills"] == ["fastapi"]

    def test_to_dict_includes_children_list(self):
        session = AgentSession(id="x", role="r", project="p", task="t")
        session.children = ["child-1", "child-2"]
        d = session.to_dict()
        assert d["children"] == ["child-1", "child-2"]


# ---------------------------------------------------------------------------
# AgentRegistry.register
# ---------------------------------------------------------------------------


class TestAgentRegistryRegister:
    """Tests for AgentRegistry.register()."""

    def test_register_returns_agent_session(self):
        registry = make_registry()
        agent = register_agent(registry)
        assert isinstance(agent, AgentSession)

    def test_register_assigns_unique_id(self):
        registry = make_registry()
        a1 = register_agent(registry)
        a2 = register_agent(registry)
        assert a1.id != a2.id

    def test_register_id_is_8_chars(self):
        registry = make_registry()
        agent = register_agent(registry)
        assert len(agent.id) == 8

    def test_register_stores_required_fields(self):
        registry = make_registry()
        agent = register_agent(registry, role="qa", project="proj-x", task="run tests")
        assert agent.role == "qa"
        assert agent.project == "proj-x"
        assert agent.task == "run tests"

    def test_register_stores_optional_name(self):
        registry = make_registry()
        agent = register_agent(registry, name="my-agent")
        assert agent.name == "my-agent"

    def test_register_stores_optional_domain(self):
        registry = make_registry()
        agent = register_agent(registry, domain="codeswiftr-com")
        assert agent.domain == "codeswiftr-com"

    def test_register_stores_tmux_session(self):
        registry = make_registry()
        agent = register_agent(registry, tmux_session="forge:opencode")
        assert agent.tmux_session == "forge:opencode"

    def test_register_stores_skills(self):
        registry = make_registry()
        agent = register_agent(registry, skills=["python", "fastapi"])
        assert agent.skills == ["python", "fastapi"]

    def test_register_empty_skills_when_none(self):
        registry = make_registry()
        agent = register_agent(registry, skills=None)
        assert agent.skills == []

    def test_register_default_status_is_active(self):
        registry = make_registry()
        agent = register_agent(registry)
        assert agent.status == "active"

    def test_register_multiple_agents_all_stored(self):
        registry = make_registry()
        agents = [register_agent(registry, role=f"role-{i}") for i in range(5)]
        active = registry.list_active()
        ids = {a.id for a in active}
        for agent in agents:
            assert agent.id in ids

    def test_register_with_parent_updates_parent_children(self):
        registry = make_registry()
        parent = register_agent(registry, role="orchestrator")
        child = register_agent(registry, role="worker", parent_id=parent.id)

        assert child.parent_id == parent.id
        stored_parent = registry.get(parent.id)
        assert child.id in stored_parent.children

    def test_register_with_nonexistent_parent_does_not_crash(self):
        registry = make_registry()
        child = register_agent(registry, role="worker", parent_id="nonexistent")
        assert child.parent_id == "nonexistent"
        assert child.id is not None

    def test_register_multiple_children_for_same_parent(self):
        registry = make_registry()
        parent = register_agent(registry, role="orchestrator")
        child1 = register_agent(registry, role="worker-1", parent_id=parent.id)
        child2 = register_agent(registry, role="worker-2", parent_id=parent.id)

        stored_parent = registry.get(parent.id)
        assert child1.id in stored_parent.children
        assert child2.id in stored_parent.children
        assert len(stored_parent.children) == 2

    def test_register_duplicate_role_allowed(self):
        """Registry does not prevent duplicate roles — each gets own ID."""
        registry = make_registry()
        a1 = register_agent(registry, role="backend-engineer")
        a2 = register_agent(registry, role="backend-engineer")
        assert a1.id != a2.id
        assert len(registry.list_active()) == 2

    def test_register_agent_is_retrievable_by_id(self):
        registry = make_registry()
        agent = register_agent(registry)
        assert registry.get(agent.id) is not None

    def test_register_progress_starts_at_zero(self):
        registry = make_registry()
        agent = register_agent(registry)
        assert agent.progress == 0


# ---------------------------------------------------------------------------
# AgentRegistry.get
# ---------------------------------------------------------------------------


class TestAgentRegistryGet:
    """Tests for AgentRegistry.get()."""

    def test_get_by_id_returns_agent(self):
        registry = make_registry()
        agent = register_agent(registry)
        result = registry.get(agent.id)
        assert result is not None
        assert result.id == agent.id

    def test_get_missing_id_returns_none(self):
        registry = make_registry()
        assert registry.get("nonexistent") is None

    def test_get_expired_agent_returns_none(self):
        registry = make_registry(expiry_seconds=1)
        agent = register_agent(registry)
        registry._agents[agent.id].last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = registry.get(agent.id)
        assert result is None

    def test_get_expired_agent_removes_it_from_internal_dict(self):
        registry = make_registry(expiry_seconds=1)
        agent = register_agent(registry)
        registry._agents[agent.id].last_activity = datetime.now(UTC) - timedelta(seconds=10)
        registry.get(agent.id)
        assert agent.id not in registry._agents

    def test_get_by_tmux_session(self):
        registry = make_registry()
        agent = register_agent(registry, tmux_session="forge:minimax")
        result = registry.get("forge:minimax")
        assert result is not None
        assert result.id == agent.id

    def test_get_by_tmux_session_expired_returns_none(self):
        registry = make_registry(expiry_seconds=1)
        agent = register_agent(registry, tmux_session="forge:glm")
        registry._agents[agent.id].last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = registry.get("forge:glm")
        assert result is None

    def test_get_by_name_case_insensitive(self):
        registry = make_registry()
        agent = register_agent(registry, name="MyAgent")
        result = registry.get("myagent")
        assert result is not None
        assert result.id == agent.id

    def test_get_by_name_exact_case(self):
        registry = make_registry()
        agent = register_agent(registry, name="ExactName")
        result = registry.get("ExactName")
        assert result is not None
        assert result.id == agent.id

    def test_get_by_name_uppercase_lookup(self):
        registry = make_registry()
        agent = register_agent(registry, name="FooBar")
        assert registry.get("FOOBAR").id == agent.id

    def test_get_by_name_lowercase_lookup(self):
        registry = make_registry()
        agent = register_agent(registry, name="FooBar")
        assert registry.get("foobar").id == agent.id

    def test_get_returns_none_when_no_agents_have_matching_name(self):
        registry = make_registry()
        register_agent(registry, name=None)
        assert registry.get("some-name") is None

    def test_get_by_name_expired_returns_none(self):
        registry = make_registry(expiry_seconds=1)
        agent = register_agent(registry, name="stale-agent")
        registry._agents[agent.id].last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = registry.get("stale-agent")
        assert result is None

    def test_get_direct_id_returns_correct_agent(self):
        registry = make_registry()
        a1 = register_agent(registry, tmux_session="forge:abc")
        a2 = register_agent(registry, role="other")
        result = registry.get(a1.id)
        assert result.id == a1.id
        result2 = registry.get(a2.id)
        assert result2.id == a2.id

    def test_get_empty_registry_returns_none(self):
        registry = make_registry()
        assert registry.get("anything") is None


# ---------------------------------------------------------------------------
# AgentRegistry.list_active
# ---------------------------------------------------------------------------


class TestAgentRegistryListActive:
    """Tests for AgentRegistry.list_active()."""

    def test_list_active_empty_registry(self):
        registry = make_registry()
        assert registry.list_active() == []

    def test_list_active_returns_all_fresh_agents(self):
        registry = make_registry()
        for i in range(3):
            register_agent(registry, role=f"role-{i}")
        result = registry.list_active()
        assert len(result) == 3

    def test_list_active_excludes_expired_agents(self):
        registry = make_registry(expiry_seconds=60)
        alive = register_agent(registry, role="alive")
        stale = register_agent(registry, role="stale")
        registry._agents[stale.id].last_activity = datetime.now(UTC) - timedelta(seconds=120)

        result = registry.list_active()
        ids = {a.id for a in result}
        assert alive.id in ids
        assert stale.id not in ids

    def test_list_active_cleans_up_expired_agents(self):
        registry = make_registry(expiry_seconds=60)
        stale = register_agent(registry)
        registry._agents[stale.id].last_activity = datetime.now(UTC) - timedelta(seconds=120)

        registry.list_active()
        assert stale.id not in registry._agents

    def test_list_active_returns_list_type(self):
        registry = make_registry()
        assert isinstance(registry.list_active(), list)

    def test_list_active_after_all_expire(self):
        registry = make_registry(expiry_seconds=60)
        for _ in range(3):
            a = register_agent(registry)
            registry._agents[a.id].last_activity = datetime.now(UTC) - timedelta(seconds=120)
        assert registry.list_active() == []

    def test_list_active_returns_new_list_each_call(self):
        """Mutating the returned list must not affect the registry."""
        registry = make_registry()
        register_agent(registry)
        l1 = registry.list_active()
        l1.clear()
        l2 = registry.list_active()
        assert len(l2) == 1


# ---------------------------------------------------------------------------
# AgentRegistry.update_progress
# ---------------------------------------------------------------------------


class TestAgentRegistryUpdateProgress:
    """Tests for AgentRegistry.update_progress()."""

    def test_update_progress_returns_agent(self):
        registry = make_registry()
        agent = register_agent(registry)
        result = registry.update_progress(agent.id, progress=50)
        assert result is not None
        assert result.id == agent.id

    def test_update_progress_sets_progress_value(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=75)
        assert registry.get(agent.id).progress == 75

    def test_update_progress_updates_last_activity(self):
        registry = make_registry()
        agent = register_agent(registry)
        old_ts = agent.last_activity
        time.sleep(0.02)
        registry.update_progress(agent.id, progress=10)
        updated = registry.get(agent.id)
        assert updated.last_activity > old_ts

    def test_update_progress_sets_current_task(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=20, current_task="Writing tests")
        assert registry.get(agent.id).current_task == "Writing tests"

    def test_update_progress_appends_files_modified_no_duplicates(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=10, files_modified=["a.py", "b.py"])
        registry.update_progress(agent.id, progress=20, files_modified=["b.py", "c.py"])
        stored = registry.get(agent.id)
        assert stored.files_modified == ["a.py", "b.py", "c.py"]

    def test_update_progress_accumulates_token_usage(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=10, token_usage={"input": 100, "output": 50})
        registry.update_progress(agent.id, progress=20, token_usage={"input": 200, "output": 75})
        stored = registry.get(agent.id)
        assert stored.token_usage == {"input": 300, "output": 125}

    def test_update_progress_returns_none_for_missing_agent(self):
        registry = make_registry()
        result = registry.update_progress("nonexistent", progress=50)
        assert result is None

    def test_update_progress_none_current_task_does_not_overwrite(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=10, current_task="task A")
        registry.update_progress(agent.id, progress=20, current_task=None)
        assert registry.get(agent.id).current_task == "task A"

    def test_update_progress_none_files_does_not_modify_list(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=10, files_modified=["a.py"])
        registry.update_progress(agent.id, progress=20, files_modified=None)
        assert registry.get(agent.id).files_modified == ["a.py"]

    def test_update_progress_zero_is_valid(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=50)
        result = registry.update_progress(agent.id, progress=0)
        assert result.progress == 0

    def test_update_progress_100_is_valid(self):
        registry = make_registry()
        agent = register_agent(registry)
        result = registry.update_progress(agent.id, progress=100)
        assert result.progress == 100

    def test_update_progress_empty_token_usage_dict(self):
        registry = make_registry()
        agent = register_agent(registry)
        result = registry.update_progress(agent.id, progress=10, token_usage={})
        assert result is not None
        assert result.token_usage == {}

    def test_update_progress_adds_new_token_key(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=10, token_usage={"cache_read": 500})
        stored = registry.get(agent.id)
        assert stored.token_usage["cache_read"] == 500


# ---------------------------------------------------------------------------
# AgentRegistry.complete
# ---------------------------------------------------------------------------


class TestAgentRegistryComplete:
    """Tests for AgentRegistry.complete()."""

    def test_complete_returns_agent(self):
        registry = make_registry()
        agent = register_agent(registry)
        result = registry.complete(agent.id)
        assert result is not None

    def test_complete_sets_status_to_completed(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.complete(agent.id)
        assert registry.get(agent.id).status == "completed"

    def test_complete_sets_progress_to_100(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.update_progress(agent.id, progress=60)
        registry.complete(agent.id)
        assert registry.get(agent.id).progress == 100

    def test_complete_updates_last_activity(self):
        registry = make_registry()
        agent = register_agent(registry)
        old_ts = agent.last_activity
        time.sleep(0.02)
        registry.complete(agent.id)
        updated = registry.get(agent.id)
        assert updated.last_activity > old_ts

    def test_complete_with_summary_appends_message(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.complete(agent.id, summary="All tests passed")
        stored = registry.get(agent.id)
        assert len(stored.messages) == 1
        msg = stored.messages[0]
        assert msg["type"] == "completion"
        assert msg["content"] == "All tests passed"
        assert "timestamp" in msg

    def test_complete_without_summary_no_message(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.complete(agent.id, summary=None)
        assert registry.get(agent.id).messages == []

    def test_complete_missing_agent_returns_none(self):
        registry = make_registry()
        result = registry.complete("nonexistent")
        assert result is None

    def test_complete_then_list_active_still_includes_agent(self):
        """Completed agents are not removed until they expire."""
        registry = make_registry(expiry_seconds=300)
        agent = register_agent(registry)
        registry.complete(agent.id)
        active = registry.list_active()
        assert any(a.id == agent.id for a in active)


# ---------------------------------------------------------------------------
# AgentRegistry.pause / resume
# ---------------------------------------------------------------------------


class TestAgentRegistryPauseResume:
    """Tests for AgentRegistry.pause() and AgentRegistry.resume()."""

    def test_pause_sets_status_to_paused(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.pause(agent.id)
        assert registry.get(agent.id).status == "paused"

    def test_pause_returns_agent_and_previous_status(self):
        registry = make_registry()
        agent = register_agent(registry)
        result_agent, prev_status = registry.pause(agent.id)
        assert result_agent is not None
        assert prev_status == "active"

    def test_pause_missing_agent_returns_none_none(self):
        registry = make_registry()
        agent, status = registry.pause("nonexistent")
        assert agent is None
        assert status is None

    def test_pause_updates_last_activity(self):
        registry = make_registry()
        agent = register_agent(registry)
        old_ts = agent.last_activity
        time.sleep(0.02)
        registry.pause(agent.id)
        assert registry.get(agent.id).last_activity > old_ts

    def test_pause_already_paused_agent(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.pause(agent.id)
        result_agent, prev_status = registry.pause(agent.id)
        assert result_agent is not None
        assert prev_status == "paused"
        assert registry.get(agent.id).status == "paused"

    def test_resume_sets_status_to_active(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.pause(agent.id)
        registry.resume(agent.id)
        assert registry.get(agent.id).status == "active"

    def test_resume_returns_agent_and_previous_status(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.pause(agent.id)
        result_agent, prev_status = registry.resume(agent.id)
        assert result_agent is not None
        assert prev_status == "paused"

    def test_resume_missing_agent_returns_none_none(self):
        registry = make_registry()
        agent, status = registry.resume("nonexistent")
        assert agent is None
        assert status is None

    def test_resume_updates_last_activity(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.pause(agent.id)
        old_ts = registry.get(agent.id).last_activity
        time.sleep(0.02)
        registry.resume(agent.id)
        assert registry.get(agent.id).last_activity > old_ts

    def test_pause_resume_cycle_multiple_times(self):
        registry = make_registry()
        agent = register_agent(registry)
        for _ in range(5):
            registry.pause(agent.id)
            assert registry.get(agent.id).status == "paused"
            registry.resume(agent.id)
            assert registry.get(agent.id).status == "active"

    def test_resume_active_agent_keeps_active(self):
        """Resuming an already-active agent should keep it active."""
        registry = make_registry()
        agent = register_agent(registry)
        result_agent, prev_status = registry.resume(agent.id)
        assert prev_status == "active"
        assert result_agent.status == "active"


# ---------------------------------------------------------------------------
# AgentRegistry.kill
# ---------------------------------------------------------------------------


class TestAgentRegistryKill:
    """Tests for AgentRegistry.kill()."""

    def test_kill_sets_status_to_failed(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.kill(agent.id)
        assert registry.get(agent.id).status == "failed"

    def test_kill_returns_agent_and_previous_status(self):
        registry = make_registry()
        agent = register_agent(registry)
        result_agent, prev_status = registry.kill(agent.id)
        assert result_agent is not None
        assert prev_status == "active"

    def test_kill_missing_agent_returns_none_none(self):
        registry = make_registry()
        agent, status = registry.kill("nonexistent")
        assert agent is None
        assert status is None

    def test_kill_with_reason_appends_message(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.kill(agent.id, reason="OOM kill signal")
        stored = registry.get(agent.id)
        assert len(stored.messages) == 1
        msg = stored.messages[0]
        assert msg["type"] == "kill"
        assert msg["content"] == "OOM kill signal"
        assert "timestamp" in msg

    def test_kill_without_reason_no_message(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.kill(agent.id, reason=None)
        assert registry.get(agent.id).messages == []

    def test_kill_updates_last_activity(self):
        registry = make_registry()
        agent = register_agent(registry)
        old_ts = agent.last_activity
        time.sleep(0.02)
        registry.kill(agent.id)
        assert registry.get(agent.id).last_activity > old_ts

    def test_killed_agent_remains_in_registry(self):
        """kill() marks as failed but does NOT remove from registry."""
        registry = make_registry()
        agent = register_agent(registry)
        registry.kill(agent.id)
        assert registry.get(agent.id) is not None

    def test_kill_paused_agent_sets_failed(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.pause(agent.id)
        result_agent, prev_status = registry.kill(agent.id)
        assert prev_status == "paused"
        assert result_agent.status == "failed"

    def test_kill_completed_agent_returns_previous_status(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.complete(agent.id)
        _, prev_status = registry.kill(agent.id)
        assert prev_status == "completed"


# ---------------------------------------------------------------------------
# AgentRegistry.send_message
# ---------------------------------------------------------------------------


class TestAgentRegistrySendMessage:
    """Tests for AgentRegistry.send_message().

    The registry does a lazy import inside send_message:
        from .messaging import get_message_queue

    Since there is no `messaging` module (the real module is `message_queue`),
    we inject a fake module into sys.modules so the lazy import resolves to our mock.
    """

    _MESSAGING_KEY = "forge_harness.webhook_server.services.messaging"

    def _make_mock_queue(self, msg_id: str = "mock-message-id-123"):
        mock_msg = MagicMock()
        mock_msg.id = msg_id
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = mock_msg
        return mock_queue, mock_msg

    def _inject_queue(self, mock_queue: MagicMock) -> None:
        """Insert fake messaging module so the lazy import resolves to mock_queue."""
        fake_mod = _make_messaging_mock(mock_queue)
        sys.modules[self._MESSAGING_KEY] = fake_mod

    def _remove_queue(self) -> None:
        sys.modules.pop(self._MESSAGING_KEY, None)

    def test_send_message_returns_agent_and_message_id(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        self._inject_queue(mock_queue)
        try:
            result_agent, message_id = registry.send_message(
                agent.id, {"type": "instruction", "content": "Do task"}
            )
        finally:
            self._remove_queue()

        assert result_agent is not None
        assert message_id == "mock-message-id-123"

    def test_send_message_appends_to_agent_messages(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        self._inject_queue(mock_queue)
        try:
            registry.send_message(agent.id, {"type": "notification", "content": "Hello"})
        finally:
            self._remove_queue()

        stored = registry.get(agent.id)
        assert len(stored.messages) == 1
        assert stored.messages[0]["type"] == "notification"
        assert stored.messages[0]["content"] == "Hello"

    def test_send_message_adds_timestamp_to_message(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        self._inject_queue(mock_queue)
        try:
            registry.send_message(agent.id, {"type": "info", "content": "Status update"})
        finally:
            self._remove_queue()

        stored = registry.get(agent.id)
        assert "timestamp" in stored.messages[0]

    def test_send_message_adds_message_id_to_message(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue("known-msg-id")
        self._inject_queue(mock_queue)
        try:
            registry.send_message(agent.id, {"type": "cmd", "content": "go"})
        finally:
            self._remove_queue()

        stored = registry.get(agent.id)
        assert stored.messages[0]["message_id"] == "known-msg-id"

    def test_send_message_missing_agent_returns_none_none(self):
        registry = make_registry()
        # No queue injection needed — early return before queue is accessed
        result_agent, message_id = registry.send_message("nonexistent", {"content": "x"})
        assert result_agent is None
        assert message_id is None

    def test_send_message_calls_queue_enqueue_with_correct_params(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        self._inject_queue(mock_queue)
        msg = {"type": "instruction", "content": "Do thing", "priority": 2, "metadata": {}}
        try:
            registry.send_message(agent.id, msg, sender_id="orchestrator", use_queue=True)
        finally:
            self._remove_queue()

        mock_queue.enqueue.assert_called_once_with(
            sender_id="orchestrator",
            recipient_id=agent.id,
            content="Do thing",
            msg_type="instruction",
            priority=2,
            metadata={},
        )

    def test_send_message_use_queue_false_does_not_access_queue(self):
        """use_queue=False: no queue import or enqueue call occurs."""
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        # Do NOT inject fake module — if queue were accessed it would raise ImportError
        result_agent, message_id = registry.send_message(
            agent.id, {"type": "note", "content": "hi"}, use_queue=False
        )
        assert result_agent is not None
        assert message_id is None

    def test_send_message_use_queue_false_message_id_is_none(self):
        registry = make_registry()
        agent = register_agent(registry)

        result_agent, message_id = registry.send_message(
            agent.id, {"type": "note", "content": "hi"}, use_queue=False
        )
        assert result_agent is not None
        assert message_id is None

    def test_send_message_default_type_is_instruction(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        self._inject_queue(mock_queue)
        try:
            registry.send_message(agent.id, {"content": "no type set"})
        finally:
            self._remove_queue()

        call_kwargs = mock_queue.enqueue.call_args[1]
        assert call_kwargs["msg_type"] == "instruction"

    def test_send_message_default_priority_is_1(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        self._inject_queue(mock_queue)
        try:
            registry.send_message(agent.id, {"content": "no priority set"})
        finally:
            self._remove_queue()

        call_kwargs = mock_queue.enqueue.call_args[1]
        assert call_kwargs["priority"] == 1

    def test_send_multiple_messages_to_same_agent(self):
        registry = make_registry()
        agent = register_agent(registry)

        for i in range(3):
            registry.send_message(
                agent.id,
                {"type": "instruction", "content": f"step {i}"},
                use_queue=False,
            )

        stored = registry.get(agent.id)
        assert len(stored.messages) == 3

    def test_send_message_metadata_forwarded_to_queue(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        self._inject_queue(mock_queue)
        msg = {"type": "cmd", "content": "do", "metadata": {"key": "value"}}
        try:
            registry.send_message(agent.id, msg)
        finally:
            self._remove_queue()

        call_kwargs = mock_queue.enqueue.call_args[1]
        assert call_kwargs["metadata"] == {"key": "value"}

    def test_send_message_default_sender_is_system(self):
        registry = make_registry()
        agent = register_agent(registry)
        mock_queue, _ = self._make_mock_queue()
        self._inject_queue(mock_queue)
        try:
            registry.send_message(agent.id, {"content": "hi"})
        finally:
            self._remove_queue()

        call_kwargs = mock_queue.enqueue.call_args[1]
        assert call_kwargs["sender_id"] == "system"


# ---------------------------------------------------------------------------
# AgentRegistry.broadcast
# ---------------------------------------------------------------------------


class TestAgentRegistryBroadcast:
    """Tests for AgentRegistry.broadcast()."""

    def test_broadcast_returns_count_of_agents_messaged(self):
        registry = make_registry()
        for _ in range(4):
            register_agent(registry)
        count = registry.broadcast({"type": "alert", "content": "Shutdown in 5 min"})
        assert count == 4

    def test_broadcast_returns_zero_for_empty_registry(self):
        registry = make_registry()
        count = registry.broadcast({"type": "alert", "content": "hello"})
        assert count == 0

    def test_broadcast_appends_message_to_all_agents(self):
        registry = make_registry()
        agents = [register_agent(registry) for _ in range(3)]
        registry.broadcast({"type": "system", "content": "Maintenance"})
        for agent in agents:
            stored = registry.get(agent.id)
            assert any(m["content"] == "Maintenance" for m in stored.messages)

    def test_broadcast_adds_timestamp_to_message(self):
        registry = make_registry()
        register_agent(registry)
        registry.broadcast({"type": "system", "content": "test"})
        stored = registry.list_active()[0]
        assert "timestamp" in stored.messages[0]

    def test_broadcast_message_is_independent_copy_per_agent(self):
        """Each agent receives its own copy, not a shared reference."""
        registry = make_registry()
        a1 = register_agent(registry)
        a2 = register_agent(registry)
        registry.broadcast({"type": "info", "content": "update"})

        stored_a1 = registry.get(a1.id)
        stored_a2 = registry.get(a2.id)
        # Mutating a1's message dict should not affect a2's
        stored_a1.messages[0]["extra"] = "modified"
        assert "extra" not in stored_a2.messages[0]

    def test_broadcast_excludes_expired_agents(self):
        """Expired agents are cleaned up before broadcast."""
        registry = make_registry(expiry_seconds=60)
        alive = register_agent(registry)
        stale = register_agent(registry)
        registry._agents[stale.id].last_activity = datetime.now(UTC) - timedelta(seconds=120)

        count = registry.broadcast({"type": "info", "content": "test"})
        assert count == 1
        assert len(registry.get(alive.id).messages) == 1

    def test_broadcast_single_agent(self):
        registry = make_registry()
        agent = register_agent(registry)
        count = registry.broadcast({"type": "ping", "content": "alive?"})
        assert count == 1
        assert len(registry.get(agent.id).messages) == 1


# ---------------------------------------------------------------------------
# AgentRegistry._cleanup_expired (internal)
# ---------------------------------------------------------------------------


class TestCleanupExpired:
    """Tests for AgentRegistry._cleanup_expired()."""

    def test_cleanup_removes_expired_agents(self):
        registry = make_registry(expiry_seconds=60)
        a1 = register_agent(registry)
        a2 = register_agent(registry)
        registry._agents[a2.id].last_activity = datetime.now(UTC) - timedelta(seconds=120)

        count = registry._cleanup_expired()
        assert count == 1
        assert a2.id not in registry._agents
        assert a1.id in registry._agents

    def test_cleanup_returns_count_of_removed_agents(self):
        registry = make_registry(expiry_seconds=60)
        for _ in range(3):
            a = register_agent(registry)
            registry._agents[a.id].last_activity = datetime.now(UTC) - timedelta(seconds=200)

        count = registry._cleanup_expired()
        assert count == 3

    def test_cleanup_returns_zero_when_none_expired(self):
        registry = make_registry(expiry_seconds=300)
        register_agent(registry)
        count = registry._cleanup_expired()
        assert count == 0

    def test_cleanup_idempotent_on_second_call(self):
        registry = make_registry(expiry_seconds=60)
        a = register_agent(registry)
        registry._agents[a.id].last_activity = datetime.now(UTC) - timedelta(seconds=120)

        registry._cleanup_expired()
        count2 = registry._cleanup_expired()
        assert count2 == 0

    def test_cleanup_leaves_fresh_agents_alone(self):
        registry = make_registry(expiry_seconds=300)
        agents = [register_agent(registry) for _ in range(5)]
        registry._cleanup_expired()
        assert len(registry._agents) == 5
        for a in agents:
            assert a.id in registry._agents


# ---------------------------------------------------------------------------
# Singleton / get_agent_registry
# ---------------------------------------------------------------------------


class TestGetAgentRegistry:
    """Tests for the get_agent_registry() singleton factory."""

    def test_get_agent_registry_returns_instance(self):
        import forge_harness.webhook_server.services.registry as reg_module

        reg_module._agent_registry = None
        registry = get_agent_registry()
        assert isinstance(registry, AgentRegistry)

    def test_get_agent_registry_returns_same_instance(self):
        import forge_harness.webhook_server.services.registry as reg_module

        reg_module._agent_registry = None
        r1 = get_agent_registry()
        r2 = get_agent_registry()
        assert r1 is r2

    def test_get_agent_registry_after_reset_creates_new_instance(self):
        import forge_harness.webhook_server.services.registry as reg_module

        reg_module._agent_registry = None
        r1 = get_agent_registry()
        reg_module._agent_registry = None
        r2 = get_agent_registry()
        assert r1 is not r2

    def test_singleton_preserves_registered_agents(self):
        import forge_harness.webhook_server.services.registry as reg_module

        reg_module._agent_registry = None
        r1 = get_agent_registry()
        agent = r1.register(role="test", project="p", task="t")

        r2 = get_agent_registry()
        assert r2.get(agent.id) is not None

    def test_get_agent_registry_default_expiry(self):
        import forge_harness.webhook_server.services.registry as reg_module

        reg_module._agent_registry = None
        registry = get_agent_registry()
        # Default is 300 seconds (5 minutes)
        assert registry._expiry_seconds == 300


# ---------------------------------------------------------------------------
# Thread-safety tests
# ---------------------------------------------------------------------------


class TestAgentRegistryThreadSafety:
    """Concurrency tests for AgentRegistry under parallel access."""

    def test_concurrent_registrations_produce_unique_ids(self):
        registry = make_registry()
        results: list[str] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def do_register():
            try:
                agent = register_agent(registry)
                with lock:
                    results.append(agent.id)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=do_register) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 50
        assert len(set(results)) == 50  # all unique

    def test_concurrent_reads_and_writes(self):
        """Simultaneous reads and writes must not corrupt data."""
        registry = make_registry()
        agents = [register_agent(registry) for _ in range(10)]
        errors: list[Exception] = []
        lock = threading.Lock()

        def writer():
            try:
                for agent in agents:
                    registry.update_progress(agent.id, progress=50)
            except Exception as e:
                with lock:
                    errors.append(e)

        def reader():
            try:
                for _ in range(20):
                    registry.list_active()
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(5)]
        threads += [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_pause_resume_does_not_crash(self):
        registry = make_registry()
        agent = register_agent(registry)
        errors: list[Exception] = []
        lock = threading.Lock()

        def toggle():
            try:
                for _ in range(10):
                    registry.pause(agent.id)
                    registry.resume(agent.id)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=toggle) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        final_status = registry.get(agent.id).status
        assert final_status in ("active", "paused")

    def test_concurrent_sends_accumulate_all_messages(self):
        registry = make_registry()
        agent = register_agent(registry)
        errors: list[Exception] = []
        lock = threading.Lock()

        def send_msg():
            try:
                registry.send_message(
                    agent.id,
                    {"type": "note", "content": "concurrent"},
                    use_queue=False,
                )
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=send_msg) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        stored = registry.get(agent.id)
        assert len(stored.messages) == 30

    def test_concurrent_cleanup_does_not_raise(self):
        registry = make_registry(expiry_seconds=60)
        for _ in range(20):
            a = register_agent(registry)
            registry._agents[a.id].last_activity = datetime.now(UTC) - timedelta(seconds=120)

        errors: list[Exception] = []
        lock = threading.Lock()

        def do_cleanup():
            try:
                registry.list_active()
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=do_cleanup) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_broadcast_does_not_crash(self):
        registry = make_registry()
        for _ in range(10):
            register_agent(registry)
        errors: list[Exception] = []
        lock = threading.Lock()

        def do_broadcast():
            try:
                registry.broadcast({"type": "ping", "content": "hello"})
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=do_broadcast) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


class TestAgentRegistryEdgeCases:
    """Miscellaneous edge cases."""

    def test_register_with_empty_string_role(self):
        registry = make_registry()
        agent = register_agent(registry, role="")
        assert agent.role == ""

    def test_register_with_empty_string_project(self):
        registry = make_registry()
        agent = register_agent(registry, project="")
        assert agent.project == ""

    def test_get_empty_string_id_returns_none(self):
        registry = make_registry()
        assert registry.get("") is None

    def test_kill_then_get_returns_failed_status(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.kill(agent.id, reason="manual kill")
        stored = registry.get(agent.id)
        assert stored.status == "failed"

    def test_complete_then_update_progress_still_works(self):
        """update_progress does not guard against completed status."""
        registry = make_registry()
        agent = register_agent(registry)
        registry.complete(agent.id)
        result = registry.update_progress(agent.id, progress=80)
        assert result is not None
        assert result.progress == 80

    def test_three_level_hierarchy(self):
        """grandparent -> parent -> child hierarchy tracking."""
        registry = make_registry()
        grandparent = register_agent(registry, role="lead")
        parent = register_agent(registry, role="mid", parent_id=grandparent.id)
        child = register_agent(registry, role="leaf", parent_id=parent.id)

        gp = registry.get(grandparent.id)
        p = registry.get(parent.id)

        assert parent.id in gp.children
        assert child.id in p.children
        assert child.parent_id == parent.id

    def test_registry_expiry_seconds_configurable(self):
        registry = make_registry(expiry_seconds=10)
        assert registry._expiry_seconds == 10

    def test_agent_session_children_empty_by_default(self):
        registry = make_registry()
        agent = register_agent(registry)
        assert registry.get(agent.id).children == []

    def test_broadcast_mutates_original_message_with_timestamp(self):
        """Broadcast adds timestamp to the passed message dict."""
        registry = make_registry()
        register_agent(registry)
        msg: dict[str, Any] = {"type": "info", "content": "original"}
        registry.broadcast(msg)
        # The original dict gets timestamp added during broadcast
        assert "timestamp" in msg

    def test_pause_then_kill_preserves_paused_as_previous_status(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.pause(agent.id)
        _, prev = registry.kill(agent.id)
        assert prev == "paused"

    def test_files_modified_deduplication(self):
        registry = make_registry()
        agent = register_agent(registry)
        # Add same file many times across calls
        for _ in range(5):
            registry.update_progress(agent.id, progress=10, files_modified=["same_file.py"])
        stored = registry.get(agent.id)
        assert stored.files_modified.count("same_file.py") == 1

    def test_token_usage_new_keys_initialized_at_zero(self):
        registry = make_registry()
        agent = register_agent(registry)
        # First update for a brand-new key
        registry.update_progress(agent.id, progress=5, token_usage={"new_key": 100})
        stored = registry.get(agent.id)
        assert stored.token_usage["new_key"] == 100

    def test_complete_message_timestamp_is_iso_string(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.complete(agent.id, summary="done")
        msg = registry.get(agent.id).messages[0]
        # Should be parseable as ISO datetime
        datetime.fromisoformat(msg["timestamp"])

    def test_kill_message_timestamp_is_iso_string(self):
        registry = make_registry()
        agent = register_agent(registry)
        registry.kill(agent.id, reason="test")
        msg = registry.get(agent.id).messages[0]
        datetime.fromisoformat(msg["timestamp"])
