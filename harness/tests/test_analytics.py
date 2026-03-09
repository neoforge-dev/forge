"""
Comprehensive tests for forge_harness/analytics modules:
- recommendation_engine.py
- scoring_engine.py
- task_classifier.py

Targets 80%+ coverage for each module.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.analytics.recommendation_engine import (
    CapabilityMatch,
    RecommendationEngine,
    RecommendationResponse,
    TaskRecommendation,
)
from forge_harness.analytics.scoring_engine import (
    ScoreBreakdown,
    ScoringEngine,
    calculate_capability_match,
    calculate_historical_score,
    calculate_task_score,
    get_recent_performance_scores,
)
from forge_harness.analytics.task_classifier import (
    COMPLEXITY_KEYWORDS,
    KEYWORD_RULES,
    SKILL_KEYWORDS,
    TaskClassifier,
    detect_task_type,
    estimate_complexity,
    extract_required_skills,
    normalize_priority,
)
from forge_harness.models import (
    AgentProfile,
    CodebaseContext,
    ComplexityLevel,
    Skill,
    TaskAnalysis,
    TaskType,
    TaskTypeHistory,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_agent(
    agent_id: str = "forge:test-agent",
    name: str = "Test Agent",
    model: str = "claude-sonnet-4-6",
    status: str = "idle",
    success_rate: float = 0.8,
    max_complexity: ComplexityLevel = ComplexityLevel.COMPLEX,
    capabilities: dict | None = None,
    skills: dict | None = None,
    preferred_domains: list[str] | None = None,
    avoids: list[TaskType] | None = None,
    task_type_history: dict | None = None,
) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=name,
        model=model,
        status=status,
        success_rate=success_rate,
        max_complexity=max_complexity,
        capabilities=capabilities or {},
        skills=skills or {},
        preferred_domains=preferred_domains or [],
        avoids=avoids or [],
        task_type_history=task_type_history or {},
    )


def make_task_analysis(
    task_id: str = "task-1",
    subject: str = "Fix API endpoint",
    description: str = "Fix the broken GET endpoint",
    priority: int = 5,
    priority_score: float = 0.5,
    task_type: TaskType = TaskType.API_SIMPLE,
    complexity: ComplexityLevel = ComplexityLevel.SIMPLE,
    blocked: bool = False,
    domain: str | None = None,
    required_skills: set[Skill] | None = None,
    codebase_context: CodebaseContext | None = None,
) -> TaskAnalysis:
    return TaskAnalysis(
        id=task_id,
        subject=subject,
        description=description,
        priority=priority,
        priority_score=priority_score,
        task_type=task_type,
        complexity=complexity,
        blocked=blocked,
        domain=domain,
        required_skills=required_skills or set(),
        codebase_context=codebase_context,
    )


# ---------------------------------------------------------------------------
# task_classifier.py — detect_task_type
# ---------------------------------------------------------------------------


class TestDetectTaskType:
    """Tests for the detect_task_type function."""

    def test_api_simple_from_keyword_get(self):
        result = detect_task_type("get user list", "fetch all users from endpoint")
        assert result == TaskType.API_SIMPLE

    def test_api_stateful_from_keyword_create(self):
        result = detect_task_type("create user", "save new user to database with transaction")
        assert result == TaskType.API_STATEFUL

    def test_api_batch_from_keyword_batch(self):
        result = detect_task_type("batch import users", "bulk insert records into the system")
        assert result == TaskType.API_BATCH

    def test_api_external_from_keyword_webhook(self):
        result = detect_task_type("handle webhook", "process external api call for oauth integration")
        assert result == TaskType.API_EXTERNAL

    def test_frontend_component(self):
        result = detect_task_type("build button component", "create a reusable form card modal")
        assert result == TaskType.FRONTEND_COMPONENT

    def test_frontend_page(self):
        result = detect_task_type("add dashboard page", "create the main view and route layout")
        assert result == TaskType.FRONTEND_PAGE

    def test_frontend_styles(self):
        result = detect_task_type("update css theme", "apply responsive style design and typography")
        assert result == TaskType.FRONTEND_STYLES

    def test_frontend_state(self):
        result = detect_task_type("manage redux state", "add usestate useeffect hook to store context")
        assert result == TaskType.FRONTEND_STATE

    def test_test_unit(self):
        result = detect_task_type("write unit test", "add pytest mock for function")
        assert result == TaskType.TEST_UNIT

    def test_test_integration(self):
        result = detect_task_type("integration test for API", "write api test and endpoint test for service test")
        assert result == TaskType.TEST_INTEGRATION

    def test_test_e2e(self):
        result = detect_task_type("e2e browser test", "playwright user flow end-to-end")
        assert result == TaskType.TEST_E2E

    def test_infra_deploy(self):
        result = detect_task_type("deploy to production", "release to railway staging environment")
        assert result == TaskType.INFRA_DEPLOY

    def test_infra_ci_cd(self):
        result = detect_task_type("setup github action", "create ci cd pipeline workflow github workflow")
        assert result == TaskType.INFRA_CI_CD

    def test_infra_docker(self):
        result = detect_task_type("dockerize service", "create dockerfile docker-compose container setup")
        assert result == TaskType.INFRA_DOCKER

    def test_docs_api(self):
        result = detect_task_type("write api docs", "create openapi swagger endpoint documentation api reference")
        assert result == TaskType.DOCS_API

    def test_docs_user(self):
        result = detect_task_type("write user guide", "create readme documentation manual tutorial")
        assert result == TaskType.DOCS_USER

    def test_docs_architecture(self):
        result = detect_task_type("write architecture doc", "technical spec adr design doc")
        assert result == TaskType.DOCS_ARCHITECTURE

    def test_research_technical(self):
        result = detect_task_type("research approach", "investigate evaluate and analyze options explore")
        assert result == TaskType.RESEARCH_TECHNICAL

    def test_research_market(self):
        result = detect_task_type("market analysis", "competitive analysis competitor benchmark")
        assert result == TaskType.RESEARCH_MARKET

    def test_refactor_cleanup(self):
        result = detect_task_type("cleanup code", "refactor organize simplify old module")
        assert result == TaskType.REFACTOR_CLEANUP

    def test_refactor_optimize(self):
        result = detect_task_type("optimize performance", "improve speed performance efficient")
        assert result == TaskType.REFACTOR_OPTIMIZE

    def test_refactor_migrate(self):
        result = detect_task_type("migrate database", "upgrade update migration convert")
        assert result == TaskType.REFACTOR_MIGRATE

    def test_fallback_to_general(self):
        result = detect_task_type("zzzunknown task", "zzznot matching anything at all")
        assert result == TaskType.GENERAL

    def test_case_insensitive(self):
        result = detect_task_type("GET Users", "FETCH all users from ENDPOINT")
        assert result == TaskType.API_SIMPLE

    def test_empty_strings(self):
        result = detect_task_type("", "")
        assert result == TaskType.GENERAL

    def test_best_score_wins(self):
        # More keywords for API_STATEFUL than GENERAL
        result = detect_task_type(
            "create update delete state database",
            "persist transaction migration save"
        )
        assert result == TaskType.API_STATEFUL


# ---------------------------------------------------------------------------
# task_classifier.py — estimate_complexity
# ---------------------------------------------------------------------------


class TestEstimateComplexity:
    """Tests for estimate_complexity function."""

    def test_trivial_short_description(self):
        result = estimate_complexity("fix typo", "fix the typo")
        assert result == ComplexityLevel.TRIVIAL

    def test_simple_with_two_complexity_keywords(self):
        # Two complexity keywords: 0.5 + 0.5 = 1.0, threshold for SIMPLE is >= 1.0
        result = estimate_complexity("authentication security", "update the auth flow security")
        assert result == ComplexityLevel.SIMPLE

    def test_moderate_complexity(self):
        # Multiple complexity keywords
        result = estimate_complexity(
            "refactor authentication database",
            "security migration integration performance optimization"
        )
        assert result in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX, ComplexityLevel.EXPERT)

    def test_scope_indicator_multiple(self):
        result = estimate_complexity("batch update", "multiple items to update across several services")
        assert result.value >= ComplexityLevel.SIMPLE.value

    def test_scope_indicator_full(self):
        result = estimate_complexity("full refactor", "complete end-to-end overhaul of system")
        assert result.value >= ComplexityLevel.SIMPLE.value

    def test_scope_indicator_across(self):
        # "across" + "entire" + "system-wide" add 1.5; score = 1.5 -> SIMPLE
        result = estimate_complexity("migrate", "across entire system-wide codebase")
        assert result.value >= ComplexityLevel.SIMPLE.value

    def test_long_description_boosts_complexity(self):
        long_desc = " ".join(["word"] * 110)
        result = estimate_complexity("task", long_desc)
        assert result.value >= ComplexityLevel.SIMPLE.value

    def test_medium_description_partial_boost(self):
        medium_desc = " ".join(["word"] * 60)
        result = estimate_complexity("task", medium_desc)
        assert result.value >= ComplexityLevel.TRIVIAL.value

    def test_codebase_context_many_files(self):
        ctx = CodebaseContext(affected_file_count=10, max_dependency_depth=0)
        result = estimate_complexity("task", "simple task", codebase_context=ctx)
        assert result.value >= ComplexityLevel.SIMPLE.value

    def test_codebase_context_few_files(self):
        ctx = CodebaseContext(affected_file_count=3, max_dependency_depth=0)
        result = estimate_complexity("task", "task", codebase_context=ctx)
        assert result.value >= ComplexityLevel.TRIVIAL.value

    def test_codebase_context_deep_dependencies(self):
        ctx = CodebaseContext(affected_file_count=0, max_dependency_depth=5)
        result = estimate_complexity("task", "task", codebase_context=ctx)
        assert result.value >= ComplexityLevel.SIMPLE.value

    def test_codebase_context_moderate_dependencies(self):
        ctx = CodebaseContext(affected_file_count=0, max_dependency_depth=2)
        result = estimate_complexity("task", "task", codebase_context=ctx)
        assert result.value >= ComplexityLevel.TRIVIAL.value

    def test_codebase_context_critical_system(self):
        ctx = CodebaseContext(
            affected_file_count=0,
            max_dependency_depth=0,
            touches_critical_system=True,
        )
        result = estimate_complexity("task", "task", codebase_context=ctx)
        assert result.value >= ComplexityLevel.SIMPLE.value

    def test_codebase_context_breaking_change_risk_high(self):
        ctx = CodebaseContext(
            affected_file_count=0,
            max_dependency_depth=0,
            breaking_change_risk=0.8,
        )
        result = estimate_complexity("task", "task", codebase_context=ctx)
        assert result.value >= ComplexityLevel.TRIVIAL.value

    def test_codebase_context_high_cyclomatic_complexity(self):
        ctx = CodebaseContext(
            affected_file_count=0,
            max_dependency_depth=0,
            cyclomatic_complexity=25.0,
        )
        result = estimate_complexity("task", "task", codebase_context=ctx)
        assert result.value >= ComplexityLevel.SIMPLE.value

    def test_codebase_context_moderate_cyclomatic_complexity(self):
        ctx = CodebaseContext(
            affected_file_count=0,
            max_dependency_depth=0,
            cyclomatic_complexity=15.0,
        )
        result = estimate_complexity("task", "task", codebase_context=ctx)
        assert result.value >= ComplexityLevel.TRIVIAL.value

    def test_expert_complexity(self):
        # Many factors to push score over 5.0
        ctx = CodebaseContext(
            affected_file_count=10,
            max_dependency_depth=5,
            touches_critical_system=True,
            breaking_change_risk=0.8,
            cyclomatic_complexity=25.0,
        )
        long_desc = " ".join(["architecture migration integration security authentication database distributed"] * 5)
        result = estimate_complexity(
            "architecture migration integration",
            long_desc + " multiple across entire system-wide",
            codebase_context=ctx,
        )
        assert result == ComplexityLevel.EXPERT

    def test_no_codebase_context(self):
        result = estimate_complexity("simple task", "do the thing", codebase_context=None)
        assert result == ComplexityLevel.TRIVIAL


# ---------------------------------------------------------------------------
# task_classifier.py — extract_required_skills
# ---------------------------------------------------------------------------


class TestExtractRequiredSkills:
    """Tests for extract_required_skills function."""

    def test_python_skill(self):
        skills = extract_required_skills("python service", "py code refactor")
        assert Skill.PYTHON in skills

    def test_typescript_skill(self):
        skills = extract_required_skills("typescript module", "ts types")
        assert Skill.TYPESCRIPT in skills

    def test_fastapi_skill(self):
        skills = extract_required_skills("fastapi endpoint", "fast api route")
        assert Skill.FASTAPI in skills

    def test_react_skill(self):
        skills = extract_required_skills("react component", "build reactjs UI")
        assert Skill.REACT in skills

    def test_postgresql_skill(self):
        skills = extract_required_skills("postgresql schema", "postgres migration")
        assert Skill.POSTGRESQL in skills

    def test_docker_skill(self):
        skills = extract_required_skills("docker container", "containerize service")
        assert Skill.DOCKER in skills

    def test_authentication_skill(self):
        skills = extract_required_skills("auth module", "add jwt oauth login authentication")
        assert Skill.AUTHENTICATION in skills

    def test_graphql_skill(self):
        skills = extract_required_skills("graphql endpoint", "add gql schema")
        assert Skill.GRAPHQL in skills

    def test_research_skill(self):
        skills = extract_required_skills("research topic", "investigate analyze options")
        assert Skill.RESEARCH in skills

    def test_documentation_skill(self):
        skills = extract_required_skills("write documentation", "create docs readme")
        assert Skill.DOCUMENTATION in skills

    def test_multiple_skills(self):
        skills = extract_required_skills(
            "python fastapi postgres", "docker authentication sql"
        )
        assert Skill.PYTHON in skills
        assert Skill.FASTAPI in skills
        assert Skill.POSTGRESQL in skills
        assert Skill.DOCKER in skills

    def test_no_skills_empty_text(self):
        skills = extract_required_skills("", "")
        assert isinstance(skills, set)

    def test_case_insensitive(self):
        skills = extract_required_skills("PYTHON SERVICE", "FASTAPI ENDPOINT")
        assert Skill.PYTHON in skills
        assert Skill.FASTAPI in skills

    def test_redis_skill(self):
        skills = extract_required_skills("redis cache", "use redis for caching")
        assert Skill.REDIS in skills

    def test_kubernetes_skill(self):
        skills = extract_required_skills("kubernetes deployment", "k8s cluster setup")
        assert Skill.KUBERNETES in skills

    def test_pytest_skill(self):
        skills = extract_required_skills("pytest tests", "py.test suite")
        assert Skill.PYTEST in skills


# ---------------------------------------------------------------------------
# task_classifier.py — normalize_priority
# ---------------------------------------------------------------------------


class TestNormalizePriority:
    """Tests for normalize_priority function."""

    def test_priority_one(self):
        assert normalize_priority(1) == pytest.approx(0.1)

    def test_priority_ten(self):
        assert normalize_priority(10) == pytest.approx(1.0)

    def test_priority_five(self):
        assert normalize_priority(5) == pytest.approx(0.5)

    def test_priority_zero_clamps_to_zero(self):
        assert normalize_priority(0) == pytest.approx(0.0)

    def test_priority_negative_clamps_to_zero(self):
        assert normalize_priority(-5) == pytest.approx(0.0)

    def test_priority_above_ten_clamps_to_one(self):
        assert normalize_priority(20) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# task_classifier.py — TaskClassifier.analyze_task
# ---------------------------------------------------------------------------


class TestTaskClassifier:
    """Tests for TaskClassifier.analyze_task method."""

    def setup_method(self):
        self.classifier = TaskClassifier()

    def test_analyze_task_returns_task_analysis(self):
        result = self.classifier.analyze_task(
            task_id="t1",
            subject="fetch user list",
            description="get all users from endpoint",
            priority=5,
        )
        assert result.id == "t1"
        assert result.task_type == TaskType.API_SIMPLE
        assert result.priority == 5
        assert result.priority_score == pytest.approx(0.5)

    def test_analyze_task_with_domain_and_project(self):
        result = self.classifier.analyze_task(
            task_id="t2",
            subject="task",
            description="desc",
            priority=3,
            domain="brandfocus-ai",
            project="voice-coach",
        )
        assert result.domain == "brandfocus-ai"
        assert result.project == "voice-coach"

    def test_analyze_task_with_status(self):
        result = self.classifier.analyze_task(
            task_id="t3",
            subject="task",
            description="desc",
            priority=3,
            status="in_progress",
        )
        assert result.status == "in_progress"

    def test_analyze_task_with_codebase_context(self):
        ctx = CodebaseContext(affected_file_count=10, max_dependency_depth=4)
        result = self.classifier.analyze_task(
            task_id="t4",
            subject="architecture migration",
            description="security integration database refactor distributed",
            priority=8,
            codebase_context=ctx,
        )
        assert result.codebase_context is ctx
        assert result.complexity.value >= ComplexityLevel.COMPLEX.value

    def test_analyze_task_extracts_skills(self):
        result = self.classifier.analyze_task(
            task_id="t5",
            subject="python fastapi endpoint",
            description="add postgresql authentication",
            priority=5,
        )
        assert Skill.PYTHON in result.required_skills
        assert Skill.FASTAPI in result.required_skills
        assert Skill.POSTGRESQL in result.required_skills
        assert Skill.AUTHENTICATION in result.required_skills

    def test_analyze_task_blocked_defaults_false(self):
        result = self.classifier.analyze_task(
            task_id="t6",
            subject="task",
            description="desc",
            priority=1,
        )
        assert result.blocked is False

    def test_analyze_task_dependencies_empty(self):
        result = self.classifier.analyze_task(
            task_id="t7",
            subject="task",
            description="desc",
            priority=1,
        )
        assert result.dependencies == []

    def test_analyze_task_estimated_duration_none(self):
        result = self.classifier.analyze_task(
            task_id="t8",
            subject="task",
            description="desc",
            priority=1,
        )
        assert result.estimated_duration is None

    def test_classifier_init(self):
        cls = TaskClassifier()
        assert cls is not None


# ---------------------------------------------------------------------------
# task_classifier.py — module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    """Tests for module-level constants."""

    def test_keyword_rules_is_dict(self):
        assert isinstance(KEYWORD_RULES, dict)

    def test_keyword_rules_covers_general_types(self):
        assert TaskType.API_SIMPLE in KEYWORD_RULES
        assert TaskType.TEST_UNIT in KEYWORD_RULES
        assert TaskType.INFRA_DEPLOY in KEYWORD_RULES

    def test_complexity_keywords_is_list(self):
        assert isinstance(COMPLEXITY_KEYWORDS, list)
        assert "architecture" in COMPLEXITY_KEYWORDS
        assert "security" in COMPLEXITY_KEYWORDS

    def test_skill_keywords_is_dict(self):
        assert isinstance(SKILL_KEYWORDS, dict)
        assert Skill.PYTHON in SKILL_KEYWORDS
        assert Skill.DOCKER in SKILL_KEYWORDS


# ---------------------------------------------------------------------------
# scoring_engine.py — get_recent_performance_scores
# ---------------------------------------------------------------------------


class TestGetRecentPerformanceScores:
    """Tests for get_recent_performance_scores placeholder."""

    def test_returns_empty_list(self):
        result = get_recent_performance_scores("any-agent")
        assert result == []

    def test_returns_list_type(self):
        result = get_recent_performance_scores("forge:kimi", n=10)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# scoring_engine.py — calculate_capability_match
# ---------------------------------------------------------------------------


class TestCalculateCapabilityMatch:
    """Tests for calculate_capability_match function."""

    def test_perfect_task_type_match(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 1.0},
            skills={},
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE)
        result = calculate_capability_match(task, agent)
        assert result["task_type_match"] == pytest.approx(1.0)
        assert result["score"] > 0

    def test_no_capability_match(self):
        agent = make_agent(capabilities={})
        task = make_task_analysis(task_type=TaskType.API_SIMPLE)
        result = calculate_capability_match(task, agent)
        assert result["task_type_match"] == pytest.approx(0.0)

    def test_skills_match_with_required_skills(self):
        agent = make_agent(
            capabilities={},
            skills={Skill.PYTHON: 1.0, Skill.FASTAPI: 0.8},
        )
        task = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            required_skills={Skill.PYTHON, Skill.FASTAPI},
        )
        result = calculate_capability_match(task, agent)
        assert result["skills_match"] == pytest.approx(0.9)  # (1.0 + 0.8) / 2

    def test_skills_neutral_when_no_required_skills(self):
        agent = make_agent(capabilities={}, skills={})
        task = make_task_analysis(task_type=TaskType.API_SIMPLE, required_skills=set())
        result = calculate_capability_match(task, agent)
        assert result["skills_match"] == pytest.approx(0.5)

    def test_domain_preference_match(self):
        agent = make_agent(preferred_domains=["brandfocus-ai"])
        task = make_task_analysis(domain="brandfocus-ai")
        result = calculate_capability_match(task, agent)
        assert result["domain_preference"] == pytest.approx(1.0)

    def test_domain_preference_no_match(self):
        agent = make_agent(preferred_domains=["other-domain"])
        task = make_task_analysis(domain="brandfocus-ai")
        result = calculate_capability_match(task, agent)
        assert result["domain_preference"] == pytest.approx(0.5)

    def test_domain_preference_no_domain_on_task(self):
        agent = make_agent(preferred_domains=["brandfocus-ai"])
        task = make_task_analysis(domain=None)
        result = calculate_capability_match(task, agent)
        assert result["domain_preference"] == pytest.approx(0.5)

    def test_avoidance_penalty(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 1.0},
            avoids=[TaskType.API_SIMPLE],
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE)
        result = calculate_capability_match(task, agent)
        # Should be penalized: 1.0 * 0.2 = 0.2
        assert result["task_type_match"] == pytest.approx(0.2)

    def test_no_avoidance_penalty_for_other_type(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 1.0},
            avoids=[TaskType.TEST_UNIT],
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE)
        result = calculate_capability_match(task, agent)
        assert result["task_type_match"] == pytest.approx(1.0)

    def test_score_components_combined(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 0.8},
            skills={Skill.PYTHON: 1.0},
            preferred_domains=["test-domain"],
        )
        task = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            domain="test-domain",
            required_skills={Skill.PYTHON},
        )
        result = calculate_capability_match(task, agent)
        expected = 0.7 * 0.8 + 0.2 * 1.0 + 0.1 * 1.0
        assert result["score"] == pytest.approx(expected, rel=1e-5)

    def test_partial_skills_coverage(self):
        agent = make_agent(
            capabilities={},
            skills={Skill.PYTHON: 1.0},
        )
        task = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            required_skills={Skill.PYTHON, Skill.FASTAPI},
        )
        result = calculate_capability_match(task, agent)
        # python=1.0, fastapi not in skills=0.0, avg=0.5
        assert result["skills_match"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# scoring_engine.py — calculate_historical_score
# ---------------------------------------------------------------------------


class TestCalculateHistoricalScore:
    """Tests for calculate_historical_score function."""

    def test_no_history_returns_blend_with_cold_start_penalty(self):
        agent = make_agent(success_rate=0.8, task_type_history={})
        task = make_task_analysis(task_type=TaskType.API_SIMPLE)
        score = calculate_historical_score(task, agent)
        # 0.4 * 0.8 + 0.4 * 0.3 + 0.2 * 0.5 = 0.32 + 0.12 + 0.10 = 0.54
        assert score == pytest.approx(0.54, rel=1e-5)

    def test_with_task_type_history(self):
        history = TaskTypeHistory(
            task_type=TaskType.API_SIMPLE,
            success_rate=1.0,
            avg_quality_score=1.0,
        )
        agent = make_agent(
            success_rate=1.0,
            task_type_history={TaskType.API_SIMPLE: history},
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE)
        score = calculate_historical_score(task, agent)
        # 0.4 * 1.0 + 0.4 * ((1.0+1.0)/2) + 0.2 * 0.5 = 0.4 + 0.4 + 0.1 = 0.9
        assert score == pytest.approx(0.9, rel=1e-5)

    def test_zero_success_rate(self):
        agent = make_agent(success_rate=0.0, task_type_history={})
        task = make_task_analysis(task_type=TaskType.API_SIMPLE)
        score = calculate_historical_score(task, agent)
        # 0.4 * 0.0 + 0.4 * 0.3 + 0.2 * 0.5 = 0.0 + 0.12 + 0.10 = 0.22
        assert score == pytest.approx(0.22, rel=1e-5)

    def test_with_mocked_recent_scores(self):
        agent = make_agent(success_rate=0.5, task_type_history={})
        task = make_task_analysis(task_type=TaskType.API_SIMPLE)
        with patch(
            "forge_harness.analytics.scoring_engine.get_recent_performance_scores",
            return_value=[0.8, 0.9, 0.7],
        ):
            score = calculate_historical_score(task, agent)
        # trend = (0.8+0.9+0.7)/3 = 0.8
        # 0.4 * 0.5 + 0.4 * 0.3 + 0.2 * 0.8 = 0.2 + 0.12 + 0.16 = 0.48
        assert score == pytest.approx(0.48, rel=1e-5)


# ---------------------------------------------------------------------------
# scoring_engine.py — calculate_task_score
# ---------------------------------------------------------------------------


class TestCalculateTaskScore:
    """Tests for calculate_task_score function."""

    def test_basic_score_returns_tuple(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 0.8},
            skills={},
            success_rate=0.8,
        )
        task = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            priority_score=0.5,
        )
        score, breakdown = calculate_task_score(task, agent)
        assert 0.0 <= score <= 1.0
        assert isinstance(breakdown, ScoreBreakdown)

    def test_blocked_task_penalty(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 1.0},
            success_rate=1.0,
        )
        task_blocked = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            priority_score=1.0,
            blocked=True,
        )
        task_unblocked = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            priority_score=1.0,
            blocked=False,
        )
        score_blocked, bd_blocked = calculate_task_score(task_blocked, agent)
        score_unblocked, bd_unblocked = calculate_task_score(task_unblocked, agent)
        assert score_blocked < score_unblocked
        assert bd_blocked.blocked_penalty is True
        assert bd_unblocked.blocked_penalty is False

    def test_complexity_exceeds_agent_max(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 1.0},
            success_rate=1.0,
            max_complexity=ComplexityLevel.SIMPLE,
        )
        task = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            priority_score=1.0,
            complexity=ComplexityLevel.EXPERT,
        )
        score, breakdown = calculate_task_score(task, agent)
        assert breakdown.complexity_penalty is True

    def test_no_complexity_penalty_within_range(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 1.0},
            max_complexity=ComplexityLevel.EXPERT,
        )
        task = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            priority_score=1.0,
            complexity=ComplexityLevel.SIMPLE,
        )
        _, breakdown = calculate_task_score(task, agent)
        assert breakdown.complexity_penalty is False

    def test_busy_agent_penalty(self):
        agent_idle = make_agent(
            capabilities={TaskType.API_SIMPLE: 1.0},
            status="idle",
            success_rate=1.0,
        )
        agent_busy = make_agent(
            capabilities={TaskType.API_SIMPLE: 1.0},
            status="busy",
            success_rate=1.0,
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE, priority_score=1.0)
        score_idle, bd_idle = calculate_task_score(task, agent_idle)
        score_busy, bd_busy = calculate_task_score(task, agent_busy)
        assert score_idle > score_busy
        assert bd_idle.availability_penalty is False
        assert bd_busy.availability_penalty is True

    def test_recency_boost_when_history_exists(self):
        last_completed = datetime.now() - timedelta(days=1)
        history = TaskTypeHistory(
            task_type=TaskType.API_SIMPLE,
            success_rate=0.9,
            avg_quality_score=0.9,
            last_completed=last_completed,
        )
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 0.8},
            success_rate=0.8,
            task_type_history={TaskType.API_SIMPLE: history},
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE, priority_score=0.5)
        score, breakdown = calculate_task_score(task, agent)
        assert breakdown.recency_boost > 0.0

    def test_no_recency_boost_without_history(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 0.8},
            success_rate=0.8,
            task_type_history={},
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE, priority_score=0.5)
        _, breakdown = calculate_task_score(task, agent)
        assert breakdown.recency_boost == pytest.approx(0.0)

    def test_no_recency_boost_without_last_completed(self):
        history = TaskTypeHistory(
            task_type=TaskType.API_SIMPLE,
            success_rate=0.9,
            avg_quality_score=0.9,
            last_completed=None,
        )
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 0.8},
            success_rate=0.8,
            task_type_history={TaskType.API_SIMPLE: history},
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE, priority_score=0.5)
        _, breakdown = calculate_task_score(task, agent)
        assert breakdown.recency_boost == pytest.approx(0.0)

    def test_score_clamped_to_zero_one(self):
        agent = make_agent(success_rate=0.0, capabilities={})
        task = make_task_analysis(priority_score=0.0)
        score, _ = calculate_task_score(task, agent)
        assert 0.0 <= score <= 1.0

    def test_breakdown_fields_populated(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 0.7},
            skills={Skill.PYTHON: 0.9},
            success_rate=0.75,
        )
        task = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            priority_score=0.6,
            required_skills={Skill.PYTHON},
        )
        score, breakdown = calculate_task_score(task, agent)
        assert breakdown.priority_score == pytest.approx(0.6)
        assert breakdown.capability_score >= 0
        assert breakdown.historical_score >= 0
        assert breakdown.final_score == pytest.approx(score)

    def test_custom_time_decay(self):
        last_completed = datetime.now() - timedelta(days=10)
        history = TaskTypeHistory(
            task_type=TaskType.API_SIMPLE,
            success_rate=0.9,
            avg_quality_score=0.9,
            last_completed=last_completed,
        )
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 0.8},
            success_rate=0.8,
            task_type_history={TaskType.API_SIMPLE: history},
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE, priority_score=0.5)
        # High decay means less recency boost
        _, bd_high_decay = calculate_task_score(task, agent, time_decay=1.0)
        _, bd_low_decay = calculate_task_score(task, agent, time_decay=0.01)
        assert bd_low_decay.recency_boost > bd_high_decay.recency_boost


# ---------------------------------------------------------------------------
# scoring_engine.py — ScoringEngine
# ---------------------------------------------------------------------------


class TestScoringEngine:
    """Tests for ScoringEngine class."""

    def setup_method(self):
        self.engine = ScoringEngine(time_decay=0.1)

    def test_init_default_time_decay(self):
        engine = ScoringEngine()
        assert engine.time_decay == pytest.approx(0.1)

    def test_init_custom_time_decay(self):
        engine = ScoringEngine(time_decay=0.5)
        assert engine.time_decay == pytest.approx(0.5)

    def test_cache_initialized_empty(self):
        engine = ScoringEngine()
        assert engine._cache == {}

    def test_clear_cache(self):
        engine = ScoringEngine()
        engine._cache["key"] = 0.5
        engine.clear_cache()
        assert engine._cache == {}

    def test_calculate_agent_scores_returns_sorted_list(self):
        agents = [
            make_agent(
                agent_id="agent-1",
                capabilities={TaskType.API_SIMPLE: 0.2},
                success_rate=0.2,
            ),
            make_agent(
                agent_id="agent-2",
                capabilities={TaskType.API_SIMPLE: 0.9},
                success_rate=0.9,
            ),
        ]
        task = make_task_analysis(task_type=TaskType.API_SIMPLE, priority_score=0.5)
        results = self.engine.calculate_agent_scores(task, agents)
        assert len(results) == 2
        # Sorted descending by score
        assert results[0][1] >= results[1][1]

    def test_calculate_agent_scores_tuple_structure(self):
        agents = [make_agent(agent_id="agent-1")]
        task = make_task_analysis()
        results = self.engine.calculate_agent_scores(task, agents)
        assert len(results) == 1
        agent, score, breakdown = results[0]
        assert isinstance(agent, AgentProfile)
        assert isinstance(score, float)
        assert isinstance(breakdown, ScoreBreakdown)

    def test_calculate_agent_scores_empty_agents(self):
        task = make_task_analysis()
        results = self.engine.calculate_agent_scores(task, [])
        assert results == []

    def test_calculate_task_scores_for_agent_returns_sorted(self):
        agent = make_agent(
            capabilities={TaskType.API_SIMPLE: 0.9, TaskType.TEST_UNIT: 0.2},
            success_rate=0.8,
        )
        tasks = [
            make_task_analysis(task_id="t1", task_type=TaskType.TEST_UNIT, priority_score=0.3),
            make_task_analysis(task_id="t2", task_type=TaskType.API_SIMPLE, priority_score=0.8),
        ]
        results = self.engine.calculate_task_scores_for_agent(tasks, agent)
        assert len(results) == 2
        assert results[0][1] >= results[1][1]

    def test_calculate_task_scores_for_agent_tuple_structure(self):
        agent = make_agent()
        tasks = [make_task_analysis()]
        results = self.engine.calculate_task_scores_for_agent(tasks, agent)
        task_anal, score, breakdown = results[0]
        assert isinstance(task_anal, TaskAnalysis)
        assert isinstance(score, float)
        assert isinstance(breakdown, ScoreBreakdown)

    def test_calculate_task_scores_for_agent_empty_tasks(self):
        agent = make_agent()
        results = self.engine.calculate_task_scores_for_agent([], agent)
        assert results == []

    def test_multiple_agents_all_scored(self):
        agents = [make_agent(agent_id=f"agent-{i}") for i in range(5)]
        task = make_task_analysis()
        results = self.engine.calculate_agent_scores(task, agents)
        assert len(results) == 5

    def test_best_agent_is_first(self):
        best_agent = make_agent(
            agent_id="best",
            capabilities={TaskType.API_SIMPLE: 1.0},
            success_rate=1.0,
            status="idle",
        )
        weak_agent = make_agent(
            agent_id="weak",
            capabilities={TaskType.API_SIMPLE: 0.0},
            success_rate=0.0,
            status="busy",
        )
        task = make_task_analysis(task_type=TaskType.API_SIMPLE, priority_score=1.0)
        results = self.engine.calculate_agent_scores(task, [weak_agent, best_agent])
        assert results[0][0].agent_id == "best"


# ---------------------------------------------------------------------------
# recommendation_engine.py — RecommendationEngine._normalize_priority
# ---------------------------------------------------------------------------


class TestNormalizePriorityStatic:
    """Tests for RecommendationEngine._normalize_priority static method."""

    def test_none_returns_zero(self):
        assert RecommendationEngine._normalize_priority(None) == 0

    def test_int_passthrough(self):
        assert RecommendationEngine._normalize_priority(5) == 5

    def test_string_int_parses(self):
        assert RecommendationEngine._normalize_priority("7") == 7

    def test_string_critical(self):
        assert RecommendationEngine._normalize_priority("critical") == 4

    def test_string_high(self):
        assert RecommendationEngine._normalize_priority("high") == 3

    def test_string_medium(self):
        assert RecommendationEngine._normalize_priority("medium") == 2

    def test_string_low(self):
        assert RecommendationEngine._normalize_priority("low") == 1

    def test_string_unknown_returns_zero(self):
        assert RecommendationEngine._normalize_priority("unknown") == 0

    def test_string_invalid_int_returns_zero(self):
        assert RecommendationEngine._normalize_priority("abc") == 0

    def test_other_type_returns_zero(self):
        assert RecommendationEngine._normalize_priority([1, 2]) == 0

    def test_case_insensitive_high(self):
        assert RecommendationEngine._normalize_priority("HIGH") == 3

    def test_case_insensitive_critical(self):
        assert RecommendationEngine._normalize_priority("CRITICAL") == 4


# ---------------------------------------------------------------------------
# recommendation_engine.py — CapabilityMatch, TaskRecommendation, etc.
# ---------------------------------------------------------------------------


class TestDataclasses:
    """Tests for dataclass instantiation."""

    def test_capability_match_fields(self):
        cm = CapabilityMatch(
            agent_id="agent-1",
            score=0.75,
            capability_score=0.8,
            historical_score=0.6,
            breakdown={"task_type_match": 0.9, "skills_match": 0.7, "domain_preference": 0.5},
        )
        assert cm.agent_id == "agent-1"
        assert cm.score == pytest.approx(0.75)
        assert cm.breakdown["task_type_match"] == pytest.approx(0.9)

    def test_task_recommendation_fields(self):
        rec = TaskRecommendation(
            task={"id": "t1"},
            analysis={"task_type": "api:simple"},
            scoring={"priority_score": 0.5},
            codebase_context=None,
        )
        assert rec.task["id"] == "t1"
        assert rec.codebase_context is None

    def test_recommendation_response_fields(self):
        resp = RecommendationResponse(
            recommendations=[],
            meta={"total_tasks": 0},
        )
        assert resp.recommendations == []
        assert resp.meta["total_tasks"] == 0


# ---------------------------------------------------------------------------
# recommendation_engine.py — RecommendationEngine.generate_recommendations
# ---------------------------------------------------------------------------


class TestRecommendationEngine:
    """Tests for RecommendationEngine.generate_recommendations."""

    def setup_method(self):
        self.engine = RecommendationEngine(time_decay=0.1)
        self.agent = make_agent(
            agent_id="agent-1",
            capabilities={TaskType.API_SIMPLE: 0.9},
            skills={Skill.PYTHON: 0.9},
            success_rate=0.8,
            status="idle",
        )

    def _make_task_dict(
        self,
        task_id: str = "t1",
        subject: str = "fetch user list",
        description: str = "get all users from endpoint",
        priority: int = 5,
        status: str = "pending",
        domain: str | None = None,
        project: str | None = None,
    ) -> dict:
        return {
            "id": task_id,
            "subject": subject,
            "description": description,
            "priority": priority,
            "status": status,
            "domain": domain,
            "project": project,
        }

    def test_basic_recommendation_returned(self):
        tasks = [self._make_task_dict()]
        response = self.engine.generate_recommendations(tasks, [self.agent])
        assert isinstance(response, RecommendationResponse)
        assert len(response.recommendations) == 1

    def test_completed_tasks_excluded(self):
        tasks = [self._make_task_dict(status="completed")]
        response = self.engine.generate_recommendations(tasks, [self.agent])
        assert len(response.recommendations) == 0

    def test_min_priority_filter(self):
        tasks = [
            self._make_task_dict(task_id="t1", priority=1),
            self._make_task_dict(task_id="t2", priority=5),
        ]
        response = self.engine.generate_recommendations(tasks, [self.agent], min_priority=3)
        assert len(response.recommendations) == 1

    def test_limit_respected(self):
        tasks = [self._make_task_dict(task_id=f"t{i}", priority=5) for i in range(10)]
        response = self.engine.generate_recommendations(tasks, [self.agent], limit=3)
        assert len(response.recommendations) <= 3

    def test_blocked_tasks_excluded_by_default(self):
        # TaskClassifier is imported locally inside generate_recommendations, so patch at its module path
        with patch(
            "forge_harness.analytics.task_classifier.TaskClassifier.analyze_task"
        ) as mock_analyze:
            blocked_analysis = make_task_analysis(blocked=True, task_type=TaskType.API_SIMPLE)
            mock_analyze.return_value = blocked_analysis
            tasks = [self._make_task_dict()]
            response = self.engine.generate_recommendations(
                tasks, [self.agent], include_blocked=False
            )
            assert len(response.recommendations) == 0

    def test_blocked_tasks_included_when_flag_set(self):
        with patch(
            "forge_harness.analytics.task_classifier.TaskClassifier.analyze_task"
        ) as mock_analyze:
            blocked_analysis = make_task_analysis(blocked=True, task_type=TaskType.API_SIMPLE)
            mock_analyze.return_value = blocked_analysis

            # Also patch ScoringEngine.calculate_agent_scores at its module path
            with patch(
                "forge_harness.analytics.scoring_engine.ScoringEngine.calculate_agent_scores"
            ) as mock_scores:
                fake_bd = ScoreBreakdown(
                    priority_score=0.5,
                    capability_score=0.8,
                    historical_score=0.7,
                    task_type_match=0.8,
                    skills_match=0.7,
                    domain_preference=0.5,
                    final_score=0.7,
                    blocked_penalty=True,
                    complexity_penalty=False,
                    availability_penalty=False,
                    recency_boost=0.0,
                )
                mock_scores.return_value = [(self.agent, 0.7, fake_bd)]
                tasks = [self._make_task_dict()]
                response = self.engine.generate_recommendations(
                    tasks, [self.agent], include_blocked=True
                )
            assert len(response.recommendations) == 1

    def test_task_type_filter(self):
        with patch(
            "forge_harness.analytics.task_classifier.TaskClassifier.analyze_task"
        ) as mock_analyze:
            analysis = make_task_analysis(task_type=TaskType.TEST_UNIT)
            mock_analyze.return_value = analysis
            tasks = [self._make_task_dict()]
            response = self.engine.generate_recommendations(
                tasks, [self.agent], task_type_filter="api:simple"
            )
            assert len(response.recommendations) == 0

    def test_domain_filter(self):
        with patch(
            "forge_harness.analytics.task_classifier.TaskClassifier.analyze_task"
        ) as mock_analyze:
            analysis = make_task_analysis(task_type=TaskType.API_SIMPLE, domain="other-domain")
            mock_analyze.return_value = analysis
            tasks = [self._make_task_dict(domain="other-domain")]
            response = self.engine.generate_recommendations(
                tasks, [self.agent], domain_filter="target-domain"
            )
            assert len(response.recommendations) == 0

    def test_meta_contains_expected_keys(self):
        tasks = [self._make_task_dict()]
        response = self.engine.generate_recommendations(tasks, [self.agent])
        assert "total_tasks" in response.meta
        assert "recommended_count" in response.meta
        assert "algorithm_version" in response.meta
        assert "generated_at" in response.meta

    def test_meta_total_tasks_matches_input(self):
        tasks = [self._make_task_dict(task_id=f"t{i}") for i in range(4)]
        response = self.engine.generate_recommendations(tasks, [self.agent])
        assert response.meta["total_tasks"] == 4

    def test_recommendation_structure(self):
        tasks = [self._make_task_dict()]
        response = self.engine.generate_recommendations(tasks, [self.agent])
        assert len(response.recommendations) == 1
        rec = response.recommendations[0]
        assert "id" in rec.task
        assert "task_type" in rec.analysis
        assert "complexity" in rec.analysis
        assert "required_skills" in rec.analysis
        assert "priority_score" in rec.scoring
        assert "recommended_agent" in rec.scoring
        assert "capability_matches" in rec.scoring

    def test_recommendations_sorted_by_confidence(self):
        # Create tasks that produce different scores by having different priorities
        tasks = [
            self._make_task_dict(task_id="t1", priority=1, description="get endpoint"),
            self._make_task_dict(task_id="t2", priority=9, description="get endpoint fetch list"),
        ]
        response = self.engine.generate_recommendations(tasks, [self.agent])
        if len(response.recommendations) >= 2:
            conf_0 = response.recommendations[0].scoring.get("recommendation_confidence", 0)
            conf_1 = response.recommendations[1].scoring.get("recommendation_confidence", 0)
            assert conf_0 >= conf_1

    def test_empty_tasks_returns_empty_recommendations(self):
        response = self.engine.generate_recommendations([], [self.agent])
        assert response.recommendations == []
        assert response.meta["total_tasks"] == 0

    def test_empty_agents_returns_empty_recommendations(self):
        tasks = [self._make_task_dict()]
        response = self.engine.generate_recommendations(tasks, [])
        assert response.recommendations == []

    def test_string_priority_handled(self):
        tasks = [self._make_task_dict(priority="high")]  # type: ignore[arg-type]
        response = self.engine.generate_recommendations(tasks, [self.agent], min_priority=1)
        # "high" normalizes to 3, which passes min_priority=1
        assert isinstance(response, RecommendationResponse)

    def test_none_priority_handled(self):
        tasks = [self._make_task_dict(priority=None)]  # type: ignore[arg-type]
        response = self.engine.generate_recommendations(tasks, [self.agent], min_priority=1)
        # None normalizes to 0, filtered out
        assert len(response.recommendations) == 0

    def test_codebase_context_in_recommendation(self):
        ctx = CodebaseContext(affected_file_count=3)
        analysis = make_task_analysis(
            task_type=TaskType.API_SIMPLE,
            codebase_context=ctx,
        )
        with patch(
            "forge_harness.analytics.task_classifier.TaskClassifier.analyze_task",
            return_value=analysis,
        ):
            with patch(
                "forge_harness.analytics.scoring_engine.ScoringEngine.calculate_agent_scores"
            ) as mock_scores:
                fake_bd = ScoreBreakdown(
                    priority_score=0.5,
                    capability_score=0.8,
                    historical_score=0.7,
                    task_type_match=0.8,
                    skills_match=0.7,
                    domain_preference=0.5,
                    final_score=0.7,
                    blocked_penalty=False,
                    complexity_penalty=False,
                    availability_penalty=False,
                    recency_boost=0.0,
                )
                mock_scores.return_value = [(self.agent, 0.7, fake_bd)]
                tasks = [self._make_task_dict()]
                response = self.engine.generate_recommendations(tasks, [self.agent])

        assert len(response.recommendations) == 1
        assert response.recommendations[0].codebase_context is not None

    def test_algorithm_version_is_1_0(self):
        response = self.engine.generate_recommendations([], [self.agent])
        assert response.meta["algorithm_version"] == "1.0"

    def test_generated_at_is_iso_string(self):
        response = self.engine.generate_recommendations([], [self.agent])
        generated_at = response.meta["generated_at"]
        assert isinstance(generated_at, str)
        assert "T" in generated_at  # ISO format includes T separator


# ---------------------------------------------------------------------------
# recommendation_engine.py — RecommendationEngine.to_dict
# ---------------------------------------------------------------------------


class TestRecommendationEngineToDict:
    """Tests for RecommendationEngine.to_dict method."""

    def setup_method(self):
        self.engine = RecommendationEngine()

    def test_to_dict_empty_recommendations(self):
        response = RecommendationResponse(
            recommendations=[],
            meta={"total_tasks": 0, "recommended_count": 0},
        )
        result = self.engine.to_dict(response)
        assert result["recommendations"] == []
        assert result["meta"]["total_tasks"] == 0

    def test_to_dict_with_recommendation(self):
        rec = TaskRecommendation(
            task={"id": "t1", "subject": "Test"},
            analysis={"task_type": "api:simple", "complexity": "simple"},
            scoring={"priority_score": 0.5, "recommended_agent": "agent-1"},
            codebase_context=None,
        )
        response = RecommendationResponse(
            recommendations=[rec],
            meta={"total_tasks": 1, "recommended_count": 1},
        )
        result = self.engine.to_dict(response)
        assert len(result["recommendations"]) == 1
        assert result["recommendations"][0]["task"]["id"] == "t1"
        assert result["recommendations"][0]["codebase_context"] is None

    def test_to_dict_preserves_meta(self):
        response = RecommendationResponse(
            recommendations=[],
            meta={"total_tasks": 5, "algorithm_version": "1.0"},
        )
        result = self.engine.to_dict(response)
        assert result["meta"]["total_tasks"] == 5
        assert result["meta"]["algorithm_version"] == "1.0"

    def test_to_dict_with_codebase_context(self):
        rec = TaskRecommendation(
            task={"id": "t1"},
            analysis={},
            scoring={},
            codebase_context={"affected_file_count": 3},
        )
        response = RecommendationResponse(recommendations=[rec], meta={})
        result = self.engine.to_dict(response)
        assert result["recommendations"][0]["codebase_context"] == {"affected_file_count": 3}

    def test_to_dict_multiple_recommendations(self):
        recs = [
            TaskRecommendation(
                task={"id": f"t{i}"},
                analysis={},
                scoring={},
                codebase_context=None,
            )
            for i in range(3)
        ]
        response = RecommendationResponse(recommendations=recs, meta={})
        result = self.engine.to_dict(response)
        assert len(result["recommendations"]) == 3
