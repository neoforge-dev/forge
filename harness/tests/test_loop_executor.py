"""Tests for iteration loop executor."""

from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge_harness.iteration.loop_executor import (
    IterationSummary,
    LoopConfig,
    LoopExecutor,
    LoopResult,
    create_loop_executor,
)
from forge_harness.iteration.session_orchestrator import SessionResult
from forge_harness.iteration.tracker import TrackerStats


class TestLoopConfig:
    """Tests for LoopConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = LoopConfig(project_name="test-project")

        assert config.project_name == "test-project"
        assert config.max_iterations == 100
        assert config.cooldown_seconds == 30.0
        assert config.max_consecutive_failures == 3
        assert config.auto_commit is True
        assert config.auto_pr is False
        assert config.stop_on_all_complete is True

    def test_custom_values(self):
        """Test custom configuration values."""
        config = LoopConfig(
            project_name="custom-project",
            max_iterations=50,
            cooldown_seconds=10.0,
            max_consecutive_failures=5,
            auto_commit=False,
            auto_pr=True,
            stop_on_all_complete=False,
        )

        assert config.project_name == "custom-project"
        assert config.max_iterations == 50
        assert config.cooldown_seconds == 10.0
        assert config.max_consecutive_failures == 5
        assert config.auto_commit is False
        assert config.auto_pr is True
        assert config.stop_on_all_complete is False


class TestIterationSummary:
    """Tests for IterationSummary dataclass."""

    def test_success_summary(self):
        """Test successful iteration summary."""
        summary = IterationSummary(
            iteration_number=1,
            success=True,
            features_completed=["feature-1", "feature-2"],
            duration_seconds=45.5,
        )

        assert summary.iteration_number == 1
        assert summary.success is True
        assert summary.features_completed == ["feature-1", "feature-2"]
        assert summary.duration_seconds == 45.5
        assert summary.error is None

    def test_failure_summary(self):
        """Test failed iteration summary."""
        summary = IterationSummary(
            iteration_number=2,
            success=False,
            duration_seconds=10.0,
            error="Test failed",
        )

        assert summary.iteration_number == 2
        assert summary.success is False
        assert summary.features_completed == []
        assert summary.duration_seconds == 10.0
        assert summary.error == "Test failed"


class TestLoopResult:
    """Tests for LoopResult dataclass."""

    def test_success_result(self):
        """Test successful loop result."""
        stats = TrackerStats(
            total_iterations=5,
            completed_iterations=5,
            failed_iterations=0,
            total_features=10,
            total_tests=50,
            total_lines=1000,
        )

        result = LoopResult(
            success=True,
            iterations_completed=5,
            iterations_failed=0,
            total_features=10,
            total_duration_seconds=300.0,
            stopped_reason="Completed all iterations",
            final_stats=stats,
        )

        assert result.success is True
        assert result.iterations_completed == 5
        assert result.iterations_failed == 0
        assert result.total_features == 10
        assert result.total_duration_seconds == 300.0
        assert result.stopped_reason == "Completed all iterations"
        assert result.final_stats == stats
        assert result.summaries == []

    def test_failure_result(self):
        """Test failed loop result."""
        summaries = [
            IterationSummary(1, True, ["feature-1"], 30.0),
            IterationSummary(2, False, [], 10.0, "Error occurred"),
        ]

        result = LoopResult(
            success=False,
            iterations_completed=1,
            iterations_failed=1,
            total_features=1,
            total_duration_seconds=50.0,
            summaries=summaries,
            stopped_reason="Too many consecutive failures (3)",
        )

        assert result.success is False
        assert result.iterations_completed == 1
        assert result.iterations_failed == 1
        assert len(result.summaries) == 2
        assert result.stopped_reason == "Too many consecutive failures (3)"


class TestLoopExecutor:
    """Tests for LoopExecutor class."""

    @pytest.fixture
    def mock_orchestrator(self):
        """Create a mock orchestrator."""
        orchestrator = Mock()
        orchestrator.start_iteration = Mock()
        orchestrator.complete_iteration = Mock()
        orchestrator.get_stats = Mock(
            return_value=TrackerStats(
                total_iterations=0,
                completed_iterations=0,
                failed_iterations=0,
                total_features=0,
                total_tests=0,
                total_lines=0,
            )
        )
        return orchestrator

    @pytest.fixture
    def executor(self, tmp_path, mock_orchestrator):
        """Create a test executor."""
        config = LoopConfig(
            project_name="test-project",
            max_iterations=5,
            cooldown_seconds=0.1,  # Short cooldown for tests
            max_consecutive_failures=3,
        )

        with patch(
            "forge_harness.iteration.loop_executor.create_session_orchestrator"
        ) as mock_create:
            mock_create.return_value = mock_orchestrator
            executor = LoopExecutor(config=config, repo_path=tmp_path)
            executor._orchestrator = mock_orchestrator
            return executor

    def test_initialization(self, executor, tmp_path):
        """Test executor initialization."""
        assert executor.config.project_name == "test-project"
        assert executor.config.max_iterations == 5
        assert executor.repo_path == tmp_path
        assert executor._should_stop is False
        assert executor._consecutive_failures == 0

    def test_default_feature_source(self, executor):
        """Test default feature source returns empty list."""
        features = executor._default_feature_source()
        assert features == []

    def test_stop_signal(self, executor):
        """Test stop signal handling."""
        assert executor._should_stop is False
        executor.stop()
        assert executor._should_stop is True

    def test_successful_iterations(self, executor, mock_orchestrator):
        """Test loop with all successful iterations."""
        # Setup feature source
        executor.feature_source = lambda: ["feature-1", "feature-2"]

        # Mock successful iterations
        mock_orchestrator.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["feature-1", "feature-2"],
        )

        result = executor.run()

        assert result.success is True
        assert result.iterations_completed == 5
        assert result.iterations_failed == 0
        assert result.total_features == 10  # 2 features * 5 iterations
        assert len(result.summaries) == 5
        assert all(s.success for s in result.summaries)
        assert result.stopped_reason is None

    def test_failed_iterations(self, executor, mock_orchestrator):
        """Test loop with failed iterations."""
        executor.feature_source = lambda: ["feature-1"]

        # Mock failed iterations
        mock_orchestrator.complete_iteration.return_value = SessionResult(
            success=False,
            iteration_number=1,
            error="Test failed",
        )

        result = executor.run()

        assert result.success is False
        assert result.iterations_completed == 0
        assert result.iterations_failed == 3  # Should stop at max_consecutive_failures
        assert result.stopped_reason == "Too many consecutive failures (3)"
        assert len(result.summaries) == 3

    def test_mixed_success_and_failure(self, executor, mock_orchestrator):
        """Test loop with mixed success and failure."""
        executor.feature_source = lambda: ["feature-1"]

        # Alternate between success and failure
        results = [
            SessionResult(success=True, iteration_number=1, features_completed=["feature-1"]),
            SessionResult(success=False, iteration_number=2, error="Error 1"),
            SessionResult(success=True, iteration_number=3, features_completed=["feature-1"]),
            SessionResult(success=False, iteration_number=4, error="Error 2"),
            SessionResult(success=True, iteration_number=5, features_completed=["feature-1"]),
        ]
        mock_orchestrator.complete_iteration.side_effect = results

        result = executor.run()

        assert result.iterations_completed == 3
        assert result.iterations_failed == 2
        assert result.success is True  # More successes than failures
        assert len(result.summaries) == 5

    def test_stop_signal_handling(self, executor, mock_orchestrator):
        """Test that stop signal interrupts loop."""
        executor.feature_source = lambda: ["feature-1"]
        mock_orchestrator.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["feature-1"],
        )

        # Stop after first iteration
        def stop_after_first(*args, **kwargs):
            executor.stop()
            return SessionResult(success=True, iteration_number=1, features_completed=["feature-1"])

        mock_orchestrator.complete_iteration.side_effect = stop_after_first

        result = executor.run()

        assert result.iterations_completed == 1
        assert result.stopped_reason == "Manual stop requested"
        assert len(result.summaries) == 1

    def test_stop_on_all_complete(self, executor, mock_orchestrator):
        """Test loop stops when no features remain."""
        call_count = [0]

        def feature_source():
            call_count[0] += 1
            if call_count[0] <= 2:
                return ["feature-1"]
            return []  # No more features

        executor.feature_source = feature_source
        mock_orchestrator.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["feature-1"],
        )

        result = executor.run()

        assert result.iterations_completed == 2
        assert result.stopped_reason == "All features completed"

    def test_exception_handling(self, executor, mock_orchestrator):
        """Test handling of exceptions during iteration."""
        executor.feature_source = lambda: ["feature-1"]
        mock_orchestrator.complete_iteration.side_effect = Exception("Unexpected error")

        result = executor.run()

        assert result.success is False
        assert result.iterations_failed == 3  # Stops at max_consecutive_failures
        assert all(not s.success for s in result.summaries)
        assert all("Unexpected error" in s.error for s in result.summaries)

    def test_consecutive_failures_reset(self, executor, mock_orchestrator):
        """Test that consecutive failures counter resets on success."""
        executor.feature_source = lambda: ["feature-1"]

        # Fail twice, then succeed, then fail twice more
        results = [
            SessionResult(success=False, iteration_number=1, error="Error 1"),
            SessionResult(success=False, iteration_number=2, error="Error 2"),
            SessionResult(success=True, iteration_number=3, features_completed=["feature-1"]),
            SessionResult(success=False, iteration_number=4, error="Error 3"),
            SessionResult(success=False, iteration_number=5, error="Error 4"),
        ]
        mock_orchestrator.complete_iteration.side_effect = results

        result = executor.run()

        # Should complete all 5 iterations because success resets counter
        assert result.iterations_completed == 1
        assert result.iterations_failed == 4
        assert len(result.summaries) == 5

    def test_custom_feature_source(self, executor, mock_orchestrator):
        """Test using custom feature source callback."""
        features_list = [
            ["feature-1", "feature-2"],
            ["feature-3"],
            ["feature-4", "feature-5", "feature-6"],
        ]
        call_count = [0]

        def custom_source():
            if call_count[0] < len(features_list):
                features = features_list[call_count[0]]
                call_count[0] += 1
                return features
            return []

        executor.feature_source = custom_source
        executor.config.max_iterations = 3

        def mock_complete(features, tests_added, lines_added):
            return SessionResult(success=True, iteration_number=1, features_completed=features)

        mock_orchestrator.complete_iteration.side_effect = mock_complete

        result = executor.run()

        assert result.iterations_completed == 3
        assert result.summaries[0].features_completed == ["feature-1", "feature-2"]
        assert result.summaries[1].features_completed == ["feature-3"]
        assert result.summaries[2].features_completed == ["feature-4", "feature-5", "feature-6"]

    @pytest.mark.asyncio
    async def test_async_execution(self, executor, mock_orchestrator):
        """Test asynchronous loop execution."""
        executor.feature_source = lambda: ["feature-1"]
        mock_orchestrator.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["feature-1"],
        )

        result = await executor.run_async()

        assert result.success is True
        assert result.iterations_completed == 5
        assert len(result.summaries) == 5

    @pytest.mark.asyncio
    async def test_async_stop_signal(self, executor, mock_orchestrator):
        """Test stop signal in async mode."""
        executor.feature_source = lambda: ["feature-1"]

        def stop_after_first(*args, **kwargs):
            executor.stop()
            return SessionResult(success=True, iteration_number=1, features_completed=["feature-1"])

        mock_orchestrator.complete_iteration.side_effect = stop_after_first

        result = await executor.run_async()

        assert result.iterations_completed == 1
        assert result.stopped_reason == "Manual stop requested"

    def test_generate_summary_report(self, executor):
        """Test summary report generation."""
        summaries = [
            IterationSummary(1, True, ["feature-1", "feature-2"], 30.5),
            IterationSummary(2, True, ["feature-3"], 25.0),
            IterationSummary(3, False, [], 10.0, "Test failed"),
        ]

        result = LoopResult(
            success=True,
            iterations_completed=2,
            iterations_failed=1,
            total_features=3,
            total_duration_seconds=75.5,
            summaries=summaries,
            stopped_reason="Manual stop requested",
        )

        report = executor.generate_summary_report(result)

        assert "# Iteration Loop Summary" in report
        assert "**Project:** test-project" in report
        assert "**Total Duration:** 75.5s" in report
        assert "**Stop Reason:** Manual stop requested" in report
        assert "- Iterations Completed: 2" in report
        assert "- Iterations Failed: 1" in report
        assert "- Total Features: 3" in report
        assert "- Success Rate: 66.7%" in report
        assert "| 1 | ✅ | feature-1, feature-2 | 30.5s | - |" in report
        assert "| 2 | ✅ | feature-3 | 25.0s | - |" in report
        assert "| 3 | ❌ | - | 10.0s | Test failed |" in report

    def test_summary_report_truncates_long_errors(self, executor):
        """Test that long errors are truncated in summary."""
        long_error = "This is a very long error message " * 10

        summaries = [
            IterationSummary(1, False, [], 10.0, long_error),
        ]

        result = LoopResult(
            success=False,
            iterations_completed=0,
            iterations_failed=1,
            total_features=0,
            total_duration_seconds=10.0,
            summaries=summaries,
        )

        report = executor.generate_summary_report(result)

        # Error should be truncated to 30 chars + "..."
        assert "..." in report
        lines = report.split("\n")
        error_line = [l for l in lines if "| 1 |" in l][0]
        error_part = error_line.split("|")[5].strip()
        assert len(error_part) <= 34  # 30 + "..."

    def test_summary_report_truncates_many_features(self, executor):
        """Test that many features are truncated in summary."""
        many_features = [f"feature-{i}" for i in range(10)]

        summaries = [
            IterationSummary(1, True, many_features, 30.0),
        ]

        result = LoopResult(
            success=True,
            iterations_completed=1,
            iterations_failed=0,
            total_features=10,
            total_duration_seconds=30.0,
            summaries=summaries,
        )

        report = executor.generate_summary_report(result)

        # Should only show first 2 features + count
        assert "feature-0, feature-1 +8" in report

    def test_final_stats_included(self, executor, mock_orchestrator):
        """Test that final stats are included in result."""
        executor.feature_source = lambda: ["feature-1"]

        stats = TrackerStats(
            total_iterations=3,
            completed_iterations=3,
            failed_iterations=0,
            total_features=3,
            total_tests=15,
            total_lines=300,
        )
        mock_orchestrator.get_stats.return_value = stats
        mock_orchestrator.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["feature-1"],
        )

        executor.config.max_iterations = 3
        result = executor.run()

        assert result.final_stats == stats
        assert result.final_stats.total_iterations == 3
        assert result.final_stats.completed_iterations == 3


class TestFactoryFunction:
    """Tests for create_loop_executor factory function."""

    def test_factory_default_values(self, tmp_path):
        """Test factory with default values."""
        with patch("forge_harness.iteration.loop_executor.create_session_orchestrator"):
            executor = create_loop_executor(
                project_name="test-project",
                repo_path=tmp_path,
            )

            assert executor.config.project_name == "test-project"
            assert executor.config.max_iterations == 100
            assert executor.config.cooldown_seconds == 30.0
            assert executor.repo_path == tmp_path

    def test_factory_custom_values(self, tmp_path):
        """Test factory with custom values."""

        def custom_source():
            return ["feature-1"]

        with patch("forge_harness.iteration.loop_executor.create_session_orchestrator"):
            executor = create_loop_executor(
                project_name="custom-project",
                max_iterations=50,
                cooldown_seconds=10.0,
                repo_path=tmp_path,
                feature_source=custom_source,
            )

            assert executor.config.project_name == "custom-project"
            assert executor.config.max_iterations == 50
            assert executor.config.cooldown_seconds == 10.0
            assert executor.feature_source == custom_source
            assert executor.repo_path == tmp_path

    def test_factory_default_repo_path(self):
        """Test factory uses current directory by default."""
        with patch("forge_harness.iteration.loop_executor.create_session_orchestrator"):
            executor = create_loop_executor(project_name="test-project")

            assert executor.repo_path == Path.cwd()


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_iterations(self, tmp_path):
        """Test loop with zero iterations."""
        config = LoopConfig(project_name="test", max_iterations=0)
        with patch("forge_harness.iteration.loop_executor.create_session_orchestrator"):
            executor = LoopExecutor(config=config, repo_path=tmp_path)
            result = executor.run()

            assert result.iterations_completed == 0
            assert result.iterations_failed == 0
            assert len(result.summaries) == 0

    def test_single_iteration(self, tmp_path):
        """Test loop with single iteration."""
        config = LoopConfig(project_name="test", max_iterations=1, cooldown_seconds=0.01)

        with patch(
            "forge_harness.iteration.loop_executor.create_session_orchestrator"
        ) as mock_create:
            mock_orch = Mock()
            mock_orch.start_iteration = Mock()
            mock_orch.complete_iteration.return_value = SessionResult(
                success=True,
                iteration_number=1,
                features_completed=["feature-1"],
            )
            mock_orch.get_stats.return_value = TrackerStats(
                total_iterations=1,
                completed_iterations=1,
                failed_iterations=0,
                total_features=1,
                total_tests=5,
                total_lines=100,
            )
            mock_create.return_value = mock_orch

            executor = LoopExecutor(config=config, repo_path=tmp_path)
            executor.feature_source = lambda: ["feature-1"]
            result = executor.run()

            assert result.iterations_completed == 1
            assert len(result.summaries) == 1

    def test_no_cooldown_between_iterations(self, tmp_path):
        """Test loop with zero cooldown completes quickly."""
        config = LoopConfig(project_name="test", max_iterations=3, cooldown_seconds=0.0)

        with patch(
            "forge_harness.iteration.loop_executor.create_session_orchestrator"
        ) as mock_create:
            mock_orch = Mock()
            mock_orch.start_iteration = Mock()
            mock_orch.complete_iteration.return_value = SessionResult(
                success=True,
                iteration_number=1,
                features_completed=["feature-1"],
            )
            mock_orch.get_stats.return_value = TrackerStats(
                total_iterations=3,
                completed_iterations=3,
                failed_iterations=0,
                total_features=3,
                total_tests=15,
                total_lines=300,
            )
            mock_create.return_value = mock_orch

            executor = LoopExecutor(config=config, repo_path=tmp_path)
            executor.feature_source = lambda: ["feature-1"]

            start = datetime.now()
            result = executor.run()
            duration = (datetime.now() - start).total_seconds()

            # Should complete very quickly without cooldown
            assert duration < 1.0
            assert result.iterations_completed == 3

    def test_empty_features_list(self, tmp_path):
        """Test handling of empty features list."""
        config = LoopConfig(
            project_name="test",
            max_iterations=5,
            cooldown_seconds=0.01,
            stop_on_all_complete=True,
        )

        with patch(
            "forge_harness.iteration.loop_executor.create_session_orchestrator"
        ) as mock_create:
            mock_orch = Mock()
            mock_create.return_value = mock_orch

            executor = LoopExecutor(config=config, repo_path=tmp_path)
            executor.feature_source = lambda: []  # Always empty

            result = executor.run()

            assert result.iterations_completed == 0
            assert result.stopped_reason == "All features completed"
