# Node Lead FSM — Lightweight Agent State Machine

**Author:** prya orchestrator
**Date:** 2026-03-01
**Status:** IMPLEMENTED (all phases)

---

## Problem

Fleet agents (node leads) suffer from three recurring failure modes:

1. **Silent death** — agent crashes or hits rate limit, stays dead indefinitely
2. **Context overflow** — agent fills context without delivering results, work is lost
3. **Stuck states** — agent tries to commit (violating rules) or enters infinite loop

Current hooks (`heartbeat_eval.sh`, `context_guard.sh`) are orchestrator-focused. They fire for fleet agents too but emit wrong instructions (COMMIT, DISPATCH, /handoff) that confuse agents.

## Design Principles

1. **Role-aware hooks** — same `.claude/settings.json`, branched behavior by `$FORGE_AGENT_TYPE`
2. **Result-first** — always write partial results before dying
3. **Self-healing** — agents auto-recover from transient errors
4. **Zero overhead for orchestrator** — existing orchestrator FSM unchanged

## State Machine

```
                    ┌─────────┐
                    │  IDLE    │◄──────────────────┐
                    └────┬─────┘                   │
                         │ dispatch received       │
                    ┌────▼─────┐                   │
                    │ WORKING  │                   │
                    └──┬──┬──┬─┘                   │
                       │  │  │                     │
              ctx>60%  │  │  │ task complete       │
              ┌────────┘  │  └──────────┐          │
              │           │             │          │
         ┌────▼────┐  error        ┌────▼──────┐   │
         │ WRAPUP  │    │          │ DELIVERING │   │
         └────┬────┘    │          └────┬───────┘   │
              │    ┌────▼─────┐        │           │
              │    │ RECOVERY │        │           │
              │    └────┬─────┘        │           │
              │         │              │           │
              ▼         ▼              ▼           │
         ┌──────────────────────────────────┐      │
         │     WRITE RESULTS (.md file)     │──────┘
         └──────────────────────────────────┘
```

### States

| State | Trigger | Behavior |
|-------|---------|----------|
| **IDLE** | Agent at prompt, no active dispatch | Sidecar: `status=idle`. Orchestrator can dispatch. |
| **WORKING** | Dispatch received | Sidecar: `status=working, task_id=X`. Agent executes task. |
| **WRAPUP** | ctx > 60% | Stop hook warns: "Wrap up — write results now." At 75%: force-write partial results. |
| **DELIVERING** | Task complete | Agent writes `.forge/heartbeat/results/AGENT-TASKID.md`. |
| **RECOVERY** | Error (API 429, file not found, build fail) | Retry once. If persistent, write error report to results file. |

## Implementation

### Phase 1: Role-Aware Hooks (1 hour)

Modify existing hooks to check `$FORGE_AGENT_TYPE`:

#### `heartbeat_eval.sh` — Fleet Branch

```bash
# At top of heartbeat_eval.sh, after FORGE_ROOT
AGENT_TYPE="${FORGE_AGENT_TYPE:-orchestrator}"

if [ "$AGENT_TYPE" = "fleet" ]; then
  # Fleet agent: lightweight status only
  AGENT_NAME="${FORGE_AGENT_NAME:-unknown}"
  RESULTS="$FORGE_ROOT/.forge/heartbeat/results"
  CTX_FILE="$FORGE_ROOT/.forge/heartbeat/context_percent"

  CTX_PCT=0
  if [ -f "$CTX_FILE" ]; then
    val=$(cat "$CTX_FILE" 2>/dev/null | tr -d '[:space:]')
    [[ "$val" =~ ^[0-9]+$ ]] && CTX_PCT="$val"
  fi

  # Check if agent has written results recently
  HAS_RESULTS="no"
  if [ -d "$RESULTS" ]; then
    recent=$(find "$RESULTS" -name "${AGENT_NAME}-*.md" -newer "$FORGE_ROOT/.forge/heartbeat/loop_state.json" 2>/dev/null | wc -l)
    [ "$recent" -gt 0 ] && HAS_RESULTS="yes"
  fi

  # Context warnings for fleet agents
  if [ "$CTX_PCT" -gt 75 ]; then
    echo "FLEET CRITICAL: ctx ${CTX_PCT}% — STOP working. Write partial results to .forge/heartbeat/results/${AGENT_NAME}-PARTIAL.md NOW. Then stop."
  elif [ "$CTX_PCT" -gt 60 ]; then
    echo "FLEET WARNING: ctx ${CTX_PCT}% — Wrap up current task. Write results soon."
  fi

  # Remind of rules
  if [ "$HAS_RESULTS" = "no" ]; then
    echo "FLEET: No results file found. Remember to write results to .forge/heartbeat/results/ when done."
  fi

  echo "FLEET: DO NOT COMMIT. DO NOT run /handoff. Write results file and stop."
  exit 0
fi

# ... rest of existing orchestrator FSM ...
```

#### `context_guard.sh` — Fleet Branch

```bash
# After reading FORGE_ROOT and CTX_FILE
AGENT_TYPE="${FORGE_AGENT_TYPE:-orchestrator}"

if [ "$AGENT_TYPE" = "fleet" ]; then
  # Fleet agents: don't block stop, don't demand /handoff
  # Instead: if context is high, just remind to write results
  if [ "$CTX_PCT" -gt 75 ]; then
    AGENT_NAME="${FORGE_AGENT_NAME:-unknown}"
    RESULTS="$FORGE_ROOT/.forge/heartbeat/results/${AGENT_NAME}-PARTIAL.md"
    if [ ! -f "$RESULTS" ]; then
      # No results written — block once and demand results
      cat <<EOJSON
{"decision":"block","reason":"FLEET AGENT: ctx at ${CTX_PCT}%. You MUST write partial results to .forge/heartbeat/results/${AGENT_NAME}-PARTIAL.md before stopping. Include what you completed and what remains."}
EOJSON
      exit 0
    fi
  fi
  # Allow stop (results exist or context is fine)
  exit 0
fi

# ... rest of existing orchestrator context guard ...
```

### Phase 2: Agent Sidecar File (30 min)

Each fleet agent writes a sidecar status file:

```
.forge/heartbeat/agents/{agent-name}.json
```

```json
{
  "agent": "pool-t1-minimax",
  "status": "working",
  "task_id": "IS-EXIT-INTENT",
  "dispatch_file": ".forge/dispatches/minimax-IS-EXIT-INTENT.md",
  "started_at": "2026-03-01T18:27:00Z",
  "context_pct": 27,
  "last_heartbeat": "2026-03-01T18:30:00Z"
}
```

The orchestrator's heartbeat can then detect:
- **Stale agents**: `last_heartbeat` > 30 min old → likely dead
- **Overloaded agents**: `context_pct` > 60 → about to need clearing
- **Completed agents**: `status=idle` with recent results file → ready for new dispatch

### Phase 3: Auto-Recovery Hook (30 min)

Add a `PostToolUse` hook that catches common errors:

```bash
# .claude/hooks/fleet_error_recovery.sh
AGENT_TYPE="${FORGE_AGENT_TYPE:-orchestrator}"
[ "$AGENT_TYPE" != "fleet" ] && exit 0

input=$(cat)
tool_name=$(echo "$input" | jq -r '.tool_name // ""')
error=$(echo "$input" | jq -r '.error // ""')

# Detect 429 rate limit
if echo "$error" | grep -q "429"; then
  AGENT_NAME="${FORGE_AGENT_NAME:-unknown}"
  cat > "$FORGE_ROOT/.forge/heartbeat/results/${AGENT_NAME}-ERROR.md" <<EOF
# Error Report: Rate Limited

**Agent:** $AGENT_NAME
**Error:** 429 Rate Limit
**Time:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Action:** Agent should stop and wait for rate limit reset.
EOF
  echo "FLEET: Rate limited. Error report written. Stop now."
fi

# Detect git lock
if echo "$error" | grep -q "index.lock"; then
  rm -f "$FORGE_ROOT/.git/index.lock" 2>/dev/null
  echo "FLEET: Removed stale git index.lock. Retry your operation."
fi
```

## Migration Path

### Step 1: Add Role Checks (no behavior change)
- Add `$FORGE_AGENT_TYPE` checks to all hooks
- Fleet agents get `exit 0` (no output) — same as current behavior minus confusing messages

### Step 2: Enable Fleet Warnings
- Fleet agents get context warnings (WRAPUP at 60%, CRITICAL at 75%)
- Fleet agents get "DO NOT COMMIT" reminders

### Step 3: Enable Fleet Context Guard
- Block stop only when no results file exists and context > 75%
- Auto-write error report as last resort

### Step 4: Add Sidecar + Orchestrator Detection
- Fleet agents write status to sidecar
- Orchestrator heartbeat reads sidecars for fleet health

## Effort Estimate

| Phase | Work | Time | Status |
|-------|------|------|--------|
| Phase 1: Role-aware hooks | Modify 2 shell scripts | 1 hour | DONE |
| Phase 2: Agent sidecar | Add JSON write to hooks | 30 min | DONE |
| Phase 3: Error recovery hook | New shell script + settings.json | 30 min | DONE |
| Phase 4: Orchestrator detection | Modify heartbeat_eval.sh | 30 min | DONE |
| **Total** | | **2.5 hours** | |

## Files to Modify

| File | Change |
|------|--------|
| `.claude/hooks/heartbeat_eval.sh` | Add fleet branch at top |
| `.claude/hooks/context_guard.sh` | Add fleet branch at top |
| `.claude/hooks/fleet_error_recovery.sh` | **New** — PostToolUse hook |
| `.claude/settings.json` | Add PostToolUse hook entry |

## Success Criteria

1. Fleet agents never see orchestrator FSM instructions (COMMIT, DISPATCH, /handoff)
2. Fleet agents get context warnings at 60% and forced result delivery at 75%
3. Dead agents detected within 30 minutes via stale sidecar
4. "Commit this" stuck state eliminated (DO NOT COMMIT reminder in every stop output)
5. Rate limit errors auto-reported to results file

## Risks

- **Hook overhead**: Each Stop hook adds ~50ms. Fleet agents may have slower response cycles. Mitigation: keep fleet branch minimal (< 10ms).
- **Sidecar staleness**: If agent crashes hard, sidecar won't update. Mitigation: orchestrator treats >30min stale as "presumed dead."
- **Race conditions**: Multiple agents writing to same results dir. Mitigation: agent name prefix on all files (already in place).
