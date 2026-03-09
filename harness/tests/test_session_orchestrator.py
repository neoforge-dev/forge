"""Tests for Session Orchestrator."""

from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.iteration.auto_commit import CommitResult
from forge_harness.iteration.pr_generator import PRResult
from forge_harness.iteration.session_orchestrator import (
    SessionConfig,
    SessionOrchestrator,
    SessionResult,
    create_session_orchestrator,
)
from forge_harness.iteration.tracker import IterationRecord, TrackerStats


class TestSessionConfig:
    """Test SessionConfig dataclass."""

    def test_default_values(self):
        config = SessionConfig(project_name="test-project")

        assert config.project_name == "test-project"
        assert config.base_branch == "main"
        assert config.auto_commit is True
        assert config.auto_pr is False
        assert config.pr_draft is True
        assert config.commit_scope == "harness"

    def test_custom_values(self):
        config = SessionConfig(
            project_name="my-project",
            base_branch="develop",
            auto_commit=False,
            auto_pr=True,
            pr_draft=False,
            commit_scope="backend",
        )

        assert config.project_name == "my-project"
        assert config.base_branch == "develop"
        assert config.auto_commit is False
        assert config.auto_pr is True
        assert config.pr_draft is False
        assert config.commit_scope == "backend"


class TestSessionResult:
    """Test SessionResult dataclass."""

    def test_success_result(self):
        commit_result = CommitResult(success=True, commit_hash="abc123", message="feat: test")

        result = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["Feature A", "Feature B"],
            commit_result=commit_result,
            duration_seconds=45.5,
        )

        assert result.success is True
        assert result.iteration_number == 1
        assert len(result.features_completed) == 2
        assert result.commit_result.commit_hash == "abc123"
        assert result.pr_result is None
        assert result.stats is None
        assert result.error is None
        assert result.duration_seconds == 45.5

    def test_failure_result(self):
        result = SessionResult(
            success=False,
            iteration_number=2,
            error="Something went wrong",
        )

        assert result.success is False
        assert result.iteration_number == 2
        assert result.error == "Something went wrong"
        assert len(result.features_completed) == 0

    def test_result_with_pr(self):
        pr_result = PRResult(
            success=True,
            pr_url="https://github.com/org/repo/pull/123",
            pr_number=123,
        )

        result = SessionResult(
            success=True,
            iteration_number=1,
            pr_result=pr_result,
        )

        assert result.pr_result.pr_number == 123
        assert "pull/123" in result.pr_result.pr_url


class TestSessionOrchestrator:
    """Test SessionOrchestrator class."""

    @pytest.fixture
    def mock_tracker(self):
        tracker = Mock()
        tracker.get_stats.return_value = TrackerStats(total_iterations=0)
        tracker.start_iteration.return_value = Mock()
        tracker.complete_iteration.return_value = IterationRecord(
            iteration_number=1,
            started_at=datetime.now(),
        )
        tracker.fail_iteration.return_value = IterationRecord(
            iteration_number=1,
            started_at=datetime.now(),
        )
        return tracker

    @pytest.fixture
    def mock_committer(self):
        committer = Mock()
        committer.commit.return_value = CommitResult(
            success=True,
            commit_hash="abc123",
            message="feat: test commit",
        )
        return committer

    @pytest.fixture
    def mock_pr_generator(self):
        pr_gen = Mock()
        pr_gen.create_pr.return_value = PRResult(
            success=True,
            pr_url="https://github.com/org/repo/pull/1",
            pr_number=1,
        )
        return pr_gen

    @pytest.fixture
    def orchestrator(self, mock_tracker, mock_committer, mock_pr_generator):
        config = SessionConfig(project_name="test-project")

        with (
            patch(
                "forge_harness.iteration.session_orchestrator.create_tracker",
                return_value=mock_tracker,
            ),
            patch(
                "forge_harness.iteration.session_orchestrator.create_auto_committer",
                return_value=mock_committer,
            ),
            patch(
                "forge_harness.iteration.session_orchestrator.create_pr_generator",
                return_value=mock_pr_generator,
            ),
        ):
            return SessionOrchestrator(config=config)

    def test_initialization(self):
        config = SessionConfig(project_name="test")

        with (
            patch("forge_harness.iteration.session_orchestrator.create_tracker"),
            patch("forge_harness.iteration.session_orchestrator.create_auto_committer"),
            patch("forge_harness.iteration.session_orchestrator.create_pr_generator"),
        ):
            orchestrator = SessionOrchestrator(config=config)

            assert orchestrator.config == config
            assert orchestrator._current_iteration is None

    def test_start_iteration(self, orchestrator, mock_tracker):
        mock_tracker.get_stats.return_value = TrackerStats(total_iterations=5)

        iteration_num = orchestrator.start_iteration()

        assert iteration_num == 6
        assert orchestrator._current_iteration == 6
        mock_tracker.start_iteration.assert_called_once_with(6)

    def test_complete_iteration_success(self, orchestrator, mock_tracker, mock_committer):
        # Start an iteration first
        orchestrator._current_iteration = 1

        features = ["Add auth", "Fix bug"]
        result = orchestrator.complete_iteration(
            features=features,
            tests_added=5,
            lines_added=100,
            notes="All working",
        )

        assert result.success is True
        assert result.iteration_number == 1
        assert result.features_completed == features
        assert result.commit_result.success is True
        assert orchestrator._current_iteration is None

        mock_tracker.complete_iteration.assert_called_once()
        mock_committer.commit.assert_called_once()

    def test_complete_iteration_without_start(self, orchestrator):
        result = orchestrator.complete_iteration(
            features=["Feature A"],
        )

        assert result.success is False
        assert result.error == "No iteration in progress. Call start_iteration() first."

    def test_complete_iteration_no_commit_when_no_features(self, orchestrator, mock_committer):
        orchestrator._current_iteration = 1

        result = orchestrator.complete_iteration(
            features=[],
            tests_added=3,
        )

        assert result.success is True
        mock_committer.commit.assert_not_called()

    def test_complete_iteration_with_commit_failure(self, orchestrator, mock_committer):
        orchestrator._current_iteration = 1
        mock_committer.commit.return_value = CommitResult(success=False, error="Nothing to commit")

        result = orchestrator.complete_iteration(
            features=["Feature A"],
        )

        # Session still succeeds even if commit fails
        assert result.success is True
        assert result.commit_result.success is False

    def test_complete_iteration_with_pr_creation(self, orchestrator, mock_pr_generator):
        orchestrator.config.auto_pr = True
        orchestrator._current_iteration = 1

        features = ["Feature A", "Feature B", "Feature C"]
        result = orchestrator.complete_iteration(
            features=features,
            tests_added=10,
            lines_added=500,
        )

        assert result.success is True
        assert result.pr_result is not None
        assert result.pr_result.success is True

        mock_pr_generator.create_pr.assert_called_once()
        call_args = mock_pr_generator.create_pr.call_args[0][0]
        assert call_args.features == features
        assert "Feature A, Feature B" in call_args.title

    def test_complete_iteration_no_pr_without_commit(
        self, orchestrator, mock_committer, mock_pr_generator
    ):
        orchestrator.config.auto_pr = True
        orchestrator._current_iteration = 1

        mock_committer.commit.return_value = CommitResult(success=False, error="Failed")

        result = orchestrator.complete_iteration(
            features=["Feature A"],
        )

        # PR should not be created if commit failed
        mock_pr_generator.create_pr.assert_not_called()

    def test_complete_iteration_exception_handling(self, orchestrator, mock_tracker):
        orchestrator._current_iteration = 1
        mock_tracker.complete_iteration.side_effect = RuntimeError("Database error")

        result = orchestrator.complete_iteration(
            features=["Feature A"],
        )

        assert result.success is False
        assert "Database error" in result.error
        assert orchestrator._current_iteration is None
        mock_tracker.fail_iteration.assert_called_once()

    def test_fail_iteration(self, orchestrator, mock_tracker):
        orchestrator._current_iteration = 1

        result = orchestrator.fail_iteration(reason="Test failure")

        assert result.success is False
        assert result.iteration_number == 1
        assert result.error == "Test failure"
        assert orchestrator._current_iteration is None

        mock_tracker.fail_iteration.assert_called_once_with(reason="Test failure")

    def test_fail_iteration_without_start(self, orchestrator):
        result = orchestrator.fail_iteration(reason="No iteration")

        assert result.success is False
        assert result.error == "No iteration in progress"

    def test_get_stats(self, orchestrator, mock_tracker):
        expected_stats = TrackerStats(
            total_iterations=10,
            completed_iterations=8,
            failed_iterations=2,
            total_features=25,
        )
        mock_tracker.get_stats.return_value = expected_stats

        stats = orchestrator.get_stats()

        assert stats == expected_stats
        mock_tracker.get_stats.assert_called_once()

    def test_get_recent_iterations(self, orchestrator, mock_tracker):
        expected_records = [
            IterationRecord(iteration_number=1, started_at=datetime.now()),
            IterationRecord(iteration_number=2, started_at=datetime.now()),
        ]
        mock_tracker.get_recent.return_value = expected_records

        records = orchestrator.get_recent_iterations(count=2)

        assert len(records) == 2
        mock_tracker.get_recent.assert_called_once_with(2)

    def test_auto_commit_disabled(self, orchestrator, mock_committer):
        orchestrator.config.auto_commit = False
        orchestrator._current_iteration = 1

        result = orchestrator.complete_iteration(
            features=["Feature A"],
        )

        assert result.success is True
        mock_committer.commit.assert_not_called()

    def test_files_changed_passed_to_committer(self, orchestrator, mock_committer):
        orchestrator._current_iteration = 1
        files = ["file1.py", "file2.py"]

        result = orchestrator.complete_iteration(
            features=["Feature A"],
            files_changed=files,
        )

        call_args = mock_committer.commit.call_args[0][0]
        assert call_args.files_changed == files


class TestCreateSessionOrchestrator:
    """Test factory function."""

    @patch("forge_harness.iteration.session_orchestrator.create_tracker")
    @patch("forge_harness.iteration.session_orchestrator.create_auto_committer")
    @patch("forge_harness.iteration.session_orchestrator.create_pr_generator")
    def test_create_with_defaults(self, mock_pr_gen, mock_committer, mock_tracker):
        orchestrator = create_session_orchestrator(project_name="test-project")

        assert orchestrator.config.project_name == "test-project"
        assert orchestrator.config.auto_commit is True
        assert orchestrator.config.auto_pr is False

    @patch("forge_harness.iteration.session_orchestrator.create_tracker")
    @patch("forge_harness.iteration.session_orchestrator.create_auto_committer")
    @patch("forge_harness.iteration.session_orchestrator.create_pr_generator")
    def test_create_with_custom_settings(self, mock_pr_gen, mock_committer, mock_tracker):
        repo_path = Path("/test/repo")
        orchestrator = create_session_orchestrator(
            project_name="custom-project",
            repo_path=repo_path,
            auto_commit=False,
            auto_pr=True,
        )

        assert orchestrator.config.project_name == "custom-project"
        assert orchestrator.config.auto_commit is False
        assert orchestrator.config.auto_pr is True
        assert orchestrator.repo_path == repo_path


class TestFullWorkflow:
    """Integration-style tests for complete workflows."""

    @pytest.fixture
    def full_orchestrator(self):
        config = SessionConfig(
            project_name="integration-test",
            auto_commit=True,
            auto_pr=True,
        )

        tracker = Mock()
        tracker.get_stats.return_value = TrackerStats(total_iterations=0)
        tracker.start_iteration.return_value = Mock()
        tracker.complete_iteration.return_value = IterationRecord(
            iteration_number=1,
            started_at=datetime.now(),
        )

        committer = Mock()
        committer.commit.return_value = CommitResult(
            success=True,
            commit_hash="abc123",
            message="feat: test",
        )

        pr_gen = Mock()
        pr_gen.create_pr.return_value = PRResult(
            success=True,
            pr_url="https://github.com/org/repo/pull/1",
            pr_number=1,
        )

        with (
            patch(
                "forge_harness.iteration.session_orchestrator.create_tracker", return_value=tracker
            ),
            patch(
                "forge_harness.iteration.session_orchestrator.create_auto_committer",
                return_value=committer,
            ),
            patch(
                "forge_harness.iteration.session_orchestrator.create_pr_generator",
                return_value=pr_gen,
            ),
        ):
            return SessionOrchestrator(config=config)

    def test_complete_session_workflow(self, full_orchestrator):
        # Start iteration
        iteration_num = full_orchestrator.start_iteration()
        assert iteration_num == 1

        # Complete with commit and PR
        result = full_orchestrator.complete_iteration(
            features=["Auth system", "User profile"],
            tests_added=15,
            lines_added=500,
        )

        assert result.success is True
        assert result.iteration_number == 1
        assert len(result.features_completed) == 2
        assert result.commit_result.success is True
        assert result.pr_result.success is True
        assert result.duration_seconds > 0

    def test_multiple_iterations(self, full_orchestrator):
        # First iteration
        full_orchestrator.start_iteration()
        result1 = full_orchestrator.complete_iteration(
            features=["Feature 1"],
        )
        assert result1.success is True

        # Second iteration
        full_orchestrator.start_iteration()
        result2 = full_orchestrator.complete_iteration(
            features=["Feature 2"],
        )
        assert result2.success is True

    def test_failure_recovery(self, full_orchestrator):
        # Start and fail
        full_orchestrator.start_iteration()
        fail_result = full_orchestrator.fail_iteration(reason="Tests failed")
        assert fail_result.success is False

        # Can start new iteration after failure
        iteration_num = full_orchestrator.start_iteration()
        assert iteration_num > 0

        result = full_orchestrator.complete_iteration(
            features=["Fixed feature"],
        )
        assert result.success is True
