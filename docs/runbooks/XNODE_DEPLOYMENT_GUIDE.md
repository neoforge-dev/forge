# XNode Deployment Guide

One-command bootstrap for adding a new node to the FORGE cross-node mesh.

## Quick Start

```bash
# Linux (systemd) or macOS (launchd) — same command
cd /path/to/FORGE
bash harness/scripts/xnode-bootstrap.sh --token $FORGE_WEBHOOK_TOKEN
```

**Required Environment:**
- `FORGE_WEBHOOK_TOKEN` — Shared auth token (get from hub node)
- forged daemon must be running on the hub (default: `http://prya.ts.net:8081`)

## One-Liner Installs

### Linux (Systemd User Service)

```bash
curl -fsSL https://raw.githubusercontent.com/bogdan-veliscu/FORGE/main/harness/scripts/xnode-bootstrap.sh | \
  FORGE_WEBHOOK_TOKEN=xxx bash -s -- --token $FORGE_WEBHOOK_TOKEN
```

Creates:
- `~/.config/systemd/user/forge-xnode-listener.service` — SSE bridge
- `~/.config/systemd/user/forge-heartbeat.timer` — 60s heartbeat

### macOS (LaunchAgent)

```bash
curl -fsSL https://raw.githubusercontent.com/bogdan-veliscu/FORGE/main/harness/scripts/xnode-bootstrap.sh | \
  FORGE_WEBHOOK_TOKEN=xxx bash -s -- --token $FORGE_WEBHOOK_TOKEN
```

Creates:
- `~/Library/LaunchAgents/com.forge.xnode.listener.plist`
- `~/Library/LaunchAgents/com.forge.heartbeat.plist`

## Parameters

| Flag | Environment | Default | Description |
|------|-------------|---------|-------------|
| `--hub-url URL` | `FORGE_API_URL` | `http://prya.ts.net:8081` | forged API URL |
| `--token TOKEN` | `FORGE_WEBHOOK_TOKEN` | *(required)* | Auth token |
| `--node-id ID` | `FORGE_NODE_ID` | hostname | Node identifier |
| `--dry-run` | — | — | Preview without changes |

### Examples

```bash
# Minimal — uses defaults for hub URL and node ID
bash harness/scripts/xnode-bootstrap.sh --token mysecret

# Explicit hub and node
bash harness/scripts/xnode-bootstrap.sh \
  --hub-url http://prya.ts.net:8081 \
  --token mysecret \
  --node-id sati

# Preview mode
bash harness/scripts/xnode-bootstrap.sh --token mysecret --dry-run
```

## Heartbeat Configuration

The heartbeat timer fires every **60 seconds** and publishes node telemetry to the forged API:

- **Linux**: `systemctl --user status forge-heartbeat.timer`
- **macOS**: `launchctl list | grep com.forge.heartbeat`

Heartbeat data includes:
- Node ID
- Timestamp
- CPU/memory usage
- Active agents count

## Verification

```bash
# 1. Check hub health
curl -sf http://prya.ts.net:8081/health

# 2. Check xnode listener status
forge node list

# 3. Check heartbeat stream
forge node status

# 4. View logs
tail -f .forge/logs/xnode-listener.log
tail -f .forge/logs/heartbeat.log

# 5. Verify node in forged API
curl -H "Authorization: Bearer $TOKEN" \
  http://prya.ts.net:8081/api/nodes | jq .
```

## Troubleshooting

### Hub Unreachable

**Symptoms:** `Hub health check failed — Tailscale may be offline`

**Fix:**
```bash
# Verify Tailscale connectivity
tailscale status

# Ping hub node
ping -c 3 prya.ts.net

# Check forged API running
curl http://prya.ts.net:8081/health
```

### Port Conflicts

**Symptoms:** Listener fails to start, `address already in use`

**Fix:**
```bash
# Find process using port 8080
lsof -i :8081  # macOS
ss -tlnp | grep 8080  # Linux

# Kill conflicting process or change forged port
```

### Firewall Blocking

**Symptoms:** Connection timeout, connection refused

**Fix:**
```bash
# Linux: Allow traffic on Tailscale interface
sudo ufw allow in on tailscale0
sudo firewall-cmd --add-interface=tailscale0 --permanent

# macOS: Firewall settings in System Preferences
```

### Stale Heartbeats

**Symptoms:** Node shows "offline" in forged dashboard, last heartbeat >2min ago

**Fix:**
```bash
# Restart heartbeat timer
systemctl --user restart forge-heartbeat.timer  # Linux
launchctl kickstart -k gui/$(id -u)/com.forge.heartbeat  # macOS

# Check timer status
systemctl --user status forge-heartbeat.timer
journalctl --user -u forge-heartbeat -n 20

# Manually trigger heartbeat
cd harness
uv run python -m forge_harness.cli_v2 nodes heartbeat
```

### Service Not Starting

**Symptoms:** Listener exits immediately, logs show import errors

**Fix:**
```bash
# Verify uv installed
which uv

# Reinstall uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Check service logs
journalctl --user -u forge-xnode-listener -f  # Linux
log stream --predicate 'process == "uv"' --info  # macOS
```

### Node ID Collision

**Symptoms:** Two machines register with same name, heartbeat conflicts

**Fix:**
```bash
# Uninstall and reinstall with explicit node ID
systemctl --user stop forge-xnode-listener forge-heartbeat.timer  # Linux
systemctl --user disable forge-xnode-listener forge-heartbeat.timer
rm ~/.config/systemd/user/forge-*

# Re-run with --node-id
bash harness/scripts/xnode-bootstrap.sh --token $TOKEN --node-id unique-name
```

## Uninstall

```bash
# Linux
systemctl --user stop forge-xnode-listener forge-heartbeat.timer
systemctl --user disable forge-xnode-listener forge-heartbeat.timer
rm ~/.config/systemd/user/forge-xnode-*

# macOS
launchctl unload ~/Library/LaunchAgents/com.forge.xnode.listener.plist
launchctl unload ~/Library/LaunchAgents/com.forge.heartbeat.plist
rm ~/Library/LaunchAgents/com.forge.*.plist
```
