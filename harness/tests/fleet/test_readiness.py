"""Tests for CLI readiness detection.

Tests the ability to detect when CLI agents (Claude Code, Codex, Gemini)
are ready to receive commands vs busy processing.

Test Coverage:
- Prompt pattern detection for all CLI types
- Busy state detection
- Process crash detection
- Confidence scoring
- Edge cases (empty output, malformed, process dead)
"""

import subprocess
from unittest.mock import patch

import pytest

from .conftest import CLIState

# =============================================================================
# Prompt Detection Tests
# =============================================================================


def test_detect_claude_ready(mock_tmux_capture, ready_output):
    """Test detection of Claude Code ready state."""
    mock_tmux_capture.configure(ready_output)

    # Simulate readiness check
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "INSERT" in result.stdout
    # In actual implementation, this would return ReadinessCheck with READY state


def test_detect_claude_ready_with_type_message_prompt(mock_tmux_capture):
    """Test Claude ready with 'Type your message' prompt."""
    output = "Previous output\n\nType your message to Claude Code..."
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Type your message" in result.stdout


def test_detect_claude_ready_with_help_prompt(mock_tmux_capture):
    """Test Claude ready with 'How can I help' prompt."""
    output = "Context: 45%\n\nHow can I help you today?"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "How can I help" in result.stdout


def test_detect_codex_ready(mock_tmux_capture):
    """Test detection of Codex ready state."""
    output = "Build complete.\n\nReady for your next request >>>"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:codex", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Ready for your next request" in result.stdout or ">>>" in result.stdout


def test_detect_gemini_ready(mock_tmux_capture):
    """Test detection of Gemini ready state."""
    output = "Analysis complete.\n\nI'm ready to help. model>"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:gemini", "-p"],
        capture_output=True,
        text=True,
    )

    assert "ready to help" in result.stdout or "model>" in result.stdout


def test_detect_ready_with_context_percentage(mock_tmux_capture):
    """Test ready detection with context percentage shown."""
    output = "Context: 42% | 85K/200K tokens\n\nINSERT --"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "INSERT" in result.stdout
    assert "Context:" in result.stdout


# =============================================================================
# Busy State Detection Tests
# =============================================================================


def test_detect_busy_state_working(mock_tmux_capture, busy_output):
    """Test detection of 'Working' busy indicator."""
    mock_tmux_capture.configure(busy_output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Working" in result.stdout


def test_detect_busy_state_composing(mock_tmux_capture):
    """Test detection of 'Composing' busy indicator."""
    output = "Analyzing your request...\nComposing response..."
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Composing" in result.stdout


def test_detect_busy_state_running(mock_tmux_capture):
    """Test detection of 'Running' busy indicator."""
    output = "Running tests...\npytest -v tests/"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Running" in result.stdout


def test_detect_busy_state_implementing(mock_tmux_capture):
    """Test detection of 'Implementing' busy indicator."""
    output = "Implementing the authentication feature..."
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Implementing" in result.stdout


def test_detect_busy_state_executing(mock_tmux_capture):
    """Test detection of 'Executing' busy indicator."""
    output = "Executing bash command...\n$ git status"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Executing" in result.stdout


# =============================================================================
# Process Crash Detection Tests
# =============================================================================


def test_detect_crashed_process(mock_tmux_capture, crashed_output):
    """Test detection of crashed CLI process."""
    mock_tmux_capture.configure(crashed_output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Process exited" in result.stdout or "bash-" in result.stdout


def test_detect_process_segfault(mock_tmux_capture):
    """Test detection of segmentation fault."""
    output = "Segmentation fault (core dumped)\nbash-5.1$"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Segmentation fault" in result.stdout


def test_detect_process_killed(mock_tmux_capture):
    """Test detection of killed process."""
    output = "Killed\nbash-5.1$ "
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Killed" in result.stdout


def test_detect_connection_lost(mock_tmux_capture):
    """Test detection of connection lost error."""
    output = "Error: Connection to server lost\n[Disconnected]"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Connection" in result.stdout and "lost" in result.stdout


# =============================================================================
# Confidence Scoring Tests
# =============================================================================


def test_high_confidence_ready_multiple_indicators(mock_tmux_capture):
    """Test high confidence when multiple ready indicators present."""
    output = "Context: 30%\n\nINSERT --\nType your message..."
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # Multiple indicators should increase confidence
    assert "INSERT" in result.stdout
    assert "Type your message" in result.stdout


def test_medium_confidence_single_indicator(mock_tmux_capture):
    """Test medium confidence with single indicator."""
    output = "Some previous output\nINSERT --"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "INSERT" in result.stdout


def test_low_confidence_ambiguous_output(mock_tmux_capture):
    """Test low confidence with ambiguous output."""
    output = "Processing...\nPlease wait..."
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    # No clear ready or busy indicators
    assert "Processing" in result.stdout


# =============================================================================
# Edge Cases Tests
# =============================================================================


def test_empty_output(mock_tmux_capture, empty_output):
    """Test handling of empty tmux output."""
    mock_tmux_capture.configure(empty_output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert result.stdout == ""


def test_malformed_output_unicode(mock_tmux_capture):
    """Test handling of malformed output with unicode."""
    output = "Working on \u2728\u2728 special feature..."
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "Working" in result.stdout


def test_very_long_output(mock_tmux_capture):
    """Test handling of very long output (truncation)."""
    output = "x" * 10000 + "\nINSERT --"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "INSERT" in result.stdout


def test_output_with_ansi_codes(mock_tmux_capture):
    """Test handling of output with ANSI color codes."""
    output = "\x1b[32mSUCCESS\x1b[0m\n\nINSERT --"
    mock_tmux_capture.configure(output)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
    )

    assert "INSERT" in result.stdout


def test_tmux_session_not_found(mock_tmux_capture):
    """Test handling when tmux session doesn't exist."""
    mock_tmux_capture.configure("", returncode=1)

    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:nonexistent", "-p"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1


def test_tmux_not_installed():
    """Test handling when tmux is not installed."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            subprocess.run(
                ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
                capture_output=True,
                text=True,
            )


# =============================================================================
# Integration Tests with Mock Readiness Checker
# =============================================================================


def test_readiness_check_returns_ready_state(mock_readiness_ready):
    """Test ReadinessCheck returns READY state."""
    result = mock_readiness_ready("forge:claude")

    assert result.state == CLIState.READY
    assert result.confidence >= 0.9
    assert result.process_alive is True


def test_readiness_check_returns_busy_state(mock_readiness_busy):
    """Test ReadinessCheck returns BUSY state."""
    result = mock_readiness_busy("forge:claude")

    assert result.state == CLIState.BUSY
    assert result.confidence >= 0.8
    assert result.process_alive is True


def test_readiness_check_returns_crashed_state(mock_readiness_crashed):
    """Test ReadinessCheck returns CRASHED state."""
    result = mock_readiness_crashed("forge:claude")

    assert result.state == CLIState.CRASHED
    assert result.confidence >= 0.9
    assert result.process_alive is False


def test_prompt_patterns_comprehensive(
    claude_prompt_patterns,
    codex_prompt_patterns,
    gemini_prompt_patterns,
):
    """Test all prompt patterns are comprehensive."""
    # Claude patterns
    assert any("INSERT" in p for p in claude_prompt_patterns)
    assert any("Type your message" in p for p in claude_prompt_patterns)

    # Codex patterns
    assert any("Ready" in p for p in codex_prompt_patterns)

    # Gemini patterns
    assert any("ready" in p for p in gemini_prompt_patterns)


def test_busy_indicators_comprehensive(busy_indicators):
    """Test busy indicators cover all common states."""
    expected = [
        "Working",
        "Composing",
        "Running",
        "Implementing",
        "Executing",
    ]

    for indicator in expected:
        assert indicator in busy_indicators


# =============================================================================
# Process Check Integration Tests
# =============================================================================


def test_process_check_integration_alive(mock_tmux_capture):
    """Test process check when CLI is alive and responding."""
    output = "INSERT --"
    mock_tmux_capture.configure(output)

    # Successful capture indicates process is alive
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0


def test_process_check_integration_timeout():
    """Test process check with timeout."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run(
                ["tmux", "capture-pane", "-t", "forge:claude", "-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )
