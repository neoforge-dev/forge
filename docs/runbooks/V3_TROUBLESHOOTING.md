# V3 Troubleshooting

> [!IMPORTANT]
> This document has been superseded by the comprehensive **[V3 Operations Guide](../v3/OPERATIONS.md)**.

## Quick Reference

### Server won't start
Check port 8081: `lsof -i :8081`
Kill existing: `pkill forge-v3`

### Claims not being judged
Check orchestrator: `pgrep -f v3-claim-orchestrator`
Restart: `./scripts/v3-claim-orchestrator.sh &`

### Tests failing
Check server running: `curl http://localhost:8081/api/health`
Run tests: `cd cmd/forge-v3 && go test -v`

---
*For monitoring, alerting thresholds, and standard recovery procedures, see **[docs/v3/OPERATIONS.md](../v3/OPERATIONS.md)**.*
