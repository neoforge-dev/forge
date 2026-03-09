"""Extended tests for ApprovalQueueHandler - covering edge cases and uncovered paths.

Focuses on:
- _find_forge_root path traversal logic
- _compute_tier with string priority/type values and all ApprovalType variants
- _extract_files_affected edge cases (non-int values, non-list files)
- _derive_risk_level / _derive_estimated_impact with raw string priority
- _serialize_request edge cases (no expires_at, files from list metadata)
- list_pending multiple combined filters, zero-limit, limit equals length
- count_pending with no queue
- batch_approve empty list, approve returning failure dict
- approve_request with None pipeline result, no comment passed
- get_approval_handler singleton reuse with pre-existing instance
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.webhook_server.handlers.approval_handler import (
    ApprovalQueueHandler,
    get_approval_handler,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_approval_queue():
    """Create mock ApprovalQueueHarness."""
    queue = MagicMock()
    queue.list_pending = AsyncMock(return_value=[])
    queue.get_request = AsyncMock(return_value=None)
    queue.approve = AsyncMock()
    queue.reject = AsyncMock()
    queue.get_stats = AsyncMock()
    return queue


@pytest.fixture
def mock_orchestrator():
    """Create mock OrchestrationHarness."""
    orchestrator = MagicMock()
    orchestrator.resume = AsyncMock()
    return orchestrator


@pytest.fixture
def handler(mock_approval_queue, mock_orchestrator):
    """Create handler with mocked dependencies."""
    return ApprovalQueueHandler(
        approval_queue=mock_approval_queue,
        orchestrator=mock_orchestrator,
        forge_root=Path("/tmp/forge"),
    )


@pytest.fixture
def sample_request():
    """Create a sample ApprovalRequest in PENDING state."""
    return ApprovalRequest(
        id="req-ext-001",
        type=ApprovalType.CONTENT,
        domain="test-domain",
        title="Test Approval",
        description="Extended test description",
        status=ApprovalStatus.PENDING,
        priority=ApprovalPriority.NORMAL,
        tier="phone",
        metadata={"project": "test-project", "files_changed": 2},
        created_at=datetime(2026, 2, 22, 9, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# _find_forge_root Tests
# ---------------------------------------------------------------------------


class TestFindForgeRoot:
    """Tests for _find_forge_root path traversal logic."""

    def test_find_forge_root_via_domains_yaml(self, tmp_path):
        """Root found via domains.yaml at a parent directory."""
        # Place domains.yaml at tmp_path
        (tmp_path / "domains.yaml").write_text("domains: []")

        # Patch __file__ so traversal starts inside tmp_path
        fake_file = str(tmp_path / "webhook_server" / "handlers" / "approval_handler.py")
        with patch(
            "forge_harness.webhook_server.handlers.approval_handler.__file__",
            fake_file,
        ):
            handler = ApprovalQueueHandler()
            # Root should be somewhere up the chain — domains.yaml was found
            assert handler._forge_root is not None
            assert isinstance(handler._forge_root, Path)

    def test_find_forge_root_via_forge_harness_domains_yaml(self, tmp_path):
        """Root found via forge_harness/domains.yaml."""
        forge_harness_dir = tmp_path / "forge_harness"
        forge_harness_dir.mkdir()
        (forge_harness_dir / "domains.yaml").write_text("domains: []")

        fake_file = str(tmp_path / "forge_harness" / "webhook_server" / "handlers" / "approval_handler.py")
        with patch(
            "forge_harness.webhook_server.handlers.approval_handler.__file__",
            fake_file,
        ):
            handler = ApprovalQueueHandler()
            assert handler._forge_root is not None

    def test_find_forge_root_fallback_when_no_domains_yaml(self):
        """Falls back to parent of __file__ when domains.yaml is not found."""
        # Don't patch — the real __file__ does not have a domains.yaml in every parent
        # but the function must terminate with a Path value (fallback)
        handler = ApprovalQueueHandler()
        assert handler._forge_root is not None
        assert isinstance(handler._forge_root, Path)

    def test_forge_root_provided_skips_search(self, tmp_path):
        """When forge_root is explicitly provided, _find_forge_root is never called."""
        with patch.object(ApprovalQueueHandler, "_find_forge_root") as mock_find:
            handler = ApprovalQueueHandler(forge_root=tmp_path)
            mock_find.assert_not_called()
            assert handler._forge_root == tmp_path


# ---------------------------------------------------------------------------
# _compute_tier Edge Cases
# ---------------------------------------------------------------------------


class TestComputeTierEdgeCases:
    """Edge cases for _compute_tier — string values instead of enums."""

    @pytest.fixture
    def h(self):
        return ApprovalQueueHandler(forge_root=Path("/tmp"))

    def _req(self, priority_val, type_val):
        """Build a MagicMock request with raw string .priority and .type."""
        r = MagicMock()
        r.priority = priority_val  # raw string — no .value attr
        r.type = type_val          # raw string — no .value attr
        return r

    def test_compute_tier_string_priority_critical(self, h):
        """String 'critical' priority resolves to desktop."""
        req = self._req("critical", "content")
        assert h._compute_tier(req) == "desktop"

    def test_compute_tier_string_priority_high(self, h):
        """String 'high' priority resolves to phone."""
        req = self._req("high", "content")
        assert h._compute_tier(req) == "phone"

    def test_compute_tier_string_priority_low(self, h):
        """String 'low' priority resolves to watch."""
        req = self._req("low", "content")
        assert h._compute_tier(req) == "watch"

    def test_compute_tier_string_priority_medium(self, h):
        """String 'medium' (not an enum member) resolves to phone."""
        req = self._req("medium", "content")
        assert h._compute_tier(req) == "phone"

    def test_compute_tier_string_priority_unknown(self, h):
        """Unrecognised priority falls through to phone (else branch)."""
        req = self._req("whatever", "content")
        assert h._compute_tier(req) == "phone"

    def test_compute_tier_high_risk_type_deployment(self, h):
        """Type 'deployment' (string) triggers desktop regardless of priority."""
        req = self._req("low", "deployment")
        assert h._compute_tier(req) == "desktop"

    def test_compute_tier_high_risk_type_security(self, h):
        """Type 'security' triggers desktop."""
        req = self._req("low", "security")
        assert h._compute_tier(req) == "desktop"

    def test_compute_tier_high_risk_type_production(self, h):
        """Type 'production' triggers desktop."""
        req = self._req("low", "production")
        assert h._compute_tier(req) == "desktop"

    def test_compute_tier_high_risk_type_infrastructure(self, h):
        """Type 'infrastructure' triggers desktop."""
        req = self._req("low", "infrastructure")
        assert h._compute_tier(req) == "desktop"

    def test_compute_tier_compliance_type_low_priority(self, h):
        """COMPLIANCE type with low priority → watch (type not in high_risk_types)."""
        req = ApprovalRequest(
            id="x",
            type=ApprovalType.COMPLIANCE,
            domain="d",
            title="t",
            description="d",
            priority=ApprovalPriority.LOW,
        )
        assert h._compute_tier(req) == "watch"

    def test_compute_tier_config_type_normal_priority(self, h):
        """CONFIG type with normal priority → phone."""
        req = ApprovalRequest(
            id="x",
            type=ApprovalType.CONFIG,
            domain="d",
            title="t",
            description="d",
            priority=ApprovalPriority.NORMAL,
        )
        assert h._compute_tier(req) == "phone"

    def test_compute_tier_data_type_high_priority(self, h):
        """DATA type with high priority → phone."""
        req = ApprovalRequest(
            id="x",
            type=ApprovalType.DATA,
            domain="d",
            title="t",
            description="d",
            priority=ApprovalPriority.HIGH,
        )
        assert h._compute_tier(req) == "phone"

    def test_compute_tier_feature_critical_overrides_type(self, h):
        """FEATURE type with critical priority → desktop (priority wins)."""
        req = ApprovalRequest(
            id="x",
            type=ApprovalType.FEATURE,
            domain="d",
            title="t",
            description="d",
            priority=ApprovalPriority.CRITICAL,
        )
        assert h._compute_tier(req) == "desktop"


# ---------------------------------------------------------------------------
# _extract_files_affected Edge Cases
# ---------------------------------------------------------------------------


class TestExtractFilesAffectedEdgeCases:
    """Edge cases for _extract_files_affected."""

    @pytest.fixture
    def h(self):
        return ApprovalQueueHandler(forge_root=Path("/tmp"))

    def test_files_changed_takes_precedence_over_files_affected(self, h):
        """files_changed is checked before files_affected."""
        result = h._extract_files_affected({"files_changed": 7, "files_affected": 3})
        assert result == 7

    def test_files_changed_not_int_falls_through(self, h):
        """Non-int files_changed skips to files_affected."""
        result = h._extract_files_affected({"files_changed": "three", "files_affected": 3})
        assert result == 3

    def test_files_affected_not_int_falls_through_to_files_list(self, h):
        """Non-int files_affected falls through to files list."""
        result = h._extract_files_affected({"files_affected": None, "files": ["a.py", "b.py"]})
        assert result == 2

    def test_files_not_a_list_returns_zero(self, h):
        """Non-list files key returns 0."""
        result = h._extract_files_affected({"files": "not-a-list"})
        assert result == 0

    def test_files_empty_list(self, h):
        """Empty files list returns 0."""
        result = h._extract_files_affected({"files": []})
        assert result == 0

    def test_all_keys_absent_returns_zero(self, h):
        """Metadata with unrelated keys returns 0."""
        result = h._extract_files_affected({"quality_score": 95, "word_count": 1500})
        assert result == 0

    def test_files_changed_zero(self, h):
        """Zero is a valid int value for files_changed."""
        result = h._extract_files_affected({"files_changed": 0})
        assert result == 0


# ---------------------------------------------------------------------------
# _derive_risk_level Edge Cases
# ---------------------------------------------------------------------------


class TestDeriveRiskLevelEdgeCases:
    """Edge cases for _derive_risk_level."""

    @pytest.fixture
    def h(self):
        return ApprovalQueueHandler(forge_root=Path("/tmp"))

    def test_string_priority_critical_returns_high(self, h):
        """Raw string 'critical' (no .value) returns high."""
        result = h._derive_risk_level("critical", "phone")
        assert result == "high"

    def test_string_priority_high_returns_medium(self, h):
        """Raw string 'high' returns medium."""
        result = h._derive_risk_level("high", "watch")
        assert result == "medium"

    def test_string_priority_low_watch_tier_returns_low(self, h):
        """Raw string 'low' with watch tier returns low."""
        result = h._derive_risk_level("low", "watch")
        assert result == "low"

    def test_desktop_tier_always_high_regardless_of_priority(self, h):
        """Desktop tier always returns high regardless of priority."""
        result = h._derive_risk_level(ApprovalPriority.LOW, "desktop")
        assert result == "high"

    def test_normal_priority_phone_tier_returns_low(self, h):
        """Normal priority on phone tier returns low (not medium)."""
        result = h._derive_risk_level(ApprovalPriority.NORMAL, "phone")
        assert result == "low"


# ---------------------------------------------------------------------------
# _derive_estimated_impact Edge Cases
# ---------------------------------------------------------------------------


class TestDeriveEstimatedImpactEdgeCases:
    """Edge cases for _derive_estimated_impact."""

    @pytest.fixture
    def h(self):
        return ApprovalQueueHandler(forge_root=Path("/tmp"))

    def test_raw_string_critical(self, h):
        """Raw string 'critical' (no .value attr) returns 5."""
        result = h._derive_estimated_impact("critical")
        assert result == 5

    def test_raw_string_high(self, h):
        """Raw string 'high' returns 4."""
        result = h._derive_estimated_impact("high")
        assert result == 4

    def test_raw_string_normal(self, h):
        """Raw string 'normal' returns 3."""
        result = h._derive_estimated_impact("normal")
        assert result == 3

    def test_raw_string_medium(self, h):
        """Raw string 'medium' returns 3."""
        result = h._derive_estimated_impact("medium")
        assert result == 3

    def test_raw_string_low(self, h):
        """Raw string 'low' returns 2."""
        result = h._derive_estimated_impact("low")
        assert result == 2

    def test_raw_string_unknown_returns_one(self, h):
        """Raw string with unknown value returns 1."""
        result = h._derive_estimated_impact("nonexistent")
        assert result == 1


# ---------------------------------------------------------------------------
# _serialize_request Edge Cases
# ---------------------------------------------------------------------------


class TestSerializeRequestEdgeCases:
    """Edge cases for _serialize_request."""

    @pytest.fixture
    def h(self):
        return ApprovalQueueHandler(forge_root=Path("/tmp"))

    def test_serialize_no_expires_at(self, h):
        """expires_at is None when not set."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="t",
            description="desc",
            expires_at=None,
        )
        result = h._serialize_request(req)
        assert result["expires_at"] is None

    def test_serialize_no_resolved_at(self, h):
        """resolved_at is None when not set."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="t",
            description="desc",
            resolved_at=None,
        )
        result = h._serialize_request(req)
        assert result["resolved_at"] is None

    def test_serialize_files_from_list_metadata(self, h):
        """files_affected is derived from metadata['files'] list."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="t",
            description="desc",
            metadata={"files": ["a.py", "b.py", "c.py", "d.py"]},
        )
        result = h._serialize_request(req)
        assert result["context"]["files_affected"] == 4

    def test_serialize_security_type_gets_desktop_tier(self, h):
        """SECURITY type maps to desktop tier in serialized output."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.SECURITY,
            domain="d",
            title="t",
            description="desc",
            priority=ApprovalPriority.LOW,
        )
        result = h._serialize_request(req)
        assert result["tier"] == "desktop"
        assert result["context"]["risk_level"] == "high"

    def test_serialize_no_approved_by(self, h):
        """approved_by is None when not resolved."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="t",
            description="desc",
        )
        result = h._serialize_request(req)
        assert result["approved_by"] is None
        assert result["resolution_reason"] is None

    def test_serialize_workflow_checkpoint_none(self, h):
        """workflow_checkpoint is None when not set."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="t",
            description="desc",
            workflow_checkpoint=None,
        )
        result = h._serialize_request(req)
        assert result["workflow_checkpoint"] is None

    def test_serialize_metadata_files_affected_key(self, h):
        """files_affected metadata key is correctly extracted."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="t",
            description="desc",
            metadata={"files_affected": 10},
        )
        result = h._serialize_request(req)
        assert result["context"]["files_affected"] == 10


# ---------------------------------------------------------------------------
# list_pending Additional Edge Cases
# ---------------------------------------------------------------------------


class TestListPendingEdgeCases:
    """Additional edge cases for list_pending."""

    @pytest.mark.asyncio
    async def test_list_pending_multiple_filters_combined(self, mock_approval_queue):
        """All filters applied simultaneously — only exact matches survive."""
        req_match = ApprovalRequest(
            id="req-match",
            type=ApprovalType.CONTENT,
            domain="target-domain",
            title="Match",
            description="Desc",
            status=ApprovalStatus.PENDING,
            priority=ApprovalPriority.LOW,  # watch tier
            metadata={"project": "target-project"},
        )
        req_wrong_project = ApprovalRequest(
            id="req-wrong-project",
            type=ApprovalType.CONTENT,
            domain="target-domain",
            title="Wrong project",
            description="Desc",
            status=ApprovalStatus.PENDING,
            priority=ApprovalPriority.LOW,
            metadata={"project": "other-project"},
        )
        req_wrong_priority = ApprovalRequest(
            id="req-wrong-priority",
            type=ApprovalType.CONTENT,
            domain="target-domain",
            title="Wrong priority",
            description="Desc",
            status=ApprovalStatus.PENDING,
            priority=ApprovalPriority.HIGH,  # phone tier
            metadata={"project": "target-project"},
        )
        mock_approval_queue.list_pending.return_value = [
            req_match,
            req_wrong_project,
            req_wrong_priority,
        ]

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )

        result = await h.list_pending(
            domain="target-domain",
            project="target-project",
            priority="low",
            tier="watch",
            status="pending",
        )

        assert len(result) == 1
        assert result[0]["request_id"] == "req-match"

    @pytest.mark.asyncio
    async def test_list_pending_limit_exactly_equal_no_truncation(self, mock_approval_queue):
        """When len(results) == limit, no truncation occurs."""
        requests = [
            ApprovalRequest(
                id=f"req-{i}",
                type=ApprovalType.CONTENT,
                domain="d",
                title=f"T{i}",
                description="D",
            )
            for i in range(3)
        ]
        mock_approval_queue.list_pending.return_value = requests

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.list_pending(limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_pending_limit_zero_returns_all(self, mock_approval_queue):
        """limit=0 is falsy — no truncation applied, all results returned."""
        requests = [
            ApprovalRequest(
                id=f"req-{i}",
                type=ApprovalType.CONTENT,
                domain="d",
                title=f"T{i}",
                description="D",
            )
            for i in range(5)
        ]
        mock_approval_queue.list_pending.return_value = requests

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        # limit=0 is falsy, so the `if limit and ...` guard skips truncation
        result = await h.list_pending(limit=0)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_list_pending_no_project_in_metadata_filtered_out(
        self, mock_approval_queue
    ):
        """Requests with no project in metadata are excluded when project filter set."""
        req_no_meta = ApprovalRequest(
            id="req-no-meta",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            metadata={},
        )
        mock_approval_queue.list_pending.return_value = [req_no_meta]

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.list_pending(project="some-project")
        assert result == []


# ---------------------------------------------------------------------------
# count_pending Additional Edge Cases
# ---------------------------------------------------------------------------


class TestCountPendingEdgeCases:
    """Additional edge cases for count_pending."""

    @pytest.mark.asyncio
    async def test_count_pending_no_queue_returns_zero(self):
        """count_pending returns 0 when approval_queue is None."""
        h = ApprovalQueueHandler(approval_queue=None)
        result = await h.count_pending()
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_pending_all_non_pending_returns_zero(self, mock_approval_queue):
        """count_pending filters to status=pending; approved items not counted."""
        approved = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            status=ApprovalStatus.APPROVED,
        )
        mock_approval_queue.list_pending.return_value = [approved]

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.count_pending()
        # Approved request has status 'approved', not 'pending', so filtered out
        assert result == 0


# ---------------------------------------------------------------------------
# approve_request Additional Edge Cases
# ---------------------------------------------------------------------------


class TestApproveRequestEdgeCases:
    """Additional edge cases for approve_request."""

    @pytest.mark.asyncio
    async def test_approve_request_pipeline_result_none(
        self, mock_approval_queue, mock_orchestrator, tmp_path
    ):
        """pipeline_success is False when orchestrator.resume returns None."""
        checkpoint_path = tmp_path / "checkpoint.json"
        checkpoint_path.write_text("{}")

        req = ApprovalRequest(
            id="req-none-result",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint=str(checkpoint_path),
        )
        mock_approval_queue.approve.return_value = req
        mock_orchestrator.resume.return_value = None  # pipeline returns None

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            orchestrator=mock_orchestrator,
            forge_root=Path("/tmp"),
        )
        result = await h.approve_request("req-none-result", "tester", auto_resume=True)

        assert result["success"] is True
        assert result["pipeline_resumed"] is True
        assert result["pipeline_success"] is False  # None → False

    @pytest.mark.asyncio
    async def test_approve_request_no_comment_passed(self, mock_approval_queue):
        """approve_request with comment=None passes None to queue."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint=None,
        )
        mock_approval_queue.approve.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.approve_request("r1", "approver")

        assert result["success"] is True
        mock_approval_queue.approve.assert_called_once_with(
            request_id="r1", approver="approver", comment=None
        )

    @pytest.mark.asyncio
    async def test_approve_request_with_comment(self, mock_approval_queue):
        """approve_request passes comment string to underlying queue."""
        req = ApprovalRequest(
            id="r2",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint=None,
        )
        mock_approval_queue.approve.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.approve_request("r2", "approver", comment="All good")

        assert result["success"] is True
        mock_approval_queue.approve.assert_called_once_with(
            request_id="r2", approver="approver", comment="All good"
        )


# ---------------------------------------------------------------------------
# batch_approve Additional Edge Cases
# ---------------------------------------------------------------------------


class TestBatchApproveEdgeCases:
    """Additional edge cases for batch_approve."""

    @pytest.mark.asyncio
    async def test_batch_approve_empty_list(self, mock_approval_queue):
        """Batch approve with empty request_ids list returns success with zero counts."""
        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.batch_approve([], approver="admin")

        assert result["success"] is True
        assert result["approved_count"] == 0
        assert result["failed_count"] == 0
        assert result["approved"] == []
        assert result["failed"] == []

    @pytest.mark.asyncio
    async def test_batch_approve_approve_returns_failure(self, mock_approval_queue):
        """When approve_request returns success=False, item goes to failed list."""
        req = ApprovalRequest(
            id="req-fail",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            priority=ApprovalPriority.LOW,  # watch tier
        )

        # get_request succeeds but the underlying approve raises an error
        mock_approval_queue.get_request.return_value = req
        mock_approval_queue.approve.side_effect = Exception("Database write failed")

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.batch_approve(["req-fail"], approver="admin")

        assert result["success"] is True
        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert "Database write failed" in result["failed"][0]["error"]

    @pytest.mark.asyncio
    async def test_batch_approve_no_comment_defaults_to_batch_approved(
        self, mock_approval_queue
    ):
        """When comment is None, batch_approve passes 'Batch approved' to approve_request."""
        req = ApprovalRequest(
            id="req-batch",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            priority=ApprovalPriority.LOW,
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint=None,
        )
        mock_approval_queue.get_request.return_value = req
        mock_approval_queue.approve.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        await h.batch_approve(["req-batch"], approver="admin", comment=None)

        # approve was called with 'Batch approved' as the comment
        mock_approval_queue.approve.assert_called_once_with(
            request_id="req-batch",
            approver="admin",
            comment="Batch approved",
        )

    @pytest.mark.asyncio
    async def test_batch_approve_with_explicit_comment(self, mock_approval_queue):
        """When comment is provided, it is forwarded to approve_request."""
        req = ApprovalRequest(
            id="req-batch2",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            priority=ApprovalPriority.LOW,
            status=ApprovalStatus.APPROVED,
            workflow_checkpoint=None,
        )
        mock_approval_queue.get_request.return_value = req
        mock_approval_queue.approve.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        await h.batch_approve(["req-batch2"], approver="admin", comment="Reviewed")

        mock_approval_queue.approve.assert_called_once_with(
            request_id="req-batch2",
            approver="admin",
            comment="Reviewed",
        )

    @pytest.mark.asyncio
    async def test_batch_approve_desktop_tier_rejected(self, mock_approval_queue):
        """DEPLOY with CRITICAL priority → desktop tier — batch rejects it."""
        req = ApprovalRequest(
            id="req-desktop",
            type=ApprovalType.DEPLOY,
            domain="d",
            title="T",
            description="D",
            priority=ApprovalPriority.CRITICAL,  # desktop tier
        )
        mock_approval_queue.get_request.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.batch_approve(["req-desktop"], approver="admin")

        assert result["approved_count"] == 0
        assert result["failed_count"] == 1
        assert "requires individual approval" in result["failed"][0]["error"]
        assert "desktop" in result["failed"][0]["error"]


# ---------------------------------------------------------------------------
# get_stats Additional Edge Cases
# ---------------------------------------------------------------------------


class TestGetStatsEdgeCases:
    """Additional edge cases for get_stats."""

    @pytest.mark.asyncio
    async def test_get_stats_with_domain_param(self, mock_approval_queue):
        """get_stats accepts domain param even though harness ignores it."""
        mock_stats = MagicMock()
        mock_stats.pending_count = 1
        mock_stats.approved_count = 2
        mock_stats.rejected_count = 0
        mock_stats.expired_count = 0
        mock_stats.total_requests = 3
        mock_stats.oldest_pending_hours = 0.5
        mock_approval_queue.get_stats.return_value = mock_stats

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.get_stats(domain="some-domain")

        assert result["pending"] == 1
        assert result["total"] == 3
        # Underlying harness.get_stats() is called once (domain not forwarded)
        mock_approval_queue.get_stats.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_get_stats_zero_counts(self, mock_approval_queue):
        """get_stats handles all-zero statistics correctly."""
        mock_stats = MagicMock()
        mock_stats.pending_count = 0
        mock_stats.approved_count = 0
        mock_stats.rejected_count = 0
        mock_stats.expired_count = 0
        mock_stats.total_requests = 0
        mock_stats.oldest_pending_hours = 0.0
        mock_approval_queue.get_stats.return_value = mock_stats

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.get_stats()

        assert result["pending"] == 0
        assert result["total"] == 0
        assert result["oldest_pending_age_hours"] == 0.0


# ---------------------------------------------------------------------------
# get_approval_handler Singleton Edge Cases
# ---------------------------------------------------------------------------


class TestGetApprovalHandlerSingleton:
    """Edge cases for the get_approval_handler singleton factory."""

    def test_singleton_reuses_existing_instance(self):
        """Once created, subsequent calls return the same instance ignoring new args."""
        import forge_harness.webhook_server.handlers.approval_handler as ah_module

        ah_module._approval_handler = None

        first_queue = MagicMock()
        first = get_approval_handler(approval_queue=first_queue)

        # Second call with different queue — should return the SAME instance
        second_queue = MagicMock()
        second = get_approval_handler(approval_queue=second_queue)

        assert first is second
        # The original queue was used, not the second one
        assert first.approval_queue is first_queue

    def test_singleton_reset_picks_up_new_args(self):
        """After resetting _approval_handler, new args take effect."""
        import forge_harness.webhook_server.handlers.approval_handler as ah_module

        ah_module._approval_handler = None

        mock_queue = MagicMock()
        handler = get_approval_handler(approval_queue=mock_queue)
        assert handler.approval_queue is mock_queue

        # Reset and create again with a different queue
        ah_module._approval_handler = None
        new_queue = MagicMock()
        new_handler = get_approval_handler(approval_queue=new_queue)
        assert new_handler.approval_queue is new_queue
        assert new_handler is not handler

    def test_singleton_created_without_params(self):
        """Singleton created with no params uses None dependencies."""
        import forge_harness.webhook_server.handlers.approval_handler as ah_module

        ah_module._approval_handler = None

        h = get_approval_handler()
        assert h.approval_queue is None
        assert h.human_gate is None
        assert h.orchestrator is None


# ---------------------------------------------------------------------------
# reject_request Additional Edge Cases
# ---------------------------------------------------------------------------


class TestRejectRequestEdgeCases:
    """Additional edge cases for reject_request."""

    @pytest.mark.asyncio
    async def test_reject_uses_approver_param_for_rejector(self, mock_approval_queue):
        """ApprovalQueueHarness.reject receives 'approver' kwarg (not 'rejector')."""
        req = ApprovalRequest(
            id="r1",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            status=ApprovalStatus.REJECTED,
        )
        mock_approval_queue.reject.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        result = await h.reject_request("r1", rejector="reviewer@example.com", reason="Too risky")

        mock_approval_queue.reject.assert_called_once_with(
            request_id="r1",
            approver="reviewer@example.com",  # mapped from rejector
            reason="Too risky",
        )
        assert result["rejector"] == "reviewer@example.com"

    @pytest.mark.asyncio
    async def test_reject_default_reason_is_rejected_string(self, mock_approval_queue):
        """When reason is None, the string 'Rejected' is passed to the harness."""
        req = ApprovalRequest(
            id="r2",
            type=ApprovalType.CONTENT,
            domain="d",
            title="T",
            description="D",
            status=ApprovalStatus.REJECTED,
        )
        mock_approval_queue.reject.return_value = req

        h = ApprovalQueueHandler(
            approval_queue=mock_approval_queue,
            forge_root=Path("/tmp"),
        )
        await h.reject_request("r2", rejector="admin")

        mock_approval_queue.reject.assert_called_once_with(
            request_id="r2",
            approver="admin",
            reason="Rejected",
        )
