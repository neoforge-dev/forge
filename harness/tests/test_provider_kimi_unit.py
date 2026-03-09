"""Unit tests for KimiProvider (forge_harness/providers/kimi.py).

Tests cover:
- All properties (name, executable, supports_stream_json, supports_auto_approve)
- build_command() in every flag combination
- TTY-detection branch (non-interactive fallback to --print)
- Inherited base-class behaviours (get_env_vars, __repr__, supports_session_continue)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from forge_harness.providers.kimi import KimiProvider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> KimiProvider:
    """Return a fresh KimiProvider instance."""
    return KimiProvider()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Return a temporary workspace directory."""
    return tmp_path / "workspace"


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

class TestKimiProviderProperties:
    """Tests for all KimiProvider properties."""

    def test_name_returns_kimi(self, provider: KimiProvider) -> None:
        assert provider.name == "kimi"

    def test_executable_returns_kimi(self, provider: KimiProvider) -> None:
        assert provider.executable == "kimi"

    def test_supports_stream_json_is_true(self, provider: KimiProvider) -> None:
        assert provider.supports_stream_json is True

    def test_supports_auto_approve_is_true(self, provider: KimiProvider) -> None:
        assert provider.supports_auto_approve is True

    def test_supports_session_continue_inherits_true(self, provider: KimiProvider) -> None:
        """Inherited from AgentProvider — defaults to True."""
        assert provider.supports_session_continue is True

    def test_repr_contains_class_and_name(self, provider: KimiProvider) -> None:
        """Inherited __repr__ from AgentProvider."""
        r = repr(provider)
        assert "KimiProvider" in r
        assert "kimi" in r

    def test_get_env_vars_returns_empty_dict(self, provider: KimiProvider) -> None:
        """Inherited from AgentProvider — no provider-specific env vars."""
        assert provider.get_env_vars() == {}


# ---------------------------------------------------------------------------
# build_command — baseline
# ---------------------------------------------------------------------------

class TestBuildCommandBaseline:
    """Minimal / default invocations of build_command."""

    def test_minimal_command_in_tty(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """In a TTY, only the core flags and --prompt are emitted."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("do something", workspace)

        assert cmd[0] == "kimi"
        assert "--work-dir" in cmd
        assert str(workspace) in cmd
        assert "--prompt" in cmd
        assert "do something" in cmd
        # No extra flags expected
        assert "--yolo" not in cmd
        assert "--model" not in cmd
        assert "--session" not in cmd
        assert "--continue" not in cmd
        assert "--print" not in cmd
        assert "--output-format" not in cmd

    def test_minimal_command_not_tty_adds_print(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """Outside a TTY, --print is added to avoid interactive hangs."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            cmd = provider.build_command("do something", workspace)

        assert "--print" in cmd
        assert "--output-format" not in cmd

    def test_command_starts_with_executable(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace)
        assert cmd[0] == provider.executable

    def test_work_dir_flag_present(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace)
        idx = cmd.index("--work-dir")
        assert cmd[idx + 1] == str(workspace)

    def test_prompt_flag_at_end(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """--prompt <text> should be the last pair in the command."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("my task", workspace)
        assert cmd[-2] == "--prompt"
        assert cmd[-1] == "my task"

    def test_returns_list_of_strings(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace)
        assert isinstance(cmd, list)
        assert all(isinstance(part, str) for part in cmd)


# ---------------------------------------------------------------------------
# build_command — auto_approve
# ---------------------------------------------------------------------------

class TestBuildCommandAutoApprove:
    """Tests for the --yolo / auto_approve flag."""

    def test_auto_approve_adds_yolo(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, auto_approve=True)
        assert "--yolo" in cmd

    def test_auto_approve_false_omits_yolo(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, auto_approve=False)
        assert "--yolo" not in cmd

    def test_yolo_appears_before_prompt(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, auto_approve=True)
        assert cmd.index("--yolo") < cmd.index("--prompt")


# ---------------------------------------------------------------------------
# build_command — model selection
# ---------------------------------------------------------------------------

class TestBuildCommandModel:
    """Tests for the --model flag."""

    def test_model_flag_added_when_provided(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, model="kimi-k1")
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "kimi-k1"

    def test_model_none_omits_flag(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, model=None)
        assert "--model" not in cmd

    def test_model_empty_string_omits_flag(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """Empty string is falsy — --model should be omitted."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, model="")
        assert "--model" not in cmd


# ---------------------------------------------------------------------------
# build_command — session management
# ---------------------------------------------------------------------------

class TestBuildCommandSessionManagement:
    """Tests for session_id and continue_session flags."""

    def test_session_id_adds_session_flag(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, session_id="sess-abc")
        assert "--session" in cmd
        idx = cmd.index("--session")
        assert cmd[idx + 1] == "sess-abc"

    def test_session_id_suppresses_continue(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """When session_id is given, --continue must NOT appear."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command(
                "task", workspace, session_id="sess-abc", continue_session=True
            )
        assert "--session" in cmd
        assert "--continue" not in cmd

    def test_continue_session_adds_continue_flag(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, continue_session=True)
        assert "--continue" in cmd
        assert "--session" not in cmd

    def test_neither_session_nor_continue(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace)
        assert "--session" not in cmd
        assert "--continue" not in cmd

    def test_continue_appears_before_prompt(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, continue_session=True)
        assert cmd.index("--continue") < cmd.index("--prompt")


# ---------------------------------------------------------------------------
# build_command — stream_json
# ---------------------------------------------------------------------------

class TestBuildCommandStreamJson:
    """Tests for the --output-format stream-json --print combination."""

    def test_stream_json_adds_print_and_output_format(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, stream_json=True)
        assert "--print" in cmd
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"

    def test_stream_json_false_in_tty_no_print(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """In a TTY without stream_json, --print must NOT appear."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, stream_json=False)
        assert "--print" not in cmd
        assert "--output-format" not in cmd

    def test_stream_json_overrides_tty_detection(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """stream_json=True takes precedence even in a non-TTY environment."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            cmd = provider.build_command("task", workspace, stream_json=True)
        # --print appears exactly once
        assert cmd.count("--print") == 1
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"

    def test_stream_json_flags_appear_before_prompt(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, stream_json=True)
        prompt_idx = cmd.index("--prompt")
        assert cmd.index("--print") < prompt_idx
        assert cmd.index("--output-format") < prompt_idx


# ---------------------------------------------------------------------------
# build_command — labels and timeout (unsupported, silently ignored)
# ---------------------------------------------------------------------------

class TestBuildCommandIgnoredOptions:
    """Labels and timeout are documented as unsupported but must not crash."""

    def test_labels_are_silently_ignored(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command(
                "task", workspace, labels=["tag1", "tag2"]
            )
        assert isinstance(cmd, list)
        # No label-related flags should appear
        for label in ("tag1", "tag2", "--label", "--tag"):
            assert label not in cmd

    def test_timeout_is_silently_ignored(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace, timeout=120)
        assert "--timeout" not in cmd

    def test_extra_kwargs_are_silently_ignored(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command(
                "task", workspace, unknown_flag=True, another="val"
            )
        assert isinstance(cmd, list)


# ---------------------------------------------------------------------------
# build_command — combined flag scenarios
# ---------------------------------------------------------------------------

class TestBuildCommandCombinations:
    """Integration-style tests combining multiple flags."""

    def test_all_flags_enabled_tty(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command(
                "complex task",
                workspace,
                model="kimi-k2",
                stream_json=True,
                auto_approve=True,
                session_id="sess-xyz",
                labels=["a"],
                timeout=60,
            )
        assert "--yolo" in cmd
        assert "--model" in cmd
        assert "--session" in cmd
        assert "--print" in cmd
        assert "--output-format" in cmd
        assert "--continue" not in cmd  # session_id wins
        assert cmd[-1] == "complex task"
        assert cmd[-2] == "--prompt"

    def test_auto_approve_and_continue_session(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command(
                "task", workspace, auto_approve=True, continue_session=True
            )
        assert "--yolo" in cmd
        assert "--continue" in cmd

    def test_model_and_non_tty_without_stream_json(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            cmd = provider.build_command("task", workspace, model="kimi-k1")
        assert "--model" in cmd
        assert "--print" in cmd          # TTY fallback
        assert "--output-format" not in cmd

    def test_workspace_path_stringified(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """Path objects must be converted to strings in the command list."""
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command("task", workspace)
        idx = cmd.index("--work-dir")
        value = cmd[idx + 1]
        assert isinstance(value, str)
        assert value == str(workspace)

    def test_prompt_with_special_characters(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        """Prompts with special characters must pass through verbatim."""
        special = 'Fix "auth" & reload: <token=$SECRET>'
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command(special, workspace)
        assert cmd[-1] == special

    def test_prompt_with_newlines(
        self, provider: KimiProvider, workspace: Path
    ) -> None:
        multi = "Step 1: install\nStep 2: test\nStep 3: deploy"
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            cmd = provider.build_command(multi, workspace)
        assert cmd[-1] == multi


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

class TestKimiProviderInstantiation:
    """Verify that KimiProvider can be instantiated without arguments."""

    def test_default_instantiation(self) -> None:
        p = KimiProvider()
        assert isinstance(p, KimiProvider)

    def test_is_agent_provider_subclass(self) -> None:
        from forge_harness.providers.base import AgentProvider
        assert issubclass(KimiProvider, AgentProvider)

    def test_multiple_instances_are_independent(self) -> None:
        p1 = KimiProvider()
        p2 = KimiProvider()
        assert p1 is not p2
        assert p1.name == p2.name
