"""Tests for `forge tasks import` command and the _task_parser helper.

Covers:
- parse_heartbeat_task: YAML frontmatter format
- parse_heartbeat_task: simple header format
- parse_heartbeat_task: edge cases (empty, malformed, minimal)
- import_tasks: dry-run shows count without creating tasks
- import_tasks: live import creates tasks via CommandCenterClient (mocked)
- import_tasks: --archive moves files to .archive/
- import_tasks: skips already-imported tasks (title dedup)
- import_tasks: --json output format
- import_tasks: empty source directory
- import_tasks: source directory not found
- import_tasks: partial failures (malformed files)
- import_tasks: API failure for individual task
- import_tasks: archive dir created automatically
- import_tasks: default source path used when --source omitted
- import_tasks: priority normalisation (critical, blocking, p0, low)
- import_tasks: all files scanned (not just dispatch-*.md)
- import_tasks: subdirectories (archive/) excluded from scan
- import_tasks: JSON dry-run output structure
- import_tasks: JSON live output structure
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli
from forge_harness.cli_v2._task_parser import parse_heartbeat_task

# ===========================================================================
# Helpers / fixtures
# ===========================================================================


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def forge_root(tmp_path: Path) -> Path:
    """Minimal FORGE root with .forge-root marker."""
    (tmp_path / ".forge-root").write_text("")
    return tmp_path


@pytest.fixture
def tasks_dir(forge_root: Path) -> Path:
    """Create the default heartbeat tasks directory."""
    d = forge_root / ".forge" / "heartbeat" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_yaml_file(directory: Path, name: str, **fields) -> Path:
    """Write a task file with YAML frontmatter."""
    defaults = {
        "title": "Test Task Title",
        "priority": "medium",
        "status": "pending",
        "assignee": "gemini",
        "description": "A detailed description of the task.",
    }
    defaults.update(fields)
    lines = ["---"]
    for k, v in defaults.items():
        lines.append(f"{k}: {v}")
    lines += ["---", "", "## Body", "Some body content here."]
    p = directory / name
    p.write_text("\n".join(lines))
    return p


def _make_simple_file(
    directory: Path,
    name: str,
    title: str = "# Fix the Login Bug",
    priority: str = "**Priority:** high",
    status: str = "**Status:** in_progress",
    assignee: str = "**Assigned to:** opencode",
    body: str = "Some task description content here.",
) -> Path:
    """Write a task file in the simple header format."""
    content = f"""{title}

{priority}
{status}
{assignee}

{body}
"""
    p = directory / name
    p.write_text(content)
    return p


def _invoke_import(
    runner: CliRunner,
    forge_root: Path,
    extra_args: list,
    client_mock: MagicMock,
    json_mode: bool = False,
) -> object:
    """Invoke `forge tasks import` with isolated filesystem + mocked client.

    When *json_mode* is True, ``--json`` is placed on the group (``tasks``),
    which is the correct position for the group-level flag.
    """
    if json_mode:
        cmd = ["tasks", "--json", "import"] + extra_args
    else:
        cmd = ["tasks", "import"] + extra_args
    with runner.isolated_filesystem(temp_dir=forge_root):
        with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as cls:
            cls.from_env.return_value = client_mock
            return runner.invoke(cli, cmd)


# ===========================================================================
# parse_heartbeat_task — YAML frontmatter
# ===========================================================================


class TestParseYamlFrontmatter:
    """Parser tests for YAML frontmatter format."""

    def test_extracts_title(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", title="Implement OAuth2 Login")
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Implement OAuth2 Login"

    def test_extracts_priority_high(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", priority="high")
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "high"

    def test_extracts_priority_critical_maps_to_high(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", priority="critical")
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "high"

    def test_extracts_priority_p0_maps_to_high(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", priority="P0")
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "high"

    def test_extracts_priority_low(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", priority="low")
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "low"

    def test_extracts_priority_medium_default(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", priority="medium")
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "medium"

    def test_extracts_status(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", status="in_progress")
        meta = parse_heartbeat_task(f)
        assert meta["status"] == "in_progress"

    def test_extracts_assignee(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", assignee="kimi")
        meta = parse_heartbeat_task(f)
        assert meta["assignee"] == "kimi"

    def test_extracts_description(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md", description="Deploy voice coach to Railway")
        meta = parse_heartbeat_task(f)
        assert meta["description"] == "Deploy voice coach to Railway"

    def test_source_file_is_set(self, tmp_path: Path):
        f = _make_yaml_file(tmp_path, "task.md")
        meta = parse_heartbeat_task(f)
        assert meta["source_file"] == str(f)

    def test_missing_optional_fields_have_defaults(self, tmp_path: Path):
        """File with only title in frontmatter should still parse."""
        content = "---\ntitle: Minimal Task Title\n---\n\nBody here."
        f = tmp_path / "minimal.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Minimal Task Title"
        assert meta["priority"] == "medium"
        assert meta["status"] == "pending"
        assert meta["assignee"] == ""


# ===========================================================================
# parse_heartbeat_task — simple header format
# ===========================================================================


class TestParseSimpleHeaderFormat:
    """Parser tests for simple header (human-authored) format."""

    def test_extracts_h1_title(self, tmp_path: Path):
        f = _make_simple_file(tmp_path, "task.md", title="# Refactor Auth Module")
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Refactor Auth Module"

    def test_extracts_bold_priority_high(self, tmp_path: Path):
        f = _make_simple_file(tmp_path, "task.md", priority="**Priority:** high")
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "high"

    def test_extracts_bold_priority_critical_maps_to_high(self, tmp_path: Path):
        f = _make_simple_file(tmp_path, "task.md", priority="**Priority:** CRITICAL")
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "high"

    def test_extracts_bold_priority_low(self, tmp_path: Path):
        f = _make_simple_file(tmp_path, "task.md", priority="**Priority:** low")
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "low"

    def test_extracts_bold_status(self, tmp_path: Path):
        f = _make_simple_file(tmp_path, "task.md", status="**Status:** done")
        meta = parse_heartbeat_task(f)
        assert meta["status"] == "done"

    def test_extracts_bold_assignee(self, tmp_path: Path):
        f = _make_simple_file(tmp_path, "task.md", assignee="**Assigned to:** cursor")
        meta = parse_heartbeat_task(f)
        assert meta["assignee"] == "cursor"

    def test_task_colon_title_pattern(self, tmp_path: Path):
        """Files using 'TASK: <title>' should extract that as the title."""
        content = "TASK: Deploy Code Atlas to Production\n\n**Priority:** high\n"
        f = tmp_path / "dispatch-task.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Deploy Code Atlas to Production"

    def test_plain_priority_line(self, tmp_path: Path):
        """'Priority: high' without bold markers should be recognised."""
        content = "# Some Task Title\n\nPriority: high\n\nDescription here."
        f = tmp_path / "plain.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "high"

    def test_fallback_title_from_filename(self, tmp_path: Path):
        """When no H1/TASK: line exists, title is derived from filename."""
        content = "Just some content without a heading.\n\nMore content."
        f = tmp_path / "fix-login-bug.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert "fix" in meta["title"].lower() or "login" in meta["title"].lower()


# ===========================================================================
# parse_heartbeat_task — edge / error cases
# ===========================================================================


class TestParseEdgeCases:
    """Edge case and error handling for the parser."""

    def test_empty_file_raises_value_error(self, tmp_path: Path):
        f = tmp_path / "empty.md"
        f.write_text("")
        with pytest.raises(ValueError, match="empty"):
            parse_heartbeat_task(f)

    def test_whitespace_only_file_raises_value_error(self, tmp_path: Path):
        f = tmp_path / "blank.md"
        f.write_text("   \n\n  ")
        with pytest.raises(ValueError, match="empty"):
            parse_heartbeat_task(f)

    def test_nonexistent_file_raises_value_error(self, tmp_path: Path):
        f = tmp_path / "nonexistent.md"
        with pytest.raises(ValueError, match="Cannot read"):
            parse_heartbeat_task(f)

    def test_malformed_yaml_still_parses_gracefully(self, tmp_path: Path):
        """Broken YAML frontmatter falls back to body parsing."""
        content = "---\ntitle: [broken yaml\n---\n\n# Actual Title\n\n**Priority:** medium\n"
        f = tmp_path / "broken.md"
        f.write_text(content)
        # Should not raise — falls back to body parsing
        meta = parse_heartbeat_task(f)
        assert meta["title"]  # Some title extracted

    def test_file_with_only_heading(self, tmp_path: Path):
        content = "# Just a Heading\n"
        f = tmp_path / "heading-only.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["title"] == "Just a Heading"
        assert meta["priority"] == "medium"

    def test_priority_blocking_maps_to_high(self, tmp_path: Path):
        content = "# Task\n\n**Priority:** blocking\n"
        f = tmp_path / "blocking.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "high"

    def test_unknown_priority_defaults_to_medium(self, tmp_path: Path):
        content = "# Task\n\n**Priority:** someweirdvalue\n"
        f = tmp_path / "weird.md"
        f.write_text(content)
        meta = parse_heartbeat_task(f)
        assert meta["priority"] == "medium"


# ===========================================================================
# forge tasks import — dry-run
# ===========================================================================


class TestImportDryRun:
    """Dry-run shows count without creating tasks."""

    def test_dry_run_shows_found_count(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Task Alpha Needs Doing")
        _make_yaml_file(tasks_dir, "task2.md", title="Task Beta Feature Request")
        mock = MagicMock()

        result = _invoke_import(runner, forge_root, ["--dry-run"], mock)

        assert result.exit_code == 0
        mock.sync_create_task.assert_not_called()

    def test_dry_run_json_reports_would_import(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Task Alpha Description Here")
        _make_yaml_file(tasks_dir, "task2.md", title="Task Beta Feature Request")
        mock = MagicMock()

        result = _invoke_import(runner, forge_root, ["--dry-run"], mock, json_mode=True)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["dry_run"] is True
        assert data["data"]["found"] == 2
        assert data["data"]["would_import"] == 2
        mock.sync_create_task.assert_not_called()

    def test_dry_run_json_includes_file_list(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Task Alpha Description Here")
        mock = MagicMock()

        result = _invoke_import(runner, forge_root, ["--dry-run"], mock, json_mode=True)

        data = json.loads(result.output)
        assert len(data["data"]["files"]) == 1
        assert data["data"]["files"][0]["file"] == "task1.md"

    def test_dry_run_reports_parse_errors_without_aborting(self, runner, forge_root, tasks_dir):
        """Malformed files should show in dry-run errors, not abort the run."""
        _make_yaml_file(tasks_dir, "good.md", title="Good Task Description Here")
        (tasks_dir / "empty.md").write_text("")
        mock = MagicMock()

        result = _invoke_import(runner, forge_root, ["--dry-run"], mock, json_mode=True)

        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["parse_errors"] == 1
        assert data["data"]["would_import"] == 1


# ===========================================================================
# forge tasks import — live import
# ===========================================================================


class TestImportLive:
    """Live import creates tasks via CommandCenterClient."""

    def test_import_creates_tasks(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Implement SSO Login for Platform")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t1", "subject": "Implement SSO Login"}

        result = _invoke_import(runner, forge_root, [], mock)

        assert result.exit_code == 0
        mock.sync_create_task.assert_called_once()
        call_kwargs = mock.sync_create_task.call_args
        assert "Implement SSO Login for Platform" in (
            call_kwargs.kwargs.get("subject") or call_kwargs.args[0]
        )

    def test_import_passes_priority_to_client(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Critical Security Patch", priority="critical")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t1"}

        _invoke_import(runner, forge_root, [], mock)

        call_kwargs = mock.sync_create_task.call_args
        assert (call_kwargs.kwargs.get("priority") or call_kwargs.args[2]) == "high"

    def test_import_json_reports_correct_counts(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Task One Alpha Import")
        _make_yaml_file(tasks_dir, "task2.md", title="Task Two Beta Feature")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "tx"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["imported"] == 2
        assert data["data"]["skipped"] == 0
        assert data["data"]["failed"] == 0
        assert data["data"]["found"] == 2

    def test_import_counts_api_failures_as_failed(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Failing Task Due To API")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = None  # API returns falsy

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert data["data"]["failed"] == 1
        assert data["data"]["imported"] == 0

    def test_import_counts_parse_failures_as_failed(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "good.md", title="Good Task Description Here")
        (tasks_dir / "bad.md").write_text("")  # empty = parse error
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t1"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert data["data"]["imported"] == 1
        assert data["data"]["failed"] == 1

    def test_import_handles_api_exception_per_task(self, runner, forge_root, tasks_dir):
        """Exception thrown by sync_create_task is counted as failed, not fatal."""
        _make_yaml_file(tasks_dir, "task1.md", title="Task That Raises Exception")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.side_effect = RuntimeError("connection refused")

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert result.exit_code == 0
        assert data["data"]["failed"] == 1

    def test_import_all_md_files_not_just_dispatch(self, runner, forge_root, tasks_dir):
        """All .md files in source dir are scanned, not just dispatch-*.md."""
        _make_yaml_file(tasks_dir, "active-opencode.md", title="Active Task For OpenCode")
        _make_yaml_file(tasks_dir, "milestone-a.md", title="Milestone A Git Guard")
        _make_yaml_file(tasks_dir, "dispatch-pi.md", title="Dispatch Pi Code Atlas Deploy")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert data["data"]["found"] == 3
        assert mock.sync_create_task.call_count == 3

    def test_import_excludes_subdirectory_files(self, runner, forge_root, tasks_dir):
        """Files inside subdirectories (e.g., .archive/) are NOT imported."""
        _make_yaml_file(tasks_dir, "task1.md", title="Top Level Task Description")
        sub = tasks_dir / ".archive"
        sub.mkdir()
        _make_yaml_file(sub, "old-task.md", title="Archived Task Should Be Ignored")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert data["data"]["found"] == 1


# ===========================================================================
# forge tasks import — skip already-imported tasks
# ===========================================================================


class TestImportSkipDuplicates:
    """Tasks matching an existing title are skipped."""

    def test_skips_task_already_in_command_center(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Deploy Voice Coach to Railway")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = [{"subject": "Deploy Voice Coach to Railway"}]
        mock.sync_create_task.return_value = {"id": "t"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert data["data"]["skipped"] == 1
        assert data["data"]["imported"] == 0
        mock.sync_create_task.assert_not_called()

    def test_skips_duplicate_within_same_run(self, runner, forge_root, tasks_dir):
        """Two files with the same title: first is imported, second is skipped."""
        _make_yaml_file(tasks_dir, "task1.md", title="Duplicate Task Title Here")
        _make_yaml_file(tasks_dir, "task2.md", title="Duplicate Task Title Here")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        # imported + skipped == 2
        assert data["data"]["imported"] + data["data"]["skipped"] == 2
        assert mock.sync_create_task.call_count == 1

    def test_list_tasks_failure_does_not_abort(self, runner, forge_root, tasks_dir):
        """If sync_list_tasks raises, import proceeds with empty existing set."""
        _make_yaml_file(tasks_dir, "task1.md", title="Resilient Import Task Test")
        mock = MagicMock()
        mock.sync_list_tasks.side_effect = RuntimeError("api down")
        mock.sync_create_task.return_value = {"id": "t"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert result.exit_code == 0
        assert data["data"]["imported"] == 1


# ===========================================================================
# forge tasks import — --archive flag
# ===========================================================================


class TestImportArchive:
    """Archive flag moves imported files to .archive/."""

    def test_archive_moves_imported_file(self, runner, forge_root, tasks_dir):
        f = _make_yaml_file(tasks_dir, "task1.md", title="Archivable Task For Testing")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t"}

        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as cls:
                cls.from_env.return_value = mock
                runner.invoke(cli, ["tasks", "import", "--archive"])

        archive_path = tasks_dir / ".archive" / "task1.md"
        assert archive_path.exists(), "File should have been moved to .archive/"
        assert not f.exists(), "Original file should be removed after archive"

    def test_archive_dir_created_automatically(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Auto Archive Dir Creation Test")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t"}

        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as cls:
                cls.from_env.return_value = mock
                runner.invoke(cli, ["tasks", "import", "--archive"])

        assert (tasks_dir / ".archive").is_dir()

    def test_archive_count_reported_in_json(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task1.md", title="Count Archive In JSON Output")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t"}

        result = _invoke_import(runner, forge_root, ["--archive"], mock, json_mode=True)

        data = json.loads(result.output)
        assert data["data"]["archived"] == 1

    def test_no_archive_when_api_returns_falsy(self, runner, forge_root, tasks_dir):
        """Files should NOT be archived when task creation fails."""
        f = _make_yaml_file(tasks_dir, "task1.md", title="Not Archived On Failure")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = None  # creation failed

        with runner.isolated_filesystem(temp_dir=forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as cls:
                cls.from_env.return_value = mock
                runner.invoke(cli, ["tasks", "import", "--archive"])

        # Original file must still exist
        assert f.exists(), "File must not be moved when import failed"


# ===========================================================================
# forge tasks import — empty directory / missing source
# ===========================================================================


class TestImportEdgeCases:
    """Edge case scenarios for forge tasks import."""

    def test_empty_directory_exits_zero(self, runner, forge_root, tasks_dir):
        mock = MagicMock()
        result = _invoke_import(runner, forge_root, [], mock)
        assert result.exit_code == 0
        mock.sync_create_task.assert_not_called()

    def test_empty_directory_json_reports_zero(self, runner, forge_root, tasks_dir):
        mock = MagicMock()
        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["found"] == 0
        assert data["data"]["imported"] == 0

    def test_missing_source_dir_exits_nonzero(self, runner, forge_root):
        mock = MagicMock()
        result = _invoke_import(runner, forge_root, ["--source", "nonexistent/path"], mock)
        assert result.exit_code != 0

    def test_missing_source_dir_json_reports_failure(self, runner, forge_root):
        mock = MagicMock()
        result = _invoke_import(
            runner,
            forge_root,
            ["--source", "nonexistent/path"],
            mock,
            json_mode=True,
        )
        data = json.loads(result.output)
        assert data["success"] is False

    def test_dry_run_empty_directory_exits_zero(self, runner, forge_root, tasks_dir):
        mock = MagicMock()
        result = _invoke_import(runner, forge_root, ["--dry-run"], mock, json_mode=True)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["found"] == 0


# ===========================================================================
# forge tasks import — JSON output envelope
# ===========================================================================


class TestImportJsonOutput:
    """Verify JSON output conforms to FORGE envelope schema."""

    def test_json_envelope_has_success_field(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task.md", title="JSON Envelope Validation Test")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t1"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert "success" in data
        assert "data" in data
        assert "error" in data
        assert "timestamp" in data
        assert "command" in data

    def test_json_envelope_command_field(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task.md", title="Command Field JSON Test Here")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t1"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        assert data["command"] == "forge tasks import"

    def test_json_live_data_has_all_count_fields(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task.md", title="All Count Fields JSON Test")
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []
        mock.sync_create_task.return_value = {"id": "t1"}

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        data = json.loads(result.output)
        for field in ("found", "imported", "skipped", "failed", "archived", "dry_run"):
            assert field in data["data"], f"Missing field: {field}"

    def test_json_dry_run_data_has_all_fields(self, runner, forge_root, tasks_dir):
        _make_yaml_file(tasks_dir, "task.md", title="All Dry Run Fields JSON Test")
        mock = MagicMock()

        result = _invoke_import(runner, forge_root, ["--dry-run"], mock, json_mode=True)

        data = json.loads(result.output)
        for field in ("dry_run", "found", "would_import", "parse_errors", "files", "errors"):
            assert field in data["data"], f"Missing field: {field}"

    def test_json_output_is_valid_json(self, runner, forge_root, tasks_dir):
        """Output must be parseable as JSON when --json flag is used."""
        mock = MagicMock()
        mock.sync_list_tasks.return_value = []

        result = _invoke_import(runner, forge_root, [], mock, json_mode=True)

        # Should not raise
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)
