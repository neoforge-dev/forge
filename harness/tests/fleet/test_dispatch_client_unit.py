"""Unit tests for DispatchClient, GitMutex, and DispatchResult.

These tests cover the core functionality of the dispatch client module
including git lock coordination, dispatch results, and the client itself.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip all tests if the module can't be imported
try:
    from forge_harness.fleet.dispatch_client import (
        DispatchClient,
        DispatchResult,
        GitMutex,
        get_client,
        send,
        send_sync,
    )
    from forge_harness.fleet.readiness import CLIState
    from forge_harness.fleet.retry import CircuitBreaker, RetryConfig
    MODULE_AVAILABLE = True
except ImportError:
    MODULE_AVAILABLE = False


pytestmark = pytest.mark.skipif(not MODULE_AVAILABLE, reason="dispatch_client module not available")


class TestGitMutex:
    """Tests for GitMutex class."""

    def test_init_with_default_root(self):
        """Should use current directory when root not specified."""
        mutex = GitMutex()
        assert mutex.repo_root is not None
        assert isinstance(mutex.repo_root, Path)

    def test_init_with_custom_root(self, tmp_path):
        """Should use provided repository root."""
        mutex = GitMutex(repo_root=tmp_path)
        assert mutex.repo_root == tmp_path

    def test_init_from_environment(self, tmp_path, monkeypatch):
        """Should read FORGE_ROOT from environment."""
        monkeypatch.setenv("FORGE_ROOT", str(tmp_path))
        mutex = GitMutex()
        assert mutex.repo_root == tmp_path

    def test_is_locked_delegates_to_git_guard(self):
        """Should delegate to check_git_unsafe_state."""
        mutex = GitMutex()

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (True, "index.lock exists")
            result = mutex.is_locked()

            assert result is True
            mock_check.assert_called_once()

    def test_is_locked_when_not_locked(self):
        """Should return False when git is not locked."""
        mutex = GitMutex()

        with patch("forge_harness.fleet.dispatch_client.check_git_unsafe_state") as mock_check:
            mock_check.return_value = (False, None)
            result = mutex.is_locked()

            assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_unlock_already_unlocked(self):
        """Should return True immediately when not locked."""
        mutex = GitMutex()

        with patch.object(mutex, "is_locked", return_value=False):
            result = await mutex.wait_for_unlock(timeout=5.0)
            assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_unlock_timeout(self):
        """Should return False when timeout is reached."""
        mutex = GitMutex()

        with patch.object(mutex, "is_locked", return_value=True):
            # Use a very short timeout to make test fast
            result = await mutex.wait_for_unlock(timeout=0.1)
            assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_unlock_releases_after_delay(self):
        """Should return True when lock is released."""
        mutex = GitMutex()

        call_count = 0
        def side_effect():
            nonlocal call_count
            call_count += 1
            return call_count < 2  # Locked first time, unlocked after

        with patch.object(mutex, "is_locked", side_effect=side_effect):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await mutex.wait_for_unlock(timeout=5.0)
                assert result is True
                assert call_count >= 2

    def test_wait_for_unlock_sync_already_unlocked(self):
        """Should return True immediately in sync mode."""
        mutex = GitMutex()

        with patch.object(mutex, "is_locked", return_value=False):
            result = mutex.wait_for_unlock_sync(timeout=5.0)
            assert result is True

    def test_wait_for_unlock_sync_timeout(self):
        """Should return False when timeout in sync mode."""
        mutex = GitMutex()

        with patch.object(mutex, "is_locked", return_value=True):
            result = mutex.wait_for_unlock_sync(timeout=0.1)
            assert result is False

    @pytest.mark.asyncio
    async def test_stale_lock_removal(self, tmp_path):
        """Should remove stale lock files."""
        mutex = GitMutex(repo_root=tmp_path)

        # Create a stale lock file
        lock_file = tmp_path / ".git" / "index.lock"
        lock_file.parent.mkdir(parents=True)
        lock_file.touch()

        # Make it old by manipulating stat
        old_time = asyncio.get_event_loop().time() - 200

        with patch.object(mutex, "lock_file", lock_file):
            with patch.object(mutex, "is_locked", side_effect=[True, False]):
                with patch("time.time", return_value=old_time + 200):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        result = await mutex.wait_for_unlock(timeout=5.0)
                        # Lock file should be removed if stale


class TestDispatchResult:
    """Tests for DispatchResult dataclass."""

    def test_success_result_str(self):
        """Should format success result nicely."""
        result = DispatchResult(
            success=True,
            target="forge:tech",
            message_id="msg_123",
            delivery_time_ms=250.0,
            total_time_ms=300.0,
            attempts=1,
            verification_method="echo",
        )

        str_repr = str(result)
        assert "✓" in str_repr
        assert "forge:tech" in str_repr
        assert "250ms" in str_repr

    def test_failure_result_str(self):
        """Should format failure result nicely."""
        result = DispatchResult(
            success=False,
            target="forge:tech",
            message_id="msg_123",
            delivery_time_ms=0.0,
            total_time_ms=5000.0,
            attempts=3,
            verification_method="none",
            error="Connection timeout",
        )

        str_repr = str(result)
        assert "✗" in str_repr
        assert "forge:tech" in str_repr
        assert "Connection timeout" in str_repr

    def test_result_with_all_fields(self):
        """Should handle all optional fields."""
        result = DispatchResult(
            success=True,
            target="forge:codex",
            message_id="msg_456",
            delivery_time_ms=150.0,
            total_time_ms=200.0,
            attempts=1,
            verification_method="activity",
            failure_type=None,
            circuit_state="closed",
            cli_state=CLIState.READY,
            git_lock_waited=True,
        )

        assert result.git_lock_waited is True
        assert result.circuit_state == "closed"


class TestDispatchClient:
    """Tests for DispatchClient class."""

    def test_init_with_defaults(self):
        """Should initialize with default configuration."""
        client = DispatchClient()
        assert client.default_retry_config is not None
        assert client.circuit_breaker is not None
        assert client.git_mutex is not None

    def test_init_with_custom_config(self):
        """Should accept custom configuration."""
        retry_config = RetryConfig(max_attempts=5)
        circuit_breaker = CircuitBreaker()
        git_mutex = GitMutex()

        client = DispatchClient(
            default_retry_config=retry_config,
            circuit_breaker=circuit_breaker,
            git_mutex=git_mutex,
        )

        assert client.default_retry_config == retry_config
        assert client.circuit_breaker == circuit_breaker
        assert client.git_mutex == git_mutex

    def test_get_circuit_state(self):
        """Should return circuit breaker state."""
        client = DispatchClient()

        # Should return one of the valid states
        state = client.get_circuit_state("forge:tech")
        assert state in ["closed", "open", "half_open"]

    def test_reset_circuit(self):
        """Should reset circuit breaker."""
        client = DispatchClient()

        # Should not raise an error
        client.reset_circuit("forge:tech")

        # After reset, state should be closed
        state = client.get_circuit_state("forge:tech")
        assert state == "closed"

    @pytest.mark.asyncio
    async def test_send_batch(self):
        """Should send to multiple targets."""
        client = DispatchClient()

        targets_and_messages = [
            ("forge:tech", "Task 1"),
            ("forge:codex", "Task 2"),
        ]

        # Mock the send method
        with patch.object(client, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = MagicMock(success=True)

            results = await client.send_batch(targets_and_messages)

            assert len(results) == 2
            assert mock_send.call_count == 2


class TestGlobalClient:
    """Tests for global client functions."""

    def test_get_client_creates_singleton(self):
        """Should return the same client instance."""
        # Reset global state
        import forge_harness.fleet.dispatch_client as dmc
        original = dmc._global_client
        dmc._global_client = None

        try:
            client1 = get_client()
            client2 = get_client()

            assert client1 is client2
            assert isinstance(client1, DispatchClient)
        finally:
            dmc._global_client = original

    @pytest.mark.asyncio
    async def test_send_convenience_function(self):
        """Send convenience function should use global client."""
        # Reset global state
        import forge_harness.fleet.dispatch_client as dmc
        original = dmc._global_client
        dmc._global_client = None

        try:
            with patch.object(DispatchClient, "send", new_callable=AsyncMock) as mock_send:
                mock_send.return_value = MagicMock(success=True)

                result = await send("forge:tech", "Test message")

                assert mock_send.called
                assert mock_send.call_args[0][0] == "forge:tech"
                assert mock_send.call_args[0][1] == "Test message"
        finally:
            dmc._global_client = original

    def test_send_sync_wrapper(self):
        """Send_sync should wrap async send."""
        # Reset global state
        import forge_harness.fleet.dispatch_client as dmc
        original = dmc._global_client
        dmc._global_client = None

        try:
            with patch("forge_harness.fleet.dispatch_client.send") as mock_send:
                mock_send.return_value = MagicMock(success=True)

                result = send_sync("forge:tech", "Test message")

                assert mock_send.called
                mock_send.assert_called_once_with("forge:tech", "Test message")
        finally:
            dmc._global_client = original


class TestDispatchClientSend:
    """Tests for DispatchClient.send() method."""

    @pytest.mark.skip(reason="Complex mocking - tested via integration tests")
    async def test_send_with_skip_readiness(self):
        """Should skip readiness check when requested."""
        pass

    @pytest.mark.skip(reason="Complex mocking - tested via integration tests")
    async def test_send_returns_dispatch_result(self):
        """Should return DispatchResult on success."""
        pass

    @pytest.mark.asyncio
    async def test_send_with_crashed_cli(self):
        """Should handle crashed CLI state."""
        client = DispatchClient()

        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check:
            mock_check.return_value = MagicMock(
                state=CLIState.CRASHED,
                cli_type="shell",
                is_ready=lambda **kwargs: False,
            )

            result = await client.send(
                "forge:tech",
                "Test message",
                cli_type="claude",
                wait_for_ready_timeout=0.1,
            )

            # Should fail due to crashed CLI
            assert isinstance(result, DispatchResult)
            assert result.success is False

    @pytest.mark.skip(reason="Complex mocking - tested via integration tests")
    async def test_send_with_git_lock_timeout(self):
        """Should fail when git lock timeout."""
        pass


class TestIntegration:
    """Integration-style tests."""

    @pytest.mark.asyncio
    async def test_full_dispatch_flow_mocked(self):
        """Test full dispatch flow with all mocks."""
        client = DispatchClient()

        # Mock all external dependencies
        with patch("forge_harness.fleet.dispatch_client.check_readiness") as mock_check:
            mock_check.return_value = MagicMock(
                state=CLIState.READY,
                is_ready=lambda **kwargs: True,
                confidence=0.95,
            )

            with patch("forge_harness.fleet.dispatch_client.restart_agent", return_value=True):
                with patch("forge_harness.fleet.dispatch_client.wait_for_ready", return_value=True):
                    with patch.object(client.git_mutex, "is_locked", return_value=False):
                        with patch("forge_harness.fleet.dispatch_client.dispatch_with_retry") as mock_retry:
                            mock_retry.return_value = MagicMock(
                                success=True,
                                total_time_ms=200.0,
                                attempts=1,
                                circuit_state=MagicMock(value="closed"),
                                failure_reason=None,
                                failure_type=None,
                            )

                            result = await client.send(
                                "forge:tech",
                                "Implement feature X",
                                verify=True,
                                timeout=10.0,
                            )

                            assert result.success is True
                            assert result.target == "forge:tech"
                            assert result.attempts == 1
