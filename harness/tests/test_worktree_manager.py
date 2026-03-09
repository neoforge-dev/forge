"""Tests for the WorktreeManager module.

Covers all public methods of WorktreeManager and the Worktree dataclass,
with full mocking of subprocess calls and file-system interactions.
Edge cases covered: stale worktrees, concurrent access, cleanup failures,
partial porcelain output, and detached-HEAD states.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from forge_harness.worktree_manager import Worktree, WorktreeManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Build a fake subprocess.CompletedProcess-like mock."""
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    return result


def _make_show_ref_result(branch_exists: bool) -> MagicMock:
    """Build a fake result for `git show-ref --verify`."""
    return MagicMock(returncode=0 if branch_exists else 1)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path):
    """Create a temporary repo root directory."""
    root = tmp_path / "test-repo"
    root.mkdir()
    return root


@pytest.fixture
def manager(repo_root):
    """Create a WorktreeManager instance backed by repo_root."""
    return WorktreeManager(repo_root=repo_root)


# ---------------------------------------------------------------------------
# Worktree dataclass
# ---------------------------------------------------------------------------


class TestWorktreeDataclass:
    """Tests for the Worktree dataclass."""

    def test_stores_all_fields(self):
        """Worktree stores path, branch, and head_hash."""
        wt = Worktree(path=Path("/repo/wt"), branch="main", head_hash="abc123")
        assert wt.path == Path("/repo/wt")
        assert wt.branch == "main"
        assert wt.head_hash == "abc123"

    def test_equality_same_values(self):
        """Two Worktree instances with identical fields compare equal."""
        wt1 = Worktree(path=Path("/repo"), branch="main", head_hash="abc")
        wt2 = Worktree(path=Path("/repo"), branch="main", head_hash="abc")
        assert wt1 == wt2

    def test_inequality_different_branch(self):
        """Worktrees with different branches are not equal."""
        wt1 = Worktree(path=Path("/repo"), branch="main", head_hash="abc")
        wt2 = Worktree(path=Path("/repo"), branch="develop", head_hash="abc")
        assert wt1 != wt2

    def test_path_is_path_object(self):
        """Worktree.path accepts and stores a Path object."""
        wt = Worktree(path=Path("/some/path"), branch="b", head_hash="h")
        assert isinstance(wt.path, Path)


# ---------------------------------------------------------------------------
# WorktreeManager.__init__
# ---------------------------------------------------------------------------


class TestWorktreeManagerInit:
    """Tests for WorktreeManager initialisation."""

    def test_stores_repo_root(self, repo_root):
        """WorktreeManager stores the provided repo_root."""
        mgr = WorktreeManager(repo_root)
        assert mgr.repo_root == repo_root

    def test_accepts_path_object(self, tmp_path):
        """WorktreeManager accepts a Path instance."""
        mgr = WorktreeManager(tmp_path)
        assert isinstance(mgr.repo_root, Path)


# ---------------------------------------------------------------------------
# WorktreeManager.list_worktrees
# ---------------------------------------------------------------------------


class TestListWorktrees:
    """Tests for WorktreeManager.list_worktrees."""

    # --- single worktree ---

    def test_single_worktree_returns_one_item(self, manager, repo_root):
        """A porcelain block for one worktree yields a list of length 1."""
        output = f"worktree {repo_root}\nHEAD abc123def456789\nbranch refs/heads/main\n"
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert len(worktrees) == 1

    def test_single_worktree_correct_path(self, manager, repo_root):
        """Parsed path matches the value in the porcelain block."""
        output = f"worktree {repo_root}\nHEAD abc\nbranch refs/heads/main\n"
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert worktrees[0].path == repo_root

    def test_single_worktree_strips_refs_heads(self, manager, repo_root):
        """refs/heads/ prefix is stripped from the branch name."""
        output = f"worktree {repo_root}\nHEAD abc\nbranch refs/heads/main\n"
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert worktrees[0].branch == "main"

    def test_single_worktree_head_hash(self, manager, repo_root):
        """HEAD hash is parsed correctly."""
        output = f"worktree {repo_root}\nHEAD deadbeef\nbranch refs/heads/main\n"
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert worktrees[0].head_hash == "deadbeef"

    # --- multiple worktrees ---

    def test_two_worktrees_parsed(self, manager, repo_root):
        """Two porcelain blocks produce a list of two Worktree objects."""
        sibling = repo_root.parent / "test-repo-feature"
        output = (
            f"worktree {repo_root}\nHEAD aaa\nbranch refs/heads/main\n\n"
            f"worktree {sibling}\nHEAD bbb\nbranch refs/heads/feat/my-feature\n"
        )
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert len(worktrees) == 2

    def test_second_worktree_branch_slash_preserved(self, manager, repo_root):
        """Slashes within branch names are preserved after stripping refs/heads/."""
        sibling = repo_root.parent / "test-repo-feature"
        output = (
            f"worktree {repo_root}\nHEAD aaa\nbranch refs/heads/main\n\n"
            f"worktree {sibling}\nHEAD bbb\nbranch refs/heads/feat/my-feature\n"
        )
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert worktrees[1].branch == "feat/my-feature"

    # --- detached HEAD ---

    def test_detached_head_defaults_to_detached(self, manager, repo_root):
        """Worktree block with no branch line uses 'detached' as the branch."""
        output = f"worktree {repo_root}\nHEAD abc123def456789\n"
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert worktrees[0].branch == "detached"

    # --- empty / partial output ---

    def test_empty_output_returns_empty_list(self, manager):
        """Empty git output produces an empty list."""
        with patch("subprocess.run", return_value=_make_run_result("")):
            assert manager.list_worktrees() == []

    def test_trailing_newline_does_not_create_extra_entry(self, manager, repo_root):
        """Trailing blank lines do not generate a spurious empty worktree entry."""
        output = f"worktree {repo_root}\nHEAD abc\nbranch refs/heads/main\n\n"
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert len(worktrees) == 1

    def test_partial_block_missing_head_defaults_to_empty(self, manager, repo_root):
        """A block missing HEAD uses an empty string for head_hash."""
        output = f"worktree {repo_root}\nbranch refs/heads/partial-branch\n"
        with patch("subprocess.run", return_value=_make_run_result(output)):
            worktrees = manager.list_worktrees()
        assert worktrees[0].head_hash == ""

    # --- subprocess / error handling ---

    def test_calls_git_worktree_list_porcelain(self, manager, repo_root):
        """list_worktrees invokes the correct git subcommand."""
        with patch("subprocess.run", return_value=_make_run_result("")) as mock_run:
            manager.list_worktrees()
        mock_run.assert_called_once_with(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_called_process_error_returns_empty_list(self, manager):
        """CalledProcessError is caught and an empty list is returned."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(128, "git"),
        ):
            assert manager.list_worktrees() == []

    def test_called_process_error_logs_error(self, manager):
        """CalledProcessError triggers logger.error."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(128, "git"),
        ):
            with patch("forge_harness.worktree_manager.logger") as mock_logger:
                manager.list_worktrees()
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# WorktreeManager._parse_worktree  (internal helper)
# ---------------------------------------------------------------------------


class TestParseWorktree:
    """Tests for WorktreeManager._parse_worktree."""

    def test_all_fields_populated(self, manager):
        """All three fields are set when the dict is complete."""
        data = {"path": "/repo", "branch": "main", "head": "abc123"}
        wt = manager._parse_worktree(data)
        assert wt.path == Path("/repo")
        assert wt.branch == "main"
        assert wt.head_hash == "abc123"

    def test_missing_path_defaults_to_empty_path(self, manager):
        """Missing path key defaults to Path('')."""
        wt = manager._parse_worktree({"branch": "main", "head": "abc"})
        assert wt.path == Path("")

    def test_missing_branch_defaults_to_detached(self, manager):
        """Missing branch key defaults to 'detached'."""
        wt = manager._parse_worktree({"path": "/repo", "head": "abc"})
        assert wt.branch == "detached"

    def test_missing_head_defaults_to_empty_string(self, manager):
        """Missing head key defaults to empty string."""
        wt = manager._parse_worktree({"path": "/repo", "branch": "main"})
        assert wt.head_hash == ""

    def test_empty_dict_returns_worktree_with_defaults(self, manager):
        """An empty dict produces a Worktree with all defaults."""
        wt = manager._parse_worktree({})
        assert isinstance(wt, Worktree)
        assert wt.path == Path("")
        assert wt.branch == "detached"
        assert wt.head_hash == ""


# ---------------------------------------------------------------------------
# WorktreeManager.create_worktree
# ---------------------------------------------------------------------------


class TestCreateWorktree:
    """Tests for WorktreeManager.create_worktree."""

    # --- basic success paths ---

    def test_returns_path_existing_branch(self, manager, repo_root, tmp_path):
        """Returns the target path after creating a worktree for an existing branch."""
        target = tmp_path / "wt-existing"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_show_ref_result(True),   # branch exists
                _make_run_result(),            # git worktree add
            ]
            result = manager.create_worktree("existing-branch", path=target)
        assert result == target

    def test_returns_path_new_branch(self, manager, repo_root, tmp_path):
        """Returns the target path after creating a new branch worktree."""
        target = tmp_path / "wt-new"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_show_ref_result(False),  # branch absent
                _make_run_result(),
            ]
            result = manager.create_worktree("new-branch", path=target)
        assert result == target

    # --- -b flag behaviour ---

    def test_b_flag_used_for_new_branch(self, manager, tmp_path):
        """git worktree add uses -b when the branch does not exist."""
        target = tmp_path / "wt-new"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_show_ref_result(False),
                _make_run_result(),
            ]
            manager.create_worktree("new-branch", path=target)
        add_cmd = mock_run.call_args_list[1][0][0]
        assert "-b" in add_cmd

    def test_no_b_flag_for_existing_branch(self, manager, tmp_path):
        """git worktree add does NOT use -b when the branch already exists."""
        target = tmp_path / "wt-existing"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_show_ref_result(True),
                _make_run_result(),
            ]
            manager.create_worktree("existing-branch", path=target)
        add_cmd = mock_run.call_args_list[1][0][0]
        assert "-b" not in add_cmd

    # --- default path convention ---

    def test_default_path_is_sibling_directory(self, manager, repo_root):
        """Default path follows {repo_name}-{branch} sibling convention."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_make_show_ref_result(True), _make_run_result()]
            result = manager.create_worktree("main")
        expected = repo_root.parent / f"{repo_root.name}-main"
        assert result == expected

    def test_slash_in_branch_replaced_in_default_path(self, manager, repo_root):
        """Slashes in branch names become dashes in the auto-generated path."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_make_show_ref_result(False), _make_run_result()]
            result = manager.create_worktree("feat/some-feature")
        assert "feat-some-feature" in result.name

    # --- stale / existing path ---

    def test_existing_path_returned_without_subprocess(self, manager, tmp_path):
        """If the target path already exists no git command is run."""
        stale = tmp_path / "stale-wt"
        stale.mkdir()
        with patch("subprocess.run") as mock_run:
            result = manager.create_worktree("stale-branch", path=stale)
        mock_run.assert_not_called()
        assert result == stale

    def test_existing_path_logs_info(self, manager, tmp_path):
        """An info log is emitted when the target path already exists."""
        stale = tmp_path / "stale-wt"
        stale.mkdir()
        with patch("subprocess.run"):
            with patch("forge_harness.worktree_manager.logger") as mock_logger:
                manager.create_worktree("stale-branch", path=stale)
        mock_logger.info.assert_called_once()

    # --- failure handling ---

    def test_raises_runtime_error_on_add_failure(self, manager, tmp_path):
        """CalledProcessError during worktree add is re-raised as RuntimeError."""
        target = tmp_path / "bad-wt"
        cpe = subprocess.CalledProcessError(1, "git")
        cpe.stderr = b"fatal: something went wrong"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_make_show_ref_result(False), cpe]
            with pytest.raises(RuntimeError, match="Failed to create worktree"):
                manager.create_worktree("broken", path=target)

    def test_logs_error_on_add_failure(self, manager, tmp_path):
        """An error log is emitted when git worktree add fails."""
        target = tmp_path / "bad-wt"
        cpe = subprocess.CalledProcessError(1, "git")
        cpe.stderr = b"fatal error"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_make_show_ref_result(True), cpe]
            with patch("forge_harness.worktree_manager.logger") as mock_logger:
                with pytest.raises(RuntimeError):
                    manager.create_worktree("broken", path=target)
        mock_logger.error.assert_called_once()

    def test_logs_info_on_success(self, manager, tmp_path):
        """An info log is emitted after successful worktree creation."""
        target = tmp_path / "good-wt"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_make_show_ref_result(True), _make_run_result()]
            with patch("forge_harness.worktree_manager.logger") as mock_logger:
                manager.create_worktree("good-branch", path=target)
        mock_logger.info.assert_called()

    # --- concurrent access race ---

    def test_show_ref_race_still_succeeds(self, manager, tmp_path):
        """show-ref reports branch absent but add succeeds (race resolved by git)."""
        target = tmp_path / "race-wt"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_show_ref_result(False),  # show-ref says absent
                _make_run_result(),            # add still succeeds
            ]
            result = manager.create_worktree("race-branch", path=target)
        assert result == target


# ---------------------------------------------------------------------------
# WorktreeManager.remove_worktree
# ---------------------------------------------------------------------------


class TestRemoveWorktree:
    """Tests for WorktreeManager.remove_worktree."""

    def test_calls_git_worktree_remove(self, manager, repo_root, tmp_path):
        """remove_worktree calls `git worktree remove <path>`."""
        target = tmp_path / "some-wt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result()
            manager.remove_worktree(target)
        mock_run.assert_called_once_with(
            ["git", "worktree", "remove", str(target)],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )

    def test_force_flag_included_when_requested(self, manager, tmp_path):
        """force=True appends --force to the command."""
        target = tmp_path / "some-wt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result()
            manager.remove_worktree(target, force=True)
        assert "--force" in mock_run.call_args[0][0]

    def test_no_force_flag_by_default(self, manager, tmp_path):
        """--force is absent when force=False (the default)."""
        target = tmp_path / "some-wt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result()
            manager.remove_worktree(target)
        assert "--force" not in mock_run.call_args[0][0]

    def test_re_raises_called_process_error(self, manager, tmp_path):
        """CalledProcessError from git is re-raised (not swallowed)."""
        target = tmp_path / "bad-wt"
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                manager.remove_worktree(target)

    def test_logs_error_on_failure(self, manager, tmp_path):
        """An error log is emitted when removal fails."""
        target = tmp_path / "bad-wt"
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            with patch("forge_harness.worktree_manager.logger") as mock_logger:
                with pytest.raises(subprocess.CalledProcessError):
                    manager.remove_worktree(target)
        mock_logger.error.assert_called_once()

    def test_force_remove_stale_worktree(self, manager, tmp_path):
        """Force-removing a stale (absent-on-disk) worktree completes without error."""
        stale = tmp_path / "stale"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result()
            manager.remove_worktree(stale, force=True)
        assert "--force" in mock_run.call_args[0][0]


# ---------------------------------------------------------------------------
# WorktreeManager.prune
# ---------------------------------------------------------------------------


class TestPrune:
    """Tests for WorktreeManager.prune."""

    def test_calls_git_worktree_prune(self, manager, repo_root):
        """prune calls `git worktree prune` in the repo root."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_run_result()
            manager.prune()
        mock_run.assert_called_once_with(
            ["git", "worktree", "prune"],
            cwd=repo_root,
            check=True,
        )

    def test_prune_propagates_called_process_error(self, manager):
        """CalledProcessError from git worktree prune is not swallowed."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                manager.prune()
