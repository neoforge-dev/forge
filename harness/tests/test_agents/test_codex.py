"""Tests for CodexExecutor."""

from __future__ import annotations

import subprocess
from unittest.mock import Mock, patch

import pytest

from forge_harness.agents.codex import CodexExecutor


def test_execute_success() -> None:
    executor = CodexExecutor(timeout_seconds=300)

    mock_result = Mock(returncode=0, stdout="ok\n", stderr="")
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = executor.execute("print hello", cwd="/tmp")

    assert result.success is True
    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.stderr == ""
    assert result.error is None
    mock_run.assert_called_once()


def test_execute_failure_nonzero() -> None:
    executor = CodexExecutor(timeout_seconds=300)

    mock_result = Mock(returncode=2, stdout="", stderr="boom")
    with patch("subprocess.run", return_value=mock_result):
        result = executor.execute("fail")

    assert result.success is False
    assert result.exit_code == 2
    assert result.stderr == "boom"
    assert result.error == "nonzero_exit"


def test_execute_timeout() -> None:
    executor = CodexExecutor(timeout_seconds=1)

    exc = subprocess.TimeoutExpired(cmd=["codex", "exec", "slow"], timeout=1)
    exc.stdout = "partial"
    exc.stderr = "waiting"
    with patch("subprocess.run", side_effect=exc):
        result = executor.execute("slow")

    assert result.success is False
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.stdout == "partial"
    assert result.stderr == "waiting"
    assert result.error == "timeout"


def test_execute_timeout_bytes() -> None:
    executor = CodexExecutor(timeout_seconds=1)

    exc = subprocess.TimeoutExpired(cmd=["codex", "exec", "slow"], timeout=1)
    exc.stdout = b"partial"
    exc.stderr = b"waiting"
    with patch("subprocess.run", side_effect=exc):
        result = executor.execute("slow")

    assert result.stdout == "partial"
    assert result.stderr == "waiting"


def test_execute_codex_not_found() -> None:
    executor = CodexExecutor(timeout_seconds=1)

    with patch("subprocess.run", side_effect=FileNotFoundError("codex")):
        result = executor.execute("hello")

    assert result.success is False
    assert result.error == "codex_not_found"


def test_execute_requires_prompt() -> None:
    executor = CodexExecutor()
    with pytest.raises(ValueError):
        executor.execute("")
