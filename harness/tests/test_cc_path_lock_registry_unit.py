"""
Unit tests for forge_harness.webhook_server.services.path_lock_registry

Covers:
- LockConflictError (init, attributes, message)
- PathLockRegistry.__init__ (with and without persistence file)
- PathLockRegistry.acquire (exclusive, shared, re-entrant, conflict, TTL)
- PathLockRegistry.release (success, not found)
- PathLockRegistry.release_by_agent (all, partial, none)
- PathLockRegistry.check_conflicts (public API)
- PathLockRegistry.get_locks_for_agent
- PathLockRegistry.get_all_locks
- PathLockRegistry.cleanup_expired (public wrapper)
- PathLockRegistry._load_locks (via temp JSONL file)
- PathLockRegistry._write_jsonl (I/O error resilience)
- get_path_lock_registry / reset_path_lock_registry singleton helpers
"""

from __future__ import annotations

import json
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.webhook_server.models.path_lock import (
    LockConflict,
    PathLock,
    PathLockType,
)
from forge_harness.webhook_server.services.path_lock_registry import (
    LockConflictError,
    PathLockRegistry,
    get_path_lock_registry,
    reset_path_lock_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_registry(tmp_path: Path) -> PathLockRegistry:
    """Return a fresh PathLockRegistry backed by a temp directory (no I/O side-effects)."""
    locks_file = tmp_path / ".forge" / "locks" / "path_locks.jsonl"
    return PathLockRegistry(locks_path=locks_file)


# ---------------------------------------------------------------------------
# LockConflictError
# ---------------------------------------------------------------------------


class TestLockConflictError:
    """Tests for the LockConflictError custom exception."""

    def _make_conflict(self, agent_id: str = "forge:beta", reason: str = "exclusive lock") -> LockConflict:
        return LockConflict(
            lock_id="lock-001",
            agent_id=agent_id,
            path="/src/api.py",
            conflict_reason=reason,
        )

    def test_error_is_exception_subclass(self):
        err = LockConflictError("/src/api.py", [])
        assert isinstance(err, Exception)

    def test_error_stores_path(self):
        err = LockConflictError("/some/path.py", [])
        assert err.path == "/some/path.py"

    def test_error_stores_conflicts(self):
        conflict = self._make_conflict()
        err = LockConflictError("/src/api.py", [conflict])
        assert err.conflicts == [conflict]

    def test_error_message_contains_path(self):
        err = LockConflictError("/src/api.py", [])
        assert "/src/api.py" in str(err)

    def test_error_message_contains_agent_id(self):
        conflict = self._make_conflict(agent_id="forge:nova")
        err = LockConflictError("/src/api.py", [conflict])
        assert "forge:nova" in str(err)

    def test_error_empty_conflicts_list(self):
        err = LockConflictError("/src/api.py", [])
        assert err.conflicts == []

    def test_error_multiple_conflicts(self):
        c1 = self._make_conflict(agent_id="forge:alpha", reason="exclusive lock held by forge:alpha (lock_id=x)")
        c2 = self._make_conflict(agent_id="forge:beta", reason="exclusive lock held by forge:beta (lock_id=y)")
        err = LockConflictError("/src/api.py", [c1, c2])
        assert len(err.conflicts) == 2


# ---------------------------------------------------------------------------
# PathLockRegistry — construction
# ---------------------------------------------------------------------------


class TestPathLockRegistryInit:
    """Tests for PathLockRegistry.__init__."""

    def test_init_creates_empty_registry(self, tmp_registry: PathLockRegistry):
        assert tmp_registry.get_all_locks() == []

    def test_init_without_existing_file_is_ok(self, tmp_path: Path):
        locks_file = tmp_path / "nonexistent" / "path_locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_file)
        assert registry.get_all_locks() == []

    def test_init_accepts_string_path(self, tmp_path: Path):
        locks_file = str(tmp_path / "path_locks.jsonl")
        registry = PathLockRegistry(locks_path=locks_file)
        assert registry.get_all_locks() == []


# ---------------------------------------------------------------------------
# PathLockRegistry.acquire — exclusive lock
# ---------------------------------------------------------------------------


class TestAcquireExclusive:
    """Tests for exclusive lock acquisition."""

    def test_acquire_exclusive_returns_path_lock(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        assert isinstance(lock, PathLock)

    def test_acquire_exclusive_stores_lock(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        stored = tmp_registry.get_all_locks()
        assert any(l.lock_id == lock.lock_id for l in stored)

    def test_acquire_exclusive_correct_agent(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        assert lock.agent_id == "forge:alpha"

    def test_acquire_exclusive_correct_path(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        assert lock.path == "/src/api.py"

    def test_acquire_exclusive_correct_type(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        assert lock.lock_type == PathLockType.exclusive

    def test_acquire_exclusive_no_ttl_means_no_expiry(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        assert lock.expires_at is None

    def test_acquire_exclusive_with_ttl_sets_expiry(self, tmp_registry: PathLockRegistry):
        before = datetime.now(UTC)
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py", ttl_seconds=300)
        after = datetime.now(UTC)

        assert lock.expires_at is not None
        assert before + timedelta(seconds=299) <= lock.expires_at <= after + timedelta(seconds=301)

    def test_acquire_exclusive_with_task_id(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py", task_id="task-99")
        assert lock.task_id == "task-99"

    def test_acquire_exclusive_blocked_by_existing_exclusive(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py")
        with pytest.raises(LockConflictError) as exc_info:
            tmp_registry.acquire("forge:beta", "/src/api.py")
        assert exc_info.value.path == "/src/api.py"

    def test_acquire_exclusive_blocked_by_existing_shared(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py", lock_type=PathLockType.shared)
        with pytest.raises(LockConflictError):
            tmp_registry.acquire("forge:beta", "/src/api.py", lock_type=PathLockType.exclusive)

    def test_acquire_exclusive_different_path_does_not_conflict(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py")
        # Should NOT raise — different path
        lock2 = tmp_registry.acquire("forge:beta", "/src/other.py")
        assert lock2.path == "/src/other.py"

    def test_acquire_exclusive_reentrant_same_agent(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py")
        # Same agent can re-acquire
        lock2 = tmp_registry.acquire("forge:alpha", "/src/api.py")
        assert lock2.agent_id == "forge:alpha"


# ---------------------------------------------------------------------------
# PathLockRegistry.acquire — shared lock
# ---------------------------------------------------------------------------


class TestAcquireShared:
    """Tests for shared lock acquisition."""

    def test_shared_lock_allowed_alongside_another_shared(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py", lock_type=PathLockType.shared)
        lock2 = tmp_registry.acquire("forge:beta", "/src/api.py", lock_type=PathLockType.shared)
        assert lock2.lock_type == PathLockType.shared

    def test_shared_lock_blocked_by_exclusive_from_other_agent(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py", lock_type=PathLockType.exclusive)
        with pytest.raises(LockConflictError) as exc_info:
            tmp_registry.acquire("forge:beta", "/src/api.py", lock_type=PathLockType.shared)
        assert len(exc_info.value.conflicts) >= 1

    def test_shared_lock_conflict_list_contains_exclusive_only(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py", lock_type=PathLockType.exclusive)
        with pytest.raises(LockConflictError) as exc_info:
            tmp_registry.acquire("forge:beta", "/src/api.py", lock_type=PathLockType.shared)
        for conflict in exc_info.value.conflicts:
            assert "exclusive" in conflict.conflict_reason


# ---------------------------------------------------------------------------
# PathLockRegistry.release
# ---------------------------------------------------------------------------


class TestRelease:
    """Tests for PathLockRegistry.release()."""

    def test_release_existing_lock_returns_true(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        assert tmp_registry.release(lock.lock_id) is True

    def test_release_removes_lock_from_registry(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        tmp_registry.release(lock.lock_id)
        assert tmp_registry.get_all_locks() == []

    def test_release_nonexistent_lock_returns_false(self, tmp_registry: PathLockRegistry):
        assert tmp_registry.release("nonexistent-lock-id") is False

    def test_release_allows_subsequent_acquisition(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        tmp_registry.release(lock.lock_id)
        # Now another agent can acquire
        lock2 = tmp_registry.acquire("forge:beta", "/src/api.py")
        assert lock2.agent_id == "forge:beta"


# ---------------------------------------------------------------------------
# PathLockRegistry.release_by_agent
# ---------------------------------------------------------------------------


class TestReleaseByAgent:
    """Tests for PathLockRegistry.release_by_agent()."""

    def test_release_by_agent_returns_count(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a")
        tmp_registry.acquire("forge:alpha", "/b")
        count = tmp_registry.release_by_agent("forge:alpha")
        assert count == 2

    def test_release_by_agent_clears_all_agent_locks(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a")
        tmp_registry.acquire("forge:alpha", "/b")
        tmp_registry.release_by_agent("forge:alpha")
        remaining = tmp_registry.get_locks_for_agent("forge:alpha")
        assert remaining == []

    def test_release_by_agent_does_not_affect_other_agents(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a")
        tmp_registry.acquire("forge:beta", "/b")
        tmp_registry.release_by_agent("forge:alpha")
        assert len(tmp_registry.get_locks_for_agent("forge:beta")) == 1

    def test_release_by_agent_returns_zero_for_unknown_agent(self, tmp_registry: PathLockRegistry):
        count = tmp_registry.release_by_agent("forge:nobody")
        assert count == 0


# ---------------------------------------------------------------------------
# PathLockRegistry.check_conflicts
# ---------------------------------------------------------------------------


class TestCheckConflicts:
    """Tests for PathLockRegistry.check_conflicts() public API."""

    def test_no_conflicts_on_empty_registry(self, tmp_registry: PathLockRegistry):
        conflicts = tmp_registry.check_conflicts("/src/api.py")
        assert conflicts == []

    def test_conflict_detected_for_exclusive_lock(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py")
        conflicts = tmp_registry.check_conflicts("/src/api.py")
        assert len(conflicts) == 1
        assert conflicts[0].agent_id == "forge:alpha"

    def test_exclude_agent_id_hides_own_locks(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py")
        conflicts = tmp_registry.check_conflicts("/src/api.py", exclude_agent_id="forge:alpha")
        assert conflicts == []

    def test_conflict_contains_correct_lock_id(self, tmp_registry: PathLockRegistry):
        lock = tmp_registry.acquire("forge:alpha", "/src/api.py")
        conflicts = tmp_registry.check_conflicts("/src/api.py")
        assert conflicts[0].lock_id == lock.lock_id

    def test_shared_lock_appears_in_conflicts(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py", lock_type=PathLockType.shared)
        conflicts = tmp_registry.check_conflicts("/src/api.py")
        assert len(conflicts) == 1
        assert "shared" in conflicts[0].conflict_reason

    def test_expired_locks_not_included_in_conflicts(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/src/api.py", ttl_seconds=0)
        time.sleep(0.01)
        conflicts = tmp_registry.check_conflicts("/src/api.py")
        assert conflicts == []


# ---------------------------------------------------------------------------
# PathLockRegistry.get_locks_for_agent
# ---------------------------------------------------------------------------


class TestGetLocksForAgent:
    """Tests for PathLockRegistry.get_locks_for_agent()."""

    def test_returns_empty_for_unknown_agent(self, tmp_registry: PathLockRegistry):
        assert tmp_registry.get_locks_for_agent("forge:nobody") == []

    def test_returns_locks_for_agent(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a")
        tmp_registry.acquire("forge:alpha", "/b")
        locks = tmp_registry.get_locks_for_agent("forge:alpha")
        assert len(locks) == 2
        assert all(l.agent_id == "forge:alpha" for l in locks)

    def test_does_not_return_other_agent_locks(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a")
        tmp_registry.acquire("forge:beta", "/b")
        locks = tmp_registry.get_locks_for_agent("forge:alpha")
        assert all(l.agent_id == "forge:alpha" for l in locks)

    def test_expired_locks_excluded(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a", ttl_seconds=0)
        time.sleep(0.01)
        locks = tmp_registry.get_locks_for_agent("forge:alpha")
        assert locks == []


# ---------------------------------------------------------------------------
# PathLockRegistry.get_all_locks
# ---------------------------------------------------------------------------


class TestGetAllLocks:
    """Tests for PathLockRegistry.get_all_locks()."""

    def test_empty_on_fresh_registry(self, tmp_registry: PathLockRegistry):
        assert tmp_registry.get_all_locks() == []

    def test_returns_all_held_locks(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a")
        tmp_registry.acquire("forge:beta", "/b")
        locks = tmp_registry.get_all_locks()
        assert len(locks) == 2

    def test_expired_locks_not_returned(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a", ttl_seconds=0)
        tmp_registry.acquire("forge:beta", "/b")
        time.sleep(0.01)
        locks = tmp_registry.get_all_locks()
        assert len(locks) == 1
        assert locks[0].agent_id == "forge:beta"


# ---------------------------------------------------------------------------
# PathLockRegistry.cleanup_expired
# ---------------------------------------------------------------------------


class TestCleanupExpired:
    """Tests for PathLockRegistry.cleanup_expired() public wrapper."""

    def test_cleanup_removes_expired_locks(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a", ttl_seconds=0)
        time.sleep(0.01)
        removed = tmp_registry.cleanup_expired()
        assert removed >= 1
        assert tmp_registry.get_all_locks() == []

    def test_cleanup_returns_zero_when_nothing_expired(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a", ttl_seconds=3600)
        removed = tmp_registry.cleanup_expired()
        assert removed == 0

    def test_cleanup_does_not_remove_no_ttl_locks(self, tmp_registry: PathLockRegistry):
        tmp_registry.acquire("forge:alpha", "/a")  # No TTL
        removed = tmp_registry.cleanup_expired()
        assert removed == 0
        assert len(tmp_registry.get_all_locks()) == 1


# ---------------------------------------------------------------------------
# PathLockRegistry — persistence (_load_locks)
# ---------------------------------------------------------------------------


class TestPersistence:
    """Tests for the JSONL persistence layer."""

    def _write_jsonl(self, path: Path, records: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")

    def test_load_replays_active_locks(self, tmp_path: Path):
        locks_file = tmp_path / "path_locks.jsonl"
        # Create a lock record manually
        lock = PathLock(agent_id="forge:alpha", path="/src/api.py")
        acquire_record = {
            "type": "lock",
            "lock_id": lock.lock_id,
            "lock": lock.model_dump(mode="json"),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self._write_jsonl(locks_file, [acquire_record])

        registry = PathLockRegistry(locks_path=locks_file)
        all_locks = registry.get_all_locks()
        assert any(l.lock_id == lock.lock_id for l in all_locks)

    def test_load_skips_released_locks(self, tmp_path: Path):
        locks_file = tmp_path / "path_locks.jsonl"
        lock = PathLock(agent_id="forge:alpha", path="/src/api.py")
        acquire_record = {
            "type": "lock",
            "lock_id": lock.lock_id,
            "lock": lock.model_dump(mode="json"),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        release_record = {
            "type": "release",
            "lock_id": lock.lock_id,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self._write_jsonl(locks_file, [acquire_record, release_record])

        registry = PathLockRegistry(locks_path=locks_file)
        assert registry.get_all_locks() == []

    def test_load_skips_expired_locks(self, tmp_path: Path):
        locks_file = tmp_path / "path_locks.jsonl"
        past = datetime.now(UTC) - timedelta(seconds=1)
        lock = PathLock(
            agent_id="forge:alpha",
            path="/src/api.py",
            expires_at=past,
        )
        acquire_record = {
            "type": "lock",
            "lock_id": lock.lock_id,
            "lock": lock.model_dump(mode="json"),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self._write_jsonl(locks_file, [acquire_record])

        registry = PathLockRegistry(locks_path=locks_file)
        assert registry.get_all_locks() == []

    def test_load_skips_malformed_lines(self, tmp_path: Path):
        locks_file = tmp_path / "path_locks.jsonl"
        locks_file.parent.mkdir(parents=True, exist_ok=True)
        locks_file.write_text("not valid json\n{}\n", encoding="utf-8")

        # Should not raise
        registry = PathLockRegistry(locks_path=locks_file)
        assert registry.get_all_locks() == []

    def test_acquire_persists_lock_to_file(self, tmp_path: Path):
        locks_file = tmp_path / "path_locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_file)
        lock = registry.acquire("forge:alpha", "/src/api.py")

        assert locks_file.exists()
        content = locks_file.read_text(encoding="utf-8")
        assert lock.lock_id in content

    def test_release_persists_release_record(self, tmp_path: Path):
        locks_file = tmp_path / "path_locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_file)
        lock = registry.acquire("forge:alpha", "/src/api.py")
        registry.release(lock.lock_id)

        content = locks_file.read_text(encoding="utf-8")
        lines = [json.loads(l) for l in content.strip().splitlines()]
        release_records = [l for l in lines if l.get("type") == "release"]
        assert any(r["lock_id"] == lock.lock_id for r in release_records)

    def test_write_jsonl_survives_os_error(self, tmp_path: Path):
        """_write_jsonl should not raise on I/O error — it just logs."""
        locks_file = tmp_path / "path_locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_file)

        # Patch pathlib.Path.open at the class level so it raises OSError
        with patch("pathlib.Path.open", side_effect=OSError("disk full")):
            # Should NOT raise — _write_jsonl catches OSError and logs it
            registry._write_jsonl({"type": "test"})


# ---------------------------------------------------------------------------
# Singleton factory helpers
# ---------------------------------------------------------------------------


class TestSingletonFactory:
    """Tests for get_path_lock_registry / reset_path_lock_registry."""

    def test_get_returns_instance(self, tmp_path: Path):
        reset_path_lock_registry()
        with patch(
            "forge_harness.webhook_server.services.path_lock_registry.PathLockRegistry",
        ) as MockCls:
            MockCls.return_value = MagicMock(spec=PathLockRegistry)
            instance = get_path_lock_registry()
            assert instance is not None
        reset_path_lock_registry()

    def test_get_returns_same_instance_on_repeated_calls(self):
        reset_path_lock_registry()
        try:
            a = get_path_lock_registry()
            b = get_path_lock_registry()
            assert a is b
        finally:
            reset_path_lock_registry()

    def test_reset_allows_fresh_instance(self, tmp_path: Path):
        reset_path_lock_registry()
        first = get_path_lock_registry()
        reset_path_lock_registry()
        second = get_path_lock_registry()
        # After reset, a new instance is created
        assert first is not second
        reset_path_lock_registry()

    def test_reset_sets_global_to_none(self):
        import forge_harness.webhook_server.services.path_lock_registry as mod

        get_path_lock_registry()  # Ensure it is set
        reset_path_lock_registry()
        assert mod._registry is None
