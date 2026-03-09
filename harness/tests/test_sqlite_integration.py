"""
Integration tests for SQLite StateStore implementation.

Tests full workflows to ensure SQLite fallback works correctly
for local development without Redis.
"""

from datetime import UTC, datetime

import pytest

from forge_harness.state_store import (
    AgentRole,
    AgentSession,
    AgentStatus,
    AgentType,
    AssignmentStatus,
    ConflictRisk,
    StateStore,
    WorkAssignment,
    WorkTree,
    WorkTreeStatus,
)


@pytest.fixture
def state_store(tmp_path):
    """Create StateStore with SQLite backend."""
    store = StateStore(
        redis_url="redis://nonexistent:6379/0",  # Force SQLite
        sqlite_path=str(tmp_path / "test.db"),
    )
    store.connect()
    yield store
    store.disconnect()


class TestAgentLifecycle:
    """Test complete agent lifecycle with SQLite."""

    def test_register_and_retrieve_agent(self, state_store):
        """Can register agent and retrieve it."""
        session = AgentSession(
            session_id="agent-1",
            agent_type=AgentType.CLAUDE_CODE,
            agent_role=AgentRole.BUILDER,
            domain="test",
            project="test-project",
            capabilities=["python", "testing"],
            preferences={"model": "claude-3-opus"},
        )

        # Register
        result = state_store.register_agent(session)
        assert result is True

        # Retrieve
        retrieved = state_store.get_agent("agent-1")
        assert retrieved is not None
        assert retrieved.session_id == "agent-1"
        assert retrieved.agent_type == AgentType.CLAUDE_CODE
        assert retrieved.agent_role == AgentRole.BUILDER
        assert retrieved.domain == "test"
        assert retrieved.project == "test-project"
        assert retrieved.capabilities == ["python", "testing"]
        assert retrieved.preferences == {"model": "claude-3-opus"}

    def test_update_agent_status(self, state_store):
        """Can update agent status."""
        session = AgentSession(
            session_id="agent-2",
            agent_type=AgentType.CODEX,
            agent_role=AgentRole.QA,
            domain="test",
            project="test-project",
        )

        state_store.register_agent(session)

        # Update status
        result = state_store.update_agent_status(
            "agent-2", AgentStatus.WAITING_HUMAN, "waiting for approval"
        )
        assert result is True

        # Verify
        retrieved = state_store.get_agent("agent-2")
        assert retrieved.status == AgentStatus.WAITING_HUMAN
        assert retrieved.current_task == "waiting for approval"

    def test_get_active_agents(self, state_store):
        """Can get active agents with filters."""
        # Register agents
        agent1 = AgentSession(
            session_id="agent-3",
            agent_type=AgentType.CLAUDE_CODE,
            agent_role=AgentRole.BUILDER,
            domain="domain1",
            project="project1",
        )
        agent2 = AgentSession(
            session_id="agent-4",
            agent_type=AgentType.GEMINI,
            agent_role=AgentRole.CONTENT,
            domain="domain1",
            project="project2",
        )
        agent3 = AgentSession(
            session_id="agent-5",
            agent_type=AgentType.OPENCODE,
            agent_role=AgentRole.BUILDER,
            domain="domain2",
            project="project1",
        )

        state_store.register_agent(agent1)
        state_store.register_agent(agent2)
        state_store.register_agent(agent3)

        # Get all active agents
        all_agents = state_store.get_active_agents()
        assert len(all_agents) == 3

        # Filter by domain
        domain1_agents = state_store.get_active_agents(domain="domain1")
        assert len(domain1_agents) == 2

        # Filter by project
        project1_agents = state_store.get_active_agents(project="project1")
        assert len(project1_agents) == 2

        # Filter by both
        specific_agents = state_store.get_active_agents(domain="domain1", project="project1")
        assert len(specific_agents) == 1
        assert specific_agents[0].session_id == "agent-3"

    def test_assign_work_to_agent(self, state_store):
        """Can assign work to agent."""
        session = AgentSession(
            session_id="agent-6",
            agent_type=AgentType.CLAUDE_CODE,
            agent_role=AgentRole.BUILDER,
            domain="test",
            project="test-project",
        )

        state_store.register_agent(session)

        # Assign work
        result = state_store.assign_work("agent-6", "assignment-1", "/tmp/worktree-1")
        assert result is True

        # Verify
        retrieved = state_store.get_agent("agent-6")
        assert retrieved.assignment_id == "assignment-1"
        assert retrieved.work_tree_path == "/tmp/worktree-1"


class TestWorkLocks:
    """Test work lock operations with SQLite."""

    def test_acquire_and_release_lock(self, state_store):
        """Can acquire and release work locks."""
        # Acquire lock
        result = state_store.acquire_work_lock("task-1", "session-1")
        assert result is True

        # Verify locked
        assert state_store.is_work_locked("task-1")

        # Get holder
        holder = state_store.get_work_lock_holder("task-1")
        assert holder == "session-1"

        # Try to acquire again (should fail)
        result = state_store.acquire_work_lock("task-1", "session-2")
        assert result is False

        # Release lock
        result = state_store.release_work_lock("task-1", "session-1")
        assert result is True

        # Verify unlocked
        assert not state_store.is_work_locked("task-1")

    def test_lock_expiration(self, state_store):
        """Expired locks are cleaned up."""
        # Acquire lock with short TTL
        result = state_store.acquire_work_lock("task-2", "session-1", ttl_seconds=1)
        assert result is True
        assert state_store.is_work_locked("task-2")

        # Wait for expiration
        import time

        time.sleep(1.1)

        # Check if expired (should clean up)
        assert not state_store.is_work_locked("task-2")

        # Should be able to acquire now
        result = state_store.acquire_work_lock("task-2", "session-2")
        assert result is True


class TestAssignments:
    """Test assignment operations with SQLite."""

    def test_create_and_retrieve_assignment(self, state_store):
        """Can create and retrieve assignments."""
        assignment = WorkAssignment(
            assignment_id="assign-1",
            session_id="session-1",
            task_type="feature",
            task_description="Implement user authentication",
            priority=1,
            domain="test",
            project="test-project",
            files_affected=["src/auth.py", "tests/test_auth.py"],
            dependencies=["assign-0"],
            blocked_by=["assign-2"],
            blocking=["assign-3"],
            conflict_risk=ConflictRisk.MEDIUM,
        )

        # Create
        result = state_store.create_assignment(assignment)
        assert result is True

        # Retrieve
        retrieved = state_store.get_assignment("assign-1")
        assert retrieved is not None
        assert retrieved.assignment_id == "assign-1"
        assert retrieved.session_id == "session-1"
        assert retrieved.task_type == "feature"
        assert retrieved.priority == 1
        assert retrieved.files_affected == ["src/auth.py", "tests/test_auth.py"]
        assert retrieved.dependencies == ["assign-0"]
        assert retrieved.blocked_by == ["assign-2"]
        assert retrieved.blocking == ["assign-3"]
        assert retrieved.conflict_risk == ConflictRisk.MEDIUM

    def test_update_assignment_status(self, state_store):
        """Can update assignment status."""
        assignment = WorkAssignment(
            assignment_id="assign-2",
            session_id="session-1",
            task_type="bugfix",
            task_description="Fix login bug",
            priority=2,
            domain="test",
            project="test-project",
            files_affected=["src/auth.py"],
            dependencies=[],
        )

        state_store.create_assignment(assignment)

        # Update status
        completed_at = datetime.now(UTC)
        result = state_store.update_assignment_status(
            "assign-2", AssignmentStatus.COMPLETED, completed_at
        )
        assert result is True

        # Verify
        retrieved = state_store.get_assignment("assign-2")
        assert retrieved.status == AssignmentStatus.COMPLETED
        assert retrieved.completed_at is not None

    def test_list_assignments_with_filters(self, state_store):
        """Can list assignments with filters."""
        # Create multiple assignments
        assign1 = WorkAssignment(
            assignment_id="assign-3",
            session_id="session-1",
            task_type="feature",
            task_description="Feature 1",
            priority=1,
            domain="domain1",
            project="project1",
            files_affected=[],
            dependencies=[],
            status=AssignmentStatus.IN_PROGRESS,
        )
        assign2 = WorkAssignment(
            assignment_id="assign-4",
            session_id="session-2",
            task_type="bugfix",
            task_description="Bug 1",
            priority=2,
            domain="domain1",
            project="project2",
            files_affected=[],
            dependencies=[],
            status=AssignmentStatus.COMPLETED,
        )
        assign3 = WorkAssignment(
            assignment_id="assign-5",
            session_id="session-3",
            task_type="test",
            task_description="Test 1",
            priority=3,
            domain="domain2",
            project="project1",
            files_affected=[],
            dependencies=[],
            status=AssignmentStatus.IN_PROGRESS,
        )

        state_store.create_assignment(assign1)
        state_store.create_assignment(assign2)
        state_store.create_assignment(assign3)

        # List all
        all_assignments = state_store.list_assignments()
        assert len(all_assignments) == 3

        # Filter by domain
        domain1_assignments = state_store.list_assignments(domain="domain1")
        assert len(domain1_assignments) == 2

        # Filter by status
        in_progress = state_store.list_assignments(status=AssignmentStatus.IN_PROGRESS)
        assert len(in_progress) == 2

        # Filter by domain and status
        specific = state_store.list_assignments(
            domain="domain1", status=AssignmentStatus.IN_PROGRESS
        )
        assert len(specific) == 1
        assert specific[0].assignment_id == "assign-3"


class TestWorkTrees:
    """Test work tree operations with SQLite."""

    def test_register_and_retrieve_work_tree(self, state_store):
        """Can register and retrieve work trees."""
        work_tree = WorkTree(
            work_tree_path="/tmp/worktree-1",
            assigned_session_id="session-1",
            files_modified=["file1.py", "file2.py"],
            branch_name="feature/auth",
            base_commit="abc123",
            status=WorkTreeStatus.ACTIVE,
        )

        # Register
        result = state_store.register_work_tree(work_tree)
        assert result is True

        # Retrieve
        retrieved = state_store.get_work_tree("/tmp/worktree-1")
        assert retrieved is not None
        assert retrieved.work_tree_path == "/tmp/worktree-1"
        assert retrieved.assigned_session_id == "session-1"
        assert retrieved.files_modified == ["file1.py", "file2.py"]
        assert retrieved.branch_name == "feature/auth"
        assert retrieved.base_commit == "abc123"
        assert retrieved.status == WorkTreeStatus.ACTIVE

    def test_get_work_trees_for_session(self, state_store):
        """Can get work trees for specific session."""
        wt1 = WorkTree(
            work_tree_path="/tmp/wt1",
            assigned_session_id="session-1",
            files_modified=["file1.py"],
            branch_name="branch1",
            base_commit="commit1",
        )
        wt2 = WorkTree(
            work_tree_path="/tmp/wt2",
            assigned_session_id="session-2",
            files_modified=["file2.py"],
            branch_name="branch2",
            base_commit="commit2",
        )
        wt3 = WorkTree(
            work_tree_path="/tmp/wt3",
            assigned_session_id="session-1",
            files_modified=["file3.py"],
            branch_name="branch3",
            base_commit="commit3",
        )

        state_store.register_work_tree(wt1)
        state_store.register_work_tree(wt2)
        state_store.register_work_tree(wt3)

        # Get all work trees
        all_trees = state_store.get_work_trees_for_session("")
        assert len(all_trees) == 3

        # Get trees for session-1
        session1_trees = state_store.get_work_trees_for_session("session-1")
        assert len(session1_trees) == 2
        paths = [wt.work_tree_path for wt in session1_trees]
        assert "/tmp/wt1" in paths
        assert "/tmp/wt3" in paths

        # Get trees for session-2
        session2_trees = state_store.get_work_trees_for_session("session-2")
        assert len(session2_trees) == 1
        assert session2_trees[0].work_tree_path == "/tmp/wt2"


class TestEndToEndWorkflow:
    """Test complete end-to-end workflow with SQLite."""

    def test_full_agent_workflow(self, state_store):
        """Complete workflow: register agent, assign work, track progress."""
        # 1. Register agent
        agent = AgentSession(
            session_id="agent-workflow",
            agent_type=AgentType.CLAUDE_CODE,
            agent_role=AgentRole.BUILDER,
            domain="test",
            project="test-project",
        )
        assert state_store.register_agent(agent)

        # 2. Create assignment
        assignment = WorkAssignment(
            assignment_id="assign-workflow",
            session_id="agent-workflow",
            task_type="feature",
            task_description="Build feature X",
            priority=1,
            domain="test",
            project="test-project",
            files_affected=["src/feature.py"],
            dependencies=[],
        )
        assert state_store.create_assignment(assignment)

        # 3. Assign work to agent
        assert state_store.assign_work("agent-workflow", "assign-workflow", "/tmp/wt-workflow")

        # 4. Acquire work lock
        assert state_store.acquire_work_lock("task-workflow", "agent-workflow")

        # 5. Update status to in progress
        assert state_store.update_agent_status(
            "agent-workflow", AgentStatus.ACTIVE, "working on feature"
        )
        assert state_store.update_assignment_status("assign-workflow", AssignmentStatus.IN_PROGRESS)

        # 6. Register work tree
        work_tree = WorkTree(
            work_tree_path="/tmp/wt-workflow",
            assigned_session_id="agent-workflow",
            files_modified=["src/feature.py"],
            branch_name="feature/x",
            base_commit="abc123",
        )
        assert state_store.register_work_tree(work_tree)

        # 7. Complete work
        assert state_store.update_assignment_status(
            "assign-workflow", AssignmentStatus.COMPLETED, datetime.now(UTC)
        )
        assert state_store.update_agent_status("agent-workflow", AgentStatus.IDLE)
        assert state_store.release_work_lock("task-workflow", "agent-workflow")

        # 8. Verify final state
        final_agent = state_store.get_agent("agent-workflow")
        assert final_agent.status == AgentStatus.IDLE
        assert final_agent.assignment_id == "assign-workflow"

        final_assignment = state_store.get_assignment("assign-workflow")
        assert final_assignment.status == AssignmentStatus.COMPLETED
        assert final_assignment.completed_at is not None

        assert not state_store.is_work_locked("task-workflow")
