"""
Unit tests for forge_harness.webhook_server.services.agent_registry

Covers:
- AgentSession dataclass: to_dict, is_expired, is_stale
- AgentRegistry: register, get, list_active, update_progress, complete,
  pause, resume, kill, send_message, broadcast, _cleanup_expired
- Module-level singleton: get_agent_registry
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from threading import Thread
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import forge_harness.webhook_server.services.agent_registry as _registry_module

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.services.agent_registry import (
    AgentRegistry,
    AgentSession,
    get_agent_registry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(**kwargs: Any) -> AgentSession:
    """Return a fresh AgentSession with sensible defaults."""
    defaults: dict[str, Any] = dict(
        id="abc12345",
        role="feature-dev",
        project="codeswiftr-com/interview-simulator",
        task="implement auth",
    )
    defaults.update(kwargs)
    return AgentSession(**defaults)


def _registry_with_agent(**kwargs: Any) -> tuple[AgentRegistry, AgentSession]:
    """Return a fresh registry that already contains one registered agent."""
    reg = AgentRegistry()
    agent = reg.register(
        role=kwargs.get("role", "feature-dev"),
        project=kwargs.get("project", "codeswiftr-com/is"),
        task=kwargs.get("task", "do work"),
        name=kwargs.get("name"),
        domain=kwargs.get("domain"),
        parent_id=kwargs.get("parent_id"),
        tmux_session=kwargs.get("tmux_session"),
        skills=kwargs.get("skills"),
    )
    return reg, agent


# ===========================================================================
# AgentSession tests
# ===========================================================================

class TestAgentSessionDefaults:
    """Test default field values and construction."""

    def test_required_fields_stored(self) -> None:
        s = _make_session()
        assert s.id == "abc12345"
        assert s.role == "feature-dev"
        assert s.project == "codeswiftr-com/interview-simulator"
        assert s.task == "implement auth"

    def test_optional_fields_default_to_none_or_empty(self) -> None:
        s = _make_session()
        assert s.name is None
        assert s.domain is None
        assert s.parent_id is None
        assert s.children == []
        assert s.tmux_session is None
        assert s.skills == []
        assert s.current_task is None

    def test_status_defaults_to_active(self) -> None:
        s = _make_session()
        assert s.status == "active"

    def test_progress_defaults_to_zero(self) -> None:
        s = _make_session()
        assert s.progress == 0

    def test_mutable_defaults_are_independent(self) -> None:
        """Default list/dict fields must NOT be shared across instances."""
        s1 = _make_session(id="aaa")
        s2 = _make_session(id="bbb")
        s1.children.append("x")
        assert "x" not in s2.children

    def test_timestamps_are_utc_aware(self) -> None:
        s = _make_session()
        assert s.registered_at.tzinfo is not None
        assert s.last_activity.tzinfo is not None


# ===========================================================================
# AgentSession.to_dict
# ===========================================================================

class TestAgentSessionToDict:
    """Verify to_dict output shape and correctness."""

    def test_to_dict_contains_all_keys(self) -> None:
        s = _make_session(name="kimi", domain="codeswiftr-com", tmux_session="forge:kimi")
        d = s.to_dict()
        expected_keys = {
            "id", "role", "name", "domain", "project", "task",
            "parent_id", "children", "tmux_session", "skills",
            "status", "progress", "current_task", "files_modified",
            "token_usage", "messages_count", "registered_at",
            "last_activity", "is_stale",
        }
        assert expected_keys == set(d.keys())

    def test_to_dict_messages_count_reflects_list_length(self) -> None:
        s = _make_session()
        s.messages.append({"type": "test", "content": "hello"})
        s.messages.append({"type": "test", "content": "world"})
        assert s.to_dict()["messages_count"] == 2

    def test_to_dict_timestamps_are_iso_strings(self) -> None:
        s = _make_session()
        d = s.to_dict()
        # Should not raise
        datetime.fromisoformat(d["registered_at"])
        datetime.fromisoformat(d["last_activity"])

    def test_to_dict_is_stale_field_is_bool(self) -> None:
        s = _make_session()
        assert isinstance(s.to_dict()["is_stale"], bool)

    def test_to_dict_optional_fields_none_when_unset(self) -> None:
        s = _make_session()
        d = s.to_dict()
        assert d["name"] is None
        assert d["domain"] is None
        assert d["parent_id"] is None
        assert d["tmux_session"] is None
        assert d["current_task"] is None

    def test_to_dict_skills_propagated(self) -> None:
        s = _make_session(skills=["dispatch", "review"])
        assert s.to_dict()["skills"] == ["dispatch", "review"]


# ===========================================================================
# AgentSession.is_expired
# ===========================================================================

class TestAgentSessionIsExpired:
    """Test the is_expired predicate."""

    def test_fresh_session_not_expired(self) -> None:
        s = _make_session()
        assert s.is_expired(timeout_seconds=300) is False

    def test_old_session_is_expired(self) -> None:
        s = _make_session()
        s.last_activity = datetime.now(UTC) - timedelta(seconds=400)
        assert s.is_expired(timeout_seconds=300) is True

    def test_just_under_boundary_is_not_expired(self) -> None:
        s = _make_session()
        # Set last_activity to 1 second LESS than the timeout — still live
        s.last_activity = datetime.now(UTC) - timedelta(seconds=299)
        assert s.is_expired(timeout_seconds=300) is False

    def test_custom_timeout(self) -> None:
        s = _make_session()
        s.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        assert s.is_expired(timeout_seconds=5) is True
        assert s.is_expired(timeout_seconds=60) is False


# ===========================================================================
# AgentSession.is_stale
# ===========================================================================

class TestAgentSessionIsStale:
    """Test the is_stale predicate."""

    def test_fresh_session_not_stale(self) -> None:
        s = _make_session()
        assert s.is_stale(heartbeat_timeout_seconds=120) is False

    def test_old_session_is_stale(self) -> None:
        s = _make_session()
        s.last_activity = datetime.now(UTC) - timedelta(seconds=200)
        assert s.is_stale(heartbeat_timeout_seconds=120) is True

    def test_custom_stale_timeout(self) -> None:
        s = _make_session()
        s.last_activity = datetime.now(UTC) - timedelta(seconds=30)
        assert s.is_stale(heartbeat_timeout_seconds=20) is True
        assert s.is_stale(heartbeat_timeout_seconds=60) is False


# ===========================================================================
# AgentRegistry.register
# ===========================================================================

class TestAgentRegistryRegister:
    """Test register() method."""

    def test_register_returns_agent_session(self) -> None:
        reg = AgentRegistry()
        agent = reg.register(role="debug", project="proj/x", task="fix bug")
        assert isinstance(agent, AgentSession)

    def test_register_stores_correct_fields(self) -> None:
        reg = AgentRegistry()
        agent = reg.register(
            role="review",
            project="proj/y",
            task="review PR",
            name="kimi",
            domain="codeswiftr-com",
            tmux_session="forge:kimi",
            skills=["review", "dispatch"],
        )
        assert agent.role == "review"
        assert agent.project == "proj/y"
        assert agent.task == "review PR"
        assert agent.name == "kimi"
        assert agent.domain == "codeswiftr-com"
        assert agent.tmux_session == "forge:kimi"
        assert agent.skills == ["review", "dispatch"]

    def test_register_generates_unique_ids(self) -> None:
        reg = AgentRegistry()
        ids = {reg.register(role="r", project="p", task="t").id for _ in range(20)}
        assert len(ids) == 20

    def test_register_id_is_8_chars(self) -> None:
        reg = AgentRegistry()
        agent = reg.register(role="r", project="p", task="t")
        assert len(agent.id) == 8

    def test_register_skills_defaults_to_empty_list(self) -> None:
        reg = AgentRegistry()
        agent = reg.register(role="r", project="p", task="t")
        assert agent.skills == []

    def test_register_with_parent_updates_parent_children(self) -> None:
        reg = AgentRegistry()
        parent = reg.register(role="orchestrator", project="p", task="t")
        child = reg.register(role="worker", project="p", task="sub", parent_id=parent.id)
        assert child.id in reg.get(parent.id).children

    def test_register_with_nonexistent_parent_does_not_raise(self) -> None:
        reg = AgentRegistry()
        # Should not raise even if parent_id doesn't exist
        agent = reg.register(role="r", project="p", task="t", parent_id="ghost-id")
        assert agent.parent_id == "ghost-id"

    def test_register_multiple_children_tracked(self) -> None:
        reg = AgentRegistry()
        parent = reg.register(role="orch", project="p", task="t")
        c1 = reg.register(role="w", project="p", task="t", parent_id=parent.id)
        c2 = reg.register(role="w", project="p", task="t", parent_id=parent.id)
        children = reg.get(parent.id).children
        assert c1.id in children
        assert c2.id in children


# ===========================================================================
# AgentRegistry.get
# ===========================================================================

class TestAgentRegistryGet:
    """Test get() lookup including alternative lookup paths."""

    def test_get_by_id_returns_agent(self) -> None:
        reg, agent = _registry_with_agent()
        found = reg.get(agent.id)
        assert found is not None
        assert found.id == agent.id

    def test_get_unknown_id_returns_none(self) -> None:
        reg = AgentRegistry()
        assert reg.get("does-not-exist") is None

    def test_get_expired_agent_removes_and_returns_none(self) -> None:
        reg = AgentRegistry(expiry_seconds=1)
        agent = reg.register(role="r", project="p", task="t")
        # Force expiry
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        assert reg.get(agent.id) is None
        # Also removed from internal dict
        assert agent.id not in reg._agents

    def test_get_by_tmux_session(self) -> None:
        reg, agent = _registry_with_agent(tmux_session="forge:opencode")
        found = reg.get("forge:opencode")
        assert found is not None
        assert found.id == agent.id

    def test_get_by_tmux_session_expired_returns_none(self) -> None:
        reg = AgentRegistry(expiry_seconds=1)
        agent = reg.register(role="r", project="p", task="t", tmux_session="forge:z")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        assert reg.get("forge:z") is None

    def test_get_by_name_case_insensitive(self) -> None:
        reg, agent = _registry_with_agent(name="Kimi")
        found = reg.get("kimi")
        assert found is not None
        assert found.id == agent.id

    def test_get_by_name_expired_returns_none(self) -> None:
        reg = AgentRegistry(expiry_seconds=1)
        agent = reg.register(role="r", project="p", task="t", name="Stale")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        assert reg.get("stale") is None

    def test_get_agent_without_name_skipped_in_name_lookup(self) -> None:
        reg = AgentRegistry()
        reg.register(role="r", project="p", task="t")  # no name
        assert reg.get("anyrandomname") is None

    def test_get_does_not_remove_valid_agent(self) -> None:
        reg, agent = _registry_with_agent()
        reg.get(agent.id)
        assert reg.get(agent.id) is not None


# ===========================================================================
# AgentRegistry.list_active
# ===========================================================================

class TestAgentRegistryListActive:
    """Test list_active() including expired cleanup."""

    def test_empty_registry_returns_empty_list(self) -> None:
        reg = AgentRegistry()
        assert reg.list_active() == []

    def test_registered_agent_appears_in_list(self) -> None:
        reg, agent = _registry_with_agent()
        active = reg.list_active()
        assert any(a.id == agent.id for a in active)

    def test_expired_agents_excluded_from_list(self) -> None:
        reg = AgentRegistry(expiry_seconds=1)
        agent = reg.register(role="r", project="p", task="t")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        assert reg.list_active() == []

    def test_only_live_agents_returned(self) -> None:
        reg = AgentRegistry(expiry_seconds=300)
        live = reg.register(role="r", project="p", task="t")
        expired = reg.register(role="r", project="p", task="t")
        expired.last_activity = datetime.now(UTC) - timedelta(seconds=400)
        active_ids = {a.id for a in reg.list_active()}
        assert live.id in active_ids
        assert expired.id not in active_ids


# ===========================================================================
# AgentRegistry.update_progress
# ===========================================================================

class TestAgentRegistryUpdateProgress:
    """Test update_progress()."""

    def test_update_progress_returns_agent(self) -> None:
        reg, agent = _registry_with_agent()
        result = reg.update_progress(agent.id, progress=50)
        assert result is not None
        assert result.id == agent.id

    def test_update_progress_unknown_agent_returns_none(self) -> None:
        reg = AgentRegistry()
        assert reg.update_progress("ghost", progress=50) is None

    def test_update_progress_sets_progress(self) -> None:
        reg, agent = _registry_with_agent()
        reg.update_progress(agent.id, progress=75)
        assert reg.get(agent.id).progress == 75

    def test_update_progress_sets_current_task(self) -> None:
        reg, agent = _registry_with_agent()
        reg.update_progress(agent.id, progress=10, current_task="writing tests")
        assert reg.get(agent.id).current_task == "writing tests"

    def test_update_progress_no_current_task_leaves_existing(self) -> None:
        reg, agent = _registry_with_agent()
        reg.update_progress(agent.id, progress=10, current_task="original")
        reg.update_progress(agent.id, progress=20)  # No current_task
        assert reg.get(agent.id).current_task == "original"

    def test_update_progress_appends_new_files(self) -> None:
        reg, agent = _registry_with_agent()
        reg.update_progress(agent.id, progress=30, files_modified=["a.py", "b.py"])
        reg.update_progress(agent.id, progress=40, files_modified=["c.py"])
        assert reg.get(agent.id).files_modified == ["a.py", "b.py", "c.py"]

    def test_update_progress_deduplicates_files(self) -> None:
        reg, agent = _registry_with_agent()
        reg.update_progress(agent.id, progress=30, files_modified=["a.py"])
        reg.update_progress(agent.id, progress=40, files_modified=["a.py"])
        assert reg.get(agent.id).files_modified == ["a.py"]

    def test_update_progress_merges_token_usage(self) -> None:
        reg, agent = _registry_with_agent()
        reg.update_progress(agent.id, progress=10, token_usage={"input": 100, "output": 50})
        reg.update_progress(agent.id, progress=20, token_usage={"input": 200, "output": 30})
        usage = reg.get(agent.id).token_usage
        assert usage["input"] == 300
        assert usage["output"] == 80

    def test_update_progress_merges_token_usage_new_key(self) -> None:
        reg, agent = _registry_with_agent()
        reg.update_progress(agent.id, progress=10, token_usage={"input": 100})
        reg.update_progress(agent.id, progress=20, token_usage={"output": 50})
        usage = reg.get(agent.id).token_usage
        assert usage["input"] == 100
        assert usage["output"] == 50

    def test_update_progress_updates_last_activity(self) -> None:
        reg, agent = _registry_with_agent()
        before = agent.last_activity
        reg.update_progress(agent.id, progress=5)
        assert reg.get(agent.id).last_activity >= before


# ===========================================================================
# AgentRegistry.complete
# ===========================================================================

class TestAgentRegistryComplete:
    """Test complete()."""

    def test_complete_returns_agent(self) -> None:
        reg, agent = _registry_with_agent()
        result = reg.complete(agent.id)
        assert result is not None

    def test_complete_unknown_agent_returns_none(self) -> None:
        reg = AgentRegistry()
        assert reg.complete("ghost") is None

    def test_complete_sets_status_completed(self) -> None:
        reg, agent = _registry_with_agent()
        reg.complete(agent.id)
        assert reg.get(agent.id).status == "completed"

    def test_complete_sets_progress_100(self) -> None:
        reg, agent = _registry_with_agent()
        reg.complete(agent.id)
        assert reg.get(agent.id).progress == 100

    def test_complete_with_summary_appends_message(self) -> None:
        reg, agent = _registry_with_agent()
        reg.complete(agent.id, summary="All done")
        msgs = reg._agents[agent.id].messages
        assert len(msgs) == 1
        assert msgs[0]["type"] == "completion"
        assert msgs[0]["content"] == "All done"
        assert "timestamp" in msgs[0]

    def test_complete_without_summary_no_message_appended(self) -> None:
        reg, agent = _registry_with_agent()
        reg.complete(agent.id)
        assert reg._agents[agent.id].messages == []


# ===========================================================================
# AgentRegistry.pause
# ===========================================================================

class TestAgentRegistryPause:
    """Test pause()."""

    def test_pause_returns_agent_and_previous_status(self) -> None:
        reg, agent = _registry_with_agent()
        result_agent, prev = reg.pause(agent.id)
        assert result_agent is not None
        assert prev == "active"

    def test_pause_unknown_agent_returns_none_none(self) -> None:
        reg = AgentRegistry()
        agent, prev = reg.pause("ghost")
        assert agent is None
        assert prev is None

    def test_pause_sets_status_paused(self) -> None:
        reg, agent = _registry_with_agent()
        reg.pause(agent.id)
        assert reg._agents[agent.id].status == "paused"

    def test_pause_updates_last_activity(self) -> None:
        reg, agent = _registry_with_agent()
        before = agent.last_activity
        reg.pause(agent.id)
        assert reg._agents[agent.id].last_activity >= before

    def test_pause_captures_previous_status(self) -> None:
        reg, agent = _registry_with_agent()
        reg._agents[agent.id].status = "waiting"
        _, prev = reg.pause(agent.id)
        assert prev == "waiting"


# ===========================================================================
# AgentRegistry.resume
# ===========================================================================

class TestAgentRegistryResume:
    """Test resume()."""

    def test_resume_returns_agent_and_previous_status(self) -> None:
        reg, agent = _registry_with_agent()
        reg._agents[agent.id].status = "paused"
        result_agent, prev = reg.resume(agent.id)
        assert result_agent is not None
        assert prev == "paused"

    def test_resume_unknown_agent_returns_none_none(self) -> None:
        reg = AgentRegistry()
        agent, prev = reg.resume("ghost")
        assert agent is None
        assert prev is None

    def test_resume_sets_status_active(self) -> None:
        reg, agent = _registry_with_agent()
        reg._agents[agent.id].status = "paused"
        reg.resume(agent.id)
        assert reg._agents[agent.id].status == "active"

    def test_resume_updates_last_activity(self) -> None:
        reg, agent = _registry_with_agent()
        reg._agents[agent.id].status = "paused"
        before = agent.last_activity
        reg.resume(agent.id)
        assert reg._agents[agent.id].last_activity >= before


# ===========================================================================
# AgentRegistry.kill
# ===========================================================================

class TestAgentRegistryKill:
    """Test kill()."""

    def test_kill_returns_agent_and_previous_status(self) -> None:
        reg, agent = _registry_with_agent()
        result_agent, prev = reg.kill(agent.id)
        assert result_agent is not None
        assert prev == "active"

    def test_kill_unknown_agent_returns_none_none(self) -> None:
        reg = AgentRegistry()
        agent, prev = reg.kill("ghost")
        assert agent is None
        assert prev is None

    def test_kill_sets_status_failed(self) -> None:
        reg, agent = _registry_with_agent()
        reg.kill(agent.id)
        assert reg._agents[agent.id].status == "failed"

    def test_kill_with_reason_appends_message(self) -> None:
        reg, agent = _registry_with_agent()
        reg.kill(agent.id, reason="unresponsive")
        msgs = reg._agents[agent.id].messages
        assert len(msgs) == 1
        assert msgs[0]["type"] == "kill"
        assert msgs[0]["content"] == "unresponsive"
        assert "timestamp" in msgs[0]

    def test_kill_without_reason_no_message_appended(self) -> None:
        reg, agent = _registry_with_agent()
        reg.kill(agent.id)
        assert reg._agents[agent.id].messages == []

    def test_kill_agent_remains_in_registry(self) -> None:
        """kill() should mark as failed but NOT remove from registry."""
        reg, agent = _registry_with_agent()
        reg.kill(agent.id)
        assert agent.id in reg._agents

    def test_kill_updates_last_activity(self) -> None:
        reg, agent = _registry_with_agent()
        before = agent.last_activity
        reg.kill(agent.id)
        assert reg._agents[agent.id].last_activity >= before


# ===========================================================================
# AgentRegistry.send_message
# ===========================================================================

class TestAgentRegistrySendMessage:
    """Test send_message()."""

    def test_send_message_unknown_agent_returns_none_none(self) -> None:
        reg = AgentRegistry()
        agent, msg_id = reg.send_message("ghost", {"content": "hi"})
        assert agent is None
        assert msg_id is None

    def test_send_message_appends_to_agent_messages(self) -> None:
        reg, agent = _registry_with_agent()
        with patch(
            "forge_harness.webhook_server.services.agent_registry.get_message_queue"
        ) as mock_gq:
            mock_queue = MagicMock()
            mock_queue.enqueue.return_value = MagicMock(id="msg-uuid-123")
            mock_gq.return_value = mock_queue

            reg.send_message(agent.id, {"type": "instruction", "content": "start"})

        msgs = reg._agents[agent.id].messages
        assert len(msgs) == 1
        assert msgs[0]["type"] == "instruction"
        assert msgs[0]["content"] == "start"
        assert "timestamp" in msgs[0]

    def test_send_message_with_queue_returns_message_id(self) -> None:
        reg, agent = _registry_with_agent()
        with patch(
            "forge_harness.webhook_server.services.agent_registry.get_message_queue"
        ) as mock_gq:
            mock_queue = MagicMock()
            mock_queue.enqueue.return_value = MagicMock(id="queued-abc")
            mock_gq.return_value = mock_queue

            result_agent, msg_id = reg.send_message(
                agent.id,
                {"type": "instruction", "content": "run"},
                use_queue=True,
            )

        assert msg_id == "queued-abc"
        assert result_agent is not None

    def test_send_message_without_queue_returns_none_message_id(self) -> None:
        reg, agent = _registry_with_agent()
        result_agent, msg_id = reg.send_message(
            agent.id,
            {"type": "ping", "content": "hello"},
            use_queue=False,
        )
        assert result_agent is not None
        assert msg_id is None

    def test_send_message_without_queue_does_not_call_get_message_queue(self) -> None:
        reg, agent = _registry_with_agent()
        with patch(
            "forge_harness.webhook_server.services.agent_registry.get_message_queue"
        ) as mock_gq:
            reg.send_message(agent.id, {"content": "hi"}, use_queue=False)
            mock_gq.assert_not_called()

    def test_send_message_message_id_stored_on_message(self) -> None:
        reg, agent = _registry_with_agent()
        with patch(
            "forge_harness.webhook_server.services.agent_registry.get_message_queue"
        ) as mock_gq:
            mock_queue = MagicMock()
            mock_queue.enqueue.return_value = MagicMock(id="stored-id")
            mock_gq.return_value = mock_queue

            reg.send_message(agent.id, {"content": "test"}, use_queue=True)

        msg = reg._agents[agent.id].messages[0]
        assert msg.get("message_id") == "stored-id"

    def test_send_message_uses_defaults_for_missing_message_keys(self) -> None:
        """Enqueue should be called with fallback defaults when keys absent."""
        reg, agent = _registry_with_agent()
        with patch(
            "forge_harness.webhook_server.services.agent_registry.get_message_queue"
        ) as mock_gq:
            mock_queue = MagicMock()
            mock_queue.enqueue.return_value = MagicMock(id="x")
            mock_gq.return_value = mock_queue

            reg.send_message(agent.id, {})  # empty message

            call_kwargs = mock_queue.enqueue.call_args
            assert call_kwargs is not None
            # content defaults to ""
            assert call_kwargs.kwargs.get("content", call_kwargs.args[2] if len(call_kwargs.args) > 2 else "") == ""

    def test_send_message_sender_id_forwarded_to_queue(self) -> None:
        reg, agent = _registry_with_agent()
        with patch(
            "forge_harness.webhook_server.services.agent_registry.get_message_queue"
        ) as mock_gq:
            mock_queue = MagicMock()
            mock_queue.enqueue.return_value = MagicMock(id="y")
            mock_gq.return_value = mock_queue

            reg.send_message(
                agent.id,
                {"content": "msg"},
                sender_id="orchestrator-7",
                use_queue=True,
            )

            call_kwargs = mock_queue.enqueue.call_args
            # sender_id is the first positional or keyword arg
            assert call_kwargs.kwargs.get("sender_id") == "orchestrator-7"


# ===========================================================================
# AgentRegistry.broadcast
# ===========================================================================

class TestAgentRegistryBroadcast:
    """Test broadcast()."""

    def test_broadcast_empty_registry_returns_zero(self) -> None:
        reg = AgentRegistry()
        assert reg.broadcast({"type": "ping", "content": "hi"}) == 0

    def test_broadcast_returns_count_of_agents_reached(self) -> None:
        reg = AgentRegistry()
        reg.register(role="r", project="p", task="t")
        reg.register(role="r", project="p", task="t")
        count = reg.broadcast({"type": "alert", "content": "deploy"})
        assert count == 2

    def test_broadcast_appends_message_to_each_agent(self) -> None:
        reg = AgentRegistry()
        a1 = reg.register(role="r", project="p", task="t")
        a2 = reg.register(role="r", project="p", task="t")
        reg.broadcast({"type": "info", "content": "update"})
        assert len(reg._agents[a1.id].messages) == 1
        assert len(reg._agents[a2.id].messages) == 1

    def test_broadcast_adds_timestamp_to_message(self) -> None:
        reg = AgentRegistry()
        a = reg.register(role="r", project="p", task="t")
        msg = {"type": "ping", "content": "test"}
        reg.broadcast(msg)
        stored = reg._agents[a.id].messages[0]
        assert "timestamp" in stored

    def test_broadcast_each_agent_gets_copy_not_same_object(self) -> None:
        """Messages are copied so mutation in one agent won't affect others."""
        reg = AgentRegistry()
        a1 = reg.register(role="r", project="p", task="t")
        a2 = reg.register(role="r", project="p", task="t")
        reg.broadcast({"type": "t", "content": "c"})
        m1 = reg._agents[a1.id].messages[0]
        m2 = reg._agents[a2.id].messages[0]
        assert m1 is not m2

    def test_broadcast_excludes_expired_agents(self) -> None:
        reg = AgentRegistry(expiry_seconds=1)
        live = reg.register(role="r", project="p", task="t")
        expired = reg.register(role="r", project="p", task="t")
        expired.last_activity = datetime.now(UTC) - timedelta(seconds=10)

        count = reg.broadcast({"type": "t", "content": "c"})
        # Only live should receive it
        assert count == 1
        assert len(reg._agents[live.id].messages) == 1


# ===========================================================================
# AgentRegistry._cleanup_expired (internal)
# ===========================================================================

class TestAgentRegistryCleanupExpired:
    """Test internal _cleanup_expired()."""

    def test_cleanup_removes_expired_agents(self) -> None:
        reg = AgentRegistry(expiry_seconds=1)
        agent = reg.register(role="r", project="p", task="t")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        # Must call while holding lock per contract — call directly for test
        with reg._lock:
            removed = reg._cleanup_expired()
        assert removed == 1
        assert agent.id not in reg._agents

    def test_cleanup_returns_zero_when_nothing_expired(self) -> None:
        reg = AgentRegistry()
        reg.register(role="r", project="p", task="t")
        with reg._lock:
            removed = reg._cleanup_expired()
        assert removed == 0

    def test_cleanup_leaves_live_agents_intact(self) -> None:
        reg = AgentRegistry(expiry_seconds=300)
        a = reg.register(role="r", project="p", task="t")
        with reg._lock:
            reg._cleanup_expired()
        assert a.id in reg._agents


# ===========================================================================
# AgentRegistry thread-safety
# ===========================================================================

class TestAgentRegistryThreadSafety:
    """Smoke test: concurrent registrations should not lose agents."""

    def test_concurrent_register_all_agents_stored(self) -> None:
        reg = AgentRegistry()
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(10):
                    reg.register(role="r", project="p", task="t")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(reg._agents) == 50


# ===========================================================================
# AgentRegistry expiry_seconds configuration
# ===========================================================================

class TestAgentRegistryExpiryConfig:
    """Test that custom expiry_seconds is respected."""

    def test_custom_expiry_seconds_used_in_get(self) -> None:
        reg = AgentRegistry(expiry_seconds=60)
        agent = reg.register(role="r", project="p", task="t")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=70)
        assert reg.get(agent.id) is None

    def test_custom_expiry_seconds_not_expired_yet(self) -> None:
        reg = AgentRegistry(expiry_seconds=3600)
        agent = reg.register(role="r", project="p", task="t")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=60)
        assert reg.get(agent.id) is not None


# ===========================================================================
# Singleton: get_agent_registry
# ===========================================================================

class TestGetAgentRegistry:
    """Test module-level singleton factory get_agent_registry()."""

    def setup_method(self) -> None:
        """Reset the global singleton before each test."""
        _registry_module._agent_registry = None

    def teardown_method(self) -> None:
        """Clean up after each test."""
        _registry_module._agent_registry = None

    def test_get_agent_registry_returns_agent_registry_instance(self) -> None:
        reg = get_agent_registry()
        assert isinstance(reg, AgentRegistry)

    def test_get_agent_registry_returns_same_instance(self) -> None:
        reg1 = get_agent_registry()
        reg2 = get_agent_registry()
        assert reg1 is reg2

    def test_get_agent_registry_creates_fresh_when_none(self) -> None:
        assert _registry_module._agent_registry is None
        reg = get_agent_registry()
        assert _registry_module._agent_registry is reg

    def test_get_agent_registry_existing_instance_not_replaced(self) -> None:
        existing = AgentRegistry()
        _registry_module._agent_registry = existing
        result = get_agent_registry()
        assert result is existing
