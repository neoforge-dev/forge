# ADR-021: FORGE v3 Consolidation - Full Control Plane Unification

**Date:** 2026-03-05
**Status:** Superseded by ADR-025
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)

---

## Problem Statement

The current architecture has three major issues:

1. **Command Center Coexistence**: v3 and CC running in parallel creates double maintenance, state conflicts, and deployment complexity
2. **Sidecar File Dependency**: Context monitoring via `.forge/heartbeat/*` files is fragile, laggy (30s polling), and doesn't work cross-node
3. **Fragmented Protocols**: XNode (files), dispatch (files), telemetry (files), patterns (JSON) — each with different failure modes

This ADR proposes a **complete consolidation** into forge v3 with no legacy dependencies.

---

## Proposed Solution: Three-Phase Elimination

### Phase 1: Eliminate Sidecar Files (Week 1-2)

Replace ALL file-based state with SQLite + WebSocket:

| Current (File) | Replaced By | Benefit |
|----------------|-------------|---------|
| `.forge/heartbeat/agents/*.json` | `agent_telemetry` table + WS | Real-time, queryable |
| `.forge/heartbeat/context_percent` | `context_pct` in telemetry | Sub-second updates |
| `.forge/xnode/*.jsonl` | `xnode_messages` table + HTTP API | Works without shared FS |
| `.forge/dispatches/*.md` | `dispatches` table | Cleanup on completion |
| `.forge/learning/patterns.json` | YAML in git + SQLite runs | Version controlled |

### Phase 2: Command Center Feature Migration (Week 2-4)

Port ALL useful CC features to v3:

| CC Feature | v3 Equivalent | Effort |
|------------|---------------|--------|
| Fleet snapshot API | `GET /api/fleet` | 1 day |
| Pattern CRUD API | `GET/POST /api/patterns/*` | 2 days |
| Agent metrics/history | `GET /api/agents/:id/history` | 1 day |
| SSE streaming | SSE shim on event bus | 1 day |
| Node health API | `GET /api/nodes/health` | 1 day |
| Web dashboard | HTMX `/ui/*` | 3 days |

**Total: ~10 days**

### Phase 3: Delete Command Center (Week 5)

```bash
# Remove CC code
rm -rf harness/command_center/

# Remove CC dependencies
rm -rf node_modules/
rm package.json package-lock.json

# Remove CC environment
rm harness/command_center/.env*

# Remove Python webhook server
rm -rf harness/command_center/api/

# Update CI/CD
# Remove React build steps
# Remove uvicorn deployment
```

---

## New Architecture: Single Binary, Single Source of Truth

```
┌─────────────────────────────────────────────────────────────────────┐
│                    FORGE v3 (SOLE CONTROL PLANE)                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │  SINGLE GO BINARY                                             │ │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐ │ │
│  │  │ HTTP :8081  │ │ WS :8082    │ │ SQLite (forge-v3.db)    │ │ │
│  │  │             │ │             │ │                         │ │ │
│  │  │ /api/tasks  │ │ Agent comms │ │ • tasks                 │ │ │
│  │  │ /api/fleet  │ │ Telemetry   │ │ • events                │ │ │
│  │  │ /api/xnode  │ │ Dispatch    │ │ • telemetry (NEW)       │ │ │
│  │  │ /ui/* (HTMX)│ │ Heartbeat   │ │ • xnode_messages (NEW)  │ │ │
│  │  │             │ │             │ │ • dispatches (NEW)      │ │ │
│  │  └─────────────┘ └─────────────┘ │ • approvals             │ │ │
│  │                                  │ • pattern_runs (NEW)     │ │ │
│  │                                  └─────────────────────────┘ │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  NO Python on control plane                                        │
│  NO React build pipeline                                           │
│  NO sidecar files                                                  │
│  NO external dependencies (Redis, NATS, etc.)                     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Better Solution for Context Monitoring

### Current (Broken): Sidecar Files + Polling

```
┌─────────────┐      30s poll       ┌──────────────┐
│ heartbeat-  │ ──────────────────▶ │ .forge/      │
│ loop.sh     │                     │ heartbeat/   │
│ (cron)      │ ◀────────────────── │ *.json       │
└─────────────┘      read/write     └──────────────┘
                                           │
                                           │ stale data
                                           ▼
                                    ┌──────────────┐
                                    │ Patrol reads │
                                    │ files        │
                                    └──────────────┘

Problems:
• 30 second lag
• File write races
• No delivery guarantee
• Doesn't work cross-node
```

### Proposed: Hook Adapters + WebSocket + SQLite

```
┌───────────────────────────────────────────────────────────────────┐
│                    REAL-TIME CONTEXT MONITORING                   │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────┐    Hook Event    ┌─────────────────────────┐   │
│  │ Claude Code │ ───────────────▶ │ Hook Adapter            │   │
│  │             │                  │ (forge-adapter-claude)  │   │
│  │ .claude/    │                  │                         │   │
│  │ hooks/      │                  │ Extract:                │   │
│  │             │                  │ • context_pct           │   │
│  └─────────────┘                  │ • task_id               │   │
│                                   │ • status                │   │
│  ┌─────────────┐                  └───────────┬─────────────┘   │
│  │ OpenCode    │                              │                 │
│  │             │    Hook Event                │                 │
│  │ config/     │ ───────────────▶ ┌───────────┴───────────┐   │
│  │ hooks       │                  │ forge-adapter-opencode │   │
│  └─────────────┘                  └───────────┬───────────┘   │
│                                               │                 │
│                                               │ WS: agent.     │
│                                               │ telemetry      │
│                                               ▼                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    FORGE v3 BINARY                          ││
│  │  ┌──────────────────────────────────────────────────────┐  ││
│  │  │  WebSocket Hub                                       │  ││
│  │  │  • Real-time push to subscribers                    │  ││
│  │  │  • <100ms latency                                   │  ││
│  │  └───────────────────────────┬──────────────────────────┘  ││
│  │                              │                              ││
│  │                              ▼                              ││
│  │  ┌──────────────────────────────────────────────────────┐  ││
│  │  │  SQLite: agent_telemetry                             │  ││
│  │  │  ┌────────────────────────────────────────────────┐ │  ││
│  │  │  │ agent_id | context_pct | status | timestamp   │ │  ││
│  │  │  │ claude-1 | 0.42        | busy   | 14:23:01    │ │  ││
│  │  │  │ kimi-1   | 0.78        | busy   | 14:23:02    │ │  ││
│  │  │  └────────────────────────────────────────────────┘ │  ││
│  │  │  • Queryable history                               │  ││
│  │  │  • Trend analysis                                  │  ││
│  │  │  • Cross-node compatible                           │  ││
│  │  └──────────────────────────────────────────────────────┘  ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                   │
│  Benefits:                                                       │
│  • <100ms latency (vs 30s)                                      │
│  • No file races (single WS connection per agent)               │
│  • Works cross-node (HTTP API for remote queries)               │
│  • Queryable history (SQLite)                                   │
│  • Buffer on disconnect (adapter buffers 100 events)            │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

---

## SQLite Schema Extensions

### New Tables for Unified State

```sql
-- Agent telemetry (replaces heartbeat JSON files)
CREATE TABLE agent_telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    node        TEXT NOT NULL,
    context_pct REAL NOT NULL,
    task_id     TEXT,
    status      TEXT NOT NULL CHECK (status IN ('idle', 'busy', 'wrapup')),
    tool        TEXT NOT NULL,
    metadata    TEXT,  -- JSON: {tokens_used, files_modified, etc.}
    timestamp   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_telemetry_agent ON agent_telemetry(agent_id, timestamp DESC);
CREATE INDEX idx_telemetry_node ON agent_telemetry(node, timestamp DESC);
CREATE INDEX idx_telemetry_status ON agent_telemetry(status, timestamp DESC);

-- Cross-node messages (replaces xnode JSONL files)
CREATE TABLE xnode_messages (
    id              TEXT PRIMARY KEY,
    from_node       TEXT NOT NULL,
    to_node         TEXT NOT NULL,
    task_id         TEXT,
    summary         TEXT NOT NULL,
    priority        TEXT DEFAULT 'normal',
    payload         TEXT,  -- JSON
    status          TEXT DEFAULT 'pending',
    acked_at        TEXT,
    ack_note        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    delivered_at    TEXT
);

CREATE INDEX idx_xnode_pending ON xnode_messages(to_node, status);
CREATE INDEX idx_xnode_sent ON xnode_messages(from_node, created_at DESC);

-- Dispatch queue (replaces dispatch files)
CREATE TABLE dispatches (
    id              TEXT PRIMARY KEY,
    target_agent    TEXT NOT NULL,
    task_id         TEXT,
    message         TEXT NOT NULL,
    context_file    TEXT,  -- Optional reference to detailed file
    priority        TEXT DEFAULT 'normal',
    status          TEXT DEFAULT 'pending',
    acked_at        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at         TEXT
);

CREATE INDEX idx_dispatch_pending ON dispatches(target_agent, status);

-- Pattern runs (replaces learning JSON)
CREATE TABLE pattern_runs (
    run_id      TEXT PRIMARY KEY,
    pattern_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    domain      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    completed_at TEXT,
    success     INTEGER,
    confidence  REAL,
    tokens_used INTEGER,
    duration_s  INTEGER,
    error_type  TEXT,
    metadata    TEXT
);

CREATE INDEX idx_pattern_runs ON pattern_runs(pattern_id, started_at DESC);
```

---

## Implementation Roadmap

### Week 1: Foundation

| Day | Task | Deliverable |
|-----|------|-------------|
| 1 | Schema migration | New tables in `migrations/` |
| 2 | Telemetry API | `POST /api/agents/:id/telemetry` |
| 3 | Claude adapter | `forge-adapter-claude` binary |
| 4 | XNode API | `POST /api/xnode/send`, `/ack` |
| 5 | Dispatch API | `POST /api/dispatch/send` |

### Week 2: Migration

| Day | Task | Deliverable |
|-----|------|-------------|
| 1 | OpenCode adapter | `forge-adapter-opencode` binary |
| 2 | Parallel operation | Adapters write to BOTH v3 + files |
| 3 | Monitoring | Verify parity between systems |
| 4 | Cutover | Switch consumers to v3 APIs |
| 5 | Cleanup | Remove file-based consumers |

### Week 3: CC Feature Port

| Day | Task | Deliverable |
|-----|------|-------------|
| 1 | Fleet API | `GET /api/fleet/snapshot` |
| 2 | Patterns API | `GET/POST /api/patterns/*` |
| 3 | Agent history | `GET /api/agents/:id/history` |
| 4 | SSE shim | `/api/events/stream` |
| 5 | Node health | `GET /api/nodes/health` |

### Week 4: HTMX UI & Testing

| Day | Task | Deliverable |
|-----|------|-------------|
| 1 | HTMX dashboard | `/ui/` with fleet view |
| 2 | HTMX approvals | `/ui/approvals` |
| 3 | E2E testing | Full workflow tests |
| 4 | Documentation | Update all docs |
| 5 | Delete CC | `rm -rf harness/command_center/` |

---

## Benefits Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Control plane** | Python CC + Go v3 | Go v3 only |
| **Context monitoring** | 30s polling, files | <100ms WS, SQLite |
| **Cross-node comms** | Shared FS files | HTTP API + SQLite |
| **Dispatch** | File-based, unreliable | SQLite queue + WS |
| **Patterns** | Monolithic JSON | Git YAML + SQLite runs |
| **Deployment** | 2 binaries, npm, pip | 1 Go binary |
| **Latency** | 30s (polling) | <100ms (real-time) |
| **Reliability** | Fragile (files) | 99%+ (SQLite + WS) |
| **Observability** | Log files | Queryable SQLite |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Hook adapter complexity | Start with Claude only, iterate |
| SQLite storage growth | Retention patrol (30 days) |
| Migration bugs | 2-week parallel operation period |
| Missing CC features | Audit all endpoints before deletion |
| Team Go familiarity | Paired programming, code reviews |

---

## Decision

> **NOTE (2026-03-06):** This ADR is superseded by ADR-025 (Local Daemon per Node).
> The centralized control plane approach was rejected. Do not implement ADR-021.
> See ADR-025 for the accepted architecture.

**PROPOSED**: Implement complete consolidation following the 4-week roadmap.

**Acceptance Criteria:**
1. Zero sidecar files in `.forge/`
2. Zero Python on control plane
3. All CC features available in v3
4. Context monitoring latency <1s
5. Cross-node messaging via HTTP/SQLite
6. Single Go binary deployment

---

## Related Decisions

- ADR-014: Retire Command Center
- ADR-015: Agent Telemetry Protocol
- ADR-016: Hook Adapter Architecture
- ADR-017: Unified Event Bus
- ADR-020: Eliminate Sidecar Files

---

**Status: PROPOSED**

Implementation target: 4 weeks
Decision review: 2026-03-10
