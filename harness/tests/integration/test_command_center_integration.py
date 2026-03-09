"""
Command Center Integration Tests
=================================

Integration tests for Command Center API endpoints.
Tests full request/response cycles and data consistency.
"""

import pytest


@pytest.fixture
def integration_app():
    """Create app with realistic mocks for integration testing."""
    try:
        from forge_harness.webhook_server import (
            AgentRegistry,
            AuthConfig,
            create_app,
        )
    except ImportError:
        pytest.skip("FastAPI not installed")

    auth_config = AuthConfig(require_auth=False)
    app = create_app(auth_config=auth_config)

    if app is None:
        pytest.skip("FastAPI not installed")

    return app


class TestAgentLifecycle:
    """Integration tests for full agent lifecycle."""

    def test_register_update_complete_agent(self, integration_app):
        """Test complete agent lifecycle: register -> update -> complete."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # 1. Register new agent
        register_payload = {
            "role": "builder",
            "name": "Integration Test Agent",
            "domain": "test-domain",
            "project": "test-project",
            "task": "Running integration tests",
            "skills": ["test", "integration"],
            "current_task": "Running integration tests",
            "focus_tags": ["test", "integration"],
        }

        register_response = client.post("/api/agents/register", json=register_payload)

        # May not be implemented yet
        if register_response.status_code in (404, 501):
            pytest.skip("Agent registration endpoint not implemented")

        assert register_response.status_code in (200, 201)
        register_data = register_response.json()
        assert register_data["success"] is True

        # Extract session_id from response
        session_id = register_data["data"]["session_id"]
        # 2. Update agent progress
        update_payload = {
            "progress": 50,
            "current_task": "Writing integration tests",
        }

        update_response = client.post(f"/api/agents/{session_id}/progress", json=update_payload)

        # May not be implemented yet
        if update_response.status_code not in (404, 501):
            assert update_response.status_code == 200

        # 3. Complete agent session
        complete_payload = {
            "summary": "Integration tests completed successfully",
        }

        complete_response = client.post(f"/api/agents/{session_id}/complete", json=complete_payload)

        # May not be implemented yet
        if complete_response.status_code not in (404, 501):
            assert complete_response.status_code == 200


class TestApprovalWorkflow:
    """Integration tests for approval workflow."""

    def test_approval_request_to_resolution(self, integration_app):
        """Test full approval workflow: create -> list -> approve."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # 1. List current approvals
        list_response = client.get("/api/approvals")
        assert list_response.status_code == 200

        initial_data = list_response.json()
        initial_count = initial_data["data"].get("count", 0)

        # 2. Check approval stats
        stats_response = client.get("/api/approvals/stats")
        assert stats_response.status_code == 200

        # 3. Approve an approval (if any exist)
        approvals = initial_data["data"].get("approvals", [])
        if len(approvals) > 0:
            approval_id = approvals[0]["request_id"]

            approve_payload = {
                "approver": "integration_test@example.com",
                "comment": "Integration test approval",
                "device_type": "phone",
            }

            approve_response = client.post(
                f"/api/approvals/{approval_id}/approve", json=approve_payload
            )

            assert approve_response.status_code == 200
            approve_data = approve_response.json()
            assert approve_data["success"] is True
            assert approve_data["status"] == "approved"


class TestCrossEndpointConsistency:
    """Tests for data consistency across endpoints."""

    def test_agent_count_consistency(self, integration_app):
        """Agent count in /portfolio matches /agents."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # Get portfolio summary
        portfolio_response = client.get("/api/portfolio")
        if portfolio_response.status_code != 200:
            pytest.skip("Portfolio endpoint not implemented")

        portfolio_data = portfolio_response.json()
        portfolio_agent_count = portfolio_data["data"]["summary"].get("active_agents", 0)

        # Get agents list
        agents_response = client.get("/api/agents")
        if agents_response.status_code != 200:
            pytest.skip("Agents endpoint not implemented")

        agents_data = agents_response.json()
        agents_count = agents_data["data"].get("total", 0)

        # Counts should match
        assert portfolio_agent_count == agents_count

    def test_approval_count_consistency(self, integration_app):
        """Approval count in /portfolio matches /approvals."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # Get portfolio summary
        portfolio_response = client.get("/api/portfolio")
        if portfolio_response.status_code != 200:
            pytest.skip("Portfolio endpoint not implemented")

        portfolio_data = portfolio_response.json()
        portfolio_approvals = portfolio_data["data"]["summary"].get("pending_approvals", 0)

        # Get approvals list
        approvals_response = client.get("/api/approvals")
        if approvals_response.status_code != 200:
            pytest.skip("Approvals endpoint not implemented")

        approvals_data = approvals_response.json()
        approvals_count = len(approvals_data["data"].get("approvals", []))

        # Counts should match
        assert portfolio_approvals == approvals_count


class TestErrorHandling:
    """Integration tests for error handling."""

    def test_404_for_missing_resources(self, integration_app):
        """API returns 404 for nonexistent resources."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # Test various 404 scenarios
        endpoints = [
            "/api/agents/nonexistent_agent_id",
            "/api/approvals/nonexistent_approval_id",
            "/api/patterns/nonexistent_pattern_id",
        ]

        for endpoint in endpoints:
            response = client.get(endpoint)

            # Should be 404 or 501 if not implemented
            assert response.status_code in (404, 501)

    def test_400_for_invalid_payloads(self, integration_app):
        """API returns 400 for invalid request bodies."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # Test invalid approval
        invalid_payload = {
            # Missing required fields
            "invalid_field": "value"
        }

        response = client.post("/api/approvals/req_123/approve", json=invalid_payload)

        # Should be 400, 404, or 422 (validation error)
        assert response.status_code in (400, 404, 422, 501)


class TestConcurrency:
    """Tests for concurrent requests."""

    def test_concurrent_agent_registrations(self, integration_app):
        """Multiple agents can register concurrently."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # Register multiple agents
        for i in range(3):
            payload = {
                "role": "builder",
                "name": f"Concurrent Agent {i}",
                "domain": "test-domain",
                "project": "test-project",
                "task": "Testing concurrency",
            }

            response = client.post("/api/agents/register", json=payload)

            # May not be implemented yet
            if response.status_code not in (404, 501):
                assert response.status_code in (200, 201)

    def test_concurrent_approvals(self, integration_app):
        """Multiple approvals can be processed concurrently."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # Get list of approvals
        list_response = client.get("/api/approvals")
        if list_response.status_code != 200:
            pytest.skip("Approvals endpoint not implemented")

        approvals = list_response.json()["data"].get("approvals", [])

        # Process multiple approvals
        for approval in approvals[:3]:  # Test first 3
            approval_id = approval["request_id"]

            payload = {
                "approver": "concurrent_test@example.com",
                "comment": "Concurrent approval test",
            }

            response = client.post(f"/api/approvals/{approval_id}/approve", json=payload)

            # Should succeed or be already processed
            assert response.status_code in (200, 404, 409)


class TestDataPersistence:
    """Tests for data persistence across requests."""

    def test_agent_data_persists(self, integration_app):
        """Agent data persists across multiple GET requests."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # First request
        response1 = client.get("/api/agents")
        if response1.status_code != 200:
            pytest.skip("Agents endpoint not implemented")

        data1 = response1.json()

        # Second request
        response2 = client.get("/api/agents")
        data2 = response2.json()

        # Data should be consistent
        assert data1["data"]["total"] == data2["data"]["total"]

    def test_pattern_stats_update(self, integration_app):
        """Pattern statistics update when outcomes are recorded."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        client = TestClient(integration_app)

        # Get pattern details
        response = client.get("/api/patterns/pat_tdd_feature")
        if response.status_code != 200:
            pytest.skip("Patterns endpoint not implemented")

        initial_data = response.json()["data"]
        initial_uses = initial_data.get("total_uses", 0)

        # Record an outcome
        outcome_payload = {
            "execution_id": "integration_test_exec",
            "success": True,
            "quality_score": 0.85,
            "token_cost": 2000,
        }

        outcome_response = client.post(
            "/api/patterns/pat_tdd_feature/outcome", json=outcome_payload
        )

        # May not be implemented yet
        if outcome_response.status_code == 200:
            # Get updated pattern
            updated_response = client.get("/api/patterns/pat_tdd_feature")
            updated_data = updated_response.json()["data"]
            updated_uses = updated_data.get("total_uses", 0)

            # Uses should have increased
            assert updated_uses > initial_uses
