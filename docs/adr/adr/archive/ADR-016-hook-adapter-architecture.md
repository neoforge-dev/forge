# ADR-016: Hook Adapter Architecture for Heterogeneous Agent CLIs

**Date:** 2026-03-05
**Status:** Proposed
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)

---

## Context

FORGE runs multiple agent CLI types: Claude Code, OpenCode (hosting GLM, Kimi, MiniMax), Gemini CLI, and Amp. Each CLI has different hook mechanisms:

- **Claude Code**: `.claude/hooks/` shell scripts triggered on pre/post tool use
- **OpenCode**: Event hooks via config, less mature
- **Gemini CLI**: Limited hook support, relies on output parsing
- **Kimi/MiniMax (via OpenCode)**: Inherit OpenCode's hook system

Currently, each hook is a standalone shell script (e.g., `heartbeat_eval.sh`, `context_guard.sh`, `fleet_error_recovery.sh`) that writes sidecar files. These scripts are fragile, duplicated across CLI types, and don't communicate with v3's WebSocket protocol.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Unified shell script per CLI | Simple | Still file-based, no WS integration, duplicated logic | ❌ REJECTED |
| Single monolithic adapter | One process | Tightly coupled to all CLIs, hard to test | ❌ REJECTED |
| **Thin adapter per CLI type** | **Isolated, testable, single responsibility** | **Multiple small processes** | ✅ **ACCEPTED** |

---

## Decision

Each agent CLI type gets a **thin adapter process** that normalizes hook events into v3 WebSocket `agent.telemetry` messages (ADR-015). Adapters are small, stateless processes with a strict contract.

### Adapter Contract

Every adapter MUST:

1. **Detect hook events** from its CLI type (file watch, stdout parse, or native hook)
2. **Extract** `context_pct`, `task_id`, `status` from the event
3. **Send** `agent.telemetry` messages via WebSocket to `ws://localhost:8082`
4. **Buffer** up to 100 events if WebSocket is disconnected, replay on reconnect
5. **Heartbeat** every 30 seconds even if no state change (confirms liveness)

Every adapter MUST NOT:

- Write sidecar files (`.forge/heartbeat/agents/*.json`)
- Read or modify task state in SQLite
- Make approval decisions
- Communicate with any service other than the forge-v3 WebSocket endpoint
- Implement business logic beyond event extraction and normalization

### Adapter Implementations

| CLI Type | Adapter | Hook Source | Priority |
|----------|---------|------------|----------|
| Claude Code | `forge-adapter-claude` | `.claude/hooks/` shell triggers | **P0 — First** |
| OpenCode (GLM, Kimi, MiniMax) | `forge-adapter-opencode` | OpenCode event config | P1 |
| Gemini CLI | `forge-adapter-gemini` | Output stream parsing | P2 |
| Amp | `forge-adapter-amp` | TBD (Amp hook system) | P3 |

### Claude Code Adapter (P0)

The Claude Code adapter replaces these existing scripts:

| Script | Current Function | Adapter Equivalent |
|--------|-----------------|-------------------|
| `.claude/hooks/heartbeat_eval.sh` | Parse context %, write sidecar | Extract context_pct → `agent.telemetry` |
| `.claude/hooks/context_guard.sh` | Check context threshold, trigger handoff | Extract context_pct, patrol handles threshold |
| `.claude/hooks/fleet_error_recovery.sh` | Detect errors, attempt recovery | Extract error status → `agent.telemetry` with metadata |
| `.forge/scripts/heartbeat-loop.sh` | 30s tmux polling cron | Replaced by hook-driven + 30s heartbeat |

**Implementation sketch:**

```go
// forge-adapter-claude runs as a subprocess alongside Claude Code
type ClaudeAdapter struct {
    agentID   string
    node      string
    wsConn    *websocket.Conn
    buffer    []TelemetryEvent // max 100
}

func (a *ClaudeAdapter) OnHookEvent(hookType string, payload []byte) {
    telemetry := a.extractTelemetry(hookType, payload)
    if err := a.send(telemetry); err != nil {
        a.buffer = append(a.buffer, telemetry)
    }
}

func (a *ClaudeAdapter) extractTelemetry(hookType string, payload []byte) TelemetryEvent {
    // Parse Claude Code hook output for context_pct, task_id, status
    // Each hook type (PreToolUse, PostToolUse, Notification) has different parsing
}
```

### Lifecycle

1. **Start**: Adapter spawns when agent window is created (`forge fleet spawn`)
2. **Run**: Listens for hook events, sends telemetry, maintains heartbeat
3. **Reconnect**: On WS disconnect, buffers events, reconnects with exponential backoff
4. **Stop**: Adapter exits when agent window is destroyed

### Process Supervision

Adapters are managed by the v3 binary's agent registry. When an agent registers, v3 spawns the appropriate adapter. The patrol system monitors adapter liveness via the heartbeat signal — a missing heartbeat triggers adapter restart.

---

## Consequences

### Positive

1. **Clean separation**: Each CLI type's quirks are isolated in its own adapter
2. **Testable**: Adapters can be unit-tested with mock hook events
3. **Evolvable**: New CLI types get new adapters without touching existing ones
4. **No sidecar files**: All telemetry flows through WebSocket (ADR-015)
5. **Thin and auditable**: Each adapter is <500 LOC with a strict contract

### Negative

1. **Multiple processes**: One adapter process per active agent (lightweight, but adds to process count)
2. **CLI coupling**: Each adapter must understand its CLI's hook format (breaks if CLI changes hooks)
3. **Sequential rollout**: Must build and test one adapter at a time

### Neutral

1. **Language choice**: Adapters written in Go (compiled into v3 binary or as separate small binaries)
2. **Buffer size**: 100 events ≈ ~50 minutes of telemetry at 30s intervals — sufficient for typical disconnects

---

## Related Decisions

- ADR-015: Agent Telemetry Protocol (defines the message format adapters produce)
- ADR-017: Unified Event Bus (telemetry events enter the bus)
- ADR-008: FORGE CLI v3 Rewrite (parent architecture)

---

**Status: PROPOSED**

Decision review target: 2026-03-10
