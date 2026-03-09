# Fleet Dispatch Reliability Test Suite

Comprehensive test suite for the tmux dispatch reliability framework, designed to improve fleet agent dispatch success from 25% to 100%.

## Overview

This test suite validates all components of the reliability layer:
- **Readiness Detection** - Detect when CLI agents are ready vs busy
- **Message Verification** - Confirm messages are delivered and received
- **Retry Logic** - Exponential backoff and max retry enforcement
- **Circuit Breaker** - Prevent cascading failures with state management
- **Integration** - Full dispatch flow with all components working together
- **Chaos Testing** - Extreme scenarios and failure recovery

## Test Structure

```
tests/fleet/
├── conftest.py                    # Shared fixtures and mocks
├── test_readiness.py              # Readiness detection (15+ tests)
├── test_verification.py           # Message verification (12+ tests)
├── test_retry.py                  # Retry logic & circuit breaker (15+ tests)
├── test_dispatch_client.py        # Integration tests (10+ tests)
├── test_chaos.py                  # Chaos tests (8+ tests)
└── README.md                      # This file
```

**Total Tests: 60+**

## Running Tests

### Run All Fleet Tests
```bash
cd /Users/bogdan/work/FORGE/harness
uv run pytest tests/fleet/ -v
```

### Run Specific Test File
```bash
uv run pytest tests/fleet/test_readiness.py -v
uv run pytest tests/fleet/test_verification.py -v
uv run pytest tests/fleet/test_retry.py -v
uv run pytest tests/fleet/test_dispatch_client.py -v
uv run pytest tests/fleet/test_chaos.py -v
```

### Run with Coverage
```bash
uv run pytest tests/fleet/ --cov=forge_harness.fleet --cov-report=term-missing
```

### Run Performance Tests Only
```bash
uv run pytest tests/fleet/test_verification.py -k "performance" -v
```

### Run Chaos Tests Only
```bash
uv run pytest tests/fleet/test_chaos.py -v
```

## Test Categories

### 1. Readiness Detection Tests (`test_readiness.py`)

**Purpose:** Validate detection of CLI agent states (ready, busy, crashed)

**Key Tests:**
- `test_detect_claude_ready` - Claude Code ready state
- `test_detect_codex_ready` - Codex ready state
- `test_detect_gemini_ready` - Gemini ready state
- `test_detect_busy_state_working` - Busy indicator detection
- `test_detect_crashed_process` - Process crash detection
- `test_high_confidence_ready_multiple_indicators` - Confidence scoring
- `test_empty_output` - Empty output handling
- `test_tmux_not_installed` - tmux missing error

**Success Criteria:**
- 95%+ confidence for ready state detection
- 90%+ confidence for busy state detection
- Handles all edge cases (empty, malformed, ANSI codes)

### 2. Message Verification Tests (`test_verification.py`)

**Purpose:** Validate message delivery confirmation via echo pattern and activity detection

**Key Tests:**
- `test_echo_pattern_success` - Successful echo verification
- `test_echo_pattern_timeout` - Echo timeout fallback
- `test_activity_detection_fallback` - Activity-based verification
- `test_unique_message_ids` - Unique ID generation
- `test_concurrent_verifications_no_collision` - Concurrent verification safety
- `test_verification_completes_under_5_seconds` - Performance requirement

**Success Criteria:**
- Echo verification completes in <500ms (fast path)
- Total verification timeout at 5 seconds
- 100% unique message IDs across concurrent dispatches
- Graceful fallback to activity detection

### 3. Retry Logic Tests (`test_retry.py`)

**Purpose:** Validate exponential backoff, retry limits, and circuit breaker

**Key Tests:**
- `test_exponential_backoff_timing` - 1s, 2s, 4s, 8s, 16s pattern
- `test_max_retries_enforcement` - Max retry limit
- `test_retry_decision_transient_failure` - Retryable failures
- `test_retry_decision_terminal_failure` - Non-retryable failures
- `test_circuit_breaker_closed_to_open` - Circuit opens on threshold
- `test_circuit_breaker_half_open_to_closed` - Circuit recovery
- `test_circuit_breaker_timeout_recovery` - Timeout recovery

**Success Criteria:**
- Exponential backoff follows 2^n pattern
- Max 5 retries by default
- Circuit breaker opens after 3 failures
- Circuit breaker recovers after 30s timeout

### 4. Integration Tests (`test_dispatch_client.py`)

**Purpose:** Validate full dispatch flow with all components

**Key Tests:**
- `test_dispatch_happy_path` - Successful dispatch flow
- `test_dispatch_waits_for_ready` - Wait for agent ready
- `test_dispatch_detects_crash` - Crash detection
- `test_dispatch_restarts_crashed_agent` - Agent restart
- `test_git_lock_serializes_dispatches` - Git lock coordination
- `test_circuit_breaker_opens_on_failures` - Circuit breaker integration
- `test_metrics_records_success` - Metrics recording

**Success Criteria:**
- Happy path completes in <500ms
- Waits up to 60s for agent to become ready
- Detects and restarts crashed agents
- Git lock prevents concurrent dispatches
- Metrics recorded for all dispatches

### 5. Chaos Tests (`test_chaos.py`)

**Purpose:** Validate extreme scenarios and failure recovery

**Key Tests:**
- `test_10_parallel_dispatches_no_races` - Parallel dispatch safety
- `test_agent_killed_during_message_send` - Mid-dispatch kill
- `test_tmux_session_disconnect_fail_fast` - Session disconnection
- `test_out_of_order_message_delivery` - Out-of-order handling
- `test_extremely_long_message_handling` - >10KB messages
- `test_concurrent_circuit_breaker_state_changes` - Concurrent state changes
- `test_rapid_fire_dispatches` - 100 rapid dispatches

**Success Criteria:**
- No race conditions with 10+ parallel dispatches
- Graceful handling of agent kills
- Fail fast on tmux disconnection
- Handle messages up to 15KB
- Support 100+ rapid-fire dispatches

## Test Fixtures

### Mock Fixtures (conftest.py)

**Tmux Mocks:**
- `mock_tmux` - Mock tmux subprocess calls
- `mock_tmux_capture` - Mock capture-pane with configurable output
- `mock_tmux_send_keys` - Mock send-keys command

**Time Mocks:**
- `mock_time` - Mock time.time()
- `mock_sleep` - Mock asyncio.sleep for faster tests

**Readiness Fixtures:**
- `mock_readiness_ready` - Returns READY state
- `mock_readiness_busy` - Returns BUSY state
- `mock_readiness_crashed` - Returns CRASHED state

**Verification Fixtures:**
- `verification_echo_success` - Successful verification
- `verification_echo_timeout` - Timeout scenario
- `verification_activity_fallback` - Activity detection

**Circuit Breaker Fixtures:**
- `circuit_breaker_closed` - CLOSED state
- `circuit_breaker_open` - OPEN state
- `circuit_breaker_half_open` - HALF_OPEN state

## Data Models

### CLIState Enum
```python
class CLIState(Enum):
    READY = "ready"
    BUSY = "busy"
    CRASHED = "crashed"
    UNKNOWN = "unknown"
```

### ReadinessCheck
```python
@dataclass
class ReadinessCheck:
    state: CLIState
    confidence: float
    last_output: str
    prompt_match: str | None
    process_alive: bool
```

### VerificationResult
```python
@dataclass
class VerificationResult:
    verified: bool
    message_id: str
    delivery_time_ms: int
    method: str  # "echo" or "activity"
    error: str | None
```

### RetryResult
```python
@dataclass
class RetryResult:
    success: bool
    attempts: int
    total_time_ms: int
    final_error: str | None
```

### CircuitState Enum
```python
class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
```

## Performance Requirements

### Verification Performance
- **Fast Path:** <500ms (when agent immediately echoes)
- **Timeout:** 5 seconds maximum
- **Polling Interval:** 500ms

### Retry Performance
- **Backoff Pattern:** 1s, 2s, 4s, 8s, 16s (exponential)
- **Max Delay:** 32s (capped)
- **Max Retries:** 5 attempts
- **Total Max Time:** ~31s (1+2+4+8+16)

### Circuit Breaker Performance
- **Failure Threshold:** 3 consecutive failures
- **Timeout:** 30 seconds
- **Recovery:** Single successful request in HALF_OPEN

## Coverage Goals

- **Overall Coverage:** 80%+
- **Critical Paths:** 95%+
- **Edge Cases:** All covered
- **Error Paths:** All covered

## Test Execution Time

- **Unit Tests:** <10s total
- **Integration Tests:** <15s total
- **Chaos Tests:** <10s total
- **Full Suite:** <30s total

## Continuous Integration

Tests are designed to run in CI with:
- Mock-based isolation (no real tmux required)
- Fast execution (<30s)
- No external dependencies
- Deterministic results

## Writing New Tests

### Test Naming Convention
```python
def test_<component>_<scenario>_<expected_result>():
    """Clear docstring explaining what is tested."""
```

### Example Test Structure
```python
@pytest.mark.asyncio
async def test_dispatch_happy_path(
    mock_readiness_ready,
    mock_tmux_send_keys,
    verification_echo_success
):
    """Test successful dispatch with ready agent."""
    # Arrange
    agent_name = "claude"
    message = "Implement feature"

    # Act
    readiness = mock_readiness_ready()
    mock_tmux_send_keys.return_value = MagicMock(returncode=0)
    verification = verification_echo_success

    # Assert
    assert readiness.state == CLIState.READY
    assert verification.verified is True
```

## Debugging Tests

### Run Single Test
```bash
uv run pytest tests/fleet/test_readiness.py::test_detect_claude_ready -v
```

### Run with Print Statements
```bash
uv run pytest tests/fleet/test_readiness.py -v -s
```

### Run with Debugger
```bash
uv run pytest tests/fleet/test_readiness.py --pdb
```

### Show Test Coverage
```bash
uv run pytest tests/fleet/ --cov=forge_harness.fleet --cov-report=html
open htmlcov/index.html
```

## Known Issues

1. **Mock Timing:** Some timing tests use mock_sleep which returns instantly. Real timing behavior may differ slightly.
2. **Subprocess Mocking:** Tests mock subprocess.run, so actual tmux behavior isn't tested. Recommend manual integration testing.
3. **Concurrency:** Async tests use asyncio.gather which may execute in different order than production.

## Future Enhancements

1. **Property-Based Testing:** Use Hypothesis for property-based tests
2. **Load Testing:** Add locust/k6 tests for sustained load
3. **Real Integration Tests:** Tests against actual tmux sessions
4. **Mutation Testing:** Use mutmut to verify test quality

## Test Maintenance

### Before Committing
1. Run full test suite
2. Check coverage (must be 80%+)
3. Verify all tests pass
4. Update README if new tests added

### Quarterly Reviews
1. Review and remove redundant tests
2. Add tests for new edge cases discovered
3. Update performance benchmarks
4. Refresh documentation

## Support

For questions or issues with tests:
1. Check this README
2. Review conftest.py for available fixtures
3. Look at similar existing tests for patterns
4. Consult backend engineer implementing the components

## References

- [Pytest Documentation](https://docs.pytest.org/)
- [Pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)
- [FORGE Testing Standards](../../docs/TESTING_STANDARDS.md)
