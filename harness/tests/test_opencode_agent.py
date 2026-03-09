"""Tests for forge_harness.agents.opencode_agent module.

Tests the OpenCodeAgent class which wraps the OpenCode CLI tool
for programmatic execution.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.agents.opencode_agent import (
    AgentResult,
    AgentStatus,
    OpenCodeAgent,
    OpenCodeConfig,
    create_opencode_agent,
)


class TestAgentStatus:
    """Tests for AgentStatus enum."""

    def test_status_values(self):
        """Test all status values are defined correctly."""
        assert AgentStatus.SUCCESS.value == "success"
        assert AgentStatus.FAILED.value == "failed"
        assert AgentStatus.TIMEOUT.value == "timeout"
        assert AgentStatus.CANCELLED.value == "cancelled"

    def test_status_string_representation(self):
        """Test string representation of statuses."""
        assert str(AgentStatus.SUCCESS) == "success"
        assert str(AgentStatus.FAILED) == "failed"
        assert str(AgentStatus.TIMEOUT) == "timeout"
        assert str(AgentStatus.CANCELLED) == "cancelled"


class TestOpenCodeConfig:
    """Tests for OpenCodeConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = OpenCodeConfig()
        assert config.model == "cerebras/qwen-3-32b"
        assert config.timeout == 600
        assert config.continue_session is False
        assert config.session_id is None
        assert config.agent_name is None
        assert config.env_vars == {}

    def test_custom_values(self):
        """Test custom configuration values."""
        config = OpenCodeConfig(
            model="anthropic/claude-sonnet-4",
            timeout=300,
            continue_session=True,
            session_id="test-session",
            agent_name="test-agent",
            env_vars={"KEY": "value"},
        )
        assert config.model == "anthropic/claude-sonnet-4"
        assert config.timeout == 300
        assert config.continue_session is True
        assert config.session_id == "test-session"
        assert config.agent_name == "test-agent"
        assert config.env_vars == {"KEY": "value"}


class TestAgentResult:
    """Tests for AgentResult dataclass."""

    def test_default_values(self):
        """Test default result values."""
        result = AgentResult(status=AgentStatus.SUCCESS)
        assert result.status == AgentStatus.SUCCESS
        assert result.output == ""
        assert result.error == ""
        assert result.exit_code == -1
        assert result.duration == 0.0
        assert result.started_at is None
        assert result.completed_at is None
        assert result.command == []
        assert result.working_dir is None

    def test_custom_values(self):
        """Test custom result values."""
        started = datetime.now()
        completed = datetime.now()
        result = AgentResult(
            status=AgentStatus.FAILED,
            output="test output",
            error="test error",
            exit_code=1,
            duration=10.5,
            started_at=started,
            completed_at=completed,
            command=["opencode", "run", "test"],
            working_dir="/test/path",
        )
        assert result.status == AgentStatus.FAILED
        assert result.output == "test output"
        assert result.error == "test error"
        assert result.exit_code == 1
        assert result.duration == 10.5

    def test_success_property_true(self):
        """Test success property returns True for SUCCESS status."""
        result = AgentResult(status=AgentStatus.SUCCESS)
        assert result.success is True

    def test_success_property_false_failed(self):
        """Test success property returns False for FAILED status."""
        result = AgentResult(status=AgentStatus.FAILED)
        assert result.success is False

    def test_success_property_false_timeout(self):
        """Test success property returns False for TIMEOUT status."""
        result = AgentResult(status=AgentStatus.TIMEOUT)
        assert result.success is False

    def test_success_property_false_cancelled(self):
        """Test success property returns False for CANCELLED status."""
        result = AgentResult(status=AgentStatus.CANCELLED)
        assert result.success is False

    def test_summary_success(self):
        """Test summary generation for successful execution."""
        result = AgentResult(
            status=AgentStatus.SUCCESS,
            output="test output content",
            exit_code=0,
            duration=5.5,
        )
        summary = result.summary
        assert "success" in summary
        assert "0" in summary
        assert "5.5" in summary

    def test_summary_with_error(self):
        """Test summary generation with error."""
        result = AgentResult(
            status=AgentStatus.FAILED,
            error="Something went wrong",
            exit_code=1,
            duration=3.2,
        )
        summary = result.summary
        assert "failed" in summary
        assert "Something went wrong" in summary

    def test_summary_output_truncation(self):
        """Test that long output is truncated in summary."""
        long_output = "x" * 1000
        result = AgentResult(
            status=AgentStatus.SUCCESS,
            output=long_output,
            exit_code=0,
            duration=1.0,
        )
        summary = result.summary
        assert len(summary) < len(long_output) + 200


class TestOpenCodeAgent:
    """Tests for OpenCodeAgent class."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return OpenCodeConfig(model="test-model", timeout=60)

    @pytest.fixture
    def agent(self, config):
        """Create agent with test config."""
        return OpenCodeAgent(config)

    def test_default_executable(self):
        """Test default executable name."""
        agent = OpenCodeAgent()
        assert agent.EXECUTABLE == "opencode"

    def test_init_with_config(self, config):
        """Test initialization with config."""
        agent = OpenCodeAgent(config)
        assert agent.config == config
        assert agent._process is None

    def test_init_without_config(self):
        """Test initialization without config uses defaults."""
        agent = OpenCodeAgent()
        assert agent.config.model == "cerebras/qwen-3-32b"
        assert agent.config.timeout == 600


class TestOpenCodeAgentBuildCommand:
    """Tests for command building."""

    @pytest.fixture
    def agent(self):
        """Create agent with default config."""
        return OpenCodeAgent(OpenCodeConfig(model="test-model", timeout=60))

    def test_basic_command(self, agent):
        """Test basic command building."""
        cmd = agent._build_command("test prompt")
        assert cmd[0] == "opencode"
        assert "-m" in cmd
        assert "test-model" in cmd
        assert "run" in cmd
        assert "test prompt" in cmd

    def test_command_with_session_id(self, agent):
        """Test command building with session ID."""
        agent.config.session_id = "my-session"
        cmd = agent._build_command("test prompt")
        assert "-s" in cmd
        assert "my-session" in cmd

    def test_command_with_continue_session(self, agent):
        """Test command building with continue session flag."""
        agent.config.continue_session = True
        cmd = agent._build_command("test prompt")
        assert "-c" in cmd

    def test_command_with_agent_name(self, agent):
        """Test command building with agent name."""
        agent.config.agent_name = "my-agent"
        cmd = agent._build_command("test prompt")
        assert "--agent" in cmd
        assert "my-agent" in cmd


class TestOpenCodeAgentExecute:
    """Tests for execute method."""

    @pytest.fixture
    def agent(self):
        """Create agent with test config."""
        return OpenCodeAgent(OpenCodeConfig(model="test-model", timeout=60))

    @pytest.mark.asyncio
    async def test_execute_success(self, agent):
        """Test successful execution."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"output", b"error"))

        with patch(
            "forge_harness.agents.opencode_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("test prompt", Path("/test"))

            assert result.status == AgentStatus.SUCCESS
            assert result.exit_code == 0
            assert "output" in result.output

    @pytest.mark.asyncio
    async def test_execute_failure(self, agent):
        """Test failed execution."""
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error message"))

        with patch(
            "forge_harness.agents.opencode_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("test prompt", Path("/test"))

            assert result.status == AgentStatus.FAILED
            assert result.exit_code == 1
            assert "error message" in result.error

    @pytest.mark.asyncio
    async def test_execute_timeout(self, agent):
        """Test timeout handling."""
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.communicate = AsyncMock()
        mock_process.communicate.side_effect = TimeoutError()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch(
            "forge_harness.agents.opencode_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("test prompt", Path("/test"))

            assert result.status == AgentStatus.TIMEOUT
            assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_execute_file_not_found(self, agent):
        """Test FileNotFoundError handling."""
        with patch(
            "forge_harness.agents.opencode_agent.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("opencode not found"),
        ):
            result = await agent.execute("test prompt", Path("/test"))

            assert result.status == AgentStatus.FAILED
            assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_custom_timeout(self, agent):
        """Test execution with custom timeout."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"output", b""))

        with patch(
            "forge_harness.agents.opencode_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("test prompt", Path("/test"), timeout=120)

            assert result.status == AgentStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_execute_custom_env(self, agent):
        """Test execution with custom environment variables."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"output", b""))

        with patch(
            "forge_harness.agents.opencode_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("test prompt", Path("/test"), env={"CUSTOM": "value"})

            assert result.status == AgentStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_execute_exception(self, agent):
        """Test exception handling."""
        with patch(
            "forge_harness.agents.opencode_agent.asyncio.create_subprocess_exec",
            side_effect=RuntimeError("unexpected error"),
        ):
            result = await agent.execute("test prompt", Path("/test"))

            assert result.status == AgentStatus.FAILED
            assert "unexpected error" in result.error

    @pytest.mark.asyncio
    async def test_execute_captures_timing(self, agent):
        """Test that execution captures timing information."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"output", b""))

        with patch(
            "forge_harness.agents.opencode_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("test prompt", Path("/test"))

            assert result.started_at is not None
            assert result.completed_at is not None
            assert result.duration > 0
            assert result.command == agent._build_command("test prompt")


class TestOpenCodeAgentCancel:
    """Tests for cancel method."""

    @pytest.fixture
    def agent(self):
        """Create agent with test config."""
        return OpenCodeAgent(OpenCodeConfig(model="test-model", timeout=60))

    @pytest.mark.asyncio
    async def test_cancel_running_process(self, agent):
        """Test cancelling a running process."""
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()

        agent._process = mock_process

        result = await agent.cancel()

        assert result is True
        mock_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_already_finished(self, agent):
        """Test cancelling when process already finished."""
        mock_process = MagicMock()
        mock_process.returncode = 0

        agent._process = mock_process

        result = await agent.cancel()

        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_terminate_timeout_force_kill(self, agent):
        """Test force kill when terminate times out."""
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()

        call_count = 0

        async def wait_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError()
            return None

        mock_process.wait = AsyncMock(side_effect=wait_side_effect)
        mock_process.kill = MagicMock()

        agent._process = mock_process

        result = await agent.cancel()

        assert result is True
        mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_exception(self, agent):
        """Test cancel handles exception."""
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock(side_effect=RuntimeError("error"))
        mock_process.wait = AsyncMock()

        agent._process = mock_process

        result = await agent.cancel()

        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_no_process(self, agent):
        """Test cancel when no process running."""
        agent._process = None

        result = await agent.cancel()

        assert result is False


class TestOpenCodeAgentExecuteSync:
    """Tests for synchronous execution."""

    @pytest.fixture
    def agent(self):
        """Create agent with test config."""
        return OpenCodeAgent(OpenCodeConfig(model="test-model", timeout=60))

    def test_execute_sync(self, agent):
        """Test synchronous execution wrapper."""
        mock_result = AgentResult(
            status=AgentStatus.SUCCESS,
            output="test output",
            exit_code=0,
        )

        with patch.object(agent, "execute", return_value=mock_result):
            result = agent.execute_sync("test prompt", Path("/test"))

            assert result.status == AgentStatus.SUCCESS
            assert result.output == "test output"

    def test_execute_sync_with_timeout(self, agent):
        """Test sync execution with custom timeout."""
        mock_result = AgentResult(status=AgentStatus.SUCCESS)

        with patch.object(agent, "execute", return_value=mock_result):
            result = agent.execute_sync("test prompt", Path("/test"), timeout=300)

            assert result.status == AgentStatus.SUCCESS

    def test_execute_sync_with_env(self, agent):
        """Test sync execution with custom env."""
        mock_result = AgentResult(status=AgentStatus.SUCCESS)

        with patch.object(agent, "execute", return_value=mock_result):
            result = agent.execute_sync("test prompt", Path("/test"), env={"KEY": "value"})

            assert result.status == AgentStatus.SUCCESS


class TestCreateOpencodeAgent:
    """Tests for convenience function."""

    def test_create_with_defaults(self):
        """Test creating agent with default settings."""
        agent = create_opencode_agent()
        assert agent.config.model == "cerebras/qwen-3-32b"
        assert agent.config.timeout == 600

    def test_create_with_custom_values(self):
        """Test creating agent with custom settings."""
        agent = create_opencode_agent(model="custom-model", timeout=300)
        assert agent.config.model == "custom-model"
        assert agent.config.timeout == 300
