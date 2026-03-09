"""
Pure unit tests for forge_harness.worktree_manager

Covers:
- Worktree dataclass construction
- WorktreeManager.__init__
- WorktreeManager.list_worktrees (success, empty output, CalledProcessError)
- WorktreeManager._parse_worktree (all fields, missing fields, detached HEAD)
- WorktreeManager.create_worktree (default path, custom path, exists, new branch,
  existing branch, CalledProcessError)
- WorktreeManager.remove_worktree (without force, with force, CalledProcessError)
- WorktreeManager.prune (success, CalledProcessError)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from forge_harness.worktree_manager import Worktree, WorktreeManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(root: str = "/repo/myproject") -> WorktreeManager:
    return WorktreeManager(repo_root=Path(root))


def _cpe(returncode: int = 1, stderr: bytes = b"git error") -> subprocess.CalledProcessError:
    """Build a CalledProcessError with sensible defaults."""
    err = subprocess.CalledProcessError(returncode, ["git"])
    err.stderr = stderr
    return err


# ---------------------------------------------------------------------------
# Worktree dataclass
# ---------------------------------------------------------------------------

class TestWorktreeDataclass:
    """Tests for the Worktree dataclass."""

    def test_fields_stored(self):
        wt = Worktree(
            path=Path("/tmp/work"),
            branch="feature/xyz",
            head_hash="abc123",
        )
        assert wt.path == Path("/tmp/work")
        assert wt.branch == "feature/xyz"
        assert wt.head_hash == "abc123"

    def test_equality(self):
        a = Worktree(path=Path("/a"), branch="main", head_hash="aaa")
        b = Worktree(path=Path("/a"), branch="main", head_hash="aaa")
        assert a == b

    def test_inequality_on_branch(self):
        a = Worktree(path=Path("/a"), branch="main", head_hash="aaa")
        b = Worktree(path=Path("/a"), branch="dev", head_hash="aaa")
        assert a != b

    def test_empty_strings(self):
        wt = Worktree(path=Path(""), branch="", head_hash="")
        assert wt.branch == ""
        assert wt.head_hash == ""


# ---------------------------------------------------------------------------
# WorktreeManager.__init__
# ---------------------------------------------------------------------------

class TestWorktreeManagerInit:
    """Tests for WorktreeManager initialisation."""

    def test_repo_root_stored(self):
        mgr = _make_manager("/some/repo")
        assert mgr.repo_root == Path("/some/repo")

    def test_accepts_path_object(self):
        mgr = WorktreeManager(repo_root=Path("/explicit/path"))
        assert mgr.repo_root == Path("/explicit/path")


# ---------------------------------------------------------------------------
# WorktreeManager.list_worktrees
# ---------------------------------------------------------------------------

PORCELAIN_OUTPUT = """\
worktree /repo/myproject
HEAD deadbeef
branch refs/heads/main

worktree /tmp/myproject-feature-x
HEAD cafebabe
branch refs/heads/feature/x

"""

class TestListWorktrees:
    """Tests for WorktreeManager.list_worktrees."""

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_returns_parsed_worktrees(self, mock_run):
        mock_run.return_value = MagicMock(stdout=PORCELAIN_OUTPUT)
        mgr = _make_manager()
        result = mgr.list_worktrees()

        assert len(result) == 2
        assert result[0].path == Path("/repo/myproject")
        assert result[0].branch == "main"
        assert result[0].head_hash == "deadbeef"
        assert result[1].path == Path("/tmp/myproject-feature-x")
        assert result[1].branch == "feature/x"
        assert result[1].head_hash == "cafebabe"

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_calls_correct_command(self, mock_run):
        mock_run.return_value = MagicMock(stdout="")
        mgr = _make_manager("/my/repo")
        mgr.list_worktrees()

        mock_run.assert_called_once_with(
            ["git", "worktree", "list", "--porcelain"],
            cwd=Path("/my/repo"),
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_empty_output_returns_empty_list(self, mock_run):
        mock_run.return_value = MagicMock(stdout="")
        mgr = _make_manager()
        result = mgr.list_worktrees()
        assert result == []

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_subprocess_error_returns_empty_list(self, mock_run):
        mock_run.side_effect = _cpe()
        mgr = _make_manager()
        result = mgr.list_worktrees()
        assert result == []

    @patch("forge_harness.worktree_manager.logger")
    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_subprocess_error_logs_error(self, mock_run, mock_logger):
        mock_run.side_effect = _cpe()
        mgr = _make_manager()
        mgr.list_worktrees()
        mock_logger.error.assert_called_once()
        assert "Failed to list worktrees" in mock_logger.error.call_args.args[0]

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_single_worktree(self, mock_run):
        output = "worktree /repo\nHEAD abc\nbranch refs/heads/main\n"
        mock_run.return_value = MagicMock(stdout=output)
        result = _make_manager().list_worktrees()
        assert len(result) == 1
        assert result[0].branch == "main"

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_branch_refs_prefix_stripped(self, mock_run):
        output = "worktree /repo\nHEAD abc\nbranch refs/heads/feat/my-feature\n"
        mock_run.return_value = MagicMock(stdout=output)
        result = _make_manager().list_worktrees()
        assert result[0].branch == "feat/my-feature"

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_trailing_blank_lines_handled(self, mock_run):
        # Porcelain output typically has a trailing newline — must not crash
        output = "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n"
        mock_run.return_value = MagicMock(stdout=output)
        result = _make_manager().list_worktrees()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# WorktreeManager._parse_worktree
# ---------------------------------------------------------------------------

class TestParseWorktree:
    """Tests for WorktreeManager._parse_worktree (internal helper)."""

    def test_full_data(self):
        mgr = _make_manager()
        wt = mgr._parse_worktree(
            {"path": "/tmp/wt", "branch": "main", "head": "deadbeef"}
        )
        assert wt.path == Path("/tmp/wt")
        assert wt.branch == "main"
        assert wt.head_hash == "deadbeef"

    def test_missing_branch_defaults_to_detached(self):
        mgr = _make_manager()
        wt = mgr._parse_worktree({"path": "/tmp/wt", "head": "deadbeef"})
        assert wt.branch == "detached"

    def test_missing_head_defaults_to_empty_string(self):
        mgr = _make_manager()
        wt = mgr._parse_worktree({"path": "/tmp/wt", "branch": "main"})
        assert wt.head_hash == ""

    def test_missing_path_defaults_to_empty_path(self):
        mgr = _make_manager()
        wt = mgr._parse_worktree({"branch": "main", "head": "abc"})
        assert wt.path == Path("")

    def test_empty_dict_all_defaults(self):
        mgr = _make_manager()
        wt = mgr._parse_worktree({})
        assert wt.path == Path("")
        assert wt.branch == "detached"
        assert wt.head_hash == ""

    def test_returns_worktree_instance(self):
        mgr = _make_manager()
        result = mgr._parse_worktree({"path": "/a", "branch": "b", "head": "c"})
        assert isinstance(result, Worktree)


# ---------------------------------------------------------------------------
# WorktreeManager.create_worktree
# ---------------------------------------------------------------------------

class TestCreateWorktree:
    """Tests for WorktreeManager.create_worktree."""

    # --- default path computation -------------------------------------------

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_default_path_uses_sibling_convention(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(returncode=1)  # branch does not exist
        mgr = _make_manager("/projects/myproject")
        returned = mgr.create_worktree("feature/new")
        expected = Path("/projects") / "myproject-feature-new"
        assert returned == expected

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_slash_in_branch_replaced_with_dash(self, mock_exists, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        mgr = _make_manager("/projects/repo")
        path = mgr.create_worktree("a/b/c")
        assert "a-b-c" in path.name

    # --- path already exists ------------------------------------------------

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=True)
    def test_returns_existing_path_without_git_call_for_worktree_add(
        self, mock_exists, mock_run
    ):
        mgr = _make_manager()
        custom = Path("/existing/path")
        result = mgr.create_worktree("mybranch", path=custom)
        assert result == custom
        # subprocess may still be called for show-ref — but NOT for worktree add
        add_calls = [
            c for c in mock_run.call_args_list
            if "add" in c.args[0]
        ]
        assert len(add_calls) == 0

    @patch("forge_harness.worktree_manager.logger")
    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=True)
    def test_logs_info_when_path_exists(self, mock_exists, mock_run, mock_logger):
        mgr = _make_manager()
        mgr.create_worktree("mybranch", path=Path("/existing"))
        mock_logger.info.assert_called_once()
        assert "already exists" in mock_logger.info.call_args.args[0]

    # --- branch existence check --------------------------------------------

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_uses_dash_b_when_branch_does_not_exist(self, mock_exists, mock_run):
        # First call = show-ref (branch missing), second call = worktree add
        mock_run.side_effect = [
            MagicMock(returncode=1),  # branch does NOT exist
            MagicMock(returncode=0),  # worktree add succeeds
        ]
        mgr = _make_manager("/repo/proj")
        mgr.create_worktree("new-branch", path=Path("/tmp/wt"))
        add_call = mock_run.call_args_list[1]
        assert "-b" in add_call.args[0]

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_omits_dash_b_when_branch_exists(self, mock_exists, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # branch EXISTS
            MagicMock(returncode=0),  # worktree add
        ]
        mgr = _make_manager("/repo/proj")
        mgr.create_worktree("existing-branch", path=Path("/tmp/wt"))
        add_call = mock_run.call_args_list[1]
        assert "-b" not in add_call.args[0]

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_show_ref_checks_correct_branch_ref(self, mock_exists, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        mgr = _make_manager("/repo/proj")
        mgr.create_worktree("mybranch", path=Path("/tmp/wt"))
        show_ref_call = mock_run.call_args_list[0]
        assert show_ref_call.args[0] == [
            "git", "show-ref", "--verify", "refs/heads/mybranch"
        ]

    # --- successful creation -----------------------------------------------

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_returns_path_on_success(self, mock_exists, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        mgr = _make_manager("/repo/proj")
        result = mgr.create_worktree("feat", path=Path("/tmp/wt"))
        assert result == Path("/tmp/wt")

    @patch("forge_harness.worktree_manager.logger")
    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_logs_info_on_success(self, mock_exists, mock_run, mock_logger):
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        mgr = _make_manager("/repo/proj")
        mgr.create_worktree("feat", path=Path("/tmp/wt"))
        mock_logger.info.assert_called_once()
        assert "Created worktree" in mock_logger.info.call_args.args[0]

    # --- CalledProcessError on add -----------------------------------------

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_raises_runtime_error_on_add_failure(self, mock_exists, mock_run):
        err = _cpe(stderr=b"fatal: already checked out")
        mock_run.side_effect = [
            MagicMock(returncode=1),  # show-ref
            err,                      # worktree add fails
        ]
        mgr = _make_manager()
        with pytest.raises(RuntimeError, match="Failed to create worktree"):
            mgr.create_worktree("bad-branch", path=Path("/tmp/wt"))

    @patch("forge_harness.worktree_manager.logger")
    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_logs_error_on_add_failure(self, mock_exists, mock_run, mock_logger):
        err = _cpe(stderr=b"fatal: something went wrong")
        mock_run.side_effect = [
            MagicMock(returncode=1),
            err,
        ]
        mgr = _make_manager()
        with pytest.raises(RuntimeError):
            mgr.create_worktree("bad-branch", path=Path("/tmp/wt"))
        mock_logger.error.assert_called_once()
        assert "Failed to create worktree" in mock_logger.error.call_args.args[0]

    # --- custom path --------------------------------------------------------

    @patch("forge_harness.worktree_manager.subprocess.run")
    @patch("forge_harness.worktree_manager.Path.exists", return_value=False)
    def test_custom_path_used_in_command(self, mock_exists, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),   # branch exists
            MagicMock(returncode=0),   # add success
        ]
        mgr = _make_manager("/repo/proj")
        custom = Path("/custom/worktree")
        mgr.create_worktree("main", path=custom)
        add_cmd = mock_run.call_args_list[1].args[0]
        assert str(custom) in add_cmd


# ---------------------------------------------------------------------------
# WorktreeManager.remove_worktree
# ---------------------------------------------------------------------------

class TestRemoveWorktree:
    """Tests for WorktreeManager.remove_worktree."""

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_basic_remove_command(self, mock_run):
        mgr = _make_manager("/repo/proj")
        mgr.remove_worktree(Path("/tmp/wt"))
        mock_run.assert_called_once_with(
            ["git", "worktree", "remove", "/tmp/wt"],
            cwd=Path("/repo/proj"),
            check=True,
            capture_output=True,
        )

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_force_flag_appended(self, mock_run):
        mgr = _make_manager("/repo/proj")
        mgr.remove_worktree(Path("/tmp/wt"), force=True)
        cmd = mock_run.call_args.args[0]
        assert "--force" in cmd

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_force_false_no_flag(self, mock_run):
        mgr = _make_manager("/repo/proj")
        mgr.remove_worktree(Path("/tmp/wt"), force=False)
        cmd = mock_run.call_args.args[0]
        assert "--force" not in cmd

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_raises_called_process_error_on_failure(self, mock_run):
        mock_run.side_effect = _cpe()
        mgr = _make_manager()
        with pytest.raises(subprocess.CalledProcessError):
            mgr.remove_worktree(Path("/tmp/wt"))

    @patch("forge_harness.worktree_manager.logger")
    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_logs_error_on_failure(self, mock_run, mock_logger):
        mock_run.side_effect = _cpe()
        mgr = _make_manager()
        with pytest.raises(subprocess.CalledProcessError):
            mgr.remove_worktree(Path("/tmp/wt"))
        mock_logger.error.assert_called_once()
        assert "Failed to remove worktree" in mock_logger.error.call_args.args[0]

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_path_converted_to_string_in_command(self, mock_run):
        mgr = _make_manager()
        mgr.remove_worktree(Path("/some/path"))
        cmd = mock_run.call_args.args[0]
        assert "/some/path" in cmd


# ---------------------------------------------------------------------------
# WorktreeManager.prune
# ---------------------------------------------------------------------------

class TestPrune:
    """Tests for WorktreeManager.prune."""

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_prune_calls_correct_command(self, mock_run):
        mgr = _make_manager("/repo/proj")
        mgr.prune()
        mock_run.assert_called_once_with(
            ["git", "worktree", "prune"],
            cwd=Path("/repo/proj"),
            check=True,
        )

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_prune_uses_repo_root_as_cwd(self, mock_run):
        mgr = _make_manager("/custom/root")
        mgr.prune()
        assert mock_run.call_args.kwargs["cwd"] == Path("/custom/root")

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_prune_propagates_called_process_error(self, mock_run):
        mock_run.side_effect = _cpe()
        mgr = _make_manager()
        with pytest.raises(subprocess.CalledProcessError):
            mgr.prune()

    @patch("forge_harness.worktree_manager.subprocess.run")
    def test_prune_returns_none_on_success(self, mock_run):
        mock_run.return_value = MagicMock()
        result = _make_manager().prune()
        assert result is None
