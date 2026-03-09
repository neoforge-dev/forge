"""Tests for forge_harness.webhook_server.api.nodes module.

Tests the Node Management API Endpoints using FastAPI TestClient.
"""

from __future__ import annotations

import json
import platform
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Import the router directly
from forge_harness.webhook_server.api import nodes


@pytest.fixture
def mock_platform_node(self):
    """Mock platform.node() to return a consistent hostname."""
    with patch("forge_harness.webhook_server.api.nodes.platform") as mock:
        mock.node.return_value = "test-hostname"
        mock.processor.return_value = "x86_64"
        mock.machine.return_value = "x86_64"
        mock.system.return_value = "Linux"
        mock.release.return_value = "5.10.0"
        yield mock


@pytest.fixture
def mock_os_dependencies():
    """Mock OS-related dependencies."""
    with patch("forge_harness.webhook_server.api.nodes.os") as mock_os:
        mock_os.cpu_count.return_value = 4
        mock_os.sysconf.side_effect = AttributeError("Not supported")
        mock_os.getloadavg.return_value = (1.0, 0.5, 0.3)
        mock_os.environ.get.return_value = "/fake/forge"
        yield mock_os


@pytest.fixture
def mock_subprocess():
    """Mock subprocess calls."""
    with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock:
        mock.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock


@pytest.fixture
def mock_tmux_agents():
    """Mock tmux agent list."""
    with patch("forge_harness.webhook_server.api.nodes._get_agents_from_tmux") as mock:
        mock.return_value = [
            {
                "id": "forge-tech",
                "session_id": "forge:tech",
                "agent_id": "forge:tech",
                "name": "tech",
                "role": "developer",
                "status": "active",
                "project": "",
                "task": "",
                "progress": 0,
                "context_percentage": 45,
            }
        ]
        yield mock


@pytest.fixture
def mock_heartbeat_files():
    """Mock heartbeat file reading."""
    with patch("forge_harness.webhook_server.api.nodes._read_heartbeat_files") as mock:
        mock.return_value = []
        yield mock


@pytest.fixture
def mock_capabilities():
    """Mock capability detection."""
    with patch("forge_harness.webhook_server.api.nodes._get_capabilities") as mock:
        mock.return_value = {
            "claude": True,
            "codex": True,
            "gemini": False,
            "kimi": False,
            "opencode": True,
            "ollama": False,
            "docker": True,
            "ios_simulator": False,
            "gpu": False,
        }
        yield mock


@pytest.fixture
def mock_system_load():
    """Mock system load values."""
    with patch("forge_harness.webhook_server.api.nodes._get_system_load") as mock:
        mock.return_value = (25.0, 50.0, 30.0)
        yield mock


@pytest.fixture
def mock_system_specs():
    """Mock system specs."""
    with patch("forge_harness.webhook_server.api.nodes._get_system_specs") as mock:
        mock.return_value = {
            "cpu": "x86_64",
            "ram_gb": 16.0,
            "os": "Linux 5.10.0",
            "cores": 4,
        }
        yield mock


class TestNodeDispatchRequest:
    """Tests for NodeDispatchRequest model."""

    def test_default_values(self):
        """Test default values."""
        from forge_harness.webhook_server.api.nodes import NodeDispatchRequest

        req = NodeDispatchRequest(agent_type="claude", task="test task")
        assert req.agent_type == "claude"
        assert req.task == "test task"
        assert req.priority == "normal"
        assert req.project is None
        assert req.domain is None
        assert req.timeout_minutes is None

    def test_custom_values(self):
        """Test custom values."""
        from forge_harness.webhook_server.api.nodes import NodeDispatchRequest

        req = NodeDispatchRequest(
            agent_type="codex",
            task="test",
            priority="high",
            project="myproject",
            domain="testdomain",
            timeout_minutes=30,
        )
        assert req.priority == "high"
        assert req.project == "myproject"
        assert req.domain == "testdomain"
        assert req.timeout_minutes == 30


class TestGetSystemSpecs:
    """Tests for _get_system_specs function."""

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_get_system_specs(self, mock_os, mock_platform):
        """Test system specs retrieval."""
        mock_platform.processor.return_value = "Intel"
        mock_platform.machine.return_value = "x86_64"
        mock_platform.system.return_value = "Linux"
        mock_platform.release.return_value = "5.10"
        mock_os.cpu_count.return_value = 8
        mock_os.sysconf.side_effect = AttributeError("Not supported")

        result = nodes._get_system_specs()

        assert "cpu" in result
        assert "ram_gb" in result
        assert "os" in result
        assert "cores" in result
        assert result["cores"] == 8


class TestGetCapabilities:
    """Tests for _get_capabilities function."""

    @patch("shutil.which")
    def test_get_capabilities(self, mock_which):
        """Test capability detection."""
        mock_which.side_effect = lambda cmd: cmd in ["claude", "docker"]

        result = nodes._get_capabilities()

        assert "claude" in result
        assert "docker" in result
        assert isinstance(result["claude"], bool)


class TestGetSystemLoad:
    """Tests for _get_system_load function."""

    @patch("builtins.open")
    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_get_system_load_linux(self, mock_os, mock_open):
        """Test system load on Linux."""
        mock_os.getloadavg.return_value = (2.0, 1.5, 1.0)
        mock_os.cpu_count.return_value = 4

        mock_file_content = """MemTotal:       16000000 kB
MemAvailable:    8000000 kB
"""
        mock_open.return_value.__enter__.return_value.read.return_value = mock_file_content

        result = nodes._get_system_load()

        assert len(result) == 3
        cpu, ram, disk = result
        assert cpu >= 0

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_get_system_load_fallback(self, mock_os):
        """Test system load fallback on error."""
        mock_os.getloadavg.side_effect = OSError("Not supported")
        mock_os.cpu_count.return_value = 4
        mock_os.sysconf.side_effect = AttributeError("Not supported")

        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
                mock_sp.run.return_value = MagicMock(
                    returncode=0,
                    stdout="Pages free: 100\nPages inactive: 50\n",
                )
                result = nodes._get_system_load()
                assert len(result) == 3


class TestGetAgentsFromTmux:
    """Tests for _get_agents_from_tmux function."""

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    def test_get_agents_from_tmux_success(self, mock_subprocess, mock_platform):
        """Test successful agent retrieval from tmux."""
        mock_platform.node.return_value = "testhost"
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="tech\nbackend\ncontent",
        )

        with patch("os.path.isfile", return_value=False):
            result = nodes._get_agents_from_tmux()

        assert len(result) == 3
        assert result[0]["name"] == "tech"

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes.subprocess")
    def test_get_agents_from_tmux_failure(self, mock_subprocess, mock_platform):
        """Test tmux failure returns empty list."""
        mock_platform.node.return_value = "testhost"
        mock_subprocess.run.return_value = MagicMock(returncode=1, stdout="")

        result = nodes._get_agents_from_tmux()

        assert result == []


class TestBuildNodeResponse:
    """Tests for _build_node_response function."""

    @patch("forge_harness.webhook_server.api.nodes._get_system_specs")
    @patch("forge_harness.webhook_server.api.nodes._get_capabilities")
    @patch("forge_harness.webhook_server.api.nodes._get_system_load")
    @patch("forge_harness.webhook_server.api.nodes._get_agents_from_tmux")
    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_build_node_response(
        self,
        mock_platform,
        mock_agents,
        mock_load,
        mock_caps,
        mock_specs,
    ):
        """Test building node response."""
        mock_platform.node.return_value = "testhost"
        mock_agents.return_value = []
        mock_load.return_value = (10.0, 20.0, 30.0)
        mock_caps.return_value = {"claude": True}
        mock_specs.return_value = {"cpu": "x86_64"}

        result = nodes._build_node_response()

        assert "node_id" in result
        assert "status" in result
        assert "cpu_load" in result
        assert "ram_usage" in result
        assert "agents" in result
        assert "capabilities" in result

    @patch("forge_harness.webhook_server.api.nodes._get_system_specs")
    @patch("forge_harness.webhook_server.api.nodes._get_capabilities")
    @patch("forge_harness.webhook_server.api.nodes._get_system_load")
    @patch("forge_harness.webhook_server.api.nodes._get_agents_from_tmux")
    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_build_node_response_offline(
        self,
        mock_platform,
        mock_agents,
        mock_load,
        mock_caps,
        mock_specs,
    ):
        """Test node response when no agents."""
        mock_platform.node.return_value = "testhost"
        mock_agents.return_value = []
        mock_load.return_value = (10.0, 20.0, 30.0)
        mock_caps.return_value = {}
        mock_specs.return_value = {}

        result = nodes._build_node_response()

        assert result["status"] == "offline"

    @patch("forge_harness.webhook_server.api.nodes._get_system_specs")
    @patch("forge_harness.webhook_server.api.nodes._get_capabilities")
    @patch("forge_harness.webhook_server.api.nodes._get_system_load")
    @patch("forge_harness.webhook_server.api.nodes._get_agents_from_tmux")
    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_build_node_response_degraded(
        self,
        mock_platform,
        mock_agents,
        mock_load,
        mock_caps,
        mock_specs,
    ):
        """Test node response when agents have failed status."""
        mock_platform.node.return_value = "testhost"
        mock_agents.return_value = [{"status": "failed"}]
        mock_load.return_value = (10.0, 20.0, 30.0)
        mock_caps.return_value = {}
        mock_specs.return_value = {}

        result = nodes._build_node_response()

        assert result["status"] == "degraded"


class TestScoreNode:
    """Tests for _score_node function."""

    def test_score_node_low_load(self):
        """Test scoring with low CPU and RAM."""
        node = {"cpu_load": 20.0, "ram_usage": 30.0}
        score, reason = nodes._score_node(node)

        assert score > 0.7
        assert "low CPU" in reason

    def test_score_node_moderate_load(self):
        """Test scoring with moderate load."""
        node = {"cpu_load": 50.0, "ram_usage": 40.0}
        score, reason = nodes._score_node(node)

        assert 0.3 < score < 0.7
        assert "moderate CPU" in reason

    def test_score_node_high_load(self):
        """Test scoring with high load."""
        node = {"cpu_load": 90.0, "ram_usage": 80.0}
        score, reason = nodes._score_node(node)

        assert score < 0.3
        assert "high CPU" in reason

    def test_score_node_missing_values(self):
        """Test scoring with missing values."""
        node = {}
        score, reason = nodes._score_node(node)

        # (100-0)/100 * 0.5 + (100-0)/100 * 0.5 = 1.0
        assert score == 1.0


class TestAgeSeconds:
    """Tests for _age_seconds function."""

    def test_age_seconds_valid(self):
        """Test age calculation with valid timestamp."""
        old_time = datetime.now()
        result = nodes._age_seconds(old_time.isoformat())
        assert result >= 0

    def test_age_seconds_invalid(self):
        """Test age calculation with invalid timestamp."""
        result = nodes._age_seconds("invalid")
        assert result == float("inf")


class TestBuildRemoteNodeEntry:
    """Tests for _build_remote_node_entry function."""

    def test_build_remote_node_entry_fresh(self):
        """Test building fresh remote node."""
        hb = {
            "node_id": "remote-1",
            "received_at": datetime.now().isoformat(),
            "resources": {"cpu_load": 25.0, "ram_usage": 40.0},
            "capabilities": ["claude", "codex"],
        }
        result = nodes._build_remote_node_entry(hb)

        assert result["node_id"] == "remote-1"
        assert result["status"] == "online"
        assert result["is_fresh"] is True

    def test_build_remote_node_entry_stale(self):
        """Test building stale remote node."""
        old_time = datetime(2020, 1, 1)
        hb = {
            "node_id": "remote-1",
            "received_at": old_time.isoformat(),
            "resources": {"cpu_load": 25.0},
        }
        result = nodes._build_remote_node_entry(hb)

        assert result["status"] == "stale"
        assert result["is_fresh"] is False


class TestGetAllNodes:
    """Tests for _get_all_nodes function."""

    @patch("forge_harness.webhook_server.api.nodes._build_node_response")
    @patch("forge_harness.webhook_server.api.nodes._read_heartbeat_files")
    def test_get_all_nodes_local_only(self, mock_read, mock_build):
        """Test getting nodes when only local exists."""
        mock_read.return_value = []
        mock_build.return_value = {
            "node_id": "local-node",
            "last_heartbeat": "2024-01-01T00:00:00",
        }

        result = nodes._get_all_nodes()

        assert len(result) == 1
        assert result[0]["node_id"] == "local-node"

    @patch("forge_harness.webhook_server.api.nodes._build_node_response")
    @patch("forge_harness.webhook_server.api.nodes._build_remote_node_entry")
    @patch("forge_harness.webhook_server.api.nodes._read_heartbeat_files")
    def test_get_all_nodes_with_remote(self, mock_read, mock_build_remote, mock_build):
        """Test getting nodes with remote nodes."""
        mock_read.return_value = [{"node_id": "remote-1"}]
        mock_build.return_value = {
            "node_id": "local-node",
            "last_heartbeat": "2024-01-01T00:00:00",
        }
        mock_build_remote.return_value = {"node_id": "remote-1", "is_fresh": True}

        result = nodes._get_all_nodes()

        assert len(result) == 2


class TestReadHeartbeatFiles:
    """Tests for _read_heartbeat_files function."""

    @patch("forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR")
    def test_read_heartbeat_files_no_dir(self, mock_dir):
        """Test when heartbeat directory doesn't exist."""
        mock_dir.is_dir.return_value = False

        result = nodes._read_heartbeat_files()

        assert result == []

    @patch("pathlib.Path.glob")
    @patch("forge_harness.webhook_server.api.nodes._HEARTBEAT_NODES_DIR")
    def test_read_heartbeat_files_with_data(self, mock_dir, mock_glob):
        """Test reading heartbeat files."""
        mock_file = MagicMock()
        mock_file.read_text.return_value = '{"node_id": "test", "received_at": "2024-01-01"}'
        mock_dir.is_dir.return_value = True
        mock_dir.glob.return_value = [mock_file]

        result = nodes._read_heartbeat_files()

        assert len(result) == 1
        assert result[0]["node_id"] == "test"


# =============================================================================
# API Endpoint Tests
# =============================================================================


class TestNodeHeartbeatEndpoint:
    """Tests for POST /api/nodes/heartbeat endpoint."""

    def test_publish_node_heartbeat_success(self, tmp_path: Path, monkeypatch):
        """Telemetry heartbeat is persisted and normalized."""
        monkeypatch.setattr(nodes, "_HEARTBEAT_NODES_DIR", tmp_path)

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        payload = {
            "node_id": "Nova_One",
            "resources": {"cpu_usage_percent": 12.5, "ram_total_mb": 16000, "ram_available_mb": 8000},
            "capabilities": {"claude": True, "codex": True},
        }

        with TestClient(app) as client:
            response = client.post("/api/nodes/heartbeat", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["node_id"] == "nova-one"
        assert "received_at" in body

        written = tmp_path / "nova-one.json"
        assert written.exists()
        persisted = json.loads(written.read_text(encoding="utf-8"))
        assert persisted["node_id"] == "nova-one"
        assert "received_at" in persisted

    def test_publish_node_heartbeat_requires_node_id(self):
        """Missing node_id returns 422 validation error."""
        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.post("/api/nodes/heartbeat", json={"resources": {}})

        assert response.status_code == 422
        assert "node_id" in response.text


class TestListNodesEndpoint:
    """Tests for GET /api/nodes endpoint."""

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_list_nodes(self, mock_get_nodes):
        """Test listing nodes."""
        mock_get_nodes.return_value = [{"node_id": "node1", "name": "node1", "status": "online"}]

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get("/api/nodes")

        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert len(data["nodes"]) == 1


class TestFleetHealthEndpoint:
    """Tests for GET /api/nodes/health endpoint."""

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_fleet_health(self, mock_get_nodes):
        """Test fleet health endpoint."""
        mock_get_nodes.return_value = [
            {
                "node_id": "node1",
                "name": "node1",
                "status": "online",
                "agent_count": 2,
                "cpu_load": 25.0,
            },
            {
                "node_id": "node2",
                "name": "node2",
                "status": "offline",
                "agent_count": 0,
                "cpu_load": 0,
            },
        ]

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get("/api/nodes/health")

        assert response.status_code == 200
        data = response.json()
        assert data["total_nodes"] == 2
        assert data["online"] == 1
        assert data["offline"] == 1
        assert data["health_percentage"] == 50.0

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    def test_fleet_health_empty(self, mock_get_nodes):
        """Test fleet health with no nodes."""
        mock_get_nodes.return_value = []

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get("/api/nodes/health")

        assert response.status_code == 200
        data = response.json()
        assert data["total_nodes"] == 0
        assert data["health_percentage"] == 0.0


class TestRecommendNodeEndpoint:
    """Tests for GET /api/nodes/recommend endpoint."""

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    @patch("forge_harness.webhook_server.api.nodes._score_node")
    def test_recommend_node(self, mock_score, mock_get_nodes):
        """Test node recommendation."""
        mock_get_nodes.return_value = [
            {
                "node_id": "node1",
                "name": "node1",
                "cpu_load": 20.0,
                "ram_usage": 30.0,
                "is_fresh": True,
            }
        ]
        mock_score.return_value = (0.8, "low CPU")

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get("/api/nodes/recommend?task_type=python")

        assert response.status_code == 200
        data = response.json()
        assert "recommended_node_id" in data
        assert data["task_type"] == "python"

    @patch("forge_harness.webhook_server.api.nodes._get_all_nodes")
    @patch("forge_harness.webhook_server.api.nodes._build_node_response")
    def test_recommend_node_no_candidates(self, mock_build, mock_get_nodes):
        """Test recommendation with no candidates."""
        mock_get_nodes.return_value = []
        mock_build.return_value = {"node_id": "local", "name": "local", "cpu_load": 0}

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get("/api/nodes/recommend")

        assert response.status_code == 200
        data = response.json()
        assert "falling back" in data["reason"].lower()


class TestGetNodeEndpoint:
    """Tests for GET /api/nodes/{node_id} endpoint."""

    @patch("forge_harness.webhook_server.api.nodes.platform")
    @patch("forge_harness.webhook_server.api.nodes._build_node_response")
    def test_get_node_found(self, mock_build, mock_platform):
        """Test getting existing node."""
        mock_platform.node.return_value = "testhost"
        mock_build.return_value = {"node_id": "testhost"}

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get("/api/nodes/testhost")

        assert response.status_code == 200

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_get_node_not_found(self, mock_platform):
        """Test getting non-existent node."""
        mock_platform.node.return_value = "testhost"

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get("/api/nodes/other-host")

        assert response.status_code == 404


class TestDispatchToNodeEndpoint:
    """Tests for POST /api/nodes/{node_id}/dispatch endpoint."""

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_success(self, mock_os):
        """Test successful dispatch."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = True

        with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(returncode=0, stderr="")

            from fastapi import FastAPI

            from forge_harness.webhook_server.api.nodes import router

            app = FastAPI()
            app.include_router(router)

            with TestClient(app) as client:
                response = client.post(
                    "/api/nodes/testhost/dispatch",
                    json={"agent_type": "claude", "task": "test task"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_script_not_found(self, mock_os):
        """Test dispatch when script not found."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = False

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.post(
                "/api/nodes/testhost/dispatch",
                json={"agent_type": "claude", "task": "test"},
            )

        assert response.status_code == 500
        data = response.json()
        assert "not found" in data["message"].lower()

    @patch("forge_harness.webhook_server.api.nodes.os")
    def test_dispatch_failure(self, mock_os):
        """Test dispatch failure."""
        mock_os.environ.get.return_value = "/fake"
        mock_os.path.isfile.return_value = True

        with patch("forge_harness.webhook_server.api.nodes.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(returncode=1, stderr="Error occurred")

            from fastapi import FastAPI

            from forge_harness.webhook_server.api.nodes import router

            app = FastAPI()
            app.include_router(router)

            with TestClient(app) as client:
                response = client.post(
                    "/api/nodes/testhost/dispatch",
                    json={"agent_type": "claude", "task": "test"},
                )

            assert response.status_code == 500
            data = response.json()
            assert data["success"] is False


class TestSyncNodesEndpoint:
    """Tests for POST /api/nodes/sync endpoint."""

    @patch("forge_harness.webhook_server.api.nodes.platform")
    def test_sync_nodes(self, mock_platform):
        """Test sync nodes endpoint."""
        mock_platform.node.return_value = "testhost"

        from fastapi import FastAPI

        from forge_harness.webhook_server.api.nodes import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.post("/api/nodes/sync")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["synced_nodes"] == 1
        assert "sync_id" in data


class TestRouteUniqueness:
    """Regression test for route shadowing in full webhook app."""

    def test_nodes_heartbeat_route_defined_once_in_full_app(self):
        """`POST /api/nodes/heartbeat` must be served by a single route."""
        from forge_harness.webhook_server_main import create_app

        app = create_app()
        matches = [
            route
            for route in app.routes
            if getattr(route, "path", None) == "/api/nodes/heartbeat"
            and "POST" in getattr(route, "methods", set())
        ]

        assert len(matches) == 1
