"""
Public Status Adapter — CP-5008
================================

Thin async data-fetching layer that both ``forge status`` and the dashboard
can use to retrieve status data from the Command Center API.

This module decouples callers from dashboard internals by providing a
stable public API surface.  There is *no business logic* here — each
function makes one HTTP call, normalises the raw JSON into canonical
contract types, and returns.

Usage::

    from forge_harness.status_adapter import (
        fetch_agents,
        fetch_tasks,
        fetch_approvals,
        fetch_fleet_status,
        fetch_health,
        fetch_portfolio,
        fetch_patterns,
    )

    agents   = await fetch_agents(api_url, api_token)
    tasks    = await fetch_tasks(api_url, api_token)
    approvals = await fetch_approvals(api_url, api_token)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .shared_contracts import (
    AgentContract,
    NodeContract,
    TaskContract,
    normalize_agent,
    normalize_node,
    normalize_task,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "http://localhost:8080"


def _build_headers(api_token: str | None) -> dict[str, str]:
    """Build auth headers if a token is provided."""
    if api_token:
        return {"Authorization": f"Bearer {api_token}"}
    return {}


def _base_url(api_url: str | None) -> str:
    return (api_url or _DEFAULT_BASE_URL).rstrip("/")


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------


async def fetch_agents(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    timeout: float = 30.0,
) -> list[AgentContract]:
    """Fetch agents from the Command Center API.

    Calls ``GET /api/agents`` and returns each entry normalised through
    :func:`~forge_harness.shared_contracts.normalize_agent`.

    Args:
        api_url:   Base URL of the Command Center API.  Defaults to
                   ``http://localhost:8080``.
        api_token: Optional bearer token for authentication.
        timeout:   HTTP request timeout in seconds.

    Returns:
        List of :class:`~forge_harness.shared_contracts.AgentContract`
        objects.  Returns an empty list if the API is unreachable or
        returns a non-200 status.
    """
    import httpx

    url = _base_url(api_url) + "/api/agents"
    headers = _build_headers(api_token)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                # API may nest under "data" or return "agents" at root
                data = body.get("data", body)
                raw_agents: list[dict[str, Any]] = data.get("agents", [])
                if not isinstance(raw_agents, list):
                    raw_agents = []
                return [normalize_agent(a) for a in raw_agents if isinstance(a, dict)]
            logger.debug("fetch_agents: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_agents: error reaching %s — %s", url, exc)

    return []


async def fetch_tasks(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    timeout: float = 10.0,
) -> list[TaskContract]:
    """Fetch tasks from the Command Center API.

    Calls ``GET /api/tasks`` and returns each entry normalised through
    :func:`~forge_harness.shared_contracts.normalize_task`.

    Args:
        api_url:   Base URL of the Command Center API.
        api_token: Optional bearer token.
        timeout:   HTTP request timeout in seconds.

    Returns:
        List of :class:`~forge_harness.shared_contracts.TaskContract`
        objects.  Empty list on error.
    """
    import httpx

    url = _base_url(api_url) + "/api/tasks"
    headers = _build_headers(api_token)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                # The API nests tasks under body["data"]["tasks"] or body["tasks"]
                data = body.get("data", body)
                raw_tasks: list[dict[str, Any]] = (
                    data.get("tasks", []) if isinstance(data, dict) else []
                )
                if not isinstance(raw_tasks, list):
                    raw_tasks = []
                return [normalize_task(t) for t in raw_tasks if isinstance(t, dict)]
            logger.debug("fetch_tasks: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_tasks: error reaching %s — %s", url, exc)

    return []


async def fetch_approvals(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    status: str = "pending",
    limit: int = 50,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Fetch approval requests from the Command Center API.

    Calls ``GET /api/approvals`` and returns raw dicts — approvals do not
    yet have a dedicated Pydantic contract in ``shared_contracts.py``, so
    callers receive the normalised-but-untyped list directly.

    Each dict contains at minimum: ``id``, ``type``, ``title``, ``domain``,
    ``priority``, ``tier``, ``risk_score``, ``created_at``, ``status``.

    Args:
        api_url:   Base URL of the Command Center API.
        api_token: Optional bearer token.
        status:    Filter by approval status (default ``"pending"``).
        limit:     Maximum number of approvals to return (default 50).
        timeout:   HTTP request timeout in seconds.

    Returns:
        List of approval dicts.  Empty list on error.
    """
    import httpx

    url = _base_url(api_url) + "/api/approvals"
    headers = _build_headers(api_token)
    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                body = resp.json()
                data = body.get("data", body)
                raw: list[dict[str, Any]] = (
                    data.get("approvals", []) if isinstance(data, dict) else []
                )
                if not isinstance(raw, list):
                    raw = []
                return [a for a in raw if isinstance(a, dict)]
            logger.debug("fetch_approvals: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_approvals: error reaching %s — %s", url, exc)

    return []


async def fetch_fleet_status(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    timeout: float = 10.0,
) -> list[NodeContract]:
    """Fetch node/fleet status from the Command Center API.

    Calls ``GET /api/nodes`` and returns each node normalised through
    :func:`~forge_harness.shared_contracts.normalize_node`.

    Args:
        api_url:   Base URL of the Command Center API.
        api_token: Optional bearer token.
        timeout:   HTTP request timeout in seconds.

    Returns:
        List of :class:`~forge_harness.shared_contracts.NodeContract`
        objects.  Empty list on error.
    """
    import httpx

    url = _base_url(api_url) + "/api/nodes"
    headers = _build_headers(api_token)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                # API returns {"nodes": [...]} at root level
                raw_nodes: list[dict[str, Any]] = body.get("nodes", [])
                if not isinstance(raw_nodes, list):
                    raw_nodes = []
                return [normalize_node(n) for n in raw_nodes if isinstance(n, dict)]
            logger.debug("fetch_fleet_status: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_fleet_status: error reaching %s — %s", url, exc)

    return []


async def fetch_health(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, Any] | None:
    """Fetch health status from the Command Center API.

    Calls ``GET /api/health`` and returns the raw response body.
    Health data does not currently have a dedicated Pydantic contract, so
    the raw dict is returned for flexibility.

    Args:
        api_url:   Base URL of the Command Center API.
        api_token: Optional bearer token.
        timeout:   HTTP request timeout in seconds.

    Returns:
        Raw health dict, or ``None`` if the API is unreachable or returns
        a non-200 status.
    """
    import httpx

    url = _base_url(api_url) + "/api/health"
    headers = _build_headers(api_token)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("fetch_health: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_health: error reaching %s — %s", url, exc)

    return None


async def fetch_portfolio(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Fetch portfolio overview from the Command Center API.

    Calls ``GET /api/portfolio`` and returns the ``data`` payload.

    Args:
        api_url:   Base URL of the Command Center API.
        api_token: Optional bearer token.
        timeout:   HTTP request timeout in seconds.

    Returns:
        Portfolio dict (``domains``, ``total_projects``, etc.), or ``None``
        on error.
    """
    import httpx

    url = _base_url(api_url) + "/api/portfolio"
    headers = _build_headers(api_token)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json().get("data")
            logger.debug("fetch_portfolio: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_portfolio: error reaching %s — %s", url, exc)

    return None


async def fetch_active_leases(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Fetch tasks with active leases from the Command Center API.

    Calls ``GET /api/tasks`` and filters entries that hold a non-empty
    ``lease`` sub-object.  Each returned dict has the canonical shape::

        {
            "task_id":    str,
            "lease_state":  str,
            "lease_owner":  str,
            "lease_node":   str,
            "path_lock":    str,
            "expires_at":   str,
        }

    Args:
        api_url:   Base URL of the Command Center API.
        api_token: Optional bearer token.
        timeout:   HTTP request timeout in seconds.

    Returns:
        List of active-lease dicts.  Empty list on error or when no
        leases are active.
    """
    import httpx

    url = _base_url(api_url) + "/api/tasks"
    headers = _build_headers(api_token)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                data = body.get("data", body)
                raw_tasks: list[dict[str, Any]] = (
                    data.get("tasks", []) if isinstance(data, dict) else []
                )
                if not isinstance(raw_tasks, list):
                    raw_tasks = []

                leases: list[dict[str, Any]] = []
                for task in raw_tasks:
                    if not isinstance(task, dict):
                        continue
                    lease = task.get("lease")
                    if not isinstance(lease, dict) or not lease:
                        continue
                    owner_agent = str(lease.get("owner_agent", "") or "")
                    owner_node = str(lease.get("owner_node", "") or "")
                    if not owner_agent and not owner_node:
                        continue
                    leases.append(
                        {
                            "task_id": task.get("id", ""),
                            "lease_state": task.get("status", ""),
                            "lease_owner": owner_agent,
                            "lease_node": owner_node,
                            "path_lock": str(lease.get("path_lock", "") or ""),
                            "expires_at": str(lease.get("lease_expires_at", "") or ""),
                        }
                    )
                return leases
            logger.debug("fetch_active_leases: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_active_leases: error reaching %s — %s", url, exc)

    return []


async def fetch_patterns(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Fetch pattern stats from the Command Center API.

    Calls ``GET /api/patterns`` and returns the list of pattern dicts from
    the ``data.patterns`` key.

    Args:
        api_url:   Base URL of the Command Center API.
        api_token: Optional bearer token.
        timeout:   HTTP request timeout in seconds.

    Returns:
        List of pattern dicts.  Empty list on error.
    """
    import httpx

    url = _base_url(api_url) + "/api/patterns"
    headers = _build_headers(api_token)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                data = body.get("data", body)
                raw: list[dict[str, Any]] = (
                    data.get("patterns", []) if isinstance(data, dict) else []
                )
                if not isinstance(raw, list):
                    raw = []
                return [p for p in raw if isinstance(p, dict)]
            logger.debug("fetch_patterns: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_patterns: error reaching %s — %s", url, exc)

    return []


# ---------------------------------------------------------------------------
# Fleet summary (real-time agent + business data)
# ---------------------------------------------------------------------------


async def fetch_fleet_summary(
    api_url: str | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch the fleet summary from ``GET /api/fleet/summary``.

    This endpoint is **public** — no auth token required.  It returns
    real-time data for every known agent (context %, task, status,
    staleness) plus per-domain business metrics (deploy status, MRR,
    Stripe live flag).

    Args:
        api_url: Base URL of the Command Center API.  Defaults to
                 ``http://localhost:8080``.
        timeout: HTTP request timeout in seconds.

    Returns:
        Raw dict with keys ``fleet_agents``, ``business``, ``nodes``,
        ``domains``, ``health``, ``generated_at``.  Returns an empty
        dict (``{}``) if the API is unreachable or returns a non-200
        status.

    Example::

        summary = await fetch_fleet_summary()
        for agent in summary.get("fleet_agents", []):
            print(agent["agent"], agent["context_pct"], agent["status"])
        for domain in summary.get("business", []):
            print(domain["display_name"], domain["deploy_status"], domain["current_mrr"])
    """
    import httpx

    url = _base_url(api_url) + "/api/fleet/summary"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("fetch_fleet_summary: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("fetch_fleet_summary: error reaching %s — %s", url, exc)

    return {}


# ---------------------------------------------------------------------------
# Convenience: fetch all in parallel
# ---------------------------------------------------------------------------


async def fetch_all(
    api_url: str | None = None,
    api_token: str | None = None,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch agents, tasks, nodes, approvals, health, portfolio, patterns,
    and active leases in parallel.

    This is a convenience wrapper that fires all eight API calls concurrently
    and assembles the results into a single dict.  Useful for the full-snapshot
    path in ``forge status --json``.

    Args:
        api_url:   Base URL of the Command Center API.
        api_token: Optional bearer token.
        timeout:   HTTP request timeout in seconds.

    Returns:
        Dict with keys: ``agents``, ``tasks``, ``nodes``, ``approvals``,
        ``health``, ``portfolio``, ``patterns``, ``active_leases``,
        ``timestamp``.
        Each value is empty / ``None`` on error.
    """
    import asyncio

    agents_coro = fetch_agents(api_url, api_token, timeout=timeout)
    tasks_coro = fetch_tasks(api_url, api_token, timeout=timeout)
    nodes_coro = fetch_fleet_status(api_url, api_token, timeout=timeout)
    approvals_coro = fetch_approvals(api_url, api_token, timeout=timeout)
    health_coro = fetch_health(api_url, api_token, timeout=timeout)
    portfolio_coro = fetch_portfolio(api_url, api_token, timeout=timeout)
    patterns_coro = fetch_patterns(api_url, api_token, timeout=timeout)
    leases_coro = fetch_active_leases(api_url, api_token, timeout=timeout)

    (
        agents,
        tasks,
        nodes,
        approvals,
        health,
        portfolio,
        patterns,
        active_leases,
    ) = await asyncio.gather(
        agents_coro,
        tasks_coro,
        nodes_coro,
        approvals_coro,
        health_coro,
        portfolio_coro,
        patterns_coro,
        leases_coro,
        return_exceptions=False,
    )

    return {
        "agents": agents,
        "tasks": tasks,
        "nodes": nodes,
        "approvals": approvals,
        "health": health,
        "portfolio": portfolio,
        "patterns": patterns,
        "active_leases": active_leases,
        "timestamp": datetime.now(UTC).isoformat(),
    }
