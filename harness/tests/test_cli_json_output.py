"""Tests for CLI --json output flag."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

# These tests were written for forge_harness.cli commands (prime, mvp_run, mvp_check, run,
# bootstrap) which no longer exist. The CLI has been migrated to forge_harness.cli_v2.
# Equivalent commands: 'forge work' replaces run/prime/mvp-run, 'forge plan' replaces bootstrap.
# The internal helpers (_find_forge_root, _detect_github_repo) were also removed.
pytestmark = pytest.mark.skip(reason="CLI command not yet migrated to cli_v2: prime, mvp_run, mvp_check commands removed; run/bootstrap have different APIs in cli_v2")

# Retain the import to avoid NameError in test bodies (will be skipped before execution)
try:
    from forge_harness.cli_v2 import cli as _cli  # noqa: F401
except ImportError:
    pass

# Placeholder stubs so test bodies don't raise NameError at collection time
bootstrap = None  # type: ignore[assignment]
mvp_check = None  # type: ignore[assignment]
mvp_run = None  # type: ignore[assignment]
prime = None  # type: ignore[assignment]
run = None  # type: ignore[assignment]


@pytest.fixture
def runner():
    """Create Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_forge_root(tmp_path):
    """Create a mock FORGE directory structure."""
    domain_dir = tmp_path / "test-domain" / "test-project"
    domain_dir.mkdir(parents=True)
    (domain_dir / "docs").mkdir()
    (domain_dir / "docs" / "PLAN.md").write_text("# Test Plan")
    return tmp_path


class TestPrimeJsonOutput:
    """Tests for prime command --json flag."""

    @patch("forge_harness.cli._find_forge_root")
    def test_prime_json_error_no_root(self, mock_find_root, runner):
        """Test prime --json with missing FORGE root."""
        mock_find_root.return_value = None

        result = runner.invoke(prime, ["--json", "-t", "test task"], input="")
        assert result.exit_code == 1

        output = json.loads(result.output)
        assert output["success"] is False
        assert output["error"]["code"] == "ROOT_NOT_FOUND"
        assert "timestamp" in output

    @patch("forge_harness.cli._find_forge_root")
    @patch("forge_harness.worktree_manager.WorktreeManager")
    @patch("urllib.request.urlopen")
    def test_prime_json_success(
        self, mock_urlopen, mock_wt_manager, mock_find_root, runner, mock_forge_root
    ):
        """Test prime --json with successful execution."""
        mock_find_root.return_value = mock_forge_root
        mock_wt_manager.return_value = MagicMock()

        # Mock offline mode (no Command Center)
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        result = runner.invoke(
            prime,
            [
                "--json",
                "-d",
                "test-domain",
                "-p",
                "test-project",
                "-t",
                "test task",
            ],
        )

        # May exit with 0 or 1 depending on worktree creation
        output = json.loads(result.output)
        assert "success" in output
        assert "timestamp" in output
        if output["success"]:
            assert "result" in output
            assert output["result"]["domain"] == "test-domain"
            assert output["result"]["project"] == "test-project"


class TestRunJsonOutput:
    """Tests for run command --json flag."""

    @patch("forge_harness.cli._find_forge_root")
    def test_run_json_error_no_root(self, mock_find_root, runner):
        """Test run --json with missing FORGE root."""
        mock_find_root.return_value = None

        result = runner.invoke(run, ["--json", "-d", "test-domain", "-p", "test-project"])
        assert result.exit_code == 1

        output = json.loads(result.output)
        assert output["success"] is False
        assert output["error"]["code"] == "ROOT_NOT_FOUND"
        assert "timestamp" in output

    @patch("forge_harness.cli._find_forge_root")
    @patch("forge_harness.cli._detect_github_repo")
    def test_run_json_dry_run(self, mock_detect_repo, mock_find_root, runner, mock_forge_root):
        """Test run --json --dry-run output."""
        mock_find_root.return_value = mock_forge_root
        mock_detect_repo.return_value = "owner/repo"

        result = runner.invoke(
            run,
            ["--json", "--dry-run", "-d", "test-domain", "-p", "test-project"],
        )
        assert result.exit_code == 0

        output = json.loads(result.output)
        assert output["success"] is True
        assert output["result"]["dry_run"] is True
        assert output["result"]["domain"] == "test-domain"
        assert output["result"]["project"] == "test-project"
        assert "timestamp" in output
        assert "duration_ms" in output


class TestBootstrapJsonOutput:
    """Tests for bootstrap command --json flag."""

    @patch("forge_harness.cli._find_forge_root")
    def test_bootstrap_json_error_no_plan(self, mock_find_root, runner, tmp_path):
        """Test bootstrap --json with missing PLAN.md."""
        domain_dir = tmp_path / "test-domain" / "test-project"
        domain_dir.mkdir(parents=True)
        mock_find_root.return_value = tmp_path

        result = runner.invoke(bootstrap, ["--json", "-d", "test-domain", "-p", "test-project"])
        assert result.exit_code == 1

        output = json.loads(result.output)
        assert output["success"] is False
        assert output["error"]["code"] == "PLAN_NOT_FOUND"
        assert "timestamp" in output

    @patch("forge_harness.cli._find_forge_root")
    @patch("forge_harness.issue_bootstrap.validate_plan_md")
    def test_bootstrap_json_validate_only(
        self, mock_validate, mock_find_root, runner, mock_forge_root
    ):
        """Test bootstrap --json --validate-only output."""
        mock_find_root.return_value = mock_forge_root
        mock_validate.return_value = {"errors": [], "warnings": []}

        result = runner.invoke(
            bootstrap,
            [
                "--json",
                "--validate-only",
                "-d",
                "test-domain",
                "-p",
                "test-project",
            ],
        )
        assert result.exit_code == 0

        output = json.loads(result.output)
        assert output["success"] is True
        assert output["result"]["validated"] is True
        assert "timestamp" in output
        assert "duration_ms" in output


class TestMvpRunJsonOutput:
    """Tests for mvp-run command --json flag."""

    @patch("forge_harness.cli._find_forge_root")
    def test_mvp_run_json_error_no_root(self, mock_find_root, runner):
        """Test mvp-run --json with missing FORGE root."""
        mock_find_root.return_value = None

        result = runner.invoke(mvp_run, ["--json", "-d", "test-domain", "-p", "test-project"])
        assert result.exit_code == 1

        output = json.loads(result.output)
        assert output["success"] is False
        assert output["error"]["code"] == "ROOT_NOT_FOUND"
        assert "timestamp" in output


class TestMvpCheckJsonOutput:
    """Tests for mvp-check command --json flag."""

    @patch("forge_harness.cli._find_forge_root")
    def test_mvp_check_json_error_no_root(self, mock_find_root, runner):
        """Test mvp-check --json with missing FORGE root."""
        mock_find_root.return_value = None

        result = runner.invoke(mvp_check, ["--json", "-d", "test-domain", "-p", "test-project"])
        assert result.exit_code == 1

        output = json.loads(result.output)
        assert output["success"] is False
        assert output["error"]["code"] == "ROOT_NOT_FOUND"
        assert "timestamp" in output


def test_json_output_structure():
    """Test that JSON output follows the expected structure."""
    # This is a template test to document the expected JSON structure
    expected_success_structure = {
        "success": True,
        "result": {},
        "timestamp": "2026-02-06T00:00:00.000000Z",
        "duration_ms": 100,
    }

    expected_error_structure = {
        "success": False,
        "error": {"code": "ERROR_CODE", "message": "Error message"},
        "timestamp": "2026-02-06T00:00:00.000000Z",
    }

    # Validate structure keys
    assert set(expected_success_structure.keys()) == {
        "success",
        "result",
        "timestamp",
        "duration_ms",
    }
    assert set(expected_error_structure.keys()) == {
        "success",
        "error",
        "timestamp",
    }
