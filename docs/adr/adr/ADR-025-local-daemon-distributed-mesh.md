# ADR-025: Local Daemon per Node - Resolving Partition Tolerance

**Date:** 2026-03-05
**Status:** Accepted
**Decision Makers:** Bogdan Veliscu (CTO, FORGE)
**Amends:** ADR-000, ADR-008, ADR-020, ADR-021

---

## Context

A fundamental tension exists between:

| ADR | Principle | Implication |
|-----|-----------|-------------|
| **ADR-000** | "Local First, Globally Coordinated" | Partition tolerance is mandatory |
| **ADR-008** | "Filesystem as Source of Truth" | Works offline, eventual consistency |
| **ADR-020** | "Eliminate ALL sidecar files" | Single source of truth in SQLite |
| **ADR-021** | "Unified Control Plane" | Centralized WebSocket/HTTP |

### The Contradiction

If we eliminate ALL file-based protocols and centralize on a single SQLite instance with WebSocket, we lose partition tolerance:

```
Current Proposal (PROBLEMATIC):
┌─────────────────────────────────────────────────────────────────────┐
│                         PRYA (Central Node)                        │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Single Source of Truth: SQLite + WebSocket                   │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
         ↑                ↑                ↑
         │ WebSocket      │ WebSocket      │ WebSocket
    ┌────┴────┐      ┌────┴────┐      ┌────┴────┐
    │  SATI   │      │  NOVA   │      │  GAEA   │
    │ Worker  │      │ Worker  │      │ Worker  │
    └─────────┘      └─────────┘      └─────────┘
    
    ❌ If PRYA goes down, ALL workers stop
    ❌ Network partition = total system failure
    ❌ Violates ADR-000 "Local First"
```

### The Resolution

Each node runs its own local forge-v3 Go daemon. Daemons communicate via file-based XNode:

```
Proposed Architecture (RESOLVED):
┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│        SATI         │   │        NOVA         │   │        GAEA         │
│  ┌───────────────┐  │   │  ┌───────────────┐  │   │  ┌───────────────┐  │
│  │ forge-v3 Go   │  │   │  │ forge-v3 Go   │  │   │  │ forge-v3 Go   │  │
│  │ (Local Daemon)│  │   │  │ (Local Daemon)│  │   │  │ (Local Daemon)│  │
│  │               │  │   │  │               │  │   │  │               │  │
│  │ SQLite DB     │  │   │  │ SQLite DB     │  │   │  │ SQLite DB     │  │
│  │ WebSocket:8082│  │   │  │ WebSocket:8082│  │   │  │ WebSocket:8082│  │
│  └───────────────┘  │   │  └───────────────┘  │   │  └───────────────┘  │
│         ↑           │   │         ↑           │   │         ↑           │
│         │ WS        │   │         │ WS        │   │         │ WS        │
│    ┌────┴────┐      │   │    ┌────┴────┐      │   │    ┌────┴────┐      │
│    │ Workers │      │   │    │ Workers │      │   │    │ Workers │      │
│    │ (kimi,  │      │   │    │ (claude,│      │   │    │ (claude)│      │
│    │  gemini)│      │   │    │  cursor)│      │   │    │         │      │
│    └─────────┘      │   │    └─────────┘      │   │    └─────────┘      │
└─────────────────────┘   └─────────────────────┘   └─────────────────────┘
         │                        │                        │
         │    XNode (File-Based)  │                        │
         │    .forge/xnode/       │                        │
         └────────────────────────┴────────────────────────┘
                        ↓
              Tailscale/Syncthing Sync
              (Eventually Consistent)
              
    ✅ Each node works independently
    ✅ Network partition = degraded, not failed
    ✅ Satisfies ADR-000 "Local First"
```

---

## Decision

### 1. Local Daemon per Node

**Each node runs its own forge-v3 Go binary:**

| Node | Daemon | Database | Workers |
|------|--------|----------|---------|
| node-2 | forge-v3 (PID xxx) | .forge/forge-v3.db | kimi, gemini, opencode |
| node-3 | forge-v3 (PID xxx) | .forge/forge-v3.db | claude, cursor |
| node-1 | forge-v3 (PID xxx) | .forge/forge-v3.db | orchestrator, minimax |
| node-5 | forge-v3 (PID xxx) | .forge/forge-v3.db | claude |

### 2. Worker-to-Daemon Communication (Intra-Node)

Workers connect to their **localhost** daemon via WebSocket:

```python
# Python Hook Adapter (ADR-016)
class WorkerAdapter:
    def __init__(self):
        # Connect to LOCAL daemon, not remote
        self.ws_url = "ws://localhost:8082/ws"
        self.node_id = socket.gethostname()
```

**Benefits:**
- Zero network latency for telemetry
- Works even if other nodes are unreachable
- Sub-second context reporting

### 3. Daemon-to-Daemon Communication (Cross-Node)

Daemons communicate via **file-based XNode** (ADR-023):

```
.forge/xnode/
├── lead-inbox/
│   ├── node-2.jsonl    ← Messages FROM node-2 (read by all nodes)
│   ├── node-3.jsonl    ← Messages FROM node-3
│   └── node-1.jsonl    ← Messages FROM node-1
└── lead-outbox/
    ├── node-2.jsonl    ← Messages TO node-2
    └── node-3.jsonl    ← Messages TO node-3
```

**Sync Mechanism:**
- Tailscale for real-time (when available)
- Syncthing for reliable sync (eventual consistency)
- Git for audit trail (manual or scheduled commits)

### 4. What Dies vs What Lives

| Component | Status | Reason |
|-----------|--------|--------|
| `.forge/heartbeat/agents/*.json` | **DIES** | Replaced by WS telemetry to local daemon |
| `.forge/heartbeat/context_percent` | **DIES** | Included in telemetry message |
| `.forge/dispatches/*.md` | **DIES** | Replaced by tasks table in local SQLite |
| `.forge/xnode/*.jsonl` | **LIVES** | Cross-node communication backbone |
| `.forge/context/{domain}/*.md` | **LIVES** | Domain knowledge (git-tracked) |

---

## Reconciliation with ADR-020

### ADR-020 Amendment

**Original ADR-020 Statement:**
> "Replace ALL sidecar files with unified telemetry and control protocol flowing through v3 WebSocket/HTTP APIs."

**Amended Statement:**
> "Replace local sidecar files with unified telemetry to the local forge-v3 daemon. Preserve file-based XNode for cross-node communication to maintain partition tolerance."

### What Changes in ADR-020

```diff
- # Delete sidecar infrastructure
- rm -rf .forge/xnode/
+ # Delete local sidecars, KEEP xnode
+ rm -rf .forge/heartbeat/agents/
+ rm -f .forge/heartbeat/context_percent
+ # .forge/xnode/ PRESERVED for cross-node messaging
```

---

## Implementation

### Phase 1: Deploy Local Daemons

```bash
# On each node (node-2, node-3, node-1, node-5)
cd ./cmd/forge-v3
go build -o forge-v3 .
./forge-v3 --node node-2 --port 8081 &

# Verify local daemon
curl http://localhost:8081/api/health
```

### Phase 2: Update Hook Adapters

```python
# Before (broken - assumes central server)
WS_URL = os.environ.get("FORGE_WS_URL", "ws://node-1:8082/ws")

# After (correct - local daemon)
WS_URL = "ws://localhost:8082/ws"
```

### Phase 3: Cross-Node Sync

```bash
# XNode sync via Tailscale
syncthing --config=~/.config/syncthing --no-browser &

# Or via systemd
systemctl --user enable syncthing@forge-xnode
```

---

## Consequences

### Positive

1. **Partition Tolerance Restored**: ADR-000 principle preserved
2. **Zero Single Point of Failure**: Each node works independently
3. **Real-Time Telemetry**: WebSocket to localhost = sub-second latency
4. **Eventual Consistency**: XNode files sync when network available
5. **Git Audit Trail**: XNode messages can be committed for history

### Negative

1. **Multiple SQLite Databases**: State is distributed, not centralized
2. **Sync Complexity**: Need Syncthing or similar for file sync
3. **Conflict Resolution**: Concurrent writes to XNode need coordination
4. **No Global Real-Time View**: Must aggregate from all nodes for dashboard

### Mitigations

- SQLite: Implement periodic aggregation for global view
- Sync: Syncthing is battle-tested, minimal ops overhead
- Conflicts: XNode uses append-only JSONL, idempotency keys
- Dashboard: Build aggregation API that queries all node APIs

---

## Related Decisions

- **ADR-000**: Architecture Overview (this ADR restores its principles)
- **ADR-008**: Forge v3 Rewrite (local-first design)
- **ADR-020**: Eliminate Sidecars (amended by this ADR)
- **ADR-021**: Unified Control Plane (amended: one per node)
- **ADR-023**: XNode Evolution (file-based protocol preserved)
- **ADR-024**: Git Lock Isolation (worktrees per task)

---

**Status: PROPOSED**

This ADR resolves the philosophical contradiction identified in the v3 architecture review.

*Architecture coherence restored: Local-First + Real-Time + Partition-Tolerant*
