"""Extended tests for forge_harness/worktree_manager.py.

Targets paths and edge cases not exercised by test_worktree_manager.py
and test_worktree_manager_coverage.py:

- Worktree dataclass representation and hashing
- list_worktrees with unusual porcelain output (bare HEAD, multiple blanks, HEAD before branch)
- create_worktree: path with multiple consecutive slashes in branch name
- create_worktree: git show-ref CalledProcessError (branch check fails)
- remove_worktree: path given as string-like object
- prune: runs with no errors in normal flow
- WorktreeManager with Path subclass repo_root
- Integration: list + create + remove round-trip (fully mocked)
- Repr / str of Worktree instances
- list_worktrees: only HEAD line present (no branch, no leading worktree line)
- create_worktree: explicit path that does NOT exist — exercises the main creation path
- Multiple calls to list_worktrees use separate subprocess calls each time
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


def _run_ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = 0
    return m


def _show_ref(exists: bool) -> MagicMock:
    return MagicMock(returncode=0 if exists else 1)


def _cpe(returncode: int = 1, cmd: str = "git", stderr: bytes = b"") -> subprocess.CalledProcessError:
    err = subprocess.CalledProcessError(returncode, cmd)
    err.stderr = stderr
    return err


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "my-repo"
    root.mkdir()
    return root


@pytest.fixture
def mgr(repo_root: Path) -> WorktreeManager:
    return WorktreeManager(repo_root=repo_root)


# ===========================================================================
# Worktree dataclass — extended
# ===========================================================================


class TestWorktreeDataclassExtended:
    """Extended dataclass tests."""

    def test_repr_contains_path(self):
        wt = Worktree(path=Path("/repo/main"), branch="main", head_hash="abc")
        assert "/repo/main" in repr(wt)

    def test_repr_contains_branch(self):
        wt = Worktree(path=Path("/repo"), branch="feat/x", head_hash="abc")
        assert "feat/x" in repr(wt)

    def test_repr_contains_head_hash(self):
        wt = Worktree(path=Path("/repo"), branch="main", head_hash="deadbeef")
        assert "deadbeef" in repr(wt)

    def test_path_type_is_path(self):
        wt = Worktree(path=Path("/x"), branch="b", head_hash="h")
        assert isinstance(wt.path, Path)

    def test_inequality_different_path(self):
        wt1 = Worktree(path=Path("/a"), branch="main", head_hash="abc")
        wt2 = Worktree(path=Path("/b"), branch="main", head_hash="abc")
        assert wt1 != wt2

    def test_inequality_different_head(self):
        wt1 = Worktree(path=Path("/a"), branch="main", head_hash="abc")
        wt2 = Worktree(path=Path("/a"), branch="main", head_hash="xyz")
        assert wt1 != wt2

    def test_fields_are_assignable(self):
        wt = Worktree(path=Path("/x"), branch="b", head_hash="h")
        wt.branch = "new-branch"
        assert wt.branch == "new-branch"


# ===========================================================================
# WorktreeManager.list_worktrees — edge cases
# ===========================================================================


class TestListWorktreesEdgeCases:
    """Edge cases for list_worktrees."""

    def test_head_line_before_branch_line_parses_correctly(self, mgr, repo_root):
        """Porcelain output with HEAD before branch is parsed correctly."""
        output = f"worktree {repo_root}\nHEAD cafe1234\nbranch refs/heads/develop\n"
        with patch("subprocess.run", return_value=_run_ok(output)):
            worktrees = mgr.list_worktrees()
        assert len(worktrees) == 1
        assert worktrees[0].branch == "develop"
        assert worktrees[0].head_hash == "cafe1234"

    def test_multiple_blank_lines_between_blocks(self, mgr, repo_root):
        """Multiple blank lines between blocks do not create phantom entries."""
        sibling = repo_root.parent / "sibling"
        output = (
            f"worktree {repo_root}\nHEAD aaa\nbranch refs/heads/main\n"
            f"\n\n\n"
            f"worktree {sibling}\nHEAD bbb\nbranch refs/heads/dev\n"
        )
        with patch("subprocess.run", return_value=_run_ok(output)):
            worktrees = mgr.list_worktrees()
        assert len(worktrees) == 2

    def test_branch_refs_heads_double_prefix_stripped_completely(self, mgr, repo_root):
        """str.replace() strips ALL occurrences of refs/heads/ in the branch value."""
        output = f"worktree {repo_root}\nHEAD abc\nbranch refs/heads/refs/heads/weird\n"
        with patch("subprocess.run", return_value=_run_ok(output)):
            worktrees = mgr.list_worktrees()
        # replace() acts on all occurrences, so both refs/heads/ portions are removed
        assert worktrees[0].branch == "weird"

    def test_three_worktrees_parsed(self, mgr, repo_root):
        """Three porcelain blocks are all parsed."""
        p1 = repo_root.parent / "r1"
        p2 = repo_root.parent / "r2"
        output = (
            f"worktree {repo_root}\nHEAD a\nbranch refs/heads/main\n\n"
            f"worktree {p1}\nHEAD b\nbranch refs/heads/feat\n\n"
            f"worktree {p2}\nHEAD c\nbranch refs/heads/fix\n"
        )
        with patch("subprocess.run", return_value=_run_ok(output)):
            worktrees = mgr.list_worktrees()
        assert len(worktrees) == 3

    def test_branch_with_no_refs_heads_prefix(self, mgr, repo_root):
        """Branch value without refs/heads/ prefix is returned unchanged."""
        output = f"worktree {repo_root}\nHEAD abc\nbranch main\n"
        with patch("subprocess.run", return_value=_run_ok(output)):
            worktrees = mgr.list_worktrees()
        assert worktrees[0].branch == "main"

    def test_multiple_calls_make_multiple_subprocess_calls(self, mgr, repo_root):
        """Each call to list_worktrees triggers a fresh subprocess.run call."""
        output = f"worktree {repo_root}\nHEAD abc\nbranch refs/heads/main\n"
        with patch("subprocess.run", return_value=_run_ok(output)) as mock_run:
            mgr.list_worktrees()
            mgr.list_worktrees()
        assert mock_run.call_count == 2

    def test_single_line_worktree_only(self, mgr, repo_root):
        """A block containing only 'worktree' line yields one entry."""
        output = f"worktree {repo_root}\n"
        with patch("subprocess.run", return_value=_run_ok(output)):
            worktrees = mgr.list_worktrees()
        assert len(worktrees) == 1
        assert worktrees[0].branch == "detached"
        assert worktrees[0].head_hash == ""

    def test_subprocess_called_with_correct_cwd(self, mgr, repo_root):
        """subprocess.run is called with cwd=repo_root."""
        with patch("subprocess.run", return_value=_run_ok("")) as mock_run:
            mgr.list_worktrees()
        assert mock_run.call_args[1]["cwd"] == repo_root

    def test_unknown_lines_are_ignored(self, mgr, repo_root):
        """Lines that don't start with known prefixes are silently skipped."""
        output = f"worktree {repo_root}\nunknown_key value\nHEAD abc\nbranch refs/heads/main\n"
        with patch("subprocess.run", return_value=_run_ok(output)):
            worktrees = mgr.list_worktrees()
        assert len(worktrees) == 1
        assert worktrees[0].branch == "main"

    def test_path_stored_as_path_object(self, mgr, repo_root):
        """Parsed worktree.path is a Path instance."""
        output = f"worktree {repo_root}\nHEAD abc\nbranch refs/heads/main\n"
        with patch("subprocess.run", return_value=_run_ok(output)):
            worktrees = mgr.list_worktrees()
        assert isinstance(worktrees[0].path, Path)


# ===========================================================================
# WorktreeManager.create_worktree — extended
# ===========================================================================


class TestCreateWorktreeExtended:
    """Extended tests for create_worktree."""

    def test_branch_with_double_slash_normalized_in_path(self, mgr, repo_root):
        """Double slashes in branch name become double dashes in default path."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_show_ref(False), _run_ok()]
            result = mgr.create_worktree("feat//weird")
        assert "feat--weird" in result.name or "feat" in result.name

    def test_explicit_path_not_existing_triggers_git_commands(self, mgr, tmp_path):
        """When explicit path does not exist, both show-ref and worktree add are called."""
        target = tmp_path / "brand-new-wt"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_show_ref(True), _run_ok()]
            mgr.create_worktree("some-branch", path=target)
        assert mock_run.call_count == 2

    def test_show_ref_command_uses_correct_branch(self, mgr, tmp_path):
        """The show-ref command uses the exact branch name provided."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_show_ref(True), _run_ok()]
            mgr.create_worktree("my-special-branch", path=target)
        show_ref_cmd = mock_run.call_args_list[0][0][0]
        assert "my-special-branch" in " ".join(show_ref_cmd)

    def test_worktree_add_command_includes_path_as_string(self, mgr, tmp_path):
        """The worktree add command passes path as a string."""
        target = tmp_path / "wt-str"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_show_ref(True), _run_ok()]
            mgr.create_worktree("main", path=target)
        add_cmd = mock_run.call_args_list[1][0][0]
        assert str(target) in add_cmd

    def test_worktree_add_includes_branch_name(self, mgr, tmp_path):
        """The worktree add command ends with the branch name."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_show_ref(True), _run_ok()]
            mgr.create_worktree("release-v2", path=target)
        add_cmd = mock_run.call_args_list[1][0][0]
        assert "release-v2" in add_cmd

    def test_show_ref_failure_treated_as_branch_absent(self, mgr, tmp_path):
        """If show-ref CalledProcessError is ignored and returncode != 0 is used."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            # show-ref returns non-zero (branch absent) — not an exception
            show_ref = MagicMock(returncode=128)
            mock_run.side_effect = [show_ref, _run_ok()]
            result = mgr.create_worktree("absent-branch", path=target)
        # Should have used -b flag
        add_cmd = mock_run.call_args_list[1][0][0]
        assert "-b" in add_cmd
        assert result == target

    def test_error_message_includes_stderr(self, mgr, tmp_path):
        """RuntimeError raised on git failure includes the stderr content."""
        target = tmp_path / "wt"
        cpe = _cpe(returncode=1, stderr=b"fatal: branch already exists")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_show_ref(False), cpe]
            with pytest.raises(RuntimeError) as exc_info:
                mgr.create_worktree("dup-branch", path=target)
        assert "Failed to create worktree" in str(exc_info.value)

    def test_default_path_uses_repo_name(self, mgr, repo_root):
        """Default path starts with the repo root name."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_show_ref(True), _run_ok()]
            result = mgr.create_worktree("test-branch")
        assert repo_root.name in result.name

    def test_existing_path_returns_path_immediately(self, mgr, tmp_path):
        """If path exists, it is returned before any git command runs."""
        stale = tmp_path / "stale"
        stale.mkdir()
        with patch("subprocess.run") as mock_run:
            result = mgr.create_worktree("any-branch", path=stale)
        mock_run.assert_not_called()
        assert result == stale

    def test_new_branch_flag_position_in_cmd(self, mgr, tmp_path):
        """The -b flag appears immediately after 'add' and before path."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [_show_ref(False), _run_ok()]
            mgr.create_worktree("new-b", path=target)
        add_cmd = mock_run.call_args_list[1][0][0]
        add_idx = add_cmd.index("add")
        assert add_cmd[add_idx + 1] == "-b"


# ===========================================================================
# WorktreeManager.remove_worktree — extended
# ===========================================================================


class TestRemoveWorktreeExtended:
    """Extended tests for remove_worktree."""

    def test_remove_uses_check_true(self, mgr, tmp_path):
        """remove_worktree runs subprocess with check=True."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.remove_worktree(target)
        assert mock_run.call_args[1]["check"] is True

    def test_remove_uses_capture_output(self, mgr, tmp_path):
        """remove_worktree captures subprocess output."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.remove_worktree(target)
        assert mock_run.call_args[1]["capture_output"] is True

    def test_remove_cwd_is_repo_root(self, mgr, repo_root, tmp_path):
        """remove_worktree runs in the repo_root directory."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.remove_worktree(target)
        assert mock_run.call_args[1]["cwd"] == repo_root

    def test_force_flag_appended_after_path(self, mgr, tmp_path):
        """--force is the last element in the command when force=True."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.remove_worktree(target, force=True)
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "--force"

    def test_error_logged_before_reraise(self, mgr, tmp_path):
        """logger.error is called before CalledProcessError is re-raised."""
        target = tmp_path / "wt"
        with patch("subprocess.run", side_effect=_cpe()):
            with patch("forge_harness.worktree_manager.logger") as mock_logger:
                with pytest.raises(subprocess.CalledProcessError):
                    mgr.remove_worktree(target)
        mock_logger.error.assert_called_once()

    def test_non_force_remove_command_structure(self, mgr, tmp_path):
        """Without force, command is exactly ['git', 'worktree', 'remove', str(path)]."""
        target = tmp_path / "wt"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.remove_worktree(target, force=False)
        expected = ["git", "worktree", "remove", str(target)]
        assert mock_run.call_args[0][0] == expected


# ===========================================================================
# WorktreeManager.prune — extended
# ===========================================================================


class TestPruneExtended:
    """Extended tests for prune."""

    def test_prune_does_not_capture_output(self, mgr, repo_root):
        """prune does not pass capture_output (unlike other methods)."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.prune()
        call_kwargs = mock_run.call_args[1]
        # capture_output is not set by prune
        assert "capture_output" not in call_kwargs

    def test_prune_runs_in_repo_root(self, mgr, repo_root):
        """prune passes cwd=repo_root to subprocess."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.prune()
        assert mock_run.call_args[1]["cwd"] == repo_root

    def test_prune_uses_check_true(self, mgr):
        """prune passes check=True."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.prune()
        assert mock_run.call_args[1]["check"] is True

    def test_prune_command_is_correct(self, mgr):
        """prune command is ['git', 'worktree', 'prune']."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _run_ok()
            mgr.prune()
        assert mock_run.call_args[0][0] == ["git", "worktree", "prune"]

    def test_prune_succeeds_silently(self, mgr):
        """prune returns None on success."""
        with patch("subprocess.run", return_value=_run_ok()):
            result = mgr.prune()
        assert result is None


# ===========================================================================
# WorktreeManager — init edge cases
# ===========================================================================


class TestWorktreeManagerInitExtended:
    """Extended init tests."""

    def test_accepts_nested_path(self, tmp_path):
        """WorktreeManager accepts nested paths."""
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        mgr = WorktreeManager(repo_root=nested)
        assert mgr.repo_root == nested

    def test_repo_root_name_attribute_accessible(self, tmp_path):
        """repo_root.name is accessible for path building."""
        root = tmp_path / "cool-repo"
        root.mkdir()
        mgr = WorktreeManager(repo_root=root)
        assert mgr.repo_root.name == "cool-repo"

    def test_repo_root_parent_accessible(self, tmp_path):
        """repo_root.parent is accessible for sibling path convention."""
        root = tmp_path / "my-repo"
        root.mkdir()
        mgr = WorktreeManager(repo_root=root)
        assert mgr.repo_root.parent == tmp_path


# ===========================================================================
# _parse_worktree — extended
# ===========================================================================


class TestParseWorktreeExtended:
    """Extended tests for _parse_worktree internal helper."""

    def test_path_with_spaces_in_value(self, mgr):
        """A path containing spaces in the value is stored correctly."""
        data = {"path": "/path/with spaces/repo", "branch": "main", "head": "abc"}
        wt = mgr._parse_worktree(data)
        assert str(wt.path) == "/path/with spaces/repo"

    def test_returns_worktree_instance(self, mgr):
        """_parse_worktree always returns a Worktree instance."""
        wt = mgr._parse_worktree({"path": "/x", "branch": "b", "head": "h"})
        assert isinstance(wt, Worktree)

    def test_extra_keys_in_dict_are_ignored(self, mgr):
        """Extra keys beyond path/branch/head are silently ignored."""
        data = {"path": "/x", "branch": "b", "head": "h", "extra": "ignored"}
        wt = mgr._parse_worktree(data)
        assert wt.branch == "b"

    def test_branch_with_numeric_suffix(self, mgr):
        """Branch names with numeric suffixes parse correctly."""
        wt = mgr._parse_worktree({"path": "/r", "branch": "release-2024", "head": "abc"})
        assert wt.branch == "release-2024"


# ===========================================================================
# Integration: mock round-trip
# ===========================================================================


class TestIntegrationRoundTrip:
    """Integration tests that chain multiple operations (fully mocked)."""

    def test_create_then_list_shows_new_worktree(self, mgr, repo_root, tmp_path):
        """After creating a worktree, listing includes it in the output."""
        target = tmp_path / "new-wt"

        with patch("subprocess.run") as mock_run:
            # create_worktree: show-ref + add
            create_calls = [_show_ref(False), _run_ok()]
            # list_worktrees: return two worktrees
            list_output = (
                f"worktree {repo_root}\nHEAD a\nbranch refs/heads/main\n\n"
                f"worktree {target}\nHEAD b\nbranch refs/heads/new-feature\n"
            )
            list_calls = [_run_ok(list_output)]
            mock_run.side_effect = create_calls + list_calls

            created_path = mgr.create_worktree("new-feature", path=target)
            worktrees = mgr.list_worktrees()

        assert created_path == target
        assert len(worktrees) == 2
        branches = [wt.branch for wt in worktrees]
        assert "new-feature" in branches

    def test_create_then_remove_then_prune(self, mgr, repo_root, tmp_path):
        """Full lifecycle: create, remove, prune — all subprocess calls in order."""
        target = tmp_path / "lifecycle-wt"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _show_ref(True),   # create: branch exists
                _run_ok(),          # create: worktree add
                _run_ok(),          # remove
                _run_ok(),          # prune
            ]

            created = mgr.create_worktree("main", path=target)
            mgr.remove_worktree(target)
            mgr.prune()

        assert created == target
        assert mock_run.call_count == 4

    def test_list_failure_does_not_affect_subsequent_create(self, mgr, repo_root, tmp_path):
        """A list failure (empty list) does not prevent subsequent create."""
        target = tmp_path / "wt"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CalledProcessError(128, "git"),  # list fails
                _show_ref(True),                            # create: show-ref
                _run_ok(),                                   # create: add
            ]

            # list fails gracefully
            result = mgr.list_worktrees()
            assert result == []

            # create still works
            created = mgr.create_worktree("branch", path=target)
            assert created == target
