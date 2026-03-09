"""Tests for forge_harness.commands.approvals module.

Tests all approval command CLI commands using pytest and unittest.mock.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from forge_harness.approval_queue import ApprovalQueueStats
from forge_harness.commands.approvals import (
    approvals,
    approvals_approve,
    approvals_cancel,
    approvals_cleanup,
    approvals_create,
    approvals_list,
    approvals_reject,
    approvals_show,
    approvals_stats,
)

# =============================================================================
# Fixtures and Helpers
# =============================================================================


@pytest.fixture
def runner():
    """Create a Click test runner."""
    return CliRunner()


@pytest.fixture
def mock_asyncio_run():
    """Mock asyncio.run to return mocked results."""
    with patch("forge_harness.commands.approvals.asyncio.run") as mock:
        yield mock


@pytest.fixture
def sample_request():
    """Create a sample approval request."""
    mock_request = MagicMock()
    mock_request.id = "test-123"
    mock_request.type = MagicMock()
    mock_request.type.value = "feature"
    mock_request.domain = "test-domain"
    mock_request.title = "Test Approval Request"
    mock_request.status = MagicMock()
    mock_request.status.value = "pending"
    mock_request.priority = MagicMock()
    mock_request.priority.value = "normal"
    mock_request.created_at = datetime.now(UTC)
    mock_request.expires_at = None
    mock_request.approved_by = None
    mock_request.resolved_at = None
    mock_request.resolution_reason = None
    mock_request.description = "Test description"
    mock_request.metadata = {"key": "value"}
    mock_request.workflow_checkpoint = None
    mock_request.tags = ["tag1", "tag2"]
    mock_request.age_hours = 2.5
    mock_request.to_dict.return_value = {
        "id": "test-123",
        "type": "feature",
        "domain": "test-domain",
        "title": "Test Approval Request",
        "status": "pending",
    }
    return mock_request


@pytest.fixture
def sample_request_approved():
    """Create a sample approved request."""
    mock_request = MagicMock()
    mock_request.id = "test-456"
    mock_request.type = MagicMock()
    mock_request.type.value = "deploy"
    mock_request.domain = "test-domain"
    mock_request.title = "Deploy Request"
    mock_request.status = MagicMock()
    mock_request.status.value = "approved"
    mock_request.priority = MagicMock()
    mock_request.priority.value = "high"
    mock_request.created_at = datetime.now(UTC) - timedelta(hours=5)
    mock_request.expires_at = None
    mock_request.approved_by = "admin"
    mock_request.resolved_at = datetime.now(UTC)
    mock_request.resolution_reason = "Approved for deployment"
    mock_request.description = "Deploy to production"
    mock_request.metadata = {}
    mock_request.workflow_checkpoint = "checkpoint-123"
    mock_request.tags = []
    mock_request.age_hours = 5.0
    return mock_request


@pytest.fixture
def sample_stats():
    """Create sample approval statistics as a real dataclass."""
    return ApprovalQueueStats(
        total_requests=10,
        pending_count=3,
        approved_count=5,
        rejected_count=2,
        expired_count=0,
        avg_resolution_hours=2.5,
        oldest_pending_hours=24.0,
        by_type={"feature": 3, "deploy": 2, "content": 1},
        by_domain={"test-domain": 5, "other-domain": 1},
    )


# =============================================================================
# Test Approvals Group
# =============================================================================


class TestApprovalsGroup:
    """Tests for the main approvals group command."""

    def test_approvals_group_invokes(self, runner):
        """Test that approvals group can be invoked."""
        result = runner.invoke(approvals, ["--help"])
        assert result.exit_code == 0
        assert "Manage approval queue" in result.output

    def test_approvals_list_command_exists(self, runner):
        """Test list command is available."""
        result = runner.invoke(approvals, ["list", "--help"])
        assert result.exit_code == 0

    def test_approvals_show_command_exists(self, runner):
        """Test show command is available."""
        result = runner.invoke(approvals, ["show", "--help"])
        assert result.exit_code == 0

    def test_approvals_approve_command_exists(self, runner):
        """Test approve command is available."""
        result = runner.invoke(approvals, ["approve", "--help"])
        assert result.exit_code == 0

    def test_approvals_reject_command_exists(self, runner):
        """Test reject command is available."""
        result = runner.invoke(approvals, ["reject", "--help"])
        assert result.exit_code == 0

    def test_approvals_cancel_command_exists(self, runner):
        """Test cancel command is available."""
        result = runner.invoke(approvals, ["cancel", "--help"])
        assert result.exit_code == 0

    def test_approvals_stats_command_exists(self, runner):
        """Test stats command is available."""
        result = runner.invoke(approvals, ["stats", "--help"])
        assert result.exit_code == 0

    def test_approvals_create_command_exists(self, runner):
        """Test create command is available."""
        result = runner.invoke(approvals, ["create", "--help"])
        assert result.exit_code == 0

    def test_approvals_cleanup_command_exists(self, runner):
        """Test cleanup command is available."""
        result = runner.invoke(approvals, ["cleanup", "--help"])
        assert result.exit_code == 0


# =============================================================================
# Test Approvals List Command
# =============================================================================


class TestApprovalsList:
    """Tests for the approvals list command."""

    def test_list_empty(self, runner):
        """Test listing with no requests."""
        mock_queue = MagicMock()
        mock_queue.list_pending = AsyncMock(return_value=[])

        with patch("forge_harness.commands.approvals.asyncio.run", return_value=[]):
            with patch(
                "forge_harness.approval_queue.create_approval_queue", return_value=mock_queue
            ):
                result = runner.invoke(approvals_list, [])

        assert result.exit_code == 0
        assert "No pending approval requests" in result.output

    def test_list_with_requests(self, runner, sample_request):
        """Test listing with pending requests."""
        mock_queue = MagicMock()
        mock_queue.list_pending = AsyncMock(return_value=[sample_request])

        with patch("forge_harness.commands.approvals.asyncio.run", return_value=[sample_request]):
            with patch(
                "forge_harness.approval_queue.create_approval_queue", return_value=mock_queue
            ):
                result = runner.invoke(approvals_list, [])

        assert result.exit_code == 0
        assert "test-123" in result.output

    def test_list_json_output(self, runner, sample_request):
        """Test list with JSON output."""
        mock_queue = MagicMock()
        mock_queue.list_pending = AsyncMock(return_value=[sample_request])

        with patch("forge_harness.commands.approvals.asyncio.run", return_value=[sample_request]):
            with patch(
                "forge_harness.approval_queue.create_approval_queue", return_value=mock_queue
            ):
                result = runner.invoke(approvals_list, ["--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_list_with_domain_filter(self, runner, sample_request):
        """Test list with domain filter."""
        mock_queue = MagicMock()
        mock_queue.list_pending = AsyncMock(return_value=[sample_request])

        with patch("forge_harness.commands.approvals.asyncio.run", return_value=[sample_request]):
            with patch(
                "forge_harness.approval_queue.create_approval_queue", return_value=mock_queue
            ):
                result = runner.invoke(approvals_list, ["--domain", "test-domain"])

        assert result.exit_code == 0

    def test_list_show_all(self, runner, sample_request):
        """Test list with show all flag."""
        mock_queue = MagicMock()
        mock_queue.storage = MagicMock()
        mock_queue.storage.list_all = AsyncMock(return_value=[sample_request])

        with patch("forge_harness.commands.approvals.asyncio.run", return_value=[sample_request]):
            with patch(
                "forge_harness.approval_queue.create_approval_queue", return_value=mock_queue
            ):
                result = runner.invoke(approvals_list, ["--all"])

        assert result.exit_code == 0

    def test_list_invalid_type_filter(self, runner):
        """Test list with invalid type filter."""
        result = runner.invoke(approvals_list, ["--type", "invalid_type"])

        assert result.exit_code == 1

    def test_list_with_type_filter(self, runner, sample_request):
        """Test list with valid type filter."""
        mock_queue = MagicMock()
        mock_queue.list_pending = AsyncMock(return_value=[sample_request])

        with patch("forge_harness.commands.approvals.asyncio.run", return_value=[sample_request]):
            with patch(
                "forge_harness.approval_queue.create_approval_queue", return_value=mock_queue
            ):
                result = runner.invoke(approvals_list, ["--type", "feature"])

        assert result.exit_code == 0

    def test_list_custom_storage_dir(self, runner):
        """Test list with custom storage directory."""
        mock_queue = MagicMock()
        mock_queue.list_pending = AsyncMock(return_value=[])

        with patch("forge_harness.commands.approvals.asyncio.run", return_value=[]):
            with patch(
                "forge_harness.approval_queue.create_approval_queue", return_value=mock_queue
            ):
                result = runner.invoke(approvals_list, ["--storage-dir", "/custom/path"])

        assert result.exit_code == 0

    def test_list_error_handling(self, runner):
        """Test list with error handling."""
        mock_queue = MagicMock()
        mock_queue.list_pending = AsyncMock(side_effect=Exception("Database error"))

        with patch(
            "forge_harness.commands.approvals.asyncio.run", side_effect=Exception("Database error")
        ):
            with patch(
                "forge_harness.approval_queue.create_approval_queue", return_value=mock_queue
            ):
                result = runner.invoke(approvals_list, [])

        assert result.exit_code == 1
        assert "Error" in result.output


# =============================================================================
# Test Approvals Show Command
# =============================================================================


class TestApprovalsShow:
    """Tests for the approvals show command."""

    def test_show_request_not_found(self, runner, mock_asyncio_run):
        """Test show with non-existent request."""
        mock_queue = MagicMock()
        mock_queue.get_request = AsyncMock(return_value=None)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=None):
                result = runner.invoke(approvals_show, ["nonexistent-id"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_show_request_found(self, runner, mock_asyncio_run, sample_request):
        """Test show with existing request."""
        mock_queue = MagicMock()
        mock_queue.get_request = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(approvals_show, ["test-123"])

        assert result.exit_code == 0
        assert "test-123" in result.output
        assert "Test Approval Request" in result.output


# =============================================================================
# Test Approvals Approve Command
# =============================================================================


class TestApprovalsApprove:
    """Tests for the approvals approve command."""

    def test_approve_success(self, runner, mock_asyncio_run, sample_request_approved):
        """Test successful approval."""
        mock_queue = MagicMock()
        mock_queue.approve = AsyncMock(return_value=sample_request_approved)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run", return_value=sample_request_approved
            ):
                with (
                    patch(
                        "forge_harness.harness_registry.create_harness_registry"
                    ) as mock_registry,
                    patch(
                        "forge_harness.orchestration_harness.create_orchestration_harness"
                    ) as mock_orch,
                ):
                    mock_registry.return_value = MagicMock()
                    mock_orch_instance = MagicMock()
                    mock_orch_instance.resume = AsyncMock(
                        return_value=MagicMock(success=True, duration_seconds=10.0)
                    )
                    mock_orch.return_value = mock_orch_instance

                    result = runner.invoke(approvals_approve, ["test-456"])

        assert result.exit_code == 0
        assert "Approved" in result.output

    def test_approve_with_custom_approver(self, runner, mock_asyncio_run, sample_request_approved):
        """Test approval with custom approver."""
        mock_queue = MagicMock()
        mock_queue.approve = AsyncMock(return_value=sample_request_approved)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run", return_value=sample_request_approved
            ):
                with patch("forge_harness.harness_registry.create_harness_registry"):
                    with patch("forge_harness.orchestration_harness.create_orchestration_harness"):
                        result = runner.invoke(
                            approvals_approve, ["test-456", "--approver", "custom-user"]
                        )

        assert result.exit_code == 0

    def test_approve_with_comment(self, runner, mock_asyncio_run, sample_request_approved):
        """Test approval with comment."""
        mock_queue = MagicMock()
        mock_queue.approve = AsyncMock(return_value=sample_request_approved)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run", return_value=sample_request_approved
            ):
                with patch("forge_harness.harness_registry.create_harness_registry"):
                    with patch("forge_harness.orchestration_harness.create_orchestration_harness"):
                        result = runner.invoke(
                            approvals_approve, ["test-456", "--comment", "Looks good"]
                        )

        assert result.exit_code == 0

    def test_approve_no_resume(self, runner, mock_asyncio_run, sample_request_approved):
        """Test approval without auto-resume."""
        mock_queue = MagicMock()
        mock_queue.approve = AsyncMock(return_value=sample_request_approved)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run", return_value=sample_request_approved
            ):
                result = runner.invoke(approvals_approve, ["test-456", "--no-resume"])

        assert result.exit_code == 0
        assert "pipeline resume" in result.output

    def test_approve_not_found(self, runner, mock_asyncio_run):
        """Test approval with non-existent request."""
        mock_queue = MagicMock()
        mock_queue.approve = AsyncMock(side_effect=ValueError("Request not found"))

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run",
                side_effect=ValueError("Request not found"),
            ):
                result = runner.invoke(approvals_approve, ["nonexistent"])

        assert result.exit_code == 1


# =============================================================================
# Test Approvals Reject Command
# =============================================================================


class TestApprovalsReject:
    """Tests for the approvals reject command."""

    def test_reject_success(self, runner, mock_asyncio_run, sample_request):
        """Test successful rejection."""
        mock_queue = MagicMock()
        mock_queue.reject = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(
                    approvals_reject, ["test-123", "--reason", "Not ready for deployment"]
                )

        assert result.exit_code == 0
        assert "Rejected" in result.output

    def test_reject_missing_reason(self, runner):
        """Test rejection without required reason."""
        result = runner.invoke(approvals_reject, ["test-123"])

        assert result.exit_code == 2

    def test_reject_with_custom_approver(self, runner, mock_asyncio_run, sample_request):
        """Test rejection with custom approver."""
        mock_queue = MagicMock()
        mock_queue.reject = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(
                    approvals_reject, ["test-123", "--reason", "Test", "--approver", "reviewer"]
                )

        assert result.exit_code == 0

    def test_reject_not_found(self, runner, mock_asyncio_run):
        """Test rejection with non-existent request."""
        mock_queue = MagicMock()
        mock_queue.reject = AsyncMock(side_effect=ValueError("Request not found"))

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run",
                side_effect=ValueError("Request not found"),
            ):
                result = runner.invoke(approvals_reject, ["nonexistent", "--reason", "Test"])

        assert result.exit_code == 1


# =============================================================================
# Test Approvals Cancel Command
# =============================================================================


class TestApprovalsCancel:
    """Tests for the approvals cancel command."""

    def test_cancel_success(self, runner, mock_asyncio_run, sample_request):
        """Test successful cancellation."""
        mock_queue = MagicMock()
        mock_queue.cancel = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(approvals_cancel, ["test-123"])

        assert result.exit_code == 0
        assert "Cancelled" in result.output

    def test_cancel_with_reason(self, runner, mock_asyncio_run, sample_request):
        """Test cancellation with reason."""
        mock_queue = MagicMock()
        mock_queue.cancel = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(
                    approvals_cancel, ["test-123", "--reason", "No longer needed"]
                )

        assert result.exit_code == 0

    def test_cancel_not_found(self, runner, mock_asyncio_run):
        """Test cancellation of non-existent request."""
        mock_queue = MagicMock()
        mock_queue.cancel = AsyncMock(side_effect=ValueError("Request not found"))

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run",
                side_effect=ValueError("Request not found"),
            ):
                result = runner.invoke(approvals_cancel, ["nonexistent"])

        assert result.exit_code == 1


# =============================================================================
# Test Approvals Stats Command
# =============================================================================


class TestApprovalsStats:
    """Tests for the approvals stats command."""

    def test_stats_success(self, runner, mock_asyncio_run, sample_stats):
        """Test successful stats display."""
        mock_queue = MagicMock()
        mock_queue.get_stats = AsyncMock(return_value=sample_stats)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_stats):
                result = runner.invoke(approvals_stats, [])

        assert result.exit_code == 0
        assert "Approval Queue Statistics" in result.output
        assert "Total requests:" in result.output

    def test_stats_json_output(self, runner, mock_asyncio_run, sample_stats):
        """Test stats with JSON output."""
        mock_queue = MagicMock()
        mock_queue.get_stats = AsyncMock(return_value=sample_stats)

        stats_dict = {
            "total_requests": 10,
            "pending_count": 3,
            "approved_count": 5,
            "rejected_count": 2,
            "expired_count": 0,
            "avg_resolution_hours": 2.5,
            "oldest_pending_hours": 24.0,
            "by_type": {"feature": 3},
            "by_domain": {"test-domain": 5},
        }

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_stats):
                with patch("dataclasses.asdict", return_value=stats_dict):
                    result = runner.invoke(approvals_stats, ["--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total_requests" in data

    def test_stats_custom_storage_dir(self, runner, mock_asyncio_run, sample_stats):
        """Test stats with custom storage directory."""
        mock_queue = MagicMock()
        mock_queue.get_stats = AsyncMock(return_value=sample_stats)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_stats):
                result = runner.invoke(approvals_stats, ["--storage-dir", "/custom/path"])

        assert result.exit_code == 0


# =============================================================================
# Test Approvals Create Command
# =============================================================================


class TestApprovalsCreate:
    """Tests for the approvals create command."""

    def test_create_success(self, runner, mock_asyncio_run, sample_request):
        """Test successful request creation."""
        mock_queue = MagicMock()
        mock_queue.create_request = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(
                    approvals_create, ["--type", "feature", "--title", "New Feature"]
                )

        assert result.exit_code == 0
        assert "created" in result.output

    def test_create_with_description(self, runner, mock_asyncio_run, sample_request):
        """Test creation with description."""
        mock_queue = MagicMock()
        mock_queue.create_request = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(
                    approvals_create,
                    [
                        "--type",
                        "feature",
                        "--title",
                        "New Feature",
                        "--description",
                        "Feature description",
                    ],
                )

        assert result.exit_code == 0

    def test_create_with_domain(self, runner, mock_asyncio_run, sample_request):
        """Test creation with custom domain."""
        mock_queue = MagicMock()
        mock_queue.create_request = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(
                    approvals_create,
                    ["--type", "feature", "--title", "New Feature", "--domain", "custom-domain"],
                )

        assert result.exit_code == 0

    def test_create_with_priority(self, runner, mock_asyncio_run, sample_request):
        """Test creation with priority."""
        mock_queue = MagicMock()
        mock_queue.create_request = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(
                    approvals_create,
                    ["--type", "feature", "--title", "New Feature", "--priority", "critical"],
                )

        assert result.exit_code == 0

    def test_create_missing_title(self, runner):
        """Test creation without required title."""
        result = runner.invoke(approvals_create, ["--type", "feature"])

        assert result.exit_code == 2

    def test_create_invalid_type(self, runner):
        """Test creation with invalid type."""
        result = runner.invoke(approvals_create, ["--type", "invalid", "--title", "Test"])

        assert result.exit_code == 2

    def test_create_invalid_priority(self, runner):
        """Test creation with invalid priority."""
        result = runner.invoke(
            approvals_create, ["--type", "feature", "--title", "Test", "--priority", "invalid"]
        )

        assert result.exit_code == 2

    def test_create_json_output(self, runner, mock_asyncio_run, sample_request):
        """Test creation with JSON output."""
        mock_queue = MagicMock()
        mock_queue.create_request = AsyncMock(return_value=sample_request)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=sample_request):
                result = runner.invoke(
                    approvals_create, ["--type", "feature", "--title", "New Feature", "--json"]
                )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "id" in data


# =============================================================================
# Test Approvals Cleanup Command
# =============================================================================


class TestApprovalsCleanup:
    """Tests for the approvals cleanup command."""

    def test_cleanup_dry_run(self, runner, mock_asyncio_run, sample_request_approved):
        """Test cleanup in dry-run mode."""
        mock_queue = MagicMock()
        mock_queue.storage = MagicMock()
        mock_queue.storage.list_all = AsyncMock(return_value=[sample_request_approved])

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run",
                return_value=[sample_request_approved],
            ):
                with patch("datetime.datetime") as mock_datetime:
                    mock_datetime.now.return_value = datetime.now(UTC)
                    mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

                    result = runner.invoke(approvals_cleanup, ["--dry-run"])

        assert result.exit_code == 0

    def test_cleanup_actual(self, runner, mock_asyncio_run):
        """Test actual cleanup."""
        mock_queue = MagicMock()
        mock_queue.cleanup_old_requests = AsyncMock(return_value=5)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=5):
                result = runner.invoke(approvals_cleanup, [])

        assert result.exit_code == 0
        assert "Cleaned up" in result.output

    def test_cleanup_custom_days(self, runner, mock_asyncio_run):
        """Test cleanup with custom days."""
        mock_queue = MagicMock()
        mock_queue.cleanup_old_requests = AsyncMock(return_value=3)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=3):
                result = runner.invoke(approvals_cleanup, ["--days", "60"])

        assert result.exit_code == 0

    def test_cleanup_custom_storage_dir(self, runner, mock_asyncio_run):
        """Test cleanup with custom storage directory."""
        mock_queue = MagicMock()
        mock_queue.cleanup_old_requests = AsyncMock(return_value=0)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=0):
                result = runner.invoke(approvals_cleanup, ["--storage-dir", "/custom/path"])

        assert result.exit_code == 0


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_list_handles_exception(self, runner):
        """Test list command handles exceptions gracefully."""
        mock_queue = MagicMock()
        mock_queue.list_pending = AsyncMock(side_effect=RuntimeError("Database connection failed"))

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run",
                side_effect=RuntimeError("Database connection failed"),
            ):
                result = runner.invoke(approvals_list, [])

        assert result.exit_code == 1

    def test_create_handles_invalid_type(self, runner):
        """Test create command rejects invalid types."""
        result = runner.invoke(approvals_create, ["--type", "fake", "--title", "Test"])

        assert result.exit_code == 2

    def test_approve_handles_resume_error(self, runner, mock_asyncio_run, sample_request_approved):
        """Test approve handles auto-resume errors."""
        mock_queue = MagicMock()
        mock_queue.approve = AsyncMock(return_value=sample_request_approved)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch(
                "forge_harness.commands.approvals.asyncio.run", return_value=sample_request_approved
            ):
                with patch(
                    "forge_harness.harness_registry.create_harness_registry",
                    side_effect=Exception("Registry error"),
                ):
                    result = runner.invoke(approvals_approve, ["test-456"])

        assert result.exit_code == 0

    def test_stats_handles_zero_requests(self, runner, mock_asyncio_run):
        """Test stats with no requests."""
        zero_stats = ApprovalQueueStats(
            total_requests=0,
            pending_count=0,
            approved_count=0,
            rejected_count=0,
            expired_count=0,
            avg_resolution_hours=0,
            oldest_pending_hours=None,
            by_type={},
            by_domain={},
        )

        mock_queue = MagicMock()
        mock_queue.get_stats = AsyncMock(return_value=zero_stats)

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=zero_stats):
                result = runner.invoke(approvals_stats, [])

        assert result.exit_code == 0

    def test_cleanup_dry_run_nothing_to_delete(self, runner, mock_asyncio_run):
        """Test cleanup dry-run with nothing to delete."""
        mock_queue = MagicMock()
        mock_queue.storage = MagicMock()
        mock_queue.storage.list_all = AsyncMock(return_value=[])

        with patch("forge_harness.approval_queue.create_approval_queue", return_value=mock_queue):
            with patch("forge_harness.commands.approvals.asyncio.run", return_value=[]):
                with patch("datetime.datetime") as mock_datetime:
                    mock_datetime.now.return_value = datetime.now(UTC)
                    mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

                    result = runner.invoke(approvals_cleanup, ["--dry-run"])

        assert result.exit_code == 0
        assert "No requests" in result.output
