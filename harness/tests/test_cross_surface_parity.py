"""
Cross-Surface Parity Tests — CP-3005
=====================================

Asserts that the CLI (``forge status --json``) and CC Web API produce
identical contract shapes for tasks, agents, and nodes.  Both surfaces run
raw API dicts through the same ``normalize_*`` helpers from
``shared_contracts.py`` via ``status_adapter.py``.

If a future change removes or renames a field on any contract model, the
drift-detection tests here will break immediately — catching the regression
before it silently diverges across surfaces.

Test categories:
  1. Contract Shape Tests        — mock HTTP → adapter ≡ direct normalize_*
  2. StatusSnapshot Tests        — fetch_all() produces expected structure
  3. Drift Detection Tests       — required fields guard, missing-field defaults
  4. Round-trip Tests            — contract → dict → normalize → same contract
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_harness.shared_contracts import (
    AgentContract,
    NodeContract,
    StatusSnapshot,
    TaskContract,
    normalize_agent,
    normalize_node,
    normalize_task,
)
from forge_harness.status_adapter import (
    fetch_agents,
    fetch_all,
    fetch_fleet_status,
    fetch_tasks,
)

# ---------------------------------------------------------------------------
# Fixture data — realistic raw API payloads
# ---------------------------------------------------------------------------

RAW_AGENT: dict[str, Any] = {
    "session_id": "sess-abc123",
    "agent_role": "backend-engineer",
    "agent_name": "kimi",
    "domain": "codeswiftr-com",
    "project": "interview-simulator",
    "current_task": "CP-3005 parity tests",
    "focus_tags": ["testing", "contracts"],
    "status": "active",
    "progress_pct": 42.0,
    "tasks_completed": 7,
    "tasks_remaining": 3,
    "started_at": "2026-02-22T10:00:00+00:00",
    "last_activity": "2026-02-22T10:30:00+00:00",
    "token_usage": 15000,
    "api_calls": 8,
    "pending_approval_id": None,
    "parent_id": None,
    "children": [],
    "source": "command-center",
}

RAW_TASK: dict[str, Any] = {
    "id": "task-xyz789",
    "subject": "Add cross-surface parity tests",
    "description": "CP-3005 — verify CLI and API return identical shapes",
    "priority": "high",
    "status": "in_progress",
    "required_role": "backend-engineer",
    "claimed_by": "kimi",
    "domain": "harness",
    "project": "forge-harness",
    "order": 1,
    "lease": None,
    "depends_on": [],
    "created_at": "2026-02-22T09:00:00+00:00",
    "updated_at": "2026-02-22T10:00:00+00:00",
    "completed_at": None,
}

RAW_NODE: dict[str, Any] = {
    "node_id": "node-prya",
    "name": "prya",
    "status": "online",
    "agent_count": 5,
    "cpu_load": 0.38,
    "ram_usage": 0.62,
    "last_heartbeat": "2026-02-22T10:29:00+00:00",
    "latency_ms": 12,
    "agents": [
        {
            "id": "agent-1",
            "session_id": "sess-001",
            "agent_id": "a001",
            "name": "kimi",
            "role": "backend-engineer",
            "status": "active",
            "project": "interview-simulator",
            "task": "CP-3005",
            "progress": 42,
            "context_percentage": 35,
        }
    ],
    "load_average": 1.2,
    "disk_usage": 0.45,
    "alerts": [],
    "capabilities": {"claude": True, "docker": True},
    "specs": {"cpu": "Apple M2", "ram_gb": 16, "os": "macOS 15", "cores": 8},
    "is_fresh": True,
    "age_seconds": 60.0,
    "received_at": "2026-02-22T10:29:05+00:00",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(json_body: dict[str, Any], status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_body
    return mock_resp


def _async_context_client(mock_resp: MagicMock) -> MagicMock:
    """Return a mock AsyncClient context manager that yields a client
    whose get() method returns mock_resp."""
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


# ===========================================================================
# 1. Contract Shape Tests
# ===========================================================================


class TestContractShapeMatch:
    """Verify the adapter and the direct normalizer produce identical contracts."""

    def test_cli_and_api_agent_shape_match(self) -> None:
        """fetch_agents() and normalize_agent() must produce identical AgentContract."""
        # Simulate the raw JSON the CC API returns for GET /api/agents
        api_body = {"data": {"agents": [RAW_AGENT]}}

        mock_resp = _make_mock_response(api_body)
        mock_ctx = _async_context_client(mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            adapter_result = asyncio.run(fetch_agents("http://localhost:8080"))

        assert len(adapter_result) == 1
        adapter_agent = adapter_result[0]

        # Direct normalize path (CLI surface)
        direct_agent = normalize_agent(RAW_AGENT)

        assert isinstance(adapter_agent, AgentContract)
        assert isinstance(direct_agent, AgentContract)
        assert adapter_agent == direct_agent, (
            f"Adapter and direct normalize produced different shapes.\n"
            f"Adapter: {adapter_agent.model_dump()}\n"
            f"Direct:  {direct_agent.model_dump()}"
        )

    def test_cli_and_api_task_shape_match(self) -> None:
        """fetch_tasks() and normalize_task() must produce identical TaskContract."""
        api_body = {"data": {"tasks": [RAW_TASK]}}

        mock_resp = _make_mock_response(api_body)
        mock_ctx = _async_context_client(mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            adapter_result = asyncio.run(fetch_tasks("http://localhost:8080"))

        assert len(adapter_result) == 1
        adapter_task = adapter_result[0]

        direct_task = normalize_task(RAW_TASK)

        assert isinstance(adapter_task, TaskContract)
        assert isinstance(direct_task, TaskContract)
        assert adapter_task == direct_task, (
            f"Adapter and direct normalize produced different shapes.\n"
            f"Adapter: {adapter_task.model_dump()}\n"
            f"Direct:  {direct_task.model_dump()}"
        )

    def test_cli_and_api_node_shape_match(self) -> None:
        """fetch_fleet_status() and normalize_node() must produce identical NodeContract."""
        # NOTE: fleet endpoint returns {"nodes": [...]} at root (no data wrapper)
        api_body = {"nodes": [RAW_NODE]}

        mock_resp = _make_mock_response(api_body)
        mock_ctx = _async_context_client(mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            adapter_result = asyncio.run(fetch_fleet_status("http://localhost:8080"))

        assert len(adapter_result) == 1
        adapter_node = adapter_result[0]

        direct_node = normalize_node(RAW_NODE)

        assert isinstance(adapter_node, NodeContract)
        assert isinstance(direct_node, NodeContract)
        assert adapter_node == direct_node, (
            f"Adapter and direct normalize produced different shapes.\n"
            f"Adapter: {adapter_node.model_dump()}\n"
            f"Direct:  {direct_node.model_dump()}"
        )


# ===========================================================================
# 2. StatusSnapshot Tests
# ===========================================================================


class TestStatusSnapshot:
    """Validate the StatusSnapshot structure returned by fetch_all()."""

    def _build_all_mocks(self) -> dict[str, MagicMock]:
        """Return a dict mapping fetch function names to mock context managers."""
        agents_resp = _make_mock_response({"data": {"agents": [RAW_AGENT]}})
        tasks_resp = _make_mock_response({"data": {"tasks": [RAW_TASK]}})
        nodes_resp = _make_mock_response({"nodes": [RAW_NODE]})
        approvals_resp = _make_mock_response({"data": {"approvals": []}})
        health_resp = _make_mock_response({"status": "ok"})
        portfolio_resp = _make_mock_response({"data": {"total_projects": 95}})
        patterns_resp = _make_mock_response({"data": {"patterns": []}})

        # All share the same client mock; order of get() calls matches fetch_all():
        # agents, tasks, nodes, approvals, health, portfolio, patterns
        responses = [
            agents_resp,
            tasks_resp,
            nodes_resp,
            approvals_resp,
            health_resp,
            portfolio_resp,
            patterns_resp,
        ]

        mock_client = AsyncMock()
        mock_client.get.side_effect = responses

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        return mock_ctx

    def test_status_snapshot_contains_all_contract_types(self) -> None:
        """fetch_all() result must include agents, tasks, nodes with correct types."""
        mock_ctx = self._build_all_mocks()

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            result = asyncio.run(fetch_all("http://localhost:8080"))

        assert "agents" in result, "fetch_all() result is missing 'agents' key"
        assert "tasks" in result, "fetch_all() result is missing 'tasks' key"
        assert "nodes" in result, "fetch_all() result is missing 'nodes' key"

        assert len(result["agents"]) == 1
        assert len(result["tasks"]) == 1
        assert len(result["nodes"]) == 1

        assert isinstance(result["agents"][0], AgentContract)
        assert isinstance(result["tasks"][0], TaskContract)
        assert isinstance(result["nodes"][0], NodeContract)

    def test_status_snapshot_json_serializable(self) -> None:
        """StatusSnapshot.model_dump() must produce only JSON-serializable primitives."""
        agent = normalize_agent(RAW_AGENT)
        task = normalize_task(RAW_TASK)
        node = normalize_node(RAW_NODE)

        snapshot = StatusSnapshot(
            agents=[agent],
            tasks=[task],
            nodes=[node],
        )

        dumped = snapshot.model_dump()

        # If any datetime/pydantic object leaked, json.dumps will raise TypeError
        try:
            serialized = json.dumps(dumped)
        except TypeError as exc:
            pytest.fail(
                f"StatusSnapshot.model_dump() contains a non-JSON-serializable value: {exc}"
            )

        # Round-trip sanity: deserialize and check top-level keys are present
        reloaded = json.loads(serialized)
        assert "agents" in reloaded
        assert "tasks" in reloaded
        assert "nodes" in reloaded
        assert "timestamp" in reloaded


# ===========================================================================
# 3. Drift Detection Tests
# ===========================================================================


class TestDriftDetection:
    """Guard rails: if required fields are removed, these tests break."""

    # --- Required field assertions ---

    def test_agent_contract_required_fields(self) -> None:
        """AgentContract must expose the minimum required fields for all surfaces."""
        required = {"session_id", "agent_role", "agent_name", "status", "progress_pct"}
        model_fields = set(AgentContract.model_fields.keys())
        missing = required - model_fields
        assert not missing, (
            f"AgentContract is missing required fields: {missing}. "
            "Removing these will break CLI and Web surface parity."
        )

    def test_task_contract_required_fields(self) -> None:
        """TaskContract must expose the minimum required fields for all surfaces."""
        required = {"id", "subject", "status", "priority"}
        model_fields = set(TaskContract.model_fields.keys())
        missing = required - model_fields
        assert not missing, (
            f"TaskContract is missing required fields: {missing}. "
            "Removing these will break CLI and Web surface parity."
        )

    def test_node_contract_required_fields(self) -> None:
        """NodeContract must expose the minimum required fields for all surfaces."""
        required = {"node_id", "name", "status", "agent_count"}
        model_fields = set(NodeContract.model_fields.keys())
        missing = required - model_fields
        assert not missing, (
            f"NodeContract is missing required fields: {missing}. "
            "Removing these will break CLI and Web surface parity."
        )

    # --- Missing-field default handling ---

    def test_normalize_agent_handles_missing_fields(self) -> None:
        """normalize_agent() with minimal input must produce a valid AgentContract."""
        minimal: dict[str, Any] = {"agent_role": "engineer"}
        result = normalize_agent(minimal)

        assert isinstance(result, AgentContract)
        # session_id falls back to f"agent-{role}"
        assert result.session_id == "agent-engineer"
        assert result.agent_role == "engineer"
        assert result.agent_name == "engineer"  # falls back to role
        assert result.status == "idle"
        assert result.progress_pct == 0.0
        assert result.tasks_completed == 0
        assert result.tasks_remaining == 0
        assert result.focus_tags == []
        assert result.children == []

    def test_normalize_task_handles_missing_fields(self) -> None:
        """normalize_task() with minimal input must produce a valid TaskContract."""
        minimal: dict[str, Any] = {}
        result = normalize_task(minimal)

        assert isinstance(result, TaskContract)
        assert result.id == ""
        assert result.subject == ""
        assert result.description == ""
        assert result.priority == "medium"
        assert result.status == "pending"
        assert result.depends_on == []
        assert result.lease is None

    def test_normalize_node_handles_missing_fields(self) -> None:
        """normalize_node() with minimal input must produce a valid NodeContract."""
        minimal: dict[str, Any] = {}
        result = normalize_node(minimal)

        assert isinstance(result, NodeContract)
        assert result.node_id == ""
        assert result.name == ""
        assert result.status == "offline"
        assert result.agent_count == 0
        assert result.cpu_load == 0.0
        assert result.ram_usage == 0.0
        assert result.agents == []
        assert result.alerts == []


# ===========================================================================
# 4. Round-trip Tests
# ===========================================================================


class TestRoundTrip:
    """Contract → dict → normalize → same contract."""

    def test_agent_contract_roundtrip(self) -> None:
        """An AgentContract serialized to dict and normalized back must be equal."""
        original = normalize_agent(RAW_AGENT)
        dumped = original.model_dump()

        # model_dump() uses snake_case field names which match normalize_agent()
        # field aliases.  The only name-mapping normalize_agent() handles is
        # role/name → agent_role/agent_name, so we must re-map them here.
        dumped.setdefault("agent_role", dumped.get("agent_role", original.agent_role))
        dumped.setdefault("agent_name", dumped.get("agent_name", original.agent_name))

        restored = normalize_agent(dumped)

        assert isinstance(restored, AgentContract)
        assert original == restored, (
            f"Round-trip produced different AgentContract.\n"
            f"Original: {original.model_dump()}\n"
            f"Restored: {restored.model_dump()}"
        )

    def test_task_contract_roundtrip(self) -> None:
        """A TaskContract serialized to dict and normalized back must be equal."""
        original = normalize_task(RAW_TASK)
        dumped = original.model_dump()

        # model_dump() uses None for completed_at — normalize_task handles that
        restored = normalize_task(dumped)

        assert isinstance(restored, TaskContract)
        assert original == restored, (
            f"Round-trip produced different TaskContract.\n"
            f"Original: {original.model_dump()}\n"
            f"Restored: {restored.model_dump()}"
        )
