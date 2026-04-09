# Node Reconnection Runbook

> **SUPERSEDED (S116):** This document described v2 CLI xnode listener bootstrapping via SSH.
> The v3 architecture uses hub-spoke with all nodes pointing to `prya:8081`.
> Nodes self-register via `forge init` and heartbeat via `forge agent heartbeat`.
>
> **Current node setup:** See `docs/NODE_SETUP.md` and `bin/forge-node-join.sh`.
> **Cross-node messaging:** See `cmd/forged/message_relay.go` (git-based relay via `.forge/messages/`).

## Current Architecture (v3, S116)

All nodes connect to the hub daemon at `prya:8081` over Tailscale.
No SSH from prya to other nodes. Cross-node coordination is git-based.

### Adding a New Node

```bash
# On the new node:
git clone <repo> ~/work/FORGE && cd ~/work/FORGE
bash bin/forge-node-join.sh
```

### Verifying Node Connectivity

```bash
# From any node:
forge node list
forge node status <node-name>
```

### Tailscale Status

| Node | Tailscale IP | Role |
|------|-------------|------|
| prya | 100.70.79.45 | Hub (daemon host) |
| sati | 100.80.39.128 | Worker (heavy compute) |
| nova | 100.101.55.65 | Worker (iOS builds) |
| vega | 100.83.227.5 | Auxiliary (low priority) |
| gaea | — | Auxiliary (laptop, off-hours) |
