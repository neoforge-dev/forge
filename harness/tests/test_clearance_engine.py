"""
Tests for Clearance Engine Module
=================================

Tests for role-based work allocation, conflict detection, dependency tracking,
and work assignment management.
"""

import json
from unittest.mock import Mock, patch

import pytest

from forge_harness.clearance_engine import (
    AssignmentRequest,
    ClearanceEngine,
    ConflictDetector,
    DependencyTracker,
    RoleClearance,
    WorkAllocator,
)
from forge_harness.state_store import (
    AgentRole,
    AgentSession,
    AgentStatus,
    AgentType,
    AssignmentStatus,
    ConflictRisk,
    StateStore,
    WorkAssignment,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_forge_root(tmp_path):
    """Create temporary FORGE root directory."""
    return tmp_path


@pytest.fixture
def mock_state_store():
    """Create mock state store."""
    store = Mock(spec=StateStore)
    store.get_active_agents = Mock(return_value=[])
    store.get_assignment = Mock(return_value=None)
    store.create_assignment = Mock(return_value=True)
    store.assign_work = Mock(return_value=True)
    store.acquire_work_lock = Mock(return_value=True)
    store.release_work_lock = Mock(return_value=True)
    store.publish_status_change = Mock(return_value=True)
    store.is_connected = Mock(return_value=True)
    return store


@pytest.fixture
def sample_agent_session():
    """Create sample agent session."""
    return AgentSession(
        session_id="test-agent-001",
        agent_type=AgentType.CLAUDE_CODE,
        agent_role=AgentRole.BUILDER,
        domain="codeswiftr-com",
        project="interview-simulator",
        status=AgentStatus.ACTIVE,
    )


@pytest.fixture
def sample_work_assignment():
    """Create sample work assignment."""
    return WorkAssignment(
        assignment_id="assign-001",
        session_id="test-agent-001",
        task_type="feature",
        task_description="Implement user authentication",
        priority=80,
        domain="codeswiftr-com",
        project="interview-simulator",
        files_affected=["src/auth.py", "tests/test_auth.py"],
        dependencies=[],
        status=AssignmentStatus.ASSIGNED,
    )


# =============================================================================
# RoleClearance Tests
# =============================================================================


class TestRoleClearance:
    """Tests for RoleClearance role-based work rules."""

    def test_cto_can_work_on_everything(self):
        """CTO role should have access to all files."""
        can_work, _ = RoleClearance.can_work_on_file(AgentRole.CTO, "src/auth.py")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.CTO, "docs/README.md")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.CTO, "tests/test_api.py")
        assert can_work is True

    def test_builder_can_work_on_source_files(self):
        """BUILDER role should access source code and tests."""
        can_work, _ = RoleClearance.can_work_on_file(AgentRole.BUILDER, "src/api.py")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.BUILDER, "backend/main.py")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.BUILDER, "tests/test_api.py")
        assert can_work is True

    def test_builder_cannot_work_on_docs(self):
        """BUILDER role should not access documentation."""
        can_work, reason = RoleClearance.can_work_on_file(AgentRole.BUILDER, "docs/PLAN.md")
        assert can_work is False
        assert "blocked by pattern" in reason

        can_work, reason = RoleClearance.can_work_on_file(AgentRole.BUILDER, "CLAUDE.md")
        assert can_work is False

    def test_pm_can_work_on_docs(self):
        """PM role should access documentation files."""
        can_work, _ = RoleClearance.can_work_on_file(AgentRole.PM, "docs/PLAN.md")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.PM, "README.md")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.PM, "content/blog-post.md")
        assert can_work is True

    def test_pm_cannot_work_on_source(self):
        """PM role should not access source code."""
        can_work, reason = RoleClearance.can_work_on_file(AgentRole.PM, "src/api.py")
        assert can_work is False
        assert "blocked by pattern" in reason

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.PM, "backend/main.py")
        assert can_work is False

    def test_qa_can_work_on_tests(self):
        """QA role should access test files."""
        can_work, _ = RoleClearance.can_work_on_file(AgentRole.QA, "tests/test_api.py")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.QA, "e2e/auth.spec.ts")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.QA, "test-integration.py")
        assert can_work is True

    def test_qa_cannot_work_on_source(self):
        """QA role should not access source code."""
        can_work, reason = RoleClearance.can_work_on_file(AgentRole.QA, "src/api.py")
        assert can_work is False
        assert "blocked by pattern" in reason

    def test_content_can_work_on_marketing(self):
        """CONTENT role should access content and marketing files."""
        can_work, _ = RoleClearance.can_work_on_file(AgentRole.CONTENT, "content/blog-post.md")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.CONTENT, "marketing/email.html")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.CONTENT, "posts/article.md")
        assert can_work is True

    def test_content_cannot_work_on_code(self):
        """CONTENT role should not access code files."""
        can_work, reason = RoleClearance.can_work_on_file(AgentRole.CONTENT, "src/api.py")
        assert can_work is False
        assert "blocked by pattern" in reason

    def test_unknown_role_cannot_work(self):
        """Unknown role should be rejected."""
        can_work, reason = RoleClearance.can_work_on_file(Mock(value="unknown"), "any-file.py")
        assert can_work is False
        assert "Unknown role" in reason

    def test_requires_human_approval_for_major_changes(self):
        """Major architectural changes should require human approval."""
        requires = RoleClearance.requires_human(
            AgentRole.CTO, "feature", ["major_architectural_change"]
        )
        assert requires is True

    def test_requires_human_approval_for_security(self):
        """Security critical changes should require human approval."""
        requires = RoleClearance.requires_human(AgentRole.CTO, "feature", ["security_critical"])
        assert requires is True

    def test_builder_deploy_requires_human(self):
        """Builder deploying to production should require human approval."""
        requires = RoleClearance.requires_human(
            AgentRole.BUILDER, "feature", ["deploy_to_production"]
        )
        assert requires is True

    def test_no_human_approval_for_simple_test(self):
        """Simple test changes should not require human approval."""
        requires = RoleClearance.requires_human(AgentRole.QA, "test", [])
        assert requires is False

    def test_unknown_role_requires_human(self):
        """Unknown role should always require human approval."""
        requires = RoleClearance.requires_human(Mock(value="unknown"), "feature", [])
        assert requires is True

    def test_get_focus_areas_builder(self):
        """Get focus areas for BUILDER role."""
        focus = RoleClearance.get_focus_areas(AgentRole.BUILDER)
        assert "implementation" in focus
        assert "testing" in focus
        assert "refactoring" in focus

    def test_get_focus_areas_pm(self):
        """Get focus areas for PM role."""
        focus = RoleClearance.get_focus_areas(AgentRole.PM)
        assert "planning" in focus
        assert "priorities" in focus
        assert "documentation" in focus

    def test_get_focus_areas_unknown_role(self):
        """Unknown role should have empty focus areas."""
        focus = RoleClearance.get_focus_areas(Mock(value="unknown"))
        assert focus == []


# =============================================================================
# ConflictDetector Tests
# =============================================================================


class TestConflictDetector:
    """Tests for ConflictDetector conflict detection logic."""

    def test_no_conflicts_with_no_active_agents(self, mock_state_store):
        """Should detect no conflicts when no other agents active."""
        detector = ConflictDetector(mock_state_store)
        mock_state_store.get_active_agents.return_value = []

        risk, conflicts = detector.detect_conflicts(
            files=["src/auth.py"],
            session_id="agent-001",
            domain="test-domain",
            project="test-project",
        )

        assert risk == ConflictRisk.LOW
        assert len(conflicts) == 0

    def test_detect_file_conflict_with_other_agent(
        self, mock_state_store, sample_agent_session, sample_work_assignment
    ):
        """Should detect file conflicts with other active agents."""
        detector = ConflictDetector(mock_state_store)

        # Setup: another agent working on same file
        other_agent = AgentSession(
            session_id="agent-002",
            agent_type=AgentType.CLAUDE_CODE,
            agent_role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
            status=AgentStatus.ACTIVE,
            assignment_id="assign-002",
        )
        mock_state_store.get_active_agents.return_value = [other_agent]
        mock_state_store.get_assignment.return_value = WorkAssignment(
            assignment_id="assign-002",
            session_id="agent-002",
            task_type="feature",
            task_description="Other task",
            priority=50,
            domain="test-domain",
            project="test-project",
            files_affected=["src/auth.py", "src/utils.py"],
            dependencies=[],
        )

        risk, conflicts = detector.detect_conflicts(
            files=["src/auth.py", "src/api.py"],
            session_id="agent-001",
            domain="test-domain",
            project="test-project",
        )

        assert risk == ConflictRisk.LOW
        assert len(conflicts) >= 1
        assert conflicts[0]["type"] == "file_conflict"
        assert conflicts[0]["agent_id"] == "agent-002"
        assert "src/auth.py" in conflicts[0]["files"]

    def test_medium_risk_with_multiple_conflicts(self, mock_state_store, sample_agent_session):
        """Multiple conflicts should raise risk to MEDIUM."""
        detector = ConflictDetector(mock_state_store)

        # Setup: two other agents with conflicts
        agents = []
        for i in range(2):
            agent = AgentSession(
                session_id=f"agent-00{i + 2}",
                agent_type=AgentType.CLAUDE_CODE,
                agent_role=AgentRole.BUILDER,
                domain="test-domain",
                project="test-project",
                status=AgentStatus.ACTIVE,
                assignment_id=f"assign-00{i + 2}",
            )
            agents.append(agent)

        mock_state_store.get_active_agents.return_value = agents

        def get_assignment_mock(assignment_id):
            return WorkAssignment(
                assignment_id=assignment_id,
                session_id=assignment_id.replace("assign", "agent"),
                task_type="feature",
                task_description="Task",
                priority=50,
                domain="test-domain",
                project="test-project",
                files_affected=["src/auth.py"],
                dependencies=[],
            )

        mock_state_store.get_assignment.side_effect = get_assignment_mock

        risk, conflicts = detector.detect_conflicts(
            files=["src/auth.py"],
            session_id="agent-001",
            domain="test-domain",
            project="test-project",
        )

        assert risk == ConflictRisk.MEDIUM
        assert len(conflicts) == 2

    def test_high_risk_with_many_files_conflict(self, mock_state_store):
        """Conflicts with 5+ files should be HIGH risk."""
        detector = ConflictDetector(mock_state_store)

        # Setup: one agent with many conflicting files
        agent = AgentSession(
            session_id="agent-002",
            agent_type=AgentType.CLAUDE_CODE,
            agent_role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
            status=AgentStatus.ACTIVE,
            assignment_id="assign-002",
        )
        mock_state_store.get_active_agents.return_value = [agent]
        mock_state_store.get_assignment.return_value = WorkAssignment(
            assignment_id="assign-002",
            session_id="agent-002",
            task_type="feature",
            task_description="Task",
            priority=50,
            domain="test-domain",
            project="test-project",
            files_affected=[f"src/file{i}.py" for i in range(6)],
            dependencies=[],
        )

        risk, conflicts = detector.detect_conflicts(
            files=[f"src/file{i}.py" for i in range(6)],
            session_id="agent-001",
            domain="test-domain",
            project="test-project",
        )

        assert risk == ConflictRisk.HIGH
        assert len(conflicts) >= 1

    def test_exclude_self_from_conflict_detection(self, mock_state_store, sample_agent_session):
        """Should not detect conflict with self."""
        detector = ConflictDetector(mock_state_store)

        mock_state_store.get_active_agents.return_value = [sample_agent_session]

        risk, conflicts = detector.detect_conflicts(
            files=["src/auth.py"],
            session_id=sample_agent_session.session_id,
            domain=sample_agent_session.domain,
            project=sample_agent_session.project,
        )

        assert risk == ConflictRisk.LOW
        assert len(conflicts) == 0

    def test_check_recent_commits_no_conflicts(self, mock_state_store):
        """Should detect no conflicts when no recent commits."""
        detector = ConflictDetector(mock_state_store)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="")

            conflicts = detector._check_recent_commits(
                files=["src/auth.py"], project="test-project"
            )

        assert len(conflicts) == 0

    @patch("subprocess.run")
    def test_check_recent_commits_with_conflicts(self, mock_run, mock_state_store):
        """Should detect conflicts from recent commits."""
        detector = ConflictDetector(mock_state_store)

        # Mock git log output
        mock_run.return_value = Mock(
            returncode=0,
            stdout="abc123|john|feat: Add auth\nsrc/auth.py\npackage.json\n",
        )

        conflicts = detector._check_recent_commits(files=["src/auth.py"], project="test-project")

        assert len(conflicts) >= 1
        assert conflicts[0]["type"] == "recent_commit"
        assert conflicts[0]["file"] == "src/auth.py"

    @patch("subprocess.run")
    def test_check_recent_commits_handles_exception(self, mock_run, mock_state_store):
        """Should handle subprocess exceptions gracefully."""
        detector = ConflictDetector(mock_state_store)

        mock_run.side_effect = TimeoutError("Command timed out")

        conflicts = detector._check_recent_commits(files=["src/auth.py"], project="test-project")

        assert len(conflicts) == 0


# =============================================================================
# DependencyTracker Tests
# =============================================================================


class TestDependencyTracker:
    """Tests for DependencyTracker dependency management."""

    def test_not_blocked_with_no_dependencies(self, mock_state_store):
        """Should not be blocked with no dependencies."""
        tracker = DependencyTracker(mock_state_store)

        is_blocked, blocking = tracker.is_blocked([])

        assert is_blocked is False
        assert len(blocking) == 0

    def test_not_blocked_with_completed_dependencies(self, mock_state_store):
        """Should not be blocked if all dependencies completed."""
        tracker = DependencyTracker(mock_state_store)

        completed_dep = WorkAssignment(
            assignment_id="dep-001",
            session_id="agent-001",
            task_type="feature",
            task_description="Dependency task",
            priority=50,
            domain="test",
            project="test",
            files_affected=[],
            dependencies=[],
            status=AssignmentStatus.COMPLETED,
        )
        mock_state_store.get_assignment.return_value = completed_dep

        is_blocked, blocking = tracker.is_blocked(["dep-001"])

        assert is_blocked is False
        assert len(blocking) == 0

    def test_blocked_with_incomplete_dependency(self, mock_state_store):
        """Should be blocked if dependency is not completed."""
        tracker = DependencyTracker(mock_state_store)

        incomplete_dep = WorkAssignment(
            assignment_id="dep-001",
            session_id="agent-001",
            task_type="feature",
            task_description="Dependency task",
            priority=50,
            domain="test",
            project="test",
            files_affected=[],
            dependencies=[],
            status=AssignmentStatus.IN_PROGRESS,
        )
        mock_state_store.get_assignment.return_value = incomplete_dep

        is_blocked, blocking = tracker.is_blocked(["dep-001"])

        assert is_blocked is True
        assert len(blocking) == 1
        assert blocking[0]["dependency_id"] == "dep-001"
        assert blocking[0]["status"] == "in_progress"

    def test_update_blocking_status_success(self, mock_state_store, sample_work_assignment):
        """Should successfully update blocking status."""
        tracker = DependencyTracker(mock_state_store)

        mock_state_store.get_assignment.return_value = sample_work_assignment

        result = tracker.update_blocking_status(
            assignment_id="assign-001",
            blocking=["assign-002", "assign-003"],
            blocked_by=["assign-000"],
        )

        assert result is True
        assert mock_state_store.create_assignment.called

    def test_update_blocking_status_assignment_not_found(self, mock_state_store):
        """Should fail if assignment not found."""
        tracker = DependencyTracker(mock_state_store)

        mock_state_store.get_assignment.return_value = None

        result = tracker.update_blocking_status(
            assignment_id="nonexistent", blocking=[], blocked_by=[]
        )

        assert result is False


# =============================================================================
# WorkAllocator Tests
# =============================================================================


class TestWorkAllocator:
    """Tests for WorkAllocator work allocation logic."""

    @pytest.fixture
    def work_allocator(self, mock_state_store, temp_forge_root):
        """Create WorkAllocator instance."""
        return WorkAllocator(
            state_store=mock_state_store,
            forge_root=temp_forge_root,
            github_repo="test-org/test-repo",
        )

    def test_allocate_work_no_available_work(self, work_allocator):
        """Should fail when no work available."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        with patch.object(work_allocator, "_get_available_work", return_value=[]):
            response = work_allocator.allocate_work(request)

        assert response.success is False
        assert "No available work" in response.message

    def test_allocate_work_no_cleared_work(self, work_allocator):
        """Should fail when work doesn't match role clearance."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        # Work items that don't match BUILDER focus areas
        work_items = [
            {
                "task_id": "task-001",
                "task_type": "doc",
                "description": "Update documentation",
                "priority": 50,
                "files_affected": ["docs/README.md"],
                "dependencies": [],
                "tags": ["docs"],
                "source": "test",
            }
        ]

        with patch.object(work_allocator, "_get_available_work", return_value=work_items):
            response = work_allocator.allocate_work(request)

        assert response.success is False
        assert "No work available for role" in response.message

    def test_allocate_work_success(self, work_allocator, mock_state_store):
        """Should successfully allocate work."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        work_items = [
            {
                "task_id": "task-001",
                "task_type": "feature",
                "description": "Implement authentication refactoring",
                "priority": 80,
                "files_affected": ["src/auth.py"],
                "dependencies": [],
                "tags": ["implementation"],
                "source": "test",
            }
        ]

        with patch.object(work_allocator, "_get_available_work", return_value=work_items):
            response = work_allocator.allocate_work(request)

        assert response.success is True
        assert response.assignment_id is not None
        assert response.task_type == "feature"
        assert response.priority == 80
        assert mock_state_store.create_assignment.called
        assert mock_state_store.acquire_work_lock.called

    def test_allocate_work_with_conflicts_creates_worktree(self, work_allocator, mock_state_store):
        """Should create worktree for medium/high conflict risk."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        work_items = [
            {
                "task_id": "task-001",
                "task_type": "feature",
                "description": "Implement testing",
                "priority": 80,
                "files_affected": ["src/auth.py"],
                "dependencies": [],
                "tags": ["testing"],
                "source": "test",
            }
        ]

        # Mock conflict detector to return MEDIUM risk
        with patch.object(work_allocator, "_get_available_work", return_value=work_items):
            with patch.object(
                work_allocator.conflict_detector,
                "detect_conflicts",
                return_value=(ConflictRisk.MEDIUM, []),
            ):
                response = work_allocator.allocate_work(request)

        assert response.success is True
        assert response.work_tree_path is not None
        assert "worktrees" in response.work_tree_path

    def test_allocate_work_task_already_locked(self, work_allocator, mock_state_store):
        """Should fail if task is already locked."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        work_items = [
            {
                "task_id": "task-001",
                "task_type": "feature",
                "description": "Implement testing",
                "priority": 80,
                "files_affected": ["src/auth.py"],
                "dependencies": [],
                "tags": ["testing"],
                "source": "test",
            }
        ]

        mock_state_store.acquire_work_lock.return_value = False

        with patch.object(work_allocator, "_get_available_work", return_value=work_items):
            response = work_allocator.allocate_work(request)

        assert response.success is False
        assert "already locked" in response.message

    def test_get_github_issues_success(self, work_allocator):
        """Should fetch GitHub issues successfully."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        mock_issues = [
            {
                "number": 42,
                "title": "Implement feature X",
                "body": "Description",
                "labels": [{"name": "priority:8"}, {"name": "feature"}],
                "state": "OPEN",
            }
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=json.dumps(mock_issues), stderr="")

            work_items = work_allocator._get_github_issues(request)

        assert len(work_items) > 0
        assert work_items[0]["task_id"] == "github-issue-42"
        assert work_items[0]["priority"] == 8

    def test_get_github_issues_handles_failure(self, work_allocator):
        """Should handle GitHub CLI failure gracefully."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error")

            work_items = work_allocator._get_github_issues(request)

        assert len(work_items) == 0

    def test_get_plan_epics_success(self, work_allocator, temp_forge_root):
        """Should parse PLAN.md epics successfully."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        # Create PLAN.md with ICE-scored epics
        plan_dir = temp_forge_root / "test-domain" / "test-project"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "PLAN.md"
        plan_file.write_text(
            """
# Project Plan

## 1. User Authentication [I:8 C:9 E:7] (Score: 8.0)
Details about auth...

## 2. Payment Integration [I:7 C:6 E:4] (Score: 5.7)
Details about payments...
        """
        )

        work_items = work_allocator._get_plan_epics(request)

        assert len(work_items) == 2
        assert work_items[0]["task_id"] == "plan-epic-1"
        assert "Authentication" in work_items[0]["description"]
        assert work_items[0]["priority"] == 80

    def test_get_plan_epics_file_not_found(self, work_allocator):
        """Should handle missing PLAN.md gracefully."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="nonexistent",
            project="nonexistent",
        )

        work_items = work_allocator._get_plan_epics(request)

        assert len(work_items) == 0

    def test_get_living_docs_tasks_success(self, work_allocator, temp_forge_root):
        """Should parse living docs tasks successfully."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        # Create active-context.md
        docs_dir = temp_forge_root / "test-domain" / "test-project" / "docs"
        docs_dir.mkdir(parents=True)
        context_file = docs_dir / "active-context.md"
        context_file.write_text(
            """
# Active Context

## Tasks
- [ ] Implement user registration
- [ ] Add password validation
- [ ] Write integration tests
        """
        )

        work_items = work_allocator._get_living_docs_tasks(request)

        assert len(work_items) == 3
        assert "registration" in work_items[0]["description"]

    def test_filter_by_focus_tags(self, work_allocator):
        """Should filter work items by focus tags."""
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
            focus_tags=["authentication", "security"],
        )

        work_items = [
            {
                "task_id": "task-001",
                "description": "Implement authentication",
                "tags": ["auth", "security"],
            },
            {
                "task_id": "task-002",
                "description": "Update styling",
                "tags": ["ui", "css"],
            },
            {
                "task_id": "task-003",
                "description": "Security audit",
                "tags": ["security"],
            },
        ]

        filtered = work_allocator._filter_by_focus(request, work_items)

        assert len(filtered) == 2
        assert filtered[0]["task_id"] == "task-001"
        assert filtered[1]["task_id"] == "task-003"


# =============================================================================
# ClearanceEngine Tests
# =============================================================================


class TestClearanceEngine:
    """Tests for ClearanceEngine main interface."""

    @pytest.fixture
    def clearance_engine(self, mock_state_store, temp_forge_root):
        """Create ClearanceEngine instance."""
        return ClearanceEngine(
            state_store=mock_state_store,
            forge_root=temp_forge_root,
            github_repo="test-org/test-repo",
        )

    def test_request_clearance_success(self, clearance_engine):
        """Should successfully request clearance."""
        work_items = [
            {
                "task_id": "task-001",
                "task_type": "feature",
                "description": "Implement testing",
                "priority": 80,
                "files_affected": ["src/auth.py"],
                "dependencies": [],
                "tags": ["testing"],
                "source": "test",
            }
        ]

        with patch.object(
            clearance_engine.work_allocator, "_get_available_work", return_value=work_items
        ):
            response = clearance_engine.request_clearance(
                session_id="agent-001",
                agent_role=AgentRole.BUILDER,
                domain="test-domain",
                project="test-project",
            )

        assert response.success is True
        assert response.assignment_id is not None

    def test_request_clearance_with_preferences(self, clearance_engine):
        """Should use agent preferences when allocating work."""
        work_items = [
            {
                "task_id": "task-001",
                "task_type": "feature",
                "description": "Implement testing",
                "priority": 80,
                "files_affected": ["src/auth.py"],
                "dependencies": [],
                "tags": ["testing"],
                "source": "test",
            }
        ]

        with patch.object(
            clearance_engine.work_allocator, "_get_available_work", return_value=work_items
        ):
            response = clearance_engine.request_clearance(
                session_id="agent-001",
                agent_role=AgentRole.BUILDER,
                domain="test-domain",
                project="test-project",
                preferences={"focus": "backend"},
                focus_tags=["api", "testing"],
            )

        assert response.success is True

    def test_complete_assignment_success(self, clearance_engine, sample_work_assignment):
        """Should successfully complete an assignment."""
        clearance_engine.state_store.get_assignment.return_value = sample_work_assignment

        result = clearance_engine.complete_assignment(
            session_id="test-agent-001",
            assignment_id="assign-001",
            summary="Task completed successfully",
        )

        assert result is True
        assert clearance_engine.state_store.create_assignment.called
        assert clearance_engine.state_store.release_work_lock.called
        assert clearance_engine.state_store.assign_work.called
        assert clearance_engine.state_store.publish_status_change.called

    def test_complete_assignment_not_found(self, clearance_engine):
        """Should fail when assignment not found."""
        clearance_engine.state_store.get_assignment.return_value = None

        result = clearance_engine.complete_assignment(
            session_id="test-agent-001",
            assignment_id="nonexistent",
            summary="Task completed",
        )

        assert result is False

    def test_get_assignments_empty(self, clearance_engine):
        """Should return empty list for get_assignments (TODO implementation)."""
        # This tests the incomplete functionality at line 1032
        assignments = clearance_engine.get_assignments()

        assert assignments == []
        assert isinstance(assignments, list)

    def test_get_assignments_with_filters(self, clearance_engine):
        """Should accept filter parameters for get_assignments (TODO implementation)."""
        # This tests the incomplete functionality at line 1032
        assignments = clearance_engine.get_assignments(
            domain="test-domain",
            project="test-project",
            status=AssignmentStatus.COMPLETED,
        )

        assert assignments == []
        assert isinstance(assignments, list)


# =============================================================================
# Integration Tests
# =============================================================================


class TestClearanceEngineIntegration:
    """Integration tests for full clearance workflow."""

    @pytest.fixture
    def integration_engine(self, tmp_path, mock_state_store):
        """Create clearance engine for integration testing."""
        # Use mock state store since SQLite implementation is incomplete
        return ClearanceEngine(
            state_store=mock_state_store,
            forge_root=tmp_path,
            github_repo="test-org/test-repo",
        )

    def test_full_assignment_workflow(self, integration_engine, tmp_path, sample_work_assignment):
        """Test complete workflow from request to completion."""
        # Setup: Create PLAN.md with work
        plan_dir = tmp_path / "test-domain" / "test-project"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "PLAN.md"
        plan_file.write_text(
            """
## 1. Implement Testing [I:8 C:9 E:7] (Score: 8.0)
Add comprehensive test coverage.
        """
        )

        # Step 1: Request clearance
        response = integration_engine.request_clearance(
            session_id="agent-001",
            agent_role=AgentRole.BUILDER,
            domain="test-domain",
            project="test-project",
        )

        assert response.success is True
        assignment_id = response.assignment_id
        assert assignment_id is not None

        # Step 2: Complete assignment
        # Mock the assignment retrieval
        sample_work_assignment.assignment_id = assignment_id
        integration_engine.state_store.get_assignment.return_value = sample_work_assignment

        result = integration_engine.complete_assignment(
            session_id="agent-001",
            assignment_id=assignment_id,
            summary="Tests implemented successfully",
        )

        assert result is True


# =============================================================================
# Edge Cases and Error Handling Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_allocation_with_empty_focus_tags(self, mock_state_store, temp_forge_root):
        """Should handle empty focus tags."""
        allocator = WorkAllocator(mock_state_store, temp_forge_root)
        request = AssignmentRequest(
            session_id="agent-001",
            role=AgentRole.BUILDER,
            domain="test",
            project="test",
            focus_tags=[],
        )

        work_items = [{"task_id": "task-001", "description": "Task", "tags": []}]

        filtered = allocator._filter_by_focus(request, work_items)

        # With no focus tags, should return all items
        assert len(filtered) == len(work_items)

    def test_conflict_detection_with_no_assignment(self, mock_state_store):
        """Should handle agents without assignments gracefully."""
        detector = ConflictDetector(mock_state_store)

        agent = AgentSession(
            session_id="agent-002",
            agent_type=AgentType.CLAUDE_CODE,
            agent_role=AgentRole.BUILDER,
            domain="test",
            project="test",
            status=AgentStatus.ACTIVE,
            assignment_id=None,  # No assignment
        )
        mock_state_store.get_active_agents.return_value = [agent]

        risk, conflicts = detector.detect_conflicts(
            files=["src/auth.py"],
            session_id="agent-001",
            domain="test",
            project="test",
        )

        assert risk == ConflictRisk.LOW
        assert len(conflicts) == 0

    def test_role_clearance_with_wildcard_patterns(self):
        """Should handle wildcard patterns correctly."""
        # Test that *.md pattern matches any markdown file
        can_work, _ = RoleClearance.can_work_on_file(AgentRole.PM, "docs/test.md")
        assert can_work is True

        can_work, _ = RoleClearance.can_work_on_file(AgentRole.PM, "notes.md")
        assert can_work is True

    def test_dependency_tracking_with_nonexistent_dependency(self, mock_state_store):
        """Should handle missing dependencies gracefully."""
        tracker = DependencyTracker(mock_state_store)
        mock_state_store.get_assignment.return_value = None

        # Non-existent dependency should not block (optimistic assumption)
        is_blocked, blocking = tracker.is_blocked(["nonexistent-dep"])

        assert is_blocked is False
        assert len(blocking) == 0
