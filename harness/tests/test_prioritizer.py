"""
Tests for forge_harness/iteration/prioritizer.py - Smart Task Prioritization
"""

import json

import pytest

from forge_harness.iteration.prioritizer import (
    PrioritizedTask,
    prioritize_tasks,
)


class TestWeightedScoring:
    """Tests for weighted scoring formula."""

    def test_basic_scoring_formula(self, tmp_path):
        """Verify scoring formula with known inputs."""
        # Create test features file
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Test Feature",
                    "priority": "high",
                    "domain": "test-domain",
                    "dependencies": ["dep-1", "dep-2"],
                    "estimated_tokens": 2500,
                    "has_failing_tests": False,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        # Prioritize tasks
        tasks = prioritize_tasks(str(features_path))

        assert len(tasks) == 1
        task = tasks[0]

        # Verify components
        # Impact = (2 deps × 2) + high_priority_weight(3) = 4 + 3 = 7
        assert task.impact == 7.0

        # Urgency = 1.0 (baseline, no failing tests, not critical)
        assert task.urgency == 1.0

        # Effort = 2500 / 1000 = 2.5
        assert task.effort == 2.5

        # Score = (7 + 1 - 2.5) × 1.0 = 5.5
        assert task.score == 5.5

    def test_failing_tests_boost(self, tmp_path):
        """Verify failing tests adds urgency boost."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Test Feature",
                    "priority": "medium",
                    "domain": "test-domain",
                    "dependencies": [],
                    "estimated_tokens": 1000,
                    "has_failing_tests": True,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        task = tasks[0]

        # Urgency = 1.0 (baseline) + 3.0 (failing tests) = 4.0
        assert task.urgency == 4.0

    def test_critical_priority_boost(self, tmp_path):
        """Verify critical priority adds urgency boost."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Test Feature",
                    "priority": "critical",
                    "domain": "test-domain",
                    "dependencies": [],
                    "estimated_tokens": 1000,
                    "has_failing_tests": False,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        task = tasks[0]

        # Urgency = 1.0 (baseline) + 2.0 (critical) = 3.0
        assert task.urgency == 3.0

    def test_combined_urgency_boosts(self, tmp_path):
        """Verify failing tests and critical priority combine."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Test Feature",
                    "priority": "critical",
                    "domain": "test-domain",
                    "dependencies": [],
                    "estimated_tokens": 1000,
                    "has_failing_tests": True,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        task = tasks[0]

        # Urgency = 1.0 (baseline) + 3.0 (failing tests) + 2.0 (critical) = 6.0
        assert task.urgency == 6.0


class TestDomainBoost:
    """Tests for domain boost multiplier."""

    def test_voice_coach_domain_boost(self, tmp_path):
        """Verify voice-coach domain gets 1.5x boost."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Voice Coach Feature",
                    "priority": "medium",
                    "domain": "voice-coach",
                    "dependencies": [],
                    "estimated_tokens": 1000,
                    "has_failing_tests": False,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        task = tasks[0]

        # Base score = (impact + urgency - effort) = (2 + 1 - 1) = 2
        # Final score = 2 × 1.5 = 3.0
        assert task.score == 3.0

    def test_command_center_domain_boost(self, tmp_path):
        """Verify command_center domain gets 1.5x boost."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Command Center Feature",
                    "priority": "medium",
                    "domain": "command_center",
                    "dependencies": [],
                    "estimated_tokens": 1000,
                    "has_failing_tests": False,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        task = tasks[0]

        # Base score = (impact + urgency - effort) = (2 + 1 - 1) = 2
        # Final score = 2 × 1.5 = 3.0
        assert task.score == 3.0

    def test_regular_domain_no_boost(self, tmp_path):
        """Verify regular domains get no boost."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Regular Feature",
                    "priority": "medium",
                    "domain": "other-domain",
                    "dependencies": [],
                    "estimated_tokens": 1000,
                    "has_failing_tests": False,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        task = tasks[0]

        # Base score = (impact + urgency - effort) = (2 + 1 - 1) = 2
        # Final score = 2 × 1.0 = 2.0
        assert task.score == 2.0


class TestRankingOrder:
    """Tests for task ranking order."""

    def test_tasks_sorted_by_score_descending(self, tmp_path):
        """Verify tasks are sorted by score (highest first)."""
        features = {
            "features": [
                {
                    "id": "LOW",
                    "title": "Low Priority",
                    "priority": "low",
                    "domain": "test",
                    "dependencies": [],
                    "estimated_tokens": 3000,
                    "has_failing_tests": False,
                },
                {
                    "id": "HIGH",
                    "title": "High Priority",
                    "priority": "high",
                    "domain": "voice-coach",
                    "dependencies": ["dep-1", "dep-2"],
                    "estimated_tokens": 1000,
                    "has_failing_tests": True,
                },
                {
                    "id": "MEDIUM",
                    "title": "Medium Priority",
                    "priority": "medium",
                    "domain": "test",
                    "dependencies": ["dep-1"],
                    "estimated_tokens": 2000,
                    "has_failing_tests": False,
                },
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))

        # Verify we got all tasks
        assert len(tasks) == 3

        # Verify sorting (scores should be descending)
        scores = [task.score for task in tasks]
        assert scores == sorted(scores, reverse=True)

        # HIGH should be first (high priority, domain boost, failing tests, low effort)
        assert tasks[0].task_id == "HIGH"

    def test_empty_features_list(self, tmp_path):
        """Verify empty features list returns empty result."""
        features = {"features": []}

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        assert tasks == []

    def test_missing_features_key(self, tmp_path):
        """Verify missing features key returns empty result."""
        features = {"version": "1.0"}

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        assert tasks == []


class TestReasoningString:
    """Tests for reasoning string generation."""

    def test_reasoning_includes_all_components(self, tmp_path):
        """Verify reasoning string includes all score components."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Test Feature",
                    "priority": "high",
                    "domain": "voice-coach",
                    "dependencies": ["dep-1"],
                    "estimated_tokens": 2500,
                    "has_failing_tests": True,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        task = tasks[0]

        reasoning = task.reasoning

        # Should include score
        assert "Score" in reasoning

        # Should include impact with deps and priority
        assert "impact=" in reasoning
        assert "1 deps" in reasoning
        assert "high priority" in reasoning

        # Should include urgency with failing tests
        assert "urgency=" in reasoning
        assert "failing tests" in reasoning

        # Should include effort with tokens
        assert "effort=" in reasoning
        assert "2500 tokens" in reasoning

        # Should include domain boost
        assert "domain_boost=1.5x" in reasoning
        assert "voice-coach" in reasoning

    def test_reasoning_without_boosts(self, tmp_path):
        """Verify reasoning for task without boosts."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Test Feature",
                    "priority": "medium",
                    "domain": "other",
                    "dependencies": [],
                    "estimated_tokens": 1000,
                    "has_failing_tests": False,
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))
        task = tasks[0]

        reasoning = task.reasoning

        # Should show baseline urgency
        assert "baseline" in reasoning

        # Should NOT include domain boost
        assert "domain_boost" not in reasoning


class TestPrioritizedTaskDataclass:
    """Tests for PrioritizedTask dataclass."""

    def test_task_attributes(self):
        """Verify PrioritizedTask has all required attributes."""
        task = PrioritizedTask(
            task_id="TEST-001",
            name="Test Task",
            score=10.5,
            impact=5.0,
            urgency=3.0,
            effort=2.5,
            reasoning="Test reasoning",
        )

        assert task.task_id == "TEST-001"
        assert task.name == "Test Task"
        assert task.score == 10.5
        assert task.impact == 5.0
        assert task.urgency == 3.0
        assert task.effort == 2.5
        assert task.reasoning == "Test reasoning"

    def test_task_repr(self):
        """Verify PrioritizedTask repr is readable."""
        task = PrioritizedTask(
            task_id="TEST-001",
            name="Test Task",
            score=10.5,
            impact=5.0,
            urgency=3.0,
            effort=2.5,
            reasoning="Test reasoning",
        )

        repr_str = repr(task)

        # Should include key info
        assert "TEST-001" in repr_str
        assert "Test Task" in repr_str
        assert "10.5" in repr_str


class TestErrorHandling:
    """Tests for error handling."""

    def test_missing_file_raises_error(self):
        """Verify missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            prioritize_tasks("/nonexistent/path/features.json")

    def test_invalid_json_raises_error(self, tmp_path):
        """Verify invalid JSON raises JSONDecodeError."""
        features_path = tmp_path / "features.json"
        features_path.write_text("{invalid json")

        with pytest.raises(json.JSONDecodeError):
            prioritize_tasks(str(features_path))

    def test_missing_optional_fields_uses_defaults(self, tmp_path):
        """Verify missing optional fields use sensible defaults."""
        features = {
            "features": [
                {
                    "id": "TEST-001",
                    "title": "Minimal Feature",
                    # Missing: priority, domain, dependencies, estimated_tokens, has_failing_tests
                }
            ]
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))

        assert len(tasks) == 1
        task = tasks[0]

        # Should have defaults applied
        assert task.impact == 2.0  # medium priority weight
        assert task.urgency == 1.0  # baseline
        assert task.effort == 1.0  # 1000 tokens default


class TestIntegrationWithRealData:
    """Integration tests with realistic data."""

    def test_realistic_feature_set(self, tmp_path):
        """Test with realistic feature set."""
        features = {
            "version": "2.0",
            "features": [
                {
                    "id": "HRN-001",
                    "title": "Critical Production Bug",
                    "priority": "critical",
                    "domain": "voice-coach",
                    "dependencies": ["HRN-002", "HRN-003"],
                    "estimated_tokens": 1500,
                    "has_failing_tests": True,
                },
                {
                    "id": "HRN-002",
                    "title": "Nice to Have Feature",
                    "priority": "low",
                    "domain": "other-domain",
                    "dependencies": [],
                    "estimated_tokens": 5000,
                    "has_failing_tests": False,
                },
                {
                    "id": "HRN-003",
                    "title": "Important Refactor",
                    "priority": "high",
                    "domain": "command_center",
                    "dependencies": ["HRN-004"],
                    "estimated_tokens": 3000,
                    "has_failing_tests": False,
                },
            ],
        }

        features_path = tmp_path / "features.json"
        features_path.write_text(json.dumps(features))

        tasks = prioritize_tasks(str(features_path))

        # Critical bug should be first
        assert tasks[0].task_id == "HRN-001"

        # Low priority with high effort should be last
        assert tasks[-1].task_id == "HRN-002"

        # All tasks should have reasoning
        for task in tasks:
            assert task.reasoning
            assert len(task.reasoning) > 0
