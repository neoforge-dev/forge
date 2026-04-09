# Node Rollout Checklist — Multi-Node Lease System

**Audience**: Orchestrator, on-call engineer enabling a new node
**Last updated**: 2026-02-21
**Related docs**: `docs/runbooks/MULTI_NODE_LEASE_OPERATIONS.md`, `docs/NODE_ONBOARDING.md`, `docs/MULTI_NODE_IMPLEMENTATION_PLAN.md`

---

## Purpose

This checklist governs the safe, incremental rollout of the multi-node lease system. Each node must pass all verification steps before it is considered active and eligible for task dispatch. Enable nodes one at a time, confirm stability, then proceed to the next.

---

## Pre-Requisites

Complete all of the following before enabling any node.

- [ ] Webhook server is running and the lease endpoints respond:
  - `POST /api/tasks/{id}/lease/claim` returns 200 or 409 (not 404)
  - `POST /api/tasks/{id}/lease/renew` endpoint exists
  - `POST /api/tasks/{id}/lease/release` endpoint exists
  - `POST /api/tasks/{id}/requeue` endpoint exists
- [ ] `StaleLeaseRecoveryService` is active and polling (check logs for `stale-lease-auto-recovery` entries or run `forge doctor --component lease-recovery`)
- [ ] Heartbeat directory exists and is writable:
  ```bash
  ls /home/openclaw/work/FORGE/.forge/heartbeat/nodes/
  ```
  Create it if missing:
  ```bash
  mkdir -p /home/openclaw/work/FORGE/.forge/heartbeat/nodes/
  ```
- [ ] `forge status --nodes` returns at least the local node (confirms API endpoint is reachable and heartbeat pipeline is operational)
- [ ] `GET /api/nodes/health` returns a valid JSON response (not an error)
- [ ] Lease TTL is configured (default: 30 minutes). Confirm in server config.

---

## Per-Node Checklist

Run this checklist for each node in the order specified in the Rollout Order section. Check each item before marking the node as active.

### Node: prya

- [ ] Heartbeat writer active — file updates at `.forge/heartbeat/nodes/prya.json` every 60 seconds
  ```bash
  # Confirm file is being written (timestamp should be <120s old)
  stat /home/openclaw/work/FORGE/.forge/heartbeat/nodes/prya.json
  ```
- [ ] Agent sessions registered with `tmux_session` field in node record (required for dispatch routing)
- [ ] Test lease claim: create a test task, claim it with a lease from `prya`, verify HTTP 200
- [ ] Test path lock enforcement: claim two tasks with the same `path_lock` value, verify second claim returns HTTP 409 `PATH_LOCK_CONFLICT`
- [ ] Test auto-recovery: claim a task with `lease_expires_at` set to 1 minute in the past, wait up to 60 seconds, verify task returns to `pending` status automatically
- [ ] `forge status --nodes` shows `prya` as **online** (green)
- [ ] `GET /api/nodes/recommend` includes `prya` in its response

---

### Node: nova

- [ ] Heartbeat writer active — file updates at `.forge/heartbeat/nodes/nova.json` every 60 seconds
  ```bash
  stat /home/openclaw/work/FORGE/.forge/heartbeat/nodes/nova.json
  ```
- [ ] Agent sessions registered with `tmux_session` field in node record
- [ ] Test lease claim: create a test task, claim it with a lease from `nova`, verify HTTP 200
- [ ] Test path lock enforcement: claim two tasks with the same `path_lock` value from `nova`, verify second claim returns HTTP 409 `PATH_LOCK_CONFLICT`
- [ ] Test auto-recovery: claim a task with an expired lease from `nova`, verify auto-requeue within 60 seconds
- [ ] `forge status --nodes` shows `nova` as **online** (green)
- [ ] `GET /api/nodes/recommend` includes `nova` in its response

---

### Node: sati

- [ ] Heartbeat writer active — file updates at `.forge/heartbeat/nodes/sati.json` every 60 seconds
  ```bash
  stat /home/openclaw/work/FORGE/.forge/heartbeat/nodes/sati.json
  ```
- [ ] Agent sessions registered with `tmux_session` field in node record
- [ ] Test lease claim: create a test task, claim it with a lease from `sati`, verify HTTP 200
- [ ] Test path lock enforcement: claim two tasks with the same `path_lock` value from `sati`, verify second claim returns HTTP 409 `PATH_LOCK_CONFLICT`
- [ ] Test auto-recovery: claim a task with an expired lease from `sati`, verify auto-requeue within 60 seconds
- [ ] `forge status --nodes` shows `sati` as **online** (green)
- [ ] `GET /api/nodes/recommend` includes `sati` in its response

---

### Node: vega

- [ ] Heartbeat writer active — file updates at `.forge/heartbeat/nodes/vega.json` every 60 seconds
  ```bash
  stat /home/openclaw/work/FORGE/.forge/heartbeat/nodes/vega.json
  ```
- [ ] Agent sessions registered with `tmux_session` field in node record
- [ ] Test lease claim: create a test task, claim it with a lease from `vega`, verify HTTP 200
- [ ] Test path lock enforcement: claim two tasks with the same `path_lock` value from `vega`, verify second claim returns HTTP 409 `PATH_LOCK_CONFLICT`
- [ ] Test auto-recovery: claim a task with an expired lease from `vega`, verify auto-requeue within 60 seconds
- [ ] `forge status --nodes` shows `vega` as **online** (green)
- [ ] `GET /api/nodes/recommend` includes `vega` in its response

---

### Node: gaea

- [ ] Heartbeat writer active — file updates at `.forge/heartbeat/nodes/gaea.json` every 60 seconds
  ```bash
  stat /home/openclaw/work/FORGE/.forge/heartbeat/nodes/gaea.json
  ```
- [ ] Agent sessions registered with `tmux_session` field in node record
- [ ] Test lease claim: create a test task, claim it with a lease from `gaea`, verify HTTP 200
- [ ] Test path lock enforcement: claim two tasks with the same `path_lock` value from `gaea`, verify second claim returns HTTP 409 `PATH_LOCK_CONFLICT`
- [ ] Test auto-recovery: claim a task with an expired lease from `gaea`, verify auto-requeue within 60 seconds
- [ ] `forge status --nodes` shows `gaea` as **online** (green)
- [ ] `GET /api/nodes/recommend` includes `gaea` in its response

---

## Go / No-Go Criteria

Evaluate each node against these criteria after completing its per-node checklist.

### GREEN — Proceed

All of the following are true:

- All per-node checklist items are checked.
- Node appears in `GET /api/nodes` with `is_fresh: true`.
- Lease claim, path lock, and auto-recovery tests all passed.
- Heartbeat timestamp is less than 120 seconds old.

**Action**: Mark node as active. Proceed to the next node in rollout order.

### YELLOW — Investigate Before Proceeding

Any of the following:

- Heartbeat is intermittent: node was fresh less than 50% of checks over the last hour.
- Auto-recovery test took longer than 90 seconds (suggests recovery service lag).
- Scheduler recommendations include the node but with low weight or caveats.

**Action**: Do not enable the node for production task dispatch until the cause is identified. Common causes: network instability, clock skew between nodes, heartbeat writer crash-looping.

### RED — Do Not Enable

Any of the following:

- No heartbeat file present or file not updated in more than 5 minutes.
- Lease claim test returned an unexpected error (not 200 or 409).
- Auto-recovery test never requeued the task (after 3 minutes).
- Node does not appear in `GET /api/nodes` response at all.

**Action**: Do not enable. Fix the underlying issue and rerun the full per-node checklist from the beginning.

---

## Rollout Order

Enable nodes strictly in this sequence. Confirm GREEN status before proceeding.

| Step | Node | Role | Rationale |
|------|------|------|-----------|
| 1 | **prya** | Primary | Already running the webhook server; lowest risk first |
| 2 | **nova** | Secondary | Highest agent capacity; validate multi-node lease handoff |
| 3 | **sati** | Tertiary | Enable as it comes online |
| 4 | **vega** | Tertiary | Enable as it comes online |
| 5 | **gaea** | Tertiary | Enable as it comes online |

Do not skip ahead. A failure in a tertiary node is much easier to diagnose if the primary and secondary are confirmed stable first.

---

## Rollback Procedure

If a node fails post-enablement and must be taken offline:

### Step 1 — Disable Heartbeat Writer

Stop the heartbeat writer process on the failing node. This prevents the scheduler from routing new tasks to it. The node will become stale (>120s) within two minutes and drop out of scheduler recommendations automatically.

### Step 2 — Release All Leases Owned by the Failing Node

Identify all tasks leased to agents on the failing node:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8081/api/tasks?status=assigned" \
  | jq '[.[] | select(.lease.owner_node == "<failing-node>")]'
```

For each task, force-release the lease:

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"owner_node":"<failing-node>","owner_agent":"<agent>"}' \
  http://localhost:8081/api/tasks/{id}/lease/release
```

### Step 3 — Confirm Auto-Requeue

Tasks whose leases were released will be requeued to `pending`. Confirm with:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8081/api/tasks?status=pending"
```

If you did not manually release leases and instead chose to let them expire naturally, the default TTL is 30 minutes. Tasks will auto-requeue via `StaleLeaseRecoveryService` after that window.

### Step 4 — Verify Remaining Nodes Are Healthy

```bash
forge status --nodes
```

Confirm all other active nodes show GREEN / `is_fresh: true`. The failing node should appear as stale or absent.

### Re-enabling After Rollback

To re-enable a rolled-back node, rerun its full per-node checklist from the beginning. Do not skip directly to the final verification steps.

---

## Verification Commands Reference

```bash
# Check node health endpoint
curl -H "Authorization: Bearer $TOKEN" http://localhost:8081/api/nodes/health

# Get scheduler recommendations
curl -H "Authorization: Bearer $TOKEN" http://localhost:8081/api/nodes/recommend

# List all assigned tasks (check for stale leases)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8081/api/tasks?status=assigned

# Live node + task dashboard
forge status

# Node-only view
forge node list

# Task-only view with lease badges
forge status --tasks
```
