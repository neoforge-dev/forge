"""Unit tests for forge_harness.iteration.demo_prioritizer and prioritizer module.

Tests cover:
- demo functions (basic, failures, quick wins, dependency-first, portfolio scan)
- PrioritizedTask dataclass (properties, to_dict, __post_init__)
- PrioritizationStrategy enum
- calculate_score function
- estimate_effort_from_criteria
- identify_dependencies / identify_blockers
- load_features_from_file / find_features_in_portfolio
- prioritize_tasks (various strategies)
- boost_for_failures
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.iteration.assess import (
    AssessmentReport,
    GitStatus,
    Issue,
    IssueType,
    TestResults,
)
from forge_harness.iteration.prioritizer import (
    PrioritizationStrategy,
    PrioritizedTask,
    _estimate_impact,
    _estimate_urgency,
    boost_for_failures,
    calculate_score,
    estimate_effort_from_criteria,
    find_features_in_portfolio,
    identify_blockers,
    identify_dependencies,
    load_features_from_file,
    prioritize_tasks,
)

# =============================================================================
# PrioritizedTask dataclass tests
# =============================================================================


class TestPrioritizedTask:
    """Tests for PrioritizedTask dataclass."""

    def test_feature_id_defaults_to_task_id(self):
        t = PrioritizedTask(
            task_id="T-1", title="Test", score=5.0, impact=5.0,
            urgency=3.0, effort=2.0, domain_weight=1.0, reasoning="test",
        )
        assert t.feature_id == "T-1"

    def test_feature_id_explicit(self):
        t = PrioritizedTask(
            task_id="T-1", title="Test", score=5.0, impact=5.0,
            urgency=3.0, effort=2.0, domain_weight=1.0, reasoning="test",
            feature_id="F-1",
        )
        assert t.feature_id == "F-1"

    def test_is_blocked_true(self):
        t = PrioritizedTask(
            task_id="T-1", title="Test", score=5.0, impact=5.0,
            urgency=3.0, effort=2.0, domain_weight=1.0, reasoning="test",
            blockers=["T-2"],
        )
        assert t.is_blocked is True

    def test_is_blocked_false(self):
        t = PrioritizedTask(
            task_id="T-1", title="Test", score=5.0, impact=5.0,
            urgency=3.0, effort=2.0, domain_weight=1.0, reasoning="test",
        )
        assert t.is_blocked is False

    def test_impact_effort_ratio(self):
        t = PrioritizedTask(
            task_id="T-1", title="Test", score=5.0, impact=10.0,
            urgency=3.0, effort=2.0, domain_weight=1.0, reasoning="test",
        )
        assert t.impact_effort_ratio == 5.0

    def test_impact_effort_ratio_zero_effort(self):
        """Effort=0 should use max(effort, 1) = 1."""
        t = PrioritizedTask(
            task_id="T-1", title="Test", score=5.0, impact=10.0,
            urgency=3.0, effort=0.0, domain_weight=1.0, reasoning="test",
        )
        assert t.impact_effort_ratio == 10.0

    def test_to_dict(self):
        t = PrioritizedTask(
            task_id="T-1", title="Test", score=5.0, impact=5.0,
            urgency=3.0, effort=2.0, domain_weight=1.0, reasoning="test",
        )
        d = t.to_dict()
        assert d["feature_id"] == "T-1"
        assert d["score"] == 5.0
        assert d["dependencies"] == []

    def test_metadata_non_dict_converted(self):
        """Non-dict metadata should be converted to empty dict."""
        t = PrioritizedTask(
            task_id="T-1", title="Test", score=5.0, impact=5.0,
            urgency=3.0, effort=2.0, domain_weight=1.0, reasoning="test",
            metadata="bad_metadata",
        )
        assert t.metadata == {}


# =============================================================================
# PrioritizationStrategy enum tests
# =============================================================================


class TestPrioritizationStrategy:
    def test_all_strategies(self):
        assert PrioritizationStrategy.IMPACT_FIRST.value == "impact-first"
        assert PrioritizationStrategy.QUICK_WINS.value == "quick-wins"
        assert PrioritizationStrategy.BALANCED.value == "balanced"
        assert PrioritizationStrategy.URGENCY_FIRST.value == "urgency-first"
        assert PrioritizationStrategy.DEPENDENCY_FIRST.value == "dependency-first"


# =============================================================================
# calculate_score tests
# =============================================================================


class TestCalculateScore:
    def test_impact_first(self):
        score = calculate_score(8, 5, 3, 1.0, PrioritizationStrategy.IMPACT_FIRST)
        assert score == (8 * 2 + 5) * 1.0

    def test_quick_wins(self):
        score = calculate_score(8, 5, 2, 1.0, PrioritizationStrategy.QUICK_WINS)
        assert score == ((8 * 5) / 2) * 1.0

    def test_balanced(self):
        score = calculate_score(8, 5, 2, 1.0, PrioritizationStrategy.BALANCED)
        assert score == ((8 * 5) / 2) * 1.0

    def test_urgency_first(self):
        score = calculate_score(8, 5, 2, 1.0, PrioritizationStrategy.URGENCY_FIRST)
        assert score == (5 * 2 + 8) / 2 * 1.0

    def test_dependency_first(self):
        score = calculate_score(8, 5, 2, 1.0, PrioritizationStrategy.DEPENDENCY_FIRST)
        assert score == (8 + 5) / 2 * 1.0

    def test_domain_weight_applied(self):
        base = calculate_score(5, 5, 2, 1.0, PrioritizationStrategy.BALANCED)
        boosted = calculate_score(5, 5, 2, 1.5, PrioritizationStrategy.BALANCED)
        assert boosted == base * 1.5

    def test_zero_effort_quick_wins(self):
        """Quick wins uses max(effort, 1) to avoid div by zero."""
        score = calculate_score(5, 5, 0, 1.0, PrioritizationStrategy.QUICK_WINS)
        assert score == (5 * 5) / 1 * 1.0


# =============================================================================
# estimate_effort_from_criteria tests
# =============================================================================


class TestEstimateEffort:
    def test_no_criteria_returns_3(self):
        assert estimate_effort_from_criteria([]) == 3

    def test_simple_criteria(self):
        result = estimate_effort_from_criteria(["Add a button"])
        assert 1 <= result <= 5

    def test_complex_criteria_higher_effort(self):
        criteria = [
            "Integrate with external API service",
            "Database migration for schema",
            "Performance optimization with cache",
            "Test validation of all endpoints",
        ]
        result = estimate_effort_from_criteria(criteria)
        assert result >= 3

    def test_single_criterion_low_effort(self):
        result = estimate_effort_from_criteria(["Fix typo"])
        assert result <= 3

    def test_many_criteria_higher_effort(self):
        criteria = [f"Criterion {i}" for i in range(10)]
        result = estimate_effort_from_criteria(criteria)
        assert result >= 3


# =============================================================================
# identify_dependencies tests
# =============================================================================


class TestIdentifyDependencies:
    def test_explicit_dependencies(self):
        features = [
            {"id": "F-1", "dependencies": ["F-2"]},
            {"id": "F-2", "dependencies": []},
        ]
        deps = identify_dependencies(features)
        assert "F-2" in deps["F-1"]

    def test_no_dependencies(self):
        features = [{"id": "F-1"}, {"id": "F-2"}]
        deps = identify_dependencies(features)
        assert deps["F-1"] == []

    def test_reference_in_criteria(self):
        features = [
            {"id": "F-1", "acceptance_criteria": ["Depends on F-2 being done"]},
            {"id": "F-2"},
        ]
        deps = identify_dependencies(features)
        assert "F-2" in deps["F-1"]

    def test_reference_in_description(self):
        features = [
            {"id": "F-1", "description": "Needs F-2"},
            {"id": "F-2"},
        ]
        deps = identify_dependencies(features)
        assert "F-2" in deps["F-1"]

    def test_no_self_reference(self):
        features = [{"id": "F-1", "description": "F-1 is self"}]
        deps = identify_dependencies(features)
        assert "F-1" not in deps["F-1"]

    def test_missing_id_skipped(self):
        features = [{"title": "no id"}, {"id": "F-1"}]
        deps = identify_dependencies(features)
        assert "F-1" in deps
        assert len(deps) == 1


# =============================================================================
# identify_blockers tests
# =============================================================================


class TestIdentifyBlockers:
    def test_no_blockers_when_deps_complete(self):
        deps = {"F-1": ["F-2"]}
        status = {"F-2": "complete"}
        assert identify_blockers("F-1", deps, status) == []

    def test_blocker_when_dep_pending(self):
        deps = {"F-1": ["F-2"]}
        status = {"F-2": "pending"}
        assert identify_blockers("F-1", deps, status) == ["F-2"]

    def test_no_deps_no_blockers(self):
        assert identify_blockers("F-1", {}, {}) == []

    def test_multiple_blockers(self):
        deps = {"F-1": ["F-2", "F-3"]}
        status = {"F-2": "pending", "F-3": "in-progress"}
        blockers = identify_blockers("F-1", deps, status)
        assert "F-2" in blockers
        assert "F-3" in blockers

    def test_done_status_not_blocker(self):
        deps = {"F-1": ["F-2"]}
        status = {"F-2": "done"}
        assert identify_blockers("F-1", deps, status) == []


# =============================================================================
# load_features_from_file tests
# =============================================================================


class TestLoadFeatures:
    def test_missing_file(self, tmp_path):
        result = load_features_from_file(tmp_path / "nonexistent.json")
        assert result == []

    def test_dict_with_features_list(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": [{"id": "F-1"}, {"id": "F-2"}]}))
        result = load_features_from_file(f)
        assert len(result) == 2

    def test_dict_with_features_object(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": {"backend": {"title": "Backend"}}}))
        result = load_features_from_file(f)
        assert len(result) == 1
        assert result[0]["id"] == "backend"

    def test_top_level_list(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps([{"id": "F-1"}]))
        result = load_features_from_file(f)
        assert len(result) == 1

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text("not json {{{")
        result = load_features_from_file(f)
        assert result == []

    def test_non_dict_features_skipped(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": [{"id": "F-1"}, "bad", 42]}))
        result = load_features_from_file(f)
        assert len(result) == 1

    def test_unexpected_structure(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text('"just a string"')
        result = load_features_from_file(f)
        assert result == []


# =============================================================================
# find_features_in_portfolio tests
# =============================================================================


class TestFindFeatures:
    def test_finds_features_files(self, tmp_path):
        # Create structure
        (tmp_path / "project-a").mkdir()
        (tmp_path / "project-a" / "features.json").write_text("{}")
        (tmp_path / "project-b").mkdir()
        (tmp_path / "project-b" / "features.json").write_text("{}")
        files = find_features_in_portfolio(tmp_path)
        assert len(files) == 2

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "features.json").write_text("{}")
        files = find_features_in_portfolio(tmp_path)
        assert len(files) == 0

    def test_skips_node_modules(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "features.json").write_text("{}")
        files = find_features_in_portfolio(tmp_path)
        assert len(files) == 0


# =============================================================================
# prioritize_tasks tests
# =============================================================================


class TestPrioritizeTasks:
    def test_empty_features(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": []}))
        tasks = prioritize_tasks(features_paths=[f])
        assert tasks == []

    def test_basic_prioritization(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": [
            {"id": "F-1", "title": "High impact", "priority": "high"},
            {"id": "F-2", "title": "Low impact", "priority": "low"},
        ]}))
        tasks = prioritize_tasks(features_paths=[f])
        assert len(tasks) == 2
        # High priority should score higher
        assert tasks[0].task_id == "F-1"

    def test_completed_tasks_excluded(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": [
            {"id": "F-1", "title": "Done", "status": "complete"},
            {"id": "F-2", "title": "Pending", "priority": "high"},
        ]}))
        tasks = prioritize_tasks(features_paths=[f])
        assert len(tasks) == 1
        assert tasks[0].task_id == "F-2"

    def test_dependency_first_boosts_unblocked(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": [
            {"id": "F-1", "title": "Blocked", "priority": "high", "dependencies": ["F-3"]},
            {"id": "F-2", "title": "Unblocked", "priority": "medium"},
            {"id": "F-3", "title": "Dep", "priority": "low"},
        ]}))
        tasks = prioritize_tasks(
            features_paths=[f],
            strategy=PrioritizationStrategy.DEPENDENCY_FIRST,
        )
        # F-2 and F-3 are unblocked, should get 2x boost
        unblocked_ids = [t.task_id for t in tasks if not t.is_blocked]
        assert "F-2" in unblocked_ids

    def test_string_path_uses_simple_prioritizer(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": [
            {"id": "F-1", "title": "Task", "priority": "high"},
        ]}))
        tasks = prioritize_tasks(features_paths=str(f))
        assert len(tasks) == 1

    def test_custom_domain_weights(self, tmp_path):
        f = tmp_path / "features.json"
        f.write_text(json.dumps({"features": [
            {"id": "F-1", "title": "Task", "priority": "medium"},
        ]}))
        tasks = prioritize_tasks(
            features_paths=[f],
            domain_weights={"custom-domain": 2.0},
        )
        assert len(tasks) == 1


# =============================================================================
# boost_for_failures tests
# =============================================================================


class TestBoostForFailures:
    def _make_task(self, title="test task", score=10.0):
        return PrioritizedTask(
            task_id="T-1", title=title, score=score, impact=5.0,
            urgency=3.0, effort=2.0, domain_weight=1.0, reasoning="base",
        )

    def test_test_task_boosted_on_failures(self):
        task = self._make_task(title="Add test coverage")
        assessment = AssessmentReport()
        assessment.test_results = TestResults(
            total_tests=10, passed_count=8, failed_count=2,
        )
        boost_for_failures([task], assessment)
        assert task.score == 15.0  # 10 * 1.5
        assert task.metadata.get("failure_boost") is True

    def test_build_task_boosted_on_build_errors(self):
        task = self._make_task(title="Fix build errors")
        assessment = AssessmentReport()
        assessment.issues = [
            Issue(issue_type=IssueType.BUILD_ERROR, severity="high", message="fail"),
        ]
        boost_for_failures([task], assessment)
        assert task.score == 18.0  # 10 * 1.8

    def test_git_task_boosted_on_conflicts(self):
        task = self._make_task(title="Resolve merge conflict")
        assessment = AssessmentReport()
        assessment.git_status = GitStatus(total_conflicts=3)
        boost_for_failures([task], assessment)
        assert task.score == 17.0  # 10 * 1.7

    def test_no_boost_when_no_issues(self):
        task = self._make_task(title="Add test coverage")
        assessment = AssessmentReport()
        boost_for_failures([task], assessment)
        assert task.score == 10.0
        assert "failure_boost" not in task.metadata

    def test_unrelated_task_not_boosted(self):
        task = self._make_task(title="Update readme")
        assessment = AssessmentReport()
        assessment.test_results = TestResults(
            total_tests=10, passed_count=8, failed_count=2,
        )
        boost_for_failures([task], assessment)
        assert task.score == 10.0


# =============================================================================
# _estimate_impact / _estimate_urgency tests
# =============================================================================


class TestEstimateImpactUrgency:
    def test_critical_impact(self):
        assert _estimate_impact({}, "critical", None) == 9

    def test_low_impact(self):
        assert _estimate_impact({}, "low", None) == 3

    def test_epic_boost(self):
        assert _estimate_impact({}, "medium", "Quality Gates") == 7  # 5 + 2

    def test_impact_capped_at_10(self):
        assert _estimate_impact({}, "critical", "Quality Gates") == 10  # 9 + 2 capped

    def test_critical_urgency(self):
        assert _estimate_urgency({}, "critical") == 10

    def test_low_urgency(self):
        assert _estimate_urgency({}, "low") == 2

    def test_urgency_boost_for_keywords(self):
        feature = {"title": "Fix urgent blocker"}
        result = _estimate_urgency(feature, "medium")
        assert result >= 7  # 4 + 3

    def test_urgency_bug_keywords(self):
        feature = {"title": "Fix bug in auth"}
        result = _estimate_urgency(feature, "medium")
        assert result >= 6  # 4 + 2

    def test_urgency_capped_at_10(self):
        feature = {"title": "Fix urgent critical blocker"}
        result = _estimate_urgency(feature, "critical")
        assert result == 10


# =============================================================================
# Demo function tests
# =============================================================================


class TestDemoPrioritizer:
    """Tests for demo_prioritizer functions that can run without features.json."""

    def test_demo_basic_prioritization(self, capsys):
        """Demo runs without error (may or may not find features.json)."""
        from forge_harness.iteration.demo_prioritizer import demo_basic_prioritization
        demo_basic_prioritization()
        captured = capsys.readouterr()
        assert "DEMO: Basic Prioritization" in captured.out

    def test_demo_quick_wins(self, capsys):
        from forge_harness.iteration.demo_prioritizer import demo_quick_wins
        demo_quick_wins()
        captured = capsys.readouterr()
        assert "DEMO: Quick Wins Strategy" in captured.out

    def test_demo_dependency_first(self, capsys):
        from forge_harness.iteration.demo_prioritizer import demo_dependency_first
        demo_dependency_first()
        captured = capsys.readouterr()
        assert "DEMO: Dependency-First Strategy" in captured.out
