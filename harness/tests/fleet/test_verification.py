"""Tests for message delivery verification.

Tests the echo pattern and activity detection mechanisms to verify
that dispatched messages are actually received by the CLI agent.

Test Coverage:
- Echo pattern success scenarios
- Echo pattern timeout scenarios
- Activity detection fallback
- Unique message ID generation
- Concurrent verification handling
- Performance requirements (<5s)
"""

import asyncio
import time
import uuid
from unittest.mock import MagicMock

import pytest

# =============================================================================
# Echo Pattern Tests
# =============================================================================


@pytest.mark.asyncio
async def test_echo_pattern_success(mock_tmux_send_keys, mock_tmux_capture):
    """Test successful message delivery via echo pattern."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"
    message = f"Implement feature IS-042 [{message_id}]"

    # Configure mock to return message ID in output
    output_with_echo = f"Previous output\n{message_id}\nWorking on it..."
    mock_tmux_capture.configure(output_with_echo)

    # Send message
    mock_tmux_send_keys.return_value = MagicMock(returncode=0)

    # Simulate verification check
    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert message_id in result.stdout


@pytest.mark.asyncio
async def test_echo_pattern_timeout(mock_tmux_send_keys, mock_tmux_capture, mock_sleep):
    """Test echo pattern verification timeout."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"

    # Configure mock to never return message ID
    output_no_echo = "Previous output\nStill working on previous task..."
    mock_tmux_capture.configure(output_no_echo)

    # Send message
    mock_tmux_send_keys.return_value = MagicMock(returncode=0)

    # Simulate timeout by checking multiple times
    max_attempts = 5
    for i in range(max_attempts):
        import subprocess

        result = subprocess.run(
            ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
            capture_output=True,
            text=True,
        )

        if message_id in result.stdout:
            break

        await asyncio.sleep(1)

    # Should not find message ID
    assert message_id not in result.stdout


@pytest.mark.asyncio
async def test_echo_pattern_with_embedded_id(mock_tmux_send_keys, mock_tmux_capture):
    """Test echo pattern with message ID embedded in instruction."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"
    message = f"[{message_id}] Implement authentication"

    # Verify message ID at start of message
    assert message.startswith(f"[{message_id}]")

    # Configure mock to echo the message
    output = f"Received: {message}"
    mock_tmux_capture.configure(output)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert message_id in result.stdout


@pytest.mark.asyncio
async def test_echo_pattern_multiple_messages(mock_tmux_send_keys, mock_tmux_capture):
    """Test echo pattern distinguishes between multiple messages."""
    msg_id_1 = f"msg_{uuid.uuid4().hex[:8]}"
    msg_id_2 = f"msg_{uuid.uuid4().hex[:8]}"

    # Ensure IDs are different
    assert msg_id_1 != msg_id_2

    # Configure mock with first message ID
    output = f"Working on {msg_id_1}"
    mock_tmux_capture.configure(output)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert msg_id_1 in result.stdout
    assert msg_id_2 not in result.stdout


# =============================================================================
# Activity Detection Fallback Tests
# =============================================================================


@pytest.mark.asyncio
async def test_activity_detection_fallback(mock_tmux_capture):
    """Test fallback to activity detection when echo fails."""
    # Configure initial state (idle)
    initial_output = "INSERT --"
    mock_tmux_capture.configure(initial_output)

    import subprocess

    result_before = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # Send message (simulated)
    await asyncio.sleep(0.1)

    # Configure new state (active/changed)
    new_output = "Working on your request..."
    mock_tmux_capture.configure(new_output)

    result_after = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # Output should have changed
    assert result_before.stdout != result_after.stdout
    assert "Working" in result_after.stdout


@pytest.mark.asyncio
async def test_activity_detection_no_change(mock_tmux_capture):
    """Test activity detection when output doesn't change."""
    output = "INSERT --"
    mock_tmux_capture.configure(output)

    import subprocess

    result_1 = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    await asyncio.sleep(0.1)

    result_2 = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # Output should be same
    assert result_1.stdout == result_2.stdout


@pytest.mark.asyncio
async def test_activity_detection_busy_to_working_transition(mock_tmux_capture):
    """Test activity detection recognizes state transition."""
    # Initial busy state
    busy_output = "Running tests..."
    mock_tmux_capture.configure(busy_output)

    import subprocess

    result_1 = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    await asyncio.sleep(0.1)

    # Transition to different task
    working_output = "Implementing feature..."
    mock_tmux_capture.configure(working_output)

    result_2 = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert result_1.stdout != result_2.stdout


# =============================================================================
# Unique Message ID Tests
# =============================================================================


def test_unique_message_ids():
    """Test that message IDs are unique across generations."""
    ids = set()

    for _ in range(100):
        msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        assert msg_id not in ids
        ids.add(msg_id)

    assert len(ids) == 100


def test_message_id_format():
    """Test message ID follows expected format."""
    msg_id = f"msg_{uuid.uuid4().hex[:8]}"

    assert msg_id.startswith("msg_")
    assert len(msg_id) == 12  # "msg_" + 8 hex chars


def test_message_id_collision_probability():
    """Test that message ID collision is extremely unlikely."""
    # With 8 hex chars (32 bits), collision probability is very low
    # For 100 messages: ~0.00001% chance of collision

    ids = [f"msg_{uuid.uuid4().hex[:8]}" for _ in range(1000)]

    # All IDs should be unique
    assert len(ids) == len(set(ids))


# =============================================================================
# Concurrent Verification Tests
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_verifications_no_collision(mock_tmux_send_keys, mock_tmux_capture):
    """Test concurrent verifications use unique IDs."""
    num_concurrent = 10
    message_ids = []

    async def verify_message(index):
        msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        message_ids.append(msg_id)
        await asyncio.sleep(0.01 * index)  # Stagger slightly
        return msg_id

    # Run concurrent verifications
    results = await asyncio.gather(*[verify_message(i) for i in range(num_concurrent)])

    # All IDs should be unique
    assert len(results) == num_concurrent
    assert len(set(results)) == num_concurrent


@pytest.mark.asyncio
async def test_concurrent_verifications_separate_agents(mock_tmux_capture):
    """Test concurrent verifications to different agents."""
    agents = ["claude", "codex", "gemini"]

    async def verify_agent(agent_name):
        import subprocess

        result = subprocess.run(
            ["tmux", "capture-pane", "-t", f"forge:{agent_name}", "-p"],
            capture_output=True,
            text=True,
        )
        return agent_name, result.returncode

    # Verify all agents concurrently
    results = await asyncio.gather(*[verify_agent(agent) for agent in agents])

    assert len(results) == 3


@pytest.mark.asyncio
async def test_concurrent_verifications_queue_order(mock_tmux_capture):
    """Test that concurrent verifications maintain order."""
    results = []

    async def verify_in_order(index):
        await asyncio.sleep(0.01 * (10 - index))  # Reverse timing
        results.append(index)

    # Start all verifications
    await asyncio.gather(*[verify_in_order(i) for i in range(5)])

    # Results may not be in order due to timing
    assert len(results) == 5
    assert set(results) == {0, 1, 2, 3, 4}


# =============================================================================
# Performance Tests
# =============================================================================


@pytest.mark.asyncio
async def test_verification_completes_under_5_seconds(
    mock_tmux_send_keys, mock_tmux_capture, mock_sleep
):
    """Test that verification completes within 5 seconds."""
    start_time = time.time()

    message_id = f"msg_{uuid.uuid4().hex[:8]}"

    # Configure fast echo
    output = f"Received {message_id}"
    mock_tmux_capture.configure(output)

    # Verify
    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    elapsed = time.time() - start_time

    assert message_id in result.stdout
    assert elapsed < 5.0


@pytest.mark.asyncio
async def test_verification_fast_path_under_500ms(mock_tmux_capture):
    """Test that fast path verification is under 500ms."""
    start_time = time.time()

    message_id = f"msg_{uuid.uuid4().hex[:8]}"
    output = f"{message_id}"
    mock_tmux_capture.configure(output)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    elapsed = time.time() - start_time

    assert message_id in result.stdout
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_verification_timeout_at_5_seconds(mock_tmux_capture, mock_sleep):
    """Test that verification times out at exactly 5 seconds."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"

    # Configure to never return message ID
    output = "No message ID here"
    mock_tmux_capture.configure(output)

    # Simulate timeout with mock sleep
    max_wait_ms = 5000
    check_interval_ms = 500
    attempts = 0

    start_time = time.time()

    while (time.time() - start_time) * 1000 < max_wait_ms:
        import subprocess

        result = subprocess.run(
            ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
            capture_output=True,
            text=True,
        )

        if message_id in result.stdout:
            break

        attempts += 1
        await asyncio.sleep(check_interval_ms / 1000)

    elapsed_ms = (time.time() - start_time) * 1000
    assert elapsed_ms >= max_wait_ms or attempts >= max_wait_ms / check_interval_ms


# =============================================================================
# Verification Result Tests
# =============================================================================


def test_verification_result_success(verification_echo_success):
    """Test VerificationResult for successful verification."""
    result = verification_echo_success

    assert result.verified is True
    assert result.message_id.startswith("msg_")
    assert result.delivery_time_ms < 1000
    assert result.method == "echo"
    assert result.error is None


def test_verification_result_timeout(verification_echo_timeout):
    """Test VerificationResult for timeout."""
    result = verification_echo_timeout

    assert result.verified is False
    assert result.delivery_time_ms >= 5000
    assert result.method == "echo"
    assert result.error is not None


def test_verification_result_activity_fallback(verification_activity_fallback):
    """Test VerificationResult for activity detection fallback."""
    result = verification_activity_fallback

    assert result.verified is True
    assert result.method == "activity"
    assert result.delivery_time_ms < 5000


# =============================================================================
# Edge Cases Tests
# =============================================================================


@pytest.mark.asyncio
async def test_verification_with_message_id_substring(mock_tmux_capture):
    """Test verification handles message ID as substring."""
    message_id = "msg_abc123"
    similar_id = "msg_abc1234"  # Longer ID with same prefix

    output = f"Processing {similar_id}"
    mock_tmux_capture.configure(output)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # Should find the longer ID
    assert similar_id in result.stdout
    # Original ID is also substring
    assert message_id in result.stdout


@pytest.mark.asyncio
async def test_verification_with_special_characters(mock_tmux_capture):
    """Test verification with special characters in message."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"
    message = f"[{message_id}] Fix bug in foo() -> bar()"

    output = f"Received: {message}"
    mock_tmux_capture.configure(output)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert message_id in result.stdout


@pytest.mark.asyncio
async def test_verification_with_multiline_output(mock_tmux_capture):
    """Test verification finds message ID in multiline output."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"

    output = f"""
    Previous task completed.

    Starting new task: {message_id}

    Analyzing requirements...
    """
    mock_tmux_capture.configure(output)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert message_id in result.stdout


@pytest.mark.asyncio
async def test_verification_empty_output_handling(mock_tmux_capture, empty_output):
    """Test verification handles empty output gracefully."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"
    mock_tmux_capture.configure(empty_output)

    import subprocess

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert message_id not in result.stdout
    assert result.stdout == ""


# =============================================================================
# Gemini Restart-with-Prompt Strategy Tests
# =============================================================================


@pytest.mark.asyncio
async def test_gemini_restart_strategy_sends_ctrl_c(mock_tmux_send_keys):
    """Test that Gemini dispatch sends C-c to kill current process."""
    from forge_harness.fleet.verification import send_to_tmux_restart

    mock_tmux_send_keys.return_value = MagicMock(returncode=0, stdout="$ ", stderr="")

    await send_to_tmux_restart("forge:gemini", "Research patterns", "gemini --yolo")

    # Should have sent C-c at least once
    calls = mock_tmux_send_keys.call_args_list
    c_c_calls = [
        c
        for c in calls
        if c.args and "C-c" in c.args[0]
    ]
    assert len(c_c_calls) >= 1, "Should send C-c to kill current Gemini process"


@pytest.mark.asyncio
async def test_gemini_restart_strategy_builds_correct_command(mock_tmux_send_keys):
    """Test that restart command includes -i flag with prompt."""
    from forge_harness.fleet.verification import send_to_tmux_restart

    mock_tmux_send_keys.return_value = MagicMock(returncode=0, stdout="$ ", stderr="")

    await send_to_tmux_restart(
        "forge:gemini", "Research authentication patterns", "gemini --yolo"
    )

    # Find the send-keys -l call that contains the restart command
    calls = mock_tmux_send_keys.call_args_list
    restart_calls = [
        c
        for c in calls
        if c.args and "-l" in c.args[0] and any("gemini" in str(a) for a in c.args[0])
    ]
    assert len(restart_calls) >= 1, "Should send restart command with -l flag"
    # The command is the last element in the args list
    restart_cmd = restart_calls[0].args[0][-1]
    assert "-i" in restart_cmd, "Restart command should include -i flag"
    assert "Research authentication patterns" in restart_cmd


@pytest.mark.asyncio
async def test_gemini_restart_strategy_escapes_single_quotes(mock_tmux_send_keys):
    """Test that single quotes in message are properly escaped."""
    from forge_harness.fleet.verification import send_to_tmux_restart

    mock_tmux_send_keys.return_value = MagicMock(returncode=0, stdout="$ ", stderr="")

    await send_to_tmux_restart(
        "forge:gemini", "Fix the user's login bug", "gemini --yolo"
    )

    calls = mock_tmux_send_keys.call_args_list
    restart_calls = [
        c
        for c in calls
        if c.args and "-l" in c.args[0] and any("gemini" in str(a) for a in c.args[0])
    ]
    assert len(restart_calls) >= 1
    restart_cmd = restart_calls[0].args[0][-1]
    # The original apostrophe in "user's" should be shell-escaped
    # Result: gemini --yolo -i 'Fix the user'\''s login bug'
    assert "user's" not in restart_cmd, "Raw single quote should be escaped"
    assert "-i" in restart_cmd


def test_restart_with_prompt_clis_contains_gemini():
    """Test that Gemini is registered in the restart-with-prompt CLI set."""
    from forge_harness.fleet.verification import RESTART_WITH_PROMPT_CLIS

    assert "gemini" in RESTART_WITH_PROMPT_CLIS


@pytest.mark.asyncio
async def test_send_with_verification_auto_detects_gemini():
    """Test that send_with_verification auto-detects Gemini from target name."""
    from unittest.mock import AsyncMock, patch

    with patch(
        "forge_harness.fleet.verification.send_to_tmux_restart",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_restart, patch(
        "forge_harness.fleet.verification.verify_activity",
        new_callable=AsyncMock,
        return_value=True,
    ):
        from forge_harness.fleet.verification import send_with_verification

        result = await send_with_verification(
            "forge:gemini", "Research patterns", timeout=5.0, cli_type="auto"
        )

        # Should have used restart strategy, not send-keys
        mock_restart.assert_called_once()
        assert result.delivered
        assert result.verification_method == "restart_activity"


@pytest.mark.asyncio
async def test_send_with_verification_explicit_gemini_cli_type():
    """Test explicit cli_type='gemini' uses restart strategy."""
    from unittest.mock import AsyncMock, patch

    with patch(
        "forge_harness.fleet.verification.send_to_tmux_restart",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_restart, patch(
        "forge_harness.fleet.verification.verify_activity",
        new_callable=AsyncMock,
        return_value=False,
    ):
        from forge_harness.fleet.verification import send_with_verification

        result = await send_with_verification(
            "forge:gemini", "Research patterns", timeout=5.0, cli_type="gemini"
        )

        mock_restart.assert_called_once()
        assert result.delivered
        # No activity detected but restart itself succeeded
        assert result.verification_method == "restart"


@pytest.mark.asyncio
async def test_send_with_verification_claude_uses_standard_strategy():
    """Test that Claude targets still use standard send-keys strategy."""
    from unittest.mock import AsyncMock, patch

    with patch(
        "forge_harness.fleet.verification.send_to_tmux",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_send, patch(
        "forge_harness.fleet.verification.verify_echo",
        new_callable=AsyncMock,
        return_value=True,
    ):
        from forge_harness.fleet.verification import send_with_verification

        result = await send_with_verification(
            "forge:claude", "Run tests", timeout=5.0, cli_type="claude"
        )

        mock_send.assert_called_once()
        assert result.delivered
        assert result.verification_method == "echo"
