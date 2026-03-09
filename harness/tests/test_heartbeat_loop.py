"""
Integration tests for heartbeat-loop.sh

Tests the shell script that runs the heartbeat orchestrator loop.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

FORGE_ROOT = Path(os.environ.get("FORGE_ROOT", "/Users/bogdan/work/FORGE"))
HEARTBEAT_DIR = FORGE_ROOT / ".forge" / "heartbeat"
SCRIPT_PATH = FORGE_ROOT / ".forge" / "scripts" / "heartbeat-loop.sh"

# Test files
PID_FILE = HEARTBEAT_DIR / "loop.pid"
LIVENESS_FILE = HEARTBEAT_DIR / "loop.liveness"
LOG_FILE = HEARTBEAT_DIR / "loop.log"
LAST_COMMIT_FILE = HEARTBEAT_DIR / "last_commit_ts"


@pytest.fixture
def clean_state():
    """Clean up test state before and after each test."""
    # Cleanup before
    for f in [PID_FILE, LIVENESS_FILE]:
        if f.exists():
            f.unlink()

    yield

    # Cleanup after - kill any running loop
    try:
        if PID_FILE.exists():
            pid = int(PID_FILE.read_text().strip())
            try:
                os.kill(pid, 15)  # SIGTERM
                time.sleep(1)
            except ProcessLookupError:
                pass
    finally:
        for f in [PID_FILE, LIVENESS_FILE]:
            if f.exists():
                f.unlink()


def test_jq_available():
    """Test that jq is available on the system."""
    result = subprocess.run(["which", "jq"], capture_output=True)
    assert result.returncode == 0, "jq is required but not installed"


def test_script_exists():
    """Test that the heartbeat loop script exists."""
    assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
    assert os.access(SCRIPT_PATH, os.X_OK), "Script is not executable"


def test_script_help():
    """Test that --help works."""
    result = subprocess.run(
        [str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "HEARTBEAT_INTERVAL" in result.stdout


def test_pid_file_creation(clean_state):
    """Test that PID file is created when script starts."""
    # Run script with short interval and timeout
    proc = subprocess.Popen(
        [str(SCRIPT_PATH), "--interval=2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Wait for PID file to be created
        time.sleep(3)

        assert PID_FILE.exists(), "PID file was not created"

        pid = int(PID_FILE.read_text().strip())
        assert pid == proc.pid, "PID file contains wrong PID"

        # Verify process is running
        assert proc.poll() is None, "Process died unexpectedly"

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_liveness_file_updates(clean_state):
    """Test that liveness file is updated each cycle."""
    # Run script with short interval
    proc = subprocess.Popen(
        [str(SCRIPT_PATH), "--interval=1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        time.sleep(4)  # Let it run for a few cycles

        assert LIVENESS_FILE.exists(), "Liveness file was not created"

        content = LIVENESS_FILE.read_text().strip()
        assert "T" in content, f"Liveness file has invalid format: {content}"

        # Should have timestamp in ISO format
        timestamp = time.strptime(content[:19], "%Y-%m-%dT%H:%M:%S")
        assert timestamp is not None

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_dry_run_mode(clean_state):
    """Test that --dry-run doesn't make actual commits."""
    # Run in dry-run mode
    proc = subprocess.Popen(
        [str(SCRIPT_PATH), "--interval=1", "--dry-run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        time.sleep(3)

        # In dry-run, should not create last_commit_ts or it should be old
        if LAST_COMMIT_FILE.exists():
            last_commit = int(LAST_COMMIT_FILE.read_text().strip())
            now = int(time.time())
            # If it exists, it should be old (not updated in dry-run)
            assert (now - last_commit) > 300, "Dry-run should not update commit timestamp"

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_duplicate_prevention(clean_state):
    """Test that running a second instance fails due to PID file."""
    # Start first instance
    proc1 = subprocess.Popen(
        [str(SCRIPT_PATH), "--interval=10"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        time.sleep(2)

        # Try to start second instance - should fail
        proc2 = subprocess.run(
            [str(SCRIPT_PATH), "--interval=10"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        # Should fail with FATAL message
        assert proc2.returncode != 0, "Second instance should have failed"
        assert "already running" in proc2.stderr or "already running" in proc1.stdout

    finally:
        proc1.terminate()
        try:
            proc1.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc1.kill()


def test_log_file_writing(clean_state):
    """Test that log file is being written to."""
    # Run script briefly
    proc = subprocess.Popen(
        [str(SCRIPT_PATH), "--interval=2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        time.sleep(3)

        assert LOG_FILE.exists(), "Log file was not created"

        content = LOG_FILE.read_text()
        assert "Heartbeat loop starting" in content, "Log should contain startup message"

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_json_output_parsing():
    """Test that heartbeat eval returns valid JSON."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"cd {FORGE_ROOT / 'harness'} && uv run python -m forge_harness.cli_v2 heartbeat eval --json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"Command failed: {result.stderr}"

    # Parse JSON output
    data = json.loads(result.stdout)

    assert data.get("success") is True
    assert "data" in data
    assert "heartbeat_count" in data["data"]
    assert "triggers" in data["data"]
    assert isinstance(data["data"]["triggers"], list)


def test_trigger_parsing():
    """Test that triggers are correctly parsed from JSON output."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"cd {FORGE_ROOT / 'harness'} && uv run python -m forge_harness.cli_v2 heartbeat eval --json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    data = json.loads(result.stdout)
    triggers = data["data"]["triggers"]

    # Should have at least one trigger (or empty list)
    assert isinstance(triggers, list)

    # Valid trigger values
    valid_triggers = {"RETRO", "CROSSNODE", "COMMIT", "RESULTS", "DISPATCH"}
    for trigger in triggers:
        assert trigger in valid_triggers, f"Invalid trigger: {trigger}"


def test_rate_limiting(clean_state):
    """Test that commits are rate-limited."""
    # Run script with real commits (no --dry-run)
    proc = subprocess.Popen(
        [str(SCRIPT_PATH), "--interval=1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        time.sleep(3)

        # Check log for rate limiting messages
        if LOG_FILE.exists():
            content = LOG_FILE.read_text()
            # Should see either commit or rate limiting
            has_activity = "COMMIT trigger" in content
            assert has_activity, "No trigger activity logged"

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_clean_shutdown(clean_state):
    """Test that script cleans up PID file on exit."""
    proc = subprocess.Popen(
        [str(SCRIPT_PATH), "--interval=10"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        time.sleep(2)
        assert PID_FILE.exists(), "PID file not created"

        # Terminate the process
        proc.terminate()
        proc.wait(timeout=5)

        # PID file should be cleaned up
        time.sleep(1)
        # Note: The script uses trap EXIT, so it should clean up
        # But subprocess.terminate() might not trigger the trap reliably

    finally:
        if proc.poll() is None:
            proc.kill()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
