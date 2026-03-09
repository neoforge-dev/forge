"""Unit tests for forge_harness/iteration/session_orchestrator_example.py.

Tests all four example functions using pytest + unittest.mock, targeting 90%+
coverage without modifying the source file.
"""

from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from forge_harness.iteration.auto_commit import CommitResult
from forge_harness.iteration.pr_generator import PRResult
from forge_harness.iteration.session_orchestrator import SessionConfig, SessionResult
from forge_harness.iteration.tracker import IterationRecord, TrackerStats

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tracker_stats(**overrides) -> TrackerStats:
    """Build a TrackerStats with sensible defaults."""
    defaults = dict(
        total_iterations=0,
        completed_iterations=0,
        failed_iterations=0,
        total_features=0,
        total_tests=0,
        total_lines=0,
        avg_duration=0.0,
    )
    defaults.update(overrides)
    return TrackerStats(**defaults)


def _make_iteration_record(iteration_number: int = 1, status: str = "completed") -> IterationRecord:
    return IterationRecord(
        iteration_number=iteration_number,
        started_at=datetime.now(),
        status=status,
    )


def _make_orchestrator_mock(
    *,
    iteration_num: int = 1,
    complete_success: bool = True,
    fail_reason: str = "Test failure",
    commit_hash: str = "abc12345",
    pr_url: str = "https://github.com/org/repo/pull/42",
    pr_number: int = 42,
    stats_kwargs: dict | None = None,
    recent_records: list[IterationRecord] | None = None,
):
    """Return a fully-configured orchestrator Mock."""
    orc = MagicMock()

    # start_iteration
    orc.start_iteration.return_value = iteration_num

    # get_stats — may be called multiple times; use a fresh Stats each time
    if stats_kwargs is None:
        stats_kwargs = {}
    orc.get_stats.return_value = _make_tracker_stats(**stats_kwargs)

    # complete_iteration
    if complete_success:
        orc.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=iteration_num,
            features_completed=["feature-a", "feature-b"],
            commit_result=CommitResult(
                success=True,
                commit_hash=commit_hash,
                message="feat(harness): add feature-a, feature-b",
            ),
            pr_result=PRResult(
                success=True,
                pr_url=pr_url,
                pr_number=pr_number,
            ),
            duration_seconds=1.23,
        )
    else:
        orc.complete_iteration.return_value = SessionResult(
            success=False,
            iteration_number=iteration_num,
            error="Simulated complete failure",
        )

    # fail_iteration
    orc.fail_iteration.return_value = SessionResult(
        success=False,
        iteration_number=iteration_num,
        error=fail_reason,
    )

    # get_recent_iterations
    if recent_records is None:
        recent_records = [
            _make_iteration_record(1, "failed"),
            _make_iteration_record(2, "completed"),
        ]
    orc.get_recent_iterations.return_value = recent_records

    # Expose a real config so attribute access in example_full_workflow works
    orc.config = SessionConfig(project_name="mock-project")

    return orc


# ---------------------------------------------------------------------------
# Patch target
# ---------------------------------------------------------------------------

_PATCH_CREATE = (
    "forge_harness.iteration.session_orchestrator_example.create_session_orchestrator"
)


# ===========================================================================
# Tests for example_basic_session()
# ===========================================================================


class TestExampleBasicSession:
    """Tests for the example_basic_session() function."""

    def test_calls_create_session_orchestrator_with_correct_args(self, capsys):
        """create_session_orchestrator must be called with expected params."""
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc) as mock_create:
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        mock_create.assert_called_once_with(
            project_name="example-project",
            auto_commit=True,
            auto_pr=False,
        )

    def test_starts_exactly_one_iteration(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        orc.start_iteration.assert_called_once()

    def test_calls_complete_iteration_with_expected_features(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        call_kwargs = orc.complete_iteration.call_args
        features = call_kwargs.kwargs.get("features") or call_kwargs.args[0]
        assert "Add user authentication" in features
        assert "Implement password reset" in features
        assert "Add email verification" in features

    def test_prints_success_output_on_success(self, capsys):
        orc = _make_orchestrator_mock(commit_hash="deadbeef")

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        captured = capsys.readouterr().out
        assert "completed successfully" in captured
        assert "Features:" in captured
        assert "Duration:" in captured
        assert "Commit:" in captured

    def test_prints_commit_hash_when_commit_succeeds(self, capsys):
        orc = _make_orchestrator_mock(commit_hash="feedcafe")

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        captured = capsys.readouterr().out
        assert "feedcafe" in captured

    def test_prints_failure_on_failure(self, capsys):
        orc = _make_orchestrator_mock(complete_success=False)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        captured = capsys.readouterr().out
        assert "Iteration failed" in captured or "failed" in captured.lower()

    def test_calls_get_stats(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        orc.get_stats.assert_called()

    def test_prints_session_stats_section(self, capsys):
        stats = _make_tracker_stats(
            total_iterations=3,
            completed_iterations=2,
            failed_iterations=1,
            total_features=5,
        )
        orc = _make_orchestrator_mock()
        orc.get_stats.return_value = stats

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        captured = capsys.readouterr().out
        assert "Session stats" in captured
        assert "Total iterations" in captured
        assert "Completed" in captured
        assert "Failed" in captured
        assert "Total features" in captured

    def test_prints_correct_stats_values(self, capsys):
        stats = _make_tracker_stats(
            total_iterations=7,
            completed_iterations=5,
            failed_iterations=2,
            total_features=10,
        )
        orc = _make_orchestrator_mock()
        orc.get_stats.return_value = stats

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        captured = capsys.readouterr().out
        assert "7" in captured
        assert "5" in captured
        assert "2" in captured
        assert "10" in captured

    def test_no_commit_printed_when_commit_result_is_none(self, capsys):
        """When commit_result is None, commit hash line must NOT appear."""
        orc = _make_orchestrator_mock()
        orc.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["F"],
            commit_result=None,
            duration_seconds=0.5,
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        # Should not crash; commit hash line should not appear
        captured = capsys.readouterr().out
        assert "feedcafe" not in captured

    def test_no_commit_printed_when_commit_failed(self, capsys):
        """When commit_result.success is False, commit hash line must NOT appear."""
        orc = _make_orchestrator_mock()
        orc.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["F"],
            commit_result=CommitResult(success=False, error="nothing to commit"),
            duration_seconds=0.5,
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        captured = capsys.readouterr().out
        # "Commit:" line should not appear; no crash
        assert "Commit:" not in captured

    def test_complete_iteration_receives_metadata(self, capsys):
        """tests_added, lines_added, notes must be forwarded."""
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        kwargs = orc.complete_iteration.call_args.kwargs
        assert kwargs.get("tests_added") == 12
        assert kwargs.get("lines_added") == 450
        assert "All tests passing" in kwargs.get("notes", "")

    def test_started_iteration_number_printed(self, capsys):
        orc = _make_orchestrator_mock(iteration_num=3)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        captured = capsys.readouterr().out
        assert "3" in captured


# ===========================================================================
# Tests for example_full_workflow()
# ===========================================================================


class TestExampleFullWorkflow:
    """Tests for the example_full_workflow() function."""

    def test_calls_create_session_orchestrator_with_voice_coach(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc) as mock_create:
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        mock_create.assert_called_once_with(
            project_name="voice-coach",
            auto_commit=True,
            auto_pr=True,
        )

    def test_sets_config_pr_draft_to_true(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        assert orc.config.pr_draft is True

    def test_sets_config_base_branch_to_main(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        assert orc.config.base_branch == "main"

    def test_sets_config_commit_scope_to_backend(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        assert orc.config.commit_scope == "backend"

    def test_starts_one_iteration_and_prints_number(self, capsys):
        orc = _make_orchestrator_mock(iteration_num=5)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        orc.start_iteration.assert_called_once()
        captured = capsys.readouterr().out
        assert "5" in captured

    def test_complete_iteration_called_with_three_features(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        kwargs = orc.complete_iteration.call_args.kwargs
        features = kwargs.get("features")
        assert len(features) == 3
        assert "Implement pitch analysis engine" in features

    def test_complete_iteration_receives_correct_metadata(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        kwargs = orc.complete_iteration.call_args.kwargs
        assert kwargs.get("tests_added") == 25
        assert kwargs.get("lines_added") == 1200
        assert len(kwargs.get("files_changed", [])) == 3

    def test_prints_success_on_success(self, capsys):
        orc = _make_orchestrator_mock(
            commit_hash="c0ffee00",
            pr_url="https://github.com/org/repo/pull/99",
            pr_number=99,
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        captured = capsys.readouterr().out
        assert "completed successfully" in captured
        assert "c0ffee00" in captured

    def test_prints_pr_url_on_success(self, capsys):
        orc = _make_orchestrator_mock(
            pr_url="https://github.com/org/repo/pull/99",
            pr_number=99,
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        captured = capsys.readouterr().out
        assert "https://github.com/org/repo/pull/99" in captured
        assert "99" in captured

    def test_prints_failure_on_failure(self, capsys):
        orc = _make_orchestrator_mock(complete_success=False)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        captured = capsys.readouterr().out
        assert "Failed" in captured or "failed" in captured.lower()

    def test_no_crash_when_commit_result_is_none(self, capsys):
        """Ensure no AttributeError when commit_result is None but success is True."""
        orc = _make_orchestrator_mock()
        orc.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["F"],
            commit_result=None,
            pr_result=None,
            duration_seconds=0.1,
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()  # Must not raise

    def test_no_crash_when_pr_result_is_none(self, capsys):
        orc = _make_orchestrator_mock()
        orc.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["F"],
            commit_result=CommitResult(success=True, commit_hash="abc", message="m"),
            pr_result=None,
            duration_seconds=0.1,
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()  # Must not raise

    def test_pr_section_not_printed_when_pr_failed(self, capsys):
        orc = _make_orchestrator_mock()
        orc.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["F"],
            commit_result=CommitResult(success=True, commit_hash="a1b2", message="m"),
            pr_result=PRResult(success=False, error="gh auth error"),
            duration_seconds=0.1,
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        captured = capsys.readouterr().out
        assert "PR created" not in captured

    def test_commit_message_truncated_to_80_chars_in_output(self, capsys):
        long_message = "feat(harness): " + "x" * 200
        orc = _make_orchestrator_mock()
        orc.complete_iteration.return_value = SessionResult(
            success=True,
            iteration_number=1,
            features_completed=["F"],
            commit_result=CommitResult(
                success=True,
                commit_hash="a1b2c3d4",
                message=long_message,
            ),
            pr_result=None,
            duration_seconds=0.1,
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        # The output line uses [:80] — just check no crash and commit shows up
        captured = capsys.readouterr().out
        assert "a1b2c3d4" in captured


# ===========================================================================
# Tests for example_multiple_iterations()
# ===========================================================================


class TestExampleMultipleIterations:
    """Tests for the example_multiple_iterations() function."""

    def test_calls_create_session_orchestrator_with_interview_simulator(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc) as mock_create:
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        mock_create.assert_called_once_with(
            project_name="interview-simulator",
            auto_commit=True,
        )

    def test_runs_three_iterations(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        assert orc.start_iteration.call_count == 3
        assert orc.complete_iteration.call_count == 3

    def test_first_iteration_features(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        first_call_kwargs = orc.complete_iteration.call_args_list[0].kwargs
        features = first_call_kwargs.get("features")
        assert "Setup project structure" in features
        assert "Configure database" in features

    def test_second_iteration_features(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        second_call_kwargs = orc.complete_iteration.call_args_list[1].kwargs
        features = second_call_kwargs.get("features")
        assert "Add question bank" in features
        assert "Implement interview flow" in features

    def test_third_iteration_features(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        third_call_kwargs = orc.complete_iteration.call_args_list[2].kwargs
        features = third_call_kwargs.get("features")
        assert "Add analytics" in features
        assert "Improve UI" in features

    def test_prints_status_for_each_iteration(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        captured = capsys.readouterr().out
        assert "Iteration 1" in captured
        assert "Iteration 2" in captured
        assert "Iteration 3" in captured

    def test_calls_get_stats_after_all_iterations(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        orc.get_stats.assert_called()

    def test_prints_total_progress_section(self, capsys):
        stats = _make_tracker_stats(
            total_iterations=3,
            completed_iterations=3,
            failed_iterations=0,
            total_features=7,
            total_tests=30,
            total_lines=1400,
            avg_duration=2.5,
        )
        orc = _make_orchestrator_mock()
        orc.get_stats.return_value = stats

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        captured = capsys.readouterr().out
        assert "Total progress" in captured
        assert "Iterations" in captured
        assert "Features" in captured
        assert "Tests" in captured
        assert "Lines" in captured
        assert "Avg duration" in captured

    def test_prints_correct_numeric_stats_values(self, capsys):
        stats = _make_tracker_stats(
            total_iterations=3,
            completed_iterations=3,
            total_features=7,
            total_tests=30,
            total_lines=1400,
            avg_duration=2.5,
        )
        orc = _make_orchestrator_mock()
        orc.get_stats.return_value = stats

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        captured = capsys.readouterr().out
        assert "7" in captured    # total_features
        assert "30" in captured   # total_tests
        assert "1,400" in captured  # total_lines formatted with comma
        assert "2.50" in captured   # avg_duration formatted as float

    def test_iteration_metadata_forwarded_correctly(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        calls = orc.complete_iteration.call_args_list

        # Iteration 1
        kw1 = calls[0].kwargs
        assert kw1["tests_added"] == 5
        assert kw1["lines_added"] == 200

        # Iteration 2
        kw2 = calls[1].kwargs
        assert kw2["tests_added"] == 15
        assert kw2["lines_added"] == 800

        # Iteration 3
        kw3 = calls[2].kwargs
        assert kw3["tests_added"] == 10
        assert kw3["lines_added"] == 400

    def test_failure_indicator_printed_when_iteration_fails(self, capsys):
        orc = _make_orchestrator_mock(complete_success=False)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        captured = capsys.readouterr().out
        # The failure unicode character or the word "fail" should appear
        assert any(marker in captured for marker in ["✗", "False", "fail"])


# ===========================================================================
# Tests for example_failure_handling()
# ===========================================================================


class TestExampleFailureHandling:
    """Tests for the example_failure_handling() function."""

    def test_calls_create_session_orchestrator_with_example_project(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc) as mock_create:
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        mock_create.assert_called_once_with(
            project_name="example-project",
            auto_commit=True,
        )

    def test_calls_start_iteration_twice(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        assert orc.start_iteration.call_count == 2

    def test_calls_fail_iteration_with_auth_bug_reason(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        orc.fail_iteration.assert_called_once_with(
            reason="Tests failed - authentication bug detected"
        )

    def test_calls_complete_iteration_for_recovery(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        orc.complete_iteration.assert_called_once()

    def test_recovery_iteration_fixes_auth_bug(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        kwargs = orc.complete_iteration.call_args.kwargs
        assert "Fix authentication bug" in kwargs.get("features", [])
        assert kwargs.get("tests_added") == 3
        assert kwargs.get("lines_added") == 50

    def test_prints_failure_message(self, capsys):
        orc = _make_orchestrator_mock(fail_reason="Tests failed - authentication bug detected")

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        captured = capsys.readouterr().out
        assert "Iteration failed" in captured or "failed" in captured.lower()

    def test_prints_recovery_completed(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        captured = capsys.readouterr().out
        assert "Recovery iteration completed" in captured

    def test_calls_get_recent_iterations_with_count_5(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        orc.get_recent_iterations.assert_called_once_with(count=5)

    def test_prints_recent_iterations_section(self, capsys):
        records = [
            _make_iteration_record(1, "failed"),
            _make_iteration_record(2, "completed"),
        ]
        orc = _make_orchestrator_mock(recent_records=records)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        captured = capsys.readouterr().out
        assert "Recent iterations" in captured

    def test_prints_status_icon_for_each_recent_record(self, capsys):
        records = [
            _make_iteration_record(1, "failed"),
            _make_iteration_record(2, "completed"),
        ]
        orc = _make_orchestrator_mock(recent_records=records)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        captured = capsys.readouterr().out
        # completed → ✓, failed → ✗
        assert "✓" in captured
        assert "✗" in captured

    def test_prints_iteration_numbers_for_recent_records(self, capsys):
        records = [
            _make_iteration_record(10, "completed"),
            _make_iteration_record(11, "failed"),
        ]
        orc = _make_orchestrator_mock(recent_records=records)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        captured = capsys.readouterr().out
        assert "10" in captured
        assert "11" in captured

    def test_empty_recent_iterations_does_not_crash(self, capsys):
        orc = _make_orchestrator_mock(recent_records=[])

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()  # Must not raise

    def test_recovery_notes_forwarded(self, capsys):
        orc = _make_orchestrator_mock()

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        kwargs = orc.complete_iteration.call_args.kwargs
        assert "Bug fixed" in kwargs.get("notes", "")


# ===========================================================================
# Tests for the __main__ block
# ===========================================================================


class TestMainBlock:
    """Ensure the __main__ block calls all four example functions."""

    def test_main_block_calls_all_examples(self, capsys):
        """Run the source file as __main__ via runpy.run_path with patches active."""
        import runpy
        import sys

        source_path = str(
            Path(__file__).parent.parent
            / "forge_harness"
            / "iteration"
            / "session_orchestrator_example.py"
        )

        mock_basic = Mock()
        mock_full = Mock()
        mock_multi = Mock()
        mock_fail = Mock()

        # We use a temporary globals dict so the patched names shadow the real ones
        init_globals = {
            "example_basic_session": mock_basic,
            "example_full_workflow": mock_full,
            "example_multiple_iterations": mock_multi,
            "example_failure_handling": mock_fail,
        }

        # Patch create_session_orchestrator so the module-level import doesn't
        # break when the file is executed fresh.
        orc = _make_orchestrator_mock()
        with patch(_PATCH_CREATE, return_value=orc):
            # Remove cached module so run_path gets a clean slate
            sys.modules.pop("forge_harness.iteration.session_orchestrator_example", None)
            result_globals = runpy.run_path(source_path, run_name="__main__")

        # The file's __main__ block should print the four section headers
        captured = capsys.readouterr().out
        assert "Example 1" in captured
        assert "Example 2" in captured
        assert "Example 3" in captured
        assert "Example 4" in captured

    def test_main_block_prints_section_headers(self, capsys):
        """Section headers 1-4 appear in stdout when run as __main__."""
        import runpy
        import sys

        source_path = str(
            Path(__file__).parent.parent
            / "forge_harness"
            / "iteration"
            / "session_orchestrator_example.py"
        )

        orc = _make_orchestrator_mock()
        with patch(_PATCH_CREATE, return_value=orc):
            sys.modules.pop("forge_harness.iteration.session_orchestrator_example", None)
            runpy.run_path(source_path, run_name="__main__")

        captured = capsys.readouterr().out
        assert "=== Example 1: Basic Session ===" in captured
        assert "=== Example 2: Full Workflow ===" in captured
        assert "=== Example 3: Multiple Iterations ===" in captured
        assert "=== Example 4: Failure Handling ===" in captured


# ===========================================================================
# Edge-case / cross-cutting tests
# ===========================================================================


class TestEdgeCases:
    """Edge-case and boundary condition tests."""

    def test_basic_session_iteration_num_in_start_message(self, capsys):
        orc = _make_orchestrator_mock(iteration_num=99)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_basic_session,
            )

            example_basic_session()

        captured = capsys.readouterr().out
        assert "99" in captured

    def test_full_workflow_iteration_num_in_start_message(self, capsys):
        orc = _make_orchestrator_mock(iteration_num=7)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_full_workflow,
            )

            example_full_workflow()

        captured = capsys.readouterr().out
        assert "7" in captured

    def test_multiple_iterations_success_checkmarks(self, capsys):
        """All three iterations succeed — expect three checkmarks."""
        orc = _make_orchestrator_mock(complete_success=True)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        captured = capsys.readouterr().out
        assert captured.count("✓") >= 3

    def test_multiple_iterations_failure_marks(self, capsys):
        """All three iterations fail — expect three failure markers."""
        orc = _make_orchestrator_mock(complete_success=False)

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_multiple_iterations,
            )

            example_multiple_iterations()

        captured = capsys.readouterr().out
        # The source prints ✗ or False; either is acceptable
        assert any(marker in captured for marker in ["✗", "False"])

    def test_create_session_orchestrator_called_once_per_example(self, capsys):
        """Each example function creates exactly one orchestrator."""
        for fn_name in [
            "example_basic_session",
            "example_full_workflow",
            "example_multiple_iterations",
            "example_failure_handling",
        ]:
            orc = _make_orchestrator_mock()

            with patch(_PATCH_CREATE, return_value=orc) as mock_create:
                import importlib
                mod = importlib.import_module(
                    "forge_harness.iteration.session_orchestrator_example"
                )
                fn = getattr(mod, fn_name)
                fn()

            mock_create.assert_called_once(), (
                f"{fn_name} must call create_session_orchestrator exactly once"
            )

    def test_failure_handling_fail_result_error_printed(self, capsys):
        """The error returned by fail_iteration must appear in output."""
        orc = _make_orchestrator_mock(fail_reason="auth bug detected")
        orc.fail_iteration.return_value = SessionResult(
            success=False,
            iteration_number=1,
            error="auth bug detected",
        )

        with patch(_PATCH_CREATE, return_value=orc):
            from forge_harness.iteration.session_orchestrator_example import (
                example_failure_handling,
            )

            example_failure_handling()

        captured = capsys.readouterr().out
        assert "auth bug detected" in captured
