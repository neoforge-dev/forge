"""
Tests for ``forge status --json`` node/lease details (CP-3002).

Covers:
  - _node_to_json_dict helper projection and lease placeholder
  - ``forge status --nodes --json`` output shape
  - ``forge status --json`` full snapshot node array shape
  - Node fields: node_id, name, status, agent_count, cpu_load, ram_usage,
    last_heartbeat, lease
  - Empty node lists (API offline / no nodes)
  - Multiple nodes in output
  - fetch_all parallel path used in full snapshot
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.status import _node_to_json_dict, status

from forge_harness.shared_contracts import (
    NodeContract,
    normalize_node,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_node(**kwargs: Any) -> NodeContract:
    """Build a NodeContract from keyword overrides."""
    defaults = {
        "node_id": "prya",
        "name": "prya-workstation",
        "status": "online",
        "agent_count": 3,
        "cpu_load": 42.5,
        "ram_usage": 61.2,
        "last_heartbeat": "2026-02-22T10:00:00+00:00",
    }
    defaults.update(kwargs)
    return normalize_node(defaults)


@pytest.fixture()
def single_node() -> NodeContract:
    return _make_node()


@pytest.fixture()
def multi_nodes() -> list[NodeContract]:
    return [
        _make_node(
            node_id="prya",
            name="prya-workstation",
            status="online",
            agent_count=3,
            cpu_load=42.5,
            ram_usage=61.2,
            last_heartbeat="2026-02-22T10:00:00+00:00",
        ),
        _make_node(
            node_id="vega",
            name="vega-server",
            status="degraded",
            agent_count=1,
            cpu_load=88.0,
            ram_usage=90.5,
            last_heartbeat="2026-02-22T09:55:00+00:00",
        ),
        _make_node(
            node_id="nova",
            name="nova-laptop",
            status="offline",
            agent_count=0,
            cpu_load=0.0,
            ram_usage=0.0,
            last_heartbeat="",
        ),
    ]


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Unit tests — _node_to_json_dict helper
# ---------------------------------------------------------------------------


class TestNodeToJsonDict:
    """Unit tests for the _node_to_json_dict projection helper."""

    def test_required_keys_present(self, single_node: NodeContract) -> None:
        result = _node_to_json_dict(single_node)
        expected_keys = {
            "node_id",
            "name",
            "status",
            "agent_count",
            "cpu_load",
            "ram_usage",
            "last_heartbeat",
            "lease",
        }
        assert set(result.keys()) == expected_keys

    def test_values_match_node(self, single_node: NodeContract) -> None:
        result = _node_to_json_dict(single_node)
        assert result["node_id"] == "prya"
        assert result["name"] == "prya-workstation"
        assert result["status"] == "online"
        assert result["agent_count"] == 3
        assert result["cpu_load"] == pytest.approx(42.5)
        assert result["ram_usage"] == pytest.approx(61.2)
        assert result["last_heartbeat"] == "2026-02-22T10:00:00+00:00"

    def test_lease_is_none_placeholder(self, single_node: NodeContract) -> None:
        """lease is None — placeholder for future node-level lease support."""
        result = _node_to_json_dict(single_node)
        assert result["lease"] is None

    def test_no_extra_internal_fields_leaked(self, single_node: NodeContract) -> None:
        """Helper should NOT include internal fields like latency_ms, agents, specs."""
        result = _node_to_json_dict(single_node)
        assert "latency_ms" not in result
        assert "agents" not in result
        assert "capabilities" not in result
        assert "specs" not in result
        assert "alerts" not in result
        assert "is_fresh" not in result
        assert "age_seconds" not in result
        assert "received_at" not in result

    def test_offline_node_zero_metrics(self) -> None:
        node = _make_node(
            node_id="nova",
            name="nova-laptop",
            status="offline",
            agent_count=0,
            cpu_load=0.0,
            ram_usage=0.0,
            last_heartbeat="",
        )
        result = _node_to_json_dict(node)
        assert result["status"] == "offline"
        assert result["agent_count"] == 0
        assert result["cpu_load"] == 0.0
        assert result["ram_usage"] == 0.0
        assert result["last_heartbeat"] == ""

    def test_degraded_node(self) -> None:
        node = _make_node(status="degraded", cpu_load=95.0, ram_usage=99.1)
        result = _node_to_json_dict(node)
        assert result["status"] == "degraded"
        assert result["cpu_load"] == pytest.approx(95.0)
        assert result["ram_usage"] == pytest.approx(99.1)

    def test_multiple_nodes_projection(self, multi_nodes: list[NodeContract]) -> None:
        results = [_node_to_json_dict(n) for n in multi_nodes]
        assert len(results) == 3
        node_ids = [r["node_id"] for r in results]
        assert "prya" in node_ids
        assert "vega" in node_ids
        assert "nova" in node_ids
        # All must have the lease placeholder
        assert all(r["lease"] is None for r in results)

    def test_empty_node_list(self) -> None:
        results = [_node_to_json_dict(n) for n in []]
        assert results == []


# ---------------------------------------------------------------------------
# CLI integration tests — forge status --nodes --json
# ---------------------------------------------------------------------------


class TestStatusNodesJson:
    """Integration tests for ``forge status --nodes --json``."""

    def _invoke(
        self,
        runner: CliRunner,
        node_list: list[NodeContract],
        extra_args: list[str] | None = None,
    ) -> Any:
        args = ["--nodes", "--json"] + (extra_args or [])
        with (
            patch(
                "forge_harness.cli_v2.status.require_forge_root",
                return_value="/tmp/forge-test",
            ),
            patch(
                # Functions are imported from status_adapter inside the function body,
                # so we patch at the source module.
                "forge_harness.status_adapter.fetch_fleet_status",
                new=AsyncMock(return_value=node_list),
            ),
        ):
            return runner.invoke(status, args)

    def test_exit_zero(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        assert result.exit_code == 0, result.output

    def test_output_is_valid_json(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_envelope_success_true(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        assert parsed["success"] is True

    def test_data_is_list(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        assert isinstance(parsed["data"], list)

    def test_single_node_shape(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        node = parsed["data"][0]
        assert node["node_id"] == "prya"
        assert node["name"] == "prya-workstation"
        assert node["status"] == "online"
        assert node["agent_count"] == 3
        assert "cpu_load" in node
        assert "ram_usage" in node
        assert "last_heartbeat" in node
        assert "lease" in node
        assert node["lease"] is None

    def test_single_node_all_required_fields(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        node = parsed["data"][0]
        required_fields = {
            "node_id", "name", "status", "agent_count",
            "cpu_load", "ram_usage", "last_heartbeat", "lease",
        }
        assert required_fields.issubset(set(node.keys()))

    def test_multiple_nodes(self, runner: CliRunner, multi_nodes: list[NodeContract]) -> None:
        result = self._invoke(runner, multi_nodes)
        parsed = json.loads(result.output)
        data = parsed["data"]
        assert len(data) == 3
        ids = [n["node_id"] for n in data]
        assert set(ids) == {"prya", "vega", "nova"}

    def test_multiple_nodes_all_have_lease_none(
        self, runner: CliRunner, multi_nodes: list[NodeContract]
    ) -> None:
        result = self._invoke(runner, multi_nodes)
        parsed = json.loads(result.output)
        assert all(n["lease"] is None for n in parsed["data"])

    def test_empty_nodes(self, runner: CliRunner) -> None:
        result = self._invoke(runner, [])
        parsed = json.loads(result.output)
        assert parsed["success"] is True
        assert parsed["data"] == []

    def test_command_field_in_envelope(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        assert parsed.get("command") == "forge status --nodes"

    def test_timestamp_in_envelope(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        assert "timestamp" in parsed
        assert parsed["timestamp"]  # non-empty string

    def test_no_internal_fields_in_node(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        node = parsed["data"][0]
        assert "latency_ms" not in node
        assert "agents" not in node
        assert "capabilities" not in node
        assert "specs" not in node

    def test_offline_node_values(self, runner: CliRunner) -> None:
        offline = _make_node(
            node_id="nova",
            name="nova-laptop",
            status="offline",
            agent_count=0,
            cpu_load=0.0,
            ram_usage=0.0,
            last_heartbeat="",
        )
        result = self._invoke(runner, [offline])
        parsed = json.loads(result.output)
        node = parsed["data"][0]
        assert node["status"] == "offline"
        assert node["agent_count"] == 0
        assert node["cpu_load"] == 0.0
        assert node["ram_usage"] == 0.0

    def test_error_json_on_fetch_failure(self, runner: CliRunner) -> None:
        with (
            patch(
                "forge_harness.cli_v2.status.require_forge_root",
                return_value="/tmp/forge-test",
            ),
            patch(
                "forge_harness.status_adapter.fetch_fleet_status",
                new=AsyncMock(side_effect=RuntimeError("API unreachable")),
            ),
        ):
            result = runner.invoke(status, ["--nodes", "--json"])
        parsed = json.loads(result.output)
        assert parsed["success"] is False
        assert "error" in parsed


# ---------------------------------------------------------------------------
# CLI integration tests — forge status --json (full snapshot)
# ---------------------------------------------------------------------------

_EMPTY_FETCH_ALL: dict[str, Any] = {
    "agents": [],
    "tasks": [],
    "nodes": [],
    "approvals": [],
    "health": None,
    "portfolio": None,
    "patterns": [],
    "timestamp": "2026-02-22T10:00:00+00:00",
}


def _make_dashboard_mock() -> MagicMock:
    """Return a minimal dashboard mock that satisfies the file-based calls."""
    dash = MagicMock(spec_set=None)
    # _load_active_pipelines() -> list
    dash._load_active_pipelines = MagicMock(return_value=[])
    # _load_mvp_status() -> list
    dash._load_mvp_status = MagicMock(return_value=[])
    # _load_ralph_status() -> object with no __dict__ special casing needed
    ralph_stub = MagicMock()
    del ralph_stub.__dict__  # remove so hasattr check returns False
    dash._load_ralph_status = MagicMock(return_value=ralph_stub)
    return dash


class TestStatusFullSnapshotJson:
    """Integration tests for ``forge status --json`` (full snapshot path)."""

    def _invoke(
        self,
        runner: CliRunner,
        node_list: list[NodeContract] | None = None,
    ) -> Any:
        """Invoke status --json with proper mocking of individual fetch functions.

        The status.py code calls individual fetch_tasks, fetch_agents, fetch_fleet_status
        etc., not fetch_all. So we mock those functions directly.
        """
        nodes = node_list if node_list is not None else []

        with (
            patch(
                "forge_harness.cli_v2.status.require_forge_root",
                return_value="/tmp/forge-test",
            ),
            patch(
                "forge_harness.status_adapter.fetch_tasks",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "forge_harness.status_adapter.fetch_agents",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "forge_harness.status_adapter.fetch_fleet_status",
                new=AsyncMock(return_value=nodes),
            ),
            patch(
                "forge_harness.status_adapter.fetch_approvals",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "forge_harness.status_adapter.fetch_portfolio",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "forge_harness.status_adapter.fetch_patterns",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                # create_dashboard is imported from forge_harness.dashboard inside
                # the function body, so patch at the source module.
                "forge_harness.dashboard.create_dashboard",
                return_value=_make_dashboard_mock(),
            ),
        ):
            return runner.invoke(status, ["--json"])

    def test_exit_zero(self, runner: CliRunner) -> None:
        result = self._invoke(runner)
        assert result.exit_code == 0, result.output

    def test_output_is_valid_json(self, runner: CliRunner) -> None:
        result = self._invoke(runner)
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_envelope_success_true(self, runner: CliRunner) -> None:
        result = self._invoke(runner)
        parsed = json.loads(result.output)
        assert parsed["success"] is True

    def test_top_level_keys_present(self, runner: CliRunner) -> None:
        result = self._invoke(runner)
        parsed = json.loads(result.output)
        data = parsed["data"]
        expected_top_keys = {
            "tasks", "agents", "nodes", "approvals",
            "portfolio", "patterns", "pipelines", "mvp_status",
            "ralph_status", "task_stats", "timestamp",
        }
        assert expected_top_keys.issubset(set(data.keys()))

    def test_nodes_is_list_when_empty(self, runner: CliRunner) -> None:
        result = self._invoke(runner)
        parsed = json.loads(result.output)
        assert isinstance(parsed["data"]["nodes"], list)
        assert parsed["data"]["nodes"] == []

    def test_single_node_in_snapshot(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        nodes = parsed["data"]["nodes"]
        assert len(nodes) == 1
        node = nodes[0]
        assert node["node_id"] == "prya"
        assert node["name"] == "prya-workstation"
        assert node["status"] == "online"
        assert node["agent_count"] == 3
        assert "cpu_load" in node
        assert "ram_usage" in node
        assert "last_heartbeat" in node
        assert "lease" in node

    def test_node_lease_is_none_placeholder(self, runner: CliRunner, single_node: NodeContract) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        assert parsed["data"]["nodes"][0]["lease"] is None

    def test_multiple_nodes_in_snapshot(
        self, runner: CliRunner, multi_nodes: list[NodeContract]
    ) -> None:
        result = self._invoke(runner, multi_nodes)
        parsed = json.loads(result.output)
        nodes = parsed["data"]["nodes"]
        assert len(nodes) == 3
        ids = {n["node_id"] for n in nodes}
        assert ids == {"prya", "vega", "nova"}

    def test_all_nodes_have_required_fields(
        self, runner: CliRunner, multi_nodes: list[NodeContract]
    ) -> None:
        result = self._invoke(runner, multi_nodes)
        parsed = json.loads(result.output)
        required = {
            "node_id", "name", "status", "agent_count",
            "cpu_load", "ram_usage", "last_heartbeat", "lease",
        }
        for node in parsed["data"]["nodes"]:
            assert required.issubset(set(node.keys())), (
                f"Node {node.get('node_id')} missing fields: "
                f"{required - set(node.keys())}"
            )

    def test_nodes_no_internal_fields(
        self, runner: CliRunner, single_node: NodeContract
    ) -> None:
        result = self._invoke(runner, [single_node])
        parsed = json.loads(result.output)
        node = parsed["data"]["nodes"][0]
        assert "latency_ms" not in node
        assert "agents" not in node
        assert "capabilities" not in node
        assert "specs" not in node
        assert "alerts" not in node
        assert "is_fresh" not in node
        assert "age_seconds" not in node

    def test_command_field(self, runner: CliRunner) -> None:
        result = self._invoke(runner)
        parsed = json.loads(result.output)
        assert parsed.get("command") == "forge status"

    def test_agents_and_tasks_in_snapshot(self, runner: CliRunner) -> None:
        result = self._invoke(runner)
        parsed = json.loads(result.output)
        data = parsed["data"]
        assert isinstance(data["agents"], list)
        assert isinstance(data["tasks"], list)

    def test_error_json_on_fetch_all_failure(self, runner: CliRunner) -> None:
        with (
            patch(
                "forge_harness.cli_v2.status.require_forge_root",
                return_value="/tmp/forge-test",
            ),
            patch(
                "forge_harness.status_adapter.fetch_tasks",
                new=AsyncMock(side_effect=ConnectionError("CC offline")),
            ),
            patch(
                "forge_harness.dashboard.create_dashboard",
                return_value=_make_dashboard_mock(),
            ),
        ):
            result = runner.invoke(status, ["--json"])
        parsed = json.loads(result.output)
        assert parsed["success"] is False
        assert parsed.get("error") is not None
