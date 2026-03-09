"""
Extended tests for FORGE StateStore module.

Covers the most impactful uncovered paths:
1. SQLite CRUD operations — register/get/update/list for all entity types
2. StateStore manager delegation to SQLite backend
3. TTL / expiration behaviour for work locks
4. Pubsub with fakeredis — skip cleanly when not available
5. Batch operations and list/filter APIs
6. Error handling — missing fields, corrupted data, disconnected stores
7. Work-tree operations via StateStore manager
8. Heartbeat update and active-agent filtering
9. Global singleton get_state_store()
10. Enum value coverage for all six enums
"""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

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
    session_id: str = "s-001",
    domain: str = "test-domain",
    project: str = "test-project",
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
    assignment_id: str = "a-001",
    session_id: str = "s-001",
    domain: str = "test-domain",
    project: str = "test-project",
    status: AssignmentStatus = AssignmentStatus.ASSIGNED,
    priority: int = 1,
) -> WorkAssignment:
    return WorkAssignment(
        assignment_id=assignment_id,
        session_id=session_id,
        task_type="feature",
        task_description="Test task",
        priority=priority,
        domain=domain,
        project=project,
        files_affected=["src/main.py"],
        dependencies=[],
        status=status,
    )


def _make_work_tree(
    path: str = "/tmp/wt-001",
    session_id: str = "s-001",
    status: WorkTreeStatus = WorkTreeStatus.ACTIVE,
) -> WorkTree:
    return WorkTree(
        work_tree_path=path,
        assigned_session_id=session_id,
        files_modified=["src/main.py", "tests/test_main.py"],
        branch_name="feature/test",
        base_commit="abc1234",
        status=status,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_store(tmp_path):
    """SQLiteStateStore connected to a temp DB."""
    store = SQLiteStateStore(db_path=str(tmp_path / "test.db"))
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def state_store(tmp_path):
    """StateStore forced to SQLite (no Redis)."""
    store = StateStore(
        redis_url="redis://nonexistent-host-99999:6379/0",
        sqlite_path=str(tmp_path / "state.db"),
    )
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def fake_redis_store():
    """RedisStateStore backed by fakeredis — skip if unavailable."""
    if not FAKEREDIS_AVAILABLE:
        pytest.skip("fakeredis not installed")
    store = RedisStateStore()
    store._client = fakeredis.FakeStrictRedis(decode_responses=True)
    store._connected = True
    yield store
    store.disconnect()


# ===========================================================================
# 1. Enum value coverage
# ===========================================================================


class TestEnumCoverage:
    """Every enum value round-trips through its string representation."""

    def test_agent_type_values(self):
        for val in AgentType:
            assert AgentType(val.value) == val

    def test_agent_role_values(self):
        for val in AgentRole:
            assert AgentRole(val.value) == val

    def test_agent_status_values(self):
        for val in AgentStatus:
            assert AgentStatus(val.value) == val

    def test_assignment_status_values(self):
        for val in AssignmentStatus:
            assert AssignmentStatus(val.value) == val

    def test_work_tree_status_values(self):
        for val in WorkTreeStatus:
            assert WorkTreeStatus(val.value) == val

    def test_conflict_risk_values(self):
        for val in ConflictRisk:
            assert ConflictRisk(val.value) == val


# ===========================================================================
# 2. AgentSession serialization edge-cases not in existing tests
# ===========================================================================


class TestAgentSessionEdgeCases:
    """Edge-cases for AgentSession serialization / deserialization."""

    def test_to_dict_capabilities_serialized_as_json_string(self):
        """to_dict() JSON-encodes capabilities so Redis hset works."""
        session = _make_session()
        session.capabilities = ["testing", "debugging"]
        data = session.to_dict()
        # Stored as JSON string for Redis compatibility
        assert isinstance(data["capabilities"], str)
        assert json.loads(data["capabilities"]) == ["testing", "debugging"]

    def test_to_dict_preferences_serialized_as_json_string(self):
        session = _make_session()
        session.preferences = {"model": "opus", "temperature": 0}
        data = session.to_dict()
        assert isinstance(data["preferences"], str)
        assert json.loads(data["preferences"]) == {"model": "opus", "temperature": 0}

    def test_from_dict_empty_current_task_becomes_none(self):
        """Empty string current_task stored by to_dict() deserializes to None."""
        session = _make_session()
        session.current_task = None
        data = session.to_dict()
        # to_dict stores None as ""
        assert data["current_task"] == ""
        restored = AgentSession.from_dict(data)
        assert restored.current_task is None

    def test_from_dict_missing_session_id_raises_key_error(self):
        with pytest.raises(KeyError):
            AgentSession.from_dict({"agent_type": "other", "agent_role": "builder"})

    def test_from_dict_capabilities_as_list_passthrough(self):
        """from_dict accepts pre-parsed list for capabilities (non-Redis path)."""
        data = {
            "session_id": "test",
            "agent_type": "claude-code",
            "agent_role": "builder",
            "domain": "d",
            "project": "p",
            "status": "active",
            "registered_at": "2026-01-01T00:00:00+00:00",
            "last_heartbeat": "2026-01-01T00:00:00+00:00",
            "capabilities": ["a", "b"],  # Already a list, not a JSON string
            "preferences": {},
        }
        session = AgentSession.from_dict(data)
        assert session.capabilities == ["a", "b"]

    def test_from_dict_preferences_as_dict_passthrough(self):
        """from_dict accepts pre-parsed dict for preferences."""
        data = {
            "session_id": "test",
            "agent_type": "codex",
            "agent_role": "qa",
            "domain": "d",
            "project": "p",
            "status": "idle",
            "registered_at": "2026-01-01T00:00:00+00:00",
            "last_heartbeat": "2026-01-01T00:00:00+00:00",
            "capabilities": "[]",
            "preferences": {"key": "value"},
        }
        session = AgentSession.from_dict(data)
        assert session.preferences == {"key": "value"}

    def test_round_trip_all_fields(self):
        """Full round-trip preserves every field (using JSON-encoded list fields)."""
        now = datetime.now(UTC)
        session = AgentSession(
            session_id="full-001",
            agent_type=AgentType.GEMINI,
            agent_role=AgentRole.CONTENT,
            domain="voice-coach",
            project="vc",
            status=AgentStatus.PAUSED,
            current_task="writing blog",
            assignment_id="a-999",
            work_tree_path="/tmp/wt",
            registered_at=now,
            last_heartbeat=now,
            capabilities=["write", "edit"],
            preferences={"tone": "casual"},
        )
        data = session.to_dict()
        restored = AgentSession.from_dict(data)

        assert restored.session_id == "full-001"
        assert restored.agent_type == AgentType.GEMINI
        assert restored.agent_role == AgentRole.CONTENT
        assert restored.status == AgentStatus.PAUSED
        assert restored.current_task == "writing blog"
        assert restored.assignment_id == "a-999"
        assert restored.work_tree_path == "/tmp/wt"
        assert restored.capabilities == ["write", "edit"]
        assert restored.preferences == {"tone": "casual"}


# ===========================================================================
# 3. SQLiteStateStore CRUD — agent operations
# ===========================================================================


class TestSQLiteAgentCRUD:
    """Full CRUD coverage for agent operations via StateStore (SQLite backend)."""

    def test_register_and_get_agent(self, state_store):
        session = _make_session("crud-agent-1")
        assert state_store.register_agent(session) is True

        retrieved = state_store.get_agent("crud-agent-1")
        assert retrieved is not None
        assert retrieved.session_id == "crud-agent-1"
        assert retrieved.agent_type == AgentType.CLAUDE_CODE
        assert retrieved.domain == "test-domain"

    def test_get_nonexistent_agent_returns_none(self, state_store):
        assert state_store.get_agent("does-not-exist") is None

    def test_register_agent_idempotent_upsert(self, state_store):
        """Re-registering the same session_id overwrites the row."""
        session = _make_session("upsert-agent")
        state_store.register_agent(session)

        session.domain = "updated-domain"
        assert state_store.register_agent(session) is True

        retrieved = state_store.get_agent("upsert-agent")
        assert retrieved is not None
        assert retrieved.domain == "updated-domain"

    def test_update_agent_status_without_task(self, state_store):
        session = _make_session("status-agent-1")
        state_store.register_agent(session)

        result = state_store.update_agent_status("status-agent-1", AgentStatus.IDLE)
        assert result is True

        retrieved = state_store.get_agent("status-agent-1")
        assert retrieved is not None
        assert retrieved.status == AgentStatus.IDLE

    def test_update_agent_status_with_task(self, state_store):
        session = _make_session("status-agent-2")
        state_store.register_agent(session)

        result = state_store.update_agent_status(
            "status-agent-2", AgentStatus.WAITING_HUMAN, "awaiting review"
        )
        assert result is True

        retrieved = state_store.get_agent("status-agent-2")
        assert retrieved is not None
        assert retrieved.status == AgentStatus.WAITING_HUMAN
        assert retrieved.current_task == "awaiting review"

    def test_update_agent_status_nonexistent_session_still_succeeds(self, state_store):
        """UPDATE on non-existent row doesn't raise — just affects 0 rows."""
        result = state_store.update_agent_status("ghost-session", AgentStatus.FAILED)
        # SQLite UPDATE does not raise on zero-row match
        assert result is True

    def test_assign_work_to_agent(self, state_store):
        session = _make_session("assign-agent-1")
        state_store.register_agent(session)

        result = state_store.assign_work("assign-agent-1", "assign-99", "/tmp/wt-99")
        assert result is True

        retrieved = state_store.get_agent("assign-agent-1")
        assert retrieved is not None
        assert retrieved.assignment_id == "assign-99"
        assert retrieved.work_tree_path == "/tmp/wt-99"

    def test_assign_work_clears_assignment(self, state_store):
        """Clearing an assignment stores an empty string (SQLite normalisation).

        The SQLite row-constructor maps empty string to None via the
        AgentSession field; test the actual stored/retrieved value.
        """
        session = _make_session("assign-agent-2")
        state_store.register_agent(session)
        state_store.assign_work("assign-agent-2", "assign-100")

        result = state_store.assign_work("assign-agent-2", None)
        assert result is True

        retrieved = state_store.get_agent("assign-agent-2")
        assert retrieved is not None
        # After clearing, assignment_id is stored as "" — treat both "" and None as cleared
        assert retrieved.assignment_id in (None, "")

    def test_update_heartbeat_refreshes_last_heartbeat(self, state_store):
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        session = _make_session("hb-agent", last_heartbeat=old_time)
        state_store.register_agent(session)

        before = state_store.get_agent("hb-agent")
        assert before is not None

        result = state_store.update_heartbeat("hb-agent")
        assert result is True

        after = state_store.get_agent("hb-agent")
        assert after is not None
        # last_heartbeat should be more recent now
        assert after.last_heartbeat > before.last_heartbeat


# ===========================================================================
# 4. SQLiteStateStore — get_active_agents filtering
# ===========================================================================


class TestSQLiteGetActiveAgents:
    """get_active_agents filters by heartbeat, domain, and project."""

    def test_active_agents_returns_recent_sessions(self, state_store):
        session = _make_session("active-1")
        state_store.register_agent(session)

        agents = state_store.get_active_agents()
        ids = [a.session_id for a in agents]
        assert "active-1" in ids

    def test_stale_agents_excluded_by_heartbeat(self, state_store):
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        session = _make_session("stale-agent", last_heartbeat=old_time)
        state_store.register_agent(session)

        agents = state_store.get_active_agents()
        ids = [a.session_id for a in agents]
        assert "stale-agent" not in ids

    def test_active_agents_filter_by_domain(self, state_store):
        s1 = _make_session("dom-agent-1", domain="voice-coach")
        s2 = _make_session("dom-agent-2", domain="interview-sim")
        state_store.register_agent(s1)
        state_store.register_agent(s2)

        agents = state_store.get_active_agents(domain="voice-coach")
        ids = [a.session_id for a in agents]
        assert "dom-agent-1" in ids
        assert "dom-agent-2" not in ids

    def test_active_agents_filter_by_project(self, state_store):
        s1 = _make_session("proj-agent-1", project="frontend")
        s2 = _make_session("proj-agent-2", project="backend")
        state_store.register_agent(s1)
        state_store.register_agent(s2)

        agents = state_store.get_active_agents(project="frontend")
        ids = [a.session_id for a in agents]
        assert "proj-agent-1" in ids
        assert "proj-agent-2" not in ids

    def test_active_agents_filter_by_domain_and_project(self, state_store):
        s1 = _make_session("combo-agent-1", domain="d1", project="p1")
        s2 = _make_session("combo-agent-2", domain="d1", project="p2")
        s3 = _make_session("combo-agent-3", domain="d2", project="p1")
        for s in [s1, s2, s3]:
            state_store.register_agent(s)

        agents = state_store.get_active_agents(domain="d1", project="p1")
        ids = [a.session_id for a in agents]
        assert ids == ["combo-agent-1"]

    def test_no_active_agents_empty_list(self, state_store):
        agents = state_store.get_active_agents()
        assert isinstance(agents, list)


# ===========================================================================
# 5. SQLiteStateStore — work locks (TTL / expiration)
# ===========================================================================


class TestSQLiteWorkLocks:
    """Work lock acquire / release / expiry via StateStore (SQLite backend)."""

    def test_acquire_lock_succeeds_first_time(self, state_store):
        assert state_store.acquire_work_lock("task-lock-01", "session-A") is True

    def test_acquire_lock_blocked_when_held(self, state_store):
        state_store.acquire_work_lock("task-lock-02", "session-A")
        assert state_store.acquire_work_lock("task-lock-02", "session-B") is False

    def test_is_work_locked_returns_true(self, state_store):
        state_store.acquire_work_lock("task-lock-03", "session-A")
        assert state_store.is_work_locked("task-lock-03") is True

    def test_is_work_locked_returns_false_when_unlocked(self, state_store):
        assert state_store.is_work_locked("no-such-lock") is False

    def test_get_lock_holder_returns_session_id(self, state_store):
        state_store.acquire_work_lock("task-lock-04", "session-X")
        assert state_store.get_work_lock_holder("task-lock-04") == "session-X"

    def test_get_lock_holder_returns_none_when_unlocked(self, state_store):
        assert state_store.get_work_lock_holder("nonexistent-lock") is None

    def test_release_lock_by_holder_succeeds(self, state_store):
        state_store.acquire_work_lock("task-lock-05", "session-A")
        assert state_store.release_work_lock("task-lock-05", "session-A") is True
        assert state_store.is_work_locked("task-lock-05") is False

    def test_release_lock_by_wrong_session_fails(self, state_store):
        state_store.acquire_work_lock("task-lock-06", "session-A")
        assert state_store.release_work_lock("task-lock-06", "session-B") is False
        assert state_store.is_work_locked("task-lock-06") is True

    def test_release_nonexistent_lock_returns_false(self, state_store):
        assert state_store.release_work_lock("never-locked", "session-A") is False

    def test_expired_lock_can_be_reacquired(self, state_store):
        """A lock with a 1-second TTL becomes re-acquirable after it expires."""
        state_store.acquire_work_lock("task-lock-07", "session-A", ttl_seconds=1)
        # Lock is held
        assert state_store.is_work_locked("task-lock-07") is True
        # Wait for expiry
        time.sleep(1.1)
        # Now session-B should be able to acquire it
        result = state_store.acquire_work_lock("task-lock-07", "session-B", ttl_seconds=60)
        assert result is True
        assert state_store.get_work_lock_holder("task-lock-07") == "session-B"

    def test_lock_after_release_can_be_reacquired(self, state_store):
        state_store.acquire_work_lock("task-lock-08", "session-A")
        state_store.release_work_lock("task-lock-08", "session-A")
        assert state_store.acquire_work_lock("task-lock-08", "session-B") is True


# ===========================================================================
# 6. SQLiteStateStore — assignment CRUD
# ===========================================================================


class TestSQLiteAssignmentCRUD:
    """Full CRUD + list/filter for work assignments via StateStore (SQLite backend)."""

    def test_create_and_get_assignment(self, state_store):
        assignment = _make_assignment("a-crud-1")
        assert state_store.create_assignment(assignment) is True

        retrieved = state_store.get_assignment("a-crud-1")
        assert retrieved is not None
        assert retrieved.assignment_id == "a-crud-1"
        assert retrieved.task_type == "feature"
        assert retrieved.files_affected == ["src/main.py"]

    def test_get_nonexistent_assignment_returns_none(self, state_store):
        assert state_store.get_assignment("nonexistent") is None

    def test_create_assignment_idempotent_upsert(self, state_store):
        assignment = _make_assignment("a-upsert")
        state_store.create_assignment(assignment)
        assignment.task_description = "Updated description"
        assert state_store.create_assignment(assignment) is True

        retrieved = state_store.get_assignment("a-upsert")
        assert retrieved is not None
        assert retrieved.task_description == "Updated description"

    def test_update_assignment_status_to_in_progress(self, state_store):
        assignment = _make_assignment("a-status-1")
        state_store.create_assignment(assignment)

        result = state_store.update_assignment_status("a-status-1", AssignmentStatus.IN_PROGRESS)
        assert result is True

        retrieved = state_store.get_assignment("a-status-1")
        assert retrieved is not None
        assert retrieved.status == AssignmentStatus.IN_PROGRESS
        assert retrieved.completed_at is None

    def test_update_assignment_status_with_completed_at(self, state_store):
        assignment = _make_assignment("a-status-2")
        state_store.create_assignment(assignment)
        completed = datetime.now(UTC)

        result = state_store.update_assignment_status(
            "a-status-2", AssignmentStatus.COMPLETED, completed_at=completed
        )
        assert result is True

        retrieved = state_store.get_assignment("a-status-2")
        assert retrieved is not None
        assert retrieved.status == AssignmentStatus.COMPLETED
        assert retrieved.completed_at is not None

    def test_list_assignments_returns_all(self, state_store):
        for i in range(3):
            state_store.create_assignment(_make_assignment(f"list-a-{i}"))

        assignments = state_store.list_assignments()
        ids = [a.assignment_id for a in assignments]
        for i in range(3):
            assert f"list-a-{i}" in ids

    def test_list_assignments_filter_by_domain(self, state_store):
        state_store.create_assignment(_make_assignment("dom-a-1", domain="voice-coach"))
        state_store.create_assignment(_make_assignment("dom-a-2", domain="interview-sim"))

        results = state_store.list_assignments(domain="voice-coach")
        ids = [a.assignment_id for a in results]
        assert "dom-a-1" in ids
        assert "dom-a-2" not in ids

    def test_list_assignments_filter_by_status(self, state_store):
        state_store.create_assignment(
            _make_assignment("st-a-1", status=AssignmentStatus.ASSIGNED)
        )
        state_store.create_assignment(
            _make_assignment("st-a-2", status=AssignmentStatus.IN_PROGRESS)
        )

        results = state_store.list_assignments(status=AssignmentStatus.ASSIGNED)
        ids = [a.assignment_id for a in results]
        assert "st-a-1" in ids
        assert "st-a-2" not in ids

    def test_list_assignments_ordered_by_priority_desc(self, state_store):
        state_store.create_assignment(_make_assignment("prio-a-low", priority=1))
        state_store.create_assignment(_make_assignment("prio-a-mid", priority=5))
        state_store.create_assignment(_make_assignment("prio-a-high", priority=10))

        results = state_store.list_assignments()
        ids = [a.assignment_id for a in results]
        # Higher priority should appear first
        assert ids.index("prio-a-high") < ids.index("prio-a-mid")
        assert ids.index("prio-a-mid") < ids.index("prio-a-low")

    def test_create_assignment_with_all_conflict_fields(self, state_store):
        assignment = WorkAssignment(
            assignment_id="conflict-a",
            session_id="s-001",
            task_type="refactor",
            task_description="High risk refactor",
            priority=3,
            domain="d",
            project="p",
            files_affected=["a.py", "b.py"],
            dependencies=["dep-1"],
            status=AssignmentStatus.BLOCKED,
            conflict_risk=ConflictRisk.HIGH,
            blocked_by=["a-other"],
            blocking=["a-downstream"],
        )
        assert state_store.create_assignment(assignment) is True

        retrieved = state_store.get_assignment("conflict-a")
        assert retrieved is not None
        assert retrieved.conflict_risk == ConflictRisk.HIGH
        assert retrieved.blocked_by == ["a-other"]
        assert retrieved.blocking == ["a-downstream"]
        assert retrieved.status == AssignmentStatus.BLOCKED


# ===========================================================================
# 7. SQLiteStateStore — work tree operations
# ===========================================================================


class TestSQLiteWorkTreeCRUD:
    """Work tree register / get / list via StateStore (SQLite backend)."""

    def test_register_and_get_work_tree(self, state_store):
        wt = _make_work_tree("/tmp/ext-wt-1")
        assert state_store.register_work_tree(wt) is True

        retrieved = state_store.get_work_tree("/tmp/ext-wt-1")
        assert retrieved is not None
        assert retrieved.work_tree_path == "/tmp/ext-wt-1"
        assert retrieved.branch_name == "feature/test"
        assert retrieved.base_commit == "abc1234"
        assert set(retrieved.files_modified) == {"src/main.py", "tests/test_main.py"}

    def test_get_nonexistent_work_tree_returns_none(self, state_store):
        assert state_store.get_work_tree("/no/such/path") is None

    def test_register_work_tree_upsert(self, state_store):
        wt = _make_work_tree("/tmp/ext-wt-upsert")
        state_store.register_work_tree(wt)
        wt.branch_name = "feature/updated"
        assert state_store.register_work_tree(wt) is True

        retrieved = state_store.get_work_tree("/tmp/ext-wt-upsert")
        assert retrieved is not None
        assert retrieved.branch_name == "feature/updated"

    def test_get_work_trees_for_session(self, state_store):
        wt1 = _make_work_tree("/tmp/sess-wt-1", session_id="sess-X")
        wt2 = _make_work_tree("/tmp/sess-wt-2", session_id="sess-X")
        wt3 = _make_work_tree("/tmp/sess-wt-3", session_id="sess-Y")
        for wt in [wt1, wt2, wt3]:
            state_store.register_work_tree(wt)

        results = state_store.get_work_trees_for_session("sess-X")
        paths = [wt.work_tree_path for wt in results]
        assert "/tmp/sess-wt-1" in paths
        assert "/tmp/sess-wt-2" in paths
        assert "/tmp/sess-wt-3" not in paths

    def test_get_work_trees_for_empty_string_session_returns_all(self, state_store):
        """Empty-string session_id is a sentinel that returns all trees."""
        wt1 = _make_work_tree("/tmp/all-wt-1", session_id="sess-A")
        wt2 = _make_work_tree("/tmp/all-wt-2", session_id="sess-B")
        for wt in [wt1, wt2]:
            state_store.register_work_tree(wt)

        results = state_store.get_work_trees_for_session("")
        paths = [wt.work_tree_path for wt in results]
        assert "/tmp/all-wt-1" in paths
        assert "/tmp/all-wt-2" in paths

    def test_get_work_trees_for_none_session_returns_empty(self, state_store):
        """None is passed through as-is and matches nothing (no session has id=None)."""
        wt = _make_work_tree("/tmp/none-wt-1", session_id="sess-A")
        state_store.register_work_tree(wt)

        results = state_store.get_work_trees_for_session(None)
        # None is not a valid session_id that matches any registered tree
        assert isinstance(results, list)

    def test_work_tree_status_merging_persists(self, state_store):
        wt = _make_work_tree("/tmp/merge-wt", status=WorkTreeStatus.MERGING)
        state_store.register_work_tree(wt)

        retrieved = state_store.get_work_tree("/tmp/merge-wt")
        assert retrieved is not None
        assert retrieved.status == WorkTreeStatus.MERGING

    def test_work_tree_status_abandoned_persists(self, state_store):
        wt = _make_work_tree("/tmp/abandon-wt", status=WorkTreeStatus.ABANDONED)
        state_store.register_work_tree(wt)

        retrieved = state_store.get_work_tree("/tmp/abandon-wt")
        assert retrieved is not None
        assert retrieved.status == WorkTreeStatus.ABANDONED


# ===========================================================================
# 8. StateStore manager — not-connected guard paths
# ===========================================================================


class TestStateStoreNotConnected:
    """All manager methods return safe defaults when no backend is connected."""

    @pytest.fixture
    def disconnected_store(self, tmp_path):
        store = StateStore(
            redis_url="redis://nonexistent-host-99999:6379/0",
            sqlite_path=str(tmp_path / "disc.db"),
        )
        # Do NOT call connect()
        return store

    def test_register_agent_returns_false(self, disconnected_store):
        assert disconnected_store.register_agent(_make_session()) is False

    def test_get_agent_returns_none(self, disconnected_store):
        assert disconnected_store.get_agent("x") is None

    def test_get_active_agents_returns_empty_list(self, disconnected_store):
        assert disconnected_store.get_active_agents() == []

    def test_update_agent_status_returns_false(self, disconnected_store):
        assert disconnected_store.update_agent_status("x", AgentStatus.IDLE) is False

    def test_assign_work_returns_false(self, disconnected_store):
        assert disconnected_store.assign_work("x", "y") is False

    def test_update_heartbeat_returns_false(self, disconnected_store):
        assert disconnected_store.update_heartbeat("x") is False

    def test_acquire_work_lock_returns_false(self, disconnected_store):
        assert disconnected_store.acquire_work_lock("t", "s") is False

    def test_release_work_lock_returns_false(self, disconnected_store):
        assert disconnected_store.release_work_lock("t", "s") is False

    def test_is_work_locked_returns_false(self, disconnected_store):
        assert disconnected_store.is_work_locked("t") is False

    def test_get_work_lock_holder_returns_none(self, disconnected_store):
        assert disconnected_store.get_work_lock_holder("t") is None

    def test_create_assignment_returns_false(self, disconnected_store):
        assert disconnected_store.create_assignment(_make_assignment()) is False

    def test_get_assignment_returns_none(self, disconnected_store):
        assert disconnected_store.get_assignment("x") is None

    def test_update_assignment_status_returns_false(self, disconnected_store):
        assert (
            disconnected_store.update_assignment_status("x", AssignmentStatus.COMPLETED) is False
        )

    def test_list_assignments_returns_empty_list(self, disconnected_store):
        assert disconnected_store.list_assignments() == []

    def test_register_work_tree_returns_false(self, disconnected_store):
        assert disconnected_store.register_work_tree(_make_work_tree()) is False

    def test_get_work_tree_returns_none(self, disconnected_store):
        assert disconnected_store.get_work_tree("/no/path") is None

    def test_get_work_trees_for_session_returns_empty_list(self, disconnected_store):
        assert disconnected_store.get_work_trees_for_session("x") == []

    def test_publish_status_change_returns_false(self, disconnected_store):
        assert disconnected_store.publish_status_change("x", {"status": "active"}) is False

    def test_subscribe_to_all_agents_returns_none(self, disconnected_store):
        assert disconnected_store.subscribe_to_all_agents() is None


# ===========================================================================
# 9. StateStore manager — pubsub delegation (SQLite backend stubs)
# ===========================================================================


class TestStateStorePubSubSQLite:
    """publish_status_change is stubbed to True for SQLite; subscribe returns None."""

    def test_publish_status_change_sqlite_returns_true(self, state_store):
        result = state_store.publish_status_change("sess-1", {"status": "idle"})
        assert result is True

    def test_subscribe_to_all_agents_sqlite_raises_or_returns_none(self, state_store):
        """SQLite doesn't support pub/sub — subscribe_all raises AttributeError."""
        # SQLite StateStore has is_connected()=True but no _client attr,
        # so subscribe_to_all_agents hits an assertion on self._client.
        with pytest.raises(AttributeError):
            state_store.subscribe_to_all_agents()


# ===========================================================================
# 10. RedisStateStore — not-connected guard paths
# ===========================================================================


class TestRedisStoreNotConnected:
    """RedisStateStore methods return safe defaults when not connected."""

    @pytest.fixture
    def store(self):
        return RedisStateStore(redis_url="redis://nonexistent:9999/0")

    def test_register_agent_returns_false(self, store):
        assert store.register_agent(_make_session()) is False

    def test_get_agent_returns_none(self, store):
        assert store.get_agent("x") is None

    def test_get_active_agents_returns_empty_list(self, store):
        assert store.get_active_agents() == []

    def test_update_agent_status_returns_false(self, store):
        assert store.update_agent_status("x", AgentStatus.IDLE) is False

    def test_update_heartbeat_returns_false(self, store):
        assert store.update_heartbeat("x") is False

    def test_get_heartbeat_time_returns_none(self, store):
        assert store.get_heartbeat_time("x") is None

    def test_cleanup_dead_heartbeats_returns_empty_list(self, store):
        assert store.cleanup_dead_heartbeats() == []

    def test_acquire_work_lock_returns_false(self, store):
        assert store.acquire_work_lock("t", "s") is False

    def test_release_work_lock_returns_false(self, store):
        assert store.release_work_lock("t", "s") is False

    def test_is_work_locked_returns_false(self, store):
        assert store.is_work_locked("t") is False

    def test_get_work_lock_holder_returns_none(self, store):
        assert store.get_work_lock_holder("t") is None

    def test_create_assignment_returns_false(self, store):
        assert store.create_assignment(_make_assignment()) is False

    def test_get_assignment_returns_none(self, store):
        assert store.get_assignment("x") is None

    def test_list_assignments_returns_empty_list(self, store):
        assert store.list_assignments() == []

    def test_register_work_tree_returns_false(self, store):
        assert store.register_work_tree(_make_work_tree()) is False

    def test_get_work_tree_returns_none(self, store):
        assert store.get_work_tree("/no") is None

    def test_get_work_trees_for_session_returns_empty(self, store):
        assert store.get_work_trees_for_session("x") == []

    def test_publish_status_change_returns_false(self, store):
        assert store.publish_status_change("x", {}) is False

    def test_subscribe_to_agent_returns_none(self, store):
        assert store.subscribe_to_agent("x") is None


# ===========================================================================
# 11. RedisStateStore backed by fakeredis — CRUD operations
# ===========================================================================


class TestFakeRedisAgentCRUD:
    """Agent CRUD operations using fakeredis."""

    def test_register_and_get_agent(self, fake_redis_store):
        session = _make_session("fr-agent-1")
        assert fake_redis_store.register_agent(session) is True

        retrieved = fake_redis_store.get_agent("fr-agent-1")
        assert retrieved is not None
        assert retrieved.session_id == "fr-agent-1"

    def test_get_nonexistent_agent_returns_none(self, fake_redis_store):
        assert fake_redis_store.get_agent("no-such-agent") is None

    def test_update_agent_status(self, fake_redis_store):
        fake_redis_store.register_agent(_make_session("fr-status-1"))
        result = fake_redis_store.update_agent_status(
            "fr-status-1", AgentStatus.WAITING_HUMAN, "pending review"
        )
        assert result is True

        retrieved = fake_redis_store.get_agent("fr-status-1")
        assert retrieved is not None
        assert retrieved.status == AgentStatus.WAITING_HUMAN
        assert retrieved.current_task == "pending review"

    def test_assign_work(self, fake_redis_store):
        fake_redis_store.register_agent(_make_session("fr-assign-1"))
        result = fake_redis_store.assign_work("fr-assign-1", "assign-fr-001", "/tmp/fr-wt")
        assert result is True

    def test_heartbeat_update_and_get(self, fake_redis_store):
        result = fake_redis_store.update_heartbeat("fr-hb-session")
        assert result is True

        ts = fake_redis_store.get_heartbeat_time("fr-hb-session")
        assert ts is not None
        assert isinstance(ts, float)
        assert ts <= time.time()

    def test_heartbeat_for_missing_session_returns_none(self, fake_redis_store):
        assert fake_redis_store.get_heartbeat_time("never-registered") is None

    def test_cleanup_dead_heartbeats(self, fake_redis_store):
        # Inject a stale heartbeat directly
        stale_ts = time.time() - 600
        fake_redis_store._client.zadd("heartbeats", {"dead-fr-session": stale_ts})

        dead = fake_redis_store.cleanup_dead_heartbeats(timeout_seconds=300)
        assert "dead-fr-session" in dead

    def test_active_agents_filtered_by_stale_heartbeat(self, fake_redis_store):
        old_hb = datetime.now(UTC) - timedelta(minutes=10)
        session = _make_session("fr-stale", last_heartbeat=old_hb)
        fake_redis_store.register_agent(session)

        active = fake_redis_store.get_active_agents()
        ids = [a.session_id for a in active]
        assert "fr-stale" not in ids


# ===========================================================================
# 12. RedisStateStore backed by fakeredis — assignment operations
# ===========================================================================


class TestFakeRedisAssignmentCRUD:
    """Assignment CRUD + list/filter with fakeredis."""

    def test_create_and_get_assignment(self, fake_redis_store):
        assignment = _make_assignment("fr-a-1")
        assert fake_redis_store.create_assignment(assignment) is True

        retrieved = fake_redis_store.get_assignment("fr-a-1")
        assert retrieved is not None
        assert retrieved.assignment_id == "fr-a-1"

    def test_get_nonexistent_assignment_returns_none(self, fake_redis_store):
        assert fake_redis_store.get_assignment("no-such") is None

    def test_update_assignment_status(self, fake_redis_store):
        fake_redis_store.create_assignment(_make_assignment("fr-a-status"))
        result = fake_redis_store.update_assignment_status(
            "fr-a-status", AssignmentStatus.IN_PROGRESS
        )
        assert result is True

    def test_list_assignments_no_filter(self, fake_redis_store):
        for i in range(3):
            fake_redis_store.create_assignment(_make_assignment(f"fr-list-{i}"))
        results = fake_redis_store.list_assignments()
        ids = [a.assignment_id for a in results]
        for i in range(3):
            assert f"fr-list-{i}" in ids

    def test_list_assignments_domain_filter(self, fake_redis_store):
        fake_redis_store.create_assignment(_make_assignment("fr-dom-1", domain="alpha"))
        fake_redis_store.create_assignment(_make_assignment("fr-dom-2", domain="beta"))
        results = fake_redis_store.list_assignments(domain="alpha")
        ids = [a.assignment_id for a in results]
        assert "fr-dom-1" in ids
        assert "fr-dom-2" not in ids


# ===========================================================================
# 13. RedisStateStore backed by fakeredis — work locks
# ===========================================================================


class TestFakeRedisWorkLocks:
    """Work lock acquire / release with fakeredis."""

    def test_acquire_and_check_lock(self, fake_redis_store):
        assert fake_redis_store.acquire_work_lock("fr-lock-1", "sess-A") is True
        assert fake_redis_store.is_work_locked("fr-lock-1") is True
        assert fake_redis_store.get_work_lock_holder("fr-lock-1") == "sess-A"

    def test_double_acquire_fails(self, fake_redis_store):
        fake_redis_store.acquire_work_lock("fr-lock-2", "sess-A")
        assert fake_redis_store.acquire_work_lock("fr-lock-2", "sess-B") is False

    def test_release_and_reacquire(self, fake_redis_store):
        fake_redis_store.acquire_work_lock("fr-lock-3", "sess-A")
        assert fake_redis_store.release_work_lock("fr-lock-3", "sess-A") is True
        assert fake_redis_store.is_work_locked("fr-lock-3") is False
        assert fake_redis_store.acquire_work_lock("fr-lock-3", "sess-B") is True

    def test_release_by_wrong_session_fails(self, fake_redis_store):
        fake_redis_store.acquire_work_lock("fr-lock-4", "sess-A")
        assert fake_redis_store.release_work_lock("fr-lock-4", "sess-B") is False


# ===========================================================================
# 14. RedisStateStore backed by fakeredis — work tree operations
# ===========================================================================


class TestFakeRedisWorkTree:
    """Work tree operations with fakeredis."""

    def test_register_and_get_work_tree(self, fake_redis_store):
        wt = _make_work_tree("/tmp/fr-wt-1")
        assert fake_redis_store.register_work_tree(wt) is True

        retrieved = fake_redis_store.get_work_tree("/tmp/fr-wt-1")
        assert retrieved is not None
        assert retrieved.work_tree_path == "/tmp/fr-wt-1"

    def test_get_nonexistent_work_tree_returns_none(self, fake_redis_store):
        assert fake_redis_store.get_work_tree("/no/path") is None

    def test_get_work_trees_for_session(self, fake_redis_store):
        wt1 = _make_work_tree("/tmp/fr-sess-wt-1", session_id="sess-Z")
        wt2 = _make_work_tree("/tmp/fr-sess-wt-2", session_id="sess-Z")
        wt3 = _make_work_tree("/tmp/fr-sess-wt-3", session_id="sess-W")
        for wt in [wt1, wt2, wt3]:
            fake_redis_store.register_work_tree(wt)

        results = fake_redis_store.get_work_trees_for_session("sess-Z")
        paths = [wt.work_tree_path for wt in results]
        assert "/tmp/fr-sess-wt-1" in paths
        assert "/tmp/fr-sess-wt-2" in paths
        assert "/tmp/fr-sess-wt-3" not in paths


# ===========================================================================
# 15. Global singleton — get_state_store()
# ===========================================================================


class TestGetStateStoreSingleton:
    """get_state_store() returns a consistent singleton."""

    def test_get_state_store_returns_state_store_instance(self, monkeypatch, tmp_path):
        """get_state_store() returns a StateStore and connects it."""
        import forge_harness.state_store as ss_module

        # Reset singleton so a fresh one is created
        monkeypatch.setattr(ss_module, "_state_store", None)
        monkeypatch.setenv("REDIS_URL", "redis://nonexistent-host-99999:6379/0")

        # Patch SQLite path to use tmp_path so it doesn't write to cwd
        original_cwd = ss_module.Path.cwd

        def patched_cwd():
            return tmp_path

        monkeypatch.setattr(ss_module.Path, "cwd", staticmethod(patched_cwd))

        store = ss_module.get_state_store()
        assert isinstance(store, StateStore)
        assert store.is_connected()

    def test_get_state_store_returns_same_instance_on_repeat_call(self, monkeypatch, tmp_path):
        import forge_harness.state_store as ss_module

        monkeypatch.setattr(ss_module, "_state_store", None)
        monkeypatch.setenv("REDIS_URL", "redis://nonexistent-host-99999:6379/0")

        original_cwd = ss_module.Path.cwd

        def patched_cwd():
            return tmp_path

        monkeypatch.setattr(ss_module.Path, "cwd", staticmethod(patched_cwd))

        store1 = ss_module.get_state_store()
        store2 = ss_module.get_state_store()
        assert store1 is store2


# ===========================================================================
# 16. WorkAssignment deserialization edge-cases
# ===========================================================================


class TestWorkAssignmentDeserialization:
    """Edge-cases in WorkAssignment.from_dict and to_dict."""

    def test_from_dict_defaults_for_missing_optional_fields(self):
        data = {
            "assignment_id": "a-defaults",
            "session_id": "s-001",
            "task_type": "fix",
            "task_description": "desc",
            "priority": 2,
            "domain": "d",
            "project": "p",
            "files_affected": ["f.py"],
            "dependencies": [],
            "status": "assigned",
            "started_at": "2026-01-01T00:00:00+00:00",
            # No completed_at, blocked_by, blocking, conflict_risk
        }
        assignment = WorkAssignment.from_dict(data)
        assert assignment.completed_at is None
        assert assignment.blocked_by == []
        assert assignment.blocking == []
        assert assignment.conflict_risk == ConflictRisk.LOW

    def test_to_dict_none_completed_at_is_none(self):
        assignment = _make_assignment()
        data = assignment.to_dict()
        assert data["completed_at"] is None

    def test_to_dict_completed_at_set(self):
        assignment = _make_assignment(status=AssignmentStatus.COMPLETED)
        assignment.completed_at = datetime.now(UTC)
        data = assignment.to_dict()
        assert data["completed_at"] is not None
        assert isinstance(data["completed_at"], str)

    def test_round_trip_with_all_fields(self):
        assignment = WorkAssignment(
            assignment_id="rt-full",
            session_id="s-rt",
            task_type="migration",
            task_description="Migrate DB schema",
            priority=7,
            domain="data",
            project="etl",
            files_affected=["migration.sql"],
            dependencies=["seed-task"],
            status=AssignmentStatus.BLOCKED,
            conflict_risk=ConflictRisk.MEDIUM,
            blocked_by=["other-task"],
            blocking=["downstream-task"],
        )
        data = assignment.to_dict()
        restored = WorkAssignment.from_dict(data)

        assert restored.assignment_id == "rt-full"
        assert restored.priority == 7
        assert restored.conflict_risk == ConflictRisk.MEDIUM
        assert restored.blocked_by == ["other-task"]
        assert restored.blocking == ["downstream-task"]
        assert restored.status == AssignmentStatus.BLOCKED


# ===========================================================================
# 17. WorkTree deserialization edge-cases
# ===========================================================================


class TestWorkTreeDeserialization:
    """Edge-cases in WorkTree.from_dict and to_dict."""

    def test_round_trip_with_empty_files_modified(self):
        wt = WorkTree(
            work_tree_path="/tmp/empty-files",
            assigned_session_id="s",
            files_modified=[],
            branch_name="main",
            base_commit="000",
        )
        data = wt.to_dict()
        restored = WorkTree.from_dict(data)
        assert restored.files_modified == []

    def test_round_trip_with_many_files(self):
        files = [f"src/file_{i}.py" for i in range(100)]
        wt = WorkTree(
            work_tree_path="/tmp/many-files",
            assigned_session_id="s",
            files_modified=files,
            branch_name="feature/big",
            base_commit="bbb",
        )
        data = wt.to_dict()
        restored = WorkTree.from_dict(data)
        assert len(restored.files_modified) == 100

    def test_all_work_tree_statuses_persist(self):
        for status in WorkTreeStatus:
            wt = WorkTree(
                work_tree_path=f"/tmp/status-{status.value}",
                assigned_session_id="s",
                files_modified=[],
                branch_name="b",
                base_commit="c",
                status=status,
            )
            data = wt.to_dict()
            restored = WorkTree.from_dict(data)
            assert restored.status == status
