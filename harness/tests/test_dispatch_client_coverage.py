"""Unit tests for DispatchClient - targeting 60%+ coverage.

This module tests the core dispatch_client functionality that currently has only 29% coverage.
Key areas to test:
- GitMutex (lock detection, waiting, stale lock removal)
- DispatchResult dataclass
- DispatchClient initialization and configuration
- DispatchClient.send() with various scenarios
- Module-level convenience functions (get_client, send, send_sync)
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.fleet.dispatch_client import (
    DispatchClient,
    DispatchResult,
    GitMutex,
    get_client,
    send,
    send_sync,
)
from forge_harness.fleet.retry import CircuitBreaker, FailureType, RetryConfig

# =============================================================================
# GitMutex Tests
# =============================================================================


class TestGitMutex:
    """Tests for GitMutex class."""

    def test_init_with_default_repo_root(self, tmp_path):
        """Test GitMutex initialization with auto-detected repo root."""
        with patch.dict("os.environ", {"FORGE_ROOT": str(tmp_path)}):
            mutex = GitMutex()
            assert mutex.repo_root == tmp_path
            assert mutex.lock_file == tmp_path / ".git" / "index.lock"

    def test_init_with_provided_repo_root(self, tmp_path):
        """Test GitMutex initialization with explicit repo root."""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        mutex = GitMutex(repo_root=repo)
        assert mutex.repo_root == repo
        assert mutex.lock_file == repo / ".git" / "index.lock"

    def test_init_fallback_to_cwd(self, tmp_path):
        """Test GitMutex falls back to cwd when FORGE_ROOT not set."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("pathlib.Path.cwd", return_value=tmp_path):
                mutex = GitMutex()
                assert mutex.repo_root == tmp_path

    def test_is_locked_when_unsafe_state(self, tmp_path):
        """Test is_locked returns True when git is in unsafe state."""
        mutex = GitMutex(repo_root=tmp_path)

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (True, "index.lock exists")
            assert mutex.is_locked() is True

    def test_is_locked_when_safe(self, tmp_path):
        """Test is_locked returns False when git is safe."""
        mutex = GitMutex(repo_root=tmp_path)

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (False, "")
            assert mutex.is_locked() is False

    @pytest.mark.asyncio
    async def test_wait_for_unlock_immediate_release(self, tmp_path):
        """Test wait_for_unlock when lock is released immediately."""
        mutex = GitMutex(repo_root=tmp_path)

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (False, "")
            result = await mutex.wait_for_unlock(timeout=5.0)
            assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_unlock_with_release(self, tmp_path):
        """Test wait_for_unlock waits and succeeds when lock is released."""
        mutex = GitMutex(repo_root=tmp_path)

        call_count = 0

        def side_effect(*args):
            nonlocal call_count
            call_count += 1
            return (call_count < 3, "locked")  # Locked first 2 calls

        with patch(
            "forge_harness.fleet.dispatch_client.check_git_unsafe_state", side_effect=side_effect
        ):
            with patch("asyncio.sleep") as mock_sleep:
                result = await mutex.wait_for_unlock(timeout=5.0)
                assert result is True
                assert call_count == 3
                assert mock_sleep.called

    @pytest.mark.asyncio
    async def test_wait_for_unlock_timeout(self, tmp_path):
        """Test wait_for_unlock returns False on timeout."""
        mutex = GitMutex(repo_root=tmp_path)

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (True, "still locked")
            with patch("asyncio.sleep"):
                start = time.time()
                result = await mutex.wait_for_unlock(timeout=0.1)
                elapsed = time.time() - start
                assert result is False
                assert elapsed < 1.0  # Should timeout quickly

    @pytest.mark.asyncio
    async def test_wait_for_unlock_removes_stale_lock(self, tmp_path):
        """Test wait_for_unlock removes stale lock files (>5 min old)."""
        mutex = GitMutex(repo_root=tmp_path)

        # Create a stale lock file
        git_dir = tmp_path / ".git"
        git_dir.mkdir(parents=True)
        lock_file = git_dir / "index.lock"
        lock_file.write_text("")

        # Set file mtime to 6 minutes ago
        old_time = time.time() - 360
        lock_file.touch()
        import os

        os.utime(lock_file, (old_time, old_time))

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (True, "locked")  # Still reports locked
            with patch("asyncio.sleep"):
                result = await mutex.wait_for_unlock(timeout=5.0)
                assert result is True
                assert not lock_file.exists()  # Should be removed

    def test_wait_for_unlock_sync_immediate(self, tmp_path):
        """Test wait_for_unlock_sync when lock is released immediately."""
        mutex = GitMutex(repo_root=tmp_path)

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (False, "")
            result = mutex.wait_for_unlock_sync(timeout=5.0)
            assert result is True

    def test_wait_for_unlock_sync_with_release(self, tmp_path):
        """Test wait_for_unlock_sync waits and succeeds."""
        mutex = GitMutex(repo_root=tmp_path)

        call_count = 0

        def side_effect(*args):
            nonlocal call_count
            call_count += 1
            return (call_count < 3, "locked")

        with patch(
            "forge_harness.fleet.dispatch_client.check_git_unsafe_state", side_effect=side_effect
        ):
            with patch("time.sleep") as mock_sleep:
                result = mutex.wait_for_unlock_sync(timeout=5.0)
                assert result is True
                assert call_count == 3

    def test_wait_for_unlock_sync_timeout(self, tmp_path):
        """Test wait_for_unlock_sync returns False on timeout."""
        mutex = GitMutex(repo_root=tmp_path)

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (True, "still locked")
            with patch("time.sleep"):
                result = mutex.wait_for_unlock_sync(timeout=0.1)
                assert result is False

    def test_wait_for_unlock_sync_removes_stale_lock(self, tmp_path):
        """Test wait_for_unlock_sync removes stale lock files."""
        mutex = GitMutex(repo_root=tmp_path)

        # Create a stale lock file
        git_dir = tmp_path / ".git"
        git_dir.mkdir(parents=True)
        lock_file = git_dir / "index.lock"
        lock_file.write_text("")

        # Set file mtime to 6 minutes ago
        old_time = time.time() - 360
        import os

        os.utime(lock_file, (old_time, old_time))

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (True, "locked")
            with patch("time.sleep"):
                result = mutex.wait_for_unlock_sync(timeout=5.0)
                assert result is True
                assert not lock_file.exists()


# =============================================================================
# DispatchResult Tests
# =============================================================================


class TestDispatchResult:
    """Tests for DispatchResult dataclass."""

    def test_dispatch_result_success_str(self):
        """Test DispatchResult string representation for success."""
        result = DispatchResult(
            success=True,
            target="forge:test",
            message_id="msg_123",
            delivery_time_ms=150.5,
            total_time_ms=200.0,
            attempts=1,
            verification_method="echo",
        )
        str_repr = str(result)
        assert "✓ Dispatched" in str_repr
        assert "forge:test" in str_repr
        assert "echo" in str_repr
        assert "150ms" in str_repr or "151ms" in str_repr

    def test_dispatch_result_failure_str(self):
        """Test DispatchResult string representation for failure."""
        result = DispatchResult(
            success=False,
            target="forge:test",
            message_id="msg_123",
            delivery_time_ms=0,
            total_time_ms=5000.0,
            attempts=3,
            verification_method="",
            error="Agent not responding",
            failure_type=FailureType.CLI_CRASHED,
        )
        str_repr = str(result)
        assert "✗ Dispatch" in str_repr
        assert "failed" in str_repr
        assert "Agent not responding" in str_repr
        assert "3 attempts" in str_repr

    def test_dispatch_result_with_optional_fields(self):
        """Test DispatchResult with all optional fields set."""
        from forge_harness.fleet.readiness import CLIState

        result = DispatchResult(
            success=True,
            target="forge:claude",
            message_id="msg_abc",
            delivery_time_ms=100.0,
            total_time_ms=150.0,
            attempts=2,
            verification_method="receipt",
            error=None,
            failure_type=None,
            circuit_state="closed",
            cli_state=CLIState.READY,
            git_lock_waited=True,
        )
        assert result.success is True
        assert result.circuit_state == "closed"
        assert result.git_lock_waited is True
        assert result.cli_state == CLIState.READY


# =============================================================================
# DispatchClient Tests
# =============================================================================


class TestDispatchClient:
    """Tests for DispatchClient class."""

    def test_init_with_defaults(self):
        """Test DispatchClient initialization with default values."""
        client = DispatchClient()
        assert client.default_retry_config is not None
        assert client.circuit_breaker is not None
        assert client.git_mutex is not None

    def test_init_with_custom_config(self):
        """Test DispatchClient initialization with custom configuration."""
        custom_retry = RetryConfig(max_attempts=5)
        custom_circuit = CircuitBreaker(failure_threshold=10)
        custom_mutex = GitMutex()

        client = DispatchClient(
            default_retry_config=custom_retry,
            circuit_breaker=custom_circuit,
            git_mutex=custom_mutex,
        )
        assert client.default_retry_config == custom_retry
        assert client.circuit_breaker == custom_circuit
        assert client.git_mutex == custom_mutex

    @pytest.mark.asyncio
    async def test_send_successful_dispatch(self):
        """Test successful message dispatch."""
        client = DispatchClient()

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_ready:
            with patch("forge_harness.fleet.dispatch_client.send_with_verification") as mock_send:
                mock_ready.return_value = MagicMock(
                    state=MagicMock(value="ready"),
                    cli_type="test",
                    confidence=0.95,
                )

                mock_send.return_value = MagicMock(
                    verified=True,
                    delivery_time_ms=100.0,
                    message_id="msg_123",
                    method="echo",
                )

                with patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_retry:
                    mock_retry.return_value = MagicMock(
                        success=True,
                        attempts=1,
                        total_time_ms=150.0,
                    )

                    result = await client.send("forge:test", "Test message")

                    # Should succeed
                    assert isinstance(result, DispatchResult)

    @pytest.mark.asyncio
    async def test_send_with_git_lock_waiting(self):
        """Test dispatch waits for git lock when needed."""
        client = DispatchClient()

        with patch.object(client.git_mutex, "is_locked", return_value=True):
            with patch.object(client.git_mutex, "wait_for_unlock", return_value=True):
                with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_ready:
                    mock_ready.return_value = MagicMock(
                        state=MagicMock(value="ready"),
                        cli_type="test",
                        confidence=0.95,
                    )

                    with patch(
                        "forge_harness.fleet.dispatch_client.dispatch_with_retry"
                    ) as mock_retry:
                        mock_retry.return_value = MagicMock(
                            success=True,
                            attempts=1,
                            total_time_ms=200.0,
                        )

                        with patch(
                            "forge_harness.fleet.dispatch_client.send_with_verification"
                        ) as mock_send:
                            mock_send.return_value = MagicMock(
                                verified=True,
                                delivery_time_ms=100.0,
                                message_id="msg_123",
                                method="echo",
                            )

                            result = await client.send("forge:test", "Test message")
                            assert result.success is True
                            assert result.git_lock_waited is True

    @pytest.mark.asyncio
    async def test_send_with_custom_params(self):
        """Test dispatch with custom parameters."""
        client = DispatchClient()

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_ready:
            mock_ready.return_value = MagicMock(
                state=MagicMock(value="ready"),
                cli_type="claude",
                confidence=0.95,
            )

            with patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_retry:
                mock_retry.return_value = MagicMock(
                    success=True,
                    attempts=1,
                    total_time_ms=150.0,
                )

                with patch(
                    "forge_harness.fleet.dispatch_client.send_with_verification"
                ) as mock_send:
                    mock_send.return_value = MagicMock(
                        verified=True,
                        delivery_time_ms=100.0,
                        message_id="msg_456",
                        method="receipt",
                    )

                    result = await client.send(
                        target="forge:claude",
                        message="Custom message",
                        cli_type="claude",
                        verify=True,
                        timeout=30.0,
                        max_retries=3,
                        wait_for_ready_timeout=10.0,
                    )

                    assert isinstance(result, DispatchResult)
                    assert result.target == "forge:claude"


# =============================================================================
# Module-Level Function Tests
# =============================================================================


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def test_get_client_creates_singleton(self):
        """Test get_client returns singleton instance."""
        # Clear any existing global client
        import forge_harness.fleet.dispatch_client as module

        original = module._global_client
        module._global_client = None

        try:
            client1 = get_client()
            client2 = get_client()
            assert client1 is client2
            assert isinstance(client1, DispatchClient)
        finally:
            module._global_client = original

    @pytest.mark.asyncio
    async def test_send_function(self):
        """Test send() convenience function."""
        # Clear global client
        import forge_harness.fleet.dispatch_client as module

        original = module._global_client
        module._global_client = None

        try:
            with patch.object(DispatchClient, "send") as mock_send:
                mock_send.return_value = MagicMock(
                    success=True,
                    target="forge:test",
                    message_id="msg_123",
                )

                result = await send("forge:test", "Test message")

                assert result.success is True
                mock_send.assert_called_once()
        finally:
            module._global_client = original

    def test_send_sync_function(self):
        """Test send_sync() synchronous convenience function."""
        import forge_harness.fleet.dispatch_client as module

        original = module._global_client
        module._global_client = None

        try:
            with patch.object(DispatchClient, "send") as mock_send:
                mock_send.return_value = MagicMock(
                    success=True,
                    target="forge:test",
                    message_id="msg_456",
                )

                result = send_sync("forge:test", "Test message")

                assert result.success is True
                mock_send.assert_called_once()
        finally:
            module._global_client = original

    def test_send_sync_with_loop_running(self):
        """Test send_sync when event loop is already running."""
        import forge_harness.fleet.dispatch_client as module

        original = module._global_client
        module._global_client = None

        try:
            with patch.object(DispatchClient, "send") as mock_send:
                mock_send.return_value = MagicMock(
                    success=True,
                    target="forge:test",
                    message_id="msg_789",
                )

                # Simulate loop already running
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                try:
                    result = send_sync("forge:test", "Test message")
                    assert result.success is True
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)
        finally:
            module._global_client = original


# =============================================================================
# Integration-Style Tests
# =============================================================================


class TestDispatchScenarios:
    """Integration-style tests for common dispatch scenarios."""

    @pytest.mark.asyncio
    async def test_dispatch_to_ready_agent(self):
        """Test dispatching to an agent that is ready."""
        client = DispatchClient()

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check:
            mock_check.return_value = MagicMock(
                state=MagicMock(value="ready"),
                agent_name="claude",
                version="1.0.0",
                cli_type="claude",
                confidence=0.95,
            )

            with patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_dispatch:
                mock_dispatch.return_value = MagicMock(
                    success=True,
                    attempts=1,
                    total_time_ms=100.0,
                )

                with patch(
                    "forge_harness.fleet.dispatch_client.send_with_verification"
                ) as mock_verify:
                    mock_verify.return_value = MagicMock(
                        verified=True,
                        delivery_time_ms=50.0,
                        message_id="msg_test",
                        method="echo",
                    )

                    result = await client.send("forge:claude", "Implement feature X")

                    assert result.success is True
                    assert result.target == "forge:claude"
                    assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_dispatch_with_circuit_breaker_open(self):
        """Test dispatch when circuit breaker is open."""
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        # Force circuit breaker open for the test target
        target = "forge:test"
        client.circuit_breaker.record_failure(target, FailureType.CLI_NOT_READY)
        client.circuit_breaker.record_failure(target, FailureType.CLI_NOT_READY)
        client.circuit_breaker.record_failure(target, FailureType.CLI_NOT_READY)
        client.circuit_breaker.record_failure(target, FailureType.CLI_NOT_READY)
        client.circuit_breaker.record_failure(target, FailureType.CLI_NOT_READY)

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check:
            mock_check.return_value = MagicMock(
                state=MagicMock(value="ready"),
                cli_type="test",
                confidence=0.95,
            )

            with patch("forge_harness.fleet.dispatch_client.send_with_verification") as mock_send:
                mock_send.return_value = MagicMock(
                    verified=True,
                    delivery_time_ms=100.0,
                    message_id="msg_123",
                    method="echo",
                )

                result = await client.send(target, "Test message")

                # Should still work, but circuit breaker state should be reflected
                assert isinstance(result, DispatchResult)

    @pytest.mark.asyncio
    async def test_dispatch_failure_handling(self):
        """Test handling of dispatch failures."""
        client = DispatchClient()

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check:
            mock_check.return_value = MagicMock(
                state=MagicMock(value="ready"),
                cli_type="test",
                confidence=0.95,
            )

            with patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_dispatch:
                mock_dispatch.return_value = MagicMock(
                    success=False,
                    attempts=3,
                    total_time_ms=5000.0,
                    failure_reason="Agent crashed",
                    failure_type=FailureType.CLI_CRASHED,
                    circuit_state=MagicMock(value="open"),
                )

                result = await client.send("forge:test", "Test message")

                assert result.success is False
                assert result.attempts == 3
                assert result.error == "Agent crashed"


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_send_with_empty_message(self):
        """Test dispatch with empty message."""
        client = DispatchClient()

        # Should handle empty message gracefully
        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check:
            mock_check.return_value = MagicMock(
                state=MagicMock(value="ready"),
                cli_type="test",
                confidence=0.95,
            )

            with patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_dispatch:
                mock_dispatch.return_value = MagicMock(
                    success=True,
                    attempts=1,
                    total_time_ms=50.0,
                )

                with patch(
                    "forge_harness.fleet.dispatch_client.send_with_verification"
                ) as mock_verify:
                    mock_verify.return_value = MagicMock(
                        verified=True,
                        delivery_time_ms=25.0,
                        message_id="msg_empty",
                        method="echo",
                    )

                    result = await client.send("forge:test", "")
                    assert result.success is True

    @pytest.mark.asyncio
    async def test_send_with_special_characters(self):
        """Test dispatch with special characters in message."""
        client = DispatchClient()

        message = "Test with special chars: émojis 🎉 and symbols @#$%"

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check:
            mock_check.return_value = MagicMock(
                state=MagicMock(value="ready"),
                cli_type="test",
                confidence=0.95,
            )

            with patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_dispatch:
                mock_dispatch.return_value = MagicMock(
                    success=True,
                    attempts=1,
                    total_time_ms=50.0,
                )

                with patch(
                    "forge_harness.fleet.dispatch_client.send_with_verification"
                ) as mock_verify:
                    mock_verify.return_value = MagicMock(
                        verified=True,
                        delivery_time_ms=25.0,
                        message_id="msg_special",
                        method="echo",
                    )

                    result = await client.send("forge:test", message)
                    assert result.success is True

    def test_git_mutex_with_nonexistent_repo(self, tmp_path):
        """Test GitMutex handles non-existent repo gracefully."""
        nonexistent = tmp_path / "does_not_exist"
        mutex = GitMutex(repo_root=nonexistent)

        # Should not raise when checking lock
        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (False, "")
            assert mutex.is_locked() is False

    @pytest.mark.asyncio
    async def test_dispatch_with_wait_for_ready(self):
        """Test dispatch with wait_for_ready parameter."""
        client = DispatchClient()

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check:
            # First return not ready, then wait_for_ready will be called
            mock_check.return_value = MagicMock(
                state=MagicMock(value="starting"),
                cli_type="test",
                confidence=0.5,  # Below 0.7 threshold
                is_ready=MagicMock(return_value=False),
            )

            with patch("forge_harness.fleet.dispatch_client.wait_for_ready") as mock_wait:
                mock_wait.return_value = MagicMock(
                    ready=True,
                    state=MagicMock(value="ready"),
                )

                with patch(
                    "forge_harness.fleet.dispatch_client.dispatch_with_retry"
                ) as mock_dispatch:
                    mock_dispatch.return_value = MagicMock(
                        success=True,
                        attempts=1,
                        total_time_ms=200.0,
                    )

                    with patch(
                        "forge_harness.fleet.dispatch_client.send_with_verification"
                    ) as mock_verify:
                        mock_verify.return_value = MagicMock(
                            verified=True,
                            delivery_time_ms=100.0,
                            message_id="msg_wait",
                            method="echo",
                        )

                        result = await client.send(
                            "forge:test",
                            "Test message",
                            wait_for_ready_timeout=15.0,
                        )

                        assert result.success is True
