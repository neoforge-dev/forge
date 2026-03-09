"""Comprehensive tests for PathLockRegistry (DF-3002).

Tests for forge_harness/webhook_server/services/path_lock_registry.py

Coverage areas:
  - Initialization (empty state, custom locks_path)
  - Lock acquisition (exclusive, shared, same-agent relock)
  - Lock release (known, unknown, by agent)
  - Conflict detection (exclusive vs exclusive, exclusive vs shared, shared vs shared)
  - Expired lock cleanup
  - Active lock queries (by agent, all)
  - Singleton pattern
  - Thread safety
  - JSONL persistence
  - Edge cases
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def registry(tmp_path: Path) -> PathLockRegistry:
    """Fresh PathLockRegistry with temp path for each test."""
    locks_path = tmp_path / "locks.jsonl"
    return PathLockRegistry(locks_path=locks_path)


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """Reset the global singleton before and after every test."""
    reset_path_lock_registry()
    yield
    reset_path_lock_registry()


# ============================================================================
# Initialization Tests
# ============================================================================


class TestPathLockRegistryInit:
    """Tests for PathLockRegistry initialization."""

    def test_init_with_empty_state(self, tmp_path: Path) -> None:
        """Registry starts with empty state when no persistence file exists."""
        locks_path = tmp_path / "locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_path)

        assert registry.get_all_locks() == []
        assert registry.get_locks_for_agent("any-agent") == []

    def test_init_with_custom_locks_path(self, tmp_path: Path) -> None:
        """Registry can be initialized with a custom locks path."""
        custom_path = tmp_path / "custom" / "path" / "locks.jsonl"
        registry = PathLockRegistry(locks_path=custom_path)

        # Acquire a lock to trigger persistence
        registry.acquire("agent-1", "/test/path")

        # Persistence file should be created at custom path
        assert custom_path.exists()

    def test_init_loads_existing_locks(self, tmp_path: Path) -> None:
        """Registry loads existing locks from persistence file on init."""
        locks_path = tmp_path / "locks.jsonl"

        # Create first registry and acquire lock
        registry1 = PathLockRegistry(locks_path=locks_path)
        lock = registry1.acquire("agent-1", "/test/path")

        # Create second registry (simulating restart) - should load the lock
        registry2 = PathLockRegistry(locks_path=locks_path)
        loaded_locks = registry2.get_all_locks()

        assert len(loaded_locks) == 1
        assert loaded_locks[0].lock_id == lock.lock_id
        assert loaded_locks[0].agent_id == "agent-1"
        assert loaded_locks[0].path == "/test/path"

    def test_init_skips_expired_locks_on_load(self, tmp_path: Path) -> None:
        """Registry skips expired locks when loading from persistence."""
        locks_path = tmp_path / "locks.jsonl"

        # Create registry with locks
        registry1 = PathLockRegistry(locks_path=locks_path)

        # Acquire lock that expires immediately
        registry1.acquire("agent-1", "/expired/path", ttl_seconds=0.001)
        time.sleep(0.01)  # Wait for expiry

        # Acquire lock that doesn't expire
        registry1.acquire("agent-2", "/active/path")

        # Create new registry - should only load active lock
        registry2 = PathLockRegistry(locks_path=locks_path)
        loaded_locks = registry2.get_all_locks()

        assert len(loaded_locks) == 1
        assert loaded_locks[0].path == "/active/path"


# ============================================================================
# Lock Acquisition Tests
# ============================================================================


class TestLockAcquisition:
    """Tests for lock acquisition."""

    def test_acquire_exclusive_lock(self, registry: PathLockRegistry) -> None:
        """Can acquire an exclusive lock."""
        lock = registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)

        assert lock.agent_id == "agent-1"
        assert lock.path == "/test/path"
        assert lock.lock_type == PathLockType.exclusive
        assert lock.lock_id is not None

    def test_acquire_shared_lock(self, registry: PathLockRegistry) -> None:
        """Can acquire a shared lock."""
        lock = registry.acquire("agent-1", "/test/path", lock_type=PathLockType.shared)

        assert lock.lock_type == PathLockType.shared

    def test_acquire_with_task_id(self, registry: PathLockRegistry) -> None:
        """Can acquire a lock with associated task ID."""
        lock = registry.acquire(
            "agent-1", "/test/path", task_id="task-123", lock_type=PathLockType.exclusive
        )

        assert lock.task_id == "task-123"

    def test_acquire_with_ttl(self, registry: PathLockRegistry) -> None:
        """Can acquire a lock with TTL."""
        lock = registry.acquire(
            "agent-1", "/test/path", ttl_seconds=300, lock_type=PathLockType.exclusive
        )

        assert lock.expires_at is not None
        # Should expire roughly 5 minutes from now
        expected_expiry = datetime.now(UTC) + timedelta(seconds=300)
        assert abs((lock.expires_at - expected_expiry).total_seconds()) < 1

    def test_same_agent_can_relock(self, registry: PathLockRegistry) -> None:
        """Same agent can acquire multiple locks on same path."""
        # First lock
        lock1 = registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)

        # Same agent can acquire another lock on same path
        lock2 = registry.acquire("agent-1", "/test/path", lock_type=PathLockType.shared)

        assert lock1.lock_id != lock2.lock_id
        assert len(registry.get_all_locks()) == 2

    def test_default_lock_type_is_exclusive(self, registry: PathLockRegistry) -> None:
        """Default lock type is exclusive."""
        lock = registry.acquire("agent-1", "/test/path")

        assert lock.lock_type == PathLockType.exclusive


# ============================================================================
# Lock Release Tests
# ============================================================================


class TestLockRelease:
    """Tests for lock release."""

    def test_release_known_lock(self, registry: PathLockRegistry) -> None:
        """Can release a known lock."""
        lock = registry.acquire("agent-1", "/test/path")

        result = registry.release(lock.lock_id)

        assert result is True
        assert len(registry.get_all_locks()) == 0

    def test_release_unknown_lock(self, registry: PathLockRegistry) -> None:
        """Releasing unknown lock returns False."""
        result = registry.release("non-existent-lock-id")

        assert result is False

    def test_release_by_agent(self, registry: PathLockRegistry) -> None:
        """Can release all locks by agent."""
        # Acquire multiple locks for same agent
        registry.acquire("agent-1", "/path/1")
        registry.acquire("agent-1", "/path/2")
        registry.acquire("agent-1", "/path/3")

        # Acquire lock for different agent
        registry.acquire("agent-2", "/path/4")

        # Release all locks for agent-1
        released = registry.release_by_agent("agent-1")

        assert released == 3
        assert len(registry.get_all_locks()) == 1
        assert registry.get_all_locks()[0].agent_id == "agent-2"

    def test_release_by_agent_no_locks(self, registry: PathLockRegistry) -> None:
        """Release by agent returns 0 when agent has no locks."""
        released = registry.release_by_agent("agent-with-no-locks")

        assert released == 0


# ============================================================================
# Conflict Detection Tests
# ============================================================================


class TestConflictDetection:
    """Tests for lock conflict detection."""

    def test_exclusive_vs_exclusive_conflict(self, registry: PathLockRegistry) -> None:
        """Exclusive lock conflicts with another exclusive lock."""
        registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)

        with pytest.raises(LockConflictError) as exc_info:
            registry.acquire("agent-2", "/test/path", lock_type=PathLockType.exclusive)

        assert "agent-1" in str(exc_info.value)
        assert "/test/path" in str(exc_info.value)

    def test_exclusive_vs_shared_conflict(self, registry: PathLockRegistry) -> None:
        """Exclusive lock conflicts with shared lock from different agent."""
        registry.acquire("agent-1", "/test/path", lock_type=PathLockType.shared)

        with pytest.raises(LockConflictError) as exc_info:
            registry.acquire("agent-2", "/test/path", lock_type=PathLockType.exclusive)

        assert "agent-1" in str(exc_info.value)

    def test_shared_vs_exclusive_conflict(self, registry: PathLockRegistry) -> None:
        """Shared lock conflicts with exclusive lock from different agent."""
        registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)

        with pytest.raises(LockConflictError) as exc_info:
            registry.acquire("agent-2", "/test/path", lock_type=PathLockType.shared)

        assert "agent-1" in str(exc_info.value)

    def test_shared_vs_shared_no_conflict(self, registry: PathLockRegistry) -> None:
        """Shared locks don't conflict with each other."""
        registry.acquire("agent-1", "/test/path", lock_type=PathLockType.shared)

        # Should not raise
        lock2 = registry.acquire("agent-2", "/test/path", lock_type=PathLockType.shared)

        assert lock2.agent_id == "agent-2"
        assert len(registry.get_all_locks()) == 2

    def test_no_conflict_on_different_paths(self, registry: PathLockRegistry) -> None:
        """Locks on different paths don't conflict."""
        registry.acquire("agent-1", "/path/1", lock_type=PathLockType.exclusive)

        # Should not raise - different path
        lock2 = registry.acquire("agent-1", "/path/2", lock_type=PathLockType.exclusive)

        assert lock2.path == "/path/2"

    def test_check_conflicts_returns_conflicts(self, registry: PathLockRegistry) -> None:
        """check_conflicts returns list of conflicts."""
        lock = registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)

        conflicts = registry.check_conflicts("/test/path")

        assert len(conflicts) == 1
        assert conflicts[0].agent_id == "agent-1"
        assert conflicts[0].lock_id == lock.lock_id

    def test_check_conflicts_excludes_agent(self, registry: PathLockRegistry) -> None:
        """check_conflicts can exclude specific agent."""
        registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)

        # Excluding agent-1 should return no conflicts
        conflicts = registry.check_conflicts("/test/path", exclude_agent_id="agent-1")

        assert len(conflicts) == 0

    def test_check_conflicts_no_conflicts(self, registry: PathLockRegistry) -> None:
        """check_conflicts returns empty list when no conflicts."""
        conflicts = registry.check_conflicts("/non-locked/path")

        assert conflicts == []


# ============================================================================
# Expired Lock Cleanup Tests
# ============================================================================


class TestExpiredLockCleanup:
    """Tests for expired lock cleanup."""

    def test_cleanup_expired_removes_expired_locks(self, registry: PathLockRegistry) -> None:
        """cleanup_expired removes expired locks."""
        # Acquire lock with short TTL
        registry.acquire("agent-1", "/expired/path", ttl_seconds=0.001)
        time.sleep(0.01)  # Wait for expiry

        # Acquire lock without TTL (never expires) - this also cleans expired
        # So we need to check that only active lock remains
        registry.acquire("agent-2", "/active/path")

        # At this point, expired lock should already be cleaned by acquire
        # But let's verify the state
        all_locks = registry.get_all_locks()
        assert len(all_locks) == 1
        assert all_locks[0].path == "/active/path"

        # Now test cleanup_expired on another expired lock
        registry.acquire("agent-3", "/expired2/path", ttl_seconds=0.001)
        time.sleep(0.01)

        # Cleanup should remove the newly expired lock
        removed = registry.cleanup_expired()
        assert removed == 1

    def test_cleanup_expired_keeps_unexpired_locks(self, registry: PathLockRegistry) -> None:
        """cleanup_expired keeps locks that haven't expired."""
        # Acquire lock with long TTL
        registry.acquire("agent-1", "/path/1", ttl_seconds=3600)
        registry.acquire("agent-2", "/path/2")  # No TTL

        removed = registry.cleanup_expired()

        assert removed == 0
        assert len(registry.get_all_locks()) == 2

    def test_cleanup_expired_returns_count(self, registry: PathLockRegistry) -> None:
        """cleanup_expired returns number of removed locks."""
        # Acquire multiple expired locks
        registry.acquire("agent-1", "/path/1", ttl_seconds=0.001)
        registry.acquire("agent-2", "/path/2", ttl_seconds=0.001)
        time.sleep(0.01)

        removed = registry.cleanup_expired()

        assert removed == 2

    def test_acquire_auto_cleans_expired(self, registry: PathLockRegistry) -> None:
        """acquire automatically cleans expired locks before checking."""
        # Acquire expired lock
        registry.acquire("agent-1", "/expired/path", ttl_seconds=0.001)
        time.sleep(0.01)

        # New acquire should clean expired and succeed
        lock = registry.acquire("agent-2", "/expired/path")

        assert lock.agent_id == "agent-2"
        assert len(registry.get_all_locks()) == 1


# ============================================================================
# Active Lock Query Tests
# ============================================================================


class TestActiveLockQueries:
    """Tests for querying active locks."""

    def test_get_locks_for_agent(self, registry: PathLockRegistry) -> None:
        """Can get all locks for specific agent."""
        registry.acquire("agent-1", "/path/1")
        registry.acquire("agent-1", "/path/2")
        registry.acquire("agent-2", "/path/3")

        agent1_locks = registry.get_locks_for_agent("agent-1")

        assert len(agent1_locks) == 2
        assert all(lock.agent_id == "agent-1" for lock in agent1_locks)

    def test_get_locks_for_agent_no_locks(self, registry: PathLockRegistry) -> None:
        """get_locks_for_agent returns empty list for agent with no locks."""
        locks = registry.get_locks_for_agent("agent-with-no-locks")

        assert locks == []

    def test_get_all_locks(self, registry: PathLockRegistry) -> None:
        """Can get all active locks."""
        registry.acquire("agent-1", "/path/1")
        registry.acquire("agent-2", "/path/2")
        registry.acquire("agent-3", "/path/3")

        all_locks = registry.get_all_locks()

        assert len(all_locks) == 3

    def test_get_all_locks_empty(self, registry: PathLockRegistry) -> None:
        """get_all_locks returns empty list when no locks."""
        all_locks = registry.get_all_locks()

        assert all_locks == []


# ============================================================================
# Singleton Tests
# ============================================================================


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_path_lock_registry_returns_same_instance(self) -> None:
        """get_path_lock_registry returns the same instance."""
        reset_path_lock_registry()

        registry1 = get_path_lock_registry()
        registry2 = get_path_lock_registry()

        assert registry1 is registry2

    def test_reset_path_lock_registry_creates_new_instance(self) -> None:
        """reset_path_lock_registry allows creating new instance."""
        registry1 = get_path_lock_registry()
        reset_path_lock_registry()
        registry2 = get_path_lock_registry()

        assert registry1 is not registry2

    def test_singleton_is_thread_safe(self) -> None:
        """Singleton creation is thread-safe."""
        reset_path_lock_registry()
        registries: list[PathLockRegistry] = []

        def get_registry() -> None:
            registries.append(get_path_lock_registry())

        threads = [threading.Thread(target=get_registry) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same instance
        assert all(r is registries[0] for r in registries)


# ============================================================================
# Thread Safety Tests
# ============================================================================


class TestThreadSafety:
    """Tests for thread safety under concurrent access."""

    def test_concurrent_acquire(self, tmp_path: Path) -> None:
        """Multiple threads can acquire locks concurrently."""
        locks_path = tmp_path / "locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_path)
        errors: list[Exception] = []

        def worker(agent_id: str) -> None:
            try:
                for i in range(10):
                    registry.acquire(f"{agent_id}", f"/path/{agent_id}/{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"agent-{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(registry.get_all_locks()) == 50  # 5 agents * 10 locks each

    def test_concurrent_acquire_release(self, tmp_path: Path) -> None:
        """Concurrent acquire and release operations are thread-safe."""
        locks_path = tmp_path / "locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_path)
        errors: list[Exception] = []
        lock_ids: list[str] = []

        def acquirer() -> None:
            try:
                for i in range(20):
                    lock = registry.acquire("agent-1", f"/path/{i}")
                    lock_ids.append(lock.lock_id)
            except Exception as e:
                errors.append(e)

        def releaser() -> None:
            try:
                for _ in range(20):
                    if lock_ids:
                        lock_id = lock_ids.pop(0)
                        registry.release(lock_id)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=acquirer)
        t2 = threading.Thread(target=releaser)

        t1.start()
        time.sleep(0.01)  # Let acquirer get ahead
        t2.start()

        t1.join()
        t2.join()

        assert len(errors) == 0

    def test_concurrent_check_conflicts(self, tmp_path: Path) -> None:
        """Concurrent check_conflicts calls are thread-safe."""
        locks_path = tmp_path / "locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_path)
        errors: list[Exception] = []
        results: list[list[LockConflict]] = []

        # Pre-populate with locks
        for i in range(10):
            registry.acquire(f"agent-{i}", "/shared/path", lock_type=PathLockType.shared)

        def checker() -> None:
            try:
                for _ in range(50):
                    conflicts = registry.check_conflicts("/shared/path")
                    results.append(conflicts)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=checker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 250  # 5 threads * 50 checks each


# ============================================================================
# JSONL Persistence Tests
# ============================================================================


class TestJsonlPersistence:
    """Tests for JSONL persistence."""

    def test_persist_lock_creates_file(self, registry: PathLockRegistry, tmp_path: Path) -> None:
        """Acquiring a lock creates the persistence file."""
        locks_path = tmp_path / "locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_path)

        registry.acquire("agent-1", "/test/path")

        assert locks_path.exists()

    def test_persist_lock_writes_record(self, registry: PathLockRegistry, tmp_path: Path) -> None:
        """Acquiring a lock writes a lock record to JSONL."""
        locks_path = tmp_path / "locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_path)

        lock = registry.acquire("agent-1", "/test/path")

        content = locks_path.read_text()
        record = json.loads(content.strip().split("\n")[0])

        assert record["type"] == "lock"
        assert record["lock_id"] == lock.lock_id
        assert record["lock"]["agent_id"] == "agent-1"
        assert record["lock"]["path"] == "/test/path"

    def test_persist_release_writes_record(self, registry: PathLockRegistry, tmp_path: Path) -> None:
        """Releasing a lock writes a release record to JSONL."""
        locks_path = tmp_path / "locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_path)

        lock = registry.acquire("agent-1", "/test/path")
        registry.release(lock.lock_id)

        content = locks_path.read_text()
        lines = content.strip().split("\n")

        # Second line should be release record
        release_record = json.loads(lines[1])

        assert release_record["type"] == "release"
        assert release_record["lock_id"] == lock.lock_id

    def test_load_locks_replays_records(self, tmp_path: Path) -> None:
        """Loading replays lock records and skips released locks."""
        locks_path = tmp_path / "locks.jsonl"

        # Create first registry and acquire/release locks
        registry1 = PathLockRegistry(locks_path=locks_path)
        lock1 = registry1.acquire("agent-1", "/kept/path")  # Not released
        lock2 = registry1.acquire("agent-2", "/released/path")  # Will be released
        registry1.release(lock2.lock_id)

        # Create second registry - should only load lock1
        registry2 = PathLockRegistry(locks_path=locks_path)
        loaded_locks = registry2.get_all_locks()

        assert len(loaded_locks) == 1
        assert loaded_locks[0].lock_id == lock1.lock_id

    def test_load_locks_skips_malformed_lines(self, tmp_path: Path) -> None:
        """Loading skips malformed JSON lines."""
        locks_path = tmp_path / "locks.jsonl"
        locks_path.parent.mkdir(parents=True, exist_ok=True)

        # Write malformed and valid lines
        with locks_path.open("w") as f:
            f.write("not valid json\n")
            f.write('{"type": "lock", "lock_id": "test-123", "lock": {"lock_id": "test-123", "agent_id": "agent-1", "path": "/test", "lock_type": "exclusive", "acquired_at": "2024-01-01T00:00:00+00:00"}}\n')

        # Should not raise, should load valid record
        registry = PathLockRegistry(locks_path=locks_path)

        assert len(registry.get_all_locks()) == 1

    def test_write_jsonl_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Writing JSONL creates parent directories if needed."""
        locks_path = tmp_path / "deep" / "nested" / "dir" / "locks.jsonl"
        registry = PathLockRegistry(locks_path=locks_path)

        registry.acquire("agent-1", "/test/path")

        assert locks_path.exists()


# ============================================================================
# Edge Case Tests
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_path(self, registry: PathLockRegistry) -> None:
        """Can acquire lock on empty string path."""
        lock = registry.acquire("agent-1", "")

        assert lock.path == ""

    def test_long_path(self, registry: PathLockRegistry) -> None:
        """Can acquire lock on very long path."""
        long_path = "/" + "/".join([f"segment{i}" * 50 for i in range(20)])

        lock = registry.acquire("agent-1", long_path)

        assert lock.path == long_path

    def test_path_with_special_chars(self, registry: PathLockRegistry) -> None:
        """Can acquire lock on path with special characters."""
        special_path = "/path/with spaces/and-dashes/and_underscores/and.dots/and@symbols"

        lock = registry.acquire("agent-1", special_path)

        assert lock.path == special_path

    def test_unicode_path(self, registry: PathLockRegistry) -> None:
        """Can acquire lock on path with unicode characters."""
        unicode_path = "/path/with/unicode/文件/📁/日本語"

        lock = registry.acquire("agent-1", unicode_path)

        assert lock.path == unicode_path

    def test_agent_id_with_special_chars(self, registry: PathLockRegistry) -> None:
        """Can acquire lock with special agent ID characters."""
        special_agent = "forge:nova-agent_123.test@domain"

        lock = registry.acquire(special_agent, "/test/path")

        assert lock.agent_id == special_agent

    def test_multiple_shared_locks_same_path(self, registry: PathLockRegistry) -> None:
        """Multiple agents can hold shared locks on same path."""
        for i in range(10):
            registry.acquire(f"agent-{i}", "/shared/path", lock_type=PathLockType.shared)

        assert len(registry.get_all_locks()) == 10

    def test_same_path_different_types(self, registry: PathLockRegistry) -> None:
        """Same agent can hold different lock types on same path."""
        registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)
        registry.acquire("agent-1", "/test/path", lock_type=PathLockType.shared)

        locks = registry.get_locks_for_agent("agent-1")

        assert len(locks) == 2
        lock_types = {lock.lock_type for lock in locks}
        assert lock_types == {PathLockType.exclusive, PathLockType.shared}

    def test_lock_conflict_error_attributes(self, registry: PathLockRegistry) -> None:
        """LockConflictError has correct attributes."""
        registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)

        with pytest.raises(LockConflictError) as exc_info:
            registry.acquire("agent-2", "/test/path", lock_type=PathLockType.exclusive)

        error = exc_info.value
        assert error.path == "/test/path"
        assert len(error.conflicts) == 1
        assert error.conflicts[0].agent_id == "agent-1"

    def test_path_normalization_not_performed(self, registry: PathLockRegistry) -> None:
        """Paths are treated as strings without normalization."""
        # These are considered different paths
        registry.acquire("agent-1", "/path/to/file")
        registry.acquire("agent-2", "/path/to/file/")  # Trailing slash
        registry.acquire("agent-3", "/path//to/file")  # Double slash

        assert len(registry.get_all_locks()) == 3

    def test_acquire_after_release_same_path(self, registry: PathLockRegistry) -> None:
        """Can re-acquire lock on path after releasing."""
        lock = registry.acquire("agent-1", "/test/path", lock_type=PathLockType.exclusive)
        registry.release(lock.lock_id)

        # Should be able to acquire again
        lock2 = registry.acquire("agent-2", "/test/path", lock_type=PathLockType.exclusive)

        assert lock2.agent_id == "agent-2"

    def test_get_locks_excludes_expired(self, registry: PathLockRegistry) -> None:
        """get_locks_for_agent excludes expired locks."""
        registry.acquire("agent-1", "/expired/path", ttl_seconds=0.001)
        time.sleep(0.01)
        registry.acquire("agent-1", "/active/path")

        locks = registry.get_locks_for_agent("agent-1")

        assert len(locks) == 1
        assert locks[0].path == "/active/path"

    def test_check_conflicts_cleans_expired(self, registry: PathLockRegistry) -> None:
        """check_conflicts cleans expired locks before checking."""
        registry.acquire("agent-1", "/path", ttl_seconds=0.001)
        time.sleep(0.01)

        conflicts = registry.check_conflicts("/path")

        assert conflicts == []

    def test_lock_with_zero_ttl_expires_immediately(self, registry: PathLockRegistry) -> None:
        """Lock with TTL=0 expires immediately."""
        registry.acquire("agent-1", "/path", ttl_seconds=0)
        time.sleep(0.01)

        locks = registry.get_all_locks()

        assert len(locks) == 0  # Should be cleaned up
