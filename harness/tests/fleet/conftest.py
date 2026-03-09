"""Shared pytest fixtures for fleet dispatch reliability tests.

Provides mocks and fixtures for testing tmux dispatch components:
- Readiness detection
- Message verification
- Retry logic
- Circuit breaker
- Full integration scenarios
"""

from dataclasses import dataclass
from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =============================================================================
# Enums and Data Models (to be implemented in actual code)
# =============================================================================


class CLIState(Enum):
    """CLI readiness states."""

    READY = "ready"
    BUSY = "busy"
    CRASHED = "crashed"
    UNKNOWN = "unknown"


@dataclass
class ReadinessCheck:
    """Result of a readiness check."""

    state: CLIState
    confidence: float
    last_output: str
    prompt_match: str | None = None
    process_alive: bool = True


@dataclass
class VerificationResult:
    """Result of message verification."""

    verified: bool
    message_id: str
    delivery_time_ms: int
    method: str  # "echo" or "activity"
    error: str | None = None


@dataclass
class RetryResult:
    """Result of retry operation."""

    success: bool
    attempts: int
    total_time_ms: int
    final_error: str | None = None


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_tmux():
    """Mock tmux command execution via subprocess.run."""
    with patch("subprocess.run") as mock:
        # Default success response
        mock.return_value = MagicMock(
            returncode=0,
            stdout="INSERT --",
            stderr="",
        )
        yield mock


@pytest.fixture
def mock_tmux_capture():
    """Mock tmux capture-pane with configurable output."""
    with patch("subprocess.run") as mock:

        def configure_output(output: str, returncode: int = 0):
            """Configure the mock to return specific output."""
            mock.return_value = MagicMock(
                returncode=returncode,
                stdout=output,
                stderr="",
            )

        mock.configure = configure_output
        configure_output("INSERT --")  # Default
        yield mock


@pytest.fixture
def mock_tmux_send_keys():
    """Mock tmux send-keys command."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock


@pytest.fixture
def mock_time():
    """Mock time.time() for testing timing logic."""
    with patch("time.time") as mock:
        mock.return_value = 1000.0
        yield mock


@pytest.fixture
def mock_sleep():
    """Mock asyncio.sleep for faster tests."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock:
        yield mock


# =============================================================================
# Readiness Check Fixtures
# =============================================================================


@pytest.fixture
def ready_output():
    """Sample tmux output showing ready state."""
    return """
    Previous command output...

    INSERT --
    Type your message to Claude Code...
    """


@pytest.fixture
def busy_output():
    """Sample tmux output showing busy state."""
    return """
    Previous context...

    Working on your request...
    Implementing the feature now.
    """


@pytest.fixture
def crashed_output():
    """Sample tmux output showing crashed process."""
    return """
    Error: Process terminated unexpectedly
    [Process exited with code 1]
    bash-5.1$
    """


@pytest.fixture
def empty_output():
    """Empty tmux output."""
    return ""


@pytest.fixture
def mock_readiness_ready():
    """Mock readiness check returning READY state."""

    def _check(*args, **kwargs):
        return ReadinessCheck(
            state=CLIState.READY,
            confidence=0.95,
            last_output="INSERT --",
            prompt_match="INSERT",
            process_alive=True,
        )

    return _check


@pytest.fixture
def mock_readiness_busy():
    """Mock readiness check returning BUSY state."""

    def _check(*args, **kwargs):
        return ReadinessCheck(
            state=CLIState.BUSY,
            confidence=0.90,
            last_output="Working on your request...",
            prompt_match=None,
            process_alive=True,
        )

    return _check


@pytest.fixture
def mock_readiness_crashed():
    """Mock readiness check returning CRASHED state."""

    def _check(*args, **kwargs):
        return ReadinessCheck(
            state=CLIState.CRASHED,
            confidence=0.99,
            last_output="[Process exited with code 1]",
            prompt_match=None,
            process_alive=False,
        )

    return _check


# =============================================================================
# Verification Fixtures
# =============================================================================


@pytest.fixture
def verification_echo_success():
    """Mock successful echo verification."""
    return VerificationResult(
        verified=True,
        message_id="msg_abc123",
        delivery_time_ms=250,
        method="echo",
        error=None,
    )


@pytest.fixture
def verification_echo_timeout():
    """Mock echo verification timeout."""
    return VerificationResult(
        verified=False,
        message_id="msg_timeout",
        delivery_time_ms=5000,
        method="echo",
        error="Echo pattern not detected within timeout",
    )


@pytest.fixture
def verification_activity_fallback():
    """Mock activity-based verification."""
    return VerificationResult(
        verified=True,
        message_id="msg_activity",
        delivery_time_ms=1000,
        method="activity",
        error=None,
    )


# =============================================================================
# Circuit Breaker Fixtures
# =============================================================================


@pytest.fixture
def circuit_breaker_closed():
    """Circuit breaker in CLOSED state."""
    from unittest.mock import MagicMock

    breaker = MagicMock()
    breaker.state = CircuitState.CLOSED
    breaker.failure_count = 0
    breaker.can_attempt.return_value = True
    return breaker


@pytest.fixture
def circuit_breaker_open():
    """Circuit breaker in OPEN state."""
    from unittest.mock import MagicMock

    breaker = MagicMock()
    breaker.state = CircuitState.OPEN
    breaker.failure_count = 5
    breaker.can_attempt.return_value = False
    return breaker


@pytest.fixture
def circuit_breaker_half_open():
    """Circuit breaker in HALF_OPEN state."""
    from unittest.mock import MagicMock

    breaker = MagicMock()
    breaker.state = CircuitState.HALF_OPEN
    breaker.failure_count = 3
    breaker.can_attempt.return_value = True
    return breaker


# =============================================================================
# Dispatch Client Fixtures
# =============================================================================


@pytest.fixture
def forge_root(tmp_path):
    """Create temporary FORGE root directory."""
    root = tmp_path / "forge"
    root.mkdir()

    # Create expected directories
    (root / ".forge" / "heartbeat").mkdir(parents=True)
    (root / ".forge" / "metrics").mkdir(parents=True)

    return root


@pytest.fixture
def dispatch_client(forge_root):
    """Create DispatchClient instance for testing.

    Note: This will fail until DispatchClient is implemented.
    For now, returns a mock object.
    """
    from unittest.mock import MagicMock

    client = MagicMock()
    client.forge_root = forge_root
    client.metrics_dir = forge_root / ".forge" / "metrics"

    return client


@pytest.fixture
def mock_git_lock():
    """Mock git lock coordination."""
    with patch("filelock.FileLock") as mock:
        lock = MagicMock()
        lock.__enter__ = MagicMock(return_value=lock)
        lock.__exit__ = MagicMock(return_value=None)
        mock.return_value = lock
        yield mock


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def sample_task():
    """Sample task for dispatch testing."""
    return {
        "id": "IS-042",
        "title": "Implement user authentication",
        "description": "Add JWT-based authentication to API",
        "priority": "high",
        "estimated_effort": "medium",
    }


@pytest.fixture
def sample_dispatch_message():
    """Sample dispatch message."""
    return "Implement feature IS-042: Add JWT authentication to API"


@pytest.fixture
def claude_prompt_patterns():
    """Common Claude Code prompt patterns."""
    return [
        "INSERT --",
        "Type your message",
        "How can I help",
        "What would you like",
    ]


@pytest.fixture
def codex_prompt_patterns():
    """Common Codex prompt patterns."""
    return [
        "Ready for your next request",
        "What shall I build",
        ">>>",
    ]


@pytest.fixture
def gemini_prompt_patterns():
    """Common Gemini prompt patterns."""
    return [
        "I'm ready to help",
        "What can I do",
        "model>",
    ]


@pytest.fixture
def busy_indicators():
    """Indicators that agent is busy."""
    return [
        "Working",
        "Composing",
        "Running",
        "Implementing",
        "Writing",
        "Executing",
        "Testing",
        "Building",
        "Analyzing",
    ]


# =============================================================================
# Metrics Fixtures
# =============================================================================


@pytest.fixture
def mock_metrics_recorder():
    """Mock metrics recording (no-op since forge_harness.fleet.metrics never existed)."""
    return MagicMock()


@pytest.fixture
def dispatch_metrics():
    """Sample dispatch metrics."""
    return {
        "total_dispatches": 100,
        "successful_dispatches": 95,
        "failed_dispatches": 5,
        "avg_delivery_time_ms": 300,
        "circuit_breaker_opens": 2,
        "retries": 10,
    }


# =============================================================================
# Async Test Utilities
# =============================================================================


@pytest.fixture
def event_loop():
    """Provide event loop for async tests."""
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Cleanup
# =============================================================================


@pytest.fixture(autouse=True)
def cleanup_module_state():
    """Clean up any module-level state between tests."""
    yield
    # Reset any global state here if needed
