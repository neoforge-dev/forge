# Heartbeat Operations

**Last Updated:** 2026-03-19 (S120)
**Audience:** Humans and agents managing FORGE fleet heartbeats

> **Note:** This runbook uses the v4 Go CLI (`forge`). For daemon operations, see `cmd/forged/`.

---

## Quick Reference

| Task | Command |
|------|---------|
| Check daemon health | `forge daemon status` |
| List registered agents | `forge agent list` |
| Send agent heartbeat | `forge agent heartbeat <agent-id>` |
| View patrol status | `forge patrol list` |
| Full system status | `forge status` |

---

## Daemon Health

The forged daemon (port :8081) handles all heartbeat processing.

```bash
# Check daemon is running
forge daemon status

# Expected output:
# Daemon is running on :8081 (PID: 12345)

# If not running, start it
forge daemon start
```

---

## Agent Heartbeats

Agents send heartbeats to the daemon every 60 seconds.

```bash
# List all registered agents
forge agent list

# Manually send heartbeat for an agent
forge agent heartbeat kimi

# Check agent health via patrol
forge patrol list | grep health
```

### Heartbeat Files

Agent heartbeats are stored in:
```
.forge/heartbeat/nodes/{node}.json
```

```bash
# View heartbeat data for a node
cat .forge/heartbeat/nodes/prya.json | jq .
```

---

## Patrol Monitoring

The daemon runs periodic patrols for health monitoring:

```bash
# List all patrols
forge patrol list

# Key patrols for heartbeat monitoring:
# - agent-health: Marks stale agents offline
# - dispatch-timeout: Flags unanswered dispatches
# - result-monitor: Auto-completes tasks from result files
```

---

## Troubleshooting

### Agent shows offline but is running

```bash
# Force heartbeat
forge agent heartbeat <agent-id>

# Check daemon connection
curl http://localhost:8081/health

# Verify agent is in registry
forge agent list | grep <agent-id>
```

### Daemon not responding

```bash
# Check if daemon process exists
ps aux | grep forged

# Restart daemon
forge daemon restart

# Check daemon logs
tail -50 /tmp/forged.log
```

### Stale heartbeat data

```bash
# Agent health patrol cleans up stale heartbeats every 120s
forge patrol list | grep agent-health

# Manual cleanup: delete stale heartbeat file
rm .forge/heartbeat/nodes/<stale-node>.json
```

---

## Architecture

```
┌─────────────┐     HTTP/WS      ┌─────────────┐
│   Agent     │ ───────────────▶ │   forged    │
│  (kimi)     │   heartbeat      │  (:8081)    │
└─────────────┘                  └─────────────┘
                                       │
                                       ▼
                                 ┌─────────────┐
                                 │   SQLite    │
                                 │ forge-v3.db │
                                 └─────────────┘
```

- **Heartbeat interval:** 60 seconds
- **Stale threshold:** 180 seconds (3 missed heartbeats)
- **Cleanup patrol:** `agent-health` runs every 120 seconds

---

## Reference

- Daemon: `cmd/forged/`
- CLI: `cmd/forge/`
- Patrols: `cmd/forged/patrol.go`
- WebSocket: `cmd/forged/websocket.go`
