# ADR-016: Adapter Supervisor & Terminal Integration

**Date:** 2026-03-05
**Status:** SUPERSEDED by ADR-036 — council vote 2026-03-09 (2-1). The adapter subprocess telemetry model was never built and is architecturally incompatible with reality: agents self-register + self-claim via HTTP, send heartbeats directly, run in tmux independently. ADR-036 (Autonomous Fleet Execution, COMPLETE S89) is the implemented model.
**Decision Makers:** Bogdan Veliscu (CTO, FORGE)

---

## Context

FORGE runs multiple agent CLI types: Claude Code, OpenCode, Gemini CLI, and Amp. Previous architectures proposed either completely decoupling adapters (via independent WebSockets) or making the Go daemon the direct subprocess manager for the agent CLIs themselves.

Making the Go daemon the direct manager of agent CLIs introduces severe complexity:
1. **Terminal Integration:** We lose `tmux`. Users can no longer attach to a session to see what the agent is doing or interact with it.
2. **Parser Bloat:** Agent CLI outputs change constantly. Building terminal emulators, PTY allocation, and text parsers inside the core Go daemon creates immense technical debt.

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Go as Agent Supervisor | Single process | Kills `tmux` access, massive PTY/parser complexity in core | ❌ REJECTED |
| Independent Adapters | Decoupled | Orphan processes, lifecycle management overhead | ❌ REJECTED |
| **Adapter Supervisor + tmux** | **Keeps terminal access, isolates parser bugs, clean lifecycle** | **Requires managing adapter subprocesses** | ✅ **ACCEPTED** |

---

## Decision

We will adopt a hybrid approach: **Agent CLIs run in `tmux`**, and the `forge-v3` Go daemon acts as the **Process Supervisor for Adapters**.

### 1. Terminal Execution (tmux)
Agent CLIs are still spawned inside `tmux` sessions (e.g., `tmux new-session -d -s forge:claude`). This preserves the operator's ability to `tmux attach` and manually interact with the agent when it prompts for inputs (like OAuth flows or approvals).

### 2. The Adapter Supervisor
Instead of eliminating adapters, the `forge-v3` Go daemon natively supervises them as child processes.

- **Spawning:** When an agent is dispatched, the Go daemon executes: `exec.Command("forge-adapter-claude", "--agent", "claude-1")`
- **Telemetry:** The adapter watches the agent (via hooks or tmux pipe-pane), normalizes the telemetry, and writes it to `stdout`.
- **Parsing:** The Go daemon simply reads the adapter's `stdout` (which outputs structured JSON events) and pushes them to the Event Bus.
- **Lifecycle:** If the adapter crashes, the Go daemon restarts it. If the Go daemon shuts down, the adapter is killed.

---

## Consequences

### Positive

1. **Terminal Access Preserved:** Operators can still monitor and interact with agents natively via `tmux`.
2. **Parser Isolation:** Buggy CLI parsers live in small Python/Go adapter scripts. They do not crash or force recompilation of the core Go daemon.
3. **Clean Lifecycle:** The Go daemon owns the adapters. No orphan processes or complex WebSocket reconnection logic is needed.

### Negative

1. **tmux Dependency:** We remain tied to `tmux` for our terminal multiplexing, preventing a pure cross-platform binary distribution.
2. **Multiple Processes:** Still requires running adapter processes alongside the Go daemon.

## Related Decisions
- Replaces the previous `ADR-016-subprocess-supervisor.md` draft.
- Connects to ADR-027 (Fleet Observability) by standardizing the telemetry ingestion.

**Status: PROPOSED**
