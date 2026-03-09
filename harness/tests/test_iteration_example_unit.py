"""Unit tests for forge_harness.iteration.example (main() function).

Coverage:
- main() returns early when features.json does not exist
- main() calls prioritize_tasks() with the correct path
- main() prints the expected task count
- main() prints the top 10 tasks with correct formatting
- main() prints the score distribution table
- main() prints urgent tasks section when urgency >= 4 tasks exist
- main() skips urgent tasks section when no task has urgency >= 4
- main() handles an empty task list gracefully
- main() handles a task list shorter than 10 items
- score distribution bucketing (Very High / High / Medium / Low)
- percentage calculation when tasks list is non-empty
- urgent tasks list is capped at top-5
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    task_id: str = "T-001",
    name: str = "Test Task",
    score: float = 5.0,
    impact: float = 3.0,
    urgency: float = 2.0,
    effort: float = 1.0,
    reasoning: str = "Some reasoning",
) -> SimpleNamespace:
    """Build a minimal task-like object matching the attributes accessed in example.main()."""
    return SimpleNamespace(
        task_id=task_id,
        name=name,
        score=score,
        impact=impact,
        urgency=urgency,
        effort=effort,
        reasoning=reasoning,
    )


def _make_tasks(count: int, urgency: float = 2.0, score: float = 5.0) -> list[SimpleNamespace]:
    """Build *count* identical tasks for convenience."""
    return [
        _make_task(
            task_id=f"T-{i:03d}",
            name=f"Task {i}",
            score=score,
            urgency=urgency,
        )
        for i in range(1, count + 1)
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MODULE = "forge_harness.iteration.example"


# ===========================================================================
# 1. Early-exit when features.json is missing
# ===========================================================================

class TestMissingFeaturesFile:
    def test_returns_none_when_features_json_missing(self, tmp_path):
        """main() must return early (None) when features.json does not exist."""
        from forge_harness.iteration.example import main

        missing = tmp_path / "features.json"
        assert not missing.exists()

        with patch(f"{_MODULE}.Path") as mock_path_cls:
            # Construct a fake path chain that resolves to *missing*
            fake_path = MagicMock(spec=Path)
            fake_path.__truediv__ = MagicMock(return_value=missing)
            fake_path.parent.parent.parent = tmp_path
            mock_path_cls.return_value = fake_path

            result = main()

        assert result is None

    def test_prints_error_message_when_missing(self, tmp_path, capsys):
        """main() must print an error message when features.json is absent."""
        from forge_harness.iteration.example import main

        missing = tmp_path / "nonexistent" / "features.json"

        with patch(f"{_MODULE}.Path") as mock_path_cls:
            fake_path = MagicMock(spec=Path)
            fake_path.__truediv__ = MagicMock(return_value=missing)
            fake_path.parent.parent.parent = tmp_path
            mock_path_cls.return_value = fake_path

            main()

        captured = capsys.readouterr()
        assert "Error" in captured.out
        assert "not found" in captured.out

    def test_prioritize_tasks_not_called_when_file_missing(self, tmp_path):
        """prioritize_tasks() must NOT be called if features.json is absent."""
        from forge_harness.iteration.example import main

        missing = tmp_path / "features.json"

        with patch(f"{_MODULE}.Path") as mock_path_cls, \
             patch(f"{_MODULE}.prioritize_tasks") as mock_prioritize:
            fake_path = MagicMock(spec=Path)
            fake_path.__truediv__ = MagicMock(return_value=missing)
            fake_path.parent.parent.parent = tmp_path
            mock_path_cls.return_value = fake_path

            main()

        mock_prioritize.assert_not_called()


# ===========================================================================
# 2. Successful invocation — basic output
# ===========================================================================

class TestSuccessfulRun:
    def _run_main_with_tasks(self, tmp_path, tasks: list, capsys):
        """Helper: write a dummy features.json, patch prioritize_tasks, run main()."""
        features_file = tmp_path / "features.json"
        features_file.write_text("{}")

        from forge_harness.iteration.example import main

        with patch(f"{_MODULE}.Path") as mock_path_cls, \
             patch(f"{_MODULE}.prioritize_tasks", return_value=tasks):
            fake_path = MagicMock(spec=Path)
            fake_path.__truediv__ = MagicMock(return_value=features_file)
            fake_path.parent.parent.parent = tmp_path
            mock_path_cls.return_value = fake_path

            main()

        return capsys.readouterr()

    def test_calls_prioritize_tasks_with_correct_path(self, tmp_path):
        """main() must call prioritize_tasks() with the resolved features.json path."""
        features_file = tmp_path / "features.json"
        features_file.write_text("{}")

        from forge_harness.iteration.example import main

        with patch(f"{_MODULE}.Path") as mock_path_cls, \
             patch(f"{_MODULE}.prioritize_tasks", return_value=[]) as mock_pt:
            fake_path = MagicMock(spec=Path)
            fake_path.__truediv__ = MagicMock(return_value=features_file)
            fake_path.parent.parent.parent = tmp_path
            mock_path_cls.return_value = fake_path

            main()

        mock_pt.assert_called_once_with(str(features_file))

    def test_prints_task_count(self, tmp_path, capsys):
        """main() must print the number of tasks found."""
        tasks = _make_tasks(5)
        captured = self._run_main_with_tasks(tmp_path, tasks, capsys)
        assert "5" in captured.out
        assert "tasks" in captured.out.lower()

    def test_prints_loading_message(self, tmp_path, capsys):
        """main() must print a loading message before displaying results."""
        tasks = _make_tasks(3)
        captured = self._run_main_with_tasks(tmp_path, tasks, capsys)
        assert "prioritiz" in captured.out.lower()

    def test_prints_top_10_section_header(self, tmp_path, capsys):
        """main() must print the 'Top 10 Prioritized Tasks' header."""
        tasks = _make_tasks(15)
        captured = self._run_main_with_tasks(tmp_path, tasks, capsys)
        assert "Top 10" in captured.out

    def test_only_top_10_tasks_listed_when_more_available(self, tmp_path, capsys):
        """main() must list at most 10 tasks in the top-10 block."""
        tasks = _make_tasks(20)
        captured = self._run_main_with_tasks(tmp_path, tasks, capsys)
        # Each task is printed with its 1-based index; index 11 must NOT appear
        assert ". [T-011]" not in captured.out
        assert "1. [T-001]" in captured.out

    def test_all_tasks_listed_when_fewer_than_10(self, tmp_path, capsys):
        """When fewer than 10 tasks exist, all of them are listed."""
        tasks = _make_tasks(3)
        captured = self._run_main_with_tasks(tmp_path, tasks, capsys)
        assert "1. [T-001]" in captured.out
        assert "3. [T-003]" in captured.out

    def test_task_score_formatted_to_2dp(self, tmp_path, capsys):
        """Task score must be printed to 2 decimal places."""
        tasks = [_make_task(score=7.0)]
        captured = self._run_main_with_tasks(tmp_path, tasks, capsys)
        assert "7.00" in captured.out

    def test_task_impact_urgency_effort_printed(self, tmp_path, capsys):
        """Impact, urgency, and effort must appear for each listed task."""
        tasks = [_make_task(impact=4.0, urgency=3.0, effort=2.0)]
        captured = self._run_main_with_tasks(tmp_path, tasks, capsys)
        assert "Impact" in captured.out
        assert "Urgency" in captured.out
        assert "Effort" in captured.out

    def test_task_reasoning_printed(self, tmp_path, capsys):
        """The task reasoning string must appear in the output."""
        tasks = [_make_task(reasoning="My custom reason")]
        captured = self._run_main_with_tasks(tmp_path, tasks, capsys)
        assert "My custom reason" in captured.out


# ===========================================================================
# 3. Score distribution
# ===========================================================================

class TestScoreDistribution:
    def _run(self, tmp_path, tasks, capsys):
        features_file = tmp_path / "features.json"
        features_file.write_text("{}")

        from forge_harness.iteration.example import main

        with patch(f"{_MODULE}.Path") as mock_path_cls, \
             patch(f"{_MODULE}.prioritize_tasks", return_value=tasks):
            fake_path = MagicMock(spec=Path)
            fake_path.__truediv__ = MagicMock(return_value=features_file)
            fake_path.parent.parent.parent = tmp_path
            mock_path_cls.return_value = fake_path

            main()

        return capsys.readouterr()

    def test_score_distribution_header_printed(self, tmp_path, capsys):
        tasks = _make_tasks(5)
        captured = self._run(tmp_path, tasks, capsys)
        assert "Score Distribution" in captured.out

    def test_very_high_bucket_label_printed(self, tmp_path, capsys):
        tasks = [_make_task(score=12.0)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "Very High" in captured.out

    def test_high_bucket_label_printed(self, tmp_path, capsys):
        tasks = [_make_task(score=7.5)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "High" in captured.out

    def test_medium_bucket_label_printed(self, tmp_path, capsys):
        tasks = [_make_task(score=4.0)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "Medium" in captured.out

    def test_low_bucket_label_printed(self, tmp_path, capsys):
        tasks = [_make_task(score=1.5)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "Low" in captured.out

    def test_score_at_exactly_10_is_very_high(self, tmp_path, capsys):
        """Boundary: score == 10.0 must land in 'Very High (10+)' bucket."""
        tasks = [_make_task(score=10.0)]
        captured = self._run(tmp_path, tasks, capsys)
        # Very High count should be 1
        lines = [l for l in captured.out.splitlines() if "Very High" in l]
        assert lines, "Expected 'Very High' line in output"
        assert "1" in lines[0]

    def test_score_just_below_10_is_high(self, tmp_path, capsys):
        """Boundary: score == 9.99 must land in 'High (5-10)' bucket."""
        tasks = [_make_task(score=9.99)]
        captured = self._run(tmp_path, tasks, capsys)
        lines = [l for l in captured.out.splitlines() if "High (5-10)" in l]
        assert lines
        assert "1" in lines[0]

    def test_percentage_shown_in_distribution(self, tmp_path, capsys):
        """Percentage string must appear in score distribution block."""
        tasks = _make_tasks(4, score=5.0)
        captured = self._run(tmp_path, tasks, capsys)
        assert "%" in captured.out

    def test_empty_task_list_shows_zero_percentages(self, tmp_path, capsys):
        """With an empty task list the distribution must not raise and show 0 counts."""
        captured = self._run(tmp_path, [], capsys)
        assert "0" in captured.out


# ===========================================================================
# 4. Urgent tasks section
# ===========================================================================

class TestUrgentTasksSection:
    def _run(self, tmp_path, tasks, capsys):
        features_file = tmp_path / "features.json"
        features_file.write_text("{}")

        from forge_harness.iteration.example import main

        with patch(f"{_MODULE}.Path") as mock_path_cls, \
             patch(f"{_MODULE}.prioritize_tasks", return_value=tasks):
            fake_path = MagicMock(spec=Path)
            fake_path.__truediv__ = MagicMock(return_value=features_file)
            fake_path.parent.parent.parent = tmp_path
            mock_path_cls.return_value = fake_path

            main()

        return capsys.readouterr()

    def test_urgent_section_printed_when_urgency_gte_4(self, tmp_path, capsys):
        """Urgent section must appear when at least one task has urgency >= 4."""
        tasks = [_make_task(urgency=4.0)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "Urgent" in captured.out

    def test_urgent_section_omitted_when_all_urgency_below_4(self, tmp_path, capsys):
        """Urgent section must NOT appear when all tasks have urgency < 4."""
        tasks = [_make_task(urgency=3.9)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "Urgent" not in captured.out

    def test_urgent_section_shows_task_count(self, tmp_path, capsys):
        """The urgent section header must include the count of urgent tasks."""
        tasks = [_make_task(urgency=5.0), _make_task(task_id="T-002", name="B", urgency=4.5)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "2" in captured.out

    def test_urgent_section_capped_at_5_tasks(self, tmp_path, capsys):
        """Only the first 5 urgent tasks are printed even when more exist.

        The urgent section is separated from the top-10 block by the 'Urgent Tasks'
        header, so we isolate just that portion of the output before asserting
        that task index 6 is absent from it.
        """
        tasks = [_make_task(task_id=f"U-{i:03d}", name=f"Urgent {i}", urgency=5.0) for i in range(1, 10)]
        captured = self._run(tmp_path, tasks, capsys)

        # Split output at the urgent section header so we only inspect that block
        parts = captured.out.split("Urgent Tasks")
        assert len(parts) == 2, "Expected exactly one 'Urgent Tasks' section in output"
        urgent_section = parts[1]

        assert "[U-005]" in urgent_section
        assert "[U-006]" not in urgent_section

    def test_urgent_section_shows_task_id_and_name(self, tmp_path, capsys):
        """Each urgent task line must include the task_id and name."""
        tasks = [_make_task(task_id="URG-1", name="Fix the thing", urgency=4.0)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "URG-1" in captured.out
        assert "Fix the thing" in captured.out

    def test_urgent_section_shows_score(self, tmp_path, capsys):
        """Each urgent task line must include the score."""
        tasks = [_make_task(urgency=4.0, score=8.5)]
        captured = self._run(tmp_path, tasks, capsys)
        assert "8.50" in captured.out
