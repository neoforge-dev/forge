"""
Tests for CP-3002 — enriched forge status --json output.

Verifies that ``forge status --json`` includes the new top-level fields:
  - nodes       list of node detail dicts
  - active_leases  list of active-lease dicts
  - fleet          fleet summary sub-object
  - approvals_pending  integer count of pending approvals

Also exercises the new CommandCenterClient methods (list_nodes,
list_active_leases) and the new status_adapter function (fetch_active_leases).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def forge_root(tmp_path):
    """Minimal forge root with sentinel file."""
    (tmp_path / ".forge-root").write_text("")
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_NODE = {
    "node_id": "prya",
    "name": "prya",
    "status": "online",
    "agent_count": 2,
    "cpu_load": 45.0,
    "ram_usage": 60.0,
    "last_heartbeat": "2026-02-23T10:00:00+00:00",
}

_SAMPLE_TASK_WITH_LEASE = {
    "id": "task-abc",
    "subject": "Implement feature X",
    "description": "Backend work",
    "priority": "high",
    "status": "in_progress",
    "lease": {
        "owner_agent": "agent-forge-01",
        "owner_node": "prya",
        "path_lock": "backend/api",
        "lease_expires_at": "2026-02-23T11:00:00+00:00",
    },
}

_SAMPLE_TASK_NO_LEASE = {
    "id": "task-xyz",
    "subject": "Review PR #99",
    "description": "Code review",
    "priority": "medium",
    "status": "pending",
    "lease": None,
}


def _invoke_status_json(runner: CliRunner, forge_root, extra_env: dict | None = None):
    """Invoke ``forge status --json`` inside a fake forge root."""
    from forge_harness.cli_v2 import cli

    env = {"FORGE_ROOT": str(forge_root)}
    if extra_env:
        env.update(extra_env)

    with runner.isolated_filesystem(temp_dir=forge_root):
        result = runner.invoke(cli, ["status", "--json"], env=env, catch_exceptions=False)
    return result


# ---------------------------------------------------------------------------
# Unit tests — _node_to_json_dict helper
# ---------------------------------------------------------------------------


class TestNodeToJsonDict:
    """Unit tests for the _node_to_json_dict helper in status.py."""

    def test_returns_required_fields(self):
        """Must include all six CP-3002 fields."""
        from forge_harness.cli_v2.status import _node_to_json_dict

        from forge_harness.shared_contracts import normalize_node

        node = normalize_node(_SAMPLE_NODE)
        result = _node_to_json_dict(node)

        assert set(result.keys()) >= {
            "node_id",
            "status",
            "agent_count",
            "cpu_load",
            "ram_usage",
            "last_heartbeat",
        }

    def test_node_id_value(self):
        """node_id must match the source dict."""
        from forge_harness.cli_v2.status import _node_to_json_dict

        from forge_harness.shared_contracts import normalize_node

        node = normalize_node(_SAMPLE_NODE)
        result = _node_to_json_dict(node)
        assert result["node_id"] == "prya"

    def test_cpu_load_is_float(self):
        """cpu_load must be a float."""
        from forge_harness.cli_v2.status import _node_to_json_dict

        from forge_harness.shared_contracts import normalize_node

        node = normalize_node(_SAMPLE_NODE)
        result = _node_to_json_dict(node)
        assert isinstance(result["cpu_load"], float)

    def test_agent_count_is_int(self):
        """agent_count must be an int."""
        from forge_harness.cli_v2.status import _node_to_json_dict

        from forge_harness.shared_contracts import normalize_node

        node = normalize_node(_SAMPLE_NODE)
        result = _node_to_json_dict(node)
        assert isinstance(result["agent_count"], int)


# ---------------------------------------------------------------------------
# Unit tests — _build_fleet_summary helper
# ---------------------------------------------------------------------------


class TestBuildFleetSummary:
    """Unit tests for the _build_fleet_summary helper."""

    def test_empty_agents(self):
        """Empty agent list returns all-zero counts."""
        from forge_harness.cli_v2.status import _build_fleet_summary

        result = _build_fleet_summary([])
        assert result["total"] == 0
        assert result["active"] == 0
        assert result["idle"] == 0

    def test_counts_active(self):
        """Active agents are counted correctly."""
        from forge_harness.cli_v2.status import _build_fleet_summary

        from forge_harness.shared_contracts import AgentContract

        agents = [
            AgentContract(session_id="a1", agent_role="backend", agent_name="a1", status="active"),
            AgentContract(session_id="a2", agent_role="backend", agent_name="a2", status="active"),
            AgentContract(session_id="a3", agent_role="backend", agent_name="a3", status="idle"),
        ]
        result = _build_fleet_summary(agents)
        assert result["total"] == 3
        assert result["active"] == 2
        assert result["idle"] == 1

    def test_counts_failed(self):
        """Failed agents are counted correctly."""
        from forge_harness.cli_v2.status import _build_fleet_summary

        from forge_harness.shared_contracts import AgentContract

        agents = [
            AgentContract(session_id="a1", agent_role="r", agent_name="n", status="failed"),
            AgentContract(session_id="a2", agent_role="r", agent_name="n", status="active"),
        ]
        result = _build_fleet_summary(agents)
        assert result["failed"] == 1
        assert result["active"] == 1

    def test_returns_required_keys(self):
        """Fleet summary must contain total, active, idle, completed, failed."""
        from forge_harness.cli_v2.status import _build_fleet_summary

        result = _build_fleet_summary([])
        assert set(result.keys()) >= {"total", "active", "idle", "completed", "failed"}


# ---------------------------------------------------------------------------
# Unit tests — fetch_active_leases (status_adapter)
# ---------------------------------------------------------------------------


class TestFetchActiveLeases:
    """Tests for fetch_active_leases in status_adapter."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        """Returns empty list when API is unreachable."""
        from forge_harness.status_adapter import fetch_active_leases

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.side_effect = ConnectionRefusedError("refused")
            result = await fetch_active_leases("http://localhost:9999")

        assert result == []

    @pytest.mark.asyncio
    async def test_filters_tasks_without_lease(self):
        """Tasks without a lease sub-object are excluded."""
        from forge_harness.status_adapter import fetch_active_leases

        api_response = {
            "tasks": [
                _SAMPLE_TASK_NO_LEASE,
                _SAMPLE_TASK_WITH_LEASE,
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await fetch_active_leases()

        # Only the task with a real owner should be returned
        assert len(result) == 1
        assert result[0]["task_id"] == "task-abc"

    @pytest.mark.asyncio
    async def test_lease_dict_shape(self):
        """Each active-lease dict must contain the six required fields."""
        from forge_harness.status_adapter import fetch_active_leases

        api_response = {"tasks": [_SAMPLE_TASK_WITH_LEASE]}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await fetch_active_leases()

        assert len(result) == 1
        lease = result[0]
        for key in ("task_id", "lease_state", "lease_owner", "lease_node", "path_lock", "expires_at"):
            assert key in lease, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_lease_owner_populated(self):
        """lease_owner must reflect owner_agent from the task lease."""
        from forge_harness.status_adapter import fetch_active_leases

        api_response = {"tasks": [_SAMPLE_TASK_WITH_LEASE]}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await fetch_active_leases()

        assert result[0]["lease_owner"] == "agent-forge-01"
        assert result[0]["lease_node"] == "prya"

    @pytest.mark.asyncio
    async def test_skips_lease_without_owner(self):
        """A lease dict with empty owner_agent and owner_node is skipped."""
        from forge_harness.status_adapter import fetch_active_leases

        task_empty_owner = {
            "id": "task-empty",
            "subject": "Some task",
            "description": "No real owner",
            "priority": "low",
            "status": "pending",
            "lease": {
                "owner_agent": "",
                "owner_node": "",
                "path_lock": "",
                "lease_expires_at": "",
            },
        }

        api_response = {"tasks": [task_empty_owner]}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_response

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await fetch_active_leases()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_200(self):
        """Returns empty list when API returns non-200 status."""
        from forge_harness.status_adapter import fetch_active_leases

        mock_resp = MagicMock()
        mock_resp.status_code = 503

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await fetch_active_leases()

        assert result == []


# ---------------------------------------------------------------------------
# Unit tests — CommandCenterClient.list_nodes
# ---------------------------------------------------------------------------


class TestCommandCenterClientListNodes:
    """Tests for CommandCenterClient.list_nodes."""

    @pytest.mark.asyncio
    async def test_returns_list_of_nodes(self):
        """list_nodes returns a list of raw node dicts."""
        from forge_harness.command_center_client import CommandCenterClient

        api_response = MagicMock()
        api_response.success = True
        api_response.data = {"nodes": [_SAMPLE_NODE]}

        client = CommandCenterClient()
        with patch.object(client, "_request", AsyncMock(return_value=api_response)):
            result = await client.list_nodes()

        assert len(result) == 1
        assert result[0]["node_id"] == "prya"

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        """list_nodes returns empty list when API call fails."""
        from forge_harness.command_center_client import CommandCenterClient

        api_response = MagicMock()
        api_response.success = False
        api_response.error = "Connection refused"

        client = CommandCenterClient()
        with patch.object(client, "_request", AsyncMock(return_value=api_response)):
            result = await client.list_nodes()

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_list_response(self):
        """list_nodes handles a response where data is a plain list."""
        from forge_harness.command_center_client import CommandCenterClient

        api_response = MagicMock()
        api_response.success = True
        api_response.data = [_SAMPLE_NODE]

        client = CommandCenterClient()
        with patch.object(client, "_request", AsyncMock(return_value=api_response)):
            result = await client.list_nodes()

        assert len(result) == 1

    def test_sync_list_nodes_callable(self):
        """sync_list_nodes method exists on CommandCenterClient."""
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient()
        assert callable(client.sync_list_nodes)


# ---------------------------------------------------------------------------
# Unit tests — CommandCenterClient.list_active_leases
# ---------------------------------------------------------------------------


class TestCommandCenterClientListActiveLeases:
    """Tests for CommandCenterClient.list_active_leases."""

    @pytest.mark.asyncio
    async def test_extracts_active_leases(self):
        """list_active_leases returns only tasks with owner-populated leases."""
        from forge_harness.command_center_client import CommandCenterClient

        api_response = MagicMock()
        api_response.success = True
        api_response.data = {
            "tasks": [
                _SAMPLE_TASK_WITH_LEASE,
                _SAMPLE_TASK_NO_LEASE,
            ]
        }

        client = CommandCenterClient()
        with patch.object(client, "_request", AsyncMock(return_value=api_response)):
            result = await client.list_active_leases()

        assert len(result) == 1
        assert result[0]["task_id"] == "task-abc"

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        """list_active_leases returns empty list when API call fails."""
        from forge_harness.command_center_client import CommandCenterClient

        api_response = MagicMock()
        api_response.success = False
        api_response.error = "Timeout"

        client = CommandCenterClient()
        with patch.object(client, "_request", AsyncMock(return_value=api_response)):
            result = await client.list_active_leases()

        assert result == []

    @pytest.mark.asyncio
    async def test_lease_shape(self):
        """Each lease dict contains the expected six keys."""
        from forge_harness.command_center_client import CommandCenterClient

        api_response = MagicMock()
        api_response.success = True
        api_response.data = {"tasks": [_SAMPLE_TASK_WITH_LEASE]}

        client = CommandCenterClient()
        with patch.object(client, "_request", AsyncMock(return_value=api_response)):
            result = await client.list_active_leases()

        assert result
        lease = result[0]
        for key in ("task_id", "lease_state", "lease_owner", "lease_node", "path_lock", "expires_at"):
            assert key in lease, f"Missing key: {key}"

    def test_sync_list_active_leases_callable(self):
        """sync_list_active_leases method exists on CommandCenterClient."""
        from forge_harness.command_center_client import CommandCenterClient

        client = CommandCenterClient()
        assert callable(client.sync_list_active_leases)


# ---------------------------------------------------------------------------
# Integration tests — forge status --json output shape
# ---------------------------------------------------------------------------


class TestStatusJsonEnriched:
    """Integration tests for the enriched forge status --json output."""

    @pytest.fixture
    def mock_all_data(self):
        """Return a complete fetch_all result that includes all new CP-3002 fields."""
        from forge_harness.shared_contracts import (
            AgentContract,
            NodeContract,
            TaskContract,
        )

        nodes = [NodeContract(node_id="prya", name="prya", status="online", agent_count=2,
                              cpu_load=40.0, ram_usage=55.0, last_heartbeat="2026-02-23T10:00:00+00:00")]
        agents = [
            AgentContract(session_id="s1", agent_role="backend", agent_name="a1", status="active"),
            AgentContract(session_id="s2", agent_role="content", agent_name="a2", status="idle"),
        ]
        tasks = [TaskContract(id="t1", subject="Do X", description="Backend task")]
        active_leases = [
            {
                "task_id": "t1",
                "lease_state": "in_progress",
                "lease_owner": "agent-forge-01",
                "lease_node": "prya",
                "path_lock": "backend/api",
                "expires_at": "2026-02-23T11:00:00+00:00",
            }
        ]
        approvals = [{"id": "apr-1", "status": "pending", "title": "Deploy v2"}]

        return {
            "agents": agents,
            "tasks": tasks,
            "nodes": nodes,
            "approvals": approvals,
            "health": {"status": "ok"},
            "portfolio": {"total_projects": 95},
            "patterns": [],
            "active_leases": active_leases,
            "timestamp": "2026-02-23T10:00:00+00:00",
        }

    def _build_mocked_status(self, runner, forge_root, mock_all_data):
        """Invoke status --json with fetch_all and dashboard mocked."""
        from forge_harness.cli_v2 import cli

        # Use a plain object with __dict__ so ``hasattr(obj, '__dict__')`` works
        # and ``obj.__dict__`` returns a plain dict without MagicMock interference.
        class _FakeRalph:
            pass

        mock_ralph = _FakeRalph()

        mock_dashboard = MagicMock()
        mock_dashboard._load_active_pipelines.return_value = []
        mock_dashboard._load_mvp_status.return_value = []
        mock_dashboard._load_ralph_status.return_value = mock_ralph

        with (
            patch("forge_harness.cli_v2.status.asyncio.run", return_value=mock_all_data),
            # create_dashboard is imported lazily inside the function body, so
            # we must patch at the source module level.
            patch(
                "forge_harness.dashboard.create_dashboard",
                return_value=mock_dashboard,
            ),
        ):
            with runner.isolated_filesystem(temp_dir=forge_root):
                result = runner.invoke(
                    cli,
                    ["status", "--json"],
                    env={"FORGE_ROOT": str(forge_root)},
                    catch_exceptions=False,
                )
        return result

    def test_output_is_valid_json(self, runner, forge_root, mock_all_data):
        """forge status --json should emit valid JSON."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_output_has_nodes_key(self, runner, forge_root, mock_all_data):
        """Top-level 'nodes' key must be present in --json output."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert "nodes" in data

    def test_nodes_shape(self, runner, forge_root, mock_all_data):
        """Each node in 'nodes' must have the six CP-3002 fields."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        nodes = data["nodes"]
        assert isinstance(nodes, list)
        assert len(nodes) >= 1
        node = nodes[0]
        for key in ("node_id", "status", "agent_count", "cpu_load", "ram_usage", "last_heartbeat"):
            assert key in node, f"Missing node key: {key}"

    def test_output_has_active_leases_key(self, runner, forge_root, mock_all_data):
        """Top-level 'active_leases' key must be present in --json output."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert "active_leases" in data

    def test_active_leases_is_list(self, runner, forge_root, mock_all_data):
        """'active_leases' must be a list."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert isinstance(data["active_leases"], list)

    def test_active_leases_shape(self, runner, forge_root, mock_all_data):
        """Each entry in 'active_leases' must have the six CP-3002 fields."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        leases = data["active_leases"]
        assert len(leases) == 1
        for key in ("task_id", "lease_state", "lease_owner", "lease_node", "path_lock", "expires_at"):
            assert key in leases[0], f"Missing lease key: {key}"

    def test_output_has_fleet_key(self, runner, forge_root, mock_all_data):
        """Top-level 'fleet' key must be present in --json output."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert "fleet" in data

    def test_fleet_summary_shape(self, runner, forge_root, mock_all_data):
        """'fleet' must contain total, active, idle, completed, failed."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        fleet = data["fleet"]
        for key in ("total", "active", "idle", "completed", "failed"):
            assert key in fleet, f"Missing fleet key: {key}"

    def test_fleet_totals_are_correct(self, runner, forge_root, mock_all_data):
        """Fleet total must match the number of agents in mock_all_data."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        # mock_all_data has 2 agents (1 active, 1 idle)
        assert data["fleet"]["total"] == 2
        assert data["fleet"]["active"] == 1
        assert data["fleet"]["idle"] == 1

    def test_output_has_approvals_pending_key(self, runner, forge_root, mock_all_data):
        """Top-level 'approvals_pending' key must be present in --json output."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert "approvals_pending" in data

    def test_approvals_pending_is_int(self, runner, forge_root, mock_all_data):
        """'approvals_pending' must be an integer."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert isinstance(data["approvals_pending"], int)

    def test_approvals_pending_count(self, runner, forge_root, mock_all_data):
        """'approvals_pending' must equal the length of the approvals list."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        # mock_all_data has 1 approval
        assert data["approvals_pending"] == 1

    def test_portfolio_key_present(self, runner, forge_root, mock_all_data):
        """Backward-compat: 'portfolio' key must still be present."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert "portfolio" in data

    def test_approvals_list_preserved(self, runner, forge_root, mock_all_data):
        """Backward-compat: 'approvals' list key must still be present."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert "approvals" in data
        assert isinstance(data["approvals"], list)

    def test_zero_active_leases_when_none(self, runner, forge_root, mock_all_data):
        """active_leases must be an empty list when no leases are active."""
        mock_all_data_no_leases = {**mock_all_data, "active_leases": []}
        result = self._build_mocked_status(runner, forge_root, mock_all_data_no_leases)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert data["active_leases"] == []

    def test_zero_approvals_pending_when_none(self, runner, forge_root, mock_all_data):
        """approvals_pending must be 0 when no approvals exist."""
        mock_all_data_no_approvals = {**mock_all_data, "approvals": []}
        result = self._build_mocked_status(runner, forge_root, mock_all_data_no_approvals)
        parsed = json.loads(result.output)
        data = parsed.get("data", parsed)
        assert data["approvals_pending"] == 0

    def test_success_flag_in_envelope(self, runner, forge_root, mock_all_data):
        """The JSON envelope must include success: true."""
        result = self._build_mocked_status(runner, forge_root, mock_all_data)
        parsed = json.loads(result.output)
        assert parsed.get("success") is True


# ---------------------------------------------------------------------------
# Regression: fetch_all includes active_leases key
# ---------------------------------------------------------------------------


class TestFetchAllIncludesActiveLeases:
    """Verify that fetch_all returns the active_leases key."""

    @pytest.mark.asyncio
    async def test_fetch_all_has_active_leases(self):
        """fetch_all must include 'active_leases' in its return dict."""
        from forge_harness.status_adapter import fetch_all

        with patch("forge_harness.status_adapter.fetch_active_leases", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_agents", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_tasks", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_fleet_status", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_approvals", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_health", AsyncMock(return_value=None)), \
             patch("forge_harness.status_adapter.fetch_portfolio", AsyncMock(return_value=None)), \
             patch("forge_harness.status_adapter.fetch_patterns", AsyncMock(return_value=[])):
            result = await fetch_all()

        assert "active_leases" in result

    @pytest.mark.asyncio
    async def test_fetch_all_active_leases_value(self):
        """fetch_all active_leases must match what fetch_active_leases returns."""
        from forge_harness.status_adapter import fetch_all

        expected = [{"task_id": "t1", "lease_state": "in_progress", "lease_owner": "a1",
                     "lease_node": "n1", "path_lock": "", "expires_at": ""}]

        with patch("forge_harness.status_adapter.fetch_active_leases", AsyncMock(return_value=expected)), \
             patch("forge_harness.status_adapter.fetch_agents", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_tasks", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_fleet_status", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_approvals", AsyncMock(return_value=[])), \
             patch("forge_harness.status_adapter.fetch_health", AsyncMock(return_value=None)), \
             patch("forge_harness.status_adapter.fetch_portfolio", AsyncMock(return_value=None)), \
             patch("forge_harness.status_adapter.fetch_patterns", AsyncMock(return_value=[])):
            result = await fetch_all()

        assert result["active_leases"] == expected
