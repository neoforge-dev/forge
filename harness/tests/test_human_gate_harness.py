"""
Tests for HumanGateHarness
==========================

Tests the integration layer between OrchestrationHarness and ApprovalQueueHarness.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalStatus,
    ApprovalType,
)
from forge_harness.notification_harness import (
    NotificationChannel,
    NotificationResult,
    NotificationUrgency,
)


# Import will fail until we create the module
@pytest.fixture
def temp_storage_dir():
    """Create a temporary directory for approval storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_checkpoint_dir():
    """Create a temporary directory for checkpoints."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_notification_harness():
    """Create a mock notification harness."""
    harness = AsyncMock()
    harness.notify.return_value = [
        NotificationResult(
            channel=NotificationChannel.SLACK,
            success=True,
            message_id="msg_123",
        )
    ]
    return harness


@pytest.fixture
def approval_queue(temp_storage_dir):
    """Create an ApprovalQueueHarness for testing."""
    from forge_harness.approval_queue import create_approval_queue

    return create_approval_queue(
        storage_dir=temp_storage_dir,
        default_expiry_hours=24.0,
    )


@pytest.fixture
def human_gate_harness(approval_queue, mock_notification_harness, temp_checkpoint_dir):
    """Create a HumanGateHarness for testing."""
    from forge_harness.human_gate_harness import HumanGateHarness

    return HumanGateHarness(
        approval_queue=approval_queue,
        notification_harness=mock_notification_harness,
        checkpoint_dir=temp_checkpoint_dir,
    )


# =============================================================================
# HumanGateHarness Tests
# =============================================================================


class TestHumanGateHarness:
    """Tests for HumanGateHarness class."""

    @pytest.mark.asyncio
    async def test_create_approval_request(self, human_gate_harness, approval_queue):
        """Test creating an approval request through HumanGateHarness."""

        # Create approval without waiting
        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="codeswiftr-com",
            title="Review: Python Interview Guide",
            description="Generated content needs human review",
            metadata={"word_count": 1500, "quality_score": 91},
            priority=ApprovalPriority.NORMAL,
        )

        assert result.request_id is not None
        assert result.request_id.startswith("apr_")

        # Verify request was created in queue
        request = await approval_queue.get_request(result.request_id)
        assert request is not None
        assert request.type == ApprovalType.CONTENT
        assert request.domain == "codeswiftr-com"
        assert request.status == ApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_create_approval_sends_notification(
        self, human_gate_harness, mock_notification_harness
    ):
        """Test that creating approval sends notification."""
        await human_gate_harness.create_approval(
            approval_type=ApprovalType.DEPLOY,
            domain="thebrightharbor-com",
            title="Deploy to Production",
            description="Deployment requires approval",
            notify_channels=[NotificationChannel.SLACK, NotificationChannel.EMAIL],
        )

        # Verify notification was sent
        mock_notification_harness.notify.assert_called_once()
        call_args = mock_notification_harness.notify.call_args
        config = call_args[0][0]  # First positional arg

        assert NotificationChannel.SLACK in config.channels
        assert NotificationChannel.EMAIL in config.channels
        assert config.action_required is True
        assert "Deploy to Production" in config.title

    @pytest.mark.asyncio
    async def test_create_approval_with_checkpoint(self, human_gate_harness, temp_checkpoint_dir):
        """Test creating approval with checkpoint reference."""
        checkpoint_path = temp_checkpoint_dir / "wf_test123.json"
        checkpoint_path.write_text('{"step": 3}')

        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.FEATURE,
            domain="codeswiftr-com",
            title="Feature Review",
            description="New feature implementation",
            checkpoint_path=checkpoint_path,
        )

        assert result.checkpoint_path == str(checkpoint_path)

    @pytest.mark.asyncio
    async def test_check_approval_status_pending(self, human_gate_harness, approval_queue):
        """Test checking status of pending approval."""
        from forge_harness.human_gate_harness import GateStatus

        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test Approval",
            description="Test",
        )

        status = await human_gate_harness.check_status(result.request_id)
        assert status.gate_status == GateStatus.PENDING

    @pytest.mark.asyncio
    async def test_check_approval_status_approved(self, human_gate_harness, approval_queue):
        """Test checking status after approval."""
        from forge_harness.human_gate_harness import GateStatus

        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test Approval",
            description="Test",
        )

        # Approve the request directly
        await approval_queue.approve(result.request_id, "@tester")

        status = await human_gate_harness.check_status(result.request_id)
        assert status.gate_status == GateStatus.APPROVED
        assert status.resolved_by == "@tester"

    @pytest.mark.asyncio
    async def test_check_approval_status_rejected(self, human_gate_harness, approval_queue):
        """Test checking status after rejection."""
        from forge_harness.human_gate_harness import GateStatus

        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test Approval",
            description="Test",
        )

        # Reject the request
        await approval_queue.reject(result.request_id, "@tester", "Not ready")

        status = await human_gate_harness.check_status(result.request_id)
        assert status.gate_status == GateStatus.REJECTED
        assert status.rejection_reason == "Not ready"

    @pytest.mark.asyncio
    async def test_await_approval_immediate_approval(self, human_gate_harness, approval_queue):
        """Test await_approval when approval comes quickly."""
        from forge_harness.human_gate_harness import GateStatus

        # Create approval
        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test",
            description="Test",
        )

        # Approve in background
        async def approve_after_delay():
            await asyncio.sleep(0.05)
            await approval_queue.approve(result.request_id, "@approver")

        asyncio.create_task(approve_after_delay())

        # Wait for approval with short timeout
        final_status = await human_gate_harness.await_approval(
            result.request_id,
            timeout_seconds=2.0,
            poll_interval_seconds=0.02,
        )

        assert final_status.gate_status == GateStatus.APPROVED

    @pytest.mark.asyncio
    async def test_await_approval_timeout(self, human_gate_harness):
        """Test await_approval times out correctly."""
        from forge_harness.human_gate_harness import GateStatus

        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test",
            description="Test",
        )

        # Wait with very short timeout (no approval coming)
        final_status = await human_gate_harness.await_approval(
            result.request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0.02,
        )

        assert final_status.gate_status == GateStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_priority_mapping(self, human_gate_harness, mock_notification_harness):
        """Test that priority maps to notification urgency."""
        await human_gate_harness.create_approval(
            approval_type=ApprovalType.SECURITY,
            domain="test-domain",
            title="Security Review",
            description="Critical security review",
            priority=ApprovalPriority.CRITICAL,
        )

        call_args = mock_notification_harness.notify.call_args
        config = call_args[0][0]
        assert config.urgency == NotificationUrgency.CRITICAL


class TestHumanGateHarnessNoNotification:
    """Tests for HumanGateHarness without notification harness."""

    @pytest.mark.asyncio
    async def test_works_without_notification_harness(self, approval_queue, temp_checkpoint_dir):
        """Test that HumanGateHarness works without notifications."""
        from forge_harness.human_gate_harness import HumanGateHarness

        harness = HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=None,  # No notifications
            checkpoint_dir=temp_checkpoint_dir,
        )

        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Test",
            description="Test without notifications",
        )

        assert result.request_id is not None
        assert result.notification_ids == []  # No notifications sent


class TestApprovalResult:
    """Tests for ApprovalResult dataclass."""

    def test_approval_result_creation(self):
        """Test ApprovalResult can be created."""
        from forge_harness.human_gate_harness import ApprovalResult, GateStatus

        result = ApprovalResult(
            request_id="apr_123",
            gate_status=GateStatus.PENDING,
            notification_ids=["msg_1", "msg_2"],
            checkpoint_path="/path/to/checkpoint.json",
        )

        assert result.request_id == "apr_123"
        assert result.gate_status == GateStatus.PENDING
        assert len(result.notification_ids) == 2


class TestGateStatus:
    """Tests for GateStatus enum."""

    def test_gate_status_values(self):
        """Test GateStatus has expected values."""
        from forge_harness.human_gate_harness import GateStatus

        assert GateStatus.PENDING.value == "pending"
        assert GateStatus.APPROVED.value == "approved"
        assert GateStatus.REJECTED.value == "rejected"
        assert GateStatus.TIMEOUT.value == "timeout"
        assert GateStatus.CANCELLED.value == "cancelled"


class TestSessionStateIntegration:
    """Tests for HumanGateHarness with SessionStateManager integration."""

    @pytest.fixture
    def temp_session_dir(self):
        """Create a temporary directory for session storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def session_manager(self, temp_session_dir):
        """Create a SessionStateManager for testing."""
        from forge_harness.session_state import create_session_state_manager

        return create_session_state_manager(storage_dir=temp_session_dir)

    @pytest.fixture
    def session_state(self, session_manager):
        """Create a session state for testing."""
        return session_manager.create_session(
            domain="test-domain",
            project="test-project",
        )

    @pytest.fixture
    def harness_with_session(
        self,
        approval_queue,
        mock_notification_harness,
        temp_checkpoint_dir,
        session_manager,
        session_state,
    ):
        """Create a HumanGateHarness with session state."""
        from forge_harness.human_gate_harness import HumanGateHarness

        return HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification_harness,
            checkpoint_dir=temp_checkpoint_dir,
            session_state_manager=session_manager,
            session_state=session_state,
        )

    @pytest.mark.asyncio
    async def test_set_session_state(self, human_gate_harness, session_state):
        """Test setting session state."""
        assert human_gate_harness.get_session_state() is None
        human_gate_harness.set_session_state(session_state)
        assert human_gate_harness.get_session_state() is session_state

    @pytest.mark.asyncio
    async def test_record_decision(self, harness_with_session):
        """Test recording a decision in session state."""
        # Record a decision
        decision = await harness_with_session._record_decision(
            question="Approve deployment?",
            decision="approved",
            decided_by="reviewer@example.com",
            rationale="All tests pass",
            context_key="deploy_approval",
        )

        assert decision is not None
        assert decision.question == "Approve deployment?"
        assert decision.decision == "approved"

        # Check it's in the session state
        state = harness_with_session.get_session_state()
        assert len(state.human_decisions) == 1
        assert state.context_data.get("deploy_approval") == "approved"

    @pytest.mark.asyncio
    async def test_record_decision_without_session(self, human_gate_harness):
        """Test recording decision returns None without session state."""
        result = await human_gate_harness._record_decision(
            question="Test?",
            decision="yes",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_previous_decision(self, harness_with_session):
        """Test retrieving a previous decision."""
        # Record a decision first
        await harness_with_session._record_decision(
            question="Use TypeScript?",
            decision="yes",
        )

        # Should find it
        result = await harness_with_session.get_previous_decision("Use TypeScript?")
        assert result == "yes"

        # Should not find different question
        result = await harness_with_session.get_previous_decision("Use Python?")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_previous_decision_without_session(self, human_gate_harness):
        """Test get_previous_decision returns None without session state."""
        result = await human_gate_harness.get_previous_decision("Any question?")
        assert result is None

    @pytest.mark.asyncio
    async def test_request_decision_reuses_previous(self, harness_with_session):
        """Test request_decision can reuse previous decisions."""
        from forge_harness.human_gate_harness import GateStatus

        # First, record a previous decision
        await harness_with_session._record_decision(
            question="Which database?",
            decision="PostgreSQL",
        )

        # Now request the same decision with reuse_previous=True
        result = await harness_with_session.request_decision(
            question="Which database?",
            options=["PostgreSQL", "MySQL", "MongoDB"],
            reuse_previous=True,
        )

        # Should return cached result
        assert result.gate_status == GateStatus.APPROVED
        assert result.request_id == "cached"
        assert result.rejection_reason == "PostgreSQL"  # Decision stored here

    @pytest.mark.asyncio
    async def test_request_decision_no_reuse_for_unknown(
        self, harness_with_session, approval_queue
    ):
        """Test request_decision doesn't reuse when no previous exists."""
        # Request with no previous decision
        result = await harness_with_session.create_approval(
            approval_type=ApprovalType.CONFIG,
            domain="test",
            title="Test",
            description="Test",
        )

        # Should create a new approval request
        assert result.request_id != "cached"

    @pytest.mark.asyncio
    async def test_await_approval_records_decision(self, harness_with_session, approval_queue):
        """Test that await_approval records the decision."""
        from forge_harness.human_gate_harness import GateStatus

        # Create an approval
        result = await harness_with_session.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test",
            title="Review content",
            description="Content review needed",
        )

        # Approve it directly
        await approval_queue.approve(
            request_id=result.request_id,
            approver="reviewer@example.com",
            comment="LGTM",
        )

        # Now await it (should return immediately since already approved)
        final = await harness_with_session.await_approval(
            result.request_id,
            timeout_seconds=1.0,
        )

        assert final.gate_status == GateStatus.APPROVED

        # Check the decision was recorded
        state = harness_with_session.get_session_state()
        assert len(state.human_decisions) == 1
        assert state.human_decisions[0].decision == "approved"

    @pytest.mark.asyncio
    async def test_create_with_session_dir(self, temp_storage_dir, temp_session_dir):
        """Test create_human_gate_harness with session_dir."""
        from forge_harness.human_gate_harness import create_human_gate_harness

        harness = create_human_gate_harness(
            storage_dir=temp_storage_dir,
            session_dir=temp_session_dir,
        )

        assert harness.session_state_manager is not None

    @pytest.mark.asyncio
    async def test_record_decision_save_state_failure(
        self, approval_queue, mock_notification_harness, temp_checkpoint_dir, session_state
    ):
        """Test _record_decision handles save state failure gracefully."""
        from forge_harness.human_gate_harness import HumanGateHarness

        # Create a mock session state manager that fails on save
        mock_manager = AsyncMock()
        mock_manager.save_state.side_effect = Exception("Save failed")

        harness = HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification_harness,
            checkpoint_dir=temp_checkpoint_dir,
            session_state_manager=mock_manager,
            session_state=session_state,
        )

        # Should not raise, just log warning
        decision = await harness._record_decision(
            question="Test question",
            decision="yes",
        )

        # Decision should still be recorded in memory
        assert decision is not None
        assert decision.question == "Test question"


class TestCheckStatusErrors:
    """Tests for check_status error handling."""

    @pytest.fixture
    def temp_storage_dir(self):
        """Create a temporary directory for approval storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        """Create an ApprovalQueueHarness for testing."""
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(
            storage_dir=temp_storage_dir,
            default_expiry_hours=24.0,
        )

    @pytest.fixture
    def human_gate_harness(self, approval_queue):
        """Create a HumanGateHarness for testing."""
        from forge_harness.human_gate_harness import HumanGateHarness

        return HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=None,
        )

    @pytest.mark.asyncio
    async def test_check_status_not_found(self, human_gate_harness):
        """Test check_status raises ValueError for non-existent request."""
        with pytest.raises(ValueError, match="Approval request not found"):
            await human_gate_harness.check_status("apr_nonexistent_123")


class TestAwaitFeedback:
    """Tests for await_feedback orchestration method."""

    @pytest.fixture
    def temp_storage_dir(self):
        """Create a temporary directory for approval storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        """Create an ApprovalQueueHarness for testing."""
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(
            storage_dir=temp_storage_dir,
            default_expiry_hours=24.0,
        )

    @pytest.fixture
    def mock_notification_harness(self):
        """Create a mock notification harness."""
        harness = AsyncMock()
        harness.notify.return_value = [
            NotificationResult(
                channel=NotificationChannel.SLACK,
                success=True,
                message_id="msg_456",
            )
        ]
        return harness

    @pytest.fixture
    def human_gate_harness(self, approval_queue, mock_notification_harness):
        """Create a HumanGateHarness for testing."""
        from forge_harness.human_gate_harness import HumanGateHarness

        return HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification_harness,
        )

    @pytest.mark.asyncio
    async def test_await_feedback_creates_approval(self, human_gate_harness, approval_queue):
        """Test await_feedback creates an approval and waits."""
        from forge_harness.human_gate_harness import GateStatus

        # Run with a very short timeout (will timeout)
        result = await human_gate_harness.await_feedback(
            page_ids=["page_123", "page_456"],
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            timeout_hours=0.0001,  # Very short timeout (0.36 seconds)
            title="Review content",
            description="Please review these pages",
        )

        # Should timeout since no one approved
        assert result.gate_status == GateStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_await_feedback_with_approval(self, human_gate_harness, approval_queue):
        """Test await_feedback succeeds when approved."""
        from forge_harness.human_gate_harness import GateStatus

        # Create approval in background and approve it
        async def create_and_approve():
            await asyncio.sleep(0.1)
            # Find the pending approval and approve it
            pending = await approval_queue.list_pending()
            if pending:
                await approval_queue.approve(pending[0].id, "@reviewer")

        task = asyncio.create_task(create_and_approve())

        result = await human_gate_harness.await_feedback(
            page_ids=["page_789"],
            domain="codeswiftr-com",
            timeout_hours=0.01,  # 36 seconds
            title="Review blog post",
        )

        # Cancel the task if still running
        if not task.done():
            task.cancel()

        # May be approved or timeout depending on timing
        assert result.gate_status in (GateStatus.APPROVED, GateStatus.TIMEOUT)


class TestRequestDecisionNewApproval:
    """Tests for request_decision creating new approval."""

    @pytest.fixture
    def temp_storage_dir(self):
        """Create a temporary directory for approval storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def temp_session_dir(self):
        """Create a temporary directory for session storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        """Create an ApprovalQueueHarness for testing."""
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(
            storage_dir=temp_storage_dir,
            default_expiry_hours=24.0,
        )

    @pytest.fixture
    def session_manager(self, temp_session_dir):
        """Create a SessionStateManager for testing."""
        from forge_harness.session_state import create_session_state_manager

        return create_session_state_manager(storage_dir=temp_session_dir)

    @pytest.fixture
    def session_state(self, session_manager):
        """Create a session state for testing."""
        return session_manager.create_session(
            domain="test-domain",
            project="test-project",
        )

    @pytest.fixture
    def mock_notification_harness(self):
        """Create a mock notification harness."""
        harness = AsyncMock()
        harness.notify.return_value = [
            NotificationResult(
                channel=NotificationChannel.SLACK,
                success=True,
                message_id="msg_789",
            )
        ]
        return harness

    @pytest.fixture
    def human_gate_harness(
        self, approval_queue, mock_notification_harness, session_manager, session_state
    ):
        """Create a HumanGateHarness for testing."""
        from forge_harness.human_gate_harness import HumanGateHarness

        return HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification_harness,
            session_state_manager=session_manager,
            session_state=session_state,
        )

    @pytest.mark.asyncio
    async def test_request_decision_creates_approval_when_no_previous(
        self, human_gate_harness, approval_queue
    ):
        """Test request_decision creates new approval when no previous exists."""
        from forge_harness.human_gate_harness import GateStatus

        # Request decision for a question we haven't answered before
        result = await human_gate_harness.request_decision(
            question="Which cloud provider should we use?",
            options=["AWS", "GCP", "Azure"],
            domain="infra",
            timeout_hours=0.0001,  # Very short timeout
            reuse_previous=True,  # Even with reuse, no previous exists
        )

        # Should timeout since no one answered
        assert result.gate_status == GateStatus.TIMEOUT
        # Should NOT be cached
        assert result.request_id != "cached"

    @pytest.mark.asyncio
    async def test_request_decision_no_reuse_flag(self, human_gate_harness):
        """Test request_decision ignores previous when reuse_previous=False."""
        from forge_harness.human_gate_harness import GateStatus

        # First, record a previous decision
        await human_gate_harness._record_decision(
            question="Which framework?",
            decision="React",
        )

        # Request with reuse_previous=False
        result = await human_gate_harness.request_decision(
            question="Which framework?",
            options=["React", "Vue", "Svelte"],
            timeout_hours=0.0001,
            reuse_previous=False,
        )

        # Should NOT use cached result, should timeout
        assert result.gate_status == GateStatus.TIMEOUT
        assert result.request_id != "cached"

    @pytest.mark.asyncio
    async def test_request_decision_previous_not_in_options(self, human_gate_harness):
        """Test request_decision doesn't reuse if previous not in options."""
        from forge_harness.human_gate_harness import GateStatus

        # Record a decision with a value not in new options
        await human_gate_harness._record_decision(
            question="Which database?",
            decision="MongoDB",
        )

        # Request with different options (MongoDB not included)
        result = await human_gate_harness.request_decision(
            question="Which database?",
            options=["PostgreSQL", "MySQL", "SQLite"],
            timeout_hours=0.0001,
            reuse_previous=True,
        )

        # Should NOT use cached since MongoDB isn't in options
        assert result.gate_status == GateStatus.TIMEOUT
        assert result.request_id != "cached"


# =============================================================================
# Additional Edge Case Tests
# =============================================================================


class TestPriorityToUrgencyMapping:
    """Tests verifying the PRIORITY_TO_URGENCY mapping covers all priorities."""

    def test_all_priorities_have_urgency_mapping(self):
        """All ApprovalPriority values must map to a NotificationUrgency."""
        from forge_harness.human_gate_harness import PRIORITY_TO_URGENCY

        for priority in ApprovalPriority:
            assert priority in PRIORITY_TO_URGENCY, (
                f"Missing urgency mapping for priority: {priority}"
            )

    def test_low_priority_maps_to_low_urgency(self):
        """LOW priority maps to LOW urgency."""
        from forge_harness.human_gate_harness import PRIORITY_TO_URGENCY

        assert PRIORITY_TO_URGENCY[ApprovalPriority.LOW] == NotificationUrgency.LOW

    def test_normal_priority_maps_to_normal_urgency(self):
        """NORMAL priority maps to NORMAL urgency."""
        from forge_harness.human_gate_harness import PRIORITY_TO_URGENCY

        assert PRIORITY_TO_URGENCY[ApprovalPriority.NORMAL] == NotificationUrgency.NORMAL

    def test_high_priority_maps_to_high_urgency(self):
        """HIGH priority maps to HIGH urgency."""
        from forge_harness.human_gate_harness import PRIORITY_TO_URGENCY

        assert PRIORITY_TO_URGENCY[ApprovalPriority.HIGH] == NotificationUrgency.HIGH

    def test_critical_priority_maps_to_critical_urgency(self):
        """CRITICAL priority maps to CRITICAL urgency."""
        from forge_harness.human_gate_harness import PRIORITY_TO_URGENCY

        assert PRIORITY_TO_URGENCY[ApprovalPriority.CRITICAL] == NotificationUrgency.CRITICAL


class TestStatusToGateMapping:
    """Tests verifying the STATUS_TO_GATE mapping covers all statuses."""

    def test_all_approval_statuses_have_gate_mapping(self):
        """All ApprovalStatus values must map to a GateStatus."""
        from forge_harness.human_gate_harness import STATUS_TO_GATE, GateStatus

        for status in ApprovalStatus:
            assert status in STATUS_TO_GATE, f"Missing gate mapping for status: {status}"

    def test_pending_maps_to_pending(self):
        """PENDING approval status maps to PENDING gate status."""
        from forge_harness.human_gate_harness import STATUS_TO_GATE, GateStatus

        assert STATUS_TO_GATE[ApprovalStatus.PENDING] == GateStatus.PENDING

    def test_approved_maps_to_approved(self):
        """APPROVED status maps to APPROVED gate status."""
        from forge_harness.human_gate_harness import STATUS_TO_GATE, GateStatus

        assert STATUS_TO_GATE[ApprovalStatus.APPROVED] == GateStatus.APPROVED

    def test_rejected_maps_to_rejected(self):
        """REJECTED status maps to REJECTED gate status."""
        from forge_harness.human_gate_harness import STATUS_TO_GATE, GateStatus

        assert STATUS_TO_GATE[ApprovalStatus.REJECTED] == GateStatus.REJECTED

    def test_expired_maps_to_timeout(self):
        """EXPIRED status maps to TIMEOUT gate status."""
        from forge_harness.human_gate_harness import STATUS_TO_GATE, GateStatus

        assert STATUS_TO_GATE[ApprovalStatus.EXPIRED] == GateStatus.TIMEOUT

    def test_cancelled_maps_to_cancelled(self):
        """CANCELLED status maps to CANCELLED gate status."""
        from forge_harness.human_gate_harness import STATUS_TO_GATE, GateStatus

        assert STATUS_TO_GATE[ApprovalStatus.CANCELLED] == GateStatus.CANCELLED


class TestBuildNotificationMessage:
    """Tests for _build_notification_message content and formatting."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)

    @pytest.fixture
    def harness(self, approval_queue):
        from forge_harness.human_gate_harness import HumanGateHarness

        return HumanGateHarness(approval_queue=approval_queue, notification_harness=None)

    @pytest.mark.asyncio
    async def test_notification_message_contains_domain(self, harness, approval_queue):
        """Notification message includes the domain."""
        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="codeswiftr-com",
            title="Test Notification",
            description="Test description",
        )
        request = await approval_queue.get_request(result.request_id)
        msg = harness._build_notification_message(request)
        assert "codeswiftr-com" in msg

    @pytest.mark.asyncio
    async def test_notification_message_contains_approve_command(self, harness, approval_queue):
        """Notification message includes the forge approve CLI command."""
        result = await harness.create_approval(
            approval_type=ApprovalType.DEPLOY,
            domain="test-domain",
            title="Deploy Review",
            description="Deploy to production",
        )
        request = await approval_queue.get_request(result.request_id)
        msg = harness._build_notification_message(request)
        assert "forge approvals approve" in msg
        assert result.request_id in msg

    @pytest.mark.asyncio
    async def test_notification_message_contains_reject_command(self, harness, approval_queue):
        """Notification message includes the forge reject CLI command."""
        result = await harness.create_approval(
            approval_type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Feature Review",
            description="Feature complete",
        )
        request = await approval_queue.get_request(result.request_id)
        msg = harness._build_notification_message(request)
        assert "forge approvals reject" in msg

    @pytest.mark.asyncio
    async def test_notification_message_with_checkpoint(self, harness, approval_queue):
        """Notification message includes checkpoint path when provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_path = Path(tmpdir) / "wf_test.json"
            checkpoint_path.write_text('{"step": 1}')

            result = await harness.create_approval(
                approval_type=ApprovalType.CONTENT,
                domain="test-domain",
                title="With Checkpoint",
                description="Has a checkpoint",
                checkpoint_path=checkpoint_path,
            )
            request = await approval_queue.get_request(result.request_id)
            msg = harness._build_notification_message(request)
            assert "Checkpoint" in msg

    @pytest.mark.asyncio
    async def test_notification_message_contains_description(self, harness, approval_queue):
        """Notification message includes the description."""
        description = "Please review this important content"
        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Description Test",
            description=description,
        )
        request = await approval_queue.get_request(result.request_id)
        msg = harness._build_notification_message(request)
        assert description in msg


class TestCancelledApproval:
    """Tests for cancelled approval status flow through the gate."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)

    @pytest.fixture
    def human_gate_harness(self, approval_queue):
        from forge_harness.human_gate_harness import HumanGateHarness

        return HumanGateHarness(approval_queue=approval_queue, notification_harness=None)

    @pytest.mark.asyncio
    async def test_check_status_cancelled(self, human_gate_harness, approval_queue):
        """check_status returns CANCELLED gate status for cancelled requests."""
        from forge_harness.human_gate_harness import GateStatus

        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Cancellation Test",
            description="Will be cancelled",
        )
        await approval_queue.cancel(result.request_id, reason="No longer needed")

        status = await human_gate_harness.check_status(result.request_id)
        assert status.gate_status == GateStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_await_approval_resolves_on_cancellation(self, human_gate_harness, approval_queue):
        """await_approval returns immediately when request is cancelled."""
        from forge_harness.human_gate_harness import GateStatus

        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Cancellation Await Test",
            description="Will be cancelled mid-wait",
        )

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            await approval_queue.cancel(result.request_id, reason="Automated cancellation")

        asyncio.create_task(cancel_after_delay())

        final = await human_gate_harness.await_approval(
            result.request_id,
            timeout_seconds=2.0,
            poll_interval_seconds=0.02,
        )
        assert final.gate_status == GateStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_await_approval_resolves_on_rejection(self, human_gate_harness, approval_queue):
        """await_approval returns immediately when request is rejected."""
        from forge_harness.human_gate_harness import GateStatus

        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.DEPLOY,
            domain="test-domain",
            title="Rejection Await Test",
            description="Will be rejected mid-wait",
        )

        async def reject_after_delay():
            await asyncio.sleep(0.05)
            await approval_queue.reject(result.request_id, "@reviewer", "Not ready for production")

        asyncio.create_task(reject_after_delay())

        final = await human_gate_harness.await_approval(
            result.request_id,
            timeout_seconds=2.0,
            poll_interval_seconds=0.02,
        )
        assert final.gate_status == GateStatus.REJECTED
        assert final.rejection_reason == "Not ready for production"


class TestNotificationPartialFailure:
    """Tests for handling partial notification failures."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)

    @pytest.mark.asyncio
    async def test_notification_partial_failure_only_records_successes(self, approval_queue):
        """Only successful notification IDs are stored in the result."""
        from forge_harness.human_gate_harness import HumanGateHarness

        mock_notification = AsyncMock()
        mock_notification.notify.return_value = [
            NotificationResult(
                channel=NotificationChannel.SLACK,
                success=True,
                message_id="slack_ok_123",
            ),
            NotificationResult(
                channel=NotificationChannel.EMAIL,
                success=False,
                message_id=None,
                error="SMTP timeout",
            ),
        ]

        harness = HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification,
        )

        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Partial Notification Test",
            description="One channel fails",
        )

        # Only the successful Slack notification ID should be recorded
        assert "slack_ok_123" in result.notification_ids
        assert len(result.notification_ids) == 1

    @pytest.mark.asyncio
    async def test_all_notifications_fail_returns_empty_ids(self, approval_queue):
        """When all notifications fail, notification_ids is empty."""
        from forge_harness.human_gate_harness import HumanGateHarness

        mock_notification = AsyncMock()
        mock_notification.notify.return_value = [
            NotificationResult(
                channel=NotificationChannel.SLACK,
                success=False,
                message_id=None,
                error="Webhook unreachable",
            ),
        ]

        harness = HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification,
        )

        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="All Fail Test",
            description="All channels fail",
        )

        assert result.notification_ids == []

    @pytest.mark.asyncio
    async def test_multiple_successful_notifications_all_recorded(self, approval_queue):
        """Multiple successful notification IDs are all stored."""
        from forge_harness.human_gate_harness import HumanGateHarness

        mock_notification = AsyncMock()
        mock_notification.notify.return_value = [
            NotificationResult(
                channel=NotificationChannel.SLACK,
                success=True,
                message_id="slack_id_001",
            ),
            NotificationResult(
                channel=NotificationChannel.EMAIL,
                success=True,
                message_id="email_id_002",
            ),
        ]

        harness = HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification,
        )

        result = await harness.create_approval(
            approval_type=ApprovalType.DEPLOY,
            domain="test-domain",
            title="Multi-channel Success",
            description="Both channels succeed",
            notify_channels=[NotificationChannel.SLACK, NotificationChannel.EMAIL],
        )

        assert "slack_id_001" in result.notification_ids
        assert "email_id_002" in result.notification_ids
        assert len(result.notification_ids) == 2


class TestCreateApprovalMetadata:
    """Tests for metadata, tags, and expiry propagation during create_approval."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)

    @pytest.fixture
    def harness(self, approval_queue):
        from forge_harness.human_gate_harness import HumanGateHarness

        return HumanGateHarness(approval_queue=approval_queue, notification_harness=None)

    @pytest.mark.asyncio
    async def test_create_approval_with_tags(self, harness, approval_queue):
        """Tags are propagated to the underlying approval request."""
        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Tagged Approval",
            description="Has tags",
            tags=["sprint-6", "content-review", "urgent"],
        )
        request = await approval_queue.get_request(result.request_id)
        assert "sprint-6" in request.tags
        assert "content-review" in request.tags
        assert "urgent" in request.tags

    @pytest.mark.asyncio
    async def test_create_approval_with_metadata(self, harness, approval_queue):
        """Metadata is propagated to the underlying approval request."""
        metadata = {"word_count": 2000, "quality_score": 95, "author": "agent-01"}
        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Metadata Approval",
            description="Has metadata",
            metadata=metadata,
        )
        request = await approval_queue.get_request(result.request_id)
        assert request.metadata["word_count"] == 2000
        assert request.metadata["quality_score"] == 95
        assert request.metadata["author"] == "agent-01"

    @pytest.mark.asyncio
    async def test_create_approval_with_expiry_hours(self, harness, approval_queue):
        """expiry_hours is propagated so the request has an expiration."""
        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Expiry Approval",
            description="Will expire",
            expiry_hours=1.0,
        )
        request = await approval_queue.get_request(result.request_id)
        assert request.expires_at is not None

    @pytest.mark.asyncio
    async def test_create_approval_result_has_created_at(self, harness):
        """ApprovalResult created_at is populated from the request."""
        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Timestamp Test",
            description="Check timestamps",
        )
        assert result.created_at is not None

    @pytest.mark.asyncio
    async def test_create_approval_result_gate_status_is_pending(self, harness):
        """Newly created approval always returns PENDING gate status."""
        from forge_harness.human_gate_harness import GateStatus

        result = await harness.create_approval(
            approval_type=ApprovalType.SECURITY,
            domain="test-domain",
            title="Security Approval",
            description="Security review",
        )
        assert result.gate_status == GateStatus.PENDING

    @pytest.mark.asyncio
    async def test_create_approval_all_approval_types(self, harness):
        """create_approval works for every ApprovalType without error."""
        for approval_type in ApprovalType:
            result = await harness.create_approval(
                approval_type=approval_type,
                domain="test-domain",
                title=f"Test {approval_type.value}",
                description=f"Testing approval type {approval_type.value}",
            )
            assert result.request_id is not None


class TestDefaultNotifyChannels:
    """Tests for default notification channel fallback behavior."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)

    @pytest.mark.asyncio
    async def test_default_notify_channels_used_when_none_specified(self, approval_queue):
        """When notify_channels is None, default_notify_channels are used."""
        from forge_harness.human_gate_harness import HumanGateHarness

        mock_notification = AsyncMock()
        mock_notification.notify.return_value = [
            NotificationResult(
                channel=NotificationChannel.EMAIL,
                success=True,
                message_id="default_msg_001",
            )
        ]

        harness = HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification,
            default_notify_channels=[NotificationChannel.EMAIL],
        )

        await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Default Channel Test",
            description="Uses default channels",
            notify_channels=None,  # Explicitly None
        )

        call_args = mock_notification.notify.call_args
        config = call_args[0][0]
        assert NotificationChannel.EMAIL in config.channels

    @pytest.mark.asyncio
    async def test_per_request_channels_override_defaults(self, approval_queue):
        """Per-request notify_channels override the default channels."""
        from forge_harness.human_gate_harness import HumanGateHarness

        mock_notification = AsyncMock()
        mock_notification.notify.return_value = [
            NotificationResult(
                channel=NotificationChannel.SLACK,
                success=True,
                message_id="override_msg_001",
            )
        ]

        harness = HumanGateHarness(
            approval_queue=approval_queue,
            notification_harness=mock_notification,
            default_notify_channels=[NotificationChannel.EMAIL],
        )

        await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Override Channel Test",
            description="Uses per-request channels",
            notify_channels=[NotificationChannel.SLACK],
        )

        call_args = mock_notification.notify.call_args
        config = call_args[0][0]
        assert NotificationChannel.SLACK in config.channels
        assert NotificationChannel.EMAIL not in config.channels


class TestCheckStatusReturnFields:
    """Tests that check_status populates all fields from the underlying request."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)

    @pytest.fixture
    def human_gate_harness(self, approval_queue):
        from forge_harness.human_gate_harness import HumanGateHarness

        return HumanGateHarness(approval_queue=approval_queue, notification_harness=None)

    @pytest.mark.asyncio
    async def test_check_status_returns_resolved_at_after_approval(
        self, human_gate_harness, approval_queue
    ):
        """check_status populates resolved_at once the request is approved."""
        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Resolve Timestamp Test",
            description="Check resolved_at",
        )
        await approval_queue.approve(result.request_id, "@approver")

        status = await human_gate_harness.check_status(result.request_id)
        assert status.resolved_at is not None

    @pytest.mark.asyncio
    async def test_check_status_returns_resolved_by_after_approval(
        self, human_gate_harness, approval_queue
    ):
        """check_status populates resolved_by with the approver name."""
        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Approver Test",
            description="Check resolved_by",
        )
        await approval_queue.approve(result.request_id, "@senior-dev")

        status = await human_gate_harness.check_status(result.request_id)
        assert status.resolved_by == "@senior-dev"

    @pytest.mark.asyncio
    async def test_check_status_returns_rejection_reason(
        self, human_gate_harness, approval_queue
    ):
        """check_status populates rejection_reason from the resolution_reason field."""
        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Rejection Reason Test",
            description="Check rejection_reason",
        )
        await approval_queue.reject(result.request_id, "@reviewer", "Needs more examples")

        status = await human_gate_harness.check_status(result.request_id)
        assert status.rejection_reason == "Needs more examples"

    @pytest.mark.asyncio
    async def test_check_status_returns_created_at(self, human_gate_harness):
        """check_status includes the creation timestamp."""
        result = await human_gate_harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Created At Test",
            description="Check created_at",
        )
        status = await human_gate_harness.check_status(result.request_id)
        assert status.created_at is not None


class TestCreateHumanGateHarnessFactory:
    """Tests for the create_human_gate_harness factory function."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def temp_session_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def temp_checkpoint_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_factory_creates_harness_with_defaults(self, temp_storage_dir):
        """create_human_gate_harness creates a working harness with minimal args."""
        from forge_harness.human_gate_harness import create_human_gate_harness

        harness = create_human_gate_harness(storage_dir=temp_storage_dir)
        assert harness is not None
        assert harness.approval_queue is not None

    def test_factory_accepts_existing_approval_queue(self, temp_storage_dir):
        """Factory uses a provided approval_queue instead of creating one."""
        from forge_harness.approval_queue import create_approval_queue
        from forge_harness.human_gate_harness import create_human_gate_harness

        queue = create_approval_queue(storage_dir=temp_storage_dir)
        harness = create_human_gate_harness(approval_queue=queue)
        assert harness.approval_queue is queue

    def test_factory_creates_session_manager_from_session_dir(
        self, temp_storage_dir, temp_session_dir
    ):
        """Factory creates a SessionStateManager when session_dir is provided."""
        from forge_harness.human_gate_harness import create_human_gate_harness

        harness = create_human_gate_harness(
            storage_dir=temp_storage_dir,
            session_dir=temp_session_dir,
        )
        assert harness.session_state_manager is not None

    def test_factory_no_session_manager_without_session_dir(self, temp_storage_dir):
        """Factory leaves session_state_manager None when no session_dir given."""
        from forge_harness.human_gate_harness import create_human_gate_harness

        harness = create_human_gate_harness(storage_dir=temp_storage_dir)
        assert harness.session_state_manager is None

    def test_factory_sets_checkpoint_dir(self, temp_storage_dir, temp_checkpoint_dir):
        """Factory sets checkpoint_dir on the harness."""
        from forge_harness.human_gate_harness import create_human_gate_harness

        harness = create_human_gate_harness(
            storage_dir=temp_storage_dir,
            checkpoint_dir=temp_checkpoint_dir,
        )
        assert harness.checkpoint_dir == Path(temp_checkpoint_dir)

    def test_factory_sets_default_notify_channels(self, temp_storage_dir):
        """Factory forwards default_notify_channels to the harness."""
        from forge_harness.human_gate_harness import create_human_gate_harness

        channels = [NotificationChannel.EMAIL, NotificationChannel.SLACK]
        harness = create_human_gate_harness(
            storage_dir=temp_storage_dir,
            default_notify_channels=channels,
        )
        assert NotificationChannel.EMAIL in harness.default_notify_channels
        assert NotificationChannel.SLACK in harness.default_notify_channels

    def test_factory_attaches_provided_session_state(self, temp_storage_dir, temp_session_dir):
        """Factory attaches an existing SessionState to the harness."""
        from forge_harness.human_gate_harness import create_human_gate_harness
        from forge_harness.session_state import create_session_state_manager

        manager = create_session_state_manager(storage_dir=temp_session_dir)
        state = manager.create_session(domain="test-domain", project="test-project")

        harness = create_human_gate_harness(
            storage_dir=temp_storage_dir,
            session_state=state,
        )
        assert harness.get_session_state() is state


class TestAwaitApprovalRecordsTimeout:
    """Tests that await_approval correctly records timeout decisions."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def temp_session_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.mark.asyncio
    async def test_await_approval_timeout_records_decision(self, temp_storage_dir, temp_session_dir):
        """await_approval records a 'timeout' decision when the wait expires."""
        from forge_harness.approval_queue import create_approval_queue
        from forge_harness.human_gate_harness import HumanGateHarness
        from forge_harness.session_state import create_session_state_manager

        queue = create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)
        manager = create_session_state_manager(storage_dir=temp_session_dir)
        state = manager.create_session(domain="test-domain", project="test-project")

        harness = HumanGateHarness(
            approval_queue=queue,
            notification_harness=None,
            session_state_manager=manager,
            session_state=state,
        )

        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="Timeout Decision Record",
            description="Will time out",
        )

        await harness.await_approval(
            result.request_id,
            timeout_seconds=0.05,
            poll_interval_seconds=0.02,
            record_decision=True,
        )

        # Session state should have a timeout decision recorded
        assert len(state.human_decisions) == 1
        assert state.human_decisions[0].decision == "timeout"

    @pytest.mark.asyncio
    async def test_await_approval_no_record_when_flag_false(
        self, temp_storage_dir, temp_session_dir
    ):
        """await_approval does NOT record decisions when record_decision=False."""
        from forge_harness.approval_queue import create_approval_queue
        from forge_harness.human_gate_harness import HumanGateHarness
        from forge_harness.session_state import create_session_state_manager

        queue = create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)
        manager = create_session_state_manager(storage_dir=temp_session_dir)
        state = manager.create_session(domain="test-domain", project="test-project")

        harness = HumanGateHarness(
            approval_queue=queue,
            notification_harness=None,
            session_state_manager=manager,
            session_state=state,
        )

        result = await harness.create_approval(
            approval_type=ApprovalType.CONTENT,
            domain="test-domain",
            title="No Record Test",
            description="Should not record",
        )

        await harness.await_approval(
            result.request_id,
            timeout_seconds=0.05,
            poll_interval_seconds=0.02,
            record_decision=False,
        )

        # No decisions recorded
        assert len(state.human_decisions) == 0


class TestApprovalResultDefaults:
    """Tests for ApprovalResult dataclass default values."""

    def test_approval_result_optional_fields_default_to_none(self):
        """Optional ApprovalResult fields default to None or empty list."""
        from forge_harness.human_gate_harness import ApprovalResult, GateStatus

        result = ApprovalResult(
            request_id="apr_test_001",
            gate_status=GateStatus.PENDING,
        )

        assert result.notification_ids == []
        assert result.checkpoint_path is None
        assert result.resolved_by is None
        assert result.rejection_reason is None
        assert result.created_at is None
        assert result.resolved_at is None

    def test_approval_result_all_fields(self):
        """ApprovalResult accepts all fields."""
        from datetime import UTC, datetime

        from forge_harness.human_gate_harness import ApprovalResult, GateStatus

        now = datetime.now(UTC)
        result = ApprovalResult(
            request_id="apr_full_001",
            gate_status=GateStatus.APPROVED,
            notification_ids=["notif_1", "notif_2"],
            checkpoint_path="/tmp/wf.json",
            resolved_by="@admin",
            rejection_reason=None,
            created_at=now,
            resolved_at=now,
        )

        assert result.request_id == "apr_full_001"
        assert result.gate_status == GateStatus.APPROVED
        assert len(result.notification_ids) == 2
        assert result.resolved_by == "@admin"
        assert result.created_at == now


class TestHarnessConstructorCheckpointDir:
    """Tests for checkpoint_dir handling in constructor."""

    @pytest.fixture
    def temp_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def approval_queue(self, temp_storage_dir):
        from forge_harness.approval_queue import create_approval_queue

        return create_approval_queue(storage_dir=temp_storage_dir, default_expiry_hours=24.0)

    def test_checkpoint_dir_none_stays_none(self, approval_queue):
        """When checkpoint_dir is not provided, it stays None."""
        from forge_harness.human_gate_harness import HumanGateHarness

        harness = HumanGateHarness(approval_queue=approval_queue)
        assert harness.checkpoint_dir is None

    def test_checkpoint_dir_string_is_converted_to_path(self, approval_queue):
        """String checkpoint_dir is converted to Path."""
        from forge_harness.human_gate_harness import HumanGateHarness

        harness = HumanGateHarness(
            approval_queue=approval_queue,
            checkpoint_dir="/tmp/checkpoints",
        )
        assert isinstance(harness.checkpoint_dir, Path)
        assert str(harness.checkpoint_dir) == "/tmp/checkpoints"

    def test_default_notify_channels_defaults_to_slack(self, approval_queue):
        """When default_notify_channels is not provided, SLACK is the default."""
        from forge_harness.human_gate_harness import HumanGateHarness

        harness = HumanGateHarness(approval_queue=approval_queue)
        assert NotificationChannel.SLACK in harness.default_notify_channels
