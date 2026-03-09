"""Tests for forge_harness/webhook_server/api/config.py"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = Mock()
    request.headers = {}
    request.client = Mock(host="192.168.1.100")
    return request


class TestExtractRequestContext:
    """Tests for _extract_request_context helper."""

    def test_extract_from_x_forwarded_for(self, mock_request):
        """Test extracting IP from X-Forwarded-For header."""
        from forge_harness.webhook_server.api.config import _extract_request_context

        mock_request.headers = {
            "X-Forwarded-For": "203.0.113.195, 70.41.3.18, 150.172.238.178",
            "User-Agent": "Mozilla/5.0",
        }

        ip, user_agent = _extract_request_context(mock_request)

        assert ip == "203.0.113.195"
        assert user_agent == "Mozilla/5.0"

    def test_extract_from_x_real_ip(self, mock_request):
        """Test extracting IP from X-Real-IP header."""
        from forge_harness.webhook_server.api.config import _extract_request_context

        mock_request.headers = {"X-Real-IP": "198.51.100.42", "User-Agent": "curl/7.64.1"}

        ip, user_agent = _extract_request_context(mock_request)

        assert ip == "198.51.100.42"
        assert user_agent == "curl/7.64.1"

    def test_extract_from_client_host(self, mock_request):
        """Test extracting IP from client host."""
        from forge_harness.webhook_server.api.config import _extract_request_context

        mock_request.headers = {"User-Agent": "PostmanRuntime/7.26.8"}

        ip, user_agent = _extract_request_context(mock_request)

        assert ip == "192.168.1.100"
        assert user_agent == "PostmanRuntime/7.26.8"

    def test_extract_with_no_request(self):
        """Test extraction when no request provided."""
        from forge_harness.webhook_server.api.config import _extract_request_context

        ip, user_agent = _extract_request_context(None)

        assert ip is None
        assert user_agent is None

    def test_extract_with_no_client(self, mock_request):
        """Test extraction when request has no client."""
        from forge_harness.webhook_server.api.config import _extract_request_context

        mock_request.client = None

        ip, user_agent = _extract_request_context(mock_request)

        assert ip is None


class TestGetLLMConfig:
    """Tests for GET /api/config/llm endpoint."""

    @pytest.mark.asyncio
    async def test_get_llm_config(self):
        """Test getting LLM configuration."""
        from forge_harness.webhook_server.api.config import get_llm_config

        response = await get_llm_config()

        assert "llm" in response
        assert "timestamp" in response
        assert response["llm"]["provider"] == "claude"
        assert response["llm"]["model"] == "claude-sonnet-4-5"


class TestUpdateLLMConfig:
    """Tests for POST /api/config/llm endpoint."""

    @pytest.mark.asyncio
    async def test_update_llm_config_provider(self, mock_request):
        """Test updating LLM provider."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate, update_llm_config

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.config.get_audit_logger") as mock_logger:
            mock_audit = AsyncMock()
            mock_logger.return_value = mock_audit

            update = LLMConfigUpdate(provider="openai")

            response = await update_llm_config(update, mock_request, _=mock_auth)

            assert response["status"] == "updated"
            assert response["llm"]["provider"] == "openai"
            mock_audit.log_configuration_change.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_llm_config_model(self, mock_request):
        """Test updating LLM model."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate, update_llm_config

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.config.get_audit_logger") as mock_logger:
            mock_audit = AsyncMock()
            mock_logger.return_value = mock_audit

            update = LLMConfigUpdate(model="gpt-4")

            response = await update_llm_config(update, mock_request, _=mock_auth)

            assert response["llm"]["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_update_llm_config_temperature(self, mock_request):
        """Test updating LLM temperature."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate, update_llm_config

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.config.get_audit_logger") as mock_logger:
            mock_audit = AsyncMock()
            mock_logger.return_value = mock_audit

            update = LLMConfigUpdate(temperature=0.9)

            response = await update_llm_config(update, mock_request, _=mock_auth)

            assert response["llm"]["temperature"] == 0.9

    @pytest.mark.asyncio
    async def test_update_llm_config_max_tokens(self, mock_request):
        """Test updating LLM max tokens."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate, update_llm_config

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.config.get_audit_logger") as mock_logger:
            mock_audit = AsyncMock()
            mock_logger.return_value = mock_audit

            update = LLMConfigUpdate(max_tokens=8192)

            response = await update_llm_config(update, mock_request, _=mock_auth)

            assert response["llm"]["max_tokens"] == 8192

    @pytest.mark.asyncio
    async def test_update_llm_config_multiple_fields(self, mock_request):
        """Test updating multiple config fields at once."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate, update_llm_config

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.config.get_audit_logger") as mock_logger:
            mock_audit = AsyncMock()
            mock_logger.return_value = mock_audit

            update = LLMConfigUpdate(provider="anthropic", model="claude-opus-4", temperature=0.5)

            response = await update_llm_config(update, mock_request, _=mock_auth)

            assert response["llm"]["provider"] == "anthropic"
            assert response["llm"]["model"] == "claude-opus-4"
            assert response["llm"]["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_update_llm_config_no_changes(self, mock_request):
        """Test update with no actual changes."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate, update_llm_config

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.config.get_audit_logger") as mock_logger:
            mock_audit = AsyncMock()
            mock_logger.return_value = mock_audit

            update = LLMConfigUpdate()

            response = await update_llm_config(update, mock_request, _=mock_auth)

            # No changes should not log
            mock_audit.log_configuration_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_tracks_old_values(self, mock_request):
        """Test that updates track old values for audit."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate, update_llm_config

        mock_auth = AsyncMock()

        with patch("forge_harness.webhook_server.api.config.get_audit_logger") as mock_logger:
            mock_audit = AsyncMock()
            mock_logger.return_value = mock_audit

            # First update to set a known state
            update1 = LLMConfigUpdate(model="gpt-3.5")
            await update_llm_config(update1, mock_request, _=mock_auth)

            # Second update should track the old value
            update2 = LLMConfigUpdate(model="gpt-4")
            await update_llm_config(update2, mock_request, _=mock_auth)

            # Check that audit was called twice (once for each update)
            assert mock_audit.log_configuration_change.call_count == 2


class TestLLMConfigUpdate:
    """Tests for LLMConfigUpdate model."""

    def test_llm_config_update_all_fields(self):
        """Test creating LLMConfigUpdate with all fields."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate

        config = LLMConfigUpdate(provider="openai", model="gpt-4", temperature=0.8, max_tokens=4000)

        assert config.provider == "openai"
        assert config.model == "gpt-4"
        assert config.temperature == 0.8
        assert config.max_tokens == 4000

    def test_llm_config_update_optional_fields(self):
        """Test LLMConfigUpdate with optional fields."""
        from forge_harness.webhook_server.api.config import LLMConfigUpdate

        config = LLMConfigUpdate(provider="claude")

        assert config.provider == "claude"
        assert config.model is None
        assert config.temperature is None
        assert config.max_tokens is None
