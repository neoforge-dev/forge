# ADR-009: Agentic Orchestration Patterns Integration

**Date:** 2026-03-02
**Status:** Accepted
**Decision Makers:**
- kilo (pool-t2 agent, Architecture Review)
- cursor (pool-t2 agent, Technical Review)
- amp (pool-t2 agent, Operations Review)
- gemini (nf.lead agent, Strategic Review)

---

## Context

During the FORGE CLI v3 specification process, comprehensive research on agentic orchestration patterns was conducted. Sources included:

- **Microsoft Learn**: Magentic orchestration with dynamic task ledgers
- **Anthropic**: Production multi-agent system insights
- **GitHub**: Mission control operator experience patterns
- **Academic research**: AgileCoder (sprint-based), Flow (DAG workflows), ReAct/Self-Refine
- **Industry practitioners**: Brethorst (race mode), Osmani (competitive validation)

The question: Which of these patterns should FORGE v3 adopt, defer, or reject?

### Evaluation Criteria

1. **Operational reliability** - Checkpoints, retries, resumability
2. **Human governance** - Approval gates without killing velocity
3. **Parallelism safety** - Isolation primitives, deterministic verification
4. **Extensibility** - Don't lock into rigid workflow engines

### Alternatives Considered

| Pattern | Source | Pros | Cons | Verdict |
|---------|--------|------|------|---------|
| **Magentic Ledger** | Microsoft | Versioned plans, audit trail, backtracking | Schema complexity | ✅ **ADOPT** |
| **Race Mode** | Brethorst/Osmani | Quality through competition | 2-4× token cost | ✅ **ADOPT (constrained)** |
| **Mission Control** | GitHub | Real-time oversight, steering | 4-6× operator burden | ✅ **ADOPT (opt-in)** |
| **Sprint-Based** | AgileCoder | Familiar agile model | High token cost, over-engineering | ❌ **REJECT** |
| **Event-Driven (Kafka)** | Confluent | Decoupled, scalable | 3 orders of magnitude over-provisioned | ❌ **REJECT** |
| **Flow DAG** | ICLR 2025 | Modular workflows | Research-stage complexity | ⚠️ **DEFER** |

---

## Decision

### ADOPT: Magentic Ledger (Phase 1)

**What:** Versioned, evolving plan entity with audit trail between feature request and task execution.

**Schema:**
```sql
CREATE TABLE plan_versions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    plan TEXT NOT NULL,  -- JSON: objective, steps, constraints
    reason TEXT,         -- Why this version exists
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE plan_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- plan.created, plan.revised, plan.completed
    payload TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

**Task Lifecycle Extension:**
```
REQUESTED → PLANNED → QUEUED → ASSIGNED → EXECUTING → COMPLETED
         ↑___________|
              (replanning)
```

**Rationale:**
- FORGE already has event-sourced task log (80% there)
- Adds "intent durability" - the *why* behind decisions
- Enables Context Archaeology (understanding past decisions)
- Differentiates from Temporal.io (code durability → intent durability)

**Trade-offs:**
- (+) Audit trail for all planning decisions
- (+) Backtracking and replanning support
- (+) Natural fit with existing event sourcing
- (-) Additional schema complexity
- (-) Need to version plan schema over time

---

### ADOPT: Race Mode (Phase 3, Constrained)

**What:** Spin up N isolated instances on git worktrees, deterministic tests select winner.

**Configuration:**
```go
type RaceConfig struct {
    Enabled       bool
    DefaultCount  int  // 1 = no racing
    MaxCount      int  // Hard cap: 4
    
    // Operational constraints
    MaxConcurrentRaces   int  // 3 fleet-wide
    VerificationTimeout  time.Duration  // 10-20 min
    
    Triggers []RaceTrigger
}

var DefaultRaceTriggers = []RaceTrigger{
    {TaskType: "security_change", RiskTier: "high", MinRaceCount: 2},
    {TaskType: "security_change", RiskTier: "critical", MinRaceCount: 3},
    {TaskType: "architecture_decision", RiskTier: "*", MinRaceCount: 2},
    {TaskType: "database_migration", RiskTier: "high", MinRaceCount: 2},
}
```

**Constraints (from operational analysis):**
- Max 3 concurrent races fleet-wide
- Nova/sati nodes only (prya has CC overhead, OOM risk)
- Early termination at confidence >0.95
- Never race T1 tasks (waste rate 50-75%)

**Break-Even Analysis:**

| Scenario | Wrong-Choice Cost | Race Worth It? |
|----------|-------------------|----------------|
| T1 task (typo fix) | ~5 min redo | ❌ Never |
| T2 bug fix (clear) | ~30 min redo | ❌ Never |
| T3 architecture | ~1 week if wrong | ✅ Always |
| Security-critical | Breach = catastrophic | ✅ Always |

**Rationale:**
- Quality through competition for high-stakes decisions
- Cost justified when "cost of choosing wrong >> cost of wasted compute"
- Automated governance reduces human review burden

**Trade-offs:**
- (+) Higher quality for critical tasks
- (+) Objective selection via tests
- (+) Reduces human review burden
- (-) 2-4× token cost
- (-) Complex worktree management
- (-) Resource exhaustion risk without constraints

---

### ADOPT: Mission Control (Phase 4, Opt-In Only)

**What:** Centralized oversight with real-time logs, pause/resume, steering capabilities.

**Implementation:**
```bash
# Normal mode (default) - 14-55 min/day operator burden
forge daemon --mode autonomous

# Mission control mode (opt-in) - for critical periods
forge daemon --mode mission-control
# → Disables auto-approve
# → Streams all events to TUI
# → Requires human ACK per task
# → Auto-reverts to autonomous after 4 hours
```

**TUI Extensions:**
- Real-time task logs via WebSocket
- Task detail panel with live events
- Pause/resume controls
- PR/branch jump links
- Plan history viewer

**Operator Burden Comparison:**

| Model | Daily Time | Verdict |
|-------|-----------|---------|
| Current v2 | 60-170 min | Baseline |
| Full mission control | 350-760 min | ❌ Rejected (4-6× increase) |
| v3 automated | 14-55 min | ✅ Target |
| Mission control opt-in | +variable | ✅ For critical periods |

**Rationale:**
- Essential for B2B/Enterprise trust and safety
- Provides "pilot cockpit" UX for critical operations
- Proactive steering vs reactive approvals

**Trade-offs:**
- (+) Real-time visibility and control
- (+) Critical for production-grade autonomy
- (+) Enables trust in autonomous systems
- (-) 4-6× operator burden if default
- (-) Can become "shiny object" distraction

---

### REJECT: Sprint-Based Multi-Agent

**Rationale:**
- Over-engineering for current scale (4 nodes, 10 agents)
- High token cost reported in AgileCoder research
- v3's simpler task-based model sufficient
- Can simulate with Dark Factory lanes if needed

**Alternative:** Use Dark Factory lanes for workflow stages

---

### REJECT: Kafka/EventBridge Event-Driven Architecture

**Rationale:**
- **3 orders of magnitude over-provisioned** for current scale
- SQLite handles ~500 writes/second; FORGE uses ~0.006 writes/second
- Adds 2+ services to manage (Kafka, ZooKeeper)
- Operational complexity >> value at 4 nodes

**Current Scale:**
- Tasks: ~20-50/day = 0.001 writes/sec
- Events: ~200-500/day = 0.006 writes/sec
- Heartbeats: 10 agents × 1/min = 0.17 writes/sec
- **Headroom: 3 orders of magnitude**

**Migration Path:**

| Scale | Backend | When |
|-------|---------|------|
| Current (95 projects) | SQLite + WebSocket | Now - 2027 |
| Medium (500 projects) | PostgreSQL + SSE | >5K tasks/day |
| Large (5K+ projects) | Kafka + PostgreSQL | >50K tasks/day |

**Design for Future:**
- Treat `task_events` as canonical log
- Use schema compatible with Kafka topics
- Keep consumers decoupled via interfaces

---

## Consequences

### Positive

1. **Intent Durability** - Magentic Ledger makes the *why* as durable as the *what*
2. **Quality Governance** - Race mode automates quality for critical decisions
3. **Trust & Safety** - Mission control enables production-grade autonomy
4. **Differentiation** - "OS for Durable Planning" vs "code executor"
5. **Sustainability** - Constraints prevent resource exhaustion

### Negative

1. **Schema Complexity** - Additional tables for plan versioning
2. **Token Cost** - Race mode 2-4× cost for critical tasks
3. **Implementation Time** - 8 additional days across phases
4. **Learning Curve** - New concepts for operators

### Neutral

1. **Scope Expansion** - Phase 1 now includes magentic ledger
2. **Deferred Complexity** - Kafka rejected, SQLite maintained
3. **Operational Model** - Opt-in preserves solo-operator sustainability

---

## Implementation Timeline

### Phase 1 (Weeks 1-8): Magentic Ledger
- Schema: `plan_versions`, `plan_events` tables
- Task lifecycle: add `PLANNED` state
- CLI: `forge task plan`, `forge task replan`

### Phase 2 (Weeks 9-12): Mission Control Foundation
- TUI extensions for real-time logs
- Plan history viewer
- Pause/resume API

### Phase 3 (Weeks 13-16): Race Mode
- RaceManager with worktree isolation
- Token budget integration
- Flag-gated triggers

### Phase 4 (Week 17+): Mission Control Opt-In
- `--mode mission-control` flag
- Auto-revert after 4 hours

---

## Related Decisions

- ADR-008: FORGE CLI v3 Rewrite (parent decision)
- ADR-010: XNode Protocol Evolution (cross-node coordination)
- ADR-011: Lease System Design (race mode integration)

## References

- FORGE CLI v3 Agentic Patterns Final: `docs/plans/FORGE_CLI_V3_AGENTIC_PATTERNS_FINAL.md`
- Microsoft Learn: Magentic Orchestration Patterns
- Anthropic: Multi-Agent Research System
- GitHub: Mission Control Best Practices
- Brethorst: Orchestrating Agentic Coding
- AgileCoder: arXiv:2406.11912

---

**Status: ACCEPTED**

Magentic Ledger: Phase 1 (Weeks 1-8)
Race Mode: Phase 3 (Weeks 13-16)
Mission Control: Phase 4 (Week 17+)
