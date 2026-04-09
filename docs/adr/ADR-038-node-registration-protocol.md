# ADR-038: Node Registration Protocol

**Status:** Proposed
**Date:** 2026-03-08
**Author:** Lead Orchestrator (prya)
**Supersedes:** None
**Related:** ADR-023 (XNode Evolution), ADR-027 (Fleet Observability)

---

## Context

The FORGE fleet discovered three compounding bugs that caused nodes (vega, nova, gaea) to be invisible in the fleet dashboard:

1. **main.go:1216 hardcoded `nodeID = "sati"`** — any node running without `NODE_ID` env var registered as sati, silently overwriting sati's record.
2. **`forge work --daemon` never registered the node** — agent heartbeats updated `agent_heartbeats` but never created/updated the `nodes` table entry.
3. **xnode.go hardcoded `nodeID+":8081"` as node address** — no Tailscale IP detection, so cross-node routing used wrong addresses.

These bugs compounded: a node could be online, its agents sending heartbeats, but the node itself invisible or pointing to the wrong IP.

---

## Decision

### Protocol (now exposed via `forge node join`)

Every node MUST register itself explicitly before participating in the fleet:

```
POST /api/xnode/nodes/register
{
  "id": "<hostname>",
  "hostname": "<hostname>",
  "address": "<tailscale-ip>:8081",
  "status": "online"
}
```

### Address Resolution Order

1. `NODE_ADDR` env var (explicit override, set by `forge node join`)
2. `detectTailscaleIP()` — runs `tailscale ip -4` if available
3. `nodeID:8081` — hostname fallback (LAN only, not cross-node)

### NODE_ID Resolution Order

1. `NODE_ID` env var (explicit)
2. `os.Hostname()` — system hostname
3. `"unknown"` — last resort (should never happen in practice)

### Daemon Self-Registration

At daemon startup, `main.go` now:
1. Resolves `NODE_ID` via the order above
2. Resolves `NODE_ADDRESS` via the order above
3. Calls `xnodeController.UpsertNode(selfNode)` to register in local DB
4. Nodes joining via `forge work --daemon` are covered by `forge node join`

### Tooling

`forge node join` is the canonical join flow:
- auto-detects hostname + Tailscale IP
- calls `POST /api/xnode/nodes/register` explicitly
- starts `forge work --daemon` with `NODE_ID` set
- uses the conventional node→agent mapping unless overridden

---

## Consequences

### Positive
- Any node running `forge node join` appears correctly in `forge node list`
- Cross-node task routing uses Tailscale IPs (works across subnets)
- No more silent identity collisions (was `sati` everywhere)

### Negative
- Nodes that don't run `forge node join` are still invisible (no auto-discovery)
- Requires `NODE_ID` set correctly in systemd/launchd service files

### Neutral
- The `nodes` table entry expires after 5 minutes without heartbeat — this is correct behavior

---

## ADR Candidates for Future Work

- **ADR-039**: Automatic node heartbeat (daemon sends periodic heartbeat to own DB + to prya)
- **ADR-040**: Node discovery via mDNS or Tailscale API (eliminate manual registration)
