"""Tests for forge_harness.iteration.prioritizer module."""

from __future__ import annotations

import json

import pytest

from forge_harness.iteration.assess import AssessmentReport, Issue, IssueType
from forge_harness.iteration.prioritizer import (
    PrioritizationStrategy,
    PrioritizedTask,
    boost_for_failures,
    calculate_score,
    estimate_effort_from_criteria,
    find_features_in_portfolio,
    identify_blockers,
    identify_dependencies,
    load_features_from_file,
    prioritize_tasks,
)


class TestPrioritizedTask:
    """Test PrioritizedTask dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        task = PrioritizedTask(
            feature_id="HRN-001",
            title="Test task",
            score=10.5,
            impact=8,
            urgency=7,
            effort=3,
            domain_weight=1.2,
            reasoning="Test reasoning",
            dependencies=["HRN-002"],
            blockers=["HRN-003"],
            status="pending",
            epic="Test Epic",
            acceptance_criteria=["Criterion 1"],
            test_command="pytest",
            metadata={"key": "value"},
        )

        data = task.to_dict()
        assert data["feature_id"] == "HRN-001"
        assert data["title"] == "Test task"
        assert data["score"] == 10.5
        assert data["impact"] == 8
        assert data["urgency"] == 7
        assert data["effort"] == 3
        assert data["domain_weight"] == 1.2
        assert data["reasoning"] == "Test reasoning"
        assert data["dependencies"] == ["HRN-002"]
        assert data["blockers"] == ["HRN-003"]

    def test_is_blocked(self):
        """Test is_blocked property."""
        unblocked = PrioritizedTask(
            feature_id="HRN-001",
            title="Test",
            score=10.0,
            impact=5,
            urgency=5,
            effort=3,
            domain_weight=1.0,
            reasoning="",
        )
        assert unblocked.is_blocked is False

        blocked = PrioritizedTask(
            feature_id="HRN-001",
            title="Test",
            score=10.0,
            impact=5,
            urgency=5,
            effort=3,
            domain_weight=1.0,
            reasoning="",
            blockers=["HRN-002"],
        )
        assert blocked.is_blocked is True

    def test_impact_effort_ratio(self):
        """Test impact/effort ratio calculation."""
        task = PrioritizedTask(
            feature_id="HRN-001",
            title="Test",
            score=10.0,
            impact=8,
            urgency=5,
            effort=2,
            domain_weight=1.0,
            reasoning="",
        )
        assert task.impact_effort_ratio == 4.0  # 8 / 2


class TestLoadFeatures:
    """Test feature loading functions."""

    def test_load_features_from_file(self, tmp_path):
        """Test loading features from valid JSON file."""
        features_file = tmp_path / "features.json"
        features_data = {
            "version": "2.0",
            "features": [
                {"id": "TEST-001", "title": "Feature 1", "status": "pending"},
                {"id": "TEST-002", "title": "Feature 2", "status": "complete"},
            ],
        }
        features_file.write_text(json.dumps(features_data))

        features = load_features_from_file(features_file)
        assert len(features) == 2
        assert features[0]["id"] == "TEST-001"
        assert features[1]["id"] == "TEST-002"

    def test_load_features_from_list_format(self, tmp_path):
        """Test loading features from list-format JSON."""
        features_file = tmp_path / "features.json"
        features_data = [
            {"id": "TEST-001", "title": "Feature 1"},
            {"id": "TEST-002", "title": "Feature 2"},
        ]
        features_file.write_text(json.dumps(features_data))

        features = load_features_from_file(features_file)
        assert len(features) == 2

    def test_load_features_nonexistent_file(self, tmp_path):
        """Test loading from nonexistent file returns empty list."""
        features = load_features_from_file(tmp_path / "nonexistent.json")
        assert features == []

    def test_load_features_invalid_json(self, tmp_path):
        """Test loading invalid JSON returns empty list."""
        features_file = tmp_path / "features.json"
        features_file.write_text("{ invalid json }")

        features = load_features_from_file(features_file)
        assert features == []


class TestFindFeaturesInPortfolio:
    """Test portfolio feature discovery."""

    def test_find_features_in_portfolio(self, tmp_path):
        """Test finding features.json files in directory tree."""
        # Create directory structure
        (tmp_path / "domain1" / "project1").mkdir(parents=True)
        (tmp_path / "domain2" / "project2").mkdir(parents=True)
        (tmp_path / "harness").mkdir()

        # Create features files
        (tmp_path / "domain1" / "project1" / "features.json").write_text("{}")
        (tmp_path / "domain2" / "project2" / "features.json").write_text("{}")
        (tmp_path / "harness" / "features.json").write_text("{}")

        # Should skip hidden directories
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "features.json").write_text("{}")

        features_files = find_features_in_portfolio(tmp_path)
        assert len(features_files) == 3

        # Verify .git was skipped
        assert not any(".git" in str(f) for f in features_files)


class TestEffortEstimation:
    """Test effort estimation from acceptance criteria."""

    def test_estimate_effort_trivial(self):
        """Test trivial effort estimation."""
        criteria = ["Simple change"]
        effort = estimate_effort_from_criteria(criteria)
        assert effort == 1

    def test_estimate_effort_simple(self):
        """Test simple effort estimation."""
        criteria = ["Add feature A", "Update feature B"]
        effort = estimate_effort_from_criteria(criteria)
        # 2 criteria = base_effort 1.0, no complexity = 1
        assert effort == 1

    def test_estimate_effort_medium(self):
        """Test medium effort estimation."""
        criteria = [
            "Implement new API endpoint",
            "Add validation",
            "Write tests",
        ]
        effort = estimate_effort_from_criteria(criteria)
        assert effort == 3

    def test_estimate_effort_complex(self):
        """Test complex effort estimation."""
        criteria = [
            "Integrate external API service",
            "Create database migration",
            "Add comprehensive test coverage",
            "Optimize performance",
        ]
        effort = estimate_effort_from_criteria(criteria)
        assert effort >= 4

    def test_estimate_effort_very_complex(self):
        """Test very complex effort estimation."""
        criteria = [
            "Integrate multiple external services with webhooks",
            "Design and implement complex database schema",
            "Add comprehensive integration test suite",
            "Optimize for high-performance caching",
            "Implement real-time updates",
            "Add detailed monitoring and logging",
        ]
        effort = estimate_effort_from_criteria(criteria)
        assert effort == 5

    def test_estimate_effort_empty_criteria(self):
        """Test default effort for empty criteria."""
        effort = estimate_effort_from_criteria([])
        assert effort == 3  # Default medium


class TestDependencyAnalysis:
    """Test dependency identification."""

    def test_identify_dependencies_explicit(self):
        """Test identifying explicit dependencies."""
        features = [
            {
                "id": "HRN-001",
                "dependencies": ["HRN-002", "HRN-003"],
            },
            {"id": "HRN-002"},
            {"id": "HRN-003"},
        ]

        deps = identify_dependencies(features)
        # Check sets since order isn't guaranteed
        assert set(deps["HRN-001"]) == {"HRN-002", "HRN-003"}
        assert deps["HRN-002"] == []
        assert deps["HRN-003"] == []

    def test_identify_dependencies_from_criteria(self):
        """Test identifying dependencies from acceptance criteria."""
        features = [
            {
                "id": "HRN-001",
                "acceptance_criteria": [
                    "Uses HRN-002 for data loading",
                    "Depends on HRN-003 completion",
                ],
            },
            {"id": "HRN-002"},
            {"id": "HRN-003"},
        ]

        deps = identify_dependencies(features)
        assert "HRN-002" in deps["HRN-001"]
        assert "HRN-003" in deps["HRN-001"]

    def test_identify_dependencies_from_description(self):
        """Test identifying dependencies from description."""
        features = [
            {
                "id": "HRN-001",
                "description": "Builds on HRN-002 and requires HRN-003",
            },
            {"id": "HRN-002"},
            {"id": "HRN-003"},
        ]

        deps = identify_dependencies(features)
        assert "HRN-002" in deps["HRN-001"]
        assert "HRN-003" in deps["HRN-001"]

    def test_identify_blockers(self):
        """Test identifying blockers from dependencies."""
        dependencies = {
            "HRN-001": ["HRN-002", "HRN-003"],
            "HRN-002": [],
        }
        feature_status = {
            "HRN-001": "pending",
            "HRN-002": "complete",
            "HRN-003": "pending",
        }

        blockers = identify_blockers("HRN-001", dependencies, feature_status)
        assert "HRN-003" in blockers
        assert "HRN-002" not in blockers  # Complete, not a blocker


class TestScoringStrategies:
    """Test different prioritization strategies."""

    def test_calculate_score_impact_first(self):
        """Test impact-first strategy prioritizes high impact."""
        score_high_impact = calculate_score(
            impact=10,
            urgency=5,
            effort=5,
            domain_weight=1.0,
            strategy=PrioritizationStrategy.IMPACT_FIRST,
        )

        score_low_impact = calculate_score(
            impact=3,
            urgency=5,
            effort=1,
            domain_weight=1.0,
            strategy=PrioritizationStrategy.IMPACT_FIRST,
        )

        assert score_high_impact > score_low_impact

    def test_calculate_score_quick_wins(self):
        """Test quick-wins strategy prioritizes high impact/low effort."""
        score_quick_win = calculate_score(
            impact=8,
            urgency=8,
            effort=1,
            domain_weight=1.0,
            strategy=PrioritizationStrategy.QUICK_WINS,
        )

        score_complex = calculate_score(
            impact=8,
            urgency=8,
            effort=5,
            domain_weight=1.0,
            strategy=PrioritizationStrategy.QUICK_WINS,
        )

        assert score_quick_win > score_complex

    def test_calculate_score_balanced(self):
        """Test balanced strategy considers all factors."""
        score = calculate_score(
            impact=8,
            urgency=6,
            effort=3,
            domain_weight=1.5,
            strategy=PrioritizationStrategy.BALANCED,
        )

        # Should be (8 * 6) / 3 * 1.5 = 24
        assert score == 24.0

    def test_calculate_score_urgency_first(self):
        """Test urgency-first strategy prioritizes urgent tasks."""
        score_urgent = calculate_score(
            impact=5,
            urgency=10,
            effort=3,
            domain_weight=1.0,
            strategy=PrioritizationStrategy.URGENCY_FIRST,
        )

        score_not_urgent = calculate_score(
            impact=5,
            urgency=2,
            effort=3,
            domain_weight=1.0,
            strategy=PrioritizationStrategy.URGENCY_FIRST,
        )

        assert score_urgent > score_not_urgent

    def test_calculate_score_domain_weight(self):
        """Test domain weight multiplier."""
        score_normal = calculate_score(
            impact=5,
            urgency=5,
            effort=2,
            domain_weight=1.0,
            strategy=PrioritizationStrategy.BALANCED,
        )

        score_weighted = calculate_score(
            impact=5,
            urgency=5,
            effort=2,
            domain_weight=1.5,
            strategy=PrioritizationStrategy.BALANCED,
        )

        assert score_weighted == score_normal * 1.5


class TestFailureBoosting:
    """Test boosting priority based on assessment failures."""

    def test_boost_for_failing_tests(self):
        """Test boosting tasks related to failing tests."""
        tasks = [
            PrioritizedTask(
                feature_id="HRN-001",
                title="Add test coverage",
                score=10.0,
                impact=5,
                urgency=5,
                effort=3,
                domain_weight=1.0,
                reasoning="",
            ),
            PrioritizedTask(
                feature_id="HRN-002",
                title="Add new feature",
                score=10.0,
                impact=5,
                urgency=5,
                effort=3,
                domain_weight=1.0,
                reasoning="",
            ),
        ]

        # Create assessment with failed tests
        assessment = AssessmentReport()
        assessment.test_results.failed_count = 5

        boost_for_failures(tasks, assessment)

        # Test-related task should be boosted
        assert tasks[0].score == 15.0  # 10.0 * 1.5
        assert "[BOOST: Failing tests detected]" in tasks[0].reasoning
        assert tasks[0].metadata.get("failure_boost") is True

        # Non-test task should not be boosted
        assert tasks[1].score == 10.0

    def test_boost_for_build_errors(self):
        """Test boosting tasks related to build errors."""
        tasks = [
            PrioritizedTask(
                feature_id="HRN-001",
                title="Fix lint errors",
                score=10.0,
                impact=5,
                urgency=5,
                effort=3,
                domain_weight=1.0,
                reasoning="",
            ),
        ]

        assessment = AssessmentReport()
        assessment.issues.append(
            Issue(
                issue_type=IssueType.BUILD_ERROR,
                severity="high",
                message="Build failed",
            )
        )

        boost_for_failures(tasks, assessment)

        assert tasks[0].score == 18.0  # 10.0 * 1.8
        assert "[BOOST: Build errors detected]" in tasks[0].reasoning


class TestPrioritizeTasks:
    """Test main prioritization function."""

    def test_weighted_scoring(self, tmp_path):
        """Test weighted scoring with domain and failure boosts."""
        # Create features file
        features_file = tmp_path / "voice-coach" / "features.json"
        features_file.parent.mkdir(parents=True)

        features_data = {
            "features": [
                {
                    "id": "VC-001",
                    "title": "Fix failing tests",
                    "status": "pending",
                    "priority": "high",
                    "epic": "Quality Gates",
                    "acceptance_criteria": [
                        "Fix test A",
                        "Fix test B",
                    ],
                },
                {
                    "id": "VC-002",
                    "title": "Add new feature",
                    "status": "pending",
                    "priority": "medium",
                    "acceptance_criteria": [
                        "Implement feature",
                        "Add tests",
                        "Update docs",
                    ],
                },
                {
                    "id": "VC-003",
                    "title": "Complete feature",
                    "status": "complete",
                    "priority": "high",
                },
            ],
        }
        features_file.write_text(json.dumps(features_data))

        # Create assessment with failures
        assessment = AssessmentReport()
        assessment.test_results.failed_count = 3

        # Prioritize with voice-coach weight
        tasks = prioritize_tasks(
            features_paths=[features_file],
            assessment_report=assessment,
            strategy=PrioritizationStrategy.BALANCED,
            domain_weights={"voice-coach": 1.5},
        )

        # Should have 2 tasks (excluding completed)
        assert len(tasks) == 2

        # First task should be VC-001 (test-related with failure boost)
        assert tasks[0].feature_id == "VC-001"
        assert tasks[0].domain_weight == 1.5
        assert "[BOOST: Failing tests detected]" in tasks[0].reasoning

        # Scores should be in descending order
        assert tasks[0].score >= tasks[1].score

    def test_prioritize_quick_wins(self, tmp_path):
        """Test quick-wins strategy prioritizes high value/low effort."""
        features_file = tmp_path / "features.json"
        features_data = {
            "features": [
                {
                    "id": "QW-001",
                    "title": "Quick win",
                    "priority": "high",
                    "acceptance_criteria": ["Simple change"],
                },
                {
                    "id": "QW-002",
                    "title": "Complex task",
                    "priority": "high",
                    "acceptance_criteria": [
                        "Integrate external API",
                        "Database migration",
                        "Complex testing",
                        "Performance optimization",
                        "Documentation",
                    ],
                },
            ],
        }
        features_file.write_text(json.dumps(features_data))

        tasks = prioritize_tasks(
            features_paths=[features_file],
            strategy=PrioritizationStrategy.QUICK_WINS,
        )

        # Quick win should be first
        assert tasks[0].feature_id == "QW-001"
        assert tasks[0].effort < tasks[1].effort

    def test_prioritize_dependency_first(self, tmp_path):
        """Test dependency-first strategy boosts unblocked tasks."""
        features_file = tmp_path / "features.json"
        features_data = {
            "features": [
                {
                    "id": "DEP-001",
                    "title": "Foundation task",
                    "priority": "medium",
                    "acceptance_criteria": ["Build foundation"],
                },
                {
                    "id": "DEP-002",
                    "title": "Dependent task",
                    "priority": "high",
                    "dependencies": ["DEP-001"],
                    "acceptance_criteria": ["Build on DEP-001"],
                },
            ],
        }
        features_file.write_text(json.dumps(features_data))

        tasks = prioritize_tasks(
            features_paths=[features_file],
            strategy=PrioritizationStrategy.DEPENDENCY_FIRST,
        )

        # Foundation task should be first (no blockers)
        assert tasks[0].feature_id == "DEP-001"
        assert len(tasks[0].blockers) == 0
        assert "[BOOST: No blockers]" in tasks[0].reasoning

        # Dependent task should be second (has blocker)
        assert tasks[1].feature_id == "DEP-002"
        assert "DEP-001" in tasks[1].blockers

    def test_prioritize_no_features(self, tmp_path):
        """Test prioritization with no features returns empty list."""
        tasks = prioritize_tasks(
            features_paths=[tmp_path / "nonexistent.json"],
        )
        assert tasks == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
