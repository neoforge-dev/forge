# ADR-002: Dispatch Consolidation to Single Path

**Date:** 2026-02-23
**Status:** Proposed
**Decision Makers:** Bogdan (CTO), Orchestrators

## Context

8 different scripts/entry points can send messages to agents:

| # | Script | Backend | Reliability |
|---|--------|---------|-------------|
| 1 | `forge dispatch send` (CLI v2) | DispatchClient | High |
| 2 | `agent-message.sh` (DELETED) | DispatchClient (inline) | High |
| 3 | `dispatch-task.sh` | dispatch_to_agent + tmux | Medium |
| 4 | `forge-cli.sh dispatch` | forge dispatch send | Low |
| 5 | `forge dispatch send` | Raw tmux | Low |
| 6 | `fleet-dispatch.sh` | fleet_dispatch.py | Medium |
| 7 | `fleet_dispatch.py` | tmux subprocess | Medium |
| 8 | `dispatch-wrapper.sh` | Raw tmux | Low |

Measured reliability: DispatchClient path ~100%, tmux path ~25-30% (git locks, agent not ready).

## Decision

Consolidate to `forge dispatch send` as the single canonical dispatch path.

1. `forge dispatch send <target> "<message>"` — primary CLI command (all features consolidated)
2. `agent-message.sh` — DELETED (migration complete, all gaps closed)
3. All other scripts deprecated over Sprint 11

## Consequences

### Positive
- Single, tested, reliable dispatch path
- DispatchClient handles verification, circuit breaking, retry
- Consistent flags and behavior

### Negative
- Agents must have `COMMAND_CENTER_URL` configured
- Shell-only environments lose direct tmux dispatch

### Neutral
- Migration requires updating AGENTS.md, skill fallbacks, fleet-dispatch references
- Raw tmux kept as emergency fallback only
