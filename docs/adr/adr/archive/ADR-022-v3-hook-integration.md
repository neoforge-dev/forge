# ADR-022: FORGE v3 Hook Integration Pattern

**Date:** 2026-03-02
**Status:** Proposed
**Decision Makers:**
- gemini (nf.lead agent, Strategic Review)
- kilo (pool-t2 agent, Architecture Review)
- cursor (pool-t2 agent, Technical Review)

## Context

FORGE CLI v3 (as defined in ADR-008) transitions from a file-based task queue to a persistent Go-based orchestrator. A critical requirement for this transition is the "Royal Jelly" pattern, where the filesystem remains the source of truth for domain context, while the Go orchestrator provides a high-performance API and TUI for fleet management.

To enable seamless multi-agent orchestration, we need a robust mechanism to:
1. Detect updates to domain context sidecar files (`.forge/context/{domain}/*.md`).
2. Expose this context via a standardized API for workers and the UI.
3. Bridge the gap between the asynchronous filesystem operations and the real-time WebSocket-based orchestrator.

This "Hook Integration" is the primary adapter that allows v3 to ingest and serve context artifacts without forcing all agents to migrate away from their established file-based workflows immediately.

## Alternatives Considered

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Polling** | Simple to implement, no OS dependencies. | High latency or high CPU usage, inefficient for 100+ projects. | ❌ REJECTED |
| **inotify / fsnotify** | Real-time, low overhead. | Platform-specific (Linux only for inotify), potential for missed events on high-volume writes. | ❌ REJECTED |
| **Hook Adapter (Selected)** | **Decoupled, event-driven, standard API contract.** | **Requires explicit "hook" calls or a watcher process.** | ✅ **ACCEPTED** |

## Decision

We will implement the **v3 Hook Integration Pattern** using a dedicated context adapter within the Go orchestrator.

### 1. Context Sidecar Files
Domain leads will continue to write to `.forge/context/{domain}/`:
- `decisions.md`
- `failures.md`
- `lead-context.md`
- `active-sprint.md`

### 2. The Hook Adapter
The Go orchestrator will include a `ContextWatcher` that monitors these directories. When a change is detected (or a manual `forge hook context update` is called), the adapter:
1. Parses the Markdown artifacts.
2. Updates the `context_artifacts` table in SQLite.
3. Broadcasts a `context.updated` event over the WebSocket to all connected subscribers (UI, other agents).

### 3. API Contract
The orchestrator will expose a standardized endpoint for context retrieval:

**Endpoint:** `GET /api/agents/{agent_id}/context`

**Response Payload:**
```json
{
  "agent_id": "is.lead",
  "domain": "is",
  "last_updated": "2026-03-02T14:30:00Z",
  "artifacts": [
    {
      "type": "lead-context",
      "content": "# Interview Simulator Lead Context...",
      "hash": "sha256:..."
    },
    {
      "type": "decisions",
      "content": "...",
      "hash": "sha256:..."
    }
  ],
  "envelope_status": "synced"
}
```

### 4. Implementation Details
- **Watch Strategy:** Use `fsnotify` in Go for cross-platform file watching (Linux/macOS/Windows).
- **Graceful Degradation:** If the watcher fails, the API falls back to direct filesystem reads.
- **Idempotency:** Use SHA256 hashes of file contents to prevent redundant database writes and event broadcasts.

## Consequences

### Positive
- **Real-time Visibility:** The UI (BubbleTea/HTMX) can show live context updates as agents work.
- **Standardization:** Workers don't need to know where the files are; they just query the API.
- **Hybrid Support:** Supports both legacy file-based agents and new v3-native workers.
- **Knowledge Preservation:** Ensures "Royal Jelly" context is always indexed and searchable in the v3 SQLite store.

### Negative
- **Complexity:** Adds a sync layer between the filesystem and the database.
- **File System Stress:** Watching thousands of files across 95 projects may require careful tuning of ignore patterns and watch depth.

### Neutral
- **Dependency:** Adds `fsnotify` as a core dependency for the Go orchestrator.
- **Transitionary:** This pattern is a bridge; eventually, context may move entirely into the event-sourced database.

---

## Related Decisions
- ADR-008: FORGE CLI v3 Rewrite
- ADR-010: API Gateway for Agent Communication (Planned)
