# ADR-015: Agent Telemetry Protocol Replaces Sidecar Context Monitoring

**Date:** 2026-03-05
**Status:** Proposed
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)

---

## Context

Agent context monitoring currently relies on a fragile sidecar-file approach:

1. **`.forge/heartbeat/context_percent`**: A flat file updated by `heartbeat-loop.sh` every 30 seconds via tmux pane polling
2. **`.forge/heartbeat/agents/*.json`**: Per-agent JSON sidecar files with status, context %, and last activity
3. **`heartbeat-loop.sh`**: A 30s cron that scrapes tmux panes, parses CLI output, and writes sidecar files
4. **Patrol system**: Reads sidecar files to detect stuck agents, context exhaustion, and idle windows

This approach suffers from: 30-second polling lag (agents can exhaust context between polls), race conditions on file writes, no persistence/queryability, and complete blindness when agents run on remote nodes without shared filesystem.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Improve heartbeat-loop.sh polling interval | Minimal change | Still file-based races, doesn't work cross-node | ❌ REJECTED |
| Agent CLI plugins (native telemetry) | Perfect data | Requires changes to Claude/OpenCode/Kimi CLIs we don't control | ❌ REJECTED |
| **WebSocket `agent.telemetry` message** | **Real-time, persistent, queryable, works cross-node** | **Requires adapter per CLI type** | ✅ **ACCEPTED** |

---

## Decision

Replace shell-hook sidecar files with a WebSocket-native **`agent.telemetry`** message type. Each agent's hook adapter (ADR-016) sends telemetry directly to the v3 orchestrator via the existing WebSocket connection.

### What Dies

| Component | Location | Replacement |
|-----------|----------|-------------|
| Context percent file | `.forge/heartbeat/context_percent` | `agent_telemetry` SQLite table |
| Agent sidecar JSONs | `.forge/heartbeat/agents/*.json` | `agents` table `context_pct` column |
| heartbeat-loop.sh | `.forge/scripts/heartbeat-loop.sh` | Hook adapter WebSocket messages |
| tmux pane polling | Various scripts | Adapter extracts data from CLI hooks |

### WebSocket Message: `agent.telemetry`

```json
{
  "type": "agent.telemetry",
  "agent_id": "claude-node-3-1",
  "node": "node-3",
  "context_pct": 0.42,
  "task_id": "01JQXYZ...",
  "status": "busy",
  "tool": "claude",
  "metadata": {
    "tokens_used": 84200,
    "tokens_limit": 200000,
    "last_tool_call": "edit_file",
    "session_duration_s": 1820
  },
  "timestamp": "2026-03-05T14:23:01Z"
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_id` | string | ✅ | Unique agent identifier (`{tool}-{node}-{slot}`) |
| `node` | string | ✅ | Hostname of the node running the agent |
| `context_pct` | float | ✅ | Context window usage 0.0–1.0 |
| `task_id` | string | ❌ | Currently assigned task (null if idle) |
| `status` | enum | ✅ | `idle`, `busy`, `wrapup` |
| `tool` | string | ✅ | CLI type: `claude`, `opencode`, `kimi`, `gemini`, `amp` |
| `metadata` | object | ❌ | Tool-specific data (tokens, last action, duration) |
| `timestamp` | ISO8601 | ✅ | When telemetry was captured |

### New SQLite Schema

```sql
CREATE TABLE agent_telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    node        TEXT NOT NULL,
    context_pct REAL NOT NULL,
    task_id     TEXT,
    status      TEXT NOT NULL CHECK (status IN ('idle', 'busy', 'wrapup')),
    tool        TEXT NOT NULL,
    metadata    TEXT,  -- JSON blob
    timestamp   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_telemetry_agent ON agent_telemetry(agent_id, timestamp DESC);
CREATE INDEX idx_telemetry_node ON agent_telemetry(node, timestamp DESC);
```

On each `agent.telemetry` message, the orchestrator:
1. **Appends** to `agent_telemetry` (append-only log)
2. **Updates** `agents SET context_pct = ?, status = ?, last_seen = ?` for the latest projection

### Patrol Integration

The patrol system switches from reading sidecar files to querying the `agent_telemetry` table:

```sql
-- Find agents approaching context exhaustion
SELECT agent_id, context_pct, task_id
FROM agents
WHERE context_pct > 0.50 AND status = 'busy';

-- Find stale agents (no telemetry in 2 minutes)
SELECT agent_id, last_seen
FROM agents
WHERE last_seen < datetime('now', '-2 minutes') AND status != 'idle';
```

### Offline Buffering

If the WebSocket connection is down, the adapter buffers up to 100 telemetry events locally and replays them on reconnect (ordered by timestamp). This ensures no data loss during brief network interruptions.

---

## Consequences

### Positive

1. **Real-time**: Sub-second telemetry vs 30-second polling lag
2. **No race conditions**: Single-writer per agent via WebSocket, no concurrent file writes
3. **Persistent and queryable**: SQLite table enables historical analysis, trend detection
4. **Cross-node native**: Works identically for local and remote agents
5. **Offline resilient**: Buffer+replay ensures no data loss

### Negative

1. **Adapter dependency**: Requires one adapter per CLI type (ADR-016)
2. **Storage growth**: Append-only telemetry table needs periodic pruning (patrol job)
3. **Migration period**: Sidecar files and telemetry protocol must coexist during transition

### Neutral

1. **Schema evolution**: `metadata` JSON blob allows tool-specific fields without schema changes
2. **Telemetry frequency**: Adapters should send on state change + every 30s heartbeat (configurable)

---

## Related Decisions

- ADR-016: Hook Adapter Architecture (produces telemetry messages)
- ADR-017: Unified Event Bus (telemetry events flow through same bus)
- ADR-008: FORGE CLI v3 Rewrite (parent architecture)

---

**Status: PROPOSED**

Decision review target: 2026-03-10
