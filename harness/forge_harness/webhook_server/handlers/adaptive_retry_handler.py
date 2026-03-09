"""Adaptive Retry Handler

Provides intelligent retry logic with failure context capture and
unblock instruction generation. Ensures retries don't just replay
the same prompt but include actionable context.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from forge_harness.logging_config import get_logger
from forge_harness.models.delivery import DeliveryRecord

logger = get_logger(__name__)

# Root of the FORGE repo
_FORGE_ROOT: Path = Path(
    os.environ.get("FORGE_ROOT", Path(__file__).resolve().parents[5])
)

_RETRY_CONTEXT_DIR: Path = _FORGE_ROOT / ".forge" / "relay" / "retry_context"
_FAILURE_PATTERNS_FILE: Path = _FORGE_ROOT / ".forge" / "relay" / "failure_patterns.json"


class FailureCategory(str, Enum):
    """Categories of delivery failures for targeted retry strategies."""

    AGENT_UNAVAILABLE = "agent_unavailable"  # Target agent not online
    AGENT_NOT_READY = "agent_not_ready"  # Agent exists but not accepting messages
    TIMEOUT = "timeout"  # Delivery timed out
    PERMISSION_DENIED = "permission_denied"  # Access/permission issues
    GIT_LOCK = "git_lock"  # Git index.lock contention
    NETWORK_ERROR = "network_error"  # Network connectivity issues
    RATE_LIMITED = "rate_limited"  # Rate limit hit
    UNKNOWN = "unknown"  # Unclassified failure


class RetryStrategy(str, Enum):
    """Strategies for handling retries based on failure category."""

    IMMEDIATE = "immediate"  # Retry immediately
    BACKOFF = "backoff"  # Exponential backoff
    ESCALATE = "escalate"  # Escalate to human
    SKIP = "skip"  # Skip retry
    ROTATE_AGENT = "rotate_agent"  # Try different agent


@dataclass
class FailureContext:
    """Captured context for a failed delivery attempt.

    Attributes:
        message_id: Unique message identifier
        error_message: Raw error message
        category: Classified failure category
        timestamp: When failure occurred
        attempt_number: Which attempt this was
        agent_state: State of target agent if known
        suggested_action: Computed unblock instruction
    """

    message_id: str
    error_message: str
    category: FailureCategory
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempt_number: int = 1
    agent_state: dict[str, Any] | None = None
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "error_message": self.error_message,
            "category": self.category.value,
            "timestamp": self.timestamp.isoformat(),
            "attempt_number": self.attempt_number,
            "agent_state": self.agent_state,
            "suggested_action": self.suggested_action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureContext:
        return cls(
            message_id=data["message_id"],
            error_message=data["error_message"],
            category=FailureCategory(data.get("category", "unknown")),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            attempt_number=data.get("attempt_number", 1),
            agent_state=data.get("agent_state"),
            suggested_action=data.get("suggested_action", ""),
        )


@dataclass
class RetryContext:
    """Complete retry history and context for a message.

    Attributes:
        message_id: Unique message identifier
        task_id: Associated task identifier
        original_payload: Original message payload
        failures: List of failure contexts
        created_at: When retry tracking started
        last_attempt_at: When last attempt was made
        retry_count: Number of retry attempts
        max_retries: Maximum allowed retries
    """

    message_id: str
    task_id: str | None = None
    original_payload: str = ""
    failures: list[FailureContext] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_attempt_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "task_id": self.task_id,
            "original_payload": self.original_payload,
            "failures": [f.to_dict() for f in self.failures],
            "created_at": self.created_at.isoformat(),
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetryContext:
        return cls(
            message_id=data["message_id"],
            task_id=data.get("task_id"),
            original_payload=data.get("original_payload", ""),
            failures=[FailureContext.from_dict(f) for f in data.get("failures", [])],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_attempt_at=datetime.fromisoformat(data["last_attempt_at"]) if data.get("last_attempt_at") else None,
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
        )

    def get_latest_failure(self) -> FailureContext | None:
        """Get the most recent failure context."""
        if not self.failures:
            return None
        return self.failures[-1]

    def get_failure_summary(self) -> str:
        """Generate human-readable failure summary."""
        if not self.failures:
            return "No failures recorded"

        categories = {}
        for f in self.failures:
            cat = f.category.value
            categories[cat] = categories.get(cat, 0) + 1

        parts = [f"{count}x {cat}" for cat, count in categories.items()]
        return f"{len(self.failures)} failures: {', '.join(parts)}"


class FailurePatternClassifier:
    """Classifies error messages into failure categories."""

    # Pattern mappings: category -> list of regex patterns (as strings for simple matching)
    PATTERNS: dict[FailureCategory, list[str]] = {
        FailureCategory.AGENT_UNAVAILABLE: [
            "no agent",
            "agent not found",
            "window not found",
            "session not found",
            "can't find session",
        ],
        FailureCategory.AGENT_NOT_READY: [
            "agent not ready",
            "not accepting messages",
            "busy",
            "occupied",
            "prompt not ready",
            "at prompt",
        ],
        FailureCategory.TIMEOUT: [
            "timeout",
            "timed out",
            "deadline exceeded",
            "took too long",
        ],
        FailureCategory.PERMISSION_DENIED: [
            "permission denied",
            "access denied",
            "unauthorized",
            "forbidden",
        ],
        FailureCategory.GIT_LOCK: [
            "index.lock",
            "git lock",
            "unable to create",
            "file exists",
        ],
        FailureCategory.NETWORK_ERROR: [
            "connection refused",
            "network unreachable",
            "no route",
            "dns error",
            "connection reset",
        ],
        FailureCategory.RATE_LIMITED: [
            "rate limit",
            "too many requests",
            "429",
            "quota exceeded",
        ],
    }

    @classmethod
    def classify(cls, error_message: str) -> FailureCategory:
        """Classify an error message into a failure category."""
        error_lower = error_message.lower()

        for category, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                if pattern in error_lower:
                    return category

        return FailureCategory.UNKNOWN


class UnblockInstructionGenerator:
    """Generates context-aware unblock instructions based on failure category."""

    INSTRUCTIONS: dict[FailureCategory, str] = {
        FailureCategory.AGENT_UNAVAILABLE: (
            "The target agent window does not exist. "
            "Options: (1) Spawn the agent with `forge fleet spawn forge:AGENT`, "
            "(2) Route to a different agent, or (3) Wait for agent to come online."
        ),
        FailureCategory.AGENT_NOT_READY: (
            "The agent exists but is not ready to receive messages. "
            "The agent may be processing a previous task. "
            "Options: (1) Wait and retry with backoff, "
            "(2) Check agent status with `forge fleet heartbeat`, "
            "(3) Use `--restart` flag to restart agent CLI if stuck."
        ),
        FailureCategory.TIMEOUT: (
            "The delivery attempt timed out. "
            "Options: (1) Increase timeout threshold, "
            "(2) Check agent responsiveness, "
            "(3) Verify network connectivity."
        ),
        FailureCategory.PERMISSION_DENIED: (
            "Permission denied when attempting delivery. "
            "Options: (1) Verify agent ownership, "
            "(2) Check tmux session permissions, "
            "(3) Escalate to human operator."
        ),
        FailureCategory.GIT_LOCK: (
            "Git index.lock contention detected. "
            "Options: (1) Wait for concurrent git operations to complete, "
            "(2) Run `rm -f .git/index.lock` if stale lock, "
            "(3) Use `forge git commit` for automatic retry."
        ),
        FailureCategory.NETWORK_ERROR: (
            "Network connectivity issue detected. "
            "Options: (1) Check network status, "
            "(2) Verify target node is reachable, "
            "(3) Retry with exponential backoff."
        ),
        FailureCategory.RATE_LIMITED: (
            "Rate limit or quota exceeded. "
            "Options: (1) Wait for rate limit reset, "
            "(2) Use different agent/model, "
            "(3) Reduce request frequency."
        ),
        FailureCategory.UNKNOWN: (
            "Unknown failure type. "
            "Options: (1) Check agent logs, "
            "(2) Verify task parameters, "
            "(3) Escalate to human operator if persists."
        ),
    }

    STRATEGIES: dict[FailureCategory, RetryStrategy] = {
        FailureCategory.AGENT_UNAVAILABLE: RetryStrategy.ROTATE_AGENT,
        FailureCategory.AGENT_NOT_READY: RetryStrategy.BACKOFF,
        FailureCategory.TIMEOUT: RetryStrategy.BACKOFF,
        FailureCategory.PERMISSION_DENIED: RetryStrategy.ESCALATE,
        FailureCategory.GIT_LOCK: RetryStrategy.BACKOFF,
        FailureCategory.NETWORK_ERROR: RetryStrategy.BACKOFF,
        FailureCategory.RATE_LIMITED: RetryStrategy.BACKOFF,
        FailureCategory.UNKNOWN: RetryStrategy.ESCALATE,
    }

    @classmethod
    def generate(
        cls,
        category: FailureCategory,
        attempt_number: int,
        max_retries: int,
        previous_failures: list[FailureContext],
    ) -> tuple[str, RetryStrategy]:
        """Generate unblock instruction and retry strategy.

        Returns:
            Tuple of (instruction message, retry strategy)
        """
        base_instruction = cls.INSTRUCTIONS.get(category, cls.INSTRUCTIONS[FailureCategory.UNKNOWN])
        strategy = cls.STRATEGIES.get(category, RetryStrategy.ESCALATE)

        # Adjust strategy based on retry count
        if attempt_number >= max_retries:
            strategy = RetryStrategy.ESCALATE

        # Add retry-specific context
        if attempt_number == 1:
            context = "[First failure - will retry automatically]"
        elif attempt_number < max_retries:
            remaining = max_retries - attempt_number
            context = f"[Retry {attempt_number}/{max_retries} - {remaining} attempts remaining]"
        else:
            context = f"[Final attempt {attempt_number}/{max_retries} failed - escalation recommended]"

        # Add pattern-specific advice for repeated failures
        if len(previous_failures) >= 2:
            same_category = sum(1 for f in previous_failures if f.category == category)
            if same_category >= 2:
                context += f"\n[Pattern detected: {same_category}x {category.value} - consider {strategy.value} strategy]"

        full_instruction = f"{context}\n\n{base_instruction}"
        return full_instruction, strategy


class AdaptiveRetryHandler:
    """Handles adaptive retries with failure context and unblock instructions.

    Captures detailed failure context on each delivery attempt and generates
    actionable unblock instructions for subsequent retries. Ensures retries
    are context-aware rather than simple prompt replays.

    Usage:
        handler = get_adaptive_retry_handler()

        # On failure, capture context
        handler.capture_failure(record, error_message, agent_state)

        # On retry, get enhanced message with context
        enhanced_message = handler.prepare_retry_message(record)

        # Check if retry should proceed
        if handler.should_retry(record.message_id):
            await deliver(enhanced_message)
    """

    def __init__(self, max_retries: int = 3, context_dir: Path | None = None):
        """Initialize the adaptive retry handler.

        Args:
            max_retries: Maximum number of retry attempts
            context_dir: Directory to store retry context files
        """
        self.max_retries = max_retries
        self._context_dir = context_dir or _RETRY_CONTEXT_DIR
        self._lock = Lock()
        self._classifier = FailurePatternClassifier()
        self._instruction_generator = UnblockInstructionGenerator()

        # Ensure context directory exists
        self._context_dir.mkdir(parents=True, exist_ok=True)

    def _get_context_path(self, message_id: str) -> Path:
        """Get the file path for a message's retry context."""
        return self._context_dir / f"{message_id}.json"

    def load_context(self, message_id: str) -> RetryContext | None:
        """Load retry context for a message."""
        path = self._get_context_path(message_id)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return RetryContext.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load retry context for {message_id}: {e}")
            return None

    def save_context(self, context: RetryContext) -> None:
        """Save retry context to disk."""
        with self._lock:
            path = self._get_context_path(context.message_id)
            try:
                path.write_text(
                    json.dumps(context.to_dict(), indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                logger.error(f"Failed to save retry context for {context.message_id}: {e}")

    def capture_failure(
        self,
        record: DeliveryRecord,
        error_message: str,
        agent_state: dict[str, Any] | None = None,
    ) -> FailureContext:
        """Capture failure context for a delivery attempt.

        Args:
            record: The delivery record that failed
            error_message: The error message from the failure
            agent_state: Optional state of the target agent

        Returns:
            The captured failure context
        """
        # Load or create retry context
        context = self.load_context(record.message_id)
        if context is None:
            context = RetryContext(
                message_id=record.message_id,
                task_id=record.task_id,
                original_payload=record.payload_summary,
                max_retries=self.max_retries,
            )

        # Classify the failure
        category = self._classifier.classify(error_message)

        # Generate unblock instruction
        instruction, strategy = self._instruction_generator.generate(
            category=category,
            attempt_number=len(context.failures) + 1,
            max_retries=self.max_retries,
            previous_failures=context.failures,
        )

        # Create failure context
        failure = FailureContext(
            message_id=record.message_id,
            error_message=error_message,
            category=category,
            attempt_number=len(context.failures) + 1,
            agent_state=agent_state,
            suggested_action=instruction,
        )

        # Update context
        context.failures.append(failure)
        context.retry_count = len(context.failures)
        context.last_attempt_at = datetime.now(UTC)

        # Save updated context
        self.save_context(context)

        logger.info(
            f"Captured failure context for {record.message_id}",
            extra={
                "message_id": record.message_id,
                "category": category.value,
                "attempt": failure.attempt_number,
                "strategy": strategy.value,
            },
        )

        return failure

    def should_retry(self, message_id: str) -> tuple[bool, RetryStrategy]:
        """Determine if a message should be retried and with what strategy.

        Returns:
            Tuple of (should_retry, strategy)
        """
        context = self.load_context(message_id)
        if context is None:
            # No context means first attempt, allow retry
            return True, RetryStrategy.IMMEDIATE

        if context.retry_count >= self.max_retries:
            logger.warning(
                f"Max retries ({self.max_retries}) exceeded for {message_id}",
                extra={"message_id": message_id, "failures": len(context.failures)},
            )
            return False, RetryStrategy.ESCALATE

        # Get strategy from latest failure
        latest = context.get_latest_failure()
        if latest:
            _, strategy = self._instruction_generator.generate(
                category=latest.category,
                attempt_number=context.retry_count,
                max_retries=self.max_retries,
                previous_failures=context.failures[:-1],
            )
            return True, strategy

        return True, RetryStrategy.IMMEDIATE

    def prepare_retry_message(
        self,
        record: DeliveryRecord,
        original_message: str | None = None,
    ) -> str:
        """Prepare an enhanced retry message with failure context.

        This ensures retries include context about previous failures
        rather than just replaying the original prompt.

        Args:
            record: The delivery record being retried
            original_message: The original message content (optional)

        Returns:
            Enhanced message with failure context and unblock instructions
        """
        context = self.load_context(record.message_id)
        if context is None or not context.failures:
            # No context available, return original or default
            return original_message or record.payload_summary

        latest = context.get_latest_failure()
        if latest is None:
            return original_message or record.payload_summary

        # Build enhanced message with context
        parts = []

        # Header indicating this is a retry with context
        parts.append(f"[RETRY ATTEMPT {latest.attempt_number}/{self.max_retries}]")

        # Failure summary
        parts.append(f"\nPrevious failure: {latest.category.value}")
        if latest.error_message:
            # Truncate long error messages
            error_snippet = latest.error_message[:200]
            if len(latest.error_message) > 200:
                error_snippet += "..."
            parts.append(f"Error: {error_snippet}")

        # Unblock instructions
        parts.append("\n--- UNBLOCK INSTRUCTIONS ---")
        parts.append(latest.suggested_action)

        # Original task
        parts.append("\n--- ORIGINAL TASK ---")
        parts.append(original_message or record.payload_summary)

        # Context from previous failures if any
        if len(context.failures) > 1:
            parts.append("\n--- FAILURE HISTORY ---")
            parts.append(context.get_failure_summary())

        return "\n".join(parts)

    def get_retry_delay(self, message_id: str) -> float:
        """Calculate the recommended delay before next retry.

        Uses exponential backoff based on retry count and failure category.

        Returns:
            Recommended delay in seconds
        """
        context = self.load_context(message_id)
        if context is None:
            return 0.0

        # Base delay increases with retry count
        base_delay = min(2 ** (context.retry_count - 1), 60)  # Cap at 60s

        # Adjust based on failure category
        latest = context.get_latest_failure()
        if latest:
            if latest.category == FailureCategory.GIT_LOCK:
                # Git locks resolve quickly
                return min(base_delay, 5.0)
            elif latest.category == FailureCategory.RATE_LIMITED:
                # Rate limits need longer waits
                return max(base_delay, 30.0)
            elif latest.category == FailureCategory.AGENT_NOT_READY:
                # Agent readiness issues need moderate backoff
                return min(base_delay * 2, 30.0)

        return float(base_delay)

    def clear_context(self, message_id: str) -> None:
        """Clear retry context for a message (call on successful delivery)."""
        path = self._get_context_path(message_id)
        if path.exists():
            try:
                path.unlink()
                logger.debug(f"Cleared retry context for {message_id}")
            except Exception as e:
                logger.warning(f"Failed to clear retry context for {message_id}: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about retry contexts."""
        if not self._context_dir.exists():
            return {"total_tracked": 0, "by_category": {}}

        contexts: list[RetryContext] = []
        for path in self._context_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                contexts.append(RetryContext.from_dict(data))
            except Exception:
                continue

        # Aggregate by category
        by_category: dict[str, int] = {}
        for ctx in contexts:
            for failure in ctx.failures:
                cat = failure.category.value
                by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "total_tracked": len(contexts),
            "by_category": by_category,
            "max_retries": self.max_retries,
        }


# Singleton instance
_retry_handler: AdaptiveRetryHandler | None = None
_handler_lock = Lock()


def get_adaptive_retry_handler(max_retries: int = 3) -> AdaptiveRetryHandler:
    """Get or create the global AdaptiveRetryHandler singleton.

    Args:
        max_retries: Maximum retry attempts (only used on first call)

    Returns:
        The singleton AdaptiveRetryHandler instance
    """
    global _retry_handler
    with _handler_lock:
        if _retry_handler is None:
            _retry_handler = AdaptiveRetryHandler(max_retries=max_retries)
        return _retry_handler


def reset_adaptive_retry_handler() -> None:
    """Reset the singleton - intended for testing only."""
    global _retry_handler
    with _handler_lock:
        _retry_handler = None
