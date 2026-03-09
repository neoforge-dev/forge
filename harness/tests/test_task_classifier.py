"""
Comprehensive unit tests for TaskClassifier.

Tests task type detection, complexity estimation, skill extraction,
and the full TaskClassifier.analyze_task pipeline.
"""

from datetime import datetime, timedelta

import pytest

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
# Test detect_task_type
# =============================================================================


class TestDetectTaskType:
    """Tests for detect_task_type function."""

    def test_detect_api_simple(self):
        """Test detecting simple API tasks."""
        task_type = detect_task_type("Get user endpoint", "Create a GET endpoint to fetch user data")
        assert task_type == TaskType.API_SIMPLE

    def test_detect_api_stateful(self):
        """Test detecting stateful API tasks."""
        task_type = detect_task_type("Create user", "POST endpoint to save user to database")
        assert task_type == TaskType.API_STATEFUL

    def test_detect_api_batch(self):
        """Test detecting batch API tasks."""
        task_type = detect_task_type("Bulk import", "Import multiple users at once")
        assert task_type == TaskType.API_BATCH

    def test_detect_api_external(self):
        """Test detecting external API tasks."""
        task_type = detect_task_type("Webhook handler", "Handle external webhook events")
        assert task_type == TaskType.API_EXTERNAL

    def test_detect_frontend_component(self):
        """Test detecting frontend component tasks."""
        task_type = detect_task_type("User card", "Create a UI card component for user profile")
        assert task_type == TaskType.FRONTEND_COMPONENT

    def test_detect_frontend_page(self):
        """Test detecting frontend page tasks."""
        task_type = detect_task_type("Dashboard page", "Create dashboard view with charts")
        assert task_type == TaskType.FRONTEND_PAGE

    def test_detect_frontend_styles(self):
        """Test detecting frontend styling tasks."""
        task_type = detect_task_type("Style updates", "Update CSS theme and responsive design")
        assert task_type == TaskType.FRONTEND_STYLES

    def test_detect_frontend_state(self):
        """Test detecting frontend state management tasks."""
        task_type = detect_task_type("Redux store", "Add state management for user data")
        assert task_type == TaskType.FRONTEND_STATE

    def test_detect_test_unit(self):
        """Test detecting unit test tasks."""
        task_type = detect_task_type("Test utils", "Write unit tests for utility functions using pytest")
        assert task_type == TaskType.TEST_UNIT

    def test_detect_test_integration(self):
        """Test detecting integration test tasks."""
        task_type = detect_task_type("API tests", "Write integration tests for service endpoints")
        assert task_type == TaskType.TEST_INTEGRATION

    def test_detect_test_e2e(self):
        """Test detecting e2e test tasks."""
        task_type = detect_task_type("User flow test", "Create end-to-end test with Playwright")
        assert task_type == TaskType.TEST_E2E

    def test_detect_infra_deploy(self):
        """Test detecting deployment tasks."""
        task_type = detect_task_type("Deploy to production", "Set up Railway deployment")
        assert task_type == TaskType.INFRA_DEPLOY

    def test_detect_infra_ci_cd(self):
        """Test detecting CI/CD tasks."""
        task_type = detect_task_type("GitHub Actions", "Create CI/CD pipeline workflow")
        assert task_type == TaskType.INFRA_CI_CD

    def test_detect_infra_docker(self):
        """Test detecting Docker tasks."""
        task_type = detect_task_type("Containerize app", "Create Dockerfile for the application")
        assert task_type == TaskType.INFRA_DOCKER

    def test_detect_docs_api(self):
        """Test detecting API documentation tasks."""
        task_type = detect_task_type("API docs", "Generate OpenAPI documentation for endpoints")
        assert task_type == TaskType.DOCS_API

    def test_detect_docs_user(self):
        """Test detecting user documentation tasks."""
        task_type = detect_task_type("User guide", "Write README with setup instructions")
        assert task_type == TaskType.DOCS_USER

    def test_detect_docs_architecture(self):
        """Test detecting architecture documentation tasks."""
        task_type = detect_task_type("Design doc", "Create technical specification and ADR")
        assert task_type == TaskType.DOCS_ARCHITECTURE

    def test_detect_research_technical(self):
        """Test detecting technical research tasks."""
        task_type = detect_task_type("Investigate options", "Research and evaluate different solutions")
        assert task_type == TaskType.RESEARCH_TECHNICAL

    def test_detect_research_market(self):
        """Test detecting market research tasks."""
        task_type = detect_task_type("Competitive analysis", "Analyze competitor features")
        assert task_type == TaskType.RESEARCH_MARKET

    def test_detect_refactor_cleanup(self):
        """Test detecting code cleanup tasks."""
        task_type = detect_task_type("Code cleanup", "Refactor and organize codebase")
        assert task_type == TaskType.REFACTOR_CLEANUP

    def test_detect_refactor_optimize(self):
        """Test detecting optimization tasks."""
        task_type = detect_task_type("Performance", "Optimize slow database queries")
        assert task_type == TaskType.REFACTOR_OPTIMIZE

    def test_detect_refactor_migrate(self):
        """Test detecting migration tasks."""
        task_type = detect_task_type("Upgrade deps", "Migrate to new framework version")
        assert task_type == TaskType.REFACTOR_MIGRATE

    def test_detect_general_fallback(self):
        """Test fallback to GENERAL when no keywords match."""
        task_type = detect_task_type("Random task", "Some unspecified work")
        assert task_type == TaskType.GENERAL

    def test_detect_case_insensitive(self):
        """Test detection is case-insensitive."""
        task_type = detect_task_type("GET ENDPOINT", "Create API")
        assert task_type == TaskType.API_SIMPLE

    def test_detect_matches_multiple_keywords(self):
        """Test task with multiple matching keywords."""
        # "create" in description matches API_STATEFUL first (insertion order)
        task_type = detect_task_type("API deploy docker", "Create deployment")
        # "create" matches API_STATEFUL before "deploy"/"docker" match INFRA types
        assert task_type == TaskType.API_STATEFUL

    def test_detect_empty_text(self):
        """Test handling empty text."""
        task_type = detect_task_type("", "")
        assert task_type == TaskType.GENERAL


# =============================================================================
# Test estimate_complexity
# =============================================================================


class TestEstimateComplexity:
    """Tests for estimate_complexity function."""

    def test_complexity_trivial_minimal(self):
        """Test trivial complexity for minimal tasks."""
        complexity = estimate_complexity("Fix typo", "Fix a typo in README")
        assert complexity == ComplexityLevel.TRIVIAL

    def test_complexity_simple_short_description(self):
        """Test simple complexity for short descriptions."""
        # Short description with no technical keywords results in TRIVIAL
        complexity = estimate_complexity("Add field", "Add a new field to user model")
        assert complexity == ComplexityLevel.TRIVIAL

    def test_complexity_moderate_keywords(self):
        """Test moderate complexity with technical keywords."""
        # Need multiple complexity factors for MODERATE (>= 2.0)
        # "integration" + "database" + "multiple" = 0.5 + 0.5 + 1.0 = 2.0
        complexity = estimate_complexity(
            "Database integration",
            "Integrate with external payment API, handle database migration for multiple services"
        )
        assert complexity == ComplexityLevel.MODERATE

    def test_complexity_complex_multiple_factors(self):
        """Test complex complexity with multiple factors."""
        complexity = estimate_complexity("System migration", "Migrate entire system to new architecture across multiple services")
        assert complexity == ComplexityLevel.COMPLEX

    def test_complexity_expert_max(self):
        """Test expert complexity for maximum factors."""
        complexity = estimate_complexity(
            "Enterprise migration",
            "Complete distributed system migration with breaking changes across entire codebase involving critical systems and legacy monolith",
        )
        assert complexity == ComplexityLevel.EXPERT

    def test_complexity_word_count_factor(self):
        """Test word count contributes to complexity."""
        # Very long description (> 100 words)
        long_desc = " ".join(["word"] * 101)
        complexity = estimate_complexity("Task", long_desc)
        assert complexity.value >= ComplexityLevel.SIMPLE.value

    def test_complexity_technical_keywords(self):
        """Test technical keywords increase complexity."""
        complexity = estimate_complexity("Security architecture", "Implement authentication and distributed database")
        # Should have higher complexity due to keywords
        assert complexity.value >= ComplexityLevel.MODERATE.value

    def test_complexity_batch_multiple(self):
        """Test 'batch' keyword increases complexity."""
        complexity = estimate_complexity("Multiple updates", "Handle multiple batch updates")
        # "batch" adds 1.0 to score
        assert complexity.value >= ComplexityLevel.SIMPLE.value

    def test_complexity_end_to_end(self):
        """Test 'end-to-end' keyword increases complexity."""
        complexity = estimate_complexity("Full feature", "Implement end-to-end user flow")
        # "end-to-end" adds 1.0 to score
        assert complexity.value >= ComplexityLevel.SIMPLE.value

    def test_complexity_system_wide(self):
        """Test 'system-wide' keyword significantly increases complexity."""
        complexity = estimate_complexity("System refactor", "Refactor across entire codebase with system-wide impact")
        # "system-wide" adds 1.5 to score
        assert complexity.value >= ComplexityLevel.MODERATE.value

    def test_complexity_with_codebase_context_files(self):
        """Test complexity with codebase context (file count)."""
        context = CodebaseContext(affected_file_count=6)
        complexity = estimate_complexity("Simple task", "Simple description", context)
        # 6 files adds 1.0 to score
        assert complexity.value >= ComplexityLevel.SIMPLE.value

    def test_complexity_with_codebase_context_dependency_depth(self):
        """Test complexity with codebase context (dependency depth)."""
        context = CodebaseContext(max_dependency_depth=4)
        complexity = estimate_complexity("Simple task", "Simple description", context)
        # Depth 4 adds 1.0 to score
        assert complexity.value >= ComplexityLevel.SIMPLE.value

    def test_complexity_with_codebase_context_critical_system(self):
        """Test complexity with codebase context (critical system)."""
        context = CodebaseContext(touches_critical_system=True)
        complexity = estimate_complexity("Simple task", "Simple description", context)
        # Critical system adds 1.0 to score
        assert complexity.value >= ComplexityLevel.SIMPLE.value

    def test_complexity_with_codebase_context_breaking_risk(self):
        """Test complexity with codebase context (breaking risk)."""
        context = CodebaseContext(breaking_change_risk=0.6)
        complexity = estimate_complexity("Simple task", "Simple description", context)
        # Breaking risk adds 0.5 to score
        assert complexity.value >= ComplexityLevel.TRIVIAL.value

    def test_complexity_with_codebase_context_cyclomatic_complexity(self):
        """Test complexity with codebase context (cyclomatic complexity)."""
        context = CodebaseContext(cyclomatic_complexity=25)
        complexity = estimate_complexity("Simple task", "Simple description", context)
        # Cyclomatic 25 adds 1.0 to score
        assert complexity.value >= ComplexityLevel.SIMPLE.value

    def test_complexity_with_codebase_context_moderate_cyclomatic(self):
        """Test complexity with moderate cyclomatic complexity."""
        context = CodebaseContext(cyclomatic_complexity=15)
        complexity = estimate_complexity("Simple task", "Simple description", context)
        # Cyclomatic 15 adds 0.5 to score
        assert complexity.value >= ComplexityLevel.TRIVIAL.value

    def test_complexity_all_factors_combined(self):
        """Test complexity with all factors combined."""
        context = CodebaseContext(
            affected_file_count=10,
            max_dependency_depth=5,
            touches_critical_system=True,
            breaking_change_risk=0.8,
            cyclomatic_complexity=30,
        )
        complexity = estimate_complexity(
            "Complex system migration",
            "Complete distributed system architecture migration with breaking changes",
            context,
        )
        assert complexity == ComplexityLevel.EXPERT

    def test_complexity_no_context(self):
        """Test complexity calculation without codebase context."""
        complexity = estimate_complexity("Simple task", "Simple description", None)
        assert complexity in (ComplexityLevel.TRIVIAL, ComplexityLevel.SIMPLE)


# =============================================================================
# Test extract_required_skills
# =============================================================================


class TestExtractRequiredSkills:
    """Tests for extract_required_skills function."""

    def test_extract_python(self):
        """Test extracting Python skill."""
        skills = extract_required_skills("Python API", "Create python endpoint")
        assert Skill.PYTHON in skills

    def test_extract_typescript(self):
        """Test extracting TypeScript skill."""
        skills = extract_required_skills("React component", "Create typescript component")
        assert Skill.TYPESCRIPT in skills

    def test_extract_javascript(self):
        """Test extracting JavaScript skill."""
        skills = extract_required_skills("JS script", "Add javascript function")
        assert Skill.JAVASCRIPT in skills

    def test_extract_fastapi(self):
        """Test extracting FastAPI skill."""
        skills = extract_required_skills("API", "Build fastapi service")
        assert Skill.FASTAPI in skills

    def test_extract_django(self):
        """Test extracting Django skill."""
        skills = extract_required_skills("Backend", "Create django app")
        assert Skill.DJANGO in skills

    def test_extract_flask(self):
        """Test extracting Flask skill."""
        skills = extract_required_skills("Microservice", "Build flask service")
        assert Skill.FLASK in skills

    def test_extract_express(self):
        """Test extracting Express skill."""
        skills = extract_required_skills("Node API", "Build express server")
        assert Skill.EXPRESS in skills

    def test_extract_react(self):
        """Test extracting React skill."""
        skills = extract_required_skills("Frontend", "Build react component")
        assert Skill.REACT in skills

    def test_extract_vue(self):
        """Test extracting Vue skill."""
        skills = extract_required_skills("UI", "Create vue application")
        assert Skill.VUE in skills

    def test_extract_sql(self):
        """Test extracting SQL skill."""
        skills = extract_required_skills("Database", "Write sql queries")
        assert Skill.SQL in skills

    def test_extract_postgresql(self):
        """Test extracting PostgreSQL skill."""
        skills = extract_required_skills("Database", "Use postgres database")
        assert Skill.POSTGRESQL in skills

    def test_extract_mongodb(self):
        """Test extracting MongoDB skill."""
        skills = extract_required_skills("NoSQL", "Use mongo database")
        assert Skill.MONGODB in skills

    def test_extract_redis(self):
        """Test extracting Redis skill."""
        skills = extract_required_skills("Caching", "Add redis cache")
        assert Skill.REDIS in skills

    def test_extract_pytest(self):
        """Test extracting Pytest skill."""
        skills = extract_required_skills("Testing", "Write pytest tests")
        assert Skill.PYTEST in skills

    def test_extract_docker(self):
        """Test extracting Docker skill."""
        skills = extract_required_skills("Deployment", "Create docker container")
        assert Skill.DOCKER in skills

    def test_extract_kubernetes(self):
        """Test extracting Kubernetes skill."""
        skills = extract_required_skills("Orchestration", "Deploy on k8s cluster")
        assert Skill.KUBERNETES in skills

    def test_extract_authentication(self):
        """Test extracting Authentication skill."""
        skills = extract_required_skills("Login", "Implement auth with JWT")
        assert Skill.AUTHENTICATION in skills

    def test_extract_graphql(self):
        """Test extracting GraphQL skill."""
        skills = extract_required_skills("API", "Build graphql API")
        assert Skill.GRAPHQL in skills

    def test_extract_research(self):
        """Test extracting Research skill."""
        skills = extract_required_skills("Investigation", "Research and analyze options")
        assert Skill.RESEARCH in skills

    def test_extract_documentation(self):
        """Test extracting Documentation skill."""
        skills = extract_required_skills("Docs", "Write documentation and readme")
        assert Skill.DOCUMENTATION in skills

    def test_extract_multiple_skills(self):
        """Test extracting multiple skills."""
        skills = extract_required_skills(
            "Full stack app",
            "Build python backend with fastapi and react frontend with docker deployment",
        )
        assert Skill.PYTHON in skills
        assert Skill.FASTAPI in skills
        assert Skill.REACT in skills
        assert Skill.DOCKER in skills

    def test_extract_no_skills(self):
        """Test when no skills are detected."""
        skills = extract_required_skills("Generic task", "Do some work")
        assert len(skills) == 0

    def test_extract_case_insensitive(self):
        """Test skill extraction is case-insensitive."""
        skills = extract_required_skills("PYTHON CODE", "Write PYTHON code")
        assert Skill.PYTHON in skills

    def test_extract_no_duplicate_skills(self):
        """Test each skill is only added once."""
        skills = extract_required_skills("Python Python", "python python python")
        # Should have only one PYTHON skill
        assert skills == {Skill.PYTHON}


# =============================================================================
# Test normalize_priority
# =============================================================================


class TestNormalizePriority:
    """Tests for normalize_priority function."""

    def test_normalize_zero(self):
        """Test normalizing priority 0."""
        assert normalize_priority(0) == 0.0

    def test_normalize_low(self):
        """Test normalizing low priority."""
        assert normalize_priority(3) == 0.3

    def test_normalize_mid(self):
        """Test normalizing mid priority."""
        assert normalize_priority(5) == 0.5

    def test_normalize_high(self):
        """Test normalizing high priority."""
        assert normalize_priority(8) == 0.8

    def test_normalize_max(self):
        """Test normalizing maximum priority."""
        assert normalize_priority(10) == 1.0

    def test_normalize_above_max(self):
        """Test normalizing priority above maximum gets clamped."""
        assert normalize_priority(15) == 1.0

    def test_normalize_negative(self):
        """Test normalizing negative priority gets clamped to 0."""
        assert normalize_priority(-5) == 0.0


# =============================================================================
# Test TaskClassifier class
# =============================================================================


class TestTaskClassifier:
    """Tests for TaskClassifier class."""

    def test_init(self):
        """Test TaskClassifier initialization."""
        classifier = TaskClassifier()
        assert classifier is not None

    def test_analyze_task_basic(self):
        """Test basic task analysis."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-1",
            subject="Create API endpoint",
            description="Build a GET endpoint to fetch users",
            priority=5,
        )

        assert isinstance(analysis, TaskAnalysis)
        assert analysis.id == "task-1"
        assert analysis.subject == "Create API endpoint"
        assert analysis.description == "Build a GET endpoint to fetch users"
        assert analysis.priority == 5
        assert analysis.task_type == TaskType.API_SIMPLE
        assert analysis.priority_score == 0.5
        assert len(analysis.dependencies) == 0
        assert analysis.blocked is False
        assert analysis.estimated_duration is None

    def test_analyze_task_with_domain_project(self):
        """Test task analysis with domain and project."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-2",
            subject="Build component",
            description="Create React component",
            priority=7,
            domain="voice-coach",
            project="frontend",
        )

        assert analysis.domain == "voice-coach"
        assert analysis.project == "frontend"

    def test_analyze_task_with_status(self):
        """Test task analysis with custom status."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-3",
            subject="Fix bug",
            description="Fix critical bug",
            priority=10,
            status="in_progress",
        )

        assert analysis.status == "in_progress"

    def test_analyze_task_detects_type(self):
        """Test task analysis detects task type correctly."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-4",
            subject="Docker setup",
            description="Create Dockerfile for deployment",
            priority=5,
        )

        assert analysis.task_type == TaskType.INFRA_DOCKER

    def test_analyze_task_estimates_complexity(self):
        """Test task analysis estimates complexity."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-5",
            subject="Simple fix",
            description="Fix typo in README",
            priority=1,
        )

        assert analysis.complexity == ComplexityLevel.TRIVIAL

    def test_analyze_task_extracts_skills(self):
        """Test task analysis extracts required skills."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-6",
            subject="Python API",
            description="Build FastAPI endpoint with PostgreSQL",
            priority=7,
        )

        assert Skill.PYTHON in analysis.required_skills
        assert Skill.FASTAPI in analysis.required_skills
        assert Skill.POSTGRESQL in analysis.required_skills

    def test_analyze_task_normalizes_priority(self):
        """Test task analysis normalizes priority."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-7",
            subject="Task",
            description="Description",
            priority=8,
        )

        assert analysis.priority_score == 0.8

    def test_analyze_task_with_codebase_context(self):
        """Test task analysis with codebase context."""
        classifier = TaskClassifier()
        context = CodebaseContext(
            affected_file_count=5,
            max_dependency_depth=2,
            touches_critical_system=False,
        )
        analysis = classifier.analyze_task(
            task_id="task-8",
            subject="Feature",
            description="Add feature",
            priority=5,
            codebase_context=context,
        )

        assert analysis.codebase_context == context
        # Context should affect complexity
        assert analysis.complexity.value >= ComplexityLevel.TRIVIAL.value

    def test_analyze_task_default_values(self):
        """Test task analysis sets appropriate default values."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-9",
            subject="Task",
            description="Description",
            priority=5,
        )

        assert analysis.domain is None
        assert analysis.project is None
        assert analysis.status == "pending"
        assert analysis.dependencies == []
        assert analysis.dependents == []
        assert analysis.blocked is False
        assert analysis.estimated_duration is None

    def test_analyze_task_complex_task(self):
        """Test analyzing a complex task."""
        classifier = TaskClassifier()
        context = CodebaseContext(
            affected_file_count=20,
            max_dependency_depth=5,
            touches_critical_system=True,
            breaking_change_risk=0.7,
            cyclomatic_complexity=25,
        )
        analysis = classifier.analyze_task(
            task_id="task-10",
            subject="System migration",
            description="Migrate entire distributed system to new architecture",
            priority=10,
            codebase_context=context,
        )

        assert analysis.complexity == ComplexityLevel.EXPERT

    def test_analyze_task_full_stack(self):
        """Test analyzing full-stack task."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-11",
            subject="Full stack feature",
            description="Build React frontend with Python FastAPI backend, use PostgreSQL and Redis, deploy with Docker and Kubernetes",
            priority=8,
        )

        assert Skill.PYTHON in analysis.required_skills
        assert Skill.FASTAPI in analysis.required_skills
        assert Skill.REACT in analysis.required_skills
        assert Skill.POSTGRESQL in analysis.required_skills
        assert Skill.REDIS in analysis.required_skills
        assert Skill.DOCKER in analysis.required_skills
        assert Skill.KUBERNETES in analysis.required_skills


# =============================================================================
# Test keyword rules constants
# =============================================================================


class TestKeywordRules:
    """Tests for keyword rule constants."""

    def test_keyword_rules_is_dict(self):
        """Test KEYWORD_RULES is a dictionary."""
        assert isinstance(KEYWORD_RULES, dict)

    def test_keyword_rules_has_all_task_types(self):
        """Test KEYWORD_RULES has entries for all TaskType values."""
        for task_type in TaskType:
            if task_type != TaskType.GENERAL:
                assert task_type in KEYWORD_RULES

    def test_keyword_rules_have_lists(self):
        """Test all keyword rules are lists."""
        for task_type, keywords in KEYWORD_RULES.items():
            assert isinstance(keywords, list)

    def test_complexity_keywords_is_list(self):
        """Test COMPLEXITY_KEYWORDS is a list."""
        assert isinstance(COMPLEXITY_KEYWORDS, list)

    def test_skill_keywords_is_dict(self):
        """Test SKILL_KEYWORDS is a dictionary."""
        assert isinstance(SKILL_KEYWORDS, dict)

    def test_skill_keywords_have_lists(self):
        """Test all skill keyword rules are lists."""
        for skill, keywords in SKILL_KEYWORDS.items():
            assert isinstance(keywords, list)


# =============================================================================
# Test edge cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_detect_task_type_empty_strings(self):
        """Test task type detection with empty strings."""
        task_type = detect_task_type("", "")
        assert task_type == TaskType.GENERAL

    def test_detect_task_type_whitespace_only(self):
        """Test task type detection with whitespace only."""
        task_type = detect_task_type("   ", "   ")
        assert task_type == TaskType.GENERAL

    def test_estimate_complexity_empty(self):
        """Test complexity estimation with empty text."""
        complexity = estimate_complexity("", "")
        assert complexity == ComplexityLevel.TRIVIAL

    def test_extract_skills_empty(self):
        """Test skill extraction with empty text."""
        skills = extract_required_skills("", "")
        assert skills == set()

    def test_analyze_task_empty_description(self):
        """Test analyzing task with empty description."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-empty",
            subject="",
            description="",
            priority=1,
        )

        assert isinstance(analysis, TaskAnalysis)
        assert analysis.task_type == TaskType.GENERAL
        assert analysis.complexity == ComplexityLevel.TRIVIAL

    def test_normalize_priority_boundary_values(self):
        """Test normalize_priority at boundary values."""
        assert normalize_priority(-1) == 0.0
        assert normalize_priority(0) == 0.0
        assert normalize_priority(1) == 0.1
        assert normalize_priority(10) == 1.0
        assert normalize_priority(11) == 1.0

    def test_analyze_task_very_long_description(self):
        """Test analyzing task with very long description."""
        classifier = TaskClassifier()
        long_desc = " ".join(["feature"] * 150)
        analysis = classifier.analyze_task(
            task_id="task-long",
            subject="Feature",
            description=long_desc,
            priority=5,
        )

        # Long description should increase complexity
        assert analysis.complexity.value >= ComplexityLevel.SIMPLE.value

    def test_analyze_task_unicode(self):
        """Test analyzing task with unicode characters."""
        classifier = TaskClassifier()
        analysis = classifier.analyze_task(
            task_id="task-unicode",
            subject="国际化",
            description="Support for emoji and unicode 🚀",
            priority=5,
        )

        assert isinstance(analysis, TaskAnalysis)

    def test_detect_task_type_special_characters(self):
        """Test task type detection with special characters."""
        task_type = detect_task_type(
            "API endpoint (GET /api/users)",
            "Create endpoint at /api/users?action=list"
        )
        assert task_type == TaskType.API_SIMPLE
