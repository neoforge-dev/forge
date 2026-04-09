# FORGE Node Setup Guide

**Last Updated:** 2026-03-19 (S120)
**Audience:** Humans and agents setting up new FORGE fleet nodes

---

## Quick Reference

| Node Type | Bootstrap | Config | Verify |
|-----------|-----------|--------|--------|
| **Lead** (prya) | `git pull && bash bin/node-setup.sh` | `forge env` | `forge daemon start` |
| **Worker** (sati, nova, vega, gaea) | `git pull && bash bin/node-setup.sh` | `FORGE_API_URL=http://prya:8081` | `forge status` |

---

## Prerequisites

All nodes require:

| Requirement | Install |
|-------------|---------|
| **POSIX shell** (bash/sh) | Built-in |
| **curl or wget** | `apt install curl` / `brew install curl` |
| **git** | `apt install git` / `brew install git` |
| **Tailscale** | [tailscale.com/download](https://tailscale.com/download) |
| **Go 1.22+** | `brew install go` or download from go.dev |

Verify:
```bash
which curl git go tailscale
```

---

## Node Bootstrap

### Step 1: Clone and Sync

```bash
# Clone FORGE
git clone https://github.com/codeswiftr/forge-mono.git ~/work/forge-mono
cd ~/work/FORGE

# Pull latest
git pull --no-recurse-submodules
```

### Step 2: Run Migration Script

```bash
bash bin/node-setup.sh
```

This script:
- Builds the `forge` CLI
- Builds the `forged` daemon
- Sets up environment variables

### Step 3: Configure Node Role

**Lead nodes (prya)** - Run the daemon locally:
```bash
# Start daemon
forge daemon start

# Verify
forge daemon status
curl http://localhost:8081/health
```

**Worker nodes (sati, nova, vega, gaea)** - Connect to prya:
```bash
# Set environment (add to ~/.bashrc or ~/.zshrc)
export FORGE_API_URL=http://prya:8081

# Verify connection
forge status
```

---

## Verify Node Setup

```bash
# Check daemon health (lead) or connection (worker)
forge status

# Check patrols
forge patrol list

# Check fleet
forge fleet windows

# Check environment
forge env
```

---

## Service Management

### Start/Stop Daemon (Lead Nodes)

```bash
# Start daemon
forge daemon start

# Check daemon status
forge daemon status

# Stop daemon
forge daemon stop

# Restart daemon (after code changes)
forge daemon restart
```

### Monitor Daemon

```bash
# Watch daemon logs
tail -f /tmp/forged.log

# Check health endpoint
curl http://localhost:8081/health

# Check metrics
curl http://localhost:8081/api/metrics
```

---

## Troubleshooting

| Issue | Diagnosis | Fix |
|-------|-----------|-----|
| Daemon not running | `forge daemon status` | `forge daemon start` |
| Can't reach prya | `curl http://prya:8081/health` | Check Tailscale |
| Port conflicts | `lsof -i:8081` | `pkill forged` |
| Stale processes | `ps aux \| grep forged` | `forge daemon stop && forge daemon start` |
| Git index lock | `.git/index.lock exists` | `rm -f .git/index.lock` |

---

## Node Resource Budget

| Node | RAM | Max Agents | Allowed Models |
|------|-----|------------|----------------|
| **prya** | 16 GB | 2 max | Claude Code (minimax/glm), Kimi — NO OpenCode/Kilo |
| **sati** | 64 GB | 5-6 | OpenCode, Kilo, Kimi, GLM — heavy workloads |
| **nova** | 48 GB | 3-4 | Worktree agents, iOS builds |
| **vega** | 16 GB | 1-2 | Auxiliary only (no iOS — Ventura too old) |
| **gaea** | 16 GB | 2-3 | M1 Pro laptop, off-hours only |

**CRITICAL:** Never spawn OpenCode or Kilo on prya — causes OOM at 93% RAM.

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `FORGE_API_URL` | `http://localhost:8081` | Daemon API URL |
| `PORT` | `8081` | HTTP API port |
| `WS_PORT` | `8082` | WebSocket port |
| `DB_PATH` | `./.forge/forge-v3.db` | SQLite path |
| `FORGE_ROOT` | `.` | Root for xnode file paths |
| `NODE_ID` | `os.Hostname()` | This node's identity |

---

## Reference

- CLI: `cmd/forge/`
- Daemon: `cmd/forged/`
- ADR-025: Node registration (deferred)
- `docs/NODE_QUICKSTART.md` — simplified setup
