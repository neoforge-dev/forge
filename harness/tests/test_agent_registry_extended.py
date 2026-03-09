"""
Extended tests for AgentRegistry, AgentSession and EventBus services.

Covers:
- AgentSession initialization, to_dict, TTL/expiry/staleness
- AgentRegistry: registration, deregistration, lifecycle, status tracking,
  progress, complete, pause, resume, kill, send_message, broadcast,
  cleanup, singleton, thread safety
- EventBus: publish/subscribe, SSE formatting, multiple subscribers,
  unsubscribe, close_all, counter, singleton, full-queue drop
"""

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.services.agent_registry import (
    AgentRegistry,
    AgentSession,
    get_agent_registry,
)
from forge_harness.webhook_server.services.event_bus import (
    EventBus,
    SSEEvent,
    get_event_bus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(**kwargs) -> AgentSession:
    """Return a minimal AgentSession with sensible defaults."""
    defaults = dict(id="ag-001", role="backend-engineer", project="test-proj", task="do stuff")
    defaults.update(kwargs)
    return AgentSession(**defaults)


def _fresh_registry(expiry_seconds: int = 300) -> AgentRegistry:
    """Return a brand-new AgentRegistry instance (not the singleton)."""
    return AgentRegistry(expiry_seconds=expiry_seconds)


# ---------------------------------------------------------------------------
# AgentSession – initialisation
# ---------------------------------------------------------------------------


class TestAgentSessionInit:
    def test_required_fields_stored(self):
        s = _make_session()
        assert s.id == "ag-001"
        assert s.role == "backend-engineer"
        assert s.project == "test-proj"
        assert s.task == "do stuff"

    def test_optional_fields_default_none(self):
        s = _make_session()
        assert s.name is None
        assert s.domain is None
        assert s.parent_id is None
        assert s.tmux_session is None

    def test_list_fields_default_empty(self):
        s = _make_session()
        assert s.children == []
        assert s.skills == []
        assert s.files_modified == []
        assert s.messages == []

    def test_dict_field_default_empty(self):
        s = _make_session()
        assert s.token_usage == {}

    def test_default_status_is_active(self):
        s = _make_session()
        assert s.status == "active"

    def test_default_progress_zero(self):
        s = _make_session()
        assert s.progress == 0

    def test_timestamps_are_utc_aware(self):
        s = _make_session()
        assert s.registered_at.tzinfo is not None
        assert s.last_activity.tzinfo is not None

    def test_optional_fields_accepted(self):
        s = _make_session(
            name="alpha",
            domain="codeswiftr-com",
            parent_id="parent-01",
            tmux_session="forge:main",
            skills=["python", "testing"],
        )
        assert s.name == "alpha"
        assert s.domain == "codeswiftr-com"
        assert s.parent_id == "parent-01"
        assert s.tmux_session == "forge:main"
        assert s.skills == ["python", "testing"]

    def test_mutable_list_fields_are_independent(self):
        """Each instance gets its own list, not a shared default."""
        a = _make_session(id="a1")
        b = _make_session(id="b1")
        a.children.append("child-x")
        assert b.children == []


# ---------------------------------------------------------------------------
# AgentSession – to_dict
# ---------------------------------------------------------------------------


class TestAgentSessionToDict:
    def test_to_dict_contains_all_keys(self):
        s = _make_session()
        d = s.to_dict()
        for key in (
            "id", "role", "name", "domain", "project", "task",
            "parent_id", "children", "tmux_session", "skills",
            "status", "progress", "current_task", "files_modified",
            "token_usage", "messages_count", "registered_at",
            "last_activity", "is_stale",
        ):
            assert key in d, f"Missing key: {key}"

    def test_messages_count_reflects_list_length(self):
        s = _make_session()
        s.messages.append({"type": "info", "content": "hello"})
        s.messages.append({"type": "info", "content": "world"})
        assert s.to_dict()["messages_count"] == 2

    def test_registered_at_is_iso_string(self):
        s = _make_session()
        ts = s.to_dict()["registered_at"]
        # Should be parseable
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None

    def test_is_stale_reflects_current_state(self):
        s = _make_session()
        # Fresh session should not be stale
        assert s.to_dict()["is_stale"] is False


# ---------------------------------------------------------------------------
# AgentSession – TTL / expiry / staleness
# ---------------------------------------------------------------------------


class TestAgentSessionTTL:
    def test_fresh_session_not_expired(self):
        s = _make_session()
        assert s.is_expired(timeout_seconds=300) is False

    def test_old_session_is_expired(self):
        s = _make_session()
        s.last_activity = datetime.now(UTC) - timedelta(seconds=400)
        assert s.is_expired(timeout_seconds=300) is True

    def test_custom_timeout_respected(self):
        s = _make_session()
        s.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        assert s.is_expired(timeout_seconds=5) is True
        assert s.is_expired(timeout_seconds=30) is False

    def test_fresh_session_not_stale(self):
        s = _make_session()
        assert s.is_stale(heartbeat_timeout_seconds=120) is False

    def test_old_session_is_stale(self):
        s = _make_session()
        s.last_activity = datetime.now(UTC) - timedelta(seconds=200)
        assert s.is_stale(heartbeat_timeout_seconds=120) is True

    def test_stale_threshold_lower_than_expired_threshold(self):
        s = _make_session()
        s.last_activity = datetime.now(UTC) - timedelta(seconds=150)
        # stale (>120s) but not expired (<300s)
        assert s.is_stale() is True
        assert s.is_expired() is False


# ---------------------------------------------------------------------------
# AgentRegistry – registration
# ---------------------------------------------------------------------------


class TestAgentRegistryRegister:
    def test_register_returns_agent_session(self):
        reg = _fresh_registry()
        agent = reg.register(role="dev", project="proj-a", task="task-1")
        assert isinstance(agent, AgentSession)

    def test_registered_agent_has_unique_id(self):
        reg = _fresh_registry()
        a = reg.register(role="dev", project="p", task="t")
        b = reg.register(role="dev", project="p", task="t")
        assert a.id != b.id

    def test_registered_agent_id_is_8_chars(self):
        reg = _fresh_registry()
        agent = reg.register(role="dev", project="p", task="t")
        assert len(agent.id) == 8

    def test_register_stores_agent(self):
        reg = _fresh_registry()
        agent = reg.register(role="dev", project="proj", task="task")
        assert reg.get(agent.id) is not None

    def test_register_with_optional_params(self):
        reg = _fresh_registry()
        agent = reg.register(
            role="qa",
            project="proj",
            task="test suite",
            name="QA Bot",
            domain="codeswiftr-com",
            tmux_session="forge:qa",
            skills=["pytest"],
        )
        assert agent.name == "QA Bot"
        assert agent.domain == "codeswiftr-com"
        assert agent.tmux_session == "forge:qa"
        assert agent.skills == ["pytest"]

    def test_register_child_updates_parent_children_list(self):
        reg = _fresh_registry()
        parent = reg.register(role="orchestrator", project="p", task="parent task")
        child = reg.register(role="worker", project="p", task="child task", parent_id=parent.id)
        updated_parent = reg.get(parent.id)
        assert child.id in updated_parent.children

    def test_register_child_with_nonexistent_parent_does_not_raise(self):
        reg = _fresh_registry()
        # Should complete without exception
        child = reg.register(role="worker", project="p", task="t", parent_id="no-such-parent")
        assert child is not None

    def test_multiple_registrations_all_tracked(self):
        reg = _fresh_registry()
        ids = {reg.register(role="r", project="p", task="t").id for _ in range(5)}
        assert len(reg.list_active()) == 5
        assert len(ids) == 5


# ---------------------------------------------------------------------------
# AgentRegistry – get (lookup by id, tmux_session, name)
# ---------------------------------------------------------------------------


class TestAgentRegistryGet:
    def test_get_by_id(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        found = reg.get(agent.id)
        assert found is not None
        assert found.id == agent.id

    def test_get_nonexistent_id_returns_none(self):
        reg = _fresh_registry()
        assert reg.get("no-such-id") is None

    def test_get_by_tmux_session(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t", tmux_session="forge:main")
        found = reg.get("forge:main")
        assert found is not None
        assert found.id == agent.id

    def test_get_by_name_case_insensitive(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t", name="AlphaBot")
        found = reg.get("alphabot")
        assert found is not None
        assert found.id == agent.id

    def test_get_expired_agent_returns_none(self):
        reg = _fresh_registry(expiry_seconds=1)
        agent = reg.register(role="r", project="p", task="t")
        # Force expiry
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = reg.get(agent.id)
        assert result is None

    def test_get_by_tmux_expired_returns_none(self):
        reg = _fresh_registry(expiry_seconds=1)
        agent = reg.register(role="r", project="p", task="t", tmux_session="forge:old")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = reg.get("forge:old")
        assert result is None

    def test_get_by_name_expired_returns_none(self):
        reg = _fresh_registry(expiry_seconds=1)
        agent = reg.register(role="r", project="p", task="t", name="OldBot")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=10)
        result = reg.get("OldBot")
        assert result is None


# ---------------------------------------------------------------------------
# AgentRegistry – list_active / cleanup
# ---------------------------------------------------------------------------


class TestAgentRegistryListAndCleanup:
    def test_list_active_returns_all_fresh(self):
        reg = _fresh_registry()
        for i in range(3):
            reg.register(role="r", project="p", task=f"t{i}")
        assert len(reg.list_active()) == 3

    def test_list_active_excludes_expired(self):
        reg = _fresh_registry(expiry_seconds=5)
        fresh = reg.register(role="r", project="p", task="fresh")
        stale = reg.register(role="r", project="p", task="stale")
        stale.last_activity = datetime.now(UTC) - timedelta(seconds=100)
        active = reg.list_active()
        ids = [a.id for a in active]
        assert fresh.id in ids
        assert stale.id not in ids

    def test_cleanup_removes_expired(self):
        reg = _fresh_registry(expiry_seconds=5)
        agent = reg.register(role="r", project="p", task="t")
        agent.last_activity = datetime.now(UTC) - timedelta(seconds=100)
        reg.list_active()  # triggers cleanup
        assert reg.get(agent.id) is None


# ---------------------------------------------------------------------------
# AgentRegistry – update_progress
# ---------------------------------------------------------------------------


class TestAgentRegistryUpdateProgress:
    def test_update_progress_sets_progress(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        result = reg.update_progress(agent.id, progress=50)
        assert result is not None
        assert result.progress == 50

    def test_update_progress_sets_current_task(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.update_progress(agent.id, progress=10, current_task="writing tests")
        updated = reg.get(agent.id)
        assert updated.current_task == "writing tests"

    def test_update_progress_appends_files(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.update_progress(agent.id, progress=20, files_modified=["a.py", "b.py"])
        reg.update_progress(agent.id, progress=30, files_modified=["c.py", "a.py"])
        updated = reg.get(agent.id)
        # No duplicates
        assert updated.files_modified.count("a.py") == 1
        assert "b.py" in updated.files_modified
        assert "c.py" in updated.files_modified

    def test_update_progress_merges_token_usage(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.update_progress(agent.id, progress=10, token_usage={"input": 100, "output": 50})
        reg.update_progress(agent.id, progress=20, token_usage={"input": 200, "output": 75})
        updated = reg.get(agent.id)
        assert updated.token_usage["input"] == 300
        assert updated.token_usage["output"] == 125

    def test_update_progress_nonexistent_returns_none(self):
        reg = _fresh_registry()
        assert reg.update_progress("no-such-id", progress=50) is None

    def test_update_progress_refreshes_last_activity(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        old_time = datetime.now(UTC) - timedelta(minutes=3)
        agent.last_activity = old_time
        reg.update_progress(agent.id, progress=10)
        updated = reg.get(agent.id)
        assert updated.last_activity > old_time


# ---------------------------------------------------------------------------
# AgentRegistry – complete
# ---------------------------------------------------------------------------


class TestAgentRegistryComplete:
    def test_complete_sets_status(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        result = reg.complete(agent.id)
        assert result.status == "completed"

    def test_complete_sets_progress_100(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        result = reg.complete(agent.id)
        assert result.progress == 100

    def test_complete_with_summary_appends_message(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.complete(agent.id, summary="All done")
        updated = reg.get(agent.id)
        types = [m["type"] for m in updated.messages]
        assert "completion" in types
        contents = [m["content"] for m in updated.messages]
        assert "All done" in contents

    def test_complete_without_summary_no_message(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.complete(agent.id)
        updated = reg.get(agent.id)
        assert len(updated.messages) == 0

    def test_complete_nonexistent_returns_none(self):
        reg = _fresh_registry()
        assert reg.complete("ghost") is None


# ---------------------------------------------------------------------------
# AgentRegistry – pause / resume
# ---------------------------------------------------------------------------


class TestAgentRegistryPauseResume:
    def test_pause_changes_status_to_paused(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        result, prev = reg.pause(agent.id)
        assert result.status == "paused"
        assert prev == "active"

    def test_resume_changes_status_to_active(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.pause(agent.id)
        result, prev = reg.resume(agent.id)
        assert result.status == "active"
        assert prev == "paused"

    def test_pause_nonexistent_returns_none_tuple(self):
        reg = _fresh_registry()
        agent, prev = reg.pause("no-such")
        assert agent is None
        assert prev is None

    def test_resume_nonexistent_returns_none_tuple(self):
        reg = _fresh_registry()
        agent, prev = reg.resume("no-such")
        assert agent is None
        assert prev is None

    def test_pause_returns_previous_status(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        # Manually set to waiting
        agent.status = "waiting"
        result, prev = reg.pause(agent.id)
        assert prev == "waiting"


# ---------------------------------------------------------------------------
# AgentRegistry – kill
# ---------------------------------------------------------------------------


class TestAgentRegistryKill:
    def test_kill_sets_status_to_failed(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        result, prev = reg.kill(agent.id)
        assert result.status == "failed"

    def test_kill_returns_previous_status(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        result, prev = reg.kill(agent.id)
        assert prev == "active"

    def test_kill_with_reason_appends_message(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.kill(agent.id, reason="out of tokens")
        updated = reg.get(agent.id)
        types = [m["type"] for m in updated.messages]
        assert "kill" in types
        contents = [m["content"] for m in updated.messages]
        assert "out of tokens" in contents

    def test_kill_agent_remains_in_registry(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.kill(agent.id)
        # Should still be retrievable (not deleted)
        assert reg.get(agent.id) is not None

    def test_kill_nonexistent_returns_none_tuple(self):
        reg = _fresh_registry()
        agent, prev = reg.kill("ghost")
        assert agent is None
        assert prev is None


# ---------------------------------------------------------------------------
# AgentRegistry – send_message
# ---------------------------------------------------------------------------


class TestAgentRegistrySendMessage:
    def test_send_message_appends_to_agent(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        msg = {"type": "instruction", "content": "do something"}
        reg.send_message(agent.id, msg, use_queue=False)
        updated = reg.get(agent.id)
        assert len(updated.messages) == 1

    def test_send_message_adds_timestamp(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        msg = {"type": "notification", "content": "ping"}
        reg.send_message(agent.id, msg, use_queue=False)
        updated = reg.get(agent.id)
        assert "timestamp" in updated.messages[0]

    def test_send_message_to_nonexistent_returns_none_tuple(self):
        reg = _fresh_registry()
        result = reg.send_message("ghost", {"type": "x", "content": "y"}, use_queue=False)
        assert result == (None, None)

    def test_send_message_with_queue_returns_message_id(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        msg = {"type": "instruction", "content": "task dispatch"}
        _agent, message_id = reg.send_message(agent.id, msg, use_queue=True)
        assert message_id is not None

    def test_send_multiple_messages_accumulate(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        for i in range(5):
            reg.send_message(agent.id, {"type": "msg", "content": f"m{i}"}, use_queue=False)
        updated = reg.get(agent.id)
        assert len(updated.messages) == 5


# ---------------------------------------------------------------------------
# AgentRegistry – broadcast
# ---------------------------------------------------------------------------


class TestAgentRegistryBroadcast:
    def test_broadcast_reaches_all_agents(self):
        reg = _fresh_registry()
        agents = [reg.register(role="r", project="p", task=f"t{i}") for i in range(4)]
        count = reg.broadcast({"type": "alert", "content": "heads up"})
        assert count == 4
        for ag in agents:
            updated = reg.get(ag.id)
            assert any(m.get("type") == "alert" for m in updated.messages)

    def test_broadcast_returns_zero_when_no_agents(self):
        reg = _fresh_registry()
        count = reg.broadcast({"type": "alert", "content": "empty"})
        assert count == 0

    def test_broadcast_adds_timestamp(self):
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        reg.broadcast({"type": "info", "content": "hi"})
        updated = reg.get(agent.id)
        assert "timestamp" in updated.messages[0]

    def test_broadcast_message_copied_per_agent(self):
        """Each agent should get an independent copy of the message."""
        reg = _fresh_registry()
        a1 = reg.register(role="r", project="p", task="t1")
        a2 = reg.register(role="r", project="p", task="t2")
        reg.broadcast({"type": "sync", "content": "go"})
        updated_a1 = reg.get(a1.id)
        updated_a2 = reg.get(a2.id)
        # Mutating one copy should not affect the other
        updated_a1.messages[0]["extra"] = "modified"
        assert "extra" not in updated_a2.messages[0]


# ---------------------------------------------------------------------------
# AgentRegistry – singleton (get_agent_registry)
# ---------------------------------------------------------------------------


class TestAgentRegistrySingleton:
    def test_get_agent_registry_returns_instance(self):
        reg = get_agent_registry()
        assert isinstance(reg, AgentRegistry)

    def test_get_agent_registry_returns_same_instance(self):
        r1 = get_agent_registry()
        r2 = get_agent_registry()
        assert r1 is r2

    def test_get_agent_registry_state_persists(self):
        reg = get_agent_registry()
        agent = reg.register(role="r", project="singleton-proj", task="t")
        reg2 = get_agent_registry()
        assert reg2.get(agent.id) is not None


# ---------------------------------------------------------------------------
# AgentRegistry – thread safety
# ---------------------------------------------------------------------------


class TestAgentRegistryThreadSafety:
    def test_concurrent_registrations(self):
        """100 threads each registering one agent — all should succeed."""
        reg = _fresh_registry()
        errors = []

        def _register():
            try:
                reg.register(role="worker", project="p", task="t")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_register) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(reg.list_active()) == 100

    def test_concurrent_reads_and_writes(self):
        """Mixed reads and writes should not raise."""
        reg = _fresh_registry()
        agent = reg.register(role="r", project="p", task="t")
        errors = []

        def _read():
            try:
                for _ in range(20):
                    reg.get(agent.id)
            except Exception as e:
                errors.append(e)

        def _write():
            try:
                for i in range(20):
                    reg.update_progress(agent.id, progress=i)
            except Exception as e:
                errors.append(e)

        threads = [
            *[threading.Thread(target=_read) for _ in range(5)],
            *[threading.Thread(target=_write) for _ in range(5)],
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ---------------------------------------------------------------------------
# SSEEvent – initialization and to_sse_format
# ---------------------------------------------------------------------------


class TestSSEEventInit:
    def test_required_fields_stored(self):
        evt = SSEEvent(id="e1", event="agent.progress", data={"progress": 50})
        assert evt.id == "e1"
        assert evt.event == "agent.progress"
        assert evt.data == {"progress": 50}

    def test_default_source_is_webhook_server(self):
        evt = SSEEvent(id="e2", event="test.event", data={})
        assert evt.source == "webhook-server"

    def test_custom_source_accepted(self):
        evt = SSEEvent(id="e3", event="test.event", data={}, source="custom")
        assert evt.source == "custom"

    def test_timestamp_defaults_to_utc_string(self):
        evt = SSEEvent(id="e4", event="t", data={})
        assert evt.timestamp is not None
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(evt.timestamp)
        assert parsed is not None

    def test_custom_timestamp_accepted(self):
        ts = "2026-01-01T00:00:00+00:00"
        evt = SSEEvent(id="e5", event="t", data={}, timestamp=ts)
        assert evt.timestamp == ts


class TestSSEEventToSseFormat:
    def _make_event(self, **kwargs):
        defaults = dict(
            id="evt-001",
            event="agent.registered",
            data={"agent_id": "ag-42"},
            timestamp="2026-02-22T12:00:00+00:00",
            source="webhook-server",
        )
        defaults.update(kwargs)
        return SSEEvent(**defaults)

    def test_format_contains_id_line(self):
        result = self._make_event().to_sse_format()
        assert "id: evt-001" in result

    def test_format_contains_event_line(self):
        result = self._make_event().to_sse_format()
        assert "event: agent.registered" in result

    def test_format_contains_data_line(self):
        result = self._make_event().to_sse_format()
        assert result.count("data: ") == 1

    def test_format_ends_with_double_newline(self):
        result = self._make_event().to_sse_format()
        assert result.endswith("\n\n")

    def test_data_field_is_valid_json(self):
        result = self._make_event().to_sse_format()
        data_line = [l for l in result.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert isinstance(payload, dict)

    def test_payload_contains_envelope_fields(self):
        result = self._make_event().to_sse_format()
        data_line = [l for l in result.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        for key in ("id", "type", "timestamp", "source", "data"):
            assert key in payload, f"Missing key in payload: {key}"

    def test_payload_type_maps_to_event(self):
        result = self._make_event(event="agent.completed").to_sse_format()
        data_line = [l for l in result.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["type"] == "agent.completed"

    def test_payload_data_contains_original_data(self):
        result = self._make_event(data={"foo": "bar"}).to_sse_format()
        data_line = [l for l in result.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"] == {"foo": "bar"}

    def test_payload_source_preserved(self):
        result = self._make_event(source="test-source").to_sse_format()
        data_line = [l for l in result.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["source"] == "test-source"

    def test_nested_data_serialised(self):
        nested = {"agents": [{"id": "a1", "progress": 80}], "count": 1}
        result = self._make_event(data=nested).to_sse_format()
        data_line = [l for l in result.splitlines() if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        assert payload["data"]["count"] == 1
        assert payload["data"]["agents"][0]["id"] == "a1"


# ---------------------------------------------------------------------------
# EventBus – subscribe / unsubscribe / publish
# ---------------------------------------------------------------------------


class TestEventBusSubscribe:
    def setup_method(self):
        # Each test needs a fresh EventBus without the singleton interfering
        self.bus = EventBus.__new__(EventBus)
        self.bus._initialized = False
        self.bus.__init__()

    def test_subscribe_returns_asyncio_queue(self):
        q = self.bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_subscribe_adds_to_subscriber_list(self):
        q1 = self.bus.subscribe()
        q2 = self.bus.subscribe()
        assert len(self.bus._subscribers) == 2

    def test_unsubscribe_removes_queue(self):
        q = self.bus.subscribe()
        self.bus.unsubscribe(q)
        assert q not in self.bus._subscribers

    def test_unsubscribe_unknown_queue_does_not_raise(self):
        q = asyncio.Queue()
        # Should not raise even though never subscribed
        self.bus.unsubscribe(q)


class TestEventBusPublish:
    def setup_method(self):
        self.loop = asyncio.new_event_loop()
        self.bus = EventBus.__new__(EventBus)
        self.bus._initialized = False
        self.bus.__init__()

    def teardown_method(self):
        self.loop.close()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_publish_puts_event_in_subscriber_queue(self):
        q = self.bus.subscribe()

        async def _test():
            await self.bus.publish("agent.registered", {"id": "ag-1"})
            evt = await asyncio.wait_for(q.get(), timeout=1.0)
            return evt

        evt = self._run(_test())
        assert isinstance(evt, SSEEvent)
        assert evt.event == "agent.registered"

    def test_publish_event_data_matches(self):
        q = self.bus.subscribe()

        async def _test():
            await self.bus.publish("agent.progress", {"progress": 42})
            return await asyncio.wait_for(q.get(), timeout=1.0)

        evt = self._run(_test())
        assert evt.data["progress"] == 42

    def test_publish_increments_event_counter(self):
        async def _test():
            before = self.bus._event_counter
            await self.bus.publish("x", {})
            await self.bus.publish("y", {})
            return before, self.bus._event_counter

        before, after = self._run(_test())
        assert after == before + 2

    def test_publish_event_id_matches_counter(self):
        q = self.bus.subscribe()

        async def _test():
            await self.bus.publish("test.event", {})
            return await asyncio.wait_for(q.get(), timeout=1.0)

        evt = self._run(_test())
        assert evt.id == str(self.bus._event_counter)

    def test_publish_custom_source(self):
        q = self.bus.subscribe()

        async def _test():
            await self.bus.publish("test.event", {}, source="custom-src")
            return await asyncio.wait_for(q.get(), timeout=1.0)

        evt = self._run(_test())
        assert evt.source == "custom-src"

    def test_publish_to_multiple_subscribers(self):
        q1 = self.bus.subscribe()
        q2 = self.bus.subscribe()
        q3 = self.bus.subscribe()

        async def _test():
            await self.bus.publish("broadcast.event", {"msg": "hello"})
            e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
            e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
            e3 = await asyncio.wait_for(q3.get(), timeout=1.0)
            return e1, e2, e3

        e1, e2, e3 = self._run(_test())
        for e in (e1, e2, e3):
            assert e.event == "broadcast.event"
            assert e.data["msg"] == "hello"

    def test_publish_after_unsubscribe_not_received(self):
        q = self.bus.subscribe()
        self.bus.unsubscribe(q)

        async def _test():
            await self.bus.publish("test.event", {})
            return q.empty()

        assert self._run(_test()) is True

    def test_publish_no_subscribers_does_not_raise(self):
        async def _test():
            await self.bus.publish("test.event", {})

        # Should complete without exception
        self._run(_test())

    def test_publish_full_queue_drops_event(self):
        """When a queue is full, the event should be dropped (no exception)."""
        q = asyncio.Queue(maxsize=1)
        with self.bus._subscriber_lock:
            self.bus._subscribers.append(q)

        async def _test():
            # Fill the queue
            await self.bus.publish("event.one", {"n": 1})
            # Second publish should drop silently — queue is full
            await self.bus.publish("event.two", {"n": 2})
            return q.qsize()

        size = self._run(_test())
        assert size == 1  # Only first event remained


class TestEventBusCloseAll:
    def setup_method(self):
        self.loop = asyncio.new_event_loop()
        self.bus = EventBus.__new__(EventBus)
        self.bus._initialized = False
        self.bus.__init__()

    def teardown_method(self):
        self.loop.close()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_close_all_sends_none_to_each_subscriber(self):
        q1 = self.bus.subscribe()
        q2 = self.bus.subscribe()

        async def _test():
            await self.bus.close_all()
            s1 = await asyncio.wait_for(q1.get(), timeout=1.0)
            s2 = await asyncio.wait_for(q2.get(), timeout=1.0)
            return s1, s2

        s1, s2 = self._run(_test())
        assert s1 is None
        assert s2 is None

    def test_close_all_with_no_subscribers_does_not_raise(self):
        async def _test():
            await self.bus.close_all()

        self._run(_test())


# ---------------------------------------------------------------------------
# EventBus – singleton (get_event_bus)
# ---------------------------------------------------------------------------


class TestEventBusSingleton:
    def test_get_event_bus_returns_event_bus_instance(self):
        bus = get_event_bus()
        assert isinstance(bus, EventBus)

    def test_get_event_bus_returns_same_instance_twice(self):
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2

    def test_event_bus_singleton_pattern(self):
        """Direct construction also returns singleton."""
        b1 = EventBus()
        b2 = EventBus()
        assert b1 is b2

    def test_event_bus_initialized_flag(self):
        bus = EventBus()
        assert bus._initialized is True


# ---------------------------------------------------------------------------
# EventBus – event ordering and multiple events
# ---------------------------------------------------------------------------


class TestEventBusOrdering:
    def setup_method(self):
        self.loop = asyncio.new_event_loop()
        self.bus = EventBus.__new__(EventBus)
        self.bus._initialized = False
        self.bus.__init__()

    def teardown_method(self):
        self.loop.close()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_events_received_in_publish_order(self):
        q = self.bus.subscribe()

        async def _test():
            for i in range(5):
                await self.bus.publish(f"event.{i}", {"seq": i})
            events = []
            for _ in range(5):
                events.append(await asyncio.wait_for(q.get(), timeout=1.0))
            return events

        events = self._run(_test())
        seqs = [e.data["seq"] for e in events]
        assert seqs == [0, 1, 2, 3, 4]

    def test_event_ids_are_sequential_strings(self):
        q = self.bus.subscribe()

        async def _test():
            for _ in range(3):
                await self.bus.publish("t", {})
            return [await asyncio.wait_for(q.get(), timeout=1.0) for _ in range(3)]

        events = self._run(_test())
        ids = [int(e.id) for e in events]
        # IDs should be consecutive integers (monotonically increasing)
        assert ids[1] == ids[0] + 1
        assert ids[2] == ids[1] + 1
