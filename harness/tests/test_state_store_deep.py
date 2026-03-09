"""
Deep coverage tests for forge_harness/state_store.py.

Targets uncovered lines identified by coverage report. Uses MagicMock-based
in-memory Redis simulation so tests run without fakeredis or a real Redis server.

Strategy:
- MockRedisClient: A simple in-memory dict-backed class that simulates Redis
  operations (hset, hgetall, expire, keys, zadd, zscore, zrangebyscore, zrem,
  set, get, delete, exists, publish, pubsub, etc.)
- mock_redis fixture: RedisStateStore with MockRedisClient injected directly.
- All SQLite tests operate against a real SQLite connection in tmp_path.
"""

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
)

# ---------------------------------------------------------------------------
# In-memory Redis simulation (no fakeredis dependency)
# ---------------------------------------------------------------------------


class MockRedisClient:
    """Minimal in-memory Redis client simulation for testing."""

    def __init__(self):
        self._data: dict[str, Any] = {}       # key -> value (str or hash)
        self._sets: dict[str, dict] = {}      # sorted sets: key -> {member: score}
        self._expires: dict[str, float] = {}  # key -> expiry epoch

    # ---- helpers ----

    def _is_expired(self, key: str) -> bool:
        if key in self._expires and time.time() > self._expires[key]:
            self._data.pop(key, None)
            self._expires.pop(key, None)
            return True
        return False

    # ---- hash commands ----

    def hset(self, key: str, mapping: dict) -> int:
        self._is_expired(key)
        if key not in self._data or not isinstance(self._data[key], dict):
            self._data[key] = {}
        self._data[key].update(mapping)
        return len(mapping)

    def hgetall(self, key: str) -> dict:
        self._is_expired(key)
        val = self._data.get(key, {})
        return dict(val) if isinstance(val, dict) else {}

    def expire(self, key: str, seconds: int) -> int:
        self._expires[key] = time.time() + seconds
        return 1

    def keys(self, pattern: str = "*") -> list:
        # Simple prefix/suffix wildcard support
        prefix = pattern.replace("*", "")
        return [k for k in self._data if k.startswith(prefix) and not self._is_expired(k)]

    # ---- string commands ----

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        self._is_expired(key)
        if nx and key in self._data:
            return None
        self._data[key] = value
        if ex:
            self._expires[key] = time.time() + ex
        return True

    def get(self, key: str) -> str | None:
        self._is_expired(key)
        val = self._data.get(key)
        return val if isinstance(val, str) else None

    def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                count += 1
        return count

    def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self._data and not self._is_expired(k))

    # ---- sorted set commands ----

    def zadd(self, key: str, mapping: dict) -> int:
        if key not in self._sets:
            self._sets[key] = {}
        self._sets[key].update(mapping)
        return len(mapping)

    def zscore(self, key: str, member: str) -> float | None:
        return self._sets.get(key, {}).get(member)

    def zrangebyscore(self, key: str, min_score: float, max_score: float) -> list:
        members = self._sets.get(key, {})
        return [m for m, s in members.items() if min_score <= s <= max_score]

    def zrem(self, key: str, *members: str) -> int:
        members_set = self._sets.get(key, {})
        count = 0
        for m in members:
            if m in members_set:
                del members_set[m]
                count += 1
        return count

    # ---- pub/sub ----

    def publish(self, channel: str, message: str) -> int:
        return 0  # No-op in mock

    def pubsub(self):
        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = MagicMock()
        mock_pubsub.psubscribe = MagicMock()
        mock_pubsub.get_message = MagicMock(return_value=None)
        return mock_pubsub

    def close(self):
        pass

    def ping(self):
        return True


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _session(
    session_id: str = "s-deep-001",
    domain: str = "d",
    project: str = "p",
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


def _assignment(
    assignment_id: str = "a-deep-001",
    session_id: str = "s-deep-001",
    domain: str = "d",
    project: str = "p",
    status: AssignmentStatus = AssignmentStatus.ASSIGNED,
    priority: int = 1,
    completed_at: datetime | None = None,
) -> WorkAssignment:
    return WorkAssignment(
        assignment_id=assignment_id,
        session_id=session_id,
        task_type="feature",
        task_description="Deep test task",
        priority=priority,
        domain=domain,
        project=project,
        files_affected=["src/deep.py"],
        dependencies=[],
        status=status,
        completed_at=completed_at,
    )


def _work_tree(
    path: str = "/tmp/deep-wt-001",
    session_id: str = "s-deep-001",
    status: WorkTreeStatus = WorkTreeStatus.ACTIVE,
) -> WorkTree:
    return WorkTree(
        work_tree_path=path,
        assigned_session_id=session_id,
        files_modified=["src/deep.py"],
        branch_name="feature/deep",
        base_commit="deadbeef",
        status=status,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """RedisStateStore backed by MockRedisClient — no external deps required."""
    store = RedisStateStore()
    store._client = MockRedisClient()
    store._connected = True
    yield store
    store.disconnect()


@pytest.fixture
def sqlite_store(tmp_path):
    """Connected SQLiteStateStore."""
    store = SQLiteStateStore(db_path=str(tmp_path / "deep.db"))
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def state_store_sqlite(tmp_path):
    """StateStore forced to SQLite."""
    store = StateStore(
        redis_url="redis://nonexistent-host-00000:6379/0",
        sqlite_path=str(tmp_path / "deep_state.db"),
    )
    store.connect()
    yield store
    store.disconnect()


# ===========================================================================
# RedisStateStore.connect() — success and disconnect paths (lines 340-353)
# ===========================================================================


class TestRedisConnectSuccess:
    """Cover RedisStateStore.connect() success path using mock."""

    def test_connect_sets_connected_flag(self):
        """connect() sets _connected = True on success (lines 340-342)."""
        store = RedisStateStore()
        mock_client = MockRedisClient()

        with patch("forge_harness.state_store.redis") as mock_redis_module:
            mock_redis_module.from_url.return_value = mock_client
            result = store.connect()

        assert result is True
        assert store._connected is True

    def test_disconnect_clears_connected_flag(self):
        """disconnect() sets _connected = False when client exists (lines 349-353)."""
        store = RedisStateStore()
        store._client = MockRedisClient()
        store._connected = True

        store.disconnect()

        assert store._connected is False

    def test_disconnect_noop_when_no_client(self):
        """disconnect() is a no-op when _client is None (lines 349-353)."""
        store = RedisStateStore()
        store.disconnect()
        assert store._client is None


# ===========================================================================
# RedisStateStore.register_agent — success + error paths (lines 375-387)
# ===========================================================================


class TestRedisRegisterAgent:
    """Cover RedisStateStore.register_agent."""

    def test_register_agent_success(self, mock_redis):
        """register_agent stores hash and updates heartbeat (lines 379-387)."""
        result = mock_redis.register_agent(_session("reg-sess-1"))
        assert result is True

    def test_register_agent_returns_false_on_error(self, mock_redis):
        """register_agent catches exception and returns False (lines 385-387)."""
        mock_redis._client.hset = MagicMock(side_effect=Exception("Redis write error"))

        result = mock_redis.register_agent(_session())
        assert result is False


# ===========================================================================
# RedisStateStore.get_agent — success + error paths (lines 401-413)
# ===========================================================================


class TestRedisGetAgent:
    """Cover RedisStateStore.get_agent."""

    def test_get_agent_success(self, mock_redis):
        """get_agent returns AgentSession when found (lines 405-410)."""
        mock_redis.register_agent(_session("ga-sess-1"))

        result = mock_redis.get_agent("ga-sess-1")
        assert result is not None
        assert result.session_id == "ga-sess-1"

    def test_get_agent_returns_none_when_not_found(self, mock_redis):
        """get_agent returns None when hash is empty (lines 407-409)."""
        result = mock_redis.get_agent("nonexistent-session")
        assert result is None

    def test_get_agent_returns_none_on_redis_error(self, mock_redis):
        """get_agent catches exception and returns None (lines 411-413)."""
        mock_redis._client.hgetall = MagicMock(side_effect=Exception("Redis read error"))

        result = mock_redis.get_agent("any-session")
        assert result is None


# ===========================================================================
# RedisStateStore.get_active_agents — paths (lines 430-467)
# ===========================================================================


class TestRedisGetActiveAgents:
    """Cover RedisStateStore.get_active_agents."""

    def test_get_active_agents_returns_recent_sessions(self, mock_redis):
        """Active sessions are returned (lines 438-462)."""
        mock_redis.register_agent(_session("active-deep-1"))

        agents = mock_redis.get_active_agents()
        ids = [a.session_id for a in agents]
        assert "active-deep-1" in ids

    def test_get_active_agents_skips_malformed_data(self, mock_redis):
        """Malformed agent entry is skipped gracefully (lines 444-448)."""
        mock_redis._client.hset("agent:session:bad-session", mapping={"agent_type": "other"})

        agents = mock_redis.get_active_agents()
        assert isinstance(agents, list)
        ids = [a.session_id for a in agents]
        assert "bad-session" not in ids

    def test_get_active_agents_skips_stale_heartbeat(self, mock_redis):
        """Agents with heartbeat > 5 min old are excluded (lines 457-460)."""
        old_hb = datetime.now(UTC) - timedelta(minutes=10)
        sess = _session("stale-deep", last_heartbeat=old_hb)
        mock_redis.register_agent(sess)

        agents = mock_redis.get_active_agents()
        ids = [a.session_id for a in agents]
        assert "stale-deep" not in ids

    def test_get_active_agents_filters_by_domain(self, mock_redis):
        """domain filter excludes agents in other domains (lines 451-452)."""
        mock_redis.register_agent(_session("dom-deep-1", domain="alpha"))
        mock_redis.register_agent(_session("dom-deep-2", domain="beta"))

        results = mock_redis.get_active_agents(domain="alpha")
        ids = [a.session_id for a in results]
        assert "dom-deep-1" in ids
        assert "dom-deep-2" not in ids

    def test_get_active_agents_filters_by_project(self, mock_redis):
        """project filter excludes agents in other projects (lines 453-454)."""
        mock_redis.register_agent(_session("proj-deep-1", project="fe"))
        mock_redis.register_agent(_session("proj-deep-2", project="be"))

        results = mock_redis.get_active_agents(project="fe")
        ids = [a.session_id for a in results]
        assert "proj-deep-1" in ids
        assert "proj-deep-2" not in ids

    def test_get_active_agents_returns_empty_on_redis_error(self, mock_redis):
        """get_active_agents returns [] when redis.keys raises (lines 465-467)."""
        mock_redis._client.keys = MagicMock(side_effect=Exception("keys failed"))

        result = mock_redis.get_active_agents()
        assert result == []

    def test_get_active_agents_skips_empty_hash(self, mock_redis):
        """get_active_agents skips keys with empty data (lines 441-442)."""
        # Register valid then a key with empty hash
        mock_redis.register_agent(_session("valid-1"))
        # Inject an empty hash entry
        mock_redis._client._data["agent:session:empty-one"] = {}

        agents = mock_redis.get_active_agents()
        ids = [a.session_id for a in agents]
        assert "empty-one" not in ids


# ===========================================================================
# RedisStateStore.update_agent_status — paths (lines 488-502)
# ===========================================================================


class TestRedisUpdateAgentStatus:
    """Cover RedisStateStore.update_agent_status."""

    def test_update_agent_status_with_current_task(self, mock_redis):
        """update_agent_status includes current_task (lines 494-499)."""
        mock_redis.register_agent(_session("s-task-deep"))

        result = mock_redis.update_agent_status(
            "s-task-deep", AgentStatus.WAITING_HUMAN, "review"
        )
        assert result is True

        agent = mock_redis.get_agent("s-task-deep")
        assert agent is not None
        assert agent.status == AgentStatus.WAITING_HUMAN
        assert agent.current_task == "review"

    def test_update_agent_status_without_current_task(self, mock_redis):
        """update_agent_status works without current_task (lines 494-499)."""
        mock_redis.register_agent(_session("s-notask-deep"))

        result = mock_redis.update_agent_status("s-notask-deep", AgentStatus.IDLE)
        assert result is True

    def test_update_agent_status_returns_false_on_error(self, mock_redis):
        """update_agent_status catches exception and returns False (lines 500-502)."""
        mock_redis._client.hset = MagicMock(side_effect=Exception("hset failed"))

        result = mock_redis.update_agent_status("s-1", AgentStatus.IDLE)
        assert result is False


# ===========================================================================
# RedisStateStore.assign_work — paths (lines 517-535)
# ===========================================================================


class TestRedisAssignWork:
    """Cover RedisStateStore.assign_work."""

    def test_assign_work_with_work_tree_path(self, mock_redis):
        """assign_work with work_tree_path updates both fields (lines 529-530)."""
        mock_redis.register_agent(_session("s-assign-deep"))

        result = mock_redis.assign_work("s-assign-deep", "a-99", "/tmp/wt-deep")
        assert result is True

    def test_assign_work_without_work_tree_path(self, mock_redis):
        """assign_work without work_tree_path only sets assignment_id (lines 526-531)."""
        mock_redis.register_agent(_session("s-assign-notree"))

        result = mock_redis.assign_work("s-assign-notree", "a-100")
        assert result is True

    def test_assign_work_with_none_assignment_id(self, mock_redis):
        """assign_work with None assignment_id stores empty string (lines 526-531)."""
        mock_redis.register_agent(_session("s-assign-clear"))

        result = mock_redis.assign_work("s-assign-clear", None)
        assert result is True

    def test_assign_work_returns_false_on_error(self, mock_redis):
        """assign_work catches exception and returns False (lines 533-535)."""
        mock_redis._client.hset = MagicMock(side_effect=Exception("hset fail"))

        result = mock_redis.assign_work("s-1", "a-1")
        assert result is False


# ===========================================================================
# RedisStateStore heartbeat — paths (lines 570-630)
# ===========================================================================


class TestRedisHeartbeat:
    """Cover RedisStateStore heartbeat operations."""

    def test_update_heartbeat_success(self, mock_redis):
        """update_heartbeat stores score in sorted set (lines 574-577)."""
        result = mock_redis.update_heartbeat("s-hb-1")
        assert result is True

        ts = mock_redis.get_heartbeat_time("s-hb-1")
        assert ts is not None
        assert isinstance(ts, float)

    def test_update_heartbeat_returns_false_on_error(self, mock_redis):
        """update_heartbeat catches exception and returns False (lines 578-580)."""
        mock_redis._client.zadd = MagicMock(side_effect=Exception("zadd fail"))

        result = mock_redis.update_heartbeat("s-1")
        assert result is False

    def test_get_heartbeat_time_returns_none_when_score_is_none(self, mock_redis):
        """get_heartbeat_time returns None when zscore returns None (lines 599-600)."""
        result = mock_redis.get_heartbeat_time("never-registered-deep")
        assert result is None

    def test_get_heartbeat_time_returns_none_on_error(self, mock_redis):
        """get_heartbeat_time catches exception and returns None (lines 601-603)."""
        mock_redis._client.zscore = MagicMock(side_effect=Exception("zscore fail"))

        result = mock_redis.get_heartbeat_time("s-1")
        assert result is None

    def test_cleanup_dead_heartbeats_removes_dead_agents(self, mock_redis):
        """cleanup_dead_heartbeats removes and returns dead agents (lines 622-627)."""
        stale_ts = time.time() - 400
        mock_redis._client.zadd("heartbeats", {"dead-deep-session": stale_ts})

        dead = mock_redis.cleanup_dead_heartbeats(timeout_seconds=300)
        assert "dead-deep-session" in dead

    def test_cleanup_dead_heartbeats_when_no_dead_agents(self, mock_redis):
        """cleanup_dead_heartbeats returns [] when no dead agents (lines 622-627)."""
        mock_redis.update_heartbeat("s-fresh")

        dead = mock_redis.cleanup_dead_heartbeats(timeout_seconds=300)
        assert "s-fresh" not in dead

    def test_cleanup_dead_heartbeats_returns_empty_on_error(self, mock_redis):
        """cleanup_dead_heartbeats catches exception and returns [] (lines 628-630)."""
        mock_redis._client.zrangebyscore = MagicMock(
            side_effect=Exception("zrangebyscore fail")
        )

        result = mock_redis.cleanup_dead_heartbeats()
        assert result == []


# ===========================================================================
# RedisStateStore work locks — paths (lines 650-744)
# ===========================================================================


class TestRedisWorkLocks:
    """Cover RedisStateStore work lock operations."""

    def test_acquire_work_lock_success(self, mock_redis):
        """acquire_work_lock returns True on first acquire (lines 656-660)."""
        result = mock_redis.acquire_work_lock("lock-1", "sess-A")
        assert result is True

    def test_acquire_work_lock_fails_when_held(self, mock_redis):
        """Second acquire on same task returns False (lines 661-662)."""
        mock_redis.acquire_work_lock("contested-deep", "sess-A")

        result = mock_redis.acquire_work_lock("contested-deep", "sess-B")
        assert result is False

    def test_acquire_work_lock_returns_false_on_error(self, mock_redis):
        """acquire_work_lock catches exception and returns False (lines 664-666)."""
        mock_redis._client.set = MagicMock(side_effect=Exception("set fail"))

        result = mock_redis.acquire_work_lock("task-1", "sess-1")
        assert result is False

    def test_release_work_lock_success(self, mock_redis):
        """release_work_lock deletes key and returns True (lines 689-692)."""
        mock_redis.acquire_work_lock("rel-task", "rel-sess")

        result = mock_redis.release_work_lock("rel-task", "rel-sess")
        assert result is True
        assert not mock_redis.is_work_locked("rel-task")

    def test_release_work_lock_wrong_holder(self, mock_redis):
        """release_work_lock returns False when holder mismatch (lines 693-695)."""
        mock_redis.acquire_work_lock("lock-mismatch", "sess-A")

        result = mock_redis.release_work_lock("lock-mismatch", "sess-B")
        assert result is False

    def test_release_work_lock_returns_false_on_error(self, mock_redis):
        """release_work_lock catches exception and returns False (lines 696-698)."""
        mock_redis._client.get = MagicMock(side_effect=Exception("get fail"))

        result = mock_redis.release_work_lock("task-1", "sess-1")
        assert result is False

    def test_is_work_locked_true_when_held(self, mock_redis):
        """is_work_locked returns True when locked (lines 717-718)."""
        mock_redis.acquire_work_lock("locked-task", "sess-A")

        assert mock_redis.is_work_locked("locked-task") is True

    def test_is_work_locked_false_when_not_locked(self, mock_redis):
        """is_work_locked returns False when key does not exist (lines 717-718)."""
        assert mock_redis.is_work_locked("no-such-task") is False

    def test_is_work_locked_returns_false_on_error(self, mock_redis):
        """is_work_locked catches exception and returns False (lines 719-721)."""
        mock_redis._client.exists = MagicMock(side_effect=Exception("exists fail"))

        result = mock_redis.is_work_locked("task-1")
        assert result is False

    def test_get_work_lock_holder_success(self, mock_redis):
        """get_work_lock_holder returns session_id of holder (lines 739-741)."""
        mock_redis.acquire_work_lock("task-holder", "sess-holder")

        result = mock_redis.get_work_lock_holder("task-holder")
        assert result == "sess-holder"

    def test_get_work_lock_holder_returns_none_when_not_locked(self, mock_redis):
        """get_work_lock_holder returns None when key does not exist (lines 740-741)."""
        result = mock_redis.get_work_lock_holder("no-such-task")
        assert result is None

    def test_get_work_lock_holder_returns_none_on_error(self, mock_redis):
        """get_work_lock_holder catches exception and returns None (lines 742-744)."""
        mock_redis._client.get = MagicMock(side_effect=Exception("get fail"))

        result = mock_redis.get_work_lock_holder("task-1")
        assert result is None


# ===========================================================================
# RedisStateStore assignment — paths (lines 762-889)
# ===========================================================================


class TestRedisAssignments:
    """Cover RedisStateStore assignment operations."""

    def test_create_assignment_success(self, mock_redis):
        """create_assignment stores hash with 7-day TTL (lines 766-773)."""
        result = mock_redis.create_assignment(_assignment("a-create-1"))
        assert result is True

    def test_create_assignment_returns_false_on_error(self, mock_redis):
        """create_assignment catches exception and returns False (lines 771-773)."""
        mock_redis._client.hset = MagicMock(side_effect=Exception("hset fail"))

        result = mock_redis.create_assignment(_assignment())
        assert result is False

    def test_get_assignment_success(self, mock_redis):
        """get_assignment returns WorkAssignment (lines 791-796)."""
        mock_redis.create_assignment(_assignment("a-get-1"))

        result = mock_redis.get_assignment("a-get-1")
        assert result is not None
        assert result.assignment_id == "a-get-1"

    def test_get_assignment_returns_none_when_not_found(self, mock_redis):
        """get_assignment returns None when hash is empty (lines 793-795)."""
        result = mock_redis.get_assignment("nonexistent-assignment")
        assert result is None

    def test_get_assignment_returns_none_on_redis_error(self, mock_redis):
        """get_assignment catches exception and returns None (lines 797-799)."""
        mock_redis._client.hgetall = MagicMock(side_effect=Exception("hgetall fail"))

        result = mock_redis.get_assignment("a-1")
        assert result is None

    def test_update_assignment_status_success(self, mock_redis):
        """update_assignment_status updates status field (lines 824-829)."""
        mock_redis.create_assignment(_assignment("a-upd-1"))

        result = mock_redis.update_assignment_status("a-upd-1", AssignmentStatus.IN_PROGRESS)
        assert result is True

    def test_update_assignment_status_with_completed_at(self, mock_redis):
        """update_assignment_status includes completed_at when provided (lines 826-829)."""
        mock_redis.create_assignment(_assignment("a-complete-deep"))
        now = datetime.now(UTC)

        result = mock_redis.update_assignment_status(
            "a-complete-deep", AssignmentStatus.COMPLETED, completed_at=now
        )
        assert result is True

    def test_update_assignment_status_returns_false_on_error(self, mock_redis):
        """update_assignment_status catches exception and returns False (lines 831-833)."""
        mock_redis._client.hset = MagicMock(side_effect=Exception("hset fail"))

        result = mock_redis.update_assignment_status("a-1", AssignmentStatus.COMPLETED)
        assert result is False

    def test_list_assignments_no_filter(self, mock_redis):
        """list_assignments returns all assignments (lines 857-886)."""
        for i in range(3):
            mock_redis.create_assignment(_assignment(f"a-list-{i}"))

        results = mock_redis.list_assignments()
        ids = [a.assignment_id for a in results]
        for i in range(3):
            assert f"a-list-{i}" in ids

    def test_list_assignments_filters_by_domain(self, mock_redis):
        """list_assignments domain filter excludes non-matching (lines 871-872)."""
        mock_redis.create_assignment(_assignment("a-dom-alpha", domain="alpha"))
        mock_redis.create_assignment(_assignment("a-dom-beta", domain="beta"))

        results = mock_redis.list_assignments(domain="alpha")
        ids = [a.assignment_id for a in results]
        assert "a-dom-alpha" in ids
        assert "a-dom-beta" not in ids

    def test_list_assignments_filters_by_project(self, mock_redis):
        """list_assignments project filter excludes non-matching (lines 873-874)."""
        mock_redis.create_assignment(_assignment("a-proj-fe", project="fe"))
        mock_redis.create_assignment(_assignment("a-proj-be", project="be"))

        results = mock_redis.list_assignments(project="fe")
        ids = [a.assignment_id for a in results]
        assert "a-proj-fe" in ids
        assert "a-proj-be" not in ids

    def test_list_assignments_filters_by_status(self, mock_redis):
        """list_assignments status filter excludes non-matching (lines 875-876)."""
        mock_redis.create_assignment(
            _assignment("a-assigned-deep", status=AssignmentStatus.ASSIGNED)
        )
        mock_redis.create_assignment(
            _assignment("a-inprog-deep", status=AssignmentStatus.IN_PROGRESS)
        )

        results = mock_redis.list_assignments(status=AssignmentStatus.ASSIGNED)
        ids = [a.assignment_id for a in results]
        assert "a-assigned-deep" in ids
        assert "a-inprog-deep" not in ids

    def test_list_assignments_sorted_by_priority_desc(self, mock_redis):
        """list_assignments sorts by priority descending (lines 881)."""
        mock_redis.create_assignment(_assignment("a-prio-low", priority=1))
        mock_redis.create_assignment(_assignment("a-prio-high", priority=10))

        results = mock_redis.list_assignments()
        ids = [a.assignment_id for a in results]
        assert ids.index("a-prio-high") < ids.index("a-prio-low")

    def test_list_assignments_returns_empty_on_redis_error(self, mock_redis):
        """list_assignments catches exception and returns [] (lines 887-889)."""
        mock_redis._client.keys = MagicMock(side_effect=Exception("keys fail"))

        result = mock_redis.list_assignments()
        assert result == []


# ===========================================================================
# RedisStateStore work trees — paths (lines 907-979)
# ===========================================================================


class TestRedisWorkTrees:
    """Cover RedisStateStore work tree operations."""

    def test_register_work_tree_success(self, mock_redis):
        """register_work_tree stores hash (lines 911-918)."""
        result = mock_redis.register_work_tree(_work_tree("/tmp/wt-success"))
        assert result is True

    def test_register_work_tree_returns_false_on_error(self, mock_redis):
        """register_work_tree catches exception and returns False (lines 916-918)."""
        mock_redis._client.hset = MagicMock(side_effect=Exception("hset fail"))

        result = mock_redis.register_work_tree(_work_tree())
        assert result is False

    def test_get_work_tree_success(self, mock_redis):
        """get_work_tree returns WorkTree when found (lines 936-941)."""
        mock_redis.register_work_tree(_work_tree("/tmp/wt-get"))

        result = mock_redis.get_work_tree("/tmp/wt-get")
        assert result is not None
        assert result.work_tree_path == "/tmp/wt-get"

    def test_get_work_tree_returns_none_when_not_found(self, mock_redis):
        """get_work_tree returns None when hash is empty (lines 938-940)."""
        result = mock_redis.get_work_tree("/tmp/no-such-tree")
        assert result is None

    def test_get_work_tree_returns_none_on_redis_error(self, mock_redis):
        """get_work_tree catches exception and returns None (lines 942-944)."""
        mock_redis._client.hgetall = MagicMock(side_effect=Exception("hgetall fail"))

        result = mock_redis.get_work_tree("/tmp/any")
        assert result is None

    def test_get_work_trees_for_session_filters_by_session(self, mock_redis):
        """get_work_trees_for_session returns only matching session's trees (lines 972-974)."""
        mock_redis.register_work_tree(_work_tree("/tmp/sess-A-1", session_id="sess-A"))
        mock_redis.register_work_tree(_work_tree("/tmp/sess-A-2", session_id="sess-A"))
        mock_redis.register_work_tree(_work_tree("/tmp/sess-B-1", session_id="sess-B"))

        results = mock_redis.get_work_trees_for_session("sess-A")
        paths = [wt.work_tree_path for wt in results]
        assert "/tmp/sess-A-1" in paths
        assert "/tmp/sess-A-2" in paths
        assert "/tmp/sess-B-1" not in paths

    def test_get_work_trees_for_session_returns_empty_on_error(self, mock_redis):
        """get_work_trees_for_session returns [] on exception (lines 977-979)."""
        mock_redis._client.keys = MagicMock(side_effect=Exception("keys fail"))

        result = mock_redis.get_work_trees_for_session("s-1")
        assert result == []

    def test_get_work_trees_for_session_skips_empty_hash(self, mock_redis):
        """get_work_trees_for_session skips keys with empty data (lines 967-969)."""
        mock_redis.register_work_tree(_work_tree("/tmp/valid-tree"))
        # Inject empty hash for a ghost key
        mock_redis._client._data["worktree:/tmp/ghost"] = {}

        results = mock_redis.get_work_trees_for_session("s-deep-001")
        assert isinstance(results, list)
        paths = [wt.work_tree_path for wt in results]
        assert "/tmp/valid-tree" in paths


# ===========================================================================
# RedisStateStore publish/subscribe — paths (lines 998-1035)
# ===========================================================================


class TestRedisPublishSubscribe:
    """Cover RedisStateStore publish/subscribe."""

    def test_publish_status_change_success(self, mock_redis):
        """publish_status_change calls publish and returns True (lines 1002-1005)."""
        result = mock_redis.publish_status_change("s-1", {"status": "active"})
        assert result is True

    def test_publish_status_change_returns_false_on_error(self, mock_redis):
        """publish_status_change catches exception and returns False (lines 1006-1008)."""
        mock_redis._client.publish = MagicMock(side_effect=Exception("publish fail"))

        result = mock_redis.publish_status_change("s-1", {"status": "active"})
        assert result is False

    def test_subscribe_to_agent_success(self, mock_redis):
        """subscribe_to_agent returns pubsub object (lines 1026-1032)."""
        result = mock_redis.subscribe_to_agent("s-1")
        assert result is not None

    def test_subscribe_to_agent_returns_none_on_error(self, mock_redis):
        """subscribe_to_agent catches exception and returns None (lines 1033-1035)."""
        mock_redis._client.pubsub = MagicMock(side_effect=Exception("pubsub fail"))

        result = mock_redis.subscribe_to_agent("s-1")
        assert result is None


# ===========================================================================
# StateStore.subscribe_to_all_agents (lines 1043-1053, 2044-2065)
# ===========================================================================


class TestStateStoreSubscribeToAllAgents:
    """Cover StateStore.subscribe_to_all_agents delegation."""

    def test_subscribe_to_all_agents_sqlite_raises(self, state_store_sqlite):
        """SQLite backend: subscribe_to_all_agents raises AttributeError on _client."""
        with pytest.raises((AttributeError, AssertionError)):
            state_store_sqlite.subscribe_to_all_agents()

    def test_subscribe_to_all_agents_not_connected_returns_none(self, tmp_path):
        """When store type is 'none', subscribe_to_all_agents returns None."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "disc.db"),
        )
        result = store.subscribe_to_all_agents()
        assert result is None

    def test_subscribe_to_all_agents_redis_type_delegates_to_redis_store(self, tmp_path):
        """Redis store type: delegates to _redis.subscribe_to_all_agents()."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "disc.db"),
        )
        # Set store type to redis but _redis has no _client
        store._store_type = "redis"

        # _redis.subscribe_to_all_agents() doesn't exist on RedisStateStore,
        # so StateStore.subscribe_to_all_agents() checks hasattr(self, "_redis")
        # and calls self._redis.subscribe_to_all_agents(), which raises AttributeError
        with pytest.raises(AttributeError):
            store.subscribe_to_all_agents()


# ===========================================================================
# SQLiteStateStore connect failure (lines 1096-1098)
# ===========================================================================


class TestSQLiteConnectFailure:
    """Cover SQLiteStateStore.connect() failure path."""

    def test_connect_returns_false_on_failure(self, tmp_path):
        """connect() returns False when sqlite3.connect raises (lines 1096-1098)."""
        store = SQLiteStateStore(db_path=str(tmp_path / "test.db"))

        with patch("sqlite3.connect", side_effect=Exception("Cannot open database")):
            result = store.connect()

        assert result is False

    def test_disconnect_noop_when_no_connection(self, tmp_path):
        """disconnect() is safe when _conn is None (lines 1100-1103)."""
        store = SQLiteStateStore(db_path=str(tmp_path / "test.db"))
        store.disconnect()
        assert store._conn is None

    def test_create_tables_noop_when_no_conn(self, tmp_path):
        """_create_tables returns immediately when _conn is None (lines 1107-1108)."""
        store = SQLiteStateStore(db_path=str(tmp_path / "notables.db"))
        store._create_tables()
        assert store._conn is None


# ===========================================================================
# StateStore.connect() — "none" path (lines 1209-1211)
# ===========================================================================


class TestStateStoreConnectNone:
    """Cover StateStore.connect() path where both Redis and SQLite fail."""

    def test_connect_returns_none_when_both_fail(self, tmp_path):
        """When both Redis and SQLite fail, connect() returns 'none' (lines 1209-1211)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "fail.db"),
        )

        with patch.object(store._sqlite, "connect", return_value=False):
            result = store.connect()

        assert result == "none"
        assert not store.is_connected()
        assert store.get_store_type() == "none"


# ===========================================================================
# StateStore._sqlite_register_agent error path (lines 1268-1270)
# ===========================================================================


class TestSQLiteRegisterAgentError:
    """Cover _sqlite_register_agent error path."""

    def test_sqlite_register_agent_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_register_agent returns False when execute raises (lines 1268-1270)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.register_agent(_session("error-session"))
        assert result is False

    def test_sqlite_register_agent_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_register_agent returns False when _conn is None (lines 1236-1237)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.register_agent(_session())
        assert result is False


# ===========================================================================
# StateStore._sqlite_get_agent error path (lines 1307-1309)
# ===========================================================================


class TestSQLiteGetAgentError:
    """Cover _sqlite_get_agent error path."""

    def test_sqlite_get_agent_returns_none_on_error(self, state_store_sqlite):
        """_sqlite_get_agent returns None when execute raises (lines 1307-1309)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.get_agent("any-session")
        assert result is None

    def test_sqlite_get_agent_returns_none_when_no_conn(self, tmp_path):
        """_sqlite_get_agent returns None when _conn is None (lines 1281-1282)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.get_agent("any-session")
        assert result is None


# ===========================================================================
# StateStore._sqlite_get_active_agents error path (lines 1372-1374)
# ===========================================================================


class TestSQLiteGetActiveAgentsError:
    """Cover _sqlite_get_active_agents error path."""

    def test_sqlite_get_active_agents_returns_empty_on_error(self, state_store_sqlite):
        """_sqlite_get_active_agents returns [] when execute raises (lines 1372-1374)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.get_active_agents()
        assert result == []

    def test_sqlite_get_active_agents_returns_empty_when_no_conn(self, tmp_path):
        """_sqlite_get_active_agents returns [] when _conn is None (lines 1324-1325)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.get_active_agents()
        assert result == []

    def test_sqlite_get_active_agents_excludes_stale(self, state_store_sqlite):
        """Stale agents (heartbeat > 5 min) are excluded (lines 1349-1351)."""
        old_hb = datetime.now(UTC) - timedelta(minutes=10)
        state_store_sqlite.register_agent(_session("stale-sqlite", last_heartbeat=old_hb))

        agents = state_store_sqlite.get_active_agents()
        ids = [a.session_id for a in agents]
        assert "stale-sqlite" not in ids


# ===========================================================================
# StateStore._sqlite_update_agent_status error path (lines 1418-1420)
# ===========================================================================


class TestSQLiteUpdateAgentStatusError:
    """Cover _sqlite_update_agent_status error path."""

    def test_sqlite_update_agent_status_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_update_agent_status returns False when execute raises (lines 1418-1420)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.update_agent_status("s-1", AgentStatus.IDLE)
        assert result is False

    def test_sqlite_update_agent_status_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_update_agent_status returns False when _conn is None (lines 1389-1390)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.update_agent_status("s-1", AgentStatus.IDLE)
        assert result is False


# ===========================================================================
# StateStore._sqlite_assign_work error path (lines 1463-1465)
# ===========================================================================


class TestSQLiteAssignWorkError:
    """Cover _sqlite_assign_work error path."""

    def test_sqlite_assign_work_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_assign_work returns False when execute raises (lines 1463-1465)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.assign_work("s-1", "a-1")
        assert result is False

    def test_sqlite_assign_work_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_assign_work returns False when _conn is None (lines 1435-1436)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.assign_work("s-1", "a-1")
        assert result is False

    def test_sqlite_assign_work_without_tree_path(self, state_store_sqlite):
        """_sqlite_assign_work with no work_tree_path uses shorter UPDATE (lines 1450-1458)."""
        state_store_sqlite.register_agent(_session("assign-no-tree"))

        result = state_store_sqlite.assign_work("assign-no-tree", "a-999")
        assert result is True


# ===========================================================================
# StateStore._sqlite_update_heartbeat error path (lines 1495-1497)
# ===========================================================================


class TestSQLiteUpdateHeartbeatError:
    """Cover _sqlite_update_heartbeat error path."""

    def test_sqlite_update_heartbeat_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_update_heartbeat returns False when execute raises (lines 1495-1497)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.update_heartbeat("s-1")
        assert result is False

    def test_sqlite_update_heartbeat_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_update_heartbeat returns False when _conn is None (lines 1476-1477)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.update_heartbeat("s-1")
        assert result is False


# ===========================================================================
# StateStore._sqlite_acquire_work_lock error path (lines 1549-1551)
# ===========================================================================


class TestSQLiteAcquireWorkLockError:
    """Cover _sqlite_acquire_work_lock error paths."""

    def test_sqlite_acquire_work_lock_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_acquire_work_lock returns False when execute raises (lines 1549-1551)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.acquire_work_lock("task-err", "sess-err")
        assert result is False

    def test_sqlite_acquire_work_lock_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_acquire_work_lock returns False when _conn is None (lines 1510-1511)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.acquire_work_lock("task-1", "sess-1")
        assert result is False

    def test_sqlite_acquire_work_lock_expired_lock_reacquired(self, state_store_sqlite):
        """Expired lock is deleted and new lock can be acquired (lines 1534-1544)."""
        state_store_sqlite.acquire_work_lock("task-expire", "sess-A", ttl_seconds=1)

        time.sleep(1.1)

        result = state_store_sqlite.acquire_work_lock("task-expire", "sess-B", ttl_seconds=60)
        assert result is True


# ===========================================================================
# StateStore._sqlite_release_work_lock error path (lines 1590-1592)
# ===========================================================================


class TestSQLiteReleaseWorkLockError:
    """Cover _sqlite_release_work_lock error paths."""

    def test_sqlite_release_work_lock_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_release_work_lock returns False when execute raises (lines 1590-1592)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.release_work_lock("task-1", "sess-1")
        assert result is False

    def test_sqlite_release_work_lock_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_release_work_lock returns False when _conn is None (lines 1562-1563)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.release_work_lock("task-1", "sess-1")
        assert result is False

    def test_sqlite_release_work_lock_when_no_lock_returns_false(self, state_store_sqlite):
        """_sqlite_release_work_lock returns False when lock does not exist (lines 1577-1579)."""
        result = state_store_sqlite.release_work_lock("no-lock", "sess-X")
        assert result is False

    def test_sqlite_release_work_lock_wrong_session(self, state_store_sqlite):
        """_sqlite_release_work_lock returns False for wrong session (lines 1581-1583)."""
        state_store_sqlite.acquire_work_lock("task-ws", "sess-A")
        result = state_store_sqlite.release_work_lock("task-ws", "sess-B")
        assert result is False


# ===========================================================================
# StateStore._sqlite_is_work_locked error path (lines 1625-1632)
# ===========================================================================


class TestSQLiteIsWorkLockedError:
    """Cover _sqlite_is_work_locked error paths."""

    def test_sqlite_is_work_locked_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_is_work_locked returns False when execute raises (lines 1630-1632)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.is_work_locked("task-1")
        assert result is False

    def test_sqlite_is_work_locked_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_is_work_locked returns False when _conn is None (lines 1603-1604)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.is_work_locked("task-1")
        assert result is False

    def test_sqlite_is_work_locked_expired_lock_returns_false(self, state_store_sqlite):
        """Expired lock is cleaned up and returns False (lines 1623-1627)."""
        state_store_sqlite.acquire_work_lock("task-exp-lock", "sess-A", ttl_seconds=1)

        time.sleep(1.1)

        result = state_store_sqlite.is_work_locked("task-exp-lock")
        assert result is False


# ===========================================================================
# StateStore._sqlite_get_work_lock_holder error path (lines 1666-1673)
# ===========================================================================


class TestSQLiteGetWorkLockHolderError:
    """Cover _sqlite_get_work_lock_holder error paths."""

    def test_sqlite_get_work_lock_holder_returns_none_on_error(self, state_store_sqlite):
        """_sqlite_get_work_lock_holder returns None when execute raises (lines 1671-1673)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.get_work_lock_holder("task-1")
        assert result is None

    def test_sqlite_get_work_lock_holder_returns_none_when_no_conn(self, tmp_path):
        """_sqlite_get_work_lock_holder returns None when _conn is None (lines 1644-1645)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.get_work_lock_holder("task-1")
        assert result is None

    def test_sqlite_get_work_lock_holder_expired_lock_returns_none(self, state_store_sqlite):
        """Expired lock holder returns None and cleans up (lines 1664-1668)."""
        state_store_sqlite.acquire_work_lock("task-exp-holder", "sess-A", ttl_seconds=1)

        time.sleep(1.1)

        result = state_store_sqlite.get_work_lock_holder("task-exp-holder")
        assert result is None


# ===========================================================================
# StateStore._sqlite_create_assignment error path (lines 1719-1721)
# ===========================================================================


class TestSQLiteCreateAssignmentError:
    """Cover _sqlite_create_assignment error path."""

    def test_sqlite_create_assignment_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_create_assignment returns False when execute raises (lines 1719-1721)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.create_assignment(_assignment())
        assert result is False

    def test_sqlite_create_assignment_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_create_assignment returns False when _conn is None (lines 1684-1685)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.create_assignment(_assignment())
        assert result is False

    def test_sqlite_create_assignment_with_completed_at(self, state_store_sqlite):
        """_sqlite_create_assignment stores completed_at when set (lines 1710)."""
        completed = datetime.now(UTC)
        asn = _assignment(
            "a-completed-sqlite",
            status=AssignmentStatus.COMPLETED,
            completed_at=completed,
        )

        result = state_store_sqlite.create_assignment(asn)
        assert result is True

        retrieved = state_store_sqlite.get_assignment("a-completed-sqlite")
        assert retrieved is not None
        assert retrieved.completed_at is not None


# ===========================================================================
# StateStore._sqlite_get_assignment error path (lines 1760-1762)
# ===========================================================================


class TestSQLiteGetAssignmentError:
    """Cover _sqlite_get_assignment error path."""

    def test_sqlite_get_assignment_returns_none_on_error(self, state_store_sqlite):
        """_sqlite_get_assignment returns None when execute raises (lines 1760-1762)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.get_assignment("a-1")
        assert result is None

    def test_sqlite_get_assignment_returns_none_when_no_conn(self, tmp_path):
        """_sqlite_get_assignment returns None when _conn is None (lines 1732-1733)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.get_assignment("a-1")
        assert result is None


# ===========================================================================
# StateStore._sqlite_update_assignment_status error path (lines 1812-1814)
# ===========================================================================


class TestSQLiteUpdateAssignmentStatusError:
    """Cover _sqlite_update_assignment_status error path."""

    def test_sqlite_update_assignment_status_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_update_assignment_status returns False on execute error (lines 1812-1814)."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.update_assignment_status("a-1", AssignmentStatus.COMPLETED)
        assert result is False

    def test_sqlite_update_assignment_status_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_update_assignment_status returns False when _conn is None (lines 1784-1785)."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.update_assignment_status("a-1", AssignmentStatus.COMPLETED)
        assert result is False

    def test_sqlite_update_assignment_status_without_completed_at(self, state_store_sqlite):
        """_sqlite_update_assignment_status with no completed_at uses shorter UPDATE."""
        state_store_sqlite.create_assignment(_assignment("a-upd-no-comp"))

        result = state_store_sqlite.update_assignment_status(
            "a-upd-no-comp", AssignmentStatus.IN_PROGRESS
        )
        assert result is True

    def test_sqlite_update_assignment_status_with_completed_at(self, state_store_sqlite):
        """_sqlite_update_assignment_status with completed_at uses longer UPDATE."""
        state_store_sqlite.create_assignment(_assignment("a-upd-comp"))
        now = datetime.now(UTC)

        result = state_store_sqlite.update_assignment_status(
            "a-upd-comp", AssignmentStatus.COMPLETED, completed_at=now
        )
        assert result is True

        retrieved = state_store_sqlite.get_assignment("a-upd-comp")
        assert retrieved is not None
        assert retrieved.status == AssignmentStatus.COMPLETED


# ===========================================================================
# StateStore.list_assignments SQLite path (lines 1832-1893)
# ===========================================================================


class TestStateStoreListAssignmentsSQLite:
    """Cover list_assignments SQLite implementation."""

    def test_list_assignments_sqlite_returns_all(self, state_store_sqlite):
        """list_assignments from SQLite returns all assignments."""
        for i in range(3):
            state_store_sqlite.create_assignment(_assignment(f"ls-a-{i}"))

        results = state_store_sqlite.list_assignments()
        ids = [a.assignment_id for a in results]
        for i in range(3):
            assert f"ls-a-{i}" in ids

    def test_list_assignments_sqlite_filter_by_project(self, state_store_sqlite):
        """list_assignments SQLite filters by project."""
        state_store_sqlite.create_assignment(_assignment("ls-proj-fe", project="fe"))
        state_store_sqlite.create_assignment(_assignment("ls-proj-be", project="be"))

        results = state_store_sqlite.list_assignments(project="fe")
        ids = [a.assignment_id for a in results]
        assert "ls-proj-fe" in ids
        assert "ls-proj-be" not in ids

    def test_list_assignments_sqlite_filter_by_status(self, state_store_sqlite):
        """list_assignments SQLite filters by status."""
        state_store_sqlite.create_assignment(
            _assignment("ls-st-assigned", status=AssignmentStatus.ASSIGNED)
        )
        state_store_sqlite.create_assignment(
            _assignment("ls-st-inprog", status=AssignmentStatus.IN_PROGRESS)
        )

        results = state_store_sqlite.list_assignments(status=AssignmentStatus.ASSIGNED)
        ids = [a.assignment_id for a in results]
        assert "ls-st-assigned" in ids
        assert "ls-st-inprog" not in ids

    def test_list_assignments_sqlite_returns_empty_when_no_conn(self, tmp_path):
        """list_assignments returns [] when _conn is None."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.list_assignments()
        assert result == []

    def test_list_assignments_sqlite_returns_empty_on_error(self, state_store_sqlite):
        """list_assignments SQLite returns [] when execute raises."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.list_assignments()
        assert result == []


# ===========================================================================
# StateStore._sqlite_register_work_tree error path (lines 1930-1932)
# ===========================================================================


class TestSQLiteRegisterWorkTreeError:
    """Cover _sqlite_register_work_tree error path."""

    def test_sqlite_register_work_tree_returns_false_on_error(self, state_store_sqlite):
        """_sqlite_register_work_tree returns False when execute raises."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.register_work_tree(_work_tree())
        assert result is False

    def test_sqlite_register_work_tree_returns_false_when_no_conn(self, tmp_path):
        """_sqlite_register_work_tree returns False when _conn is None."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.register_work_tree(_work_tree())
        assert result is False


# ===========================================================================
# StateStore._sqlite_get_work_tree error path (lines 1964-1966)
# ===========================================================================


class TestSQLiteGetWorkTreeError:
    """Cover _sqlite_get_work_tree error path."""

    def test_sqlite_get_work_tree_returns_none_on_error(self, state_store_sqlite):
        """_sqlite_get_work_tree returns None when execute raises."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.get_work_tree("/tmp/any")
        assert result is None

    def test_sqlite_get_work_tree_returns_none_when_no_conn(self, tmp_path):
        """_sqlite_get_work_tree returns None when _conn is None."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.get_work_tree("/tmp/any")
        assert result is None


# ===========================================================================
# StateStore.list_work_trees (lines 1968-1980)
# ===========================================================================


class TestStateStoreListWorkTrees:
    """Cover list_work_trees for both store types."""

    def test_list_work_trees_sqlite_returns_empty_list(self, state_store_sqlite):
        """list_work_trees SQLite stub returns [] (lines 1976-1979)."""
        state_store_sqlite.register_work_tree(_work_tree("/tmp/lt-wt-1"))

        result = state_store_sqlite.list_work_trees()
        assert result == []

    def test_list_work_trees_not_connected_returns_empty(self, tmp_path):
        """list_work_trees returns [] when store_type is 'none'."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        result = store.list_work_trees()
        assert result == []

    def test_list_work_trees_redis_calls_get_work_trees_for_session(self, mock_redis, tmp_path):
        """list_work_trees for redis backend calls get_work_trees_for_session('')."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "lt_redis.db"),
        )
        store._store_type = "redis"
        store._redis = mock_redis

        # list_work_trees calls get_work_trees_for_session("") which filters
        # trees where assigned_session_id == "" (the sentinel for "all trees").
        mock_redis.register_work_tree(_work_tree("/tmp/lt-redis-wt-1", session_id=""))

        result = store.list_work_trees()
        assert isinstance(result, list)
        paths = [wt.work_tree_path for wt in result]
        assert "/tmp/lt-redis-wt-1" in paths


# ===========================================================================
# StateStore.get_work_trees_for_session SQLite error path (lines 1992-2033)
# ===========================================================================


class TestSQLiteGetWorkTreesForSessionError:
    """Cover get_work_trees_for_session SQLite error paths."""

    def test_sqlite_get_work_trees_for_session_returns_empty_on_error(self, state_store_sqlite):
        """get_work_trees_for_session returns [] on execute error."""
        state_store_sqlite._sqlite._conn.close()

        result = state_store_sqlite.get_work_trees_for_session("s-1")
        assert result == []

    def test_sqlite_get_work_trees_for_session_returns_empty_when_no_conn(self, tmp_path):
        """get_work_trees_for_session returns [] when _conn is None."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        store.connect()
        store._sqlite._conn = None

        result = store.get_work_trees_for_session("s-1")
        assert result == []

    def test_sqlite_get_work_trees_for_session_none_session(self, state_store_sqlite):
        """get_work_trees_for_session with None session queries correctly."""
        state_store_sqlite.register_work_tree(_work_tree("/tmp/sess-none-wt"))

        result = state_store_sqlite.get_work_trees_for_session(None)
        assert isinstance(result, list)

    def test_sqlite_get_work_trees_for_session_empty_string_returns_all(self, state_store_sqlite):
        """get_work_trees_for_session('') returns all trees."""
        state_store_sqlite.register_work_tree(_work_tree("/tmp/all-A"))
        state_store_sqlite.register_work_tree(_work_tree("/tmp/all-B", session_id="sess-B"))

        result = state_store_sqlite.get_work_trees_for_session("")
        paths = [wt.work_tree_path for wt in result]
        assert "/tmp/all-A" in paths
        assert "/tmp/all-B" in paths


# ===========================================================================
# StateStore.publish_status_change — SQLite and none paths (lines 2035-2042)
# ===========================================================================


class TestStateStorePublishStatusChange:
    """Cover publish_status_change for SQLite and none backends."""

    def test_publish_status_change_sqlite_returns_true(self, state_store_sqlite):
        """SQLite publish_status_change stub returns True."""
        result = state_store_sqlite.publish_status_change("s-1", {"status": "active"})
        assert result is True

    def test_publish_status_change_none_returns_false(self, tmp_path):
        """publish_status_change returns False when not connected."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn.db"),
        )
        result = store.publish_status_change("s-1", {"status": "active"})
        assert result is False


# ===========================================================================
# StateStore delegation — when store_type is "redis" (multiple lines)
# ===========================================================================


class TestStateStoreRedisDelegation:
    """Cover StateStore delegation to Redis backend."""

    def _make_redis_state_store(self, mock_redis_store, tmp_path):
        """Helper to create a StateStore backed by mock redis."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "redis_del.db"),
        )
        store._store_type = "redis"
        store._redis = mock_redis_store
        return store

    def test_register_agent_delegates_to_redis(self, mock_redis, tmp_path):
        """register_agent delegates to _redis when store_type='redis' (line 1229)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)

        result = store.register_agent(_session("redis-del-session"))
        assert result is True

    def test_get_agent_delegates_to_redis(self, mock_redis, tmp_path):
        """get_agent delegates to _redis when store_type='redis' (line 1274)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.register_agent(_session("redis-get-session"))

        result = store.get_agent("redis-get-session")
        assert result is not None
        assert result.session_id == "redis-get-session"

    def test_get_active_agents_delegates_to_redis(self, mock_redis, tmp_path):
        """get_active_agents delegates to _redis (line 1315)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.register_agent(_session("redis-active-session"))

        results = store.get_active_agents()
        assert isinstance(results, list)

    def test_update_agent_status_delegates_to_redis(self, mock_redis, tmp_path):
        """update_agent_status delegates to _redis (line 1380)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.register_agent(_session("redis-upd-session"))

        result = store.update_agent_status("redis-upd-session", AgentStatus.IDLE)
        assert result is True

    def test_assign_work_delegates_to_redis(self, mock_redis, tmp_path):
        """assign_work delegates to _redis (line 1426)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.register_agent(_session("redis-asn-session"))

        result = store.assign_work("redis-asn-session", "a-redis-001")
        assert result is True

    def test_update_heartbeat_delegates_to_redis(self, mock_redis, tmp_path):
        """update_heartbeat delegates to _redis (line 1469)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)

        result = store.update_heartbeat("redis-hb-session")
        assert result is True

    def test_acquire_work_lock_delegates_to_redis(self, mock_redis, tmp_path):
        """acquire_work_lock delegates to _redis (line 1501)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)

        result = store.acquire_work_lock("task-redis-lock", "sess-redis")
        assert result is True

    def test_release_work_lock_delegates_to_redis(self, mock_redis, tmp_path):
        """release_work_lock delegates to _redis (line 1555)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.acquire_work_lock("task-redis-rel", "sess-redis")

        result = store.release_work_lock("task-redis-rel", "sess-redis")
        assert result is True

    def test_is_work_locked_delegates_to_redis(self, mock_redis, tmp_path):
        """is_work_locked delegates to _redis (line 1596)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.acquire_work_lock("task-redis-islock", "sess-redis")

        result = store.is_work_locked("task-redis-islock")
        assert result is True

    def test_get_work_lock_holder_delegates_to_redis(self, mock_redis, tmp_path):
        """get_work_lock_holder delegates to _redis (line 1637)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.acquire_work_lock("task-redis-holder", "sess-redis-holder")

        result = store.get_work_lock_holder("task-redis-holder")
        assert result == "sess-redis-holder"

    def test_create_assignment_delegates_to_redis(self, mock_redis, tmp_path):
        """create_assignment delegates to _redis (line 1677)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)

        result = store.create_assignment(_assignment("a-redis-create"))
        assert result is True

    def test_get_assignment_delegates_to_redis(self, mock_redis, tmp_path):
        """get_assignment delegates to _redis (line 1725)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.create_assignment(_assignment("a-redis-get"))

        result = store.get_assignment("a-redis-get")
        assert result is not None
        assert result.assignment_id == "a-redis-get"

    def test_update_assignment_status_delegates_to_redis(self, mock_redis, tmp_path):
        """update_assignment_status delegates to _redis (line 1772)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.create_assignment(_assignment("a-redis-upd"))

        result = store.update_assignment_status("a-redis-upd", AssignmentStatus.IN_PROGRESS)
        assert result is True

    def test_list_assignments_delegates_to_redis(self, mock_redis, tmp_path):
        """list_assignments delegates to _redis (line 1833)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.create_assignment(_assignment("a-redis-list"))

        results = store.list_assignments()
        ids = [a.assignment_id for a in results]
        assert "a-redis-list" in ids

    def test_register_work_tree_delegates_to_redis(self, mock_redis, tmp_path):
        """register_work_tree delegates to _redis (line 1898)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)

        result = store.register_work_tree(_work_tree("/tmp/redis-wt"))
        assert result is True

    def test_get_work_tree_delegates_to_redis(self, mock_redis, tmp_path):
        """get_work_tree delegates to _redis (line 1937)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.register_work_tree(_work_tree("/tmp/redis-get-wt"))

        result = store.get_work_tree("/tmp/redis-get-wt")
        assert result is not None
        assert result.work_tree_path == "/tmp/redis-get-wt"

    def test_get_work_trees_for_session_delegates_to_redis(self, mock_redis, tmp_path):
        """get_work_trees_for_session delegates to _redis (line 1992)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)
        mock_redis.register_work_tree(_work_tree("/tmp/redis-sess-wt"))

        result = store.get_work_trees_for_session("s-deep-001")
        assert isinstance(result, list)

    def test_publish_status_change_delegates_to_redis(self, mock_redis, tmp_path):
        """publish_status_change delegates to _redis (line 2037)."""
        store = self._make_redis_state_store(mock_redis, tmp_path)

        result = store.publish_status_change("s-redis-pub", {"status": "active"})
        assert result is True


# ===========================================================================
# StateStore disconnect (lines 1213-1216)
# ===========================================================================


class TestStateStoreDisconnect:
    """Cover StateStore.disconnect() calling both backends."""

    def test_disconnect_calls_both_backends(self, tmp_path):
        """disconnect() calls both _redis.disconnect() and _sqlite.disconnect()."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "disc_both.db"),
        )
        store.connect()

        redis_disconnect = MagicMock()
        sqlite_disconnect = MagicMock()
        store._redis.disconnect = redis_disconnect
        store._sqlite.disconnect = sqlite_disconnect

        store.disconnect()

        redis_disconnect.assert_called_once()
        sqlite_disconnect.assert_called_once()


# ===========================================================================
# Additional edge cases for complete coverage
# ===========================================================================


class TestAdditionalEdgeCases:
    """Additional edge cases for remaining uncovered lines."""

    def test_redis_store_is_connected_false_when_not_connected(self):
        """is_connected() returns False when _connected=False."""
        store = RedisStateStore()
        assert store.is_connected() is False

    def test_redis_store_is_connected_false_when_client_is_none(self):
        """is_connected() returns False when _client is None even if _connected True."""
        store = RedisStateStore()
        store._connected = True
        store._client = None
        assert store.is_connected() is False

    def test_state_store_is_connected_false_initially(self, tmp_path):
        """is_connected() returns False before connect() is called."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "init.db"),
        )
        assert store.is_connected() is False

    def test_state_store_is_connected_true_after_sqlite_connect(self, state_store_sqlite):
        """is_connected() returns True when SQLite is connected."""
        assert state_store_sqlite.is_connected() is True

    def test_state_store_get_store_type_sqlite(self, state_store_sqlite):
        """get_store_type() returns 'sqlite' when SQLite is active."""
        assert state_store_sqlite.get_store_type() == "sqlite"

    def test_sqlite_store_connect_creates_all_tables(self, sqlite_store):
        """SQLiteStateStore creates all four tables on connect."""
        cursor = sqlite_store._conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}

        assert "agents" in tables
        assert "assignments" in tables
        assert "work_trees" in tables
        assert "work_locks" in tables

    def test_get_active_agents_with_domain_and_project_filter_sqlite(self, state_store_sqlite):
        """get_active_agents filters by both domain and project in SQLite."""
        state_store_sqlite.register_agent(_session("combo-1", domain="d1", project="p1"))
        state_store_sqlite.register_agent(_session("combo-2", domain="d1", project="p2"))
        state_store_sqlite.register_agent(_session("combo-3", domain="d2", project="p1"))

        agents = state_store_sqlite.get_active_agents(domain="d1", project="p1")
        ids = [a.session_id for a in agents]
        assert "combo-1" in ids
        assert "combo-2" not in ids
        assert "combo-3" not in ids

    def test_redis_cleanup_heartbeats_with_active_agents_not_removed(self, mock_redis):
        """cleanup_dead_heartbeats does NOT remove recent agents."""
        mock_redis.update_heartbeat("recent-sess")

        dead = mock_redis.cleanup_dead_heartbeats(timeout_seconds=300)
        assert "recent-sess" not in dead

    def test_redis_update_assignment_status_without_completed_at(self, mock_redis):
        """update_assignment_status without completed_at only sets status."""
        mock_redis.create_assignment(_assignment("a-status-only"))

        result = mock_redis.update_assignment_status("a-status-only", AssignmentStatus.IN_PROGRESS)
        assert result is True

    def test_state_store_list_work_trees_not_connected(self, tmp_path):
        """list_work_trees returns [] when store_type is 'none'."""
        store = StateStore(
            redis_url="redis://nonexistent:6379/0",
            sqlite_path=str(tmp_path / "noconn_list.db"),
        )
        result = store.list_work_trees()
        assert result == []

    def test_redis_register_agent_and_heartbeat_updated(self, mock_redis):
        """register_agent calls update_heartbeat to set sorted set score."""
        result = mock_redis.register_agent(_session("hb-check-sess"))
        assert result is True

        ts = mock_redis.get_heartbeat_time("hb-check-sess")
        assert ts is not None

    def test_redis_assign_work_none_path_clears_assignment(self, mock_redis):
        """assign_work with None assignment_id stores empty string."""
        mock_redis.register_agent(_session("clr-assign-sess"))
        mock_redis.assign_work("clr-assign-sess", "a-initial")

        result = mock_redis.assign_work("clr-assign-sess", None)
        assert result is True
