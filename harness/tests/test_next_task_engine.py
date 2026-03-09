"""
TDD Tests for the Next Task Engine.

Tests the scoring algorithm and API endpoint defined in:
  docs/architecture/NEXT_TASK_ENGINE_DESIGN.md

These tests are written BEFORE the implementation (TDD).
The engine module will live at: forge_harness/next_task_engine.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Enums & data classes mirroring the design doc.
# These will be replaced by imports once the engine module exists.
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    API_SIMPLE = "api:simple"
    API_STATEFUL = "api:stateful"
    API_BATCH = "api:batch"
    API_EXTERNAL = "api:external"
    FRONTEND_COMPONENT = "frontend:component"
    FRONTEND_PAGE = "frontend:page"
    FRONTEND_STYLES = "frontend:styles"
    FRONTEND_STATE = "frontend:state"
    TEST_UNIT = "test:unit"
    TEST_INTEGRATION = "test:integration"
    TEST_E2E = "test:e2e"
    INFRA_DEPLOY = "infra:deploy"
    INFRA_CI_CD = "infra:ci_cd"
    INFRA_DOCKER = "infra:docker"
    DOCS_API = "docs:api"
    DOCS_USER = "docs:user"
    DOCS_ARCHITECTURE = "docs:architecture"
    RESEARCH_TECHNICAL = "research:technical"
    RESEARCH_MARKET = "research:market"
    REFACTOR_CLEANUP = "refactor:cleanup"
    REFACTOR_OPTIMIZE = "refactor:optimize"
    REFACTOR_MIGRATE = "refactor:migrate"
    GENERAL = "general"


class ComplexityLevel(int, Enum):
    TRIVIAL = 1
    SIMPLE = 2
    MODERATE = 3
    COMPLEX = 4
    EXPERT = 5


class Skill(str, Enum):
    PYTHON = "python"
    REACT = "react"
    TYPESCRIPT = "typescript"
    FASTAPI = "fastapi"
    JWT = "jwt"
    AUTHENTICATION = "authentication"
    CSS = "css"
    TESTING = "testing"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    DOCUMENTATION = "documentation"


@dataclass
class TaskTypeHistory:
    task_type: TaskType
    tasks_completed: int = 0
    tasks_failed: int = 0
    success_rate: float = 0.5
    avg_duration: timedelta = field(default_factory=lambda: timedelta(hours=2))
    avg_quality_score: float = 0.7
    last_completed: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )


@dataclass
class TaskAnalysis:
    id: str
    subject: str
    description: str = ""
    priority: int = 5
    domain: str | None = None
    project: str | None = None
    status: str = "pending"
    task_type: TaskType = TaskType.GENERAL
    complexity: ComplexityLevel = ComplexityLevel.MODERATE
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    blocked: bool = False
    estimated_duration: timedelta | None = None
    required_skills: set[Skill] = field(default_factory=set)
    priority_score: float = 0.5
    capability_match_score: float | None = None
    historical_score: float | None = None
    final_score: float | None = None
    recommended_agent: str | None = None


@dataclass
class AgentProfile:
    agent_id: str
    name: str = ""
    model: str = ""
    capabilities: dict[TaskType, float] = field(default_factory=dict)
    skills: dict[Skill, float] = field(default_factory=dict)
    max_complexity: ComplexityLevel = ComplexityLevel.COMPLEX
    preferred_domains: list[str] = field(default_factory=list)
    avoids: list[TaskType] = field(default_factory=list)
    current_task_id: str | None = None
    status: str = "idle"
    last_activity: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )
    total_tasks_completed: int = 0
    success_rate: float = 0.8
    avg_task_duration: timedelta = field(default_factory=lambda: timedelta(hours=2))
    avg_quality_score: float = 0.8
    task_type_history: dict[TaskType, TaskTypeHistory] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure-function implementations of the algorithms from the design doc.
# These mirror the spec exactly and will be replaced by real imports.
# ---------------------------------------------------------------------------

def calculate_capability_match(task: TaskAnalysis, agent: AgentProfile) -> float:
    task_type_score = agent.capabilities.get(task.task_type, 0.0)
    if task.required_skills:
        skill_scores = [agent.skills.get(s, 0.0) for s in task.required_skills]
        skills_score = sum(skill_scores) / len(skill_scores)
    else:
        skills_score = 0.5
    domain_score = 1.0 if (task.domain and task.domain in agent.preferred_domains) else 0.5
    if task.task_type in agent.avoids:
        task_type_score *= 0.2
    return 0.7 * task_type_score + 0.2 * skills_score + 0.1 * domain_score


def calculate_historical_score(
    task: TaskAnalysis,
    agent: AgentProfile,
    recent_scores: list[float] | None = None,
) -> float:
    success_rate = agent.success_rate
    if task.task_type in agent.task_type_history:
        h = agent.task_type_history[task.task_type]
        type_performance = (h.success_rate + h.avg_quality_score) / 2
    else:
        type_performance = 0.3
    if recent_scores:
        trend = sum(recent_scores) / len(recent_scores)
    else:
        trend = 0.5
    return 0.4 * success_rate + 0.4 * type_performance + 0.2 * trend


def calculate_task_score(
    task: TaskAnalysis,
    agent: AgentProfile,
    time_decay: float = 0.1,
    recent_scores: list[float] | None = None,
) -> float:
    priority_score = task.priority_score
    capability_score = calculate_capability_match(task, agent)
    historical_score = calculate_historical_score(task, agent, recent_scores)

    final_score = 0.4 * priority_score + 0.4 * capability_score + 0.2 * historical_score

    if task.blocked:
        final_score *= 0.1
    if task.complexity.value > agent.max_complexity.value:
        final_score *= 0.3
    if agent.status != "idle":
        final_score *= 0.5

    if task.task_type in agent.task_type_history:
        h = agent.task_type_history[task.task_type]
        days_since = (datetime.now(tz=UTC) - h.last_completed).days
        recency_boost = math.exp(-time_decay * days_since)
        final_score *= 1 + 0.1 * recency_boost

    return max(0.0, min(1.0, final_score))


# ===================================================================
# SCORING ALGORITHM TESTS
# ===================================================================


class TestCalculateCapabilityMatch:
    """Tests for the capability-match sub-score (design §Capability Match)."""

    def test_perfect_match(self):
        task = TaskAnalysis(
            id="t1",
            subject="Add auth endpoint",
            task_type=TaskType.API_STATEFUL,
            required_skills={Skill.PYTHON, Skill.FASTAPI},
        )
        agent = AgentProfile(
            agent_id="forge:opencode",
            capabilities={TaskType.API_STATEFUL: 1.0},
            skills={Skill.PYTHON: 1.0, Skill.FASTAPI: 1.0},
            preferred_domains=["brandfocus-ai"],
        )
        task.domain = "brandfocus-ai"

        score = calculate_capability_match(task, agent)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_no_matching_capabilities(self):
        task = TaskAnalysis(
            id="t2",
            subject="Build React page",
            task_type=TaskType.FRONTEND_PAGE,
            required_skills={Skill.REACT, Skill.TYPESCRIPT},
        )
        agent = AgentProfile(
            agent_id="forge:opencode",
            capabilities={},
            skills={},
        )
        score = calculate_capability_match(task, agent)
        assert score == pytest.approx(0.05, abs=0.01)

    def test_avoided_task_type_penalty(self):
        task = TaskAnalysis(
            id="t3",
            subject="Style buttons",
            task_type=TaskType.FRONTEND_STYLES,
        )
        agent = AgentProfile(
            agent_id="forge:backend",
            capabilities={TaskType.FRONTEND_STYLES: 0.8},
            avoids=[TaskType.FRONTEND_STYLES],
        )
        normal_agent = AgentProfile(
            agent_id="forge:frontend",
            capabilities={TaskType.FRONTEND_STYLES: 0.8},
        )
        score_avoided = calculate_capability_match(task, agent)
        score_normal = calculate_capability_match(task, normal_agent)
        assert score_avoided < score_normal

    def test_domain_preference_boost(self):
        task = TaskAnalysis(
            id="t4",
            subject="Fix API",
            task_type=TaskType.API_SIMPLE,
            domain="codeswiftr-com",
        )
        preferred = AgentProfile(
            agent_id="a1",
            capabilities={TaskType.API_SIMPLE: 0.8},
            preferred_domains=["codeswiftr-com"],
        )
        neutral = AgentProfile(
            agent_id="a2",
            capabilities={TaskType.API_SIMPLE: 0.8},
        )
        assert calculate_capability_match(task, preferred) > calculate_capability_match(
            task, neutral
        )

    def test_no_required_skills_gives_neutral(self):
        task = TaskAnalysis(id="t5", subject="General task")
        agent = AgentProfile(agent_id="a", capabilities={TaskType.GENERAL: 0.6})
        score = calculate_capability_match(task, agent)
        # skills_score should default to 0.5
        expected = 0.7 * 0.6 + 0.2 * 0.5 + 0.1 * 0.5
        assert score == pytest.approx(expected, abs=0.01)


class TestCalculateHistoricalScore:
    """Tests for the historical-performance sub-score (design §Historical)."""

    def test_with_task_type_history(self):
        task = TaskAnalysis(id="t1", subject="x", task_type=TaskType.API_STATEFUL)
        agent = AgentProfile(
            agent_id="a",
            success_rate=0.9,
            task_type_history={
                TaskType.API_STATEFUL: TaskTypeHistory(
                    task_type=TaskType.API_STATEFUL,
                    success_rate=0.95,
                    avg_quality_score=0.85,
                )
            },
        )
        score = calculate_historical_score(task, agent)
        # 0.4*0.9 + 0.4*((0.95+0.85)/2) + 0.2*0.5 = 0.36 + 0.36 + 0.1 = 0.82
        assert score == pytest.approx(0.82, abs=0.01)

    def test_cold_start_penalty(self):
        task = TaskAnalysis(id="t2", subject="x", task_type=TaskType.INFRA_DOCKER)
        agent = AgentProfile(agent_id="a", success_rate=0.8)
        score = calculate_historical_score(task, agent)
        # type_performance = 0.3 (cold start)
        expected = 0.4 * 0.8 + 0.4 * 0.3 + 0.2 * 0.5
        assert score == pytest.approx(expected, abs=0.01)

    def test_recent_scores_override_neutral(self):
        task = TaskAnalysis(id="t3", subject="x")
        agent = AgentProfile(agent_id="a", success_rate=0.8)
        recent = [0.9, 0.95, 0.85, 0.9, 0.92]
        score = calculate_historical_score(task, agent, recent_scores=recent)
        trend = sum(recent) / len(recent)
        expected = 0.4 * 0.8 + 0.4 * 0.3 + 0.2 * trend
        assert score == pytest.approx(expected, abs=0.01)


class TestCalculateTaskScore:
    """Tests for the full multi-factor scoring (design §Scoring Algorithm)."""

    def _idle_agent(self, **overrides) -> AgentProfile:
        defaults = dict(
            agent_id="forge:opencode",
            capabilities={TaskType.API_STATEFUL: 0.9},
            skills={Skill.PYTHON: 0.9, Skill.FASTAPI: 0.85},
            success_rate=0.85,
            status="idle",
            max_complexity=ComplexityLevel.COMPLEX,
        )
        defaults.update(overrides)
        return AgentProfile(**defaults)

    def _standard_task(self, **overrides) -> TaskAnalysis:
        defaults = dict(
            id="task-1",
            subject="Add user auth",
            priority=8,
            task_type=TaskType.API_STATEFUL,
            complexity=ComplexityLevel.MODERATE,
            priority_score=0.8,
            blocked=False,
            required_skills={Skill.PYTHON, Skill.FASTAPI},
        )
        defaults.update(overrides)
        return TaskAnalysis(**defaults)

    def test_score_in_unit_range(self):
        score = calculate_task_score(self._standard_task(), self._idle_agent())
        assert 0.0 <= score <= 1.0

    def test_high_match_produces_high_score(self):
        score = calculate_task_score(self._standard_task(), self._idle_agent())
        assert score > 0.5

    def test_blocked_task_heavy_penalty(self):
        normal = calculate_task_score(self._standard_task(), self._idle_agent())
        blocked = calculate_task_score(
            self._standard_task(blocked=True), self._idle_agent()
        )
        assert blocked < normal * 0.2

    def test_complexity_exceeds_agent_limit(self):
        agent = self._idle_agent(max_complexity=ComplexityLevel.SIMPLE)
        task = self._standard_task(complexity=ComplexityLevel.COMPLEX)
        score = calculate_task_score(task, agent)
        normal = calculate_task_score(self._standard_task(), self._idle_agent())
        assert score < normal * 0.5

    def test_busy_agent_penalty(self):
        idle_score = calculate_task_score(self._standard_task(), self._idle_agent())
        busy_score = calculate_task_score(
            self._standard_task(), self._idle_agent(status="busy")
        )
        assert busy_score == pytest.approx(idle_score * 0.5, abs=0.05)

    def test_recency_boost_for_recent_completion(self):
        recent_history = TaskTypeHistory(
            task_type=TaskType.API_STATEFUL,
            success_rate=0.9,
            avg_quality_score=0.9,
            last_completed=datetime.now(tz=UTC) - timedelta(hours=1),
        )
        agent = self._idle_agent(
            task_type_history={TaskType.API_STATEFUL: recent_history}
        )
        score_recent = calculate_task_score(self._standard_task(), agent)

        stale_history = TaskTypeHistory(
            task_type=TaskType.API_STATEFUL,
            success_rate=0.9,
            avg_quality_score=0.9,
            last_completed=datetime.now(tz=UTC) - timedelta(days=90),
        )
        agent_stale = self._idle_agent(
            task_type_history={TaskType.API_STATEFUL: stale_history}
        )
        score_stale = calculate_task_score(self._standard_task(), agent_stale)
        assert score_recent >= score_stale

    def test_zero_priority_gives_low_score(self):
        task = self._standard_task(priority_score=0.0)
        score = calculate_task_score(task, self._idle_agent())
        full = calculate_task_score(self._standard_task(), self._idle_agent())
        assert score < full

    def test_all_penalties_stacked(self):
        agent = self._idle_agent(
            status="busy", max_complexity=ComplexityLevel.TRIVIAL
        )
        task = self._standard_task(blocked=True, complexity=ComplexityLevel.EXPERT)
        score = calculate_task_score(task, agent)
        assert score < 0.05


class TestTaskRanking:
    """Tests that multiple tasks are ranked correctly for an agent."""

    def test_higher_priority_ranks_first(self):
        agent = AgentProfile(
            agent_id="a",
            capabilities={TaskType.API_SIMPLE: 0.8, TaskType.DOCS_USER: 0.8},
            success_rate=0.8,
            status="idle",
        )
        high = TaskAnalysis(
            id="high", subject="Important", priority_score=0.9, task_type=TaskType.API_SIMPLE
        )
        low = TaskAnalysis(
            id="low", subject="Minor", priority_score=0.2, task_type=TaskType.API_SIMPLE
        )
        tasks = sorted(
            [low, high], key=lambda t: calculate_task_score(t, agent), reverse=True
        )
        assert tasks[0].id == "high"

    def test_blocked_task_sorts_last(self):
        agent = AgentProfile(
            agent_id="a",
            capabilities={TaskType.GENERAL: 0.7},
            success_rate=0.8,
            status="idle",
        )
        ok = TaskAnalysis(id="ok", subject="Ready", priority_score=0.5)
        blocked = TaskAnalysis(id="blocked", subject="Waiting", priority_score=0.9, blocked=True)
        tasks = sorted(
            [blocked, ok], key=lambda t: calculate_task_score(t, agent), reverse=True
        )
        assert tasks[0].id == "ok"

    def test_best_agent_selected_for_research_task(self):
        research = TaskAnalysis(
            id="r1",
            subject="Research real-time sync",
            task_type=TaskType.RESEARCH_TECHNICAL,
            priority_score=0.7,
            required_skills={Skill.RESEARCH, Skill.ANALYSIS},
        )
        gemini = AgentProfile(
            agent_id="forge:gemini",
            capabilities={TaskType.RESEARCH_TECHNICAL: 0.95},
            skills={Skill.RESEARCH: 0.95, Skill.ANALYSIS: 0.9},
            success_rate=0.9,
            status="idle",
        )
        opencode = AgentProfile(
            agent_id="forge:opencode",
            capabilities={TaskType.RESEARCH_TECHNICAL: 0.4},
            skills={Skill.RESEARCH: 0.3, Skill.ANALYSIS: 0.4},
            success_rate=0.85,
            status="idle",
        )
        assert calculate_task_score(research, gemini) > calculate_task_score(
            research, opencode
        )


# ===================================================================
# TASK TYPE DETECTION TESTS
# ===================================================================


class TestTaskTypeDetection:
    """Tests for keyword-based task type classification (design §Task Type Detection)."""

    @pytest.mark.parametrize(
        "subject,expected_type",
        [
            ("Add GET endpoint to list users", TaskType.API_SIMPLE),
            ("Create user with database migration", TaskType.API_STATEFUL),
            ("Batch import CSV data", TaskType.API_BATCH),
            ("Integrate Stripe webhook", TaskType.API_EXTERNAL),
            ("Build login form component", TaskType.FRONTEND_COMPONENT),
            ("Create dashboard page route", TaskType.FRONTEND_PAGE),
            ("Add unit test for auth module", TaskType.TEST_UNIT),
            ("Write end-to-end playwright test", TaskType.TEST_E2E),
            ("Deploy to Railway production", TaskType.INFRA_DEPLOY),
            ("Create Dockerfile for backend", TaskType.INFRA_DOCKER),
            ("Write API documentation for openapi", TaskType.DOCS_API),
            ("Research best caching strategies", TaskType.RESEARCH_TECHNICAL),
            ("Refactor and cleanup auth module", TaskType.REFACTOR_CLEANUP),
            ("Optimize database query performance", TaskType.REFACTOR_OPTIMIZE),
        ],
    )
    def test_keyword_detection(self, subject, expected_type):
        """The keyword classifier should map known phrases to correct types."""
        detected = detect_task_type_keywords(subject)
        assert detected == expected_type

    def test_unknown_text_returns_general(self):
        assert detect_task_type_keywords("do the thing") == TaskType.GENERAL


def detect_task_type_keywords(text: str) -> TaskType:
    """Minimal keyword classifier matching the design doc's fast path."""
    text = text.lower()
    keyword_rules: dict[TaskType, list[str]] = {
        TaskType.API_SIMPLE: ["get", "list", "fetch", "basic post", "endpoint"],
        TaskType.API_STATEFUL: ["create", "update", "delete", "state", "database", "migration"],
        TaskType.API_BATCH: ["batch", "bulk", "import", "export", "multi"],
        TaskType.API_EXTERNAL: ["webhook", "external", "api call", "integration"],
        TaskType.FRONTEND_COMPONENT: ["component", "ui element", "button", "form", "card"],
        TaskType.FRONTEND_PAGE: ["page", "route", "view", "screen"],
        TaskType.FRONTEND_STYLES: ["style", "css", "design", "theme", "responsive"],
        TaskType.FRONTEND_STATE: ["redux", "context", "store", "hook"],
        TaskType.TEST_UNIT: ["unit test", "test function", "mock"],
        TaskType.TEST_INTEGRATION: ["integration test", "api test", "endpoint test"],
        TaskType.TEST_E2E: ["e2e", "end-to-end", "playwright", "cypress", "user flow"],
        TaskType.INFRA_DEPLOY: ["deploy", "railway", "vercel", "production"],
        TaskType.INFRA_CI_CD: ["github action", "workflow", "ci cd", "pipeline"],
        TaskType.INFRA_DOCKER: ["docker", "container", "dockerfile"],
        TaskType.DOCS_API: ["api docs", "endpoint documentation", "openapi"],
        TaskType.DOCS_USER: ["user guide", "readme"],
        TaskType.DOCS_ARCHITECTURE: ["architecture", "design doc", "technical spec"],
        TaskType.RESEARCH_TECHNICAL: ["research", "investigate", "explore", "analyze"],
        TaskType.RESEARCH_MARKET: ["market", "competitive"],
        TaskType.REFACTOR_CLEANUP: ["cleanup", "refactor", "organize"],
        TaskType.REFACTOR_OPTIMIZE: ["optimize", "performance", "speed"],
        TaskType.REFACTOR_MIGRATE: ["migrate", "upgrade"],
    }
    best_match: TaskType | None = None
    best_score = 0
    for task_type, keywords in keyword_rules.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_match = task_type
    return best_match if best_match and best_score > 0 else TaskType.GENERAL


# ===================================================================
# COMPLEXITY ESTIMATION TESTS
# ===================================================================


class TestComplexityEstimation:
    """Tests for complexity scoring (design §Complexity Estimation)."""

    def test_short_simple_task_is_trivial(self):
        score = _estimate_complexity_score("fix typo in readme")
        assert score < 1

    def test_complex_keywords_raise_score(self):
        score = _estimate_complexity_score(
            "design a new authentication architecture with database migration "
            "and performance optimization for the distributed security system"
        )
        assert score >= 3.5

    def test_scope_indicators_add_complexity(self):
        base = _estimate_complexity_score("add feature")
        scoped = _estimate_complexity_score("add multiple end-to-end complete features")
        assert scoped > base


def _estimate_complexity_score(text: str) -> float:
    """Subset of the design doc's complexity estimator (no codebase context)."""
    text = text.lower()
    score = 0.0
    word_count = len(text.split())
    if word_count > 100:
        score += 1
    elif word_count > 50:
        score += 0.5
    complex_keywords = [
        "architecture", "migration", "integration", "performance",
        "security", "authentication", "database", "distributed",
        "refactor", "optimize", "scalable",
    ]
    score += sum(1 for kw in complex_keywords if kw in text) * 0.5
    if "multiple" in text or "several" in text or "batch" in text:
        score += 1
    if "end-to-end" in text or "full" in text or "complete" in text:
        score += 1
    return score


# ===================================================================
# API ENDPOINT TESTS
# ===================================================================


class TestRecommendedTasksEndpoint:
    """Tests for GET /api/tasks/recommended (design §API Specification).

    Uses a mock response shape since the endpoint doesn't exist yet.
    When the real endpoint is wired up, swap the mock for a TestClient.
    """

    def _mock_recommendations(
        self,
        tasks: list[dict] | None = None,
        agent_id: str | None = None,
        limit: int = 10,
        include_blocked: bool = False,
    ) -> dict:
        """Simulate the response shape from the design doc."""
        if tasks is None:
            tasks = [
                {
                    "task": {
                        "id": "task-123",
                        "subject": "Add user authentication API endpoint",
                        "description": "Implement JWT-based authentication",
                        "priority": 8,
                        "domain": "brandfocus-ai",
                        "project": "voice-coach",
                        "status": "pending",
                    },
                    "analysis": {
                        "task_type": "api:stateful",
                        "complexity": "moderate",
                        "dependencies": [],
                        "blocked": False,
                        "estimated_duration": "2h 30m",
                        "required_skills": ["PYTHON", "FASTAPI", "JWT", "AUTHENTICATION"],
                    },
                    "scoring": {
                        "priority_score": 0.8,
                        "capability_matches": [
                            {
                                "agent_id": "forge:opencode",
                                "score": 0.92,
                                "capability_score": 0.95,
                                "historical_score": 0.88,
                                "breakdown": {
                                    "task_type_match": 0.95,
                                    "skills_match": 0.90,
                                    "domain_preference": 1.0,
                                },
                            }
                        ],
                        "recommended_agent": "forge:opencode",
                        "recommendation_confidence": 0.92,
                    },
                },
                {
                    "task": {
                        "id": "task-456",
                        "subject": "Create profile card component",
                        "description": "React component for user profile",
                        "priority": 5,
                        "domain": "codeswiftr-com",
                        "project": "interview-simulator",
                        "status": "pending",
                    },
                    "analysis": {
                        "task_type": "frontend:component",
                        "complexity": "simple",
                        "dependencies": [],
                        "blocked": False,
                        "estimated_duration": "1h",
                        "required_skills": ["REACT", "TYPESCRIPT", "CSS"],
                    },
                    "scoring": {
                        "priority_score": 0.5,
                        "capability_matches": [
                            {
                                "agent_id": "forge:codex",
                                "score": 0.88,
                                "capability_score": 0.90,
                                "historical_score": 0.82,
                                "breakdown": {
                                    "task_type_match": 0.90,
                                    "skills_match": 0.85,
                                    "domain_preference": 0.5,
                                },
                            }
                        ],
                        "recommended_agent": "forge:codex",
                        "recommendation_confidence": 0.88,
                    },
                },
            ]

        filtered = tasks
        if agent_id:
            filtered = [
                t
                for t in filtered
                if any(
                    m["agent_id"] == agent_id
                    for m in t["scoring"]["capability_matches"]
                )
            ]
        if not include_blocked:
            filtered = [t for t in filtered if not t["analysis"]["blocked"]]
        filtered = filtered[:limit]

        return {
            "recommendations": filtered,
            "meta": {
                "total_tasks": len(tasks),
                "recommended_count": len(filtered),
                "algorithm_version": "1.0",
                "generated_at": datetime.now(tz=UTC).isoformat(),
            },
        }

    def test_response_structure(self):
        resp = self._mock_recommendations()
        assert "recommendations" in resp
        assert "meta" in resp
        assert isinstance(resp["recommendations"], list)

    def test_recommendation_has_required_fields(self):
        resp = self._mock_recommendations()
        rec = resp["recommendations"][0]
        assert "task" in rec
        assert "analysis" in rec
        assert "scoring" in rec
        assert rec["task"]["id"] == "task-123"
        assert rec["scoring"]["recommended_agent"] == "forge:opencode"

    def test_limit_parameter(self):
        resp = self._mock_recommendations(limit=1)
        assert len(resp["recommendations"]) == 1
        assert resp["meta"]["recommended_count"] == 1

    def test_agent_id_filter(self):
        resp = self._mock_recommendations(agent_id="forge:opencode")
        for rec in resp["recommendations"]:
            agents = [m["agent_id"] for m in rec["scoring"]["capability_matches"]]
            assert "forge:opencode" in agents

    def test_blocked_tasks_excluded_by_default(self):
        tasks = [
            {
                "task": {"id": "blocked-1", "subject": "Blocked", "description": "",
                         "priority": 9, "domain": None, "project": None, "status": "pending"},
                "analysis": {"task_type": "general", "complexity": "simple",
                             "dependencies": ["dep-1"], "blocked": True,
                             "estimated_duration": "1h", "required_skills": []},
                "scoring": {"priority_score": 0.9, "capability_matches": [],
                            "recommended_agent": None, "recommendation_confidence": 0.0},
            }
        ]
        resp = self._mock_recommendations(tasks=tasks, include_blocked=False)
        assert len(resp["recommendations"]) == 0

    def test_blocked_tasks_included_when_requested(self):
        tasks = [
            {
                "task": {"id": "blocked-1", "subject": "Blocked", "description": "",
                         "priority": 9, "domain": None, "project": None, "status": "pending"},
                "analysis": {"task_type": "general", "complexity": "simple",
                             "dependencies": ["dep-1"], "blocked": True,
                             "estimated_duration": "1h", "required_skills": []},
                "scoring": {"priority_score": 0.9, "capability_matches": [],
                            "recommended_agent": None, "recommendation_confidence": 0.0},
            }
        ]
        resp = self._mock_recommendations(tasks=tasks, include_blocked=True)
        assert len(resp["recommendations"]) == 1

    def test_meta_includes_algorithm_version(self):
        resp = self._mock_recommendations()
        assert resp["meta"]["algorithm_version"] == "1.0"

    def test_scoring_breakdown_fields(self):
        resp = self._mock_recommendations()
        match = resp["recommendations"][0]["scoring"]["capability_matches"][0]
        assert "score" in match
        assert "capability_score" in match
        assert "historical_score" in match
        assert "breakdown" in match
        bd = match["breakdown"]
        assert "task_type_match" in bd
        assert "skills_match" in bd
        assert "domain_preference" in bd

    def test_recommendations_sorted_by_score_descending(self):
        resp = self._mock_recommendations()
        scores = [
            r["scoring"]["recommendation_confidence"]
            for r in resp["recommendations"]
        ]
        assert scores == sorted(scores, reverse=True)

    def test_empty_task_list_returns_empty(self):
        resp = self._mock_recommendations(tasks=[])
        assert resp["recommendations"] == []
        assert resp["meta"]["total_tasks"] == 0


# ===================================================================
# DESIGN-DOC SCENARIO TESTS
# ===================================================================


class TestDesignDocScenarios:
    """Validate the three example scenarios from the design doc appendix."""

    def test_scenario_1_api_endpoint_task(self):
        """forge:opencode should outscore forge:codex for an API auth task."""
        task = TaskAnalysis(
            id="s1",
            subject="Add user authentication endpoint with JWT tokens",
            task_type=TaskType.API_STATEFUL,
            complexity=ComplexityLevel.MODERATE,
            priority_score=0.8,
            required_skills={Skill.PYTHON, Skill.FASTAPI, Skill.JWT, Skill.AUTHENTICATION},
        )
        opencode = AgentProfile(
            agent_id="forge:opencode",
            capabilities={TaskType.API_STATEFUL: 0.95},
            skills={
                Skill.PYTHON: 0.95, Skill.FASTAPI: 0.9,
                Skill.JWT: 0.85, Skill.AUTHENTICATION: 0.9,
            },
            success_rate=0.88,
            status="idle",
            task_type_history={
                TaskType.API_STATEFUL: TaskTypeHistory(
                    task_type=TaskType.API_STATEFUL, success_rate=0.9, avg_quality_score=0.86,
                )
            },
        )
        codex = AgentProfile(
            agent_id="forge:codex",
            capabilities={TaskType.API_STATEFUL: 0.5},
            skills={Skill.PYTHON: 0.6, Skill.FASTAPI: 0.4},
            success_rate=0.75,
            status="idle",
        )
        assert calculate_task_score(task, opencode) > calculate_task_score(task, codex)

    def test_scenario_2_frontend_component(self):
        """forge:codex should outscore forge:opencode for a React component."""
        task = TaskAnalysis(
            id="s2",
            subject="Create user profile card component with avatar",
            task_type=TaskType.FRONTEND_COMPONENT,
            complexity=ComplexityLevel.SIMPLE,
            priority_score=0.5,
            required_skills={Skill.TYPESCRIPT, Skill.REACT, Skill.CSS},
        )
        codex = AgentProfile(
            agent_id="forge:codex",
            capabilities={TaskType.FRONTEND_COMPONENT: 0.9},
            skills={Skill.TYPESCRIPT: 0.85, Skill.REACT: 0.9, Skill.CSS: 0.8},
            success_rate=0.82,
            status="idle",
        )
        opencode = AgentProfile(
            agent_id="forge:opencode",
            capabilities={TaskType.FRONTEND_COMPONENT: 0.3},
            skills={Skill.TYPESCRIPT: 0.4, Skill.REACT: 0.3, Skill.CSS: 0.2},
            success_rate=0.85,
            status="idle",
        )
        assert calculate_task_score(task, codex) > calculate_task_score(task, opencode)

    def test_scenario_3_research_task(self):
        """forge:gemini should outscore forge:opencode for research."""
        task = TaskAnalysis(
            id="s3",
            subject="Research best practices for real-time data synchronization",
            task_type=TaskType.RESEARCH_TECHNICAL,
            complexity=ComplexityLevel.MODERATE,
            priority_score=0.7,
            required_skills={Skill.RESEARCH, Skill.ANALYSIS, Skill.DOCUMENTATION},
        )
        gemini = AgentProfile(
            agent_id="forge:gemini",
            capabilities={TaskType.RESEARCH_TECHNICAL: 0.95},
            skills={Skill.RESEARCH: 0.95, Skill.ANALYSIS: 0.9, Skill.DOCUMENTATION: 0.9},
            success_rate=0.92,
            status="idle",
        )
        opencode = AgentProfile(
            agent_id="forge:opencode",
            capabilities={TaskType.RESEARCH_TECHNICAL: 0.4},
            skills={Skill.RESEARCH: 0.3, Skill.ANALYSIS: 0.4, Skill.DOCUMENTATION: 0.5},
            success_rate=0.85,
            status="idle",
        )
        assert calculate_task_score(task, gemini) > calculate_task_score(task, opencode)
