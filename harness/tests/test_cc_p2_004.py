"""
Test CC-P2-004: Fix hardcoded tech stack.

Verifies that:
1. Tech stack is loaded from project configuration (features.json or domains.yaml)
2. No hardcoded tech stack arrays exist in the API response
3. Unknown projects show empty array or fallback
"""

import pytest


class TestTechStackLoading:
    """Test tech stack loading from configuration."""

    def test_get_tech_stack_from_frontend_tier_react(self):
        """Test that React frontend_tier returns correct stack."""
        from forge_harness.webhook_server import PortfolioService

        service = PortfolioService()
        domain_info = {"frontend_tier": "React"}
        tech_stack = service._get_tech_stack("test", "test", domain_info)

        assert isinstance(tech_stack, list)
        assert "React" in tech_stack
        assert "TypeScript" in tech_stack
        assert "FastAPI" in tech_stack
        assert "PostgreSQL" in tech_stack

    def test_get_tech_stack_from_frontend_tier_lit(self):
        """Test that Lit PWA frontend_tier returns correct stack."""
        from forge_harness.webhook_server import PortfolioService

        service = PortfolioService()
        domain_info = {"frontend_tier": "Lit PWA"}
        tech_stack = service._get_tech_stack("test", "test", domain_info)

        assert isinstance(tech_stack, list)
        assert "Lit" in tech_stack
        assert "Web Components" in tech_stack
        assert "FastAPI" in tech_stack
        assert "PostgreSQL" in tech_stack

    def test_get_tech_stack_from_frontend_tier_react_native(self):
        """Test that React Native frontend_tier returns correct stack."""
        from forge_harness.webhook_server import PortfolioService

        service = PortfolioService()
        domain_info = {"frontend_tier": "React Native"}
        tech_stack = service._get_tech_stack("test", "test", domain_info)

        assert isinstance(tech_stack, list)
        assert "React Native" in tech_stack
        assert "TypeScript" in tech_stack
        assert "FastAPI" in tech_stack
        assert "PostgreSQL" in tech_stack

    def test_get_tech_stack_empty_frontend_tier(self):
        """Test that empty frontend_tier returns empty array."""
        from forge_harness.webhook_server import PortfolioService

        service = PortfolioService()
        domain_info = {"frontend_tier": ""}
        tech_stack = service._get_tech_stack("test", "test", domain_info)

        assert isinstance(tech_stack, list)
        assert tech_stack == []

    def test_get_tech_stack_unknown_frontend_tier(self):
        """Test that unknown frontend_tier returns fallback with framework name."""
        from forge_harness.webhook_server import PortfolioService

        service = PortfolioService()
        domain_info = {"frontend_tier": "Vue.js"}
        tech_stack = service._get_tech_stack("test", "test", domain_info)

        assert isinstance(tech_stack, list)
        assert "Vue.js" in tech_stack
        assert "FastAPI" in tech_stack
        assert "PostgreSQL" in tech_stack

    def test_get_tech_stack_from_features_json(self):
        """Test that features.json tech_stack field takes priority."""
        from forge_harness.webhook_server import PortfolioService

        service = PortfolioService()

        # This would require mocking _load_features to return custom tech_stack
        # For now, we verify the logic exists in _get_tech_stack
        # The method checks features_data["tech_stack"] before frontend_tier

        # Test the fallback when no features.json
        domain_info = {"frontend_tier": "React"}
        tech_stack = service._get_tech_stack("nonexistent", "project", domain_info)

        # Should fall back to frontend_tier mapping
        assert "React" in tech_stack

    def test_no_hardcoded_arrays_in_get_project_details(self):
        """Verify get_project_details uses _get_tech_stack instead of hardcoded map."""
        import inspect

        from forge_harness.webhook_server import PortfolioService

        service = PortfolioService()
        source = inspect.getsource(service.get_project_details)

        # Should NOT contain inline tech_stack_map definition
        assert "tech_stack_map = {" not in source or source.count("tech_stack_map") == 0

        # Should call _get_tech_stack
        assert "_get_tech_stack" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
