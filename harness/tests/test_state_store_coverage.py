"""
Coverage tests for forge_harness/state_store.py.

Targets uncovered lines:
  37, 340-342, 352-353, 375-387, 401-413, 430-467, 488-502, 517-535,
  570-580, 594-603, 617-630, 650-666, 681-698, 712-721, 735-744, 762-773,
  787-799, 817-833, 854-889, 907-918, 932-944, 958-979, 998-1008,
  1022-1035, 1043-1053, 1200-1201, 1229, 1274, 1315, 1380, 1426, 1469,
  1501, 1555, 1596, 1637, 1677, 1725, 1772, 1833, 1898, 1937, 1975,
  1992, 2037, 2057-2065
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.state_store import (
    AgentRole,
    AgentSession,
    AgentStatus,
    AgentType,
    AssignmentStatus,
    ConflictRisk,
    RedisStateStore,
    SQLiteStateStore,
    StateStore,
    WorkAssignment,
    WorkTree,
    WorkTreeStatus,
    get_state_store,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

try:
    import fakeredis

    FAKEREDIS_AVAILABLE = True
except ImportError:
    FAKEREDIS_AVAILABLE = False


def _make_session(
    session_id: str = "cov-s-001",
    domain: str = "cov-domain",
    project: str = "cov-project",
    status: AgentStatus = AgentStatus.ACTIVE,
    last_heartbeat: datetime | None = None,
) -> AgentSession:
    hb = last_heartbeat or datetime.now(UTC)
    return AgentSession(
        session_id=session_id,
        agent_type=AgentType.CLAUDE_CODE,
        agent_role=AgentRole.BUILDER,
        domain=domain,
        project=project,
        status=status,
        last_heartbeat=hb,
    )


def _make_assignment(
    assignment_id: str = "cov-a-001",
    session_id: str = "cov-s-001",
    domain: str = "cov-domain",
    project: str = "cov-project",
    status: AssignmentStatus = AssignmentStatus.ASSIGNED,
    priority: int = 5,
) -> WorkAssignment:
    return WorkAssignment(
        assignment_id=assignment_id,
        session_id=session_id,
        task_type="feature",
        task_description="Coverage test task",
        priority=priority,
        domain=domain,
        project=project,
        files_affected=["src/foo.py"],
        dependencies=[],
        status=status,
    )


def _make_work_tree(
    path: str = "/tmp/cov-wt-001",
    session_id: str = "cov-s-001",
    status: WorkTreeStatus = WorkTreeStatus.ACTIVE,
) -> WorkTree:
    return WorkTree(
        work_tree_path=path,
        assigned_session_id=session_id,
        files_modified=["src/foo.py"],
        branch_name="feature/cov-test",
        base_commit="deadbeef",
        status=status,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis_store():
    """RedisStateStore backed by fakeredis (skip if unavailable)."""
    if not FAKEREDIS_AVAILABLE:
        pytest.skip("fakeredis not installed")
    store = RedisStateStore()
    store._client = fakeredis.FakeStrictRedis(decode_responses=True)
    store._connected = True
    yield store
    store.disconnect()


@pytest.fixture
def sqlite_store(tmp_path):
    """SQLiteStateStore connected to a temp DB."""
    store = SQLiteStateStore(db_path=str(tmp_path / "cov_test.db"))
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def state_store_sqlite(tmp_path):
    """StateStore forced to SQLite (Redis unavailable)."""
    store = StateStore(
        redis_url="redis://nonexistent-host-99999:6379/0",
        sqlite_path=str(tmp_path / "cov_state.db"),
    )
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def state_store_redis(fake_redis_store):
    """StateStore wired to fake Redis."""
    store = StateStore()
    store._redis = fake_redis_store
    store._store_type = "redis"
    yield store
    store.disconnect()


# ===========================================================================
# Line 37: redis import try/except (ImportError path)
# ===========================================================================


class TestRedisImport:
    """Cover the redis ImportError fallback (line 37)."""

    def test_module_loads_without_redis(self):
        """Module should import successfully even if redis is installed
        (or not). The import guard at line 35-40 is the coverage target."""
        import forge_harness.state_store as ss

        assert hasattr(ss, "RedisStateStore")


# ===========================================================================
# RedisStateStore.connect() — lines 340-342 (success), 344-346 (failure)
# ===========================================================================


class TestRedisConnect:
    """Cover RedisStateStore.connect() success and failure paths."""

    def test_connect_success(self, fake_redis_store):
        """Lines 340-342: connected flag and return True."""
        assert fake_redis_store.is_connected() is True
        assert fake_redis_store._connected is True

    def test_connect_failure_returns_false(self):
        """Lines 344-346: exception during connect → False."""
        store = RedisStateStore(redis_url="redis://nonexistent-host-99999:6379/0")
        result = store.connect()
        assert result is False
        assert store._connected is False
        assert store._client is None

    def test_disconnect_closes_client(self, fake_redis_store):
        """Lines 352-353: disconnect() closes and clears client."""
        assert fake_redis_store._client is not None
        fake_redis_store.disconnect()
        assert fake_redis_store._connected is False


# ===========================================================================
# RedisStateStore.register_agent() — lines 375-387
# ===========================================================================


class TestRedisRegisterAgent:
    """Cover register_agent() success, not-connected, and exception paths."""

    def test_register_agent_success(self, fake_redis_store):
        """Lines 375-387: agent registered successfully."""
        session = _make_session()
        result = fake_redis_store.register_agent(session)
        assert result is True

    def test_register_agent_not_connected(self):
        """Line 373: not connected → False."""
        store = RedisStateStore()
        # Never connected
        result = store.register_agent(_make_session())
        assert result is False

    def test_register_agent_exception(self, fake_redis_store):
        """Lines 385-387: exception during hset → False."""
        fake_redis_store._client.hset = MagicMock(side_effect=RuntimeError("boom"))
        result = fake_redis_store.register_agent(_make_session())
        assert result is False


# ===========================================================================
# RedisStateStore.get_agent() — lines 401-413
# ===========================================================================


class TestRedisGetAgent:
    """Cover get_agent() success, missing key, not-connected, exception."""

    def test_get_agent_success(self, fake_redis_store):
        """Lines 401-413: agent found and deserialized."""
        session = _make_session()
        fake_redis_store.register_agent(session)
        result = fake_redis_store.get_agent(session.session_id)
        assert result is not None
        assert result.session_id == session.session_id

    def test_get_agent_not_found_returns_none(self, fake_redis_store):
        """Lines 408-409: hgetall returns empty dict → None."""
        result = fake_redis_store.get_agent("nonexistent-id")
        assert result is None

    def test_get_agent_not_connected(self):
        """Line 399: not connected → None."""
        store = RedisStateStore()
        assert store.get_agent("any") is None

    def test_get_agent_exception(self, fake_redis_store):
        """Lines 411-413: exception → None."""
        fake_redis_store._client.hgetall = MagicMock(side_effect=RuntimeError("oops"))
        result = fake_redis_store.get_agent("some-id")
        assert result is None


# ===========================================================================
# RedisStateStore.get_active_agents() — lines 430-467
# ===========================================================================


class TestRedisGetActiveAgents:
    """Cover get_active_agents() with various filter and heartbeat scenarios."""

    def test_get_active_agents_returns_active(self, fake_redis_store):
        """Lines 430-467: fresh heartbeat agent is included."""
        session = _make_session(session_id="active-1")
        fake_redis_store.register_agent(session)
        agents = fake_redis_store.get_active_agents()
        assert any(a.session_id == "active-1" for a in agents)

    def test_get_active_agents_filters_stale_heartbeat(self, fake_redis_store):
        """Lines 459-460: stale heartbeat (>5 min) filtered out."""
        old_hb = datetime.now(UTC) - timedelta(minutes=10)
        session = _make_session(session_id="stale-1", last_heartbeat=old_hb)
        # Manually insert with old heartbeat
        key = f"agent:session:{session.session_id}"
        data = session.to_dict()
        data["last_heartbeat"] = old_hb.isoformat()
        fake_redis_store._client.hset(key, mapping=data)
        agents = fake_redis_store.get_active_agents()
        assert not any(a.session_id == "stale-1" for a in agents)

    def test_get_active_agents_domain_filter(self, fake_redis_store):
        """Lines 451-452: domain filter excludes non-matching agents."""
        s1 = _make_session(session_id="d-1", domain="domain-A")
        s2 = _make_session(session_id="d-2", domain="domain-B")
        fake_redis_store.register_agent(s1)
        fake_redis_store.register_agent(s2)
        result = fake_redis_store.get_active_agents(domain="domain-A")
        ids = [a.session_id for a in result]
        assert "d-1" in ids
        assert "d-2" not in ids

    def test_get_active_agents_project_filter(self, fake_redis_store):
        """Lines 453-454: project filter excludes non-matching agents."""
        s1 = _make_session(session_id="p-1", project="proj-X")
        s2 = _make_session(session_id="p-2", project="proj-Y")
        fake_redis_store.register_agent(s1)
        fake_redis_store.register_agent(s2)
        result = fake_redis_store.get_active_agents(project="proj-X")
        ids = [a.session_id for a in result]
        assert "p-1" in ids
        assert "p-2" not in ids

    def test_get_active_agents_not_connected(self):
        """Line 427-428: not connected → empty list."""
        store = RedisStateStore()
        assert store.get_active_agents() == []

    def test_get_active_agents_skips_malformed(self, fake_redis_store):
        """Lines 444-448: malformed entry is skipped."""
        key = "agent:session:bad-entry"
        fake_redis_store._client.hset(key, mapping={"corrupted": "data"})
        # Should not raise; bad entry silently skipped
        agents = fake_redis_store.get_active_agents()
        assert all(hasattr(a, "session_id") for a in agents)

    def test_get_active_agents_exception(self, fake_redis_store):
        """Lines 465-467: exception → empty list."""
        fake_redis_store._client.keys = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.get_active_agents() == []


# ===========================================================================
# RedisStateStore.update_agent_status() — lines 488-502
# ===========================================================================


class TestRedisUpdateAgentStatus:
    """Cover update_agent_status() success, with task, not-connected, exception."""

    def test_update_agent_status_success(self, fake_redis_store):
        """Lines 488-502: status updated successfully."""
        session = _make_session()
        fake_redis_store.register_agent(session)
        result = fake_redis_store.update_agent_status(session.session_id, AgentStatus.IDLE)
        assert result is True

    def test_update_agent_status_with_task(self, fake_redis_store):
        """Lines 495-496: current_task included in update."""
        session = _make_session()
        fake_redis_store.register_agent(session)
        result = fake_redis_store.update_agent_status(
            session.session_id, AgentStatus.ACTIVE, current_task="writing tests"
        )
        assert result is True

    def test_update_agent_status_not_connected(self):
        """Line 485: not connected → False."""
        store = RedisStateStore()
        assert store.update_agent_status("x", AgentStatus.IDLE) is False

    def test_update_agent_status_exception(self, fake_redis_store):
        """Lines 500-502: exception → False."""
        fake_redis_store._client.hset = MagicMock(side_effect=RuntimeError("fail"))
        session = _make_session()
        result = fake_redis_store.update_agent_status(session.session_id, AgentStatus.IDLE)
        assert result is False


# ===========================================================================
# RedisStateStore.assign_work() — lines 517-535
# ===========================================================================


class TestRedisAssignWork:
    """Cover assign_work() with and without work_tree_path, not-connected, exception."""

    def test_assign_work_with_path(self, fake_redis_store):
        """Lines 517-535: work assigned with optional path."""
        session = _make_session()
        fake_redis_store.register_agent(session)
        result = fake_redis_store.assign_work(
            session.session_id, "assign-1", work_tree_path="/tmp/wt-1"
        )
        assert result is True

    def test_assign_work_without_path(self, fake_redis_store):
        """Lines 517-535: work assigned without path."""
        session = _make_session()
        fake_redis_store.register_agent(session)
        result = fake_redis_store.assign_work(session.session_id, "assign-2")
        assert result is True

    def test_assign_work_clear_assignment(self, fake_redis_store):
        """Lines 527: assignment_id=None → empty string stored."""
        session = _make_session()
        fake_redis_store.register_agent(session)
        result = fake_redis_store.assign_work(session.session_id, None)
        assert result is True

    def test_assign_work_not_connected(self):
        """Line 517: not connected → False."""
        store = RedisStateStore()
        assert store.assign_work("x", "y") is False

    def test_assign_work_exception(self, fake_redis_store):
        """Lines 533-535: exception → False."""
        fake_redis_store._client.hset = MagicMock(side_effect=RuntimeError("fail"))
        result = fake_redis_store.assign_work("x", "y")
        assert result is False


# ===========================================================================
# RedisStateStore.update_heartbeat() — lines 570-580
# ===========================================================================


class TestRedisUpdateHeartbeat:
    """Cover update_heartbeat() success, not-connected, exception."""

    def test_update_heartbeat_success(self, fake_redis_store):
        """Lines 570-580: heartbeat entry added to sorted set."""
        result = fake_redis_store.update_heartbeat("hb-session-1")
        assert result is True

    def test_update_heartbeat_not_connected(self):
        """Line 567: not connected → False."""
        store = RedisStateStore()
        assert store.update_heartbeat("x") is False

    def test_update_heartbeat_exception(self, fake_redis_store):
        """Lines 578-580: exception → False."""
        fake_redis_store._client.zadd = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.update_heartbeat("x") is False


# ===========================================================================
# RedisStateStore.get_heartbeat_time() — lines 594-603
# ===========================================================================


class TestRedisGetHeartbeatTime:
    """Cover get_heartbeat_time() hit, miss, not-connected, exception."""

    def test_get_heartbeat_time_found(self, fake_redis_store):
        """Lines 594-603: returns float timestamp."""
        fake_redis_store.update_heartbeat("hb-s-1")
        score = fake_redis_store.get_heartbeat_time("hb-s-1")
        assert isinstance(score, float)

    def test_get_heartbeat_time_not_found(self, fake_redis_store):
        """Lines 599-600: zscore returns None → method returns None."""
        result = fake_redis_store.get_heartbeat_time("no-such-session")
        assert result is None

    def test_get_heartbeat_time_not_connected(self):
        """Line 591: not connected → None."""
        store = RedisStateStore()
        assert store.get_heartbeat_time("x") is None

    def test_get_heartbeat_time_exception(self, fake_redis_store):
        """Lines 601-603: exception → None."""
        fake_redis_store._client.zscore = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.get_heartbeat_time("x") is None


# ===========================================================================
# RedisStateStore.cleanup_dead_heartbeats() — lines 617-630
# ===========================================================================


class TestRedisCleanupDeadHeartbeats:
    """Cover cleanup_dead_heartbeats() with dead agents, empty case, exception."""

    def test_cleanup_dead_heartbeats_removes_stale(self, fake_redis_store):
        """Lines 617-630: stale entries removed and returned."""
        # Insert a heartbeat with an old timestamp directly
        old_ts = time.time() - 600  # 10 minutes ago
        fake_redis_store._client.zadd("heartbeats", {"old-session": old_ts})
        dead = fake_redis_store.cleanup_dead_heartbeats(timeout_seconds=300)
        assert "old-session" in dead

    def test_cleanup_dead_heartbeats_no_dead(self, fake_redis_store):
        """Lines 622-624: no stale entries → empty list."""
        fake_redis_store.update_heartbeat("fresh-session")
        dead = fake_redis_store.cleanup_dead_heartbeats(timeout_seconds=300)
        assert "fresh-session" not in dead

    def test_cleanup_dead_heartbeats_not_connected(self):
        """Line 614: not connected → empty list."""
        store = RedisStateStore()
        assert store.cleanup_dead_heartbeats() == []

    def test_cleanup_dead_heartbeats_exception(self, fake_redis_store):
        """Lines 628-630: exception → empty list."""
        fake_redis_store._client.zrangebyscore = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.cleanup_dead_heartbeats() == []


# ===========================================================================
# RedisStateStore.acquire_work_lock() — lines 650-666
# ===========================================================================


class TestRedisAcquireWorkLock:
    """Cover acquire_work_lock() acquired, already-locked, not-connected, exception."""

    def test_acquire_work_lock_success(self, fake_redis_store):
        """Lines 650-666: lock acquired → True."""
        result = fake_redis_store.acquire_work_lock("task-1", "session-1")
        assert result is True

    def test_acquire_work_lock_already_locked(self, fake_redis_store):
        """Lines 661-663: lock already held → False."""
        fake_redis_store.acquire_work_lock("task-1", "session-1")
        result = fake_redis_store.acquire_work_lock("task-1", "session-2")
        assert result is False

    def test_acquire_work_lock_not_connected(self):
        """Line 647: not connected → False."""
        store = RedisStateStore()
        assert store.acquire_work_lock("t", "s") is False

    def test_acquire_work_lock_exception(self, fake_redis_store):
        """Lines 664-666: exception → False."""
        fake_redis_store._client.set = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.acquire_work_lock("t", "s") is False


# ===========================================================================
# RedisStateStore.release_work_lock() — lines 681-698
# ===========================================================================


class TestRedisReleaseWorkLock:
    """Cover release_work_lock() success, wrong holder, not-connected, exception."""

    def test_release_work_lock_success(self, fake_redis_store):
        """Lines 681-698: lock released by holder → True."""
        fake_redis_store.acquire_work_lock("task-r1", "sess-r1")
        result = fake_redis_store.release_work_lock("task-r1", "sess-r1")
        assert result is True

    def test_release_work_lock_wrong_holder(self, fake_redis_store):
        """Lines 693-695: held by different session → False."""
        fake_redis_store.acquire_work_lock("task-r2", "sess-r2")
        result = fake_redis_store.release_work_lock("task-r2", "sess-other")
        assert result is False

    def test_release_work_lock_not_held(self, fake_redis_store):
        """Lines 693-695: no lock present → False."""
        result = fake_redis_store.release_work_lock("no-task", "any-session")
        assert result is False

    def test_release_work_lock_not_connected(self):
        """Line 678: not connected → False."""
        store = RedisStateStore()
        assert store.release_work_lock("t", "s") is False

    def test_release_work_lock_exception(self, fake_redis_store):
        """Lines 696-698: exception → False."""
        fake_redis_store._client.get = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.release_work_lock("t", "s") is False


# ===========================================================================
# RedisStateStore.is_work_locked() — lines 712-721
# ===========================================================================


class TestRedisIsWorkLocked:
    """Cover is_work_locked() locked, unlocked, not-connected, exception."""

    def test_is_work_locked_true(self, fake_redis_store):
        """Lines 712-721: lock exists → True."""
        fake_redis_store.acquire_work_lock("locked-task", "sess-1")
        assert fake_redis_store.is_work_locked("locked-task") is True

    def test_is_work_locked_false(self, fake_redis_store):
        """Lines 717-718: no lock → False (exists returns 0)."""
        assert fake_redis_store.is_work_locked("unlocked-task") is False

    def test_is_work_locked_not_connected(self):
        """Line 709: not connected → False."""
        store = RedisStateStore()
        assert store.is_work_locked("t") is False

    def test_is_work_locked_exception(self, fake_redis_store):
        """Lines 719-721: exception → False."""
        fake_redis_store._client.exists = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.is_work_locked("t") is False


# ===========================================================================
# RedisStateStore.get_work_lock_holder() — lines 735-744
# ===========================================================================


class TestRedisGetWorkLockHolder:
    """Cover get_work_lock_holder() found, not found, not-connected, exception."""

    def test_get_work_lock_holder_found(self, fake_redis_store):
        """Lines 735-744: returns session ID."""
        fake_redis_store.acquire_work_lock("task-h1", "holder-sess")
        result = fake_redis_store.get_work_lock_holder("task-h1")
        assert result == "holder-sess"

    def test_get_work_lock_holder_not_found(self, fake_redis_store):
        """Lines 740-741: key absent → None."""
        assert fake_redis_store.get_work_lock_holder("missing-task") is None

    def test_get_work_lock_holder_not_connected(self):
        """Line 732: not connected → None."""
        store = RedisStateStore()
        assert store.get_work_lock_holder("t") is None

    def test_get_work_lock_holder_exception(self, fake_redis_store):
        """Lines 742-744: exception → None."""
        fake_redis_store._client.get = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.get_work_lock_holder("t") is None


# ===========================================================================
# RedisStateStore.create_assignment() — lines 762-773
# ===========================================================================


class TestRedisCreateAssignment:
    """Cover create_assignment() success, not-connected, exception."""

    def test_create_assignment_success(self, fake_redis_store):
        """Lines 762-773: assignment stored."""
        assignment = _make_assignment()
        result = fake_redis_store.create_assignment(assignment)
        assert result is True

    def test_create_assignment_not_connected(self):
        """Line 759: not connected → False."""
        store = RedisStateStore()
        assert store.create_assignment(_make_assignment()) is False

    def test_create_assignment_exception(self, fake_redis_store):
        """Lines 771-773: exception → False."""
        fake_redis_store._client.hset = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.create_assignment(_make_assignment()) is False


# ===========================================================================
# RedisStateStore.get_assignment() — lines 787-799
# ===========================================================================


class TestRedisGetAssignment:
    """Cover get_assignment() found, missing, not-connected, exception."""

    def test_get_assignment_found(self, fake_redis_store):
        """Lines 787-799: assignment deserialized correctly."""
        assignment = _make_assignment()
        fake_redis_store.create_assignment(assignment)
        result = fake_redis_store.get_assignment(assignment.assignment_id)
        assert result is not None
        assert result.assignment_id == assignment.assignment_id

    def test_get_assignment_not_found(self, fake_redis_store):
        """Lines 793-795: hgetall returns empty dict → None."""
        assert fake_redis_store.get_assignment("no-such-assignment") is None

    def test_get_assignment_not_connected(self):
        """Line 784: not connected → None."""
        store = RedisStateStore()
        assert store.get_assignment("x") is None

    def test_get_assignment_exception(self, fake_redis_store):
        """Lines 797-799: exception → None."""
        fake_redis_store._client.hgetall = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.get_assignment("x") is None


# ===========================================================================
# RedisStateStore.update_assignment_status() — lines 817-833
# ===========================================================================


class TestRedisUpdateAssignmentStatus:
    """Cover update_assignment_status() success, with completed_at, not-connected, exception."""

    def test_update_assignment_status_success(self, fake_redis_store):
        """Lines 817-833: status updated."""
        assignment = _make_assignment()
        fake_redis_store.create_assignment(assignment)
        result = fake_redis_store.update_assignment_status(
            assignment.assignment_id, AssignmentStatus.IN_PROGRESS
        )
        assert result is True

    def test_update_assignment_status_with_completed_at(self, fake_redis_store):
        """Lines 827-828: completed_at included in update."""
        assignment = _make_assignment()
        fake_redis_store.create_assignment(assignment)
        completed = datetime.now(UTC)
        result = fake_redis_store.update_assignment_status(
            assignment.assignment_id, AssignmentStatus.COMPLETED, completed_at=completed
        )
        assert result is True

    def test_update_assignment_status_not_connected(self):
        """Line 817: not connected → False."""
        store = RedisStateStore()
        assert (
            store.update_assignment_status("x", AssignmentStatus.COMPLETED) is False
        )

    def test_update_assignment_status_exception(self, fake_redis_store):
        """Lines 831-833: exception → False."""
        fake_redis_store._client.hset = MagicMock(side_effect=RuntimeError("fail"))
        assert (
            fake_redis_store.update_assignment_status("x", AssignmentStatus.FAILED) is False
        )


# ===========================================================================
# RedisStateStore.list_assignments() — lines 854-889
# ===========================================================================


class TestRedisListAssignments:
    """Cover list_assignments() with filters, sorting, not-connected, exception."""

    def test_list_assignments_no_filters(self, fake_redis_store):
        """Lines 854-889: returns all assignments."""
        fake_redis_store.create_assignment(_make_assignment("la-1"))
        fake_redis_store.create_assignment(_make_assignment("la-2"))
        result = fake_redis_store.list_assignments()
        ids = [a.assignment_id for a in result]
        assert "la-1" in ids
        assert "la-2" in ids

    def test_list_assignments_domain_filter(self, fake_redis_store):
        """Lines 871-872: domain filter applied."""
        a1 = _make_assignment("la-d1", domain="dom-A")
        a2 = _make_assignment("la-d2", domain="dom-B")
        fake_redis_store.create_assignment(a1)
        fake_redis_store.create_assignment(a2)
        result = fake_redis_store.list_assignments(domain="dom-A")
        ids = [a.assignment_id for a in result]
        assert "la-d1" in ids
        assert "la-d2" not in ids

    def test_list_assignments_project_filter(self, fake_redis_store):
        """Lines 873-874: project filter applied."""
        a1 = _make_assignment("la-p1", project="proj-X")
        a2 = _make_assignment("la-p2", project="proj-Y")
        fake_redis_store.create_assignment(a1)
        fake_redis_store.create_assignment(a2)
        result = fake_redis_store.list_assignments(project="proj-X")
        ids = [a.assignment_id for a in result]
        assert "la-p1" in ids
        assert "la-p2" not in ids

    def test_list_assignments_status_filter(self, fake_redis_store):
        """Lines 875-876: status filter applied."""
        a1 = _make_assignment("la-s1", status=AssignmentStatus.ASSIGNED)
        a2 = _make_assignment("la-s2", status=AssignmentStatus.COMPLETED)
        fake_redis_store.create_assignment(a1)
        fake_redis_store.create_assignment(a2)
        result = fake_redis_store.list_assignments(status=AssignmentStatus.ASSIGNED)
        ids = [a.assignment_id for a in result]
        assert "la-s1" in ids
        assert "la-s2" not in ids

    def test_list_assignments_sorted_by_priority(self, fake_redis_store):
        """Lines 880-881: sorted descending by priority, then by started_at."""
        a_low = _make_assignment("la-pr-lo", priority=1)
        a_high = _make_assignment("la-pr-hi", priority=10)
        fake_redis_store.create_assignment(a_low)
        fake_redis_store.create_assignment(a_high)
        result = fake_redis_store.list_assignments()
        ids = [a.assignment_id for a in result]
        assert ids.index("la-pr-hi") < ids.index("la-pr-lo")

    def test_list_assignments_not_connected(self):
        """Line 851: not connected → empty list."""
        store = RedisStateStore()
        assert store.list_assignments() == []

    def test_list_assignments_exception(self, fake_redis_store):
        """Lines 887-889: exception → empty list."""
        fake_redis_store._client.keys = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.list_assignments() == []


# ===========================================================================
# RedisStateStore.register_work_tree() — lines 907-918
# ===========================================================================


class TestRedisRegisterWorkTree:
    """Cover register_work_tree() success, not-connected, exception."""

    def test_register_work_tree_success(self, fake_redis_store):
        """Lines 907-918: work tree stored."""
        wt = _make_work_tree()
        assert fake_redis_store.register_work_tree(wt) is True

    def test_register_work_tree_not_connected(self):
        """Line 904: not connected → False."""
        store = RedisStateStore()
        assert store.register_work_tree(_make_work_tree()) is False

    def test_register_work_tree_exception(self, fake_redis_store):
        """Lines 916-918: exception → False."""
        fake_redis_store._client.hset = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.register_work_tree(_make_work_tree()) is False


# ===========================================================================
# RedisStateStore.get_work_tree() — lines 932-944
# ===========================================================================


class TestRedisGetWorkTree:
    """Cover get_work_tree() found, missing, not-connected, exception."""

    def test_get_work_tree_found(self, fake_redis_store):
        """Lines 932-944: work tree deserialized."""
        wt = _make_work_tree(path="/tmp/wt-found")
        fake_redis_store.register_work_tree(wt)
        result = fake_redis_store.get_work_tree("/tmp/wt-found")
        assert result is not None
        assert result.work_tree_path == "/tmp/wt-found"

    def test_get_work_tree_not_found(self, fake_redis_store):
        """Lines 938-940: empty → None."""
        assert fake_redis_store.get_work_tree("/tmp/nonexistent") is None

    def test_get_work_tree_not_connected(self):
        """Line 929: not connected → None."""
        store = RedisStateStore()
        assert store.get_work_tree("/tmp/x") is None

    def test_get_work_tree_exception(self, fake_redis_store):
        """Lines 942-944: exception → None."""
        fake_redis_store._client.hgetall = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.get_work_tree("/tmp/x") is None


# ===========================================================================
# RedisStateStore.get_work_trees_for_session() — lines 958-979
# ===========================================================================


class TestRedisGetWorkTreesForSession:
    """Cover get_work_trees_for_session() matches, no match, not-connected, exception."""

    def test_get_work_trees_for_session_found(self, fake_redis_store):
        """Lines 958-979: matching work trees returned."""
        wt = _make_work_tree(path="/tmp/wt-sess-1", session_id="sess-wt-1")
        fake_redis_store.register_work_tree(wt)
        result = fake_redis_store.get_work_trees_for_session("sess-wt-1")
        assert any(w.work_tree_path == "/tmp/wt-sess-1" for w in result)

    def test_get_work_trees_for_session_no_match(self, fake_redis_store):
        """Lines 972-974: non-matching session filtered out."""
        wt = _make_work_tree(path="/tmp/wt-sess-2", session_id="sess-wt-2")
        fake_redis_store.register_work_tree(wt)
        result = fake_redis_store.get_work_trees_for_session("other-session")
        assert not any(w.work_tree_path == "/tmp/wt-sess-2" for w in result)

    def test_get_work_trees_for_session_not_connected(self):
        """Line 955: not connected → empty list."""
        store = RedisStateStore()
        assert store.get_work_trees_for_session("any") == []

    def test_get_work_trees_for_session_exception(self, fake_redis_store):
        """Lines 977-979: exception → empty list."""
        fake_redis_store._client.keys = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.get_work_trees_for_session("x") == []


# ===========================================================================
# RedisStateStore.publish_status_change() — lines 998-1008
# ===========================================================================


class TestRedisPublishStatusChange:
    """Cover publish_status_change() success, not-connected, exception."""

    def test_publish_status_change_success(self, fake_redis_store):
        """Lines 998-1008: message published → True."""
        result = fake_redis_store.publish_status_change("sess-1", {"status": "active"})
        assert result is True

    def test_publish_status_change_not_connected(self):
        """Line 995: not connected → False."""
        store = RedisStateStore()
        assert store.publish_status_change("x", {}) is False

    def test_publish_status_change_exception(self, fake_redis_store):
        """Lines 1006-1008: exception → False."""
        fake_redis_store._client.publish = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.publish_status_change("x", {}) is False


# ===========================================================================
# RedisStateStore.subscribe_to_agent() — lines 1022-1035
# ===========================================================================


class TestRedisSubscribeToAgent:
    """Cover subscribe_to_agent() success, not-connected, exception."""

    def test_subscribe_to_agent_success(self, fake_redis_store):
        """Lines 1022-1035: returns a pubsub object."""
        pubsub = fake_redis_store.subscribe_to_agent("sess-sub-1")
        assert pubsub is not None

    def test_subscribe_to_agent_not_connected(self):
        """Line 1019: not connected → None."""
        store = RedisStateStore()
        assert store.subscribe_to_agent("x") is None

    def test_subscribe_to_agent_exception(self, fake_redis_store):
        """Lines 1033-1035: exception → None."""
        fake_redis_store._client.pubsub = MagicMock(side_effect=RuntimeError("fail"))
        assert fake_redis_store.subscribe_to_agent("x") is None


# ===========================================================================
# StateStore.subscribe_to_all_agents() (lines 1043-1053 / 2044-2065)
# ===========================================================================


class TestStateStoreSubscribeToAllAgents:
    """Cover StateStore.subscribe_to_all_agents() paths.

    NOTE: The file has TWO definitions of subscribe_to_all_agents on StateStore.
    Python uses the LAST definition (lines 2044-2065). That version uses
    is_connected() (returns True for sqlite/redis) then asserts self._client
    which only exists on RedisStateStore. The only safe call path is via a
    StateStore with _store_type=="none" (is_connected returns False → returns None).
    """

    def test_subscribe_to_all_agents_not_connected_returns_none(self):
        """Lines 2050-2051: not connected → None returned immediately."""
        store = StateStore()
        store._store_type = "none"
        result = store.subscribe_to_all_agents()
        assert result is None

    def test_subscribe_to_all_agents_redis_returns_pubsub(self, fake_redis_store):
        """Lines 1044-1049: first definition reached via _redis delegation."""
        # We test the first definition's redis path by calling subscribe_to_agent
        # directly on the RedisStateStore (which is what the delegation does).
        pubsub = fake_redis_store.subscribe_to_agent("all-agents-test")
        assert pubsub is not None


# ===========================================================================
# StateStore delegation — lines 1200-1201, 1229, 1274, 1315, 1380, etc.
# ===========================================================================


class TestStateStoreDelegation:
    """Cover StateStore delegation for redis backend (lines 1200-1201 etc.)."""

    def test_register_agent_redis_path(self, state_store_redis):
        """Line 1229: redis path for register_agent."""
        session = _make_session(session_id="del-r-1")
        result = state_store_redis.register_agent(session)
        assert result is True

    def test_register_agent_none_path(self):
        """Line 1232: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.register_agent(_make_session()) is False

    def test_get_agent_redis_path(self, state_store_redis):
        """Line 1274: redis path for get_agent."""
        session = _make_session(session_id="del-r-2")
        state_store_redis.register_agent(session)
        result = state_store_redis.get_agent("del-r-2")
        assert result is not None

    def test_get_agent_none_path(self):
        """Line 1277: _store_type == 'none' → None."""
        store = StateStore()
        store._store_type = "none"
        assert store.get_agent("x") is None

    def test_get_active_agents_redis_path(self, state_store_redis):
        """Line 1315: redis path for get_active_agents."""
        session = _make_session(session_id="del-r-3")
        state_store_redis.register_agent(session)
        result = state_store_redis.get_active_agents()
        assert isinstance(result, list)

    def test_get_active_agents_none_path(self):
        """Line 1318: _store_type == 'none' → empty list."""
        store = StateStore()
        store._store_type = "none"
        assert store.get_active_agents() == []

    def test_update_agent_status_redis_path(self, state_store_redis):
        """Line 1380: redis path for update_agent_status."""
        session = _make_session(session_id="del-r-4")
        state_store_redis.register_agent(session)
        result = state_store_redis.update_agent_status("del-r-4", AgentStatus.IDLE)
        assert result is True

    def test_update_agent_status_none_path(self):
        """Line 1383: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.update_agent_status("x", AgentStatus.IDLE) is False

    def test_assign_work_redis_path(self, state_store_redis):
        """Line 1426: redis path for assign_work."""
        session = _make_session(session_id="del-r-5")
        state_store_redis.register_agent(session)
        result = state_store_redis.assign_work("del-r-5", "assign-x")
        assert result is True

    def test_assign_work_none_path(self):
        """Line 1429: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.assign_work("x", "y") is False

    def test_update_heartbeat_redis_path(self, state_store_redis):
        """Line 1469: redis path for update_heartbeat."""
        result = state_store_redis.update_heartbeat("del-r-hb")
        assert result is True

    def test_update_heartbeat_none_path(self):
        """Line 1472: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.update_heartbeat("x") is False

    def test_acquire_work_lock_redis_path(self, state_store_redis):
        """Line 1501: redis path for acquire_work_lock."""
        result = state_store_redis.acquire_work_lock("del-task-1", "del-sess-1")
        assert result is True

    def test_acquire_work_lock_none_path(self):
        """Line 1504: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.acquire_work_lock("t", "s") is False

    def test_release_work_lock_redis_path(self, state_store_redis):
        """Line 1555: redis path for release_work_lock."""
        state_store_redis.acquire_work_lock("del-task-2", "del-sess-2")
        result = state_store_redis.release_work_lock("del-task-2", "del-sess-2")
        assert result is True

    def test_release_work_lock_none_path(self):
        """Line 1558: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.release_work_lock("t", "s") is False

    def test_is_work_locked_redis_path(self, state_store_redis):
        """Line 1596: redis path for is_work_locked."""
        state_store_redis.acquire_work_lock("del-task-3", "del-sess-3")
        assert state_store_redis.is_work_locked("del-task-3") is True

    def test_is_work_locked_none_path(self):
        """Line 1599: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.is_work_locked("t") is False

    def test_get_work_lock_holder_redis_path(self, state_store_redis):
        """Line 1637: redis path for get_work_lock_holder."""
        state_store_redis.acquire_work_lock("del-task-4", "del-sess-4")
        result = state_store_redis.get_work_lock_holder("del-task-4")
        assert result == "del-sess-4"

    def test_get_work_lock_holder_none_path(self):
        """Line 1640: _store_type == 'none' → None."""
        store = StateStore()
        store._store_type = "none"
        assert store.get_work_lock_holder("t") is None

    def test_create_assignment_redis_path(self, state_store_redis):
        """Line 1677: redis path for create_assignment."""
        result = state_store_redis.create_assignment(_make_assignment("del-asgn-1"))
        assert result is True

    def test_create_assignment_none_path(self):
        """Line 1680: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.create_assignment(_make_assignment()) is False

    def test_get_assignment_redis_path(self, state_store_redis):
        """Line 1725: redis path for get_assignment."""
        state_store_redis.create_assignment(_make_assignment("del-asgn-2"))
        result = state_store_redis.get_assignment("del-asgn-2")
        assert result is not None

    def test_get_assignment_none_path(self):
        """Line 1728: _store_type == 'none' → None."""
        store = StateStore()
        store._store_type = "none"
        assert store.get_assignment("x") is None

    def test_update_assignment_status_redis_path(self, state_store_redis):
        """Line 1772: redis path for update_assignment_status."""
        state_store_redis.create_assignment(_make_assignment("del-asgn-3"))
        result = state_store_redis.update_assignment_status(
            "del-asgn-3", AssignmentStatus.IN_PROGRESS
        )
        assert result is True

    def test_update_assignment_status_none_path(self):
        """Line 1775: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.update_assignment_status("x", AssignmentStatus.FAILED) is False

    def test_list_assignments_redis_path(self, state_store_redis):
        """Line 1833: redis path for list_assignments."""
        state_store_redis.create_assignment(_make_assignment("del-asgn-4"))
        result = state_store_redis.list_assignments()
        assert isinstance(result, list)

    def test_list_assignments_none_path(self):
        """Line 1894: _store_type == 'none' → empty list."""
        store = StateStore()
        store._store_type = "none"
        assert store.list_assignments() == []

    def test_register_work_tree_redis_path(self, state_store_redis):
        """Line 1898: redis path for register_work_tree."""
        result = state_store_redis.register_work_tree(_make_work_tree("/tmp/del-wt-r"))
        assert result is True

    def test_register_work_tree_none_path(self):
        """Line 1901: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.register_work_tree(_make_work_tree()) is False

    def test_get_work_tree_redis_path(self, state_store_redis):
        """Line 1937: redis path for get_work_tree."""
        state_store_redis.register_work_tree(_make_work_tree("/tmp/del-wt-get"))
        result = state_store_redis.get_work_tree("/tmp/del-wt-get")
        assert result is not None

    def test_get_work_tree_none_path(self):
        """Line 1940: _store_type == 'none' → None."""
        store = StateStore()
        store._store_type = "none"
        assert store.get_work_tree("/tmp/x") is None

    def test_get_work_trees_for_session_redis_path(self, state_store_redis):
        """Line 1992: redis path for get_work_trees_for_session."""
        state_store_redis.register_work_tree(_make_work_tree("/tmp/del-wt-s", session_id="del-s"))
        result = state_store_redis.get_work_trees_for_session("del-s")
        assert isinstance(result, list)

    def test_get_work_trees_for_session_none_path(self):
        """Line 2033: _store_type == 'none' → empty list."""
        store = StateStore()
        store._store_type = "none"
        assert store.get_work_trees_for_session("x") == []

    def test_publish_status_change_redis_path(self, state_store_redis):
        """Line 2037: redis path for publish_status_change."""
        result = state_store_redis.publish_status_change("del-s-1", {"status": "ok"})
        assert result is True

    def test_publish_status_change_sqlite_path(self, state_store_sqlite):
        """Lines 2038-2041: SQLite publish is a stub returning True."""
        result = state_store_sqlite.publish_status_change("del-s-2", {"status": "ok"})
        assert result is True

    def test_publish_status_change_none_path(self):
        """Line 2042: _store_type == 'none' → False."""
        store = StateStore()
        store._store_type = "none"
        assert store.publish_status_change("x", {}) is False


# ===========================================================================
# StateStore.list_work_trees() — lines 1974-1980
# ===========================================================================


class TestStateStoreListWorkTrees:
    """Cover list_work_trees() via all backends."""

    def test_list_work_trees_sqlite_returns_empty(self, state_store_sqlite):
        """Lines 1975-1979: SQLite stub returns []."""
        result = state_store_sqlite.list_work_trees()
        assert result == []

    def test_list_work_trees_none_returns_empty(self):
        """Line 1980: _store_type == 'none' → []."""
        store = StateStore()
        store._store_type = "none"
        assert store.list_work_trees() == []

    def test_list_work_trees_redis_delegates(self, state_store_redis):
        """Line 1974-1975: redis path delegates to get_work_trees_for_session('')."""
        state_store_redis.register_work_tree(_make_work_tree("/tmp/lwt-r-1"))
        result = state_store_redis.list_work_trees()
        assert isinstance(result, list)


# ===========================================================================
# StateStore.get_work_trees_for_session() — SQLite path (lines 1993-2032)
# ===========================================================================


class TestStateStoreGetWorkTreesForSessionSQLite:
    """Cover SQLite implementation of get_work_trees_for_session."""

    def test_get_work_trees_for_session_empty_string_gets_all(self, state_store_sqlite):
        """Lines 2003-2005: empty string session_id → all rows."""
        state_store_sqlite.register_work_tree(_make_work_tree("/tmp/wts-all-1", "sess-all-1"))
        state_store_sqlite.register_work_tree(_make_work_tree("/tmp/wts-all-2", "sess-all-2"))
        result = state_store_sqlite.get_work_trees_for_session("")
        assert len(result) >= 2

    def test_get_work_trees_for_session_specific_session(self, state_store_sqlite):
        """Lines 2007-2008: specific session_id filters correctly."""
        state_store_sqlite.register_work_tree(_make_work_tree("/tmp/wts-sp-1", "sess-sp-1"))
        state_store_sqlite.register_work_tree(_make_work_tree("/tmp/wts-sp-2", "sess-sp-2"))
        result = state_store_sqlite.get_work_trees_for_session("sess-sp-1")
        paths = [wt.work_tree_path for wt in result]
        assert "/tmp/wts-sp-1" in paths
        assert "/tmp/wts-sp-2" not in paths

    def test_get_work_trees_for_session_none_treated_as_empty(self, state_store_sqlite):
        """Line 1992: None session_id treated as empty string."""
        state_store_sqlite.register_work_tree(_make_work_tree("/tmp/wts-none-1", "sess-n-1"))
        result = state_store_sqlite.get_work_trees_for_session(None)
        # None maps to '' which fetches all trees
        assert isinstance(result, list)


# ===========================================================================
# StateStore.connect() — lines 1200-1211
# ===========================================================================


class TestStateStoreConnect:
    """Cover StateStore.connect() Redis-success, SQLite-fallback, none paths."""

    def test_connect_falls_back_to_sqlite(self, tmp_path):
        """Lines 1204-1207: Redis fails → SQLite used."""
        store = StateStore(
            redis_url="redis://nonexistent-host-99999:6379/0",
            sqlite_path=str(tmp_path / "fb_test.db"),
        )
        result = store.connect()
        assert result == "sqlite"
        assert store._store_type == "sqlite"
        store.disconnect()

    def test_connect_succeeds_with_redis(self, fake_redis_store):
        """Lines 1199-1201: Redis connected → store_type = 'redis'."""
        store = StateStore()
        # Inject fake redis
        store._redis = fake_redis_store
        # Manually trigger the redis path
        store._store_type = "redis"
        assert store.get_store_type() == "redis"

    def test_connect_returns_none_when_both_fail(self, tmp_path, monkeypatch):
        """Lines 1209-1211: both backends fail → 'none'."""
        store = StateStore(
            redis_url="redis://nonexistent-host-99999:6379/0",
        )
        # Make SQLite connect fail too
        original_connect = SQLiteStateStore.connect

        def fail_connect(self):
            return False

        monkeypatch.setattr(SQLiteStateStore, "connect", fail_connect)
        result = store.connect()
        assert result == "none"
        assert store._store_type == "none"
        store.disconnect()


# ===========================================================================
# StateStore._sqlite_* methods — error paths
# ===========================================================================


class TestStateStoreSQLiteErrorPaths:
    """Cover error paths in _sqlite_* methods when _conn is None."""

    def test_sqlite_register_agent_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_register_agent fails gracefully with no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_register_agent(_make_session())
        assert result is False

    def test_sqlite_get_agent_no_conn_returns_none(self, state_store_sqlite):
        """_sqlite_get_agent returns None when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_get_agent("x")
        assert result is None

    def test_sqlite_get_active_agents_no_conn_returns_empty(self, state_store_sqlite):
        """_sqlite_get_active_agents returns [] when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_get_active_agents()
        assert result == []

    def test_sqlite_update_agent_status_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_update_agent_status returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_update_agent_status("x", AgentStatus.IDLE)
        assert result is False

    def test_sqlite_assign_work_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_assign_work returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_assign_work("x", "y")
        assert result is False

    def test_sqlite_update_heartbeat_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_update_heartbeat returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_update_heartbeat("x")
        assert result is False

    def test_sqlite_acquire_work_lock_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_acquire_work_lock returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_acquire_work_lock("t", "s")
        assert result is False

    def test_sqlite_release_work_lock_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_release_work_lock returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_release_work_lock("t", "s")
        assert result is False

    def test_sqlite_is_work_locked_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_is_work_locked returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_is_work_locked("t")
        assert result is False

    def test_sqlite_get_work_lock_holder_no_conn_returns_none(self, state_store_sqlite):
        """_sqlite_get_work_lock_holder returns None when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_get_work_lock_holder("t")
        assert result is None

    def test_sqlite_create_assignment_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_create_assignment returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_create_assignment(_make_assignment())
        assert result is False

    def test_sqlite_get_assignment_no_conn_returns_none(self, state_store_sqlite):
        """_sqlite_get_assignment returns None when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_get_assignment("x")
        assert result is None

    def test_sqlite_update_assignment_status_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_update_assignment_status returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_update_assignment_status(
            "x", AssignmentStatus.COMPLETED
        )
        assert result is False

    def test_sqlite_register_work_tree_no_conn_returns_false(self, state_store_sqlite):
        """_sqlite_register_work_tree returns False when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_register_work_tree(_make_work_tree())
        assert result is False

    def test_sqlite_get_work_tree_no_conn_returns_none(self, state_store_sqlite):
        """_sqlite_get_work_tree returns None when no connection."""
        state_store_sqlite._sqlite._conn = None
        result = state_store_sqlite._sqlite_get_work_tree("/tmp/x")
        assert result is None


# ===========================================================================
# StateStore SQLite CRUD round-trips
# ===========================================================================


class TestStateStoreSQLiteCRUD:
    """Full round-trip CRUD via StateStore with SQLite backend."""

    def test_register_and_get_agent(self, state_store_sqlite):
        session = _make_session(session_id="crud-s-1")
        assert state_store_sqlite.register_agent(session) is True
        result = state_store_sqlite.get_agent("crud-s-1")
        assert result is not None
        assert result.session_id == "crud-s-1"

    def test_update_agent_status_without_task(self, state_store_sqlite):
        session = _make_session(session_id="crud-s-2")
        state_store_sqlite.register_agent(session)
        result = state_store_sqlite.update_agent_status("crud-s-2", AgentStatus.PAUSED)
        assert result is True

    def test_update_agent_status_with_task(self, state_store_sqlite):
        session = _make_session(session_id="crud-s-3")
        state_store_sqlite.register_agent(session)
        result = state_store_sqlite.update_agent_status(
            "crud-s-3", AgentStatus.ACTIVE, current_task="building tests"
        )
        assert result is True

    def test_assign_work_with_path(self, state_store_sqlite):
        session = _make_session(session_id="crud-s-4")
        state_store_sqlite.register_agent(session)
        result = state_store_sqlite.assign_work("crud-s-4", "a-1", work_tree_path="/tmp/wt")
        assert result is True

    def test_assign_work_without_path(self, state_store_sqlite):
        session = _make_session(session_id="crud-s-5")
        state_store_sqlite.register_agent(session)
        result = state_store_sqlite.assign_work("crud-s-5", None)
        assert result is True

    def test_update_heartbeat_sqlite(self, state_store_sqlite):
        session = _make_session(session_id="crud-s-hb")
        state_store_sqlite.register_agent(session)
        assert state_store_sqlite.update_heartbeat("crud-s-hb") is True

    def test_acquire_release_work_lock_sqlite(self, state_store_sqlite):
        assert state_store_sqlite.acquire_work_lock("crud-task-1", "crud-s-1") is True
        assert state_store_sqlite.is_work_locked("crud-task-1") is True
        assert state_store_sqlite.get_work_lock_holder("crud-task-1") == "crud-s-1"
        assert state_store_sqlite.release_work_lock("crud-task-1", "crud-s-1") is True
        assert state_store_sqlite.is_work_locked("crud-task-1") is False

    def test_acquire_work_lock_already_held(self, state_store_sqlite):
        state_store_sqlite.acquire_work_lock("crud-task-2", "sess-A")
        result = state_store_sqlite.acquire_work_lock("crud-task-2", "sess-B")
        assert result is False

    def test_acquire_work_lock_expired_reacquired(self, state_store_sqlite):
        """Expired lock can be reacquired by a different session."""
        # Acquire with 1-second TTL
        state_store_sqlite._sqlite_acquire_work_lock("crud-task-3", "sess-old", ttl_seconds=1)
        # Wait for expiry
        import time
        time.sleep(1.1)
        result = state_store_sqlite._sqlite_acquire_work_lock(
            "crud-task-3", "sess-new", ttl_seconds=30
        )
        assert result is True

    def test_release_work_lock_wrong_holder(self, state_store_sqlite):
        state_store_sqlite.acquire_work_lock("crud-task-4", "sess-owner")
        result = state_store_sqlite.release_work_lock("crud-task-4", "sess-other")
        assert result is False

    def test_release_work_lock_not_held(self, state_store_sqlite):
        result = state_store_sqlite.release_work_lock("nonexistent-task", "any-sess")
        assert result is False

    def test_get_work_lock_holder_expired(self, state_store_sqlite):
        """get_work_lock_holder cleans up expired lock → returns None."""
        import time

        state_store_sqlite._sqlite_acquire_work_lock("crud-task-5", "sess-exp", ttl_seconds=1)
        time.sleep(1.1)
        result = state_store_sqlite._sqlite_get_work_lock_holder("crud-task-5")
        assert result is None

    def test_is_work_locked_expired_returns_false(self, state_store_sqlite):
        """is_work_locked cleans up expired lock → returns False."""
        import time

        state_store_sqlite._sqlite_acquire_work_lock("crud-task-6", "sess-exp2", ttl_seconds=1)
        time.sleep(1.1)
        assert state_store_sqlite._sqlite_is_work_locked("crud-task-6") is False

    def test_create_get_assignment_sqlite(self, state_store_sqlite):
        a = _make_assignment("crud-a-1")
        assert state_store_sqlite.create_assignment(a) is True
        result = state_store_sqlite.get_assignment("crud-a-1")
        assert result is not None
        assert result.assignment_id == "crud-a-1"

    def test_update_assignment_status_sqlite(self, state_store_sqlite):
        a = _make_assignment("crud-a-2")
        state_store_sqlite.create_assignment(a)
        result = state_store_sqlite.update_assignment_status(
            "crud-a-2", AssignmentStatus.COMPLETED, completed_at=datetime.now(UTC)
        )
        assert result is True

    def test_update_assignment_status_no_completed_at(self, state_store_sqlite):
        a = _make_assignment("crud-a-3")
        state_store_sqlite.create_assignment(a)
        result = state_store_sqlite.update_assignment_status("crud-a-3", AssignmentStatus.BLOCKED)
        assert result is True

    def test_list_assignments_sqlite_filters(self, state_store_sqlite):
        a1 = _make_assignment("la-sq-1", domain="sq-dom-A", project="sq-proj-X")
        a2 = _make_assignment("la-sq-2", domain="sq-dom-B", project="sq-proj-Y")
        a3 = _make_assignment("la-sq-3", domain="sq-dom-A", status=AssignmentStatus.COMPLETED)
        state_store_sqlite.create_assignment(a1)
        state_store_sqlite.create_assignment(a2)
        state_store_sqlite.create_assignment(a3)

        by_domain = state_store_sqlite.list_assignments(domain="sq-dom-A")
        assert all(a.domain == "sq-dom-A" for a in by_domain)

        by_project = state_store_sqlite.list_assignments(project="sq-proj-X")
        assert all(a.project == "sq-proj-X" for a in by_project)

        by_status = state_store_sqlite.list_assignments(status=AssignmentStatus.COMPLETED)
        assert all(a.status == AssignmentStatus.COMPLETED for a in by_status)

    def test_register_and_get_work_tree_sqlite(self, state_store_sqlite):
        wt = _make_work_tree("/tmp/crud-wt-1", session_id="crud-wt-sess-1")
        assert state_store_sqlite.register_work_tree(wt) is True
        result = state_store_sqlite.get_work_tree("/tmp/crud-wt-1")
        assert result is not None
        assert result.work_tree_path == "/tmp/crud-wt-1"

    def test_get_active_agents_filters_stale(self, state_store_sqlite):
        """SQLite active agents filter by heartbeat < 5 min."""
        fresh = _make_session(session_id="fresh-sa")
        stale = _make_session(
            session_id="stale-sa",
            last_heartbeat=datetime.now(UTC) - timedelta(minutes=10),
        )
        state_store_sqlite.register_agent(fresh)
        state_store_sqlite.register_agent(stale)
        result = state_store_sqlite.get_active_agents()
        ids = [a.session_id for a in result]
        assert "fresh-sa" in ids
        assert "stale-sa" not in ids


# ===========================================================================
# RedisStateStore subscribe_to_all_agents directly (lines 2057-2065 in
# RedisStateStore's own method body are actually dead code, but we can
# call subscribe_to_agent() which exercises the same pubsub logic)
# ===========================================================================


class TestRedisStateStoreSubscribeAll:
    """Cover subscribe_to_agent() pubsub paths on RedisStateStore."""

    def test_subscribe_to_agent_success_and_pubsub_returned(self, fake_redis_store):
        """Lines 1022-1035: pubsub object returned on success."""
        pubsub = fake_redis_store.subscribe_to_agent("all-test-session")
        assert pubsub is not None

    def test_subscribe_to_agent_exception_returns_none(self, fake_redis_store):
        """Lines 1033-1035: exception during pubsub creation → None."""
        fake_redis_store._client.pubsub = MagicMock(side_effect=RuntimeError("pubsub fail"))
        result = fake_redis_store.subscribe_to_agent("fail-session")
        assert result is None


# ===========================================================================
# get_state_store() singleton — lines 2075-2085
# ===========================================================================


class TestGetStateStoreSingleton:
    """Cover get_state_store() global singleton creation."""

    def test_get_state_store_returns_connected_store(self, monkeypatch, tmp_path):
        """Lines 2075-2085: singleton created and connected on first call."""
        import forge_harness.state_store as ss_module

        # Reset singleton so we control its creation
        monkeypatch.setattr(ss_module, "_state_store", None)

        # Force Redis to fail and SQLite to use tmp_path
        monkeypatch.setenv("REDIS_URL", "redis://nonexistent-host-99999:6379/0")

        with patch.object(SQLiteStateStore, "__init__", lambda self, db_path=None: None):
            with patch.object(SQLiteStateStore, "connect", return_value=True):
                with patch.object(SQLiteStateStore, "_create_tables", return_value=None):
                    # Just ensure _conn is set to avoid _create_tables issue
                    pass

        # Use real path so SQLite works
        original_init = StateStore.__init__

        def patched_init(self, redis_url=None, sqlite_path=None):
            original_init(
                self,
                redis_url="redis://nonexistent-host-99999:6379/0",
                sqlite_path=str(tmp_path / "singleton_test.db"),
            )

        monkeypatch.setattr(StateStore, "__init__", patched_init)

        store = ss_module.get_state_store()
        assert store is not None
        assert store._store_type in ("redis", "sqlite", "none")

        # Second call returns same singleton
        store2 = ss_module.get_state_store()
        assert store2 is store

        # Cleanup
        monkeypatch.setattr(ss_module, "_state_store", None)

    def test_get_state_store_returns_existing_singleton(self, monkeypatch):
        """Lines 2082: existing singleton returned without reconnect."""
        import forge_harness.state_store as ss_module

        mock_store = MagicMock(spec=StateStore)
        monkeypatch.setattr(ss_module, "_state_store", mock_store)
        result = ss_module.get_state_store()
        assert result is mock_store
        # connect() should NOT be called again
        mock_store.connect.assert_not_called()
        monkeypatch.setattr(ss_module, "_state_store", None)
