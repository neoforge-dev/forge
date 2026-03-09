"""
Tests for forge health check subcommand - Node heartbeat health.

Covers:
    forge health check                      All nodes
    forge health check --node <id>          Single node filter
    forge health check --json               JSON output
    forge health check --stale-threshold    Custom stale threshold
    forge health check --offline-threshold  Custom offline threshold

Also covers the helper utilities:
    _compute_node_status
    _age_label
    _parse_iso_age
    _load_nodes_from_store
    _load_nodes_from_ops_files
    _load_all_nodes
    _status_style (node string variants)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from forge_harness.cli_v2.health import (
    _OFFLINE_THRESHOLD,
    _STALE_THRESHOLD,
    _age_label,
    _compute_node_status,
    _load_all_nodes,
    _load_nodes_from_ops_files,
    _load_nodes_from_store,
    _parse_iso_age,
    _status_style,
    health,
    health_check,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now(offset_seconds: float = 0.0) -> str:
    """Return a UTC ISO timestamp offset by *offset_seconds* from now."""
    ts = datetime.now(UTC) - timedelta(seconds=offset_seconds)
    return ts.isoformat()


def _write_store_record(store_path: Path, record: dict) -> None:
    """Append a JSON record to the JSONL store file."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with store_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _write_ops_state(ops_dir: Path, hostname: str, data: dict) -> None:
    """Write a .state.json ops heartbeat file."""
    ops_dir.mkdir(parents=True, exist_ok=True)
    (ops_dir / f"{hostname}.state.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 1. Unit tests: _compute_node_status
# ---------------------------------------------------------------------------


class TestComputeNodeStatus:
    def test_fresh_heartbeat_is_healthy(self):
        assert _compute_node_status(30.0) == "healthy"

    def test_at_stale_boundary_is_healthy(self):
        # Exactly at the threshold is still healthy.
        assert _compute_node_status(_STALE_THRESHOLD) == "healthy"

    def test_just_past_stale_threshold_is_stale(self):
        assert _compute_node_status(_STALE_THRESHOLD + 1) == "stale"

    def test_at_offline_boundary_is_stale(self):
        assert _compute_node_status(_OFFLINE_THRESHOLD) == "stale"

    def test_just_past_offline_threshold_is_offline(self):
        assert _compute_node_status(_OFFLINE_THRESHOLD + 1) == "offline"

    def test_very_old_heartbeat_is_offline(self):
        assert _compute_node_status(999999) == "offline"

    def test_zero_age_is_healthy(self):
        assert _compute_node_status(0.0) == "healthy"


# ---------------------------------------------------------------------------
# 2. Unit tests: _age_label
# ---------------------------------------------------------------------------


class TestAgeLabel:
    def test_unknown_for_infinity(self):
        assert _age_label(float("inf")) == "unknown"

    def test_seconds_label(self):
        label = _age_label(45.0)
        assert "s" in label
        assert "45" in label

    def test_minutes_label(self):
        label = _age_label(90.0)
        assert "m" in label

    def test_hours_label(self):
        label = _age_label(7200.0)
        assert "h" in label

    def test_zero_age(self):
        label = _age_label(0.0)
        assert "0s" in label


# ---------------------------------------------------------------------------
# 3. Unit tests: _parse_iso_age
# ---------------------------------------------------------------------------


class TestParseIsoAge:
    def test_empty_string_returns_inf(self):
        assert _parse_iso_age("") == float("inf")

    def test_invalid_string_returns_inf(self):
        assert _parse_iso_age("not-a-date") == float("inf")

    def test_recent_timestamp_is_small(self):
        ts = _iso_now(offset_seconds=10)
        age = _parse_iso_age(ts)
        assert 0 <= age <= 15  # allow a couple of seconds' drift

    def test_old_timestamp_is_large(self):
        ts = _iso_now(offset_seconds=600)
        age = _parse_iso_age(ts)
        assert age >= 595

    def test_naive_timestamp_is_accepted(self):
        # A naive timestamp (no tzinfo) should not raise.
        ts = datetime.utcnow().isoformat()
        age = _parse_iso_age(ts)
        assert age != float("inf")


# ---------------------------------------------------------------------------
# 4. Unit tests: _status_style (node string variant)
# ---------------------------------------------------------------------------


class TestStatusStyleNodeStrings:
    def test_healthy_returns_green(self):
        assert "green" in _status_style("healthy")

    def test_stale_returns_yellow(self):
        assert "yellow" in _status_style("stale")

    def test_offline_returns_red(self):
        assert "red" in _status_style("offline")

    def test_unknown_status_returns_non_empty(self):
        style = _status_style("unknown_xyz")
        assert isinstance(style, str)
        assert len(style) > 0


# ---------------------------------------------------------------------------
# 5. Unit tests: _load_nodes_from_store
# ---------------------------------------------------------------------------


class TestLoadNodesFromStore:
    def test_empty_store_returns_empty_list(self, tmp_path):
        assert _load_nodes_from_store(tmp_path) == []

    def test_missing_store_returns_empty_list(self, tmp_path):
        # Directory exists but JSONL file does not.
        (tmp_path / ".forge" / "heartbeats").mkdir(parents=True)
        assert _load_nodes_from_store(tmp_path) == []

    def test_single_healthy_node(self, tmp_path):
        store = tmp_path / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {
                "node_id": "nova",
                "hostname": "nova.local",
                "last_seen": _iso_now(offset_seconds=10),
                "status": "healthy",
                "active_agents": 3,
                "active_leases": 1,
                "cpu_percent": 25.0,
                "memory_percent": 50.0,
                "ttl_seconds": 120,
            },
        )
        nodes = _load_nodes_from_store(tmp_path)
        assert len(nodes) == 1
        n = nodes[0]
        assert n["node_id"] == "nova"
        assert n["hostname"] == "nova.local"
        assert n["status"] == "healthy"
        assert n["active_agents"] == 3
        assert n["source"] == "store"

    def test_last_write_wins_for_same_node(self, tmp_path):
        store = tmp_path / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(store, {"node_id": "nova", "hostname": "old", "last_seen": _iso_now(600), "status": "healthy", "active_agents": 1, "active_leases": 0})
        _write_store_record(store, {"node_id": "nova", "hostname": "new", "last_seen": _iso_now(5), "status": "healthy", "active_agents": 5, "active_leases": 2})
        nodes = _load_nodes_from_store(tmp_path)
        assert len(nodes) == 1
        assert nodes[0]["hostname"] == "new"
        assert nodes[0]["active_agents"] == 5

    def test_stale_node_status_is_recomputed(self, tmp_path):
        store = tmp_path / ".forge" / "heartbeats" / "nodes.jsonl"
        # Store says "healthy" but timestamp is very old.
        _write_store_record(
            store,
            {
                "node_id": "prya",
                "hostname": "prya",
                "last_seen": _iso_now(offset_seconds=_STALE_THRESHOLD + 30),
                "status": "healthy",
                "active_agents": 0,
                "active_leases": 0,
            },
        )
        nodes = _load_nodes_from_store(tmp_path)
        assert len(nodes) == 1
        assert nodes[0]["status"] == "stale"

    def test_malformed_lines_are_skipped(self, tmp_path):
        store = tmp_path / ".forge" / "heartbeats" / "nodes.jsonl"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(
            '{"node_id": "good", "last_seen": "' + _iso_now(5) + '", "status": "healthy"}\n'
            "not valid json\n"
            "\n",
            encoding="utf-8",
        )
        nodes = _load_nodes_from_store(tmp_path)
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "good"


# ---------------------------------------------------------------------------
# 6. Unit tests: _load_nodes_from_ops_files
# ---------------------------------------------------------------------------


class TestLoadNodesFromOpsFiles:
    def test_empty_directory_returns_empty_list(self, tmp_path):
        ops_dir = tmp_path / ".forge" / "ops" / "heartbeat"
        ops_dir.mkdir(parents=True)
        assert _load_nodes_from_ops_files(tmp_path) == []

    def test_missing_directory_returns_empty_list(self, tmp_path):
        assert _load_nodes_from_ops_files(tmp_path) == []

    def test_single_ops_node(self, tmp_path):
        ops_dir = tmp_path / ".forge" / "ops" / "heartbeat"
        _write_ops_state(
            ops_dir,
            "sati",
            {
                "listener": "xnode",
                "hostname": "sati",
                "last_heartbeat": _iso_now(offset_seconds=20),
                "status": "connected",
            },
        )
        nodes = _load_nodes_from_ops_files(tmp_path)
        assert len(nodes) == 1
        n = nodes[0]
        assert n["hostname"] == "sati"
        assert n["status"] == "healthy"
        assert n["source"] == "ops"

    def test_non_json_files_are_skipped(self, tmp_path):
        ops_dir = tmp_path / ".forge" / "ops" / "heartbeat"
        ops_dir.mkdir(parents=True)
        (ops_dir / "noise.state.json").write_text("not json", encoding="utf-8")
        assert _load_nodes_from_ops_files(tmp_path) == []


# ---------------------------------------------------------------------------
# 7. Unit tests: _load_all_nodes (merge logic)
# ---------------------------------------------------------------------------


class TestLoadAllNodes:
    def test_store_wins_over_ops_on_same_node_id(self, tmp_path):
        # Ops file says node "shared" has 0 agents.
        ops_dir = tmp_path / ".forge" / "ops" / "heartbeat"
        _write_ops_state(
            ops_dir,
            "shared",
            {"hostname": "shared", "last_heartbeat": _iso_now(10), "status": "connected"},
        )
        # Store says 5 agents.
        store = tmp_path / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {
                "node_id": "shared",
                "hostname": "shared",
                "last_seen": _iso_now(5),
                "status": "healthy",
                "active_agents": 5,
                "active_leases": 2,
            },
        )
        nodes = _load_all_nodes(tmp_path)
        assert len(nodes) == 1
        assert nodes[0]["active_agents"] == 5
        assert nodes[0]["source"] == "store"

    def test_ops_nodes_included_when_no_store(self, tmp_path):
        ops_dir = tmp_path / ".forge" / "ops" / "heartbeat"
        _write_ops_state(
            ops_dir,
            "nova",
            {"hostname": "nova", "last_heartbeat": _iso_now(15), "status": "connected"},
        )
        nodes = _load_all_nodes(tmp_path)
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "nova"

    def test_both_sources_merged(self, tmp_path):
        ops_dir = tmp_path / ".forge" / "ops" / "heartbeat"
        _write_ops_state(
            ops_dir,
            "ops-only",
            {"hostname": "ops-only", "last_heartbeat": _iso_now(5), "status": "connected"},
        )
        store = tmp_path / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {"node_id": "store-only", "hostname": "store-only", "last_seen": _iso_now(5), "status": "healthy", "active_agents": 0, "active_leases": 0},
        )
        nodes = _load_all_nodes(tmp_path)
        node_ids = {n["node_id"] for n in nodes}
        assert "ops-only" in node_ids
        assert "store-only" in node_ids


# ---------------------------------------------------------------------------
# 8. CLI integration tests: forge health check
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def forge_root(tmp_path):
    """Create a minimal FORGE root in a temporary directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


class TestHealthCheckCLI:
    def test_help_shows_check_subcommand(self, runner):
        result = runner.invoke(health, ["--help"])
        assert result.exit_code == 0
        assert "check" in result.output

    def test_check_help(self, runner):
        result = runner.invoke(health_check, ["--help"])
        assert result.exit_code == 0
        assert "heartbeat" in result.output.lower()

    def test_check_no_nodes_exits_zero(self, runner, forge_root):
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, [])
        assert result.exit_code == 0
        assert "no heartbeat" in result.output.lower()

    def test_check_healthy_nodes_table(self, runner, forge_root):
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {
                "node_id": "nova",
                "hostname": "nova.local",
                "last_seen": _iso_now(5),
                "status": "healthy",
                "active_agents": 2,
                "active_leases": 1,
            },
        )
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, [])
        assert result.exit_code == 0
        assert "nova" in result.output
        assert "healthy" in result.output

    def test_check_stale_node_exits_nonzero(self, runner, forge_root):
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {
                "node_id": "prya",
                "hostname": "prya",
                "last_seen": _iso_now(_STALE_THRESHOLD + 60),
                "status": "healthy",
                "active_agents": 0,
                "active_leases": 0,
            },
        )
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, [])
        assert result.exit_code != 0
        assert "stale" in result.output.lower()

    def test_check_offline_node_exits_nonzero(self, runner, forge_root):
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {
                "node_id": "dead",
                "hostname": "dead",
                "last_seen": _iso_now(_OFFLINE_THRESHOLD + 120),
                "status": "healthy",
                "active_agents": 0,
                "active_leases": 0,
            },
        )
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, [])
        assert result.exit_code != 0
        assert "offline" in result.output.lower()

    def test_check_json_output_structure(self, runner, forge_root):
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {
                "node_id": "nova",
                "hostname": "nova.local",
                "last_seen": _iso_now(10),
                "status": "healthy",
                "active_agents": 1,
                "active_leases": 0,
            },
        )
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["success"] is True
        data = payload["data"]
        assert "total_nodes" in data
        assert "healthy" in data
        assert "stale" in data
        assert "offline" in data
        assert "nodes" in data
        assert data["total_nodes"] == 1
        assert data["healthy"] == 1
        node = data["nodes"][0]
        assert node["node_id"] == "nova"
        assert "age_seconds" in node
        assert "age_label" in node

    def test_check_json_contains_thresholds(self, runner, forge_root):
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--json"])
        payload = json.loads(result.output)
        assert "thresholds" in payload["data"]
        thresholds = payload["data"]["thresholds"]
        assert thresholds["stale_seconds"] == _STALE_THRESHOLD
        assert thresholds["offline_seconds"] == _OFFLINE_THRESHOLD

    def test_check_node_filter_by_id(self, runner, forge_root):
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(store, {"node_id": "nova", "hostname": "nova.local", "last_seen": _iso_now(5), "status": "healthy", "active_agents": 0, "active_leases": 0})
        _write_store_record(store, {"node_id": "prya", "hostname": "prya.local", "last_seen": _iso_now(5), "status": "healthy", "active_agents": 0, "active_leases": 0})
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--node", "nova"])
        assert result.exit_code == 0
        assert "nova" in result.output
        assert "prya" not in result.output

    def test_check_node_filter_not_found_exits_nonzero(self, runner, forge_root):
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--node", "nonexistent"])
        assert result.exit_code != 0

    def test_check_node_filter_not_found_json(self, runner, forge_root):
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--node", "ghost", "--json"])
        assert result.exit_code != 0
        payload = json.loads(result.output)
        assert payload["success"] is False

    def test_check_custom_stale_threshold(self, runner, forge_root):
        """A node 50s old should be stale with --stale-threshold 30."""
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {"node_id": "nova", "hostname": "nova", "last_seen": _iso_now(50), "status": "healthy", "active_agents": 0, "active_leases": 0},
        )
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--stale-threshold", "30"])
        assert result.exit_code != 0
        assert "stale" in result.output.lower()

    def test_check_custom_offline_threshold(self, runner, forge_root):
        """A node 200s old should be offline with --offline-threshold 150."""
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {"node_id": "nova", "hostname": "nova", "last_seen": _iso_now(200), "status": "healthy", "active_agents": 0, "active_leases": 0},
        )
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--offline-threshold", "150"])
        assert result.exit_code != 0
        assert "offline" in result.output.lower()

    def test_check_ops_fallback(self, runner, forge_root):
        """Nodes from .forge/ops/heartbeat/*.state.json appear when store absent."""
        ops_dir = forge_root / ".forge" / "ops" / "heartbeat"
        _write_ops_state(
            ops_dir,
            "sati",
            {"hostname": "sati", "last_heartbeat": _iso_now(5), "status": "connected"},
        )
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, [])
        assert result.exit_code == 0
        assert "sati" in result.output

    def test_check_json_stale_nodes_exits_nonzero(self, runner, forge_root):
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(
            store,
            {"node_id": "stale-node", "hostname": "stale-node", "last_seen": _iso_now(_STALE_THRESHOLD + 60), "status": "healthy", "active_agents": 0, "active_leases": 0},
        )
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--json"])
        assert result.exit_code != 0
        payload = json.loads(result.output)
        assert payload["data"]["stale"] >= 1

    def test_check_multiple_nodes_summary_counts(self, runner, forge_root):
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(store, {"node_id": "a", "hostname": "a", "last_seen": _iso_now(5), "status": "healthy", "active_agents": 1, "active_leases": 0})
        _write_store_record(store, {"node_id": "b", "hostname": "b", "last_seen": _iso_now(_STALE_THRESHOLD + 30), "status": "healthy", "active_agents": 0, "active_leases": 0})
        _write_store_record(store, {"node_id": "c", "hostname": "c", "last_seen": _iso_now(_OFFLINE_THRESHOLD + 30), "status": "healthy", "active_agents": 0, "active_leases": 0})
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--json"])
        payload = json.loads(result.output)
        data = payload["data"]
        assert data["total_nodes"] == 3
        assert data["healthy"] == 1
        assert data["stale"] == 1
        assert data["offline"] == 1

    def test_check_node_filter_by_hostname(self, runner, forge_root):
        """--node should match on hostname as well as node_id."""
        store = forge_root / ".forge" / "heartbeats" / "nodes.jsonl"
        _write_store_record(store, {"node_id": "n1", "hostname": "nova.local", "last_seen": _iso_now(5), "status": "healthy", "active_agents": 0, "active_leases": 0})
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health_check, ["--node", "nova.local"])
        assert result.exit_code == 0
        assert "nova.local" in result.output

    def test_health_group_still_shows_check_in_help(self, runner):
        result = runner.invoke(health, ["check", "--help"])
        assert result.exit_code == 0
        assert "heartbeat" in result.output.lower() or "node" in result.output.lower()


# ---------------------------------------------------------------------------
# 9. Backward-compatibility: existing health command (agent check) still works
# ---------------------------------------------------------------------------


class TestHealthLegacyAgentCheck:
    def test_health_help_includes_agent_options(self, runner):
        result = runner.invoke(health, ["--help"])
        assert result.exit_code == 0

    def test_health_requires_agent_name_without_all_flag(self, runner):
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=Path("/tmp")):
            result = runner.invoke(health, [], catch_exceptions=False)
        # Should show an error or exit non-zero when no agent and no --all
        assert result.exit_code != 0 or "required" in result.output.lower() or "agent" in result.output.lower()

    def test_health_check_subcommand_invocable_via_group(self, runner, forge_root):
        """The check subcommand is reachable through the health group."""
        with patch("forge_harness.cli_v2.health.require_forge_root", return_value=forge_root):
            result = runner.invoke(health, ["check"])
        # Should not produce an error about unknown subcommand
        assert "no such command" not in result.output.lower()
