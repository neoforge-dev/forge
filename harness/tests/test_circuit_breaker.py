"""Tests for circuit breaker pattern."""

from __future__ import annotations

import asyncio

import pytest

from forge_harness.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    circuit_breaker,
    get_circuit_breaker,
    list_circuit_breakers,
    reset_circuit_breaker,
)


class TestCircuitBreakerBasics:
    """Basic circuit breaker tests."""

    @pytest.fixture
    def breaker(self):
        """Create a fresh circuit breaker for testing."""
        return CircuitBreaker(
            name="test",
            failure_threshold=3,
            failure_window=10.0,
            recovery_timeout=1.0,
            success_threshold=2,
        )

    def test_initial_state_is_closed(self, breaker):
        """Circuit starts in closed state."""
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_success_stays_closed(self, breaker):
        """Successful calls keep circuit closed."""
        async with breaker:
            pass  # Simulates successful call

        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.successful_requests == 1

    @pytest.mark.asyncio
    async def test_failures_trip_circuit(self, breaker):
        """After threshold failures, circuit opens."""
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("Service unavailable")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN
        assert breaker.stats.failed_requests == 3

    @pytest.mark.asyncio
    async def test_open_circuit_rejects_requests(self, breaker):
        """Open circuit rejects requests without calling service."""
        # Trip the circuit
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("Service unavailable")
            except ConnectionError:
                pass

        # Now should reject
        with pytest.raises(CircuitOpenError) as exc_info:
            async with breaker:
                pass

        assert exc_info.value.name == "test"
        assert breaker.stats.rejected_requests == 1

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self, breaker):
        """Circuit transitions to half-open after recovery timeout."""
        # Trip the circuit
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("Service unavailable")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(1.1)

        # Should transition to half-open on next request
        async with breaker:
            pass  # Successful call

        assert breaker.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self, breaker):
        """Enough successes in half-open close the circuit."""
        # Trip the circuit
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("Service unavailable")
            except ConnectionError:
                pass

        await asyncio.sleep(1.1)

        # Two successes should close circuit (success_threshold=2)
        async with breaker:
            pass
        async with breaker:
            pass

        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self, breaker):
        """Failure in half-open reopens the circuit."""
        # Trip the circuit
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("Service unavailable")
            except ConnectionError:
                pass

        await asyncio.sleep(1.1)

        # First request transitions to half-open, then fails
        try:
            async with breaker:
                raise ConnectionError("Still broken")
        except ConnectionError:
            pass

        assert breaker.state == CircuitState.OPEN


class TestCircuitBreakerEdgeCases:
    """Edge case tests for circuit breaker."""

    @pytest.mark.asyncio
    async def test_excluded_exceptions_not_counted(self):
        """Excluded exceptions don't count as failures."""
        breaker = CircuitBreaker(
            name="test_exclude",
            failure_threshold=2,
            excluded_exceptions=(ValueError,),
        )

        # These shouldn't count as failures
        for _ in range(5):
            try:
                async with breaker:
                    raise ValueError("Expected error")
            except ValueError:
                pass

        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.failed_requests == 0

    @pytest.mark.asyncio
    async def test_failures_expire_outside_window(self):
        """Failures outside the window don't count."""
        breaker = CircuitBreaker(
            name="test_window",
            failure_threshold=3,
            failure_window=0.5,  # 500ms window
        )

        # Two failures
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.CLOSED

        # Wait for failures to expire
        await asyncio.sleep(0.6)

        # One more failure shouldn't trip
        try:
            async with breaker:
                raise ConnectionError("fail")
        except ConnectionError:
            pass

        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_call_method(self):
        """Test the call() method for wrapping functions."""
        breaker = CircuitBreaker(name="test_call", failure_threshold=3)

        async def successful_func():
            return "success"

        async def failing_func():
            raise RuntimeError("failed")

        # Successful call
        result = await breaker.call(successful_func)
        assert result == "success"
        assert breaker.stats.successful_requests == 1

        # Failing call
        with pytest.raises(RuntimeError):
            await breaker.call(failing_func)
        assert breaker.stats.failed_requests == 1

    def test_to_dict(self):
        """Test circuit breaker serialization."""
        breaker = CircuitBreaker(name="test_dict", failure_threshold=5)
        data = breaker.to_dict()

        assert data["name"] == "test_dict"
        assert data["state"] == "closed"
        assert data["failure_threshold"] == 5
        assert "stats" in data


class TestCircuitBreakerDecorator:
    """Tests for the circuit_breaker decorator."""

    @pytest.mark.asyncio
    async def test_decorator_wraps_function(self):
        """Decorator properly wraps async functions."""
        call_count = 0

        @circuit_breaker("test_decorator", failure_threshold=2)
        async def my_function():
            nonlocal call_count
            call_count += 1
            return "done"

        result = await my_function()
        assert result == "done"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_decorator_tracks_failures(self):
        """Decorator tracks failures and trips circuit."""

        @circuit_breaker("test_decorator_fail", failure_threshold=2, recovery_timeout=10.0)
        async def failing_function():
            raise ConnectionError("down")

        # Trip the circuit
        for _ in range(2):
            try:
                await failing_function()
            except ConnectionError:
                pass

        # Should now reject
        with pytest.raises(CircuitOpenError):
            await failing_function()


class TestGlobalRegistry:
    """Tests for global circuit breaker registry."""

    def test_get_circuit_breaker_creates_new(self):
        """get_circuit_breaker creates new breaker if not exists."""
        breaker = get_circuit_breaker("test_new_breaker")
        assert breaker.name == "test_new_breaker"

    def test_get_circuit_breaker_returns_existing(self):
        """get_circuit_breaker returns existing breaker."""
        breaker1 = get_circuit_breaker("test_singleton")
        breaker2 = get_circuit_breaker("test_singleton")
        assert breaker1 is breaker2

    def test_list_circuit_breakers(self):
        """list_circuit_breakers returns all breakers."""
        get_circuit_breaker("test_list_1")
        get_circuit_breaker("test_list_2")
        breakers = list_circuit_breakers()

        names = [b["name"] for b in breakers]
        assert "test_list_1" in names
        assert "test_list_2" in names

    @pytest.mark.asyncio
    async def test_reset_circuit_breaker(self):
        """reset_circuit_breaker closes an open circuit."""
        breaker = get_circuit_breaker("test_reset", failure_threshold=2)

        # Trip the circuit
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("down")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Reset it
        result = reset_circuit_breaker("test_reset")
        assert result is True
        assert breaker.state == CircuitState.CLOSED

    def test_reset_nonexistent_returns_false(self):
        """Resetting non-existent breaker returns False."""
        result = reset_circuit_breaker("nonexistent_breaker")
        assert result is False


class TestCircuitStats:
    """Tests for circuit breaker statistics."""

    @pytest.mark.asyncio
    async def test_stats_track_correctly(self):
        """Stats are tracked accurately."""
        breaker = CircuitBreaker(name="test_stats", failure_threshold=3)

        # 3 successes
        for _ in range(3):
            async with breaker:
                pass

        # 2 failures
        for _ in range(2):
            try:
                async with breaker:
                    raise ConnectionError("fail")
            except ConnectionError:
                pass

        assert breaker.stats.total_requests == 5
        assert breaker.stats.successful_requests == 3
        assert breaker.stats.failed_requests == 2
        assert breaker.stats.last_success_time is not None
        assert breaker.stats.last_failure_time is not None

    def test_stats_to_dict(self):
        """Stats serialize correctly."""
        breaker = CircuitBreaker(name="test_stats_dict", failure_threshold=3)
        data = breaker.stats.to_dict()

        assert "total_requests" in data
        assert "successful_requests" in data
        assert "failed_requests" in data
        assert "success_rate" in data
        assert "recent_state_changes" in data


class TestDefaultBreakers:
    """Tests for pre-configured circuit breakers."""

    def test_default_breakers_exist(self):
        """Default breakers are created on import."""
        breakers = list_circuit_breakers()
        names = [b["name"] for b in breakers]

        # These should exist from setup_default_breakers()
        assert "code_atlas" in names
        assert "tech_diligence" in names
        assert "notion" in names
        assert "github" in names


class TestCircuitBreakerBridgeIntegration:
    """Test circuit breaker integration with bridge clients."""

    @pytest.mark.asyncio
    async def test_tech_diligence_bridge_uses_circuit_breaker(self):
        """Tech Diligence bridge wraps calls with circuit breaker."""
        from unittest.mock import AsyncMock, patch

        from forge_harness.meta_learning.bridges.tech_diligence import (
            TechDiligenceBridge,
        )

        # Reset the circuit breaker to clean state
        reset_circuit_breaker("tech_diligence")

        bridge = TechDiligenceBridge(
            base_url="http://test-diligence.local",
            enabled=True,
        )

        # Mock the HTTP client to fail
        with patch.object(bridge, "_get_client") as mock_client:
            mock_response = AsyncMock()
            mock_response.get = AsyncMock(side_effect=ConnectionError("Service down"))
            mock_client.return_value = mock_response

            # First few failures should still call the service
            for _ in range(5):
                signals = await bridge.get_signals("test", "project")
                assert signals.blocking_issues == []  # Empty signals on error

            # Now the circuit should be open
            breaker = get_circuit_breaker("tech_diligence")
            assert breaker.state == CircuitState.OPEN

            # Next call should be rejected without calling service
            call_count = mock_response.get.call_count
            signals = await bridge.get_signals("test", "project")
            assert signals.blocking_issues == []
            # Call count should not increase (circuit open)
            assert mock_response.get.call_count == call_count

    @pytest.mark.asyncio
    async def test_code_atlas_bridge_uses_circuit_breaker(self):
        """Code Atlas bridge wraps calls with circuit breaker."""
        from unittest.mock import AsyncMock, patch

        from forge_harness.meta_learning.bridges.code_atlas import CodeAtlasBridge

        # Reset the circuit breaker to clean state
        reset_circuit_breaker("code_atlas")

        bridge = CodeAtlasBridge(
            base_url="http://test-atlas.local",
            enabled=True,
        )

        # Track that circuit breaker is accessed
        breaker = get_circuit_breaker("code_atlas")
        initial_requests = breaker.stats.total_requests

        # Create a mock client that returns empty results
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = AsyncMock(return_value={"problems": [], "results": []})
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.post = AsyncMock(return_value=mock_response)

        # Patch _get_client to return our mock
        with patch.object(bridge, "_get_client", return_value=mock_client):
            signals = await bridge.get_signals("test", "project")
            assert signals.related_patterns == []

            # Verify circuit breaker was used (request count increased)
            assert breaker.stats.total_requests > initial_requests

    @pytest.mark.asyncio
    async def test_circuit_breaker_graceful_degradation(self):
        """Bridges return empty signals when circuit is open."""
        from unittest.mock import patch

        from forge_harness.meta_learning.bridges.tech_diligence import (
            TechDiligenceBridge,
        )

        # Reset and manually open the circuit
        reset_circuit_breaker("tech_diligence")
        breaker = get_circuit_breaker("tech_diligence")

        # Trip the circuit manually
        for _ in range(5):
            try:
                async with breaker:
                    raise ConnectionError("Service down")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Bridge should return empty signals without calling service
        bridge = TechDiligenceBridge(
            base_url="http://test-diligence.local",
            enabled=True,
        )

        with patch.object(bridge, "_get_client") as mock_client:
            signals = await bridge.get_signals("test", "project")
            # Should not have called the client (circuit open)
            mock_client.assert_not_called()
            # Should return empty signals
            assert signals.blocking_issues == []
            assert signals.warnings == []


class TestHealthEndpointCircuitState:
    """Test health endpoint includes circuit breaker state."""

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_circuits(self):
        """Health endpoint returns circuit breaker states."""
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/health/full")
        assert response.status_code == 200

        data = response.json()
        assert "circuits" in data

        # Verify default circuits are present
        circuits = data["circuits"]
        assert "tech_diligence" in circuits
        assert "code_atlas" in circuits

    @pytest.mark.asyncio
    async def test_health_endpoint_circuit_state_format(self):
        """Circuit state has correct format."""
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import create_app

        app = create_app()
        client = TestClient(app)

        response = client.get("/health/full")
        assert response.status_code == 200

        data = response.json()
        circuits = data["circuits"]

        # Check format of each circuit
        for circuit_name, circuit_state in circuits.items():
            assert "state" in circuit_state
            assert circuit_state["state"] in ["closed", "open", "half_open"]
            assert "failures" in circuit_state
            assert isinstance(circuit_state["failures"], int)
            assert "last_failure" in circuit_state
            # last_failure can be None or ISO string
            assert "retry_after" in circuit_state
            assert isinstance(circuit_state["retry_after"], (int, float))

    @pytest.mark.asyncio
    async def test_health_endpoint_shows_open_circuit(self):
        """Health endpoint shows when a circuit is open."""
        from fastapi.testclient import TestClient

        from forge_harness.webhook_server import create_app

        # Reset and trip a circuit
        reset_circuit_breaker("tech_diligence")
        breaker = get_circuit_breaker("tech_diligence")

        for _ in range(5):
            try:
                async with breaker:
                    raise ConnectionError("Service down")
            except ConnectionError:
                pass

        assert breaker.state == CircuitState.OPEN

        app = create_app()
        client = TestClient(app)

        response = client.get("/health/full")
        assert response.status_code == 200

        data = response.json()
        tech_diligence_circuit = data["circuits"]["tech_diligence"]

        assert tech_diligence_circuit["state"] == "open"
        assert tech_diligence_circuit["failures"] >= 5
        assert tech_diligence_circuit["retry_after"] > 0
