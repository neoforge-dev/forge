"""
Tests for Agent Telemetry Module
=================================

Tests automatic agent registration and telemetry integration with Command Center.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

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
def telemetry_config():
    """Create test telemetry configuration."""
    return TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="test-token",
        domain="test-domain",
        project="test-project",
        role="test-agent",
        session_id="test-session",
        heartbeat_interval=1,  # Fast for testing
    )


@pytest.fixture
def telemetry(telemetry_config):
    """Create test telemetry instance."""
    return AgentTelemetry(telemetry_config)


@pytest.fixture
def registered_telemetry(telemetry):
    """Create a telemetry instance already in the registered state."""
    telemetry._registered = True
    telemetry._agent_id = "agent-123"
    return telemetry


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    yield mock_client


@pytest.fixture
def success_response():
    """Build a mock 200 OK response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    return mock_response


@pytest.fixture
def error_response():
    """Build a mock 500 error response."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    return mock_response


# =============================================================================
# TelemetryConfig Tests
# =============================================================================


def test_telemetry_config_direct_construction():
    """Test direct TelemetryConfig construction with required fields."""
    config = TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="my-token",
        domain="my-domain",
        project="my-project",
        role="feature-dev",
    )
    assert config.webhook_url == "http://localhost:8000"
    assert config.token == "my-token"
    assert config.domain == "my-domain"
    assert config.project == "my-project"
    assert config.role == "feature-dev"
    assert config.enabled is True
    assert config.heartbeat_interval == 30
    assert config.skills == []
    assert config.name is None
    assert config.parent_id is None
    assert config.tmux_session is None


def test_telemetry_config_from_env():
    """Test creating TelemetryConfig from environment variables."""
    env = {
        "FORGE_WEBHOOK_URL": "http://webhook.example.com",
        "FORGE_WEBHOOK_TOKEN": "secret-token",
        "FORGE_DOMAIN": "codeswiftr-com",
        "FORGE_PROJECT": "interview-simulator",
    }

    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env(role="feature-dev")

    assert config.webhook_url == "http://webhook.example.com"
    assert config.token == "secret-token"
    assert config.domain == "codeswiftr-com"
    assert config.project == "interview-simulator"
    assert config.role == "feature-dev"
    assert config.session_id is not None  # Auto-generated


def test_telemetry_config_from_env_defaults():
    """Test TelemetryConfig defaults when env vars not set (except required token)."""
    env = {"FORGE_WEBHOOK_TOKEN": "required-token"}
    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env()

    assert config.webhook_url == "http://localhost:8000"
    assert config.token == "required-token"
    assert config.domain == "unknown"
    assert config.project == "unknown"
    assert config.role == "builder"


def test_telemetry_config_from_env_raises_without_token():
    """Test that from_env raises ValueError when FORGE_WEBHOOK_TOKEN is missing."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="FORGE_WEBHOOK_TOKEN"):
            TelemetryConfig.from_env()


def test_telemetry_config_session_id_generation():
    """Test automatic session ID generation."""
    config1 = TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="test",
        domain="test",
        project="test",
        role="test",
    )
    config2 = TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="test",
        domain="test",
        project="test",
        role="test",
    )

    # Each config should get a unique session ID
    assert config1.session_id is not None
    assert config2.session_id is not None
    assert config1.session_id != config2.session_id


def test_telemetry_config_session_id_length():
    """Test that auto-generated session ID is 8 chars (UUID[:8])."""
    config = TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="test",
        domain="test",
        project="test",
        role="test",
    )
    assert len(config.session_id) == 8


def test_telemetry_config_explicit_session_id():
    """Test that an explicit session ID is preserved."""
    config = TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="test",
        domain="test",
        project="test",
        role="test",
        session_id="my-session",
    )
    assert config.session_id == "my-session"


def test_telemetry_config_from_env_with_overrides():
    """Test that explicit domain/project override environment values."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "tok",
        "FORGE_DOMAIN": "env-domain",
        "FORGE_PROJECT": "env-project",
    }
    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env(domain="override-domain", project="override-project")

    assert config.domain == "override-domain"
    assert config.project == "override-project"


def test_telemetry_config_from_env_tmux_session_var():
    """Test tmux session detection from TMUX_SESSION env var."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "tok",
        "TMUX_SESSION": "prya:1",
    }
    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env()
    assert config.tmux_session == "prya:1"


def test_telemetry_config_from_env_tmux_detection():
    """Test tmux session detection from TMUX env var format."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "tok",
        "TMUX": "/tmp/tmux-501/default,12345,0",
    }
    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env()
    assert config.tmux_session == "tmux:12345"


def test_telemetry_config_from_env_skills():
    """Test skills are parsed from comma-separated FORGE_AGENT_SKILLS."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "tok",
        "FORGE_AGENT_SKILLS": "skill1,skill2,skill3",
    }
    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env()
    assert config.skills == ["skill1", "skill2", "skill3"]


def test_telemetry_config_from_env_no_skills():
    """Test skills defaults to empty list when FORGE_AGENT_SKILLS not set."""
    env = {"FORGE_WEBHOOK_TOKEN": "tok"}
    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env()
    assert config.skills == []


def test_telemetry_config_from_env_heartbeat_interval():
    """Test custom heartbeat interval from env."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "tok",
        "FORGE_HEARTBEAT_INTERVAL": "60",
    }
    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env()
    assert config.heartbeat_interval == 60


def test_telemetry_config_from_env_telemetry_disabled():
    """Test telemetry disabled flag from env."""
    for disabled_value in ("false", "0", "no"):
        env = {
            "FORGE_WEBHOOK_TOKEN": "tok",
            "FORGE_TELEMETRY_ENABLED": disabled_value,
        }
        with patch.dict(os.environ, env, clear=True):
            config = TelemetryConfig.from_env()
        assert config.enabled is False, f"Expected disabled for FORGE_TELEMETRY_ENABLED={disabled_value}"


def test_telemetry_config_from_env_telemetry_enabled():
    """Test telemetry enabled flag from env with truthy values."""
    for enabled_value in ("true", "1", "yes"):
        env = {
            "FORGE_WEBHOOK_TOKEN": "tok",
            "FORGE_TELEMETRY_ENABLED": enabled_value,
        }
        with patch.dict(os.environ, env, clear=True):
            config = TelemetryConfig.from_env()
        assert config.enabled is True, f"Expected enabled for FORGE_TELEMETRY_ENABLED={enabled_value}"


def test_telemetry_config_from_env_optional_fields():
    """Test FORGE_AGENT_NAME and FORGE_PARENT_AGENT_ID from env."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "tok",
        "FORGE_AGENT_NAME": "my-agent",
        "FORGE_PARENT_AGENT_ID": "parent-42",
    }
    with patch.dict(os.environ, env, clear=True):
        config = TelemetryConfig.from_env()
    assert config.name == "my-agent"
    assert config.parent_id == "parent-42"


# =============================================================================
# AgentTelemetry property tests
# =============================================================================


def test_agent_telemetry_initial_state(telemetry):
    """Test initial state of AgentTelemetry."""
    assert telemetry.agent_id is None
    assert telemetry.is_registered is False
    assert telemetry._heartbeat_task is None


def test_agent_telemetry_agent_id_property(telemetry):
    """Test agent_id property reflects internal state."""
    telemetry._agent_id = "agent-xyz"
    assert telemetry.agent_id == "agent-xyz"


def test_agent_telemetry_is_registered_property(telemetry):
    """Test is_registered property reflects internal state."""
    assert telemetry.is_registered is False
    telemetry._registered = True
    assert telemetry.is_registered is True


# =============================================================================
# Agent Registration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_register_agent_success(telemetry, mock_httpx_client):
    """Test successful agent registration."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "data": {"id": "agent-123"},
    }
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        agent_id = await telemetry.register()

    assert agent_id == "agent-123"
    assert telemetry.agent_id == "agent-123"
    assert telemetry.is_registered is True

    # Verify API call
    mock_httpx_client.post.assert_called_once()
    call_args = mock_httpx_client.post.call_args
    assert call_args[0][0] == "http://localhost:8000/api/agents/register"
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_register_agent_payload_required_fields(telemetry, mock_httpx_client):
    """Test that required fields are always sent in registration payload."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True, "data": {"id": "agent-1"}}
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await telemetry.register()

    payload = mock_httpx_client.post.call_args[1]["json"]
    assert "role" in payload
    assert "project" in payload
    assert "task" in payload
    assert "domain" in payload
    assert payload["role"] == "test-agent"
    assert payload["project"] == "test-project"
    assert payload["domain"] == "test-domain"
    assert "test-domain/test-project" in payload["task"]


@pytest.mark.asyncio
async def test_register_agent_with_optional_fields(mock_httpx_client):
    """Test agent registration with optional fields."""
    config = TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="test-token",
        domain="test-domain",
        project="test-project",
        role="test-agent",
        name="Test Agent",
        parent_id="parent-123",
        tmux_session="tmux:1234",
        skills=["skill1", "skill2"],
    )
    telemetry = AgentTelemetry(config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "data": {"id": "agent-123"},
    }
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await telemetry.register()

    payload = mock_httpx_client.post.call_args[1]["json"]
    assert payload["name"] == "Test Agent"
    assert payload["parent_id"] == "parent-123"
    assert payload["tmux_session"] == "tmux:1234"
    assert payload["skills"] == ["skill1", "skill2"]


@pytest.mark.asyncio
async def test_register_agent_without_optional_fields(mock_httpx_client):
    """Test agent registration without optional fields omits them from payload."""
    config = TelemetryConfig(
        webhook_url="http://localhost:8000",
        token="test-token",
        domain="test-domain",
        project="test-project",
        role="test-agent",
        # name, parent_id, tmux_session, skills all absent/empty
    )
    telemetry = AgentTelemetry(config)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True, "data": {"id": "agent-1"}}
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await telemetry.register()

    payload = mock_httpx_client.post.call_args[1]["json"]
    assert "name" not in payload
    assert "parent_id" not in payload
    assert "tmux_session" not in payload
    assert "skills" not in payload


@pytest.mark.asyncio
async def test_register_agent_failure_500(telemetry, mock_httpx_client):
    """Test agent registration failure on HTTP 500."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        agent_id = await telemetry.register()

    assert agent_id is None
    assert telemetry.agent_id is None
    assert telemetry.is_registered is False


@pytest.mark.asyncio
async def test_register_agent_unexpected_response_format(telemetry, mock_httpx_client):
    """Test registration when response JSON has unexpected structure."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    # success=False branch
    mock_response.json.return_value = {"success": False, "error": "bad request"}
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        agent_id = await telemetry.register()

    assert agent_id is None
    assert telemetry.is_registered is False


@pytest.mark.asyncio
async def test_register_agent_response_missing_data_key(telemetry, mock_httpx_client):
    """Test registration when response has success=True but no 'data' key."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True}  # No "data" key
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        agent_id = await telemetry.register()

    assert agent_id is None
    assert telemetry.is_registered is False


@pytest.mark.asyncio
async def test_register_agent_already_registered(telemetry):
    """Test that re-registration returns existing agent ID without HTTP call."""
    telemetry._registered = True
    telemetry._agent_id = "existing-123"

    with patch("httpx.AsyncClient") as mock_cls:
        agent_id = await telemetry.register()

    assert agent_id == "existing-123"
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_register_agent_disabled(telemetry):
    """Test registration when telemetry is disabled."""
    telemetry.config.enabled = False

    with patch("httpx.AsyncClient") as mock_cls:
        agent_id = await telemetry.register()

    assert agent_id is None
    assert telemetry.is_registered is False
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_register_agent_network_exception(telemetry, mock_httpx_client):
    """Test registration handles network errors gracefully."""
    mock_httpx_client.post = AsyncMock(side_effect=ConnectionError("Network unreachable"))

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        agent_id = await telemetry.register()

    assert agent_id is None
    assert telemetry.is_registered is False


@pytest.mark.asyncio
async def test_register_agent_httpx_import_error(telemetry):
    """Test registration handles ImportError for httpx gracefully."""
    with patch.dict("sys.modules", {"httpx": None}):
        agent_id = await telemetry.register()

    assert agent_id is None


# =============================================================================
# Heartbeat Tests
# =============================================================================


@pytest.mark.asyncio
async def test_heartbeat_success(registered_telemetry, mock_httpx_client):
    """Test successful heartbeat."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.heartbeat()

    assert success is True
    mock_httpx_client.post.assert_called_once_with(
        "http://localhost:8000/api/agents/agent-123/heartbeat",
        headers={"Authorization": "Bearer test-token"},
    )


@pytest.mark.asyncio
async def test_heartbeat_failure(registered_telemetry, mock_httpx_client):
    """Test heartbeat returns False on non-200 response."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.heartbeat()

    assert success is False


@pytest.mark.asyncio
async def test_heartbeat_not_registered(telemetry):
    """Test heartbeat when agent not registered returns False."""
    success = await telemetry.heartbeat()
    assert success is False


@pytest.mark.asyncio
async def test_heartbeat_disabled(registered_telemetry):
    """Test heartbeat when telemetry is disabled returns False."""
    registered_telemetry.config.enabled = False
    success = await registered_telemetry.heartbeat()
    assert success is False


@pytest.mark.asyncio
async def test_heartbeat_no_agent_id(telemetry):
    """Test heartbeat when registered=True but no agent_id returns False."""
    telemetry._registered = True
    telemetry._agent_id = None
    success = await telemetry.heartbeat()
    assert success is False


@pytest.mark.asyncio
async def test_heartbeat_network_exception(registered_telemetry, mock_httpx_client):
    """Test heartbeat handles exceptions gracefully."""
    mock_httpx_client.post = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.heartbeat()

    assert success is False


# =============================================================================
# Heartbeat Loop Tests
# =============================================================================


@pytest.mark.asyncio
async def test_heartbeat_loop_sends_multiple_beats(registered_telemetry, mock_httpx_client):
    """Test heartbeat loop sends multiple heartbeats over time."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await registered_telemetry.start_heartbeat_loop()
        await asyncio.sleep(2.5)
        await registered_telemetry.stop_heartbeat_loop()

    assert mock_httpx_client.post.call_count >= 2


@pytest.mark.asyncio
async def test_start_heartbeat_loop_idempotent(registered_telemetry, mock_httpx_client):
    """Test that calling start_heartbeat_loop twice does not create a second task."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await registered_telemetry.start_heartbeat_loop()
        first_task = registered_telemetry._heartbeat_task
        await registered_telemetry.start_heartbeat_loop()  # Second call
        second_task = registered_telemetry._heartbeat_task
        await registered_telemetry.stop_heartbeat_loop()

    assert first_task is second_task


@pytest.mark.asyncio
async def test_start_heartbeat_loop_requires_registration(telemetry):
    """Test that start_heartbeat_loop does nothing when agent is not registered."""
    await telemetry.start_heartbeat_loop()
    assert telemetry._heartbeat_task is None


@pytest.mark.asyncio
async def test_start_heartbeat_loop_disabled(registered_telemetry):
    """Test that start_heartbeat_loop does nothing when telemetry is disabled."""
    registered_telemetry.config.enabled = False
    await registered_telemetry.start_heartbeat_loop()
    assert registered_telemetry._heartbeat_task is None


@pytest.mark.asyncio
async def test_stop_heartbeat_loop_no_task(telemetry):
    """Test stop_heartbeat_loop when no task is running is a no-op."""
    # Should not raise
    await telemetry.stop_heartbeat_loop()
    assert telemetry._heartbeat_task is None


@pytest.mark.asyncio
async def test_stop_heartbeat_loop_clears_task(registered_telemetry, mock_httpx_client):
    """Test stop_heartbeat_loop clears the internal task reference."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await registered_telemetry.start_heartbeat_loop()
        assert registered_telemetry._heartbeat_task is not None
        await registered_telemetry.stop_heartbeat_loop()

    assert registered_telemetry._heartbeat_task is None


@pytest.mark.asyncio
async def test_stop_heartbeat_loop_timeout_cancels_task(registered_telemetry):
    """Test stop_heartbeat_loop cancels task on timeout."""
    # Create a task that runs forever so wait_for times out
    async def never_stops():
        await asyncio.sleep(9999)

    registered_telemetry._heartbeat_task = asyncio.create_task(never_stops())

    with patch("asyncio.wait_for", side_effect=TimeoutError):
        await registered_telemetry.stop_heartbeat_loop()

    assert registered_telemetry._heartbeat_task is None


@pytest.mark.asyncio
async def test_stop_heartbeat_loop_generic_exception(registered_telemetry):
    """Test stop_heartbeat_loop handles generic exceptions from wait_for gracefully."""
    async def never_stops():
        await asyncio.sleep(9999)

    registered_telemetry._heartbeat_task = asyncio.create_task(never_stops())

    # Raise a non-TimeoutError exception from wait_for
    with patch("asyncio.wait_for", side_effect=RuntimeError("unexpected")):
        await registered_telemetry.stop_heartbeat_loop()

    # Task ref should still be cleared
    assert registered_telemetry._heartbeat_task is None


@pytest.mark.asyncio
async def test_heartbeat_loop_handles_cancelled_error(registered_telemetry, mock_httpx_client):
    """Test _heartbeat_loop handles CancelledError gracefully."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await registered_telemetry.start_heartbeat_loop()
        # Cancel the task directly
        registered_telemetry._heartbeat_task.cancel()
        try:
            await registered_telemetry._heartbeat_task
        except asyncio.CancelledError:
            pass
        registered_telemetry._heartbeat_task = None


@pytest.mark.asyncio
async def test_heartbeat_loop_cancelled_error_logs_debug(registered_telemetry, mock_httpx_client):
    """Test _heartbeat_loop logs debug message on CancelledError (covers line 461)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        # Run the internal loop directly in a task and cancel it
        task = asyncio.create_task(registered_telemetry._heartbeat_loop())
        await asyncio.sleep(0.05)  # Allow loop to start
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # _heartbeat_loop re-raises after logging


@pytest.mark.asyncio
async def test_heartbeat_loop_handles_unexpected_exception(registered_telemetry):
    """Test _heartbeat_loop handles unexpected exceptions without crashing."""
    async def failing_heartbeat():
        raise RuntimeError("Unexpected boom")

    registered_telemetry.heartbeat = failing_heartbeat

    # Run the loop directly — it should catch the exception and not propagate
    task = asyncio.create_task(registered_telemetry._heartbeat_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# =============================================================================
# Progress Reporting Tests
# =============================================================================


@pytest.mark.asyncio
async def test_report_progress_success(registered_telemetry, mock_httpx_client):
    """Test successful progress reporting."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_progress(
            progress=50,
            current_task="Implementing feature",
            files_modified=["src/main.py"],
            token_usage={"input": 100, "output": 50},
        )

    assert success is True
    call_args = mock_httpx_client.post.call_args
    payload = call_args[1]["json"]
    assert payload["progress"] == 50
    assert payload["current_task"] == "Implementing feature"
    assert payload["files_modified"] == ["src/main.py"]
    assert payload["token_usage"] == {"input": 100, "output": 50}


@pytest.mark.asyncio
async def test_report_progress_minimal(registered_telemetry, mock_httpx_client):
    """Test progress report with only required progress field."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_progress(progress=75)

    assert success is True
    payload = mock_httpx_client.post.call_args[1]["json"]
    assert payload["progress"] == 75
    assert "current_task" not in payload
    assert "files_modified" not in payload
    assert "token_usage" not in payload


@pytest.mark.asyncio
async def test_report_progress_correct_endpoint(registered_telemetry, mock_httpx_client):
    """Test that progress is sent to the correct endpoint URL."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await registered_telemetry.report_progress(progress=10)

    url = mock_httpx_client.post.call_args[0][0]
    assert url == "http://localhost:8000/api/agents/agent-123/progress"


@pytest.mark.asyncio
async def test_report_progress_failure(registered_telemetry, mock_httpx_client):
    """Test progress report returns False on non-200 response."""
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_progress(progress=10)

    assert success is False


@pytest.mark.asyncio
async def test_report_progress_not_registered(telemetry):
    """Test progress report returns False when not registered."""
    success = await telemetry.report_progress(progress=10)
    assert success is False


@pytest.mark.asyncio
async def test_report_progress_disabled(registered_telemetry):
    """Test progress report returns False when telemetry disabled."""
    registered_telemetry.config.enabled = False
    success = await registered_telemetry.report_progress(progress=10)
    assert success is False


@pytest.mark.asyncio
async def test_report_progress_network_exception(registered_telemetry, mock_httpx_client):
    """Test progress report handles network exception gracefully."""
    mock_httpx_client.post = AsyncMock(side_effect=Exception("Timeout"))

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_progress(progress=10)

    assert success is False


# =============================================================================
# Error Reporting Tests
# =============================================================================


@pytest.mark.asyncio
async def test_report_error_success(registered_telemetry, mock_httpx_client):
    """Test successful error reporting."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_error(
            error="Test failed",
            context={"test": "test_auth", "line": 42},
        )

    assert success is True
    payload = mock_httpx_client.post.call_args[1]["json"]
    assert payload["error"] == "Test failed"
    assert payload["context"] == {"test": "test_auth", "line": 42}
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_report_error_201_accepted(registered_telemetry, mock_httpx_client):
    """Test error report succeeds on 201 status code."""
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_error(error="Minor issue")

    assert success is True


@pytest.mark.asyncio
async def test_report_error_without_context(registered_telemetry, mock_httpx_client):
    """Test error report without context omits context field from payload."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_error(error="Something went wrong")

    assert success is True
    payload = mock_httpx_client.post.call_args[1]["json"]
    assert "context" not in payload


@pytest.mark.asyncio
async def test_report_error_correct_endpoint(registered_telemetry, mock_httpx_client):
    """Test that error is sent to the correct endpoint URL."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await registered_telemetry.report_error(error="oops")

    url = mock_httpx_client.post.call_args[0][0]
    assert url == "http://localhost:8000/api/agents/agent-123/error"


@pytest.mark.asyncio
async def test_report_error_failure(registered_telemetry, mock_httpx_client):
    """Test error report returns False on non-200/201 response."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Server Error"
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_error(error="something bad")

    assert success is False


@pytest.mark.asyncio
async def test_report_error_not_registered(telemetry):
    """Test error report returns False when not registered."""
    success = await telemetry.report_error(error="oops")
    assert success is False


@pytest.mark.asyncio
async def test_report_error_disabled(registered_telemetry):
    """Test error report returns False when telemetry disabled."""
    registered_telemetry.config.enabled = False
    success = await registered_telemetry.report_error(error="oops")
    assert success is False


@pytest.mark.asyncio
async def test_report_error_network_exception(registered_telemetry, mock_httpx_client):
    """Test error report handles network exception gracefully."""
    mock_httpx_client.post = AsyncMock(side_effect=RuntimeError("Network gone"))

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.report_error(error="trigger this")

    assert success is False


# =============================================================================
# Completion Tests
# =============================================================================


@pytest.mark.asyncio
async def test_complete_success(registered_telemetry, mock_httpx_client):
    """Test successful agent completion."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.complete(
            summary={"features_completed": 5, "duration": 300}
        )

    assert success is True
    payload = mock_httpx_client.post.call_args[1]["json"]
    assert payload["status"] == "completed"
    assert payload["summary"]["features_completed"] == 5


@pytest.mark.asyncio
async def test_complete_without_summary(registered_telemetry, mock_httpx_client):
    """Test complete without summary omits summary from payload."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.complete()

    assert success is True
    payload = mock_httpx_client.post.call_args[1]["json"]
    assert payload["status"] == "completed"
    assert "summary" not in payload


@pytest.mark.asyncio
async def test_complete_correct_endpoint(registered_telemetry, mock_httpx_client):
    """Test that complete sends to the correct endpoint."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await registered_telemetry.complete()

    url = mock_httpx_client.post.call_args[0][0]
    assert url == "http://localhost:8000/api/agents/agent-123/complete"


@pytest.mark.asyncio
async def test_complete_failure(registered_telemetry, mock_httpx_client):
    """Test complete returns False on non-200 response."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.complete()

    assert success is False


@pytest.mark.asyncio
async def test_complete_not_registered(telemetry):
    """Test complete returns False when not registered."""
    success = await telemetry.complete()
    assert success is False


@pytest.mark.asyncio
async def test_complete_disabled(registered_telemetry):
    """Test complete returns False when telemetry disabled."""
    registered_telemetry.config.enabled = False
    success = await registered_telemetry.complete()
    assert success is False


@pytest.mark.asyncio
async def test_complete_stops_heartbeat_loop(registered_telemetry, mock_httpx_client):
    """Test that complete() stops a running heartbeat loop."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_httpx_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        await registered_telemetry.start_heartbeat_loop()
        assert registered_telemetry._heartbeat_task is not None
        await registered_telemetry.complete()

    assert registered_telemetry._heartbeat_task is None


@pytest.mark.asyncio
async def test_complete_network_exception(registered_telemetry, mock_httpx_client):
    """Test complete handles network exception gracefully."""
    mock_httpx_client.post = AsyncMock(side_effect=OSError("Connection refused"))

    with patch("httpx.AsyncClient", return_value=mock_httpx_client):
        success = await registered_telemetry.complete()

    assert success is False


# =============================================================================
# Factory Function Tests
# =============================================================================


@patch.dict(os.environ, {"FORGE_WEBHOOK_TOKEN": "env-token", "FORGE_WEBHOOK_URL": "http://env.com"})
def test_create_agent_telemetry_with_all_args():
    """Test create_agent_telemetry factory with all arguments supplied."""
    telemetry = create_agent_telemetry(
        webhook_url="http://test.com",
        token="test-token",
        domain="test-domain",
        project="test-project",
        role="test-role",
        session_id="my-session",
        name="my-agent",
        parent_id="parent-1",
        tmux_session="tmux:42",
        skills=["a", "b"],
        heartbeat_interval=60,
        enabled=True,
    )

    assert isinstance(telemetry, AgentTelemetry)
    assert telemetry.config.webhook_url == "http://test.com"
    assert telemetry.config.token == "test-token"
    assert telemetry.config.domain == "test-domain"
    assert telemetry.config.project == "test-project"
    assert telemetry.config.role == "test-role"
    assert telemetry.config.session_id == "my-session"
    assert telemetry.config.name == "my-agent"
    assert telemetry.config.parent_id == "parent-1"
    assert telemetry.config.tmux_session == "tmux:42"
    assert telemetry.config.skills == ["a", "b"]
    assert telemetry.config.heartbeat_interval == 60


@patch.dict(os.environ, {"FORGE_WEBHOOK_TOKEN": "env-token", "FORGE_WEBHOOK_URL": "http://env.com"})
def test_create_agent_telemetry_disabled():
    """Test create_agent_telemetry with enabled=False."""
    telemetry = create_agent_telemetry(
        webhook_url="http://test.com",
        token="test-token",
        domain="d",
        project="p",
        enabled=False,
    )
    assert telemetry.config.enabled is False


@patch.dict(os.environ, {"FORGE_WEBHOOK_TOKEN": "env-token", "FORGE_WEBHOOK_URL": "http://env.com"})
def test_create_agent_telemetry_custom_heartbeat_interval():
    """Test create_agent_telemetry overrides heartbeat_interval when non-default."""
    telemetry = create_agent_telemetry(
        webhook_url="http://test.com",
        token="test-token",
        domain="d",
        project="p",
        heartbeat_interval=45,
    )
    assert telemetry.config.heartbeat_interval == 45


def test_create_agent_telemetry_default_heartbeat_unchanged():
    """Test that default heartbeat_interval=30 does not override env-derived value."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "tok",
        "FORGE_HEARTBEAT_INTERVAL": "120",
    }
    with patch.dict(os.environ, env, clear=True):
        telemetry = create_agent_telemetry(
            webhook_url="http://test.com",
            token="test-token",
            domain="d",
            project="p",
            # heartbeat_interval left at default 30
        )
    # When the caller uses the default 30, we don't override the env-derived value
    assert telemetry.config.heartbeat_interval == 120


def test_create_agent_telemetry_from_env():
    """Test create_agent_telemetry_from_env factory function."""
    env = {
        "FORGE_WEBHOOK_URL": "http://env.com",
        "FORGE_WEBHOOK_TOKEN": "env-token",
    }

    with patch.dict(os.environ, env, clear=True):
        telemetry = create_agent_telemetry_from_env(
            domain="test-domain",
            project="test-project",
            role="test-role",
        )

    assert isinstance(telemetry, AgentTelemetry)
    assert telemetry.config.webhook_url == "http://env.com"
    assert telemetry.config.token == "env-token"
    assert telemetry.config.domain == "test-domain"
    assert telemetry.config.project == "test-project"
    assert telemetry.config.role == "test-role"


def test_create_agent_telemetry_from_env_defaults():
    """Test create_agent_telemetry_from_env uses env-derived defaults."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "env-tok",
        "FORGE_DOMAIN": "env-dom",
        "FORGE_PROJECT": "env-proj",
    }
    with patch.dict(os.environ, env, clear=True):
        telemetry = create_agent_telemetry_from_env()

    assert telemetry.config.domain == "env-dom"
    assert telemetry.config.project == "env-proj"
    assert telemetry.config.role == "builder"


def test_create_agent_telemetry_partial_overrides():
    """Test create_agent_telemetry does not override fields left as None."""
    env = {
        "FORGE_WEBHOOK_TOKEN": "env-tok",
        "FORGE_DOMAIN": "env-dom",
        "FORGE_AGENT_NAME": "env-name",
    }
    with patch.dict(os.environ, env, clear=True):
        # Only supply webhook_url and token — other fields come from env
        telemetry = create_agent_telemetry(
            webhook_url="http://override.com",
            token="override-tok",
            # name not provided — should stay as env-derived "env-name"
        )

    assert telemetry.config.webhook_url == "http://override.com"
    assert telemetry.config.token == "override-tok"
    assert telemetry.config.name == "env-name"
