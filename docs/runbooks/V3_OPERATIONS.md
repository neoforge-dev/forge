# FORGE V3 Operations Guide

**Status:** Phase 1 (Operational)  
**Last Updated:** 2026-03-03  
**Components:** Core Server (Go), WebSocket Hub, Task Queue, SQLite/PostgreSQL

---

## 1. System Overview

FORGE V3 is the high-performance core of the agentic fleet. It manages task distribution, agent registration, and cross-node communication (XNode).

### Core Components
| Component | Process Name | Default Port | Responsibility |
|-----------|--------------|--------------|----------------|
| **Core Server** | `forge-v3` | 8081 (API) | REST API, Task Management, State |
| **WS Hub** | `forge-v3` | 8082 (WS) | Real-time Agent Communication |
| **Database** | N/A | Local File | SQLite (`.forge/forge-v3.db`) or Postgres |

---

## 2. Monitoring & Observability

### 2.1 Health Check Endpoints
Always verify system health using these endpoints:

*   **Basic Health:** `GET http://localhost:8081/api/health`
    *   Expected: `{"status":"ok", "timestamp":"..."}`
*   **Detailed Health:** `GET http://localhost:8081/api/health/detailed`
    *   Provides status of SQLite, WebSocket Hub, and active worker counts.
*   **Status/Phase:** `GET http://localhost:8081/api/status`
    *   Expected: `{"version":"3.0.0", "phase":"0.5", "status":"running"}`

### 2.2 Metrics (Prometheus Style)
The server exposes metrics at `GET http://localhost:8081/metrics`.

**Key Metrics to Watch:**
*   `forge_requests_total`: Total HTTP traffic.
*   `forge_active_connections`: Current number of connected agents via WebSocket.
*   `forge_tasks_completed` / `forge_tasks_failed`: Task throughput and reliability.
*   `forge_response_time_avg`: Average latency (seconds).

### 2.3 Debugging Endpoint
For deep inspection of internal state:
*   **Debug Info:** `GET http://localhost:8081/debug`
    *   Returns uptime, worker IDs, and specific task counters.

### 2.4 TUI Logs
Live operational logs for the Terminal UI:
*   `GET http://localhost:8081/api/tui/logs?limit=50`

---

## 3. Alerting Thresholds (Recommended)

While V3 does not yet have a built-in alerting engine, external monitors (e.g., Prometheus/Grafana) should use these thresholds:

| Metric | Warning Threshold | Critical Threshold |
|--------|-------------------|--------------------|
| **HTTP 5xx Rate** | > 1% over 5m | > 5% over 1m |
| **Response Time** | > 200ms (avg) | > 1s (avg) |
| **WS Heartbeat** | > 30s delay | > 60s (Disconnect) |
| **Task Failure Rate** | > 10% | > 25% |
| **Context Exhaustion**| > 50% | > 80% (Mandatory Handoff) |

---

## 4. Standard Recovery Procedures

### 4.1 Server Won't Start (Port Conflict)
**Symptom:** `bind: address already in use` error on startup.
1. Identify the process: `lsof -i :8081`
2. Kill the stale process: `pkill forge-v3`
3. Restart: `cd cmd/forge-v3 && ./forge-v3 &`

### 4.2 WebSocket Disconnects (Stale Workers)
**Symptom:** Agents are running but not receiving tasks.
1. Check active workers: `curl http://localhost:8081/api/debug`
2. If the worker ID is missing, restart the agent process.
3. If multiple workers are missing, restart the V3 server.

### 4.3 Database Locked (SQLite)
**Symptom:** `database is locked` errors in logs.
1. Check for zombie processes holding the DB: `fuser .forge/forge-v3.db`
2. Kill offending processes.
3. Ensure `WAL` mode is enabled (default in V3).

### 4.4 Claims/Tasks Stalled
**Symptom:** Tasks are in `queued` status but not moving to `assigned`.
1. Verify workers are connected: `curl http://localhost:8081/api/health/detailed`
2. Check the claim orchestrator: `pgrep -f v3-claim-orchestrator`
3. Manual trigger: `./scripts/v3-claim-orchestrator.sh &`

---

## 5. Troubleshooting Guide

### 5.1 Connection Refused
*   **Check:** Is the server binary running? `ps aux | grep forge-v3`
*   **Check:** Is it listening on the right interface? (Default is `localhost` or `:8081`)
*   **Fix:** Restart the server using the provided Makefiles or start scripts.

### 5.2 404 Not Found on API
*   **Check:** Verify the endpoint prefix. All V3 APIs use `/api/`.
*   **Check:** `GET /api/status` to confirm you are talking to a V3 server and not a V2 leftover.

### 5.3 WebSocket Handshake Failure
*   **Check:** Ensure the client is hitting port `8082` (or the configured `WS_PORT`).
*   **Check:** Verify the `agent.register` message format matches ADR-011.

---

## 6. Maintenance Tasks

### 6.1 Database Backups
For SQLite, backup the `.db` file daily:
```bash
cp .forge/forge-v3.db .forge/backups/forge-v3-$(date +%F).db
```

### 6.2 Log Rotation
V3 logs to `/tmp/v3-*.log` by default in some configurations. Ensure these are cleaned or rotated to prevent disk exhaustion.

### 6.3 Parity Checks
Run periodic consistency checks between V2 and V3 state:
`GET http://localhost:8081/api/parity`
