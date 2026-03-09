"""Node Management API Endpoints

REST API for multi-node fleet management.
Matches frontend contract in command_center/src/api/client.ts (CC-P4-010).

Endpoints:
    GET  /api/nodes              - List all nodes
    GET  /api/nodes/health       - Fleet health summary
    GET  /api/nodes/recommend    - Recommend node for task type
    GET  /api/nodes/{node_id}    - Get node details
    POST /api/nodes/{node_id}/dispatch - Dispatch task to node
    POST /api/nodes/sync         - Sync state across nodes
"""

import json
import os
import platform
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from forge_harness.logging_config import get_logger
from forge_harness.webhook_server.core.dependencies import require_permission
from forge_harness.webhook_server.models.sse_events import SSEEventType
from forge_harness.webhook_server.services.event_emitter import get_event_emitter
from forge_harness.webhook_server.services.heartbeat_store import (
    get_heartbeat_store,
)
from forge_harness.webhook_server.services.path_lock_registry import (
    get_path_lock_registry,
)
from forge_harness.webhook_server.services.scheduler_policy import (
    SchedulerRecommendation,
    get_scheduler_policy,
)

logger = get_logger(__name__)

router = APIRouter()


# =============================================================================
# Request/Response Models (match frontend TypeScript interfaces)
# =============================================================================


class NodeDispatchRequest(BaseModel):
    agent_type: str
    task: str
    priority: str = "normal"
    project: str | None = None
    domain: str | None = None
    timeout_minutes: int | None = None


# =============================================================================
# Node Discovery (from tmux + system info)
# =============================================================================


def _get_system_specs() -> dict[str, Any]:
    """Get current machine specs."""

    cpu_info = platform.processor() or platform.machine()
    try:
        cores = os.cpu_count() or 1
    except Exception:
        cores = 1

    try:
        mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        ram_gb = round(mem / (1024**3), 1)
    except (ValueError, OSError, AttributeError):
        ram_gb = 0

    return {
        "cpu": cpu_info,
        "ram_gb": ram_gb,
        "os": f"{platform.system()} {platform.release()}",
        "cores": cores,
    }


def _get_capabilities() -> dict[str, bool]:
    """Detect available agent capabilities on this node."""
    import shutil

    def _has(cmd: str) -> bool:
        return shutil.which(cmd) is not None

    return {
        "claude": _has("claude"),
        "codex": _has("codex"),
        "gemini": _has("gemini"),
        "kimi": _has("kimi"),
        "opencode": _has("opencode"),
        "ollama": _has("ollama"),
        "docker": _has("docker"),
        "ios_simulator": _has("xcrun"),
        "gpu": False,  # Would need nvidia-smi or similar
    }


def _get_system_load() -> tuple[float, float, float]:
    """Get CPU load, RAM usage, disk usage as percentages."""
    cpu_load = 0.0
    ram_usage = 0.0
    disk_usage = 0.0

    try:
        load_avg = os.getloadavg()
        cores = os.cpu_count() or 1
        cpu_load = min(round((load_avg[0] / cores) * 100, 1), 100.0)
    except (OSError, AttributeError):
        pass

    try:
        import shutil

        total, used, free = shutil.disk_usage("/")
        disk_usage = round((used / total) * 100, 1)
    except Exception:
        pass

    # Try /proc/meminfo first (Linux), then vm_stat (macOS)
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]  # value in kB
                    try:
                        mem[key] = int(val)
                    except ValueError:
                        pass
            total = mem.get("MemTotal", 0)
            avail = mem.get("MemAvailable", 0)
            if total > 0:
                ram_usage = round(((total - avail) / total) * 100, 1)
    except FileNotFoundError:
        # macOS — use vm_stat
        try:
            result = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                stats = {}
                for line in lines[1:]:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().rstrip(".")
                        try:
                            stats[key] = int(val)
                        except ValueError:
                            pass
                free_pages = stats.get("Pages free", 0) + stats.get("Pages inactive", 0)
                total_pages = sum(stats.values()) if stats else 1
                if total_pages > 0:
                    ram_usage = round((1 - free_pages / total_pages) * 100, 1)
        except Exception:
            pass
    except Exception:
        pass

    return cpu_load, ram_usage, disk_usage


def _get_agents_from_tmux() -> list[dict[str, Any]]:
    """Get agent info from tmux forge session."""
    agents = []
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", "forge", "-F", "#{window_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return agents

        hostname = platform.node().split(".")[0]
        for window in result.stdout.strip().split("\n"):
            if not window or window == hostname or window == "heartbeat":
                continue

            # Get health if available
            health_status = "active"
            context_pct = 0
            try:
                import json

                hr = subprocess.run(
                    ["forge", "health", f"forge:{window}", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if hr.returncode == 0:
                    hdata = json.loads(hr.stdout.strip())
                    # Extract data from JSON output structure
                    if isinstance(hdata, dict) and "data" in hdata:
                        data = hdata.get("data", {})
                    else:
                        data = hdata
                    status_map = {
                        "HEALTHY": "active",
                        "IDLE": "idle",
                        "WARNING": "active",
                        "EXHAUSTED": "failed",
                        "FAILED": "failed",
                        "UNKNOWN": "active",
                    }
                    health_status = status_map.get(data.get("status", ""), "active")
                    context_pct = max(data.get("context_pct", 0), 0)
            except Exception:
                pass

            agents.append(
                {
                    "id": f"forge-{window}",
                    "session_id": f"forge:{window}",
                    "agent_id": f"forge:{window}",
                    "name": window,
                    "role": "developer",
                    "status": health_status,
                    "project": "",
                    "task": "",
                    "progress": 0,
                    "context_percentage": context_pct,
                }
            )
    except Exception as e:
        logger.warning(f"Failed to get agents from tmux: {e}")

    return agents


def _build_node_response(node_id: str | None = None) -> dict[str, Any]:
    """Build NodeStatusApiResponse for the current machine."""
    hostname = platform.node().split(".")[0]
    nid = node_id or hostname
    agents = _get_agents_from_tmux()
    cpu_load, ram_usage, disk_usage = _get_system_load()

    # Determine node status
    if not agents:
        status = "offline"
    elif any(a["status"] == "failed" for a in agents):
        status = "degraded"
    else:
        status = "online"

    return {
        "node_id": nid,
        "name": hostname,
        "status": status,
        "agent_count": len(agents),
        "cpu_load": cpu_load,
        "ram_usage": ram_usage,
        "last_heartbeat": datetime.now(UTC).isoformat(),
        "latency_ms": 0,
        "agents": agents,
        "load_average": cpu_load,
        "disk_usage": disk_usage,
        "alerts": [],
        "capabilities": _get_capabilities(),
        "specs": _get_system_specs(),
    }


# =============================================================================
# Fleet-Aware Helpers (CP-2005 / CP-2004)
# =============================================================================

# forge_root is 4 levels up from this file:
# nodes.py -> api/ -> webhook_server/ -> forge_harness/ -> harness/ -> FORGE/
_FORGE_ROOT: Path = Path(__file__).resolve().parents[4]
_HEARTBEAT_NODES_DIR: Path = _FORGE_ROOT / ".forge" / "heartbeat" / "nodes"
_STALE_THRESHOLD_SECONDS: int = 120


def _read_heartbeat_files() -> list[dict[str, Any]]:
    """Read all node heartbeat JSON files from .forge/heartbeat/nodes/.

    Returns a list of raw heartbeat dicts, each guaranteed to have at least
    ``node_id`` and ``received_at``.
    """
    nodes: list[dict[str, Any]] = []
    if not _HEARTBEAT_NODES_DIR.is_dir():
        return nodes

    for path in _HEARTBEAT_NODES_DIR.glob("*.json"):
        try:
            raw = path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw)
            if "node_id" not in data:
                continue
            # Accept files without received_at — use timestamp or file mtime
            if "received_at" not in data:
                if "timestamp" in data:
                    data["received_at"] = data["timestamp"]
                else:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                    data["received_at"] = mtime.isoformat()
            nodes.append(data)
        except Exception as exc:
            logger.warning("Failed to parse heartbeat file %s: %s", path, exc)

    return nodes


def _age_seconds(received_at_str: str) -> float:
    """Return age in seconds since ``received_at`` ISO timestamp."""
    try:
        received_at = datetime.fromisoformat(received_at_str)
        # Make timezone-aware if naive
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return max((now - received_at).total_seconds(), 0.0)
    except Exception:
        return float("inf")


def _build_remote_node_entry(hb: dict[str, Any]) -> dict[str, Any]:
    """Convert a heartbeat dict into a node response entry for a remote node.

    Supports the current CLI field names sent by ``forge nodes heartbeat``:
      - resources.cpu_usage_percent  → cpu_load
      - resources.ram_available_mb + resources.ram_total_mb → ram_usage (%)
      - resources.disk_free_gb       → disk_usage (inverted to % used)

    Also accepts legacy field names for backward compatibility with older
    heartbeat files that may still be on disk:
      - resources.cpu_load / resources.cpu_percent
      - resources.ram_usage / resources.memory_percent
      - resources.disk_usage
    """
    received_at = hb.get("received_at", "")
    age = _age_seconds(received_at)
    is_fresh = age <= _STALE_THRESHOLD_SECONDS

    resources: dict[str, Any] = hb.get("resources", {})

    # --- CPU ---
    # Prefer the field emitted by the current CLI (cpu_usage_percent),
    # fall back to legacy names for older heartbeat files.
    if "cpu_usage_percent" in resources:
        cpu_load = float(resources["cpu_usage_percent"])
    else:
        cpu_load = float(resources.get("cpu_load", resources.get("cpu_percent", 0)))

    # --- RAM ---
    # Current CLI sends ram_available_mb + ram_total_mb; derive usage %.
    # Fall back to legacy ram_usage / memory_percent for older files.
    if "ram_available_mb" in resources and "ram_total_mb" in resources:
        ram_total_mb = float(resources["ram_total_mb"])
        ram_avail_mb = float(resources["ram_available_mb"])
        if ram_total_mb > 0:
            ram_usage = round((1.0 - ram_avail_mb / ram_total_mb) * 100.0, 1)
        else:
            ram_usage = 0.0
    else:
        ram_usage = float(resources.get("ram_usage", resources.get("memory_percent", 0)))

    # --- Disk ---
    # Current CLI sends disk_free_gb (absolute free GB).
    # We don't know total disk from the heartbeat alone, so we surface it as
    # a best-effort percentage only when legacy disk_usage (%) is present;
    # otherwise we leave it as 0 so the UI shows an honest unknown.
    if "disk_usage" in resources:
        disk_usage = float(resources["disk_usage"])
    else:
        # disk_free_gb available but no total — cannot convert to %; store 0.
        disk_usage = 0.0

    if is_fresh:
        status = "online"
    else:
        status = "stale"

    # --- Agents from heartbeat ---
    # Nodes now push agent inventory via the "agents" field in heartbeat.
    hb_agents = hb.get("agents", [])
    if not isinstance(hb_agents, list):
        hb_agents = []
    agents_list = [
        {
            "id": f"forge-{a.get('name', 'unknown')}",
            "session_id": f"forge:{a.get('name', 'unknown')}",
            "agent_id": f"forge:{a.get('name', 'unknown')}",
            "name": a.get("name", "unknown"),
            "role": "developer",
            "status": a.get("status", "active"),
            "project": "",
            "task": "",
            "progress": 0,
            "context_percentage": a.get("context_percentage", 0),
        }
        for a in hb_agents
        if isinstance(a, dict) and a.get("name")
    ]

    return {
        "node_id": hb.get("node_id", "unknown"),
        "name": hb.get("node_id", "unknown"),
        "status": status,
        "agent_count": len(agents_list),
        "cpu_load": cpu_load,
        "ram_usage": ram_usage,
        "last_heartbeat": hb.get("timestamp", received_at),
        "latency_ms": 0,
        "agents": agents_list,
        "load_average": cpu_load,
        "disk_usage": disk_usage,
        "disk_free_gb": float(resources.get("disk_free_gb", 0)),
        "alerts": [],
        "capabilities": {cap: True for cap in hb.get("capabilities", [])},
        "specs": {},
        "current_task": hb.get("current_task", ""),
        # Fleet-awareness metadata
        "is_fresh": is_fresh,
        "age_seconds": round(age, 1),
        "received_at": received_at,
    }


def _get_all_nodes() -> list[dict[str, Any]]:
    """Return a merged list of all known nodes (local + remote heartbeat files).

    The local node is always first in the list.  Remote nodes discovered via
    heartbeat files follow, de-duplicated against the local node's ID.
    """
    local = _build_node_response()
    local_id = local["node_id"]
    # Annotate local node with freshness fields
    local["is_fresh"] = True
    local["age_seconds"] = 0.0
    local["received_at"] = local["last_heartbeat"]

    result: list[dict[str, Any]] = [local]

    for hb in _read_heartbeat_files():
        if hb.get("node_id") == local_id:
            # Skip if heartbeat file describes the local node
            continue
        result.append(_build_remote_node_entry(hb))

    return result


# =============================================================================
# Scheduler Scoring (CP-2004)
# =============================================================================


def _score_node(node: dict[str, Any]) -> tuple[float, str]:
    """Return (score, reason) for a candidate node.

    Higher score = better candidate.  Scoring formula:
    - CPU component: (100 - cpu_load) / 100  → 0.0 … 1.0
    - Memory component: (100 - ram_usage) / 100 → 0.0 … 1.0
    - Combined: 0.5 * cpu + 0.5 * ram
    """
    cpu_load = float(node.get("cpu_load", 0))
    ram_usage = float(node.get("ram_usage", 0))

    cpu_score = (100.0 - min(cpu_load, 100.0)) / 100.0
    ram_score = (100.0 - min(ram_usage, 100.0)) / 100.0
    combined = round(0.5 * cpu_score + 0.5 * ram_score, 4)

    reasons: list[str] = []
    if cpu_load < 30:
        reasons.append(f"low CPU ({cpu_load}%)")
    elif cpu_load < 70:
        reasons.append(f"moderate CPU ({cpu_load}%)")
    else:
        reasons.append(f"high CPU ({cpu_load}%)")

    if ram_usage < 50:
        reasons.append(f"ample memory ({100 - ram_usage:.0f}% free)")
    else:
        reasons.append(f"limited memory ({100 - ram_usage:.0f}% free)")

    return combined, "; ".join(reasons)


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/api/nodes/heartbeat")
async def publish_node_heartbeat(payload: dict[str, Any], request: Request) -> JSONResponse:
    """Persist node telemetry heartbeat for cross-node fleet visibility.

    Expected payload shape is the current ``forge nodes heartbeat`` telemetry
    contract, which must include ``node_id``.

    Also records to HeartbeatStore for scheduler policy integration (CP-2004/CP-2005).
    """
    raw_node_id = str(payload.get("node_id", "")).strip()
    if not raw_node_id:
        raise HTTPException(status_code=422, detail="Field 'node_id' is required")

    node_id = raw_node_id.lower().replace("_", "-").replace(" ", "-")
    received_at = datetime.now(UTC).isoformat()

    heartbeat = dict(payload)
    heartbeat["node_id"] = node_id
    heartbeat["received_at"] = received_at
    if (
        not isinstance(heartbeat.get("timestamp"), str)
        or not str(heartbeat.get("timestamp", "")).strip()
    ):
        heartbeat["timestamp"] = received_at

    try:
        _HEARTBEAT_NODES_DIR.mkdir(parents=True, exist_ok=True)
        path = _HEARTBEAT_NODES_DIR / f"{node_id}.json"
        tmp = _HEARTBEAT_NODES_DIR / f"{node_id}.json.tmp"
        tmp.write_text(json.dumps(heartbeat, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.error("Failed to persist node heartbeat for %s: %s", node_id, exc)
        raise HTTPException(status_code=500, detail="Failed to persist node heartbeat") from exc

    # Emit SSE event via EventEmitter (for ring buffer / polling API)
    try:
        emitter = get_event_emitter()
        emitter.emit(SSEEventType.fleet_heartbeat, heartbeat, source="nodes-api")
    except Exception as e:
        logger.warning(f"Failed to emit fleet.heartbeat to EventEmitter: {e}")

    # CP-2004/CP-2005: Record to HeartbeatStore for scheduler policy
    try:
        resources = heartbeat.get("resources", {})
        store = get_heartbeat_store()
        store.record(
            node_id=node_id,
            data={
                "hostname": heartbeat.get("hostname", node_id),
                "cpu_percent": float(resources.get("cpu_usage_percent", resources.get("cpu_load", 0))),
                "memory_percent": float(resources.get("ram_usage", resources.get("memory_percent", 0))),
                "active_agents": len(heartbeat.get("agents", [])) or heartbeat.get("active_agents", 0),
                "active_leases": heartbeat.get("active_leases", 0),
            },
        )
        logger.debug(f"Recorded heartbeat to HeartbeatStore for node {node_id}")
    except Exception as e:
        logger.warning(f"Failed to record heartbeat to HeartbeatStore: {e}")

    node_entry = _build_remote_node_entry(heartbeat)
    return JSONResponse(
        {
            "success": True,
            "node_id": node_id,
            "received_at": received_at,
            "node": node_entry,
        }
    )


@router.get("/api/nodes")
async def list_nodes(request: Request) -> JSONResponse:
    """List all known nodes (local + remote via heartbeat files).

    Each node entry includes ``is_fresh``, ``age_seconds``, and ``status``
    fields reflecting its freshness.  Remote nodes older than 120 s are marked
    ``status="stale"``.
    """
    nodes = _get_all_nodes()
    return JSONResponse({"nodes": nodes})


@router.get("/api/nodes/health")
async def fleet_health(request: Request) -> JSONResponse:
    """Get aggregated fleet health across all known nodes."""
    nodes = _get_all_nodes()

    online_count = 0
    degraded_count = 0
    offline_count = 0
    total_agents = 0

    for n in nodes:
        status = n.get("status", "unknown")
        total_agents += n.get("agent_count", 0)
        if status == "online":
            online_count += 1
        elif status == "degraded":
            degraded_count += 1
        else:
            # "offline", "stale", "unknown", or "maintenance" all count as offline
            offline_count += 1

    total_nodes = len(nodes)
    health_pct = round((online_count / total_nodes) * 100.0, 1) if total_nodes else 0.0

    node_summaries = [
        {
            "node_id": n["node_id"],
            "name": n["name"],
            "status": n["status"],
            "agent_count": n.get("agent_count", 0),
            "cpu_load": n.get("cpu_load", 0),
        }
        for n in nodes
    ]

    return JSONResponse(
        {
            "total_nodes": total_nodes,
            "online": online_count,
            "degraded": degraded_count,
            "offline": offline_count,
            "health_percentage": health_pct,
            "total_agents": total_agents,
            "nodes": node_summaries,
        }
    )


@router.get("/api/nodes/recommend")
async def recommend_node(
    task_type: str = "general",
    path_lock: str | None = None,
    exclude_nodes: str | None = None,
) -> JSONResponse:
    """Recommend best node for a task using the real SchedulerPolicy (CP-2004/CP-2005).

    Uses the SchedulerPolicy singleton which scores nodes based on:
    - Availability (40%): CPU/memory headroom
    - Capacity (30%): Available agent slots
    - Affinity (20%): Task-type capability match
    - Path safety (10%): No conflicting path locks

    Query params:
        task_type:      Hint for capability filtering (e.g. "python", "ios").
        path_lock:      Path to check for lock conflicts before recommending.
        exclude_nodes:  Comma-separated list of node IDs to exclude from candidates.
    """
    # DF-0102: Check path_lock conflicts before recommending
    locked_paths: list[dict[str, Any]] = []
    if path_lock:
        registry = get_path_lock_registry()
        conflicts = registry.check_conflicts(path_lock)
        if conflicts:
            # Path is locked - gather conflict details
            for conflict in conflicts:
                locked_paths.append(
                    {
                        "path": conflict.path,
                        "agent_id": conflict.agent_id,
                        "lock_id": conflict.lock_id,
                        "reason": conflict.conflict_reason,
                    }
                )
            # Return conflict response instead of recommendation
            local = _build_node_response()
            return JSONResponse(
                {
                    "recommended_node_id": None,
                    "node_name": None,
                    "reason": f"Path '{path_lock}' is locked by another node",
                    "current_load": None,
                    "estimated_wait_seconds": None,
                    "score": None,
                    "task_type": task_type,
                    "path_lock": path_lock,
                    "locked_paths": locked_paths,
                    "candidates": [],
                    "conflict_detected": True,
                },
                status_code=409,  # Conflict
            )

    # Parse exclude_nodes
    excluded: list[str] = []
    if exclude_nodes:
        excluded = [n.strip() for n in exclude_nodes.split(",") if n.strip()]

    # CP-2004/CP-2005: Use real SchedulerPolicy
    try:
        # Sync heartbeat files to HeartbeatStore first
        _sync_heartbeat_files_to_store()

        policy = get_scheduler_policy()
        recommendations = policy.recommend(
            task_type=task_type,
            path_lock=path_lock,
            exclude_nodes=excluded if excluded else None,
        )

        if not recommendations:
            # Fallback: return local node
            local = _build_node_response()
            return JSONResponse(
                {
                    "recommended_node_id": local["node_id"],
                    "node_name": local["name"],
                    "reason": "No candidates from scheduler policy; falling back to local",
                    "current_load": local["cpu_load"],
                    "estimated_wait_seconds": 0,
                    "score": 0.0,
                    "task_type": task_type,
                    "path_lock": path_lock,
                    "candidates": [],
                    "locked_paths": locked_paths,
                    "conflict_detected": bool(locked_paths),
                }
            )

        # Build response from top recommendation
        best: SchedulerRecommendation = recommendations[0]

        candidate_list = [
            {
                "node_id": r.node_id,
                "node_name": r.node_id,  # node_id is the name in current schema
                "score": r.score,
                "reason": r.reason,
                "available_agents": r.available_agents,
                "path_conflicts": r.path_conflicts,
            }
            for r in recommendations
        ]

        return JSONResponse(
            {
                "recommended_node_id": best.node_id,
                "node_name": best.node_id,
                "reason": best.reason,
                "current_load": None,  # Not tracked per-node in SchedulerRecommendation
                "estimated_wait_seconds": 0,
                "score": best.score,
                "task_type": task_type,
                "path_lock": path_lock,
                "candidates": candidate_list,
                "locked_paths": locked_paths,
                "conflict_detected": bool(locked_paths),
            }
        )

    except Exception as e:
        logger.error(f"SchedulerPolicy error, falling back to simple scoring: {e}")
        # Fallback to simple scoring
        all_nodes = _get_all_nodes()
        candidates = [n for n in all_nodes if n.get("is_fresh", True) and n["node_id"] not in excluded]
        if not candidates:
            candidates = [all_nodes[0]] if all_nodes else []

        if not candidates:
            local = _build_node_response()
            return JSONResponse(
                {
                    "recommended_node_id": local["node_id"],
                    "node_name": local["name"],
                    "reason": "No candidate nodes available; falling back to local",
                    "current_load": local["cpu_load"],
                    "estimated_wait_seconds": 0,
                    "candidates": [],
                    "locked_paths": locked_paths,
                    "conflict_detected": bool(locked_paths),
                }
            )

        scored: list[tuple[float, dict[str, Any], str]] = []
        for node in candidates:
            score, reason = _score_node(node)
            scored.append((score, node, reason))

        scored.sort(key=lambda t: t[0], reverse=True)
        best_score, best_node, best_reason = scored[0]

        candidate_list = [
            {
                "node_id": c_node["node_id"],
                "node_name": c_node["name"],
                "score": c_score,
                "reason": c_reason,
                "cpu_load": c_node.get("cpu_load", 0),
                "ram_usage": c_node.get("ram_usage", 0),
            }
            for c_score, c_node, c_reason in scored
        ]

        return JSONResponse(
            {
                "recommended_node_id": best_node["node_id"],
                "node_name": best_node["name"],
                "reason": best_reason,
                "current_load": best_node.get("cpu_load", 0),
                "estimated_wait_seconds": 0,
                "score": best_score,
                "task_type": task_type,
                "path_lock": path_lock,
                "candidates": candidate_list,
                "locked_paths": locked_paths,
                "conflict_detected": bool(locked_paths),
            }
        )


def _sync_heartbeat_files_to_store() -> None:
    """Sync heartbeat files from .forge/heartbeat/nodes/ to HeartbeatStore.

    This ensures the SchedulerPolicy has access to remote node telemetry.
    Called by recommend_node before querying the policy.
    """
    try:
        store = get_heartbeat_store()
        for hb in _read_heartbeat_files():
            node_id = hb.get("node_id")
            if not node_id:
                continue
            resources = hb.get("resources", {})
            # Only sync if not already in store (avoid overwriting fresher data)
            existing = store.get(node_id)
            if existing and existing.is_fresh():
                continue
            store.record(
                node_id=node_id,
                data={
                    "hostname": hb.get("hostname", node_id),
                    "cpu_percent": float(resources.get("cpu_usage_percent", resources.get("cpu_load", 0))),
                    "memory_percent": float(resources.get("ram_usage", resources.get("memory_percent", 0))),
                    "active_agents": hb.get("active_agents", 0),
                    "active_leases": hb.get("active_leases", 0),
                },
            )
    except Exception as e:
        logger.warning(f"Failed to sync heartbeat files to store: {e}")


@router.get("/api/nodes/openclaw")
async def openclaw_status() -> JSONResponse:
    """Return OpenClaw gateway status for all known nodes.

    For the local node:
    - Checks whether the ``openclaw`` binary is reachable via ``openclaw --version``
    - Tests a TCP socket connection to the OpenClaw gateway port (default 18789,
      overridable via ``~/.openclaw/openclaw.json`` → ``port`` key)

    For remote nodes discovered via heartbeat files:
    - Checks whether ``openclaw`` appears in the node's ``capabilities`` list/dict
    - Uses heartbeat freshness to derive ``connection_status``

    Response shape::

        {
          "nodes": [
            {
              "node_id": "prya",
              "gateway_status": "running",
              "openclaw_version": "0.5.2",
              "connection_status": "connected",
              "last_seen": "<ISO-8601>"
            }
          ],
          "generated_at": "<ISO-8601>"
        }

    No authentication required (informational endpoint).
    """
    import socket

    nodes_out: list[dict[str, Any]] = []
    now_iso = datetime.now(UTC).isoformat()

    # -------------------------------------------------------------------------
    # Local node
    # -------------------------------------------------------------------------
    local_hostname = platform.node().split(".")[0]

    # 1. Get openclaw version via subprocess
    openclaw_version = "unknown"
    try:
        result = subprocess.run(
            ["openclaw", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Version is typically on stdout, strip whitespace
            raw = (result.stdout.strip() or result.stderr.strip()).splitlines()
            openclaw_version = raw[0] if raw else "unknown"
    except FileNotFoundError:
        openclaw_version = "unknown"
    except Exception as exc:
        logger.debug("openclaw --version error on local node: %s", exc)
        openclaw_version = "unknown"

    # 2. Read gateway port from ~/.openclaw/openclaw.json
    gateway_port = 18789
    try:
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        if config_path.is_file():
            import json as _json

            cfg = _json.loads(config_path.read_text(encoding="utf-8"))
            gateway_port = int(cfg.get("port", 18789))
    except Exception as exc:
        logger.debug("Cannot read openclaw config: %s", exc)

    # 3. Test TCP connection to gateway
    gateway_status = "unknown"
    try:
        with socket.create_connection(("127.0.0.1", gateway_port), timeout=2):
            gateway_status = "running"
    except OSError:
        gateway_status = "stopped"
    except Exception as exc:
        logger.debug("Socket check for openclaw gateway failed: %s", exc)
        gateway_status = "unknown"

    connection_status = "connected" if gateway_status == "running" else "disconnected"

    nodes_out.append(
        {
            "node_id": local_hostname,
            "gateway_status": gateway_status,
            "openclaw_version": openclaw_version,
            "connection_status": connection_status,
            "last_seen": now_iso,
        }
    )

    # -------------------------------------------------------------------------
    # Remote nodes (from heartbeat files)
    # -------------------------------------------------------------------------
    try:
        heartbeats = _read_heartbeat_files()
        for hb in heartbeats:
            remote_id = hb.get("node_id", "unknown")
            if remote_id == local_hostname:
                continue  # already included above

            # Check capabilities: may be a list or a dict of {name: bool}
            caps = hb.get("capabilities", [])
            has_openclaw = False
            if isinstance(caps, dict):
                has_openclaw = bool(caps.get("openclaw", False))
            elif isinstance(caps, list):
                has_openclaw = "openclaw" in caps

            received_at = hb.get("received_at", hb.get("timestamp", ""))
            age = _age_seconds(received_at) if received_at else float("inf")
            is_fresh = age <= _STALE_THRESHOLD_SECONDS

            remote_connection = "connected" if (has_openclaw and is_fresh) else "disconnected"

            nodes_out.append(
                {
                    "node_id": remote_id,
                    "gateway_status": "unknown",
                    "openclaw_version": "unknown",
                    "connection_status": remote_connection,
                    "last_seen": received_at or now_iso,
                }
            )
    except Exception as exc:
        logger.warning("Failed to read remote heartbeats for openclaw status: %s", exc)

    return JSONResponse(
        {
            "nodes": nodes_out,
            "generated_at": now_iso,
        }
    )


@router.get("/api/nodes/{node_id}")
async def get_node(node_id: str) -> JSONResponse:
    """Get node details by ID."""
    hostname = platform.node().split(".")[0]
    if node_id != hostname:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    return JSONResponse(_build_node_response(node_id))


@router.post("/api/nodes/{node_id}/dispatch")
async def dispatch_to_node(node_id: str, body: NodeDispatchRequest) -> JSONResponse:
    """Dispatch a task to a specific node with ACK tracking (CP-2006).

    Uses `forge dispatch send` for reliable dispatch. Returns dispatch receipt
    with ACK confirmation. Failed dispatches are queued for retry.
    """
    dispatch_id = str(uuid.uuid4())[:8]
    dispatch_ts = datetime.now(UTC).isoformat()

    # Use forge dispatch send (agent-message.sh has been deleted)
    forge_root = os.environ.get("FORGE_ROOT", ".")
    script = shutil.which("forge") or os.path.join(forge_root, "harness/.venv/bin/forge")

    target = f"forge:{body.agent_type}"
    success = False
    message = ""
    ack_received = False
    queued_for_retry = False

    if os.path.isfile(script) or shutil.which("forge"):
        try:
            result = subprocess.run(
                ["forge", "dispatch", "send", target, body.task],
                capture_output=True,
                text=True,
                timeout=15,
            )
            success = result.returncode == 0
            # Check for ACK in output
            output = result.stdout + result.stderr
            ack_received = "dispatched" in output.lower() or "sent" in output.lower() or success

            if success:
                message = "Task dispatched with ACK"
            else:
                message = f"Dispatch failed: {result.stderr[:200]}"
                # Queue for retry on failure (CP-2006)
                queued_for_retry = _queue_dispatch_for_retry(
                    dispatch_id=dispatch_id,
                    node_id=node_id,
                    target=target,
                    task=body.task,
                    priority=body.priority,
                )
                if queued_for_retry:
                    message += " (queued for retry)"

        except subprocess.TimeoutExpired:
            message = "Dispatch timed out after 15s"
            queued_for_retry = _queue_dispatch_for_retry(
                dispatch_id=dispatch_id,
                node_id=node_id,
                target=target,
                task=body.task,
                priority=body.priority,
            )
            if queued_for_retry:
                message += " (queued for retry)"
        except Exception as e:
            message = f"Dispatch error: {e}"
            queued_for_retry = _queue_dispatch_for_retry(
                dispatch_id=dispatch_id,
                node_id=node_id,
                target=target,
                task=body.task,
                priority=body.priority,
            )
            if queued_for_retry:
                message += " (queued for retry)"
    else:
        message = "Dispatch script not found"

    # Emit dispatch event for observability
    try:
        emitter = get_event_emitter()
        emitter.emit(
            SSEEventType.dispatch,
            {
                "dispatch_id": dispatch_id,
                "node_id": node_id,
                "agent_id": target,
                "status": "dispatched" if success else "failed",
                "ack_received": ack_received,
                "queued_for_retry": queued_for_retry,
                "timestamp": dispatch_ts,
            },
            source="nodes-api",
        )
    except Exception as e:
        logger.warning(f"Failed to emit dispatch event: {e}")

    return JSONResponse(
        {
            "success": success,
            "dispatch_id": dispatch_id,
            "node_id": node_id,
            "agent_id": target,
            "status": "dispatched" if success else "queued" if queued_for_retry else "failed",
            "message": message,
            "ack_received": ack_received,
            "queued_for_retry": queued_for_retry,
            "timestamp": dispatch_ts,
        },
        status_code=200 if success else 202 if queued_for_retry else 500,
    )


# Dispatch retry queue (CP-2006)
_DISPATCH_QUEUE_DIR: Path = _FORGE_ROOT / ".forge" / "dispatch_queue"


def _queue_dispatch_for_retry(
    dispatch_id: str,
    node_id: str,
    target: str,
    task: str,
    priority: str = "normal",
) -> bool:
    """Queue a failed dispatch for retry (CP-2006).

    Writes a JSON file to .forge/dispatch_queue/ for the retry worker.

    Returns True if queued successfully.
    """
    try:
        _DISPATCH_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        queue_entry = {
            "dispatch_id": dispatch_id,
            "node_id": node_id,
            "target": target,
            "task": task,
            "priority": priority,
            "queued_at": datetime.now(UTC).isoformat(),
            "retry_count": 0,
            "status": "pending",
        }
        queue_file = _DISPATCH_QUEUE_DIR / f"{dispatch_id}.json"
        queue_file.write_text(json.dumps(queue_entry, indent=2), encoding="utf-8")
        logger.info(f"Queued dispatch {dispatch_id} for retry")
        return True
    except Exception as e:
        logger.error(f"Failed to queue dispatch for retry: {e}")
        return False


@router.post("/api/nodes/sync")
async def sync_nodes() -> JSONResponse:
    """Sync state across nodes. Single-node: no-op."""
    hostname = platform.node().split(".")[0]
    return JSONResponse(
        {
            "success": True,
            "synced_nodes": 1,
            "failed_nodes": 0,
            "sync_id": str(uuid.uuid4())[:8],
            "timestamp": datetime.now(UTC).isoformat(),
            "details": [
                {
                    "node_id": hostname,
                    "success": True,
                    "synced_at": datetime.now(UTC).isoformat(),
                }
            ],
        }
    )


@router.get("/api/node-auth/jwt-secret")
async def get_jwt_secret(
    user: dict = Depends(require_permission("admin")),
) -> JSONResponse:
    """Return current JWT secret (auth-protected, lead nodes only).

    This endpoint allows follower nodes to fetch the stable JWT secret
    during bootstrap so tokens survive CC restarts.
    """
    # Security: Distribution only allowed from lead nodes
    is_lead = os.environ.get("FORGE_IS_LEAD", "false").lower() == "true"
    if not is_lead:
        # Fallback check: if we are prya (hardcoded default lead), we are lead
        hostname = platform.node().split(".")[0]
        if hostname != "prya":
            raise HTTPException(
                status_code=403,
                detail="JWT secret distribution only allowed from lead nodes",
            )

    secret = os.environ.get("FORGE_JWT_SECRET")
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="FORGE_JWT_SECRET not configured on lead node",
        )

    return JSONResponse(
        {
            "secret": secret,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
