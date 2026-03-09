"""Integration tests for health checks and agent supervisor.

Tests the connection between HealthCheckRegistry and AgentSupervisor
for automatic agent restart on health failure.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from forge_harness.agent_supervisor import AgentSpec, AgentSupervisor, RestartPolicy
from forge_harness.health_checks import (
    HealthCheckRegistry,
    HealthStatus,
    ServiceHealth,
    get_health_registry,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def health_registry():
    """Create a fresh HealthCheckRegistry for testing."""
    return HealthCheckRegistry()


@pytest.fixture
def forge_root(tmp_path):
    """Create a temporary forge root directory."""
    root = tmp_path / "forge"
    root.mkdir()
    return root


@pytest.fixture
def supervisor(forge_root, tmp_path):
    """Create an AgentSupervisor instance for testing."""
    state_file = tmp_path / "supervisor_state.json"
    spawn_script = tmp_path / "spawn.sh"
    spawn_script.write_text("#!/bin/bash\necho 'spawning agent'")
    spawn_script.chmod(0o755)

    return AgentSupervisor(
        forge_root=forge_root,
        state_file=state_file,
        spawn_script=spawn_script,
    )


@pytest.fixture
def agent_spec():
    """Create a sample AgentSpec for testing."""
    return AgentSpec(
        agent_id="test-agent-001",
        role="backend-engineer",
        task="Fix authentication bug",
        session="forge",
        window="tech",
        health_check_interval=0.1,
        restart_delay=0.1,
    )


# ============================================================================
# HealthCheckRegistry Failure Tracking Tests
# ============================================================================


class TestHealthFailureTracking:
    """Test health check failure tracking."""

    @pytest.mark.asyncio
    async def test_failure_count_increments(self, health_registry):
        """Failure count increments on unhealthy status."""

        async def failing_check():
            return ServiceHealth(
                name="failing",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                message="Service down",
            )

        health_registry.register("failing", failing_check)

        # First failure
        await health_registry.check_service("failing")
        assert health_registry.get_failure_count("failing") == 1

        # Second failure
        await health_registry.check_service("failing")
        assert health_registry.get_failure_count("failing") == 2

        # Third failure
        await health_registry.check_service("failing")
        assert health_registry.get_failure_count("failing") == 3

    @pytest.mark.asyncio
    async def test_failure_count_resets_on_success(self, health_registry):
        """Failure count resets when service becomes healthy."""
        check_count = 0

        async def intermittent_check():
            nonlocal check_count
            check_count += 1
            if check_count <= 2:
                return ServiceHealth(
                    name="intermittent",
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=0,
                )
            return ServiceHealth(
                name="intermittent",
                status=HealthStatus.HEALTHY,
                latency_ms=10.0,
            )

        health_registry.register("intermittent", intermittent_check)

        # Two failures
        await health_registry.check_service("intermittent")
        await health_registry.check_service("intermittent")
        assert health_registry.get_failure_count("intermittent") == 2

        # Success - should reset
        await health_registry.check_service("intermittent")
        assert health_registry.get_failure_count("intermittent") == 0

    @pytest.mark.asyncio
    async def test_degraded_status_resets_failure_count(self, health_registry):
        """Degraded status (not unhealthy) resets failure count."""

        async def degraded_check():
            return ServiceHealth(
                name="slow",
                status=HealthStatus.DEGRADED,
                latency_ms=6000.0,
            )

        health_registry.register("slow", degraded_check)

        # Should not increment failure count
        await health_registry.check_service("slow")
        assert health_registry.get_failure_count("slow") == 0

    @pytest.mark.asyncio
    async def test_callback_invoked_on_max_failures(self, health_registry):
        """Callback is invoked when MAX_CONSECUTIVE_FAILURES is reached."""
        callback_calls = []

        def failure_callback(service_name: str, failure_count: int, health: ServiceHealth):
            callback_calls.append((service_name, failure_count, health.status))

        health_registry.on_health_failure(failure_callback)

        async def failing_check():
            return ServiceHealth(
                name="failing",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
            )

        health_registry.register("failing", failing_check)

        # First two failures - no callback
        await health_registry.check_service("failing")
        await health_registry.check_service("failing")
        assert len(callback_calls) == 0

        # Third failure - callback triggered
        await health_registry.check_service("failing")
        assert len(callback_calls) == 1
        assert callback_calls[0][0] == "failing"
        assert callback_calls[0][1] == 3
        assert callback_calls[0][2] == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_callback_invoked_on_subsequent_failures(self, health_registry):
        """Callback continues to be invoked on failures after threshold."""
        callback_count = 0

        def failure_callback(service_name: str, failure_count: int, health: ServiceHealth):
            nonlocal callback_count
            callback_count += 1

        health_registry.on_health_failure(failure_callback)

        async def failing_check():
            return ServiceHealth(
                name="failing",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
            )

        health_registry.register("failing", failing_check)

        # 5 failures - should trigger callback 3 times (at 3, 4, 5)
        for _ in range(5):
            await health_registry.check_service("failing")

        assert callback_count == 3

    @pytest.mark.asyncio
    async def test_multiple_callbacks_registered(self, health_registry):
        """Multiple callbacks can be registered and all are invoked."""
        callback1_called = False
        callback2_called = False

        def callback1(service_name: str, failure_count: int, health: ServiceHealth):
            nonlocal callback1_called
            callback1_called = True

        def callback2(service_name: str, failure_count: int, health: ServiceHealth):
            nonlocal callback2_called
            callback2_called = True

        health_registry.on_health_failure(callback1)
        health_registry.on_health_failure(callback2)

        async def failing_check():
            return ServiceHealth("failing", HealthStatus.UNHEALTHY, 0)

        health_registry.register("failing", failing_check)

        # Trigger failure threshold
        for _ in range(3):
            await health_registry.check_service("failing")

        assert callback1_called
        assert callback2_called

    @pytest.mark.asyncio
    async def test_callback_exception_handled(self, health_registry):
        """Callback exceptions don't crash health checks."""

        def failing_callback(service_name: str, failure_count: int, health: ServiceHealth):
            raise Exception("Callback error")

        health_registry.on_health_failure(failing_callback)

        async def failing_check():
            return ServiceHealth("failing", HealthStatus.UNHEALTHY, 0)

        health_registry.register("failing", failing_check)

        # Should not raise even though callback fails
        for _ in range(3):
            await health_registry.check_service("failing")


# ============================================================================
# AgentSupervisor Restart Tests
# ============================================================================


class TestAgentRestart:
    """Test agent restart functionality."""

    @pytest.mark.asyncio
    async def test_restart_agent_success(self, supervisor, agent_spec):
        """Can restart agent successfully."""
        supervisor.register(agent_spec)

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            result = await supervisor.restart_agent("test-agent-001", reason="test")

        assert result is True
        assert agent_spec.restart_count == 1
        assert agent_spec.status == "running"

    @pytest.mark.asyncio
    async def test_restart_agent_unknown_id(self, supervisor):
        """Restarting unknown agent returns False."""
        result = await supervisor.restart_agent("unknown-001", reason="test")
        assert result is False

    @pytest.mark.asyncio
    async def test_restart_agent_respects_never_policy(self, supervisor, agent_spec):
        """NEVER restart policy prevents restart."""
        agent_spec.restart_policy = RestartPolicy.NEVER
        supervisor.register(agent_spec)

        result = await supervisor.restart_agent("test-agent-001", reason="test")
        assert result is False
        assert agent_spec.restart_count == 0

    @pytest.mark.asyncio
    async def test_restart_agent_max_restarts_enforced(self, supervisor, agent_spec):
        """Cannot restart beyond max_restarts limit."""
        agent_spec.max_restarts = 2
        agent_spec.restart_count = 2
        supervisor.register(agent_spec)

        result = await supervisor.restart_agent("test-agent-001", reason="test")
        assert result is False
        assert agent_spec.restart_count == 2

    @pytest.mark.asyncio
    async def test_restart_agent_increments_count(self, supervisor, agent_spec):
        """Restart count increments with each restart."""
        supervisor.register(agent_spec)

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            await supervisor.restart_agent("test-agent-001")
            await supervisor.restart_agent("test-agent-001")

        assert agent_spec.restart_count == 2

    @pytest.mark.asyncio
    async def test_restart_agent_spawn_failure(self, supervisor, agent_spec):
        """Restart returns False when spawn fails."""
        supervisor.register(agent_spec)

        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            result = await supervisor.restart_agent("test-agent-001")

        assert result is False

    @pytest.mark.asyncio
    async def test_restart_agent_callback_invoked(self, supervisor, agent_spec):
        """Restart triggers agent_restarted callback."""
        callback_calls = []

        def callback(event_type: str, spec: AgentSpec):
            callback_calls.append(event_type)

        supervisor.on_event(callback)
        supervisor.register(agent_spec)

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            await supervisor.restart_agent("test-agent-001")

        assert "agent_restarted" in callback_calls


# ============================================================================
# Integration Tests
# ============================================================================


class TestHealthSupervisorIntegration:
    """Test integration between health checks and supervisor."""

    @pytest.mark.asyncio
    async def test_health_failure_triggers_restart(self, supervisor, agent_spec):
        """Health check failure triggers agent restart."""
        supervisor.register(agent_spec)

        # Mock successful spawn
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            # Simulate health failure
            supervisor._on_health_failure(
                "test-agent-001",
                3,
                ServiceHealth("test-agent-001", HealthStatus.UNHEALTHY, 0),
            )

            # Wait for async restart
            await asyncio.sleep(0.2)

        assert agent_spec.restart_count == 1
        assert agent_spec.status == "running"

    @pytest.mark.asyncio
    async def test_health_failure_no_agent_registered(self, supervisor):
        """Health failure for unknown agent is handled gracefully."""
        # Should not raise
        supervisor._on_health_failure(
            "unknown-agent",
            3,
            ServiceHealth("unknown-agent", HealthStatus.UNHEALTHY, 0),
        )

    @pytest.mark.asyncio
    async def test_register_with_health_checks(self, supervisor):
        """Supervisor can register with health check system."""
        # Should not raise
        supervisor.register_with_health_checks()

        # Verify callback was registered
        registry = get_health_registry()
        assert len(registry._failure_callbacks) > 0

    @pytest.mark.asyncio
    async def test_end_to_end_integration(self, supervisor, agent_spec):
        """End-to-end test: health check failure -> restart."""
        supervisor.register(agent_spec)
        supervisor.register_with_health_checks()

        registry = get_health_registry()

        # Register failing health check for agent
        async def failing_check():
            return ServiceHealth(
                name="test-agent-001",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                message="Agent not responding",
            )

        registry.register("test-agent-001", failing_check)

        # Mock successful spawn
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            # Trigger 3 consecutive failures
            for _ in range(3):
                await registry.check_service("test-agent-001")

            # Wait for async restart
            await asyncio.sleep(0.2)

        # Agent should be restarted
        assert agent_spec.restart_count == 1
        assert agent_spec.status == "running"

    @pytest.mark.asyncio
    async def test_health_recovery_after_restart(self, supervisor, agent_spec):
        """After restart, health check failures reset."""
        supervisor.register(agent_spec)
        supervisor.register_with_health_checks()

        registry = get_health_registry()

        check_count = 0

        async def intermittent_check():
            nonlocal check_count
            check_count += 1
            if check_count <= 3:
                return ServiceHealth(
                    name="test-agent-001",
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=0,
                )
            return ServiceHealth(
                name="test-agent-001",
                status=HealthStatus.HEALTHY,
                latency_ms=10.0,
            )

        registry.register("test-agent-001", intermittent_check)

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            # 3 failures - triggers restart
            for _ in range(3):
                await registry.check_service("test-agent-001")

            await asyncio.sleep(0.2)

            # Next check is healthy - failure count should reset
            await registry.check_service("test-agent-001")
            assert registry.get_failure_count("test-agent-001") == 0

    @pytest.mark.asyncio
    async def test_multiple_agents_independent_health(self, supervisor, tmp_path):
        """Multiple agents have independent health tracking."""
        agent1 = AgentSpec(
            agent_id="agent-001",
            role="backend",
            task="task1",
            health_check_interval=0.1,
            restart_delay=0.1,
        )
        agent2 = AgentSpec(
            agent_id="agent-002",
            role="frontend",
            task="task2",
            health_check_interval=0.1,
            restart_delay=0.1,
        )

        supervisor.register(agent1)
        supervisor.register(agent2)
        supervisor.register_with_health_checks()

        registry = get_health_registry()

        # Agent 1 fails
        async def failing_check():
            return ServiceHealth("agent-001", HealthStatus.UNHEALTHY, 0)

        # Agent 2 healthy
        async def healthy_check():
            return ServiceHealth("agent-002", HealthStatus.HEALTHY, 10.0)

        registry.register("agent-001", failing_check)
        registry.register("agent-002", healthy_check)

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            # Run health checks
            for _ in range(3):
                await registry.check_service("agent-001")
                await registry.check_service("agent-002")

            await asyncio.sleep(0.2)

        # Only agent 1 should be restarted
        assert agent1.restart_count == 1
        assert agent2.restart_count == 0
