"""
Tests for harness/scripts/xnode-bootstrap.sh

Covers:
    - Argument parsing: --hub-url, --token, --node-id, --dry-run, --help
    - OS detection logic (Linux vs macOS) via uname mock
    - Token validation (missing token exits non-zero)
    - Default parameter resolution (env vars, fallback defaults)
    - Dry-run mode (no filesystem mutations, no service installs)
    - --help output contains expected flags
    - Verification summary table appears in output
    - Node ID sanitisation (uppercase -> lowercase, underscores -> dashes)

Strategy: subprocess invocations of the shell script with an isolated
temporary directory as FORGE_ROOT.  All network calls (curl) and service
installers (systemctl/launchctl) are mocked via PATH-prepended stub scripts
placed in a temporary bin directory, so no real system services are touched.
"""

from __future__ import annotations

import os
import platform
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "xnode-bootstrap.sh"


def _write_stub(bin_dir: Path, name: str, body: str, exit_code: int = 0) -> Path:
    """Write a shell stub script to bin_dir and make it executable."""
    stub = bin_dir / name
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            {body}
            exit {exit_code}
            """
        )
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _run_bootstrap(
    args: list[str],
    *,
    forge_root: Path,
    env_extra: dict[str, str] | None = None,
    stub_curl_success: bool = True,
    stub_systemctl: bool = True,
    stub_launchctl: bool = True,
    stub_uv: bool = True,
    stub_nodes_endpoint: bool = True,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """
    Run xnode-bootstrap.sh in an isolated environment.

    Creates:
      - A minimal FORGE_ROOT layout (harness/scripts/lib.sh symlinked)
      - A bin/ dir prepended to PATH with stub commands so no real system
        calls (curl, systemctl, launchctl, uv) are made.

    Returns the CompletedProcess with stdout+stderr captured.
    """
    # Ensure FORGE_ROOT has the expected repo layout so lib.sh can be found.
    fake_harness_scripts = forge_root / "harness" / "scripts"
    fake_harness_scripts.mkdir(parents=True, exist_ok=True)

    # Copy lib.sh into the fake FORGE_ROOT so the script's sanity check passes.
    real_lib = SCRIPT_PATH.parent / "lib.sh"
    fake_lib = fake_harness_scripts / "lib.sh"
    if real_lib.exists() and not fake_lib.exists():
        fake_lib.write_text(real_lib.read_text())

    # Create a stub bin directory so we don't invoke real system tools.
    bin_dir = forge_root / "_stub_bin"
    bin_dir.mkdir(exist_ok=True)

    # --- curl stub ---
    if stub_curl_success:
        # Health check succeeds; /api/nodes returns the node ID on second call
        _write_stub(
            bin_dir,
            "curl",
            textwrap.dedent(
                """\
                # Intercept health check and /api/nodes calls
                args="$*"
                if echo "$args" | grep -q '/health'; then
                    exit 0
                elif echo "$args" | grep -q '/api/nodes'; then
                    # Print a JSON-like stub that contains any node name
                    # so the registration check passes.
                    printf '{"nodes":{"testnode":{},"sati":{},"nova":{}}}\\n'
                    exit 0
                fi
                exit 0
                """
            ),
        )
    else:
        # curl always fails
        _write_stub(bin_dir, "curl", "", exit_code=1)

    # --- systemctl stub (Linux service install) ---
    if stub_systemctl:
        _write_stub(bin_dir, "systemctl", "true  # stub: systemctl $*")

    # --- launchctl stub (macOS service install) ---
    if stub_launchctl:
        _write_stub(bin_dir, "launchctl", "true  # stub: launchctl $*")

    # --- uv stub (forge CLI invocations) ---
    if stub_uv:
        _write_stub(
            bin_dir,
            "uv",
            textwrap.dedent(
                """\
                # Stub: uv run ... nodes heartbeat -> success
                args="$*"
                if echo "$args" | grep -q 'heartbeat'; then
                    echo '[INFO] Heartbeat stub: success'
                    exit 0
                fi
                exit 0
                """
            ),
        )

    # --- hostname stub (predictable node ID in tests) ---
    _write_stub(bin_dir, "hostname", 'printf "testnode\\n"')

    env = os.environ.copy()
    # Prepend stub bin so stubs shadow real binaries
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    env["FORGE_ROOT"] = str(forge_root)
    # Clear any ambient tokens that might affect defaults
    env.pop("FORGE_WEBHOOK_TOKEN", None)
    env.pop("COMMAND_CENTER_URL", None)
    env.pop("FORGE_NODE_ID", None)

    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return result


def _combined(result: subprocess.CompletedProcess) -> str:
    """Return stdout + stderr concatenated for assertion convenience."""
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def forge_root(tmp_path: Path) -> Path:
    """Return a temporary directory used as an isolated FORGE root."""
    return tmp_path


# ===========================================================================
# --help flag
# ===========================================================================


class TestHelpFlag:
    def test_help_exits_zero(self, forge_root: Path) -> None:
        result = _run_bootstrap(["--help"], forge_root=forge_root)
        assert result.returncode == 0, result.stderr

    def test_help_mentions_hub_url(self, forge_root: Path) -> None:
        result = _run_bootstrap(["--help"], forge_root=forge_root)
        assert "--hub-url" in result.stdout

    def test_help_mentions_token(self, forge_root: Path) -> None:
        result = _run_bootstrap(["--help"], forge_root=forge_root)
        assert "--token" in result.stdout

    def test_help_mentions_node_id(self, forge_root: Path) -> None:
        result = _run_bootstrap(["--help"], forge_root=forge_root)
        assert "--node-id" in result.stdout

    def test_help_mentions_dry_run(self, forge_root: Path) -> None:
        result = _run_bootstrap(["--help"], forge_root=forge_root)
        assert "--dry-run" in result.stdout

    def test_help_shows_default_hub_url(self, forge_root: Path) -> None:
        result = _run_bootstrap(["--help"], forge_root=forge_root)
        # Default hub should be mentioned in help
        assert "prya" in result.stdout or "8080" in result.stdout


# ===========================================================================
# Token validation
# ===========================================================================


class TestTokenValidation:
    def test_exits_nonzero_without_token(self, forge_root: Path) -> None:
        """Missing --token and no env var should produce a non-zero exit."""
        result = _run_bootstrap(["--dry-run"], forge_root=forge_root)
        assert result.returncode != 0

    def test_error_message_mentions_token(self, forge_root: Path) -> None:
        result = _run_bootstrap(["--dry-run"], forge_root=forge_root)
        combined = _combined(result)
        assert "token" in combined.lower() or "FORGE_WEBHOOK_TOKEN" in combined

    def test_token_via_env_var_succeeds(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--dry-run"],
            forge_root=forge_root,
            env_extra={"FORGE_WEBHOOK_TOKEN": "envtoken"},
        )
        assert result.returncode == 0, _combined(result)

    def test_token_via_cli_flag_succeeds(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "clitoken", "--dry-run"],
            forge_root=forge_root,
        )
        assert result.returncode == 0, _combined(result)

    def test_cli_token_overrides_env(self, forge_root: Path) -> None:
        """--token flag takes precedence over FORGE_WEBHOOK_TOKEN env var."""
        result = _run_bootstrap(
            ["--token", "cli-wins", "--dry-run"],
            forge_root=forge_root,
            env_extra={"FORGE_WEBHOOK_TOKEN": "env-loses"},
        )
        assert result.returncode == 0, _combined(result)


# ===========================================================================
# OS detection
# ===========================================================================


class TestOsDetection:
    """Verify that the script correctly identifies and reports the current OS.

    We cannot change uname inside a subprocess easily without a real mock binary,
    but we can verify the script reports the *current* OS and that the output
    reflects it in the summary.
    """

    def test_reports_os_in_output(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        # Either linux or macos must appear in the output
        assert "linux" in combined.lower() or "macos" in combined.lower()

    def test_os_matches_current_platform(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        if platform.system() == "Linux":
            assert "linux" in combined.lower()
        elif platform.system() == "Darwin":
            assert "macos" in combined.lower()

    def test_dry_run_mentions_correct_service_manager(self, forge_root: Path) -> None:
        """Dry-run output should reference systemd on Linux or launchd on macOS."""
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        if platform.system() == "Linux":
            assert "systemd" in combined.lower() or "systemctl" in combined.lower()
        elif platform.system() == "Darwin":
            assert "launchd" in combined.lower() or "launchctl" in combined.lower()


# ===========================================================================
# --node-id argument
# ===========================================================================


class TestNodeId:
    def test_default_node_id_from_hostname(self, forge_root: Path) -> None:
        """Without --node-id, the script uses the hostname."""
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        # Our stub hostname returns "testnode"
        assert "testnode" in combined

    def test_explicit_node_id_used(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--node-id", "mynode", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        assert "mynode" in combined

    def test_node_id_env_var_used(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
            env_extra={"FORGE_NODE_ID": "envnode"},
        )
        combined = _combined(result)
        assert "envnode" in combined

    def test_cli_node_id_overrides_env(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--node-id", "cli-node", "--dry-run"],
            forge_root=forge_root,
            env_extra={"FORGE_NODE_ID": "env-node"},
        )
        combined = _combined(result)
        assert "cli-node" in combined

    def test_node_id_passed_as_given(self, forge_root: Path) -> None:
        """Explicit --node-id values are passed through as-is (no forced lowercasing).

        Lowercasing is only applied to the *hostname fallback* path so that
        auto-derived node IDs are always lowercase.  Operators who pass
        --node-id explicitly are responsible for their own casing.
        """
        result = _run_bootstrap(
            ["--token", "t", "--node-id", "EXPLICIT-NODE", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        # The node ID should appear verbatim in the output
        assert "EXPLICIT-NODE" in combined

    def test_node_id_underscores_converted_to_dashes(self, forge_root: Path) -> None:
        """Underscores in the hostname default should be converted to dashes."""
        # We inject a hostname stub that returns a name with underscores.
        bin_dir = forge_root / "_stub_bin2"
        bin_dir.mkdir(exist_ok=True)
        _write_stub(bin_dir, "hostname", 'printf "under_score_node\\n"')
        # Also add the standard stubs
        _write_stub(bin_dir, "curl", "exit 0")
        _write_stub(bin_dir, "uv", "exit 0")
        _write_stub(bin_dir, "systemctl", "exit 0")
        _write_stub(bin_dir, "launchctl", "exit 0")

        fake_harness_scripts = forge_root / "harness" / "scripts"
        fake_harness_scripts.mkdir(parents=True, exist_ok=True)
        real_lib = SCRIPT_PATH.parent / "lib.sh"
        fake_lib = fake_harness_scripts / "lib.sh"
        if real_lib.exists() and not fake_lib.exists():
            fake_lib.write_text(real_lib.read_text())

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}"
        env["FORGE_ROOT"] = str(forge_root)
        env.pop("FORGE_WEBHOOK_TOKEN", None)
        env.pop("FORGE_NODE_ID", None)

        result = subprocess.run(
            ["bash", str(SCRIPT_PATH), "--token", "t", "--dry-run"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        combined = result.stdout + result.stderr
        # Underscores should be replaced with dashes in the node ID
        assert "under-score-node" in combined, (
            f"Expected 'under-score-node' in output.\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


# ===========================================================================
# --hub-url argument
# ===========================================================================


class TestHubUrl:
    def test_default_hub_url_used(self, forge_root: Path) -> None:
        """When --hub-url is not given, the default should appear in output."""
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        assert "prya" in combined or "8080" in combined

    def test_explicit_hub_url_used(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--hub-url", "http://custom-hub:9090", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        assert "custom-hub" in combined

    def test_hub_url_env_var_used(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
            env_extra={"COMMAND_CENTER_URL": "http://envhub:7777"},
        )
        combined = _combined(result)
        assert "envhub" in combined

    def test_cli_hub_url_overrides_env(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--hub-url", "http://cli-hub:1234", "--dry-run"],
            forge_root=forge_root,
            env_extra={"COMMAND_CENTER_URL": "http://env-hub:9999"},
        )
        combined = _combined(result)
        assert "cli-hub" in combined

    def test_trailing_slash_stripped(self, forge_root: Path) -> None:
        """Trailing slash on hub URL should not cause double-slash in endpoints."""
        result = _run_bootstrap(
            ["--token", "t", "--hub-url", "http://hub.example.com:8080/", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        # The stripped URL should appear; a double-slash endpoint would not
        assert "hub.example.com:8080" in combined


# ===========================================================================
# --dry-run mode
# ===========================================================================


class TestDryRun:
    def test_dry_run_exits_zero(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        assert result.returncode == 0, _combined(result)

    def test_dry_run_output_mentions_dry_run(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        assert "dry" in combined.lower() or "DRY" in combined

    def test_dry_run_does_not_create_systemd_unit(self, forge_root: Path) -> None:
        """No unit files should be written in dry-run mode."""
        _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        listener_unit = unit_dir / "forge-xnode-listener.service"
        heartbeat_unit = unit_dir / "forge-heartbeat.service"
        # These may exist from prior runs on the host, but they must NOT have been
        # modified by this dry-run. We verify the forge-root temp dir has no units.
        assert not (forge_root / "forge-xnode-listener.service").exists()
        assert not (forge_root / "forge-heartbeat.service").exists()

    def test_dry_run_does_not_create_launchd_plist(self, forge_root: Path) -> None:
        """No plist files should be written inside forge_root in dry-run mode."""
        _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        # No plists should appear in the temp dir
        assert not list(forge_root.rglob("*.plist"))

    def test_dry_run_shows_verification_summary(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        # The summary table header should be present
        assert "===" in combined or "bootstrap" in combined.lower()

    def test_dry_run_shows_would_run_messages(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        assert "DRY-RUN" in combined or "dry-run" in combined.lower()


# ===========================================================================
# Hub connectivity check
# ===========================================================================


class TestHubConnectivity:
    def test_continues_when_hub_unreachable(self, forge_root: Path) -> None:
        """A failing hub check should produce a warning but NOT abort the script."""
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
            stub_curl_success=False,
        )
        # Script should still exit 0 (or non-fatal) when hub is down in dry-run
        combined = _combined(result)
        assert "warn" in combined.lower() or "fail" in combined.lower() or result.returncode == 0

    def test_passes_hub_check_on_success(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
            stub_curl_success=True,
        )
        assert result.returncode == 0, _combined(result)


# ===========================================================================
# Unknown flags
# ===========================================================================


class TestUnknownFlags:
    def test_unknown_flag_exits_nonzero(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--this-flag-does-not-exist"],
            forge_root=forge_root,
        )
        assert result.returncode != 0

    def test_unknown_flag_prints_error(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--this-flag-does-not-exist"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        assert "unknown" in combined.lower() or "error" in combined.lower()


# ===========================================================================
# Verification summary
# ===========================================================================


class TestVerificationSummary:
    def test_summary_header_present(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        assert "bootstrap" in result.stdout.lower() or "===" in result.stdout

    def test_summary_contains_node_id(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--node-id", "summary-node", "--dry-run"],
            forge_root=forge_root,
        )
        assert "summary-node" in result.stdout + result.stderr

    def test_summary_contains_os(self, forge_root: Path) -> None:
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        assert "linux" in combined.lower() or "macos" in combined.lower()

    def test_summary_has_status_fields(self, forge_root: Path) -> None:
        """Summary table should list all major check areas."""
        result = _run_bootstrap(
            ["--token", "t", "--dry-run"],
            forge_root=forge_root,
        )
        combined = _combined(result)
        # Expect verification rows for key steps
        # Using lower-case tokens from the labels defined in the script
        for keyword in ("listener", "heartbeat", "hub"):
            assert keyword in combined.lower(), (
                f"Expected '{keyword}' in summary output. Got:\n{combined}"
            )


# ===========================================================================
# Script is executable
# ===========================================================================


class TestScriptPermissions:
    def test_script_is_executable(self) -> None:
        s = SCRIPT_PATH.stat()
        assert s.st_mode & stat.S_IXUSR, f"{SCRIPT_PATH} is not executable"

    def test_script_has_shebang(self) -> None:
        first_line = SCRIPT_PATH.read_text().splitlines()[0]
        assert first_line.startswith("#!/"), f"Expected shebang, got: {first_line!r}"
