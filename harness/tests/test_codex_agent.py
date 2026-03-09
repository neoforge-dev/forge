"""Tests for forge_harness.agents.codex_agent module.

Tests the CodexAgent class which wraps the Codex CLI tool
for programmatic execution.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.agents.codex_agent import (
    AgentResult,
    CodexAgent,
    CodexConfig,
    create_codex_agent,
)

# =============================================================================
# AgentResult Tests
# =============================================================================


class TestAgentResult:
    """Tests for AgentResult dataclass."""

    def test_default_values(self):
        """Test default field values."""
        result = AgentResult(success=True, output="hello", duration_seconds=1.5)
        assert result.success is True
        assert result.output == "hello"
        assert result.duration_seconds == 1.5
        assert result.error is None
        assert result.exit_code == 0
        assert result.files_modified == []
        assert result.tokens_used == 0
        assert result.model == ""

    def test_custom_values(self):
        """Test explicitly set field values."""
        result = AgentResult(
            success=False,
            output="some output",
            duration_seconds=5.0,
            error="something went wrong",
            exit_code=1,
            files_modified=["a.py", "b.py"],
            tokens_used=42,
            model="gpt-4o",
        )
        assert result.success is False
        assert result.error == "something went wrong"
        assert result.exit_code == 1
        assert result.files_modified == ["a.py", "b.py"]
        assert result.tokens_used == 42
        assert result.model == "gpt-4o"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        result = AgentResult(
            success=True,
            output="ok",
            duration_seconds=2.0,
            error=None,
            exit_code=0,
            files_modified=["x.py"],
            tokens_used=10,
            model="gpt-4o",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == "ok"
        assert d["duration_seconds"] == 2.0
        assert d["error"] is None
        assert d["exit_code"] == 0
        assert d["files_modified"] == ["x.py"]
        assert d["tokens_used"] == 10
        assert d["model"] == "gpt-4o"

    def test_to_dict_all_keys_present(self):
        """Test all expected keys are present in serialized dict."""
        result = AgentResult(success=False, output="", duration_seconds=0.0)
        d = result.to_dict()
        expected_keys = {
            "success",
            "output",
            "duration_seconds",
            "error",
            "exit_code",
            "files_modified",
            "tokens_used",
            "model",
        }
        assert set(d.keys()) == expected_keys

    def test_from_dict_full(self):
        """Test deserialization from full dictionary."""
        data = {
            "success": True,
            "output": "result text",
            "duration_seconds": 3.14,
            "error": None,
            "exit_code": 0,
            "files_modified": ["foo.py"],
            "tokens_used": 99,
            "model": "gpt-4-turbo",
        }
        result = AgentResult.from_dict(data)
        assert result.success is True
        assert result.output == "result text"
        assert result.duration_seconds == 3.14
        assert result.error is None
        assert result.exit_code == 0
        assert result.files_modified == ["foo.py"]
        assert result.tokens_used == 99
        assert result.model == "gpt-4-turbo"

    def test_from_dict_minimal(self):
        """Test deserialization from minimal dictionary uses defaults."""
        data = {
            "success": False,
            "output": "",
            "duration_seconds": 0.5,
        }
        result = AgentResult.from_dict(data)
        assert result.success is False
        assert result.error is None
        assert result.exit_code == 0
        assert result.files_modified == []
        assert result.tokens_used == 0
        assert result.model == ""

    def test_roundtrip_serialization(self):
        """Test that to_dict followed by from_dict is lossless."""
        original = AgentResult(
            success=True,
            output="test",
            duration_seconds=1.23,
            error="minor",
            exit_code=0,
            files_modified=["a.py", "b.py"],
            tokens_used=7,
            model="gpt-4o",
        )
        restored = AgentResult.from_dict(original.to_dict())
        assert restored.success == original.success
        assert restored.output == original.output
        assert restored.duration_seconds == original.duration_seconds
        assert restored.error == original.error
        assert restored.exit_code == original.exit_code
        assert restored.files_modified == original.files_modified
        assert restored.tokens_used == original.tokens_used
        assert restored.model == original.model

    def test_files_modified_default_factory(self):
        """Test that files_modified uses independent lists per instance."""
        r1 = AgentResult(success=True, output="", duration_seconds=0.0)
        r2 = AgentResult(success=True, output="", duration_seconds=0.0)
        r1.files_modified.append("x.py")
        assert r2.files_modified == []


# =============================================================================
# CodexConfig Tests
# =============================================================================


class TestCodexConfig:
    """Tests for CodexConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = CodexConfig()
        assert config.timeout == 300.0
        assert config.model == "gpt-4o"
        assert config.auto_approve is True
        assert config.quiet is False
        assert config.json_output is False

    def test_custom_values(self):
        """Test custom configuration values."""
        config = CodexConfig(
            timeout=600.0,
            model="gpt-4-turbo",
            auto_approve=False,
            quiet=True,
            json_output=True,
        )
        assert config.timeout == 600.0
        assert config.model == "gpt-4-turbo"
        assert config.auto_approve is False
        assert config.quiet is True
        assert config.json_output is True

    def test_to_dict(self):
        """Test serialization to dictionary."""
        config = CodexConfig(timeout=120.0, model="gpt-4o", auto_approve=False, quiet=True, json_output=True)
        d = config.to_dict()
        assert d == {
            "timeout": 120.0,
            "model": "gpt-4o",
            "auto_approve": False,
            "quiet": True,
            "json_output": True,
        }

    def test_to_dict_all_keys(self):
        """Test all expected keys are present."""
        config = CodexConfig()
        d = config.to_dict()
        assert set(d.keys()) == {"timeout", "model", "auto_approve", "quiet", "json_output"}

    def test_from_dict_full(self):
        """Test deserialization from full dictionary."""
        data = {
            "timeout": 450.0,
            "model": "gpt-4-turbo",
            "auto_approve": False,
            "quiet": True,
            "json_output": True,
        }
        config = CodexConfig.from_dict(data)
        assert config.timeout == 450.0
        assert config.model == "gpt-4-turbo"
        assert config.auto_approve is False
        assert config.quiet is True
        assert config.json_output is True

    def test_from_dict_minimal_uses_defaults(self):
        """Test deserialization from empty dict uses defaults."""
        config = CodexConfig.from_dict({})
        assert config.timeout == 300.0
        assert config.model == "gpt-4o"
        assert config.auto_approve is True
        assert config.quiet is False
        assert config.json_output is False

    def test_from_dict_partial(self):
        """Test deserialization from partial dictionary."""
        data = {"model": "gpt-4-mini", "timeout": 60.0}
        config = CodexConfig.from_dict(data)
        assert config.model == "gpt-4-mini"
        assert config.timeout == 60.0
        assert config.auto_approve is True  # default

    def test_roundtrip_serialization(self):
        """Test to_dict -> from_dict is lossless."""
        original = CodexConfig(
            timeout=123.0, model="gpt-x", auto_approve=False, quiet=True, json_output=True
        )
        restored = CodexConfig.from_dict(original.to_dict())
        assert restored.timeout == original.timeout
        assert restored.model == original.model
        assert restored.auto_approve == original.auto_approve
        assert restored.quiet == original.quiet
        assert restored.json_output == original.json_output


# =============================================================================
# CodexAgent Initialization Tests
# =============================================================================


class TestCodexAgentInit:
    """Tests for CodexAgent initialization."""

    def test_default_executable(self):
        """Test that EXECUTABLE class attribute is 'codex'."""
        assert CodexAgent.EXECUTABLE == "codex"

    def test_init_with_default_config(self):
        """Test initialization without config uses defaults."""
        agent = CodexAgent()
        assert agent.config.model == "gpt-4o"
        assert agent.config.timeout == 300.0
        assert agent.config.auto_approve is True

    def test_init_with_custom_config(self):
        """Test initialization with custom config."""
        config = CodexConfig(timeout=600.0, model="gpt-4-turbo")
        agent = CodexAgent(config=config)
        assert agent.config is config
        assert agent.config.timeout == 600.0
        assert agent.config.model == "gpt-4-turbo"

    def test_init_with_custom_logger(self):
        """Test initialization with a custom logger."""
        import logging

        custom_logger = logging.getLogger("test_logger")
        agent = CodexAgent(logger=custom_logger)
        assert agent.logger is custom_logger

    def test_init_creates_default_logger(self):
        """Test that a logger is created when none is provided."""
        agent = CodexAgent()
        assert agent.logger is not None


# =============================================================================
# CodexAgent.build_command Tests
# =============================================================================


class TestCodexAgentBuildCommand:
    """Tests for CodexAgent.build_command method."""

    @pytest.fixture
    def agent(self):
        """Agent with a known default config."""
        config = CodexConfig(
            timeout=300.0,
            model="gpt-4o",
            auto_approve=True,
            quiet=False,
            json_output=False,
        )
        return CodexAgent(config=config)

    def test_basic_command_structure(self, agent):
        """Test command starts with 'codex exec'."""
        cmd = agent.build_command("do something")
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"

    def test_model_flag_included(self, agent):
        """Test model flag is present."""
        cmd = agent.build_command("do something")
        assert "-m" in cmd
        model_idx = cmd.index("-m")
        assert cmd[model_idx + 1] == "gpt-4o"

    def test_auto_approve_flag(self, agent):
        """Test auto-approve flag '-y' is added when enabled."""
        cmd = agent.build_command("do something")
        assert "-y" in cmd

    def test_auto_approve_flag_not_added_when_disabled(self):
        """Test auto-approve flag is absent when disabled."""
        config = CodexConfig(auto_approve=False)
        agent = CodexAgent(config=config)
        cmd = agent.build_command("do something")
        assert "-y" not in cmd

    def test_quiet_flag_when_enabled(self):
        """Test --quiet flag is added when quiet=True."""
        config = CodexConfig(quiet=True)
        agent = CodexAgent(config=config)
        cmd = agent.build_command("do something")
        assert "--quiet" in cmd

    def test_quiet_flag_absent_when_disabled(self, agent):
        """Test --quiet flag is absent when quiet=False."""
        cmd = agent.build_command("do something")
        assert "--quiet" not in cmd

    def test_json_output_flag_when_enabled(self):
        """Test --json flag is added when json_output=True."""
        config = CodexConfig(json_output=True)
        agent = CodexAgent(config=config)
        cmd = agent.build_command("do something")
        assert "--json" in cmd

    def test_json_output_flag_absent_when_disabled(self, agent):
        """Test --json flag is absent when json_output=False."""
        cmd = agent.build_command("do something")
        assert "--json" not in cmd

    def test_prompt_is_last_element(self, agent):
        """Test that the prompt is the final element of the command."""
        prompt = "implement feature X"
        cmd = agent.build_command(prompt)
        assert cmd[-1] == prompt

    def test_working_dir_not_in_command(self, agent):
        """Test working_dir does not affect command list."""
        cmd_with = agent.build_command("do something", working_dir=Path("/tmp"))
        cmd_without = agent.build_command("do something")
        assert cmd_with == cmd_without

    def test_kwargs_override_model(self, agent):
        """Test model can be overridden via kwargs."""
        cmd = agent.build_command("do something", model="gpt-4-turbo")
        assert "gpt-4-turbo" in cmd
        assert "gpt-4o" not in cmd

    def test_kwargs_override_auto_approve_false(self, agent):
        """Test auto_approve=False via kwargs disables the -y flag."""
        cmd = agent.build_command("do something", auto_approve=False)
        assert "-y" not in cmd

    def test_kwargs_override_auto_approve_true(self):
        """Test auto_approve=True via kwargs enables the -y flag."""
        config = CodexConfig(auto_approve=False)
        agent = CodexAgent(config=config)
        cmd = agent.build_command("do something", auto_approve=True)
        assert "-y" in cmd

    def test_kwargs_override_quiet(self, agent):
        """Test quiet=True via kwargs enables --quiet."""
        cmd = agent.build_command("do something", quiet=True)
        assert "--quiet" in cmd

    def test_kwargs_override_json_output(self, agent):
        """Test json_output=True via kwargs enables --json."""
        cmd = agent.build_command("do something", json_output=True)
        assert "--json" in cmd

    def test_all_flags_enabled(self):
        """Test command with all optional flags enabled."""
        config = CodexConfig(
            model="gpt-4o", auto_approve=True, quiet=True, json_output=True
        )
        agent = CodexAgent(config=config)
        cmd = agent.build_command("prompt text")
        assert "-m" in cmd
        assert "-y" in cmd
        assert "--quiet" in cmd
        assert "--json" in cmd
        assert cmd[-1] == "prompt text"

    def test_empty_model_skips_flag(self):
        """Test that empty model string skips -m flag."""
        config = CodexConfig(model="")
        agent = CodexAgent(config=config)
        cmd = agent.build_command("do something", model="")
        assert "-m" not in cmd


# =============================================================================
# CodexAgent._parse_output Tests
# =============================================================================


class TestCodexAgentParseOutput:
    """Tests for CodexAgent._parse_output method."""

    @pytest.fixture
    def agent(self):
        return CodexAgent()

    def test_returns_tuple(self, agent):
        """Test that _parse_output returns a tuple of (str, list)."""
        result = agent._parse_output("hello", "")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], list)

    def test_basic_stdout_passthrough(self, agent):
        """Test plain stdout is returned as output."""
        output, files = agent._parse_output("some output", "")
        assert output == "some output"
        assert files == []

    def test_stderr_appended_when_not_error(self, agent):
        """Test non-error stderr is appended to output."""
        output, _ = agent._parse_output("stdout content", "extra info")
        assert "stdout content" in output
        assert "extra info" in output

    def test_stderr_error_prefix_excluded(self, agent):
        """Test stderr starting with 'Error' is not appended to stdout."""
        output, _ = agent._parse_output("stdout text", "Error: something failed")
        # Full output should just be stdout
        assert output == "stdout text"

    def test_parse_modified_pattern(self, agent):
        """Test 'Modified: path' pattern is detected."""
        stdout = "Modified: src/app.py\nDone."
        output, files = agent._parse_output(stdout, "")
        assert "src/app.py" in files

    def test_parse_created_pattern(self, agent):
        """Test 'Created: path' pattern is detected."""
        stdout = "Created: tests/test_new.py\nDone."
        output, files = agent._parse_output(stdout, "")
        assert "tests/test_new.py" in files

    def test_parse_edited_pattern(self, agent):
        """Test 'Edited path' pattern is detected."""
        stdout = "Edited src/utils.py"
        output, files = agent._parse_output(stdout, "")
        assert "src/utils.py" in files

    def test_parse_writing_to_pattern(self, agent):
        """Test 'Writing to path' pattern is detected."""
        stdout = "Writing to config/settings.py"
        output, files = agent._parse_output(stdout, "")
        assert "config/settings.py" in files

    def test_multiple_patterns_combined(self, agent):
        """Test multiple file-detection patterns in one output."""
        stdout = (
            "Modified: a.py\n"
            "Created: b.py\n"
            "Edited c.py\n"
            "Writing to d.py\n"
        )
        output, files = agent._parse_output(stdout, "")
        assert "a.py" in files
        assert "b.py" in files
        assert "c.py" in files
        assert "d.py" in files

    def test_duplicate_files_removed(self, agent):
        """Test duplicate file paths are deduplicated."""
        stdout = "Modified: x.py\nModified: x.py\n"
        _, files = agent._parse_output(stdout, "")
        assert files.count("x.py") == 1

    def test_order_preserved_after_dedup(self, agent):
        """Test that order is preserved when removing duplicates."""
        stdout = "Modified: a.py\nCreated: b.py\nModified: a.py\n"
        _, files = agent._parse_output(stdout, "")
        assert files[0] == "a.py"
        assert files[1] == "b.py"

    def test_empty_stdout_empty_stderr(self, agent):
        """Test both empty strings return empty output and files."""
        output, files = agent._parse_output("", "")
        assert output == ""
        assert files == []

    def test_output_stripped(self, agent):
        """Test output is stripped of leading/trailing whitespace."""
        output, _ = agent._parse_output("  hello world  ", "")
        assert output == "hello world"

    def test_files_paths_stripped(self, agent):
        """Test file paths have whitespace stripped."""
        stdout = "Modified:   spaced/path.py  \n"
        _, files = agent._parse_output(stdout, "")
        assert files[0] == "spaced/path.py"

    def test_stderr_only_when_stdout_empty(self, agent):
        """Test when stdout is empty, stderr (non-error) becomes output."""
        output, _ = agent._parse_output("", "useful info")
        assert output == "useful info"

    def test_files_in_stderr(self, agent):
        """Test file patterns in stderr are detected when appended."""
        stdout = ""
        stderr = "Modified: lib/helper.py"
        _, files = agent._parse_output(stdout, stderr)
        assert "lib/helper.py" in files


# =============================================================================
# CodexAgent.is_available Tests
# =============================================================================


class TestCodexAgentIsAvailable:
    """Tests for CodexAgent.is_available static method."""

    def test_available_when_returncode_zero(self):
        """Test returns True when codex --version exits with code 0."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            available = CodexAgent.is_available()

        assert available is True
        mock_run.assert_called_once_with(
            ["codex", "--version"],
            capture_output=True,
            timeout=10,
        )

    def test_not_available_when_returncode_nonzero(self):
        """Test returns False when codex --version exits with non-zero code."""
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            available = CodexAgent.is_available()

        assert available is False

    def test_not_available_when_file_not_found(self):
        """Test returns False when codex binary is not found."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            available = CodexAgent.is_available()

        assert available is False

    def test_not_available_when_timeout(self):
        """Test returns False when codex --version times out."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=10)):
            available = CodexAgent.is_available()

        assert available is False


# =============================================================================
# CodexAgent.execute (async) Tests
# =============================================================================


class TestCodexAgentExecute:
    """Tests for the async execute method."""

    @pytest.fixture
    def agent(self):
        """Agent with short timeout for tests."""
        config = CodexConfig(timeout=30.0, model="gpt-4o")
        return CodexAgent(config=config)

    @pytest.mark.asyncio
    async def test_execute_success(self, agent):
        """Test successful execution returns AgentResult with success=True."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"task completed", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("implement feature X")

        assert result.success is True
        assert result.exit_code == 0
        assert "task completed" in result.output

    @pytest.mark.asyncio
    async def test_execute_failure_nonzero_exit(self, agent):
        """Test failed execution sets success=False and captures stderr."""
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"fatal error occurred"))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("bad task")

        assert result.success is False
        assert result.exit_code == 1
        assert result.error is not None
        assert "fatal error occurred" in result.error

    @pytest.mark.asyncio
    async def test_execute_success_error_is_none(self, agent):
        """Test that error field is None on success."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"all good", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("some task")

        assert result.error is None

    @pytest.mark.asyncio
    async def test_execute_timeout_kills_process(self, agent):
        """Test timeout kills process and returns timeout error."""
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.communicate = AsyncMock(side_effect=TimeoutError())
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("long running task")

        assert result.success is False
        assert result.exit_code == -1
        assert "timed out" in result.error
        mock_process.kill.assert_called_once()
        mock_process.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_file_not_found(self, agent):
        """Test FileNotFoundError when codex binary missing."""
        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("codex: not found"),
        ):
            result = await agent.execute("some task")

        assert result.success is False
        assert result.exit_code == -1
        assert "codex executable not found" in result.error
        assert "npm install" in result.error

    @pytest.mark.asyncio
    async def test_execute_unexpected_exception(self, agent):
        """Test generic exception is caught and returned as error."""
        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            side_effect=RuntimeError("unexpected boom"),
        ):
            result = await agent.execute("some task")

        assert result.success is False
        assert result.exit_code == -1
        assert "Unexpected error" in result.error
        assert "unexpected boom" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_working_dir(self, agent):
        """Test working directory is passed to subprocess."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"done", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ) as mock_exec:
            await agent.execute("task", working_dir=Path("/workspace"))

        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs["cwd"] == "/workspace"

    @pytest.mark.asyncio
    async def test_execute_without_working_dir(self, agent):
        """Test cwd is None when working_dir is not provided."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"done", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ) as mock_exec:
            await agent.execute("task")

        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs["cwd"] is None

    @pytest.mark.asyncio
    async def test_execute_uses_config_timeout(self, agent):
        """Test the config timeout is used for wait_for."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            with patch(
                "forge_harness.agents.codex_agent.asyncio.wait_for",
                wraps=asyncio.wait_for,
            ) as mock_wait:
                await agent.execute("task")

        mock_wait.assert_called_once()
        _, call_kwargs = mock_wait.call_args
        assert call_kwargs["timeout"] == agent.config.timeout

    @pytest.mark.asyncio
    async def test_execute_kwargs_override_timeout(self, agent):
        """Test timeout kwarg overrides config timeout."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            with patch(
                "forge_harness.agents.codex_agent.asyncio.wait_for",
                wraps=asyncio.wait_for,
            ) as mock_wait:
                await agent.execute("task", timeout=999.0)

        _, call_kwargs = mock_wait.call_args
        assert call_kwargs["timeout"] == 999.0

    @pytest.mark.asyncio
    async def test_execute_model_in_result(self, agent):
        """Test that model used is recorded in AgentResult."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"done", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("task")

        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_execute_model_kwargs_override_in_result(self, agent):
        """Test that kwargs model override is reflected in result."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"done", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("task", model="gpt-4-turbo")

        assert result.model == "gpt-4-turbo"

    @pytest.mark.asyncio
    async def test_execute_duration_is_positive(self, agent):
        """Test that duration_seconds is a positive float."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"done", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("task")

        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_execute_parses_files_modified(self, agent):
        """Test that files_modified are populated from output."""
        stdout = b"Modified: src/main.py\nCreated: tests/test_main.py\nDone."
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(stdout, b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("task")

        assert "src/main.py" in result.files_modified
        assert "tests/test_main.py" in result.files_modified

    @pytest.mark.asyncio
    async def test_execute_stderr_on_failure(self, agent):
        """Test stderr is included in error when exit code is non-zero."""
        mock_process = AsyncMock()
        mock_process.returncode = 2
        mock_process.communicate = AsyncMock(return_value=(b"partial", b"Error: bad input"))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("bad task")

        assert result.error is not None
        assert "Error: bad input" in result.error

    @pytest.mark.asyncio
    async def test_execute_exit_code_on_timeout(self, agent):
        """Test exit_code is -1 on timeout."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=TimeoutError())
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await agent.execute("task")

        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_execute_exit_code_on_file_not_found(self, agent):
        """Test exit_code is -1 when binary not found."""
        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError(),
        ):
            result = await agent.execute("task")

        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_execute_output_empty_on_error(self, agent):
        """Test output is empty string on FileNotFoundError."""
        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError(),
        ):
            result = await agent.execute("task")

        assert result.output == ""

    @pytest.mark.asyncio
    async def test_execute_with_all_flags(self, agent):
        """Test execute with all optional kwargs."""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch(
            "forge_harness.agents.codex_agent.asyncio.create_subprocess_exec",
            return_value=mock_process,
        ) as mock_exec:
            result = await agent.execute(
                "task",
                working_dir=Path("/tmp"),
                model="gpt-4-turbo",
                auto_approve=False,
                quiet=True,
                json_output=True,
            )

        assert result.success is True
        # Verify the command passed to subprocess has the overridden flags
        call_args = mock_exec.call_args[0]  # positional args unpacked as *cmd
        assert "gpt-4-turbo" in call_args
        assert "-y" not in call_args
        assert "--quiet" in call_args
        assert "--json" in call_args


# =============================================================================
# CodexAgent.execute_sync Tests
# =============================================================================


class TestCodexAgentExecuteSync:
    """Tests for the synchronous execute_sync wrapper."""

    @pytest.fixture
    def agent(self):
        return CodexAgent(config=CodexConfig(timeout=30.0))

    def test_execute_sync_success(self, agent):
        """Test sync wrapper returns result from async execute."""
        mock_result = AgentResult(
            success=True,
            output="sync result",
            duration_seconds=0.1,
            model="gpt-4o",
        )

        async def fake_execute(prompt, working_dir=None, **kwargs):
            return mock_result

        with patch.object(agent, "execute", side_effect=fake_execute):
            result = agent.execute_sync("sync task")

        assert result.success is True
        assert result.output == "sync result"

    def test_execute_sync_passes_working_dir(self, agent):
        """Test working_dir is forwarded to async execute."""
        received = {}

        async def fake_execute(prompt, working_dir=None, **kwargs):
            received["working_dir"] = working_dir
            return AgentResult(success=True, output="", duration_seconds=0.0)

        with patch.object(agent, "execute", side_effect=fake_execute):
            agent.execute_sync("task", working_dir=Path("/my/dir"))

        assert received["working_dir"] == Path("/my/dir")

    def test_execute_sync_passes_kwargs(self, agent):
        """Test kwargs are forwarded to async execute."""
        received = {}

        async def fake_execute(prompt, working_dir=None, **kwargs):
            received.update(kwargs)
            return AgentResult(success=True, output="", duration_seconds=0.0)

        with patch.object(agent, "execute", side_effect=fake_execute):
            agent.execute_sync("task", model="gpt-4-turbo", quiet=True)

        assert received.get("model") == "gpt-4-turbo"
        assert received.get("quiet") is True

    def test_execute_sync_failure(self, agent):
        """Test sync wrapper returns failure result correctly."""
        mock_result = AgentResult(
            success=False,
            output="",
            duration_seconds=0.5,
            error="something broke",
            exit_code=1,
        )

        async def fake_execute(prompt, working_dir=None, **kwargs):
            return mock_result

        with patch.object(agent, "execute", side_effect=fake_execute):
            result = agent.execute_sync("bad task")

        assert result.success is False
        assert result.error == "something broke"


# =============================================================================
# create_codex_agent Factory Function Tests
# =============================================================================


class TestCreateCodexAgent:
    """Tests for the create_codex_agent factory function."""

    def test_returns_codex_agent_instance(self):
        """Test factory returns a CodexAgent."""
        agent = create_codex_agent()
        assert isinstance(agent, CodexAgent)

    def test_default_timeout(self):
        """Test factory uses default timeout."""
        agent = create_codex_agent()
        assert agent.config.timeout == 300.0

    def test_default_model(self):
        """Test factory uses default model."""
        agent = create_codex_agent()
        assert agent.config.model == "gpt-4o"

    def test_default_auto_approve(self):
        """Test factory enables auto-approve by default."""
        agent = create_codex_agent()
        assert agent.config.auto_approve is True

    def test_custom_timeout(self):
        """Test factory accepts custom timeout."""
        agent = create_codex_agent(timeout=600.0)
        assert agent.config.timeout == 600.0

    def test_custom_model(self):
        """Test factory accepts custom model."""
        agent = create_codex_agent(model="gpt-4-turbo")
        assert agent.config.model == "gpt-4-turbo"

    def test_custom_auto_approve_false(self):
        """Test factory accepts auto_approve=False."""
        agent = create_codex_agent(auto_approve=False)
        assert agent.config.auto_approve is False

    def test_kwargs_quiet(self):
        """Test factory forwards quiet kwarg to config."""
        agent = create_codex_agent(quiet=True)
        assert agent.config.quiet is True

    def test_kwargs_json_output(self):
        """Test factory forwards json_output kwarg to config."""
        agent = create_codex_agent(json_output=True)
        assert agent.config.json_output is True

    def test_default_quiet_false(self):
        """Test default quiet is False."""
        agent = create_codex_agent()
        assert agent.config.quiet is False

    def test_default_json_output_false(self):
        """Test default json_output is False."""
        agent = create_codex_agent()
        assert agent.config.json_output is False

    def test_all_custom_params(self):
        """Test factory with all custom parameters."""
        agent = create_codex_agent(
            timeout=120.0,
            model="gpt-4-mini",
            auto_approve=False,
            quiet=True,
            json_output=True,
        )
        assert agent.config.timeout == 120.0
        assert agent.config.model == "gpt-4-mini"
        assert agent.config.auto_approve is False
        assert agent.config.quiet is True
        assert agent.config.json_output is True
