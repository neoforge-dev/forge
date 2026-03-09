"""Dispatch and Task Input Validation with Ambiguity Guardrails.

This module provides strict validation for dispatch/task/agent inputs to prevent
ambiguous operations. All ambiguous requests fail-fast with structured errors
that include next_action guidance.

Ambiguity Cases Handled:
- Ambiguous or partial agent IDs
- Invalid/unknown task IDs
- Invalid lifecycle transitions
- Missing required dispatch parameters
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from forge_harness.task_queue import TaskStatus


class ValidationErrorCode(Enum):
    """Structured error codes for validation failures."""

    # Agent validation
    AGENT_ID_AMBIGUOUS = "AGENT_ID_AMBIGUOUS"
    AGENT_ID_INVALID = "AGENT_ID_INVALID"
    AGENT_ID_MISSING = "AGENT_ID_MISSING"

    # Task validation
    TASK_ID_INVALID = "TASK_ID_INVALID"
    TASK_ID_MISSING = "TASK_ID_MISSING"

    # Lifecycle validation
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    LIFECYCLE_FORBIDDEN = "LIFECYCLE_FORBIDDEN"

    # Parameter validation
    PARAM_MISSING = "PARAM_MISSING"
    PARAM_INVALID = "PARAM_INVALID"

    # Message validation
    MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
    MESSAGE_EMPTY = "MESSAGE_EMPTY"


@dataclass
class ValidationError:
    """Structured validation error with next_action guidance."""

    code: ValidationErrorCode
    message: str
    field: str | None = None
    provided_value: Any = None
    next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-friendly dict."""
        result = {
            "code": self.code.value,
            "message": self.message,
        }
        if self.field:
            result["field"] = self.field
        if self.provided_value is not None:
            result["provided_value"] = str(self.provided_value)[:100]  # Truncate long values
        if self.next_action:
            result["next_action"] = self.next_action
        return result

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"


# =============================================================================
# Agent ID Validation
# =============================================================================

# Valid agent ID patterns
VALID_AGENT_PREFIXES = ("forge:", "forge-")
VALID_AGENT_NAMES = frozenset(
    {
        # Core agents
        "nova",
        "gemini",
        "claude",
        "codex",
        "kimi",
        "kimi-a",
        "kimi-b",
        "minimax",
        "open-glm",
        "glm",
        "kilo-glm",
        "kilo-max",
        "open-max",
        # Domain-specific
        "opencode",
        "pi",
        "amp",
        "cursor",
        "cursor-a",
        "cursor-b",
        # Legacy
        "orchestrator",
        "tech",
        "backend",
        "frontend",
        "content",
        "research",
        # Node-specific patterns
        "sati",
        "vega",
        "gaea",
    }
)

# Partial name patterns that could match multiple agents
PARTIAL_NAME_PATTERNS = {
    "kimi": ["kimi-a", "kimi-b"],
    "cursor": ["cursor-a", "cursor-b"],
    "open": ["open-glm", "open-max", "opencode"],
    "glm": ["glm", "kilo-glm"],
    "max": ["open-max", "kilo-max"],
}


def validate_agent_id(agent_id: str | None) -> ValidationError | None:
    """Validate agent ID for ambiguity and validity.

    Args:
        agent_id: The agent identifier to validate

    Returns:
        ValidationError if invalid/ambiguous, None if valid
    """
    # Check for missing agent ID
    if agent_id is None:
        return ValidationError(
            code=ValidationErrorCode.AGENT_ID_MISSING,
            message="Agent ID is required",
            field="agent_id",
            next_action="Provide a valid agent ID (e.g., 'forge:gemini', 'forge:kimi-a')",
        )

    # Normalize
    agent_id = str(agent_id).strip()

    # Check for empty after stripping
    if not agent_id:
        return ValidationError(
            code=ValidationErrorCode.AGENT_ID_MISSING,
            message="Agent ID cannot be empty",
            field="agent_id",
            next_action="Provide a valid agent ID (e.g., 'forge:gemini', 'forge:kimi-a')",
        )

    # Extract the agent name (after colon, or full ID if no colon)
    if ":" in agent_id:
        prefix, name = agent_id.split(":", 1)
    elif agent_id.startswith("forge-"):
        name = agent_id.replace("forge-", "")
    else:
        name = agent_id

    # Check for ambiguous partial names
    if name.lower() in PARTIAL_NAME_PATTERNS:
        matches = PARTIAL_NAME_PATTERNS[name.lower()]
        return ValidationError(
            code=ValidationErrorCode.AGENT_ID_AMBIGUOUS,
            message=f"Agent ID '{agent_id}' is ambiguous - could match: {', '.join(matches)}",
            field="agent_id",
            provided_value=agent_id,
            next_action=f"Use a fully qualified agent ID: {', '.join(f'forge:{m}' for m in matches)}",
        )

    # Check for valid agent names (exact match)
    if name.lower() in VALID_AGENT_NAMES:
        return None  # Valid

    # Check for invalid characters
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return ValidationError(
            code=ValidationErrorCode.AGENT_ID_INVALID,
            message=f"Agent ID '{agent_id}' contains invalid characters",
            field="agent_id",
            provided_value=agent_id,
            next_action="Use only alphanumeric characters, hyphens, and underscores",
        )

    # Unknown but syntactically valid agent
    # We allow this as new agents may be added - but warn
    return None  # Allow unknown but valid-format IDs


# =============================================================================
# Task ID Validation
# =============================================================================

# Valid task ID pattern (UUID-like or short ID)
TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{4,40}$")


def validate_task_id(task_id: str | None) -> ValidationError | None:
    """Validate task ID for validity.

    Args:
        task_id: The task identifier to validate

    Returns:
        ValidationError if invalid, None if valid
    """
    # Check for missing task ID
    if task_id is None:
        return ValidationError(
            code=ValidationErrorCode.TASK_ID_MISSING,
            message="Task ID is required",
            field="task_id",
            next_action="Provide a valid task ID (e.g., 'abc123', 'task-001')",
        )

    # Normalize
    task_id = str(task_id).strip()

    # Check for empty after stripping
    if not task_id:
        return ValidationError(
            code=ValidationErrorCode.TASK_ID_MISSING,
            message="Task ID cannot be empty",
            field="task_id",
            next_action="Provide a valid task ID (e.g., 'abc123', 'task-001')",
        )

    # Check format
    if not TASK_ID_PATTERN.match(task_id):
        return ValidationError(
            code=ValidationErrorCode.TASK_ID_INVALID,
            message=f"Task ID '{task_id}' has invalid format",
            field="task_id",
            provided_value=task_id,
            next_action="Task ID must be 4-40 alphanumeric characters, hyphens, or underscores",
        )

    return None


# =============================================================================
# Task Lifecycle Validation
# =============================================================================


# TaskStatus is imported from forge_harness.task_queue (canonical definition)

# Valid status transitions
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PENDING},
    TaskStatus.COMPLETED: set(),  # Terminal state
    TaskStatus.FAILED: {TaskStatus.PENDING, TaskStatus.ASSIGNED},  # Can retry
    TaskStatus.CANCELLED: {TaskStatus.PENDING},  # Can requeue
}


def validate_status_transition(
    from_status: str | TaskStatus | None,
    to_status: str | TaskStatus | None,
) -> ValidationError | None:
    """Validate a task status transition.

    Args:
        from_status: Current task status
        to_status: Desired new status

    Returns:
        ValidationError if transition is invalid, None if valid
    """
    # Check for None values
    if from_status is None:
        return ValidationError(
            code=ValidationErrorCode.INVALID_STATUS_TRANSITION,
            message="Current status is required",
            field="status",
            next_action=f"Valid statuses: {', '.join(s.value for s in TaskStatus)}",
        )

    if to_status is None:
        return ValidationError(
            code=ValidationErrorCode.INVALID_STATUS_TRANSITION,
            message="Target status is required",
            field="status",
            next_action=f"Valid statuses: {', '.join(s.value for s in TaskStatus)}",
        )

    # Normalize statuses
    from_enum: TaskStatus | None = None
    to_enum: TaskStatus | None = None

    if isinstance(from_status, TaskStatus):
        from_enum = from_status
    elif isinstance(from_status, str):
        try:
            from_enum = TaskStatus(from_status)
        except ValueError:
            return ValidationError(
                code=ValidationErrorCode.INVALID_STATUS_TRANSITION,
                message=f"Invalid current status: '{from_status}'",
                field="status",
                provided_value=from_status,
                next_action=f"Valid statuses: {', '.join(s.value for s in TaskStatus)}",
            )

    if isinstance(to_status, TaskStatus):
        to_enum = to_status
    elif isinstance(to_status, str):
        try:
            to_enum = TaskStatus(to_status)
        except ValueError:
            return ValidationError(
                code=ValidationErrorCode.INVALID_STATUS_TRANSITION,
                message=f"Invalid target status: '{to_status}'",
                field="status",
                provided_value=to_status,
                next_action=f"Valid statuses: {', '.join(s.value for s in TaskStatus)}",
            )

    # Check if transition is valid
    if from_enum is None or to_enum is None:
        return ValidationError(
            code=ValidationErrorCode.INVALID_STATUS_TRANSITION,
            message="Invalid status value",
            field="status",
            next_action=f"Valid statuses: {', '.join(s.value for s in TaskStatus)}",
        )

    valid_targets = VALID_TRANSITIONS.get(from_enum, set())
    if to_enum not in valid_targets:
        if not valid_targets:
            return ValidationError(
                code=ValidationErrorCode.LIFECYCLE_FORBIDDEN,
                message=f"Cannot transition from '{from_enum.value}' - it is a terminal state",
                field="status",
                provided_value=from_enum.value,
                next_action="Task must be requeued or a new task created",
            )
        return ValidationError(
            code=ValidationErrorCode.INVALID_STATUS_TRANSITION,
            message=f"Invalid transition from '{from_enum.value}' to '{to_enum.value}'",
            field="status",
            provided_value={"from": from_enum.value, "to": to_enum.value},
            next_action=f"Valid transitions from '{from_enum.value}': {', '.join(s.value for s in valid_targets)}",
        )

    return None


# =============================================================================
# Dispatch Parameter Validation
# =============================================================================

# Maximum message length
MAX_MESSAGE_LENGTH = 5000
# Recommended message length (for tmux compatibility)
RECOMMENDED_MESSAGE_LENGTH = 80


def validate_dispatch_params(
    agent_id: str | None,
    message: str | None,
    *,
    max_message_length: int = MAX_MESSAGE_LENGTH,
) -> list[ValidationError]:
    """Validate dispatch parameters for ambiguity and completeness.

    Args:
        agent_id: Target agent ID
        message: Dispatch message
        max_message_length: Maximum allowed message length

    Returns:
        List of ValidationErrors (empty if all valid)
    """
    errors: list[ValidationError] = []

    # Validate agent ID
    agent_error = validate_agent_id(agent_id)
    if agent_error:
        errors.append(agent_error)

    # Validate message
    if message is None:
        errors.append(
            ValidationError(
                code=ValidationErrorCode.PARAM_MISSING,
                message="Message is required",
                field="message",
                next_action="Provide a non-empty message for the agent",
            )
        )
    else:
        message = str(message).strip()
        if not message:
            errors.append(
                ValidationError(
                    code=ValidationErrorCode.MESSAGE_EMPTY,
                    message="Message cannot be empty",
                    field="message",
                    next_action="Provide a non-empty message for the agent",
                )
            )
        elif len(message) > max_message_length:
            errors.append(
                ValidationError(
                    code=ValidationErrorCode.MESSAGE_TOO_LONG,
                    message=f"Message exceeds maximum length of {max_message_length} characters",
                    field="message",
                    provided_value=f"{len(message)} chars",
                    next_action=f"Truncate message to {max_message_length} chars or less. For tmux compatibility, keep under {RECOMMENDED_MESSAGE_LENGTH} chars.",
                )
            )

    return errors


# =============================================================================
# Task Parameter Validation
# =============================================================================

# Required fields for task creation
TASK_REQUIRED_FIELDS = {"subject"}
# Optional fields with validation
TASK_OPTIONAL_FIELDS = {
    "description": str,
    "priority": str,
    "required_role": str,
    "domain": str,
    "project": str,
}


def validate_task_params(
    params: dict[str, Any],
    *,
    require_subject: bool = True,
) -> list[ValidationError]:
    """Validate task creation/update parameters.

    Args:
        params: Task parameters to validate
        require_subject: Whether subject/title is required

    Returns:
        List of ValidationErrors (empty if all valid)
    """
    errors: list[ValidationError] = []

    # Check required fields
    if require_subject and "subject" not in params:
        errors.append(
            ValidationError(
                code=ValidationErrorCode.PARAM_MISSING,
                message="Task subject is required",
                field="subject",
                next_action="Provide a subject/title for the task",
            )
        )

    # Validate subject length
    subject = params.get("subject")
    if subject:
        subject = str(subject).strip()
        if not subject:
            errors.append(
                ValidationError(
                    code=ValidationErrorCode.PARAM_INVALID,
                    message="Task subject cannot be empty",
                    field="subject",
                    next_action="Provide a non-empty subject",
                )
            )
        elif len(subject) > 200:
            errors.append(
                ValidationError(
                    code=ValidationErrorCode.PARAM_INVALID,
                    message="Task subject exceeds maximum length of 200 characters",
                    field="subject",
                    provided_value=f"{len(subject)} chars",
                    next_action="Shorten subject to 200 chars or less",
                )
            )

    # Validate priority if provided
    priority = params.get("priority")
    if priority:
        valid_priorities = {"critical", "high", "medium", "low"}
        if priority.lower() not in valid_priorities:
            errors.append(
                ValidationError(
                    code=ValidationErrorCode.PARAM_INVALID,
                    message=f"Invalid priority: '{priority}'",
                    field="priority",
                    provided_value=priority,
                    next_action=f"Valid priorities: {', '.join(sorted(valid_priorities))}",
                )
            )

    # Validate status if provided
    status = params.get("status")
    if status:
        try:
            TaskStatus(status)
        except ValueError:
            errors.append(
                ValidationError(
                    code=ValidationErrorCode.PARAM_INVALID,
                    message=f"Invalid status: '{status}'",
                    field="status",
                    provided_value=status,
                    next_action=f"Valid statuses: {', '.join(s.value for s in TaskStatus)}",
                )
            )

    return errors


# =============================================================================
# Validation Result
# =============================================================================


@dataclass
class ValidationResult:
    """Result of validation operation."""

    valid: bool
    errors: list[ValidationError]

    @classmethod
    def from_errors(cls, errors: list[ValidationError]) -> ValidationResult:
        """Create result from error list."""
        return cls(valid=len(errors) == 0, errors=errors)

    def to_dict(self) -> dict[str, Any]:
        """Convert to API-friendly dict."""
        if self.valid:
            return {"valid": True, "errors": []}
        return {
            "valid": False,
            "errors": [e.to_dict() for e in self.errors],
        }


def validate_dispatch(agent_id: str | None, message: str | None) -> ValidationResult:
    """Validate dispatch request.

    Args:
        agent_id: Target agent ID
        message: Dispatch message

    Returns:
        ValidationResult with errors if any
    """
    errors = validate_dispatch_params(agent_id, message)
    return ValidationResult.from_errors(errors)


def validate_task(task_id: str | None, params: dict[str, Any]) -> ValidationResult:
    """Validate task request.

    Args:
        task_id: Task ID
        params: Task parameters

    Returns:
        ValidationResult with errors if any
    """
    errors: list[ValidationError] = []

    # Validate task ID
    task_id_error = validate_task_id(task_id)
    if task_id_error:
        errors.append(task_id_error)

    # Validate params
    param_errors = validate_task_params(params)
    errors.extend(param_errors)

    return ValidationResult.from_errors(errors)
