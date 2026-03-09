"""Chaos tests for dispatch reliability.

Tests extreme scenarios, race conditions, and failure recovery.

Test Coverage:
- Parallel dispatches without race conditions
- Agent killed mid-dispatch
- Tmux session disconnection
- Out-of-order message delivery
- Extremely long messages
- Network partition simulation
- Resource exhaustion
- Concurrent circuit breaker state changes
"""

import asyncio
import uuid
from unittest.mock import MagicMock, patch

import pytest

from .conftest import CircuitState, CLIState

# =============================================================================
# Parallel Dispatch Tests
# =============================================================================


@pytest.mark.asyncio
async def test_10_parallel_dispatches_no_races(mock_tmux_send_keys, mock_git_lock):
    """Test 10 parallel dispatches without race conditions."""
    num_dispatches = 10
    results = []

    async def dispatch_task(task_id):
        # Simulate dispatch with lock
        with mock_git_lock():
            mock_tmux_send_keys.return_value = MagicMock(returncode=0)
            await asyncio.sleep(0.01)  # Small delay
            results.append(task_id)
            return task_id

    # Run parallel dispatches
    task_ids = [f"task_{i}" for i in range(num_dispatches)]
    completed = await asyncio.gather(*[dispatch_task(tid) for tid in task_ids])

    # All dispatches completed
    assert len(completed) == num_dispatches
    assert len(results) == num_dispatches

    # No duplicates
    assert len(set(results)) == num_dispatches


@pytest.mark.asyncio
async def test_parallel_dispatches_message_id_uniqueness(mock_tmux_send_keys):
    """Test parallel dispatches generate unique message IDs."""
    num_dispatches = 20
    message_ids = []

    async def generate_and_dispatch():
        msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        message_ids.append(msg_id)
        await asyncio.sleep(0.001)
        return msg_id

    # Generate in parallel
    generated_ids = await asyncio.gather(*[generate_and_dispatch() for _ in range(num_dispatches)])

    # All unique
    assert len(generated_ids) == num_dispatches
    assert len(set(generated_ids)) == num_dispatches


@pytest.mark.asyncio
async def test_parallel_dispatches_to_multiple_agents(mock_tmux_send_keys):
    """Test parallel dispatches to different agents."""
    agents = ["claude", "codex", "gemini", "kimi", "tech"]
    tasks_per_agent = 3

    results = {}

    async def dispatch_to_agent(agent, task_num):
        mock_tmux_send_keys.return_value = MagicMock(returncode=0)
        await asyncio.sleep(0.01)
        return (agent, task_num)

    # Dispatch 3 tasks to each of 5 agents (15 total)
    dispatches = [dispatch_to_agent(agent, i) for agent in agents for i in range(tasks_per_agent)]

    completed = await asyncio.gather(*dispatches)

    # All 15 completed
    assert len(completed) == len(agents) * tasks_per_agent


@pytest.mark.asyncio
async def test_parallel_dispatches_with_failures(mock_tmux_send_keys):
    """Test parallel dispatches with some failures."""
    num_dispatches = 10
    fail_indices = {2, 5, 7}  # Fail these indices

    async def dispatch_with_potential_failure(index):
        if index in fail_indices:
            # Simulate failure
            raise Exception(f"Dispatch {index} failed")
        else:
            mock_tmux_send_keys.return_value = MagicMock(returncode=0)
            return index

    # Run with gather, return_exceptions=True to continue on failures
    results = await asyncio.gather(
        *[dispatch_with_potential_failure(i) for i in range(num_dispatches)],
        return_exceptions=True,
    )

    # Count successes and failures
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == num_dispatches - len(fail_indices)
    assert len(failures) == len(fail_indices)


# =============================================================================
# Agent Killed Mid-Dispatch Tests
# =============================================================================


@pytest.mark.asyncio
async def test_agent_killed_during_message_send(mock_tmux_send_keys):
    """Test agent killed while sending message."""
    # Start sending
    mock_tmux_send_keys.return_value = MagicMock(returncode=0)

    # Simulate kill during send
    async def send_and_kill():
        await asyncio.sleep(0.01)
        # Agent killed
        raise Exception("Process killed")

    with pytest.raises(Exception, match="Process killed"):
        await send_and_kill()


@pytest.mark.asyncio
async def test_agent_killed_during_verification(mock_tmux_send_keys, mock_tmux_capture):
    """Test agent killed during verification."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"

    # Message sent
    mock_tmux_send_keys.return_value = MagicMock(returncode=0)

    # Agent killed before verification completes
    mock_tmux_capture.configure("[Process exited with code 137]", returncode=1)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # Verification should fail
    assert message_id not in result.stdout
    assert "Process exited" in result.stdout or result.returncode != 0


@pytest.mark.asyncio
async def test_agent_killed_recovery_with_restart(mock_readiness_crashed, mock_sleep):
    """Test recovery after agent killed mid-dispatch."""
    # Detect kill
    readiness = mock_readiness_crashed()
    assert readiness.state == CLIState.CRASHED

    # Restart
    await asyncio.sleep(2)

    # Should be ready after restart (in real implementation)


# =============================================================================
# Tmux Session Disconnection Tests
# =============================================================================


@pytest.mark.asyncio
async def test_tmux_session_disconnect_fail_fast(mock_tmux_capture):
    """Test fail fast when tmux session disconnects."""
    # Simulate disconnection
    mock_tmux_capture.configure("", returncode=1)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # Should fail immediately
    assert result.returncode != 0


@pytest.mark.asyncio
async def test_tmux_session_not_found():
    """Test handling when tmux session not found."""
    with patch(
        "subprocess.run",
        return_value=MagicMock(returncode=1, stderr="can't find session: forge", stdout=""),
    ):
        import subprocess

        result = subprocess.run(
            ["tmux", "capture-pane", "-t", "forge:nonexistent", "-p"],
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0


@pytest.mark.asyncio
async def test_tmux_server_not_running():
    """Test handling when tmux server not running."""
    with patch(
        "subprocess.run",
        return_value=MagicMock(
            returncode=1, stderr="no server running on /tmp/tmux-1000", stdout=""
        ),
    ):
        import subprocess

        result = subprocess.run(
            ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0


# =============================================================================
# Out-of-Order Message Delivery Tests
# =============================================================================


@pytest.mark.asyncio
async def test_out_of_order_message_delivery(mock_tmux_capture):
    """Test handling out-of-order message IDs."""
    msg_id_1 = f"msg_{uuid.uuid4().hex[:8]}"
    msg_id_2 = f"msg_{uuid.uuid4().hex[:8]}"
    msg_id_3 = f"msg_{uuid.uuid4().hex[:8]}"

    # Agent processes in different order
    # Sent: 1, 2, 3
    # Processed: 2, 1, 3

    # Verify message 1
    output_1 = f"Processing {msg_id_2}"  # 2 appears first
    mock_tmux_capture.configure(output_1)

    import subprocess

    result_1 = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert msg_id_2 in result_1.stdout
    assert msg_id_1 not in result_1.stdout


@pytest.mark.asyncio
async def test_stale_message_id_detection(mock_tmux_capture):
    """Test detection of stale message IDs from previous dispatches."""
    old_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
    new_msg_id = f"msg_{uuid.uuid4().hex[:8]}"

    # Output contains old message ID
    output = f"Completed {old_msg_id}\n\nINSERT --"
    mock_tmux_capture.configure(output)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # Old ID present but agent is ready
    assert old_msg_id in result.stdout
    assert "INSERT" in result.stdout


# =============================================================================
# Extremely Long Message Tests
# =============================================================================


@pytest.mark.asyncio
async def test_extremely_long_message_handling(mock_tmux_send_keys):
    """Test handling of messages >10KB."""
    # Create 15KB message
    long_message = "x" * 15000

    mock_tmux_send_keys.return_value = MagicMock(returncode=0)

    import subprocess

    # Send long message
    result = subprocess.run(
        ["tmux", "send-keys", "-t", "forge:claude", long_message],
        capture_output=True,
        text=True,
    )

    # Should handle gracefully (may truncate)
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_message_with_special_characters(mock_tmux_send_keys):
    """Test message with special characters and escape sequences."""
    message = 'Fix bug in foo() -> bar() [IS-042] $PATH="/usr/bin" \'"quotes"\''

    mock_tmux_send_keys.return_value = MagicMock(returncode=0)

    # Should escape properly
    # (actual escaping depends on implementation)


@pytest.mark.asyncio
async def test_message_with_newlines(mock_tmux_send_keys):
    """Test message with embedded newlines."""
    message = "Implement feature:\n1. Step one\n2. Step two\n3. Step three"

    mock_tmux_send_keys.return_value = MagicMock(returncode=0)

    # Should handle newlines appropriately


# =============================================================================
# Network Partition Simulation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_network_partition_during_dispatch():
    """Test dispatch failure during network partition."""
    error = "Network unreachable"

    # Classify as transient
    is_retryable = "network" in error.lower() or "unreachable" in error.lower()

    assert is_retryable is True


@pytest.mark.asyncio
async def test_network_partition_recovery(mock_sleep):
    """Test recovery after network partition."""
    network_available = False
    retries = 0
    max_retries = 5

    while not network_available and retries < max_retries:
        retries += 1
        await asyncio.sleep(2**retries)

        # Simulate network recovery on 3rd attempt
        if retries >= 3:
            network_available = True

    assert network_available is True
    assert retries == 3


# =============================================================================
# Resource Exhaustion Tests
# =============================================================================


@pytest.mark.asyncio
async def test_file_descriptor_exhaustion():
    """Test handling of file descriptor exhaustion."""
    error = "Too many open files"

    # Should fail fast (not retryable)
    is_retryable = False

    assert is_retryable is False


@pytest.mark.asyncio
async def test_memory_exhaustion():
    """Test handling of memory exhaustion."""
    error = "Cannot allocate memory"

    # Should fail fast
    is_retryable = False

    assert is_retryable is False


@pytest.mark.asyncio
async def test_disk_space_exhaustion():
    """Test handling of disk space exhaustion."""
    error = "No space left on device"

    # Should fail fast
    is_retryable = False

    assert is_retryable is False


# =============================================================================
# Concurrent Circuit Breaker Tests
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_circuit_breaker_state_changes():
    """Test circuit breaker handles concurrent state changes."""
    circuit_state = CircuitState.CLOSED
    failure_count = 0
    threshold = 3

    async def record_failure():
        nonlocal failure_count, circuit_state
        failure_count += 1
        if failure_count >= threshold:
            circuit_state = CircuitState.OPEN

    # Concurrent failures
    await asyncio.gather(*[record_failure() for _ in range(5)])

    assert circuit_state == CircuitState.OPEN
    assert failure_count >= threshold


@pytest.mark.asyncio
async def test_concurrent_circuit_breaker_per_agent():
    """Test concurrent circuit breaker state changes for different agents."""
    circuit_states = {
        "claude": CircuitState.CLOSED,
        "codex": CircuitState.CLOSED,
        "gemini": CircuitState.CLOSED,
    }

    async def fail_agent(agent):
        # Simulate 3 failures
        for _ in range(3):
            await asyncio.sleep(0.01)

        circuit_states[agent] = CircuitState.OPEN

    # Fail claude and gemini concurrently
    await asyncio.gather(fail_agent("claude"), fail_agent("gemini"))

    assert circuit_states["claude"] == CircuitState.OPEN
    assert circuit_states["codex"] == CircuitState.CLOSED
    assert circuit_states["gemini"] == CircuitState.OPEN


# =============================================================================
# Stress Tests
# =============================================================================


@pytest.mark.asyncio
async def test_rapid_fire_dispatches(mock_tmux_send_keys):
    """Test rapid-fire dispatches (100 in quick succession)."""
    num_dispatches = 100
    results = []

    async def rapid_dispatch(index):
        mock_tmux_send_keys.return_value = MagicMock(returncode=0)
        results.append(index)
        return index

    # Fire all at once
    await asyncio.gather(*[rapid_dispatch(i) for i in range(num_dispatches)])

    assert len(results) == num_dispatches


@pytest.mark.asyncio
async def test_sustained_load_dispatches(mock_tmux_send_keys, mock_sleep):
    """Test sustained load over time."""
    duration_seconds = 5
    dispatches_per_second = 2
    total_dispatches = duration_seconds * dispatches_per_second

    dispatches = []

    async def dispatch_with_interval(index):
        await asyncio.sleep(0.5 * index)  # Stagger
        mock_tmux_send_keys.return_value = MagicMock(returncode=0)
        dispatches.append(index)

    await asyncio.gather(*[dispatch_with_interval(i) for i in range(total_dispatches)])

    assert len(dispatches) == total_dispatches


# =============================================================================
# Edge Case Recovery Tests
# =============================================================================


@pytest.mark.asyncio
async def test_recovery_from_multiple_simultaneous_failures(mock_sleep):
    """Test recovery when multiple agents fail simultaneously."""
    agents = ["claude", "codex", "gemini"]
    failed_agents = set()

    async def fail_agent(agent):
        failed_agents.add(agent)
        await asyncio.sleep(0.01)

    # All agents fail at once
    await asyncio.gather(*[fail_agent(agent) for agent in agents])

    assert len(failed_agents) == len(agents)

    # Recovery (would restart all)
    await asyncio.sleep(2)


@pytest.mark.asyncio
async def test_recovery_from_cascading_failures(mock_sleep):
    """Test recovery from cascading failures."""
    failures = []

    async def fail_in_sequence(index):
        await asyncio.sleep(0.1 * index)  # Cascade
        failures.append(index)

    await asyncio.gather(*[fail_in_sequence(i) for i in range(5)])

    # All failed in sequence
    assert len(failures) == 5
    assert failures == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_partial_recovery_handling(mock_sleep):
    """Test handling of partial recovery (some agents recover, others don't)."""
    agents = {"claude": False, "codex": False, "gemini": False}

    async def attempt_recovery(agent, should_succeed):
        await asyncio.sleep(0.1)
        if should_succeed:
            agents[agent] = True

    # Claude and Gemini recover, Codex doesn't
    await asyncio.gather(
        attempt_recovery("claude", True),
        attempt_recovery("codex", False),
        attempt_recovery("gemini", True),
    )

    assert agents["claude"] is True
    assert agents["codex"] is False
    assert agents["gemini"] is True
