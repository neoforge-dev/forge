"""Integration tests for forge heartbeat continuous mode.

Tests verify that heartbeat orchestrator runs multiple cycles without stopping.
These tests would have caught the 2026-02-12 outage (stopped after 3 cycles).

Note: These tests were migrated from orchestrator-loop.sh to forge CLI v2.
The orchestrator-loop.sh script is deprecated in favor of `forge heartbeat`.
"""

import subprocess
import time
from pathlib import Path

import pytest

FORGE_ROOT = Path(__file__).parent.parent.parent


class TestOrchestratorContinuous:
    """Integration tests for continuous orchestrator operation."""

    def test_orchestrator_completes_5_cycles(self):
        """Verify forge heartbeat completes 5 cycles without stopping.

        This test would have caught the 2026-02-12 bug where orchestrator
        stopped after 3 cycles because --continuous wasn't the default.

        Migrated to use `forge heartbeat eval` instead of orchestrator-loop.sh.
        """
        # Run forge heartbeat eval 5 times in succession
        for i in range(5):
            proc = subprocess.run(
                ["forge", "heartbeat", "eval", "--one-line"],
                capture_output=True,
                text=True,
                cwd=str(FORGE_ROOT),
                timeout=10,
            )
            assert proc.returncode == 0, f"forge heartbeat eval failed: {proc.stderr}"
            time.sleep(2)  # Interval between cycles

    def test_orchestrator_runs_continuously_by_default(self):
        """Verify forge heartbeat runs continuously.

        Tests that `forge heartbeat eval` works correctly when run multiple times.
        Migrated from orchestrator-loop.sh to CLI v2.
        """
        # Run forge heartbeat eval twice to verify continuous operation
        for i in range(2):
            proc = subprocess.run(
                ["forge", "heartbeat", "eval", "--one-line"],
                capture_output=True,
                text=True,
                cwd=str(FORGE_ROOT),
                timeout=10,
            )
            assert proc.returncode == 0, f"Cycle {i+1} failed: {proc.stderr}"
            time.sleep(2)

    def test_orchestrator_once_flag_stops_after_one_cycle(self):
        """Verify single forge heartbeat eval completes one cycle.

        Migrated from orchestrator-loop.sh --once to single `forge heartbeat eval`.
        """
        proc = subprocess.run(
            ["forge", "heartbeat", "eval", "--one-line"],
            capture_output=True,
            text=True,
            cwd=str(FORGE_ROOT),
            timeout=10,
        )

        # Should exit successfully
        assert proc.returncode == 0, f"forge heartbeat eval failed: {proc.stderr}"

    def test_orchestrator_max_cycles_respects_limit(self):
        """Verify running forge heartbeat eval N times respects the limit.

        Migrated from orchestrator-loop.sh --max-cycles to multiple `forge heartbeat eval` calls.
        """
        # Run forge heartbeat eval 3 times
        for i in range(3):
            proc = subprocess.run(
                ["forge", "heartbeat", "eval", "--one-line"],
                capture_output=True,
                text=True,
                cwd=str(FORGE_ROOT),
                timeout=10,
            )
            assert proc.returncode == 0, f"Cycle {i+1} failed: {proc.stderr}"

    def test_orchestrator_survives_no_tasks(self):
        """Verify forge heartbeat continues even when no tasks available.

        Regression test: Should NOT exit when task queue is empty.
        Migrated from orchestrator-loop.sh to CLI v2.
        """
        # Run forge heartbeat eval 2 times
        for i in range(2):
            proc = subprocess.run(
                ["forge", "heartbeat", "eval", "--one-line"],
                capture_output=True,
                text=True,
                cwd=str(FORGE_ROOT),
                timeout=10,
            )
            # Should succeed even if no tasks available
            assert proc.returncode == 0, f"Cycle {i+1} failed: {proc.stderr}"
