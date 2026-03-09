"""Shared workflow enums and types.

Contains enums used across workflow_harness and orchestration_harness
to avoid duplication.
"""

from enum import Enum


class StepStatus(str, Enum):
    """Status of a workflow/pipeline step.

    Upgraded to (str, Enum) for JSON serialization safety.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
