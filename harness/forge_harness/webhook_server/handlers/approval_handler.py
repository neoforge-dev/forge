"""Approval Handler

Business logic for approval queue operations.
"""

from pathlib import Path
from typing import Any

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)


class ApprovalQueueHandler:
    """Handles approval queue operations for the webhook server.

    Provides a bridge between HTTP endpoints and the ApprovalQueueHarness.
    """

    def __init__(
        self,
        approval_queue: Any = None,
        human_gate: Any = None,
        orchestrator: Any = None,
        forge_root: Path | None = None,
    ):
        """Initialize approval queue handler.

        Args:
            approval_queue: ApprovalQueueHarness instance
            human_gate: HumanGateHarness instance for approval operations
            orchestrator: OrchestrationHarness for pipeline resumption
            forge_root: Root path of FORGE repository
        """
        self.approval_queue = approval_queue
        self.human_gate = human_gate
        self.orchestrator = orchestrator
        self._forge_root = forge_root or self._find_forge_root()

    def _find_forge_root(self) -> Path:
        """Find FORGE root directory."""
        # Try to find by looking for domains.yaml
        current = Path(__file__).parent
        while current != current.parent:
            if (current / "domains.yaml").exists():
                return current
            if (current / "forge_harness" / "domains.yaml").exists():
                return current
            current = current.parent
        # Default to parent of this file
        return Path(__file__).parent.parent

    async def list_pending(
        self,
        domain: str | None = None,
        project: str | None = None,
        priority: str | None = None,
        tier: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List pending approval requests with filtering.

        Args:
            domain: Optional domain filter
            project: Optional project filter
            priority: Optional priority filter (low, medium, high, critical)
            tier: Optional tier filter (watch, phone, desktop)
            status: Optional status filter (pending, approved, rejected, expired)
            limit: Maximum number of results

        Returns:
            List of pending approval requests as dicts
        """
        if self.approval_queue is None:
            return []

        try:
            requests = await self.approval_queue.list_pending(
                domain=domain,
            )

            # Serialize all requests first (to compute tier)
            serialized = [self._serialize_request(r) for r in requests]

            # Apply additional filters
            if project:
                serialized = [r for r in serialized if r.get("project") == project]
            if priority:
                serialized = [r for r in serialized if r.get("priority") == priority]
            if tier:
                serialized = [r for r in serialized if r.get("tier") == tier]
            if status:
                serialized = [r for r in serialized if r.get("status") == status]

            # Apply limit
            if limit and len(serialized) > limit:
                serialized = serialized[:limit]

            return serialized
        except Exception as e:
            logger.error(f"Failed to list pending approvals: {e}")
            return []

    async def count_pending(
        self,
        domain: str | None = None,
        project: str | None = None,
        priority: str | None = None,
        tier: str | None = None,
    ) -> int:
        """Count pending approval requests.

        Args:
            domain: Optional domain filter
            project: Optional project filter
            priority: Optional priority filter
            tier: Optional tier filter

        Returns:
            Count of pending approvals matching filters
        """
        approvals = await self.list_pending(
            domain=domain,
            project=project,
            priority=priority,
            tier=tier,
            status="pending",
            limit=10000,  # Large limit for counting
        )
        return len(approvals)

    async def batch_approve(
        self,
        request_ids: list[str],
        approver: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Approve multiple requests at once.

        Only approves requests that are low-risk (watch tier).

        Args:
            request_ids: List of request IDs to approve
            approver: Identifier of the approver
            comment: Optional approval comment

        Returns:
            Result dict with approved/failed counts
        """
        if self.approval_queue is None:
            return {"success": False, "error": "Approval queue not configured"}

        approved = []
        failed = []

        for request_id in request_ids:
            try:
                # Get request to check tier
                request = await self.get_request(request_id)
                if request is None:
                    failed.append({"request_id": request_id, "error": "Not found"})
                    continue

                # Only allow batch approval for watch tier
                if request.get("tier") != "watch":
                    failed.append(
                        {
                            "request_id": request_id,
                            "error": f"Tier '{request.get('tier')}' requires individual approval",
                        }
                    )
                    continue

                # Approve the request
                result = await self.approve_request(
                    request_id=request_id,
                    approver=approver,
                    comment=comment or "Batch approved",
                    auto_resume=True,
                )

                if result.get("success"):
                    approved.append(request_id)
                else:
                    failed.append({"request_id": request_id, "error": result.get("error")})

            except Exception as e:
                failed.append({"request_id": request_id, "error": str(e)})

        return {
            "success": True,
            "approved_count": len(approved),
            "failed_count": len(failed),
            "approved": approved,
            "failed": failed,
        }

    async def get_request(self, request_id: str) -> dict[str, Any] | None:
        """Get approval request details.

        Args:
            request_id: The approval request ID

        Returns:
            Request details as dict, or None if not found
        """
        if self.approval_queue is None:
            return None

        try:
            request = await self.approval_queue.get_request(request_id)
            if request is None:
                return None
            return self._serialize_request(request)
        except Exception as e:
            logger.error(f"Failed to get approval request {request_id}: {e}")
            return None

    async def approve_request(
        self,
        request_id: str,
        approver: str,
        comment: str | None = None,
        auto_resume: bool = True,
    ) -> dict[str, Any]:
        """Approve a request.

        Args:
            request_id: The approval request ID
            approver: Identifier of the approver
            comment: Optional approval comment
            auto_resume: Whether to auto-resume associated pipeline

        Returns:
            Result dict with status and optional resume result
        """
        if self.approval_queue is None:
            return {"success": False, "error": "Approval queue not configured"}

        try:
            approved = await self.approval_queue.approve(
                request_id=request_id,
                approver=approver,
                comment=comment,
            )

            result: dict[str, Any] = {
                "success": True,
                "request_id": request_id,
                "status": "approved",
                "approver": approver,
            }

            # Auto-resume pipeline if configured and checkpoint exists
            if auto_resume and approved.workflow_checkpoint and self.orchestrator:
                try:
                    checkpoint_path = Path(approved.workflow_checkpoint)
                    if checkpoint_path.exists():
                        pipeline_result = await self.orchestrator.resume(checkpoint_path)
                        result["pipeline_resumed"] = True
                        result["pipeline_success"] = (
                            pipeline_result.success if pipeline_result else False
                        )
                except Exception as resume_error:
                    logger.warning(f"Failed to auto-resume pipeline: {resume_error}")
                    result["pipeline_resumed"] = False
                    result["resume_error"] = str(resume_error)

            return result
        except Exception as e:
            logger.error(f"Failed to approve request {request_id}: {e}")
            return {"success": False, "error": str(e)}

    async def reject_request(
        self,
        request_id: str,
        rejector: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Reject a request.

        Args:
            request_id: The approval request ID
            rejector: Identifier of the rejector
            reason: Optional rejection reason

        Returns:
            Result dict with status
        """
        if self.approval_queue is None:
            return {"success": False, "error": "Approval queue not configured"}

        try:
            await self.approval_queue.reject(
                request_id=request_id,
                approver=rejector,  # ApprovalQueueHarness uses 'approver' for both
                reason=reason or "Rejected",
            )

            return {
                "success": True,
                "request_id": request_id,
                "status": "rejected",
                "rejector": rejector,
            }
        except Exception as e:
            logger.error(f"Failed to reject request {request_id}: {e}")
            return {"success": False, "error": str(e)}

    async def get_stats(self, domain: str | None = None) -> dict[str, Any]:
        """Get approval queue statistics.

        Args:
            domain: Optional domain filter (currently not implemented in underlying harness)

        Returns:
            Statistics dict
        """
        if self.approval_queue is None:
            return {"error": "Approval queue not configured"}

        try:
            # Note: ApprovalQueueHarness.get_stats() doesn't take domain filter yet
            stats = await self.approval_queue.get_stats()
            return {
                "pending": stats.pending_count,
                "approved": stats.approved_count,
                "rejected": stats.rejected_count,
                "expired": stats.expired_count,
                "total": stats.total_requests,
                "oldest_pending_age_hours": stats.oldest_pending_hours,
            }
        except Exception as e:
            logger.error(f"Failed to get approval stats: {e}")
            return {"error": str(e)}

    def _compute_tier(self, request: Any) -> str:
        """Compute notification tier based on priority and type.

        Tiers:
            - watch: Binary approve/reject, low risk (test retry, lint fix)
            - phone: Summary context needed, medium risk (feature complete)
            - desktop: Full review required, high risk (security, production deploy)

        Args:
            request: ApprovalRequest instance

        Returns:
            Tier string: "watch", "phone", or "desktop"
        """
        priority = (
            request.priority.value if hasattr(request.priority, "value") else request.priority
        )
        approval_type = request.type.value if hasattr(request.type, "value") else request.type

        # High risk types always desktop
        high_risk_types = {"deployment", "security", "production", "infrastructure"}
        if approval_type in high_risk_types:
            return "desktop"

        # Priority mapping
        if priority == "critical":
            return "desktop"
        elif priority == "high":
            return "phone"
        elif priority == "low":
            return "watch"
        else:  # medium or default
            return "phone"

    def _extract_files_affected(self, metadata: dict | None) -> int:
        """Extract files affected count from metadata."""
        if not metadata:
            return 0
        if isinstance(metadata.get("files_changed"), int):
            return metadata["files_changed"]
        if isinstance(metadata.get("files_affected"), int):
            return metadata["files_affected"]
        files = metadata.get("files")
        if isinstance(files, list):
            return len(files)
        return 0

    def _derive_risk_level(self, priority: Any, tier: str) -> str:
        """Derive risk level from priority and tier."""
        priority_value = priority.value if hasattr(priority, "value") else str(priority)
        if tier == "desktop" or priority_value == "critical":
            return "high"
        if priority_value == "high":
            return "medium"
        return "low"

    def _derive_estimated_impact(self, priority: Any) -> int:
        """Derive estimated impact score from priority."""
        priority_value = priority.value if hasattr(priority, "value") else str(priority)
        impact_map = {
            "critical": 5,
            "high": 4,
            "normal": 3,
            "medium": 3,
            "low": 2,
        }
        return impact_map.get(priority_value, 1)

    def _serialize_request(self, request: Any) -> dict[str, Any]:
        """Serialize an ApprovalRequest to dict.

        Args:
            request: ApprovalRequest instance

        Returns:
            Dict representation
        """
        tier = self._compute_tier(request)
        project = request.metadata.get("project") if request.metadata else None
        files_affected = self._extract_files_affected(request.metadata)
        risk_level = self._derive_risk_level(request.priority, tier)
        estimated_impact = self._derive_estimated_impact(request.priority)

        return {
            "request_id": request.id,
            "approval_type": request.type.value,
            "domain": request.domain,
            "project": project,
            "title": request.title,
            "description": request.description,
            "status": request.status.value,
            "priority": request.priority.value,
            "tier": tier,
            "created_at": request.created_at.isoformat(),
            "expires_at": request.expires_at.isoformat() if request.expires_at else None,
            "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
            "approved_by": request.approved_by,
            "resolution_reason": request.resolution_reason,
            "workflow_checkpoint": request.workflow_checkpoint,
            "context": {
                "files_affected": files_affected,
                "risk_level": risk_level,
                "estimated_impact": estimated_impact,
            },
        }


# Singleton instance
_approval_handler: ApprovalQueueHandler | None = None


def get_approval_handler(
    approval_queue: Any = None,
    human_gate: Any = None,
    orchestrator: Any = None,
) -> ApprovalQueueHandler:
    """Get or create the approval handler singleton.

    Returns:
        ApprovalQueueHandler: The approval handler instance
    """
    global _approval_handler
    if _approval_handler is None:
        _approval_handler = ApprovalQueueHandler(
            approval_queue=approval_queue,
            human_gate=human_gate,
            orchestrator=orchestrator,
        )
    return _approval_handler
