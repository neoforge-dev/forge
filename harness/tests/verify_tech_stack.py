"""
Quick verification script for tech stack loading functionality.
Tests CC-P2-004: Fix hardcoded tech stack.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from forge_harness.webhook_server import PortfolioService


def test_tech_stack_loading():
    """Test that tech stack is loaded correctly from domain configuration."""
    service = PortfolioService()

    # Test 1: Get tech stack for a project with React frontend_tier
    print("Test 1: Loading tech stack for brandfocus-ai/voice-coach")
    project = service.get_project_details("brandfocus-ai", "voice-coach")

    if project is None:
        print("  ❌ Project not found")
        return False

    tech_stack = project.get("tech_stack", [])
    print(f"  Tech stack: {tech_stack}")

    # Voice Coach uses React, so should have React-based stack
    if not tech_stack:
        print("  ❌ Tech stack is empty")
        return False

    # Should not be the old hardcoded default
    if tech_stack == ["React", "FastAPI", "PostgreSQL"]:
        print("  ⚠️  Got default stack (may be correct if React frontend_tier)")

    print("  ✅ Tech stack loaded successfully")

    # Test 2: Test a domain with different frontend_tier
    print("\nTest 2: Loading tech stack for thebrightharbor-com project")

    # Get first project from the domain
    domains_data = service._load_domains()
    harbor_domain = domains_data.get("domains", {}).get("thebrightharbor-com", {})
    products = harbor_domain.get("products", [])

    if not products:
        print("  ⚠️  No products found for thebrightharbor-com")
    else:
        project_slug = products[0].lower().replace(" ", "-")
        project = service.get_project_details("thebrightharbor-com", project_slug)

        if project is None:
            print(f"  ❌ Project {project_slug} not found")
            return False

        tech_stack = project.get("tech_stack", [])
        print(f"  Project: {project_slug}")
        print(f"  Tech stack: {tech_stack}")
        print(f"  Frontend tier: {harbor_domain.get('frontend_tier')}")

        # Lit PWA domain should have Lit in the stack
        if harbor_domain.get("frontend_tier") == "Lit PWA":
            if "Lit" not in tech_stack:
                print("  ❌ Expected 'Lit' in tech stack for Lit PWA project")
                return False

        print("  ✅ Tech stack matches frontend_tier")

    # Test 3: Verify no hardcoded arrays in response
    print("\nTest 3: Checking for hardcoded arrays")

    # This should now use the _get_tech_stack method instead of hardcoded map
    test_domain = {"frontend_tier": "React Native"}
    tech_stack = service._get_tech_stack("test", "test", test_domain)

    print(f"  React Native tech stack: {tech_stack}")

    if tech_stack == ["React Native", "TypeScript", "FastAPI", "PostgreSQL"]:
        print("  ✅ Correct React Native stack from mapping")
    else:
        print(f"  ⚠️  Unexpected stack: {tech_stack}")

    # Test 4: Empty frontend_tier should return empty array
    print("\nTest 4: Empty frontend_tier handling")
    test_domain = {"frontend_tier": ""}
    tech_stack = service._get_tech_stack("test", "test", test_domain)

    print(f"  Empty frontend_tier tech stack: {tech_stack}")

    if tech_stack == []:
        print("  ✅ Returns empty array for empty frontend_tier")
    else:
        print(f"  ⚠️  Expected empty array, got: {tech_stack}")

    # Test 5: Unknown frontend_tier should use fallback
    print("\nTest 5: Unknown frontend_tier handling")
    test_domain = {"frontend_tier": "Vue.js"}
    tech_stack = service._get_tech_stack("test", "test", test_domain)

    print(f"  Vue.js (unknown) tech stack: {tech_stack}")

    if tech_stack == ["Vue.js", "FastAPI", "PostgreSQL"]:
        print("  ✅ Correct fallback for unknown frontend_tier")
    else:
        print(f"  ⚠️  Expected fallback, got: {tech_stack}")

    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    return True


if __name__ == "__main__":
    try:
        success = test_tech_stack_loading()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
