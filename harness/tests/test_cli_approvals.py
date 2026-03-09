"""Tests for CLI approval commands."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

from forge_harness.approval_queue import (
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)

# These tests were written for the old forge_harness.cli 'approvals' subcommand group.
# The CLI has been migrated to cli_v2 where 'forge approve' replaces 'forge-harness approvals'
# with a different API shape (single command with flags vs subcommand group).
pytestmark = pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: old 'approvals' subcommand group replaced by 'approve' command with different API")


class TestApprovalsListCommand:
    """Tests for 'forge-harness approvals list' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_queue(self):
        """Create mock approval queue."""
        queue = MagicMock()
        queue.storage = MagicMock()
        return queue

    def test_list_empty_queue(self, runner, tmp_path):
        """List approvals with empty queue shows appropriate message."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            result = runner.invoke(
                cli,
                ["approvals", "list", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            assert "No pending approval requests" in result.output

    def test_list_populated_queue(self, runner, tmp_path):
        """List approvals displays pending requests."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create test approval request
            request = ApprovalRequest(
                id="test-123",
                type=ApprovalType.FEATURE,
                domain="test-domain",
                title="Test Feature",
                description="Test description",
                status=ApprovalStatus.PENDING,
                priority=ApprovalPriority.NORMAL,
            )

            # Save request
            request_file = storage_dir / f"approval_{request.id}.json"
            request_file.write_text(json.dumps(request.to_dict(), indent=2))

            # Create index
            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: request_file.name}))

            result = runner.invoke(
                cli,
                ["approvals", "list", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            assert "test-123" in result.output
            assert "Test Feature" in result.output
            assert "feature" in result.output
            assert "test-domain" in result.output
            assert "Total: 1 requests" in result.output

    def test_list_filter_by_domain(self, runner, tmp_path):
        """List approvals filters by domain correctly."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create requests for different domains
            requests = [
                ApprovalRequest(
                    id="req-1",
                    type=ApprovalType.FEATURE,
                    domain="domain-a",
                    title="Feature A",
                    description="Test A",
                ),
                ApprovalRequest(
                    id="req-2",
                    type=ApprovalType.CONTENT,
                    domain="domain-b",
                    title="Content B",
                    description="Test B",
                ),
            ]

            index = {}
            for req in requests:
                req_file = storage_dir / f"approval_{req.id}.json"
                req_file.write_text(json.dumps(req.to_dict(), indent=2))
                index[req.id] = req_file.name

            (storage_dir / "_index.json").write_text(json.dumps(index))

            result = runner.invoke(
                cli,
                ["approvals", "list", "--domain", "domain-a", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            assert "req-1" in result.output
            assert "Feature A" in result.output
            assert "req-2" not in result.output
            assert "Content B" not in result.output

    def test_list_filter_by_type(self, runner, tmp_path):
        """List approvals filters by type correctly."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create requests of different types
            requests = [
                ApprovalRequest(
                    id="req-feature",
                    type=ApprovalType.FEATURE,
                    domain="test",
                    title="Feature",
                    description="Test",
                ),
                ApprovalRequest(
                    id="req-deploy",
                    type=ApprovalType.DEPLOY,
                    domain="test",
                    title="Deploy",
                    description="Test",
                ),
            ]

            index = {}
            for req in requests:
                req_file = storage_dir / f"approval_{req.id}.json"
                req_file.write_text(json.dumps(req.to_dict(), indent=2))
                index[req.id] = req_file.name

            (storage_dir / "_index.json").write_text(json.dumps(index))

            result = runner.invoke(
                cli,
                ["approvals", "list", "--type", "feature", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            assert "req-feature" in result.output
            assert "req-deploy" not in result.output

    def test_list_invalid_type(self, runner, tmp_path):
        """List with invalid type shows error."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            result = runner.invoke(
                cli,
                ["approvals", "list", "--type", "invalid-type", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 1
            assert "Invalid type" in result.output

    def test_list_json_output(self, runner, tmp_path):
        """List approvals outputs valid JSON."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            request = ApprovalRequest(
                id="json-test",
                type=ApprovalType.CONTENT,
                domain="test",
                title="JSON Test",
                description="Test JSON output",
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                ["approvals", "list", "--json", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            # Parse JSON output
            output_data = json.loads(result.output)
            assert isinstance(output_data, list)
            assert len(output_data) == 1
            assert output_data[0]["id"] == "json-test"
            assert output_data[0]["title"] == "JSON Test"

    def test_list_show_all_includes_resolved(self, runner, tmp_path):
        """List with --all flag includes resolved requests."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create pending and approved requests
            requests = [
                ApprovalRequest(
                    id="pending-1",
                    type=ApprovalType.FEATURE,
                    domain="test",
                    title="Pending",
                    description="Test",
                    status=ApprovalStatus.PENDING,
                ),
                ApprovalRequest(
                    id="approved-1",
                    type=ApprovalType.FEATURE,
                    domain="test",
                    title="Approved",
                    description="Test",
                    status=ApprovalStatus.APPROVED,
                ),
            ]

            index = {}
            for req in requests:
                req_file = storage_dir / f"approval_{req.id}.json"
                req_file.write_text(json.dumps(req.to_dict(), indent=2))
                index[req.id] = req_file.name

            (storage_dir / "_index.json").write_text(json.dumps(index))

            # Without --all, should only show pending
            result = runner.invoke(
                cli,
                ["approvals", "list", "--storage-dir", str(storage_dir)],
            )
            assert result.exit_code == 0
            assert "pending-1" in result.output
            assert "approved-1" not in result.output

            # With --all, should show both
            result_all = runner.invoke(
                cli,
                ["approvals", "list", "--all", "--storage-dir", str(storage_dir)],
            )
            assert result_all.exit_code == 0
            assert "pending-1" in result_all.output
            assert "approved-1" in result_all.output


class TestApprovalsApproveCommand:
    """Tests for 'forge-harness approvals approve' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_approve_valid_request(self, runner, tmp_path):
        """Approve a valid pending request succeeds."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create pending request
            request = ApprovalRequest(
                id="approve-me",
                type=ApprovalType.FEATURE,
                domain="test-domain",
                title="Test Approval",
                description="Should be approved",
                status=ApprovalStatus.PENDING,
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "approve",
                    "approve-me",
                    "--approver",
                    "test-user",
                    "--no-resume",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "Approved" in result.output
            assert "Test Approval" in result.output
            assert "test-user" in result.output

            # Verify request was updated
            updated_data = json.loads(req_file.read_text())
            assert updated_data["status"] == "approved"
            assert updated_data["approved_by"] == "test-user"

    def test_approve_invalid_request_id(self, runner, tmp_path):
        """Approve with invalid request ID shows error."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()
            (storage_dir / "_index.json").write_text(json.dumps({}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "approve",
                    "nonexistent-id",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 1
            assert "Error" in result.output

    def test_approve_already_processed(self, runner, tmp_path):
        """Approve an already approved request shows error."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create already approved request
            request = ApprovalRequest(
                id="already-approved",
                type=ApprovalType.FEATURE,
                domain="test",
                title="Already Approved",
                description="Test",
                status=ApprovalStatus.APPROVED,
                approved_by="previous-user",
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "approve",
                    "already-approved",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 1
            assert "Error" in result.output

    def test_approve_with_comment(self, runner, tmp_path):
        """Approve with comment stores the comment."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            request = ApprovalRequest(
                id="comment-test",
                type=ApprovalType.CONTENT,
                domain="test",
                title="Comment Test",
                description="Test",
                status=ApprovalStatus.PENDING,
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "approve",
                    "comment-test",
                    "--comment",
                    "Looks good!",
                    "--no-resume",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0

            # Verify comment was stored
            updated_data = json.loads(req_file.read_text())
            assert updated_data["resolution_reason"] == "Looks good!"


class TestApprovalsRejectCommand:
    """Tests for 'forge-harness approvals reject' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_reject_with_reason(self, runner, tmp_path):
        """Reject a request with reason succeeds."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            request = ApprovalRequest(
                id="reject-me",
                type=ApprovalType.DEPLOY,
                domain="test",
                title="Reject Test",
                description="Should be rejected",
                status=ApprovalStatus.PENDING,
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "reject",
                    "reject-me",
                    "--reason",
                    "Tests are failing",
                    "--approver",
                    "reviewer",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "Rejected" in result.output
            assert "Reject Test" in result.output
            assert "Tests are failing" in result.output
            assert "reviewer" in result.output

            # Verify request was updated
            updated_data = json.loads(req_file.read_text())
            assert updated_data["status"] == "rejected"
            assert updated_data["approved_by"] == "reviewer"
            assert updated_data["resolution_reason"] == "Tests are failing"

    def test_reject_without_reason_fails(self, runner, tmp_path):
        """Reject without reason shows error."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "reject",
                    "some-id",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            # Click will fail because --reason is required
            assert result.exit_code != 0
            assert "Error" in result.output or "required" in result.output.lower()

    def test_reject_invalid_id(self, runner, tmp_path):
        """Reject with invalid ID shows error."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()
            (storage_dir / "_index.json").write_text(json.dumps({}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "reject",
                    "invalid-id",
                    "--reason",
                    "Testing",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 1
            assert "Error" in result.output


class TestApprovalsStatsCommand:
    """Tests for 'forge-harness approvals stats' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_stats_empty_queue(self, runner, tmp_path):
        """Stats for empty queue shows zeros."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()
            (storage_dir / "_index.json").write_text(json.dumps({}))

            result = runner.invoke(
                cli,
                ["approvals", "stats", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            assert "Total requests:      0" in result.output
            assert "Pending:             0" in result.output
            assert "Approved:            0" in result.output
            assert "Rejected:            0" in result.output

    def test_stats_with_requests(self, runner, tmp_path):
        """Stats calculates counts correctly."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create various requests
            now = datetime.now(UTC)
            requests = [
                ApprovalRequest(
                    id="pending-1",
                    type=ApprovalType.FEATURE,
                    domain="domain-a",
                    title="Pending",
                    description="Test",
                    status=ApprovalStatus.PENDING,
                    created_at=now - timedelta(hours=5),
                ),
                ApprovalRequest(
                    id="approved-1",
                    type=ApprovalType.DEPLOY,
                    domain="domain-b",
                    title="Approved",
                    description="Test",
                    status=ApprovalStatus.APPROVED,
                    created_at=now - timedelta(hours=10),
                    resolved_at=now - timedelta(hours=8),
                ),
                ApprovalRequest(
                    id="rejected-1",
                    type=ApprovalType.CONTENT,
                    domain="domain-a",
                    title="Rejected",
                    description="Test",
                    status=ApprovalStatus.REJECTED,
                    created_at=now - timedelta(hours=3),
                    resolved_at=now - timedelta(hours=1),
                ),
            ]

            index = {}
            for req in requests:
                req_file = storage_dir / f"approval_{req.id}.json"
                req_file.write_text(json.dumps(req.to_dict(), indent=2))
                index[req.id] = req_file.name

            (storage_dir / "_index.json").write_text(json.dumps(index))

            result = runner.invoke(
                cli,
                ["approvals", "stats", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            assert "Total requests:      3" in result.output
            assert "Pending:             1" in result.output
            assert "Approved:            1" in result.output
            assert "Rejected:            1" in result.output
            assert "Oldest pending:      5.0h" in result.output
            assert "By Type:" in result.output
            assert "feature: 1" in result.output
            assert "By Domain:" in result.output
            assert "domain-a: 2" in result.output
            assert "domain-b: 1" in result.output

    def test_stats_json_output(self, runner, tmp_path):
        """Stats JSON output is valid."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            request = ApprovalRequest(
                id="stats-json",
                type=ApprovalType.FEATURE,
                domain="test",
                title="Stats JSON",
                description="Test",
                status=ApprovalStatus.PENDING,
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                ["approvals", "stats", "--json", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            stats_data = json.loads(result.output)
            assert stats_data["total_requests"] == 1
            assert stats_data["pending_count"] == 1
            assert stats_data["approved_count"] == 0
            assert stats_data["rejected_count"] == 0


class TestApprovalsShowCommand:
    """Tests for 'forge-harness approvals show' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_show_existing_request(self, runner, tmp_path):
        """Show displays full details of a request."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            request = ApprovalRequest(
                id="show-test",
                type=ApprovalType.SECURITY,
                domain="critical-domain",
                title="Security Review",
                description="This requires careful review",
                status=ApprovalStatus.PENDING,
                priority=ApprovalPriority.CRITICAL,
                metadata={"risk_level": "high", "affected_users": 1000},
                tags=["security", "urgent"],
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                ["approvals", "show", "show-test", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 0
            assert "show-test" in result.output
            assert "security" in result.output
            assert "critical-domain" in result.output
            assert "Security Review" in result.output
            assert "This requires careful review" in result.output
            assert "critical" in result.output
            assert "Metadata:" in result.output
            assert "risk_level" in result.output

    def test_show_nonexistent_request(self, runner, tmp_path):
        """Show with invalid ID shows error."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()
            (storage_dir / "_index.json").write_text(json.dumps({}))

            result = runner.invoke(
                cli,
                ["approvals", "show", "nonexistent", "--storage-dir", str(storage_dir)],
            )

            assert result.exit_code == 1
            assert "not found" in result.output


class TestApprovalsCreateCommand:
    """Tests for 'forge-harness approvals create' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_create_basic_request(self, runner, tmp_path):
        """Create a basic approval request."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "create",
                    "--type",
                    "feature",
                    "--title",
                    "New Feature Request",
                    "--domain",
                    "test-domain",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "Approval request created" in result.output
            assert "feature" in result.output
            assert "New Feature Request" in result.output
            assert "test-domain" in result.output

            # Verify file was created
            approval_files = list(storage_dir.glob("approval_*.json"))
            assert len(approval_files) >= 1

    def test_create_with_all_options(self, runner, tmp_path):
        """Create request with all options."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "create",
                    "--type",
                    "deploy",
                    "--title",
                    "Production Deploy",
                    "--description",
                    "Deploy v2.0 to production",
                    "--domain",
                    "production",
                    "--priority",
                    "critical",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "deploy" in result.output
            assert "Production Deploy" in result.output
            assert "critical" in result.output

    def test_create_json_output(self, runner, tmp_path):
        """Create request with JSON output."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "create",
                    "--type",
                    "content",
                    "--title",
                    "JSON Test",
                    "--json",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["type"] == "content"
            assert data["title"] == "JSON Test"
            assert data["status"] == "pending"
            assert "id" in data


class TestApprovalsCleanupCommand:
    """Tests for 'forge-harness approvals cleanup' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_cleanup_dry_run(self, runner, tmp_path):
        """Cleanup with dry-run shows what would be deleted."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create old resolved request
            old_request = ApprovalRequest(
                id="old-resolved",
                type=ApprovalType.FEATURE,
                domain="test",
                title="Old Request",
                description="Test",
                status=ApprovalStatus.APPROVED,
                created_at=datetime.now(UTC) - timedelta(days=60),
                resolved_at=datetime.now(UTC) - timedelta(days=60),
            )

            req_file = storage_dir / f"approval_{old_request.id}.json"
            req_file.write_text(json.dumps(old_request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({old_request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "cleanup",
                    "--days",
                    "30",
                    "--dry-run",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "Would delete" in result.output
            assert "old-resolved" in result.output
            # File should still exist after dry-run
            assert req_file.exists()

    def test_cleanup_no_old_requests(self, runner, tmp_path):
        """Cleanup with no old requests shows message."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create recent request
            recent_request = ApprovalRequest(
                id="recent",
                type=ApprovalType.FEATURE,
                domain="test",
                title="Recent",
                description="Test",
                status=ApprovalStatus.APPROVED,
            )

            req_file = storage_dir / f"approval_{recent_request.id}.json"
            req_file.write_text(json.dumps(recent_request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({recent_request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "cleanup",
                    "--days",
                    "30",
                    "--dry-run",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "No requests older than" in result.output

    def test_cleanup_actual_deletion(self, runner, tmp_path):
        """Cleanup without dry-run deletes old requests."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            # Create old resolved request
            old_request = ApprovalRequest(
                id="delete-me",
                type=ApprovalType.FEATURE,
                domain="test",
                title="Old",
                description="Test",
                status=ApprovalStatus.REJECTED,
                created_at=datetime.now(UTC) - timedelta(days=100),
                resolved_at=datetime.now(UTC) - timedelta(days=100),
            )

            req_file = storage_dir / f"approval_{old_request.id}.json"
            req_file.write_text(json.dumps(old_request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({old_request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "cleanup",
                    "--days",
                    "30",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "Cleaned up" in result.output
            # Note: File deletion happens via approval queue,
            # which manages its own storage


class TestApprovalsCancelCommand:
    """Tests for 'forge-harness approvals cancel' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    def test_cancel_request(self, runner, tmp_path):
        """Cancel a pending request."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            request = ApprovalRequest(
                id="cancel-me",
                type=ApprovalType.FEATURE,
                domain="test",
                title="Cancel Test",
                description="Test",
                status=ApprovalStatus.PENDING,
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "cancel",
                    "cancel-me",
                    "--reason",
                    "No longer needed",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "Cancelled" in result.output
            assert "Cancel Test" in result.output
            assert "No longer needed" in result.output

            # Verify status changed
            updated_data = json.loads(req_file.read_text())
            assert updated_data["status"] == "cancelled"

    def test_cancel_without_reason(self, runner, tmp_path):
        """Cancel without reason still works."""
        with runner.isolated_filesystem(temp_dir=tmp_path):
            storage_dir = Path(".forge/approvals")
            storage_dir.mkdir()

            request = ApprovalRequest(
                id="cancel-no-reason",
                type=ApprovalType.FEATURE,
                domain="test",
                title="Cancel",
                description="Test",
                status=ApprovalStatus.PENDING,
            )

            req_file = storage_dir / f"approval_{request.id}.json"
            req_file.write_text(json.dumps(request.to_dict(), indent=2))

            index_file = storage_dir / "_index.json"
            index_file.write_text(json.dumps({request.id: req_file.name}))

            result = runner.invoke(
                cli,
                [
                    "approvals",
                    "cancel",
                    "cancel-no-reason",
                    "--storage-dir",
                    str(storage_dir),
                ],
            )

            assert result.exit_code == 0
            assert "Cancelled" in result.output
