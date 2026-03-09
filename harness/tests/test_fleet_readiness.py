"""Tests for forge_harness.fleet.readiness module.

Tests the CLI readiness detection for tmux agents.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.fleet.readiness import (
    BUSY_PATTERNS,
    READY_PATTERNS,
    SHELL_PATTERNS,
    STARTING_PATTERNS,
    CLIState,
    ReadinessResult,
    check_pane_alive,
    check_readiness,
    detect_cli_type,
    detect_context_percent,
    get_cli_command,
    get_pane_content,
    restart_agent,
    score_readiness,
    wait_for_ready,
)


class TestCLIState:
    """Tests for CLIState enum."""

    def test_state_values(self):
        """Test all state values are defined correctly."""
        assert CLIState.READY.value == "ready"
        assert CLIState.BUSY.value == "busy"
        assert CLIState.STARTING.value == "starting"
        assert CLIState.CRASHED.value == "crashed"
        assert CLIState.UNKNOWN.value == "unknown"


class TestReadinessPatterns:
    """Tests for readiness pattern constants."""

    def test_ready_patterns_claude(self):
        """Test Claude ready patterns are defined."""
        assert "claude" in READY_PATTERNS
        assert len(READY_PATTERNS["claude"]) > 0

    def test_ready_patterns_codex(self):
        """Test Codex ready patterns are defined."""
        assert "codex" in READY_PATTERNS
        assert len(READY_PATTERNS["codex"]) > 0

    def test_ready_patterns_gemini(self):
        """Test Gemini ready patterns are defined."""
        assert "gemini" in READY_PATTERNS
        assert len(READY_PATTERNS["gemini"]) > 0

    def test_busy_patterns_defined(self):
        """Test busy patterns are defined."""
        assert len(BUSY_PATTERNS) > 0

    def test_starting_patterns_defined(self):
        """Test starting patterns are defined."""
        assert len(STARTING_PATTERNS) > 0

    def test_shell_patterns_defined(self):
        """Test shell patterns are defined."""
        assert len(SHELL_PATTERNS) > 0


class TestReadinessResult:
    """Tests for ReadinessResult dataclass."""

    def test_default_values(self):
        """Test default result values."""
        result = ReadinessResult(
            state=CLIState.READY,
            confidence=0.8,
            cli_type="claude",
            indicators=[],
            pane_alive=True,
        )
        assert result.state == CLIState.READY
        assert result.confidence == 0.8
        assert result.cli_type == "claude"
        assert result.indicators == []
        assert result.pane_alive is True
        assert result.raw_content is None

    def test_custom_values(self):
        """Test custom result values."""
        result = ReadinessResult(
            state=CLIState.BUSY,
            confidence=0.6,
            cli_type="codex",
            indicators=["busy:Working..."],
            pane_alive=True,
            raw_content="test content",
        )
        assert result.state == CLIState.BUSY
        assert result.raw_content == "test content"

    def test_is_ready_default_threshold(self):
        """Test is_ready with default threshold."""
        result = ReadinessResult(
            state=CLIState.READY,
            confidence=0.8,
            cli_type="claude",
            indicators=[],
            pane_alive=True,
        )
        assert result.is_ready() is True

    def test_is_ready_custom_threshold(self):
        """Test is_ready with custom threshold."""
        result = ReadinessResult(
            state=CLIState.READY,
            confidence=0.8,
            cli_type="claude",
            indicators=[],
            pane_alive=True,
        )
        assert result.is_ready(min_confidence=0.9) is False

    def test_is_ready_not_ready_state(self):
        """Test is_ready when state is not READY."""
        result = ReadinessResult(
            state=CLIState.BUSY,
            confidence=0.9,
            cli_type="claude",
            indicators=[],
            pane_alive=True,
        )
        assert result.is_ready() is False


class TestCheckPaneAlive:
    """Tests for check_pane_alive function."""

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_pane_alive_success(self, mock_run):
        """Test successful pane alive check."""
        mock_run.return_value = MagicMock(returncode=0, stdout="12345")
        result = check_pane_alive("forge:tech")
        assert result is True

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_pane_not_found(self, mock_run):
        """Test pane not found."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = check_pane_alive("forge:tech")
        assert result is False

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_empty_pid(self, mock_run):
        """Test empty PID."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = check_pane_alive("forge:tech")
        assert result is False

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_process_dead(self, mock_run):
        """Test when process is dead."""
        mock_run.return_value = MagicMock(returncode=0, stdout="12345")
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="12345"),
            MagicMock(returncode=1, stdout=""),
        ]
        result = check_pane_alive("forge:tech")
        assert result is False

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_timeout_error(self, mock_run):
        """Test timeout error handling."""
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 1)
        result = check_pane_alive("forge:tech")
        assert result is False

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_subprocess_error(self, mock_run):
        """Test subprocess error handling."""
        mock_run.side_effect = subprocess.SubprocessError("Error")
        result = check_pane_alive("forge:tech")
        assert result is False


class TestGetPaneContent:
    """Tests for get_pane_content function."""

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_get_content_success(self, mock_run):
        """Test successful content retrieval."""
        mock_run.return_value = MagicMock(returncode=0, stdout="test content\nmulti line")
        result = get_pane_content("forge:tech")
        assert result == "test content\nmulti line"

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_capture_failed(self, mock_run):
        """Test failed capture."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_pane_content("forge:tech")
        assert result is None

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_timeout_error(self, mock_run):
        """Test timeout error handling."""
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 1)
        result = get_pane_content("forge:tech")
        assert result is None


class TestDetectCliType:
    """Tests for detect_cli_type function."""

    def test_detect_claude(self):
        """Test Claude detection."""
        content = "Claude Code is ready\n❯"
        assert detect_cli_type(content) == "claude"

    def test_detect_codex(self):
        """Test Codex detection."""
        content = "OpenAI Codex\n›"
        assert detect_cli_type(content) == "codex"

    def test_detect_gemini(self):
        """Test Gemini detection."""
        content = "╭─ Gemini ─╮\n╰"
        assert detect_cli_type(content) == "gemini"

    def test_detect_shell(self):
        """Test shell detection."""
        content = "$ \n"
        assert detect_cli_type(content) == "shell"

    def test_detect_shell_dollar_prompt(self):
        """Test shell detection with dollar prompt."""
        content = "$ "
        assert detect_cli_type(content) == "shell"

    def test_detect_shell_hash_prompt(self):
        """Test shell detection with hash prompt."""
        content = "# "
        assert detect_cli_type(content) == "shell"

    def test_detect_shell_fancy_prompt(self):
        """Test shell detection with fancy prompt."""
        content = "➜ "
        assert detect_cli_type(content) == "shell"

    def test_detect_unknown(self):
        """Test unknown CLI detection."""
        content = "random text content"
        assert detect_cli_type(content) == "unknown"

    def test_detect_empty(self):
        """Test empty content."""
        assert detect_cli_type("") == "unknown"
        assert detect_cli_type(None) == "unknown"  # type: ignore


class TestScoreReadiness:
    """Tests for score_readiness function."""

    def test_empty_content(self):
        """Test with empty content."""
        result = score_readiness("", "claude")
        assert result.state == CLIState.UNKNOWN
        assert result.confidence == 0.0

    def test_none_content(self):
        """Test with None content."""
        result = score_readiness(None, "claude")  # type: ignore
        assert result.state == CLIState.UNKNOWN
        assert result.confidence == 0.0

    def test_busy_state(self):
        """Test busy state detection."""
        content = "Thinking... Working..."
        result = score_readiness(content, "claude")
        assert result.state == CLIState.BUSY
        assert result.confidence > 0.5

    def test_starting_state(self):
        """Test starting state detection."""
        content = "Loading... Initializing..."
        result = score_readiness(content, "claude")
        assert result.state == CLIState.STARTING

    def test_ready_state_claude(self):
        """Test ready state for Claude."""
        content = "Claude Code\n❯"
        result = score_readiness(content, "claude")
        assert result.state == CLIState.READY
        assert result.confidence > 0.5

    def test_ready_state_codex(self):
        """Test ready state for Codex."""
        content = "OpenAI Codex\n›"
        result = score_readiness(content, "codex")
        assert result.state == CLIState.READY

    def test_ready_state_gemini(self):
        """Test ready state for Gemini."""
        content = "╭─ Gemini ─╮\nType your message"
        result = score_readiness(content, "gemini")
        assert result.state == CLIState.READY

    def test_shell_state(self):
        """Test shell (crashed) state."""
        content = " $ "
        result = score_readiness(content, "claude")
        assert result.state == CLIState.CRASHED

    def test_unknown_state(self):
        """Test unknown state."""
        content = "some random text with no indicators"
        result = score_readiness(content, "claude")
        assert result.state == CLIState.UNKNOWN

    def test_auto_detect_cli_type(self):
        """Test auto-detection of CLI type."""
        content = "Claude Code\n❯"
        result = score_readiness(content, "auto")
        assert result.state == CLIState.READY
        assert result.cli_type == "claude"

    def test_include_raw_content(self):
        """Test raw content inclusion."""
        content = "test content"
        result = score_readiness(content, "claude", include_raw=True)
        assert result.raw_content == content

    def test_exclude_raw_content(self):
        """Test raw content exclusion."""
        content = "test content"
        result = score_readiness(content, "claude", include_raw=False)
        assert result.raw_content is None


class TestCheckReadiness:
    """Tests for check_readiness function."""

    @patch("forge_harness.fleet.readiness.get_pane_content")
    @patch("forge_harness.fleet.readiness.check_pane_alive")
    def test_check_readiness_crash(self, mock_alive, mock_content):
        """Test readiness check when pane is dead."""
        mock_alive.return_value = False
        result = check_readiness("forge:tech")
        assert result.state == CLIState.CRASHED
        assert result.pane_alive is False

    @patch("forge_harness.fleet.readiness.get_pane_content")
    @patch("forge_harness.fleet.readiness.check_pane_alive")
    def test_check_readiness_content_unavailable(self, mock_alive, mock_content):
        """Test readiness check when content unavailable."""
        mock_alive.return_value = True
        mock_content.return_value = None
        result = check_readiness("forge:tech")
        assert result.state == CLIState.UNKNOWN

    @patch("forge_harness.fleet.readiness.score_readiness")
    @patch("forge_harness.fleet.readiness.get_pane_content")
    @patch("forge_harness.fleet.readiness.check_pane_alive")
    def test_check_readiness_success(self, mock_alive, mock_content, mock_score):
        """Test successful readiness check."""
        mock_alive.return_value = True
        mock_content.return_value = "Claude Code"
        mock_score.return_value = ReadinessResult(
            state=CLIState.READY,
            confidence=0.8,
            cli_type="claude",
            indicators=[],
            pane_alive=True,
        )
        result = check_readiness("forge:tech")
        assert result.state == CLIState.READY


class TestWaitForReady:
    """Tests for wait_for_ready function."""

    @patch("forge_harness.fleet.readiness.check_readiness")
    def test_wait_ready_immediate(self, mock_check):
        """Test immediate readiness."""
        mock_check.return_value = ReadinessResult(
            state=CLIState.READY,
            confidence=0.8,
            cli_type="claude",
            indicators=[],
            pane_alive=True,
        )
        result = wait_for_ready("forge:tech", timeout=10)
        assert result is True

    @patch("forge_harness.fleet.readiness.check_readiness")
    def test_wait_ready_after_retry(self, mock_check):
        """Test readiness after retries."""
        mock_check.side_effect = [
            ReadinessResult(
                state=CLIState.BUSY,
                confidence=0.5,
                cli_type="claude",
                indicators=[],
                pane_alive=True,
            ),
            ReadinessResult(
                state=CLIState.READY,
                confidence=0.8,
                cli_type="claude",
                indicators=[],
                pane_alive=True,
            ),
        ]
        result = wait_for_ready("forge:tech", timeout=10)
        assert result is True

    @patch("forge_harness.fleet.readiness.check_readiness")
    def test_wait_ready_crash(self, mock_check):
        """Test readiness when CLI crashes."""
        mock_check.return_value = ReadinessResult(
            state=CLIState.CRASHED,
            confidence=0.9,
            cli_type="unknown",
            indicators=[],
            pane_alive=False,
        )
        result = wait_for_ready("forge:tech", timeout=10)
        assert result is False

    @patch("forge_harness.fleet.readiness.check_readiness")
    def test_wait_ready_timeout(self, mock_check):
        """Test readiness timeout."""
        mock_check.return_value = ReadinessResult(
            state=CLIState.BUSY,
            confidence=0.5,
            cli_type="claude",
            indicators=[],
            pane_alive=True,
        )
        result = wait_for_ready("forge:tech", timeout=1)
        assert result is False


class TestGetCliCommand:
    """Tests for get_cli_command function."""

    def test_get_claude_command(self):
        """Test getting Claude command."""
        assert get_cli_command("claude") == "claude --dangerously-skip-permissions"

    def test_get_codex_command(self):
        """Test getting Codex command."""
        assert get_cli_command("codex") == "codex --dangerously-bypass-approvals-and-sandbox"

    def test_get_gemini_command(self):
        """Test getting Gemini command."""
        assert get_cli_command("gemini") == "gemini --yolo"

    def test_get_opencode_command(self):
        """Test getting OpenCode command."""
        assert get_cli_command("opencode") == "opencode --dangerously-skip-permissions"

    def test_get_kimi_command(self):
        """Test getting Kimi command."""
        assert get_cli_command("kimi") == "kimi -y"

    def test_get_cursor_command(self):
        """Test getting Cursor command."""
        assert get_cli_command("cursor") == "cursor"

    def test_get_glm_command(self):
        """Test getting GLM command (Claude Code agent)."""
        assert get_cli_command("glm") == "claude --dangerously-skip-permissions"

    def test_get_minimax_command(self):
        """Test getting MiniMax command (Claude Code agent)."""
        assert get_cli_command("minimax") == "claude --dangerously-skip-permissions"

    def test_get_unknown_command(self):
        """Test unknown CLI returns None."""
        assert get_cli_command("unknown") is None

    def test_get_case_insensitive(self):
        """Test case insensitivity."""
        assert get_cli_command("CLAUDE") == "claude --dangerously-skip-permissions"
        assert get_cli_command("Claude") == "claude --dangerously-skip-permissions"


class TestConfidenceScoring:
    """Tests for confidence scoring logic."""

    def test_busy_confidence_calculation(self):
        """Test confidence calculation for busy state."""
        content = "Working...\nThinking..."
        result = score_readiness(content, "claude")
        assert result.state == CLIState.BUSY
        assert result.confidence >= 0.5
        assert result.confidence <= 0.9

    def test_starting_confidence_calculation(self):
        """Test confidence calculation for starting state."""
        content = "Loading..."
        result = score_readiness(content, "claude")
        assert result.state == CLIState.STARTING
        assert result.confidence >= 0.4
        assert result.confidence <= 0.8

    def test_ready_confidence_calculation(self):
        """Test confidence calculation for ready state."""
        content = "Claude Code\n❯\nshortcuts"
        result = score_readiness(content, "claude")
        assert result.state == CLIState.READY
        assert result.confidence >= 0.6
        assert result.confidence <= 0.95

    def test_indicators_list(self):
        """Test that indicators are captured."""
        content = "Working... Testing..."
        result = score_readiness(content, "claude")
        assert len(result.indicators) > 0
        assert all("busy:" in i for i in result.indicators)


class TestDetectContextPercent:
    """Tests for detect_context_percent function."""

    def test_detect_context_percent_claude_code(self):
        """Claude Code 'Context left until auto-compact: 5%' -> 95% used."""
        content = "Claude Code\n❯\nContext left until auto-compact: 5%"
        result = detect_context_percent(content)
        assert result == 95

    def test_detect_context_percent_claude_code_full(self):
        """Claude Code 'Context left until auto-compact: 0%' -> 100% used."""
        content = "Claude Code\n❯\nContext left until auto-compact: 0%"
        result = detect_context_percent(content)
        assert result == 100

    def test_detect_context_percent_kimi(self):
        """Kimi 'context: 17.3%' -> 17% used (rounded)."""
        content = "kimi\ncontext: 17.3%"
        result = detect_context_percent(content)
        assert result == 17

    def test_detect_context_percent_pi(self):
        """Pi '60.0%/205k (auto)' -> 60% used."""
        content = "Pi CLI\n60.0%/205k (auto)"
        result = detect_context_percent(content)
        assert result == 60

    def test_detect_context_percent_gemini_none(self):
        """Gemini has no context percentage visible -> None."""
        content = "╭─ Gemini ─╮\nType your message\n╰"
        result = detect_context_percent(content)
        assert result is None

    def test_detect_context_percent_empty(self):
        """Empty content -> None."""
        assert detect_context_percent("") is None
        assert detect_context_percent(None) is None  # type: ignore


class TestScoreReadinessContextExhausted:
    """Tests for CONTEXT_EXHAUSTED state in score_readiness."""

    def test_score_readiness_context_exhausted(self):
        """Ready CLI with >90% context usage -> CONTEXT_EXHAUSTED."""
        # Claude Code pane at prompt with nearly-full context
        content = "Claude Code\n❯\nshortcuts\nContext left until auto-compact: 5%"
        result = score_readiness(content, "claude")
        assert result.state == CLIState.CONTEXT_EXHAUSTED
        assert result.context_percent == 95
        assert any("context:95%" in ind for ind in result.indicators)

    def test_score_readiness_context_ok(self):
        """Ready CLI with 50% context usage -> READY (not exhausted)."""
        content = "Claude Code\n❯\nshortcuts\nContext left until auto-compact: 50%"
        result = score_readiness(content, "claude")
        assert result.state == CLIState.READY
        assert result.context_percent == 50
        assert any("context:50%" in ind for ind in result.indicators)

    def test_score_readiness_context_boundary(self):
        """Ready CLI at exactly 90% context -> READY (boundary is exclusive: > 90)."""
        content = "Claude Code\n❯\nshortcuts\nContext left until auto-compact: 10%"
        result = score_readiness(content, "claude")
        # 100 - 10 = 90 — NOT exhausted (threshold is > 90, not >= 90)
        assert result.state == CLIState.READY
        assert result.context_percent == 90

    def test_score_readiness_no_context_info(self):
        """Ready CLI with no context info -> READY with context_percent=None."""
        content = "Claude Code\n❯\nshortcuts"
        result = score_readiness(content, "claude")
        assert result.state == CLIState.READY
        assert result.context_percent is None

    def test_score_readiness_context_exhausted_kimi(self):
        """Kimi at 95% context -> CONTEXT_EXHAUSTED."""
        content = "kimi\ncontext: 95.0%\nType your message"
        # Kimi patterns: detect via codex patterns (›) or just set cli_type directly
        # Use "kimi" as cli_type which falls back to unknown ready patterns
        # but let's test with "codex" patterns visible too
        content_with_prompt = "kimi\ncontext: 95.0%\n›"
        result = score_readiness(content_with_prompt, "codex")
        assert result.state == CLIState.CONTEXT_EXHAUSTED
        assert result.context_percent == 95


class TestRestartAgent:
    """Tests for restart_agent function."""

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_restart_agent_claude(self, mock_run):
        """Restart a Claude Code agent sends correct tmux commands."""
        mock_run.return_value = MagicMock(returncode=0)
        result = restart_agent("forge:glm", "claude")
        assert result is True
        assert mock_run.call_count == 3

        # First call: Ctrl-C
        first_call_args = mock_run.call_args_list[0][0][0]
        assert "C-c" in first_call_args

        # Third call: start command
        third_call_args = mock_run.call_args_list[2][0][0]
        assert "claude --dangerously-skip-permissions" in " ".join(third_call_args)

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_restart_agent_unknown_type_infers_from_window(self, mock_run):
        """Unknown cli_type falls back to window name for command lookup."""
        mock_run.return_value = MagicMock(returncode=0)
        # "forge:kimi" window name "kimi" should resolve to "kimi -y"
        result = restart_agent("forge:kimi", "unknown_type")
        assert result is True
        third_call_args = mock_run.call_args_list[2][0][0]
        assert "kimi -y" in " ".join(third_call_args)

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_restart_agent_completely_unknown_returns_false(self, mock_run):
        """When both cli_type and window name are unknown, return False."""
        result = restart_agent("forge:mystery", "totally_unknown")
        assert result is False
        mock_run.assert_not_called()

    @patch("forge_harness.fleet.readiness.subprocess.run")
    def test_restart_agent_subprocess_error(self, mock_run):
        """subprocess.SubprocessError during restart returns False."""
        import subprocess

        mock_run.side_effect = subprocess.SubprocessError("tmux gone")
        result = restart_agent("forge:glm", "claude")
        assert result is False


class TestDispatchClientAlwaysRestart:
    """Tests verifying that DispatchClient.send() always restarts before dispatch.

    The always-restart behavior ensures agents have a fresh context window for
    every dispatch, regardless of their current state (READY, BUSY, STARTING,
    CONTEXT_EXHAUSTED). Only CRASHED state is exempt — a crashed pane cannot
    be restarted via the CLI restart sequence.
    """

    @pytest.mark.asyncio
    async def test_restart_happens_when_agent_is_ready(self):
        """Restart is called even when the agent is already READY.

        Previously, restart only triggered on CONTEXT_EXHAUSTED. Now it always
        triggers for any non-CRASHED state, including READY.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        ready_result = ReadinessResult(
            state=CLIState.READY,
            confidence=0.95,
            cli_type="claude",
            indicators=["ready:❯"],
            pane_alive=True,
        )

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=ready_result,
        ) as mock_check:
            with patch(
                "forge_harness.fleet.dispatch_client.restart_agent",
                return_value=True,
            ) as mock_restart:
                with patch(
                    "forge_harness.fleet.dispatch_client.wait_for_ready",
                    return_value=True,
                ):
                    with patch(
                        "forge_harness.fleet.dispatch_client.dispatch_with_retry",
                        new_callable=AsyncMock,
                    ) as mock_retry:
                        mock_retry.return_value = MagicMock(
                            success=True,
                            attempts=1,
                            total_time_ms=150.0,
                            circuit_state=MagicMock(value="closed"),
                            failure_reason=None,
                            failure_type=None,
                        )

                        with patch.object(
                            client.git_mutex, "is_locked", return_value=False
                        ):
                            result = await client.send(
                                "forge:glm",
                                "Implement feature X",
                                cli_type="claude",
                            )

        # restart_agent MUST be called even though the agent was READY
        mock_restart.assert_called_once_with("forge:glm", "claude")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_restart_happens_when_agent_is_context_exhausted(self):
        """Restart is called when agent is CONTEXT_EXHAUSTED (unchanged from before)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.fleet.dispatch_client import DispatchClient

        client = DispatchClient()

        exhausted_result = ReadinessResult(
            state=CLIState.CONTEXT_EXHAUSTED,
            confidence=0.9,
            cli_type="claude",
            indicators=["context:95%"],
            pane_alive=True,
            context_percent=95,
        )

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=exhausted_result,
        ):
            with patch(
                "forge_harness.fleet.dispatch_client.restart_agent",
                return_value=True,
            ) as mock_restart:
                with patch(
                    "forge_harness.fleet.dispatch_client.wait_for_ready",
                    return_value=True,
                ):
                    with patch(
                        "forge_harness.fleet.dispatch_client.dispatch_with_retry",
                        new_callable=AsyncMock,
                    ) as mock_retry:
                        mock_retry.return_value = MagicMock(
                            success=True,
                            attempts=1,
                            total_time_ms=200.0,
                            circuit_state=MagicMock(value="closed"),
                            failure_reason=None,
                            failure_type=None,
                        )

                        with patch.object(
                            client.git_mutex, "is_locked", return_value=False
                        ):
                            result = await client.send(
                                "forge:glm",
                                "Implement feature X",
                                cli_type="claude",
                            )

        mock_restart.assert_called_once_with("forge:glm", "claude")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_restart_happens_when_agent_is_busy(self):
        """Restart is called when agent is BUSY (was previously only waited on)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.fleet.dispatch_client import DispatchClient

        client = DispatchClient()

        busy_result = ReadinessResult(
            state=CLIState.BUSY,
            confidence=0.8,
            cli_type="claude",
            indicators=["busy:Thinking..."],
            pane_alive=True,
        )

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=busy_result,
        ):
            with patch(
                "forge_harness.fleet.dispatch_client.restart_agent",
                return_value=True,
            ) as mock_restart:
                with patch(
                    "forge_harness.fleet.dispatch_client.wait_for_ready",
                    return_value=True,
                ):
                    with patch(
                        "forge_harness.fleet.dispatch_client.dispatch_with_retry",
                        new_callable=AsyncMock,
                    ) as mock_retry:
                        mock_retry.return_value = MagicMock(
                            success=True,
                            attempts=1,
                            total_time_ms=180.0,
                            circuit_state=MagicMock(value="closed"),
                            failure_reason=None,
                            failure_type=None,
                        )

                        with patch.object(
                            client.git_mutex, "is_locked", return_value=False
                        ):
                            result = await client.send(
                                "forge:glm",
                                "Implement feature X",
                                cli_type="claude",
                            )

        mock_restart.assert_called_once_with("forge:glm", "claude")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_crashed_state_returns_error_without_restart(self):
        """CRASHED state is exempt: returns error immediately, no restart attempted."""
        from unittest.mock import MagicMock, patch

        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        crashed_result = ReadinessResult(
            state=CLIState.CRASHED,
            confidence=0.9,
            cli_type="unknown",
            indicators=[],
            pane_alive=False,
        )

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=crashed_result,
        ):
            with patch(
                "forge_harness.fleet.dispatch_client.restart_agent",
            ) as mock_restart:
                result = await client.send(
                    "forge:glm",
                    "Implement feature X",
                    cli_type="claude",
                )

        # restart_agent must NOT be called for CRASHED state
        mock_restart.assert_not_called()
        assert result.success is False
        assert result.cli_state == CLIState.CRASHED
        assert result.failure_type == FailureType.CLI_CRASHED

    @pytest.mark.asyncio
    async def test_skip_readiness_bypasses_restart(self):
        """skip_readiness=True skips both readiness check and restart."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.fleet.dispatch_client import DispatchClient

        client = DispatchClient()

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
        ) as mock_check:
            with patch(
                "forge_harness.fleet.dispatch_client.restart_agent",
            ) as mock_restart:
                with patch(
                    "forge_harness.fleet.dispatch_client.dispatch_with_retry",
                    new_callable=AsyncMock,
                ) as mock_retry:
                    mock_retry.return_value = MagicMock(
                        success=True,
                        attempts=1,
                        total_time_ms=100.0,
                        circuit_state=MagicMock(value="closed"),
                        failure_reason=None,
                        failure_type=None,
                    )

                    with patch.object(
                        client.git_mutex, "is_locked", return_value=False
                    ):
                        result = await client.send(
                            "forge:glm",
                            "Implement feature X",
                            skip_readiness=True,
                        )

        # Neither check_readiness nor restart_agent should be called
        mock_check.assert_not_called()
        mock_restart.assert_not_called()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_restart_failure_returns_error(self):
        """When restart_agent returns False, dispatch returns a failure result."""
        from unittest.mock import MagicMock, patch

        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        ready_result = ReadinessResult(
            state=CLIState.READY,
            confidence=0.9,
            cli_type="claude",
            indicators=["ready:❯"],
            pane_alive=True,
        )

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=ready_result,
        ):
            with patch(
                "forge_harness.fleet.dispatch_client.restart_agent",
                return_value=False,  # restart fails
            ):
                result = await client.send(
                    "forge:glm",
                    "Implement feature X",
                    cli_type="claude",
                )

        assert result.success is False
        assert result.failure_type == FailureType.CLI_NOT_READY
        assert "Failed to restart" in (result.error or "")

    @pytest.mark.asyncio
    async def test_restart_then_not_ready_returns_error(self):
        """When restart succeeds but CLI does not become ready, returns error."""
        from unittest.mock import MagicMock, patch

        from forge_harness.fleet.dispatch_client import DispatchClient
        from forge_harness.fleet.retry import FailureType

        client = DispatchClient()

        ready_result = ReadinessResult(
            state=CLIState.READY,
            confidence=0.9,
            cli_type="claude",
            indicators=["ready:❯"],
            pane_alive=True,
        )

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=ready_result,
        ):
            with patch(
                "forge_harness.fleet.dispatch_client.restart_agent",
                return_value=True,
            ):
                with patch(
                    "forge_harness.fleet.dispatch_client.wait_for_ready",
                    return_value=False,  # never becomes ready after restart
                ):
                    result = await client.send(
                        "forge:glm",
                        "Implement feature X",
                        cli_type="claude",
                        wait_for_ready_timeout=5.0,
                    )

        assert result.success is False
        assert result.failure_type == FailureType.CLI_NOT_READY
        assert "restarted but did not become ready" in (result.error or "")

    @pytest.mark.asyncio
    async def test_restart_uses_window_name_when_cli_type_is_auto(self):
        """When cli_type='auto', restart uses the window name from the target."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from forge_harness.fleet.dispatch_client import DispatchClient

        client = DispatchClient()

        ready_result = ReadinessResult(
            state=CLIState.READY,
            confidence=0.9,
            cli_type="claude",
            indicators=["ready:❯"],
            pane_alive=True,
        )

        with patch(
            "forge_harness.fleet.dispatch_client.check_readiness",
            return_value=ready_result,
        ):
            with patch(
                "forge_harness.fleet.dispatch_client.restart_agent",
                return_value=True,
            ) as mock_restart:
                with patch(
                    "forge_harness.fleet.dispatch_client.wait_for_ready",
                    return_value=True,
                ):
                    with patch(
                        "forge_harness.fleet.dispatch_client.dispatch_with_retry",
                        new_callable=AsyncMock,
                    ) as mock_retry:
                        mock_retry.return_value = MagicMock(
                            success=True,
                            attempts=1,
                            total_time_ms=150.0,
                            circuit_state=MagicMock(value="closed"),
                            failure_reason=None,
                            failure_type=None,
                        )

                        with patch.object(
                            client.git_mutex, "is_locked", return_value=False
                        ):
                            result = await client.send(
                                "forge:glm",
                                "Implement feature X",
                                cli_type="auto",  # auto-detect
                            )

        # With cli_type="auto", restart_cli is inferred from "forge:glm" → "glm"
        mock_restart.assert_called_once_with("forge:glm", "glm")
        assert result.success is True
