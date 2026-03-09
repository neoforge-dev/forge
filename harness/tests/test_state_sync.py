"""
Tests for forge_harness.state_sync module.

Coverage targets:
- SyncResult model: all fields, properties, to_dict()
- StateSynchronizer.sync_agents_to_files: happy path, updates, no-id skip, OS error
- StateSynchronizer.sync_files_to_store: happy path, corrupt JSON, missing dir, non-dict
- StateSynchronizer.diff: added / updated / removed detection
- StateSynchronizer.reconcile: file-only, store-only, lww by timestamp, conflict flag
- Edge cases: missing sessions dir, empty states, corrupt JSON, no timestamps
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.state_sync import (
    StateSynchronizer,
    SyncResult,
    _agent_id,
    _normalise_agent,
    _session_filename,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Return a temporary .forge/sessions directory."""
    d = tmp_path / ".forge/sessions"
    d.mkdir()
    return d


@pytest.fixture
def syncer(sessions_dir: Path) -> StateSynchronizer:
    """Return a StateSynchronizer backed by a temp directory."""
    return StateSynchronizer(sessions_dir=sessions_dir)


def _make_agent(
    agent_id: str = "agent-001",
    status: str = "active",
    heartbeat_offset_seconds: int = 0,
    **extra: object,
) -> dict:
    """Helper: produce a minimal agent dict."""
    hb = datetime.now(UTC) + timedelta(seconds=heartbeat_offset_seconds)
    return {
        "id": agent_id,
        "session_id": agent_id,
        "role": "builder",
        "name": f"Agent {agent_id}",
        "domain": "test-domain",
        "project": "test-project",
        "task": "some task",
        "status": status,
        "progress": 50,
        "current_task": "doing stuff",
        "files_modified": [],
        "token_usage": {},
        "last_activity": hb.isoformat(),
        "last_heartbeat": hb.isoformat(),
        "registered_at": (hb - timedelta(hours=1)).isoformat(),
        **extra,
    }


# =============================================================================
# SyncResult Tests
# =============================================================================


class TestSyncResult:
    """Tests for the SyncResult data model."""

    def test_default_fields(self):
        """Verify defaults after construction."""
        result = SyncResult()

        assert result.added == []
        assert result.updated == []
        assert result.removed == []
        assert result.conflicts == []
        assert result.errors == {}
        assert result.success is True
        assert isinstance(result.timestamp, datetime)

    def test_total_changes_empty(self):
        """total_changes is 0 when nothing changed."""
        result = SyncResult()
        assert result.total_changes == 0

    def test_total_changes_populated(self):
        """total_changes sums added + updated + removed."""
        result = SyncResult(
            added=["a", "b"],
            updated=["c"],
            removed=["d", "e", "f"],
        )
        assert result.total_changes == 6

    def test_has_conflicts_false(self):
        """has_conflicts is False when conflicts list is empty."""
        assert SyncResult().has_conflicts is False

    def test_has_conflicts_true(self):
        """has_conflicts is True when conflicts list is non-empty."""
        assert SyncResult(conflicts=["agent-001"]).has_conflicts is True

    def test_to_dict_shape(self):
        """to_dict returns the expected keys and types."""
        ts = datetime.now(UTC)
        result = SyncResult(
            added=["a"],
            updated=["b"],
            removed=["c"],
            conflicts=["d"],
            errors={"e": "boom"},
            timestamp=ts,
            success=False,
        )
        d = result.to_dict()

        assert d["added"] == ["a"]
        assert d["updated"] == ["b"]
        assert d["removed"] == ["c"]
        assert d["conflicts"] == ["d"]
        assert d["errors"] == {"e": "boom"}
        assert d["timestamp"] == ts.isoformat()
        assert d["success"] is False
        assert d["total_changes"] == 3

    def test_to_dict_with_all_defaults(self):
        """to_dict works on a fully default SyncResult."""
        d = SyncResult().to_dict()
        assert d["total_changes"] == 0
        assert d["success"] is True

    def test_success_flag_defaults_true(self):
        """success is True by default."""
        assert SyncResult().success is True

    def test_success_flag_can_be_false(self):
        """success can be set to False."""
        result = SyncResult(success=False)
        assert result.success is False


# =============================================================================
# _normalise_agent / _agent_id helpers
# =============================================================================


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_normalise_preserves_id(self):
        """_normalise_agent keeps existing 'id' key."""
        raw = {"id": "abc", "status": "active"}
        result = _normalise_agent(raw)
        assert result["id"] == "abc"
        assert result["session_id"] == "abc"

    def test_normalise_promotes_session_id(self):
        """_normalise_agent promotes session_id → id when id is absent."""
        raw = {"session_id": "xyz", "role": "builder"}
        result = _normalise_agent(raw)
        assert result["id"] == "xyz"
        assert result["session_id"] == "xyz"

    def test_normalise_defaults(self):
        """_normalise_agent fills in default values."""
        result = _normalise_agent({"id": "abc"})
        assert result["status"] == "unknown"
        assert result["progress"] == 0
        assert result["files_modified"] == []
        assert result["token_usage"] == {}
        assert result["is_stale"] is False

    def test_agent_id_prefers_id(self):
        """_agent_id returns 'id' when both are present."""
        assert _agent_id({"id": "first", "session_id": "second"}) == "first"

    def test_agent_id_falls_back_to_session_id(self):
        """_agent_id falls back to session_id when id is absent."""
        assert _agent_id({"session_id": "session-abc"}) == "session-abc"

    def test_agent_id_empty_string_for_missing(self):
        """_agent_id returns empty string when neither key exists."""
        assert _agent_id({}) == ""

    def test_session_filename(self):
        """_session_filename formats the filename correctly."""
        assert _session_filename("my-agent") == "session_my-agent.json"


# =============================================================================
# StateSynchronizer – sync_agents_to_files
# =============================================================================


class TestSyncAgentsToFiles:
    """Tests for sync_agents_to_files."""

    def test_writes_single_agent(self, syncer, sessions_dir):
        """A single agent is written to a JSON file."""
        agent = _make_agent("agent-001")
        result = syncer.sync_agents_to_files([agent])

        assert result.success is True
        assert "agent-001" in result.added
        assert result.total_changes == 1

        written = json.loads((sessions_dir / "session_agent-001.json").read_text())
        assert written["id"] == "agent-001"
        assert written["status"] == "active"

    def test_writes_multiple_agents(self, syncer, sessions_dir):
        """Multiple agents are all written correctly."""
        agents = [_make_agent(f"agent-{i:03d}") for i in range(5)]
        result = syncer.sync_agents_to_files(agents)

        assert result.success is True
        assert len(result.added) == 5
        assert result.total_changes == 5

        for i in range(5):
            assert (sessions_dir / f"session_agent-{i:03d}.json").exists()

    def test_update_existing_agent(self, syncer, sessions_dir):
        """Second sync of same agent ID is recorded as updated, not added."""
        agent = _make_agent("agent-001", status="active")
        syncer.sync_agents_to_files([agent])

        agent_v2 = _make_agent("agent-001", status="completed")
        result = syncer.sync_agents_to_files([agent_v2])

        assert "agent-001" in result.updated
        assert result.added == []
        written = json.loads((sessions_dir / "session_agent-001.json").read_text())
        assert written["status"] == "completed"

    def test_preserves_registered_at_from_file(self, syncer, sessions_dir):
        """registered_at from an existing file is preserved if not in registry data."""
        original_time = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        existing = {"id": "agent-001", "session_id": "agent-001", "registered_at": original_time}
        (sessions_dir / "session_agent-001.json").write_text(json.dumps(existing))

        # Registry data without registered_at
        agent = {"id": "agent-001", "session_id": "agent-001", "status": "active"}
        syncer.sync_agents_to_files([agent])

        written = json.loads((sessions_dir / "session_agent-001.json").read_text())
        assert written["registered_at"] == original_time

    def test_skips_agents_without_id(self, syncer):
        """Agents lacking both id and session_id are silently skipped."""
        result = syncer.sync_agents_to_files([{"role": "builder", "status": "active"}])
        assert result.total_changes == 0
        assert result.success is True

    def test_handles_os_error_on_write(self, syncer, sessions_dir):
        """OS error during write marks success=False and logs the error."""
        agent = _make_agent("agent-err")

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = syncer.sync_agents_to_files([agent])

        assert result.success is False
        assert "agent-err" in result.errors

    def test_creates_sessions_dir_if_missing(self, tmp_path):
        """sync_agents_to_files creates the directory if it doesn't exist."""
        missing_dir = tmp_path / "new_sessions"
        assert not missing_dir.exists()

        syncer = StateSynchronizer(sessions_dir=missing_dir)
        result = syncer.sync_agents_to_files([_make_agent("agent-001")])

        assert missing_dir.exists()
        assert result.success is True
        assert "agent-001" in result.added

    def test_default_heartbeat_set(self, syncer, sessions_dir):
        """last_heartbeat is set from last_activity if absent."""
        activity = datetime.now(UTC).isoformat()
        agent = {"id": "a1", "session_id": "a1", "last_activity": activity}
        syncer.sync_agents_to_files([agent])

        written = json.loads((sessions_dir / "session_a1.json").read_text())
        assert written["last_heartbeat"] == activity

    def test_empty_registry_produces_empty_result(self, syncer):
        """Empty agent list returns a zero-change SyncResult."""
        result = syncer.sync_agents_to_files([])
        assert result.total_changes == 0
        assert result.success is True


# =============================================================================
# StateSynchronizer – sync_files_to_store
# =============================================================================


class TestSyncFilesToStore:
    """Tests for sync_files_to_store."""

    def test_reads_all_session_files(self, syncer, sessions_dir):
        """All valid session files are returned."""
        for i in range(3):
            (sessions_dir / f"session_agent-{i:03d}.json").write_text(
                json.dumps(_make_agent(f"agent-{i:03d}"))
            )

        result = syncer.sync_files_to_store()

        assert len(result) == 3
        for i in range(3):
            assert f"agent-{i:03d}" in result

    def test_skips_corrupt_json(self, syncer, sessions_dir):
        """Files with invalid JSON are silently skipped."""
        (sessions_dir / "session_good.json").write_text(json.dumps(_make_agent("good")))
        (sessions_dir / "session_bad.json").write_text("{not valid json")

        result = syncer.sync_files_to_store()

        assert "good" in result
        assert len(result) == 1

    def test_skips_non_dict_json(self, syncer, sessions_dir):
        """JSON files that are not objects (e.g. arrays) are skipped."""
        (sessions_dir / "session_ok.json").write_text(json.dumps(_make_agent("ok")))
        (sessions_dir / "session_arr.json").write_text(json.dumps([1, 2, 3]))

        result = syncer.sync_files_to_store()

        assert "ok" in result
        assert len(result) == 1

    def test_returns_empty_for_missing_directory(self, tmp_path):
        """Returns empty dict when sessions_dir does not exist."""
        syncer = StateSynchronizer(sessions_dir=tmp_path / "nonexistent")
        result = syncer.sync_files_to_store()
        assert result == {}

    def test_returns_empty_for_empty_directory(self, syncer, sessions_dir):
        """Returns empty dict when sessions_dir is empty."""
        result = syncer.sync_files_to_store()
        assert result == {}

    def test_normalises_session_id_only_files(self, syncer, sessions_dir):
        """Files using session_id (not id) are normalised correctly."""
        raw = {"session_id": "sess-xyz", "domain": "d", "project": "p"}
        (sessions_dir / "session_sess-xyz.json").write_text(json.dumps(raw))

        result = syncer.sync_files_to_store()

        assert "sess-xyz" in result
        assert result["sess-xyz"]["id"] == "sess-xyz"

    def test_custom_dir_argument(self, tmp_path, sessions_dir):
        """Passing sessions_dir to sync_files_to_store overrides the instance dir."""
        other_dir = tmp_path / "other_sessions"
        other_dir.mkdir()
        (other_dir / "session_other.json").write_text(json.dumps(_make_agent("other")))

        syncer = StateSynchronizer(sessions_dir=sessions_dir)
        result = syncer.sync_files_to_store(sessions_dir=other_dir)

        assert "other" in result
        # Original sessions_dir is untouched
        assert syncer.sync_files_to_store() == {}

    def test_skips_files_without_id(self, syncer, sessions_dir):
        """Files with no id or session_id are skipped."""
        raw = {"role": "builder", "status": "active"}
        (sessions_dir / "session_no_id.json").write_text(json.dumps(raw))

        result = syncer.sync_files_to_store()
        assert len(result) == 0


# =============================================================================
# StateSynchronizer – diff
# =============================================================================


class TestDiff:
    """Tests for the diff method."""

    def test_empty_states_produce_no_changes(self, syncer):
        """diff of two empty dicts yields no changes."""
        result = syncer.diff({}, {})
        assert result.total_changes == 0

    def test_detects_added_agents(self, syncer):
        """Agents present in store but not in file are in result.added."""
        file_state: dict = {}
        store_state = {
            "a1": _make_agent("a1"),
            "a2": _make_agent("a2"),
        }
        result = syncer.diff(file_state, store_state)

        assert sorted(result.added) == ["a1", "a2"]
        assert result.removed == []
        assert result.updated == []

    def test_detects_removed_agents(self, syncer):
        """Agents present in file but not in store are in result.removed."""
        file_state = {
            "a1": _make_agent("a1"),
        }
        store_state: dict = {}
        result = syncer.diff(file_state, store_state)

        assert result.removed == ["a1"]
        assert result.added == []
        assert result.updated == []

    def test_detects_updated_agents(self, syncer):
        """Agents in both states with different content are in result.updated."""
        file_state = {"a1": _make_agent("a1", status="active")}
        store_state = {"a1": _make_agent("a1", status="completed")}

        result = syncer.diff(file_state, store_state)

        assert "a1" in result.updated
        assert result.added == []
        assert result.removed == []

    def test_no_change_when_identical(self, syncer):
        """Identical states produce no diff."""
        agent = _make_agent("a1")
        result = syncer.diff({"a1": agent}, {"a1": dict(agent)})
        assert result.total_changes == 0

    def test_combined_changes(self, syncer):
        """diff correctly handles simultaneous adds, updates and removals."""
        # Build agents whose timestamps are identical so only status differs
        ts = datetime.now(UTC).isoformat()

        def _frozen_agent(agent_id: str, status: str) -> dict:
            a = _make_agent(agent_id, status=status)
            a["last_heartbeat"] = ts
            a["last_activity"] = ts
            a["registered_at"] = ts
            return a

        keep_agent = _frozen_agent("keep", "active")
        file_state = {
            "keep": dict(keep_agent),
            "update": _frozen_agent("update", "active"),
            "remove": _frozen_agent("remove", "active"),
        }
        store_state = {
            "keep": dict(keep_agent),  # exact same content → no update
            "update": _frozen_agent("update", "completed"),  # status changed → updated
            "new": _frozen_agent("new", "active"),  # not in file → added
        }
        result = syncer.diff(file_state, store_state)

        assert result.added == ["new"]
        assert result.removed == ["remove"]
        assert result.updated == ["update"]

    def test_diff_result_is_sorted(self, syncer):
        """added, updated, and removed lists are sorted."""
        file_state = {"z": _make_agent("z"), "m": _make_agent("m", status="active")}
        store_state = {"a": _make_agent("a"), "m": _make_agent("m", status="idle")}

        result = syncer.diff(file_state, store_state)

        assert result.added == sorted(result.added)
        assert result.removed == sorted(result.removed)
        assert result.updated == sorted(result.updated)


# =============================================================================
# StateSynchronizer – reconcile
# =============================================================================


class TestReconcile:
    """Tests for the reconcile method."""

    def test_file_only_agents_included(self, syncer):
        """Agents only in file state appear in merged output."""
        file_state = {"a1": _make_agent("a1")}
        merged = syncer.reconcile(file_state, {})
        assert "a1" in merged

    def test_store_only_agents_included(self, syncer):
        """Agents only in store state appear in merged output."""
        store_state = {"a2": _make_agent("a2")}
        merged = syncer.reconcile({}, store_state)
        assert "a2" in merged

    def test_store_wins_when_newer(self, syncer):
        """Store record wins when its heartbeat is newer."""
        old_hb = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        new_hb = datetime.now(UTC).isoformat()

        file_agent = _make_agent("a1", status="idle")
        file_agent["last_heartbeat"] = old_hb

        store_agent = _make_agent("a1", status="active")
        store_agent["last_heartbeat"] = new_hb

        merged = syncer.reconcile({"a1": file_agent}, {"a1": store_agent})

        assert merged["a1"]["status"] == "active"

    def test_file_wins_when_newer(self, syncer):
        """File record wins when its heartbeat is newer."""
        new_hb = datetime.now(UTC).isoformat()
        old_hb = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()

        file_agent = _make_agent("a1", status="completed")
        file_agent["last_heartbeat"] = new_hb

        store_agent = _make_agent("a1", status="active")
        store_agent["last_heartbeat"] = old_hb

        merged = syncer.reconcile({"a1": file_agent}, {"a1": store_agent})

        assert merged["a1"]["status"] == "completed"

    def test_conflict_flagged_when_same_timestamp_different_content(self, syncer):
        """Agents with equal timestamps but different content are flagged."""
        ts = datetime.now(UTC).isoformat()

        file_agent = _make_agent("a1", status="idle")
        file_agent["last_heartbeat"] = ts

        store_agent = _make_agent("a1", status="active")
        store_agent["last_heartbeat"] = ts

        merged = syncer.reconcile({"a1": file_agent}, {"a1": store_agent})

        assert merged["a1"].get("_conflict") is True

    def test_no_conflict_when_same_timestamp_same_content(self, syncer):
        """Equal timestamps with identical content do not produce a conflict."""
        ts = datetime.now(UTC).isoformat()
        agent = _make_agent("a1")
        agent["last_heartbeat"] = ts

        merged = syncer.reconcile({"a1": dict(agent)}, {"a1": dict(agent)})

        assert merged["a1"].get("_conflict") is not True

    def test_store_preferred_when_no_timestamps(self, syncer):
        """Store side is preferred when neither record has any timestamp."""
        file_agent = {"id": "a1", "session_id": "a1", "status": "idle"}
        store_agent = {"id": "a1", "session_id": "a1", "status": "active"}

        merged = syncer.reconcile({"a1": file_agent}, {"a1": store_agent})

        assert merged["a1"]["status"] == "active"

    def test_store_preferred_when_only_file_has_no_timestamp(self, syncer):
        """Store wins when only file has no timestamp."""
        ts = datetime.now(UTC).isoformat()
        file_agent = {"id": "a1", "session_id": "a1", "status": "old"}
        store_agent = _make_agent("a1", status="new")
        store_agent["last_heartbeat"] = ts

        merged = syncer.reconcile({"a1": file_agent}, {"a1": store_agent})

        assert merged["a1"]["status"] == "new"

    def test_file_preferred_when_only_store_has_no_timestamp(self, syncer):
        """File wins when only store has no timestamp."""
        ts = datetime.now(UTC).isoformat()
        file_agent = _make_agent("a1", status="file-status")
        file_agent["last_heartbeat"] = ts
        store_agent = {"id": "a1", "session_id": "a1", "status": "store-status"}

        merged = syncer.reconcile({"a1": file_agent}, {"a1": store_agent})

        assert merged["a1"]["status"] == "file-status"

    def test_all_agents_present_in_merged(self, syncer):
        """All agents from both sides appear in the merged output."""
        file_state = {"a": _make_agent("a"), "b": _make_agent("b")}
        store_state = {"b": _make_agent("b"), "c": _make_agent("c")}

        merged = syncer.reconcile(file_state, store_state)

        assert "a" in merged
        assert "b" in merged
        assert "c" in merged

    def test_empty_states_yield_empty_merge(self, syncer):
        """reconcile of two empty dicts returns an empty dict."""
        assert syncer.reconcile({}, {}) == {}


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Verify the synchronizer is safe when called from multiple threads."""

    def test_concurrent_sync_agents_to_files(self, syncer):
        """Concurrent writes from multiple threads don't raise exceptions."""
        errors: list[Exception] = []
        threads = []

        def write_agents(thread_id: int) -> None:
            try:
                agents = [_make_agent(f"thread{thread_id}-agent{j}") for j in range(5)]
                syncer.sync_agents_to_files(agents)
            except Exception as exc:
                errors.append(exc)

        for i in range(4):
            t = threading.Thread(target=write_agents, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_sync_files_to_store(self, syncer, sessions_dir):
        """Concurrent reads don't raise exceptions."""
        for i in range(10):
            (sessions_dir / f"session_agent-{i:03d}.json").write_text(
                json.dumps(_make_agent(f"agent-{i:03d}"))
            )

        errors: list[Exception] = []
        threads = []

        def read_agents() -> None:
            try:
                syncer.sync_files_to_store()
            except Exception as exc:
                errors.append(exc)

        for _ in range(4):
            t = threading.Thread(target=read_agents)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_sync_agents_to_files_with_extra_fields(self, syncer, sessions_dir):
        """Extra fields in agent dict are preserved in the written file."""
        agent = _make_agent("a1")
        agent["custom_field"] = "custom_value"
        syncer.sync_agents_to_files([agent])

        written = json.loads((sessions_dir / "session_a1.json").read_text())
        assert written["custom_field"] == "custom_value"

    def test_sync_files_to_store_with_corrupt_and_valid(self, syncer, sessions_dir):
        """Mix of corrupt and valid files — only valid ones returned."""
        (sessions_dir / "session_valid.json").write_text(json.dumps(_make_agent("valid")))
        (sessions_dir / "session_corrupt1.json").write_text("not json at all")
        (sessions_dir / "session_corrupt2.json").write_text("{broken}")

        result = syncer.sync_files_to_store()

        assert len(result) == 1
        assert "valid" in result

    def test_diff_with_only_updated(self, syncer):
        """Diff of same IDs with all different values returns all as updated."""
        file_state = {f"agent-{i}": _make_agent(f"agent-{i}", status="active") for i in range(3)}
        store_state = {f"agent-{i}": _make_agent(f"agent-{i}", status="idle") for i in range(3)}

        result = syncer.diff(file_state, store_state)

        assert len(result.updated) == 3
        assert result.added == []
        assert result.removed == []

    def test_reconcile_prefers_store_for_no_timestamp_conflict(self, syncer):
        """No-timestamp conflict always resolved by choosing store side."""
        file_agent = {"id": "x", "session_id": "x", "status": "file"}
        store_agent = {"id": "x", "session_id": "x", "status": "store"}

        merged = syncer.reconcile({"x": file_agent}, {"x": store_agent})

        assert merged["x"]["status"] == "store"

    def test_sync_result_timestamp_is_utc_aware(self):
        """SyncResult timestamp is timezone-aware UTC."""
        result = SyncResult()
        assert result.timestamp.tzinfo is not None

    def test_initialise_without_sessions_dir(self, tmp_path, monkeypatch):
        """Default sessions_dir is .forge/sessions in cwd."""
        monkeypatch.chdir(tmp_path)
        syncer = StateSynchronizer()
        assert syncer.sessions_dir == Path(".forge/sessions")

    def test_sessions_dir_created_on_demand(self, tmp_path):
        """Directory is created only when needed (not at __init__ time)."""
        missing = tmp_path / "will_be_created"
        syncer = StateSynchronizer(sessions_dir=missing)

        assert not missing.exists()  # Not created at init

        syncer.sync_agents_to_files([_make_agent("a1")])

        assert missing.exists()  # Created on demand

    def test_sync_agents_to_files_skips_corrupt_existing_scan(self, syncer, sessions_dir):
        """Corrupt existing session files during the initial scan are silently ignored."""
        # Write a corrupt file that will be encountered during the initial ID scan
        (sessions_dir / "session_corrupt_scan.json").write_text("{not valid")

        # Should still succeed even though the existing-ID scan hits a bad file
        agent = _make_agent("new-agent")
        result = syncer.sync_agents_to_files([agent])

        assert result.success is True
        assert "new-agent" in result.added

    def test_sync_agents_to_files_corrupt_existing_file_on_read(self, syncer, sessions_dir):
        """Corrupt session file during registered_at read is silently skipped."""
        # Create a corrupt session file with the same ID as the agent being written
        (sessions_dir / "session_agent-001.json").write_text("{bad json")

        # Even though the existing file is corrupt, the write should succeed
        agent = _make_agent("agent-001")
        del agent["registered_at"]  # Force the code path that reads from existing
        result = syncer.sync_agents_to_files([agent])

        assert result.success is True

    def test_sync_files_to_store_handles_os_error(self, syncer, sessions_dir):
        """OS error during session file read is silently skipped."""
        (sessions_dir / "session_good.json").write_text(json.dumps(_make_agent("good")))
        (sessions_dir / "session_bad.json").write_text(json.dumps(_make_agent("bad")))

        with patch.object(
            Path,
            "read_text",
            side_effect=[
                json.dumps(_make_agent("good")),
                OSError("permission denied"),
            ],
        ):
            result = syncer.sync_files_to_store()

        # At least the operation should succeed without crashing
        assert isinstance(result, dict)

    def test_parse_timestamp_with_naive_datetime(self, syncer):
        """_parse_timestamp promotes naive datetimes to UTC-aware."""
        from forge_harness.state_sync import StateSynchronizer as StateSynchronizerAlias

        # Use a naive ISO string (no +00:00)
        naive_ts = "2026-01-15T10:30:00"  # no timezone
        agent = {"last_heartbeat": naive_ts}
        ts = StateSynchronizerAlias._parse_timestamp(agent)
        assert ts is not None
        assert ts.tzinfo is not None  # should be UTC-aware

    def test_parse_timestamp_with_invalid_value(self, syncer):
        """_parse_timestamp returns None when all timestamp fields are invalid."""
        from forge_harness.state_sync import StateSynchronizer as StateSynchronizerAlias

        agent = {"last_heartbeat": "not-a-date", "last_activity": 12345, "registered_at": "bad"}
        ts = StateSynchronizerAlias._parse_timestamp(agent)
        assert ts is None
