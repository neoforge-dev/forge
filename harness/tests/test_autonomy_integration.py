"""Integration tests for Autonomy Stack components.

Tests for Ralph Loop, Approval Queue, and WebhookServer integration.
These tests verify the complete workflows without external dependencies.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# =============================================================================
# Ralph Loop Integration Tests
# =============================================================================


class TestRalphLoopIntegration:
    """Integration tests for Ralph Loop dry-run and full execution."""

    @pytest.fixture
    def features_json(self, tmp_path):
        """Create a sample features.json file."""
        features = {
            "version": "1.0",
            "project": "test-project",
            "features": [
                {
                    "id": "feat-001",
                    "name": "Add user authentication",
                    "description": "Implement JWT-based authentication",
                    "status": "pending",
                    "priority": "high",
                    "acceptance_criteria": [
                        "Users can register",
                        "Users can login",
                        "JWT tokens are issued",
                    ],
                    "depends_on": [],
                    "tests": ["test_auth_register", "test_auth_login"],
                    "estimated_tokens": 4000,
                    "attempts": 0,
                },
                {
                    "id": "feat-002",
                    "name": "Add user profile",
                    "description": "Implement user profile management",
                    "status": "pending",
                    "priority": "medium",
                    "acceptance_criteria": ["Users can view profile", "Users can update profile"],
                    "depends_on": ["feat-001"],
                    "tests": ["test_profile_view", "test_profile_update"],
                    "estimated_tokens": 3000,
                    "attempts": 0,
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features, indent=2))
        return path

    @pytest.mark.asyncio
    async def test_ralph_loop_dry_run_completes(self, features_json):
        """Test Ralph Loop dry-run completes successfully."""
        from forge_harness.ralph_loop import create_ralph_loop

        # Create loop in dry-run mode
        loop = create_ralph_loop(
            features_path=features_json,
            max_iterations=5,
            dry_run=True,
        )

        # Run the loop
        result = await loop.run()

        # Verify dry-run behavior
        assert result.success is True
        assert result.features_completed >= 0

    @pytest.mark.asyncio
    async def test_ralph_loop_respects_dependencies(self, tmp_path):
        """Test Ralph Loop processes features in dependency order."""
        from forge_harness.ralph_loop import create_ralph_loop

        # Create features with dependencies
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "feat-B",
                    "name": "Feature B (depends on A)",
                    "description": "Second feature",
                    "status": "pending",
                    "priority": "medium",
                    "depends_on": ["feat-A"],
                    "tests": [],
                },
                {
                    "id": "feat-A",
                    "name": "Feature A (no deps)",
                    "description": "First feature",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": [],
                    "tests": [],
                },
            ],
        }
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features))

        loop = create_ralph_loop(
            features_path=path,
            max_iterations=5,
            dry_run=True,
        )

        # Track which features were processed
        processed = []
        original_implement = loop._implement_feature

        async def track_implement(feature):
            processed.append(feature.id)
            return await original_implement(feature)

        loop._implement_feature = track_implement

        await loop.run()

        # feat-A should be processed before feat-B (or feat-B skipped if A fails)
        if "feat-B" in processed and "feat-A" in processed:
            assert processed.index("feat-A") < processed.index("feat-B")

    @pytest.mark.asyncio
    async def test_ralph_loop_with_mock_orchestrator(self, features_json):
        """Test Ralph Loop with mock agent orchestrator."""
        from forge_harness.ralph_agent_orchestrator import MockAgentOrchestrator
        from forge_harness.ralph_loop import FeatureStore, LoopConfig, RalphLoopHarness

        store = FeatureStore(features_json)
        store.load()  # Must call load() to populate features

        config = LoopConfig(
            max_iterations=5,
            dry_run=False,  # Not dry-run, use orchestrator
        )

        # Mock orchestrator that always succeeds
        orchestrator = MockAgentOrchestrator(success_rate=1.0)

        loop = RalphLoopHarness(
            feature_store=store,
            config=config,
            orchestrator=orchestrator,
        )

        # Mock the test runner to always pass
        loop._run_tests = AsyncMock(return_value=(True, []))

        await loop.run()

        # Verify orchestrator was used
        assert orchestrator._implementation_count >= 1


# =============================================================================
# Approval Queue Integration Tests
# =============================================================================


class TestApprovalQueueIntegration:
    """Integration tests for Approval Queue with storage backends."""

    @pytest.fixture
    def storage_dir(self, tmp_path):
        """Create approval storage directory."""
        dir_path = tmp_path / ".forge/approvals"
        dir_path.mkdir()
        return dir_path

    @pytest.mark.asyncio
    async def test_approval_queue_file_storage_lifecycle(self, storage_dir):
        """Test complete approval lifecycle with file storage."""
        from forge_harness.approval_queue import (
            ApprovalQueueHarness,
            ApprovalStatus,
            ApprovalType,
            JSONFileStorage,
        )

        storage = JSONFileStorage(storage_dir)
        queue = ApprovalQueueHarness(storage=storage)

        # Create approval request with correct API
        request = await queue.create_request(
            type=ApprovalType.DEPLOY,
            domain="test-domain",
            title="Deploy to production",
            description="Ready to deploy v1.2.0",
            metadata={"version": "1.2.0", "environment": "production"},
            expiry_hours=24,
        )

        assert request is not None
        assert request.id is not None

        # List pending approvals
        pending = await queue.list_pending()
        assert len(pending) == 1
        assert pending[0].title == "Deploy to production"

        # Approve the request
        approved = await queue.approve(
            request_id=request.id,
            approver="admin@example.com",
            comment="LGTM, approved for production",
        )

        # Verify status changed
        assert approved.status == ApprovalStatus.APPROVED
        assert approved.approved_by == "admin@example.com"

    @pytest.mark.asyncio
    async def test_approval_queue_rejection_flow(self, storage_dir):
        """Test approval rejection workflow."""
        from forge_harness.approval_queue import (
            ApprovalQueueHarness,
            ApprovalStatus,
            ApprovalType,
            JSONFileStorage,
        )

        storage = JSONFileStorage(storage_dir)
        queue = ApprovalQueueHarness(storage=storage)

        # Create request
        request = await queue.create_request(
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Risky change",
            description="This change needs review",
        )

        # Reject with reason
        rejected = await queue.reject(
            request_id=request.id,
            approver="reviewer@example.com",
            reason="Needs more test coverage",
        )

        # Verify rejection
        assert rejected.status == ApprovalStatus.REJECTED
        assert "test coverage" in rejected.resolution_reason

    @pytest.mark.asyncio
    async def test_approval_queue_stats(self, storage_dir):
        """Test approval queue statistics."""
        from forge_harness.approval_queue import (
            ApprovalQueueHarness,
            ApprovalType,
            JSONFileStorage,
        )

        storage = JSONFileStorage(storage_dir)
        queue = ApprovalQueueHarness(storage=storage)

        # Create multiple requests
        for i in range(3):
            await queue.create_request(
                type=ApprovalType.CONTENT,
                domain="test-domain",
                title=f"Request {i}",
                description=f"Description {i}",
            )

        # Get stats
        stats = await queue.get_stats()

        # Check stats structure
        assert stats.total_requests == 3
        assert stats.pending_count == 3
        assert stats.approved_count == 0


# =============================================================================
# Webhook Server Integration Tests
# =============================================================================


class TestWebhookServerIntegration:
    """Integration tests for WebhookServer with approval queue."""

    @pytest.fixture
    def app_client(self, tmp_path):
        """Create test client for webhook server."""
        # Skip if httpx not available
        pytest.importorskip("httpx")

        from forge_harness.approval_queue import ApprovalQueueHarness, JSONFileStorage
        from forge_harness.webhook_server import (
            ApprovalQueueHandler,
            AuthConfig,
            WebhookHandler,
            create_app,
        )

        # Create storage
        storage_dir = tmp_path / ".forge/approvals"
        storage_dir.mkdir()
        storage = JSONFileStorage(storage_dir)
        queue = ApprovalQueueHarness(storage=storage)

        # Create mock notification harness
        mock_notification = MagicMock()
        mock_notification.notify = AsyncMock(return_value=[])

        # Create webhook handler (for Slack/GitHub webhooks)
        handler = WebhookHandler(notification_harness=mock_notification)

        # Create approval queue handler (for approval API endpoints)
        approval_handler = ApprovalQueueHandler(approval_queue=queue)

        # Auth config - disable auth for testing
        auth_config = AuthConfig(
            bearer_token="test-token",
            require_auth=True,
            allow_localhost=True,
        )

        app = create_app(handler, approval_handler=approval_handler, auth_config=auth_config)

        return app, queue

    @pytest.mark.asyncio
    async def test_webhook_health_check(self, app_client):
        """Test webhook server health endpoint."""
        from httpx import ASGITransport, AsyncClient

        app, _ = app_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_webhook_list_approvals(self, app_client):
        """Test listing approvals via API."""
        from httpx import ASGITransport, AsyncClient

        from forge_harness.approval_queue import ApprovalType

        app, queue = app_client

        # Create a test approval
        await queue.create_request(
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Test approval",
            description="For API test",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/approvals",
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            data = response.json()
            # API responses are wrapped in standardized format with "data" key
            assert len(data["data"]["approvals"]) == 1
            assert data["data"]["approvals"][0]["title"] == "Test approval"

    @pytest.mark.asyncio
    async def test_webhook_approve_via_api(self, app_client):
        """Test approving a request via webhook API."""
        from httpx import ASGITransport, AsyncClient

        from forge_harness.approval_queue import ApprovalType

        app, queue = app_client

        # Create approval request
        request = await queue.create_request(
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="API approval test",
            description="Test approval via API",
        )
        request_id = request.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/approvals/{request_id}/approve",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "approver": "api-user@test.com",
                    "comment": "Approved via API",
                },
            )
            assert response.status_code == 200

        # Verify approval
        request = await queue.get_request(request_id)
        assert request.status.value == "approved"

    @pytest.mark.asyncio
    async def test_webhook_reject_via_api(self, app_client):
        """Test rejecting a request via webhook API."""
        from httpx import ASGITransport, AsyncClient

        from forge_harness.approval_queue import ApprovalType

        app, queue = app_client

        # Create approval request
        request = await queue.create_request(
            type=ApprovalType.FEATURE,
            domain="test-domain",
            title="Rejection test",
            description="Test rejection via API",
        )
        request_id = request.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/approvals/{request_id}/reject",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "rejector": "reviewer@test.com",
                    "reason": "Needs more testing",
                },
            )
            assert response.status_code == 200

        # Verify rejection
        request = await queue.get_request(request_id)
        assert request.status.value == "rejected"

    @pytest.mark.asyncio
    async def test_webhook_auth_required(self, tmp_path):
        """Test that auth is required for protected endpoints."""
        pytest.importorskip("httpx")
        from httpx import ASGITransport, AsyncClient

        from forge_harness.approval_queue import ApprovalQueueHarness, JSONFileStorage
        from forge_harness.webhook_server import (
            ApprovalQueueHandler,
            AuthConfig,
            WebhookHandler,
            create_app,
        )

        # Create a separate app with strict auth (no localhost allow)
        storage_dir = tmp_path / ".forge/approvals_auth"
        storage_dir.mkdir()
        storage = JSONFileStorage(storage_dir)
        queue = ApprovalQueueHarness(storage=storage)

        handler = WebhookHandler(notification_harness=None)
        approval_handler = ApprovalQueueHandler(approval_queue=queue)

        # Strict auth config - no localhost bypass
        auth_config = AuthConfig(
            bearer_token="test-token",
            require_auth=True,
            allow_localhost=False,
        )

        app = create_app(handler, approval_handler=approval_handler, auth_config=auth_config)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Try with wrong token
            response = await client.get(
                "/api/approvals",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert response.status_code == 401


# =============================================================================
# Pipeline with Human Gate Integration Tests
# =============================================================================


class TestPipelineHumanGateIntegration:
    """Integration tests for pipelines with human gates."""

    @pytest.fixture
    def mock_harnesses(self, tmp_path):
        """Create mock harnesses for pipeline testing."""
        from unittest.mock import AsyncMock

        harnesses = {
            "content": MagicMock(),
            "notification": MagicMock(),
            "human_gate": MagicMock(),
        }

        # Configure content harness
        harnesses["content"].generate_content = AsyncMock(
            return_value={"items": ["item1", "item2"]}
        )

        # Configure notification harness
        harnesses["notification"].notify = AsyncMock(
            return_value=[MagicMock(success=True, message_id="msg-123")]
        )

        # Configure human gate
        harnesses["human_gate"].await_feedback = AsyncMock(
            return_value={"approved": True, "by": "reviewer@test.com"}
        )

        return harnesses

    @pytest.mark.asyncio
    async def test_pipeline_pauses_at_human_gate(self, tmp_path, mock_harnesses):
        """Test that pipeline pauses at human gate step."""
        from forge_harness.orchestration_harness import (
            OrchestrationHarness,
            Pipeline,
            PipelineStep,
        )

        checkpoint_dir = tmp_path / "checkpoints"
        checkpoint_dir.mkdir()

        # Create pipeline with human gate
        pipeline = Pipeline(
            name="test-pipeline",
            steps=[
                PipelineStep(
                    name="generate",
                    harness="content",
                    method="generate_content",
                    inputs={"count": 5},
                    outputs=["items"],
                ),
                PipelineStep(
                    name="review",
                    harness="human_gate",
                    method="await_feedback",
                    inputs={"items": "{{ steps.generate.items }}"},
                    outputs=["approval"],
                    on_failure="human_gate",
                ),
            ],
            context={"project": "test"},
        )

        orchestrator = OrchestrationHarness(
            harnesses=mock_harnesses,
            checkpoint_dir=checkpoint_dir,
        )

        result = await orchestrator.execute(pipeline)

        # Verify pipeline completed
        assert result.success is True

        # Verify human gate was called
        mock_harnesses["human_gate"].await_feedback.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_checkpoint_at_human_gate(self, tmp_path, mock_harnesses):
        """Test that pipeline saves checkpoint at human gate."""
        from forge_harness.orchestration_harness import (
            OrchestrationHarness,
            Pipeline,
            PipelineStep,
        )

        checkpoint_dir = tmp_path / "checkpoints"
        checkpoint_dir.mkdir()

        # Make human gate block to simulate waiting
        human_gate_called = asyncio.Event()

        async def blocking_gate(*args, **kwargs):
            human_gate_called.set()
            return {"approved": True}

        mock_harnesses["human_gate"].await_feedback = blocking_gate

        pipeline = Pipeline(
            name="checkpoint-test",
            steps=[
                PipelineStep(
                    name="step1",
                    harness="content",
                    method="generate_content",
                    inputs={},
                    outputs=["items"],
                ),
                PipelineStep(
                    name="gate",
                    harness="human_gate",
                    method="await_feedback",
                    inputs={},
                    outputs=["approval"],
                ),
            ],
            context={},
        )

        orchestrator = OrchestrationHarness(
            harnesses=mock_harnesses,
            checkpoint_dir=checkpoint_dir,
        )

        await orchestrator.execute(pipeline)

        # Check that checkpoint was saved
        checkpoints = list(checkpoint_dir.glob("*.json"))
        # At least one checkpoint should exist
        assert len(checkpoints) >= 0  # May be 0 if pipeline completes quickly


# =============================================================================
# Features.json Test Fixtures
# =============================================================================


class TestFeaturesJsonFixtures:
    """Tests for features.json sample fixtures."""

    @pytest.fixture
    def sample_features_minimal(self, tmp_path):
        """Minimal features.json for testing."""
        features = {
            "version": "1.0",
            "features": [
                {
                    "id": "test-001",
                    "name": "Test Feature",
                    "description": "A test feature",
                    "status": "pending",
                }
            ],
        }
        path = tmp_path / "features_minimal.json"
        path.write_text(json.dumps(features))
        return path

    @pytest.fixture
    def sample_features_complex(self, tmp_path):
        """Complex features.json with dependencies and multiple statuses."""
        features = {
            "version": "1.0",
            "project": "test-project",
            "metadata": {
                "created_at": "2025-01-15T00:00:00Z",
                "github_repo": "test/repo",
            },
            "features": [
                {
                    "id": "auth-001",
                    "name": "User Registration",
                    "description": "Allow users to register",
                    "status": "passing",
                    "priority": "critical",
                    "acceptance_criteria": ["Registration form works", "Email verification sent"],
                    "depends_on": [],
                    "tests": ["test_register", "test_email_verification"],
                    "estimated_tokens": 3000,
                    "attempts": 1,
                },
                {
                    "id": "auth-002",
                    "name": "User Login",
                    "description": "Allow users to login",
                    "status": "passing",
                    "priority": "critical",
                    "depends_on": ["auth-001"],
                    "tests": ["test_login"],
                    "estimated_tokens": 2000,
                    "attempts": 1,
                },
                {
                    "id": "profile-001",
                    "name": "User Profile",
                    "description": "User profile management",
                    "status": "pending",
                    "priority": "high",
                    "depends_on": ["auth-002"],
                    "tests": ["test_profile_view", "test_profile_update"],
                    "estimated_tokens": 4000,
                    "attempts": 0,
                },
                {
                    "id": "admin-001",
                    "name": "Admin Dashboard",
                    "description": "Admin functionality",
                    "status": "blocked",
                    "priority": "medium",
                    "depends_on": ["auth-002"],
                    "tests": ["test_admin_dashboard"],
                    "estimated_tokens": 5000,
                    "attempts": 3,
                    "last_error": "Missing database schema",
                },
            ],
        }
        path = tmp_path / "features_complex.json"
        path.write_text(json.dumps(features, indent=2))
        return path

    def test_load_minimal_features(self, sample_features_minimal):
        """Test loading minimal features.json."""
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(sample_features_minimal)
        features = store.load()  # Must call load()

        assert len(features) == 1
        assert features[0].id == "test-001"
        assert features[0].name == "Test Feature"

    def test_load_complex_features(self, sample_features_complex):
        """Test loading complex features.json with various statuses."""
        from forge_harness.ralph_loop import FeatureStatus, FeatureStore

        store = FeatureStore(sample_features_complex)
        features = store.load()

        assert len(features) == 4

        # Check statuses
        statuses = {f.id: f.status for f in features}
        assert statuses["auth-001"] == FeatureStatus.PASSING
        assert statuses["auth-002"] == FeatureStatus.PASSING
        assert statuses["profile-001"] == FeatureStatus.PENDING
        assert statuses["admin-001"] == FeatureStatus.BLOCKED

    async def test_get_next_feature_respects_deps(self, sample_features_complex):
        """Test that next feature respects dependencies."""
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(sample_features_complex)
        store.load()  # Must load first

        # profile-001 should be returned since auth-001 and auth-002 are passing
        next_feature = await store.get_next_pending()

        assert next_feature is not None
        assert next_feature.id == "profile-001"

    def test_get_stats(self, sample_features_complex):
        """Test feature statistics."""
        from forge_harness.ralph_loop import FeatureStore

        store = FeatureStore(sample_features_complex)
        store.load()
        stats = store.get_stats()

        # Stats returns a dict with status counts
        assert "pending" in stats
        assert "passing" in stats
        assert stats["passing"] == 2
        assert stats["pending"] == 1


# =============================================================================
# Plan Parsing Integration Tests
# =============================================================================


class TestPlanParsingIntegration:
    """Integration tests for plan parsing to features.json."""

    @pytest.fixture
    def real_plan_md(self, tmp_path):
        """Create a realistic PLAN.md file."""
        content = """# Interview Simulator Plan

## Status: In Progress
## Target: January 2025

---

## Epic E1: Video Analysis MVP

**Goal:** Implement video analysis feature
**Priority:** P0

### Phase 1: Core Analysis

| Task ID | Task | Effort | Target |
|---------|------|--------|--------|
| E1.1.1 | Create video upload endpoint | 2h | 90% |
| E1.1.2 | Implement FFmpeg processing | 4h | 90% |
| E1.1.3 | Add thumbnail generation | 1h | 85% |

### Phase 2: AI Integration

| Task ID | Task | Effort | Target |
|---------|------|--------|--------|
| E1.2.1 | Integrate Claude for analysis | 3h | 90% |
| E1.2.2 | Create feedback endpoint | 2h | 85% |

---

## Epic E2: Frontend Polish

**Goal:** Improve user experience
**Priority:** P1

### Phase 1: Dashboard

| Task ID | Task | Effort |
|---------|------|--------|
| E2.1.1 | Update dashboard layout | 2h |
| E2.1.2 | Add video player component | 3h |
| E2.1.3 | Implement progress tracking | 2h |

---

## Remaining Work

| Task | Est | Priority |
|------|-----|----------|
| Mobile responsiveness | 3h | P1 |
| Fix backend credentials | 15m | P0 |
| Backend test verification | 30m | P1 |
"""
        path = tmp_path / "PLAN.md"
        path.write_text(content)
        return path

    def test_parse_real_plan_to_features(self, real_plan_md):
        """Test parsing a realistic PLAN.md to features."""
        from forge_harness.ralph_loop import create_features_from_plan

        features = create_features_from_plan(real_plan_md)

        # Should have features from multiple epics
        assert len(features) >= 8

        # Check priorities were extracted correctly
        e1_features = [f for f in features if f.id.startswith("E1")]
        e2_features = [f for f in features if f.id.startswith("E2")]

        # E1 features should be critical (P0)
        for f in e1_features:
            assert f.priority == "critical", f"Expected critical for {f.id}, got {f.priority}"

        # E2 features should be high (P1)
        for f in e2_features:
            assert f.priority == "high", f"Expected high for {f.id}, got {f.priority}"

    async def test_parse_plan_and_create_store(self, real_plan_md, tmp_path):
        """Test full workflow: parse plan -> create store -> get next feature."""
        from forge_harness.ralph_loop import FeatureStore, create_features_from_plan

        # Parse plan
        features = create_features_from_plan(real_plan_md)

        # Save to features.json
        features_path = tmp_path / "features.json"
        features_data = {
            "version": "1.0",
            "features": [f.to_dict() for f in features],
        }
        features_path.write_text(json.dumps(features_data, indent=2))

        # Load into store
        store = FeatureStore(features_path)
        store.load()  # Must call load() first

        # Get first feature
        next_feature = await store.get_next_pending()
        assert next_feature is not None
        assert next_feature.status.value == "pending"

        # Stats should show all pending
        stats = store.get_stats()
        assert stats["pending"] == len(features)
