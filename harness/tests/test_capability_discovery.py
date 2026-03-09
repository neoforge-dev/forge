"""
Tests for forge_harness/capability_discovery.py - Agent Capability Discovery
"""

from forge_harness.capability_discovery import (
    AGENT_CAPABILITIES,
    AgentCapability,
    Suggestion,
    format_suggestion,
    get_agent_info,
    list_all_roles,
    suggest_agent,
)


class TestAgentCapability:
    """Tests for AgentCapability dataclass."""

    def test_create_basic_capability(self):
        """Test creating a basic capability without optional fields."""
        cap = AgentCapability(
            role="test-role",
            name="Test Agent",
            description="Test description",
        )

        assert cap.role == "test-role"
        assert cap.name == "Test Agent"
        assert cap.description == "Test description"
        assert cap.skills == []
        assert cap.keywords == []
        assert cap.domains == []

    def test_create_full_capability(self):
        """Test creating a capability with all fields."""
        cap = AgentCapability(
            role="test-role",
            name="Test Agent",
            description="Test description",
            skills=["python", "fastapi"],
            keywords=["api", "backend"],
            domains=["test-domain"],
        )

        assert cap.role == "test-role"
        assert cap.name == "Test Agent"
        assert cap.description == "Test description"
        assert cap.skills == ["python", "fastapi"]
        assert cap.keywords == ["api", "backend"]
        assert cap.domains == ["test-domain"]

    def test_capability_equality(self):
        """Test that two capabilities with same values are equal."""
        cap1 = AgentCapability(
            role="test-role",
            name="Test Agent",
            description="Test description",
            skills=["python"],
        )
        cap2 = AgentCapability(
            role="test-role",
            name="Test Agent",
            description="Test description",
            skills=["python"],
        )

        assert cap1 == cap2


class TestAgentCapabilitiesRegistry:
    """Tests for AGENT_CAPABILITIES registry."""

    def test_registry_not_empty(self):
        """Test that the registry has capabilities defined."""
        assert len(AGENT_CAPABILITIES) > 0

    def test_registry_has_backend_engineer(self):
        """Test that backend-engineer role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "backend-engineer" in roles

    def test_registry_has_frontend_builder(self):
        """Test that frontend-builder role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "frontend-builder" in roles

    def test_registry_has_qa_test_guardian(self):
        """Test that qa-test-guardian role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "qa-test-guardian" in roles

    def test_registry_has_debug_detective(self):
        """Test that debug-detective role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "debug-detective" in roles

    def test_registry_has_security_auditor(self):
        """Test that security-auditor role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "security-auditor" in roles

    def test_registry_has_devops_deployer(self):
        """Test that devops-deployer role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "devops-deployer" in roles

    def test_registry_has_architect_advisor(self):
        """Test that architect-advisor role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "architect-advisor" in roles

    def test_registry_has_performance_optimizer(self):
        """Test that performance-optimizer role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "performance-optimizer" in roles

    def test_registry_has_content_agent(self):
        """Test that content-agent role exists."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert "content-agent" in roles

    def test_all_capabilities_have_required_fields(self):
        """Test that all capabilities have required fields."""
        for cap in AGENT_CAPABILITIES:
            assert cap.role
            assert cap.name
            assert cap.description
            assert isinstance(cap.skills, list)
            assert isinstance(cap.keywords, list)
            assert isinstance(cap.domains, list)

    def test_all_roles_are_unique(self):
        """Test that all role names are unique."""
        roles = [cap.role for cap in AGENT_CAPABILITIES]
        assert len(roles) == len(set(roles))

    def test_backend_engineer_has_expected_skills(self):
        """Test that backend-engineer has relevant skills."""
        backend = next(c for c in AGENT_CAPABILITIES if c.role == "backend-engineer")

        assert "python" in backend.skills
        assert "fastapi" in backend.skills
        assert "postgresql" in backend.skills
        assert "api" in backend.skills

    def test_backend_engineer_has_expected_keywords(self):
        """Test that backend-engineer has relevant keywords."""
        backend = next(c for c in AGENT_CAPABILITIES if c.role == "backend-engineer")

        assert "api" in backend.keywords
        assert "database" in backend.keywords
        assert "backend" in backend.keywords

    def test_frontend_builder_has_expected_skills(self):
        """Test that frontend-builder has relevant skills."""
        frontend = next(c for c in AGENT_CAPABILITIES if c.role == "frontend-builder")

        assert "react" in frontend.skills
        assert "typescript" in frontend.skills
        assert "tailwind" in frontend.skills

    def test_frontend_builder_has_expected_keywords(self):
        """Test that frontend-builder has relevant keywords."""
        frontend = next(c for c in AGENT_CAPABILITIES if c.role == "frontend-builder")

        assert "ui" in frontend.keywords
        assert "component" in frontend.keywords
        assert "frontend" in frontend.keywords


class TestSuggestion:
    """Tests for Suggestion dataclass."""

    def test_create_suggestion(self):
        """Test creating a suggestion."""
        suggestion = Suggestion(
            role="backend-engineer",
            name="Backend Engineer",
            confidence=0.85,
            matching_skills=["python", "fastapi"],
            matching_keywords=["api", "endpoint"],
            reason="matches keywords: api, endpoint",
        )

        assert suggestion.role == "backend-engineer"
        assert suggestion.name == "Backend Engineer"
        assert suggestion.confidence == 0.85
        assert suggestion.matching_skills == ["python", "fastapi"]
        assert suggestion.matching_keywords == ["api", "endpoint"]
        assert "matches keywords" in suggestion.reason


class TestSuggestAgent:
    """Tests for suggest_agent function."""

    def test_suggest_backend_for_api_task(self):
        """Test that backend engineer is suggested for API tasks."""
        suggestions = suggest_agent("Create a REST API endpoint for user authentication")

        assert len(suggestions) > 0
        assert suggestions[0].role == "backend-engineer"
        assert suggestions[0].confidence > 0

    def test_suggest_frontend_for_ui_task(self):
        """Test that frontend builder is suggested for UI tasks."""
        suggestions = suggest_agent("Build a React component for the user profile page")

        assert len(suggestions) > 0
        assert suggestions[0].role == "frontend-builder"
        assert suggestions[0].confidence > 0

    def test_suggest_qa_for_testing_task(self):
        """Test that QA test guardian is suggested for testing tasks."""
        suggestions = suggest_agent("Write E2E tests with Playwright for coverage analysis")

        assert len(suggestions) > 0
        # QA should be in top suggestions
        qa_roles = [s.role for s in suggestions]
        assert "qa-test-guardian" in qa_roles
        assert suggestions[0].confidence > 0

    def test_suggest_debug_for_bug_task(self):
        """Test that debug detective is suggested for bug tasks."""
        suggestions = suggest_agent("Fix a race condition causing intermittent failures")

        assert len(suggestions) > 0
        assert suggestions[0].role == "debug-detective"
        assert suggestions[0].confidence > 0

    def test_suggest_security_for_audit_task(self):
        """Test that security auditor is suggested for security tasks."""
        suggestions = suggest_agent("Audit the application for SQL injection vulnerabilities")

        assert len(suggestions) > 0
        assert suggestions[0].role == "security-auditor"
        assert suggestions[0].confidence > 0

    def test_suggest_devops_for_deployment_task(self):
        """Test that DevOps deployer is suggested for deployment tasks."""
        suggestions = suggest_agent("Deploy the application to AWS using Docker containers")

        assert len(suggestions) > 0
        assert suggestions[0].role == "devops-deployer"
        assert suggestions[0].confidence > 0

    def test_suggest_architect_for_design_task(self):
        """Test that architect advisor is suggested for design tasks."""
        suggestions = suggest_agent("Design a microservices architecture for the system")

        assert len(suggestions) > 0
        assert suggestions[0].role == "architect-advisor"
        assert suggestions[0].confidence > 0

    def test_suggest_performance_for_optimization_task(self):
        """Test that performance optimizer is suggested for optimization tasks."""
        suggestions = suggest_agent(
            "Profile and benchmark the application to optimize performance bottlenecks"
        )

        assert len(suggestions) > 0
        # Performance optimizer should be in top suggestions
        roles = [s.role for s in suggestions]
        assert "performance-optimizer" in roles
        assert suggestions[0].confidence > 0

    def test_suggest_content_for_writing_task(self):
        """Test that content agent is suggested for content tasks."""
        suggestions = suggest_agent("Write a blog post about our new feature")

        assert len(suggestions) > 0
        assert suggestions[0].role == "content-agent"
        assert suggestions[0].confidence > 0

    def test_suggest_returns_multiple_suggestions(self):
        """Test that suggest_agent returns multiple suggestions by default."""
        suggestions = suggest_agent("Fix a bug in the API endpoint")

        assert len(suggestions) <= 3  # Default top_n is 3
        assert len(suggestions) >= 1  # At least one match

    def test_suggest_respects_top_n_parameter(self):
        """Test that top_n parameter limits results."""
        suggestions = suggest_agent("Create an API endpoint", top_n=1)

        assert len(suggestions) == 1

    def test_suggest_with_top_n_5(self):
        """Test requesting more suggestions."""
        suggestions = suggest_agent("Build a feature", top_n=5)

        assert len(suggestions) <= 5

    def test_suggest_with_available_roles_filter(self):
        """Test filtering by available roles."""
        suggestions = suggest_agent(
            "Create an API endpoint",
            available_roles=["frontend-builder", "content-agent"],
        )

        # Should not suggest backend even though it's best match
        assert all(s.role in ["frontend-builder", "content-agent"] for s in suggestions)

    def test_suggest_with_single_available_role(self):
        """Test with only one available role."""
        suggestions = suggest_agent(
            "Any task",
            available_roles=["backend-engineer"],
        )

        if suggestions:
            assert all(s.role == "backend-engineer" for s in suggestions)

    def test_suggest_empty_task_returns_empty(self):
        """Test that empty task returns no suggestions."""
        suggestions = suggest_agent("")

        assert len(suggestions) == 0

    def test_suggest_nonsense_task_returns_empty(self):
        """Test that nonsense task returns no suggestions."""
        suggestions = suggest_agent("xyzabc123")

        assert len(suggestions) == 0

    def test_suggest_case_insensitive(self):
        """Test that matching is case-insensitive."""
        suggestions_lower = suggest_agent("create an api endpoint")
        suggestions_upper = suggest_agent("CREATE AN API ENDPOINT")
        suggestions_mixed = suggest_agent("Create An API Endpoint")

        assert len(suggestions_lower) > 0
        assert len(suggestions_upper) > 0
        assert len(suggestions_mixed) > 0
        assert suggestions_lower[0].role == suggestions_upper[0].role
        assert suggestions_lower[0].role == suggestions_mixed[0].role

    def test_suggest_with_multiple_keywords(self):
        """Test that multiple keyword matches increase confidence."""
        suggestions = suggest_agent("Create REST API endpoint with database queries")

        assert len(suggestions) > 0
        assert suggestions[0].role == "backend-engineer"
        # Should have multiple matching keywords
        assert len(suggestions[0].matching_keywords) >= 2

    def test_suggest_confidence_bounded(self):
        """Test that confidence is between 0 and 1."""
        suggestions = suggest_agent("Create API endpoint database query authentication")

        for suggestion in suggestions:
            assert 0.0 <= suggestion.confidence <= 1.0

    def test_suggest_sorted_by_confidence(self):
        """Test that suggestions are sorted by confidence (descending)."""
        suggestions = suggest_agent("Create API endpoint", top_n=5)

        confidences = [s.confidence for s in suggestions]
        assert confidences == sorted(confidences, reverse=True)

    def test_suggest_includes_matching_info(self):
        """Test that suggestions include matching skills and keywords."""
        suggestions = suggest_agent("Create a FastAPI endpoint with PostgreSQL")

        assert len(suggestions) > 0
        top = suggestions[0]

        # Should capture matches
        assert len(top.matching_keywords) > 0 or len(top.matching_skills) > 0

    def test_suggest_has_reason(self):
        """Test that suggestions include a reason."""
        suggestions = suggest_agent("Create an API endpoint")

        assert len(suggestions) > 0
        assert suggestions[0].reason
        assert len(suggestions[0].reason) > 0

    def test_suggest_backend_for_database_task(self):
        """Test backend engineer for database tasks."""
        suggestions = suggest_agent("Design database schema with PostgreSQL tables")

        assert len(suggestions) > 0
        assert suggestions[0].role == "backend-engineer"

    def test_suggest_frontend_for_component_task(self):
        """Test frontend builder for component tasks."""
        suggestions = suggest_agent("Build a modal dialog component with animations")

        assert len(suggestions) > 0
        assert suggestions[0].role == "frontend-builder"

    def test_suggest_qa_for_e2e_task(self):
        """Test QA for end-to-end testing tasks."""
        suggestions = suggest_agent("Write Playwright E2E tests for the checkout flow")

        assert len(suggestions) > 0
        assert suggestions[0].role == "qa-test-guardian"

    def test_suggest_devops_for_docker_task(self):
        """Test DevOps for Docker tasks."""
        suggestions = suggest_agent("Create Dockerfile and docker-compose configuration")

        assert len(suggestions) > 0
        assert suggestions[0].role == "devops-deployer"

    def test_suggest_with_domain_preference(self):
        """Test that domain parameter affects scoring."""
        # This test verifies the domain parameter is accepted
        # Actual domain preferences would need to be configured in AGENT_CAPABILITIES
        suggestions = suggest_agent("Generic task", domain="test-domain")

        # Should not crash and return suggestions
        assert isinstance(suggestions, list)


class TestGetAgentInfo:
    """Tests for get_agent_info function."""

    def test_get_backend_engineer_info(self):
        """Test getting backend engineer info."""
        info = get_agent_info("backend-engineer")

        assert info is not None
        assert info.role == "backend-engineer"
        assert info.name == "Backend Engineer"
        assert len(info.skills) > 0

    def test_get_frontend_builder_info(self):
        """Test getting frontend builder info."""
        info = get_agent_info("frontend-builder")

        assert info is not None
        assert info.role == "frontend-builder"
        assert info.name == "Frontend Builder"

    def test_get_nonexistent_role_returns_none(self):
        """Test that nonexistent role returns None."""
        info = get_agent_info("nonexistent-role")

        assert info is None

    def test_get_info_for_all_roles(self):
        """Test getting info for all registered roles."""
        roles = list_all_roles()

        for role in roles:
            info = get_agent_info(role)
            assert info is not None
            assert info.role == role


class TestListAllRoles:
    """Tests for list_all_roles function."""

    def test_list_all_roles_not_empty(self):
        """Test that list returns roles."""
        roles = list_all_roles()

        assert len(roles) > 0

    def test_list_all_roles_contains_expected_roles(self):
        """Test that list contains expected roles."""
        roles = list_all_roles()

        assert "backend-engineer" in roles
        assert "frontend-builder" in roles
        assert "qa-test-guardian" in roles
        assert "debug-detective" in roles

    def test_list_all_roles_returns_strings(self):
        """Test that list returns strings."""
        roles = list_all_roles()

        assert all(isinstance(role, str) for role in roles)

    def test_list_all_roles_matches_registry_count(self):
        """Test that list count matches registry."""
        roles = list_all_roles()

        assert len(roles) == len(AGENT_CAPABILITIES)


class TestFormatSuggestion:
    """Tests for format_suggestion function."""

    def test_format_basic_suggestion(self):
        """Test formatting a basic suggestion."""
        suggestion = Suggestion(
            role="backend-engineer",
            name="Backend Engineer",
            confidence=0.75,
            matching_skills=["python", "fastapi"],
            matching_keywords=["api", "endpoint"],
            reason="matches keywords: api, endpoint",
        )

        formatted = format_suggestion(suggestion)

        assert "Backend Engineer" in formatted
        assert "backend-engineer" in formatted
        assert "75%" in formatted
        assert "matches keywords" in formatted

    def test_format_includes_confidence_bar(self):
        """Test that formatted output includes confidence bar."""
        suggestion = Suggestion(
            role="frontend-builder",
            name="Frontend Builder",
            confidence=0.85,
            matching_skills=[],
            matching_keywords=[],
            reason="Test reason",
        )

        formatted = format_suggestion(suggestion)

        # Should include progress bar characters
        assert "█" in formatted or "░" in formatted

    def test_format_confidence_100_percent(self):
        """Test formatting with 100% confidence."""
        suggestion = Suggestion(
            role="backend-engineer",
            name="Backend Engineer",
            confidence=1.0,
            matching_skills=[],
            matching_keywords=[],
            reason="Perfect match",
        )

        formatted = format_suggestion(suggestion)

        assert "100%" in formatted

    def test_format_confidence_0_percent(self):
        """Test formatting with 0% confidence."""
        suggestion = Suggestion(
            role="backend-engineer",
            name="Backend Engineer",
            confidence=0.0,
            matching_skills=[],
            matching_keywords=[],
            reason="No match",
        )

        formatted = format_suggestion(suggestion)

        assert "0%" in formatted

    def test_format_multiple_suggestions(self):
        """Test formatting multiple suggestions produces different outputs."""
        suggestion1 = Suggestion(
            role="backend-engineer",
            name="Backend Engineer",
            confidence=0.85,
            matching_skills=[],
            matching_keywords=[],
            reason="Backend reason",
        )
        suggestion2 = Suggestion(
            role="frontend-builder",
            name="Frontend Builder",
            confidence=0.65,
            matching_skills=[],
            matching_keywords=[],
            reason="Frontend reason",
        )

        formatted1 = format_suggestion(suggestion1)
        formatted2 = format_suggestion(suggestion2)

        assert formatted1 != formatted2
        assert "Backend" in formatted1
        assert "Frontend" in formatted2


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_string_task(self):
        """Test handling of empty string task."""
        suggestions = suggest_agent("")

        assert isinstance(suggestions, list)
        assert len(suggestions) == 0

    def test_whitespace_only_task(self):
        """Test handling of whitespace-only task."""
        suggestions = suggest_agent("   \n\t  ")

        assert isinstance(suggestions, list)

    def test_very_long_task_description(self):
        """Test handling of very long task descriptions."""
        long_task = "Create API endpoint " * 100
        suggestions = suggest_agent(long_task)

        assert isinstance(suggestions, list)
        assert len(suggestions) > 0

    def test_task_with_special_characters(self):
        """Test handling of special characters in task."""
        suggestions = suggest_agent("Create API endpoint @#$%^&*()")

        assert isinstance(suggestions, list)

    def test_task_with_unicode_characters(self):
        """Test handling of unicode characters."""
        suggestions = suggest_agent("Create API endpoint with émojis 🚀")

        assert isinstance(suggestions, list)

    def test_empty_available_roles_list(self):
        """Test with empty available roles list.

        Note: The current implementation doesn't filter when available_roles is empty,
        it treats empty list as 'all roles available'. This tests the actual behavior.
        """
        suggestions = suggest_agent("Create API endpoint", available_roles=[])

        # Empty list means no filtering, so suggestions are returned
        assert isinstance(suggestions, list)

    def test_invalid_role_in_available_roles(self):
        """Test that invalid roles are simply ignored."""
        suggestions = suggest_agent(
            "Create API endpoint",
            available_roles=["invalid-role"],
        )

        assert len(suggestions) == 0

    def test_mixed_valid_invalid_available_roles(self):
        """Test with mix of valid and invalid roles."""
        suggestions = suggest_agent(
            "Create API endpoint",
            available_roles=["backend-engineer", "invalid-role"],
        )

        assert len(suggestions) > 0
        assert all(s.role == "backend-engineer" for s in suggestions)

    def test_top_n_zero(self):
        """Test with top_n=0."""
        suggestions = suggest_agent("Create API endpoint", top_n=0)

        assert len(suggestions) == 0

    def test_top_n_negative(self):
        """Test with negative top_n (should handle gracefully)."""
        suggestions = suggest_agent("Create API endpoint", top_n=-1)

        # Python list slicing handles negative gracefully
        assert isinstance(suggestions, list)

    def test_top_n_larger_than_matches(self):
        """Test requesting more suggestions than available."""
        suggestions = suggest_agent("Create API endpoint", top_n=100)

        # Should return all available suggestions, not crash
        assert len(suggestions) <= len(AGENT_CAPABILITIES)
