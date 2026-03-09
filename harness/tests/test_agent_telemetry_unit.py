"""
Unit tests for forge_harness.agent_telemetry

Covers:
- TelemetryConfig dataclass: init, auto session_id, from_env
- AgentTelemetry: register, heartbeat, report_progress, report_error,
  complete, start_heartbeat_loop, stop_heartbeat_loop, _heartbeat_loop
- Factory functions: create_agent_telemetry, create_agent_telemetry_from_env
- Edge cases and error paths
"""

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from forge_harness.agent_telemetry import (
    AgentTelemetry,
    TelemetryConfig,
    create_agent_telemetry,
    create_agent_telemetry_from_env,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def base_config():
    """Minimal valid TelemetryConfig."""
    return TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="test-token",
        domain="test-domain",
        project="test-project",
        role="feature-dev",
    )


@pytest.fixture
def telemetry(base_config):
    """AgentTelemetry with base config, telemetry enabled."""
    return AgentTelemetry(base_config)


@pytest.fixture
def registered_telemetry(base_config):
    """AgentTelemetry that is pre-set as registered with a known agent ID."""
    t = AgentTelemetry(base_config)
    t._agent_id = "agent-abc"
    t._registered = True
    return t


def _make_http_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Helper: build a minimal mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# =============================================================================
# TelemetryConfig Tests
# =============================================================================


class TestTelemetryConfig:
    def test_required_fields_stored(self):
        config = TelemetryConfig(
            webhook_url="http://example.com",
            token="tok",
            domain="d",
            project="p",
            role="r",
        )
        assert config.webhook_url == "http://example.com"
        assert config.token == "tok"
        assert config.domain == "d"
        assert config.project == "p"
        assert config.role == "r"

    def test_session_id_auto_generated_when_none(self):
        config = TelemetryConfig(
            webhook_url="http://x", token="t", domain="d", project="p", role="r"
        )
        assert config.session_id is not None
        assert len(config.session_id) == 8

    def test_session_id_not_overwritten_when_provided(self):
        config = TelemetryConfig(
            webhook_url="http://x",
            token="t",
            domain="d",
            project="p",
            role="r",
            session_id="custom-id",
        )
        assert config.session_id == "custom-id"

    def test_defaults(self):
        config = TelemetryConfig(
            webhook_url="http://x", token="t", domain="d", project="p", role="r"
        )
        assert config.name is None
        assert config.parent_id is None
        assert config.tmux_session is None
        assert config.skills == []
        assert config.heartbeat_interval == 30
        assert config.enabled is True

    def test_skills_field_isolation(self):
        """Each instance should get its own skills list."""
        c1 = TelemetryConfig(
            webhook_url="u", token="t", domain="d", project="p", role="r"
        )
        c2 = TelemetryConfig(
            webhook_url="u", token="t", domain="d", project="p", role="r"
        )
        c1.skills.append("skill-a")
        assert c2.skills == []

    # ------------------------------------------------------------------
    # from_env tests
    # ------------------------------------------------------------------

    def test_from_env_basic(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "env-token")
        monkeypatch.delenv("FORGE_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("FORGE_DOMAIN", raising=False)
        monkeypatch.delenv("FORGE_PROJECT", raising=False)
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_NAME", raising=False)
        monkeypatch.delenv("FORGE_PARENT_AGENT_ID", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)
        monkeypatch.delenv("FORGE_HEARTBEAT_INTERVAL", raising=False)
        monkeypatch.delenv("FORGE_TELEMETRY_ENABLED", raising=False)

        config = TelemetryConfig.from_env()
        assert config.token == "env-token"
        assert config.webhook_url == "http://localhost:8000"
        assert config.domain == "unknown"
        assert config.project == "unknown"
        assert config.role == "builder"
        assert config.skills == []
        assert config.heartbeat_interval == 30
        assert config.enabled is True

    def test_from_env_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("FORGE_WEBHOOK_TOKEN", raising=False)
        with pytest.raises(ValueError, match="FORGE_WEBHOOK_TOKEN"):
            TelemetryConfig.from_env()

    def test_from_env_overrides(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.setenv("FORGE_WEBHOOK_URL", "http://custom:9000")
        monkeypatch.setenv("FORGE_DOMAIN", "env-domain")
        monkeypatch.setenv("FORGE_PROJECT", "env-project")
        monkeypatch.setenv("FORGE_AGENT_NAME", "my-agent")
        monkeypatch.setenv("FORGE_PARENT_AGENT_ID", "parent-007")
        monkeypatch.setenv("FORGE_AGENT_SKILLS", "skill-a,skill-b")
        monkeypatch.setenv("FORGE_HEARTBEAT_INTERVAL", "60")
        monkeypatch.setenv("FORGE_TELEMETRY_ENABLED", "false")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)

        config = TelemetryConfig.from_env()
        assert config.webhook_url == "http://custom:9000"
        assert config.domain == "env-domain"
        assert config.project == "env-project"
        assert config.name == "my-agent"
        assert config.parent_id == "parent-007"
        assert config.skills == ["skill-a", "skill-b"]
        assert config.heartbeat_interval == 60
        assert config.enabled is False

    def test_from_env_domain_project_args_override_env(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.setenv("FORGE_DOMAIN", "env-domain")
        monkeypatch.setenv("FORGE_PROJECT", "env-project")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        config = TelemetryConfig.from_env(domain="arg-domain", project="arg-project")
        assert config.domain == "arg-domain"
        assert config.project == "arg-project"

    def test_from_env_tmux_session_env_var(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.setenv("TMUX_SESSION", "my-session")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        config = TelemetryConfig.from_env()
        assert config.tmux_session == "my-session"

    def test_from_env_tmux_fallback_from_tmux_var(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,12345,0")
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        config = TelemetryConfig.from_env()
        assert config.tmux_session == "tmux:12345"

    def test_from_env_telemetry_enabled_variants(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        for truthy in ("true", "1", "yes"):
            monkeypatch.setenv("FORGE_TELEMETRY_ENABLED", truthy)
            c = TelemetryConfig.from_env()
            assert c.enabled is True, f"Expected enabled=True for '{truthy}'"

        for falsy in ("false", "0", "no", ""):
            monkeypatch.setenv("FORGE_TELEMETRY_ENABLED", falsy)
            c = TelemetryConfig.from_env()
            assert c.enabled is False, f"Expected enabled=False for '{falsy}'"


# =============================================================================
# AgentTelemetry property tests
# =============================================================================


class TestAgentTelemetryProperties:
    def test_agent_id_starts_none(self, telemetry):
        assert telemetry.agent_id is None

    def test_is_registered_starts_false(self, telemetry):
        assert telemetry.is_registered is False

    def test_agent_id_reflects_internal(self, telemetry):
        telemetry._agent_id = "xyz"
        assert telemetry.agent_id == "xyz"

    def test_is_registered_reflects_internal(self, telemetry):
        telemetry._registered = True
        assert telemetry.is_registered is True


# =============================================================================
# AgentTelemetry.register Tests
# =============================================================================


class TestAgentTelemetryRegister:
    @pytest.mark.asyncio
    async def test_register_disabled_returns_none(self, base_config):
        base_config.enabled = False
        t = AgentTelemetry(base_config)
        result = await t.register()
        assert result is None
        assert not t.is_registered

    @pytest.mark.asyncio
    async def test_register_already_registered_returns_existing_id(self, registered_telemetry):
        result = await registered_telemetry.register()
        assert result == "agent-abc"

    @pytest.mark.asyncio
    async def test_register_success(self, telemetry):
        mock_response = _make_http_response(
            200, json_data={"success": True, "data": {"id": "new-agent-id"}}
        )
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await telemetry.register()

        assert result == "new-agent-id"
        assert telemetry.agent_id == "new-agent-id"
        assert telemetry.is_registered is True

    @pytest.mark.asyncio
    async def test_register_with_optional_fields(self):
        """Optional fields (name, parent_id, tmux_session, skills) appear in payload."""
        config = TelemetryConfig(
            webhook_url="http://x",
            token="t",
            domain="d",
            project="p",
            role="r",
            name="agent-name",
            parent_id="parent-1",
            tmux_session="tmux:42",
            skills=["skill-a"],
        )
        t = AgentTelemetry(config)

        mock_response = _make_http_response(
            200, json_data={"success": True, "data": {"id": "opt-id"}}
        )
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        captured_payload: dict[str, Any] = {}

        async def capture_post(url, json=None, headers=None):
            captured_payload.update(json or {})
            return mock_response

        mock_client.post = capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            await t.register()

        assert captured_payload.get("name") == "agent-name"
        assert captured_payload.get("parent_id") == "parent-1"
        assert captured_payload.get("tmux_session") == "tmux:42"
        assert captured_payload.get("skills") == ["skill-a"]

    @pytest.mark.asyncio
    async def test_register_unexpected_response_returns_none(self, telemetry):
        mock_response = _make_http_response(200, json_data={"success": False})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await telemetry.register()

        assert result is None
        assert not telemetry.is_registered

    @pytest.mark.asyncio
    async def test_register_non_200_returns_none(self, telemetry):
        mock_response = _make_http_response(401, text="Unauthorized")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await telemetry.register()

        assert result is None
        assert not telemetry.is_registered

    @pytest.mark.asyncio
    async def test_register_exception_returns_none(self, telemetry):
        with patch("httpx.AsyncClient", side_effect=Exception("connection refused")):
            result = await telemetry.register()

        assert result is None
        assert not telemetry.is_registered

    @pytest.mark.asyncio
    async def test_register_response_missing_data_key(self, telemetry):
        mock_response = _make_http_response(200, json_data={"success": True})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await telemetry.register()

        assert result is None


# =============================================================================
# AgentTelemetry.heartbeat Tests
# =============================================================================


class TestAgentTelemetryHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_disabled_returns_false(self, base_config):
        base_config.enabled = False
        t = AgentTelemetry(base_config)
        t._registered = True
        t._agent_id = "id"
        assert await t.heartbeat() is False

    @pytest.mark.asyncio
    async def test_heartbeat_not_registered_returns_false(self, telemetry):
        # enabled but not registered
        assert await telemetry.heartbeat() is False

    @pytest.mark.asyncio
    async def test_heartbeat_no_agent_id_returns_false(self, telemetry):
        telemetry._registered = True
        telemetry._agent_id = None
        assert await telemetry.heartbeat() is False

    @pytest.mark.asyncio
    async def test_heartbeat_success(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.heartbeat()

        assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat_non_200_returns_false(self, registered_telemetry):
        mock_response = _make_http_response(500, text="Server error")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.heartbeat()

        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_exception_returns_false(self, registered_telemetry):
        with patch("httpx.AsyncClient", side_effect=Exception("timeout")):
            result = await registered_telemetry.heartbeat()

        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_url_contains_agent_id(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await registered_telemetry.heartbeat()

        call_url = mock_client.post.call_args[0][0]
        assert "agent-abc" in call_url
        assert call_url.endswith("/heartbeat")


# =============================================================================
# AgentTelemetry.report_progress Tests
# =============================================================================


class TestAgentTelemetryReportProgress:
    @pytest.mark.asyncio
    async def test_report_progress_disabled_returns_false(self, base_config):
        base_config.enabled = False
        t = AgentTelemetry(base_config)
        t._registered = True
        t._agent_id = "id"
        assert await t.report_progress(50) is False

    @pytest.mark.asyncio
    async def test_report_progress_not_registered_returns_false(self, telemetry):
        assert await telemetry.report_progress(50) is False

    @pytest.mark.asyncio
    async def test_report_progress_success_minimal(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.report_progress(75)

        assert result is True
        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["json"]["progress"] == 75

    @pytest.mark.asyncio
    async def test_report_progress_with_all_optional_fields(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        captured: dict[str, Any] = {}

        async def capture_post(url, json=None, headers=None):
            captured.update(json or {})
            return mock_response

        mock_client.post = capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.report_progress(
                progress=60,
                current_task="Writing tests",
                files_modified=["tests/test_a.py"],
                token_usage={"input": 100, "output": 200},
            )

        assert result is True
        assert captured["progress"] == 60
        assert captured["current_task"] == "Writing tests"
        assert captured["files_modified"] == ["tests/test_a.py"]
        assert captured["token_usage"] == {"input": 100, "output": 200}

    @pytest.mark.asyncio
    async def test_report_progress_optional_fields_absent_when_none(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        captured: dict[str, Any] = {}

        async def capture_post(url, json=None, headers=None):
            captured.update(json or {})
            return mock_response

        mock_client.post = capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            await registered_telemetry.report_progress(progress=10)

        assert "current_task" not in captured
        assert "files_modified" not in captured
        assert "token_usage" not in captured

    @pytest.mark.asyncio
    async def test_report_progress_non_200_returns_false(self, registered_telemetry):
        mock_response = _make_http_response(404, text="Not found")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.report_progress(50)

        assert result is False

    @pytest.mark.asyncio
    async def test_report_progress_exception_returns_false(self, registered_telemetry):
        with patch("httpx.AsyncClient", side_effect=RuntimeError("network error")):
            result = await registered_telemetry.report_progress(50)

        assert result is False


# =============================================================================
# AgentTelemetry.report_error Tests
# =============================================================================


class TestAgentTelemetryReportError:
    @pytest.mark.asyncio
    async def test_report_error_disabled_returns_false(self, base_config):
        base_config.enabled = False
        t = AgentTelemetry(base_config)
        t._registered = True
        t._agent_id = "id"
        assert await t.report_error("oops") is False

    @pytest.mark.asyncio
    async def test_report_error_not_registered_returns_false(self, telemetry):
        assert await telemetry.report_error("oops") is False

    @pytest.mark.asyncio
    async def test_report_error_success_200(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.report_error("test error")

        assert result is True

    @pytest.mark.asyncio
    async def test_report_error_success_201(self, registered_telemetry):
        """status_code 201 is also accepted."""
        mock_response = _make_http_response(201)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.report_error("test error")

        assert result is True

    @pytest.mark.asyncio
    async def test_report_error_payload_contains_required_fields(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        captured: dict[str, Any] = {}

        async def capture_post(url, json=None, headers=None):
            captured.update(json or {})
            return mock_response

        mock_client.post = capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            await registered_telemetry.report_error(
                "something broke", context={"file": "auth.py"}
            )

        assert captured["error"] == "something broke"
        assert "timestamp" in captured
        assert captured["context"] == {"file": "auth.py"}

    @pytest.mark.asyncio
    async def test_report_error_no_context_omits_context_key(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        captured: dict[str, Any] = {}

        async def capture_post(url, json=None, headers=None):
            captured.update(json or {})
            return mock_response

        mock_client.post = capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            await registered_telemetry.report_error("bare error")

        assert "context" not in captured

    @pytest.mark.asyncio
    async def test_report_error_non_200_returns_false(self, registered_telemetry):
        mock_response = _make_http_response(500, text="Server error")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.report_error("oops")

        assert result is False

    @pytest.mark.asyncio
    async def test_report_error_exception_returns_false(self, registered_telemetry):
        with patch("httpx.AsyncClient", side_effect=Exception("timeout")):
            result = await registered_telemetry.report_error("oops")

        assert result is False


# =============================================================================
# AgentTelemetry.complete Tests
# =============================================================================


class TestAgentTelemetryComplete:
    @pytest.mark.asyncio
    async def test_complete_disabled_returns_false(self, base_config):
        base_config.enabled = False
        t = AgentTelemetry(base_config)
        t._registered = True
        t._agent_id = "id"
        assert await t.complete() is False

    @pytest.mark.asyncio
    async def test_complete_not_registered_returns_false(self, telemetry):
        assert await telemetry.complete() is False

    @pytest.mark.asyncio
    async def test_complete_success(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.complete()

        assert result is True

    @pytest.mark.asyncio
    async def test_complete_with_summary(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        captured: dict[str, Any] = {}

        async def capture_post(url, json=None, headers=None):
            captured.update(json or {})
            return mock_response

        mock_client.post = capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            await registered_telemetry.complete(summary={"files": 5, "tests": 10})

        assert captured["status"] == "completed"
        assert captured["summary"] == {"files": 5, "tests": 10}

    @pytest.mark.asyncio
    async def test_complete_payload_status_completed(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        captured: dict[str, Any] = {}

        async def capture_post(url, json=None, headers=None):
            captured.update(json or {})
            return mock_response

        mock_client.post = capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            await registered_telemetry.complete()

        assert captured["status"] == "completed"
        assert "summary" not in captured

    @pytest.mark.asyncio
    async def test_complete_non_200_returns_false(self, registered_telemetry):
        mock_response = _make_http_response(500, text="Server error")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await registered_telemetry.complete()

        assert result is False

    @pytest.mark.asyncio
    async def test_complete_exception_returns_false(self, registered_telemetry):
        with patch("httpx.AsyncClient", side_effect=Exception("timeout")):
            result = await registered_telemetry.complete()

        assert result is False

    @pytest.mark.asyncio
    async def test_complete_stops_heartbeat_loop(self, registered_telemetry):
        """complete() should invoke stop_heartbeat_loop."""
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        registered_telemetry.stop_heartbeat_loop = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            await registered_telemetry.complete()

        registered_telemetry.stop_heartbeat_loop.assert_awaited_once()


# =============================================================================
# AgentTelemetry heartbeat loop Tests
# =============================================================================


class TestAgentTelemetryHeartbeatLoop:
    @pytest.mark.asyncio
    async def test_start_heartbeat_loop_not_registered_skips(self, telemetry):
        await telemetry.start_heartbeat_loop()
        assert telemetry._heartbeat_task is None

    @pytest.mark.asyncio
    async def test_start_heartbeat_loop_disabled_skips(self, base_config):
        base_config.enabled = False
        t = AgentTelemetry(base_config)
        t._registered = True
        await t.start_heartbeat_loop()
        assert t._heartbeat_task is None

    @pytest.mark.asyncio
    async def test_start_heartbeat_loop_already_running_skips(self, registered_telemetry):
        """If task already exists, a second call should not replace it."""
        dummy_task = MagicMock()
        registered_telemetry._heartbeat_task = dummy_task
        await registered_telemetry.start_heartbeat_loop()
        # Task should remain the same
        assert registered_telemetry._heartbeat_task is dummy_task

    @pytest.mark.asyncio
    async def test_start_and_stop_heartbeat_loop(self, registered_telemetry):
        """Start the loop, verify the task is created, then stop it cleanly."""
        heartbeat_calls = []

        async def mock_heartbeat():
            heartbeat_calls.append(1)
            return True

        registered_telemetry.heartbeat = mock_heartbeat
        registered_telemetry.config.heartbeat_interval = 0  # immediate tick

        await registered_telemetry.start_heartbeat_loop()
        assert registered_telemetry._heartbeat_task is not None

        # Let the loop run briefly
        await asyncio.sleep(0.05)

        await registered_telemetry.stop_heartbeat_loop()
        assert registered_telemetry._heartbeat_task is None

    @pytest.mark.asyncio
    async def test_stop_heartbeat_loop_no_op_when_no_task(self, telemetry):
        """stop_heartbeat_loop should be a no-op when no task is running."""
        assert telemetry._heartbeat_task is None
        await telemetry.stop_heartbeat_loop()  # should not raise
        assert telemetry._heartbeat_task is None

    @pytest.mark.asyncio
    async def test_heartbeat_loop_stops_on_event(self, registered_telemetry):
        """The internal loop should exit once _stop_heartbeat is set."""
        registered_telemetry.config.heartbeat_interval = 0

        heartbeat_mock = AsyncMock(return_value=True)
        registered_telemetry.heartbeat = heartbeat_mock

        # Start the loop task directly
        registered_telemetry._stop_heartbeat.clear()
        task = asyncio.create_task(registered_telemetry._heartbeat_loop())
        await asyncio.sleep(0.05)

        # Signal stop
        registered_telemetry._stop_heartbeat.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert task.done()

    @pytest.mark.asyncio
    async def test_heartbeat_loop_cancelled_error_handled(self, registered_telemetry):
        """CancelledError inside _heartbeat_loop should be caught gracefully."""
        async def slow_sleep(_):
            raise asyncio.CancelledError()

        registered_telemetry.heartbeat = AsyncMock(return_value=True)
        registered_telemetry.config.heartbeat_interval = 100

        with patch("asyncio.sleep", side_effect=slow_sleep):
            # Should not propagate CancelledError
            task = asyncio.create_task(registered_telemetry._heartbeat_loop())
            await asyncio.wait_for(task, timeout=2.0)

        assert task.done()

    @pytest.mark.asyncio
    async def test_heartbeat_loop_generic_exception_handled(self, registered_telemetry):
        """An unexpected Exception inside _heartbeat_loop should be caught, not propagate."""
        call_count = 0

        async def flaky_heartbeat():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("unexpected boom")

        registered_telemetry.heartbeat = flaky_heartbeat
        registered_telemetry.config.heartbeat_interval = 0

        task = asyncio.create_task(registered_telemetry._heartbeat_loop())
        await asyncio.wait_for(task, timeout=2.0)

        assert task.done()
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_stop_heartbeat_loop_timeout_cancels_task(self, registered_telemetry):
        """When wait_for times out, the task is cancelled gracefully."""
        # Create a task that never finishes on its own
        async def never_ending():
            await asyncio.sleep(9999)

        task = asyncio.create_task(never_ending())
        registered_telemetry._heartbeat_task = task

        # Patch wait_for to raise TimeoutError immediately
        async def instant_timeout(coro, timeout):
            # Cancel the awaitable to avoid task leak
            coro.cancel() if hasattr(coro, "cancel") else None
            raise TimeoutError("forced timeout")

        with patch("asyncio.wait_for", side_effect=instant_timeout):
            await registered_telemetry.stop_heartbeat_loop()

        assert registered_telemetry._heartbeat_task is None
        # Clean up the dangling task
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_stop_heartbeat_loop_exception_handled(self, registered_telemetry):
        """A generic Exception from wait_for is handled silently."""
        async def never_ending():
            await asyncio.sleep(9999)

        task = asyncio.create_task(never_ending())
        registered_telemetry._heartbeat_task = task

        async def raise_generic(coro, timeout):
            raise ValueError("some weird error")

        with patch("asyncio.wait_for", side_effect=raise_generic):
            await registered_telemetry.stop_heartbeat_loop()

        assert registered_telemetry._heartbeat_task is None
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# =============================================================================
# HTTP auth header tests
# =============================================================================


class TestAuthorizationHeader:
    @pytest.mark.asyncio
    async def test_register_sends_bearer_token(self, telemetry):
        mock_response = _make_http_response(
            200, json_data={"success": True, "data": {"id": "x"}}
        )
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await telemetry.register()

        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_heartbeat_sends_bearer_token(self, registered_telemetry):
        mock_response = _make_http_response(200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await registered_telemetry.heartbeat()

        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"


# =============================================================================
# Factory function tests
# =============================================================================


class TestCreateAgentTelemetry:
    def test_create_agent_telemetry_explicit_args(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "env-tok")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        t = create_agent_telemetry(
            webhook_url="http://custom:9000",
            token="explicit-token",
            domain="my-domain",
            project="my-project",
            role="debug",
            session_id="ses-001",
            name="my-agent",
            parent_id="parent-001",
            tmux_session="tmux:99",
            skills=["s1", "s2"],
            heartbeat_interval=60,
            enabled=True,
        )

        assert isinstance(t, AgentTelemetry)
        assert t.config.webhook_url == "http://custom:9000"
        assert t.config.token == "explicit-token"
        assert t.config.domain == "my-domain"
        assert t.config.project == "my-project"
        assert t.config.role == "debug"
        assert t.config.session_id == "ses-001"
        assert t.config.name == "my-agent"
        assert t.config.parent_id == "parent-001"
        assert t.config.tmux_session == "tmux:99"
        assert t.config.skills == ["s1", "s2"]
        assert t.config.heartbeat_interval == 60
        assert t.config.enabled is True

    def test_create_agent_telemetry_disabled(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        t = create_agent_telemetry(enabled=False)
        assert t.config.enabled is False

    def test_create_agent_telemetry_default_heartbeat_unchanged(self, monkeypatch):
        """When heartbeat_interval is 30 (default), it should not override env-based config."""
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.setenv("FORGE_HEARTBEAT_INTERVAL", "45")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        t = create_agent_telemetry()  # heartbeat_interval defaults to 30 (no override)
        # Since heartbeat_interval==30 (the sentinel), the env value (45) is preserved
        assert t.config.heartbeat_interval == 45

    def test_create_agent_telemetry_custom_heartbeat_overrides(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.setenv("FORGE_HEARTBEAT_INTERVAL", "45")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        t = create_agent_telemetry(heartbeat_interval=10)
        assert t.config.heartbeat_interval == 10

    def test_create_agent_telemetry_returns_agent_telemetry_instance(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        t = create_agent_telemetry()
        assert isinstance(t, AgentTelemetry)


class TestCreateAgentTelemetryFromEnv:
    def test_returns_agent_telemetry(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        t = create_agent_telemetry_from_env()
        assert isinstance(t, AgentTelemetry)

    def test_domain_project_role_forwarded(self, monkeypatch):
        monkeypatch.setenv("FORGE_WEBHOOK_TOKEN", "tok")
        monkeypatch.delenv("TMUX_SESSION", raising=False)
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("FORGE_AGENT_SKILLS", raising=False)

        t = create_agent_telemetry_from_env(
            domain="my-domain", project="my-project", role="reviewer"
        )
        assert t.config.domain == "my-domain"
        assert t.config.project == "my-project"
        assert t.config.role == "reviewer"

    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("FORGE_WEBHOOK_TOKEN", raising=False)
        with pytest.raises(ValueError, match="FORGE_WEBHOOK_TOKEN"):
            create_agent_telemetry_from_env()
