"""Tests for fleet/context_rotation.py - Context Rotation System.

Tests cover:
- RotationStatus enum
- RotationResult dataclass
- TaskContext dataclass
- LegacyContextRotator
- ContextStats dataclass
- FleetContextRotator
- ContextRotator facade
- Factory functions
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.fleet.context_rotation import (
    CHARS_PER_TOKEN,
    CONTEXT_WINDOW_TOKENS,
    DEFAULT_MAX_TOKENS,
    ROTATION_THRESHOLD,
    TOKENS_PER_LINE,
    ContextRotator,
    ContextStats,
    FleetContextRotator,
    LegacyContextRotator,
    RotationResult,
    RotationStatus,
    TaskContext,
    create_context_rotator,
    create_rotator,
)

# =============================================================================
# RotationStatus Tests
# =============================================================================


class TestRotationStatus:
    """Tests for RotationStatus enum."""

    def test_success_value(self) -> None:
        """Should have SUCCESS value."""
        assert RotationStatus.SUCCESS == "success"

    def test_failed_value(self) -> None:
        """Should have FAILED value."""
        assert RotationStatus.FAILED == "failed"

    def test_skipped_value(self) -> None:
        """Should have SKIPPED value."""
        assert RotationStatus.SKIPPED == "skipped"


# =============================================================================
# RotationResult Tests
# =============================================================================


class TestRotationResult:
    """Tests for RotationResult dataclass."""

    def test_creation(self) -> None:
        """Should create RotationResult."""
        result = RotationResult(
            session_id="forge:test",
            status=RotationStatus.SUCCESS,
            new_session_id="forge:test-r1234",
            old_context_pct=75,
        )

        assert result.session_id == "forge:test"
        assert result.status == RotationStatus.SUCCESS
        assert result.new_session_id == "forge:test-r1234"
        assert result.old_context_pct == 75

    def test_success_property_true(self) -> None:
        """Should return True for success status."""
        result = RotationResult(
            session_id="forge:test",
            status=RotationStatus.SUCCESS,
        )
        assert result.success is True

    def test_success_property_false(self) -> None:
        """Should return False for failed status."""
        result = RotationResult(
            session_id="forge:test",
            status=RotationStatus.FAILED,
        )
        assert result.success is False

    def test_summary_contains_session(self) -> None:
        """Summary should contain session ID."""
        result = RotationResult(
            session_id="forge:test",
            status=RotationStatus.SUCCESS,
            old_context_pct=80,
            duration=5.5,
        )
        summary = result.summary

        assert "forge:test" in summary
        assert "success" in summary
        assert "80%" in summary
        assert "5.5s" in summary

    def test_summary_with_error(self) -> None:
        """Summary should include error."""
        result = RotationResult(
            session_id="forge:test",
            status=RotationStatus.FAILED,
            error="Something went wrong",
        )
        summary = result.summary

        assert "Something went wrong" in summary


# =============================================================================
# TaskContext Tests
# =============================================================================


class TestTaskContext:
    """Tests for TaskContext dataclass."""

    def test_creation_minimal(self) -> None:
        """Should create TaskContext with minimal fields."""
        ctx = TaskContext(task="Implement feature")

        assert ctx.task == "Implement feature"
        assert ctx.description == ""
        assert ctx.files == []
        assert ctx.progress == ""
        assert ctx.notes == ""
        assert ctx.test_command is None

    def test_creation_full(self) -> None:
        """Should create TaskContext with all fields."""
        ctx = TaskContext(
            task="Implement auth",
            description="Add JWT validation",
            files=["src/auth.py", "src/models.py"],
            progress="50% complete",
            notes="Use fastapi-jwt",
            test_command="pytest tests/test_auth.py",
        )

        assert ctx.task == "Implement auth"
        assert ctx.files == ["src/auth.py", "src/models.py"]
        assert ctx.test_command == "pytest tests/test_auth.py"

    def test_to_handoff_prompt_basic(self) -> None:
        """Should generate basic handoff prompt."""
        ctx = TaskContext(task="Implement feature")
        prompt = ctx.to_handoff_prompt()

        assert "Context Rotation Handoff" in prompt
        assert "Implement feature" in prompt
        assert "Please continue the task" in prompt

    def test_to_handoff_prompt_with_description(self) -> None:
        """Should include description in prompt."""
        ctx = TaskContext(
            task="Implement auth",
            description="Add JWT validation to API",
        )
        prompt = ctx.to_handoff_prompt()

        assert "## Description" in prompt
        assert "Add JWT validation to API" in prompt

    def test_to_handoff_prompt_with_progress(self) -> None:
        """Should include progress in prompt."""
        ctx = TaskContext(
            task="Implement auth",
            progress="Models created, need routes",
        )
        prompt = ctx.to_handoff_prompt()

        assert "## Progress So Far" in prompt
        assert "Models created, need routes" in prompt

    def test_to_handoff_prompt_with_files(self) -> None:
        """Should include files in prompt."""
        ctx = TaskContext(
            task="Implement auth",
            files=["src/auth.py", "src/models.py"],
        )
        prompt = ctx.to_handoff_prompt()

        assert "## Files to Review" in prompt
        assert "`src/auth.py`" in prompt
        assert "`src/models.py`" in prompt

    def test_to_handoff_prompt_with_notes(self) -> None:
        """Should include notes in prompt."""
        ctx = TaskContext(
            task="Implement auth",
            notes="Check the token expiry logic",
        )
        prompt = ctx.to_handoff_prompt()

        assert "## Notes" in prompt
        assert "Check the token expiry logic" in prompt

    def test_to_handoff_prompt_with_test_command(self) -> None:
        """Should include test command in prompt."""
        ctx = TaskContext(
            task="Implement auth",
            test_command="pytest tests/test_auth.py -v",
        )
        prompt = ctx.to_handoff_prompt()

        assert "## Test Command" in prompt
        assert "pytest tests/test_auth.py -v" in prompt


# =============================================================================
# LegacyContextRotator Tests
# =============================================================================


class TestLegacyContextRotator:
    """Tests for LegacyContextRotator class."""

    def test_init_defaults(self) -> None:
        """Should initialize with defaults."""
        rotator = LegacyContextRotator()

        assert rotator.session_prefix == "forge"
        assert rotator.max_tokens == DEFAULT_MAX_TOKENS
        assert rotator.chars_per_token == CHARS_PER_TOKEN
        assert rotator._rotating_sessions == set()

    def test_init_custom(self) -> None:
        """Should initialize with custom values."""
        rotator = LegacyContextRotator(
            session_prefix="custom",
            max_tokens=100_000,
            chars_per_token=5,
        )

        assert rotator.session_prefix == "custom"
        assert rotator.max_tokens == 100_000
        assert rotator.chars_per_token == 5

    def test_normalize_session_id_without_prefix(self) -> None:
        """Should add prefix to bare session ID."""
        rotator = LegacyContextRotator(session_prefix="forge")
        result = rotator._normalize_session_id("tech")

        assert result == "forge:tech"

    def test_normalize_session_id_with_prefix(self) -> None:
        """Should not modify already prefixed session ID."""
        rotator = LegacyContextRotator(session_prefix="forge")
        result = rotator._normalize_session_id("forge:tech")

        assert result == "forge:tech"

    def test_estimate_context_usage_with_content(self) -> None:
        """Should estimate context from pane content."""
        rotator = LegacyContextRotator(max_tokens=1000, chars_per_token=4)

        # 400 chars = 100 tokens = 10% of 1000
        mock_content = "x" * 400

        with patch.object(rotator, "_capture_pane", return_value=mock_content):
            result = rotator.estimate_context_usage("forge:test")

            assert result == 10

    def test_estimate_context_usage_no_content(self) -> None:
        """Should return 0 when no content captured."""
        rotator = LegacyContextRotator()

        with patch.object(rotator, "_capture_pane", return_value=None):
            result = rotator.estimate_context_usage("forge:test")

            assert result == 0

    def test_estimate_context_usage_clamped(self) -> None:
        """Should clamp percentage to 0-100."""
        rotator = LegacyContextRotator(max_tokens=1000, chars_per_token=4)

        # Very large content should still return 100
        mock_content = "x" * 1_000_000

        with patch.object(rotator, "_capture_pane", return_value=mock_content):
            result = rotator.estimate_context_usage("forge:test")

            assert result == 100

    def test_should_rotate_true(self) -> None:
        """Should return True when above threshold."""
        rotator = LegacyContextRotator()

        with patch.object(rotator, "estimate_context_usage", return_value=75):
            result = rotator.should_rotate("forge:test", threshold=70)

            assert result is True

    def test_should_rotate_false(self) -> None:
        """Should return False when below threshold."""
        rotator = LegacyContextRotator()

        with patch.object(rotator, "estimate_context_usage", return_value=50):
            result = rotator.should_rotate("forge:test", threshold=70)

            assert result is False

    def test_should_rotate_already_rotating(self) -> None:
        """Should return False when already rotating."""
        rotator = LegacyContextRotator()
        rotator._rotating_sessions.add("forge:test")

        result = rotator.should_rotate("forge:test", threshold=70)

        assert result is False

    def test_generate_new_window_name(self) -> None:
        """Should generate new window name with timestamp."""
        rotator = LegacyContextRotator()

        with patch("forge_harness.fleet.context_rotation.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "1234"
            result = rotator._generate_new_window_name("forge:tech")

            assert result == "tech-r1234"

    def test_mark_session_rotating(self) -> None:
        """Should mark session as rotating."""
        rotator = LegacyContextRotator()

        rotator._mark_session_rotating("forge:test", True)
        assert "forge:test" in rotator._rotating_sessions

        rotator._mark_session_rotating("forge:test", False)
        assert "forge:test" not in rotator._rotating_sessions


# =============================================================================
# ContextStats Tests
# =============================================================================


class TestContextStats:
    """Tests for ContextStats dataclass."""

    def test_creation(self) -> None:
        """Should create ContextStats."""
        stats = ContextStats(
            session_name="forge:test",
            estimated_lines=1000,
            estimated_tokens=100_000,
            context_percent=0.5,
            should_rotate=False,
            last_checked="2024-01-01T00:00:00Z",
        )

        assert stats.session_name == "forge:test"
        assert stats.estimated_lines == 1000
        assert stats.estimated_tokens == 100_000
        assert stats.context_percent == 0.5
        assert stats.should_rotate is False


# =============================================================================
# FleetContextRotator Tests
# =============================================================================


class TestFleetContextRotator:
    """Tests for FleetContextRotator class."""

    def test_init_defaults(self, tmp_path: Path) -> None:
        """Should initialize with defaults."""
        with patch.object(Path, "cwd", return_value=tmp_path):
            rotator = FleetContextRotator()

            assert rotator.rotation_threshold == ROTATION_THRESHOLD
            assert rotator.tokens_per_line == TOKENS_PER_LINE
            assert rotator.fleet_dir.exists()

    def test_init_custom(self, tmp_path: Path) -> None:
        """Should initialize with custom values."""
        rotator = FleetContextRotator(
            forge_root=tmp_path,
            rotation_threshold=0.8,
            tokens_per_line=150,
        )

        assert rotator.rotation_threshold == 0.8
        assert rotator.tokens_per_line == 150
        assert rotator.forge_root == tmp_path

    def test_estimate_context_usage(self, tmp_path: Path) -> None:
        """Should estimate context usage."""
        rotator = FleetContextRotator(forge_root=tmp_path)

        with patch.object(rotator, "get_tmux_pane_lines", return_value=1000):
            result = rotator.estimate_context_usage("forge:test")

            # 1000 lines * 100 tokens/line = 100,000 tokens
            # 100,000 / 200,000 = 0.5
            assert result == 0.5

    def test_estimate_context_usage_exception(self, tmp_path: Path) -> None:
        """Should return 0.0 on exception."""
        rotator = FleetContextRotator(forge_root=tmp_path)

        with patch.object(rotator, "get_tmux_pane_lines", side_effect=Exception("Failed")):
            result = rotator.estimate_context_usage("forge:test")

            assert result == 0.0

    def test_should_rotate_true(self, tmp_path: Path) -> None:
        """Should return True when above threshold."""
        rotator = FleetContextRotator(forge_root=tmp_path, rotation_threshold=0.7)

        with patch.object(rotator, "estimate_context_usage", return_value=0.8):
            result = rotator.should_rotate("forge:test")

            assert result is True

    def test_should_rotate_false(self, tmp_path: Path) -> None:
        """Should return False when below threshold."""
        rotator = FleetContextRotator(forge_root=tmp_path, rotation_threshold=0.7)

        with patch.object(rotator, "estimate_context_usage", return_value=0.5):
            result = rotator.should_rotate("forge:test")

            assert result is False

    def test_should_rotate_custom_threshold(self, tmp_path: Path) -> None:
        """Should use custom threshold when provided."""
        rotator = FleetContextRotator(forge_root=tmp_path, rotation_threshold=0.7)

        with patch.object(rotator, "estimate_context_usage", return_value=0.6):
            # Above custom 0.5 threshold but below instance 0.7
            result = rotator.should_rotate("forge:test", threshold=0.5)

            assert result is True

    def test_get_context_stats(self, tmp_path: Path) -> None:
        """Should get context stats."""
        rotator = FleetContextRotator(forge_root=tmp_path)

        with patch.object(rotator, "get_tmux_pane_lines", return_value=1400):
            stats = rotator.get_context_stats("forge:test")

            assert stats.session_name == "forge:test"
            assert stats.estimated_lines == 1400
            assert stats.estimated_tokens == 140_000  # 1400 * 100
            assert stats.context_percent == 0.7  # 140K / 200K
            assert stats.should_rotate is True  # 0.7 >= 0.7 threshold

    def test_generate_handoff_prompt_file_not_found(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError when state missing."""
        rotator = FleetContextRotator(forge_root=tmp_path)

        with pytest.raises(FileNotFoundError):
            rotator.generate_handoff_prompt("forge:test")

    def test_generate_handoff_prompt_agent_not_found(self, tmp_path: Path) -> None:
        """Should raise ValueError when agent not found."""
        rotator = FleetContextRotator(forge_root=tmp_path)

        # Create state file with no matching agent
        state_file = tmp_path / ".forge/fleet" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"agents": {}}))

        with pytest.raises(ValueError, match="No agent found"):
            rotator.generate_handoff_prompt("forge:test")

    def test_generate_handoff_prompt_success(self, tmp_path: Path) -> None:
        """Should generate handoff prompt."""
        rotator = FleetContextRotator(forge_root=tmp_path)

        # Create state file
        state_file = tmp_path / ".forge/fleet" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({
            "agents": {
                "agent-1": {
                    "session": "forge",
                    "window": "test",
                    "task": "Implement feature",
                }
            }
        }))

        with patch.object(rotator, "get_tmux_pane_lines", return_value=1000), \
             patch.object(rotator.handoff_generator, "generate_handoff_prompt", return_value="# Handoff"):
            prompt = rotator.generate_handoff_prompt("forge:test")

            assert "CONTEXT ROTATION TRIGGERED" in prompt
            assert "70.0%" in prompt  # 1000 * 100 / 200000 = 0.5 -> displayed as 50.0%


# =============================================================================
# ContextRotator Facade Tests
# =============================================================================


class TestContextRotator:
    """Tests for ContextRotator facade class."""

    def test_init_legacy_mode(self) -> None:
        """Should use LegacyContextRotator when no forge_root."""
        rotator = ContextRotator()

        assert isinstance(rotator._impl, LegacyContextRotator)

    def test_init_fleet_mode(self, tmp_path: Path) -> None:
        """Should use FleetContextRotator when forge_root provided."""
        rotator = ContextRotator(forge_root=tmp_path)

        assert isinstance(rotator._impl, FleetContextRotator)

    def test_getattr_delegation(self, tmp_path: Path) -> None:
        """Should delegate attribute access to implementation."""
        rotator = ContextRotator(forge_root=tmp_path)

        # Access FleetContextRotator attribute
        assert rotator.rotation_threshold == ROTATION_THRESHOLD


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestCreateRotator:
    """Tests for create_rotator function."""

    def test_create_rotator(self) -> None:
        """Should create ContextRotator (facade for LegacyContextRotator)."""
        rotator = create_rotator()

        # create_rotator returns a ContextRotator facade
        assert isinstance(rotator, ContextRotator)
        assert isinstance(rotator._impl, LegacyContextRotator)


class TestCreateContextRotator:
    """Tests for create_context_rotator function."""

    def test_create_context_rotator_default(self) -> None:
        """Should create ContextRotator with defaults."""
        rotator = create_context_rotator()

        assert isinstance(rotator, ContextRotator)
        assert isinstance(rotator._impl, LegacyContextRotator)

    def test_create_context_rotator_with_root(self, tmp_path: Path) -> None:
        """Should create ContextRotator with forge_root."""
        rotator = create_context_rotator(forge_root=tmp_path)

        assert isinstance(rotator, ContextRotator)
        assert isinstance(rotator._impl, FleetContextRotator)

    def test_create_context_rotator_custom_threshold(self, tmp_path: Path) -> None:
        """Should create ContextRotator with custom threshold."""
        rotator = create_context_rotator(
            forge_root=tmp_path,
            rotation_threshold=0.85,
        )

        assert rotator._impl.rotation_threshold == 0.85


# =============================================================================
# Constants Tests
# =============================================================================


def test_default_max_tokens() -> None:
    """Should have correct default max tokens."""
    assert DEFAULT_MAX_TOKENS == 200_000


def test_chars_per_token() -> None:
    """Should have correct chars per token."""
    assert CHARS_PER_TOKEN == 4


def test_context_window_tokens() -> None:
    """Should have correct context window."""
    assert CONTEXT_WINDOW_TOKENS == 200_000


def test_rotation_threshold() -> None:
    """Should have correct rotation threshold."""
    assert ROTATION_THRESHOLD == 0.7


def test_tokens_per_line() -> None:
    """Should have correct tokens per line."""
    assert TOKENS_PER_LINE == 100
