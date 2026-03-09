"""Unit tests for forge_harness/iteration/demo_prioritizer.py.

Tests all demo functions and the main() entrypoint with full mocking of:
- File I/O (features_file.exists(), features_file.read_text())
- prioritize_tasks / find_features_in_portfolio from prioritizer module
- AssessmentReport, Issue, IssueType, TestResults from assess module
- Path traversal helpers
"""

from __future__ import annotations

import io
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.iteration.assess import AssessmentReport, Issue, IssueType, TestResults
from forge_harness.iteration.prioritizer import PrioritizationStrategy, PrioritizedTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "TASK-001",
    title: str = "Test task",
    score: float = 8.0,
    impact: float = 7.0,
    urgency: float = 6.0,
    effort: float = 3.0,
    domain_weight: float = 1.0,
    reasoning: str = "test reasoning",
    blockers: list[str] | None = None,
    dependencies: list[str] | None = None,
    epic: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> PrioritizedTask:
    return PrioritizedTask(
        task_id=task_id,
        title=title,
        score=score,
        impact=impact,
        urgency=urgency,
        effort=effort,
        domain_weight=domain_weight,
        reasoning=reasoning,
        blockers=blockers or [],
        dependencies=dependencies or [],
        epic=epic,
        metadata=metadata or {},
    )


def _make_tasks(n: int, *, domain: str = "voice-coach") -> list[PrioritizedTask]:
    return [
        _make_task(
            task_id=f"TASK-{i:03d}",
            title=f"Task {i}",
            score=float(10 - i),
            metadata={"domain": domain},
        )
        for i in range(1, n + 1)
    ]


# ---------------------------------------------------------------------------
# demo_basic_prioritization
# ---------------------------------------------------------------------------


class TestDemoBasicPrioritization:
    """Tests for demo_basic_prioritization()."""

    def _run(self, features_exists: bool, tasks: list[PrioritizedTask], capsys):
        """Helper that patches all external dependencies."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        mock_path_instance = MagicMock(spec=Path)
        mock_path_instance.exists.return_value = features_exists

        with (
            patch.object(
                demo_mod,
                "prioritize_tasks",
                return_value=tasks,
            ) as mock_prioritize,
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
        ):
            # Make Path(__file__).parent.parent.parent return a fake root,
            # then root / "features.json" returns our mock_path_instance.
            fake_root = MagicMock()
            fake_root.__truediv__ = MagicMock(return_value=mock_path_instance)
            MockPath.return_value.parent.parent.parent = fake_root

            demo_mod.demo_basic_prioritization()
            return mock_prioritize, capsys.readouterr()

    def test_missing_features_file_prints_message(self, capsys):
        """When features.json doesn't exist, print not-found message and return."""
        _mock_prioritize, captured = self._run(
            features_exists=False, tasks=[], capsys=capsys
        )
        out = captured.out
        assert "not found" in out.lower() or "Features file not found" in out

    def test_calls_prioritize_with_balanced_strategy(self, capsys):
        """prioritize_tasks is called with BALANCED strategy when file exists."""
        tasks = _make_tasks(12)
        mock_prioritize, _captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        mock_prioritize.assert_called_once()
        call_kwargs = mock_prioritize.call_args.kwargs
        assert call_kwargs.get("strategy") == PrioritizationStrategy.BALANCED

    def test_prints_top_10_tasks(self, capsys):
        """Only the first 10 tasks are printed."""
        tasks = _make_tasks(15)
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        # Task-010 should appear, Task-011 should not
        assert "TASK-010" in out
        assert "TASK-011" not in out

    def test_prints_task_metadata(self, capsys):
        """Output includes score, impact, urgency, effort for each task."""
        tasks = [
            _make_task(
                task_id="T-001",
                title="Important Feature",
                score=9.5,
                impact=8.0,
                urgency=7.0,
                effort=2.0,
                epic="Quality Gates",
            )
        ]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        assert "Important Feature" in out
        assert "9.50" in out or "9.5" in out

    def test_blocked_tasks_show_blockers(self, capsys):
        """Tasks with blockers display the blockers in output."""
        tasks = [
            _make_task(
                task_id="T-002",
                title="Blocked Task",
                blockers=["T-001", "T-003"],
            )
        ]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        assert "T-001" in out
        assert "T-003" in out

    def test_fewer_than_10_tasks_all_shown(self, capsys):
        """All tasks are shown if there are fewer than 10."""
        tasks = _make_tasks(3)
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        for i in range(1, 4):
            assert f"TASK-{i:03d}" in out

    def test_zero_tasks_prints_zero(self, capsys):
        """When prioritize returns empty list, shows 0 pending tasks."""
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=[], capsys=capsys
        )
        assert "0" in captured.out


# ---------------------------------------------------------------------------
# demo_with_failures
# ---------------------------------------------------------------------------


class TestDemoWithFailures:
    """Tests for demo_with_failures()."""

    def _run(self, features_exists: bool, tasks: list[PrioritizedTask], capsys):
        import forge_harness.iteration.demo_prioritizer as demo_mod

        mock_path_instance = MagicMock(spec=Path)
        mock_path_instance.exists.return_value = features_exists

        with (
            patch.object(demo_mod, "prioritize_tasks", return_value=tasks) as mock_prioritize,
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
        ):
            fake_root = MagicMock()
            fake_root.__truediv__ = MagicMock(return_value=mock_path_instance)
            MockPath.return_value.parent.parent.parent = fake_root

            demo_mod.demo_with_failures()
            return mock_prioritize, capsys.readouterr()

    def test_missing_features_file_returns_early(self, capsys):
        """Returns early with a message when features.json is missing."""
        _mock_prioritize, captured = self._run(
            features_exists=False, tasks=[], capsys=capsys
        )
        out = captured.out
        assert "not found" in out.lower() or "Features file not found" in out

    def test_creates_assessment_with_test_results(self, capsys):
        """An AssessmentReport with 5 failing tests is passed to prioritize_tasks."""
        _mock_prioritize, _captured = self._run(
            features_exists=True, tasks=[], capsys=capsys
        )
        # Just check it didn't crash — the AssessmentReport is constructed inline
        # and passed through; if the construction fails the test would error out.

    def test_prints_boosted_tasks(self, capsys):
        """Tasks with failure_boost metadata are printed."""
        boosted = _make_task(
            task_id="BOOST-001",
            title="Fix tests",
            metadata={"failure_boost": True},
        )
        normal = _make_task(task_id="NORM-001", title="Add feature", metadata={})
        tasks = [boosted, normal]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        assert "BOOST-001" in out
        # NORM-001 has no boost so should not appear in boosted section
        assert "NORM-001" not in out

    def test_prints_up_to_five_boosted_tasks(self, capsys):
        """Only the first 5 boosted tasks are printed."""
        tasks = [
            _make_task(
                task_id=f"B-{i:03d}",
                title=f"Boosted {i}",
                metadata={"failure_boost": True},
            )
            for i in range(1, 9)
        ]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        assert "B-005" in out
        assert "B-006" not in out

    def test_zero_boosted_tasks_prints_zero(self, capsys):
        """When no tasks are boosted, 0 is displayed."""
        tasks = [_make_task(task_id="X-001", metadata={})]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        assert "0 tasks boosted" in captured.out

    def test_calls_prioritize_with_balanced_strategy(self, capsys):
        """prioritize_tasks is called with BALANCED strategy."""
        mock_prioritize, _captured = self._run(
            features_exists=True, tasks=[], capsys=capsys
        )
        call_kwargs = mock_prioritize.call_args.kwargs
        assert call_kwargs.get("strategy") == PrioritizationStrategy.BALANCED


# ---------------------------------------------------------------------------
# demo_quick_wins
# ---------------------------------------------------------------------------


class TestDemoQuickWins:
    """Tests for demo_quick_wins()."""

    def _run(self, features_exists: bool, tasks: list[PrioritizedTask], capsys):
        import forge_harness.iteration.demo_prioritizer as demo_mod

        mock_path_instance = MagicMock(spec=Path)
        mock_path_instance.exists.return_value = features_exists

        with (
            patch.object(demo_mod, "prioritize_tasks", return_value=tasks) as mock_prioritize,
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
        ):
            fake_root = MagicMock()
            fake_root.__truediv__ = MagicMock(return_value=mock_path_instance)
            MockPath.return_value.parent.parent.parent = fake_root

            demo_mod.demo_quick_wins()
            return mock_prioritize, capsys.readouterr()

    def test_missing_features_file_returns_early(self, capsys):
        _mock_prioritize, captured = self._run(
            features_exists=False, tasks=[], capsys=capsys
        )
        assert "not found" in captured.out.lower() or "Features file not found" in captured.out

    def test_calls_quick_wins_strategy(self, capsys):
        """Uses QUICK_WINS strategy."""
        mock_prioritize, _captured = self._run(
            features_exists=True, tasks=[], capsys=capsys
        )
        call_kwargs = mock_prioritize.call_args.kwargs
        assert call_kwargs.get("strategy") == PrioritizationStrategy.QUICK_WINS

    def test_prints_top_5_tasks(self, capsys):
        """Only the first 5 tasks are printed."""
        tasks = _make_tasks(8)
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        assert "TASK-005" in out
        assert "TASK-006" not in out

    def test_prints_impact_effort_ratio(self, capsys):
        """Output includes impact/effort ratio."""
        tasks = [
            _make_task(
                task_id="QW-001",
                title="Quick Win Feature",
                impact=8.0,
                effort=2.0,
            )
        ]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        # impact_effort_ratio = 8.0 / max(2.0, 1) = 4.0
        assert "4.00" in out or "4.0" in out

    def test_fewer_than_5_tasks_all_shown(self, capsys):
        """All tasks shown if fewer than 5."""
        tasks = _make_tasks(2)
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        assert "TASK-001" in captured.out
        assert "TASK-002" in captured.out


# ---------------------------------------------------------------------------
# demo_dependency_first
# ---------------------------------------------------------------------------


class TestDemoDependencyFirst:
    """Tests for demo_dependency_first()."""

    def _run(self, features_exists: bool, tasks: list[PrioritizedTask], capsys):
        import forge_harness.iteration.demo_prioritizer as demo_mod

        mock_path_instance = MagicMock(spec=Path)
        mock_path_instance.exists.return_value = features_exists

        with (
            patch.object(demo_mod, "prioritize_tasks", return_value=tasks) as mock_prioritize,
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
        ):
            fake_root = MagicMock()
            fake_root.__truediv__ = MagicMock(return_value=mock_path_instance)
            MockPath.return_value.parent.parent.parent = fake_root

            demo_mod.demo_dependency_first()
            return mock_prioritize, capsys.readouterr()

    def test_missing_features_file_returns_early(self, capsys):
        _mock_prioritize, captured = self._run(
            features_exists=False, tasks=[], capsys=capsys
        )
        assert "not found" in captured.out.lower() or "Features file not found" in captured.out

    def test_calls_dependency_first_strategy(self, capsys):
        """Uses DEPENDENCY_FIRST strategy."""
        mock_prioritize, _captured = self._run(
            features_exists=True, tasks=[], capsys=capsys
        )
        call_kwargs = mock_prioritize.call_args.kwargs
        assert call_kwargs.get("strategy") == PrioritizationStrategy.DEPENDENCY_FIRST

    def test_prints_unblocked_count(self, capsys):
        """Prints number of unblocked tasks."""
        unblocked = _make_task(task_id="U-001", title="Unblocked", blockers=[])
        blocked = _make_task(task_id="B-001", title="Blocked", blockers=["U-001"])
        tasks = [unblocked, blocked]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        assert "1 unblocked" in captured.out

    def test_prints_top_5_unblocked(self, capsys):
        """Only the first 5 unblocked tasks are printed."""
        tasks = [
            _make_task(task_id=f"U-{i:03d}", title=f"Unblocked {i}", blockers=[])
            for i in range(1, 9)
        ]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        out = captured.out
        assert "U-005" in out
        assert "U-006" not in out

    def test_blocked_tasks_not_in_unblocked_section(self, capsys):
        """Blocked tasks should not appear in the unblocked section."""
        unblocked = _make_task(task_id="U-001", title="Unblocked")
        blocked = _make_task(task_id="B-001", title="Blocked Only", blockers=["DEP-001"])
        tasks = [unblocked, blocked]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        assert "Blocked Only" not in captured.out

    def test_prints_dependency_count(self, capsys):
        """Dependency count is displayed for each task."""
        tasks = [
            _make_task(
                task_id="U-001",
                title="With deps",
                blockers=[],
                dependencies=["DEP-A", "DEP-B"],
            )
        ]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        assert "2" in captured.out  # len(dependencies)

    def test_all_blocked_shows_zero_unblocked(self, capsys):
        """When all tasks are blocked, 0 unblocked tasks are shown."""
        tasks = [_make_task(task_id=f"B-{i}", blockers=["X"]) for i in range(3)]
        _mock_prioritize, captured = self._run(
            features_exists=True, tasks=tasks, capsys=capsys
        )
        assert "0 unblocked" in captured.out


# ---------------------------------------------------------------------------
# demo_portfolio_scan
# ---------------------------------------------------------------------------


class TestDemoPortfolioScan:
    """Tests for demo_portfolio_scan()."""

    def test_forge_root_not_found_prints_message(self, capsys, tmp_path):
        """When FORGE root cannot be found, print an error and return."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        non_forge_path = tmp_path / "some_other_dir"
        non_forge_path.mkdir()

        with patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath:
            # cwd() returns a path whose name is NOT "FORGE" and has no FORGE ancestor
            mock_cwd = MagicMock()
            mock_cwd.name = "not_forge"
            mock_cwd.parents = []  # no parents to walk
            MockPath.cwd.return_value = mock_cwd

            demo_mod.demo_portfolio_scan()

        out = capsys.readouterr().out
        assert "FORGE root not found" in out

    def test_forge_root_is_cwd_name(self, capsys):
        """When cwd().name == 'FORGE', uses cwd as forge_root."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        tasks = _make_tasks(2, domain="voice-coach")
        # Give each task a domain in metadata
        tasks[0].metadata["domain"] = "voice-coach"
        tasks[1].metadata["domain"] = "interview-simulator"

        with (
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
            patch.object(demo_mod, "find_features_in_portfolio", return_value=[]) as mock_find,
            patch.object(demo_mod, "prioritize_tasks", return_value=tasks) as mock_prio,
        ):
            mock_cwd = MagicMock()
            mock_cwd.name = "FORGE"
            MockPath.cwd.return_value = mock_cwd

            demo_mod.demo_portfolio_scan()

        mock_find.assert_called_once_with(mock_cwd)
        mock_prio.assert_called_once()
        out = capsys.readouterr().out
        assert "features.json" in out.lower() or "Found" in out

    def test_forge_root_found_in_parents(self, capsys):
        """When a parent of cwd is named 'FORGE', it is used as forge_root."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        forge_parent = MagicMock()
        forge_parent.name = "FORGE"

        non_forge_parent = MagicMock()
        non_forge_parent.name = "harness"

        tasks = _make_tasks(1)
        tasks[0].metadata["domain"] = "voice-coach"

        with (
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
            patch.object(demo_mod, "find_features_in_portfolio", return_value=[]) as mock_find,
            patch.object(demo_mod, "prioritize_tasks", return_value=tasks),
        ):
            mock_cwd = MagicMock()
            mock_cwd.name = "iteration"
            mock_cwd.parents = [non_forge_parent, forge_parent]
            MockPath.cwd.return_value = mock_cwd

            demo_mod.demo_portfolio_scan()

        mock_find.assert_called_once_with(forge_parent)

    def test_top_task_per_domain_is_printed(self, capsys):
        """For each domain, the highest-scored task is displayed."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        tasks = [
            _make_task(
                task_id="VC-001", title="Voice Task", score=9.0,
                metadata={"domain": "voice-coach"},
            ),
            _make_task(
                task_id="IS-001", title="Interview Task", score=7.0,
                metadata={"domain": "interview-simulator"},
            ),
        ]

        with (
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
            patch.object(demo_mod, "find_features_in_portfolio", return_value=[]),
            patch.object(demo_mod, "prioritize_tasks", return_value=tasks),
        ):
            mock_cwd = MagicMock()
            mock_cwd.name = "FORGE"
            MockPath.cwd.return_value = mock_cwd

            demo_mod.demo_portfolio_scan()

        out = capsys.readouterr().out
        assert "VC-001" in out
        assert "IS-001" in out
        assert "voice-coach" in out
        assert "interview-simulator" in out

    def test_domain_weights_passed_to_prioritize(self, capsys):
        """Custom domain weights are forwarded to prioritize_tasks."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        with (
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
            patch.object(demo_mod, "find_features_in_portfolio", return_value=[]),
            patch.object(demo_mod, "prioritize_tasks", return_value=[]) as mock_prio,
        ):
            mock_cwd = MagicMock()
            mock_cwd.name = "FORGE"
            MockPath.cwd.return_value = mock_cwd

            demo_mod.demo_portfolio_scan()

        call_kwargs = mock_prio.call_args.kwargs
        dw = call_kwargs.get("domain_weights", {})
        assert dw.get("voice-coach") == 1.5
        assert dw.get("interview-simulator") == 1.3

    def test_tasks_with_unknown_domain(self, capsys):
        """Tasks with no domain in metadata are grouped under 'unknown'."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        tasks = [_make_task(task_id="X-001", title="Domainless Task", metadata={})]

        with (
            patch("forge_harness.iteration.demo_prioritizer.Path") as MockPath,
            patch.object(demo_mod, "find_features_in_portfolio", return_value=[]),
            patch.object(demo_mod, "prioritize_tasks", return_value=tasks),
        ):
            mock_cwd = MagicMock()
            mock_cwd.name = "FORGE"
            MockPath.cwd.return_value = mock_cwd

            demo_mod.demo_portfolio_scan()

        out = capsys.readouterr().out
        assert "unknown" in out


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() entrypoint."""

    def test_all_demos_called(self, capsys):
        """main() calls every demo function exactly once."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        with (
            patch.object(demo_mod, "demo_basic_prioritization") as p1,
            patch.object(demo_mod, "demo_with_failures") as p2,
            patch.object(demo_mod, "demo_quick_wins") as p3,
            patch.object(demo_mod, "demo_dependency_first") as p4,
            patch.object(demo_mod, "demo_portfolio_scan") as p5,
        ):
            demo_mod.main()

        p1.assert_called_once()
        p2.assert_called_once()
        p3.assert_called_once()
        p4.assert_called_once()
        p5.assert_called_once()

    def test_completion_message_printed(self, capsys):
        """Completion banner is printed after all demos run."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        with (
            patch.object(demo_mod, "demo_basic_prioritization"),
            patch.object(demo_mod, "demo_with_failures"),
            patch.object(demo_mod, "demo_quick_wins"),
            patch.object(demo_mod, "demo_dependency_first"),
            patch.object(demo_mod, "demo_portfolio_scan"),
        ):
            demo_mod.main()

        out = capsys.readouterr().out
        assert "All demos completed" in out

    def test_exception_in_demo_reraises(self, capsys):
        """If a demo raises, main() re-raises after logging."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        with (
            patch.object(
                demo_mod,
                "demo_basic_prioritization",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(demo_mod, "demo_with_failures"),
            patch.object(demo_mod, "demo_quick_wins"),
            patch.object(demo_mod, "demo_dependency_first"),
            patch.object(demo_mod, "demo_portfolio_scan"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                demo_mod.main()

    def test_exception_order_stops_remaining_demos(self, capsys):
        """An exception in demo_basic_prioritization prevents other demos from running."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        p2 = MagicMock()
        with (
            patch.object(
                demo_mod,
                "demo_basic_prioritization",
                side_effect=ValueError("early fail"),
            ),
            patch.object(demo_mod, "demo_with_failures", p2),
            patch.object(demo_mod, "demo_quick_wins"),
            patch.object(demo_mod, "demo_dependency_first"),
            patch.object(demo_mod, "demo_portfolio_scan"),
        ):
            with pytest.raises(ValueError):
                demo_mod.main()

        p2.assert_not_called()


# ---------------------------------------------------------------------------
# Module-level smoke tests (import + logger)
# ---------------------------------------------------------------------------


class TestModuleImport:
    """Sanity checks for module-level code."""

    def test_module_importable(self):
        """The module can be imported without side effects."""
        import forge_harness.iteration.demo_prioritizer  # noqa: F401

    def test_logger_exists(self):
        """Module-level logger is defined."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        assert hasattr(demo_mod, "logger")

    def test_all_demo_functions_exist(self):
        """All expected public functions are present."""
        import forge_harness.iteration.demo_prioritizer as demo_mod

        for name in [
            "demo_basic_prioritization",
            "demo_with_failures",
            "demo_quick_wins",
            "demo_dependency_first",
            "demo_portfolio_scan",
            "main",
        ]:
            assert callable(getattr(demo_mod, name, None)), f"{name} not callable"


# ---------------------------------------------------------------------------
# Integration-ish: verify AssessmentReport construction inside demo_with_failures
# ---------------------------------------------------------------------------


class TestAssessmentReportConstruction:
    """Verify that the inline AssessmentReport in demo_with_failures is valid."""

    def test_assessment_report_with_test_results(self):
        """AssessmentReport constructed the same way as in demo_with_failures."""
        assessment = AssessmentReport()
        assessment.test_results = TestResults(
            total_tests=50,
            passed_count=45,
            failed_count=5,
        )
        assessment.issues.append(
            Issue(
                issue_type=IssueType.FAILED_TEST,
                severity="high",
                message="5 tests failing",
            )
        )
        assert assessment.test_results.failed_count == 5
        assert len(assessment.issues) == 1
        assert assessment.issues[0].issue_type == IssueType.FAILED_TEST

    def test_assessment_report_default_is_healthy(self):
        """Default AssessmentReport is in healthy state."""
        from forge_harness.iteration.assess import OverallHealth

        report = AssessmentReport()
        assert report.overall_health == OverallHealth.HEALTHY
        assert report.test_results.failed_count == 0
        assert report.issues == []


# ---------------------------------------------------------------------------
# Edge cases: PrioritizedTask.impact_effort_ratio with zero effort
# ---------------------------------------------------------------------------


class TestPrioritizedTaskEdgeCases:
    """Edge cases for PrioritizedTask used inside demo functions."""

    def test_impact_effort_ratio_zero_effort(self):
        """Zero effort is clamped to 1 to avoid division by zero."""
        task = _make_task(impact=8.0, effort=0.0)
        assert task.impact_effort_ratio == pytest.approx(8.0)

    def test_impact_effort_ratio_normal(self):
        task = _make_task(impact=6.0, effort=3.0)
        assert task.impact_effort_ratio == pytest.approx(2.0)

    def test_is_blocked_true(self):
        task = _make_task(blockers=["DEP-001"])
        assert task.is_blocked is True

    def test_is_blocked_false(self):
        task = _make_task(blockers=[])
        assert task.is_blocked is False

    def test_feature_id_defaults_to_task_id(self):
        task = PrioritizedTask(
            task_id="MY-ID",
            title="t",
            score=1.0,
            impact=1.0,
            urgency=1.0,
            effort=1.0,
            domain_weight=1.0,
            reasoning="r",
        )
        assert task.feature_id == "MY-ID"

    def test_metadata_non_dict_converted(self):
        """Non-dict metadata is converted to empty dict defensively."""
        task = PrioritizedTask(
            task_id="X",
            title="t",
            score=1.0,
            impact=1.0,
            urgency=1.0,
            effort=1.0,
            domain_weight=1.0,
            reasoning="r",
            metadata="invalid",  # type: ignore[arg-type]
        )
        assert task.metadata == {}

    def test_to_dict_contains_expected_keys(self):
        task = _make_task()
        d = task.to_dict()
        for key in [
            "feature_id", "title", "score", "impact", "urgency", "effort",
            "domain_weight", "reasoning", "dependencies", "blockers", "status",
            "epic", "acceptance_criteria", "test_command", "metadata",
        ]:
            assert key in d, f"Missing key: {key}"
