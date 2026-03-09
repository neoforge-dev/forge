"""Tests for forge patterns CLI commands."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2 import cli

from forge_harness.meta_learning.schemas import PatternRecord


class TestForgePatternsList:
    """Tests for 'forge patterns list' command."""

    @pytest.fixture
    def runner(self):
        """Create CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        """Create mock FORGE root with markers."""
        (tmp_path / ".forge-root").write_text("")
        (tmp_path / ".forge/learning").mkdir()
        return tmp_path

    def test_list_empty_shows_no_patterns(self, runner, mock_forge_root):
        """forge patterns list with no patterns shows 'No patterns'."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {}
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "list"])

                assert result.exit_code == 0
                assert "No patterns" in result.output

    def test_list_shows_pattern_table(self, runner, mock_forge_root):
        """forge patterns list with patterns shows table."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {
                    "auth-001": PatternRecord(
                        pattern_id="auth-001",
                        context_signature="abc123",
                        pattern_type="authentication",
                        total_applications=10,
                        successful_applications=8,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    ),
                    "api-001": PatternRecord(
                        pattern_id="api-001",
                        context_signature="def456",
                        pattern_type="api_endpoint",
                        total_applications=5,
                        successful_applications=4,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    ),
                }
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "list"])

                assert result.exit_code == 0
                assert "auth-001" in result.output
                assert "api-001" in result.output

    def test_list_with_limit(self, runner, mock_forge_root):
        """forge patterns list --limit N limits output."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {
                    f"pattern-{i}": PatternRecord(
                        pattern_id=f"pattern-{i}",
                        context_signature=f"sig-{i}",
                        pattern_type="test",
                        total_applications=10,
                        successful_applications=8,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    )
                    for i in range(5)
                }
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "list", "--limit", "2"])

                assert result.exit_code == 0
                # Should only show 2 patterns in table
                lines = [l for l in result.output.split("\n") if "pattern-" in l]
                assert len(lines) == 2

    def test_list_with_sort_applications(self, runner, mock_forge_root):
        """forge patterns list --sort applications sorts correctly."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {
                    "low": PatternRecord(
                        pattern_id="low",
                        context_signature="sig1",
                        pattern_type="test",
                        total_applications=2,
                        successful_applications=1,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    ),
                    "high": PatternRecord(
                        pattern_id="high",
                        context_signature="sig2",
                        pattern_type="test",
                        total_applications=100,
                        successful_applications=80,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    ),
                }
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "list", "--sort", "applications"])

                assert result.exit_code == 0
                # High should appear before low (sorted by applications descending)
                high_idx = result.output.find("high")
                low_idx = result.output.find("low")
                assert high_idx < low_idx


class TestForgePatternsShow:
    """Tests for 'forge patterns show' command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        (tmp_path / ".forge/learning").mkdir()
        return tmp_path

    def test_show_existing_pattern(self, runner, mock_forge_root):
        """forge patterns show PATTERN_ID shows pattern details."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store.get_pattern.return_value = PatternRecord(
                    pattern_id="auth-001",
                    context_signature="abc123",
                    pattern_type="authentication",
                    total_applications=10,
                    successful_applications=8,
                    last_applied=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                )
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "show", "auth-001"])

                assert result.exit_code == 0
                assert "auth-001" in result.output
                assert "authentication" in result.output
                assert "10" in result.output  # total applications

    def test_show_nonexistent_pattern(self, runner, mock_forge_root):
        """forge patterns show with invalid ID exits with error."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store.get_pattern.return_value = None
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "show", "nonexistent"])

                assert result.exit_code == 6  # ExitCode.NOT_FOUND


class TestForgePatternsStats:
    """Tests for 'forge patterns stats' command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        (tmp_path / ".forge/learning").mkdir()
        return tmp_path

    def test_stats_empty(self, runner, mock_forge_root):
        """forge patterns stats with no patterns shows 'No patterns'."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {}
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "stats"])

                assert result.exit_code == 0
                assert "No patterns" in result.output

    def test_stats_shows_totals(self, runner, mock_forge_root):
        """forge patterns stats shows total count."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {
                    f"pattern-{i}": PatternRecord(
                        pattern_id=f"pattern-{i}",
                        context_signature=f"sig-{i}",
                        pattern_type="test",
                        total_applications=10,
                        successful_applications=8,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    )
                    for i in range(5)
                }
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "stats"])

                assert result.exit_code == 0
                assert "Total Patterns" in result.output
                assert "5" in result.output

    def test_stats_shows_by_type(self, runner, mock_forge_root):
        """forge patterns stats shows breakdown by type."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {
                    "auth-1": PatternRecord(
                        pattern_id="auth-1",
                        context_signature="sig1",
                        pattern_type="authentication",
                        total_applications=10,
                        successful_applications=8,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    ),
                    "auth-2": PatternRecord(
                        pattern_id="auth-2",
                        context_signature="sig2",
                        pattern_type="authentication",
                        total_applications=5,
                        successful_applications=4,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    ),
                    "api-1": PatternRecord(
                        pattern_id="api-1",
                        context_signature="sig3",
                        pattern_type="api_endpoint",
                        total_applications=3,
                        successful_applications=2,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    ),
                }
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "stats"])

                assert result.exit_code == 0
                assert "authentication" in result.output
                assert "api_endpoint" in result.output


class TestForgePatternsJson:
    """Tests for JSON output mode."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_forge_root(self, tmp_path):
        (tmp_path / ".forge-root").write_text("")
        (tmp_path / ".forge/learning").mkdir()
        return tmp_path

    def test_list_json_output(self, runner, mock_forge_root):
        """forge patterns --json list outputs valid JSON."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {
                    "auth-001": PatternRecord(
                        pattern_id="auth-001",
                        context_signature="abc123",
                        pattern_type="authentication",
                        total_applications=10,
                        successful_applications=8,
                        last_applied=datetime.now(UTC),
                        created_at=datetime.now(UTC),
                    ),
                }
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "--json", "list"])

                assert result.exit_code == 0
                # Should be valid JSON
                data = json.loads(result.output)
                assert data["success"] is True
                assert "patterns" in data

    def test_show_json_output(self, runner, mock_forge_root):
        """forge patterns --json show outputs valid JSON."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store.get_pattern.return_value = PatternRecord(
                    pattern_id="auth-001",
                    context_signature="abc123",
                    pattern_type="authentication",
                    total_applications=10,
                    successful_applications=8,
                    last_applied=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                )
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "--json", "show", "auth-001"])

                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert "pattern" in data

    def test_stats_json_output(self, runner, mock_forge_root):
        """forge patterns --json stats outputs valid JSON."""
        with runner.isolated_filesystem(temp_dir=mock_forge_root):
            with patch("forge_harness.cli_v2.patterns.LearningStore") as mock_store_cls:
                mock_store = MagicMock()
                mock_store._patterns = {}
                mock_store_cls.from_config.return_value = mock_store

                result = runner.invoke(cli, ["patterns", "--json", "stats"])

                assert result.exit_code == 0
                data = json.loads(result.output)
                assert data["success"] is True
                assert "stats" in data
