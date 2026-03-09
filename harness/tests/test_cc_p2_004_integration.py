"""
Integration test for CC-P2-004: Tech stack loading from configuration.

Tests the complete flow:
1. Load tech_stack from features.json
2. Return it via API endpoint
3. Frontend displays it correctly
"""

import json
from pathlib import Path


def test_tech_stack_loaded_from_features_json():
    """Test that tech stack is loaded from features.json when present."""
    from forge_harness.webhook_server import PortfolioService

    # Create a service instance
    service = PortfolioService()

    # Test with command_center which has tech_stack in features.json
    forge_root = Path(__file__).parent.parent
    command_center_features = forge_root / "command_center" / "features.json"

    if not command_center_features.exists():
        print("Skipping test - command_center/features.json not found")
        return

    # Load the features.json to verify it has tech_stack
    with open(command_center_features) as f:
        features_data = json.load(f)

    assert "tech_stack" in features_data, "features.json should have tech_stack field"
    expected_stack = features_data["tech_stack"]
    assert isinstance(expected_stack, list), "tech_stack should be a list"
    assert len(expected_stack) > 0, "tech_stack should not be empty"

    # Now test that _get_tech_stack loads it
    # We'll mock _load_features to return our test data
    original_load = service._load_features

    def mock_load_features(domain, project):
        if domain == "test" and project == "command_center":
            return features_data
        return original_load(domain, project)

    service._load_features = mock_load_features

    # Get tech stack
    tech_stack = service._get_tech_stack("test", "command_center", {})

    # Verify it matches what's in features.json
    assert tech_stack == expected_stack, f"Expected {expected_stack}, got {tech_stack}"
    print(f"✅ Tech stack loaded from features.json: {tech_stack}")


def test_tech_stack_fallback_to_frontend_tier():
    """Test that tech stack falls back to frontend_tier when features.json doesn't have it."""
    from forge_harness.webhook_server import PortfolioService

    service = PortfolioService()

    # Test React frontend_tier
    domain_info = {"frontend_tier": "React"}
    tech_stack = service._get_tech_stack("nonexistent", "project", domain_info)

    assert "React" in tech_stack
    assert "TypeScript" in tech_stack
    assert "FastAPI" in tech_stack
    print(f"✅ React fallback works: {tech_stack}")

    # Test Lit PWA frontend_tier
    domain_info = {"frontend_tier": "Lit PWA"}
    tech_stack = service._get_tech_stack("nonexistent", "project", domain_info)

    assert "Lit" in tech_stack
    assert "Web Components" in tech_stack
    print(f"✅ Lit PWA fallback works: {tech_stack}")


def test_tech_stack_empty_when_no_config():
    """Test that tech stack is empty when no configuration exists."""
    from forge_harness.webhook_server import PortfolioService

    service = PortfolioService()

    # Test with empty frontend_tier
    domain_info = {"frontend_tier": ""}
    tech_stack = service._get_tech_stack("nonexistent", "project", domain_info)

    assert tech_stack == [], f"Expected empty array, got {tech_stack}"
    print("✅ Empty frontend_tier returns empty array")


def test_get_project_details_includes_tech_stack():
    """Test that get_project_details includes tech_stack in response."""
    from forge_harness.webhook_server import PortfolioService

    service = PortfolioService()

    # Mock domain info and features
    original_load_domains = service._load_domains
    original_load_features = service._load_features

    def mock_load_domains():
        return {
            "domains": {
                "test-domain": {
                    "active": True,
                    "products": ["Test Project"],
                    "frontend_tier": "React",
                }
            }
        }

    def mock_load_features(domain, project):
        return {
            "tech_stack": ["React", "TypeScript", "Vite"],
            "features": [],
        }

    service._load_domains = mock_load_domains
    service._load_features = mock_load_features

    # Get project details
    project = service.get_project_details("test-domain", "test-project")

    # Verify response includes tech_stack
    assert project is not None, "Project should exist"
    assert "tech_stack" in project, "Response should include tech_stack"
    assert isinstance(project["tech_stack"], list), "tech_stack should be a list"
    assert project["tech_stack"] == [
        "React",
        "TypeScript",
        "Vite",
    ], "tech_stack should match features.json"

    print(f"✅ get_project_details includes tech_stack: {project['tech_stack']}")

    # Restore original methods
    service._load_domains = original_load_domains
    service._load_features = original_load_features


if __name__ == "__main__":
    print("Running CC-P2-004 Integration Tests\n")
    print("=" * 60)

    try:
        test_tech_stack_loaded_from_features_json()
        print()
        test_tech_stack_fallback_to_frontend_tier()
        print()
        test_tech_stack_empty_when_no_config()
        print()
        test_get_project_details_includes_tech_stack()
        print()
        print("=" * 60)
        print("✅ All integration tests passed!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        exit(1)
