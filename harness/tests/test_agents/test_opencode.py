"""Tests for OpenCodeExecutor."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import AsyncMock, Mock, patch

import pytest

from forge_harness.agents.opencode import OpenCodeExecutor, TestResults


def test_execute_success() -> None:
    """Test successful execution."""
    executor = OpenCodeExecutor(timeout_seconds=300)

    mock_result = Mock(returncode=0, stdout="ok\n", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = executor.execute("create hello.py", cwd="/tmp")

    assert result.success is True
    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.stderr == ""
    assert result.error is None
    assert result.files_modified == []
    assert result.test_results is None
    mock_run.assert_called_once()


def test_execute_with_files_modified() -> None:
    """Test parsing of modified files from output."""
    executor = OpenCodeExecutor(timeout_seconds=300)

    output = """
Analyzing task...
Created: src/api/auth.py
Modified: src/api/__init__.py
✓ src/utils/helpers.py
Task complete!
"""
    mock_result = Mock(returncode=0, stdout=output, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        result = executor.execute("create auth module")

    assert result.success is True
    assert len(result.files_modified) == 3
    assert "src/api/auth.py" in result.files_modified
    assert "src/api/__init__.py" in result.files_modified
    assert "src/utils/helpers.py" in result.files_modified


def test_execute_with_test_results() -> None:
    """Test parsing of pytest results from output."""
    executor = OpenCodeExecutor(timeout_seconds=300)

    output = """
Running tests...
=== 10 passed, 2 failed, 1 skipped in 2.34s ===
FAILED tests/test_auth.py::test_invalid_token
FAILED tests/test_auth.py::test_expired_token
"""
    mock_result = Mock(returncode=0, stdout=output, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        result = executor.execute("add auth tests")

    assert result.success is True
    assert result.test_results is not None
    assert result.test_results.passed == 10
    assert result.test_results.failed == 2
    assert result.test_results.skipped == 1
    assert result.test_results.total == 13
    assert len(result.test_results.failures) == 2
    assert "tests/test_auth.py::test_invalid_token" in result.test_results.failures


def test_execute_with_both_files_and_tests() -> None:
    """Test parsing both files modified and test results."""
    executor = OpenCodeExecutor(timeout_seconds=300)

    output = """
Created: tests/test_api.py
Modified: src/api.py
Running tests...
=== 5 passed in 1.23s ===
"""
    mock_result = Mock(returncode=0, stdout=output, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        result = executor.execute("add api tests")

    assert result.success is True
    assert len(result.files_modified) == 2
    assert "tests/test_api.py" in result.files_modified
    assert "src/api.py" in result.files_modified
    assert result.test_results is not None
    assert result.test_results.passed == 5
    assert result.test_results.failed == 0
    assert result.test_results.total == 5


def test_execute_failure_nonzero() -> None:
    """Test handling of nonzero exit code."""
    executor = OpenCodeExecutor(timeout_seconds=300)

    mock_result = Mock(returncode=2, stdout="", stderr="boom")
    with patch("subprocess.run", return_value=mock_result):
        result = executor.execute("fail")

    assert result.success is False
    assert result.exit_code == 2
    assert result.stderr == "boom"
    assert result.error == "nonzero_exit"


def test_execute_timeout() -> None:
    """Test timeout handling."""
    executor = OpenCodeExecutor(timeout_seconds=1)

    exc = subprocess.TimeoutExpired(cmd=["opencode", "run", "slow"], timeout=1)
    exc.stdout = "partial output"
    exc.stderr = "waiting"
    with patch("subprocess.run", side_effect=exc):
        result = executor.execute("slow task")

    assert result.success is False
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.stdout == "partial output"
    assert result.stderr == "waiting"
    assert result.error == "timeout"


def test_execute_timeout_with_files_parsed() -> None:
    """Test that files are parsed even on timeout."""
    executor = OpenCodeExecutor(timeout_seconds=1)

    partial_output = """
Created: src/module.py
Working on more files...
"""
    exc = subprocess.TimeoutExpired(cmd=["opencode", "run", "slow"], timeout=1)
    exc.stdout = partial_output
    exc.stderr = ""
    with patch("subprocess.run", side_effect=exc):
        result = executor.execute("slow task")

    assert result.timed_out is True
    assert len(result.files_modified) == 1
    assert "src/module.py" in result.files_modified


def test_execute_timeout_bytes() -> None:
    """Test timeout handling with bytes output."""
    executor = OpenCodeExecutor(timeout_seconds=1)

    exc = subprocess.TimeoutExpired(cmd=["opencode", "run", "slow"], timeout=1)
    exc.stdout = b"partial"
    exc.stderr = b"waiting"
    with patch("subprocess.run", side_effect=exc):
        result = executor.execute("slow")

    assert result.stdout == "partial"
    assert result.stderr == "waiting"


def test_execute_opencode_not_found() -> None:
    """Test handling of missing opencode CLI."""
    executor = OpenCodeExecutor(timeout_seconds=1)

    with patch("subprocess.run", side_effect=FileNotFoundError("opencode")):
        result = executor.execute("hello")

    assert result.success is False
    assert result.error == "opencode_not_found"
    assert result.exit_code == -1


def test_execute_requires_prompt() -> None:
    """Test that empty prompt raises ValueError."""
    executor = OpenCodeExecutor()
    with pytest.raises(ValueError, match="prompt is required"):
        executor.execute("")


def test_execute_with_model() -> None:
    """Test command building with model parameter."""
    executor = OpenCodeExecutor()

    mock_result = Mock(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        executor.execute("test", model="anthropic/claude-sonnet-4")

    call_args = mock_run.call_args[0][0]
    assert "-m" in call_args
    assert "anthropic/claude-sonnet-4" in call_args


def test_execute_with_session_id() -> None:
    """Test command building with session_id parameter."""
    executor = OpenCodeExecutor()

    mock_result = Mock(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        executor.execute("test", session_id="session123")

    call_args = mock_run.call_args[0][0]
    assert "-s" in call_args
    assert "session123" in call_args


def test_execute_with_agent() -> None:
    """Test command building with agent parameter."""
    executor = OpenCodeExecutor()

    mock_result = Mock(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        executor.execute("test", agent="backend-api")

    call_args = mock_run.call_args[0][0]
    assert "--agent" in call_args
    assert "backend-api" in call_args


def test_execute_with_custom_cwd() -> None:
    """Test execution with custom working directory."""
    executor = OpenCodeExecutor()

    mock_result = Mock(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        executor.execute("test", cwd="/custom/path")

    assert mock_run.call_args[1]["cwd"] == "/custom/path"


def test_execute_with_custom_timeout() -> None:
    """Test execution with custom timeout."""
    executor = OpenCodeExecutor(timeout_seconds=100)

    mock_result = Mock(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        executor.execute("test", timeout_seconds=200)

    assert mock_run.call_args[1]["timeout"] == 200


def test_execute_with_env() -> None:
    """Test execution with custom environment variables."""
    executor = OpenCodeExecutor()

    mock_result = Mock(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        executor.execute("test", env={"CUSTOM_VAR": "value"})

    env = mock_run.call_args[1]["env"]
    assert "CUSTOM_VAR" in env
    assert env["CUSTOM_VAR"] == "value"


def test_is_available_found() -> None:
    """Test is_available when opencode is in PATH."""
    executor = OpenCodeExecutor()

    mock_result = Mock(returncode=0)
    with patch("subprocess.run", return_value=mock_result):
        assert executor.is_available() is True


def test_is_available_not_found() -> None:
    """Test is_available when opencode is not in PATH."""
    executor = OpenCodeExecutor()

    mock_result = Mock(returncode=1)
    with patch("subprocess.run", return_value=mock_result):
        assert executor.is_available() is False


def test_is_available_exception() -> None:
    """Test is_available when check raises exception."""
    executor = OpenCodeExecutor()

    with patch("subprocess.run", side_effect=Exception("error")):
        assert executor.is_available() is False


def test_parse_files_modified_empty() -> None:
    """Test parsing files from output with no files."""
    executor = OpenCodeExecutor()
    files = executor._parse_files_modified("No files here")
    assert files == []


def test_parse_files_modified_duplicates() -> None:
    """Test that duplicate files are not added twice."""
    executor = OpenCodeExecutor()
    output = """
Created: src/api.py
Modified: src/api.py
"""
    files = executor._parse_files_modified(output)
    assert len(files) == 1
    assert "src/api.py" in files


def test_parse_test_results_no_tests() -> None:
    """Test parsing test results when no tests were run."""
    executor = OpenCodeExecutor()
    result = executor._parse_test_results("No test output here")
    assert result is None


def test_parse_test_results_passed_only() -> None:
    """Test parsing test results with only passed tests."""
    executor = OpenCodeExecutor()
    output = "=== 5 passed in 1.23s ==="
    result = executor._parse_test_results(output)
    assert result is not None
    assert result.passed == 5
    assert result.failed == 0
    assert result.skipped == 0
    assert result.total == 5
    assert result.failures == []


def test_parse_test_results_with_failures() -> None:
    """Test parsing test results with failures."""
    executor = OpenCodeExecutor()
    output = """
=== 3 passed, 2 failed in 1.0s ===
FAILED tests/test_one.py::test_a
FAILED tests/test_two.py::test_b
"""
    result = executor._parse_test_results(output)
    assert result is not None
    assert result.passed == 3
    assert result.failed == 2
    assert result.skipped == 0
    assert result.total == 5
    assert len(result.failures) == 2


def test_parse_test_results_with_skipped() -> None:
    """Test parsing test results with skipped tests."""
    executor = OpenCodeExecutor()
    output = "=== 3 passed, 1 failed, 2 skipped in 1.5s ==="
    result = executor._parse_test_results(output)
    assert result is not None
    assert result.passed == 3
    assert result.failed == 1
    assert result.skipped == 2
    assert result.total == 6


@pytest.mark.asyncio
async def test_stream_execute_success() -> None:
    """Test streaming execution."""
    executor = OpenCodeExecutor()

    # Mock asyncio subprocess
    mock_process = AsyncMock()
    mock_process.wait = AsyncMock()

    # Create a mock stream reader with readline that returns lines
    lines = [b"line 1\n", b"line 2\n", b""]
    line_index = 0

    async def mock_readline():
        nonlocal line_index
        if line_index < len(lines):
            result = lines[line_index]
            line_index += 1
            return result
        return b""

    mock_stdout = AsyncMock()
    mock_stdout.readline = mock_readline
    mock_process.stdout = mock_stdout

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        chunks = []
        async for chunk in executor.stream_execute("test prompt"):
            chunks.append(chunk)

    assert len(chunks) == 2
    assert chunks[0] == "line 1\n"
    assert chunks[1] == "line 2\n"


@pytest.mark.asyncio
async def test_stream_execute_timeout() -> None:
    """Test streaming execution timeout."""
    executor = OpenCodeExecutor(timeout_seconds=0.1)

    mock_process = AsyncMock()
    mock_process.kill = AsyncMock()
    mock_process.wait = AsyncMock()

    # Simulate hanging readline
    async def mock_readline():
        await asyncio.sleep(10)  # Longer than timeout
        return b""

    mock_stdout = AsyncMock()
    mock_stdout.readline = mock_readline
    mock_process.stdout = mock_stdout

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        with pytest.raises(asyncio.TimeoutError):
            async for _ in executor.stream_execute("slow"):
                pass

    mock_process.kill.assert_called_once()


@pytest.mark.asyncio
async def test_stream_execute_requires_prompt() -> None:
    """Test that streaming with empty prompt raises ValueError."""
    executor = OpenCodeExecutor()
    with pytest.raises(ValueError, match="prompt is required"):
        async for _ in executor.stream_execute(""):
            pass


@pytest.mark.asyncio
async def test_stream_execute_not_found() -> None:
    """Test streaming when opencode CLI is not found."""
    executor = OpenCodeExecutor()

    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            async for _ in executor.stream_execute("test"):
                pass


def test_test_results_dataclass() -> None:
    """Test TestResults dataclass."""
    result = TestResults(
        passed=5,
        failed=2,
        skipped=1,
        total=8,
        failures=["test_a", "test_b"],
    )
    assert result.passed == 5
    assert result.failed == 2
    assert result.skipped == 1
    assert result.total == 8
    assert len(result.failures) == 2


def test_test_results_dataclass_defaults() -> None:
    """Test TestResults dataclass with default failures."""
    result = TestResults(passed=5, failed=0, skipped=0, total=5)
    assert result.failures == []


def test_opencode_result_dataclass() -> None:
    """Test OpenCodeResult dataclass."""
    from forge_harness.agents.opencode import OpenCodeResult

    result = OpenCodeResult(
        prompt="test",
        stdout="output",
        stderr="",
        exit_code=0,
        duration_seconds=1.5,
        timed_out=False,
        success=True,
        files_modified=["file.py"],
        test_results=TestResults(passed=5, failed=0, skipped=0, total=5),
        error=None,
    )
    assert result.prompt == "test"
    assert result.success is True
    assert len(result.files_modified) == 1
    assert result.test_results is not None


def test_opencode_result_dataclass_defaults() -> None:
    """Test OpenCodeResult dataclass with defaults."""
    from forge_harness.agents.opencode import OpenCodeResult

    result = OpenCodeResult(
        prompt="test",
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        timed_out=False,
        success=True,
    )
    assert result.files_modified == []
    assert result.test_results is None
    assert result.error is None
