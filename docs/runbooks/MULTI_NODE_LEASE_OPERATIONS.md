# Multi-Node Lease Operations Runbook

**Audience**: On-call engineers, orchestrators
**Last updated**: 2026-02-21
**Related docs**: `docs/MULTI_NODE_IMPLEMENTATION_PLAN.md`, `docs/NODE_WORK_PARTITIONING.md`, `docs/runbooks/NODE_ROLLOUT_CHECKLIST.md`

---

## Overview

Task leases provide **deterministic ownership** — one agent works one task at a time. Path locks prevent two agents from editing the same code path simultaneously.

When an agent claims a task, it holds an exclusive lease with a TTL (default 30 minutes). No other agent can pick up that task while the lease is active. If the agent crashes or goes offline, the lease expires and `StaleLeaseRecoveryService` automatically requeues the task for reassignment.

Path locks are scoped to filesystem paths (e.g., `codeswiftr-com/interview-simulator/`). Two tasks that touch the same path cannot be worked concurrently. A 409 `PATH_LOCK_CONFLICT` error signals this contention.

### Key Invariants

- A task in `assigned` status always has exactly one active lease.
- A lease `owner_node` + `owner_agent` pair uniquely identifies the holder.
- Path locks are released automatically when the lease is released or expires.
- Stale lease recovery runs every 60 seconds and operates independently of agent availability.

---

## Lease Lifecycle

### Claim

Acquire exclusive ownership of a task before starting work.

```
POST /api/tasks/{id}/lease/claim
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "lease": {
    "owner_node": "prya",
    "owner_agent": "forge:codex",
    "lease_expires_at": "2026-02-21T18:00:00Z",
    "path_lock": "codeswiftr-com/interview-simulator/"
  }
}
```

**Success**: `200 OK` — task transitions to `assigned`, lease record created.
**Conflict**: `409 LEASE_ALREADY_OWNED` — another agent already holds this task.
**Path conflict**: `409 PATH_LOCK_CONFLICT` — a different task holds a lock on the same path.

### Renew

Extend the lease expiry before it lapses. Call this periodically for long-running tasks.

```
POST /api/tasks/{id}/lease/renew
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "owner_node": "prya",
  "owner_agent": "forge:codex"
}
```

**Success**: `200 OK` — expiry extended by the configured renewal window (default 30 minutes from now).
**Error**: `403 LEASE_NOT_OWNED` — the calling agent does not hold this lease.
**Error**: `404` — task not found or lease already expired.

### Release

Voluntary release when work is complete or abandoned by the agent.

```
POST /api/tasks/{id}/lease/release
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "owner_node": "prya",
  "owner_agent": "forge:codex"
}
```

**Success**: `200 OK` — task transitions back to `pending` (or `done` if work was committed).
Path lock is released immediately.

### Requeue

Explicitly return a task to `pending` with an optional reason logged to the audit trail. Used for manual overrides or after a forced lease release.

```
POST /api/tasks/{id}/requeue
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "reason": "manual-override"
}
```

**Success**: `200 OK` — task status set to `pending`, lease cleared, path lock released.

---

## Common Scenarios

### Agent Crash

**Symptom**: Task stuck in `assigned`, agent not responding, no heartbeat renewal.

**Resolution**: Automatic. `StaleLeaseRecoveryService` checks every 60 seconds for leases past their `lease_expires_at`. Expired leases are released and the task is requeued to `pending`. No manual intervention required unless TTL is longer than acceptable downtime.

**To check**: Query assigned tasks and compare `lease_expires_at` against current time.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8080/api/tasks?status=assigned"
```

If `lease_expires_at` is in the past and the task is still `assigned`, the recovery service may be delayed. Wait up to 60 seconds or trigger manual requeue (see Incident Commands).

### Path Lock Conflict

**Symptom**: `409 PATH_LOCK_CONFLICT` when trying to claim a task.

**Cause**: Another task currently holds a path lock on the same directory. Two agents cannot write to the same code path concurrently.

**Resolution options**:

1. **Wait** — the lock releases automatically when the conflicting task completes or its lease expires.
2. **Identify the blocking task** — query assigned tasks and look for a lease with a matching `path_lock`.
3. **Force release** — if the blocking agent is confirmed dead, force-release its lease (see Incident Commands).

Do not attempt to override a path lock belonging to an actively running agent. Coordinate instead.

### Duplicate Claim Attempt

**Symptom**: `409 LEASE_ALREADY_OWNED` when claiming a task.

**Cause**: Another agent already claimed this task. The task is in `assigned` status.

**Resolution**: Choose a different task. If you believe the existing lease is stale (agent is dead), verify via node health endpoint, then force-release.

### Node Goes Offline

**Symptom**: Node heartbeat goes stale (>120 seconds since last write), tasks on that node become eligible for reassignment.

**Resolution**:

1. Tasks assigned to agents on the offline node retain their leases until TTL expires.
2. After TTL, `StaleLeaseRecoveryService` requeues them automatically.
3. When the node comes back online, its heartbeat resumes and new tasks can be dispatched to it.
4. In-flight work from before the outage should be reviewed — the agent may have completed work that was not committed before going offline.

---

## Incident Commands

### Check Active Leases

```bash
# All assigned tasks with lease metadata
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8080/api/tasks?status=assigned
```

### Force-Release a Lease

Use when the lease holder is confirmed dead and auto-recovery has not yet run.

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"owner_node":"prya","owner_agent":"forge:codex"}' \
  http://localhost:8080/api/tasks/{id}/lease/release
```

Replace `{id}` with the task UUID. The `owner_node` and `owner_agent` must match the lease record exactly.

### Requeue a Stuck Task

Use after a force-release, or when a task is in a bad state and needs to be returned to the queue.

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason":"manual-override"}' \
  http://localhost:8080/api/tasks/{id}/requeue
```

### Check Node Health

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8080/api/nodes/health
```

Returns node list with `is_fresh` flag, last heartbeat timestamp, and registered agent sessions.

### Get Scheduler Recommendations

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8080/api/nodes/recommend
```

Returns ranked list of nodes suitable for new task dispatch based on load, freshness, and capacity.

---

## Recovery Procedures

### Stale Lease Recovery (Automatic)

`StaleLeaseRecoveryService` runs on a 60-second polling loop. It:

1. Queries all tasks in `assigned` status.
2. Compares each lease's `lease_expires_at` to the current UTC time.
3. For expired leases, releases the lease and sets task status to `pending`.
4. Logs each recovery action to the audit trail with reason `stale-lease-auto-recovery`.

No manual steps needed unless the service itself is down. To verify it is running:

```bash
# Check service logs (adjust path for your deployment)
forge doctor --component lease-recovery
```

### Manual Override Procedure

Use when auto-recovery is too slow (e.g., TTL is long and downtime is critical).

1. Identify the stuck task ID from `GET /api/tasks?status=assigned`.
2. Confirm the holding agent is dead (check node health, tmux session status).
3. Force-release the lease:
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"owner_node":"<node>","owner_agent":"<agent>"}' \
     http://localhost:8080/api/tasks/{id}/lease/release
   ```
4. Requeue if the task should be picked up again:
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"reason":"manual-override"}' \
     http://localhost:8080/api/tasks/{id}/requeue
   ```
5. Verify task appears in `GET /api/tasks?status=pending`.

### Node Recovery

When an agent node reconnects after an outage:

1. The heartbeat writer resumes automatically, updating `.forge/heartbeat/nodes/{node}.json`.
2. The webhook server detects fresh heartbeats within 120 seconds.
3. The scheduler begins recommending the node for new task dispatch.
4. No manual intervention is required unless leases from before the outage need cleanup.

If the node had tasks in flight that were not committed before going offline, those tasks will have been requeued by the stale lease recovery service. The agent should check its task queue on reconnect rather than assuming prior work is still assigned.

---

## Monitoring

### CLI Dashboards

```bash
# Show all tasks with lease status and expiry times
forge status --tasks

# Show all nodes with heartbeat freshness and last-seen timestamps
forge node list

# Node status
forge node status
```

### Command Center (Web UI)

The **Task Queue** page displays lease badges inline on each task card:

- **LEASED** badge: task is assigned with an active lease, shows owner agent and expiry countdown.
- **STALE** badge: lease is past expiry and pending recovery.
- **PATH LOCKED** indicator: task is blocked by a path lock conflict.

### Alert Thresholds

| Condition | Severity | Action |
|-----------|----------|--------|
| Lease expired > 5min, not requeued | WARNING | Check `StaleLeaseRecoveryService` logs |
| Node heartbeat stale > 5min | WARNING | Investigate network / agent session |
| Node heartbeat stale > 15min | CRITICAL | Trigger manual lease release for that node's tasks |
| More than 3 tasks stuck in `assigned` | WARNING | Review for systemic issue |
| `PATH_LOCK_CONFLICT` rate spike | INFO | Check for task ordering / dependency gap |

---

## Reference

| Term | Definition |
|------|-----------|
| `lease_expires_at` | UTC timestamp after which the lease is considered stale and eligible for auto-recovery |
| `path_lock` | Filesystem path prefix that only one task may hold at a time |
| `owner_node` | Identifier of the machine (e.g., `prya`, `nova`) holding the lease |
| `owner_agent` | Agent session identifier (e.g., `forge:codex`) holding the lease |
| `StaleLeaseRecoveryService` | Background service that polls for expired leases and requeues them |
| TTL | Lease time-to-live, default 30 minutes |
