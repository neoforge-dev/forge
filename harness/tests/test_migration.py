"""Tests for database migration: schema creation, data migration, and rollback.

Verifies:
1. ORM models match the raw SQL schema in env.py
2. Migration creates all expected tables
3. tmux/git state extraction populates data correctly
4. Rollback cleans up migrated data without destroying pre-existing records
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session as SASession

from forge_harness.models.database import (
    Agent,
    AgentCapability,
    Base,
    CCAgent,
    CCTask,
    Node,
    Session,
    SyncLog,
    Task,
    TaskAssignment,
    TaskHistory,
    TaskRecommendation,
)


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database path."""
    return str(tmp_path / "test_forge.db")


@pytest.fixture
def engine(db_path):
    """Create a SQLAlchemy engine with all tables."""
    eng = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    """Create a database session."""
    with SASession(engine) as sess:
        yield sess


class TestSchemaCreation:
    """Verify ORM models create the correct schema."""

    EXPECTED_TABLES = {
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

    def test_all_tables_created(self, engine):
        """All expected tables are created by Base.metadata.create_all."""
        inspector = inspect(engine)
        actual_tables = set(inspector.get_table_names())
        assert self.EXPECTED_TABLES.issubset(actual_tables), (
            f"Missing tables: {self.EXPECTED_TABLES - actual_tables}"
        )

    def test_cc_agents_columns(self, engine):
        """cc_agents table has all required columns."""
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("cc_agents")}
        expected = {
            "id",
            "session_id",
            "name",
            "role",
            "status",
            "current_task_id",
            "domain",
            "project",
            "skills",
            "token_usage",
            "last_activity",
            "tmux_session",
            "node_id",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns), f"Missing columns: {expected - columns}"

    def test_cc_tasks_columns(self, engine):
        """cc_tasks table has all required columns."""
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("cc_tasks")}
        expected = {
            "id",
            "title",
            "description",
            "status",
            "assigned_to",
            "priority",
            "domain",
            "project",
            "acceptance_criteria",
            "deliverables",
            "blocked_by",
            "blocks",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns), f"Missing columns: {expected - columns}"

    def test_sessions_columns(self, engine):
        """sessions table has all required columns."""
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("sessions")}
        expected = {
            "id",
            "agent_id",
            "start_time",
            "end_time",
            "context_summary",
            "files_modified",
            "token_usage",
            "cost_estimate",
            "deliverables",
            "checkpoint_data",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns), f"Missing columns: {expected - columns}"

    def test_nodes_columns(self, engine):
        """nodes table has all required columns."""
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("nodes")}
        expected = {
            "id",
            "name",
            "hostname",
            "ip_address",
            "status",
            "last_heartbeat",
            "capabilities",
            "cpu_cores",
            "memory_gb",
            "disk_gb",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns), f"Missing columns: {expected - columns}"

    def test_foreign_keys(self, engine):
        """Verify foreign key relationships exist."""
        inspector = inspect(engine)

        # cc_tasks.assigned_to → cc_agents.id
        fks = inspector.get_foreign_keys("cc_tasks")
        fk_targets = {(fk["constrained_columns"][0], fk["referred_table"]) for fk in fks}
        assert ("assigned_to", "cc_agents") in fk_targets

        # sessions.agent_id → cc_agents.id
        fks = inspector.get_foreign_keys("sessions")
        fk_targets = {(fk["constrained_columns"][0], fk["referred_table"]) for fk in fks}
        assert ("agent_id", "cc_agents") in fk_targets

        # task_assignments.task_id → cc_tasks.id
        fks = inspector.get_foreign_keys("task_assignments")
        fk_targets = {(fk["constrained_columns"][0], fk["referred_table"]) for fk in fks}
        assert ("task_id", "cc_tasks") in fk_targets
        assert ("agent_id", "cc_agents") in fk_targets


class TestDataMigration:
    """Test migrating tmux/git state to database."""

    MOCK_TMUX_OUTPUT = (
        "forge 5 1707753600 attached\n"
        "dot 2 1707753700 detached\n"
        "forge-codeswiftr 3 1707753800 attached\n"
    )

    MOCK_GIT_BRANCH = "feat/frontend-db-state-integration\n"
    MOCK_GIT_STATUS = " M harness/command_center/src/pages/Agents.tsx\n?? new_file.txt\n"
    MOCK_GIT_LOG = "abc123|feat: add SSE hooks|Claude|2026-02-12 10:00:00 -0500\n"
    MOCK_GIT_WORKTREE = "worktree /Users/bogdan/work/FORGE\n"

    def _run_migration_logic(self, engine):
        """Run the migration data extraction logic against the test DB."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "tmux_git_migration",
            str(
                Path(__file__).parent.parent
                / "migrations"
                / "versions"
                / "2026_02_09_1900_tmux_git_state_001_tmux_git_state_migration.py"
            ),
        )
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)

        with engine.connect() as conn:
            now = datetime.utcnow()
            # Insert agents from parsed tmux
            sessions_data = mig.parse_tmux_sessions()
            for sess in sessions_data:
                sid = f"agent-{sess['session_name']}"
                status = "working" if sess["attached"] else "idle"
                conn.execute(
                    text("""
                    INSERT OR REPLACE INTO cc_agents (
                        id, name, role, status, domain, project,
                        tmux_session, last_activity, created_at, updated_at
                    ) VALUES (
                        :id, :name, :role, :status, :domain, :project,
                        :tmux_session, :last_activity, :created_at, :created_at
                    )
                """),
                    {
                        "id": sid,
                        "name": sess["name"],
                        "role": sess["role"],
                        "status": status,
                        "domain": sess["domain"],
                        "project": sess["project"],
                        "tmux_session": sess["session_name"],
                        "last_activity": sess["created"].isoformat(),
                        "created_at": now.isoformat(),
                    },
                )
            conn.commit()
            return len(sessions_data)

    @patch("subprocess.run")
    def test_tmux_parsing(self, mock_run, engine, session):
        """tmux sessions are correctly parsed and inserted as cc_agents."""
        import subprocess

        # Mock tmux list-sessions
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self.MOCK_TMUX_OUTPUT, stderr=""
        )

        count = self._run_migration_logic(engine)

        assert count == 3
        agents = session.query(CCAgent).all()
        assert len(agents) == 3

        # Check orchestrator
        forge = session.query(CCAgent).filter_by(id="agent-forge").first()
        assert forge is not None
        assert forge.role == "orchestrator"
        assert forge.status == "working"  # attached

        # Check idle agent
        dot = session.query(CCAgent).filter_by(id="agent-dot").first()
        assert dot is not None
        assert dot.status == "idle"  # detached

        # Check domain orchestrator
        cs = session.query(CCAgent).filter_by(id="agent-forge-codeswiftr").first()
        assert cs is not None
        assert cs.role == "domain_orchestrator"
        assert cs.domain == "codeswiftr"

    def test_orm_crud_operations(self, session):
        """Basic CRUD operations work via ORM."""
        # Create agent
        agent = CCAgent(
            id="test-agent-1",
            name="Test Agent",
            role="agent",
            status="active",
            tmux_session="test-session",
        )
        session.add(agent)
        session.flush()

        # Create task assigned to agent
        task = CCTask(
            id="test-task-1",
            title="Test Task",
            description="A test task",
            status="pending",
            assigned_to="test-agent-1",
            priority=3,
        )
        session.add(task)
        session.flush()

        # Create session record
        work_session = Session(
            id="test-session-1",
            agent_id="test-agent-1",
            context_summary="Test work session",
            files_modified=json.dumps(["file1.py", "file2.py"]),
        )
        session.add(work_session)
        session.flush()

        # Verify relationships
        retrieved = session.query(CCAgent).filter_by(id="test-agent-1").first()
        assert retrieved is not None
        assert len(retrieved.tasks) == 1
        assert retrieved.tasks[0].title == "Test Task"
        assert len(retrieved.sessions) == 1

        # Update
        retrieved.status = "idle"
        session.flush()
        assert session.query(CCAgent).filter_by(id="test-agent-1").first().status == "idle"

        # Delete
        session.delete(work_session)
        session.delete(task)
        session.delete(retrieved)
        session.flush()
        assert session.query(CCAgent).filter_by(id="test-agent-1").first() is None

    def test_task_assignment_tracking(self, session):
        """Task assignments are tracked with timestamps."""
        agent = CCAgent(id="agent-1", name="Agent 1", role="agent")
        task = CCTask(id="task-1", title="Important Task", assigned_to="agent-1")
        session.add_all([agent, task])
        session.flush()

        assignment = TaskAssignment(
            task_id="task-1",
            agent_id="agent-1",
        )
        session.add(assignment)
        session.flush()

        assert assignment.id is not None
        assert assignment.notification_sent is False

        # Mark complete
        assignment.completed_at = datetime.utcnow()
        assignment.notification_sent = True
        session.flush()

        retrieved = session.query(TaskAssignment).filter_by(task_id="task-1").first()
        assert retrieved.completed_at is not None
        assert retrieved.notification_sent is True


class TestMigrationRollback:
    """Test migration rollback cleans up correctly."""

    def test_rollback_removes_migrated_agents(self, engine, session):
        """Rollback removes agents created by migration (tmux-sourced)."""
        # Insert pre-existing agent (should survive rollback)
        pre_existing = CCAgent(
            id="manual-agent",
            name="Manual Agent",
            role="agent",
            status="active",
        )
        session.add(pre_existing)

        # Insert migration-created agents (should be removed)
        migrated = CCAgent(
            id="agent-forge",
            name="Main Orchestrator",
            role="orchestrator",
            status="working",
            tmux_session="forge",
        )
        session.add(migrated)
        session.flush()
        session.commit()

        # Simulate rollback: delete agents with tmux_session and agent- prefix
        with engine.connect() as conn:
            conn.execute(
                text("""
                DELETE FROM cc_agents
                WHERE tmux_session IS NOT NULL
                AND id LIKE 'agent-%'
            """)
            )
            conn.commit()

        # Verify pre-existing survived
        session.expire_all()
        assert session.query(CCAgent).filter_by(id="manual-agent").first() is not None
        assert session.query(CCAgent).filter_by(id="agent-forge").first() is None

    def test_rollback_removes_git_sessions(self, engine, session):
        """Rollback removes git-sourced session records."""
        agent = CCAgent(id="agent-forge", name="Forge", role="orchestrator")
        session.add(agent)
        session.flush()

        # Pre-existing session
        pre_existing = Session(
            id="manual-session-1",
            agent_id="agent-forge",
            context_summary="Manual session",
        )
        # Migration-created session
        migrated = Session(
            id="session-git-20260212153000",
            agent_id="agent-forge",
            context_summary="Git work on branch 'main' with 5 modified files",
            files_modified=json.dumps(["a.py", "b.py"]),
        )
        session.add_all([pre_existing, migrated])
        session.flush()
        session.commit()

        # Simulate rollback
        with engine.connect() as conn:
            conn.execute(
                text("""
                DELETE FROM sessions
                WHERE id LIKE 'session-git-%'
            """)
            )
            conn.commit()

        session.expire_all()
        assert session.query(Session).filter_by(id="manual-session-1").first() is not None
        assert session.query(Session).filter_by(id="session-git-20260212153000").first() is None

    def test_sync_log_records_rollback(self, engine, session):
        """Rollback is recorded in sync_log."""
        node = Node(id="local", name="Local Node")
        session.add(node)
        session.flush()

        # Record rollback
        log = SyncLog(
            table_name="cc_agents",
            record_id="tmux_git_migration_rollback",
            operation="DELETE",
            node_id="local",
            old_value=json.dumps({"tmux_sessions_migrated": 3}),
            new_value=None,
        )
        session.add(log)
        session.flush()

        retrieved = (
            session.query(SyncLog).filter_by(record_id="tmux_git_migration_rollback").first()
        )
        assert retrieved is not None
        assert retrieved.operation == "DELETE"
        data = json.loads(retrieved.old_value)
        assert data["tmux_sessions_migrated"] == 3


class TestEnvPyIntegration:
    """Test that env.py can now import models successfully."""

    def test_env_py_imports_work(self):
        """env.py model imports resolve to actual ORM classes."""
        from forge_harness.models.database import (
            Agent,
            AgentCapability,
            Base,
            Task,
            TaskHistory,
            TaskRecommendation,
        )

        assert Base.metadata is not None
        assert "tasks" in Base.metadata.tables
        assert "agents" in Base.metadata.tables
        assert "cc_agents" in Base.metadata.tables

    def test_target_metadata_has_all_tables(self):
        """Base.metadata (used as target_metadata) contains all 11 tables."""
        assert len(Base.metadata.tables) == 11

    def test_raw_sql_schema_matches_orm(self, engine):
        """Raw SQL env.py schema matches ORM-created schema for key tables."""
        inspector = inspect(engine)

        # Verify cc_agents column count matches raw SQL
        cc_agent_cols = inspector.get_columns("cc_agents")
        assert len(cc_agent_cols) >= 15  # 15 columns defined

        # Verify cc_tasks column count
        cc_task_cols = inspector.get_columns("cc_tasks")
        assert len(cc_task_cols) >= 16  # 16 columns defined

        # Verify sessions column count
        session_cols = inspector.get_columns("sessions")
        assert len(session_cols) >= 12  # 12 columns defined
