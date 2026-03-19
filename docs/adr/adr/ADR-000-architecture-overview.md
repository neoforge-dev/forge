# ADR-000: FORGE v3 Architecture Overview

**Date:** 2026-03-04  
**Status:** Accepted  
**Decision Makers:**
- node-2 (Node Orchestrator)

---

## Context

FORGE has evolved from a single-agent development harness to a multi-agent, multi-node orchestration platform. This ADR provides the high-level architecture that all other ADRs and implementations must follow.

### Key Principles

1. **Local First**: Nodes should work independently with optional upstream coordination
2. **Progressive Disclosure**: Features reveal complexity gradually
3. **Resilience Over Performance**: System must work even if upstream fails
4. **Explicit Over Implicit**: All decisions documented, no magic

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    FORGE v3 Architecture                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  CONTROL PLANE (Optional - for coordination)                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  PRYA (Global Orchestrator)                             │   │
│  │  ├─ Fleet-wide visibility                              │   │
│  │  ├─ Cross-node task routing                            │   │
│  │  └─ Global state aggregation                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│         ┌────────────────────┼────────────────────┐            │
│         │                    │                    │            │
│         ▼                    ▼                    ▼            │
│  ┌───────────┐        ┌───────────┐        ┌───────────┐      │
│  │  SATI     │        │  NOVA     │        │  VEGA     │      │
│  │ (Worker)  │        │ (Worker)  │        │ (Worker)  │      │
│  │           │        │           │        │           │      │
│  │ Local v3  │        │ Local v3  │        │ Local v3  │      │
│  │ 12 agents │        │ 6 agents  │        │ 3 agents  │      │
│  │ Fast      │        │ Fast      │        │ Fast      │      │
│  │ Resilient │        │ Resilient │        │ Resilient │      │
│  └───────────┘        └───────────┘        └───────────┘      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Node Roles

### Control Plane Node (e.g., PRYA)

**Responsibilities:**
- Fleet-wide visibility (optional)
- Cross-node coordination (optional)
- Global state aggregation (optional)

**Characteristics:**
- Light compute (can be small instance)
- If fails, worker nodes continue independently
- Used for: production coordination, global dashboards

### Worker Node (e.g., SATI, NOVA, VEGA)

**Responsibilities:**
- Local task orchestration
- Agent management
- Work execution

**Characteristics:**
- Heavy compute (64GB+ RAM, many cores)
- Self-sufficient (works without control plane)
- Fast (no network hop for local dispatch)
- Resilient (PRYA down doesn't block work)

---

## Dispatch Patterns

### Pattern 1: Local Dispatch (Preferred for Development)

```bash
# On SATI
forge dispatch send forge:kimi "Implement feature"
# → Fast (no network)
# → Works even if PRYA down
# → Best for day-to-day development
```

### Pattern 2: Coordinated Dispatch (Production)

```bash
# Via PRYA
forge dispatch send --node node-2 forge:kimi "Production task"
# → PRYA routes to SATI
# → Global visibility
# → Used for cross-node workflows
```

### Pattern 3: XNode (Cross-Node Communication)

```bash
# File-based protocol
.forge/xnode/lead-outbox/node-1.jsonl
# → Reliable file-based messaging
# → Works even with network issues
# → Eventual consistency
```

---

## Communication Protocols

### Local (Intra-Node)
- **WebSocket**: Real-time agent communication
- **SQLite**: Persistent task queue
- **REST API**: HTTP endpoints for control

### Cross-Node
- **XNode**: File-based messaging (reliable)
- **HTTP API**: Direct REST calls (fast)
- **WebSocket**: Real-time updates (optional)

---

## Data Flow

### Task Lifecycle

```
1. Task Created
   ├─ Local: via API or dispatch
   └─ Cross-node: via XNode or PRYA routing

2. Task Queued
   ├─ SQLite: persistent storage
   └─ Event: logged to event_store

3. Task Dispatched
   ├─ WebSocket: push to agent
   └─ Bridge: tmux integration

4. Task Executed
   ├─ Agent: processes task
   └─ Updates: progress reported

5. Task Completed
   ├─ Result: stored in database
   ├─ GitGuard: commits changes
   └─ Context: envelope generated

6. Task Archived
   ├─ Events: replayable history
   └─ Analytics: metrics recorded
```

---

## Decision Log

### 1. Local First (vs Centralized)
**Decision:** Nodes work independently with optional coordination  
**Rationale:**
- Resilience: PRYA failure doesn't block work
- Performance: No network latency for local tasks
- Simplicity: Easier to understand and debug

**Trade-offs:**
- (+) High availability
- (+) Fast local operations
- (-) Eventual consistency across nodes
- (-) Duplicate code on each node

### 2. SQLite First (vs PostgreSQL Immediately)
**Decision:** SQLite for Phase 1, PostgreSQL for Phase 2+  
**Rationale:**
- Zero operational overhead
- Single file backup
- Sufficient for 95 projects, ~500 tasks/day

**Migration Path:**
- Phase 1: SQLite
- Phase 2: PostgreSQL (500 projects)
- Phase 3: Sharded PostgreSQL (5K projects)
- Phase 4: Kafka (unlimited)

### 3. Go + Python (vs Single Language)
**Decision:** Go for orchestrator, Python for workers  
**Rationale:**
- Go: Single binary, true concurrency, reliable
- Python: LLM ecosystem, familiar to agents

**Trade-offs:**
- (+) Best of both worlds
- (+) Clear separation of concerns
- (-) Two-language debugging
- (-) Serialization overhead

### 4. File-Based XNode (vs Pure Network)
**Decision:** File-based protocol for cross-node  
**Rationale:**
- Reliable (survives network issues)
- Human-readable (can debug directly)
- Git-tracked (audit trail)

**Trade-offs:**
- (+) Eventual consistency
- (+) Works offline
- (-) Slower than pure network
- (-) File I/O overhead

---

## Anti-Patterns

### ❌ Don't: Force All Dispatch Through PRYA
```bash
# BAD: Creates network dependency
forge --node node-1 dispatch send forge:node-2 "Task"
# SATI → PRYA → SATI roundtrip
```

### ✅ Do: Local Dispatch by Default
```bash
# GOOD: Fast, resilient
forge dispatch send forge:kimi "Task"
# Direct local dispatch
```

### ❌ Don't: Assume Always-Connected
```bash
# BAD: System fails if network down
# Assume PRYA is always available
```

### ✅ Do: Design for Partition Tolerance
```bash
# GOOD: Local v3 keeps working
# XNode queues messages for later
```

---

## Implementation Checklist

When implementing new features, verify:

- [ ] Works on single node without PRYA?
- [ ] Can operate with network partitions?
- [ ] Clear error messages when upstream unavailable?
- [ ] Documentation updated?
- [ ] ADR created if architectural decision?

---

## Related Documents

- **ADR-008**: Core v3 rewrite decisions
- **ADR-011**: WebSocket protocol
- **ADR-010**: XNode evolution
- **docs/v3/00_MASTER_PLAN.md**: Implementation roadmap
- **docs/v3/10_IMPLEMENTATION.md**: Setup guide

---

## Future Considerations

### When Models Are 10x Better
- **Local dispatch**: Still preferred (even faster)
- **Race mode**: Launch multiple agents on same task
- **Auto-scaling**: Spawn agents on demand
- **Global optimization**: PRYA coordinates across fleet

### When Scale Grows 100x
- **SQLite → PostgreSQL**: Seamless migration
- **Single node → Sharded**: Transparent to users
- **Synchronous → Event-driven**: Kafka for streaming

---

**This ADR is the single source of truth for FORGE v3 architecture.**

All implementations must follow these principles:
1. Local first
2. Resilient by design
3. Explicit over implicit
4. Progressive disclosure

---

*Architecture defined by node-2 (Node Orchestrator)*  
*FORGE v3 - Local First, Globally Coordinated*
