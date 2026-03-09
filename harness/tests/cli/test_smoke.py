"""CLI smoke tests for FORGE CLI v2.

Each test invokes a forge CLI command via subprocess and asserts:
  1. Exit code 0
  2. Non-empty output (stdout or stderr combined)

The CLI entry point is:
    uv run python -m forge_harness.cli_v2 <subcommand>

Working directory is set to harness/ and FORGE_ROOT is set to the repo root
so that commands that read filesystem state can find their data.

Commands that require a running Command Center backend are tested via their
--help flag only, to avoid false failures in CI.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HARNESS_DIR = Path(__file__).resolve().parents[2]   # harness/
FORGE_ROOT = HARNESS_DIR.parent                      # FORGE/


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def run_forge(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run forge CLI command and return the completed process.

    stdout and stderr are captured and merged so tests can inspect combined
    output with a single assertion.
    """
    cmd = ["uv", "run", "python", "-m", "forge_harness.cli_v2", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(HARNESS_DIR),
        env={**os.environ, "FORGE_ROOT": str(FORGE_ROOT)},
    )


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    """Return stdout + stderr merged into one string."""
    return (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------------------
# Smoke test class
# ---------------------------------------------------------------------------

class TestCLISmoke:
    """Basic smoke tests — every command should exit 0 with output."""

    # ------------------------------------------------------------------
    # Top-level help / version
    # ------------------------------------------------------------------

    def test_help(self) -> None:
        """forge --help exits 0 and mentions 'forge' or 'usage'."""
        result = run_forge("--help")
        assert result.returncode == 0
        out = combined_output(result).lower()
        assert "forge" in out or "usage" in out

    def test_version(self) -> None:
        """forge --version exits 0 and contains a version string."""
        result = run_forge("--version")
        assert result.returncode == 0
        out = combined_output(result).lower()
        assert "version" in out or "2." in out

    # ------------------------------------------------------------------
    # Live commands — exit 0 expected without backend
    # ------------------------------------------------------------------

    def test_status(self) -> None:
        """forge status exits 0 and produces non-empty output."""
        result = run_forge("status", timeout=45)
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_tasks_list(self) -> None:
        """forge tasks list exits 0 (falls back gracefully when CC is down)."""
        result = run_forge("tasks", "list", timeout=30)
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_doctor(self) -> None:
        """forge doctor exits 0 or 1 and produces diagnostic output."""
        result = run_forge("doctor", timeout=60)
        out = combined_output(result).strip()
        assert out, "forge doctor should produce diagnostic output"
        # Exit code 1 is acceptable — means doctor found issues (e.g. worktree, missing config)
        assert result.returncode in (0, 1), f"Unexpected exit code {result.returncode}"

    def test_health_all(self) -> None:
        """forge health --all exits 0 and reports check results."""
        result = run_forge("health", "--all", timeout=45)
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_nodes_list_offline(self) -> None:
        """forge nodes list --offline exits 0 reading local heartbeat files."""
        result = run_forge("nodes", "list", "--offline", timeout=30)
        assert result.returncode == 0
        assert combined_output(result).strip()

    # ------------------------------------------------------------------
    # --help variants — always available regardless of backend state
    # ------------------------------------------------------------------

    def test_dispatch_help(self) -> None:
        """forge dispatch --help exits 0."""
        result = run_forge("dispatch", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_xnode_help(self) -> None:
        """forge xnode --help exits 0."""
        result = run_forge("xnode", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_logs_help(self) -> None:
        """forge logs --help exits 0."""
        result = run_forge("logs", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_events_list_help(self) -> None:
        """forge events list --help exits 0."""
        result = run_forge("events", "list", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_lead_help(self) -> None:
        """forge lead --help exits 0."""
        result = run_forge("lead", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_handoff_help(self) -> None:
        """forge handoff --help exits 0."""
        result = run_forge("handoff", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_claims_help(self) -> None:
        """forge claims --help exits 0."""
        result = run_forge("claims", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_evaluator_help(self) -> None:
        """forge evaluator --help exits 0."""
        result = run_forge("evaluator", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_git_guard_help(self) -> None:
        """forge git --help exits 0 (git-guard is registered as 'git')."""
        result = run_forge("git", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_recommend_help(self) -> None:
        """forge recommend --help exits 0."""
        result = run_forge("recommend", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_sessions_help(self) -> None:
        """forge sessions --help exits 0."""
        result = run_forge("sessions", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_quality_help(self) -> None:
        """forge quality --help exits 0."""
        result = run_forge("quality", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    # ------------------------------------------------------------------
    # Additional command coverage
    # ------------------------------------------------------------------

    def test_fleet_help(self) -> None:
        """forge fleet --help exits 0."""
        result = run_forge("fleet", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_health_help(self) -> None:
        """forge health --help exits 0."""
        result = run_forge("health", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_nodes_help(self) -> None:
        """forge nodes --help exits 0."""
        result = run_forge("nodes", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    def test_tasks_help(self) -> None:
        """forge tasks --help exits 0."""
        result = run_forge("tasks", "--help")
        assert result.returncode == 0
        assert combined_output(result).strip()

    @pytest.mark.slow
    def test_nodes_list_live(self) -> None:
        """forge nodes list (live) either exits 0 or fails gracefully (exit 4).

        Exit code 4 (NOT_FOUND / SERVICE_UNAVAILABLE) is acceptable when the
        Command Center backend is not running.
        """
        result = run_forge("nodes", "list", timeout=30)
        out = combined_output(result).strip()
        # Exit 0 = success, 1 = general error (CC not running), 4 = service unavailable
        assert result.returncode in (0, 1, 4), (
            f"nodes list returned unexpected exit code {result.returncode}.\n"
            f"Output: {out}"
        )
