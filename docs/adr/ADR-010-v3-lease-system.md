# ADR-010: Task Lease System Design

**Date:** 2026-03-02
**Status:** Accepted
**Decision Makers:**
- kilo (pool-t2 agent, Architecture Review)
- cursor (pool-t2 agent, Technical Review)
- amp (pool-t2 agent, Operations Review)

**Related:** ADR-013 (Race Mode) was withdrawn in favor of this approach (2026-03-06)

---

## Context

FORGE v3 needs a mechanism to ensure tasks are executed exactly once, even in the presence of:
- Worker disconnects and reconnects
- Orchestrator restarts
- Network partitions
- Race conditions in multi-worker dispatch

The existing Command Center API has a lease system that works well:
```
POST /api/tasks/{id}/lease/claim
POST /api/tasks/{id}/lease/renew
POST /api/tasks/{id}/lease/release
```

However, v3's event-sourced architecture requires a different approach. The question: How do we port the lease concept to v3 while maintaining exactly-once semantics?

### Alternatives Considered

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **At-least-once + idempotency** | Simple, no lease state | Risk of duplicate work, wasted compute | ❌ REJECTED |
| **At-most-once + timeout** | No duplicates | Risk of lost work if worker dies | ❌ REJECTED |
| **Lease with TTL + renewal** | Exactly-once, automatic recovery | Complexity, clock skew issues | ✅ **ACCEPTED** |
| **Distributed consensus (Raft)** | Strong consistency | Massive overkill for single orchestrator | ❌ REJECTED |

---

## Decision

Implement a **lease-based task allocation system** with the following properties:

1. **Exactly-once execution** - Task assigned to one worker at a time
2. **Automatic recovery** - Expired leases can be reclaimed
3. **Heartbeating** - Workers must renew leases periodically
4. **Conflict resolution** - Clear semantics for lease contention

### Lease Lifecycle

```
PENDING → CLAIMED (lease created) → RENEWING (heartbeat) → COMPLETED/FAILED → RELEASED
              ↓
         EXPIRED (TTL passed) → RECLAIMED → PENDING
```

### Schema

```sql
-- Lease table
CREATE TABLE leases (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,  -- One active lease per task
    agent_id TEXT NOT NULL,
    
    -- Timing
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,      -- TTL deadline
    last_renewed_at TEXT,          -- Last heartbeat
    
    -- Status
    status TEXT NOT NULL DEFAULT 'active',  -- active, released, expired
    
    -- For race mode (ADR-009)
    candidate_id TEXT,             -- Optional: race candidate identifier
    
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

-- Index for efficient expiry scanning
CREATE INDEX idx_leases_expires ON leases(expires_at);
CREATE INDEX idx_leases_agent ON leases(agent_id, status);

-- Lease events for audit trail
CREATE TABLE lease_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lease_id TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- lease.claimed, lease.renewed, lease.released, lease.expired
    payload TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Lease Manager Interface

```go
type LeaseManager interface {
    // Claim a lease for a task
    // Returns error if task already has active lease
    Claim(ctx context.Context, taskID, agentID string, ttl time.Duration) (*Lease, error)
    
    // Renew an existing lease
    // Must be called before expiry
    Renew(ctx context.Context, leaseID string, ttl time.Duration) error
    
    // Release a lease (task completed/failed)
    Release(ctx context.Context, leaseID string) error
    
    // Get lease status
    Get(ctx context.Context, leaseID string) (*Lease, error)
    
    // Get lease for task
    GetByTask(ctx context.Context, taskID string) (*Lease, error)
}

type Lease struct {
    ID        string
    TaskID    string
    AgentID   string
    ExpiresAt time.Time
    Status    string
}
```

### Default Configuration

```go
var DefaultLeaseConfig = LeaseConfig{
    // TTL: 30 minutes default
    DefaultTTL: 30 * time.Minute,
    
    // Renewal window: 5 minutes before expiry
    RenewalWindow: 5 * time.Minute,
    
    // Renewal interval: workers renew every 15 minutes
    RenewalInterval: 15 * time.Minute,
    
    // Stale lease recovery: patrol scans every 60 seconds
    StaleRecoveryInterval: 60 * time.Second,
    
    // Max lease extensions: prevent indefinite holding
    MaxRenewals: 20,  // 20 × 30 min = 10 hours max
}
```

### Error Handling

```go
var (
    // Lease already exists for this task
    ErrLeaseAlreadyOwned = errors.New("lease already owned by another agent")
    
    // Lease not found
    ErrLeaseNotFound = errors.New("lease not found")
    
    // Lease expired
    ErrLeaseExpired = errors.New("lease expired")
    
    // Renewal window passed
    ErrRenewalWindowClosed = errors.New("renewal window closed")
    
    // Max renewals reached
    ErrMaxRenewalsReached = errors.New("maximum renewals reached")
)
```

---

## Integration with Other Systems

### Worker Adapter (Python)

```python
class ForgeWorker:
    def __init__(self, agent_id, orchestrator_url):
        self.agent_id = agent_id
        self.lease_manager = LeaseManager(orchestrator_url)
        self.current_lease = None
        
    async def claim_task(self, task_id):
        """Claim lease for task"""
        lease = await self.lease_manager.claim(
            task_id=task_id,
            agent_id=self.agent_id,
            ttl=timedelta(minutes=30)
        )
        self.current_lease = lease
        
        # Start renewal loop
        asyncio.create_task(self._renewal_loop(lease.id))
        return lease
        
    async def _renewal_loop(self, lease_id):
        """Background task to renew lease every 15 minutes"""
        while self.current_lease and self.current_lease.id == lease_id:
            await asyncio.sleep(15 * 60)  # 15 minutes
            try:
                await self.lease_manager.renew(lease_id, timedelta(minutes=30))
            except Exception as e:
                logger.error(f"Lease renewal failed: {e}")
                break
                
    async def complete_task(self, result):
        """Complete task and release lease"""
        if self.current_lease:
            await self.lease_manager.release(self.current_lease.id)
            self.current_lease = None
```

### Patrol: Stale Lease Recovery

```go
type StaleLeaseRecoveryPatrol struct {
    db *sql.DB
}

func (p *StaleLeaseRecoveryPatrol) Run(ctx context.Context) error {
    // Find expired leases
    rows, err := p.db.QueryContext(ctx, `
        SELECT id, task_id, agent_id 
        FROM leases 
        WHERE expires_at < datetime('now') 
        AND status = 'active'
    `)
    if err != nil {
        return err
    }
    defer rows.Close()
    
    for rows.Next() {
        var leaseID, taskID, agentID string
        if err := rows.Scan(&leaseID, &taskID, &agentID); err != nil {
            continue
        }
        
        // Mark lease as expired
        _, err := p.db.ExecContext(ctx, `
            UPDATE leases 
            SET status = 'expired' 
            WHERE id = ?
        `, leaseID)
        if err != nil {
            continue
        }
        
        // Emit lease.expired event
        p.emitEvent(LeaseEvent{
            Type:     "lease.expired",
            LeaseID:  leaseID,
            TaskID:   taskID,
            AgentID:  agentID,
        })
        
        // Requeue task
        _, err = p.db.ExecContext(ctx, `
            UPDATE tasks 
            SET status = 'pending', assigned_to = NULL 
            WHERE id = ?
        `, taskID)
        if err != nil {
            continue
        }
        
        // Emit task.requeued event
        p.emitEvent(TaskEvent{
            Type:   "task.requeued",
            TaskID: taskID,
            Reason: "lease_expired",
        })
    }
    
    return nil
}
```

### Race Mode Integration (ADR-009)

For race mode, leases support multiple candidates per task:

```go
// Race mode: multiple leases per task (one per candidate)
type RaceLease struct {
    ID          string
    TaskID      string
    AgentID     string
    CandidateID string  // "candidate-1", "candidate-2", etc.
    ExpiresAt   time.Time
    Status      string
}

// Claim for race candidate
func (m *LeaseManager) ClaimRaceCandidate(
    ctx context.Context,
    taskID,
    agentID,
    candidateID string,
    ttl time.Duration,
) (*RaceLease, error) {
    // Allow multiple leases per task, but unique per candidate
    // Enforced by: UNIQUE(task_id, candidate_id)
}
```

---

## Consequences

### Positive

1. **Exactly-once execution** - No duplicate task execution
2. **Automatic recovery** - Expired leases requeue automatically
3. **Clear ownership** - Always know which agent owns a task
4. **Observability** - Lease events provide audit trail
5. **Graceful degradation** - Worker disconnects don't lose work

### Negative

1. **Clock skew sensitivity** - TTL relies on clock synchronization
2. **Renewal overhead** - Constant heartbeat traffic
3. **Complexity** - Additional state to manage
4. **Edge cases** - Network partitions, split-brain scenarios

### Mitigations

| Risk | Mitigation |
|------|------------|
| Clock skew | Use monotonic clocks, NTP sync required |
| Renewal storms | Jittered renewal intervals |
| Split-brain | Single orchestrator (no multi-master) |
| Orphaned leases | Patrol scans every 60 seconds |

---

## Alternatives Not Chosen

### At-least-once + Idempotency

**Why rejected:** While simpler, it risks:
- Wasted compute on duplicate execution
- Conflicting writes to shared resources
- Harder to debug "which execution won?"

### At-most-once + Timeout

**Why rejected:** Risk of lost work:
- Worker dies, task never completes
- No automatic recovery mechanism
- Requires manual intervention

### Distributed Consensus (Raft/Paxos)

**Why rejected:** Massive overkill:
- Single orchestrator node (no need for consensus)
- 10-100x complexity increase
- Operational nightmare

---

## Implementation Timeline

### Phase 1 (Weeks 1-8): Core Lease System
- Schema: `leases`, `lease_events` tables
- LeaseManager interface
- Worker adapter integration
- Stale lease recovery patrol

### Phase 2 (Weeks 9-12): Race Mode Support
- Multi-candidate leases
- Worktree isolation per candidate

### Phase 3 (Weeks 13-16): Advanced Features
- Lease metrics and alerting
- Lease contention analysis
- Optimistic locking for plan updates

---

## Related Decisions

- ADR-008: FORGE CLI v3 Rewrite (parent)
- ADR-009: Agentic Patterns (race mode integration)
- ADR-011: WebSocket Protocol (lease heartbeat transport)

## References

- Command Center Lease API: `harness/command_center/docs/ARCHITECTURE.md`
- FORGE CLI v3 Locked Spec: `docs/plans/FORGE_CLI_V3_LOCKED_SPECIFICATION.md`
- Multi-Node Lease Operations: `docs/runbooks/MULTI_NODE_LEASE_OPERATIONS.md`

---

**Status: ACCEPTED**

Implementation: Phase 1 (Weeks 1-8)
Default TTL: 30 minutes
Renewal interval: 15 minutes
Stale recovery: 60 seconds
