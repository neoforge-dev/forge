"""Integration tests for dispatch cooldown functionality."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from forge_harness.fleet.priority_dispatch import (
    DISPATCH_COOLDOWN_MINUTES,
    _recently_dispatched,
    cleanup_expired_cooldowns,
    is_recently_dispatched,
    mark_task_dispatched,
)


@pytest.fixture(autouse=True)
def clear_cooldown_cache():
    """Clear cooldown cache before each test."""
    _recently_dispatched.clear()
    yield
    _recently_dispatched.clear()


def test_cooldown_prevents_immediate_redispatch():
    """Test that cooldown prevents immediate re-dispatch of the same task."""
    task_id = "CONT-001"

    # First dispatch should be allowed
    assert not is_recently_dispatched(task_id)

    # Mark as dispatched
    mark_task_dispatched(task_id)

    # Immediate re-check should show it's in cooldown
    assert is_recently_dispatched(task_id)

    # Should remain in cooldown for the full duration
    assert is_recently_dispatched(task_id, cooldown_minutes=DISPATCH_COOLDOWN_MINUTES)


def test_cooldown_expires_after_duration():
    """Test that cooldown expires after the configured duration."""
    task_id = "CONT-002"

    # Mark as dispatched
    mark_task_dispatched(task_id)

    # Should be in cooldown
    assert is_recently_dispatched(task_id)

    # Simulate time passing by backdating the dispatch
    _recently_dispatched[task_id] = datetime.now(UTC) - timedelta(minutes=31)

    # Should no longer be in cooldown
    assert not is_recently_dispatched(task_id, cooldown_minutes=30)

    # Should be removed from cache
    assert task_id not in _recently_dispatched


def test_multiple_tasks_tracked_independently():
    """Test that multiple tasks are tracked independently."""
    tasks = ["CONT-001", "CONT-002", "CONT-003"]

    # Mark all as dispatched at different times
    now = datetime.now(UTC)
    for i, task_id in enumerate(tasks):
        _recently_dispatched[task_id] = now - timedelta(minutes=i * 10)

    # CONT-001 is fresh (0 minutes ago)
    assert is_recently_dispatched("CONT-001", cooldown_minutes=5)

    # CONT-002 is 10 minutes old (expired for 5-minute cooldown)
    assert not is_recently_dispatched("CONT-002", cooldown_minutes=5)

    # CONT-003 is 20 minutes old (expired for 5-minute cooldown)
    assert not is_recently_dispatched("CONT-003", cooldown_minutes=5)


def test_cleanup_removes_expired_entries():
    """Test that cleanup removes only expired entries."""
    now = datetime.now(UTC)

    # Add mix of fresh and expired entries
    _recently_dispatched["FRESH-1"] = now
    _recently_dispatched["FRESH-2"] = now - timedelta(minutes=10)
    _recently_dispatched["EXPIRED-1"] = now - timedelta(minutes=35)
    _recently_dispatched["EXPIRED-2"] = now - timedelta(minutes=60)

    # Run cleanup with 30-minute cooldown
    removed = cleanup_expired_cooldowns(cooldown_minutes=30)

    # Should remove 2 expired entries
    assert removed == 2

    # Fresh entries should remain
    assert "FRESH-1" in _recently_dispatched
    assert "FRESH-2" in _recently_dispatched

    # Expired entries should be removed
    assert "EXPIRED-1" not in _recently_dispatched
    assert "EXPIRED-2" not in _recently_dispatched


def test_cleanup_handles_empty_cache():
    """Test that cleanup handles empty cache gracefully."""
    _recently_dispatched.clear()

    # Should not raise and should return 0
    removed = cleanup_expired_cooldowns()
    assert removed == 0


def test_cleanup_with_all_fresh_entries():
    """Test cleanup when all entries are fresh."""
    now = datetime.now(UTC)

    # Add only fresh entries
    _recently_dispatched["TASK-1"] = now
    _recently_dispatched["TASK-2"] = now - timedelta(minutes=5)
    _recently_dispatched["TASK-3"] = now - timedelta(minutes=10)

    # Run cleanup
    removed = cleanup_expired_cooldowns(cooldown_minutes=30)

    # Nothing should be removed
    assert removed == 0
    assert len(_recently_dispatched) == 3


def test_dispatch_loop_scenario():
    """Test realistic dispatch loop scenario with cooldown."""
    task_id = "CONT-001"

    # Cycle 1: Task dispatched
    assert not is_recently_dispatched(task_id)
    mark_task_dispatched(task_id)

    # Cycle 2: 60 seconds later - still in cooldown
    assert is_recently_dispatched(task_id)

    # Cycle 3: 5 minutes later - still in cooldown
    _recently_dispatched[task_id] = datetime.now(UTC) - timedelta(minutes=5)
    assert is_recently_dispatched(task_id, cooldown_minutes=30)

    # Cycle 30: 31 minutes later - cooldown expired
    _recently_dispatched[task_id] = datetime.now(UTC) - timedelta(minutes=31)
    assert not is_recently_dispatched(task_id, cooldown_minutes=30)


def test_cooldown_configuration():
    """Test that cooldown duration is configurable."""
    task_id = "CONFIG-TEST"

    mark_task_dispatched(task_id)

    # Different cooldown durations
    assert is_recently_dispatched(task_id, cooldown_minutes=60)
    assert is_recently_dispatched(task_id, cooldown_minutes=30)
    assert is_recently_dispatched(task_id, cooldown_minutes=5)

    # Backdate to 10 minutes ago
    _recently_dispatched[task_id] = datetime.now(UTC) - timedelta(minutes=10)

    # Should vary based on cooldown setting
    assert is_recently_dispatched(task_id, cooldown_minutes=60)  # Still in cooldown
    assert is_recently_dispatched(task_id, cooldown_minutes=30)  # Still in cooldown
    assert not is_recently_dispatched(task_id, cooldown_minutes=5)  # Expired


def test_default_cooldown_constant():
    """Test that the default cooldown constant is set."""
    assert DISPATCH_COOLDOWN_MINUTES == 30
    assert isinstance(DISPATCH_COOLDOWN_MINUTES, int)
    assert DISPATCH_COOLDOWN_MINUTES > 0
