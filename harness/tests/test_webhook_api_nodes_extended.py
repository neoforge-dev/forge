"""Extended tests for forge_harness.webhook_server.api.nodes module.

Covers branches and scenarios NOT already tested in test_webhook_api_nodes.py.
Targets: node dispatch, heartbeat processing, node discovery, fleet status aggregation.
"""

from __future__ import annotations

import json
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge_harness.webhook_server.api import nodes
from forge_harness.webhook_server.api.nodes import (
    NodeDispatchRequest,
    _age_seconds,
    _build_node_response,
    _build_remote_node_entry,
    _get_agents_from_tmux,
    _get_all_nodes,
    _get_capabilities,
    _get_system_load,
    _get_system_specs,
    _read_heartbeat_files,
    _score_node,
    router,
)

# ---------------------------------------------------------------------------
# Shared test app fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    """Return a FastAPI app with the nodes router mounted."""
    _app = FastAPI()
    _app.include_router(router)
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Return a TestClient for the nodes router."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# _get_system_specs — uncovered branches
# ---------------------------------------------------------------------------


class TestGetSystemSpecsExtended:
    """Additional branches for _get_system_specs."""

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_ram_calculated_via_sysconf(self, mock_os, mock_platform):
        """sysconf returns valid page size and page count — RAM should be > 0."""
        mock_platform.processor.return_value = "ARMv8"
        mock_platform.machine.return_value = "arm64"
        mock_platform.system.return_value = "Linux"
        mock_platform.release.return_value = "6.0"
        mock_os.cpu_count.return_value = 8
        # 4096 bytes/page * 1_048_576 pages = 4 GiB
        mock_os.sysconf.side_effect = lambda key: (
            4096 if key == "SC_PAGE_SIZE" else 1_048_576
        )

        result = _get_system_specs()

        assert result["ram_gb"] > 0
        assert result["cores"] == 8
        assert result["cpu"] == "ARMv8"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_processor_fallback_to_machine(self, mock_os, mock_platform):
        """When processor() returns empty string, machine() is used."""
        mock_platform.processor.return_value = ""
        mock_platform.machine.return_value = "aarch64"
        mock_platform.system.return_value = "Linux"
        mock_platform.release.return_value = "5.15"
        mock_os.cpu_count.return_value = 4
        mock_os.sysconf.side_effect = OSError("not supported")

        result = _get_system_specs()

        assert result["cpu"] == "aarch64"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_cpu_count_exception_defaults_to_one(self, mock_os, mock_platform):
        """When os.cpu_count raises, cores defaults to 1."""
        mock_platform.processor.return_value = "x86_64"
        mock_platform.machine.return_value = "x86_64"
        mock_platform.system.return_value = "Linux"
        mock_platform.release.return_value = "5.10"
        mock_os.cpu_count.side_effect = Exception("broken")
        mock_os.sysconf.side_effect = OSError()

        result = _get_system_specs()

        assert result["cores"] == 1

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_os_string_formatted_correctly(self, mock_os, mock_platform):
        """OS string should be '{system} {release}'."""
        mock_platform.processor.return_value = "Intel"
        mock_platform.machine.return_value = "x86_64"
        mock_platform.system.return_value = "Darwin"
        mock_platform.release.return_value = "22.0.0"
        mock_os.cpu_count.return_value = 10
        mock_os.sysconf.side_effect = OSError()

        result = _get_system_specs()

        assert result["os"] == "Darwin 22.0.0"


# ---------------------------------------------------------------------------
# _get_capabilities — individual capability checks
# ---------------------------------------------------------------------------


class TestGetCapabilitiesExtended:
    """Detailed capability detection tests."""

    @patch("shutil.which")
    def test_all_capabilities_false_when_nothing_installed(self, mock_which):
        """When shutil.which returns None for all, all capabilities are False."""
        mock_which.return_value = None

        result = _get_capabilities()

        assert result["claude"] is False
        assert result["codex"] is False
        assert result["gemini"] is False
        assert result["kimi"] is False
        assert result["opencode"] is False
        assert result["ollama"] is False
        assert result["docker"] is False
        assert result["ios_simulator"] is False
        assert result["gpu"] is False

    @patch("shutil.which")
    def test_gpu_always_false(self, mock_which):
        """GPU capability is always False regardless of which()."""
        mock_which.return_value = "/usr/bin/nvidia-smi"

        result = _get_capabilities()

        assert result["gpu"] is False

    @patch("shutil.which")
    def test_ios_simulator_detected_via_xcrun(self, mock_which):
        """ios_simulator capability depends on xcrun presence."""
        mock_which.side_effect = lambda cmd: "/usr/bin/xcrun" if cmd == "xcrun" else None

        result = _get_capabilities()

        assert result["ios_simulator"] is True
        assert result["claude"] is False

    @patch("shutil.which")
    def test_opencode_detected(self, mock_which):
        """opencode capability detected via shutil.which."""
        mock_which.side_effect = lambda cmd: "/usr/local/bin/opencode" if cmd == "opencode" else None

        result = _get_capabilities()

        assert result["opencode"] is True
        assert result["gemini"] is False

    @patch("shutil.which")
    def test_ollama_detected(self, mock_which):
        """ollama capability detected via shutil.which."""
        mock_which.side_effect = lambda cmd: "/usr/local/bin/ollama" if cmd == "ollama" else None

        result = _get_capabilities()

        assert result["ollama"] is True

    @patch("shutil.which")
    def test_kimi_detected(self, mock_which):
        """kimi capability detected via shutil.which."""
        mock_which.side_effect = lambda cmd: "/usr/local/bin/kimi" if cmd == "kimi" else None

        result = _get_capabilities()

        assert result["kimi"] is True


# ---------------------------------------------------------------------------
# _get_system_load — uncovered branches
# ---------------------------------------------------------------------------


class TestGetSystemLoadExtended:
    """Additional branches for _get_system_load."""

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_disk_usage_calculated(self, mock_os):
        """Disk usage is computed from shutil.disk_usage."""
        mock_os.getloadavg.return_value = (0.0, 0.0, 0.0)
        mock_os.cpu_count.return_value = 4

        # 40 GB used out of 100 GB total → 40%
        with patch("shutil.disk_usage", return_value=(100 * 1024**3, 40 * 1024**3, 60 * 1024**3)):
            with patch("builtins.open", side_effect=FileNotFoundError):
                with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
                    mock_sp.run.return_value = MagicMock(returncode=1, stdout="")
                    _, _, disk = _get_system_load()

        assert disk == 40.0

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_cpu_load_capped_at_100(self, mock_os):
        """CPU load never exceeds 100 even with extreme load averages."""
        mock_os.getloadavg.return_value = (999.0, 0.0, 0.0)
        mock_os.cpu_count.return_value = 1

        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
                mock_sp.run.return_value = MagicMock(returncode=1, stdout="")
                cpu, _, _ = _get_system_load()

        assert cpu == 100.0

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_proc_meminfo_ram_calculation(self, mock_os):
        """RAM usage is correctly computed from /proc/meminfo."""
        mock_os.getloadavg.return_value = (1.0, 0.5, 0.3)
        mock_os.cpu_count.return_value = 4

        # MemTotal 16 GB, MemAvailable 4 GB → 75% used
        meminfo_content = "MemTotal:       16384000 kB\nMemAvailable:    4096000 kB\n"

        with patch("builtins.open", mock_open(read_data=meminfo_content)):
            _, ram, _ = _get_system_load()

        # (16384000 - 4096000) / 16384000 * 100 ≈ 75.0
        assert ram == pytest.approx(75.0, abs=0.2)

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_proc_meminfo_zero_total_skips_calculation(self, mock_os):
        """When MemTotal is 0, RAM usage remains 0."""
        mock_os.getloadavg.return_value = (0.0, 0.0, 0.0)
        mock_os.cpu_count.return_value = 4

        # Only MemAvailable, no MemTotal → total = 0
        meminfo_content = "MemAvailable:    4096000 kB\n"

        with patch("builtins.open", mock_open(read_data=meminfo_content)):
            _, ram, _ = _get_system_load()

        assert ram == 0.0

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_macos_vmstat_ram_calculated(self, mock_os):
        """vm_stat output on macOS is parsed to produce RAM usage."""
        mock_os.getloadavg.return_value = (0.5, 0.3, 0.2)
        mock_os.cpu_count.return_value = 4

        vm_stat_output = (
            "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
            "Pages free:                       100.\n"
            "Pages inactive:                    50.\n"
            "Pages active:                     800.\n"
            "Pages wired down:                 200.\n"
        )

        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
                mock_sp.run.return_value = MagicMock(returncode=0, stdout=vm_stat_output)
                _, ram, _ = _get_system_load()

        # free_pages = 100 + 50 = 150; total = 100+50+800+200 = 1150
        # ram = (1 - 150/1150) * 100 ≈ 86.96
        assert ram > 0.0

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_macos_vmstat_failure_leaves_ram_zero(self, mock_os):
        """When vm_stat exits non-zero, RAM usage remains 0."""
        mock_os.getloadavg.return_value = (0.0, 0.0, 0.0)
        mock_os.cpu_count.return_value = 4

        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
                mock_sp.run.return_value = MagicMock(returncode=1, stdout="")
                _, ram, _ = _get_system_load()

        assert ram == 0.0

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_disk_usage_exception_yields_zero(self, mock_os):
        """Exception from shutil.disk_usage leaves disk_usage at 0."""
        mock_os.getloadavg.return_value = (0.0, 0.0, 0.0)
        mock_os.cpu_count.return_value = 4

        with patch("shutil.disk_usage", side_effect=Exception("permission denied")):
            with patch("builtins.open", side_effect=FileNotFoundError):
                with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
                    mock_sp.run.return_value = MagicMock(returncode=1, stdout="")
                    _, _, disk = _get_system_load()

        assert disk == 0.0


# ---------------------------------------------------------------------------
# _get_agents_from_tmux — health script paths
# ---------------------------------------------------------------------------


class TestGetAgentsFromTmuxExtended:
    """Additional branches for _get_agents_from_tmux."""

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_hostname_window_skipped(self, mock_os, mock_subprocess, mock_platform):
        """Windows matching the hostname are skipped."""
        mock_platform.node.return_value = "testhost"
        mock_subprocess.run.return_value = MagicMock(
            returncode=0, stdout="testhost\nbackend"
        )
        mock_os.path.isfile.return_value = False
        mock_os.environ.get.return_value = "/forge"

        result = _get_agents_from_tmux()

        names = [a["name"] for a in result]
        assert "testhost" not in names
        assert "backend" in names

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_heartbeat_window_skipped(self, mock_os, mock_subprocess, mock_platform):
        """Windows named 'heartbeat' are skipped."""
        mock_platform.node.return_value = "myhost"
        mock_subprocess.run.return_value = MagicMock(
            returncode=0, stdout="heartbeat\nkimi"
        )
        mock_os.path.isfile.return_value = False
        mock_os.environ.get.return_value = "/forge"

        result = _get_agents_from_tmux()

        names = [a["name"] for a in result]
        assert "heartbeat" not in names
        assert "kimi" in names

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_health_script_executed_and_status_mapped(
        self, mock_os, mock_subprocess, mock_platform
    ):
        """When health script exists and returns HEALTHY, status is 'active'."""
        mock_platform.node.return_value = "node1"
        health_data = json.dumps({"status": "HEALTHY", "context_pct": 30})

        # First call: tmux list-windows; second call: health script
        mock_subprocess.run.side_effect = [
            MagicMock(returncode=0, stdout="agent-a"),
            MagicMock(returncode=0, stdout=health_data),
        ]
        mock_os.path.isfile.return_value = True
        mock_os.environ.get.return_value = "/forge"

        result = _get_agents_from_tmux()

        assert len(result) == 1
        assert result[0]["status"] == "active"
        assert result[0]["context_percentage"] == 30

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_health_script_exhausted_maps_to_failed(
        self, mock_os, mock_subprocess, mock_platform
    ):
        """EXHAUSTED health status maps to 'failed' agent status."""
        mock_platform.node.return_value = "node1"
        health_data = json.dumps({"status": "EXHAUSTED", "context_pct": 95})

        mock_subprocess.run.side_effect = [
            MagicMock(returncode=0, stdout="agent-b"),
            MagicMock(returncode=0, stdout=health_data),
        ]
        mock_os.path.isfile.return_value = True
        mock_os.environ.get.return_value = "/forge"

        result = _get_agents_from_tmux()

        assert result[0]["status"] == "failed"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_health_script_idle_maps_to_idle(
        self, mock_os, mock_subprocess, mock_platform
    ):
        """IDLE health status maps to 'idle' agent status."""
        mock_platform.node.return_value = "node1"
        health_data = json.dumps({"status": "IDLE", "context_pct": 0})

        mock_subprocess.run.side_effect = [
            MagicMock(returncode=0, stdout="agent-c"),
            MagicMock(returncode=0, stdout=health_data),
        ]
        mock_os.path.isfile.return_value = True
        mock_os.environ.get.return_value = "/forge"

        result = _get_agents_from_tmux()

        assert result[0]["status"] == "idle"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_health_script_failure_defaults_active(
        self, mock_os, mock_subprocess, mock_platform
    ):
        """When health script exits non-zero, status defaults to 'active'."""
        mock_platform.node.return_value = "node1"

        mock_subprocess.run.side_effect = [
            MagicMock(returncode=0, stdout="agent-d"),
            MagicMock(returncode=1, stdout=""),
        ]
        mock_os.path.isfile.return_value = True
        mock_os.environ.get.return_value = "/forge"

        result = _get_agents_from_tmux()

        assert result[0]["status"] == "active"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_health_script_bad_json_defaults_active(
        self, mock_os, mock_subprocess, mock_platform
    ):
        """When health script output is invalid JSON, status defaults to 'active'."""
        mock_platform.node.return_value = "node1"

        mock_subprocess.run.side_effect = [
            MagicMock(returncode=0, stdout="agent-e"),
            MagicMock(returncode=0, stdout="not-valid-json"),
        ]
        mock_os.path.isfile.return_value = True
        mock_os.environ.get.return_value = "/forge"

        result = _get_agents_from_tmux()

        assert result[0]["status"] == "active"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_agent_entry_structure_complete(self, mock_os, mock_subprocess, mock_platform):
        """Agent entries have all required fields."""
        mock_platform.node.return_value = "node1"
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="myagent")
        mock_os.path.isfile.return_value = False
        mock_os.environ.get.return_value = "/forge"

        result = _get_agents_from_tmux()

        assert len(result) == 1
        agent = result[0]
        required_fields = [
            "id", "session_id", "agent_id", "name", "role",
            "status", "project", "task", "progress", "context_percentage",
        ]
        for field in required_fields:
            assert field in agent, f"Missing field: {field}"

        assert agent["id"] == "forge-myagent"
        assert agent["session_id"] == "forge:myagent"
        assert agent["agent_id"] == "forge:myagent"
        assert agent["name"] == "myagent"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    def test_tmux_exception_returns_empty(self, mock_subprocess, mock_platform):
        """When subprocess.run raises, returns empty list gracefully."""
        mock_platform.node.return_value = "node1"
        mock_subprocess.run.side_effect = Exception("tmux not found")

        result = _get_agents_from_tmux()

        assert result == []


# ---------------------------------------------------------------------------
# _build_node_response — additional coverage
# ---------------------------------------------------------------------------


class TestBuildNodeResponseExtended:
    """Additional branches for _build_node_response."""

    @patch("forge_harness.webhook_server.api.nodes._get_system_specs")
    @patch("forge_harness.webhook_server.api.nodes._get_capabilities")
    @patch("forge_harness.webhook_server.api.nodes._get_system_load")
    @patch("forge_harness.webhook_server.api.nodes._get_agents_from_tmux")
    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_online_status_when_no_failed_agents(
        self, mock_platform, mock_agents, mock_load, mock_caps, mock_specs
    ):
        """Status is 'online' when agents exist and none are failed."""
        mock_platform.node.return_value = "testhost"
        mock_agents.return_value = [{"status": "active"}, {"status": "idle"}]
        mock_load.return_value = (10.0, 20.0, 30.0)
        mock_caps.return_value = {}
        mock_specs.return_value = {}

        result = _build_node_response()

        assert result["status"] == "online"

    @patch("forge_harness.webhook_server.api.nodes._get_system_specs")
    @patch("forge_harness.webhook_server.api.nodes._get_capabilities")
    @patch("forge_harness.webhook_server.api.nodes._get_system_load")
    @patch("forge_harness.webhook_server.api.nodes._get_agents_from_tmux")
    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_custom_node_id_overrides_hostname(
        self, mock_platform, mock_agents, mock_load, mock_caps, mock_specs
    ):
        """When node_id param is given, it is used instead of hostname."""
        mock_platform.node.return_value = "physical-host"
        mock_agents.return_value = []
        mock_load.return_value = (0.0, 0.0, 0.0)
        mock_caps.return_value = {}
        mock_specs.return_value = {}

        result = _build_node_response(node_id="custom-id")

        assert result["node_id"] == "custom-id"
        # name still comes from hostname
        assert result["name"] == "physical-host"

    @patch("forge_harness.webhook_server.api.nodes._get_system_specs")
    @patch("forge_harness.webhook_server.api.nodes._get_capabilities")
    @patch("forge_harness.webhook_server.api.nodes._get_system_load")
    @patch("forge_harness.webhook_server.api.nodes._get_agents_from_tmux")
    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_hostname_dots_stripped(
        self, mock_platform, mock_agents, mock_load, mock_caps, mock_specs
    ):
        """platform.node() FQDN is trimmed to just the short hostname."""
        mock_platform.node.return_value = "myhost.local.example.com"
        mock_agents.return_value = []
        mock_load.return_value = (0.0, 0.0, 0.0)
        mock_caps.return_value = {}
        mock_specs.return_value = {}

        result = _build_node_response()

        assert result["name"] == "myhost"
        assert result["node_id"] == "myhost"

    @patch("forge_harness.webhook_server.api.nodes._get_system_specs")
    @patch("forge_harness.webhook_server.api.nodes._get_capabilities")
    @patch("forge_harness.webhook_server.api.nodes._get_system_load")
    @patch("forge_harness.webhook_server.api.nodes._get_agents_from_tmux")
    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_all_expected_fields_present(
        self, mock_platform, mock_agents, mock_load, mock_caps, mock_specs
    ):
        """Response includes all mandatory fields for the API contract."""
        mock_platform.node.return_value = "host"
        mock_agents.return_value = []
        mock_load.return_value = (15.0, 45.0, 60.0)
        mock_caps.return_value = {"claude": True}
        mock_specs.return_value = {"cpu": "x86_64", "cores": 4}

        result = _build_node_response()

        required = [
            "node_id", "name", "status", "agent_count", "cpu_load",
            "ram_usage", "last_heartbeat", "latency_ms", "agents",
            "load_average", "disk_usage", "alerts", "capabilities", "specs",
        ]
        for field in required:
            assert field in result, f"Missing: {field}"

        assert result["cpu_load"] == 15.0
        assert result["ram_usage"] == 45.0
        assert result["disk_usage"] == 60.0
        assert result["latency_ms"] == 0
        assert result["alerts"] == []


# ---------------------------------------------------------------------------
# _score_node — boundary and reason string tests
# ---------------------------------------------------------------------------


class TestScoreNodeExtended:
    """Boundary and reason-string coverage for _score_node."""

    def test_score_zero_cpu_and_ram(self):
        """Perfect score when both CPU and RAM are 0."""
        node = {"cpu_load": 0.0, "ram_usage": 0.0}
        score, reason = _score_node(node)

        assert score == 1.0
        assert "low CPU" in reason
        assert "ample memory" in reason

    def test_score_max_cpu_and_ram(self):
        """Zero score when both CPU and RAM are 100."""
        node = {"cpu_load": 100.0, "ram_usage": 100.0}
        score, reason = _score_node(node)

        assert score == 0.0
        assert "high CPU" in reason
        assert "limited memory" in reason

    def test_score_boundary_cpu_30(self):
        """CPU exactly at 30 is 'low CPU', not 'moderate'."""
        node = {"cpu_load": 29.9, "ram_usage": 0.0}
        _, reason = _score_node(node)
        assert "low CPU" in reason

    def test_score_boundary_cpu_70(self):
        """CPU exactly at 70 transitions from moderate to high."""
        node = {"cpu_load": 69.9, "ram_usage": 0.0}
        _, reason = _score_node(node)
        assert "moderate CPU" in reason

        node_high = {"cpu_load": 70.0, "ram_usage": 0.0}
        _, reason_high = _score_node(node_high)
        assert "high CPU" in reason_high

    def test_score_limited_memory_boundary(self):
        """RAM at or above 50% produces 'limited memory' reason."""
        node = {"cpu_load": 0.0, "ram_usage": 50.0}
        _, reason = _score_node(node)
        assert "limited memory" in reason

    def test_score_ample_memory_boundary(self):
        """RAM below 50% produces 'ample memory' reason."""
        node = {"cpu_load": 0.0, "ram_usage": 49.9}
        _, reason = _score_node(node)
        assert "ample memory" in reason

    def test_score_cpu_capped_above_100(self):
        """cpu_load > 100 is clamped; score should not go negative."""
        node = {"cpu_load": 200.0, "ram_usage": 0.0}
        score, _ = _score_node(node)
        assert score >= 0.0

    def test_score_combined_formula(self):
        """Score matches 0.5*cpu_score + 0.5*ram_score."""
        node = {"cpu_load": 20.0, "ram_usage": 60.0}
        score, _ = _score_node(node)

        expected = round(0.5 * (80 / 100) + 0.5 * (40 / 100), 4)
        assert score == expected


# ---------------------------------------------------------------------------
# _age_seconds — timezone-aware and edge cases
# ---------------------------------------------------------------------------


class TestAgeSecondsExtended:
    """Additional tests for _age_seconds timestamp parsing."""

    def test_age_seconds_timezone_aware_recent(self):
        """UTC-aware timestamp just moments ago has near-zero age."""
        ts = datetime.now(UTC).isoformat()
        age = _age_seconds(ts)
        assert 0.0 <= age < 5.0

    def test_age_seconds_naive_timestamp_treated_as_utc(self):
        """Naive (no tz) timestamps are treated as UTC and age is positive."""
        ts = datetime.now().isoformat()
        age = _age_seconds(ts)
        assert age >= 0.0
        assert age != float("inf")

    def test_age_seconds_very_old_timestamp(self):
        """A timestamp years in the past returns a large age."""
        ts = "2000-01-01T00:00:00+00:00"
        age = _age_seconds(ts)
        # More than 10 years in seconds
        assert age > 10 * 365 * 24 * 3600

    def test_age_seconds_empty_string_returns_inf(self):
        """Empty string is unparseable → returns inf."""
        assert _age_seconds("") == float("inf")

    def test_age_seconds_none_like_string(self):
        """None-like string returns inf."""
        assert _age_seconds("None") == float("inf")

    def test_age_seconds_future_timestamp_returns_zero(self):
        """A future timestamp returns 0, not negative."""
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        age = _age_seconds(future)
        assert age == 0.0


# ---------------------------------------------------------------------------
# _build_remote_node_entry — resource naming conventions
# ---------------------------------------------------------------------------


class TestBuildRemoteNodeEntryExtended:
    """Cover alternate naming conventions and all fields."""

    def test_alternate_resource_names_cpu_percent(self):
        """cpu_percent (alternate key) is used when cpu_load absent."""
        hb = {
            "node_id": "remote-a",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {"cpu_percent": 55.0, "memory_percent": 70.0},
        }
        result = _build_remote_node_entry(hb)

        assert result["cpu_load"] == 55.0
        assert result["ram_usage"] == 70.0

    def test_cpu_load_preferred_over_cpu_percent(self):
        """When both cpu_load and cpu_percent present, cpu_load wins."""
        hb = {
            "node_id": "remote-b",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {"cpu_load": 30.0, "cpu_percent": 80.0},
        }
        result = _build_remote_node_entry(hb)

        assert result["cpu_load"] == 30.0

    def test_disk_usage_from_resources(self):
        """disk_usage is extracted from resources dict."""
        hb = {
            "node_id": "remote-c",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {"disk_usage": 42.5},
        }
        result = _build_remote_node_entry(hb)

        assert result["disk_usage"] == 42.5

    def test_capabilities_converted_to_dict(self):
        """List capabilities in heartbeat are converted to {cap: True} dict."""
        hb = {
            "node_id": "remote-d",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {},
            "capabilities": ["claude", "docker"],
        }
        result = _build_remote_node_entry(hb)

        assert result["capabilities"] == {"claude": True, "docker": True}

    def test_current_task_included(self):
        """current_task field is propagated from heartbeat."""
        hb = {
            "node_id": "remote-e",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {},
            "current_task": "Build iOS app",
        }
        result = _build_remote_node_entry(hb)

        assert result["current_task"] == "Build iOS app"

    def test_timestamp_field_used_for_last_heartbeat(self):
        """When 'timestamp' key exists it is used for last_heartbeat."""
        ts = "2024-06-01T12:00:00+00:00"
        hb = {
            "node_id": "remote-f",
            "received_at": datetime.now(UTC).isoformat(),
            "timestamp": ts,
            "resources": {},
        }
        result = _build_remote_node_entry(hb)

        assert result["last_heartbeat"] == ts

    def test_unknown_node_id_fallback(self):
        """When node_id is absent, 'unknown' is used."""
        hb = {
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {},
        }
        # We need node_id to call the function; patch it missing
        hb_missing = dict(hb)
        # _build_remote_node_entry uses hb.get("node_id", "unknown")
        result = _build_remote_node_entry(hb_missing)

        assert result["node_id"] == "unknown"

    def test_age_seconds_included_in_result(self):
        """age_seconds field is present and >= 0."""
        hb = {
            "node_id": "remote-g",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {},
        }
        result = _build_remote_node_entry(hb)

        assert "age_seconds" in result
        assert result["age_seconds"] >= 0.0

    def test_is_fresh_false_for_stale_entry(self):
        """is_fresh is False when heartbeat is older than threshold."""
        old_ts = (datetime.now(UTC) - timedelta(seconds=200)).isoformat()
        hb = {
            "node_id": "remote-h",
            "received_at": old_ts,
            "resources": {},
        }
        result = _build_remote_node_entry(hb)

        assert result["is_fresh"] is False
        assert result["status"] == "stale"

    # ------------------------------------------------------------------
    # New CLI field names (forge nodes heartbeat v2)
    # ------------------------------------------------------------------

    def test_cpu_usage_percent_field_used_as_cpu_load(self):
        """cpu_usage_percent (current CLI field) is mapped to cpu_load."""
        hb = {
            "node_id": "new-cli-node",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {
                "cpu_usage_percent": 42.0,
                "ram_available_mb": 4096,
                "ram_total_mb": 16384,
            },
        }
        result = _build_remote_node_entry(hb)

        assert result["cpu_load"] == 42.0

    def test_ram_usage_computed_from_available_and_total(self):
        """ram_available_mb + ram_total_mb are used to compute ram_usage %."""
        hb = {
            "node_id": "new-cli-node",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {
                "ram_available_mb": 4096,   # 4 GB free
                "ram_total_mb": 16384,      # 16 GB total → 75% used
            },
        }
        result = _build_remote_node_entry(hb)

        # (1 - 4096/16384) * 100 = 75.0
        assert result["ram_usage"] == pytest.approx(75.0, abs=0.1)

    def test_ram_usage_zero_when_total_is_zero(self):
        """ram_usage is 0 when ram_total_mb is 0 (avoid ZeroDivisionError)."""
        hb = {
            "node_id": "new-cli-node",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {
                "ram_available_mb": 0,
                "ram_total_mb": 0,
            },
        }
        result = _build_remote_node_entry(hb)

        assert result["ram_usage"] == 0.0

    def test_cpu_usage_percent_preferred_over_legacy_cpu_load(self):
        """When both cpu_usage_percent and cpu_load are present, cpu_usage_percent wins."""
        hb = {
            "node_id": "mixed-node",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {
                "cpu_usage_percent": 20.0,
                "cpu_load": 80.0,  # should be ignored
            },
        }
        result = _build_remote_node_entry(hb)

        assert result["cpu_load"] == 20.0

    def test_new_cli_fields_preferred_over_legacy_ram(self):
        """When both ram_available_mb/ram_total_mb and ram_usage present, new fields win."""
        hb = {
            "node_id": "mixed-node",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {
                "ram_available_mb": 8192,
                "ram_total_mb": 16384,   # → 50% used
                "ram_usage": 90.0,       # should be ignored
            },
        }
        result = _build_remote_node_entry(hb)

        # Should compute 50% from new fields, not 90% from legacy
        assert result["ram_usage"] == pytest.approx(50.0, abs=0.1)

    def test_disk_free_gb_surfaced_in_result(self):
        """disk_free_gb from CLI heartbeat is included in the result."""
        hb = {
            "node_id": "disk-node",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {
                "disk_free_gb": 120.5,
            },
        }
        result = _build_remote_node_entry(hb)

        assert result["disk_free_gb"] == 120.5
        # disk_usage stays 0 because we don't know total
        assert result["disk_usage"] == 0.0

    def test_full_new_cli_heartbeat_payload(self):
        """Full heartbeat payload from forge nodes heartbeat is parsed correctly."""
        hb = {
            "node_id": "sati",
            "timestamp": "2026-02-23T12:00:00+00:00",
            "received_at": datetime.now(UTC).isoformat(),
            "resources": {
                "cpu_usage_percent": 35.5,
                "ram_available_mb": 12288,  # 12 GB free
                "ram_total_mb": 65536,      # 64 GB total → ~81.25% used
                "disk_free_gb": 200.0,
                "queue_depth": 3,
            },
            "current_task": "Build iOS app",
            "capabilities": ["claude", "kimi", "docker"],
            "health": {"status": "healthy", "cores": 32},
        }
        result = _build_remote_node_entry(hb)

        assert result["node_id"] == "sati"
        assert result["cpu_load"] == pytest.approx(35.5, abs=0.01)
        # (1 - 12288/65536) * 100 ≈ 81.25
        assert result["ram_usage"] == pytest.approx(81.25, abs=0.1)
        assert result["disk_free_gb"] == 200.0
        assert result["current_task"] == "Build iOS app"
        assert result["capabilities"] == {"claude": True, "kimi": True, "docker": True}
        assert result["status"] == "online"
        assert result["is_fresh"] is True


# ---------------------------------------------------------------------------
# _read_heartbeat_files — invalid JSON and missing fields
# ---------------------------------------------------------------------------


class TestReadHeartbeatFilesExtended:
    """Additional edge cases for _read_heartbeat_files."""

    def _make_mock_dir(self, files: list, is_dir: bool = True) -> MagicMock:
        """Return a MagicMock Path that behaves like a heartbeat directory."""
        mock_dir = MagicMock(spec=Path)
        mock_dir.is_dir.return_value = is_dir
        mock_dir.glob.return_value = files
        return mock_dir

    def test_invalid_json_file_is_skipped(self):
        """Files with invalid JSON are silently skipped."""
        mock_file = MagicMock()
        mock_file.read_text.return_value = "{ not valid json }"
        mock_dir = self._make_mock_dir([mock_file])

        with patch("forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR", mock_dir):
            result = _read_heartbeat_files()

        assert result == []

    def test_file_missing_node_id_skipped(self):
        """Heartbeat files without node_id are skipped."""
        mock_file = MagicMock()
        mock_file.read_text.return_value = json.dumps({"received_at": "2024-01-01"})
        mock_dir = self._make_mock_dir([mock_file])

        with patch("forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR", mock_dir):
            result = _read_heartbeat_files()

        assert result == []

    def test_file_missing_received_at_skipped(self):
        """Heartbeat files without received_at are skipped."""
        mock_file = MagicMock()
        mock_file.read_text.return_value = json.dumps({"node_id": "node-x"})
        mock_dir = self._make_mock_dir([mock_file])

        with patch("forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR", mock_dir):
            result = _read_heartbeat_files()

        assert result == []

    def test_multiple_valid_files_all_returned(self):
        """Multiple valid heartbeat files are all included."""
        files = []
        for i in range(3):
            mock_file = MagicMock()
            mock_file.read_text.return_value = json.dumps(
                {"node_id": f"node-{i}", "received_at": "2024-01-01T00:00:00"}
            )
            files.append(mock_file)

        mock_dir = self._make_mock_dir(files)
        with patch("forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR", mock_dir):
            result = _read_heartbeat_files()

        assert len(result) == 3

    def test_file_read_exception_is_skipped(self):
        """Files that raise on read_text are skipped gracefully."""
        mock_file = MagicMock()
        mock_file.read_text.side_effect = OSError("permission denied")
        mock_dir = self._make_mock_dir([mock_file])

        with patch("forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR", mock_dir):
            result = _read_heartbeat_files()

        assert result == []


# ---------------------------------------------------------------------------
# _get_all_nodes — deduplication logic
# ---------------------------------------------------------------------------


class TestGetAllNodesExtended:
    """Deduplication and freshness annotation for _get_all_nodes."""

    @patch("forge_harness.webhook_server.api.nodes._build_node_response")
    @patch("forge_harness.webhook_server.api.nodes._read_heartbeat_files")
    def test_local_node_heartbeat_file_deduplicated(self, mock_read, mock_build):
        """Heartbeat file with same node_id as local is not added twice."""
        mock_build.return_value = {
            "node_id": "local-host",
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }
        mock_read.return_value = [
            {"node_id": "local-host", "received_at": datetime.now(UTC).isoformat()}
        ]

        result = _get_all_nodes()

        node_ids = [n["node_id"] for n in result]
        assert node_ids.count("local-host") == 1

    @patch("forge_harness.webhook_server.api.nodes._build_node_response")
    @patch("forge_harness.webhook_server.api.nodes._read_heartbeat_files")
    def test_local_node_always_first(self, mock_read, mock_build):
        """Local node is always the first entry."""
        mock_build.return_value = {
            "node_id": "primary",
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }
        mock_read.return_value = [
            {
                "node_id": "secondary",
                "received_at": datetime.now(UTC).isoformat(),
                "resources": {},
            }
        ]

        result = _get_all_nodes()

        assert result[0]["node_id"] == "primary"

    @patch("forge_harness.webhook_server.api.nodes._build_node_response")
    @patch("forge_harness.webhook_server.api.nodes._read_heartbeat_files")
    def test_local_node_annotated_with_freshness(self, mock_read, mock_build):
        """Local node gets is_fresh=True and age_seconds=0."""
        mock_build.return_value = {
            "node_id": "local",
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }
        mock_read.return_value = []

        result = _get_all_nodes()

        assert result[0]["is_fresh"] is True
        assert result[0]["age_seconds"] == 0.0


# ---------------------------------------------------------------------------
# Fleet health endpoint — degraded counting
# ---------------------------------------------------------------------------


class TestFleetHealthEndpointExtended:
    """Additional scenarios for GET /api/nodes/health."""

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_degraded_node_counted_correctly(self, mock_get_nodes, client):
        """Degraded nodes increment the degraded counter, not offline."""
        mock_get_nodes.return_value = [
            {"node_id": "n1", "name": "n1", "status": "online", "agent_count": 3, "cpu_load": 10},
            {"node_id": "n2", "name": "n2", "status": "degraded", "agent_count": 1, "cpu_load": 80},
            {"node_id": "n3", "name": "n3", "status": "online", "agent_count": 2, "cpu_load": 20},
        ]

        response = client.get("/api/nodes/health")

        assert response.status_code == 200
        data = response.json()
        assert data["online"] == 2
        assert data["degraded"] == 1
        assert data["offline"] == 0

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_stale_node_counted_as_offline(self, mock_get_nodes, client):
        """'stale' status falls into offline bucket."""
        mock_get_nodes.return_value = [
            {"node_id": "n1", "name": "n1", "status": "online", "agent_count": 1, "cpu_load": 0},
            {"node_id": "n2", "name": "n2", "status": "stale", "agent_count": 0, "cpu_load": 0},
        ]

        response = client.get("/api/nodes/health")

        assert response.status_code == 200
        data = response.json()
        assert data["offline"] == 1

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_total_agents_aggregated(self, mock_get_nodes, client):
        """total_agents sums agent_count across all nodes."""
        mock_get_nodes.return_value = [
            {"node_id": "n1", "name": "n1", "status": "online", "agent_count": 4, "cpu_load": 0},
            {"node_id": "n2", "name": "n2", "status": "online", "agent_count": 6, "cpu_load": 0},
        ]

        response = client.get("/api/nodes/health")

        assert response.status_code == 200
        assert response.json()["total_agents"] == 10

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_node_summaries_in_response(self, mock_get_nodes, client):
        """Response includes per-node summaries."""
        mock_get_nodes.return_value = [
            {"node_id": "n1", "name": "n1", "status": "online", "agent_count": 2, "cpu_load": 15},
        ]

        response = client.get("/api/nodes/health")

        data = response.json()
        assert len(data["nodes"]) == 1
        summary = data["nodes"][0]
        assert summary["node_id"] == "n1"
        assert summary["cpu_load"] == 15


# ---------------------------------------------------------------------------
# Recommend node endpoint — multi-candidate ordering and path_lock
# ---------------------------------------------------------------------------


class TestRecommendNodeEndpointExtended:
    """Additional scenarios for GET /api/nodes/recommend."""

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_best_node_selected_from_multiple_candidates(self, mock_get_nodes, client):
        """The node with lowest load is recommended when multiple candidates exist."""
        mock_get_nodes.return_value = [
            {"node_id": "heavy", "name": "heavy", "cpu_load": 80.0, "ram_usage": 70.0, "is_fresh": True},
            {"node_id": "light", "name": "light", "cpu_load": 10.0, "ram_usage": 20.0, "is_fresh": True},
            {"node_id": "medium", "name": "medium", "cpu_load": 45.0, "ram_usage": 40.0, "is_fresh": True},
        ]

        response = client.get("/api/nodes/recommend")

        assert response.status_code == 200
        data = response.json()
        assert data["recommended_node_id"] == "light"

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_path_lock_echoed_in_response(self, mock_get_nodes, client):
        """path_lock query param is returned in the response."""
        mock_get_nodes.return_value = [
            {"node_id": "n1", "name": "n1", "cpu_load": 5.0, "ram_usage": 10.0, "is_fresh": True},
        ]

        response = client.get("/api/nodes/recommend?path_lock=harness/")

        assert response.status_code == 200
        data = response.json()
        assert data["path_lock"] == "harness/"

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_candidates_list_in_response(self, mock_get_nodes, client):
        """candidates list is included and contains all scored nodes."""
        mock_get_nodes.return_value = [
            {"node_id": "a", "name": "a", "cpu_load": 10.0, "ram_usage": 20.0, "is_fresh": True},
            {"node_id": "b", "name": "b", "cpu_load": 50.0, "ram_usage": 60.0, "is_fresh": True},
        ]

        response = client.get("/api/nodes/recommend")

        data = response.json()
        assert "candidates" in data
        assert len(data["candidates"]) == 2

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_stale_nodes_excluded_from_candidates(self, mock_get_nodes, client):
        """Stale nodes (is_fresh=False) are excluded from recommendation candidates."""
        mock_get_nodes.return_value = [
            {"node_id": "fresh", "name": "fresh", "cpu_load": 5.0, "ram_usage": 10.0, "is_fresh": True},
            {"node_id": "stale", "name": "stale", "cpu_load": 1.0, "ram_usage": 1.0, "is_fresh": False},
        ]

        response = client.get("/api/nodes/recommend")

        data = response.json()
        # stale has lower load but must not be selected
        assert data["recommended_node_id"] == "fresh"

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_recommend_uses_local_fallback_when_all_stale(self, mock_get_nodes, client):
        """When all remote nodes are stale, local node (always first) is fallback."""
        mock_get_nodes.return_value = [
            # local node always has is_fresh=True via _get_all_nodes
            {"node_id": "local", "name": "local", "cpu_load": 30.0, "ram_usage": 40.0, "is_fresh": True},
            {"node_id": "remote1", "name": "remote1", "cpu_load": 1.0, "ram_usage": 1.0, "is_fresh": False},
        ]

        response = client.get("/api/nodes/recommend")

        data = response.json()
        assert data["recommended_node_id"] == "local"

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_score_included_in_response(self, mock_get_nodes, client):
        """The 'score' field is included in the recommendation response."""
        mock_get_nodes.return_value = [
            {"node_id": "n1", "name": "n1", "cpu_load": 20.0, "ram_usage": 30.0, "is_fresh": True},
        ]

        response = client.get("/api/nodes/recommend")

        data = response.json()
        assert "score" in data
        assert isinstance(data["score"], float)

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_task_type_default_general(self, mock_get_nodes, client):
        """Default task_type is 'general' when not specified."""
        mock_get_nodes.return_value = [
            {"node_id": "n1", "name": "n1", "cpu_load": 0.0, "ram_usage": 0.0, "is_fresh": True},
        ]

        response = client.get("/api/nodes/recommend")

        data = response.json()
        assert data["task_type"] == "general"


# ---------------------------------------------------------------------------
# Get node endpoint — additional cases
# ---------------------------------------------------------------------------


class TestGetNodeEndpointExtended:
    """Additional scenarios for GET /api/nodes/{node_id}."""

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes._build_node_response")
    def test_node_id_passed_to_build(self, mock_build, mock_platform, client):
        """The node_id from the path is passed through to _build_node_response."""
        mock_platform.node.return_value = "myhost"
        mock_build.return_value = {"node_id": "myhost", "status": "online"}

        response = client.get("/api/nodes/myhost")

        assert response.status_code == 200
        mock_build.assert_called_once_with("myhost")

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_fqdn_node_hostname_comparison_uses_short(self, mock_platform, client):
        """Comparison uses the short hostname (split on '.')."""
        mock_platform.node.return_value = "server.prod.example.com"

        # request with short name matches the split result
        with patch("forge_harness.webhook_server.api.nodes._build_node_response") as mock_build:
            mock_build.return_value = {"node_id": "server"}
            response = client.get("/api/nodes/server")

        assert response.status_code == 200

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_wrong_node_id_returns_404_with_detail(self, mock_platform, client):
        """404 response includes details about which node was not found."""
        mock_platform.node.return_value = "correcthost"

        response = client.get("/api/nodes/wronghost")

        assert response.status_code == 404
        assert "wronghost" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Dispatch endpoint — additional scenarios
# ---------------------------------------------------------------------------


class TestDispatchToNodeEndpointExtended:
    """Additional scenarios for POST /api/nodes/{node_id}/dispatch."""

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_id_is_8_char_hex(self, mock_os, client):
        """dispatch_id is present and has expected 8-char format."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = True

        with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(returncode=0, stderr="")
            response = client.post(
                "/api/nodes/mynode/dispatch",
                json={"agent_type": "claude", "task": "run tests"},
            )

        data = response.json()
        assert "dispatch_id" in data
        assert len(data["dispatch_id"]) == 8

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_target_formatted_correctly(self, mock_os, client):
        """agent_id in response is 'forge:{agent_type}'."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = True

        with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(returncode=0, stderr="")
            response = client.post(
                "/api/nodes/anynode/dispatch",
                json={"agent_type": "gemini", "task": "write docs"},
            )

        data = response.json()
        assert data["agent_id"] == "forge:gemini"

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_node_id_reflected_in_response(self, mock_os, client):
        """node_id in response matches the path parameter."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = True

        with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(returncode=0, stderr="")
            response = client.post(
                "/api/nodes/special-node/dispatch",
                json={"agent_type": "codex", "task": "refactor"},
            )

        data = response.json()
        assert data["node_id"] == "special-node"

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_exception_returns_500(self, mock_os, client):
        """When subprocess.run raises an exception, response is 500."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = True

        with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
            mock_sp.run.side_effect = Exception("subprocess crashed")
            response = client.post(
                "/api/nodes/anynode/dispatch",
                json={"agent_type": "claude", "task": "crash test"},
            )

        assert response.status_code == 500
        data = response.json()
        assert data["success"] is False
        assert "error" in data["message"].lower() or "Dispatch error" in data["message"]

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_failed_status_string(self, mock_os, client):
        """status field is 'failed' when dispatch fails."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = False

        response = client.post(
            "/api/nodes/anynode/dispatch",
            json={"agent_type": "claude", "task": "test"},
        )

        data = response.json()
        assert data["status"] == "failed"

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_success_status_string(self, mock_os, client):
        """status field is 'dispatched' on success."""
        mock_os.environ.get.return_value = "/forge"
        mock_os.path.isfile.return_value = True

        with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(returncode=0, stderr="")
            response = client.post(
                "/api/nodes/anynode/dispatch",
                json={"agent_type": "claude", "task": "test"},
            )

        assert response.json()["status"] == "dispatched"

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_stderr_included_in_failure_message(self, mock_os, client):
        """stderr output is included in the failure message."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = True

        with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(returncode=1, stderr="Connection refused")
            response = client.post(
                "/api/nodes/node1/dispatch",
                json={"agent_type": "kimi", "task": "do work"},
            )

        data = response.json()
        assert "Connection refused" in data["message"]


# ---------------------------------------------------------------------------
# Sync endpoint — structure validation
# ---------------------------------------------------------------------------


class TestSyncNodesEndpointExtended:
    """Structural validation for POST /api/nodes/sync."""

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_sync_details_contain_hostname(self, mock_platform, client):
        """details list contains entry for the local hostname."""
        mock_platform.node.return_value = "synchost"

        response = client.post("/api/nodes/sync")

        data = response.json()
        assert len(data["details"]) == 1
        assert data["details"][0]["node_id"] == "synchost"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_sync_failed_nodes_is_zero(self, mock_platform, client):
        """failed_nodes is always 0 for single-node sync."""
        mock_platform.node.return_value = "host"

        response = client.post("/api/nodes/sync")

        assert response.json()["failed_nodes"] == 0

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_sync_timestamp_is_iso_format(self, mock_platform, client):
        """timestamp field in response is a valid ISO 8601 string."""
        mock_platform.node.return_value = "host"

        response = client.post("/api/nodes/sync")

        ts = response.json()["timestamp"]
        # Should not raise
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_sync_details_entry_success_true(self, mock_platform, client):
        """details entry has success=True."""
        mock_platform.node.return_value = "host"

        response = client.post("/api/nodes/sync")

        detail = response.json()["details"][0]
        assert detail["success"] is True

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_sync_id_is_8_chars(self, mock_platform, client):
        """sync_id is an 8-char identifier."""
        mock_platform.node.return_value = "host"

        response = client.post("/api/nodes/sync")

        assert len(response.json()["sync_id"]) == 8


# ---------------------------------------------------------------------------
# List nodes endpoint — response structure
# ---------------------------------------------------------------------------


class TestListNodesEndpointExtended:
    """Structural validation for GET /api/nodes."""

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_list_nodes_empty(self, mock_get_nodes, client):
        """Returns empty nodes list when no nodes are known."""
        mock_get_nodes.return_value = []

        response = client.get("/api/nodes")

        assert response.status_code == 200
        assert response.json() == {"nodes": []}

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_list_nodes_multiple(self, mock_get_nodes, client):
        """All nodes returned from _get_all_nodes appear in the response."""
        mock_get_nodes.return_value = [
            {"node_id": "a", "status": "online"},
            {"node_id": "b", "status": "stale"},
            {"node_id": "c", "status": "online"},
        ]

        response = client.get("/api/nodes")

        data = response.json()
        assert len(data["nodes"]) == 3

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_list_nodes_response_is_json(self, mock_get_nodes, client):
        """Content-Type is application/json."""
        mock_get_nodes.return_value = []

        response = client.get("/api/nodes")

        assert "application/json" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# NodeDispatchRequest — validation edge cases
# ---------------------------------------------------------------------------


class TestNodeDispatchRequestExtended:
    """Additional validation for NodeDispatchRequest model."""

    def test_low_priority_accepted(self):
        """'low' priority is a valid string value."""
        req = NodeDispatchRequest(agent_type="claude", task="task", priority="low")
        assert req.priority == "low"

    def test_high_priority_accepted(self):
        """'high' priority is a valid string value."""
        req = NodeDispatchRequest(agent_type="codex", task="task", priority="high")
        assert req.priority == "high"

    def test_timeout_minutes_zero_accepted(self):
        """timeout_minutes of 0 is accepted."""
        req = NodeDispatchRequest(agent_type="claude", task="task", timeout_minutes=0)
        assert req.timeout_minutes == 0

    def test_all_fields_set(self):
        """All optional fields can be set simultaneously."""
        req = NodeDispatchRequest(
            agent_type="gemini",
            task="full test",
            priority="high",
            project="my-project",
            domain="voice-coach",
            timeout_minutes=60,
        )
        assert req.agent_type == "gemini"
        assert req.task == "full test"
        assert req.priority == "high"
        assert req.project == "my-project"
        assert req.domain == "voice-coach"
        assert req.timeout_minutes == 60
