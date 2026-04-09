# ADR-017: Unified Event Bus and Streaming Strategy

**Date:** 2026-03-05
**Status:** Proposed
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)

---

## Context

FORGE currently has three separate event/streaming mechanisms:

1. **Command Center SSE**: Python FastAPI endpoint streaming events to React PWA and CLI consumers
2. **File-based heartbeat polling**: Scripts read `.forge/heartbeat/` files to detect state changes
3. **Separate webhook endpoints**: Individual HTTP endpoints for different event types (task updates, approvals, fleet changes)

This fragmentation means consumers must know which mechanism to use for which event type, events are not correlated, and there's no unified history or replay capability.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Keep SSE (upgrade Python) | Minimal change | Still two streaming paths, Python on control plane | ❌ REJECTED |
| External message broker (Redis/NATS) | Battle-tested, high throughput | Operational overhead, external dependency | ❌ REJECTED |
| **In-process event bus + SQLite backing** | **Zero deps, single process, persistent, replayable** | **Single-node throughput ceiling** | ✅ **ACCEPTED** |

---

## Decision

All domain events flow through a **single in-process event bus** backed by the existing SQLite `task_events` table. WebSocket subscriptions are the primary consumer interface. SSE is provided as a thin compatibility shim.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  Event Producers                                     │
│  (task updates, agent telemetry, approvals,          │
│   patrol findings, git events, lane transitions)     │
└──────────────────────┬──────────────────────────────┘
                       │ publish()
                       ▼
┌─────────────────────────────────────────────────────┐
│  In-Process Event Bus                                │
│  ┌─────────────┐  ┌─────────────────────────────┐   │
│  │ SQLite       │  │ In-Memory PubSub            │   │
│  │ task_events  │←→│ topic → []subscriber        │   │
│  │ (persistent) │  │ (real-time fan-out)         │   │
│  └─────────────┘  └──────────┬──────────────────┘   │
└──────────────────────────────┼──────────────────────┘
                       ┌───────┴───────┐
                       ▼               ▼
              ┌──────────────┐ ┌──────────────┐
              │ WebSocket    │ │ SSE Shim     │
              │ Subscribers  │ │ /api/events/ │
              │ (primary)    │ │ stream       │
              └──────────────┘ └──────────────┘
```

### Event Topics

| Topic | Events | Example |
|-------|--------|---------|
| `task.*` | task.created, task.assigned, task.started, task.completed, task.failed | Task lifecycle |
| `agent.*` | agent.registered, agent.telemetry, agent.disconnected, agent.exhausted | Agent lifecycle (ADR-015) |
| `approval.*` | approval.requested, approval.granted, approval.rejected, approval.expired | HITL gates |
| `lane.*` | lane.promoted, lane.blocked, lane.rollback | Dark Factory |
| `patrol.*` | patrol.finding, patrol.resolved, patrol.alert | Patrol results |
| `git.*` | git.commit, git.push, git.conflict, git.lock | GitGuard events |

### WebSocket Subscription

Clients subscribe to topics via the existing v3 WebSocket connection:

```json
// Subscribe request
{
  "type": "event.subscribe",
  "topics": ["task.*", "approval.*"],
  "since": "2026-03-05T14:00:00Z"
}

// Subscribe response
{
  "type": "event.subscribed",
  "topics": ["task.*", "approval.*"],
  "backfill_count": 23
}

// Event delivery
{
  "type": "event",
  "topic": "task.completed",
  "data": {
    "task_id": "01JQXYZ...",
    "agent_id": "claude-nova-1",
    "result": "success",
    "duration_s": 342
  },
  "timestamp": "2026-03-05T14:23:01Z",
  "sequence": 4521
}
```

**`since` parameter**: On subscribe, the bus replays all events after the given timestamp from SQLite. This enables clients to catch up after disconnects without missing events.

### SSE Compatibility Shim

For legacy consumers that use SSE (scripts, monitoring tools):

```
GET /api/events/stream?topics=task,approval&since=2026-03-05T14:00:00Z

Accept: text/event-stream
Authorization: Bearer <token>
```

The SSE endpoint internally subscribes to the same event bus. It is a thin HTTP handler, not a separate streaming system.

### SQLite Backing

Events are persisted in the existing `task_events` table:

```sql
-- Existing table, extended with topic column
ALTER TABLE task_events ADD COLUMN topic TEXT;

CREATE INDEX idx_events_topic ON task_events(topic, timestamp);
CREATE INDEX idx_events_since ON task_events(timestamp);
```

**Retention**: Events older than 30 days are archived by the cleanup patrol. The `since` replay window is limited to 7 days for performance.

### What This Replaces

| Current Mechanism | Replaced By |
|-------------------|-------------|
| CC SSE streaming (Python) | v3 WebSocket subscriptions + SSE shim |
| File-based heartbeat polling | `agent.telemetry` events via bus |
| Separate webhook endpoints | Unified event topics |
| `.forge/heartbeat/*.json` state files | SQLite event store + projections |

---

## Consequences

### Positive

1. **Single event path**: All events flow through one bus — no fragmentation
2. **Replay capability**: `since` parameter enables catch-up after disconnects
3. **Persistent history**: SQLite backing provides queryable event log
4. **Real-time delivery**: In-memory pubsub for sub-millisecond fan-out
5. **SSE compatibility**: Legacy consumers continue working via thin shim

### Negative

1. **Single-node bottleneck**: In-process bus doesn't scale across orchestrator instances (acceptable for Phase 1-2)
2. **Storage growth**: All events in SQLite requires retention management
3. **Migration effort**: Existing SSE consumers must switch to WS or use shim endpoint

### Neutral

1. **Topic granularity**: Wildcard subscriptions (`task.*`) reduce client complexity
2. **Sequence numbers**: Enable exactly-once delivery detection on the client side
3. **Future migration**: Event bus interface can be backed by NATS/Kafka in Phase 3+ without changing producers/consumers

---

## Related Decisions

- ADR-008: FORGE CLI v3 Rewrite (defines SQLite event store)
- ADR-014: Retire Command Center (removes CC SSE, this provides replacement)
- ADR-015: Agent Telemetry Protocol (telemetry flows through this bus)
- ADR-011: v3 WebSocket Protocol (defines WS message format)

---

**Status: PROPOSED**

Decision review target: 2026-03-10
