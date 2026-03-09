"""Tests for models/task_models.py - Task and Agent Data Models.

Tests cover:
- TaskType enum
- ComplexityLevel enum
- Skill enum
- TaskTypeHistory dataclass
- AgentProfile dataclass
- CodebaseContext dataclass
- TaskAnalysis dataclass
- RecommendationFeedback dataclass
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from forge_harness.models.task_models import (
    AgentProfile,
    CodebaseContext,
    ComplexityLevel,
    RecommendationFeedback,
    Skill,
    TaskAnalysis,
    TaskType,
    TaskTypeHistory,
)

# =============================================================================
# TaskType Enum Tests
# =============================================================================


class TestTaskType:
    """Tests for TaskType enum."""

    def test_api_simple_value(self) -> None:
        """Should have correct API_SIMPLE value."""
        assert TaskType.API_SIMPLE.value == "api:simple"

    def test_api_stateful_value(self) -> None:
        """Should have correct API_STATEFUL value."""
        assert TaskType.API_STATEFUL.value == "api:stateful"

    def test_frontend_component_value(self) -> None:
        """Should have correct FRONTEND_COMPONENT value."""
        assert TaskType.FRONTEND_COMPONENT.value == "frontend:component"

    def test_test_unit_value(self) -> None:
        """Should have correct TEST_UNIT value."""
        assert TaskType.TEST_UNIT.value == "test:unit"

    def test_infra_deploy_value(self) -> None:
        """Should have correct INFRA_DEPLOY value."""
        assert TaskType.INFRA_DEPLOY.value == "infra:deploy"

    def test_docs_api_value(self) -> None:
        """Should have correct DOCS_API value."""
        assert TaskType.DOCS_API.value == "docs:api"

    def test_research_technical_value(self) -> None:
        """Should have correct RESEARCH_TECHNICAL value."""
        assert TaskType.RESEARCH_TECHNICAL.value == "research:technical"

    def test_refactor_cleanup_value(self) -> None:
        """Should have correct REFACTOR_CLEANUP value."""
        assert TaskType.REFACTOR_CLEANUP.value == "refactor:cleanup"

    def test_general_value(self) -> None:
        """Should have correct GENERAL value."""
        assert TaskType.GENERAL.value == "general"

    def test_from_string(self) -> None:
        """Should create from string value."""
        task_type = TaskType("api:simple")
        assert task_type == TaskType.API_SIMPLE


# =============================================================================
# ComplexityLevel Enum Tests
# =============================================================================


class TestComplexityLevel:
    """Tests for ComplexityLevel enum."""

    def test_trivial_value(self) -> None:
        """Should have correct TRIVIAL value."""
        assert ComplexityLevel.TRIVIAL.value == 1

    def test_simple_value(self) -> None:
        """Should have correct SIMPLE value."""
        assert ComplexityLevel.SIMPLE.value == 2

    def test_moderate_value(self) -> None:
        """Should have correct MODERATE value."""
        assert ComplexityLevel.MODERATE.value == 3

    def test_complex_value(self) -> None:
        """Should have correct COMPLEX value."""
        assert ComplexityLevel.COMPLEX.value == 4

    def test_expert_value(self) -> None:
        """Should have correct EXPERT value."""
        assert ComplexityLevel.EXPERT.value == 5


# =============================================================================
# Skill Enum Tests
# =============================================================================


class TestSkill:
    """Tests for Skill enum."""

    def test_python_value(self) -> None:
        """Should have correct PYTHON value."""
        assert Skill.PYTHON.value == "PYTHON"

    def test_typescript_value(self) -> None:
        """Should have correct TYPESCRIPT value."""
        assert Skill.TYPESCRIPT.value == "TYPESCRIPT"

    def test_fastapi_value(self) -> None:
        """Should have correct FASTAPI value."""
        assert Skill.FASTAPI.value == "FASTAPI"

    def test_react_value(self) -> None:
        """Should have correct REACT value."""
        assert Skill.REACT.value == "REACT"

    def test_postgresql_value(self) -> None:
        """Should have correct POSTGRESQL value."""
        assert Skill.POSTGRESQL.value == "POSTGRESQL"

    def test_docker_value(self) -> None:
        """Should have correct DOCKER value."""
        assert Skill.DOCKER.value == "DOCKER"

    def test_from_string(self) -> None:
        """Should create from string value."""
        skill = Skill("PYTHON")
        assert skill == Skill.PYTHON


# =============================================================================
# TaskTypeHistory Tests
# =============================================================================


class TestTaskTypeHistory:
    """Tests for TaskTypeHistory dataclass."""

    def test_creation_defaults(self) -> None:
        """Should create with defaults."""
        history = TaskTypeHistory(task_type=TaskType.API_SIMPLE)

        assert history.task_type == TaskType.API_SIMPLE
        assert history.tasks_completed == 0
        assert history.tasks_failed == 0
        assert history.success_rate == 0.0
        assert history.avg_duration == timedelta()
        assert history.avg_quality_score == 0.0
        assert history.last_completed is None

    def test_creation_full(self) -> None:
        """Should create with all fields."""
        now = datetime.now()
        history = TaskTypeHistory(
            task_type=TaskType.API_SIMPLE,
            tasks_completed=10,
            tasks_failed=2,
            success_rate=0.8,
            avg_duration=timedelta(hours=2),
            avg_quality_score=0.9,
            last_completed=now,
        )

        assert history.tasks_completed == 10
        assert history.tasks_failed == 2
        assert history.success_rate == 0.8
        assert history.avg_duration == timedelta(hours=2)
        assert history.avg_quality_score == 0.9
        assert history.last_completed == now

    def test_to_dict(self) -> None:
        """Should convert to dictionary."""
        now = datetime(2024, 1, 15, 10, 30, 0)
        history = TaskTypeHistory(
            task_type=TaskType.API_SIMPLE,
            tasks_completed=5,
            success_rate=0.9,
            avg_duration=timedelta(hours=1, minutes=30),
            last_completed=now,
        )

        data = history.to_dict()

        assert data["task_type"] == "api:simple"
        assert data["tasks_completed"] == 5
        assert data["success_rate"] == 0.9
        assert data["avg_duration"] == 5400  # 1.5 hours in seconds
        assert data["last_completed"] == "2024-01-15T10:30:00"

    def test_to_dict_no_last_completed(self) -> None:
        """Should handle None last_completed."""
        history = TaskTypeHistory(task_type=TaskType.API_SIMPLE)

        data = history.to_dict()

        assert data["last_completed"] is None

    def test_from_dict(self) -> None:
        """Should create from dictionary."""
        data = {
            "task_type": "api:simple",
            "tasks_completed": 5,
            "tasks_failed": 1,
            "success_rate": 0.8,
            "avg_duration": 3600,
            "avg_quality_score": 0.85,
            "last_completed": "2024-01-15T10:30:00",
        }

        history = TaskTypeHistory.from_dict(data)

        assert history.task_type == TaskType.API_SIMPLE
        assert history.tasks_completed == 5
        assert history.avg_duration == timedelta(hours=1)
        assert history.last_completed == datetime(2024, 1, 15, 10, 30, 0)

    def test_from_dict_no_last_completed(self) -> None:
        """Should handle missing last_completed."""
        data = {
            "task_type": "api:simple",
            "tasks_completed": 5,
            "tasks_failed": 0,
            "success_rate": 1.0,
            "avg_duration": 0,
            "avg_quality_score": 0.0,
            "last_completed": None,
        }

        history = TaskTypeHistory.from_dict(data)

        assert history.last_completed is None

    def test_roundtrip(self) -> None:
        """Should survive roundtrip to/from dict."""
        now = datetime.now()
        original = TaskTypeHistory(
            task_type=TaskType.API_STATEFUL,
            tasks_completed=10,
            tasks_failed=2,
            success_rate=0.8,
            avg_duration=timedelta(hours=2),
            avg_quality_score=0.9,
            last_completed=now,
        )

        data = original.to_dict()
        restored = TaskTypeHistory.from_dict(data)

        assert restored.task_type == original.task_type
        assert restored.tasks_completed == original.tasks_completed
        assert restored.success_rate == original.success_rate


# =============================================================================
# AgentProfile Tests
# =============================================================================


class TestAgentProfile:
    """Tests for AgentProfile dataclass."""

    def test_creation_minimal(self) -> None:
        """Should create with minimal fields."""
        profile = AgentProfile(
            agent_id="forge:test",
            name="Test Agent",
            model="claude-test",
        )

        assert profile.agent_id == "forge:test"
        assert profile.name == "Test Agent"
        assert profile.model == "claude-test"
        assert profile.capabilities == {}
        assert profile.skills == {}
        assert profile.max_complexity == ComplexityLevel.MODERATE
        assert profile.preferred_domains == []
        assert profile.avoids == []
        assert profile.current_task_id is None
        assert profile.status == "idle"
        assert profile.total_tasks_completed == 0

    def test_creation_full(self) -> None:
        """Should create with all fields."""
        profile = AgentProfile(
            agent_id="forge:test",
            name="Test Agent",
            model="claude-test",
            capabilities={TaskType.API_SIMPLE: 0.9, TaskType.TEST_UNIT: 0.8},
            skills={Skill.PYTHON: 0.95, Skill.FASTAPI: 0.9},
            max_complexity=ComplexityLevel.COMPLEX,
            preferred_domains=["backend", "api"],
            avoids=[TaskType.FRONTEND_COMPONENT],
            current_task_id="task-123",
            status="busy",
            total_tasks_completed=50,
            success_rate=0.92,
        )

        assert profile.capabilities[TaskType.API_SIMPLE] == 0.9
        assert profile.skills[Skill.PYTHON] == 0.95
        assert profile.max_complexity == ComplexityLevel.COMPLEX
        assert "backend" in profile.preferred_domains
        assert TaskType.FRONTEND_COMPONENT in profile.avoids
        assert profile.status == "busy"

    def test_is_available_idle(self) -> None:
        """Should be available when idle."""
        profile = AgentProfile(
            agent_id="forge:test",
            name="Test",
            model="test",
            status="idle",
        )

        assert profile.is_available() is True

    def test_is_available_waiting(self) -> None:
        """Should be available when waiting."""
        profile = AgentProfile(
            agent_id="forge:test",
            name="Test",
            model="test",
            status="waiting",
        )

        assert profile.is_available() is True

    def test_is_available_busy(self) -> None:
        """Should not be available when busy."""
        profile = AgentProfile(
            agent_id="forge:test",
            name="Test",
            model="test",
            status="busy",
        )

        assert profile.is_available() is False

    def test_is_available_paused(self) -> None:
        """Should not be available when paused."""
        profile = AgentProfile(
            agent_id="forge:test",
            name="Test",
            model="test",
            status="paused",
        )

        assert profile.is_available() is False

    def test_to_dict(self) -> None:
        """Should convert to dictionary."""
        profile = AgentProfile(
            agent_id="forge:test",
            name="Test Agent",
            model="claude-test",
            capabilities={TaskType.API_SIMPLE: 0.9},
            skills={Skill.PYTHON: 0.95},
            max_complexity=ComplexityLevel.COMPLEX,
            preferred_domains=["backend"],
            avoids=[TaskType.FRONTEND_COMPONENT],
            current_task_id="task-123",
            status="busy",
            total_tasks_completed=50,
            success_rate=0.92,
            avg_task_duration=timedelta(hours=2),
            avg_quality_score=0.88,
        )

        data = profile.to_dict()

        assert data["agent_id"] == "forge:test"
        assert data["name"] == "Test Agent"
        assert data["capabilities"] == {"api:simple": 0.9}
        assert data["skills"] == {"PYTHON": 0.95}
        assert data["max_complexity"] == 4
        assert data["avoids"] == ["frontend:component"]
        assert data["status"] == "busy"
        assert data["avg_task_duration"] == 7200  # 2 hours in seconds

    def test_from_dict(self) -> None:
        """Should create from dictionary."""
        data = {
            "agent_id": "forge:test",
            "name": "Test Agent",
            "model": "claude-test",
            "capabilities": {"api:simple": 0.9},
            "skills": {"PYTHON": 0.95},
            "max_complexity": 4,
            "preferred_domains": ["backend"],
            "avoids": ["frontend:component"],
            "current_task_id": "task-123",
            "status": "busy",
            "last_activity": "2024-01-15T10:30:00",
            "total_tasks_completed": 50,
            "success_rate": 0.92,
            "avg_task_duration": 7200,
            "avg_quality_score": 0.88,
            "task_type_history": {
                "api:simple": {
                    "task_type": "api:simple",
                    "tasks_completed": 10,
                    "tasks_failed": 0,
                    "success_rate": 1.0,
                    "avg_duration": 3600,
                    "avg_quality_score": 0.9,
                    "last_completed": "2024-01-15T10:30:00",
                }
            },
        }

        profile = AgentProfile.from_dict(data)

        assert profile.agent_id == "forge:test"
        assert profile.capabilities[TaskType.API_SIMPLE] == 0.9
        assert profile.skills[Skill.PYTHON] == 0.95
        assert profile.max_complexity == ComplexityLevel.COMPLEX
        assert TaskType.FRONTEND_COMPONENT in profile.avoids
        assert profile.status == "busy"
        assert TaskType.API_SIMPLE in profile.task_type_history

    def test_from_dict_defaults(self) -> None:
        """Should use defaults for missing optional fields."""
        data = {
            "agent_id": "forge:test",
            "name": "Test",
            "model": "test",
            "max_complexity": 3,  # Required field
        }

        profile = AgentProfile.from_dict(data)

        assert profile.capabilities == {}
        assert profile.skills == {}
        assert profile.max_complexity == ComplexityLevel.MODERATE
        assert profile.preferred_domains == []
        assert profile.avoids == []
        assert profile.current_task_id is None
        assert profile.status == "idle"
        assert profile.total_tasks_completed == 0
        assert profile.success_rate == 0.0

    def test_roundtrip(self) -> None:
        """Should survive roundtrip to/from dict."""
        original = AgentProfile(
            agent_id="forge:test",
            name="Test Agent",
            model="claude-test",
            capabilities={TaskType.API_SIMPLE: 0.9},
            skills={Skill.PYTHON: 0.95},
            max_complexity=ComplexityLevel.COMPLEX,
            preferred_domains=["backend"],
            avoids=[TaskType.FRONTEND_COMPONENT],
            current_task_id="task-123",
            status="busy",
            total_tasks_completed=50,
            success_rate=0.92,
            avg_task_duration=timedelta(hours=2),
            avg_quality_score=0.88,
        )

        data = original.to_dict()
        restored = AgentProfile.from_dict(data)

        assert restored.agent_id == original.agent_id
        assert restored.name == original.name
        assert restored.capabilities == original.capabilities
        assert restored.skills == original.skills
        assert restored.max_complexity == original.max_complexity


# =============================================================================
# CodebaseContext Tests
# =============================================================================


class TestCodebaseContext:
    """Tests for CodebaseContext dataclass."""

    def test_creation_defaults(self) -> None:
        """Should create with defaults."""
        context = CodebaseContext()

        assert context.affected_files == []
        assert context.affected_file_count == 0
        assert context.affected_modules == []
        assert context.max_dependency_depth == 0
        assert context.circular_dependencies is False
        assert context.critical_path is False
        assert context.touches_critical_system is False
        assert context.breaking_change_risk == 0.0
        assert context.existing_test_coverage == 0.0
        assert context.test_gap_score == 0.0
        assert context.cyclomatic_complexity is None
        assert context.maintainability_index is None
        assert context.domain_patterns == []
        assert context.anti_patterns == []
        assert context.suggested_improvements == []

    def test_creation_full(self) -> None:
        """Should create with all fields."""
        context = CodebaseContext(
            affected_files=["src/main.py", "src/utils.py"],
            affected_file_count=2,
            affected_modules=["api", "utils"],
            max_dependency_depth=3,
            circular_dependencies=True,
            critical_path=True,
            touches_critical_system=True,
            breaking_change_risk=0.7,
            existing_test_coverage=0.6,
            test_gap_score=0.4,
            cyclomatic_complexity=15.5,
            maintainability_index=75.0,
            domain_patterns=["repository", "service"],
            anti_patterns=["god class"],
            suggested_improvements=["refactor", "add tests"],
        )

        assert context.affected_files == ["src/main.py", "src/utils.py"]
        assert context.max_dependency_depth == 3
        assert context.circular_dependencies is True
        assert context.breaking_change_risk == 0.7
        assert context.cyclomatic_complexity == 15.5
        assert "repository" in context.domain_patterns

    def test_to_dict(self) -> None:
        """Should convert to dictionary."""
        context = CodebaseContext(
            affected_files=["src/main.py"],
            affected_file_count=1,
            max_dependency_depth=2,
            circular_dependencies=False,
            breaking_change_risk=0.3,
        )

        data = context.to_dict()

        assert data["affected_files"] == ["src/main.py"]
        assert data["affected_file_count"] == 1
        assert data["max_dependency_depth"] == 2
        assert data["circular_dependencies"] is False
        assert data["breaking_change_risk"] == 0.3


# =============================================================================
# TaskAnalysis Tests
# =============================================================================


class TestTaskAnalysis:
    """Tests for TaskAnalysis dataclass."""

    def test_creation_minimal(self) -> None:
        """Should create with minimal fields."""
        task = TaskAnalysis(
            id="task-123",
            subject="Test task",
            description="Test description",
            priority=5,
        )

        assert task.id == "task-123"
        assert task.subject == "Test task"
        assert task.description == "Test description"
        assert task.priority == 5
        assert task.domain is None
        assert task.project is None
        assert task.status == "pending"
        assert task.task_type == TaskType.GENERAL
        assert task.complexity == ComplexityLevel.SIMPLE

    def test_creation_full(self) -> None:
        """Should create with all fields."""
        context = CodebaseContext(affected_files=["src/main.py"])
        task = TaskAnalysis(
            id="task-123",
            subject="Test task",
            description="Test description",
            priority=8,
            domain="backend",
            project="api",
            status="in_progress",
            task_type=TaskType.API_SIMPLE,
            complexity=ComplexityLevel.MODERATE,
            dependencies=["task-001"],
            dependents=["task-002"],
            blocked=False,
            estimated_duration=timedelta(hours=4),
            required_skills={Skill.PYTHON, Skill.FASTAPI},
            priority_score=0.8,
            capability_match_score=0.9,
            historical_score=0.85,
            final_score=0.87,
            recommended_agent="forge:test",
            codebase_context=context,
        )

        assert task.domain == "backend"
        assert task.task_type == TaskType.API_SIMPLE
        assert task.complexity == ComplexityLevel.MODERATE
        assert task.dependencies == ["task-001"]
        assert Skill.PYTHON in task.required_skills
        assert task.priority_score == 0.8
        assert task.recommended_agent == "forge:test"
        assert task.codebase_context is context

    def test_to_dict(self) -> None:
        """Should convert to dictionary."""
        context = CodebaseContext(affected_files=["src/main.py"])
        task = TaskAnalysis(
            id="task-123",
            subject="Test task",
            description="Test description",
            priority=8,
            domain="backend",
            status="in_progress",
            task_type=TaskType.API_SIMPLE,
            complexity=ComplexityLevel.MODERATE,
            estimated_duration=timedelta(hours=4),
            required_skills={Skill.PYTHON, Skill.FASTAPI},
            priority_score=0.8,
            final_score=0.87,
            recommended_agent="forge:test",
            codebase_context=context,
        )

        data = task.to_dict()

        assert data["id"] == "task-123"
        assert data["subject"] == "Test task"
        assert data["priority"] == 8
        assert data["domain"] == "backend"
        assert data["status"] == "in_progress"
        assert data["task_type"] == "api:simple"
        assert data["complexity"] == 3
        assert data["estimated_duration"] == 14400  # 4 hours in seconds
        assert data["required_skills"] == ["PYTHON", "FASTAPI"]
        assert data["priority_score"] == 0.8
        assert data["final_score"] == 0.87
        assert data["recommended_agent"] == "forge:test"
        assert data["codebase_context"] is not None

    def test_to_dict_no_duration(self) -> None:
        """Should handle None estimated_duration."""
        task = TaskAnalysis(
            id="task-123",
            subject="Test",
            description="Test",
            priority=5,
        )

        data = task.to_dict()

        assert data["estimated_duration"] is None

    def test_to_dict_no_codebase_context(self) -> None:
        """Should handle None codebase_context."""
        task = TaskAnalysis(
            id="task-123",
            subject="Test",
            description="Test",
            priority=5,
        )

        data = task.to_dict()

        assert data["codebase_context"] is None


# =============================================================================
# RecommendationFeedback Tests
# =============================================================================


class TestRecommendationFeedback:
    """Tests for RecommendationFeedback dataclass."""

    def test_creation_minimal(self) -> None:
        """Should create with minimal fields."""
        feedback = RecommendationFeedback(
            recommendation_id="rec-123",
            task_id="task-123",
            agent_id="forge:test",
            recommended=True,
            accepted=True,
            completed=True,
            duration=timedelta(hours=2),
        )

        assert feedback.recommendation_id == "rec-123"
        assert feedback.task_id == "task-123"
        assert feedback.agent_id == "forge:test"
        assert feedback.recommended is True
        assert feedback.accepted is True
        assert feedback.completed is True
        assert feedback.duration == timedelta(hours=2)
        assert feedback.quality_score is None
        assert feedback.feedback is None
        assert feedback.was_good_recommendation is False
        assert feedback.should_adjust_algorithm is False

    def test_creation_full(self) -> None:
        """Should create with all fields."""
        feedback = RecommendationFeedback(
            recommendation_id="rec-123",
            task_id="task-123",
            agent_id="forge:test",
            recommended=True,
            accepted=True,
            completed=True,
            duration=timedelta(hours=2),
            quality_score=0.9,
            feedback="Good match for this agent",
            was_good_recommendation=True,
            should_adjust_algorithm=True,
            adjustment_notes="Increase weight for Python skills",
        )

        assert feedback.quality_score == 0.9
        assert feedback.feedback == "Good match for this agent"
        assert feedback.was_good_recommendation is True
        assert feedback.should_adjust_algorithm is True
        assert feedback.adjustment_notes == "Increase weight for Python skills"

    def test_to_dict(self) -> None:
        """Should convert to dictionary."""
        feedback = RecommendationFeedback(
            recommendation_id="rec-123",
            task_id="task-123",
            agent_id="forge:test",
            recommended=True,
            accepted=True,
            completed=True,
            duration=timedelta(hours=2, minutes=30),
            quality_score=0.9,
            feedback="Good match",
            was_good_recommendation=True,
            should_adjust_algorithm=False,
            adjustment_notes=None,
        )

        data = feedback.to_dict()

        assert data["recommendation_id"] == "rec-123"
        assert data["task_id"] == "task-123"
        assert data["recommended"] is True
        assert data["accepted"] is True
        assert data["completed"] is True
        assert data["duration"] == 9000  # 2.5 hours in seconds
        assert data["quality_score"] == 0.9
        assert data["feedback"] == "Good match"
        assert data["was_good_recommendation"] is True
        assert data["should_adjust_algorithm"] is False
        assert data["adjustment_notes"] is None
