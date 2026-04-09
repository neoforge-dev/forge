# V3 Deployment Runbook

## Quick Start
```bash
# Start v3 server
cd cmd/forge-v3
./forge-v3 &

# Verify health
curl http://localhost:8081/api/health
```

## Phases
1. Phase 0.5: Status API, SQLite, XNode
2. Phase 1: WebSocket, Task Queue
3. Phase 1.5: TUI Logs, Pause/Resume
4. Phase 2: TUI, Web UI, Notifications

## Monitoring
- Logs: /tmp/v3-*.log
- Health: curl http://localhost:8081/api/health
- Metrics: curl http://localhost:8081/metrics
