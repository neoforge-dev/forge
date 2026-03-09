"""Tests for dispatch_api module.

Tests cover:
- API-first dispatch with tmux fallback
- DispatchResult dataclass
- Health checks
- Bulk dispatch
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from forge_harness.fleet.dispatch_api import (
    FORGE_SESSION,
    AgentInfo,
    DispatchResult,
    check_tmux_health,
    dispatch_to_agent,
    get_dispatch_health,
    get_registered_agents,
    verify_dispatch,
)


class TestDispatchResult:
    """Test DispatchResult dataclass."""

    def test_dispatch_result_success(self):
        """Test successful dispatch result."""
        result = DispatchResult(
            success=True,
            agent_id="forge:opencode",
            message="Implement feature X",
            method="api",
            timestamp=datetime.now(),
        )

        assert result.success is True
        assert result.agent_id == "forge:opencode"
        assert result.method == "api"
        assert result.error is None

    def test_dispatch_result_failure(self):
        """Test failed dispatch result."""
        result = DispatchResult(
            success=False,
            agent_id="forge:gemini",
            message="Research task",
            method="tmux",
            timestamp=datetime.now(),
            error="tmux not available",
        )

        assert result.success is False
        assert result.error == "tmux not available"


class TestAgentInfo:
    """Test AgentInfo dataclass."""

    def test_agent_info_creation(self):
        """Test creating AgentInfo."""
        agent = AgentInfo(
            id="forge:codex",
            name="codex",
            tmux_window="forge:codex",
            role="frontend",
            is_available=True,
        )

        assert agent.id == "forge:codex"
        assert agent.is_available is True


class TestDispatchToAgent:
    """Test dispatch_to_agent function."""

    @pytest.mark.asyncio
    async def test_dispatch_via_api_success(self):
        """Test successful API dispatch."""
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=True)

        result = await dispatch_to_agent(
            agent_id="forge:opencode",
            message="Implement feature X",
            client=mock_client,
            verify=False,
        )

        assert result.success is True
        assert result.method == "api"
        mock_client.send_message.assert_called_once_with(
            "forge:opencode", "Implement feature X"
        )

    @pytest.mark.asyncio
    async def test_dispatch_fallback_to_tmux(self):
        """Test fallback to tmux when API fails."""
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(side_effect=Exception("API unavailable"))

        result = await dispatch_to_agent(
            agent_id="forge:gemini",
            message="Research task",
            client=mock_client,
            verify=False,
        )

        # Should fall back to tmux
        assert result.success is True
        assert result.method == "tmux"

    @pytest.mark.asyncio
    async def test_dispatch_complete_failure(self):
        """Test complete failure when both API and tmux fail."""
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(side_effect=Exception("API unavailable"))

        # Mock tmux to fail
        with patch("forge_harness.fleet.dispatch_api._dispatch_via_tmux", return_value=False):
            result = await dispatch_to_agent(
                agent_id="forge:kimi",
                message="Analysis task",
                client=mock_client,
                verify=False,
            )

        assert result.success is False
        assert result.method == "none"
        assert result.error is not None


class TestVerifyDispatch:
    """Test verify_dispatch function."""

    def test_verify_dispatch_active(self):
        """Test verification when agent is active."""
        with patch("forge_harness.fleet.dispatch_api.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Thinking about the task...",
            )

            result = verify_dispatch("opencode", session="forge")

            assert result is True

    def test_verify_dispatch_inactive(self):
        """Test verification when agent is inactive."""
        with patch("forge_harness.fleet.dispatch_api.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Waiting for input...",
            )

            result = verify_dispatch("codex", session="forge")

            assert result is False


class TestHealthChecks:
    """Test health check functions."""

    @pytest.mark.asyncio
    async def test_check_api_health_success(self):
        """Test API health check when healthy."""
        mock_client = AsyncMock()
        mock_client.list_agents = AsyncMock(return_value=[])

        from forge_harness.fleet.dispatch_api import check_api_health

        result = await check_api_health(mock_client)

        assert result is True

    @pytest.mark.asyncio
    async def test_check_api_health_failure(self):
        """Test API health check when unhealthy."""
        mock_client = AsyncMock()
        mock_client.list_agents = AsyncMock(side_effect=Exception("Connection refused"))

        from forge_harness.fleet.dispatch_api import check_api_health

        result = await check_api_health(mock_client)

        assert result is False

    def test_check_tmux_health_available(self):
        """Test tmux health check when tmux is available."""
        with patch("forge_harness.fleet.dispatch_api.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="tmux 3.3")

            result = check_tmux_health()

            assert result is True

    def test_check_tmux_health_unavailable(self):
        """Test tmux health check when tmux is unavailable."""
        with patch("forge_harness.fleet.dispatch_api.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("tmux not found")

            result = check_tmux_health()

            assert result is False

    @pytest.mark.asyncio
    async def test_get_dispatch_health(self):
        """Test comprehensive dispatch health check."""
        mock_client = AsyncMock()
        mock_client.list_agents = AsyncMock(return_value=[])

        with patch("forge_harness.fleet.dispatch_api.check_api_health", return_value=True):
            with patch("forge_harness.fleet.dispatch_api.check_tmux_health", return_value=True):
                result = await get_dispatch_health()

        assert result["api_healthy"] is True
        assert result["tmux_healthy"] is True
        assert "timestamp" in result


class TestGetRegisteredAgents:
    """Test get_registered_agents function."""

    def test_get_registered_agents_found(self):
        """Test getting registered agents when tmux sessions exist."""
        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0]

            if cmd == ["tmux", "-V"]:
                return MagicMock(returncode=0, stdout="tmux 3.3")
            if "has-session" in cmd:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        with patch("forge_harness.fleet.dispatch_api.subprocess.run", side_effect=mock_run):
            agents = get_registered_agents()

        assert len(agents) > 0
        assert any(a.id.startswith("forge:") for a in agents)

    def test_get_registered_agents_none(self):
        """Test getting registered agents when none exist."""
        with patch("forge_harness.fleet.dispatch_api.subprocess.run") as mock_run:
            mock_run.side_effect = Exception("tmux not available")

            agents = get_registered_agents()

        assert len(agents) == 0


class TestDispatchApiConfiguration:
    """Test dispatch API configuration."""

    def test_forge_session_default(self):
        """Test FORGE_SESSION default value."""
        assert FORGE_SESSION == "forge"
