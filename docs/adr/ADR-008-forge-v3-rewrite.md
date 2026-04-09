# ADR-008: FORGE CLI v3 Rewrite - Multi-Agent Orchestration Platform

**Date:** 2026-03-02
**Status:** Accepted
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)
- kilo (pool-t2 agent, Architecture Review)
- cursor (pool-t2 agent, Technical Review)
- amp (pool-t2 agent, Coordination Review)
- gemini (nf.lead agent, Strategic Review)

---

## Context

FORGE has grown from a single-agent development harness to a multi-agent, multi-node system managing 95 projects across 11 domains. The current architecture faces critical limitations:

1. **Task Loss**: File-based task queue loses work on agent restart
2. **Dispatch Unreliability**: 25% failure rate on raw tmux-based dispatch
3. **Git Lock Contention**: Multiple agents committing simultaneously causes conflicts
4. **Context Loss**: Domain leads lose accumulated knowledge across handoffs
5. **No Master Orchestration**: Ralph Loop handles features but doesn't coordinate the fleet

The system was designed for 2024-era LLMs (limited context, high failure rates, human-in-the-loop). As LLM capabilities explode (10M+ token contexts by 2026, single-pass codebase generation by 2027), FORGE must evolve from a "Code Factory" to an "Integrity & Knowledge Engine" that manages 100+ production systems with zero technical debt.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Buy (Temporal.io) | Infinite scale, battle-tested | High overhead, external lock-in, poor local dev experience | ❌ REJECTED |
| Wait (OpenAI tools) | Zero maintenance | No persistence control, data sovereignty risks, no workflow IP | ❌ REJECTED |
| Simpler (fix v2 Python) | Low initial cost | Python GIL limits, persistence debt, brittle distribution | ❌ REJECTED |
| **Build v3 (Go)** | **Custom-fit, persistent, single-binary, high performance** | **12-week development cost** | ✅ **ACCEPTED** |

---

## Decision

We will build **FORGE CLI v3**, a Go-based multi-agent orchestration platform with the following architecture:

### Core Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 1: STRATEGIC (Orchestrator)                          │
│  • Portfolio decisions, cross-domain conflict resolution    │
│  • Human escalation gateway, budget management              │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌────────────────┐┌────────────────┐┌────────────────┐
│  LAYER 2:      ││  LAYER 2:      ││  LAYER 2:      │
│  TACTICAL      ││  TACTICAL      ││  TACTICAL      │
│  (Market-based)││  (Market-based)││  (Market-based) │
│  Domain Leads  ││  Domain Leads  ││  Domain Leads  │
└────────┬───────┘└────────┬───────┘└────────┬───────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3: OPERATIONAL (Workers)                             │
│  • T1-T3 agents executing tasks                             │
│  • Ralph Loop for feature implementation                    │
│  • Fresh context per task                                   │
└─────────────────────────────────────────────────────────────┘
```

### Key Technical Decisions

#### 1. Language: Go for Orchestrator, Python for Workers

**Decision:** Go for the control plane (orchestrator, queue, TUI), Python for AI workers.

**Rationale:**
- Go provides single-binary distribution, true concurrency (goroutines), and static typing for reliability
- Python retains access to LLM ecosystem (Claude, OpenAI, etc.)
- WebSocket protocol bridges the two with minimal overhead

**Trade-offs:**
- (+) Single binary deployment across 4 nodes
- (+) Better concurrency than Python (GIL limitations)
- (-) Team needs Go training
- (-) Two-language debugging complexity

#### 2. Database: SQLite with Event Sourcing

**Decision:** SQLite with WAL mode as Phase 1 database, `task_events` as source of truth, `tasks` as minimal projection.

**Rationale:**
- SQLite requires zero operational overhead
- WAL mode provides sufficient concurrency for single-node orchestrator
- Event sourcing enables replay, debugging, and migration

**Migration Path:**
- Phase 1: SQLite (95 projects, ~500 tasks/day)
- Phase 2: PostgreSQL (500 projects, ~5K tasks/day)
- Phase 3: Sharded PostgreSQL (5K projects, ~50K tasks/day)
- Phase 4: Kafka + materialized views (unlimited scale)

**Trade-offs:**
- (+) Zero ops overhead, single file backup
- (+) Easy migration path as we scale
- (-) One writer at a time (acceptable for single orchestrator)
- (-) Not suitable for multi-orchestrator without sharding

#### 3. Royal Jelly Pattern: Filesystem as Source of Truth

**Decision:** Keep Royal Jelly files (`.forge/context/{domain}/`) as source of truth, SQLite as cache/index.

**Rationale:**
- Filesystem is human-readable, versioned by git, familiar to agents
- Context Envelopes provide structured data for bootstrapping
- Bidirectional sync keeps both in sync

**Trade-offs:**
- (+) Human can read/edit context directly
- (+) Git provides history and blame
- (-) Slightly more complex than JSON-only
- (-) Need to handle sync conflicts

#### 4. Idempotency: Required for All Dangerous Actions

**Decision:** Implement idempotency keys for: git commits, deploys, approvals, ticket creation.

**Rationale:**
- Prevents duplicate side effects on worker reconnect/retry
- Essential for exactly-once semantics in distributed system

**Implementation:**
```go
type IdempotentAction struct {
    ID          string          // ULID
    Type        string          // commit, deploy, approval
    PayloadHash string          // SHA256
    ExecutedAt  time.Time
    Result      json.RawMessage
}
```

**Trade-offs:**
- (+) Eliminates duplicate commits/deploys on retry
- (+) Enables safe worker reconnection
- (-) Additional storage overhead
- (-) Need to define "dangerous" vs "safe" actions

#### 5. Thin Vertical Slice vs Full Phase 1

**Decision:** Build one complete end-to-end flow (thin vertical slice) rather than all Phase 1 infrastructure.

**Slice Components:**
1. Feature ingestion from `features.json`
2. Task enqueue with dependencies
3. Python worker with reconnect + local queue
4. Task result + context envelope generation
5. GitGuard commit (single-writer + branch-per-task)
6. Dark Factory dev→test gates (deploy stubbed)
7. Approval workflow
8. Basic UI visibility

**Rationale:**
- Forces integration issues to surface early
- Provides working system throughout development
- Clear Go/No-Go criteria at Week 6

**Trade-offs:**
- (+) Integration tested from Day 1
- (+) Clear acceptance criteria
- (-) Less infrastructure built by Week 6
- (-) Need to expand after slice works

#### 6. Rollback Strategy: Feature Flags + Parallel Operation

**Decision:** Use feature flags to route between v2 and v3, maintain v2 compatibility until Phase 3.

**Implementation:**
```yaml
features:
  v3_orchestrator_enabled: true
  v3_task_queue_enabled: true
  v3_dark_factory_enabled: false
```

**Rollback Procedure:**
1. `forge config set v3.enabled false`
2. Routes to legacy v2 binary
3. v3 daemon can be stopped

**Fire Drill:** Weekly "Rollback Wednesday" - switch to v2 for 2 hours

**Trade-offs:**
- (+) Instant rollback without data migration
- (+) Can A/B test v2 vs v3
- (-) Double maintenance during transition
- (-) Feature flag complexity

#### 7. Approval Primitive: Unified HITL Model

**Decision:** Unify all human-in-the-loop actions under single approval primitive with 3 tiers.

**Approval Types (Day 1):**
- `task_completion` - Worker finishes task
- `merge` - Branch ready to merge
- `deploy` - Staging/production deploy
- `security_exception` - Security gate failed
- `budget_overrun` - Agent exceeded compute budget
- `pattern_failure` - Previously 100% pattern now failing
- `lane_promotion` - Dark Factory gate
- `destructive_op` - DB migration, secret rotation

**Tiers:**
- **WATCH** - Binary yes/no, <5 sec decision (auto-approve eligible)
- **PHONE** - Summary context, <2 min decision
- **DESKTOP** - Full diff review, unbounded time

**Confidence Scoring:**
```
overall = pattern*0.35 + tests*0.25 + blast_radius*0.15 + reversibility*0.15 + maturity*0.10

≥ 0.95: Auto-approve
0.70-0.94: PHONE tier human approval
< 0.70: DESKTOP tier human approval
```

**Trade-offs:**
- (+) Consistent HITL experience across all actions
- (+) Auto-approve reduces human load
- (-) Complex scoring algorithm
- (-) Need to tune thresholds per domain

#### 8. External Patterns Adoption

**Adopted Patterns:**
- **Beads hash-based IDs** - ULID for collision-resistant task IDs
- **Gas Town Patrols** - 6 standard patrols (health, timeout, approval expiry, context sync, git cleanup, queue depth)
- **Gas Town Witness** - Supervisor for stuck work detection
- **Gas Town Hooks** - Event subscriptions for UI updates
- **Antfarm declarative lanes** - YAML config for Dark Factory (minimal)

**Deferred Patterns:**
- Beads git-backed tasks (Phase 4)
- Gas Town Refinery (Phase 2)
- Antfarm full workflow DSL (Phase 3)

**Rationale:**
- Adopt proven patterns that solve immediate problems
- Defer complex patterns until core is stable
- Maintain focus on reliability over features

---

## Consequences

### Positive

1. **Reliability**: 99.9% task durability, 99% dispatch reliability vs current 75%
2. **Scalability**: Architecture supports 500→5K→50K projects via database migration path
3. **Maintainability**: Single Go binary deployment, event sourcing enables debugging via replay
4. **Future-Proof**: Event-sourced architecture enables 2027+ AI-first workflows
5. **Knowledge Preservation**: Royal Jelly + Context Envelopes preserve domain expertise
6. **Operational Excellence**: Patrol system automates health checks, git cleanup, timeout handling

### Negative

1. **Development Cost**: 12-week initial investment, team Go training required
2. **Complexity**: Two-language system (Go + Python) increases debugging difficulty
3. **Migration Risk**: 6-week parallel operation period with double maintenance
4. **Feature Delay**: Full command consolidation, Web UI, Flywheel deferred to Phase 2+

### Neutral

1. **Technology Choices**: SQLite sufficient for now, PostgreSQL migration planned
2. **Scope Reduction**: Thin vertical slice means less functionality at Week 6, but higher confidence
3. **External Dependencies**: WebSocket protocol creates coupling between Go and Python

---

## Implementation Timeline

### Phase 1: The Core (6 weeks)

**Week 1-2: Infrastructure**
- Go module setup, CI/CD
- SQLite schema + migrations (events, tasks, idempotent_actions, approvals, context_artifacts)
- Task queue CRUD with dependency support
- Agent registry with heartbeat

**Week 3-4: Communication**
- WebSocket server (Go)
- Python worker adapter (ForgeWorker class)
- Protocol spec: register, task.assigned, task.started, task.completed, ping/pong
- Heartbeat + exponential backoff reconnection

**Week 5-6: Integration**
- Royal Jelly sync (filesystem ↔ SQLite)
- Context Envelope generation at 50% threshold
- Ralph Loop as Python worker
- GitGuard (single-writer + branch-per-task)
- Dark Factory (dev + test lanes, deploy stubbed)
- Approval workflow with confidence scoring
- 6 standard patrols
- Thin vertical slice E2E test

**Go/No-Go Criteria (Week 6):**
- [ ] Orchestrator restart survives (kill -9, no task loss)
- [ ] Worker disconnect no duplicates (5 min disconnect, exactly-once)
- [ ] GitGuard single-writer enforced (two tasks same file, sequential)
- [ ] Context envelope round-trip (generate, bootstrap, resume)
- [ ] Thin slice end-to-end (feature → merge in < 30 min)

### Phase 2: The UI (4 weeks)

- BubbleTea TUI with 3-pane layout
- HTMX Web UI
- Push notifications
- Command consolidation (54 → 28)

### Phase 3: Autonomy (6 weeks)

- Flywheel mode (scan → generate → implement)
- Continuous runner (24/7 autonomous)
- Full Dark Factory lanes (deploy enabled)
- Market-based agent allocation
- PostgreSQL migration

### Phase 4: Scale (2027)

- Read replicas
- Verification pipelines
- Conflict prediction
- Agent specialization (T4, T5)
- Sharding

---

## Related Decisions

- ADR-001: CLI v2 as Canonical Entry Point (superseded by this ADR)
- ADR-002: Dispatch Consolidation to Single Path
- ADR-007: Beads Integration (referenced, partial adoption)

## References

- FORGE CLI v3 Locked Specification: `docs/plans/FORGE_CLI_V3_LOCKED_SPECIFICATION.md`
- Council Reviews: `.forge/heartbeat/results/*-FORGE-V3-FINAL.md`
- Fleet Architecture: `docs/FLEET_ARCHITECTURE.md`
- Beads Pattern: https://github.com/steveyegge/beads
- Gas Town Pattern: https://github.com/steveyegge/gas-town
- Antfarm Pattern: https://github.com/steveyegge/antfarm

---

**Status: ACCEPTED**

Implementation begins: 2026-03-03
Phase 1 completion: 2026-04-14
