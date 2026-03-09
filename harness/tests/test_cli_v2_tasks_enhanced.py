"""
Enhanced tests for forge tasks command.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner


class TestTasksValidate:
    """Test task validation functions."""

    def test_validate_task_title_too_short(self):
        """Test validation fails for short title."""
        from forge_harness.cli_v2.tasks import validate_task

        with pytest.raises(ValueError, match="Title must be at least"):
            validate_task("Hi", "A valid description")

    def test_validate_task_generic_title(self):
        """Test validation fails for generic title."""
        from forge_harness.cli_v2.tasks import validate_task

        with pytest.raises(ValueError, match="Generic titles not allowed"):
            validate_task("Unknown Task", "Some description here")

    def test_validate_task_description_too_short(self):
        """Test validation fails for short description."""
        from forge_harness.cli_v2.tasks import validate_task

        with pytest.raises(ValueError, match="Description must be at least"):
            validate_task("Valid title", "short")

    def test_validate_task_generic_description(self):
        """Test validation fails for generic description."""
        from forge_harness.cli_v2.tasks import validate_task

        with pytest.raises(ValueError, match="Generic descriptions not allowed"):
            validate_task("Valid descriptive title", "Execute task")

    def test_validate_task_success(self):
        """Test validation passes for valid input."""
        from forge_harness.cli_v2.tasks import validate_task

        # Should not raise
        validate_task("Implement user authentication", "Add JWT-based auth to the backend")


class TestTasksCommandHelp:
    """Test help commands exist."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_tasks_help(self, runner, mock_forge_root):
        """Test tasks --help works."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["tasks", "--help"])
            assert result.exit_code == 0

    def test_tasks_list_help(self, runner, mock_forge_root):
        """Test tasks list --help works."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["tasks", "list", "--help"])
            assert result.exit_code == 0

    def test_tasks_create_help(self, runner, mock_forge_root):
        """Test tasks create --help works."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            result = runner.invoke(cli, ["tasks", "create", "--help"])
            assert result.exit_code == 0


class TestTasksListEnhanced:
    """Enhanced tests for tasks list."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_tasks_list_empty_json(self, runner, mock_forge_root):
        """Test list shows no tasks JSON."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.sync_list_tasks.return_value = []
                mock_client_cls.from_env.return_value = mock_client

                result = runner.invoke(cli, ["tasks", "list", "--json"])
                assert result.exit_code == 0
                assert "success" in result.output.lower()

    def test_tasks_list_with_tasks_json(self, runner, mock_forge_root):
        """Test list shows tasks JSON."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.sync_list_tasks.return_value = [
                    {
                        "id": "1",
                        "subject": "Test Task",
                        "status": "pending",
                        "priority": "high",
                        "claimed_by": "",
                    }
                ]
                mock_client_cls.from_env.return_value = mock_client

                result = runner.invoke(cli, ["tasks", "list", "--json"])
                assert result.exit_code == 0


class TestTasksCreateEnhanced:
    """Enhanced tests for tasks create."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_tasks_create_success_json(self, runner, mock_forge_root):
        """Test create success JSON output."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.sync_create_task.return_value = {"id": "123", "subject": "Test"}
                mock_client_cls.from_env.return_value = mock_client

                result = runner.invoke(
                    cli,
                    [
                        "tasks",
                        "create",
                        "Test task title here",
                        "--description",
                        "A longer description here",
                    ],
                    catch_exceptions=False,
                )
                assert result.exit_code == 0

    def test_tasks_create_validation_error(self, runner, mock_forge_root):
        """Test create validation error."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.from_env.return_value = mock_client

                result = runner.invoke(
                    cli,
                    ["tasks", "create", "Hi", "--description", "short"],
                )
                assert result.exit_code != 0


class TestTasksStatsEnhanced:
    """Enhanced tests for tasks stats."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_tasks_stats_with_data(self, runner, mock_forge_root):
        """Test stats with data."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.sync_get_task_stats.return_value = {
                    "total": 10,
                    "pending": 5,
                    "in_progress": 3,
                    "completed": 2,
                }
                mock_client_cls.from_env.return_value = mock_client

                result = runner.invoke(cli, ["tasks", "stats", "--json"], catch_exceptions=False)
                assert result.exit_code == 0


class TestTasksRecommendedEnhanced:
    """Enhanced tests for tasks recommended."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_tasks_recommended_with_limit(self, runner, mock_forge_root):
        """Test recommended with limit."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.sync_get_recommended_tasks.return_value = [
                    {"id": "1", "subject": "Task 1", "score": 0.9}
                ]
                mock_client_cls.from_env.return_value = mock_client

                result = runner.invoke(
                    cli, ["tasks", "recommended", "--limit", "5", "--json"], catch_exceptions=False
                )
                assert result.exit_code == 0


class TestTasksDeleteEnhanced:
    """Enhanced tests for tasks delete."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_tasks_delete_with_yes(self, runner, mock_forge_root):
        """Test delete with --yes."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.sync_delete_task.return_value = True
                mock_client_cls.from_env.return_value = mock_client

                result = runner.invoke(
                    cli, ["tasks", "delete", "task-123", "--yes"], catch_exceptions=False
                )
                assert result.exit_code == 0


class TestTasksCompleteEnhanced:
    """Enhanced tests for tasks complete."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        return tmp_path

    def test_tasks_complete_success(self, runner, mock_forge_root):
        """Test complete task."""
        from forge_harness.cli_v2 import cli

        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.tasks.CommandCenterClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.sync_update_task.return_value = {"id": "123", "status": "completed"}
                mock_client_cls.from_env.return_value = mock_client

                result = runner.invoke(cli, ["tasks", "complete", "123"], catch_exceptions=False)
                assert result.exit_code == 0
