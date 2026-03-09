"""Tests for UnifiedAgentRegistry (CC Phase 1.3).

Coverage targets:
- register: writes to memory AND file
- unregister: removes from memory AND file
- list_agents: merged view (memory + file)
- get_agent: memory lookup with file fallback
- update_heartbeat: in-memory update + periodic flush
- sync_to_files: all in-memory agents flushed to disk
- sync_from_files: recovery from disk after restart
- get_stats: totals and by-status breakdown
- Concurrent access safety (threading)
- Singleton pattern (get / reset)
- Edge cases: missing dir, corrupt JSON, missing id
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge_harness.webhook_server.services.unified_registry import (
    UnifiedAgentRegistry,
    _normalise_file_agent,
    _parse_ts,
    _session_path,
    get_unified_registry,
    reset_unified_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_sessions(tmp_path: Path) -> Path:
    """Return a temporary sessions directory."""
    d = tmp_path / ".forge/sessions"
    d.mkdir()
    return d


@pytest.fixture()
def registry(tmp_sessions: Path) -> UnifiedAgentRegistry:
    """A fresh UnifiedAgentRegistry backed by a temp directory."""
    return UnifiedAgentRegistry(sessions_dir=tmp_sessions, sync_interval_seconds=30)


def _make_agent_data(**overrides: object) -> dict:
    """Build a minimal agent data dict."""
    base = {
        "role": "backend-engineer",
        "project": "voice-coach",
        "task": "implement auth",
        "domain": "brandfocus-ai",
    }
    base.update(overrides)
    return base


def _write_file_agent(sessions_dir: Path, agent_id: str, **fields: object) -> dict:
    """Write a bare-minimum session file as if a CLI script created it."""
    data = {
        "id": agent_id,
        "session_id": agent_id,
        "role": fields.get("role", "script-agent"),
        "project": fields.get("project", "some-project"),
        "task": fields.get("task", "background task"),
        "status": fields.get("status", "active"),
        "progress": fields.get("progress", 0),
        "registered_at": fields.get("registered_at", datetime.now(UTC).isoformat()),
        "last_activity": fields.get("last_activity", datetime.now(UTC).isoformat()),
    }
    data.update({k: v for k, v in fields.items() if k not in data})
    file_path = sessions_dir / f"session_{agent_id}.json"
    file_path.write_text(json.dumps(data, indent=2))
    return data


# ---------------------------------------------------------------------------
# Helper / utility function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_ts_valid_utc(self):
        now = datetime.now(UTC)
        result = _parse_ts(now.isoformat())
        assert result is not None
        assert result.tzinfo is not None

    def test_parse_ts_naive_string_gains_utc(self):
        ts = "2026-01-01T12:00:00"
        result = _parse_ts(ts)
        assert result is not None
        assert result.tzinfo is UTC

    def test_parse_ts_invalid_returns_none(self):
        assert _parse_ts("not-a-date") is None
        assert _parse_ts(None) is None
        assert _parse_ts("") is None

    def test_normalise_file_agent_adds_defaults(self):
        raw = {"id": "abc123"}
        result = _normalise_file_agent(raw)
        assert result["role"] == "unknown"
        assert result["status"] == "unknown"
        assert result["progress"] == 0
        assert result["session_id"] == "abc123"
        assert result["source"] == "file"

    def test_normalise_file_agent_session_id_alias(self):
        raw = {"session_id": "xyz789", "role": "builder"}
        result = _normalise_file_agent(raw)
        assert result["id"] == "xyz789"
        assert result["session_id"] == "xyz789"

    def test_session_path_construction(self, tmp_sessions: Path):
        path = _session_path(tmp_sessions, "abc123")
        assert path == tmp_sessions / "session_abc123.json"


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_returns_agent_with_id(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        assert "id" in agent
        assert len(agent["id"]) > 0

    def test_register_uses_provided_id(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data(id="custom-id"))
        assert agent["id"] == "custom-id"

    def test_register_sets_defaults(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        assert agent["status"] == "active"
        assert agent["progress"] == 0
        assert agent["source"] == "api"

    def test_register_persists_to_memory(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        assert agent["id"] in registry._agents

    def test_register_writes_session_file(self, registry: UnifiedAgentRegistry, tmp_sessions: Path):
        agent = registry.register(_make_agent_data())
        file_path = tmp_sessions / f"session_{agent['id']}.json"
        assert file_path.exists(), "Session file should be created on register"

    def test_register_file_contains_valid_json(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        agent = registry.register(_make_agent_data(role="test-role"))
        file_path = tmp_sessions / f"session_{agent['id']}.json"
        data = json.loads(file_path.read_text())
        assert data["id"] == agent["id"]
        assert data["role"] == "test-role"

    def test_register_file_has_session_id_alias(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        agent = registry.register(_make_agent_data())
        file_path = tmp_sessions / f"session_{agent['id']}.json"
        data = json.loads(file_path.read_text())
        assert "session_id" in data
        assert data["session_id"] == agent["id"]

    def test_register_multiple_agents(self, registry: UnifiedAgentRegistry, tmp_sessions: Path):
        ids = [registry.register(_make_agent_data(task=f"task-{i}"))["id"] for i in range(5)]
        assert len(ids) == 5
        assert len(set(ids)) == 5  # all unique
        session_files = list(tmp_sessions.glob("session_*.json"))
        assert len(session_files) == 5

    def test_register_updates_parent_children(self, registry: UnifiedAgentRegistry):
        parent = registry.register(_make_agent_data(id="parent-001"))
        child = registry.register(_make_agent_data(id="child-001", parent_id="parent-001"))
        # Parent in-memory record should list the child
        assert child["id"] in registry._agents["parent-001"]["children"]

    def test_register_with_all_optional_fields(self, registry: UnifiedAgentRegistry):
        agent = registry.register(
            _make_agent_data(
                name="Agent Alpha",
                skills=["python", "fastapi"],
                tmux_session="forge:agent-alpha",
                parent_id=None,
            )
        )
        assert agent["name"] == "Agent Alpha"
        assert agent["skills"] == ["python", "fastapi"]
        assert agent["tmux_session"] == "forge:agent-alpha"


# ---------------------------------------------------------------------------
# Unregister
# ---------------------------------------------------------------------------


class TestUnregister:
    def test_unregister_removes_from_memory(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        aid = agent["id"]
        assert registry.unregister(aid) is True
        assert aid not in registry._agents

    def test_unregister_removes_session_file(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        agent = registry.register(_make_agent_data())
        aid = agent["id"]
        file_path = tmp_sessions / f"session_{aid}.json"
        assert file_path.exists()
        registry.unregister(aid)
        assert not file_path.exists(), "Session file should be deleted on unregister"

    def test_unregister_returns_false_for_unknown_id(self, registry: UnifiedAgentRegistry):
        assert registry.unregister("nonexistent-id") is False

    def test_unregister_handles_missing_file_gracefully(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        agent = registry.register(_make_agent_data())
        aid = agent["id"]
        # Delete file manually before unregistering
        file_path = tmp_sessions / f"session_{aid}.json"
        file_path.unlink()
        # Should not raise; just remove from memory
        assert registry.unregister(aid) is True
        assert aid not in registry._agents


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------


class TestGetAgent:
    def test_get_agent_from_memory(self, registry: UnifiedAgentRegistry):
        registered = registry.register(_make_agent_data())
        fetched = registry.get_agent(registered["id"])
        assert fetched is not None
        assert fetched["id"] == registered["id"]

    def test_get_agent_returns_none_for_unknown(self, registry: UnifiedAgentRegistry):
        assert registry.get_agent("unknown-id") is None

    def test_get_agent_fallback_to_file(self, registry: UnifiedAgentRegistry, tmp_sessions: Path):
        """Agents created by external scripts (file-only) are found via file fallback."""
        _write_file_agent(tmp_sessions, "script-agent-1", role="script-runner")
        # Registry has no in-memory record for this agent
        assert "script-agent-1" not in registry._agents
        agent = registry.get_agent("script-agent-1")
        assert agent is not None
        assert agent["id"] == "script-agent-1"
        assert agent["role"] == "script-runner"

    def test_get_agent_file_fallback_loads_into_memory(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        """After a file-fallback lookup, the agent is cached in memory."""
        _write_file_agent(tmp_sessions, "file-only-1")
        registry.get_agent("file-only-1")
        assert "file-only-1" in registry._agents

    def test_get_agent_memory_returns_copy(self, registry: UnifiedAgentRegistry):
        """Returned dict is a copy; mutations do not affect the registry."""
        registered = registry.register(_make_agent_data())
        copy = registry.get_agent(registered["id"])
        copy["status"] = "mutated"
        assert registry._agents[registered["id"]]["status"] == "active"


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------


class TestListAgents:
    def test_list_agents_empty_registry(self, registry: UnifiedAgentRegistry):
        assert registry.list_agents() == []

    def test_list_agents_returns_all_memory_agents(self, registry: UnifiedAgentRegistry):
        registry.register(_make_agent_data(id="a1"))
        registry.register(_make_agent_data(id="a2"))
        agents = registry.list_agents()
        ids = {a["id"] for a in agents}
        assert ids == {"a1", "a2"}

    def test_list_agents_includes_file_only_agents(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        """Agents spawned via CLI scripts (file-only) appear in list."""
        _write_file_agent(tmp_sessions, "cli-agent-A", status="active")
        registry.register(_make_agent_data(id="api-agent-B"))
        agents = registry.list_agents()
        ids = {a["id"] for a in agents}
        assert "cli-agent-A" in ids
        assert "api-agent-B" in ids

    def test_list_agents_memory_wins_over_file(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        """If the same agent exists in both memory and file, memory record wins."""
        _write_file_agent(tmp_sessions, "both-agent", status="idle", progress=10)
        # Also register in memory with different values
        registry.register(_make_agent_data(id="both-agent", status="active", progress=75))
        agents = registry.list_agents()
        match = next(a for a in agents if a["id"] == "both-agent")
        assert match["status"] == "active"
        assert match["progress"] == 75

    def test_list_agents_status_filter(self, registry: UnifiedAgentRegistry):
        registry.register(_make_agent_data(id="act-1", status="active"))
        registry.register(_make_agent_data(id="idle-1", status="idle"))
        registry.register(_make_agent_data(id="act-2", status="active"))
        active = registry.list_agents(status="active")
        assert all(a["status"] == "active" for a in active)
        assert len(active) == 2

    def test_list_agents_status_filter_file_agents(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        _write_file_agent(tmp_sessions, "file-idle", status="idle")
        _write_file_agent(tmp_sessions, "file-active", status="active")
        active = registry.list_agents(status="active")
        ids = {a["id"] for a in active}
        assert "file-active" in ids
        assert "file-idle" not in ids

    def test_list_agents_no_session_dir(self, tmp_path: Path):
        """list_agents works even if the sessions directory does not exist."""
        missing_dir = tmp_path / "nonexistent"
        reg = UnifiedAgentRegistry(sessions_dir=missing_dir)
        reg.register(_make_agent_data(id="mem-only"))
        agents = reg.list_agents()
        assert len(agents) == 1
        assert agents[0]["id"] == "mem-only"


# ---------------------------------------------------------------------------
# update_heartbeat
# ---------------------------------------------------------------------------


class TestUpdateHeartbeat:
    def test_update_heartbeat_updates_status(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        result = registry.update_heartbeat(agent["id"], {"status": "waiting"})
        assert result is not None
        assert result["status"] == "waiting"

    def test_update_heartbeat_updates_progress(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        result = registry.update_heartbeat(agent["id"], {"progress": 42})
        assert result["progress"] == 42

    def test_update_heartbeat_updates_last_activity(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        before = agent["last_activity"]
        # Small sleep to ensure timestamp advances
        time.sleep(0.01)
        result = registry.update_heartbeat(agent["id"], {})
        assert result["last_activity"] >= before

    def test_update_heartbeat_does_not_overwrite_id(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        aid = agent["id"]
        registry.update_heartbeat(aid, {"id": "hacked-id"})
        # id is protected
        assert aid in registry._agents

    def test_update_heartbeat_returns_none_for_unknown(self, registry: UnifiedAgentRegistry):
        assert registry.update_heartbeat("ghost-id", {"status": "active"}) is None

    def test_update_heartbeat_hydrates_file_agent(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        """update_heartbeat loads a file-only agent into memory, then updates it."""
        _write_file_agent(tmp_sessions, "file-hb-agent", status="idle")
        result = registry.update_heartbeat("file-hb-agent", {"status": "active", "progress": 30})
        assert result is not None
        assert result["status"] == "active"
        assert "file-hb-agent" in registry._agents

    def test_update_heartbeat_writes_file_when_no_file_yet(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        """Heartbeat always writes session file if no file exists yet."""
        agent = registry.register(_make_agent_data(id="hb-fresh"))
        # Delete the file that register created
        file_path = tmp_sessions / "session_hb-fresh.json"
        file_path.unlink()
        # Force heartbeat — should re-create file because file doesn't exist
        registry.update_heartbeat("hb-fresh", {"progress": 5})
        assert file_path.exists()

    def test_update_heartbeat_returns_copy(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        result = registry.update_heartbeat(agent["id"], {"progress": 10})
        result["progress"] = 999
        assert registry._agents[agent["id"]]["progress"] == 10


# ---------------------------------------------------------------------------
# sync_to_files
# ---------------------------------------------------------------------------


class TestSyncToFiles:
    def test_sync_to_files_writes_all_agents(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        registry.register(_make_agent_data(id="s1"))
        registry.register(_make_agent_data(id="s2"))
        registry.register(_make_agent_data(id="s3"))
        # Clear session files to simulate a state where files are stale
        for f in tmp_sessions.glob("*.json"):
            f.unlink()
        count = registry.sync_to_files()
        assert count == 3
        written = {f.stem.replace("session_", "") for f in tmp_sessions.glob("session_*.json")}
        assert written == {"s1", "s2", "s3"}

    def test_sync_to_files_creates_dir_if_missing(self, tmp_path: Path):
        missing_dir = tmp_path / "new_sessions"
        reg = UnifiedAgentRegistry(sessions_dir=missing_dir)
        reg.register(_make_agent_data(id="create-dir-test"))
        # Remove the dir that register created (register itself calls _ensure_dir)
        import shutil

        shutil.rmtree(missing_dir)
        count = reg.sync_to_files()
        assert count == 1
        assert missing_dir.exists()

    def test_sync_to_files_updates_last_sync_at(self, registry: UnifiedAgentRegistry):
        registry.register(_make_agent_data(id="ts-agent"))
        registry.sync_to_files()
        assert registry._last_sync_at is not None

    def test_sync_to_files_overwrites_stale_files(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        agent = registry.register(_make_agent_data(id="overwrite-me"))
        # Manually write a stale file
        file_path = tmp_sessions / "session_overwrite-me.json"
        file_path.write_text(json.dumps({"id": "overwrite-me", "status": "stale"}))
        # Update in memory
        registry._agents["overwrite-me"]["status"] = "active"
        registry.sync_to_files()
        data = json.loads(file_path.read_text())
        assert data["status"] == "active"

    def test_sync_to_files_empty_registry_writes_nothing(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        count = registry.sync_to_files()
        assert count == 0


# ---------------------------------------------------------------------------
# sync_from_files
# ---------------------------------------------------------------------------


class TestSyncFromFiles:
    def test_sync_from_files_loads_file_only_agents(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        _write_file_agent(tmp_sessions, "restore-1", status="active")
        _write_file_agent(tmp_sessions, "restore-2", status="idle")
        count = registry.sync_from_files()
        assert count == 2
        assert "restore-1" in registry._agents
        assert "restore-2" in registry._agents

    def test_sync_from_files_does_not_overwrite_newer_memory(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        """Memory record with newer timestamp wins over older file."""
        old_ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        _write_file_agent(tmp_sessions, "stale-file", status="idle", last_activity=old_ts)
        # Register in memory with a newer timestamp (register sets last_activity to now)
        registry.register(_make_agent_data(id="stale-file", status="active"))
        count = registry.sync_from_files()
        # Memory record should prevail
        assert registry._agents["stale-file"]["status"] == "active"

    def test_sync_from_files_updates_when_file_is_newer(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        """File record with newer timestamp wins over older memory."""
        old_ts = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        registry.register(_make_agent_data(id="old-memory", status="active"))
        # Force an old timestamp into memory
        registry._agents["old-memory"]["last_activity"] = old_ts

        # Write a newer file
        new_ts = datetime.now(UTC).isoformat()
        _write_file_agent(tmp_sessions, "old-memory", status="completed", last_activity=new_ts)
        registry.sync_from_files()
        assert registry._agents["old-memory"]["status"] == "completed"

    def test_sync_from_files_skips_corrupt_json(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        (tmp_sessions / "session_bad.json").write_text("{not valid json}")
        _write_file_agent(tmp_sessions, "good-agent")
        # Should not raise; corrupt file is silently skipped
        count = registry.sync_from_files()
        assert count >= 1  # good-agent is loaded
        assert "good-agent" in registry._agents

    def test_sync_from_files_handles_missing_dir(self, tmp_path: Path):
        missing_dir = tmp_path / "missing"
        reg = UnifiedAgentRegistry(sessions_dir=missing_dir)
        count = reg.sync_from_files()
        assert count == 0

    def test_sync_from_files_recovery_after_restart(self, tmp_sessions: Path):
        """Simulate process restart: register agents, 'restart' (new registry), recover."""
        # Simulate first registry session
        first_registry = UnifiedAgentRegistry(sessions_dir=tmp_sessions)
        first_registry.register(_make_agent_data(id="persistent-1", status="active"))
        first_registry.register(_make_agent_data(id="persistent-2", status="waiting"))
        first_registry.sync_to_files()

        # Simulate restart: create a new registry with the same sessions dir
        second_registry = UnifiedAgentRegistry(sessions_dir=tmp_sessions)
        assert len(second_registry._agents) == 0  # memory starts empty

        count = second_registry.sync_from_files()
        assert count == 2
        ids = set(second_registry._agents.keys())
        assert "persistent-1" in ids
        assert "persistent-2" in ids


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_get_stats_empty(self, registry: UnifiedAgentRegistry):
        stats = registry.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["last_sync_at"] is None

    def test_get_stats_total(self, registry: UnifiedAgentRegistry):
        registry.register(_make_agent_data(id="g1"))
        registry.register(_make_agent_data(id="g2"))
        stats = registry.get_stats()
        assert stats["total"] == 2

    def test_get_stats_by_status(self, registry: UnifiedAgentRegistry):
        registry.register(_make_agent_data(id="act-x", status="active"))
        registry.register(_make_agent_data(id="act-y", status="active"))
        registry.register(_make_agent_data(id="idle-z", status="idle"))
        stats = registry.get_stats()
        assert stats["by_status"]["active"] == 2
        assert stats["by_status"]["idle"] == 1

    def test_get_stats_last_sync_at_after_sync(self, registry: UnifiedAgentRegistry):
        registry.register(_make_agent_data(id="sync-me"))
        registry.sync_to_files()
        stats = registry.get_stats()
        assert stats["last_sync_at"] is not None

    def test_get_stats_includes_file_only_agents(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        _write_file_agent(tmp_sessions, "file-stat-agent", status="completed")
        stats = registry.get_stats()
        assert stats["total"] >= 1
        assert stats["by_status"].get("completed", 0) >= 1

    def test_get_stats_sessions_dir(self, registry: UnifiedAgentRegistry, tmp_sessions: Path):
        stats = registry.get_stats()
        assert stats["sessions_dir"] == str(tmp_sessions)

    def test_get_stats_sync_interval(self, tmp_sessions: Path):
        reg = UnifiedAgentRegistry(sessions_dir=tmp_sessions, sync_interval_seconds=60)
        stats = reg.get_stats()
        assert stats["sync_interval_seconds"] == 60


# ---------------------------------------------------------------------------
# Concurrent access safety
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_register_no_data_race(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        """Multiple threads registering agents simultaneously should produce no duplicates."""
        n_threads = 20
        results: list[dict] = []
        errors: list[Exception] = []

        def worker():
            try:
                agent = registry.register(_make_agent_data())
                results.append(agent)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        # All registrations should have unique IDs
        ids = [r["id"] for r in results]
        assert len(ids) == len(set(ids)), "Duplicate IDs detected"
        assert len(results) == n_threads

    def test_concurrent_heartbeat_and_list(self, registry: UnifiedAgentRegistry):
        """Concurrent heartbeats and list_agents calls should not raise."""
        agent = registry.register(_make_agent_data(id="concurrent-hb"))
        errors: list[Exception] = []

        def heartbeat_worker():
            for _ in range(10):
                try:
                    registry.update_heartbeat(agent["id"], {"progress": 50})
                except Exception as exc:
                    errors.append(exc)

        def list_worker():
            for _ in range(10):
                try:
                    registry.list_agents()
                except Exception as exc:
                    errors.append(exc)

        threads = [
            threading.Thread(target=heartbeat_worker),
            threading.Thread(target=heartbeat_worker),
            threading.Thread(target=list_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

    def test_concurrent_register_and_unregister(self, registry: UnifiedAgentRegistry):
        """Mix of register and unregister calls should not corrupt internal state."""
        # Pre-register some agents to unregister
        agents = [registry.register(_make_agent_data(id=f"del-{i}")) for i in range(10)]
        errors: list[Exception] = []

        def unregister_worker(agent_id: str):
            try:
                registry.unregister(agent_id)
            except Exception as exc:
                errors.append(exc)

        def register_worker():
            try:
                registry.register(_make_agent_data())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=unregister_worker, args=(a["id"],)) for a in agents]
        threads += [threading.Thread(target=register_worker) for _ in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

    def test_concurrent_sync_to_files(self, registry: UnifiedAgentRegistry):
        """sync_to_files called from multiple threads should not corrupt files."""
        for i in range(5):
            registry.register(_make_agent_data(id=f"fsync-{i}"))

        errors: list[Exception] = []

        def sync_worker():
            try:
                registry.sync_to_files()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=sync_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"


# ---------------------------------------------------------------------------
# Singleton pattern
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_unified_registry_returns_same_instance(self, tmp_sessions: Path):
        reset_unified_registry()
        try:
            r1 = get_unified_registry(sessions_dir=tmp_sessions)
            r2 = get_unified_registry()
            assert r1 is r2
        finally:
            reset_unified_registry()

    def test_reset_unified_registry_creates_new_instance(self, tmp_sessions: Path):
        reset_unified_registry()
        r1 = get_unified_registry(sessions_dir=tmp_sessions)
        reset_unified_registry()
        r2 = get_unified_registry(sessions_dir=tmp_sessions)
        assert r1 is not r2

    def test_singleton_thread_safety(self, tmp_sessions: Path):
        """Concurrent calls to get_unified_registry return the same object."""
        reset_unified_registry()
        instances: list[UnifiedAgentRegistry] = []
        errors: list[Exception] = []

        def getter():
            try:
                instances.append(get_unified_registry(sessions_dir=tmp_sessions))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=getter) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # All should be the same object
        assert all(inst is instances[0] for inst in instances)
        reset_unified_registry()

    def test_get_unified_registry_after_reset_uses_new_dir(self, tmp_path: Path):
        """After reset, passing a new sessions_dir creates a fresh registry."""
        reset_unified_registry()
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        r1 = get_unified_registry(sessions_dir=dir1)
        reset_unified_registry()
        r2 = get_unified_registry(sessions_dir=dir2)
        assert str(r1._sessions_dir) != str(r2._sessions_dir)
        reset_unified_registry()


# ---------------------------------------------------------------------------
# Edge cases / resilience
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_register_without_role_uses_unknown(self, registry: UnifiedAgentRegistry):
        agent = registry.register({"project": "proj", "task": "do something"})
        assert agent["role"] == "unknown"

    def test_register_and_get_returns_consistent_data(self, registry: UnifiedAgentRegistry):
        registered = registry.register(_make_agent_data(id="consistency-check"))
        fetched = registry.get_agent("consistency-check")
        assert fetched["role"] == registered["role"]
        assert fetched["project"] == registered["project"]
        assert fetched["task"] == registered["task"]

    def test_list_agents_skips_corrupt_session_files(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        (tmp_sessions / "session_corrupt.json").write_text("{{invalid json]}")
        _write_file_agent(tmp_sessions, "valid-agent")
        agents = registry.list_agents()
        ids = {a["id"] for a in agents}
        assert "valid-agent" in ids

    def test_sync_from_files_ignores_non_dict_json(
        self, registry: UnifiedAgentRegistry, tmp_sessions: Path
    ):
        (tmp_sessions / "session_list.json").write_text("[1, 2, 3]")
        # Should not raise; non-dict file is silently skipped
        count = registry.sync_from_files()
        assert count == 0

    def test_register_preserves_custom_registered_at(self, registry: UnifiedAgentRegistry):
        custom_ts = "2026-01-01T00:00:00+00:00"
        agent = registry.register(_make_agent_data(registered_at=custom_ts))
        assert agent["registered_at"] == custom_ts

    def test_list_agents_with_no_status_filter_includes_all(self, registry: UnifiedAgentRegistry):
        for st in ["active", "idle", "completed", "failed"]:
            registry.register(_make_agent_data(status=st))
        agents = registry.list_agents()
        statuses = {a["status"] for a in agents}
        assert statuses == {"active", "idle", "completed", "failed"}

    def test_unregister_twice_is_idempotent(self, registry: UnifiedAgentRegistry):
        agent = registry.register(_make_agent_data())
        aid = agent["id"]
        assert registry.unregister(aid) is True
        assert registry.unregister(aid) is False  # second call returns False, no exception
