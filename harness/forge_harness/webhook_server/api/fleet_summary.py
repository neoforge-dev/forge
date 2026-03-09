"""Fleet Summary API Endpoint

Single-pane-of-glass endpoint that combines nodes, agents, tasks, and health
into one unified response for the CC Dashboard and `forge glance`.

Endpoints:
    GET /api/fleet/summary  - Unified fleet snapshot (nodes + agents + tasks + health)
    GET /api/fleet/agents   - Agent sidecar data from .forge/heartbeat/agents/*.json
"""

import json
import re
import shutil
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from forge_harness.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Path constants (mirrors nodes.py resolution: 4 parents up to FORGE root)
# ---------------------------------------------------------------------------

_FORGE_ROOT: Path = Path(__file__).resolve().parents[4]
_DISPATCHES_DIR: Path = _FORGE_ROOT / ".forge" / "dispatches"
_RESULTS_DIR: Path = _FORGE_ROOT / ".forge" / "heartbeat" / "results"
_FORGE_TASKS_DIR: Path = _FORGE_ROOT / ".forge/tasks"
_CONTEXT_DIR: Path = _FORGE_ROOT / ".forge" / "context"
_AGENTS_DIR: Path = _FORGE_ROOT / ".forge" / "heartbeat" / "agents"
_DOMAINS_YAML: Path = _FORGE_ROOT / "harness" / "forge_harness" / "domains.yaml"

# ---------------------------------------------------------------------------
# Staleness threshold (seconds)
# ---------------------------------------------------------------------------

_STALE_THRESHOLD_SECS: int = 30 * 60  # 30 minutes
_OVERLOADED_CONTEXT_PCT: int = 60

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_task_summary() -> dict[str, int]:
    """Build task priority breakdown by reading the task queue via TaskHandler.

    Falls back to direct .forge/tasks/ file scan if the handler is unavailable.
    Returns counts keyed by priority label (p0, p1, p2) plus total.
    """
    counts: dict[str, int] = {"total": 0, "p0": 0, "p1": 0, "p2": 0}

    priority_map = {
        "critical": "p0",
        "high": "p1",
        "medium": "p2",
        "low": "p2",
    }

    try:
        from forge_harness.webhook_server.handlers.task_handler import get_task_handler

        handler = get_task_handler()
        tasks = await handler.list_tasks(limit=500)

        counts["total"] = len(tasks)
        for task in tasks:
            prio = task.get("priority", "medium").lower()
            bucket = priority_map.get(prio, "p2")
            counts[bucket] += 1

        return counts

    except Exception as exc:
        logger.debug(
            "TaskHandler unavailable for fleet summary, falling back to file scan: %s", exc
        )

    # Fallback: scan .forge/tasks/ JSON files directly
    if not _FORGE_TASKS_DIR.is_dir():
        return counts

    for path in _FORGE_TASKS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            counts["total"] += 1
            prio = str(data.get("priority", "medium")).lower()
            bucket = priority_map.get(prio, "p2")
            counts[bucket] += 1
        except Exception as exc:
            logger.debug("Skipping task file %s: %s", path, exc)

    return counts


def _get_dispatch_summary() -> dict[str, int]:
    """Count active dispatch files and pending (unmatched) result files.

    Active dispatches: .forge/dispatches/*.md (excluding archive subdirectory)
    Pending results: .forge/heartbeat/results/*.md with no matching dispatch
        resolved (a simple proxy: count result files not in archive/).
    """
    active = 0
    if _DISPATCHES_DIR.is_dir():
        for p in _DISPATCHES_DIR.iterdir():
            # Only count .md files directly in dispatches/ (not in archive/)
            if p.is_file() and p.suffix == ".md":
                active += 1

    pending_results = 0
    if _RESULTS_DIR.is_dir():
        for p in _RESULTS_DIR.iterdir():
            if p.is_file() and p.suffix == ".md":
                pending_results += 1

    return {"active": active, "pending_results": pending_results}


def _get_health_summary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive top-level health metrics from the already-computed node list.

    Avoids re-querying system load — reuses data already fetched by
    _get_all_nodes() for the same request.
    """
    online_nodes = sum(1 for n in nodes if n.get("status") == "online")
    total_agents = sum(n.get("agent_count", 0) for n in nodes)

    # errors_1h: query the errors endpoint if available, else default to 0
    errors_1h = 0
    try:
        from forge_harness.webhook_server.services.event_emitter import get_event_emitter

        emitter = get_event_emitter()
        if hasattr(emitter, "get_error_count_last_hour"):
            errors_1h = emitter.get_error_count_last_hour()
    except Exception:
        pass

    # uptime_hours: read server start time if available, else 0
    uptime_hours = 0
    try:
        # The webhook server stores start time in app.state.server_start_time,
        # but we don't have access to the app object here.  Use the process
        # start time as a reasonable proxy.
        import psutil

        proc = psutil.Process()
        elapsed = time.time() - proc.create_time()
        uptime_hours = round(elapsed / 3600, 1)
    except Exception:
        pass

    return {
        "errors_1h": errors_1h,
        "uptime_hours": uptime_hours,
        "total_agents": total_agents,
        "online_nodes": online_nodes,
    }


# ---------------------------------------------------------------------------
# Domain summary helper (lightweight — no auth, called internally)
# ---------------------------------------------------------------------------

_RE_FS_BLOCKER = re.compile(r"blocker", re.IGNORECASE)
_RE_FS_NONE_BLOCKER = re.compile(r"^(none|none known)[.!]?\s*$", re.IGNORECASE)
_RE_FS_LEAD = re.compile(r"\*\*Lead:\*\*\s*(.+)")
_RE_FS_PRIORITIES_HEADING = re.compile(r"^##\s+Next\s+\d+\s+Priorities", re.IGNORECASE)
_RE_FS_PRIORITY_ITEM = re.compile(r"^\d+\.")


def _get_domain_summary() -> list[dict[str, Any]]:
    """Return a lightweight list of domain health summaries.

    Only includes ``{code, name, lead, blocker_count}`` — used by fleet_summary()
    to keep the response compact.  Wrapped in try/except; returns [] on failure.
    """
    try:
        from forge_harness.webhook_server.api.domains import DOMAIN_MAP

        result: list[dict[str, Any]] = []

        if not _CONTEXT_DIR.is_dir():
            return result

        for code, (name, _full_domain) in sorted(DOMAIN_MAP.items()):
            context_path = _CONTEXT_DIR / code / "lead-context.md"
            if not context_path.is_file():
                continue
            try:
                text = context_path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.debug("fleet_summary: cannot read %s: %s", context_path, exc)
                continue

            # Lead
            lead = "unassigned"
            m = _RE_FS_LEAD.search(text)
            if m:
                lead = m.group(1).strip()

            # Blocker count
            blocker_count = 0
            for line in text.splitlines():
                if _RE_FS_BLOCKER.search(line):
                    value_part = line.split(":", 1)[-1].strip() if ":" in line else line.strip()
                    if _RE_FS_NONE_BLOCKER.match(value_part):
                        continue
                    blocker_count += 1

            result.append(
                {
                    "code": code,
                    "name": name,
                    "lead": lead,
                    "blocker_count": blocker_count,
                }
            )

        return result
    except Exception as exc:
        logger.warning("fleet_summary: domain summary failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# OpenClaw status helper (local-only, no auth)
# ---------------------------------------------------------------------------

_OPENCLAW_DEFAULT_PORT = 18789


def _get_openclaw_status() -> dict[str, Any] | None:
    """Return a lightweight OpenClaw status dict for the local node.

    Checks:
    1. Whether the ``openclaw`` binary exists on PATH.
    2. Whether the gateway socket is reachable on port 18789 (default).

    Returns ``{"available": bool, "gateway_status": str}`` or None on failure.
    """
    try:
        available = shutil.which("openclaw") is not None

        gateway_status = "unknown"
        port = _OPENCLAW_DEFAULT_PORT
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                gateway_status = "running"
        except OSError:
            gateway_status = "stopped"
        except Exception as exc:
            logger.debug("fleet_summary: openclaw socket check failed: %s", exc)
            gateway_status = "unknown"

        return {"available": available, "gateway_status": gateway_status}
    except Exception as exc:
        logger.warning("fleet_summary: openclaw status check failed: %s", exc)
        return None


def _shape_node_for_summary(node: dict[str, Any]) -> dict[str, Any]:
    """Project a full node dict to the fleet summary shape.

    Input shape comes from nodes._build_node_response() /
    nodes._build_remote_node_entry().  Output shape matches the API contract:

        {
            "id":     str,
            "name":   str,
            "status": str,
            "cpu":    int,
            "ram":    int,
            "agents": [{"name": str, "status": str, "context_pct": int}]
        }
    """
    agents_raw: list[dict[str, Any]] = node.get("agents", [])
    agents: list[dict[str, Any]] = [
        {
            "name": a.get("name", "unknown"),
            "status": a.get("status", "active"),
            "context_pct": int(a.get("context_percentage", 0)),
        }
        for a in agents_raw
        if isinstance(a, dict)
    ]

    return {
        "id": node.get("node_id", "unknown"),
        "name": node.get("name", node.get("node_id", "unknown")),
        "status": node.get("status", "unknown"),
        "cpu": int(node.get("cpu_load", 0)),
        "ram": int(node.get("ram_usage", 0)),
        "agents": agents,
    }


# ---------------------------------------------------------------------------
# Agent sidecar helpers
# ---------------------------------------------------------------------------

_STATUS_ORDER: dict[str, int] = {
    "stale": 0,
    "overloaded": 1,
    "working": 2,
    "idle": 3,
}


def _classify_agent(data: dict[str, Any], staleness_secs: float) -> str:
    """Return the display status for an agent sidecar entry.

    Priority order: stale > overloaded > working > idle.
    The caller is responsible for computing ``staleness_secs`` from the file
    mtime (or last_heartbeat timestamp if available).
    """
    if staleness_secs >= _STALE_THRESHOLD_SECS:
        return "stale"
    if data.get("context_pct", 0) > _OVERLOADED_CONTEXT_PCT:
        return "overloaded"
    raw_status = str(data.get("status", "idle")).lower()
    if raw_status in ("working", "busy", "active"):
        return "working"
    return "idle"


def _read_fleet_agents() -> list[dict[str, Any]]:
    """Read all agent sidecar JSON files from .forge/heartbeat/agents/*.json.

    For each file, compute:
    - ``is_stale``      — file mtime older than 30 minutes
    - ``is_overloaded`` — context_pct > 60
    - ``staleness_min`` — minutes since last file modification (1 decimal)
    - ``display_status``— derived classification used for sorting

    Returns agents sorted: stale first, then overloaded, then working, then
    idle.  Within each bucket, agents are sorted alphabetically by name.
    """
    if not _AGENTS_DIR.is_dir():
        logger.debug("fleet agents: directory not found: %s", _AGENTS_DIR)
        return []

    now = time.time()
    agents: list[dict[str, Any]] = []

    for path in sorted(_AGENTS_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("fleet agents: cannot parse %s: %s", path, exc)
            continue

        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            logger.debug("fleet agents: cannot stat %s: %s", path, exc)
            mtime = now  # treat as fresh if stat fails

        staleness_secs = now - mtime
        staleness_min = round(staleness_secs / 60, 1)
        is_stale = staleness_secs >= _STALE_THRESHOLD_SECS
        is_overloaded = raw.get("context_pct", 0) > _OVERLOADED_CONTEXT_PCT
        display_status = _classify_agent(raw, staleness_secs)

        agents.append(
            {
                "agent": raw.get("agent_name") or raw.get("agent") or path.stem,
                "status": raw.get("status", "unknown"),
                "task_id": raw.get("task_id"),
                "dispatch_file": raw.get("dispatch_file"),
                "context_pct": raw.get("context_pct", 0),
                "last_heartbeat": raw.get("last_heartbeat"),
                "is_stale": is_stale,
                "is_overloaded": is_overloaded,
                "staleness_min": staleness_min,
                "display_status": display_status,
            }
        )

    # Sort: stale → overloaded → working → idle; alphabetical within bucket
    agents.sort(key=lambda a: (_STATUS_ORDER.get(a["display_status"], 99), a["agent"]))
    return agents


# ---------------------------------------------------------------------------
# Business data helper (reads raw business block from domains.yaml)
# ---------------------------------------------------------------------------


def _get_business_data() -> list[dict[str, Any]]:
    """Extract per-domain business data from domains.yaml.

    The ``business`` key is not yet modelled in DomainEntry dataclass, so we
    parse the YAML directly to avoid losing fields.  Returns a list of dicts::

        [
          {
            "domain": "codeswiftr-com",
            "display_name": "CodeSwiftr",
            "deploy_status": "live",
            "deploy_url": "https://app.codeswiftr.com",
            "deploy_platform": "railway+cloudflare",
            "current_mrr": 0,
            "target_mrr": 5000,
            "stripe_live": false,
            "human_actions_needed": ["Enable Stripe live mode (15 min)"]
          },
          ...
        ]

    Domains without a ``business`` block are omitted.  Returns [] on any error.
    """
    if not _DOMAINS_YAML.is_file():
        logger.warning("business data: domains.yaml not found at %s", _DOMAINS_YAML)
        return []

    try:
        raw = yaml.safe_load(_DOMAINS_YAML.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("business data: failed to parse domains.yaml: %s", exc)
        return []

    result: list[dict[str, Any]] = []
    for domain_key, domain_data in raw.get("domains", {}).items():
        biz = domain_data.get("business")
        if not biz:
            continue
        entry: dict[str, Any] = {
            "domain": domain_key,
            "display_name": domain_data.get("display_name", domain_key),
            "deploy_status": biz.get("deploy_status", "none"),
            "deploy_url": biz.get("deploy_url"),
            "deploy_platform": biz.get("deploy_platform"),
            "current_mrr": biz.get("current_mrr", 0),
            "target_mrr": biz.get("target_mrr", 0),
            "stripe_live": biz.get("stripe_live", False),
            "human_actions_needed": biz.get("human_actions_needed", []),
        }
        result.append(entry)

    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/fleet/agents")
async def fleet_agents() -> JSONResponse:
    """Return agent sidecar data from .forge/heartbeat/agents/*.json.

    Each sidecar file is read and enriched with computed fields.  Agents are
    sorted: stale first, then overloaded, then working, then idle.

    Response shape::

        {
          "agents": [
            {
              "agent": "minimax",
              "status": "working",
              "task_id": "IS-EXIT-INTENT",
              "dispatch_file": ".forge/dispatches/minimax-IS-EXIT-INTENT.md",
              "context_pct": 27,
              "last_heartbeat": "2026-03-01T18:30:00Z",
              "is_stale": false,
              "is_overloaded": false,
              "staleness_min": 4.2,
              "display_status": "working"
            }
          ],
          "total": 1,
          "generated_at": "<ISO-8601>"
        }

    No authentication required (same policy as /api/fleet/summary).
    """
    try:
        agents = _read_fleet_agents()
    except Exception as exc:
        logger.error("fleet_agents: unexpected error reading sidecars: %s", exc)
        agents = []

    return JSONResponse(
        {
            "agents": agents,
            "total": len(agents),
            "generated_at": datetime.now(UTC).isoformat(),
        }
    )


@router.get("/api/fleet/agents")
async def get_fleet_agents() -> JSONResponse:
    """Return all agent sidecar data from .forge/heartbeat/agents/*.json.

    This endpoint reads the new FSM agent sidecar files and returns a list
    of agent status objects, including staleness and context usage.
    """
    try:
        agents = _read_fleet_agents()
        return JSONResponse({"agents": agents})
    except Exception as exc:
        logger.error("Failed to fetch fleet agents: %s", exc)
        return JSONResponse({"agents": [], "error": str(exc)}, status_code=500)


@router.get("/api/fleet/summary")
async def fleet_summary() -> JSONResponse:
    """Return a unified fleet snapshot: nodes + agents + tasks + health.

    This is the "single pane of glass" endpoint consumed by the CC Dashboard
    and `forge glance`.  All data is collected on-demand; there is no cache.

    Response shape::

        {
          "nodes": [
            {
              "id": "prya",
              "name": "Prya",
              "status": "online",
              "cpu": 58,
              "ram": 62,
              "agents": [
                {"name": "minimax", "status": "idle", "context_pct": 15},
                {"name": "glm",     "status": "active", "context_pct": 42}
              ]
            }
          ],
          "task_summary":     {"total": 12, "p0": 1, "p1": 4, "p2": 7},
          "dispatch_summary": {"active": 2, "pending_results": 1},
          "health": {
            "errors_1h":    0,
            "uptime_hours": 72,
            "total_agents": 5,
            "online_nodes": 3
          },
          "fleet_agents": [
            {
              "agent": "minimax",
              "status": "working",
              "task_id": "IS-EXIT-INTENT",
              "dispatch_file": ".forge/dispatches/minimax-IS-EXIT-INTENT.md",
              "context_pct": 27,
              "last_heartbeat": "2026-03-01T18:30:00Z",
              "is_stale": false,
              "is_overloaded": false,
              "staleness_min": 4.2,
              "display_status": "working"
            }
          ],
          "business": [
            {
              "domain": "codeswiftr-com",
              "display_name": "CodeSwiftr",
              "deploy_status": "live",
              "deploy_url": "https://app.codeswiftr.com",
              "deploy_platform": "railway+cloudflare",
              "current_mrr": 0,
              "target_mrr": 5000,
              "stripe_live": false,
              "human_actions_needed": ["Enable Stripe live mode (15 min)"]
            }
          ],
          "generated_at": "<ISO-8601>"
        }
    """
    # 1. Nodes (reuses the existing fleet-aware helper from nodes.py)
    try:
        from forge_harness.webhook_server.api.nodes import _get_all_nodes

        raw_nodes = _get_all_nodes()
    except Exception as exc:
        logger.error("Failed to fetch nodes for fleet summary: %s", exc)
        raw_nodes = []

    shaped_nodes = [_shape_node_for_summary(n) for n in raw_nodes]

    # 2. Task summary
    try:
        task_summary = await _get_task_summary()
    except Exception as exc:
        logger.error("Failed to fetch task summary: %s", exc)
        task_summary = {"total": 0, "p0": 0, "p1": 0, "p2": 0}

    # 3. Dispatch summary
    try:
        dispatch_summary = _get_dispatch_summary()
    except Exception as exc:
        logger.error("Failed to fetch dispatch summary: %s", exc)
        dispatch_summary = {"active": 0, "pending_results": 0}

    # 4. Health (derived from node data already in memory)
    try:
        health = _get_health_summary(raw_nodes)
    except Exception as exc:
        logger.error("Failed to compute health summary: %s", exc)
        health = {"errors_1h": 0, "uptime_hours": 0, "total_agents": 0, "online_nodes": 0}

    # 5. Domain summary (additive — does not break existing response shape)
    try:
        domain_summary = _get_domain_summary()
    except Exception as exc:
        logger.error("Failed to fetch domain summary: %s", exc)
        domain_summary = []

    # 6. OpenClaw status (additive — does not break existing response shape)
    try:
        openclaw_status = _get_openclaw_status()
    except Exception as exc:
        logger.error("Failed to fetch openclaw status: %s", exc)
        openclaw_status = None

    return JSONResponse(
        {
            "nodes": shaped_nodes,
            "task_summary": task_summary,
            "dispatch_summary": dispatch_summary,
            "health": health,
            "domains": domain_summary,
            "openclaw": openclaw_status,
            "generated_at": datetime.now(UTC).isoformat(),
        }
    )
