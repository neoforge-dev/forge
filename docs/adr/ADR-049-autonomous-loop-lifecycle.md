# ADR-049: Autonomous Loop Lifecycle & Stale-Task Hygiene

**Status:** ACCEPTED  
**Date:** 2026-04-05  
**Author:** nova (orchestrator), expanded by pi  
**Council References:** S162 (stale task hygiene), S185 (loop phases), S190 (git safety)

---

## Decision

Autonomous loops follow a **4-phase lifecycle** with strict entry/exit criteria, token budgets, and auto-cancel rules. This consolidates Council S162 and S185 into a single enforceable model.

| Phase | Interval | Budget | Exit Condition |
|-------|----------|--------|----------------|
| **Discovery** | 15min | 50k tokens, max 3 iterations | Find work OR iterations exhausted |
| **Monitoring** | 60min | 20k tokens/iter | 2 consecutive "no changes" → auto-cancel |
| **Settling** | N/A | consolidation | After iter 2, mandatory consolidation write |
| **Archived** | N/A | — | Result file >7d OR task COMPLETED+promoted |

---

## Context

The fleet runs recurring "loops" (patrols, heartbeats, drift checkers, result monitors) that accumulate state:
- Patrol executions logged to SQLite
- Dispatch tasks created in the task queue
- Result files written to `.forge/heartbeat/results/`
- Report files written to `.forge/reports/`

Without hygiene, this state rots:
- Tasks stuck in QUEUED/DISPATCHED past their useful window (Council S162)
- Result files from 3+ sprints ago still in `results/` dir
- Patrol executions table growing unbounded
- Zombie loops firing past their acceptance window

---

## Lifecycle Phases (Detailed)

### Phase 1: Discovery

| Attribute | Value |
|-----------|-------|
| **Entry Criteria** | Loop dispatched with `PHASE=discovery` OR previous loop iteration found actionable work |
| **Interval** | 15 minutes |
| **Max Iterations** | 3 |
| **Token Budget** | 50,000 tokens per iteration |
| **Exit Criteria** | (a) Work discovered → transition to Monitoring, (b) 3 iterations exhausted → auto-cancel |

**Activities:**
- Scan for actionable work (drift, gaps, new items)
- Query git log, task queue, domain context
- Write findings to `.forge/heartbeat/results/`

**Consolidation Rule (P2):** After iteration 2, write a CONSOLIDATED document (not append) summarizing all findings. This prevents result file bloat.

---

### Phase 2: Monitoring

| Attribute | Value |
|-----------|-------|
| **Entry Criteria** | Discovery found actionable work AND work not yet completed |
| **Interval** | 60 minutes |
| **Max Iterations** | Unlimited (until auto-cancel triggered) |
| **Token Budget** | 20,000 tokens per iteration |
| **Exit Criteria** | 2 consecutive "no changes" iterations OR work completed |

**Activities:**
- Endpoint health checks
- Git log monitoring only (no expensive queries)
- Verify previously discovered work remains relevant

**Auto-Cancel Trigger:** After 2 consecutive iterations reporting "no changes", the loop self-cancels and emits a summary to `docs/PROMPT-{node}.md`.

---

### Phase 3: Settling

| Attribute | Value |
|-----------|-------|
| **Entry Criteria** | Iteration 2 completed in any phase |
| **Interval** | N/A (one-time) |
| **Token Budget** | Included in iteration budget |
| **Exit Criteria** | Consolidation document written |

**Consolidation Rules:**
1. After iteration 2, MANDATORY consolidation write
2. Consolidated doc replaces (not appends) previous findings
3. Format: Summary first, details after
4. Max 200 lines per consolidated result

---

### Phase 4: Archived

| Attribute | Value |
|-----------|-------|
| **Entry Criteria** | Result file age >7 days OR task COMPLETED+promoted to canonical doc |
| **Interval** | N/A |
| **Token Budget** | None |
| **Exit Criteria** | N/A (terminal state) |

**Hygiene Rules:**
1. **Result file TTL:** `.forge/heartbeat/results/*.md` older than 7 days → move to `.forge/heartbeat/results/archived/YYYY-MM/`
2. **Task prune rule:** `forge task prune` archives tasks when `state ∈ {QUEUED, DISPATCHED}` AND `heartbeat_age > 2h` AND no result file exists for `{agent}-{taskID}` (Council S162)
3. **Patrol execution TTL:** SQLite rows older than 30 days deleted weekly by `patrol-exec-gc` patrol

---

## Write Allowlist (P3)

Loop agents may **ONLY** write to these paths:

| Allowed Path | Purpose |
|--------------|---------|
| `.forge/heartbeat/results/*` | Iteration results |
| `.forge/context/*/lead-context.md` | Domain state updates |
| `.forge/context/*/decisions.md` | Architectural decisions |
| `.forge/context/*/failures.md` | Failure patterns |
| `docs/PLAN-*.md` | Planning documents |
| `.forge/loop-cache/{LOOP_ID}.json` | Shared loop cache (P4) |

**PROHIBITED:** Source code files (`*.ts`, `*.tsx`, `*.js`, `*.py`, `*.go`, `*.swift`), stylesheets, lockfiles, and ANY git operations (`git commit`, `git push`, `git add`).

---

## Auto-Cancel Conditions

A loop auto-terminates when ANY of the following occur:

| Condition | Action | Log Location |
|-----------|--------|--------------|
| 2 consecutive "no changes" in Monitoring | Self-cancel + summary | `docs/PROMPT-{node}.md` |
| 3 Discovery iterations exhausted | Self-cancel + summary | `.forge/heartbeat/results/` |
| Token budget exceeded | Immediate halt | STDERR + result file |
| Write allowlist violation | Immediate halt + alert | `docs/PROMPT-{node}.md` |
| Queue full / no idle agents (3 retries) | Defer + reschedule | Task queue metadata |

**Queue Full Handling:** If work is discovered but cannot be dispatched (queue full or no idle agents), the loop:
1. Retries 3 times with exponential backoff (1min, 5min, 15min)
2. If still blocked, defers work and records in `.forge/loop-cache/deferred-{LOOP_ID}.json`
3. Next Discovery phase checks deferred cache first

---

## Open Questions (Resolved)

| Question | Resolution | Rationale |
|----------|------------|-----------|
| Should archived results be compressed or just moved? | **Just moved** to `archived/YYYY-MM/` | Compression adds complexity; 7-day active window + 30-day archive retention is sufficient (Council S162) |
| How to handle loops that discover work but can't dispatch? | **Defer + cache** in `.forge/loop-cache/deferred-{LOOP_ID}.json` | Prevents work loss while respecting queue pressure |
| Should consolidation writes be mandatory or orchestrator-reviewed? | **Mandatory** after iter 2 (P2) | Orchestrator review happens at promotion time, not consolidation |

---

## Alternatives Considered

| Alternative | Rejected Because |
|-------------|------------------|
| Unbounded loops with manual cleanup | Current state; does not scale |
| Hard timeout per loop | Too rigid — some loops legitimately discover nothing for hours then spike |
| Centralized loop registry | Over-engineered for <20 active loops |
| Compressed archives | Added complexity without significant space savings for text files |

---

## Consequences

**Positive:**
- Predictable token burn per phase
- Result dir doesn't balloon (7-day TTL)
- Auto-cancel prevents runaway loops
- Clear write boundaries prevent source code drift

**Negative:**
- Adding `patrol-exec-gc` patrol = +1 to patrol count (ADR-009 drift)
- Archived result files harder to grep — mitigation: keep 30-day unarchived
- Deferred work cache requires cleanup (30-day TTL)

---

## Implementation Status

- [x] Task prune rule enforced via `forge task prune` (Council S162)
- [x] Loop phases documented in CLAUDE.md (Council S185)
- [x] Write allowlist defined (P3, refined by pi)
- [ ] Result file TTL script (`scripts/archive-old-results.sh`)
- [ ] `patrol-exec-gc` patrol added to `StandardPatrols()`
- [ ] Loop write allowlist enforced via dispatch template linter
- [ ] Deferred work cache cleanup job

---

## References

- Council S162: Stale task hygiene rules
- Council S185: Loop process rules (Discovery/Monitoring phases)
- Council S190: Git safety for loop agents
- `.forge/dispatch-templates/recurring-loop.md`: Dispatch template
- ADR-009: V3 agentic patterns (patrol system)
