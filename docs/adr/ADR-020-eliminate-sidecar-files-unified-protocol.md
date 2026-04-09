# ADR-020: Eliminate Sidecar Files - Unified Telemetry & Control Protocol

**Date:** 2026-03-05
**Status:** Proposed
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)

---

## Context

The current FORGE system relies heavily on **sidecar files** for cross-component communication:

| Sidecar Pattern | Location | Purpose | Problem |
|-----------------|----------|---------|---------|
| Agent heartbeat JSON | `.forge/heartbeat/agents/*.json` | Agent status, context % | 30s polling lag, file races |
| Context percent | `.forge/heartbeat/context_percent` | Single number, polled | Lost granularity |
| XNode messages | `.forge/xnode/lead-inbox/*.jsonl` | Cross-node comms | Manual polling, no delivery guarantee |
| Dispatch files | `.forge/dispatches/*.md` | Task descriptions | Orphaned files, no cleanup |
| Pattern JSON | `.forge/learning/patterns.json` | ML patterns | Monolithic, not queryable |
| Royal Jelly context | `.forge/context/{domain}/*.md` | Domain knowledge | Manual sync, drift |

### Why Sidecar Files Are Problematic

1. **No Delivery Guarantee**: Writing a file doesn't notify consumers
2. **Polling Lag**: Consumers must poll, creating latency (currently 30s)
3. **Race Conditions**: Multiple writers cause corruption
4. **No Schema Enforcement**: Files can drift without validation
5. **Cross-Node Failure**: File-based XNode fails without shared filesystem
6. **No Replay**: Once consumed, data is lost (or requires append-only logs)
7. **Observability Gap**: Hard to trace what was written when

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Keep sidecars, improve polling | No code change | Still lag, races, no guarantees | ❌ REJECTED |
| Redis pub/sub | Battle-tested, fast | External dependency, ops overhead | ❌ REJECTED |
| Message queue (NATS/RabbitMQ) | Durable, reliable | Overkill for single-node, added complexity | ❌ REJECTED |
| **In-process event bus + SQLite** | **Zero deps, persistent, real-time, queryable** | **Single-node ceiling (acceptable)** | ✅ **ACCEPTED** |

---

## Decision

Replace ALL sidecar files with a **unified telemetry and control protocol** flowing through the v3 WebSocket/HTTP APIs. The v3 binary becomes the single source of truth.

### What Dies (Complete Elimination)

| Current Sidecar | Dies With | Replaced By |
|-----------------|-----------|-------------|
| `.forge/heartbeat/agents/*.json` | ADR-015 | `agent.telemetry` WS messages → `agent_telemetry` table |
| `.forge/heartbeat/context_percent` | ADR-015 | `context_pct` field in telemetry |
| `.forge/xnode/lead-*.jsonl` | This ADR | `xnode.send`/`xnode.ack` WS messages → `xnode_messages` table |
| `.forge/dispatches/*.md` | This ADR | `dispatch.send` WS/HTTP → `dispatches` table |
| `.forge/learning/patterns.json` | ADR-018 | `.forge/patterns/*.yaml` (git) + `pattern_runs` (SQLite) |
| `.forge/heartbeat/tasks/*.json` | This ADR | `tasks` table, event-sourced |

### Architecture: Single Source of Truth

```
┌─────────────────────────────────────────────────────────────────────┐
│                    FORGE v3 BINARY                                  │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  SINGLE SOURCE OF TRUTH                                     │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │   │
│  │  │   SQLite    │  │  Event Bus  │  │  WebSocket Hub      │  │   │
│  │  │  (persist)  │←→│ (real-time) │←→│  (agent comms)      │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘  │   │
│  │         ↑                 ↑                    ↑             │   │
│  │         │                 │                    │             │   │
│  │  HTTP API (:8081)   SSE Stream (:8081)   WebSocket (:8082)   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
              ↑                       ↑                    ↑
              │                       │                    │
      ┌───────┴───────┐       ┌───────┴───────┐    ┌───────┴───────┐
      │   CLI / TUI   │       │  HTMX Web UI  │    │  iOS App      │
      │   forge ...   │       │   /ui/*       │    │  (Swift)      │
      └───────────────┘       └───────────────┘    └───────────────┘
```

**Key Principle**: All state lives in v3 SQLite. All communication flows through v3 APIs. No sidecar files anywhere.

---

## Protocol Extensions

### 1. Telemetry Protocol (ADR-015 Enhanced)

Replace heartbeat sidecars with real-time WebSocket telemetry:

```json
{
  "type": "agent.telemetry",
  "agent_id": "claude-nova-1",
  "node": "nova",
  "context_pct": 0.42,
  "status": "busy",
  "task_id": "01JQXYZ...",
  "metadata": {
    "tokens_used": 84200,
    "tokens_limit": 200000,
    "last_tool_call": "edit_file",
    "files_modified": 3,
    "tests_run": 12,
    "tests_passed": 11
  },
  "timestamp": "2026-03-05T14:23:01Z"
}
```

**Delivery**: WebSocket push (sub-second) + SQLite append (persistent)

### 2. XNode Protocol (Cross-Node Messaging)

Replace file-based XNode with SQLite-backed message queue:

**Send Message:**
```json
// POST /api/xnode/send or WS message
{
  "type": "xnode.send",
  "to_node": "sati",
  "task_id": "01JQXYZ...",
  "summary": "Deploy interview-simulator hotfix",
  "priority": "high",
  "payload": {
    "branch": "node/sati/IS-219",
    "actions": ["test", "deploy"]
  }
}
```

**Response:**
```json
{
  "type": "xnode.sent",
  "message_id": "MSG-01JQABC...",
  "to_node": "sati",
  "status": "queued",
  "delivered_at": "2026-03-05T14:23:01Z"
}
```

**Ack Message:**
```json
// POST /api/xnode/ack or WS message
{
  "type": "xnode.ack",
  "message_id": "MSG-01JQABC...",
  "status": "completed",  // or "failed", "nack"
  "note": "Deployed successfully to staging"
}
```

**SQLite Schema:**
```sql
CREATE TABLE xnode_messages (
    id              TEXT PRIMARY KEY,
    from_node       TEXT NOT NULL,
    to_node         TEXT NOT NULL,
    task_id         TEXT,
    summary         TEXT NOT NULL,
    priority        TEXT DEFAULT 'normal',
    payload         TEXT,
    status          TEXT DEFAULT 'pending',  -- pending, delivered, acked, failed
    acked_at        TEXT,
    ack_note        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    delivered_at    TEXT
);

CREATE INDEX idx_xnode_to ON xnode_messages(to_node, status);
CREATE INDEX idx_xnode_from ON xnode_messages(from_node, created_at DESC);
```

**Delivery Mechanism:**
- Messages stored in SQLite (persistent, queryable)
- Target node polls via HTTP `GET /api/xnode/pending` OR
- WebSocket subscription `{"type": "xnode.subscribe"}`
- SSE stream for nodes without WebSocket support

### 3. Dispatch Protocol

Replace dispatch files with structured dispatch queue:

```json
// POST /api/dispatch/send
{
  "type": "dispatch.send",
  "target_agent": "forge:kimi",
  "task_reference": "01JQXYZ...",
  "message": "Implement OAuth2 login",  // Short inline
  "context_file": ".forge/dispatches/oauth2.md",  // Optional: reference to detailed file
  "priority": "normal",
  "requires_ack": true
}
```

**SQLite Schema:**
```sql
CREATE TABLE dispatches (
    id              TEXT PRIMARY KEY,
    target_agent    TEXT NOT NULL,
    task_id         TEXT,
    message         TEXT NOT NULL,
    context_file    TEXT,
    priority        TEXT DEFAULT 'normal',
    status          TEXT DEFAULT 'pending',  -- pending, sent, acked, failed
    acked_at        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at         TEXT
);
```

### 4. Context Envelope Protocol

Replace manual Royal Jelly sync with event-sourced context sync:

```json
// POST /api/context/envelope
{
  "type": "context.envelope",
  "domain": "codeswiftr-com",
  "project": "interview-simulator",
  "trigger": "context_threshold",  // or "task_complete", "handoff"
  "envelope": {
    "summary": "Implemented OAuth2 login...",
    "key_changes": ["auth.py", "routes/login.py"],
    "pending_decisions": [],
    "tests_status": "passing",
    "git_status": {"branch": "feature/oauth2", "uncommitted": 0}
  }
}
```

**Storage:** `context_envelopes` table + `.forge/context/{domain}/envelope-{timestamp}.md` (human-readable backup)

---

## Hook Adapters (ADR-016 Enhanced)

Each agent CLI gets a **thin adapter** that:

1. **Captures** hook events from the CLI
2. **Normalizes** to standard telemetry format
3. **Sends** via WebSocket to v3
4. **Buffers** locally if disconnected (max 100 events)

### Adapter Output Contract

Every adapter MUST output ONLY these message types:

| Message Type | When Sent | Frequency |
|--------------|-----------|-----------|
| `agent.telemetry` | State change OR 30s heartbeat | Sub-second to 30s |
| `task.started` | Agent begins task | Once |
| `task.progress` | Significant progress | Optional |
| `task.completed` | Task finished | Once |
| `task.failed` | Task failed | Once |
| `xnode.ack` | Acknowledge cross-node message | On receipt |

Every adapter MUST NOT:

- Write ANY files to `.forge/heartbeat/`
- Poll any files
- Read or modify SQLite directly
- Make decisions about approvals or routing

### Adapter Implementations

| CLI | Hook Source | Extractor Logic |
|-----|-------------|-----------------|
| Claude Code | `.claude/hooks/` | Parse hook JSON, extract context % from `output` |
| OpenCode | Config hooks | Parse stdout for context indicators |
| Gemini CLI | Output stream | Regex parse for context markers |
| Kimi (via OpenCode) | Inherit OpenCode | Same as OpenCode |

---

## Benefits: Why This Is Better

### 1. Real-Time Delivery

| Metric | Sidecar Files | Unified Protocol |
|--------|---------------|------------------|
| Latency | 30s (polling) | <100ms (WebSocket) |
| Delivery guarantee | None | At-least-once (buffered) |
| Observability | Log files | Queryable SQLite |

### 2. Cross-Node Reliability

| Scenario | Sidecar XNode | SQLite XNode |
|----------|---------------|--------------|
| Network partition | Lost messages | Queued in SQLite |
| Node reboot | Lost messages | Persistent in SQLite |
| Shared FS unavailable | Total failure | Local SQLite, sync later |

### 3. Queryability

```sql
-- Find all agents approaching context exhaustion (impossible with files)
SELECT agent_id, AVG(context_pct) as avg_context, COUNT(*) as samples
FROM agent_telemetry
WHERE timestamp > datetime('now', '-1 hour')
GROUP BY agent_id
HAVING avg_context > 0.5;

-- Find slow cross-node deliveries
SELECT message_id, to_node,
       julianday(delivered_at) - julianday(created_at) as latency_hours
FROM xnode_messages
WHERE delivered_at IS NOT NULL
ORDER BY latency_hours DESC
LIMIT 10;
```

### 4. Replay & Debugging

All events stored in SQLite enable:
- **Time-travel debugging**: What happened at 2pm yesterday?
- **Trend analysis**: Is context exhaustion increasing?
- **Audit trail**: Who sent what message when?

---

## Migration Plan

### Phase 1: Parallel Operation (Week 1-2)

- Deploy v3 with unified protocol
- Keep sidecar files as backup
- Adapters send to BOTH v3 and write files
- Verify parity between systems

### Phase 2: Cutover (Week 3)

- Switch consumers to v3 APIs
- Adapters stop writing files
- Monitor for issues

### Phase 3: Cleanup (Week 4)

```bash
# Delete sidecar infrastructure
rm -rf .forge/heartbeat/agents/
rm -f .forge/heartbeat/context_percent
rm -rf .forge/xnode/
rm -rf .forge/dispatches/*.md  # Keep only as reference
rm -f scripts/heartbeat-loop.sh
rm -f scripts/v2-*.sh

# Update AGENTS.md
# Remove all references to file-based protocols
```

---

## Consequences

### Positive

1. **Real-time visibility**: Sub-second context monitoring vs 30s lag
2. **Reliable cross-node**: SQLite-backed message queue survives partitions
3. **Queryable history**: All telemetry in SQLite enables analysis
4. **Simpler architecture**: One protocol, one source of truth
5. **No file races**: Single-writer per connection via WebSocket
6. **Works without shared FS**: Each node has local SQLite

### Negative

1. **Adapter dependency**: Each CLI needs a hook adapter
2. **Storage growth**: SQLite grows with telemetry (mitigated by retention patrol)
3. **Migration effort**: 4 weeks of parallel operation

### Neutral

1. **SQLite is local**: Cross-node sync is async (acceptable for telemetry)
2. **Protocol versioning**: v1 protocol can evolve to v2 without breaking

---

## Related Decisions

- ADR-014: Retire Command Center (v3 becomes sole control plane)
- ADR-015: Agent Telemetry Protocol (defines telemetry message)
- ADR-016: Hook Adapter Architecture (adapters produce telemetry)
- ADR-017: Unified Event Bus (all events flow through bus)
- ADR-018: Pattern Library (replaces learning JSON)

---

## Implementation Priority

| Priority | Component | Est. Time |
|----------|-----------|-----------|
| P0 | Claude Code adapter | 2 days |
| P0 | Telemetry SQLite schema | 1 day |
| P1 | XNode SQLite + API | 2 days |
| P1 | OpenCode adapter | 2 days |
| P2 | Dispatch queue | 1 day |
| P2 | Context envelope sync | 2 days |
| P3 | Gemini adapter | 2 days |
| P3 | Migration scripts | 2 days |

**Total: ~2 weeks for core implementation**

---

**Status: PROPOSED**

Decision review target: 2026-03-10
