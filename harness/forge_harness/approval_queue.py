"""
Approval Queue Harness for FORGE Human-in-the-Loop Workflows
=============================================================

Manages approval requests for human gates in automated pipelines.

When a workflow hits a human gate, it:
1. Creates an approval request with full context
2. Notifies via configured channels (Slack, email, Notion)
3. Persists the request to storage (local JSON + optional Notion)
4. Pauses workflow execution
5. Resumes on approval/rejection via CLI or webhook

Usage:
    from forge_harness.approval_queue import (
        ApprovalQueueHarness,
        ApprovalRequest,
        ApprovalType,
        create_approval_queue,
    )

    # Create harness
    queue = create_approval_queue(
        storage_dir=Path(".forge/approvals"),
        notion_database_id="abc123",  # Optional
    )

    # Create approval request
    request = await queue.create_request(
        type=ApprovalType.CONTENT,
        domain="codeswiftr-com",
        title="Review: Python Interview Guide",
        description="Tier 1 content requires human review",
        metadata={"quality_score": 91, "word_count": 1500},
        workflow_checkpoint=Path(".workflow_checkpoints/wf_123.json"),
    )

    # List pending approvals
    pending = await queue.list_pending()

    # Approve
    approved = await queue.approve(request.id, approver="@bogdan")

    # Or reject
    rejected = await queue.reject(request.id, approver="@bogdan", reason="Needs more examples")
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from .daily_notes import EventType, get_daily_notes_service
from .logging_config import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_push_manager = None
_decision_router = None
_event_bus = None


def _get_push_manager():
    """Get push notification manager (lazy load)."""
    global _push_manager
    if _push_manager is None:
        try:
            from .push_notifications import get_push_manager

            _push_manager = get_push_manager()
        except Exception as e:
            logger.debug(f"Push notifications not available: {e}")
    return _push_manager


def _get_decision_router():
    """Get decision router (lazy load)."""
    global _decision_router
    if _decision_router is None:
        try:
            from .decision_router import DecisionRouter

            _decision_router = DecisionRouter()
        except Exception as e:
            logger.debug(f"Decision router not available: {e}")
    return _decision_router


def _get_event_bus():
    """Get event bus for SSE (lazy load)."""
    global _event_bus
    if _event_bus is None:
        try:
            from .webhook_server import get_event_bus

            _event_bus = get_event_bus()
        except Exception as e:
            logger.debug(f"Event bus not available: {e}")
    return _event_bus


# =============================================================================
# Enums
# =============================================================================


class ApprovalType(Enum):
    """Types of approval requests."""

    CONTENT = "content"  # Content review (blog posts, docs)
    DEPLOY = "deploy"  # Deployment to staging/production
    FEATURE = "feature"  # Feature implementation PR
    CONFIG = "config"  # Configuration change
    DATA = "data"  # Data migration or modification
    SECURITY = "security"  # Security-sensitive operation
    COMPLIANCE = "compliance"  # Compliance-related (COPPA, HIPAA)


class ApprovalStatus(str, Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalPriority(Enum):
    """Priority levels for approval requests."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class ApprovalRequest:
    """A request for human approval.

    Attributes:
        id: Unique identifier for the request
        type: Type of approval (content, deploy, feature, etc.)
        domain: Domain this request relates to
        title: Short title for the approval request
        description: Detailed description of what needs approval
        metadata: Additional context (quality_score, diff, etc.)
        status: Current status of the request
        priority: Priority level
        tier: Approval tier (WATCH, PHONE, DESKTOP) for device-appropriate UX
        risk_score: Risk score (0.0-1.0) for tier classification
        created_at: When the request was created
        expires_at: When the request expires (if applicable)
        approved_by: Who approved/rejected (if resolved)
        resolved_at: When the request was resolved
        resolution_reason: Reason for rejection (if rejected)
        workflow_checkpoint: Path to workflow checkpoint for resume
        notification_ids: IDs of notifications sent for this request
        tags: Optional tags for filtering
    """

    id: str
    type: ApprovalType
    domain: str
    title: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    priority: ApprovalPriority = ApprovalPriority.NORMAL
    tier: str = "PHONE"  # Default tier: WATCH, PHONE, or DESKTOP
    risk_score: float = 0.5  # Default risk score (0.0-1.0)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    approved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_reason: str | None = None
    workflow_checkpoint: str | None = None
    notification_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "id": self.id,
            "type": self.type.value,
            "domain": self.domain,
            "title": self.title,
            "description": self.description,
            "metadata": self.metadata,
            "status": self.status.value,
            "priority": self.priority.value,
            "tier": self.tier,
            "risk_score": self.risk_score,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "approved_by": self.approved_by,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_reason": self.resolution_reason,
            "workflow_checkpoint": self.workflow_checkpoint,
            "notification_ids": self.notification_ids,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRequest":
        """Create from dict."""
        return cls(
            id=data["id"],
            type=ApprovalType(data["type"]),
            domain=data["domain"],
            title=data["title"],
            description=data["description"],
            metadata=data.get("metadata", {}),
            status=ApprovalStatus(data.get("status", "pending")),
            priority=ApprovalPriority(data.get("priority", "normal")),
            tier=data.get("tier", "PHONE"),
            risk_score=data.get("risk_score", 0.5),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"])
            if data.get("expires_at")
            else None,
            approved_by=data.get("approved_by"),
            resolved_at=datetime.fromisoformat(data["resolved_at"])
            if data.get("resolved_at")
            else None,
            resolution_reason=data.get("resolution_reason"),
            workflow_checkpoint=data.get("workflow_checkpoint"),
            notification_ids=data.get("notification_ids", []),
            tags=data.get("tags", []),
        )

    @property
    def is_pending(self) -> bool:
        """Check if request is still pending."""
        return self.status == ApprovalStatus.PENDING

    @property
    def is_expired(self) -> bool:
        """Check if request has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    @property
    def age_hours(self) -> float:
        """Get age of request in hours."""
        delta = datetime.now(UTC) - self.created_at
        return delta.total_seconds() / 3600


@dataclass
class ApprovalQueueStats:
    """Statistics for the approval queue."""

    total_requests: int
    pending_count: int
    approved_count: int
    rejected_count: int
    expired_count: int
    avg_resolution_hours: float
    by_type: dict[str, int]
    by_domain: dict[str, int]
    oldest_pending_hours: float | None


# =============================================================================
# Tier Classification
# =============================================================================


def auto_classify_tier(
    approval_type: ApprovalType,
    priority: ApprovalPriority,
    metadata: dict[str, Any],
) -> tuple[str, float]:
    """Automatically classify approval tier and calculate risk score.

    Tier Classification:
        WATCH: Low risk, binary decision (lint fixes, test retries, low-risk content)
               - Quick approve/reject on smartwatch
               - Risk < 0.3

        PHONE: Medium risk, needs context (feature complete, content publish)
               - Summary view with key metrics
               - Risk 0.3-0.6

        DESKTOP: High risk, full review (security, deploy, compliance, data changes)
                 - Full diff/context required
                 - Risk > 0.6

    Args:
        approval_type: Type of approval request
        priority: Priority level
        metadata: Additional context for classification

    Returns:
        Tuple of (tier, risk_score)
    """
    # Base risk scores by approval type
    type_risk_scores = {
        ApprovalType.SECURITY: 0.9,  # Always high risk
        ApprovalType.COMPLIANCE: 0.9,  # Always high risk
        ApprovalType.DEPLOY: 0.8,  # Production deploys = high risk
        ApprovalType.DATA: 0.75,  # Data changes = high risk
        ApprovalType.CONFIG: 0.6,  # Config changes = medium risk
        ApprovalType.FEATURE: 0.5,  # Features = medium risk
        ApprovalType.CONTENT: 0.3,  # Content = low-medium risk
    }

    base_risk = type_risk_scores.get(approval_type, 0.5)

    # Adjust risk based on priority
    priority_modifiers = {
        ApprovalPriority.CRITICAL: 0.2,  # Increases risk
        ApprovalPriority.HIGH: 0.1,
        ApprovalPriority.NORMAL: 0.0,
        ApprovalPriority.LOW: -0.1,  # Decreases risk
    }
    risk_score = base_risk + priority_modifiers.get(priority, 0.0)

    # Metadata-based adjustments
    # High-quality content can reduce risk
    if approval_type == ApprovalType.CONTENT:
        quality_score = metadata.get("quality_score", 0)
        if quality_score >= 90:
            risk_score -= 0.1
        elif quality_score < 70:
            risk_score += 0.1

    # Large changes increase risk
    lines_changed = metadata.get("lines_changed", 0)
    if lines_changed > 500:
        risk_score += 0.1
    elif lines_changed > 1000:
        risk_score += 0.2

    # Production environment increases risk
    environment = metadata.get("environment", "")
    if environment in ("production", "prod"):
        risk_score += 0.2

    # Test coverage affects risk for features
    if approval_type == ApprovalType.FEATURE:
        test_coverage = metadata.get("test_coverage", 0)
        if test_coverage < 70:
            risk_score += 0.1
        elif test_coverage >= 90:
            risk_score -= 0.05

    # Clamp risk score to 0.0-1.0
    risk_score = max(0.0, min(1.0, risk_score))

    # Determine tier based on risk score
    if risk_score < 0.3:
        tier = "WATCH"
    elif risk_score < 0.6:
        tier = "PHONE"
    else:
        tier = "DESKTOP"

    # Override: Security and compliance always DESKTOP
    if approval_type in (ApprovalType.SECURITY, ApprovalType.COMPLIANCE):
        tier = "DESKTOP"
        risk_score = max(risk_score, 0.8)

    # Override: Low priority content can be WATCH
    if (
        approval_type == ApprovalType.CONTENT
        and priority == ApprovalPriority.LOW
        and risk_score < 0.3
    ):
        tier = "WATCH"

    return tier, risk_score


# =============================================================================
# Storage Interface
# =============================================================================


class ApprovalStorage:
    """Abstract storage interface for approval requests."""

    async def save(self, request: ApprovalRequest) -> None:
        """Save an approval request."""
        raise NotImplementedError

    async def load(self, request_id: str) -> ApprovalRequest | None:
        """Load an approval request by ID."""
        raise NotImplementedError

    async def list_all(self) -> list[ApprovalRequest]:
        """List all approval requests."""
        raise NotImplementedError

    async def delete(self, request_id: str) -> bool:
        """Delete an approval request."""
        raise NotImplementedError


class JSONFileStorage(ApprovalStorage):
    """JSON file-based storage for approval requests.

    Stores each request as a separate JSON file for easy inspection
    and modification. Also maintains an index file for quick lookups.
    """

    def __init__(self, storage_dir: Path):
        """Initialize JSON file storage.

        Args:
            storage_dir: Directory to store approval request files
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.storage_dir / "_index.json"
        self._index: dict[str, str] = {}  # id -> filename
        self._load_index()

    def _load_index(self) -> None:
        """Load the index file."""
        if self._index_path.exists():
            try:
                with open(self._index_path) as f:
                    self._index = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load index, rebuilding: {e}")
                self._rebuild_index()
        else:
            self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Rebuild index from files."""
        self._index = {}
        for file_path in self.storage_dir.glob("approval_*.json"):
            try:
                with open(file_path) as f:
                    data = json.load(f)
                    self._index[data["id"]] = file_path.name
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to index {file_path}: {e}")
        self._save_index()

    def _save_index(self) -> None:
        """Save the index file."""
        try:
            with open(self._index_path, "w") as f:
                json.dump(self._index, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to save index: {e}")

    def _get_file_path(self, request_id: str) -> Path:
        """Get file path for a request."""
        if request_id in self._index:
            return self.storage_dir / self._index[request_id]
        # Generate new filename using full request_id for uniqueness
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"approval_{timestamp}_{request_id}.json"
        return self.storage_dir / filename

    async def save(self, request: ApprovalRequest) -> None:
        """Save an approval request to JSON file."""
        file_path = self._get_file_path(request.id)
        try:
            with open(file_path, "w") as f:
                json.dump(request.to_dict(), f, indent=2)
            self._index[request.id] = file_path.name
            self._save_index()
            logger.debug(f"Saved approval request {request.id} to {file_path}")
        except OSError as e:
            logger.error(f"Failed to save approval request {request.id}: {e}")
            raise

    async def load(self, request_id: str) -> ApprovalRequest | None:
        """Load an approval request by ID."""
        if request_id not in self._index:
            return None
        file_path = self.storage_dir / self._index[request_id]
        if not file_path.exists():
            del self._index[request_id]
            self._save_index()
            return None
        try:
            with open(file_path) as f:
                data = json.load(f)
            return ApprovalRequest.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to load approval request {request_id}: {e}")
            return None

    async def list_all(self) -> list[ApprovalRequest]:
        """List all approval requests."""
        requests = []
        for request_id in list(self._index.keys()):
            request = await self.load(request_id)
            if request:
                requests.append(request)
        # Sort by created_at descending (newest first)
        requests.sort(key=lambda r: r.created_at, reverse=True)
        return requests

    async def delete(self, request_id: str) -> bool:
        """Delete an approval request."""
        if request_id not in self._index:
            return False
        file_path = self.storage_dir / self._index[request_id]
        try:
            if file_path.exists():
                file_path.unlink()
            del self._index[request_id]
            self._save_index()
            logger.debug(f"Deleted approval request {request_id}")
            return True
        except OSError as e:
            logger.error(f"Failed to delete approval request {request_id}: {e}")
            return False


# =============================================================================
# Approval Queue Harness
# =============================================================================


class ApprovalQueueHarness:
    """
    Manages approval requests for human-in-the-loop workflows.

    Provides:
    - Create/approve/reject approval requests
    - Persistent storage (JSON files)
    - Query by status, domain, type
    - Integration with NotificationHarness (optional)
    - Expiration handling
    """

    def __init__(
        self,
        storage: ApprovalStorage,
        notification_harness: Any | None = None,
        default_expiry_hours: float = 72.0,
    ):
        """
        Initialize ApprovalQueueHarness.

        Args:
            storage: Storage backend for approval requests
            notification_harness: Optional NotificationHarness for sending notifications
            default_expiry_hours: Default expiration time for requests (72h = 3 days)
        """
        self.storage = storage
        self.notification_harness = notification_harness
        self.default_expiry_hours = default_expiry_hours

    def _generate_id(self) -> str:
        """Generate a unique approval request ID."""
        return f"apr_{uuid.uuid4().hex[:12]}"

    async def create_request(
        self,
        type: ApprovalType,
        domain: str,
        title: str,
        description: str,
        metadata: dict[str, Any] | None = None,
        priority: ApprovalPriority = ApprovalPriority.NORMAL,
        expiry_hours: float | None = None,
        workflow_checkpoint: Path | str | None = None,
        tags: list[str] | None = None,
        tier: str | None = None,
        risk_score: float | None = None,
    ) -> ApprovalRequest:
        """
        Create a new approval request.

        Args:
            type: Type of approval (content, deploy, feature, etc.)
            domain: Domain this request relates to
            title: Short title for the request
            description: Detailed description
            metadata: Additional context
            priority: Priority level
            expiry_hours: Hours until expiration (None = use default)
            workflow_checkpoint: Path to checkpoint for workflow resume
            tags: Optional tags for filtering
            tier: Explicit tier override (WATCH, PHONE, DESKTOP)
            risk_score: Explicit risk score override (0.0-1.0)

        Returns:
            Created ApprovalRequest
        """
        request_id = self._generate_id()
        now = datetime.now(UTC)

        # Calculate expiration
        expiry = expiry_hours if expiry_hours is not None else self.default_expiry_hours
        expires_at = now + timedelta(hours=expiry) if expiry > 0 else None

        # Auto-classify tier if not explicitly provided
        meta = metadata or {}
        if tier is None or risk_score is None:
            auto_tier, auto_risk = auto_classify_tier(type, priority, meta)
            tier = tier or auto_tier
            risk_score = risk_score if risk_score is not None else auto_risk

        request = ApprovalRequest(
            id=request_id,
            type=type,
            domain=domain,
            title=title,
            description=description,
            metadata=meta,
            status=ApprovalStatus.PENDING,
            priority=priority,
            tier=tier,
            risk_score=risk_score,
            created_at=now,
            expires_at=expires_at,
            workflow_checkpoint=str(workflow_checkpoint) if workflow_checkpoint else None,
            tags=tags or [],
        )

        await self.storage.save(request)
        logger.info(
            f"Created approval request {request_id}",
            extra={
                "request_id": request_id,
                "type": type.value,
                "domain": domain,
                "priority": priority.value,
            },
        )

        # Send push notification for new approval
        await self._send_push_notification(request)

        # Publish SSE event for real-time UI updates
        await self._publish_sse_event("approval.created", request)

        return request

    async def _publish_sse_event(self, event_type: str, request: "ApprovalRequest") -> None:
        """Publish SSE event for real-time UI updates.

        Args:
            event_type: Type of event (approval.created, approval.updated)
            request: The approval request
        """
        event_bus = _get_event_bus()
        if event_bus is None:
            logger.debug("Event bus not available, skipping SSE event")
            return

        try:
            # Classify decision tier for event data
            decision_router = _get_decision_router()
            tier = "phone"  # Default tier
            risk_score = 0.5  # Default risk score

            if decision_router:
                classification = decision_router.classify(request)
                tier = classification.tier.value
                risk_score = classification.risk_score
                logger.debug(f"Classified {request.id} as tier={tier}, risk={risk_score}")

            # Build event data based on event type
            if event_type == "approval.created":
                event_data = {
                    "request_id": request.id,
                    "type": request.type.value,
                    "title": request.title,
                    "domain": request.domain,
                    "project": request.metadata.get("project", ""),
                    "priority": request.priority.value,
                    "tier": tier,
                    "risk_score": risk_score,
                }
            elif event_type == "approval.updated":
                event_data = {
                    "request_id": request.id,
                    "status": request.status.value,
                    "decided_by": request.approved_by,
                    "decided_at": request.resolved_at.isoformat() if request.resolved_at else None,
                }
            else:
                logger.warning(f"Unknown SSE event type: {event_type}")
                return

            # Publish the event
            await event_bus.publish(event_type, event_data)
            logger.debug(f"Published SSE event {event_type} for approval {request.id}")

        except Exception as e:
            # Don't fail the approval operation if SSE publish fails
            logger.warning(f"Failed to publish SSE event: {e}")

    async def _send_push_notification(self, request: "ApprovalRequest") -> None:
        """Send push notification for a new approval request.

        Uses decision router to classify tier and sends appropriate notification.
        """
        push_manager = _get_push_manager()
        if push_manager is None:
            logger.debug("Push notifications not configured, skipping")
            return

        try:
            # Classify decision tier
            decision_router = _get_decision_router()
            tier = "phone"  # Default tier
            if decision_router:
                classification = decision_router.classify(request)
                tier = classification.tier.value
                logger.debug(
                    f"Classified {request.id} as tier={tier}, reason={classification.reason}"
                )

            # Build notification payload
            from .push_notifications import PushNotificationPayload

            # Create action buttons based on tier
            actions = []
            if tier in ("watch", "phone"):
                actions = [
                    {"action": "approve", "title": "Approve"},
                    {"action": "reject", "title": "Reject"},
                ]

            payload = PushNotificationPayload(
                title=f"[{request.priority.value.upper()}] {request.title}",
                body=request.description[:200] if request.description else "",
                tag=f"approval-{request.id}",
                data={
                    "request_id": request.id,
                    "type": request.type.value,
                    "domain": request.domain,
                    "tier": tier,
                    "priority": request.priority.value,
                    "url": f"/approvals/{request.id}",
                },
                actions=actions,
            )

            # Send to all subscribed devices for this tier
            result = push_manager.send_notification(payload, tier=tier)
            logger.info(
                f"Push notification sent for {request.id}",
                extra={
                    "request_id": request.id,
                    "tier": tier,
                    "sent": result.get("sent", 0),
                    "failed": result.get("failed", 0),
                },
            )
        except Exception as e:
            # Don't fail the approval creation if push fails
            logger.warning(f"Failed to send push notification: {e}")

    async def approve(
        self,
        request_id: str,
        approver: str,
        comment: str | None = None,
    ) -> ApprovalRequest:
        """
        Approve a pending request.

        Args:
            request_id: ID of the request to approve
            approver: Who is approving (username/email)
            comment: Optional comment

        Returns:
            Updated ApprovalRequest

        Raises:
            ValueError: If request not found or not pending
        """
        request = await self.storage.load(request_id)
        if request is None:
            raise ValueError(f"Approval request not found: {request_id}")

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Request {request_id} is not pending (status: {request.status.value})"
            )

        request.status = ApprovalStatus.APPROVED
        request.approved_by = approver
        request.resolved_at = datetime.now(UTC)
        if comment:
            request.resolution_reason = comment

        await self.storage.save(request)
        logger.info(
            f"Approved request {request_id}",
            extra={
                "request_id": request_id,
                "approver": approver,
                "domain": request.domain,
            },
        )

        # Log approval decision to daily notes
        try:
            daily_notes = get_daily_notes_service()
            await daily_notes.log_event(
                event_type=EventType.APPROVAL_DECISION,
                title=f"Approved: {request.title}",
                details={
                    "request_id": request_id,
                    "type": request.type.value,
                    "domain": request.domain,
                    "project": request.metadata.get("project", "N/A"),
                    "approver": approver,
                    "priority": request.priority.value,
                    "decision": "APPROVED",
                    "comment": comment if comment else "No comment provided",
                },
            )
        except Exception as e:
            logger.debug(f"Failed to log approval decision to daily notes: {e}")

        # Publish SSE event for real-time UI updates
        await self._publish_sse_event("approval.updated", request)

        return request

    async def reject(
        self,
        request_id: str,
        approver: str,
        reason: str,
    ) -> ApprovalRequest:
        """
        Reject a pending request.

        Args:
            request_id: ID of the request to reject
            approver: Who is rejecting (username/email)
            reason: Reason for rejection

        Returns:
            Updated ApprovalRequest

        Raises:
            ValueError: If request not found or not pending
        """
        request = await self.storage.load(request_id)
        if request is None:
            raise ValueError(f"Approval request not found: {request_id}")

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Request {request_id} is not pending (status: {request.status.value})"
            )

        request.status = ApprovalStatus.REJECTED
        request.approved_by = approver
        request.resolved_at = datetime.now(UTC)
        request.resolution_reason = reason

        await self.storage.save(request)
        logger.info(
            f"Rejected request {request_id}",
            extra={
                "request_id": request_id,
                "approver": approver,
                "reason": reason,
                "domain": request.domain,
            },
        )

        # Log rejection decision to daily notes
        try:
            daily_notes = get_daily_notes_service()
            await daily_notes.log_event(
                event_type=EventType.APPROVAL_DECISION,
                title=f"Rejected: {request.title}",
                details={
                    "request_id": request_id,
                    "type": request.type.value,
                    "domain": request.domain,
                    "project": request.metadata.get("project", "N/A"),
                    "approver": approver,
                    "priority": request.priority.value,
                    "decision": "REJECTED",
                    "reason": reason,
                },
            )
        except Exception as e:
            logger.debug(f"Failed to log rejection decision to daily notes: {e}")

        # Publish SSE event for real-time UI updates
        await self._publish_sse_event("approval.updated", request)

        return request

    async def cancel(
        self,
        request_id: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        """
        Cancel a pending request.

        Args:
            request_id: ID of the request to cancel
            reason: Optional reason for cancellation

        Returns:
            Updated ApprovalRequest

        Raises:
            ValueError: If request not found or not pending
        """
        request = await self.storage.load(request_id)
        if request is None:
            raise ValueError(f"Approval request not found: {request_id}")

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Request {request_id} is not pending (status: {request.status.value})"
            )

        request.status = ApprovalStatus.CANCELLED
        request.resolved_at = datetime.now(UTC)
        request.resolution_reason = reason

        await self.storage.save(request)
        logger.info(f"Cancelled request {request_id}", extra={"request_id": request_id})

        return request

    async def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Get a specific approval request by ID."""
        return await self.storage.load(request_id)

    async def list_pending(
        self,
        domain: str | None = None,
        type: ApprovalType | None = None,
        priority: ApprovalPriority | None = None,
        tier: str | None = None,
        include_expired: bool = False,
    ) -> list[ApprovalRequest]:
        """
        List pending approval requests.

        Args:
            domain: Filter by domain
            type: Filter by type
            priority: Filter by priority
            tier: Filter by tier (WATCH, PHONE, DESKTOP)
            include_expired: Include expired requests

        Returns:
            List of pending ApprovalRequests (oldest first for queue order)
        """
        all_requests = await self.storage.list_all()
        pending = []

        for request in all_requests:
            # Filter by status
            if request.status != ApprovalStatus.PENDING:
                continue

            # Filter by expiration
            if not include_expired and request.is_expired:
                continue

            # Filter by domain
            if domain and request.domain != domain:
                continue

            # Filter by type
            if type and request.type != type:
                continue

            # Filter by priority
            if priority and request.priority != priority:
                continue

            # Filter by tier
            if tier and request.tier.upper() != tier.upper():
                continue

            pending.append(request)

        # Sort by priority (critical first) then by created_at (oldest first)
        priority_order = {
            ApprovalPriority.CRITICAL: 0,
            ApprovalPriority.HIGH: 1,
            ApprovalPriority.NORMAL: 2,
            ApprovalPriority.LOW: 3,
        }
        pending.sort(key=lambda r: (priority_order[r.priority], r.created_at))

        return pending

    async def list_resolved(
        self,
        domain: str | None = None,
        type: ApprovalType | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[ApprovalRequest]:
        """
        List resolved (approved/rejected) approval requests.

        Args:
            domain: Filter by domain
            type: Filter by type
            since: Only include requests resolved after this time
            limit: Maximum number of requests to return

        Returns:
            List of resolved ApprovalRequests (most recent first)
        """
        all_requests = await self.storage.list_all()
        resolved = []

        for request in all_requests:
            # Filter by status
            if request.status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
                continue

            # Filter by domain
            if domain and request.domain != domain:
                continue

            # Filter by type
            if type and request.type != type:
                continue

            # Filter by time
            if since and request.resolved_at and request.resolved_at < since:
                continue

            resolved.append(request)

        # Sort by resolved_at descending (most recent first)
        resolved.sort(key=lambda r: r.resolved_at or r.created_at, reverse=True)

        return resolved[:limit]

    async def expire_old_requests(self) -> list[ApprovalRequest]:
        """
        Mark expired requests as expired.

        Returns:
            List of requests that were marked expired
        """
        all_requests = await self.storage.list_all()
        expired = []

        for request in all_requests:
            if request.status == ApprovalStatus.PENDING and request.is_expired:
                request.status = ApprovalStatus.EXPIRED
                request.resolved_at = datetime.now(UTC)
                await self.storage.save(request)
                expired.append(request)
                logger.info(f"Expired request {request.id}", extra={"request_id": request.id})

        return expired

    async def get_stats(self) -> ApprovalQueueStats:
        """
        Get statistics about the approval queue.

        Returns:
            ApprovalQueueStats with queue metrics
        """
        all_requests = await self.storage.list_all()

        # Count by status
        pending_count = 0
        approved_count = 0
        rejected_count = 0
        expired_count = 0
        by_type: dict[str, int] = {}
        by_domain: dict[str, int] = {}
        resolution_times: list[float] = []
        oldest_pending_hours: float | None = None

        for request in all_requests:
            # Count by status
            if request.status == ApprovalStatus.PENDING:
                pending_count += 1
                age = request.age_hours
                if oldest_pending_hours is None or age > oldest_pending_hours:
                    oldest_pending_hours = age
            elif request.status == ApprovalStatus.APPROVED:
                approved_count += 1
            elif request.status == ApprovalStatus.REJECTED:
                rejected_count += 1
            elif request.status == ApprovalStatus.EXPIRED:
                expired_count += 1

            # Count by type
            type_key = request.type.value
            by_type[type_key] = by_type.get(type_key, 0) + 1

            # Count by domain
            by_domain[request.domain] = by_domain.get(request.domain, 0) + 1

            # Track resolution time for approved/rejected
            if request.resolved_at and request.status in (
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
            ):
                delta = request.resolved_at - request.created_at
                resolution_times.append(delta.total_seconds() / 3600)

        avg_resolution = sum(resolution_times) / len(resolution_times) if resolution_times else 0.0

        return ApprovalQueueStats(
            total_requests=len(all_requests),
            pending_count=pending_count,
            approved_count=approved_count,
            rejected_count=rejected_count,
            expired_count=expired_count,
            avg_resolution_hours=avg_resolution,
            by_type=by_type,
            by_domain=by_domain,
            oldest_pending_hours=oldest_pending_hours,
        )

    async def cleanup_old_requests(self, days: int = 30) -> int:
        """
        Delete resolved requests older than specified days.

        Args:
            days: Delete requests resolved more than this many days ago

        Returns:
            Number of requests deleted
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        all_requests = await self.storage.list_all()
        deleted = 0

        for request in all_requests:
            # Only delete resolved requests
            if request.status not in (
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.CANCELLED,
            ):
                continue

            # Check age
            resolved_at = request.resolved_at or request.created_at
            if resolved_at < cutoff:
                if await self.storage.delete(request.id):
                    deleted += 1

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old approval requests")

        return deleted


# =============================================================================
# Notion Storage
# =============================================================================


class NotionApprovalStorage(ApprovalStorage):
    """Notion database storage for approval requests.

    Syncs approval requests to a Notion database for visibility and
    human-friendly approval via Notion UI.

    Expected Notion Database Schema:
        - Title: title (request title)
        - ID: rich_text (request ID)
        - Type: select (content, deploy, feature, etc.)
        - Domain: select (domain name)
        - Status: status (Pending, Approved, Rejected, Expired, Cancelled)
        - Priority: select (low, normal, high, critical)
        - Description: rich_text
        - Approver: rich_text
        - Resolution: rich_text
        - Created: date
        - Expires: date
        - Resolved: date
        - Checkpoint: rich_text (workflow checkpoint path)
        - Tags: multi_select
    """

    # Status mapping: ApprovalStatus -> Notion status name
    STATUS_MAP = {
        ApprovalStatus.PENDING: "Pending",
        ApprovalStatus.APPROVED: "Approved",
        ApprovalStatus.REJECTED: "Rejected",
        ApprovalStatus.EXPIRED: "Expired",
        ApprovalStatus.CANCELLED: "Cancelled",
    }

    # Reverse mapping: Notion status -> ApprovalStatus
    REVERSE_STATUS_MAP = {v: k for k, v in STATUS_MAP.items()}

    def __init__(
        self,
        database_id: str,
        api_token: str | None = None,
        local_storage: ApprovalStorage | None = None,
    ):
        """Initialize Notion approval storage.

        Args:
            database_id: Notion database ID for approval requests
            api_token: Notion API token (required for direct API mode)
            local_storage: Optional local storage for fallback/cache
        """
        self.database_id = database_id
        self.api_token = api_token
        self._client = None
        self._local_storage = local_storage
        self._page_cache: dict[str, str] = {}  # request_id -> page_id

    async def _ensure_client(self):
        """Ensure the Notion client is initialized."""
        if self._client is None:
            if not self.api_token:
                raise ValueError("Notion API token required for direct API mode")
            try:
                from notion_client import AsyncClient

                self._client = AsyncClient(auth=self.api_token)
            except ImportError:
                raise ImportError(
                    "notion-client package not installed. Install with: uv add notion-client"
                )

    def _request_to_properties(self, request: ApprovalRequest) -> dict:
        """Convert ApprovalRequest to Notion properties."""
        properties = {
            "Title": {"title": [{"text": {"content": request.title}}]},
            "ID": {"rich_text": [{"text": {"content": request.id}}]},
            "Type": {"select": {"name": request.type.value}},
            "Domain": {"select": {"name": request.domain}},
            "Status": {"status": {"name": self.STATUS_MAP[request.status]}},
            "Priority": {"select": {"name": request.priority.value}},
            "Description": {"rich_text": [{"text": {"content": request.description[:2000]}}]},
            "Created": {"date": {"start": request.created_at.isoformat()}},
        }

        # Optional fields
        if request.approved_by:
            properties["Approver"] = {"rich_text": [{"text": {"content": request.approved_by}}]}

        if request.resolution_reason:
            properties["Resolution"] = {
                "rich_text": [{"text": {"content": request.resolution_reason[:2000]}}]
            }

        if request.expires_at:
            properties["Expires"] = {"date": {"start": request.expires_at.isoformat()}}

        if request.resolved_at:
            properties["Resolved"] = {"date": {"start": request.resolved_at.isoformat()}}

        if request.workflow_checkpoint:
            properties["Checkpoint"] = {
                "rich_text": [{"text": {"content": request.workflow_checkpoint}}]
            }

        if request.tags:
            properties["Tags"] = {"multi_select": [{"name": tag} for tag in request.tags]}

        return properties

    def _extract_text(self, prop: dict, prop_type: str = "rich_text") -> str:
        """Extract text from Notion property."""
        if prop_type == "title":
            items = prop.get("title", [])
        else:
            items = prop.get("rich_text", [])
        return "".join(item.get("plain_text", "") for item in items) if items else ""

    def _extract_select(self, prop: dict) -> str | None:
        """Extract value from Notion select property."""
        select = prop.get("select")
        return select.get("name") if select else None

    def _extract_status(self, prop: dict) -> str | None:
        """Extract value from Notion status property."""
        status = prop.get("status")
        return status.get("name") if status else None

    def _extract_date(self, prop: dict) -> datetime | None:
        """Extract datetime from Notion date property."""
        date_obj = prop.get("date")
        if date_obj and date_obj.get("start"):
            return datetime.fromisoformat(date_obj["start"].replace("Z", "+00:00"))
        return None

    def _extract_multi_select(self, prop: dict) -> list[str]:
        """Extract values from Notion multi_select property."""
        return [item.get("name", "") for item in prop.get("multi_select", [])]

    def _page_to_request(self, page: dict) -> ApprovalRequest:
        """Convert Notion page to ApprovalRequest."""
        props = page.get("properties", {})

        # Extract status and convert to enum
        status_name = self._extract_status(props.get("Status", {}))
        status = self.REVERSE_STATUS_MAP.get(status_name, ApprovalStatus.PENDING)

        # Extract type and priority
        type_name = self._extract_select(props.get("Type", {}))
        priority_name = self._extract_select(props.get("Priority", {}))

        return ApprovalRequest(
            id=self._extract_text(props.get("ID", {})),
            type=ApprovalType(type_name) if type_name else ApprovalType.CONTENT,
            domain=self._extract_select(props.get("Domain", {})) or "unknown",
            title=self._extract_text(props.get("Title", {}), "title"),
            description=self._extract_text(props.get("Description", {})),
            status=status,
            priority=ApprovalPriority(priority_name) if priority_name else ApprovalPriority.NORMAL,
            created_at=self._extract_date(props.get("Created", {})) or datetime.now(UTC),
            expires_at=self._extract_date(props.get("Expires", {})),
            approved_by=self._extract_text(props.get("Approver", {})) or None,
            resolved_at=self._extract_date(props.get("Resolved", {})),
            resolution_reason=self._extract_text(props.get("Resolution", {})) or None,
            workflow_checkpoint=self._extract_text(props.get("Checkpoint", {})) or None,
            tags=self._extract_multi_select(props.get("Tags", {})),
        )

    async def _find_page_by_id(self, request_id: str) -> str | None:
        """Find Notion page ID by request ID."""
        # Check cache first
        if request_id in self._page_cache:
            return self._page_cache[request_id]

        await self._ensure_client()

        # Query database for page with matching ID
        response = await self._client.databases.query(
            database_id=self.database_id,
            filter={
                "property": "ID",
                "rich_text": {"equals": request_id},
            },
        )

        results = response.get("results", [])
        if results:
            page_id = results[0]["id"]
            self._page_cache[request_id] = page_id
            return page_id

        return None

    async def save(self, request: ApprovalRequest) -> None:
        """Save approval request to Notion."""
        await self._ensure_client()

        properties = self._request_to_properties(request)

        # Check if page already exists
        page_id = await self._find_page_by_id(request.id)

        if page_id:
            # Update existing page
            await self._client.pages.update(page_id=page_id, properties=properties)
            logger.debug(f"Updated Notion page for request {request.id}")
        else:
            # Create new page
            response = await self._client.pages.create(
                parent={"database_id": self.database_id},
                properties=properties,
            )
            self._page_cache[request.id] = response["id"]
            logger.debug(f"Created Notion page for request {request.id}")

        # Also save to local storage if configured
        if self._local_storage:
            await self._local_storage.save(request)

    async def load(self, request_id: str) -> ApprovalRequest | None:
        """Load approval request from Notion."""
        await self._ensure_client()

        page_id = await self._find_page_by_id(request_id)
        if not page_id:
            # Fallback to local storage
            if self._local_storage:
                return await self._local_storage.load(request_id)
            return None

        try:
            page = await self._client.pages.retrieve(page_id=page_id)
            return self._page_to_request(page)
        except Exception as e:
            logger.warning(f"Failed to load request {request_id} from Notion: {e}")
            # Fallback to local storage
            if self._local_storage:
                return await self._local_storage.load(request_id)
            return None

    async def list_all(self) -> list[ApprovalRequest]:
        """List all approval requests from Notion."""
        await self._ensure_client()

        requests = []
        has_more = True
        next_cursor = None

        while has_more:
            kwargs = {"database_id": self.database_id}
            if next_cursor:
                kwargs["start_cursor"] = next_cursor

            response = await self._client.databases.query(**kwargs)

            for page in response.get("results", []):
                request = self._page_to_request(page)
                requests.append(request)
                # Update cache
                self._page_cache[request.id] = page["id"]

            has_more = response.get("has_more", False)
            next_cursor = response.get("next_cursor")

        # Sort by created_at descending
        requests.sort(key=lambda r: r.created_at, reverse=True)
        return requests

    async def delete(self, request_id: str) -> bool:
        """Delete approval request from Notion (archive the page)."""
        await self._ensure_client()

        page_id = await self._find_page_by_id(request_id)
        if not page_id:
            return False

        try:
            # Archive the page (Notion doesn't support hard delete via API)
            await self._client.pages.update(page_id=page_id, archived=True)
            self._page_cache.pop(request_id, None)
            logger.debug(f"Archived Notion page for request {request_id}")

            # Also delete from local storage if configured
            if self._local_storage:
                await self._local_storage.delete(request_id)

            return True
        except Exception as e:
            logger.error(f"Failed to delete request {request_id} from Notion: {e}")
            return False


# =============================================================================
# Redis Storage
# =============================================================================


class RedisApprovalStorage(ApprovalStorage):
    """Redis-based storage for approval requests with optional file backup.

    Provides fast, centralized storage for approvals using Redis.
    Optionally syncs to local file storage for backup/fallback.

    Redis Schema:
        - approval:{request_id} (hash) - Approval request data
        - approval:index:pending (set) - Set of pending request IDs
        - approval:index:domain:{domain} (set) - Requests by domain
        - approval:index:type:{type} (set) - Requests by type
    """

    def __init__(
        self,
        redis_url: str | None = None,
        local_storage: ApprovalStorage | None = None,
    ):
        """Initialize Redis approval storage.

        Args:
            redis_url: Redis connection URL (default: env var REDIS_URL or localhost)
            local_storage: Optional local storage for backup/fallback
        """
        self.redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._local_storage = local_storage
        self._client = None
        self._connected = False

    def connect(self) -> bool:
        """Connect to Redis.

        Returns:
            True if successful, False otherwise.
        """
        try:
            import redis

            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
            self._client.ping()
            self._connected = True
            logger.info(f"Connected to Redis for approval storage at {self.redis_url}")
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to Redis for approvals: {e}")
            self._connected = False
            self._client = None
            return False

    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._connected and self._client is not None

    async def save(self, request: ApprovalRequest) -> None:
        """Save approval request to Redis."""
        # Try Redis first
        if self.is_connected():
            try:
                key = f"approval:{request.id}"
                data = request.to_dict()

                # Redis hashes require string values, so serialize complex fields
                redis_data = {}
                for k, v in data.items():
                    if v is None:
                        redis_data[k] = ""
                    elif isinstance(v, (dict, list)):
                        redis_data[k] = json.dumps(v)
                    else:
                        redis_data[k] = str(v)

                # Store as hash
                self._client.hset(key, mapping=redis_data)
                self._client.expire(key, 86400 * 30)  # 30 days TTL

                # Update indexes
                if request.status == ApprovalStatus.PENDING:
                    self._client.sadd("approval:index:pending", request.id)
                else:
                    self._client.srem("approval:index:pending", request.id)

                self._client.sadd(f"approval:index:domain:{request.domain}", request.id)
                self._client.sadd(f"approval:index:type:{request.type.value}", request.id)

                logger.debug(f"Saved approval request {request.id} to Redis")
            except Exception as e:
                logger.error(f"Failed to save approval {request.id} to Redis: {e}")
                # Fall through to local storage

        # Also save to local storage if configured
        if self._local_storage:
            await self._local_storage.save(request)

    async def load(self, request_id: str) -> ApprovalRequest | None:
        """Load approval request from Redis."""
        # Try Redis first
        if self.is_connected():
            try:
                key = f"approval:{request_id}"
                redis_data = self._client.hgetall(key)
                if redis_data:
                    # Deserialize JSON fields back to their original types
                    data = {}
                    for k, v in redis_data.items():
                        if v == "" or v is None:
                            data[k] = None
                        elif k in ("metadata", "notification_ids", "tags"):
                            # These are stored as JSON
                            try:
                                data[k] = json.loads(v) if v else ({} if k == "metadata" else [])
                            except json.JSONDecodeError:
                                data[k] = {} if k == "metadata" else []
                        else:
                            data[k] = v
                    return ApprovalRequest.from_dict(data)
            except Exception as e:
                logger.error(f"Failed to load approval {request_id} from Redis: {e}")

        # Fallback to local storage
        if self._local_storage:
            return await self._local_storage.load(request_id)

        return None

    async def list_all(self) -> list[ApprovalRequest]:
        """List all approval requests from Redis."""
        # Try Redis first
        if self.is_connected():
            try:
                # Use SCAN instead of KEYS for production safety
                cursor = 0
                keys = []
                while True:
                    cursor, batch = self._client.scan(cursor, match="approval:*", count=100)
                    # Filter out index keys
                    batch_filtered = [k for k in batch if not k.startswith("approval:index:")]
                    keys.extend(batch_filtered)
                    if cursor == 0:
                        break

                requests = []
                for key in keys:
                    try:
                        redis_data = self._client.hgetall(key)
                        if redis_data:
                            # Deserialize JSON fields
                            data = {}
                            for k, v in redis_data.items():
                                if v == "" or v is None:
                                    data[k] = None
                                elif k in ("metadata", "notification_ids", "tags"):
                                    try:
                                        data[k] = (
                                            json.loads(v) if v else ({} if k == "metadata" else [])
                                        )
                                    except json.JSONDecodeError:
                                        data[k] = {} if k == "metadata" else []
                                else:
                                    data[k] = v
                            request = ApprovalRequest.from_dict(data)
                            requests.append(request)
                    except Exception as e:
                        logger.warning(f"Failed to parse approval from {key}: {e}")

                # Sort by created_at descending
                requests.sort(key=lambda r: r.created_at, reverse=True)
                return requests
            except Exception as e:
                logger.error(f"Failed to list approvals from Redis: {e}")

        # Fallback to local storage
        if self._local_storage:
            return await self._local_storage.list_all()

        return []

    async def delete(self, request_id: str) -> bool:
        """Delete approval request from Redis."""
        success = False

        # Try Redis first
        if self.is_connected():
            try:
                # Get request data before deleting to update indexes
                key = f"approval:{request_id}"
                data = self._client.hgetall(key)

                if data:
                    request = ApprovalRequest.from_dict(data)

                    # Delete from Redis
                    self._client.delete(key)

                    # Update indexes
                    self._client.srem("approval:index:pending", request_id)
                    self._client.srem(f"approval:index:domain:{request.domain}", request_id)
                    self._client.srem(f"approval:index:type:{request.type.value}", request_id)

                    logger.debug(f"Deleted approval request {request_id} from Redis")
                    success = True
            except Exception as e:
                logger.error(f"Failed to delete approval {request_id} from Redis: {e}")

        # Also delete from local storage if configured
        if self._local_storage:
            local_success = await self._local_storage.delete(request_id)
            success = success or local_success

        return success


# =============================================================================
# Factory Functions
# =============================================================================


def create_approval_queue(
    storage_dir: Path | str | None = None,
    notification_harness: Any | None = None,
    default_expiry_hours: float = 72.0,
) -> ApprovalQueueHarness:
    """
    Create an ApprovalQueueHarness with default settings.

    Args:
        storage_dir: Directory for JSON file storage (default: .forge/approvals)
        notification_harness: Optional NotificationHarness instance
        default_expiry_hours: Default expiration time for requests

    Returns:
        Configured ApprovalQueueHarness
    """
    if storage_dir is None:
        storage_dir = Path(".forge/approvals")
    else:
        storage_dir = Path(storage_dir)

    storage = JSONFileStorage(storage_dir)

    return ApprovalQueueHarness(
        storage=storage,
        notification_harness=notification_harness,
        default_expiry_hours=default_expiry_hours,
    )


def create_notion_approval_queue(
    database_id: str,
    api_token: str | None = None,
    storage_dir: Path | str | None = None,
    notification_harness: Any | None = None,
    default_expiry_hours: float = 72.0,
) -> ApprovalQueueHarness:
    """
    Create an ApprovalQueueHarness with Notion storage.

    Syncs approval requests to a Notion database for visibility
    while maintaining local file backup.

    Args:
        database_id: Notion database ID for approval requests
        api_token: Notion API token (or set NOTION_API_TOKEN env var)
        storage_dir: Directory for local JSON backup (default: .forge/approvals)
        notification_harness: Optional NotificationHarness instance
        default_expiry_hours: Default expiration time for requests

    Returns:
        Configured ApprovalQueueHarness with Notion storage

    Environment Variables:
        NOTION_API_TOKEN: Can be used instead of api_token parameter
        NOTION_APPROVAL_DATABASE_ID: Can be used instead of database_id parameter
    """
    import os

    # Get API token from env if not provided
    token = api_token or os.environ.get("NOTION_API_TOKEN")
    if not token:
        raise ValueError(
            "Notion API token required. Provide via api_token parameter "
            "or NOTION_API_TOKEN environment variable."
        )

    # Setup local storage for backup
    if storage_dir is None:
        storage_dir = Path(".forge/approvals")
    else:
        storage_dir = Path(storage_dir)

    local_storage = JSONFileStorage(storage_dir)

    # Create Notion storage with local fallback
    notion_storage = NotionApprovalStorage(
        database_id=database_id,
        api_token=token,
        local_storage=local_storage,
    )

    return ApprovalQueueHarness(
        storage=notion_storage,
        notification_harness=notification_harness,
        default_expiry_hours=default_expiry_hours,
    )


def create_approval_queue_from_env(
    notification_harness: Any | None = None,
    default_expiry_hours: float = 72.0,
) -> ApprovalQueueHarness:
    """
    Create ApprovalQueueHarness from environment variables.

    Storage priority:
    1. Redis (if REDIS_URL is set and connectable) with file backup
    2. Notion (if NOTION_APPROVAL_DATABASE_ID is set) with file backup
    3. V3 SQLite (if FORGE_APPROVALS_V3=1) — Phase 1 GitGuard integration
    4. File-based storage (fallback)

    Environment Variables:
        REDIS_URL: Redis connection URL (enables Redis mode with file backup)
        NOTION_APPROVAL_DATABASE_ID: Notion database ID (enables Notion mode)
        NOTION_API_TOKEN: Required if using Notion mode
        FORGE_APPROVALS_DIR: Local storage directory (default: .forge/approvals)
        USE_REDIS_APPROVALS: Set to 'true' to prefer Redis over file storage
        FORGE_APPROVALS_V3: Set to '1' or 'true' to store approvals in v3 SQLite

    Returns:
        Configured ApprovalQueueHarness
    """
    import os

    storage_dir = os.environ.get("FORGE_APPROVALS_DIR")
    if not storage_dir:
        forge_root = os.environ.get("FORGE_ROOT")
        if forge_root:
            storage_dir = str(Path(forge_root) / ".forge/approvals")
        else:
            storage_dir = ".forge/approvals"
    redis_url = os.environ.get("REDIS_URL")
    use_redis = os.environ.get("USE_REDIS_APPROVALS", "true").lower() in ("true", "1", "yes")

    # Priority 1: Try Redis with file backup
    if redis_url and use_redis:
        logger.info("Attempting to create Redis-backed approval queue with file backup")
        local_storage = JSONFileStorage(Path(storage_dir))
        redis_storage = RedisApprovalStorage(redis_url=redis_url, local_storage=local_storage)

        # Try to connect to Redis
        if redis_storage.connect():
            logger.info("Using Redis approval storage with file backup")
            return ApprovalQueueHarness(
                storage=redis_storage,
                notification_harness=notification_harness,
                default_expiry_hours=default_expiry_hours,
            )
        else:
            logger.warning("Redis connection failed, falling back to file storage")

    # Priority 2: Try Notion with file backup
    database_id = os.environ.get("NOTION_APPROVAL_DATABASE_ID")
    if database_id:
        logger.info("Using Notion approval storage with file backup")
        return create_notion_approval_queue(
            database_id=database_id,
            storage_dir=storage_dir,
            notification_harness=notification_harness,
            default_expiry_hours=default_expiry_hours,
        )

    # Priority 3: V3 SQLite (Phase 1 GitGuard integration)
    use_v3 = os.environ.get("FORGE_APPROVALS_V3", "").lower() in ("1", "true", "yes")
    if use_v3:
        try:
            from .gitguard.bridge import create_v3_approval_storage, get_v3_db_path

            v3_storage = create_v3_approval_storage(db_path=get_v3_db_path())
            logger.info("Using v3 SQLite approval storage (%s)", get_v3_db_path())
            return ApprovalQueueHarness(
                storage=v3_storage,
                notification_harness=notification_harness,
                default_expiry_hours=default_expiry_hours,
            )
        except Exception as e:
            logger.warning("V3 approval storage failed, falling back to file: %s", e)

    # Priority 4: File-based storage (fallback)
    logger.info("Using file-based approval storage")
    return create_approval_queue(
        storage_dir=storage_dir,
        notification_harness=notification_harness,
        default_expiry_hours=default_expiry_hours,
    )


# =============================================================================
# Global Singleton
# =============================================================================

_approval_queue: ApprovalQueueHarness | None = None


def get_approval_queue() -> ApprovalQueueHarness:
    """Get or create the global ApprovalQueueHarness singleton.

    Returns:
        ApprovalQueueHarness instance.
    """
    global _approval_queue
    if _approval_queue is None:
        _approval_queue = create_approval_queue_from_env()
    return _approval_queue
