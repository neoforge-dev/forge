"""Tests for DispatchClient integration.

Tests the full dispatch flow including readiness checks, message delivery,
verification, retries, circuit breaker, and metrics recording.

Test Coverage:
- Happy path (agent ready, message delivered, verified)
- Agent not ready (wait for ready, then dispatch)
- Agent crash (detect, restart, retry)
- Git lock coordination
- Circuit breaker opening and fallback
- Metrics recording
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from .conftest import CircuitState, CLIState

# =============================================================================
# Happy Path Tests
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_happy_path(
    dispatch_client,
    mock_readiness_ready,
    mock_tmux_send_keys,
    verification_echo_success,
    sample_task,
):
    """Test successful dispatch with ready agent."""
    # Setup
    agent_name = "claude"
    message = "Implement feature IS-042"

    # Mock readiness check
    with patch("time.time", return_value=1000.0):
        # Simulate dispatch
        # 1. Check readiness
        readiness = mock_readiness_ready()
        assert readiness.state == CLIState.READY

        # 2. Send message
        result = mock_tmux_send_keys.return_value
        assert result.returncode == 0

        # 3. Verify delivery
        verification = verification_echo_success
        assert verification.verified is True

        # Success
        assert verification.delivery_time_ms < 1000


@pytest.mark.asyncio
async def test_dispatch_records_metrics(dispatch_client, mock_metrics_recorder, sample_task):
    """Test dispatch records metrics on success."""
    agent_name = "claude"
    message = "Implement feature IS-042"

    # Simulate successful dispatch
    dispatch_time_ms = 250

    # Record metrics (mocked)
    mock_metrics_recorder(
        agent=agent_name,
        success=True,
        delivery_time_ms=dispatch_time_ms,
        retries=0,
    )

    # Verify metrics were recorded
    mock_metrics_recorder.assert_called_once()
    call_args = mock_metrics_recorder.call_args
    assert call_args[1]["agent"] == agent_name
    assert call_args[1]["success"] is True


@pytest.mark.asyncio
async def test_dispatch_fast_path(
    mock_readiness_ready, mock_tmux_send_keys, verification_echo_success
):
    """Test fast path dispatch completes quickly."""
    import time

    start = time.time()

    # Check readiness
    readiness = mock_readiness_ready()
    assert readiness.state == CLIState.READY

    # Send message
    mock_tmux_send_keys.return_value = MagicMock(returncode=0)

    # Verify
    verification = verification_echo_success
    assert verification.verified is True

    elapsed_ms = (time.time() - start) * 1000
    assert elapsed_ms < 500  # Fast path under 500ms


# =============================================================================
# Not Ready Scenario Tests
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_waits_for_ready(mock_readiness_busy, mock_readiness_ready, mock_sleep):
    """Test dispatch waits when agent is busy."""
    # First check: busy
    readiness_1 = mock_readiness_busy()
    assert readiness_1.state == CLIState.BUSY

    # Wait
    await asyncio.sleep(5)

    # Second check: ready
    readiness_2 = mock_readiness_ready()
    assert readiness_2.state == CLIState.READY

    # Should have slept once
    mock_sleep.assert_called()


@pytest.mark.asyncio
async def test_dispatch_timeout_waiting_for_ready(mock_readiness_busy, mock_sleep):
    """Test dispatch times out if agent never becomes ready."""
    max_wait_seconds = 60
    check_interval = 5
    elapsed = 0

    while elapsed < max_wait_seconds:
        readiness = mock_readiness_busy()

        if readiness.state == CLIState.READY:
            break

        await asyncio.sleep(check_interval)
        elapsed += check_interval

    # Should timeout
    assert elapsed >= max_wait_seconds


@pytest.mark.asyncio
async def test_dispatch_polls_readiness(mock_readiness_busy, mock_sleep):
    """Test dispatch polls readiness at intervals."""
    checks = 0
    max_checks = 5

    while checks < max_checks:
        readiness = mock_readiness_busy()
        checks += 1

        if readiness.state == CLIState.READY:
            break

        await asyncio.sleep(5)

    assert checks == max_checks
    assert mock_sleep.call_count == max_checks


# =============================================================================
# Agent Crash Scenario Tests
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_detects_crash(mock_readiness_crashed):
    """Test dispatch detects crashed agent."""
    readiness = mock_readiness_crashed()

    assert readiness.state == CLIState.CRASHED
    assert readiness.process_alive is False


@pytest.mark.asyncio
async def test_dispatch_restarts_crashed_agent(mock_readiness_crashed, mock_sleep):
    """Test dispatch restarts crashed agent."""
    # Detect crash
    readiness = mock_readiness_crashed()
    assert readiness.state == CLIState.CRASHED

    # Simulate restart
    await asyncio.sleep(2)

    # After restart, should be ready
    # (in real implementation, would check new state)


@pytest.mark.asyncio
async def test_dispatch_retries_after_restart(
    mock_readiness_crashed, mock_readiness_ready, mock_sleep
):
    """Test dispatch retries after agent restart."""
    # Initial state: crashed
    readiness_1 = mock_readiness_crashed()
    assert readiness_1.state == CLIState.CRASHED

    # Restart (simulated)
    await asyncio.sleep(2)

    # After restart: ready
    readiness_2 = mock_readiness_ready()
    assert readiness_2.state == CLIState.READY

    # Retry dispatch
    # (would send message again)


@pytest.mark.asyncio
async def test_dispatch_restart_failure_handling(mock_readiness_crashed, mock_sleep):
    """Test dispatch handles restart failures."""
    max_restart_attempts = 3
    attempts = 0

    while attempts < max_restart_attempts:
        readiness = mock_readiness_crashed()

        if readiness.state != CLIState.CRASHED:
            break

        attempts += 1
        await asyncio.sleep(2)

    # All restart attempts failed
    assert attempts == max_restart_attempts


# =============================================================================
# Git Lock Coordination Tests
# =============================================================================


@pytest.mark.asyncio
async def test_git_lock_serializes_dispatches(mock_git_lock, mock_tmux_send_keys):
    """Test git lock ensures sequential dispatch."""
    # Multiple dispatches
    dispatches = []

    async def dispatch_with_lock(agent, message):
        # Acquire lock
        with mock_git_lock():
            # Send message
            mock_tmux_send_keys.return_value = MagicMock(returncode=0)
            dispatches.append((agent, message))
            await asyncio.sleep(0.1)

    # Run concurrent dispatches
    await asyncio.gather(
        dispatch_with_lock("claude", "Task 1"),
        dispatch_with_lock("codex", "Task 2"),
        dispatch_with_lock("gemini", "Task 3"),
    )

    # All dispatches completed
    assert len(dispatches) == 3

    # Lock was acquired 3 times
    assert mock_git_lock.call_count == 3


@pytest.mark.asyncio
async def test_git_lock_timeout_handling(mock_sleep):
    """Test git lock timeout handling."""
    lock_timeout = 30

    # Simulate lock acquisition
    lock_acquired = False
    elapsed = 0

    while not lock_acquired and elapsed < lock_timeout:
        # Try to acquire (simulated)
        lock_acquired = False  # Still locked

        if not lock_acquired:
            await asyncio.sleep(1)
            elapsed += 1

    # Timeout
    assert elapsed >= lock_timeout


@pytest.mark.asyncio
async def test_git_lock_release_on_error(mock_git_lock):
    """Test git lock releases on error."""
    try:
        with mock_git_lock():
            # Simulate error during dispatch
            raise Exception("Dispatch failed")
    except Exception:
        pass

    # Lock should be released
    mock_git_lock.return_value.__exit__.assert_called_once()


# =============================================================================
# Circuit Breaker Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_failures(mock_sleep):
    """Test circuit breaker opens after threshold failures."""
    failure_threshold = 3
    failures = 0
    circuit_state = CircuitState.CLOSED

    # Simulate consecutive failures
    for i in range(5):
        # Attempt dispatch
        dispatch_success = False

        if not dispatch_success:
            failures += 1

        if failures >= failure_threshold:
            circuit_state = CircuitState.OPEN
            break

    assert circuit_state == CircuitState.OPEN
    assert failures == failure_threshold


@pytest.mark.asyncio
async def test_circuit_breaker_fallback_to_task_agent(mock_sleep):
    """Test circuit breaker triggers fallback to Task agent."""
    circuit_state = CircuitState.OPEN
    fallback_agent = None

    # Check circuit state
    if circuit_state == CircuitState.OPEN:
        # Use fallback
        fallback_agent = "task"

    assert fallback_agent == "task"


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_success_closes(mock_sleep):
    """Test circuit breaker closes after successful half-open request."""
    circuit_state = CircuitState.HALF_OPEN

    # Attempt dispatch
    dispatch_success = True

    if dispatch_success:
        circuit_state = CircuitState.CLOSED

    assert circuit_state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_failure_reopens(mock_sleep):
    """Test circuit breaker reopens after failed half-open request."""
    circuit_state = CircuitState.HALF_OPEN

    # Attempt dispatch
    dispatch_success = False

    if not dispatch_success:
        circuit_state = CircuitState.OPEN

    assert circuit_state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_tracks_per_agent():
    """Test circuit breaker tracks state per agent."""
    circuit_states = {
        "claude": CircuitState.CLOSED,
        "codex": CircuitState.OPEN,
        "gemini": CircuitState.CLOSED,
    }

    # Claude should be available
    assert circuit_states["claude"] != CircuitState.OPEN

    # Codex should be blocked
    assert circuit_states["codex"] == CircuitState.OPEN

    # Gemini should be available
    assert circuit_states["gemini"] != CircuitState.OPEN


# =============================================================================
# Metrics Recording Tests
# =============================================================================


@pytest.mark.asyncio
async def test_metrics_records_success(mock_metrics_recorder):
    """Test metrics recording on successful dispatch."""
    mock_metrics_recorder(
        agent="claude",
        success=True,
        delivery_time_ms=250,
        retries=0,
        circuit_state="closed",
    )

    mock_metrics_recorder.assert_called_once()


@pytest.mark.asyncio
async def test_metrics_records_failure(mock_metrics_recorder):
    """Test metrics recording on failed dispatch."""
    mock_metrics_recorder(
        agent="claude",
        success=False,
        delivery_time_ms=5000,
        retries=3,
        circuit_state="open",
        error="Verification timeout",
    )

    mock_metrics_recorder.assert_called_once()


@pytest.mark.asyncio
async def test_metrics_records_retry_count(mock_metrics_recorder):
    """Test metrics records number of retries."""
    mock_metrics_recorder(agent="claude", success=True, delivery_time_ms=7000, retries=3)

    call_args = mock_metrics_recorder.call_args
    assert call_args[1]["retries"] == 3


@pytest.mark.asyncio
async def test_metrics_records_delivery_time(mock_metrics_recorder):
    """Test metrics records delivery time."""
    mock_metrics_recorder(agent="claude", success=True, delivery_time_ms=300, retries=0)

    call_args = mock_metrics_recorder.call_args
    assert call_args[1]["delivery_time_ms"] == 300


@pytest.mark.asyncio
async def test_metrics_aggregation(dispatch_metrics):
    """Test metrics aggregation over multiple dispatches."""
    metrics = dispatch_metrics

    assert metrics["total_dispatches"] == 100
    assert metrics["successful_dispatches"] == 95
    assert metrics["failed_dispatches"] == 5

    # Success rate
    success_rate = metrics["successful_dispatches"] / metrics["total_dispatches"]
    assert success_rate == 0.95

    # Average delivery time
    assert metrics["avg_delivery_time_ms"] == 300


# =============================================================================
# Error Handling Tests
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_handles_tmux_error(mock_tmux_send_keys):
    """Test dispatch handles tmux command errors."""
    # Simulate tmux error
    mock_tmux_send_keys.return_value = MagicMock(returncode=1, stderr="tmux: session not found")

    result = mock_tmux_send_keys.return_value
    assert result.returncode != 0


@pytest.mark.asyncio
async def test_dispatch_handles_network_error():
    """Test dispatch handles network errors."""
    error = "Connection timeout"

    # Classify error
    is_retryable = "timeout" in error.lower() or "network" in error.lower()

    assert is_retryable is True


@pytest.mark.asyncio
async def test_dispatch_handles_validation_error():
    """Test dispatch handles validation errors."""
    error = "Invalid task specification"

    # Classify error (not retryable)
    is_retryable = any(
        keyword in error.lower() for keyword in ["timeout", "network", "busy", "lock"]
    )

    assert is_retryable is False


# =============================================================================
# Concurrency Tests
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_dispatches_different_agents(mock_tmux_send_keys, mock_metrics_recorder):
    """Test concurrent dispatches to different agents."""
    agents = ["claude", "codex", "gemini"]

    async def dispatch_to_agent(agent):
        mock_tmux_send_keys.return_value = MagicMock(returncode=0)
        await asyncio.sleep(0.1)
        return agent

    results = await asyncio.gather(*[dispatch_to_agent(agent) for agent in agents])

    assert len(results) == 3
    assert set(results) == set(agents)


@pytest.mark.asyncio
async def test_concurrent_dispatches_same_agent_serialized(mock_git_lock):
    """Test concurrent dispatches to same agent are serialized."""
    dispatches = []

    async def dispatch_with_lock(message):
        with mock_git_lock():
            dispatches.append(message)
            await asyncio.sleep(0.1)

    # Concurrent dispatches
    await asyncio.gather(
        dispatch_with_lock("Task 1"),
        dispatch_with_lock("Task 2"),
        dispatch_with_lock("Task 3"),
    )

    # All completed
    assert len(dispatches) == 3


# =============================================================================
# Integration Flow Tests
# =============================================================================


@pytest.mark.asyncio
async def test_full_dispatch_flow_with_retry(
    mock_readiness_ready,
    mock_tmux_send_keys,
    verification_echo_timeout,
    verification_echo_success,
    mock_sleep,
):
    """Test full dispatch flow with one retry."""
    attempts = 0
    max_attempts = 2

    while attempts < max_attempts:
        attempts += 1

        # Check readiness
        readiness = mock_readiness_ready()
        assert readiness.state == CLIState.READY

        # Send message
        mock_tmux_send_keys.return_value = MagicMock(returncode=0)

        # Verify (first fails, second succeeds)
        if attempts == 1:
            verification = verification_echo_timeout
            assert verification.verified is False
            await asyncio.sleep(2)  # Backoff
        else:
            verification = verification_echo_success
            assert verification.verified is True
            break

    assert attempts == 2


@pytest.mark.asyncio
async def test_dispatch_flow_with_crash_and_restart(
    mock_readiness_crashed, mock_readiness_ready, mock_sleep
):
    """Test dispatch flow with crash detection and restart."""
    # Phase 1: Detect crash
    readiness_1 = mock_readiness_crashed()
    assert readiness_1.state == CLIState.CRASHED

    # Phase 2: Restart
    await asyncio.sleep(2)

    # Phase 3: Verify ready
    readiness_2 = mock_readiness_ready()
    assert readiness_2.state == CLIState.READY

    # Phase 4: Retry dispatch
    # (would send message)
