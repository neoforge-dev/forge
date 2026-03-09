"""Test PUT /api/patterns/{pattern_id} endpoint (CC-P1-004).

NOTE: These tests are for an unimplemented feature. The PUT endpoint
for patterns does not exist yet in webhook_server_main.py.
Tests are marked as xfail until CC-P1-004 is implemented.
"""

import json
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from forge_harness.webhook_server import Pattern, PatternStore, create_app

# Mark all tests in this module as expected failures until endpoint is implemented
pytestmark = pytest.mark.xfail(reason="CC-P1-004 PUT /api/patterns/{id} not implemented")


@pytest.fixture
def temp_patterns_dir(tmp_path):
    """Create temporary patterns directory."""
    patterns_dir = tmp_path / ".forge/learning"
    patterns_dir.mkdir()
    return patterns_dir


@pytest.fixture
def pattern_store(temp_patterns_dir):
    """Create PatternStore with temporary directory."""
    store = PatternStore(forge_root=temp_patterns_dir.parent)

    # Add a test pattern
    pattern = Pattern(
        id="test-pattern-001",
        name="Test Pattern",
        category="testing",
        template="Test template: {var1}",
        variables=["var1"],
        success_rate=0.75,
        uses=10,
        alpha=8,
        beta=3,
        version=1,
    )
    store._patterns[pattern.id] = pattern
    store._save()

    return store


@pytest.fixture
def client(pattern_store):
    """Create test client with pattern store."""
    from forge_harness.webhook_server import AuthConfig
    # Use allow_localhost=True to bypass auth for test client (localhost)
    auth_config = AuthConfig(allow_localhost=True)
    with patch("forge_harness.webhook_server_main.get_pattern_store", return_value=pattern_store):
        app = create_app(pattern_store=pattern_store, auth_config=auth_config)
        yield TestClient(app)


def test_update_pattern_endpoint_exists(client):
    """Test that PUT /api/patterns/{pattern_id} endpoint exists."""
    response = client.put("/api/patterns/test-pattern-001", json={"name": "Updated Name"})
    # Should not return 404 or 405
    assert response.status_code in [200, 422], f"Unexpected status: {response.status_code}"


def test_update_pattern_name(client, pattern_store):
    """Test updating pattern name."""
    response = client.put("/api/patterns/test-pattern-001", json={"name": "Updated Pattern Name"})

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["data"]["name"] == "Updated Pattern Name"
    assert data["data"]["pattern_id"] == "test-pattern-001"

    # Verify pattern was updated in store
    pattern = pattern_store.get_pattern("test-pattern-001")
    assert pattern.name == "Updated Pattern Name"


def test_update_pattern_category(client, pattern_store):
    """Test updating pattern category."""
    response = client.put("/api/patterns/test-pattern-001", json={"category": "integration"})

    assert response.status_code == 200
    data = response.json()

    assert data["data"]["category"] == "integration"

    # Verify in store
    pattern = pattern_store.get_pattern("test-pattern-001")
    assert pattern.category == "integration"


def test_update_pattern_template(client, pattern_store):
    """Test updating pattern template."""
    new_template = "Updated template: {var1} and {var2}"
    response = client.put("/api/patterns/test-pattern-001", json={"template": new_template})

    assert response.status_code == 200
    data = response.json()

    assert data["data"]["template"] == new_template

    # Verify in store
    pattern = pattern_store.get_pattern("test-pattern-001")
    assert pattern.template == new_template


def test_update_pattern_variables(client, pattern_store):
    """Test updating pattern variables."""
    new_variables = ["var1", "var2", "var3"]
    response = client.put("/api/patterns/test-pattern-001", json={"variables": new_variables})

    assert response.status_code == 200
    data = response.json()

    assert data["data"]["variables"] == new_variables

    # Verify in store
    pattern = pattern_store.get_pattern("test-pattern-001")
    assert pattern.variables == new_variables


def test_update_pattern_multiple_fields(client, pattern_store):
    """Test updating multiple fields at once."""
    response = client.put(
        "/api/patterns/test-pattern-001",
        json={
            "name": "Multi-Update Pattern",
            "category": "multi",
            "template": "New template: {x}",
            "variables": ["x"],
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["data"]["name"] == "Multi-Update Pattern"
    assert data["data"]["category"] == "multi"
    assert data["data"]["template"] == "New template: {x}"
    assert data["data"]["variables"] == ["x"]


def test_update_pattern_preserves_stats(client, pattern_store):
    """Test that update preserves success_rate and usage statistics."""
    # Get original stats
    original_pattern = pattern_store.get_pattern("test-pattern-001")
    original_success_rate = original_pattern.success_rate
    original_uses = original_pattern.uses
    original_alpha = original_pattern.alpha
    original_beta = original_pattern.beta

    # Update pattern
    response = client.put("/api/patterns/test-pattern-001", json={"name": "Updated Name"})

    assert response.status_code == 200
    data = response.json()

    # Verify stats are preserved
    assert data["data"]["success_rate"] == original_success_rate
    assert data["data"]["uses"] == original_uses
    assert data["data"]["alpha"] == original_alpha
    assert data["data"]["beta"] == original_beta

    # Verify in store
    pattern = pattern_store.get_pattern("test-pattern-001")
    assert pattern.success_rate == original_success_rate
    assert pattern.uses == original_uses
    assert pattern.alpha == original_alpha
    assert pattern.beta == original_beta


def test_update_pattern_increments_version(client, pattern_store):
    """Test that update increments pattern version."""
    # Get original version
    original_pattern = pattern_store.get_pattern("test-pattern-001")
    original_version = original_pattern.version

    # Update pattern
    response = client.put("/api/patterns/test-pattern-001", json={"name": "Version Test"})

    assert response.status_code == 200
    data = response.json()

    # Verify version incremented
    assert data["data"]["version"] == original_version + 1

    # Verify in store
    pattern = pattern_store.get_pattern("test-pattern-001")
    assert pattern.version == original_version + 1


def test_update_pattern_updates_timestamp(client, pattern_store):
    """Test that update updates the updated_at timestamp."""
    # Get original timestamp
    original_pattern = pattern_store.get_pattern("test-pattern-001")
    original_timestamp = original_pattern.updated_at

    # Update pattern
    response = client.put("/api/patterns/test-pattern-001", json={"name": "Timestamp Test"})

    assert response.status_code == 200
    data = response.json()

    # Verify timestamp changed
    assert data["data"]["updated_at"] != original_timestamp

    # Verify in store
    pattern = pattern_store.get_pattern("test-pattern-001")
    assert pattern.updated_at != original_timestamp


def test_update_pattern_not_found(client):
    """Test updating non-existent pattern returns 404."""
    response = client.put("/api/patterns/non-existent-pattern", json={"name": "Should Fail"})

    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"].lower()


def test_update_pattern_partial_update(client, pattern_store):
    """Test partial update only updates provided fields."""
    # Get original pattern
    original_pattern = pattern_store.get_pattern("test-pattern-001")
    original_category = original_pattern.category
    original_template = original_pattern.template
    original_variables = original_pattern.variables

    # Update only name
    response = client.put("/api/patterns/test-pattern-001", json={"name": "Partial Update"})

    assert response.status_code == 200
    data = response.json()

    # Verify only name changed
    assert data["data"]["name"] == "Partial Update"
    assert data["data"]["category"] == original_category
    assert data["data"]["template"] == original_template
    assert data["data"]["variables"] == original_variables


@patch("forge_harness.webhook_server.get_event_bus")
def test_update_pattern_emits_sse_event(mock_event_bus, client):
    """Test that update emits SSE event: pattern.updated."""
    mock_bus = Mock()
    mock_event_bus.return_value = mock_bus

    response = client.put("/api/patterns/test-pattern-001", json={"name": "SSE Test"})

    assert response.status_code == 200

    # Verify event was published
    mock_bus.publish.assert_called_once()
    call_args = mock_bus.publish.call_args
    assert call_args[0][0] == "pattern.updated"
    assert call_args[0][1]["pattern_id"] == "test-pattern-001"
    assert call_args[0][1]["name"] == "SSE Test"


def test_update_pattern_persists_to_file(client, temp_patterns_dir):
    """Test that update persists to .forge/learning/patterns.json."""
    patterns_file = temp_patterns_dir / "patterns.json"

    # Update pattern
    response = client.put("/api/patterns/test-pattern-001", json={"name": "Persistence Test"})

    assert response.status_code == 200

    # Verify file was written
    assert patterns_file.exists()

    # Read and verify content
    with open(patterns_file) as f:
        data = json.load(f)

    assert "patterns" in data
    patterns = {p["pattern_id"]: p for p in data["patterns"]}
    assert "test-pattern-001" in patterns
    assert patterns["test-pattern-001"]["name"] == "Persistence Test"


def test_update_pattern_empty_body(client):
    """Test that empty update body still increments version."""
    response = client.put("/api/patterns/test-pattern-001", json={})

    assert response.status_code == 200
    data = response.json()

    # Version should still increment even with no field changes
    assert data["data"]["version"] == 2


def test_update_pattern_null_values_ignored(client, pattern_store):
    """Test that null values in request body are ignored."""
    original_pattern = pattern_store.get_pattern("test-pattern-001")
    original_name = original_pattern.name

    response = client.put(
        "/api/patterns/test-pattern-001",
        json={
            "name": None,  # Should be ignored
            "category": "updated",  # Should be applied
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Name should remain unchanged
    assert data["data"]["name"] == original_name
    # Category should be updated
    assert data["data"]["category"] == "updated"
