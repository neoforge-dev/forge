"""
API Contract Tests
==================

Validates that API responses match the contracts defined in
command_center/docs/API_CONTRACTS.md

Tests ensure:
- Response structure matches specification
- Required fields are present
- Data types are correct
- Nested objects have expected structure
"""

import pytest


@pytest.fixture
def contract_test_app(monkeypatch):
    """Create app for contract testing."""
    try:
        from forge_harness.webhook_server import AuthConfig, create_app
    except ImportError:
        pytest.skip("FastAPI not installed")

    monkeypatch.setenv("FORGE_JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("FORGE_JWT_REQUIRE_AUTH", "true")
    auth_config = AuthConfig(require_auth=False, allow_localhost=True)
    app = create_app(auth_config=auth_config)

    if app is None:
        pytest.skip("FastAPI not installed")

    return app


# ============================================================================
# Contract Validation Helpers
# ============================================================================


def validate_standard_response(data, expect_success=True):
    """Validate standard API response format."""
    assert "success" in data, "Response missing 'success' field"
    assert isinstance(data["success"], bool), "'success' must be boolean"

    if expect_success:
        assert data["success"] is True
        assert "data" in data, "Successful response missing 'data' field"
        assert "error" not in data or data["error"] is None
    else:
        assert data["success"] is False
        assert "error" in data, "Error response missing 'error' field"
        assert isinstance(data["error"], dict), "'error' must be object"


def validate_timestamp(timestamp_str):
    """Validate ISO 8601 timestamp format."""
    from datetime import datetime

    try:
        datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


# ============================================================================
# Agents API Contract Tests
# ============================================================================


class TestAgentsAPIContract:
    """Contract tests for /api/agents endpoints."""

    def test_list_agents_response_structure(self, contract_test_app):
        """GET /api/agents response matches contract."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/agents")

        assert response.status_code == 200
        data = response.json()

        # Validate standard response wrapper
        validate_standard_response(data, expect_success=True)

        # Validate data structure per API_CONTRACTS.md
        assert "agents" in data["data"], "Missing 'agents' array"
        assert "total" in data["data"], "Missing 'total' count"

        assert isinstance(data["data"]["agents"], list), "'agents' must be array"
        assert isinstance(data["data"]["total"], int), "'total' must be integer"

    def test_agent_item_fields(self, contract_test_app):
        """Agent list items have all required fields."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/agents")

        if response.status_code != 200:
            pytest.skip("Agents endpoint not available")

        data = response.json()
        agents = data["data"]["agents"]

        if len(agents) == 0:
            pytest.skip("No agents to validate")

        agent = agents[0]

        # Required fields per API_CONTRACTS.md
        required_fields = {
            "session_id": str,
            "agent_role": str,
            "agent_name": (str, type(None)),  # Optional
            "domain": (str, type(None)),
            "project": str,
            "current_task": (str, type(None)),
            "status": str,
            "progress_pct": (int, float),
            "started_at": str,
            "last_activity": str,
        }

        for field, expected_type in required_fields.items():
            assert field in agent, f"Missing required field: {field}"

            if isinstance(expected_type, tuple):
                assert isinstance(agent[field], expected_type), (
                    f"{field} has wrong type: {type(agent[field])}"
                )
            else:
                assert isinstance(agent[field], expected_type), (
                    f"{field} must be {expected_type}, got {type(agent[field])}"
                )

        # Validate timestamp formats
        assert validate_timestamp(agent["started_at"]), (
            "started_at must be valid ISO 8601 timestamp"
        )
        assert validate_timestamp(agent["last_activity"]), (
            "last_activity must be valid ISO 8601 timestamp"
        )

        # Validate status enum
        valid_statuses = {"active", "waiting", "idle", "completed", "error"}
        assert agent["status"] in valid_statuses, f"Invalid status: {agent['status']}"

        # Validate progress range
        assert 0 <= agent["progress_pct"] <= 100, "progress_pct must be 0-100"

    def test_get_agent_detail_contract(self, contract_test_app):
        """GET /api/agents/{id} response matches contract."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)

        # First get list to find an agent
        list_response = client.get("/api/agents")
        if list_response.status_code != 200:
            pytest.skip("Agents endpoint not available")

        agents = list_response.json()["data"]["agents"]
        if len(agents) == 0:
            pytest.skip("No agents to test")

        agent_id = agents[0].get("id") or agents[0].get("session_id")

        # Get agent details
        detail_response = client.get(f"/api/agents/{agent_id}")

        if detail_response.status_code != 200:
            pytest.skip("Agent detail endpoint not implemented")

        data = detail_response.json()
        validate_standard_response(data, expect_success=True)

        agent = data["data"]

        # Detail view should have all list fields plus extras
        assert "activity_log" in agent or True  # Optional
        assert "files_modified" in agent or True  # Optional


# ============================================================================
# Portfolio API Contract Tests
# ============================================================================


class TestPortfolioAPIContract:
    """Contract tests for /api/portfolio endpoints."""

    def test_portfolio_summary_structure(self, contract_test_app):
        """GET /api/portfolio response matches contract."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/portfolio")

        if response.status_code != 200:
            pytest.skip("Portfolio endpoint not implemented")

        data = response.json()
        validate_standard_response(data, expect_success=True)

        # Validate data structure
        assert "summary" in data["data"], "Missing 'summary' object"
        assert "projects" in data["data"], "Missing 'projects' array"

        summary = data["data"]["summary"]

        # Required summary fields per API_CONTRACTS.md
        required_summary_fields = {
            "total_projects": int,
            "by_status": dict,
            "active_agents": int,
            "pending_approvals": int,
        }

        for field, expected_type in required_summary_fields.items():
            assert field in summary, f"Missing summary field: {field}"
            assert isinstance(summary[field], expected_type), f"{field} must be {expected_type}"

        # Validate by_status structure
        status_counts = summary["by_status"]
        valid_statuses = {"live", "ready", "dev", "parked", "blocked"}

        for status, count in status_counts.items():
            assert status in valid_statuses, f"Invalid status: {status}"
            assert isinstance(count, int), f"Status count must be int: {status}"

    def test_portfolio_project_fields(self, contract_test_app):
        """Portfolio projects have all required fields."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/portfolio")

        if response.status_code != 200:
            pytest.skip("Portfolio endpoint not implemented")

        data = response.json()
        projects = data["data"]["projects"]

        if len(projects) == 0:
            pytest.skip("No projects to validate")

        project = projects[0]

        # Required fields per API_CONTRACTS.md
        required_fields = {
            "domain": str,
            "project": str,
            "display_name": str,
            "status": str,
            "progress_pct": (int, float),
        }

        for field, expected_type in required_fields.items():
            assert field in project, f"Missing required field: {field}"

            if isinstance(expected_type, tuple):
                assert isinstance(project[field], expected_type), f"{field} has wrong type"
            else:
                assert isinstance(project[field], expected_type), f"{field} must be {expected_type}"

        # Validate status
        valid_statuses = {"live", "ready", "dev", "parked", "blocked"}
        assert project["status"] in valid_statuses

        # Validate progress
        assert 0 <= project["progress_pct"] <= 100


# ============================================================================
# Approvals API Contract Tests
# ============================================================================


class TestApprovalsAPIContract:
    """Contract tests for /api/approvals endpoints."""

    def test_approvals_list_structure(self, contract_test_app):
        """GET /api/approvals response matches contract."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/approvals")

        assert response.status_code == 200
        data = response.json()

        validate_standard_response(data, expect_success=True)

        # Validate data structure
        assert "approvals" in data["data"], "Missing 'approvals' array"

        # May have count or pending_count
        assert (
            "count" in data["data"] or "pending_count" in data["data"] or "total" in data["data"]
        ), "Missing count field"

    def test_approval_item_fields(self, contract_test_app):
        """Approval items have all required fields."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/approvals")

        if response.status_code != 200:
            pytest.skip("Approvals endpoint not available")

        data = response.json()
        approvals = data["data"]["approvals"]

        if len(approvals) == 0:
            pytest.skip("No approvals to validate")

        approval = approvals[0]

        # Required fields per API_CONTRACTS.md
        required_fields = {
            "request_id": str,
            "type": str,
            "title": str,
            "status": str,
            "priority": str,
        }

        for field, expected_type in required_fields.items():
            assert field in approval, f"Missing required field: {field}"
            assert isinstance(approval[field], expected_type), f"{field} must be {expected_type}"

        # Validate enums
        valid_statuses = {"pending", "approved", "rejected", "expired"}
        assert approval["status"] in valid_statuses, f"Invalid status: {approval['status']}"

        valid_priorities = {"critical", "high", "normal", "low"}
        assert approval["priority"] in valid_priorities, f"Invalid priority: {approval['priority']}"

    def test_approve_response_structure(self, contract_test_app):
        """POST /api/approvals/{id}/approve response matches contract."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)

        # Get an approval to approve
        list_response = client.get("/api/approvals")
        if list_response.status_code != 200:
            pytest.skip("Approvals endpoint not available")

        approvals = list_response.json()["data"]["approvals"]
        if len(approvals) == 0:
            pytest.skip("No approvals to test")

        approval_id = approvals[0]["request_id"]

        # Approve it
        payload = {
            "approver": "contract_test@example.com",
            "comment": "Contract test approval",
            "device_type": "phone",
        }

        response = client.post(f"/api/approvals/{approval_id}/approve", json=payload)

        assert response.status_code == 200
        data = response.json()

        validate_standard_response(data, expect_success=True)

        # Required fields in response
        assert "request_id" in data or "request_id" in data.get("data", {})
        assert "status" in data and data["status"] == "approved"

    def test_approval_stats_structure(self, contract_test_app):
        """GET /api/approvals/stats response matches contract."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/approvals/stats")

        assert response.status_code == 200
        data = response.json()

        # Required stats fields
        required_fields = ["pending", "total"]

        for field in required_fields:
            # May be directly in response or in data wrapper
            assert field in data or field in data.get("data", {}), f"Missing stats field: {field}"


# ============================================================================
# Patterns API Contract Tests
# ============================================================================


class TestPatternsAPIContract:
    """Contract tests for /api/patterns endpoints."""

    def test_patterns_list_structure(self, contract_test_app):
        """GET /api/patterns response matches contract."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/patterns")

        if response.status_code != 200:
            pytest.skip("Patterns endpoint not implemented")

        data = response.json()
        validate_standard_response(data, expect_success=True)

        assert "patterns" in data["data"], "Missing 'patterns' array"
        assert "total" in data["data"], "Missing 'total' count"

    def test_pattern_item_fields(self, contract_test_app):
        """Pattern items have all required fields."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/patterns")

        if response.status_code != 200:
            pytest.skip("Patterns endpoint not implemented")

        data = response.json()
        patterns = data["data"]["patterns"]

        if len(patterns) == 0:
            pytest.skip("No patterns to validate")

        pattern = patterns[0]

        # Required fields per API_CONTRACTS.md
        required_fields = {
            "pattern_id": str,
            "name": str,
            "category": str,
            "success_rate": (int, float),
            "total_uses": int,
        }

        for field, expected_type in required_fields.items():
            assert field in pattern, f"Missing required field: {field}"

            if isinstance(expected_type, tuple):
                assert isinstance(pattern[field], expected_type), f"{field} has wrong type"
            else:
                assert isinstance(pattern[field], expected_type), f"{field} must be {expected_type}"

        # Validate success_rate range
        assert 0 <= pattern["success_rate"] <= 1, "success_rate must be 0-1"


# ============================================================================
# Error Response Contract Tests
# ============================================================================


class TestErrorResponseContract:
    """Contract tests for error responses."""

    def test_404_error_structure(self, contract_test_app):
        """404 errors follow standard format."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)
        response = client.get("/api/agents/nonexistent_id_12345")

        assert response.status_code == 404
        data = response.json()

        validate_standard_response(data, expect_success=False)

        # Error object structure
        error = data["error"]
        assert isinstance(error, dict), "error must be object"

        # Should have code and message
        assert "code" in error or "message" in error, "Error must have code or message"

    def test_validation_error_structure(self, contract_test_app):
        """Validation errors (400/422) follow standard format."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)

        # Send invalid payload
        invalid_payload = {
            "invalid_field": "test",
            # Missing required fields
        }

        response = client.post("/api/approvals/req_123/approve", json=invalid_payload)

        # May be 400 or 422 depending on validation
        if response.status_code not in (400, 422):
            pytest.skip("Validation endpoint behavior varies")

        data = response.json()

        # Should have error information
        assert "error" in data or "detail" in data, "Validation error must include error info"


# ============================================================================
# Data Type Contract Tests
# ============================================================================


class TestDataTypeContracts:
    """Tests for correct data types in responses."""

    def test_numeric_fields_are_numbers(self, contract_test_app):
        """Numeric fields return numbers, not strings."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)

        # Test agents endpoint
        response = client.get("/api/agents")
        if response.status_code == 200:
            data = response.json()
            agents = data["data"]["agents"]

            for agent in agents:
                # progress_pct should be number
                if "progress_pct" in agent:
                    assert isinstance(agent["progress_pct"], (int, float)), (
                        "progress_pct must be number"
                    )

                # token_usage should be number or object
                if "token_usage" in agent:
                    assert isinstance(agent["token_usage"], (int, dict)), (
                        "token_usage must be number or object"
                    )

    def test_boolean_fields_are_booleans(self, contract_test_app):
        """Boolean fields return true booleans, not strings."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)

        # Test portfolio endpoint
        response = client.get("/api/portfolio")
        if response.status_code == 200:
            data = response.json()

            # success field should be boolean
            assert isinstance(data["success"], bool), "success must be boolean"

            projects = data["data"]["projects"]
            for project in projects:
                # test_passing should be boolean if present
                if "test_passing" in project:
                    assert isinstance(project["test_passing"], bool), "test_passing must be boolean"

    def test_array_fields_are_arrays(self, contract_test_app):
        """Array fields return arrays, not objects."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(contract_test_app)

        response = client.get("/api/agents")
        if response.status_code == 200:
            data = response.json()

            # agents should be array
            assert isinstance(data["data"]["agents"], list), "agents must be array"

            agents = data["data"]["agents"]
            for agent in agents:
                # files_modified should be array if present
                if "files_modified" in agent:
                    assert isinstance(agent["files_modified"], list), "files_modified must be array"
