"""
Comprehensive tests for forge_harness/models/database.py

Tests cover:
- generate_uuid() helper
- All ORM model classes and their column definitions
- Default values for columns
- Relationship declarations
- Table names
- Primary keys, nullable constraints, and foreign keys
- In-memory SQLite database round-trips for CRUD operations
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from forge_harness.models.database import (
    Agent,
    AgentCapability,
    Base,
    CCAgent,
    CCTask,
    Node,
    SyncLog,
    Task,
    TaskAssignment,
    TaskHistory,
    TaskRecommendation,
    generate_uuid,
)
from forge_harness.models.database import (
    Session as SessionModel,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    """Create an in-memory SQLite engine and build all tables once per module."""
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db_session(engine):
    """Provide a transactional session that is rolled back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# generate_uuid
# ---------------------------------------------------------------------------


class TestGenerateUuid:
    """Tests for the generate_uuid helper function."""

    def test_returns_string(self):
        result = generate_uuid()
        assert isinstance(result, str)

    def test_returns_valid_uuid_v4(self):
        result = generate_uuid()
        parsed = uuid.UUID(result, version=4)
        # Round-trip: str(parsed) should equal result (canonical form)
        assert str(parsed) == result

    def test_each_call_returns_unique_value(self):
        ids = {generate_uuid() for _ in range(100)}
        assert len(ids) == 100

    def test_length_is_36_chars(self):
        result = generate_uuid()
        assert len(result) == 36

    def test_contains_four_hyphens(self):
        result = generate_uuid()
        assert result.count("-") == 4


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class TestBase:
    """Tests for the DeclarativeBase subclass."""

    def test_base_is_not_mapped_to_table(self):
        # Base itself should have no __tablename__
        assert not hasattr(Base, "__tablename__")

    def test_metadata_contains_all_tables(self):
        expected_tables = {
            "tasks",
            "agents",
            "agent_capabilities",
            "task_recommendations",
            "task_history",
            "cc_agents",
            "cc_tasks",
            "task_assignments",
            "sessions",
            "nodes",
            "sync_log",
        }
        assert expected_tables == set(Base.metadata.tables.keys())


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------


class TestTaskModel:
    """Tests for the Task (legacy) ORM model."""

    def test_tablename(self):
        assert Task.__tablename__ == "tasks"

    def test_primary_key_column(self):
        col = Task.__table__.c["id"]
        assert col.primary_key
        assert str(col.type) == "VARCHAR(36)"

    def test_subject_is_not_nullable(self):
        col = Task.__table__.c["subject"]
        assert not col.nullable

    def test_description_is_nullable(self):
        col = Task.__table__.c["description"]
        assert col.nullable

    def test_priority_default(self):
        col = Task.__table__.c["priority"]
        assert col.default.arg == 5

    def test_status_default(self):
        col = Task.__table__.c["status"]
        assert col.default.arg == "pending"

    def test_task_type_default(self):
        col = Task.__table__.c["task_type"]
        assert col.default.arg == "general"

    def test_complexity_default(self):
        col = Task.__table__.c["complexity"]
        assert col.default.arg == "2"

    def test_dependencies_default(self):
        col = Task.__table__.c["dependencies"]
        assert col.default.arg == "[]"

    def test_blocked_default(self):
        col = Task.__table__.c["blocked"]
        assert col.default.arg is False

    def test_timestamp_columns_are_nullable(self):
        for col_name in ("created_at", "updated_at", "started_at", "completed_at"):
            col = Task.__table__.c[col_name]
            assert col.nullable, f"{col_name} should be nullable"

    def test_relationships_defined(self):
        rels = {r.key for r in Task.__mapper__.relationships}
        assert "assigned_agent" in rels
        assert "recommendations" in rels
        assert "history" in rels

    def test_insert_and_query(self, db_session):
        task = Task(
            id=generate_uuid(),
            subject="Fix authentication bug",
            description="Detailed description",
            priority=3,
            domain="backend",
            project="auth-service",
        )
        db_session.add(task)
        db_session.flush()

        fetched = db_session.get(Task, task.id)
        assert fetched is not None
        assert fetched.subject == "Fix authentication bug"
        assert fetched.priority == 3
        assert fetched.status == "pending"

    def test_default_uuid_callable(self, db_session):
        task = Task(subject="Auto-ID task")
        db_session.add(task)
        db_session.flush()
        # id should have been generated automatically
        assert task.id is not None
        assert len(task.id) == 36


# ---------------------------------------------------------------------------
# Agent model
# ---------------------------------------------------------------------------


class TestAgentModel:
    """Tests for the Agent (legacy) ORM model."""

    def test_tablename(self):
        assert Agent.__tablename__ == "agents"

    def test_primary_key_is_string(self):
        col = Agent.__table__.c["id"]
        assert col.primary_key

    def test_name_is_not_nullable(self):
        col = Agent.__table__.c["name"]
        assert not col.nullable

    def test_model_is_not_nullable(self):
        col = Agent.__table__.c["model"]
        assert not col.nullable

    def test_status_default(self):
        col = Agent.__table__.c["status"]
        assert col.default.arg == "idle"

    def test_max_complexity_default(self):
        col = Agent.__table__.c["max_complexity"]
        assert col.default.arg == "3"

    def test_preferred_domains_default(self):
        col = Agent.__table__.c["preferred_domains"]
        assert col.default.arg == "[]"

    def test_numeric_defaults(self):
        assert Agent.__table__.c["total_tasks_completed"].default.arg == 0
        assert Agent.__table__.c["success_rate"].default.arg == 0.0
        assert Agent.__table__.c["avg_task_duration_minutes"].default.arg == 0.0
        assert Agent.__table__.c["avg_quality_score"].default.arg == 0.0

    def test_current_task_id_is_foreign_key(self):
        col = Agent.__table__.c["current_task_id"]
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].column.table.name == "tasks"

    def test_relationships_defined(self):
        rels = {r.key for r in Agent.__mapper__.relationships}
        assert "current_task" in rels
        assert "capabilities" in rels
        assert "recommendations" in rels

    def test_insert_and_query(self, db_session):
        agent = Agent(
            id="agent-001",
            name="Test Agent",
            model="claude-3-sonnet",
            status="active",
        )
        db_session.add(agent)
        db_session.flush()

        fetched = db_session.get(Agent, "agent-001")
        assert fetched.name == "Test Agent"
        assert fetched.model == "claude-3-sonnet"
        assert fetched.total_tasks_completed == 0
        assert fetched.success_rate == 0.0


# ---------------------------------------------------------------------------
# AgentCapability model
# ---------------------------------------------------------------------------


class TestAgentCapabilityModel:
    """Tests for the AgentCapability ORM model."""

    def test_tablename(self):
        assert AgentCapability.__tablename__ == "agent_capabilities"

    def test_primary_key_with_default_uuid(self):
        col = AgentCapability.__table__.c["id"]
        assert col.primary_key

    def test_agent_id_foreign_key(self):
        col = AgentCapability.__table__.c["agent_id"]
        assert not col.nullable
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "agents"

    def test_task_type_is_not_nullable(self):
        col = AgentCapability.__table__.c["task_type"]
        assert not col.nullable

    def test_numeric_defaults(self):
        assert AgentCapability.__table__.c["confidence_score"].default.arg == 0.0
        assert AgentCapability.__table__.c["tasks_completed"].default.arg == 0
        assert AgentCapability.__table__.c["tasks_failed"].default.arg == 0

    def test_last_used_is_nullable(self):
        col = AgentCapability.__table__.c["last_used"]
        assert col.nullable

    def test_relationship_to_agent(self):
        rels = {r.key for r in AgentCapability.__mapper__.relationships}
        assert "agent" in rels

    def test_insert_and_query(self, db_session):
        agent = Agent(id="cap-agent-1", name="Cap Agent", model="gpt-4")
        db_session.add(agent)
        db_session.flush()

        cap = AgentCapability(
            agent_id="cap-agent-1",
            task_type="code_review",
            confidence_score=0.85,
            tasks_completed=10,
            tasks_failed=2,
        )
        db_session.add(cap)
        db_session.flush()

        fetched = db_session.get(AgentCapability, cap.id)
        assert fetched.task_type == "code_review"
        assert fetched.confidence_score == pytest.approx(0.85)
        assert fetched.tasks_completed == 10


# ---------------------------------------------------------------------------
# TaskRecommendation model
# ---------------------------------------------------------------------------


class TestTaskRecommendationModel:
    """Tests for the TaskRecommendation ORM model."""

    def test_tablename(self):
        assert TaskRecommendation.__tablename__ == "task_recommendations"

    def test_primary_key_with_uuid(self):
        col = TaskRecommendation.__table__.c["id"]
        assert col.primary_key

    def test_task_id_foreign_key(self):
        col = TaskRecommendation.__table__.c["task_id"]
        assert not col.nullable
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "tasks"

    def test_agent_id_foreign_key(self):
        col = TaskRecommendation.__table__.c["agent_id"]
        assert not col.nullable
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "agents"

    def test_boolean_defaults(self):
        assert TaskRecommendation.__table__.c["was_recommended"].default.arg is False
        assert TaskRecommendation.__table__.c["was_accepted"].default.arg is False
        assert TaskRecommendation.__table__.c["was_completed"].default.arg is False

    def test_score_defaults(self):
        assert TaskRecommendation.__table__.c["priority_score"].default.arg == 0.0

    def test_nullable_score_columns(self):
        for col_name in ("capability_match_score", "final_score", "quality_score"):
            col = TaskRecommendation.__table__.c[col_name]
            assert col.nullable, f"{col_name} should be nullable"

    def test_relationships(self):
        rels = {r.key for r in TaskRecommendation.__mapper__.relationships}
        assert "task" in rels
        assert "agent" in rels

    def test_insert_and_query(self, db_session):
        task = Task(id=generate_uuid(), subject="Rec task")
        agent = Agent(id="rec-agent-1", name="Rec Agent", model="claude-opus")
        db_session.add_all([task, agent])
        db_session.flush()

        rec = TaskRecommendation(
            task_id=task.id,
            agent_id=agent.id,
            priority_score=0.9,
            capability_match_score=0.75,
            final_score=0.82,
            was_recommended=True,
        )
        db_session.add(rec)
        db_session.flush()

        fetched = db_session.get(TaskRecommendation, rec.id)
        assert fetched.priority_score == pytest.approx(0.9)
        assert fetched.was_recommended is True
        assert fetched.was_accepted is False


# ---------------------------------------------------------------------------
# TaskHistory model
# ---------------------------------------------------------------------------


class TestTaskHistoryModel:
    """Tests for the TaskHistory ORM model."""

    def test_tablename(self):
        assert TaskHistory.__tablename__ == "task_history"

    def test_primary_key(self):
        col = TaskHistory.__table__.c["id"]
        assert col.primary_key

    def test_task_id_foreign_key(self):
        col = TaskHistory.__table__.c["task_id"]
        assert not col.nullable
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "tasks"

    def test_new_status_is_not_nullable(self):
        col = TaskHistory.__table__.c["new_status"]
        assert not col.nullable

    def test_previous_status_is_nullable(self):
        col = TaskHistory.__table__.c["previous_status"]
        assert col.nullable

    def test_optional_columns_are_nullable(self):
        for col_name in ("changed_by", "change_reason", "extra_data", "created_at"):
            col = TaskHistory.__table__.c[col_name]
            assert col.nullable, f"{col_name} should be nullable"

    def test_relationship_to_task(self):
        rels = {r.key for r in TaskHistory.__mapper__.relationships}
        assert "task" in rels

    def test_insert_and_query(self, db_session):
        task = Task(id=generate_uuid(), subject="History task")
        db_session.add(task)
        db_session.flush()

        history = TaskHistory(
            task_id=task.id,
            previous_status="pending",
            new_status="in_progress",
            changed_by="orchestrator",
            change_reason="Started work",
        )
        db_session.add(history)
        db_session.flush()

        fetched = db_session.get(TaskHistory, history.id)
        assert fetched.new_status == "in_progress"
        assert fetched.previous_status == "pending"
        assert fetched.changed_by == "orchestrator"

    def test_extra_data_stores_json_string(self, db_session):
        task = Task(id=generate_uuid(), subject="Extra data task")
        db_session.add(task)
        db_session.flush()

        history = TaskHistory(
            task_id=task.id,
            new_status="completed",
            extra_data='{"duration_minutes": 45}',
        )
        db_session.add(history)
        db_session.flush()

        fetched = db_session.get(TaskHistory, history.id)
        assert fetched.extra_data == '{"duration_minutes": 45}'


# ---------------------------------------------------------------------------
# CCAgent model
# ---------------------------------------------------------------------------


class TestCCAgentModel:
    """Tests for the CCAgent (Command Center) ORM model."""

    def test_tablename(self):
        assert CCAgent.__tablename__ == "cc_agents"

    def test_name_is_not_nullable(self):
        col = CCAgent.__table__.c["name"]
        assert not col.nullable

    def test_status_default(self):
        col = CCAgent.__table__.c["status"]
        assert col.default.arg == "offline"

    def test_skills_default(self):
        col = CCAgent.__table__.c["skills"]
        assert col.default.arg == "[]"

    def test_token_usage_default(self):
        col = CCAgent.__table__.c["token_usage"]
        assert col.default.arg == "{}"

    def test_node_id_default(self):
        col = CCAgent.__table__.c["node_id"]
        assert col.default.arg == "local"

    def test_session_id_is_unique(self):
        col = CCAgent.__table__.c["session_id"]
        # Check uniqueness via table-level unique constraints
        unique_cols = {
            uc.columns.keys()[0]
            for uc in CCAgent.__table__.constraints
            if hasattr(uc, "columns") and len(list(uc.columns)) == 1
        }
        # session_id column has unique=True so it appears in index/constraint
        assert col.unique

    def test_relationships_defined(self):
        rels = {r.key for r in CCAgent.__mapper__.relationships}
        assert "tasks" in rels
        assert "assignments" in rels
        assert "sessions" in rels

    def test_insert_and_query(self, db_session):
        agent = CCAgent(
            id="cc-agent-001",
            name="Nova",
            role="orchestrator",
            status="active",
            skills='["python", "testing"]',
            node_id="prya",
        )
        db_session.add(agent)
        db_session.flush()

        fetched = db_session.get(CCAgent, "cc-agent-001")
        assert fetched.name == "Nova"
        assert fetched.role == "orchestrator"
        assert fetched.status == "active"
        assert fetched.node_id == "prya"

    def test_created_at_has_callable_default(self):
        # created_at uses datetime.utcnow as default
        col = CCAgent.__table__.c["created_at"]
        assert col.default is not None

    def test_optional_columns_nullable(self):
        for col_name in (
            "session_id",
            "role",
            "current_task_id",
            "domain",
            "project",
            "last_activity",
            "tmux_session",
        ):
            col = CCAgent.__table__.c[col_name]
            assert col.nullable, f"{col_name} should be nullable"


# ---------------------------------------------------------------------------
# CCTask model
# ---------------------------------------------------------------------------


class TestCCTaskModel:
    """Tests for the CCTask (Command Center) ORM model."""

    def test_tablename(self):
        assert CCTask.__tablename__ == "cc_tasks"

    def test_title_is_not_nullable(self):
        col = CCTask.__table__.c["title"]
        assert not col.nullable

    def test_status_default(self):
        col = CCTask.__table__.c["status"]
        assert col.default.arg == "pending"

    def test_priority_default(self):
        col = CCTask.__table__.c["priority"]
        assert col.default.arg == 5

    def test_json_column_defaults(self):
        for col_name in ("acceptance_criteria", "deliverables", "blocked_by", "blocks"):
            col = CCTask.__table__.c[col_name]
            assert col.default.arg == "[]", f"{col_name} default should be '[]'"

    def test_assigned_to_foreign_key(self):
        col = CCTask.__table__.c["assigned_to"]
        assert col.nullable  # tasks may be unassigned
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "cc_agents"

    def test_relationships_defined(self):
        rels = {r.key for r in CCTask.__mapper__.relationships}
        assert "agent" in rels
        assert "assignments" in rels

    def test_insert_unassigned_task(self, db_session):
        task = CCTask(id="task-cc-001", title="Implement feature X")
        db_session.add(task)
        db_session.flush()

        fetched = db_session.get(CCTask, "task-cc-001")
        assert fetched.title == "Implement feature X"
        assert fetched.status == "pending"
        assert fetched.priority == 5
        assert fetched.assigned_to is None

    def test_insert_assigned_task(self, db_session):
        agent = CCAgent(id="cc-agent-002", name="Sati", status="active")
        db_session.add(agent)
        db_session.flush()

        task = CCTask(
            id="task-cc-002",
            title="Write tests",
            assigned_to="cc-agent-002",
            priority=1,
            status="in_progress",
        )
        db_session.add(task)
        db_session.flush()

        fetched = db_session.get(CCTask, "task-cc-002")
        assert fetched.assigned_to == "cc-agent-002"
        assert fetched.priority == 1

    def test_timestamp_columns_nullable(self):
        for col_name in ("started_at", "completed_at"):
            col = CCTask.__table__.c[col_name]
            assert col.nullable


# ---------------------------------------------------------------------------
# TaskAssignment model
# ---------------------------------------------------------------------------


class TestTaskAssignmentModel:
    """Tests for the TaskAssignment ORM model."""

    def test_tablename(self):
        assert TaskAssignment.__tablename__ == "task_assignments"

    def test_primary_key_is_autoincrement_integer(self):
        col = TaskAssignment.__table__.c["id"]
        assert col.primary_key
        assert col.autoincrement

    def test_task_id_foreign_key(self):
        col = TaskAssignment.__table__.c["task_id"]
        assert not col.nullable
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "cc_tasks"

    def test_agent_id_foreign_key(self):
        col = TaskAssignment.__table__.c["agent_id"]
        assert not col.nullable
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "cc_agents"

    def test_notification_sent_default(self):
        col = TaskAssignment.__table__.c["notification_sent"]
        assert col.default.arg is False

    def test_completed_at_nullable(self):
        col = TaskAssignment.__table__.c["completed_at"]
        assert col.nullable

    def test_relationships_defined(self):
        rels = {r.key for r in TaskAssignment.__mapper__.relationships}
        assert "task" in rels
        assert "agent" in rels

    def test_insert_and_query(self, db_session):
        agent = CCAgent(id="cc-agent-ta", name="Trinity", status="active")
        task = CCTask(id="task-ta-001", title="Assigned task")
        db_session.add_all([agent, task])
        db_session.flush()

        assignment = TaskAssignment(
            task_id="task-ta-001",
            agent_id="cc-agent-ta",
        )
        db_session.add(assignment)
        db_session.flush()

        fetched = db_session.get(TaskAssignment, assignment.id)
        assert fetched.task_id == "task-ta-001"
        assert fetched.agent_id == "cc-agent-ta"
        assert fetched.notification_sent is False
        assert fetched.id is not None  # autoincrement

    def test_autoincrement_produces_unique_ids(self, db_session):
        agent = CCAgent(id="cc-agent-ta2", name="Kilo", status="active")
        task1 = CCTask(id="task-ta-10", title="Task 10")
        task2 = CCTask(id="task-ta-11", title="Task 11")
        db_session.add_all([agent, task1, task2])
        db_session.flush()

        a1 = TaskAssignment(task_id="task-ta-10", agent_id="cc-agent-ta2")
        a2 = TaskAssignment(task_id="task-ta-11", agent_id="cc-agent-ta2")
        db_session.add_all([a1, a2])
        db_session.flush()

        assert a1.id != a2.id


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------


class TestSessionModel:
    """Tests for the Session (work session) ORM model."""

    def test_tablename(self):
        assert SessionModel.__tablename__ == "sessions"

    def test_agent_id_foreign_key(self):
        col = SessionModel.__table__.c["agent_id"]
        assert not col.nullable
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "cc_agents"

    def test_numeric_defaults(self):
        assert SessionModel.__table__.c["token_usage"].default.arg == 0
        assert SessionModel.__table__.c["cost_estimate"].default.arg == 0.0

    def test_json_column_defaults(self):
        for col_name in ("files_modified", "deliverables"):
            col = SessionModel.__table__.c[col_name]
            assert col.default.arg == "[]"

    def test_nullable_columns(self):
        for col_name in ("end_time", "context_summary", "checkpoint_data"):
            col = SessionModel.__table__.c[col_name]
            assert col.nullable, f"{col_name} should be nullable"

    def test_relationship_to_agent(self):
        rels = {r.key for r in SessionModel.__mapper__.relationships}
        assert "agent" in rels

    def test_insert_and_query(self, db_session):
        agent = CCAgent(id="cc-agent-sess", name="Session Agent", status="active")
        db_session.add(agent)
        db_session.flush()

        session = SessionModel(
            id="sess-001",
            agent_id="cc-agent-sess",
            context_summary="Worked on auth module",
            token_usage=5000,
            cost_estimate=0.15,
            files_modified='["auth.py", "tests/test_auth.py"]',
        )
        db_session.add(session)
        db_session.flush()

        fetched = db_session.get(SessionModel, "sess-001")
        assert fetched.agent_id == "cc-agent-sess"
        assert fetched.token_usage == 5000
        assert fetched.cost_estimate == pytest.approx(0.15)
        assert fetched.context_summary == "Worked on auth module"

    def test_start_time_has_callable_default(self):
        col = SessionModel.__table__.c["start_time"]
        assert col.default is not None


# ---------------------------------------------------------------------------
# Node model
# ---------------------------------------------------------------------------


class TestNodeModel:
    """Tests for the Node ORM model."""

    def test_tablename(self):
        assert Node.__tablename__ == "nodes"

    def test_name_is_not_nullable(self):
        col = Node.__table__.c["name"]
        assert not col.nullable

    def test_status_default(self):
        col = Node.__table__.c["status"]
        assert col.default.arg == "offline"

    def test_numeric_defaults(self):
        assert Node.__table__.c["cpu_cores"].default.arg == 0
        assert Node.__table__.c["memory_gb"].default.arg == 0.0
        assert Node.__table__.c["disk_gb"].default.arg == 0.0

    def test_capabilities_default(self):
        col = Node.__table__.c["capabilities"]
        assert col.default.arg == "[]"

    def test_nullable_columns(self):
        for col_name in ("hostname", "ip_address", "last_heartbeat"):
            col = Node.__table__.c[col_name]
            assert col.nullable, f"{col_name} should be nullable"

    def test_relationship_to_sync_logs(self):
        rels = {r.key for r in Node.__mapper__.relationships}
        assert "sync_logs" in rels

    def test_insert_and_query(self, db_session):
        node = Node(
            id="node-prya",
            name="Prya",
            hostname="prya.local",
            ip_address="192.168.1.10",
            status="online",
            cpu_cores=8,
            memory_gb=32.0,
            disk_gb=500.0,
            capabilities='["python", "docker"]',
        )
        db_session.add(node)
        db_session.flush()

        fetched = db_session.get(Node, "node-prya")
        assert fetched.name == "Prya"
        assert fetched.hostname == "prya.local"
        assert fetched.ip_address == "192.168.1.10"
        assert fetched.status == "online"
        assert fetched.cpu_cores == 8
        assert fetched.memory_gb == pytest.approx(32.0)

    def test_created_at_has_callable_default(self):
        col = Node.__table__.c["created_at"]
        assert col.default is not None


# ---------------------------------------------------------------------------
# SyncLog model
# ---------------------------------------------------------------------------


class TestSyncLogModel:
    """Tests for the SyncLog ORM model."""

    def test_tablename(self):
        assert SyncLog.__tablename__ == "sync_log"

    def test_primary_key_is_autoincrement(self):
        col = SyncLog.__table__.c["id"]
        assert col.primary_key
        assert col.autoincrement

    def test_required_columns_not_nullable(self):
        for col_name in ("table_name", "record_id", "operation"):
            col = SyncLog.__table__.c[col_name]
            assert not col.nullable, f"{col_name} should not be nullable"

    def test_node_id_foreign_key(self):
        col = SyncLog.__table__.c["node_id"]
        fks = list(col.foreign_keys)
        assert fks[0].column.table.name == "nodes"

    def test_nullable_columns(self):
        for col_name in ("node_id", "old_value", "new_value", "checksum"):
            col = SyncLog.__table__.c[col_name]
            assert col.nullable, f"{col_name} should be nullable"

    def test_relationship_to_node(self):
        rels = {r.key for r in SyncLog.__mapper__.relationships}
        assert "node" in rels

    def test_insert_without_node(self, db_session):
        log = SyncLog(
            table_name="cc_agents",
            record_id="cc-agent-001",
            operation="INSERT",
            new_value='{"id": "cc-agent-001", "name": "Nova"}',
        )
        db_session.add(log)
        db_session.flush()

        fetched = db_session.get(SyncLog, log.id)
        assert fetched.table_name == "cc_agents"
        assert fetched.operation == "INSERT"
        assert fetched.node_id is None

    def test_insert_with_node(self, db_session):
        node = Node(id="node-sync-1", name="Sync Node")
        db_session.add(node)
        db_session.flush()

        log = SyncLog(
            table_name="cc_tasks",
            record_id="task-001",
            operation="UPDATE",
            node_id="node-sync-1",
            old_value='{"status": "pending"}',
            new_value='{"status": "completed"}',
            checksum="abc123def456",
        )
        db_session.add(log)
        db_session.flush()

        fetched = db_session.get(SyncLog, log.id)
        assert fetched.node_id == "node-sync-1"
        assert fetched.checksum == "abc123def456"

    def test_timestamp_has_callable_default(self):
        col = SyncLog.__table__.c["timestamp"]
        assert col.default is not None

    def test_autoincrement_produces_increasing_ids(self, db_session):
        logs = [
            SyncLog(table_name="t", record_id=f"r-{i}", operation="INSERT")
            for i in range(5)
        ]
        db_session.add_all(logs)
        db_session.flush()

        ids = [log.id for log in logs]
        assert ids == sorted(ids)
        assert len(set(ids)) == 5


# ---------------------------------------------------------------------------
# Relationship round-trip tests
# ---------------------------------------------------------------------------


class TestRelationships:
    """Integration tests verifying ORM relationships work correctly."""

    def test_agent_to_task_relationship(self, db_session):
        task = Task(id=generate_uuid(), subject="Rel test task")
        db_session.add(task)
        db_session.flush()

        agent = Agent(
            id="rel-agent-1",
            name="Rel Agent",
            model="gpt-4",
            current_task_id=task.id,
        )
        db_session.add(agent)
        db_session.flush()

        fetched_agent = db_session.get(Agent, "rel-agent-1")
        assert fetched_agent.current_task is not None
        assert fetched_agent.current_task.subject == "Rel test task"

    def test_task_to_history_relationship(self, db_session):
        task = Task(id=generate_uuid(), subject="Task with history")
        db_session.add(task)
        db_session.flush()

        for new_status in ("in_progress", "completed"):
            history = TaskHistory(task_id=task.id, new_status=new_status)
            db_session.add(history)
        db_session.flush()

        fetched_task = db_session.get(Task, task.id)
        assert len(fetched_task.history) == 2
        statuses = {h.new_status for h in fetched_task.history}
        assert statuses == {"in_progress", "completed"}

    def test_task_to_recommendations_relationship(self, db_session):
        task = Task(id=generate_uuid(), subject="Task with recs")
        agent = Agent(id="rec-rel-agent", name="Rec Rel Agent", model="claude")
        db_session.add_all([task, agent])
        db_session.flush()

        rec = TaskRecommendation(task_id=task.id, agent_id=agent.id)
        db_session.add(rec)
        db_session.flush()

        fetched_task = db_session.get(Task, task.id)
        assert len(fetched_task.recommendations) == 1
        assert fetched_task.recommendations[0].agent_id == agent.id

    def test_agent_to_capabilities_relationship(self, db_session):
        agent = Agent(id="cap-rel-agent", name="Cap Rel Agent", model="claude")
        db_session.add(agent)
        db_session.flush()

        for task_type in ("testing", "code_review", "deployment"):
            cap = AgentCapability(agent_id=agent.id, task_type=task_type)
            db_session.add(cap)
        db_session.flush()

        fetched_agent = db_session.get(Agent, "cap-rel-agent")
        assert len(fetched_agent.capabilities) == 3
        types = {c.task_type for c in fetched_agent.capabilities}
        assert types == {"testing", "code_review", "deployment"}

    def test_cc_agent_to_tasks_relationship(self, db_session):
        agent = CCAgent(id="cc-rel-agent", name="CC Rel Agent")
        db_session.add(agent)
        db_session.flush()

        for i in range(3):
            task = CCTask(
                id=f"cc-rel-task-{i}",
                title=f"Task {i}",
                assigned_to="cc-rel-agent",
            )
            db_session.add(task)
        db_session.flush()

        fetched_agent = db_session.get(CCAgent, "cc-rel-agent")
        assert len(fetched_agent.tasks) == 3

    def test_cc_agent_to_sessions_relationship(self, db_session):
        agent = CCAgent(id="cc-sess-agent", name="Sess Agent")
        db_session.add(agent)
        db_session.flush()

        session = SessionModel(id="sess-rel-001", agent_id="cc-sess-agent")
        db_session.add(session)
        db_session.flush()

        fetched_agent = db_session.get(CCAgent, "cc-sess-agent")
        assert len(fetched_agent.sessions) == 1
        assert fetched_agent.sessions[0].id == "sess-rel-001"

    def test_node_to_sync_logs_relationship(self, db_session):
        node = Node(id="node-rel-1", name="Rel Node")
        db_session.add(node)
        db_session.flush()

        for op in ("INSERT", "UPDATE", "DELETE"):
            log = SyncLog(
                table_name="cc_agents",
                record_id="some-id",
                operation=op,
                node_id="node-rel-1",
            )
            db_session.add(log)
        db_session.flush()

        fetched_node = db_session.get(Node, "node-rel-1")
        assert len(fetched_node.sync_logs) == 3
        ops = {log.operation for log in fetched_node.sync_logs}
        assert ops == {"INSERT", "UPDATE", "DELETE"}

    def test_cc_task_to_assignments_relationship(self, db_session):
        agent1 = CCAgent(id="cc-assign-a1", name="Agent One")
        agent2 = CCAgent(id="cc-assign-a2", name="Agent Two")
        task = CCTask(id="task-assign-001", title="Assign task")
        db_session.add_all([agent1, agent2, task])
        db_session.flush()

        a1 = TaskAssignment(task_id="task-assign-001", agent_id="cc-assign-a1")
        a2 = TaskAssignment(task_id="task-assign-001", agent_id="cc-assign-a2")
        db_session.add_all([a1, a2])
        db_session.flush()

        fetched_task = db_session.get(CCTask, "task-assign-001")
        assert len(fetched_task.assignments) == 2


# ---------------------------------------------------------------------------
# Schema-level verification via SQLAlchemy inspector
# ---------------------------------------------------------------------------


class TestSchemaInspection:
    """Verify actual DDL matches model declarations using inspector."""

    def test_all_expected_tables_exist(self, engine):
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = {
            "tasks",
            "agents",
            "agent_capabilities",
            "task_recommendations",
            "task_history",
            "cc_agents",
            "cc_tasks",
            "task_assignments",
            "sessions",
            "nodes",
            "sync_log",
        }
        assert expected.issubset(tables)

    def test_tasks_columns(self, engine):
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("tasks")}
        expected = {
            "id", "subject", "description", "priority", "domain", "project",
            "status", "task_type", "complexity", "estimated_duration_minutes",
            "dependencies", "blocked", "created_at", "updated_at",
            "started_at", "completed_at",
        }
        assert expected.issubset(cols)

    def test_agents_columns(self, engine):
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("agents")}
        expected = {
            "id", "name", "model", "status", "max_complexity",
            "preferred_domains", "current_task_id", "last_activity",
            "total_tasks_completed", "success_rate", "avg_task_duration_minutes",
            "avg_quality_score", "created_at", "updated_at",
        }
        assert expected.issubset(cols)

    def test_cc_agents_columns(self, engine):
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("cc_agents")}
        expected = {
            "id", "session_id", "name", "role", "status", "current_task_id",
            "domain", "project", "skills", "token_usage", "last_activity",
            "tmux_session", "node_id", "created_at", "updated_at",
        }
        assert expected.issubset(cols)

    def test_nodes_columns(self, engine):
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("nodes")}
        expected = {
            "id", "name", "hostname", "ip_address", "status",
            "last_heartbeat", "capabilities", "cpu_cores", "memory_gb",
            "disk_gb", "created_at", "updated_at",
        }
        assert expected.issubset(cols)

    def test_sync_log_columns(self, engine):
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("sync_log")}
        expected = {
            "id", "table_name", "record_id", "operation", "node_id",
            "old_value", "new_value", "timestamp", "checksum",
        }
        assert expected.issubset(cols)

    def test_task_assignments_primary_key(self, engine):
        inspector = inspect(engine)
        pk = inspector.get_pk_constraint("task_assignments")
        assert "id" in pk["constrained_columns"]

    def test_agents_foreign_keys(self, engine):
        inspector = inspect(engine)
        fks = inspector.get_foreign_keys("agents")
        referenced_tables = {fk["referred_table"] for fk in fks}
        assert "tasks" in referenced_tables

    def test_cc_tasks_foreign_keys(self, engine):
        inspector = inspect(engine)
        fks = inspector.get_foreign_keys("cc_tasks")
        referenced_tables = {fk["referred_table"] for fk in fks}
        assert "cc_agents" in referenced_tables

    def test_sessions_foreign_keys(self, engine):
        inspector = inspect(engine)
        fks = inspector.get_foreign_keys("sessions")
        referenced_tables = {fk["referred_table"] for fk in fks}
        assert "cc_agents" in referenced_tables

    def test_sync_log_foreign_keys(self, engine):
        inspector = inspect(engine)
        fks = inspector.get_foreign_keys("sync_log")
        referenced_tables = {fk["referred_table"] for fk in fks}
        assert "nodes" in referenced_tables
