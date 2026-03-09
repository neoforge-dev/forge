"""Comprehensive unit tests for the adaptive retry handler.

Covers:
- FailureCategory enum values
- RetryStrategy enum values
- FailureContext creation, serialization, and deserialization
- RetryContext creation, serialization, deserialization, and helpers
- FailurePatternClassifier classification logic for all categories
- UnblockInstructionGenerator strategy assignment and instruction generation
- AdaptiveRetryHandler: capture_failure, should_retry, prepare_retry_message,
  get_retry_delay, clear_context, save_context, load_context, get_stats
- Singleton get_adaptive_retry_handler and reset_adaptive_retry_handler
- Edge cases: max retries exceeded, unknown categories, missing context files
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.models.delivery import DeliveryRecord, DeliveryState
from forge_harness.webhook_server.handlers.adaptive_retry_handler import (
    AdaptiveRetryHandler,
    FailureCategory,
    FailureContext,
    FailurePatternClassifier,
    RetryContext,
    RetryStrategy,
    UnblockInstructionGenerator,
    get_adaptive_retry_handler,
    reset_adaptive_retry_handler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    message_id: str = "msg-test-001",
    task_id: str | None = "TASK-1",
    payload_summary: str = "do something",
) -> DeliveryRecord:
    """Create a minimal DeliveryRecord for testing."""
    return DeliveryRecord(
        message_id=message_id,
        task_id=task_id,
        source_node="prya",
        target_node="sati",
        state=DeliveryState.QUEUED,
        created_at=datetime.now(UTC),
        payload_summary=payload_summary,
    )


def _make_handler(tmp_path: Path, max_retries: int = 3) -> AdaptiveRetryHandler:
    """Create an AdaptiveRetryHandler using a temporary directory."""
    context_dir = tmp_path / "retry_context"
    return AdaptiveRetryHandler(max_retries=max_retries, context_dir=context_dir)


# ---------------------------------------------------------------------------
# FailureCategory enum
# ---------------------------------------------------------------------------


class TestFailureCategory:
    """Tests for the FailureCategory enum."""

    def test_all_expected_members_exist(self):
        """All documented failure categories are present."""
        expected = {
            "agent_unavailable",
            "agent_not_ready",
            "timeout",
            "permission_denied",
            "git_lock",
            "network_error",
            "rate_limited",
            "unknown",
        }
        actual = {cat.value for cat in FailureCategory}
        assert actual == expected

    def test_is_str_enum(self):
        """FailureCategory is a str enum (values are strings)."""
        assert isinstance(FailureCategory.AGENT_UNAVAILABLE, str)
        assert FailureCategory.AGENT_UNAVAILABLE == "agent_unavailable"

    def test_string_construction(self):
        """FailureCategory can be constructed from its string value."""
        assert FailureCategory("git_lock") is FailureCategory.GIT_LOCK
        assert FailureCategory("unknown") is FailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# RetryStrategy enum
# ---------------------------------------------------------------------------


class TestRetryStrategy:
    """Tests for the RetryStrategy enum."""

    def test_all_expected_members_exist(self):
        """All documented retry strategies are present."""
        expected = {"immediate", "backoff", "escalate", "skip", "rotate_agent"}
        actual = {s.value for s in RetryStrategy}
        assert actual == expected

    def test_is_str_enum(self):
        """RetryStrategy is a str enum."""
        assert isinstance(RetryStrategy.BACKOFF, str)
        assert RetryStrategy.BACKOFF == "backoff"

    def test_string_construction(self):
        """RetryStrategy can be constructed from its string value."""
        assert RetryStrategy("escalate") is RetryStrategy.ESCALATE


# ---------------------------------------------------------------------------
# FailureContext
# ---------------------------------------------------------------------------


class TestFailureContext:
    """Tests for the FailureContext dataclass."""

    def test_minimal_creation(self):
        """FailureContext can be created with required fields only."""
        ctx = FailureContext(
            message_id="msg-001",
            error_message="something went wrong",
            category=FailureCategory.TIMEOUT,
        )
        assert ctx.message_id == "msg-001"
        assert ctx.error_message == "something went wrong"
        assert ctx.category is FailureCategory.TIMEOUT
        assert ctx.attempt_number == 1
        assert ctx.agent_state is None
        assert ctx.suggested_action == ""
        assert isinstance(ctx.timestamp, datetime)

    def test_full_creation(self):
        """FailureContext can be created with all fields."""
        ts = datetime(2026, 2, 26, 12, 0, 0, tzinfo=UTC)
        ctx = FailureContext(
            message_id="msg-002",
            error_message="rate limit hit",
            category=FailureCategory.RATE_LIMITED,
            timestamp=ts,
            attempt_number=2,
            agent_state={"status": "busy"},
            suggested_action="wait and retry",
        )
        assert ctx.attempt_number == 2
        assert ctx.agent_state == {"status": "busy"}
        assert ctx.suggested_action == "wait and retry"
        assert ctx.timestamp == ts

    def test_to_dict_contains_all_fields(self):
        """to_dict produces a dict with all expected keys."""
        ctx = FailureContext(
            message_id="msg-003",
            error_message="permission denied",
            category=FailureCategory.PERMISSION_DENIED,
        )
        d = ctx.to_dict()
        assert d["message_id"] == "msg-003"
        assert d["error_message"] == "permission denied"
        assert d["category"] == "permission_denied"
        assert "timestamp" in d
        assert d["attempt_number"] == 1
        assert d["agent_state"] is None
        assert d["suggested_action"] == ""

    def test_to_dict_category_is_string_value(self):
        """to_dict serializes category as its string value, not enum member."""
        ctx = FailureContext(
            message_id="msg-004",
            error_message="git lock",
            category=FailureCategory.GIT_LOCK,
        )
        d = ctx.to_dict()
        assert d["category"] == "git_lock"
        assert isinstance(d["category"], str)

    def test_from_dict_roundtrip(self):
        """from_dict recovers a FailureContext serialized with to_dict."""
        original = FailureContext(
            message_id="msg-005",
            error_message="network error",
            category=FailureCategory.NETWORK_ERROR,
            attempt_number=3,
            agent_state={"node": "sati"},
            suggested_action="check network",
        )
        recovered = FailureContext.from_dict(original.to_dict())
        assert recovered.message_id == original.message_id
        assert recovered.error_message == original.error_message
        assert recovered.category == original.category
        assert recovered.attempt_number == original.attempt_number
        assert recovered.agent_state == original.agent_state
        assert recovered.suggested_action == original.suggested_action

    def test_from_dict_defaults_for_missing_optional_fields(self):
        """from_dict uses defaults when optional fields are absent."""
        minimal = {
            "message_id": "msg-006",
            "error_message": "err",
            "category": "unknown",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        ctx = FailureContext.from_dict(minimal)
        assert ctx.attempt_number == 1
        assert ctx.agent_state is None
        assert ctx.suggested_action == ""

    def test_from_dict_category_defaults_to_unknown_on_missing_key(self):
        """from_dict defaults category to UNKNOWN when key is absent."""
        data = {
            "message_id": "msg-007",
            "error_message": "err",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        ctx = FailureContext.from_dict(data)
        assert ctx.category is FailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# RetryContext
# ---------------------------------------------------------------------------


class TestRetryContext:
    """Tests for the RetryContext dataclass."""

    def test_minimal_creation(self):
        """RetryContext can be created with only message_id."""
        ctx = RetryContext(message_id="msg-100")
        assert ctx.message_id == "msg-100"
        assert ctx.task_id is None
        assert ctx.original_payload == ""
        assert ctx.failures == []
        assert ctx.retry_count == 0
        assert ctx.max_retries == 3
        assert ctx.last_attempt_at is None

    def test_get_latest_failure_empty(self):
        """get_latest_failure returns None when there are no failures."""
        ctx = RetryContext(message_id="msg-101")
        assert ctx.get_latest_failure() is None

    def test_get_latest_failure_returns_last(self):
        """get_latest_failure returns the last failure appended."""
        f1 = FailureContext(
            message_id="msg-102", error_message="e1", category=FailureCategory.TIMEOUT
        )
        f2 = FailureContext(
            message_id="msg-102", error_message="e2", category=FailureCategory.GIT_LOCK
        )
        ctx = RetryContext(message_id="msg-102", failures=[f1, f2])
        assert ctx.get_latest_failure() is f2

    def test_get_failure_summary_no_failures(self):
        """get_failure_summary returns descriptive text when empty."""
        ctx = RetryContext(message_id="msg-103")
        assert ctx.get_failure_summary() == "No failures recorded"

    def test_get_failure_summary_single_category(self):
        """get_failure_summary shows count and category name."""
        f = FailureContext(
            message_id="msg-104", error_message="t", category=FailureCategory.TIMEOUT
        )
        ctx = RetryContext(message_id="msg-104", failures=[f])
        summary = ctx.get_failure_summary()
        assert "1 failures" in summary
        assert "timeout" in summary

    def test_get_failure_summary_multiple_categories(self):
        """get_failure_summary groups failures by category."""
        failures = [
            FailureContext(
                message_id="msg-105", error_message="t1", category=FailureCategory.TIMEOUT
            ),
            FailureContext(
                message_id="msg-105", error_message="t2", category=FailureCategory.TIMEOUT
            ),
            FailureContext(
                message_id="msg-105", error_message="g", category=FailureCategory.GIT_LOCK
            ),
        ]
        ctx = RetryContext(message_id="msg-105", failures=failures)
        summary = ctx.get_failure_summary()
        assert "3 failures" in summary
        assert "2x timeout" in summary
        assert "1x git_lock" in summary

    def test_to_dict_roundtrip(self):
        """RetryContext serializes and deserializes correctly."""
        original = RetryContext(
            message_id="msg-106",
            task_id="TASK-5",
            original_payload="run tests",
            retry_count=1,
            max_retries=5,
        )
        recovered = RetryContext.from_dict(original.to_dict())
        assert recovered.message_id == original.message_id
        assert recovered.task_id == original.task_id
        assert recovered.original_payload == original.original_payload
        assert recovered.retry_count == original.retry_count
        assert recovered.max_retries == original.max_retries

    def test_to_dict_last_attempt_at_none(self):
        """to_dict serializes last_attempt_at as None when not set."""
        ctx = RetryContext(message_id="msg-107")
        d = ctx.to_dict()
        assert d["last_attempt_at"] is None

    def test_to_dict_last_attempt_at_set(self):
        """to_dict serializes last_attempt_at as ISO string when set."""
        ts = datetime(2026, 2, 26, 10, 0, 0, tzinfo=UTC)
        ctx = RetryContext(message_id="msg-108", last_attempt_at=ts)
        d = ctx.to_dict()
        assert d["last_attempt_at"] == ts.isoformat()

    def test_from_dict_with_failures(self):
        """from_dict reconstructs nested FailureContext objects."""
        failure = FailureContext(
            message_id="msg-109",
            error_message="timeout",
            category=FailureCategory.TIMEOUT,
        )
        original = RetryContext(message_id="msg-109", failures=[failure])
        d = original.to_dict()
        recovered = RetryContext.from_dict(d)
        assert len(recovered.failures) == 1
        assert recovered.failures[0].category is FailureCategory.TIMEOUT

    def test_from_dict_defaults_on_missing_fields(self):
        """from_dict uses defaults when optional fields are absent."""
        minimal = {
            "message_id": "msg-110",
            "created_at": datetime.now(UTC).isoformat(),
        }
        ctx = RetryContext.from_dict(minimal)
        assert ctx.task_id is None
        assert ctx.original_payload == ""
        assert ctx.failures == []
        assert ctx.retry_count == 0
        assert ctx.max_retries == 3


# ---------------------------------------------------------------------------
# FailurePatternClassifier
# ---------------------------------------------------------------------------


class TestFailurePatternClassifier:
    """Tests for FailurePatternClassifier.classify()."""

    @pytest.fixture
    def classifier(self):
        return FailurePatternClassifier()

    # AGENT_UNAVAILABLE
    @pytest.mark.parametrize(
        "error",
        [
            "no agent found",
            "Agent not found in registry",
            "window not found",
            "session not found",
            "can't find session forge:glm",
        ],
    )
    def test_classify_agent_unavailable(self, classifier, error):
        assert classifier.classify(error) is FailureCategory.AGENT_UNAVAILABLE

    # AGENT_NOT_READY
    @pytest.mark.parametrize(
        "error",
        [
            "agent not ready to receive",
            "not accepting messages at this time",
            "agent is busy",
            "agent is occupied",
            "prompt not ready",
            "at prompt — not accepting",
        ],
    )
    def test_classify_agent_not_ready(self, classifier, error):
        assert classifier.classify(error) is FailureCategory.AGENT_NOT_READY

    # TIMEOUT
    @pytest.mark.parametrize(
        "error",
        [
            "delivery timeout",
            "request timed out after 30s",
            "deadline exceeded for operation",
            "operation took too long",
        ],
    )
    def test_classify_timeout(self, classifier, error):
        assert classifier.classify(error) is FailureCategory.TIMEOUT

    # PERMISSION_DENIED
    @pytest.mark.parametrize(
        "error",
        [
            "permission denied: cannot write",
            "access denied to resource",
            "unauthorized request",
            "forbidden path",
        ],
    )
    def test_classify_permission_denied(self, classifier, error):
        assert classifier.classify(error) is FailureCategory.PERMISSION_DENIED

    # GIT_LOCK
    @pytest.mark.parametrize(
        "error",
        [
            "error: .git/index.lock exists",
            "git lock held by another process",
            "unable to create lock file",
            "file exists: cannot write",
        ],
    )
    def test_classify_git_lock(self, classifier, error):
        assert classifier.classify(error) is FailureCategory.GIT_LOCK

    # NETWORK_ERROR
    @pytest.mark.parametrize(
        "error",
        [
            "connection refused on port 8080",
            "network unreachable",
            "no route to host",
            "dns error: cannot resolve",
            "connection reset by peer",
        ],
    )
    def test_classify_network_error(self, classifier, error):
        assert classifier.classify(error) is FailureCategory.NETWORK_ERROR

    # RATE_LIMITED
    @pytest.mark.parametrize(
        "error",
        [
            "rate limit exceeded",
            "too many requests in a short period",
            "HTTP 429",
            "quota exceeded for API",
        ],
    )
    def test_classify_rate_limited(self, classifier, error):
        assert classifier.classify(error) is FailureCategory.RATE_LIMITED

    # UNKNOWN
    @pytest.mark.parametrize(
        "error",
        [
            "some completely random message",
            "",
            "xyz123 unrecognized error",
            "RuntimeError in user code",
        ],
    )
    def test_classify_unknown(self, classifier, error):
        assert classifier.classify(error) is FailureCategory.UNKNOWN

    def test_case_insensitive_matching(self, classifier):
        """Classification is case-insensitive."""
        assert classifier.classify("TIMEOUT OCCURRED") is FailureCategory.TIMEOUT
        assert classifier.classify("GIT LOCK FILE EXISTS") is FailureCategory.GIT_LOCK
        assert classifier.classify("RATE LIMIT HIT") is FailureCategory.RATE_LIMITED

    def test_first_matching_category_wins(self, classifier):
        """First matching pattern wins when multiple could match."""
        # "busy" triggers AGENT_NOT_READY; also ensure we don't accidentally
        # fall through to UNKNOWN when a match is present.
        result = classifier.classify("agent is busy with another task")
        assert result is FailureCategory.AGENT_NOT_READY


# ---------------------------------------------------------------------------
# UnblockInstructionGenerator
# ---------------------------------------------------------------------------


class TestUnblockInstructionGenerator:
    """Tests for UnblockInstructionGenerator.generate()."""

    @pytest.fixture
    def generator(self):
        return UnblockInstructionGenerator()

    def test_all_categories_have_instructions(self, generator):
        """Every FailureCategory has a corresponding instruction."""
        for cat in FailureCategory:
            assert cat in generator.INSTRUCTIONS, f"Missing instruction for {cat}"

    def test_all_categories_have_strategies(self, generator):
        """Every FailureCategory has a corresponding retry strategy."""
        for cat in FailureCategory:
            assert cat in generator.STRATEGIES, f"Missing strategy for {cat}"

    def test_first_failure_context_label(self, generator):
        """First failure includes '[First failure' label."""
        instruction, _ = generator.generate(
            category=FailureCategory.TIMEOUT,
            attempt_number=1,
            max_retries=3,
            previous_failures=[],
        )
        assert "[First failure" in instruction

    def test_intermediate_retry_context_label(self, generator):
        """Intermediate retry includes attempt count and remaining attempts."""
        instruction, _ = generator.generate(
            category=FailureCategory.TIMEOUT,
            attempt_number=2,
            max_retries=3,
            previous_failures=[],
        )
        assert "[Retry 2/3" in instruction
        assert "1 attempts remaining" in instruction

    def test_final_attempt_context_label(self, generator):
        """Final attempt includes escalation label."""
        instruction, _ = generator.generate(
            category=FailureCategory.TIMEOUT,
            attempt_number=3,
            max_retries=3,
            previous_failures=[],
        )
        assert "[Final attempt 3/3" in instruction

    def test_strategy_escalate_when_max_retries_reached(self, generator):
        """Strategy becomes ESCALATE when attempt_number >= max_retries."""
        # BACKOFF is the normal strategy for TIMEOUT
        _, strategy = generator.generate(
            category=FailureCategory.TIMEOUT,
            attempt_number=3,
            max_retries=3,
            previous_failures=[],
        )
        assert strategy is RetryStrategy.ESCALATE

    def test_strategy_for_agent_unavailable(self, generator):
        """AGENT_UNAVAILABLE maps to ROTATE_AGENT strategy below max retries."""
        _, strategy = generator.generate(
            category=FailureCategory.AGENT_UNAVAILABLE,
            attempt_number=1,
            max_retries=3,
            previous_failures=[],
        )
        assert strategy is RetryStrategy.ROTATE_AGENT

    def test_strategy_for_permission_denied(self, generator):
        """PERMISSION_DENIED always maps to ESCALATE (even on first failure)."""
        _, strategy = generator.generate(
            category=FailureCategory.PERMISSION_DENIED,
            attempt_number=1,
            max_retries=3,
            previous_failures=[],
        )
        assert strategy is RetryStrategy.ESCALATE

    def test_strategy_for_git_lock(self, generator):
        """GIT_LOCK maps to BACKOFF below max retries."""
        _, strategy = generator.generate(
            category=FailureCategory.GIT_LOCK,
            attempt_number=1,
            max_retries=3,
            previous_failures=[],
        )
        assert strategy is RetryStrategy.BACKOFF

    def test_strategy_for_rate_limited(self, generator):
        """RATE_LIMITED maps to BACKOFF below max retries."""
        _, strategy = generator.generate(
            category=FailureCategory.RATE_LIMITED,
            attempt_number=1,
            max_retries=3,
            previous_failures=[],
        )
        assert strategy is RetryStrategy.BACKOFF

    def test_pattern_detected_annotation_with_repeated_failures(self, generator):
        """Instruction includes pattern-detected annotation for repeated same-category failures."""
        prev_failure = FailureContext(
            message_id="msg-P1",
            error_message="timeout",
            category=FailureCategory.TIMEOUT,
        )
        instruction, _ = generator.generate(
            category=FailureCategory.TIMEOUT,
            attempt_number=3,
            max_retries=5,
            previous_failures=[prev_failure, prev_failure],  # 2 previous same category
        )
        assert "[Pattern detected" in instruction
        assert "timeout" in instruction

    def test_no_pattern_annotation_with_single_previous_failure(self, generator):
        """No pattern annotation when only one previous failure."""
        prev = FailureContext(
            message_id="msg-P2",
            error_message="timeout",
            category=FailureCategory.TIMEOUT,
        )
        instruction, _ = generator.generate(
            category=FailureCategory.TIMEOUT,
            attempt_number=2,
            max_retries=5,
            previous_failures=[prev],
        )
        assert "[Pattern detected" not in instruction

    def test_base_instruction_included_in_output(self, generator):
        """Base category instruction text is included in the output."""
        instruction, _ = generator.generate(
            category=FailureCategory.GIT_LOCK,
            attempt_number=1,
            max_retries=3,
            previous_failures=[],
        )
        # From INSTRUCTIONS dict for GIT_LOCK
        assert "index.lock" in instruction

    def test_unknown_category_returns_escalate(self, generator):
        """UNKNOWN category returns ESCALATE strategy."""
        _, strategy = generator.generate(
            category=FailureCategory.UNKNOWN,
            attempt_number=1,
            max_retries=3,
            previous_failures=[],
        )
        assert strategy is RetryStrategy.ESCALATE


# ---------------------------------------------------------------------------
# AdaptiveRetryHandler - context I/O
# ---------------------------------------------------------------------------


class TestAdaptiveRetryHandlerContextIO:
    """Tests for context persistence (save/load/clear)."""

    def test_load_context_returns_none_for_missing_file(self, tmp_path):
        """load_context returns None when no context file exists."""
        handler = _make_handler(tmp_path)
        assert handler.load_context("nonexistent-id") is None

    def test_save_and_load_context_roundtrip(self, tmp_path):
        """Saved context is correctly loaded back."""
        handler = _make_handler(tmp_path)
        ctx = RetryContext(
            message_id="msg-save-001",
            task_id="TASK-99",
            original_payload="test payload",
            retry_count=2,
            max_retries=3,
        )
        handler.save_context(ctx)
        loaded = handler.load_context("msg-save-001")
        assert loaded is not None
        assert loaded.message_id == "msg-save-001"
        assert loaded.task_id == "TASK-99"
        assert loaded.retry_count == 2

    def test_save_creates_json_file(self, tmp_path):
        """save_context creates a .json file in the context directory."""
        handler = _make_handler(tmp_path)
        ctx = RetryContext(message_id="msg-file-001")
        handler.save_context(ctx)
        expected_path = tmp_path / "retry_context" / "msg-file-001.json"
        assert expected_path.exists()

    def test_save_writes_valid_json(self, tmp_path):
        """save_context writes valid JSON that can be parsed."""
        handler = _make_handler(tmp_path)
        ctx = RetryContext(message_id="msg-json-001")
        handler.save_context(ctx)
        path = tmp_path / "retry_context" / "msg-json-001.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["message_id"] == "msg-json-001"

    def test_clear_context_removes_file(self, tmp_path):
        """clear_context deletes the context file."""
        handler = _make_handler(tmp_path)
        ctx = RetryContext(message_id="msg-clear-001")
        handler.save_context(ctx)
        assert (tmp_path / "retry_context" / "msg-clear-001.json").exists()
        handler.clear_context("msg-clear-001")
        assert not (tmp_path / "retry_context" / "msg-clear-001.json").exists()

    def test_clear_context_noop_for_missing_file(self, tmp_path):
        """clear_context does not raise when file does not exist."""
        handler = _make_handler(tmp_path)
        # Should not raise
        handler.clear_context("nonexistent-id")

    def test_load_context_returns_none_on_corrupt_json(self, tmp_path):
        """load_context returns None if the JSON file is corrupt."""
        handler = _make_handler(tmp_path)
        context_dir = tmp_path / "retry_context"
        context_dir.mkdir(parents=True, exist_ok=True)
        (context_dir / "msg-corrupt.json").write_text("not valid json", encoding="utf-8")
        result = handler.load_context("msg-corrupt")
        assert result is None


# ---------------------------------------------------------------------------
# AdaptiveRetryHandler - capture_failure
# ---------------------------------------------------------------------------


class TestAdaptiveRetryHandlerCaptureFailure:
    """Tests for capture_failure()."""

    def test_capture_failure_returns_failure_context(self, tmp_path):
        """capture_failure returns a FailureContext."""
        handler = _make_handler(tmp_path)
        record = _make_record()
        failure = handler.capture_failure(record, "timed out")
        assert isinstance(failure, FailureContext)

    def test_capture_failure_classifies_correctly(self, tmp_path):
        """capture_failure classifies the error message."""
        handler = _make_handler(tmp_path)
        record = _make_record()
        failure = handler.capture_failure(record, "index.lock exists")
        assert failure.category is FailureCategory.GIT_LOCK

    def test_capture_failure_sets_message_id(self, tmp_path):
        """Captured failure has the record's message_id."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-capture-001")
        failure = handler.capture_failure(record, "timeout")
        assert failure.message_id == "msg-capture-001"

    def test_capture_failure_sets_suggested_action(self, tmp_path):
        """Captured failure has a non-empty suggested_action."""
        handler = _make_handler(tmp_path)
        record = _make_record()
        failure = handler.capture_failure(record, "timeout")
        assert failure.suggested_action != ""

    def test_capture_failure_creates_context_file(self, tmp_path):
        """capture_failure persists a context file to disk."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-capture-002")
        handler.capture_failure(record, "timeout")
        assert (tmp_path / "retry_context" / "msg-capture-002.json").exists()

    def test_capture_failure_increments_retry_count(self, tmp_path):
        """Subsequent capture_failure calls increment retry_count."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-capture-003")
        handler.capture_failure(record, "timeout")
        handler.capture_failure(record, "timeout again")
        ctx = handler.load_context("msg-capture-003")
        assert ctx is not None
        assert ctx.retry_count == 2

    def test_capture_failure_stores_agent_state(self, tmp_path):
        """agent_state is stored in the failure context."""
        handler = _make_handler(tmp_path)
        record = _make_record()
        agent_state = {"status": "busy", "last_seen": "2026-02-26T10:00:00Z"}
        failure = handler.capture_failure(record, "agent busy", agent_state=agent_state)
        assert failure.agent_state == agent_state

    def test_capture_failure_attempt_number_increments(self, tmp_path):
        """attempt_number increments with each capture call."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-capture-004")
        f1 = handler.capture_failure(record, "timeout")
        f2 = handler.capture_failure(record, "timeout")
        assert f1.attempt_number == 1
        assert f2.attempt_number == 2

    def test_capture_failure_uses_payload_summary_as_original_payload(self, tmp_path):
        """capture_failure stores record.payload_summary as original_payload."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-capture-005", payload_summary="run tests now")
        handler.capture_failure(record, "timeout")
        ctx = handler.load_context("msg-capture-005")
        assert ctx is not None
        assert ctx.original_payload == "run tests now"

    def test_capture_failure_updates_last_attempt_at(self, tmp_path):
        """capture_failure updates last_attempt_at on the retry context."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-capture-006")
        handler.capture_failure(record, "timeout")
        ctx = handler.load_context("msg-capture-006")
        assert ctx is not None
        assert ctx.last_attempt_at is not None


# ---------------------------------------------------------------------------
# AdaptiveRetryHandler - should_retry
# ---------------------------------------------------------------------------


class TestAdaptiveRetryHandlerShouldRetry:
    """Tests for should_retry()."""

    def test_should_retry_true_for_unknown_message(self, tmp_path):
        """No prior context means we allow retry with IMMEDIATE strategy."""
        handler = _make_handler(tmp_path)
        should, strategy = handler.should_retry("new-message-id")
        assert should is True
        assert strategy is RetryStrategy.IMMEDIATE

    def test_should_retry_false_when_max_retries_exceeded(self, tmp_path):
        """should_retry returns False once retry_count >= max_retries."""
        handler = _make_handler(tmp_path, max_retries=2)
        record = _make_record(message_id="msg-retry-001")
        handler.capture_failure(record, "timeout")
        handler.capture_failure(record, "timeout again")
        # Now retry_count == 2 == max_retries
        should, strategy = handler.should_retry("msg-retry-001")
        assert should is False
        assert strategy is RetryStrategy.ESCALATE

    def test_should_retry_true_below_max_retries(self, tmp_path):
        """should_retry returns True when below max_retries."""
        handler = _make_handler(tmp_path, max_retries=3)
        record = _make_record(message_id="msg-retry-002")
        handler.capture_failure(record, "timeout")
        should, _ = handler.should_retry("msg-retry-002")
        assert should is True

    def test_should_retry_returns_correct_strategy_for_backoff_category(self, tmp_path):
        """should_retry returns BACKOFF strategy for GIT_LOCK failures."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-retry-003")
        handler.capture_failure(record, "index.lock exists")
        should, strategy = handler.should_retry("msg-retry-003")
        assert should is True
        assert strategy is RetryStrategy.BACKOFF

    def test_should_retry_returns_rotate_agent_for_unavailable(self, tmp_path):
        """should_retry returns ROTATE_AGENT for AGENT_UNAVAILABLE."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-retry-004")
        handler.capture_failure(record, "no agent found in session")
        should, strategy = handler.should_retry("msg-retry-004")
        assert should is True
        assert strategy is RetryStrategy.ROTATE_AGENT

    def test_should_retry_escalate_for_permission_denied(self, tmp_path):
        """should_retry returns ESCALATE for PERMISSION_DENIED even on first failure."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-retry-005")
        handler.capture_failure(record, "permission denied")
        should, strategy = handler.should_retry("msg-retry-005")
        assert should is True
        assert strategy is RetryStrategy.ESCALATE

    def test_should_retry_returns_immediate_when_no_failures_in_context(self, tmp_path):
        """Context with zero failures falls back to IMMEDIATE strategy."""
        handler = _make_handler(tmp_path)
        ctx = RetryContext(message_id="msg-retry-006", retry_count=0)
        handler.save_context(ctx)
        should, strategy = handler.should_retry("msg-retry-006")
        assert should is True
        assert strategy is RetryStrategy.IMMEDIATE


# ---------------------------------------------------------------------------
# AdaptiveRetryHandler - prepare_retry_message
# ---------------------------------------------------------------------------


class TestAdaptiveRetryHandlerPrepareRetryMessage:
    """Tests for prepare_retry_message()."""

    def test_returns_original_message_when_no_context(self, tmp_path):
        """Returns original_message when no prior context exists."""
        handler = _make_handler(tmp_path)
        record = _make_record(payload_summary="do work")
        result = handler.prepare_retry_message(record, original_message="do work")
        assert result == "do work"

    def test_returns_payload_summary_when_no_context_and_no_original(self, tmp_path):
        """Returns payload_summary when no context and no original_message given."""
        handler = _make_handler(tmp_path)
        record = _make_record(payload_summary="my payload")
        result = handler.prepare_retry_message(record)
        assert result == "my payload"

    def test_enhanced_message_includes_retry_header(self, tmp_path):
        """Enhanced message includes [RETRY ATTEMPT X/Y] header."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-prep-001")
        handler.capture_failure(record, "timeout")
        result = handler.prepare_retry_message(record)
        assert "[RETRY ATTEMPT" in result

    def test_enhanced_message_includes_failure_category(self, tmp_path):
        """Enhanced message includes the failure category name."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-prep-002")
        handler.capture_failure(record, "index.lock exists")
        result = handler.prepare_retry_message(record)
        assert "git_lock" in result

    def test_enhanced_message_includes_error_snippet(self, tmp_path):
        """Enhanced message includes (truncated) error message."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-prep-003")
        handler.capture_failure(record, "timeout on agent prya")
        result = handler.prepare_retry_message(record)
        assert "timeout on agent prya" in result

    def test_enhanced_message_truncates_long_error(self, tmp_path):
        """Error messages longer than 200 characters are truncated."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-prep-004")
        long_error = "timeout: " + "x" * 300
        handler.capture_failure(record, long_error)
        result = handler.prepare_retry_message(record)
        assert "..." in result

    def test_enhanced_message_includes_unblock_instructions(self, tmp_path):
        """Enhanced message has UNBLOCK INSTRUCTIONS section."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-prep-005")
        handler.capture_failure(record, "timeout")
        result = handler.prepare_retry_message(record)
        assert "UNBLOCK INSTRUCTIONS" in result

    def test_enhanced_message_includes_original_task(self, tmp_path):
        """Enhanced message includes ORIGINAL TASK section."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-prep-006", payload_summary="run test suite")
        handler.capture_failure(record, "timeout")
        result = handler.prepare_retry_message(record)
        assert "ORIGINAL TASK" in result
        assert "run test suite" in result

    def test_enhanced_message_includes_failure_history_after_multiple_failures(
        self, tmp_path
    ):
        """FAILURE HISTORY section appears after 2+ failures."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-prep-007")
        handler.capture_failure(record, "timeout")
        handler.capture_failure(record, "timeout again")
        result = handler.prepare_retry_message(record)
        assert "FAILURE HISTORY" in result

    def test_enhanced_message_no_history_section_for_single_failure(self, tmp_path):
        """FAILURE HISTORY section does not appear after a single failure."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-prep-008")
        handler.capture_failure(record, "timeout")
        result = handler.prepare_retry_message(record)
        assert "FAILURE HISTORY" not in result

    def test_original_message_overrides_payload_summary(self, tmp_path):
        """original_message parameter takes precedence over payload_summary."""
        handler = _make_handler(tmp_path)
        record = _make_record(
            message_id="msg-prep-009", payload_summary="from payload"
        )
        handler.capture_failure(record, "timeout")
        result = handler.prepare_retry_message(record, original_message="from parameter")
        assert "from parameter" in result
        assert "from payload" not in result


# ---------------------------------------------------------------------------
# AdaptiveRetryHandler - get_retry_delay
# ---------------------------------------------------------------------------


class TestAdaptiveRetryHandlerGetRetryDelay:
    """Tests for get_retry_delay()."""

    def test_delay_zero_for_unknown_message(self, tmp_path):
        """No context means no delay needed."""
        handler = _make_handler(tmp_path)
        assert handler.get_retry_delay("no-context-id") == 0.0

    def test_exponential_backoff_growth(self, tmp_path):
        """Delay grows exponentially with retry_count."""
        handler = _make_handler(tmp_path, max_retries=10)
        record = _make_record(message_id="msg-delay-001")
        # 1 failure → base_delay = 2^0 = 1
        handler.capture_failure(record, "some unknown failure")
        d1 = handler.get_retry_delay("msg-delay-001")
        # 2 failures → base_delay = 2^1 = 2
        handler.capture_failure(record, "some unknown failure again")
        d2 = handler.get_retry_delay("msg-delay-001")
        assert d2 >= d1

    def test_git_lock_delay_capped_at_five_seconds(self, tmp_path):
        """GIT_LOCK delay is capped at 5s regardless of retry count."""
        handler = _make_handler(tmp_path, max_retries=20)
        record = _make_record(message_id="msg-delay-002")
        # Drive retry_count high enough so uncapped base_delay would exceed 5
        for _ in range(8):
            handler.capture_failure(record, "index.lock exists")
        delay = handler.get_retry_delay("msg-delay-002")
        assert delay <= 5.0

    def test_rate_limited_delay_at_least_thirty_seconds(self, tmp_path):
        """RATE_LIMITED delay is at least 30s."""
        handler = _make_handler(tmp_path, max_retries=10)
        record = _make_record(message_id="msg-delay-003")
        handler.capture_failure(record, "rate limit exceeded")
        delay = handler.get_retry_delay("msg-delay-003")
        assert delay >= 30.0

    def test_agent_not_ready_delay_capped_at_thirty_seconds(self, tmp_path):
        """AGENT_NOT_READY delay is capped at 30s."""
        handler = _make_handler(tmp_path, max_retries=20)
        record = _make_record(message_id="msg-delay-004")
        for _ in range(10):
            handler.capture_failure(record, "agent is busy")
        delay = handler.get_retry_delay("msg-delay-004")
        assert delay <= 30.0

    def test_base_delay_capped_at_sixty_seconds(self, tmp_path):
        """Base exponential delay is capped at 60s."""
        handler = _make_handler(tmp_path, max_retries=100)
        record = _make_record(message_id="msg-delay-005")
        # 7 failures → base_delay = min(2^6, 60) = 60; ensure cap
        for _ in range(8):
            handler.capture_failure(record, "unknown random error")
        delay = handler.get_retry_delay("msg-delay-005")
        # Unknown category uses raw base_delay (no special multiplier)
        assert delay <= 60.0

    def test_returns_float(self, tmp_path):
        """get_retry_delay always returns a float."""
        handler = _make_handler(tmp_path)
        record = _make_record(message_id="msg-delay-006")
        handler.capture_failure(record, "timeout")
        delay = handler.get_retry_delay("msg-delay-006")
        assert isinstance(delay, float)


# ---------------------------------------------------------------------------
# AdaptiveRetryHandler - get_stats
# ---------------------------------------------------------------------------


class TestAdaptiveRetryHandlerGetStats:
    """Tests for get_stats()."""

    def test_stats_empty_context_dir(self, tmp_path):
        """Stats on empty directory returns zeroed structure."""
        handler = _make_handler(tmp_path)
        stats = handler.get_stats()
        assert stats["total_tracked"] == 0
        assert stats["by_category"] == {}

    def test_stats_counts_tracked_contexts(self, tmp_path):
        """total_tracked reflects number of context files."""
        handler = _make_handler(tmp_path)
        for i in range(3):
            record = _make_record(message_id=f"msg-stats-{i:03d}")
            handler.capture_failure(record, "timeout")
        stats = handler.get_stats()
        assert stats["total_tracked"] == 3

    def test_stats_by_category(self, tmp_path):
        """by_category counts failures per category."""
        handler = _make_handler(tmp_path)
        r1 = _make_record(message_id="msg-stats-cat-001")
        r2 = _make_record(message_id="msg-stats-cat-002")
        handler.capture_failure(r1, "timeout")
        handler.capture_failure(r1, "timeout again")
        handler.capture_failure(r2, "index.lock exists")
        stats = handler.get_stats()
        assert stats["by_category"].get("timeout", 0) == 2
        assert stats["by_category"].get("git_lock", 0) == 1

    def test_stats_includes_max_retries(self, tmp_path):
        """Stats includes the configured max_retries value."""
        handler = _make_handler(tmp_path, max_retries=7)
        stats = handler.get_stats()
        assert stats["max_retries"] == 7

    def test_stats_skips_corrupt_files(self, tmp_path):
        """get_stats tolerates corrupt JSON files in the context directory."""
        handler = _make_handler(tmp_path)
        # Write a valid context
        record = _make_record(message_id="msg-stats-valid")
        handler.capture_failure(record, "timeout")
        # Write a corrupt file
        context_dir = tmp_path / "retry_context"
        (context_dir / "bad-file.json").write_text("not json", encoding="utf-8")
        # Should not raise
        stats = handler.get_stats()
        assert stats["total_tracked"] == 1  # Only the valid context counted

    def test_stats_when_context_dir_does_not_exist(self, tmp_path):
        """get_stats returns zeroed structure when context_dir does not exist."""
        nonexistent = tmp_path / "nonexistent_dir"
        # Do NOT create the directory — bypass mkdir in __init__ by accessing _context_dir directly
        handler = _make_handler(tmp_path)
        handler._context_dir = nonexistent  # Point to nonexistent directory
        stats = handler.get_stats()
        assert stats["total_tracked"] == 0
        assert stats["by_category"] == {}


# ---------------------------------------------------------------------------
# Singleton: get_adaptive_retry_handler / reset_adaptive_retry_handler
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for the singleton factory and reset functions."""

    def setup_method(self):
        """Reset singleton before each test for isolation."""
        reset_adaptive_retry_handler()

    def teardown_method(self):
        """Ensure singleton is reset after each test."""
        reset_adaptive_retry_handler()

    def test_get_returns_handler_instance(self):
        """get_adaptive_retry_handler returns an AdaptiveRetryHandler."""
        handler = get_adaptive_retry_handler()
        assert isinstance(handler, AdaptiveRetryHandler)

    def test_get_returns_same_instance_on_repeat_calls(self):
        """get_adaptive_retry_handler is a true singleton."""
        h1 = get_adaptive_retry_handler()
        h2 = get_adaptive_retry_handler()
        assert h1 is h2

    def test_reset_allows_new_instance(self):
        """reset_adaptive_retry_handler clears the singleton."""
        h1 = get_adaptive_retry_handler()
        reset_adaptive_retry_handler()
        h2 = get_adaptive_retry_handler()
        assert h1 is not h2

    def test_max_retries_respected_on_first_call(self):
        """max_retries passed to first call is used."""
        handler = get_adaptive_retry_handler(max_retries=7)
        assert handler.max_retries == 7

    def test_subsequent_calls_ignore_max_retries(self):
        """max_retries on subsequent calls does NOT override the singleton."""
        get_adaptive_retry_handler(max_retries=5)
        handler = get_adaptive_retry_handler(max_retries=99)
        assert handler.max_retries == 5  # Not 99


# ---------------------------------------------------------------------------
# AdaptiveRetryHandler - thread safety (basic smoke test)
# ---------------------------------------------------------------------------


class TestAdaptiveRetryHandlerConcurrency:
    """Basic smoke test for thread-safe context saving."""

    def test_concurrent_saves_do_not_raise(self, tmp_path):
        """Multiple captures on the same message ID from different threads do not corrupt."""
        import threading

        handler = _make_handler(tmp_path, max_retries=20)
        record = _make_record(message_id="msg-concurrent-001")
        errors: list[Exception] = []

        def do_capture():
            try:
                handler.capture_failure(record, "timeout")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=do_capture) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Unexpected errors: {errors}"
        # Context should still be loadable
        ctx = handler.load_context("msg-concurrent-001")
        assert ctx is not None
