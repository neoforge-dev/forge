"""Tests for forge_harness/webhook_server/api/webhooks.py"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = Mock()
    request.json = AsyncMock(return_value={"test": "payload"})
    request.headers = {"X-Slack-Signature": "sig123", "X-Slack-Request-Timestamp": "1234567890"}
    return request


class TestSlackWebhook:
    """Tests for /api/webhooks/slack endpoint."""

    @pytest.mark.asyncio
    async def test_slack_webhook_success(self, mock_request):
        """Test successfully receiving Slack webhook."""
        from forge_harness.webhook_server.api.webhooks import slack_webhook

        response = await slack_webhook(mock_request)

        assert response["status"] == "received"
        assert response["message"] == "Slack webhook processed"
        assert "timestamp" in response

    @pytest.mark.asyncio
    async def test_slack_webhook_error(self, mock_request):
        """Test handling error in Slack webhook."""
        from forge_harness.webhook_server.api.webhooks import slack_webhook

        mock_request.json = AsyncMock(side_effect=Exception("JSON parse error"))

        response = await slack_webhook(mock_request)

        assert response["status"] == "error"
        assert "JSON parse error" in response["message"]
        assert "timestamp" in response


class TestGitHubWebhook:
    """Tests for /api/webhooks/github endpoint."""

    @pytest.mark.asyncio
    async def test_github_webhook_success(self, mock_request):
        """Test successfully receiving GitHub webhook."""
        from forge_harness.webhook_server.api.webhooks import github_webhook

        mock_request.headers = {"X-GitHub-Event": "push"}

        response = await github_webhook(mock_request)

        assert response["status"] == "received"
        assert response["event_type"] == "push"
        assert response["message"] == "GitHub webhook processed"
        assert "timestamp" in response

    @pytest.mark.asyncio
    async def test_github_webhook_unknown_event(self, mock_request):
        """Test GitHub webhook with unknown event type."""
        from forge_harness.webhook_server.api.webhooks import github_webhook

        mock_request.headers = {}

        response = await github_webhook(mock_request)

        assert response["status"] == "received"
        assert response["event_type"] == "unknown"

    @pytest.mark.asyncio
    async def test_github_webhook_error(self, mock_request):
        """Test handling error in GitHub webhook."""
        from forge_harness.webhook_server.api.webhooks import github_webhook

        mock_request.json = AsyncMock(side_effect=Exception("Processing error"))

        response = await github_webhook(mock_request)

        assert response["status"] == "error"
        assert "Processing error" in response["message"]


class TestSlackApprovalsWebhook:
    """Tests for /api/webhooks/slack/approvals endpoint."""

    @pytest.mark.asyncio
    async def test_slack_approvals_webhook_success(self, mock_request):
        """Test successfully receiving Slack approval webhook."""
        from forge_harness.webhook_server.api.webhooks import slack_approvals_webhook

        response = await slack_approvals_webhook(mock_request)

        assert response["status"] == "processed"
        assert response["type"] == "approval"
        assert "timestamp" in response

    @pytest.mark.asyncio
    async def test_slack_approvals_webhook_error(self, mock_request):
        """Test handling error in Slack approval webhook."""
        from forge_harness.webhook_server.api.webhooks import slack_approvals_webhook

        mock_request.json = AsyncMock(side_effect=Exception("Approval error"))

        response = await slack_approvals_webhook(mock_request)

        assert response["status"] == "error"
        assert "Approval error" in response["message"]


class TestTestWebhook:
    """Tests for /api/webhooks/test endpoint."""

    @pytest.mark.asyncio
    async def test_test_webhook_success(self, mock_request):
        """Test webhook test endpoint."""
        from forge_harness.webhook_server.api.webhooks import test_webhook

        test_payload = {"test_key": "test_value", "number": 123}
        mock_request.json = AsyncMock(return_value=test_payload)

        response = await test_webhook(mock_request)

        assert response["status"] == "ok"
        assert response["received"] == test_payload
        assert "timestamp" in response

    @pytest.mark.asyncio
    async def test_test_webhook_empty_payload(self, mock_request):
        """Test webhook test endpoint with empty payload."""
        from forge_harness.webhook_server.api.webhooks import test_webhook

        mock_request.json = AsyncMock(return_value={})

        response = await test_webhook(mock_request)

        assert response["status"] == "ok"
        assert response["received"] == {}

    @pytest.mark.asyncio
    async def test_test_webhook_error(self, mock_request):
        """Test handling error in test webhook."""
        from forge_harness.webhook_server.api.webhooks import test_webhook

        mock_request.json = AsyncMock(side_effect=Exception("Test error"))

        response = await test_webhook(mock_request)

        assert response["status"] == "error"
        assert "Test error" in response["message"]
