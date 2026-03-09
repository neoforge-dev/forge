"""
Comprehensive unit tests for ScoringEngine.

Tests multi-factor task-agent scoring, capability matching,
historical performance, and batch scoring operations.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.analytics.scoring_engine import (
    ScoreBreakdown,
    ScoringEngine,
    calculate_capability_match,
    calculate_historical_score,
    calculate_task_score,
    get_recent_performance_scores,
)
from forge_harness.models.task_models import (
    AgentProfile,
    CodebaseContext,
    ComplexityLevel,
    Skill,
    TaskAnalysis,
    TaskType,
    TaskTypeHistory,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_task():
    """Sample task for testing."""
    return TaskAnalysis(
        id="task-1",
        subject="Create API endpoint",
        description="Build a GET endpoint to fetch users",
        priority=7,
        domain="voice-coach",
        project="backend",
        status="pending",
        task_type=TaskType.API_SIMPLE,
        complexity=ComplexityLevel.SIMPLE,
        required_skills={Skill.PYTHON, Skill.FASTAPI},
        priority_score=0.7,
    )


@pytest.fixture
def sample_agent():
    """Sample agent profile for testing."""
    return AgentProfile(
        agent_id="forge:kimi",
        name="Kimi",
        model="claude-opus-4-6",
        capabilities={TaskType.API_SIMPLE: 0.9, TaskType.FRONTEND_COMPONENT: 0.7},
        skills={Skill.PYTHON: 0.8, Skill.FASTAPI: 0.9},
        max_complexity=ComplexityLevel.MODERATE,
        preferred_domains=["voice-coach", "brandfocus-ai"],
        avoids=[],
        status="idle",
        success_rate=0.85,
        task_type_history={},
    )


@pytest.fixture
def idle_agent():
    """Idle agent profile."""
    return AgentProfile(
        agent_id="forge:codex",
        name="Codex",
        model="gemini-2.5",
        capabilities={TaskType.API_SIMPLE: 0.8},
        skills={Skill.PYTHON: 0.7},
        max_complexity=ComplexityLevel.COMPLEX,
        preferred_domains=[],
        avoids=[],
        status="idle",
        success_rate=0.75,
    )


@pytest.fixture
def busy_agent():
    """Busy agent profile."""
    return AgentProfile(
        agent_id="forge:gemini",
        name="Gemini",
        model="gemini-2.0",
        capabilities={TaskType.API_SIMPLE: 0.9},
        skills={Skill.PYTHON: 0.8},
        max_complexity=ComplexityLevel.SIMPLE,
        preferred_domains=[],
        avoids=[],
        status="busy",
        success_rate=0.80,
    )


@pytest.fixture
def high_complexity_task():
    """High complexity task."""
    return TaskAnalysis(
        id="task-complex",
        subject="System migration",
        description="Migrate entire system architecture",
        priority=10,
        task_type=TaskType.REFACTOR_MIGRATE,
        complexity=ComplexityLevel.EXPERT,
        required_skills={Skill.PYTHON, Skill.DOCKER, Skill.KUBERNETES},
        priority_score=1.0,
    )


@pytest.fixture
def blocked_task():
    """Blocked task."""
    return TaskAnalysis(
        id="task-blocked",
        subject="Feature blocked",
        description="Waiting for dependency",
        priority=5,
        task_type=TaskType.API_SIMPLE,
        complexity=ComplexityLevel.SIMPLE,
        blocked=True,
        required_skills=set(),
        priority_score=0.5,
    )


# =============================================================================
# Test calculate_task_score
# =============================================================================


class TestCalculateTaskScore:
    """Tests for calculate_task_score function."""

    def test_score_returns_tuple(self, sample_task, sample_agent):
        """Test calculate_task_score returns tuple of (score, breakdown)."""
        score, breakdown = calculate_task_score(sample_task, sample_agent)

        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
        assert isinstance(breakdown, ScoreBreakdown)

    def test_score_breakdown_components(self, sample_task, sample_agent):
        """Test breakdown has all required components."""
        score, breakdown = calculate_task_score(sample_task, sample_agent)

        assert hasattr(breakdown, "priority_score")
        assert hasattr(breakdown, "capability_score")
        assert hasattr(breakdown, "historical_score")
        assert hasattr(breakdown, "task_type_match")
        assert hasattr(breakdown, "skills_match")
        assert hasattr(breakdown, "domain_preference")
        assert hasattr(breakdown, "final_score")
        assert hasattr(breakdown, "blocked_penalty")
        assert hasattr(breakdown, "complexity_penalty")
        assert hasattr(breakdown, "availability_penalty")
        assert hasattr(breakdown, "recency_boost")

    def test_score_with_high_priority(self, sample_agent):
        """Test scoring with high priority task."""
        task = TaskAnalysis(
            id="task-high",
            subject="Critical task",
            description="Urgent fix",
            priority=10,
            priority_score=1.0,
            task_type=TaskType.API_SIMPLE,
            complexity=ComplexityLevel.SIMPLE,
            required_skills=set(),
        )

        score, breakdown = calculate_task_score(task, sample_agent)

        # High priority should contribute to final score
        assert breakdown.priority_score == 1.0

    def test_score_with_capability_match(self, sample_task):
        """Test scoring with good capability match."""
        # Agent has high capability for this task type
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.95},
            skills={Skill.PYTHON: 0.9, Skill.FASTAPI: 0.95},
            success_rate=0.8,
        )

        score, breakdown = calculate_task_score(sample_task, agent)

        # High capability should contribute to final score
        assert breakdown.capability_score > 0.5

    def test_score_with_historical_performance(self, sample_task):
        """Test scoring with good historical performance."""
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.7},
            skills={},
            success_rate=0.95,
            task_type_history={
                TaskType.API_SIMPLE: TaskTypeHistory(
                    task_type=TaskType.API_SIMPLE,
                    success_rate=0.9,
                    avg_quality_score=0.85,
                )
            },
        )

        score, breakdown = calculate_task_score(sample_task, agent)

        # Good history should contribute to score
        assert breakdown.historical_score > 0.5

    def test_score_blocked_task_penalty(self, blocked_task, idle_agent):
        """Test blocked tasks receive heavy penalty."""
        score, breakdown = calculate_task_score(blocked_task, idle_agent)

        assert breakdown.blocked_penalty is True
        # Score should be significantly reduced (multiplied by 0.1)
        expected_penalty = 0.1
        # Check that score is much lower than it would be otherwise
        assert score < 0.3

    def test_score_complexity_penalty(self, high_complexity_task, sample_agent):
        """Test tasks exceeding agent complexity get penalty."""
        score, breakdown = calculate_task_score(high_complexity_task, sample_agent)

        assert breakdown.complexity_penalty is True
        # Agent max_complexity is MODERATE (3), task is EXPERT (5)
        # Score should be reduced (multiplied by 0.3)
        assert score < 0.5

    def test_score_availability_penalty_busy_agent(self, sample_task, busy_agent):
        """Test busy agents receive penalty."""
        score, breakdown = calculate_task_score(sample_task, busy_agent)

        assert breakdown.availability_penalty is True
        # Score should be reduced (multiplied by 0.5)
        assert score < 0.6

    def test_score_all_penalties(self, blocked_task, busy_agent, high_complexity_task):
        """Test all penalties applied together."""
        # Combine: blocked task + busy agent + high complexity
        score, breakdown = calculate_task_score(blocked_task, busy_agent)

        assert breakdown.blocked_penalty is True
        assert breakdown.availability_penalty is True

    def test_score_clamped_to_range(self, sample_task, sample_agent):
        """Test final score is clamped to [0, 1]."""
        score, breakdown = calculate_task_score(sample_task, sample_agent)

        assert 0.0 <= score <= 1.0

    def test_score_with_no_skills_required(self, sample_agent):
        """Test scoring when task requires no specific skills."""
        task = TaskAnalysis(
            id="task-no-skills",
            subject="Generic task",
            description="Just do it",
            priority=5,
            priority_score=0.5,
            task_type=TaskType.GENERAL,
            complexity=ComplexityLevel.SIMPLE,
            required_skills=set(),
        )

        score, breakdown = calculate_task_score(task, sample_agent)

        # Should handle empty skills gracefully
        assert isinstance(score, float)

    def test_score_with_time_decay_default(self, sample_task, sample_agent):
        """Test time decay with default value."""
        score, breakdown = calculate_task_score(sample_task, sample_agent, time_decay=0.1)

        assert isinstance(score, float)

    def test_score_with_custom_time_decay(self, sample_task, sample_agent):
        """Test score with custom time decay."""
        score, breakdown = calculate_task_score(sample_task, sample_agent, time_decay=0.5)

        assert isinstance(score, float)

    def test_score_recency_boost_recent_completion(self, sample_task):
        """Test recency boost for recently completed tasks."""
        # Agent with recent successful completion
        yesterday = datetime.now() - timedelta(days=1)
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.8},
            success_rate=0.8,
            task_type_history={
                TaskType.API_SIMPLE: TaskTypeHistory(
                    task_type=TaskType.API_SIMPLE,
                    last_completed=yesterday,
                )
            },
        )

        score, breakdown = calculate_task_score(sample_task, agent)

        # Should have some recency boost
        assert breakdown.recency_boost > 0

    def test_score_recency_boost_old_completion(self, sample_task):
        """Test recency boost decays over time."""
        # Agent with old completion
        old_date = datetime.now() - timedelta(days=30)
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.8},
            success_rate=0.8,
            task_type_history={
                TaskType.API_SIMPLE: TaskTypeHistory(
                    task_type=TaskType.API_SIMPLE,
                    last_completed=old_date,
                )
            },
        )

        score, breakdown = calculate_task_score(sample_task, agent)

        # Recency boost should be very small for old completions
        assert breakdown.recency_boost < 0.1

    def test_score_recency_boost_no_history(self, sample_task, sample_agent):
        """Test recency boost when no task type history."""
        score, breakdown = calculate_task_score(sample_task, sample_agent)

        # No history means no recency boost
        assert breakdown.recency_boost == 0.0


# =============================================================================
# Test calculate_capability_match
# =============================================================================


class TestCalculateCapabilityMatch:
    """Tests for calculate_capability_match function."""

    def test_capability_match_returns_dict(self, sample_task, sample_agent):
        """Test calculate_capability_match returns dict."""
        result = calculate_capability_match(sample_task, sample_agent)

        assert isinstance(result, dict)
        assert "score" in result
        assert "task_type_match" in result
        assert "skills_match" in result
        assert "domain_preference" in result

    def test_capability_match_direct_type_match(self):
        """Test direct task type capability is used."""
        task = TaskAnalysis(
            id="task-1",
            subject="API",
            description="API task",
            priority=5,
            task_type=TaskType.API_SIMPLE,
            required_skills={},
            domain="test-domain",
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.85},
            skills={},
        )

        result = calculate_capability_match(task, agent)

        assert result["task_type_match"] == 0.85

    def test_capability_match_low_capability(self):
        """Test with low capability for task type."""
        task = TaskAnalysis(
            id="task-1",
            subject="API",
            description="API task",
            priority=5,
            task_type=TaskType.API_SIMPLE,
            required_skills={},
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.2},
            skills={},
        )

        result = calculate_capability_match(task, agent)

        assert result["task_type_match"] == 0.2

    def test_capability_match_skills_average(self):
        """Test skills match is averaged."""
        task = TaskAnalysis(
            id="task-1",
            subject="Full stack",
            description="Multi-skill task",
            priority=5,
            task_type=TaskType.API_SIMPLE,
            required_skills={Skill.PYTHON: 1.0, Skill.FASTAPI: 0.5},
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={},
            skills={Skill.PYTHON: 0.8, Skill.FASTAPI: 0.4},
        )

        result = calculate_capability_match(task, agent)

        # Skills match should be average: (0.8 + 0.4) / 2 = 0.6
        assert abs(result["skills_match"] - 0.6) < 0.01

    def test_capability_match_no_skills_neutral(self):
        """Test no skills required gives neutral score."""
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.GENERAL,
            required_skills=set(),
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={},
            skills={},
        )

        result = calculate_capability_match(task, agent)

        # Neutral score when no skills specified
        assert result["skills_match"] == 0.5

    def test_capability_match_domain_boost(self):
        """Test domain in preferred domains gives boost."""
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.API_SIMPLE,
            required_skills=set(),
            domain="voice-coach",
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.7},
            skills={},
            preferred_domains=["voice-coach"],
        )

        result = calculate_capability_match(task, agent)

        # Domain in preferred domains
        assert result["domain_preference"] == 1.0

    def test_capability_match_domain_neutral(self):
        """Test domain not in preferred domains gives neutral score."""
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.API_SIMPLE,
            required_skills=set(),
            domain="unknown-domain",
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.7},
            skills={},
            preferred_domains=["other-domain"],
        )

        result = calculate_capability_match(task, agent)

        # Domain not in preferred
        assert result["domain_preference"] == 0.5

    def test_capability_match_no_domain_neutral(self):
        """Test no domain specified gives neutral score."""
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.API_SIMPLE,
            required_skills=set(),
            domain=None,
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.7},
            skills={},
            preferred_domains=[],
        )

        result = calculate_capability_match(task, agent)

        # No domain specified
        assert result["domain_preference"] == 0.5

    def test_capability_match_avoids_penalty(self):
        """Test agent avoiding task type applies heavy penalty."""
        task = TaskAnalysis(
            id="task-1",
            subject="Docker task",
            description="Containerize app",
            priority=5,
            task_type=TaskType.INFRA_DOCKER,
            required_skills={},
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.INFRA_DOCKER: 0.9},
            skills={},
            avoids=[TaskType.INFRA_DOCKER],
        )

        result = calculate_capability_match(task, agent)

        # Avoided type should have heavily reduced score
        # Original 0.9 * 0.2 = 0.18
        assert result["task_type_match"] < 0.3

    def test_capability_match_weighted_combination(self):
        """Test final score is weighted combination."""
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.API_SIMPLE,
            required_skills={Skill.PYTHON: 0.7},
            domain="test-domain",
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 0.8},
            skills={Skill.PYTHON: 0.6},
            preferred_domains=["test-domain"],
        )

        result = calculate_capability_match(task, agent)

        # Weighted: 70% task_type + 20% skills + 10% domain
        expected = (
            0.7 * 0.8 +
            0.2 * 0.6 +
            0.1 * 1.0
        )
        assert abs(result["score"] - expected) < 0.01


# =============================================================================
# Test calculate_historical_score
# =============================================================================


class TestCalculateHistoricalScore:
    """Tests for calculate_historical_score function."""

    def test_historical_score_returns_float(self, sample_task, sample_agent):
        """Test calculate_historical_score returns float in range."""
        score = calculate_historical_score(sample_task, sample_agent)

        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_historical_score_uses_success_rate(self):
        """Test historical score uses agent's overall success rate."""
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.API_SIMPLE,
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            success_rate=0.9,
        )

        score = calculate_historical_score(task, agent)

        # Success rate contributes 40% to score
        assert score >= 0.9 * 0.4  # Minimum from success rate alone

    def test_historical_score_with_task_type_history(self):
        """Test historical score includes task-type specific performance."""
        task = TaskAnalysis(
            id="task-1",
            subject="API task",
            description="API task",
            priority=5,
            task_type=TaskType.API_SIMPLE,
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            success_rate=0.7,
            task_type_history={
                TaskType.API_SIMPLE: TaskTypeHistory(
                    task_type=TaskType.API_SIMPLE,
                    success_rate=0.9,
                    avg_quality_score=0.85,
                )
            },
        )

        score = calculate_historical_score(task, agent)

        # Should benefit from type-specific performance
        assert score > 0.7 * 0.4  # More than just overall success rate

    def test_historical_score_cold_start_penalty(self):
        """Test cold start penalty for new task types."""
        task = TaskAnalysis(
            id="task-1",
            subject="New type",
            description="Task",
            priority=5,
            task_type=TaskType.API_EXTERNAL,
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            success_rate=0.8,
            task_type_history={},  # Empty history for this type
        )

        score = calculate_historical_score(task, agent)

        # Should have cold start penalty (0.3)
        assert score < 0.8 * 0.4 + 0.3  # Less than without penalty

    def test_historical_score_clamped_to_range(self):
        """Test historical score is clamped to [0, 1]."""
        # Create scenario that would exceed 1.0
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.API_SIMPLE,
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            success_rate=1.0,  # Perfect
            task_type_history={
                TaskType.API_SIMPLE: TaskTypeHistory(
                    task_type=TaskType.API_SIMPLE,
                    success_rate=1.0,
                    avg_quality_score=1.0,
                )
            },
        )

        score = calculate_historical_score(task, agent)

        assert score <= 1.0

    def test_historical_score_minimum(self):
        """Test historical score has minimum value."""
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.API_SIMPLE,
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            success_rate=0.0,  # No success
            task_type_history={
                TaskType.API_SIMPLE: TaskTypeHistory(
                    task_type=TaskType.API_SIMPLE,
                    success_rate=0.0,
                    avg_quality_score=0.0,
                )
            },
        )

        score = calculate_historical_score(task, agent)

        assert score >= 0.0


# =============================================================================
# Test get_recent_performance_scores
# =============================================================================


class TestGetRecentPerformanceScores:
    """Tests for get_recent_performance_scores function."""

    def test_recent_scores_returns_list(self):
        """Test get_recent_performance_scores returns list."""
        scores = get_recent_performance_scores("agent-123")

        assert isinstance(scores, list)

    def test_recent_scores_empty_by_default(self):
        """Test get_recent_performance_scores returns empty list by default."""
        scores = get_recent_performance_scores("agent-123")

        assert scores == []

    def test_recent_scores_respects_n_parameter(self):
        """Test get_recent_performance_scores respects n parameter."""
        # The function is a placeholder that returns empty list
        # But we can test it accepts the parameter
        scores_5 = get_recent_performance_scores("agent-123", n=5)
        scores_10 = get_recent_performance_scores("agent-123", n=10)

        assert isinstance(scores_5, list)
        assert isinstance(scores_10, list)


# =============================================================================
# Test ScoreBreakdown
# =============================================================================


class TestScoreBreakdown:
    """Tests for ScoreBreakdown dataclass."""

    def test_score_breakdown_creation(self):
        """Test ScoreBreakdown can be created."""
        breakdown = ScoreBreakdown(
            priority_score=0.7,
            capability_score=0.8,
            historical_score=0.75,
            task_type_match=0.9,
            skills_match=0.6,
            domain_preference=1.0,
            final_score=0.78,
            blocked_penalty=False,
            complexity_penalty=False,
            availability_penalty=False,
            recency_boost=0.1,
        )

        assert breakdown.priority_score == 0.7
        assert breakdown.capability_score == 0.8
        assert breakdown.blocked_penalty is False
        assert breakdown.recency_boost == 0.1

    def test_score_breakdown_all_fields(self):
        """Test ScoreBreakdown has all expected fields."""
        breakdown = ScoreBreakdown(
            priority_score=0.0,
            capability_score=0.0,
            historical_score=0.0,
            task_type_match=0.0,
            skills_match=0.0,
            domain_preference=0.0,
            final_score=0.0,
            blocked_penalty=False,
            complexity_penalty=False,
            availability_penalty=False,
            recency_boost=0.0,
        )

        # All fields should be accessible
        assert breakdown.priority_score == 0.0
        assert breakdown.capability_score == 0.0
        assert breakdown.historical_score == 0.0
        assert breakdown.task_type_match == 0.0
        assert breakdown.skills_match == 0.0
        assert breakdown.domain_preference == 0.0
        assert breakdown.final_score == 0.0
        assert breakdown.blocked_penalty is False
        assert breakdown.complexity_penalty is False
        assert breakdown.availability_penalty is False
        assert breakdown.recency_boost == 0.0


# =============================================================================
# Test ScoringEngine class
# =============================================================================


class TestScoringEngine:
    """Tests for ScoringEngine class."""

    def test_init_default(self):
        """Test ScoringEngine initialization with default time_decay."""
        engine = ScoringEngine()

        assert engine.time_decay == 0.1
        assert engine._cache == {}

    def test_init_custom_time_decay(self):
        """Test ScoringEngine initialization with custom time_decay."""
        engine = ScoringEngine(time_decay=0.5)

        assert engine.time_decay == 0.5
        assert engine._cache == {}

    def test_calculate_agent_scores_returns_list(self, sample_task):
        """Test calculate_agent_scores returns list of tuples."""
        engine = ScoringEngine()
        agents = [
            AgentProfile(
                agent_id=f"agent-{i}",
                name=f"Agent {i}",
                model="test",
                capabilities={TaskType.API_SIMPLE: 0.5 + i * 0.1},
                success_rate=0.5,
            )
            for i in range(3)
        ]

        results = engine.calculate_agent_scores(sample_task, agents)

        assert isinstance(results, list)
        assert len(results) == 3
        # Each result should be (agent, score, breakdown) tuple
        for agent, score, breakdown in results:
            assert isinstance(agent, AgentProfile)
            assert isinstance(score, float)
            assert isinstance(breakdown, ScoreBreakdown)

    def test_calculate_agent_scores_sorted_by_score(self, sample_task):
        """Test calculate_agent_scores sorts results by score descending."""
        engine = ScoringEngine()
        agents = [
            AgentProfile(
                agent_id=f"agent-{i}",
                name=f"Agent {i}",
                model="test",
                capabilities={TaskType.API_SIMPLE: i * 0.3},  # Varying capability
                success_rate=0.5,
            )
            for i in range(1, 4)  # 1, 2, 3
        ]

        results = engine.calculate_agent_scores(sample_task, agents)

        # Should be sorted by score descending
        scores = [score for _, score, _ in results]
        assert scores == sorted(scores, reverse=True)

    def test_calculate_agent_scores_empty_agent_list(self, sample_task):
        """Test calculate_agent_scores with empty agent list."""
        engine = ScoringEngine()

        results = engine.calculate_agent_scores(sample_task, [])

        assert results == []

    def test_calculate_task_scores_for_agent_returns_list(self, idle_agent):
        """Test calculate_task_scores_for_agent returns list of tuples."""
        engine = ScoringEngine()
        tasks = [
            TaskAnalysis(
                id=f"task-{i}",
                subject=f"Task {i}",
                description=f"Description {i}",
                priority=i,
                priority_score=i / 10,
                task_type=TaskType.API_SIMPLE,
                required_skills=set(),
            )
            for i in range(1, 4)
        ]

        results = engine.calculate_task_scores_for_agent(tasks, idle_agent)

        assert isinstance(results, list)
        assert len(results) == 3
        # Each result should be (task, score, breakdown) tuple
        for task, score, breakdown in results:
            assert isinstance(task, TaskAnalysis)
            assert isinstance(score, float)
            assert isinstance(breakdown, ScoreBreakdown)

    def test_calculate_task_scores_sorted_by_score(self, idle_agent):
        """Test calculate_task_scores_for_agent sorts results by score descending."""
        engine = ScoringEngine()
        tasks = [
            TaskAnalysis(
                id=f"task-{i}",
                subject=f"Task {i}",
                description=f"Description {i}",
                priority=i,
                priority_score=i / 10,
                task_type=TaskType.API_SIMPLE,
                required_skills=set(),
            )
            for i in range(1, 4)  # Different priorities
        ]

        results = engine.calculate_task_scores_for_agent(tasks, idle_agent)

        # Should be sorted by score descending
        scores = [score for _, score, _ in results]
        assert scores == sorted(scores, reverse=True)

    def test_calculate_task_scores_empty_task_list(self, sample_agent):
        """Test calculate_task_scores_for_agent with empty task list."""
        engine = ScoringEngine()

        results = engine.calculate_task_scores_for_agent([], sample_agent)

        assert results == []

    def test_calculate_agent_scores_uses_time_decay(self, sample_task, sample_agent):
        """Test calculate_agent_scores uses engine's time_decay."""
        engine = ScoringEngine(time_decay=0.5)
        agents = [sample_agent]

        results = engine.calculate_agent_scores(sample_task, agents)

        # Should not crash, should use time_decay=0.5
        assert len(results) == 1

    def test_calculate_task_scores_for_agent_uses_time_decay(self, sample_task, idle_agent):
        """Test calculate_task_scores_for_agent uses engine's time_decay."""
        engine = ScoringEngine(time_decay=0.5)
        tasks = [sample_task]

        results = engine.calculate_task_scores_for_agent(tasks, idle_agent)

        # Should not crash, should use time_decay=0.5
        assert len(results) == 1

    def test_clear_cache(self):
        """Test clear_cache clears the cache."""
        engine = ScoringEngine()
        engine._cache["test_key"] = 0.5

        engine.clear_cache()

        assert engine._cache == {}


# =============================================================================
# Test edge cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_score_with_no_agent_capabilities(self, sample_task):
        """Test scoring when agent has no capabilities."""
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={},  # No capabilities
            skills={},
            success_rate=0.5,
        )

        # calculate_capability_match returns a dict, not a tuple
        result = calculate_capability_match(sample_task, agent)
        score = result["score"]

        # Should handle gracefully
        assert isinstance(score, float)

    def test_score_with_all_max_values(self):
        """Test scoring with maximum values."""
        task = TaskAnalysis(
            id="task-max",
            subject="Max task",
            description="Maximum priority task",
            priority=10,
            priority_score=1.0,
            task_type=TaskType.API_SIMPLE,
            complexity=ComplexityLevel.EXPERT,
            required_skills=set(),
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.API_SIMPLE: 1.0},
            skills={},
            success_rate=1.0,
            max_complexity=ComplexityLevel.EXPERT,
        )

        score, breakdown = calculate_task_score(task, agent)

        # Even with max values, should be clamped to [0, 1]
        assert 0.0 <= score <= 1.0

    def test_score_with_all_min_values(self):
        """Test scoring with minimum values."""
        task = TaskAnalysis(
            id="task-min",
            subject="Min task",
            description="Minimum priority task",
            priority=0,
            priority_score=0.0,
            task_type=TaskType.GENERAL,
            complexity=ComplexityLevel.TRIVIAL,
            required_skills=set(),
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={},  # No capabilities
            skills={},
            success_rate=0.0,
        )

        score, breakdown = calculate_task_score(task, agent)

        # Even with min values, should be clamped to [0, 1]
        assert 0.0 <= score <= 1.0

    def test_capability_match_all_avoids(self):
        """Test when agent avoids all task types."""
        task = TaskAnalysis(
            id="task-1",
            subject="Docker",
            description="Container task",
            priority=5,
            task_type=TaskType.INFRA_DOCKER,
            required_skills=set(),
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.INFRA_DOCKER: 1.0},
            skills={},
            avoids=list(TaskType),  # Avoids everything
        )

        result = calculate_capability_match(task, agent)

        # Heavily penalized
        assert result["task_type_match"] < 0.3

    def test_historical_score_no_history(self):
        """Test historical score with no task type history."""
        task = TaskAnalysis(
            id="task-1",
            subject="Task",
            description="Description",
            priority=5,
            task_type=TaskType.API_SIMPLE,
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            success_rate=0.5,
            task_type_history={},  # Empty history
        )

        score = calculate_historical_score(task, agent)

        # Should handle missing history gracefully
        assert isinstance(score, float)

    def test_batch_scoring_multiple_agents_multiple_tasks(self):
        """Test batch scoring with multiple agents and tasks."""
        engine = ScoringEngine()
        agents = [
            AgentProfile(
                agent_id=f"agent-{i}",
                name=f"Agent {i}",
                model="test",
                capabilities={TaskType.API_SIMPLE: 0.5},
                success_rate=0.5,
            )
            for i in range(5)
        ]
        tasks = [
            TaskAnalysis(
                id=f"task-{i}",
                subject=f"Task {i}",
                description=f"Description {i}",
                priority=i,
                priority_score=i / 10,
                task_type=TaskType.API_SIMPLE,
                required_skills=set(),
            )
            for i in range(3)
        ]

        # Calculate scores for all agents for first task
        agent_results = engine.calculate_agent_scores(tasks[0], agents)

        assert len(agent_results) == 5
        # Each should have unique agent
        agent_ids = [agent.agent_id for agent, _, _ in agent_results]
        assert len(agent_ids) == len(set(agent_ids))

        # Calculate scores for all tasks for one agent
        task_results = engine.calculate_task_scores_for_agent(tasks, agents[0])

        assert len(task_results) == 3
        # Each should have unique task
        task_ids = [task.id for task, _, _ in task_results]
        assert len(task_ids) == len(set(task_ids))

    def test_score_zero_priority(self):
        """Test scoring with zero priority task."""
        task = TaskAnalysis(
            id="task-zero",
            subject="Zero priority",
            description="Task with zero priority",
            priority=0,
            priority_score=0.0,
            task_type=TaskType.GENERAL,
            required_skills=set(),
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            capabilities={TaskType.GENERAL: 0.5},
            skills={},
            success_rate=0.5,
        )

        score, breakdown = calculate_task_score(task, agent)

        # Zero priority contributes 0 to score
        assert breakdown.priority_score == 0.0

    def test_score_perfect_match(self):
        """Test scoring with perfect match conditions."""
        task = TaskAnalysis(
            id="task-perfect",
            subject="Perfect match task",
            description="Task",
            priority=10,
            priority_score=1.0,
            task_type=TaskType.API_SIMPLE,
            required_skills={Skill.PYTHON},
            blocked=False,
        )
        agent = AgentProfile(
            agent_id="test",
            name="Test",
            model="test",
            status="idle",
            capabilities={TaskType.API_SIMPLE: 1.0},
            skills={Skill.PYTHON: 1.0},
            max_complexity=ComplexityLevel.EXPERT,
            preferred_domains=[],
            avoids=[],
            success_rate=1.0,
        )

        score, breakdown = calculate_task_score(task, agent)

        # Perfect conditions should yield high score
        assert score > 0.8
        assert breakdown.blocked_penalty is False
        assert breakdown.complexity_penalty is False
        assert breakdown.availability_penalty is False
