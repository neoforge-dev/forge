# ADR-048: Infrastructure Honesty Cleanup

**Status:** Proposed  
**Date:** 2026-04-05  
**Author:** FORGE Council  
**Context:** S195 Infrastructure Audit Findings

---

## Context

After 194 sprints, FORGE infrastructure has accumulated "aspirational" systems that document what *should* work rather than what *actually* works.

### The Honesty Gap

| System | Documented | Actual | Gap |
|--------|-----------|--------|-----|
| Dark factory | Autonomous revenue generation | Content spam mode | 1,457 blog posts, $0 revenue |
| 38 patrols | Comprehensive monitoring | ~40% no actionable output | Log noise, DB churn |
| systemd timers | Reliable scheduling | `forge-heartbeat` broken | References non-existent binary |
| Agent fleet | 13 coordinated agents | Coordination overhead | 31% of commits are session logs |
| Multi-file state | Organized documentation | 380+ stale PLAN files | Confusion, merge conflicts |

---

## Decision

Consolidate and simplify infrastructure before optimizing.

### 1. Patrol Consolidation: 38 → 25

**Kill immediately:**
- `queue-depth` (covered by preflight)
- `daily-digest` (duplicate of cron)
- `royal-jelly-staleness` (advisory only)
- `rails8-drift` (quarterly sufficient)
- `git-cleanup` (weekly manual sufficient)

**Extend intervals:**
- `metrics-rollup`: 5m → 15m
- `council-cleanup`: 2m → 10m
- `context-compaction`: 5m → 10m
- `doc-drift`: 24h → 7d
- `dispatch-hygiene`: 24h → 7d
- `state-freshness`: 24h → 7d
- `research-expiry`: 6h → 12h
- `worktree-prune`: 1h → 6h

**Merge:** 3 XNode patrols → 1 `xnode-sync` at 2m

**Fix:** Disable broken `forge-heartbeat` systemd timer

### 2. Documentation Consolidation: 2-File State

**Allowed:**
- `docs/PROMPT-{node}.md` — Node agents write
- `docs/PLAN.md` — Orchestrator only

**Prohibited:** `PLAN-{domain}.md` files

### 3. Dark Factory Pivot

See ADR-051.

### 4. Commit Hygiene

Max 1 state-update commit per session. Batch updates.

### 5. Fleet Boundaries

Fleet cannot write to docs/PLAN.md or create PLAN-{domain}.md files.

---

## Consequences

### Positive
- ~40% reduction in log volume
- ~60% reduction in coordination commits
- Structural prevention of doc bloat

### Negative
- Temporary disruption during daemon restart
- Learning curve for new rules

---

## Success Criteria

- [ ] 38 → 25 patrols verified
- [ ] Log volume reduced ~40%
- [ ] 2-file state enforced
- [ ] PLAN-{domain}.md files archived
- [ ] `forge-heartbeat` timer disabled

---

COMPLETE
