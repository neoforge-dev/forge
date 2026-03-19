# FORGE Node Setup Guide

**Last Updated:** 2026-03-08 (S80)

**Architecture:** hub-first. `node-1:8081` is the default control plane. Other nodes connect to it via Tailscale and only need a local daemon when explicitly operating in local mode.

> Current reality: `docs/adr/INDEX.md` marks ADR-025 superseded for now. Revisit per-node daemons only if scale or latency makes hub mode insufficient.

---

## Quick Setup (any non-node-1 node)

Two commands. Run them once on each new node:

```bash
git pull && bash bin/node-migrate-v3
```

**What it does:**
1. Pulls latest code (gets the migration script itself)
2. Kills any old v2 harness work loops
3. Builds forge CLI v4 binary, installs to PATH
4. Pings `http://node-1:8081/health` via Tailscale — fails fast if unreachable
5. Verifies `forge task list` works against node-1

Add to `~/.bashrc` or `~/.zshrc`:

```bash
export FORGE_ROOT=$HOME/FORGE
export FORGE_API_URL=http://node-1:8081
export PATH="$HOME/FORGE/cmd/forge:$PATH"
```

Or run `forge init --node-id $(hostname) --control-plane http://node-1:8081` and let the CLI read `~/.forge/config.toml`.

---

## Starting an Agent Work Loop

```bash
FORGE_AGENT_TYPE=fleet FORGE_AGENT_NAME=<name> forge work --daemon
```

| Node | Typical agent names |
|------|-------------------|
| node-2 | kimi, kimi-2, opencode, kilo |
| node-3 | glm, gemini |
| node-5 | pi, minimax |
| node-1 | lead orchestrator only (no fleet agents — 16GB RAM) |

---

## Architecture

```
node-2 ──┐
node-3 ──┼──[Tailscale]──→  node-1:8081  (single v3 daemon + SQLite)
node-5 ──┘
```

All nodes read/write tasks via `FORGE_API_URL=http://node-1:8081`, share the same git repo,
watch `.forge/dispatches/`, write results to `.forge/heartbeat/results/`.

---

## Verify Node is Working

```bash
forge status
curl -sf http://node-1:8081/health && echo "✅ hub reachable"
forge task list
forge agent list
forge patrol list
```

---

## Troubleshooting

**`node-1:8081` unreachable:**
```bash
tailscale status | grep node-1
ssh node-1 'curl http://localhost:8081/health'
forge config get control_plane.url
```

**Old harness still running:**
```bash
pkill -f "forge_harness.cli_v2.*work"
pkill -f "forge-harness.*work"
```

**Binary stale (binary-freshness patrol alert on node-1):**
```bash
cd $HOME/FORGE && bin/forge-v3-restart --build
```

---

## When Local Daemons Are Needed

Local daemons make sense when:
- A node is offline frequently (laptop, edge)
- Network latency to node-1 > 100ms for heavy workloads

Default path remains: `git pull && bash bin/node-migrate-v3`
