"""Test GET /api/patterns/related endpoint (CC-P1-005).

NOTE: These tests are for an unimplemented feature. The related patterns
endpoint does not exist yet in webhook_server_main.py.
Tests are marked as xfail until CC-P1-005 is implemented.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from forge_harness.webhook_server import AuthConfig, Pattern, PatternStore, create_app

# Mark all tests in this module as expected failures until endpoint is implemented
pytestmark = pytest.mark.xfail(reason="CC-P1-005 GET /api/patterns/related not implemented")


@pytest.fixture
def temp_patterns_dir(tmp_path):
    """Create temporary patterns directory."""
    patterns_dir = tmp_path / ".forge/learning"
    patterns_dir.mkdir()
    return patterns_dir


@pytest.fixture
def pattern_store(temp_patterns_dir):
    """Create PatternStore with test patterns."""
    store = PatternStore(forge_root=temp_patterns_dir.parent)

    # Add test patterns with different categories and success rates
    patterns = [
        Pattern(
            id="feature-001",
            name="Feature Implementation Pattern",
            category="feature",
            template="Implement feature: {feature_name}",
            variables=["feature_name"],
            success_rate=0.95,
            uses=50,
            alpha=48,
            beta=3,
            version=1,
        ),
        Pattern(
            id="feature-002",
            name="Feature Testing Pattern",
            category="feature",
            template="Test feature: {feature_name}",
            variables=["feature_name"],
            success_rate=0.85,
            uses=30,
            alpha=26,
            beta=5,
            version=1,
        ),
        Pattern(
            id="deployment-001",
            name="Deployment Pattern",
            category="deployment",
            template="Deploy to {environment}",
            variables=["environment"],
            success_rate=0.90,
            uses=20,
            alpha=18,
            beta=2,
            version=1,
        ),
        Pattern(
            id="content-001",
            name="Content Creation Pattern",
            category="content",
            template="Create {content_type} for {project}",
            variables=["content_type", "project"],
            success_rate=0.75,
            uses=15,
            alpha=12,
            beta=4,
            version=1,
        ),
        Pattern(
            id="testing-001",
            name="Testing Pattern",
            category="testing",
            template="Run tests for {module}",
            variables=["module"],
            success_rate=0.98,
            uses=100,
            alpha=98,
            beta=2,
            version=1,
        ),
        Pattern(
            id="feature-003",
            name="Low Success Feature Pattern",
            category="feature",
            template="Experimental feature: {feature_name}",
            variables=["feature_name"],
            success_rate=0.40,
            uses=5,
            alpha=2,
            beta=3,
            version=1,
        ),
    ]

    for pattern in patterns:
        store._patterns[pattern.id] = pattern

    store._save()
    return store


@pytest.fixture
def client(pattern_store):
    """Create test client with pattern store."""

    with patch("forge_harness.webhook_server.get_pattern_store", return_value=pattern_store):
        # Disable auth for testing
        auth_config = AuthConfig(require_auth=False)
        app = create_app(pattern_store=pattern_store, auth_config=auth_config)
        yield TestClient(app)


def test_related_patterns_endpoint_exists(client):
    """Test that GET /api/patterns/related endpoint exists."""
    response = client.get("/api/patterns/related")
    assert response.status_code == 200


def test_related_patterns_filter_by_approval_type(client):
    """Test filtering patterns by approval_type (category match)."""
    response = client.get("/api/patterns/related?approval_type=feature&limit=5")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert "patterns" in data["data"]
    patterns = data["data"]["patterns"]

    # Should return feature patterns first (category match)
    assert len(patterns) > 0
    # First pattern should be a feature pattern with highest relevance
    first_pattern = patterns[0]
    assert first_pattern["category"] == "feature"


def test_related_patterns_sorted_by_relevance(client):
    """Test that patterns are sorted by relevance score."""
    response = client.get("/api/patterns/related?approval_type=feature&limit=5")

    assert response.status_code == 200
    data = response.json()
    patterns = data["data"]["patterns"]

    # Should have feature patterns at the top (category match bonus)
    feature_patterns = [p for p in patterns if p["category"] == "feature"]
    assert len(feature_patterns) > 0

    # Among feature patterns, higher success rate should rank higher
    if len(feature_patterns) >= 2:
        # feature-001 (0.95 success) should rank higher than feature-002 (0.85)
        # and both should rank higher than feature-003 (0.40)
        pattern_ids = [p["id"] for p in feature_patterns]
        # feature-001 should appear before feature-003
        if "feature-001" in pattern_ids and "feature-003" in pattern_ids:
            assert pattern_ids.index("feature-001") < pattern_ids.index("feature-003")


def test_related_patterns_limit_parameter(client):
    """Test that limit parameter works correctly."""
    response = client.get("/api/patterns/related?limit=3")

    assert response.status_code == 200
    data = response.json()
    patterns = data["data"]["patterns"]

    assert len(patterns) <= 3


def test_related_patterns_default_limit(client):
    """Test that default limit is 5."""
    response = client.get("/api/patterns/related")

    assert response.status_code == 200
    data = response.json()
    patterns = data["data"]["patterns"]

    # Should return max 5 patterns by default
    assert len(patterns) <= 5


def test_related_patterns_no_filters(client):
    """Test getting related patterns without filters."""
    response = client.get("/api/patterns/related")

    assert response.status_code == 200
    data = response.json()
    patterns = data["data"]["patterns"]

    # Should still return patterns, sorted by success_rate and usage
    assert len(patterns) > 0

    # Testing pattern (0.98 success, 100 uses) should rank very high
    pattern_ids = [p["id"] for p in patterns]
    # testing-001 should be in top results due to high success rate and usage
    assert "testing-001" in pattern_ids


def test_related_patterns_returns_required_fields(client):
    """Test that response includes all required fields."""
    response = client.get("/api/patterns/related?approval_type=feature&limit=5")

    assert response.status_code == 200
    data = response.json()
    patterns = data["data"]["patterns"]

    # Each pattern should have required fields
    for pattern in patterns:
        assert "id" in pattern or "pattern_id" in pattern
        assert "name" in pattern
        assert "category" in pattern
        assert "success_rate" in pattern
        assert "uses" in pattern or "total_uses" in pattern


def test_related_patterns_domain_parameter_accepted(client):
    """Test that domain parameter is accepted (for future filtering)."""
    # For now, domain filtering might not be implemented, but endpoint should accept it
    response = client.get("/api/patterns/related?domain=codeswiftr-com&limit=5")

    # Should not error
    assert response.status_code == 200


def test_related_patterns_project_parameter_accepted(client):
    """Test that project parameter is accepted (for future filtering)."""
    # For now, project filtering might not be implemented, but endpoint should accept it
    response = client.get("/api/patterns/related?project=interview-simulator&limit=5")

    # Should not error
    assert response.status_code == 200


def test_related_patterns_all_parameters(client):
    """Test using all parameters together."""
    response = client.get(
        "/api/patterns/related?"
        "approval_type=feature&"
        "domain=codeswiftr-com&"
        "project=interview-simulator&"
        "limit=3"
    )

    assert response.status_code == 200
    data = response.json()
    patterns = data["data"]["patterns"]

    # Should respect limit
    assert len(patterns) <= 3


def test_related_patterns_empty_result(tmp_path):
    """Test that endpoint handles no matching patterns gracefully."""
    from pathlib import Path

    # Create a client with empty pattern store
    empty_store = PatternStore(forge_root=Path(tmp_path))
    empty_store._patterns = {}

    # Disable auth for testing
    auth_config = AuthConfig(require_auth=False)
    app = create_app(pattern_store=empty_store, auth_config=auth_config)
    test_client = TestClient(app)

    response = test_client.get("/api/patterns/related?approval_type=feature")

    assert response.status_code == 200
    data = response.json()
    assert data["data"]["patterns"] == []
    assert data["data"]["total"] == 0


def test_related_patterns_scoring_weights(client):
    """Test that scoring weights work correctly."""
    response = client.get("/api/patterns/related?approval_type=feature&limit=10")

    assert response.status_code == 200
    data = response.json()
    patterns = data["data"]["patterns"]

    # feature-001: category match (+10) + success_rate*5 (0.95*5=4.75) + uses/100*2 (0.5*2=1.0) = ~15.75
    # feature-002: category match (+10) + success_rate*5 (0.85*5=4.25) + uses/100*2 (0.3*2=0.6) = ~14.85
    # feature-003: category match (+10) + success_rate*5 (0.40*5=2.0) + uses/100*2 (0.05*2=0.1) = ~12.1
    # testing-001: no match (0) + success_rate*5 (0.98*5=4.9) + uses/100*2 (1.0*2=2.0) = ~6.9

    # All feature patterns should rank higher than non-feature patterns
    feature_patterns = [p for p in patterns if p["category"] == "feature"]
    non_feature_patterns = [p for p in patterns if p["category"] != "feature"]

    if feature_patterns and non_feature_patterns:
        # At least one feature pattern should appear before any non-feature pattern
        first_feature_idx = min(i for i, p in enumerate(patterns) if p["category"] == "feature")
        first_non_feature_idx = min(i for i, p in enumerate(patterns) if p["category"] != "feature")
        assert first_feature_idx < first_non_feature_idx


def test_related_patterns_response_format(client):
    """Test that response follows standard API format."""
    response = client.get("/api/patterns/related?approval_type=feature")

    assert response.status_code == 200
    data = response.json()

    # Standard API response format
    assert "success" in data
    assert "data" in data
    assert isinstance(data["data"], dict)
    assert "patterns" in data["data"]
    assert "total" in data["data"]
    assert isinstance(data["data"]["patterns"], list)
    assert isinstance(data["data"]["total"], int)
