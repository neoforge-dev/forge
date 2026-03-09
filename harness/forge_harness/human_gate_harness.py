"""
Human Gate Harness for FORGE Workflows
======================================

Integration layer between OrchestrationHarness and ApprovalQueueHarness.

Provides a unified interface for:
- Creating approval requests with notifications
- Polling for approval status
- Awaiting approval with timeout
- Connecting pipeline checkpoints to approval requests

Usage:
    from forge_harness.human_gate_harness import (
        HumanGateHarness,
        ApprovalResult,
        GateStatus,
        create_human_gate_harness,
    )

    # Create harness
    harness = create_human_gate_harness(
        approval_queue=approval_queue,
        notification_harness=notification_harness,
    )

    # Create approval and wait
    result = await harness.create_approval(
        approval_type=ApprovalType.CONTENT,
        domain="codeswiftr-com",
        title="Review: Python Guide",
        description="Content needs review",
        checkpoint_path=checkpoint_path,
    )

    # Wait for human response
    status = await harness.await_approval(
        result.request_id,
        timeout_seconds=3600,
    )

    if status.gate_status == GateStatus.APPROVED:
        # Continue pipeline
        pass
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .approval_queue import (
    ApprovalPriority,
    ApprovalQueueHarness,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from .logging_config import get_logger
from .notification_harness import (
    NotificationChannel,
    NotificationConfig,
    NotificationHarness,
    NotificationUrgency,
)
from .session_state import HumanDecision, SessionState, SessionStateManager

logger = get_logger(__name__)


# =============================================================================
# Enums
# =============================================================================


class GateStatus(Enum):
    """Status of a human gate."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class ApprovalResult:
    """Result of creating or checking an approval request.

    Attributes:
        request_id: The approval request ID
        gate_status: Current status of the gate
        notification_ids: IDs of notifications sent
        checkpoint_path: Path to pipeline checkpoint (if any)
        resolved_by: Who approved/rejected (if resolved)
        rejection_reason: Reason for rejection (if rejected)
        created_at: When the request was created
        resolved_at: When the request was resolved
    """

    request_id: str
    gate_status: GateStatus
    notification_ids: list[str] = field(default_factory=list)
    checkpoint_path: str | None = None
    resolved_by: str | None = None
    rejection_reason: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None


# =============================================================================
# Priority Mapping
# =============================================================================

# Map ApprovalPriority to NotificationUrgency
PRIORITY_TO_URGENCY = {
    ApprovalPriority.LOW: NotificationUrgency.LOW,
    ApprovalPriority.NORMAL: NotificationUrgency.NORMAL,
    ApprovalPriority.HIGH: NotificationUrgency.HIGH,
    ApprovalPriority.CRITICAL: NotificationUrgency.CRITICAL,
}

# Map ApprovalStatus to GateStatus
STATUS_TO_GATE = {
    ApprovalStatus.PENDING: GateStatus.PENDING,
    ApprovalStatus.APPROVED: GateStatus.APPROVED,
    ApprovalStatus.REJECTED: GateStatus.REJECTED,
    ApprovalStatus.EXPIRED: GateStatus.TIMEOUT,
    ApprovalStatus.CANCELLED: GateStatus.CANCELLED,
}


# =============================================================================
# Human Gate Harness
# =============================================================================


class HumanGateHarness:
    """
    Bridge between OrchestrationHarness and ApprovalQueueHarness.

    Provides a unified interface for human-in-the-loop workflows:
    - Creates approval requests
    - Sends notifications via configured channels
    - Polls for approval status
    - Handles timeout scenarios

    Can be used as a harness in OrchestrationHarness pipelines.
    """

    def __init__(
        self,
        approval_queue: ApprovalQueueHarness,
        notification_harness: NotificationHarness | None = None,
        checkpoint_dir: Path | None = None,
        default_notify_channels: list[NotificationChannel] | None = None,
        session_state_manager: SessionStateManager | None = None,
        session_state: SessionState | None = None,
    ):
        """
        Initialize HumanGateHarness.

        Args:
            approval_queue: ApprovalQueueHarness instance for managing requests
            notification_harness: Optional NotificationHarness for sending alerts
            checkpoint_dir: Directory containing pipeline checkpoints
            default_notify_channels: Default channels if not specified per-request
            session_state_manager: Optional manager for persisting session state
            session_state: Optional active session state for tracking decisions
        """
        self.approval_queue = approval_queue
        self.notification_harness = notification_harness
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.default_notify_channels = default_notify_channels or [NotificationChannel.SLACK]
        self.session_state_manager = session_state_manager
        self._session_state = session_state

    def set_session_state(self, state: SessionState) -> None:
        """Set the active session state for tracking decisions.

        Args:
            state: The session state to use for tracking
        """
        self._session_state = state

    def get_session_state(self) -> SessionState | None:
        """Get the active session state.

        Returns:
            The active session state, or None if not set
        """
        return self._session_state

    async def _record_decision(
        self,
        question: str,
        decision: str,
        decided_by: str | None = None,
        rationale: str | None = None,
        context_key: str | None = None,
    ) -> HumanDecision | None:
        """Record a human decision in session state.

        Args:
            question: The question that was asked
            decision: The decision made
            decided_by: Who made the decision
            rationale: Reason for the decision
            context_key: Optional key for storing in context_data

        Returns:
            HumanDecision if recorded, None if no session state
        """
        if self._session_state is None:
            return None

        hd = self._session_state.add_decision(
            question=question,
            decision=decision,
            decided_by=decided_by,
            rationale=rationale,
            context_key=context_key,
        )

        # Auto-save if manager is configured
        if self.session_state_manager:
            try:
                await self.session_state_manager.save_state(self._session_state)
            except Exception as e:
                logger.warning(f"Failed to save session state: {e}")

        return hd

    async def get_previous_decision(self, question: str) -> str | None:
        """Check if a similar question was already answered.

        Args:
            question: The question to check

        Returns:
            The previous decision if found, None otherwise
        """
        if self._session_state is None:
            return None

        for decision in self._session_state.human_decisions:
            if decision.question == question:
                return decision.decision

        return None

    async def create_approval(
        self,
        approval_type: ApprovalType,
        domain: str,
        title: str,
        description: str,
        metadata: dict[str, Any] | None = None,
        priority: ApprovalPriority = ApprovalPriority.NORMAL,
        checkpoint_path: Path | str | None = None,
        notify_channels: list[NotificationChannel] | None = None,
        tags: list[str] | None = None,
        expiry_hours: float | None = None,
    ) -> ApprovalResult:
        """
        Create an approval request and send notifications.

        Args:
            approval_type: Type of approval (content, deploy, etc.)
            domain: Domain the request relates to
            title: Short title for the request
            description: Detailed description
            metadata: Additional context data
            priority: Priority level
            checkpoint_path: Path to pipeline checkpoint for resume
            notify_channels: Channels to send notifications to
            tags: Optional tags for filtering
            expiry_hours: Hours until expiration

        Returns:
            ApprovalResult with request ID and notification IDs
        """
        # Create the approval request
        request = await self.approval_queue.create_request(
            type=approval_type,
            domain=domain,
            title=title,
            description=description,
            metadata=metadata,
            priority=priority,
            workflow_checkpoint=checkpoint_path,
            tags=tags,
            expiry_hours=expiry_hours,
        )

        notification_ids: list[str] = []

        # Send notifications if harness is configured
        if self.notification_harness:
            channels = notify_channels or self.default_notify_channels
            urgency = PRIORITY_TO_URGENCY.get(priority, NotificationUrgency.NORMAL)

            # Build action URL if checkpoint exists
            action_url = None
            if checkpoint_path:
                action_url = f"forge approvals approve {request.id}"

            notification_config = NotificationConfig(
                channels=channels,
                title=f"[{approval_type.value.upper()}] {title}",
                message=self._build_notification_message(request),
                metadata={
                    "request_id": request.id,
                    "domain": domain,
                    "type": approval_type.value,
                    **(metadata or {}),
                },
                urgency=urgency,
                action_required=True,
                action_url=action_url,
            )

            results = await self.notification_harness.notify(notification_config)
            notification_ids = [r.message_id for r in results if r.success and r.message_id]

            # Update request with notification IDs
            if notification_ids:
                request.notification_ids = notification_ids
                await self.approval_queue.storage.save(request)

        logger.info(
            f"Created human gate approval {request.id}",
            extra={
                "request_id": request.id,
                "type": approval_type.value,
                "domain": domain,
                "notifications_sent": len(notification_ids),
            },
        )

        return ApprovalResult(
            request_id=request.id,
            gate_status=GateStatus.PENDING,
            notification_ids=notification_ids,
            checkpoint_path=str(checkpoint_path) if checkpoint_path else None,
            created_at=request.created_at,
        )

    def _build_notification_message(self, request: ApprovalRequest) -> str:
        """Build notification message from request."""
        lines = [
            f"**Domain:** {request.domain}",
            f"**Type:** {request.type.value}",
            f"**Priority:** {request.priority.value}",
            "",
            request.description,
            "",
            f"**Request ID:** `{request.id}`",
        ]

        if request.workflow_checkpoint:
            lines.append(f"**Checkpoint:** `{request.workflow_checkpoint}`")

        if request.expires_at:
            lines.append(f"**Expires:** {request.expires_at.isoformat()}")

        lines.extend(
            [
                "",
                "To approve: `forge approvals approve " + request.id + "`",
                "To reject: `forge approvals reject " + request.id + ' -r "reason"`',
            ]
        )

        return "\n".join(lines)

    async def check_status(self, request_id: str) -> ApprovalResult:
        """
        Check current status of an approval request.

        Args:
            request_id: ID of the approval request

        Returns:
            ApprovalResult with current status

        Raises:
            ValueError: If request not found
        """
        request = await self.approval_queue.get_request(request_id)
        if request is None:
            raise ValueError(f"Approval request not found: {request_id}")

        gate_status = STATUS_TO_GATE.get(request.status, GateStatus.PENDING)

        return ApprovalResult(
            request_id=request.id,
            gate_status=gate_status,
            notification_ids=request.notification_ids,
            checkpoint_path=request.workflow_checkpoint,
            resolved_by=request.approved_by,
            rejection_reason=request.resolution_reason,
            created_at=request.created_at,
            resolved_at=request.resolved_at,
        )

    async def await_approval(
        self,
        request_id: str,
        timeout_seconds: float = 3600.0,
        poll_interval_seconds: float = 5.0,
        record_decision: bool = True,
    ) -> ApprovalResult:
        """
        Wait for approval with polling.

        Args:
            request_id: ID of the approval request
            timeout_seconds: Maximum time to wait
            poll_interval_seconds: Time between status checks
            record_decision: Whether to record the decision in session state

        Returns:
            ApprovalResult with final status (approved, rejected, or timeout)
        """
        start_time = datetime.now(UTC)
        elapsed = 0.0

        # Get request details for recording decision
        original_request = await self.approval_queue.get_request(request_id)

        while elapsed < timeout_seconds:
            result = await self.check_status(request_id)

            if result.gate_status in (
                GateStatus.APPROVED,
                GateStatus.REJECTED,
                GateStatus.CANCELLED,
            ):
                logger.info(
                    f"Approval {request_id} resolved: {result.gate_status.value}",
                    extra={
                        "request_id": request_id,
                        "status": result.gate_status.value,
                        "elapsed_seconds": elapsed,
                    },
                )

                # Record the decision in session state
                if record_decision and original_request:
                    await self._record_decision(
                        question=original_request.title,
                        decision=result.gate_status.value,
                        decided_by=result.resolved_by,
                        rationale=result.rejection_reason,
                        context_key=f"approval_{request_id}",
                    )

                return result

            await asyncio.sleep(poll_interval_seconds)
            elapsed = (datetime.now(UTC) - start_time).total_seconds()

        # Timeout reached - also record as a decision
        logger.warning(
            f"Approval {request_id} timed out after {timeout_seconds}s",
            extra={"request_id": request_id, "timeout_seconds": timeout_seconds},
        )

        if record_decision and original_request:
            await self._record_decision(
                question=original_request.title,
                decision="timeout",
                rationale=f"No response received within {timeout_seconds}s",
                context_key=f"approval_{request_id}",
            )

        return ApprovalResult(
            request_id=request_id,
            gate_status=GateStatus.TIMEOUT,
            created_at=start_time,
        )

    async def await_feedback(
        self,
        page_ids: list[str] | None = None,
        approval_type: ApprovalType = ApprovalType.CONTENT,
        domain: str = "unknown",
        timeout_hours: float = 24.0,
        **kwargs,
    ) -> ApprovalResult:
        """
        Orchestration-compatible method for awaiting human feedback.

        This method is designed to be called from OrchestrationHarness pipelines.

        Args:
            page_ids: Notion page IDs (stored in metadata)
            approval_type: Type of approval
            domain: Domain context
            timeout_hours: Hours to wait
            **kwargs: Additional metadata

        Returns:
            ApprovalResult with final status
        """
        metadata = {"page_ids": page_ids, **kwargs}
        title = kwargs.get("title", f"Human Review Required - {domain}")
        description = kwargs.get(
            "description",
            f"Please review the content and approve or reject. Page IDs: {page_ids}",
        )

        result = await self.create_approval(
            approval_type=approval_type,
            domain=domain,
            title=title,
            description=description,
            metadata=metadata,
        )

        return await self.await_approval(
            result.request_id,
            timeout_seconds=timeout_hours * 3600,
        )

    async def request_decision(
        self,
        question: str,
        options: list[str],
        domain: str = "unknown",
        timeout_hours: float = 24.0,
        reuse_previous: bool = True,
    ) -> ApprovalResult:
        """
        Request a decision from a human.

        This method is designed for decision points in workflows
        where multiple options are available.

        Args:
            question: The question to ask
            options: Available options
            domain: Domain context
            timeout_hours: Hours to wait
            reuse_previous: If True, reuse previous decision for same question

        Returns:
            ApprovalResult with the decision in resolution_reason
        """
        # Check for previous decision if enabled
        if reuse_previous:
            previous = await self.get_previous_decision(question)
            if previous and previous in options:
                logger.info(
                    f"Reusing previous decision for '{question}': {previous}",
                    extra={"question": question, "previous_decision": previous},
                )
                return ApprovalResult(
                    request_id="cached",
                    gate_status=GateStatus.APPROVED,
                    rejection_reason=previous,  # Store decision here
                    resolved_by="session_cache",
                )

        description = f"{question}\n\nOptions:\n"
        for i, option in enumerate(options, 1):
            description += f"  {i}. {option}\n"
        description += "\nRespond with the option number or text."

        result = await self.create_approval(
            approval_type=ApprovalType.CONFIG,
            domain=domain,
            title=f"Decision Required: {question[:50]}...",
            description=description,
            metadata={"question": question, "options": options},
        )

        return await self.await_approval(
            result.request_id,
            timeout_seconds=timeout_hours * 3600,
        )


# =============================================================================
# Factory Functions
# =============================================================================


def create_human_gate_harness(
    approval_queue: ApprovalQueueHarness | None = None,
    notification_harness: NotificationHarness | None = None,
    storage_dir: Path | str | None = None,
    checkpoint_dir: Path | str | None = None,
    default_notify_channels: list[NotificationChannel] | None = None,
    session_state_manager: SessionStateManager | None = None,
    session_state: SessionState | None = None,
    session_dir: Path | str | None = None,
) -> HumanGateHarness:
    """
    Create a HumanGateHarness with default configuration.

    Args:
        approval_queue: Existing ApprovalQueueHarness (created if None)
        notification_harness: Existing NotificationHarness (optional)
        storage_dir: Directory for approval storage (if creating queue)
        checkpoint_dir: Directory for pipeline checkpoints
        default_notify_channels: Default notification channels
        session_state_manager: Existing SessionStateManager (created if session_dir provided)
        session_state: Active session state for tracking decisions
        session_dir: Directory for session state storage (creates manager if provided)

    Returns:
        Configured HumanGateHarness
    """
    from .approval_queue import create_approval_queue
    from .session_state import create_session_state_manager

    if approval_queue is None:
        approval_queue = create_approval_queue(storage_dir=storage_dir)

    if session_state_manager is None and session_dir is not None:
        session_state_manager = create_session_state_manager(storage_dir=session_dir)

    return HumanGateHarness(
        approval_queue=approval_queue,
        notification_harness=notification_harness,
        checkpoint_dir=Path(checkpoint_dir) if checkpoint_dir else None,
        default_notify_channels=default_notify_channels,
        session_state_manager=session_state_manager,
        session_state=session_state,
    )
