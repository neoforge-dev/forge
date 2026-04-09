# XNode Activation Checklist

**Last Updated:** 2026-03-19 (S120)
**Audience:** Humans and agents activating FORGE nodes

> **Note:** This runbook uses the v4 Go CLI (`forge`). The previous v2 Python CLI is no longer used.

---

## Quick Reference

All nodes connect to the **forged daemon** on the lead node (prya:8081). The `forge` CLI is used for all operations.

| Node Type | Setup | Verify |
|-----------|-------|--------|
| **Lead** (prya) | `git pull && forge daemon start` | `forge status` |
| **Worker** (sati, nova, vega) | `git pull && FORGE_API_URL=http://prya:8081` | `forge status` |

---

## Prerequisites

All nodes require:
- **Tailscale** running: `tailscale status`
- **Go 1.22+**: `go version`
- **forge CLI**: `forge version`
- **Connectivity to prya:8081**: `curl -sf http://prya:8081/health`

---

## Node Setup

### 1. Set environment variables

```bash
# Add to ~/.zshrc or ~/.bashrc
export FORGE_API_URL="http://prya:8081"
export FORGE_ROOT="$HOME/FORGE"
export FORGE_NODE_ID="$(hostname -s)"
```

### 2. Verify connectivity

```bash
# From any node
curl -sf http://prya:8081/health
# Expected: {"status":"ok","service":"forged",...}
```

### 3. Check node status

```bash
forge status
forge node list
```

---

## Troubleshooting

### Connection refused

```bash
# Check if daemon is running on prya
forge daemon status

# Check Tailscale
tailscale ping prya
```

### Agent not showing in `forge agent list`

```bash
# Agent must send heartbeat
forge agent heartbeat <agent-id>
```

---

## Reference

- Daemon: `cmd/forged/`
- CLI: `cmd/forge/`
- Configuration: `forge env`
