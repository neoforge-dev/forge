# ADR-007: Beads as FORGE Task Graph Backend

**Status:** SUPERSEDED — council vote 2026-03-09 (3-0). v3 SQLite task system (`forge task`) + self-claiming via `forge work --daemon` permanently replace both Beads and the planned `blocked_by` lightweight alternative. If task dependencies needed at scale (>200 tasks), implement `blocked_by` field in Go task schema (`cmd/forged/`).
**Date:** 2026-02-27
**Deciders:** Bogdan (Lead), Fleet Consensus (3 independent reviews)
**Context:** Session 40

---

## Context

FORGE currently manages work items through a multi-layered system:

1. **`task_queue.json`** — Centralized JSON file (~28 active tasks)
2. **`.forge/dispatches/*.md`** — Free-form markdown dispatch files
3. **`.forge/heartbeat/results/*.md`** — Manual results discovery
4. **CC REST API** (`/api/tasks`) — HTTP task CRUD with lease-based claiming
5. **Claude Code `TaskCreate`/`TaskUpdate`** — In-session ephemeral tasks

**Pain points observed across 40 sessions:**

| Problem | Impact | Frequency | Evidence |
|---------|--------|-----------|----------|
| Git index.lock contention | 25% commit failure rate | Every session | Post-mortem (2026-02-12), Engineering Guide |
| No dependency graph | Can't express "A blocks B" | Constant | No failures documented — orchestrator sequences manually |
| Dispatch delivery ~80% reliable | Lost work, re-dispatch needed | Weekly | fleet-dispatch.md measured rates |
| Results discovery is manual | Lead must `ls` results dir | Every session | Trivial (~5 seconds) |
| Task status drift across nodes | Duplicate claims, orphaned tasks | Occasional | Lease system under-utilized |
| Sequential IDs on branches collide | Merge conflicts on task files | Rare at current scale | ~28 tasks, agent-prefix would fix |
| No compaction/archival | Manual cleanup needed | S39 (30 min) | Infrequent |

**Beads** (by Steve Yegge, v0.56.1) is a distributed, Dolt-backed graph issue tracker designed for AI-supervised coding workflows. It addresses many of these pain points but at significant integration cost.

---

## Decision

**HOLD on full beads integration. Implement lightweight `blocked_by` alternative immediately (1 day). Revisit beads when scale thresholds are reached.**

### Rationale

Three independent reviews (technical, operational, product) reached unanimous consensus:

| Reviewer | Verdict | Key Argument |
|----------|---------|-------------|
| **pi** (product lens) | HOLD → REJECT | Opportunity cost: 5 weeks on infra while revenue = $0. Ship products first. |
| **kimi** (technical lens) | HOLD (4/5 confidence) | Timing wrong: beads API unstable (102 releases in 4.5 months), Go 1.25 dep unreleased. Core need met with `blocked_by` field. |
| **gemini** (operations lens) | HOLD | Scale mismatch: 28 tasks and 6 agents don't justify distributed SQL. Dolt safe on only 2 of 4 nodes. |

### Consensus Points

All three reviewers agreed on:

1. **The dependency graph gap is real** but can be solved with `blocked_by` field + `forge tasks ready` command
2. **5 weeks is too much** for internal tooling while revenue-generating products are unshipped
3. **Phase 1 shadow mode is reasonable** if attempted, but Phases 2-3 are under-specified
4. **Beads is a good project** that solves real problems — the timing is wrong, not the concept
5. **No rollback plan** for Phase 3 is a critical gap

### Key Corrections from Reviews

| ADR Claim | Actual | Source |
|-----------|--------|--------|
| Beads version "v0.9.11" | **v0.56.1** (102 releases in 4.5 months) | kimi: `cmd/bd/version.go` |
| `bd ready` latency "~10ms" | **200-500ms** cold start (subprocess + Dolt init), 10-50ms warm | gemini: subprocess lifecycle analysis |
| Beads fixes git lock contention | **Partially** — only for task files, not code commits (which are the main source) | kimi: lock contention is on code repo, not task metadata |
| "Offline-first" on all nodes | **Only 2 of 4 nodes** can safely run Dolt (sati 64GB, nova 48GB). prya/vega at 16GB are non-starters. | gemini: RAM constraints from CLAUDE.md |
| Dispatch reliability fixed by beads | **No** — dispatch failures come from agent lifecycle (tmux, context full), not task storage | gemini: root cause analysis |
| Comparison matrix deltas | **Inflated** — current system has offline capability (dispatch files) and audit trail (git log) | gemini + kimi |
| Go 1.25 dependency | **Go 1.25.6 is unreleased** — beads tracks bleeding-edge Go | kimi: `go.mod` analysis |

---

## Immediate Action: Lightweight Alternative (1 Day)

Implement the core value proposition — dependency-aware task selection — without new infrastructure.

### Schema Change

```json
{
  "id": "TQ-minimax-042",
  "title": "Deploy Voice Coach",
  "blocked_by": ["TQ-glm-041"],
  "claimed_by": "minimax",
  "status": "pending"
}
```

### New CLI Command: `forge tasks ready`

```python
# forge-shared/forge_shared/task_deps.py (~80-100 lines)
def get_ready_tasks(tasks: list[dict]) -> list[dict]:
    """Return tasks with no open blockers, unclaimed, sorted by priority."""
    closed_ids = {t['id'] for t in tasks if t['status'] in ('completed', 'closed')}
    return [
        t for t in tasks
        if t['status'] == 'pending'
        and not t.get('claimed_by')
        and all(b in closed_ids for b in t.get('blocked_by', []))
    ]

def detect_cycles(tasks: list[dict]) -> list[list[str]]:
    """Detect circular dependencies in task graph."""
    # Topological sort — return cycles if found
    ...

def claim_task(task_id: str, agent: str, tasks: list[dict]) -> bool:
    """Atomically claim a task (file-lock protected)."""
    ...
```

### Changes Required

| Change | Effort | File |
|--------|--------|------|
| Add `blocked_by`, `claimed_by` fields to task schema | 1 hour | `task_queue.json` schema |
| Implement `get_ready_tasks()` with cycle detection | 2 hours | `forge-shared/forge_shared/task_deps.py` |
| Add `forge tasks ready` CLI command | 2 hours | `harness/forge_harness/cli_v2/tasks.py` |
| Use agent-prefix IDs (`TQ-minimax-042`) | 1 hour | Task creation logic |
| **Total** | **1 day** | **~100 lines new code, zero new deps** |

---

## Beads: Future Consideration

Beads remains a compelling option if FORGE outgrows the lightweight solution. Conditions to revisit:

### Scale Thresholds (Revisit When ANY Are Met)

| Metric | Current | Threshold | Why |
|--------|---------|-----------|-----|
| Active tasks | ~28 | 200+ | JSON file contention becomes real |
| Concurrent agents | 4-6 | 15+ | Claim contention and dependency sequencing bottleneck |
| `blocked_by` limitations | None yet | Documented failures | Cycle detection, multi-hop deps insufficient |
| Beads release velocity | ~1/day | < 2/month | API stability for production adoption |
| Revenue status | $0 MRR | > $0 | Proves infra is the bottleneck, not product |

### What Beads Would Provide (Beyond Lightweight Solution)

| Capability | `blocked_by` field | Beads |
|-----------|-------------------|-------|
| Dependency graph | Single-hop, flat JSON | Full DAG, 5 edge types, transitive closure |
| ID collision safety | Agent-prefix (simple) | Hash-based (mathematical guarantee) |
| Compaction | Manual archive | Automatic tiered compaction |
| Cross-node sync | XNode relay (existing) | Dolt push/pull (native) |
| Version-controlled history | git log on JSON file | Dolt cell-level versioning |
| Inter-agent messaging | Dispatch files (existing) | `bd mail` (first-class) |
| Ready-work detection | ~100 lines Python | ~10ms SQL query (warm) |
| Multi-writer safety | File lock | Dolt MVCC |

### Beads Integration Risks (Unchanged)

These risks apply whenever beads is reconsidered:

1. **Dolt only safe on 2/4 nodes** — prya and vega (16GB) cannot run Dolt server
2. **Subprocess latency** — 200-500ms cold, 10-50ms warm per `bd` call
3. **API churn** — 102 releases in 4.5 months, active schema migrations
4. **Go 1.25+ requirement** — bleeding-edge, unreleased Go version
5. **Corruption surface** — 30+ files in `doctor/` package for repair tooling
6. **Dual version control** — git for code + Dolt for tasks doubles operational complexity
7. **No Phase 3 rollback plan** — cutover from CC to beads-primary needs explicit rollback procedure
8. **Git hooks conflict** — beads installs hooks, FORGE has existing hooks

---

## Alternatives Considered

### A. Full Beads Integration (5 weeks) — HOLD
The original ADR proposal. Elegant but over-engineered for current scale. See reviews for detailed analysis.

### B. `blocked_by` Field + `forge tasks ready` (1 day) — ACCEPTED
Delivers 70-80% of the dependency-tracking value with zero new dependencies. See "Immediate Action" above.

### C. GitHub Issues / GitHub Projects — REJECTED
Network-dependent, rate-limited (fleet already approaches 5000 req/hr limit during releases), no offline support.

### D. Linear / Jira / Notion — REJECTED
Vendor lock-in, API rate limits, no git integration, can't run offline.

### E. Custom Python Graph Store (SQLite + networkx) — DEFERRED
More capable than `blocked_by` field but less than beads. Consider if lightweight solution proves insufficient but beads is still too unstable.

### F. Do Nothing — VIABLE
Current system works. Pain points are friction, not failure. The orchestrator handles dependency sequencing manually. This is inelegant but functional for 40 sessions.

---

## Review History

### Fleet Review Round (2026-02-27)

**pi** (product/strategy):
> "This ADR is a classic example of engineering-for-engineering's-sake. It identifies real friction points, then proposes an elegant but over-engineered solution. Meanwhile, FORGE has $0 revenue and multiple products ready to ship. The bottleneck is not task management — it's deployment, marketing, and customer acquisition."

**kimi** (deep technical):
> "I largely agree with pi's directional assessment but arrive through different reasoning. Git lock contention at 25% is real and post-mortem'd — pi underestimates this. However, beads only fixes it for task files, not code commits. The actual version is v0.56.1, not v0.9.11 as the ADR claims. 102 releases in 4.5 months means the API is a fast-moving target."

**gemini** (operations):
> "Dolt is only safely deployable on 2 of 4 nodes. The ADR's 10ms latency claim is the SQL query time, not the full subprocess lifecycle (200-500ms). This is like deploying Kubernetes for a 3-container application. The operational overhead exceeds the value at 28 tasks and 6 agents."

All three reviews available at:
- `.forge/heartbeat/results/pi-s40-beads-review.md`
- `.forge/heartbeat/results/kimi-s40-beads-review.md`
- `.forge/heartbeat/results/gemini-s40-beads-review.md`

---

## References

- Beads source: `.scratchpad/beads/`
- FORGE task system: `harness/forge_harness/cli_v2/tasks.py`
- FORGE dispatch protocol: `forge-shared/modules/fleet-dispatch.md`
- Git lock post-mortem: `docs/POST_MORTEM_TMUX_VIOLATION_2026-02-12.md`
- Engineering guide (lock stats): `docs/FORGE_ENGINEERING_GUIDE.md:292`
- Current task queue: `.forge/heartbeat/task_queue.json`
