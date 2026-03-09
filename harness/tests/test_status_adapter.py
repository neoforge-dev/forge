"""Tests for forge_harness.status_adapter (CP-5008).

Verifies that each fetch function:
- Makes the correct HTTP request
- Returns properly normalised contract types
- Returns empty/None gracefully when the API is unreachable
- Returns empty/None gracefully when the API returns a non-200 status
- Returns empty/None gracefully when the response body is malformed

All HTTP calls are mocked with httpx.MockTransport so no live server is needed.

Run with:
    cd harness && uv run pytest tests/test_status_adapter.py -v --tb=short
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from forge_harness.shared_contracts import (
    AgentContract,
    NodeContract,
    TaskContract,
)
from forge_harness.status_adapter import (
    _base_url,
    _build_headers,
    fetch_agents,
    fetch_all,
    fetch_approvals,
    fetch_fleet_status,
    fetch_health,
    fetch_patterns,
    fetch_portfolio,
    fetch_tasks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int, body: Any) -> httpx.Response:
    """Build an httpx.Response from a status code and JSON-serialisable body."""
    content = json.dumps(body).encode()
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers={"content-type": "application/json"},
    )


class _MockTransport(httpx.AsyncBaseTransport):
    """Transport that returns a pre-baked response without network I/O."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Attach the request so callers can inspect it
        self._last_request = request
        return self._response


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_build_headers_with_token(self):
        headers = _build_headers("my-secret-token")
        assert headers == {"Authorization": "Bearer my-secret-token"}

    def test_build_headers_no_token(self):
        assert _build_headers(None) == {}
        assert _build_headers("") == {}

    def test_base_url_default(self):
        assert _base_url(None) == "http://localhost:8080"

    def test_base_url_custom(self):
        assert _base_url("http://cc.example.com") == "http://cc.example.com"

    def test_base_url_strips_trailing_slash(self):
        assert _base_url("http://cc.example.com/") == "http://cc.example.com"


# ---------------------------------------------------------------------------
# fetch_agents
# ---------------------------------------------------------------------------


class TestFetchAgents:
    @pytest.mark.asyncio
    async def test_returns_agent_contracts_on_success(self):
        body = {
            "data": {
                "agents": [
                    {"session_id": "s1", "agent_role": "backend-engineer", "agent_name": "kimi"},
                    {"session_id": "s2", "agent_role": "frontend-engineer", "agent_name": "codex"},
                ]
            }
        }
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_agents("http://localhost:8080", None)

        assert len(result) == 2
        assert all(isinstance(a, AgentContract) for a in result)
        assert result[0].session_id == "s1"
        assert result[0].agent_role == "backend-engineer"

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_non_200(self):
        transport = _MockTransport(_make_response(503, {"error": "unavailable"}))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_agents()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_connection_error(self):
        async def _raise(*_a, **_kw):
            raise httpx.ConnectError("connection refused")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_cls.return_value = mock_client

            result = await fetch_agents("http://dead-host:9999", None)

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_malformed_agents_field(self):
        """If 'agents' is not a list, return empty without raising."""
        body = {"data": {"agents": "not-a-list"}}
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_agents()

        assert result == []


# ---------------------------------------------------------------------------
# fetch_tasks
# ---------------------------------------------------------------------------


class TestFetchTasks:
    @pytest.mark.asyncio
    async def test_returns_task_contracts_on_success(self):
        body = {
            "data": {
                "tasks": [
                    {"id": "t1", "subject": "Fix auth bug", "status": "pending"},
                    {"id": "t2", "subject": "Add tests", "status": "in_progress"},
                ]
            }
        }
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_tasks("http://localhost:8080", None)

        assert len(result) == 2
        assert all(isinstance(t, TaskContract) for t in result)
        assert result[0].id == "t1"
        assert result[1].status == "in_progress"

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_404(self):
        transport = _MockTransport(_make_response(404, {"error": "not found"}))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_tasks()

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_root_level_tasks_key(self):
        """Supports body["tasks"] at root (older API shape)."""
        body = {"tasks": [{"id": "t3", "subject": "Deploy", "status": "pending"}]}
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_tasks()

        assert len(result) == 1
        assert result[0].id == "t3"


# ---------------------------------------------------------------------------
# fetch_approvals
# ---------------------------------------------------------------------------


class TestFetchApprovals:
    @pytest.mark.asyncio
    async def test_returns_approval_dicts_on_success(self):
        body = {
            "data": {
                "approvals": [
                    {
                        "id": "a1",
                        "type": "feature",
                        "title": "Deploy voice coach",
                        "status": "pending",
                        "priority": "high",
                        "tier": "PHONE",
                        "risk_score": 0.3,
                        "domain": "brandfocus-ai",
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                ]
            }
        }
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_approvals("http://localhost:8080", None)

        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert result[0]["id"] == "a1"
        assert result[0]["tier"] == "PHONE"

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_non_200(self):
        transport = _MockTransport(_make_response(500, {"error": "server error"}))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_approvals()

        assert result == []


# ---------------------------------------------------------------------------
# fetch_fleet_status
# ---------------------------------------------------------------------------


class TestFetchFleetStatus:
    @pytest.mark.asyncio
    async def test_returns_node_contracts_on_success(self):
        body = {
            "nodes": [
                {
                    "node_id": "nova",
                    "name": "Nova (M3 Max)",
                    "status": "online",
                    "agent_count": 5,
                },
                {
                    "node_id": "prya",
                    "name": "Prya",
                    "status": "online",
                    "agent_count": 3,
                },
            ]
        }
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_fleet_status("http://localhost:8080", None)

        assert len(result) == 2
        assert all(isinstance(n, NodeContract) for n in result)
        assert result[0].node_id == "nova"
        assert result[1].agent_count == 3

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_network_error(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("network timeout"))
            mock_cls.return_value = mock_client

            result = await fetch_fleet_status()

        assert result == []


# ---------------------------------------------------------------------------
# fetch_health
# ---------------------------------------------------------------------------


class TestFetchHealth:
    @pytest.mark.asyncio
    async def test_returns_raw_dict_on_success(self):
        body = {"status": "healthy", "services": {"db": "ok", "redis": "ok"}}
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_health("http://localhost:8080", None)

        assert isinstance(result, dict)
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_returns_none_on_non_200(self):
        transport = _MockTransport(_make_response(503, {}))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_health()

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_cls.return_value = mock_client

            result = await fetch_health()

        assert result is None


# ---------------------------------------------------------------------------
# fetch_portfolio
# ---------------------------------------------------------------------------


class TestFetchPortfolio:
    @pytest.mark.asyncio
    async def test_returns_portfolio_dict_on_success(self):
        portfolio_payload = {
            "total_projects": 95,
            "active_domains": 11,
            "domains": [{"id": "brandfocus-ai", "project_count": 3}],
        }
        body = {"data": portfolio_payload}
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_portfolio("http://localhost:8080", None)

        assert result == portfolio_payload
        assert result["total_projects"] == 95  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        transport = _MockTransport(_make_response(500, {}))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_portfolio()

        assert result is None


# ---------------------------------------------------------------------------
# fetch_patterns
# ---------------------------------------------------------------------------


class TestFetchPatterns:
    @pytest.mark.asyncio
    async def test_returns_pattern_dicts_on_success(self):
        body = {
            "data": {
                "patterns": [
                    {"name": "test-first", "category": "quality", "success_rate": 0.92, "uses": 14},
                    {"name": "async-io", "category": "arch", "success_rate": 0.87, "uses": 7},
                ]
            }
        }
        transport = _MockTransport(_make_response(200, body))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_patterns("http://localhost:8080", None)

        assert len(result) == 2
        assert result[0]["name"] == "test-first"
        assert result[1]["success_rate"] == 0.87

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_error(self):
        transport = _MockTransport(_make_response(503, {}))
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await fetch_patterns()

        assert result == []


# ---------------------------------------------------------------------------
# fetch_all
# ---------------------------------------------------------------------------


class TestFetchAll:
    @pytest.mark.asyncio
    async def test_returns_all_keys(self):
        """fetch_all must include all expected top-level keys in the result."""
        # Patch all seven individual fetch functions to avoid real HTTP calls
        with (
            patch(
                "forge_harness.status_adapter.fetch_agents",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "forge_harness.status_adapter.fetch_tasks",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "forge_harness.status_adapter.fetch_fleet_status",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "forge_harness.status_adapter.fetch_approvals",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "forge_harness.status_adapter.fetch_health",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "forge_harness.status_adapter.fetch_portfolio",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "forge_harness.status_adapter.fetch_patterns",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = await fetch_all("http://localhost:8080", "token")

        expected_keys = {
            "agents", "tasks", "nodes", "approvals",
            "health", "portfolio", "patterns", "timestamp",
        }
        assert expected_keys.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_timestamp_is_iso_format(self):
        with (
            patch("forge_harness.status_adapter.fetch_agents", new=AsyncMock(return_value=[])),
            patch("forge_harness.status_adapter.fetch_tasks", new=AsyncMock(return_value=[])),
            patch("forge_harness.status_adapter.fetch_fleet_status", new=AsyncMock(return_value=[])),
            patch("forge_harness.status_adapter.fetch_approvals", new=AsyncMock(return_value=[])),
            patch("forge_harness.status_adapter.fetch_health", new=AsyncMock(return_value=None)),
            patch("forge_harness.status_adapter.fetch_portfolio", new=AsyncMock(return_value=None)),
            patch("forge_harness.status_adapter.fetch_patterns", new=AsyncMock(return_value=[])),
        ):
            result = await fetch_all()

        # Should be a valid ISO-8601 string
        ts = datetime.fromisoformat(result["timestamp"])
        assert ts.tzinfo is not None
