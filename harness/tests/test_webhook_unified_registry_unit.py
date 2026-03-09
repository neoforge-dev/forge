"""
Unit tests for UnifiedAgentRegistry
=====================================

Comprehensive unit tests targeting:
- Helper functions: _now_iso, _parse_ts, _session_path, _agent_to_file_dict,
  _normalise_file_agent
- UnifiedAgentRegistry.__init__
- register: id generation, field defaults, parent-child linking, file write
- unregister: memory removal, file deletion, missing-agent case
- get_agent: memory fast-path, file fallback, normalisation, bad JSON
- list_agents: merge logic, status filter, file-only agents
- update_heartbeat: field merge, immutable-key protection, flush logic
- sync_to_files: writes all in-memory agents, handles OSError
- sync_from_files: new file agents loaded, timestamp-based precedence
- get_stats: totals, by_status, last_sync_at, sessions_dir
- _needs_file_flush: None baseline, elapsed-time logic
- _write_session_file: missing id guard, OSError handling
- get_unified_registry: singleton creation / reuse
- reset_unified_registry: clears singleton

Target: 40+ distinct test cases.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from forge_harness.webhook_server.services.unified_registry import (
    UnifiedAgentRegistry,
    _agent_to_file_dict,
    _normalise_file_agent,
    _now_iso,
    _parse_ts,
    _session_path,
    get_unified_registry,
    reset_unified_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the global singleton is wiped before and after each test."""
    reset_unified_registry()
    yield
    reset_unified_registry()


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Return a temporary directory to use as the sessions store."""
    d = tmp_path / ".forge/sessions"
    d.mkdir()
    return d


@pytest.fixture
def registry(sessions_dir: Path) -> UnifiedAgentRegistry:
    """Return a fresh UnifiedAgentRegistry backed by a temp directory."""
    return UnifiedAgentRegistry(sessions_dir=sessions_dir, sync_interval_seconds=30)


def _write_session_file(sessions_dir: Path, agent_id: str, data: dict) -> Path:
    """Helper: write a session JSON file directly for setup."""
    p = sessions_dir / f"session_{agent_id}.json"
    p.write_text(json.dumps(data))
    return p


# ===========================================================================
# Helper function tests
# ===========================================================================


class TestNowIso:
    def test_returns_string(self):
        result = _now_iso()
        assert isinstance(result, str)

    def test_is_parseable_iso(self):
        result = _now_iso()
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_has_utc_timezone(self):
        result = _now_iso()
        dt = datetime.fromisoformat(result)
        assert dt.utcoffset().total_seconds() == 0


class TestParseTs:
    def test_none_returns_none(self):
        assert _parse_ts(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_ts("") is None

    def test_invalid_string_returns_none(self):
        assert _parse_ts("not-a-date") is None

    def test_valid_utc_iso(self):
        ts = "2025-06-15T12:00:00+00:00"
        result = _parse_ts(ts)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_naive_datetime_gets_utc_timezone(self):
        ts = "2025-06-15T12:00:00"
        result = _parse_ts(ts)
        assert result is not None
        assert result.tzinfo == UTC

    def test_non_string_convertible(self):
        # Should try str(value) first
        ts = datetime.now(UTC).isoformat()
        result = _parse_ts(ts)
        assert result is not None

    def test_integer_non_parseable_returns_none(self):
        assert _parse_ts(12345) is None


class TestSessionPath:
    def test_constructs_correct_path(self, tmp_path: Path):
        result = _session_path(tmp_path, "abc123")
        assert result == tmp_path / "session_abc123.json"

    def test_different_ids_produce_different_paths(self, tmp_path: Path):
        p1 = _session_path(tmp_path, "id-1")
        p2 = _session_path(tmp_path, "id-2")
        assert p1 != p2


class TestAgentToFileDict:
    def test_copies_agent(self):
        agent = {"id": "x1", "role": "backend", "status": "active"}
        result = _agent_to_file_dict(agent)
        assert result is not agent  # must be a copy
        assert result["id"] == "x1"

    def test_adds_session_id_if_missing(self):
        agent = {"id": "x2", "status": "active"}
        result = _agent_to_file_dict(agent)
        assert result["session_id"] == "x2"

    def test_does_not_overwrite_existing_session_id(self):
        agent = {"id": "x3", "session_id": "custom-sid", "status": "active"}
        result = _agent_to_file_dict(agent)
        assert result["session_id"] == "custom-sid"

    def test_adds_last_heartbeat_from_last_activity(self):
        agent = {"id": "x4", "last_activity": "2025-01-01T00:00:00+00:00"}
        result = _agent_to_file_dict(agent)
        assert result["last_heartbeat"] == "2025-01-01T00:00:00+00:00"

    def test_last_heartbeat_not_overwritten(self):
        agent = {"id": "x5", "last_heartbeat": "2025-02-01T00:00:00+00:00"}
        result = _agent_to_file_dict(agent)
        assert result["last_heartbeat"] == "2025-02-01T00:00:00+00:00"


class TestNormaliseFileAgent:
    def test_copies_input(self):
        raw = {"id": "z1"}
        result = _normalise_file_agent(raw)
        assert result is not raw

    def test_session_id_to_id(self):
        raw = {"session_id": "sid-1"}
        result = _normalise_file_agent(raw)
        assert result["id"] == "sid-1"

    def test_id_to_session_id(self):
        raw = {"id": "id-1"}
        result = _normalise_file_agent(raw)
        assert result["session_id"] == "id-1"

    def test_sets_defaults(self):
        result = _normalise_file_agent({"id": "z2"})
        assert result["role"] == "unknown"
        assert result["project"] == "unknown"
        assert result["task"] == ""
        assert result["status"] == "unknown"
        assert result["progress"] == 0
        assert result["files_modified"] == []
        assert result["token_usage"] == {}
        assert result["skills"] == []
        assert result["children"] == []
        assert result["source"] == "file"

    def test_preserves_existing_values(self):
        raw = {"id": "z3", "role": "frontend", "status": "active", "progress": 75}
        result = _normalise_file_agent(raw)
        assert result["role"] == "frontend"
        assert result["status"] == "active"
        assert result["progress"] == 75


# ===========================================================================
# UnifiedAgentRegistry.__init__
# ===========================================================================


class TestRegistryInit:
    def test_default_sessions_dir(self):
        reg = UnifiedAgentRegistry()
        assert reg._sessions_dir == Path(".forge/sessions")

    def test_custom_sessions_dir_string(self, tmp_path: Path):
        reg = UnifiedAgentRegistry(sessions_dir=str(tmp_path))
        assert reg._sessions_dir == tmp_path

    def test_custom_sessions_dir_path(self, tmp_path: Path):
        reg = UnifiedAgentRegistry(sessions_dir=tmp_path)
        assert reg._sessions_dir == tmp_path

    def test_sync_interval_stored(self):
        reg = UnifiedAgentRegistry(sync_interval_seconds=60)
        assert reg._sync_interval == 60

    def test_initially_empty(self, sessions_dir: Path):
        reg = UnifiedAgentRegistry(sessions_dir=sessions_dir)
        assert reg._agents == {}
        assert reg._heartbeat_counter == {}
        assert reg._last_sync_at is None


# ===========================================================================
# register
# ===========================================================================


class TestRegister:
    def test_generates_id_when_not_provided(self, registry: UnifiedAgentRegistry):
        result = registry.register({"role": "backend", "project": "foo", "task": "bar"})
        assert "id" in result
        assert len(result["id"]) > 0

    def test_uses_provided_id(self, registry: UnifiedAgentRegistry):
        result = registry.register({"id": "my-custom-id", "role": "backend"})
        assert result["id"] == "my-custom-id"

    def test_returns_dict_copy(self, registry: UnifiedAgentRegistry):
        result = registry.register({"role": "backend"})
        agent_id = result["id"]
        # Mutating the returned dict should not affect internal state
        result["role"] = "CHANGED"
        assert registry._agents[agent_id]["role"] == "backend"

    def test_session_id_equals_id(self, registry: UnifiedAgentRegistry):
        result = registry.register({"role": "backend"})
        assert result["session_id"] == result["id"]

    def test_defaults_applied(self, registry: UnifiedAgentRegistry):
        result = registry.register({})
        assert result["role"] == "unknown"
        assert result["project"] == "unknown"
        assert result["task"] == ""
        assert result["status"] == "active"
        assert result["progress"] == 0

    def test_custom_fields_propagated(self, registry: UnifiedAgentRegistry):
        data = {
            "role": "frontend",
            "project": "my-project",
            "task": "render dashboard",
            "status": "waiting",
            "progress": 10,
            "skills": ["react"],
        }
        result = registry.register(data)
        assert result["role"] == "frontend"
        assert result["project"] == "my-project"
        assert result["task"] == "render dashboard"
        assert result["status"] == "waiting"
        assert result["skills"] == ["react"]

    def test_source_is_api(self, registry: UnifiedAgentRegistry):
        result = registry.register({"role": "backend"})
        assert result["source"] == "api"

    def test_writes_session_file(self, registry: UnifiedAgentRegistry, sessions_dir: Path):
        result = registry.register({"id": "file-test", "role": "backend"})
        expected = sessions_dir / "session_file-test.json"
        assert expected.exists()
        raw = json.loads(expected.read_text())
        assert raw["id"] == "file-test"

    def test_parent_child_linking(self, registry: UnifiedAgentRegistry):
        parent = registry.register({"id": "parent-1", "role": "orchestrator"})
        child = registry.register({"id": "child-1", "role": "worker", "parent_id": "parent-1"})
        # Parent's children list should be updated
        parent_in_mem = registry._agents["parent-1"]
        assert "child-1" in parent_in_mem["children"]

    def test_parent_not_in_registry_no_crash(self, registry: UnifiedAgentRegistry):
        # Should not raise even if parent doesn't exist yet
        result = registry.register({"id": "child-2", "parent_id": "ghost-parent"})
        assert result["parent_id"] == "ghost-parent"

    def test_heartbeat_counter_initialised_to_zero(self, registry: UnifiedAgentRegistry):
        result = registry.register({"id": "counter-test"})
        assert registry._heartbeat_counter["counter-test"] == 0


# ===========================================================================
# unregister
# ===========================================================================


class TestUnregister:
    def test_returns_true_when_found(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "to-remove"})
        assert registry.unregister("to-remove") is True

    def test_returns_false_when_not_found(self, registry: UnifiedAgentRegistry):
        assert registry.unregister("non-existent") is False

    def test_removes_from_memory(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "mem-remove"})
        registry.unregister("mem-remove")
        assert "mem-remove" not in registry._agents

    def test_removes_session_file(self, registry: UnifiedAgentRegistry, sessions_dir: Path):
        registry.register({"id": "file-remove"})
        assert (sessions_dir / "session_file-remove.json").exists()
        registry.unregister("file-remove")
        assert not (sessions_dir / "session_file-remove.json").exists()

    def test_no_crash_when_file_missing(self, registry: UnifiedAgentRegistry):
        # Register without writing file manually — just inject into memory
        registry._agents["no-file-id"] = {"id": "no-file-id"}
        # Should not raise even though file does not exist
        result = registry.unregister("no-file-id")
        assert result is True

    def test_oserror_on_file_delete_logged_not_raised(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        registry.register({"id": "oserr-id"})
        file_path = sessions_dir / "session_oserr-id.json"
        assert file_path.exists()
        with patch.object(file_path.__class__, "unlink", side_effect=OSError("perm denied")):
            # Should not propagate the OSError
            with patch("forge_harness.webhook_server.services.unified_registry.logger") as mock_log:
                # Patch Path.unlink at the instance level via Path.exists returning True
                with patch("pathlib.Path.unlink", side_effect=OSError("perm denied")):
                    result = registry.unregister("oserr-id")
        # Whether True or False, no exception should have propagated
        assert isinstance(result, bool)


# ===========================================================================
# get_agent
# ===========================================================================


class TestGetAgent:
    def test_returns_none_for_unknown(self, registry: UnifiedAgentRegistry):
        assert registry.get_agent("ghost") is None

    def test_returns_copy_from_memory(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "ga-1", "role": "backend"})
        result = registry.get_agent("ga-1")
        assert result is not None
        assert result["id"] == "ga-1"
        # Mutating result should not affect memory
        result["role"] = "CHANGED"
        assert registry._agents["ga-1"]["role"] == "backend"

    def test_file_fallback_when_not_in_memory(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        _write_session_file(sessions_dir, "file-only", {"id": "file-only", "role": "script"})
        result = registry.get_agent("file-only")
        assert result is not None
        assert result["id"] == "file-only"

    def test_file_fallback_loads_into_memory(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        _write_session_file(sessions_dir, "file-mem", {"id": "file-mem"})
        registry.get_agent("file-mem")
        assert "file-mem" in registry._agents

    def test_corrupt_json_returns_none(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        p = sessions_dir / "session_corrupt.json"
        p.write_text("{bad json{{")
        result = registry.get_agent("corrupt")
        assert result is None

    def test_non_dict_json_returns_none(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        p = sessions_dir / "session_list-agent.json"
        p.write_text(json.dumps([1, 2, 3]))
        result = registry.get_agent("list-agent")
        assert result is None


# ===========================================================================
# list_agents
# ===========================================================================


class TestListAgents:
    def test_empty_when_no_agents_no_files(self, registry: UnifiedAgentRegistry):
        assert registry.list_agents() == []

    def test_returns_memory_agents(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "la-1", "role": "backend", "status": "active"})
        agents = registry.list_agents()
        assert any(a["id"] == "la-1" for a in agents)

    def test_includes_file_only_agents(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        _write_session_file(sessions_dir, "file-la", {"id": "file-la", "status": "idle"})
        agents = registry.list_agents()
        assert any(a["id"] == "file-la" for a in agents)

    def test_memory_wins_over_file(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        # Write file with stale status
        _write_session_file(sessions_dir, "overlap", {"id": "overlap", "status": "idle"})
        # Register in memory with different status
        registry._agents["overlap"] = {"id": "overlap", "status": "active"}
        agents = registry.list_agents()
        match = next(a for a in agents if a["id"] == "overlap")
        assert match["status"] == "active"

    def test_status_filter_active(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "s-active", "status": "active"})
        registry.register({"id": "s-idle", "status": "idle"})
        result = registry.list_agents(status="active")
        ids = [a["id"] for a in result]
        assert "s-active" in ids
        assert "s-idle" not in ids

    def test_status_filter_returns_empty_when_no_match(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "only-active", "status": "active"})
        result = registry.list_agents(status="completed")
        assert result == []


# ===========================================================================
# update_heartbeat
# ===========================================================================


class TestUpdateHeartbeat:
    def test_returns_none_for_unknown_agent(self, registry: UnifiedAgentRegistry):
        result = registry.update_heartbeat("ghost", {"status": "active"})
        assert result is None

    def test_updates_registered_agent(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "hb-1", "status": "active"})
        result = registry.update_heartbeat("hb-1", {"status": "completed", "progress": 100})
        assert result is not None
        assert result["status"] == "completed"
        assert result["progress"] == 100

    def test_last_activity_always_updated(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "hb-2"})
        before = registry._agents["hb-2"]["last_activity"]
        time.sleep(0.01)
        result = registry.update_heartbeat("hb-2", {})
        assert result["last_activity"] >= before

    def test_immutable_keys_not_overwritten(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "hb-3"})
        result = registry.update_heartbeat(
            "hb-3",
            {"id": "HACKED", "session_id": "HACKED", "registered_at": "HACKED"},
        )
        assert result["id"] == "hb-3"
        assert result["session_id"] == "hb-3"
        assert result["registered_at"] != "HACKED"

    def test_returns_dict_copy(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "hb-4"})
        result = registry.update_heartbeat("hb-4", {"status": "idle"})
        result["status"] = "MUTATED"
        assert registry._agents["hb-4"]["status"] == "idle"

    def test_hydrates_from_file_when_not_in_memory(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        _write_session_file(sessions_dir, "hb-file", {"id": "hb-file", "status": "idle"})
        result = registry.update_heartbeat("hb-file", {"status": "active"})
        assert result is not None
        assert result["status"] == "active"

    def test_increments_heartbeat_counter(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "hb-cnt"})
        registry.update_heartbeat("hb-cnt", {})
        registry.update_heartbeat("hb-cnt", {})
        assert registry._heartbeat_counter["hb-cnt"] == 2

    def test_writes_file_when_missing(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        registry.register({"id": "hb-nofile"})
        # Remove the file that register() created
        (sessions_dir / "session_hb-nofile.json").unlink()
        registry.update_heartbeat("hb-nofile", {"status": "idle"})
        # File should be recreated
        assert (sessions_dir / "session_hb-nofile.json").exists()


# ===========================================================================
# sync_to_files
# ===========================================================================


class TestSyncToFiles:
    def test_writes_all_agents(self, registry: UnifiedAgentRegistry, sessions_dir: Path):
        registry.register({"id": "s2f-1"})
        registry.register({"id": "s2f-2"})
        count = registry.sync_to_files()
        assert count == 2

    def test_updates_last_sync_at(self, registry: UnifiedAgentRegistry):
        assert registry._last_sync_at is None
        registry.register({"id": "s2f-ts"})
        registry.sync_to_files()
        assert registry._last_sync_at is not None

    def test_empty_registry_returns_zero(self, registry: UnifiedAgentRegistry):
        assert registry.sync_to_files() == 0

    def test_oserror_during_write_counted_out(
        self, registry: UnifiedAgentRegistry
    ):
        registry.register({"id": "s2f-err"})
        with patch.object(registry, "_write_session_file", side_effect=OSError("disk full")):
            count = registry.sync_to_files()
        # OSError caught internally; count should be 0 (not written)
        assert count == 0


# ===========================================================================
# sync_from_files
# ===========================================================================


class TestSyncFromFiles:
    def test_loads_file_only_agents(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        _write_session_file(sessions_dir, "sff-1", {"id": "sff-1", "status": "active"})
        count = registry.sync_from_files()
        assert count == 1
        assert "sff-1" in registry._agents

    def test_does_not_overwrite_newer_memory_agent(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        old_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        _write_session_file(
            sessions_dir, "sff-old", {"id": "sff-old", "status": "idle", "last_activity": old_ts}
        )
        new_ts = datetime.now(UTC).isoformat()
        registry._agents["sff-old"] = {
            "id": "sff-old",
            "status": "active",
            "last_activity": new_ts,
        }
        registry.sync_from_files()
        # Memory agent (newer) should be preserved
        assert registry._agents["sff-old"]["status"] == "active"

    def test_overwrites_older_memory_agent_with_newer_file(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        new_ts = datetime.now(UTC).isoformat()
        _write_session_file(
            sessions_dir, "sff-new", {"id": "sff-new", "status": "completed", "last_activity": new_ts}
        )
        old_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        registry._agents["sff-new"] = {
            "id": "sff-new",
            "status": "waiting",
            "last_activity": old_ts,
        }
        count = registry.sync_from_files()
        assert count == 1
        assert registry._agents["sff-new"]["status"] == "completed"

    def test_no_files_returns_zero(self, registry: UnifiedAgentRegistry):
        assert registry.sync_from_files() == 0

    def test_sessions_dir_missing_returns_zero(self, tmp_path: Path):
        reg = UnifiedAgentRegistry(sessions_dir=tmp_path / "nonexistent")
        assert reg.sync_from_files() == 0


# ===========================================================================
# get_stats
# ===========================================================================


class TestGetStats:
    def test_empty_stats(self, registry: UnifiedAgentRegistry):
        stats = registry.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["last_sync_at"] is None

    def test_total_reflects_agents(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "gs-1", "status": "active"})
        registry.register({"id": "gs-2", "status": "active"})
        stats = registry.get_stats()
        assert stats["total"] == 2

    def test_by_status_counts(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "gs-a1", "status": "active"})
        registry.register({"id": "gs-a2", "status": "active"})
        registry.register({"id": "gs-i1", "status": "idle"})
        stats = registry.get_stats()
        assert stats["by_status"]["active"] == 2
        assert stats["by_status"]["idle"] == 1

    def test_last_sync_at_after_sync(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "gs-sync"})
        registry.sync_to_files()
        stats = registry.get_stats()
        assert stats["last_sync_at"] is not None

    def test_sessions_dir_in_stats(self, registry: UnifiedAgentRegistry, sessions_dir: Path):
        stats = registry.get_stats()
        assert str(sessions_dir) in stats["sessions_dir"]

    def test_sync_interval_in_stats(self, sessions_dir: Path):
        reg = UnifiedAgentRegistry(sessions_dir=sessions_dir, sync_interval_seconds=99)
        stats = reg.get_stats()
        assert stats["sync_interval_seconds"] == 99


# ===========================================================================
# _needs_file_flush
# ===========================================================================


class TestNeedsFileFlush:
    def test_true_when_last_sync_none(self, registry: UnifiedAgentRegistry):
        assert registry._needs_file_flush() is True

    def test_true_when_interval_exceeded(self, registry: UnifiedAgentRegistry):
        registry._last_sync_at = datetime.now(UTC) - timedelta(seconds=31)
        registry._sync_interval = 30
        assert registry._needs_file_flush() is True

    def test_false_when_within_interval(self, registry: UnifiedAgentRegistry):
        registry._last_sync_at = datetime.now(UTC)
        registry._sync_interval = 30
        assert registry._needs_file_flush() is False


# ===========================================================================
# _write_session_file
# ===========================================================================


class TestWriteSessionFile:
    def test_writes_valid_json(self, registry: UnifiedAgentRegistry, sessions_dir: Path):
        agent = {"id": "wsf-1", "role": "backend", "status": "active"}
        registry._write_session_file(agent)
        p = sessions_dir / "session_wsf-1.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["id"] == "wsf-1"

    def test_no_id_no_crash(self, registry: UnifiedAgentRegistry):
        # Should log warning and return without raising
        registry._write_session_file({})

    def test_oserror_logged_not_raised(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        agent = {"id": "wsf-err"}
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            # Should not propagate
            registry._write_session_file(agent)


# ===========================================================================
# Singleton: get_unified_registry / reset_unified_registry
# ===========================================================================


class TestSingleton:
    def test_get_returns_registry_instance(self, tmp_path: Path):
        reg = get_unified_registry(sessions_dir=tmp_path)
        assert isinstance(reg, UnifiedAgentRegistry)

    def test_same_instance_on_second_call(self, tmp_path: Path):
        reg1 = get_unified_registry(sessions_dir=tmp_path)
        reg2 = get_unified_registry(sessions_dir=tmp_path / "other")
        assert reg1 is reg2

    def test_reset_clears_singleton(self, tmp_path: Path):
        reg1 = get_unified_registry(sessions_dir=tmp_path)
        reset_unified_registry()
        reg2 = get_unified_registry(sessions_dir=tmp_path)
        assert reg1 is not reg2

    def test_thread_safe_creation(self, tmp_path: Path):
        """Verify that concurrent calls all get the same instance."""
        instances = []

        def _get():
            instances.append(get_unified_registry(sessions_dir=tmp_path))

        threads = [threading.Thread(target=_get) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


# ===========================================================================
# Thread safety: concurrent register / update_heartbeat
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_register(self, registry: UnifiedAgentRegistry):
        errors = []

        def _register(i):
            try:
                registry.register({"id": f"thread-{i}", "role": "worker"})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_register, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(registry._agents) == 20

    def test_concurrent_heartbeat(self, registry: UnifiedAgentRegistry):
        registry.register({"id": "shared-agent"})
        errors = []

        def _hb():
            try:
                registry.update_heartbeat("shared-agent", {"progress": 50})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_hb) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert registry._heartbeat_counter["shared-agent"] == 20


# ===========================================================================
# Integration-style: full lifecycle
# ===========================================================================


class TestFullLifecycle:
    def test_register_heartbeat_list_unregister(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        agent = registry.register({"id": "lifecycle-1", "role": "backend", "project": "p"})
        agent_id = agent["id"]

        # Heartbeat update
        updated = registry.update_heartbeat(agent_id, {"status": "active", "progress": 50})
        assert updated["progress"] == 50

        # List should include it
        agents = registry.list_agents()
        assert any(a["id"] == agent_id for a in agents)

        # Unregister
        removed = registry.unregister(agent_id)
        assert removed is True

        # No longer listed
        agents_after = registry.list_agents()
        assert not any(a["id"] == agent_id for a in agents_after)

        # Session file deleted
        assert not (sessions_dir / f"session_{agent_id}.json").exists()

    def test_sync_round_trip(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        registry.register({"id": "rt-1", "role": "backend"})
        registry.register({"id": "rt-2", "role": "frontend"})
        written = registry.sync_to_files()
        assert written == 2

        # New registry instance pointing at same dir should pick up files
        reg2 = UnifiedAgentRegistry(sessions_dir=sessions_dir)
        loaded = reg2.sync_from_files()
        assert loaded == 2
        assert "rt-1" in reg2._agents
        assert "rt-2" in reg2._agents


# ===========================================================================
# _load_file_agents edge-case coverage (lines 482, 487-490)
# ===========================================================================


class TestLoadFileAgentsEdgeCases:
    """Exercise the remaining uncovered branches in _load_file_agents."""

    def test_non_dict_json_skipped_in_list_agents(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        # Write a valid JSON array — _load_file_agents should skip it (line 482)
        p = sessions_dir / "session_non-dict.json"
        p.write_text(json.dumps([{"id": "should-be-skipped"}]))
        agents = registry.list_agents()
        assert not any(a.get("id") == "should-be-skipped" for a in agents)

    def test_corrupt_json_in_directory_skipped_gracefully(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        # Write corrupt JSON — hits the json.JSONDecodeError branch (line 487)
        (sessions_dir / "session_bad-json.json").write_text("{corrupt: true,")
        # Register a valid agent to confirm valid ones still show up
        registry.register({"id": "good-agent"})
        agents = registry.list_agents()
        ids = [a["id"] for a in agents]
        assert "good-agent" in ids
        # The corrupt file agent must NOT appear
        assert "bad-json" not in ids

    def test_oserror_on_read_skipped_gracefully(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        # Create a real file so glob finds it, then make read_text raise OSError
        p = sessions_dir / "session_oserr-read.json"
        p.write_text(json.dumps({"id": "oserr-read"}))

        original_read_text = Path.read_text

        def _patched_read_text(self, *args, **kwargs):
            if "oserr-read" in str(self):
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", _patched_read_text):
            agents = registry.list_agents()

        # The OSError file agent must not appear; no exception should propagate
        assert not any(a.get("id") == "oserr-read" for a in agents)

    def test_sync_from_files_skips_corrupt_json(
        self, registry: UnifiedAgentRegistry, sessions_dir: Path
    ):
        (sessions_dir / "session_sff-corrupt.json").write_text("{{broken")
        count = registry.sync_from_files()
        assert count == 0
        assert "sff-corrupt" not in registry._agents
