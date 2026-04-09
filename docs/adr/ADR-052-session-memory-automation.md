# ADR-052: Session Memory Consolidation & Self-Reinforcement Loop

**Date:** 2026-04-07 (revised after council vote)
**Status:** Accepted
**Decision Makers:** pi (REJECT→revise), kimi2 (APPROVE-WITH-AMENDMENTS)
**Inspired by:** [claude-memory-compiler](https://github.com/coleam00/claude-memory-compiler) pattern analysis

---

## Context

FORGE has four distinct memory/context subsystems that overlap but never converge:

| Subsystem | Write location | What reads it | Gap |
|-----------|---------------|---------------|-----|
| Royal Jelly (FS) | `.forge/context/{domain}/lead-context.md` | `load_context.sh` (SessionStart) | Manual-only updates; 8/17 stale |
| Context Envelopes (SQLite) | `context_envelopes` table | `syncRoyalJelly` patrol → FS writeback | `checkContextThreshold` patrol killed at S196 |
| Session Persist | `.forge/session-persist/latest.{json,md}` | **Nothing** — dead end | `load_context.sh` never reads it |
| forge handoff | `.forge/memories/session-live.md` | **Nothing** — dead end | Bypasses Royal Jelly entirely |

### What already works (council audit, pi vote)

Existing hooks cover the pre-compact + stop enforcement:
- `heartbeat_eval_compact.sh` (PreCompact): Full state snapshot including all Royal Jelly → `.forge/heartbeat/pre_compact_state.md`
- `context_guard.sh` (Stop): Blocks exit at >75% context without handoff
- `session_persist.sh` (Stop): Captures session metadata + transcript tail
- `load_context.sh` (SessionStart[clear]): Injects PROMPT.md + pre-compact state + Royal Jelly

### What's actually broken (not fixed by existing hooks)

1. **Dead-end writes**: `session_persist.sh` writes `latest.md` → nothing reads it. `forge handoff clean` writes `session-live.md` → nothing reads it. Two separate capture systems, both disconnected from the Royal Jelly loop.
2. **Killed patrol**: `checkContextThreshold` (auto-envelope generation from heartbeat data) was removed at S196. No automatic context saves from daemon.
3. **No feedback loop**: Session checkpoints are captured but never analyzed. No mechanism to detect patterns (which domains go stale fastest, which session types produce the most decisions) or improve capture quality over time.
4. **Node-level staleness**: 6 of the 8 stale contexts are node contexts (oc, forge, nova, gaea, prya, sati), not product domains. They go stale because no orchestrator touches those nodes — hooks can't fire when nothing happens.

---

## Decision

### Phase 1: Close the dead-end loops (2h)

Connect the disconnected systems — don't add new hooks.

**1a. `load_context.sh` reads `session-persist/latest.md`**

Add a section to the existing `load_context.sh` that injects the last session-persist snapshot when resuming after `/clear`. This closes the `session_persist.sh → ??? → load_context.sh` gap.

```bash
# After Royal Jelly section in load_context.sh
PERSIST_FILE="$FORGE_ROOT/.forge/session-persist/latest.md"
if [ -f "$PERSIST_FILE" ]; then
  echo "=== LAST SESSION PERSIST ==="
  cat "$PERSIST_FILE"
fi
```

**1b. `session_persist.sh` appends touched-domain summary**

Extend the existing Stop hook to detect which domains were touched (via `git diff --name-only` + `config/domains.yaml` lookup) and append a summary. This replaces the brittle `session-end.sh` proposed in the original ADR.

```bash
# At end of session_persist.sh, before exit 0
DOMAINS_FILE="$FORGE_ROOT/config/domains.yaml"
if [ -f "$DOMAINS_FILE" ]; then
  TOUCHED=$(git -C "$FORGE_ROOT" --no-optional-locks diff --name-only HEAD 2>/dev/null | head -50)
  if [ -n "$TOUCHED" ]; then
    echo >> "$MD_PATH"
    echo "## Touched Domains (needs Royal Jelly update)" >> "$MD_PATH"
    echo "$TOUCHED" | while read -r f; do
      # Extract first path component as potential domain indicator
      echo "- \`$f\`" >> "$MD_PATH"
    done
  fi
fi
```

**1c. Self-enforcing checkpoint retention**

Add to `heartbeat_eval_compact.sh` (PreCompact), first lines:

```bash
# Auto-cleanup: remove session checkpoints older than 30 days
find "$FORGE_ROOT/.forge/session-persist/sessions" -name "*.md" -mtime +30 -delete 2>/dev/null
find "$FORGE_ROOT/.forge/session-persist/sessions" -name "*.json" -mtime +30 -delete 2>/dev/null
```

### Phase 2: Staleness patrol (30m)

Extend the existing `context-sync` patrol in `cmd/forged/patrol.go` to check Royal Jelly staleness:

```go
// In syncRoyalJelly(), after existing sync logic:
// Check for stale contexts (>7 days without update)
staleContexts := findStaleContexts(forgeRoot, 7*24*time.Hour)
if len(staleContexts) > 0 {
    log.Warn("Royal Jelly stale contexts", "domains", staleContexts, "threshold", "7d")
}
```

This patrol already runs every 10 minutes and has access to `.forge/context/`. Adding staleness detection is a natural extension — not a new patrol.

### Phase 3: Self-reinforcement loop (future, post-MRR)

**Design principle**: Each session generates data that improves the next session's context.

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Session N   │────▶│ session_persist  │────▶│ latest.md   │
│  (agent)     │     │ (Stop hook)      │     │ + domains   │
└─────────────┘     └──────────────────┘     └──────┬──────┘
                                                     │
                           ┌─────────────────────────┘
                           ▼
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Session N+1 │◀───│ load_context.sh  │◀───│ Royal Jelly │
│  (agent)     │     │ (SessionStart)   │     │ (updated)   │
└─────────────┘     └──────────────────┘     └──────┬──────┘
                                                     │
                           ┌─────────────────────────┘
                           ▼
                    ┌──────────────────┐
                    │ context-sync     │
                    │ patrol (10m)     │
                    │ + staleness warn │
                    └──────────────────┘
```

Future reinforcement (deferred):
- **Co-change clusters**: Analyze `session-persist/sessions/` to find which files change together → auto-suggest `domains.yaml` updates
- **High-signal tagging**: When auto-memory triggers within 5m of a checkpoint, tag that checkpoint as high-signal for priority retention
- **Staleness prediction**: Track domain staleness patterns → proactively prompt orchestrators to update contexts before they expire

### What we explicitly do NOT do

| Rejected approach | Reason (council feedback) |
|-------------------|--------------------------|
| New `pre-compact.sh` hook | `heartbeat_eval_compact.sh` already does this (pi, REJECT) |
| New `session-end.sh` hook | `session_persist.sh` + `context_guard.sh` already cover this (pi, REJECT) |
| Agent SDK knowledge compilation | ~$0.50/session cost + AI drift (original ADR, retained) |
| Path-based domain heuristic | Brittle; use `domains.yaml` lookup instead (kimi2, amendment #1) |

---

## Consequences

### Positive
- Closes two dead-end write paths (`session-persist` and `forge handoff`) without new hooks
- Self-enforcing retention (cleanup runs on every PreCompact, not manual cron)
- Staleness patrol uses existing infrastructure (context-sync patrol, 10m cadence)
- Feedback loop design enables progressive improvement without upfront complexity

### Negative
- Phase 1c (retention cleanup in PreCompact) runs on every compaction — adds ~50ms to an already-running hook
- Staleness patrol warnings require orchestrator action (still discipline-dependent for the final write)
- Domain detection from `git diff --name-only` is imprecise without a proper path→domain resolver

### Neutral
- No new hooks registered in `.claude/settings.json` — all changes are extensions to existing hooks
- Phase 3 (self-reinforcement) is design-only; implementation deferred to post-$500 MRR
- Node-level context staleness (pi's root-cause finding) is accepted: nodes go stale when idle, which is expected behavior

---

## Implementation Plan

| Step | Owner | Effort | Files |
|------|-------|--------|-------|
| 1a. `load_context.sh` reads session-persist | Worktree agent | 15m | `.claude/hooks/load_context.sh` |
| 1b. `session_persist.sh` domain detection | Worktree agent | 30m | `.claude/hooks/session_persist.sh` |
| 1c. Retention cleanup in PreCompact | Worktree agent | 10m | `.claude/hooks/heartbeat_eval_compact.sh` |
| 2. Staleness detection in context-sync patrol | Worktree agent | 45m | `cmd/forged/patrol.go` |
| 3. Self-reinforcement loop | Deferred | — | Design only |

**No council vote required for implementation** — all changes extend existing files (no new hooks, no new patrols, no new binaries).
