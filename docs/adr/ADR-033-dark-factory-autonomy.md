# ADR-033: Dark Factory Autonomy — Three Missing Pieces

**Status:** 📋 PROPOSED
**Date:** 2026-03-06
**Source:** Council audit (3 agents) — prya S70 session
**Deciders:** prya lead orchestrator
**Supersedes:** None (extends ADR-009, ADR-012, ADR-028)

---

## Context

Dark Factory (autonomous task pipeline: Dev → Test → Deploy → Done) is 70-95% built:

| Component | Status | Evidence |
|-----------|--------|----------|
| Task FSM (7 states) | ✅ 100% | `task_state_machine.go` |
| Lane System (4 lanes) | ✅ 95% | `lane.go` + HTTP routes |
| Quality Gates (3 executor types) | ✅ 90% | `gate_executor.go` |
| Approval Service | ✅ 80% | `approvals.go` |
| Patrol/Recovery (8 patrols) | ✅ 70% | `patrol.go` |
| Lease System | ✅ 95% | `lease.go` |
| **End-to-End Autonomy** | ⚠️ **30%** | Scheduler + feedback loop missing |

The pipeline requires 3 manual interventions today that should be automated:

1. Calling `POST /api/tasks/{id}/lane/complete` manually after each lane
2. Calling `POST /api/tasks/{id}/complete` manually when agent finishes
3. Human approval of every transition (no confidence-based auto-approval)

---

## Decision

Implement three features to close the autonomy gap, in priority order:

### Feature 1: Auto-Promote Patrol (HIGH impact, LOW effort)

**What:** A new patrol in `cmd/forge-v3/patrol.go` that runs every 5 minutes, queries all tasks with `state=COMPLETED` in `lane=dev|test|deploy`, and calls `ProgressTask()` for each.

**Why:** Currently, lane progression is request-driven (manual). This makes it scheduled/event-driven.

**Implementation:**
```go
// In patrol.go — add to scheduledPatrols list
{
    Name:     "auto-promote",
    Interval: 5 * time.Minute,
    Handler:  autoPromoteCompletedTasks,
}

func autoPromoteCompletedTasks(ctx context.Context, db *sql.DB, lm *LaneManager) error {
    // Query tasks: state=COMPLETED, lane IN (dev, test, deploy)
    // For each: call lm.ProgressTask(ctx, task.ID)
    // Log: promoted N tasks, blocked M (pending approval)
}
```

**Acceptance criteria:**
- Task in `state=COMPLETED, lane=dev` auto-moves to `lane=test` within 5 min
- If gates fail → creates approval request, logs reason
- Patrol logs "promoted N, blocked M (approval needed)" per run

### Feature 2: Result File Monitor (HIGH impact, LOW effort)

**What:** A new patrol that watches `.forge/heartbeat/results/` every 2 minutes, matches result files to open tasks, and calls `POST /api/tasks/{id}/complete` when a result file appears.

**Why:** Currently, agent completion requires a manual API call. Result files are the canonical signal that work is done.

**Convention:** `{agent-name}-{task-id}.md` → matches task by ID prefix.

**Implementation:**
```go
// In patrol.go — add patrol
{
    Name:     "result-monitor",
    Interval: 2 * time.Minute,
    Handler:  monitorResultFiles,
}

func monitorResultFiles(ctx context.Context, db *sql.DB, forgeRoot string) error {
    // Scan .forge/heartbeat/results/*.md
    // Parse filename: {agent}-{taskID}.md
    // Check if task.state == RUNNING
    // If yes: POST /api/tasks/{taskID}/complete with result_summary from file
    // Move processed result to .forge/heartbeat/results/processed/
}
```

**Acceptance criteria:**
- Agent writes result file → task auto-completes within 2 min
- Processed files moved to `results/processed/` to prevent double-processing
- Logs: "completed task TASK-X from result file kimi-TASK-X.md"

### Feature 3: Confidence Scoring (MEDIUM impact, MEDIUM effort)

**What:** Implement ADR-012 confidence scoring. Calculate a confidence score (0.0-1.0) based on test pass rate, coverage %, lint issues, and task blast radius. Auto-approve transitions where `confidence > threshold`.

**Thresholds (configurable):**
```toml
[dark_factory.confidence]
dev_to_test_threshold = 0.85
test_to_deploy_threshold = 0.90
deploy_to_done_threshold = 0.95  # High bar for production
```

**Score factors:**
- Test pass rate: 40% weight
- Coverage % (vs baseline): 30% weight
- Lint issues (0 = max): 20% weight
- Blast radius (files changed): 10% negative weight

**Implementation:** New file `confidence_scorer.go` in `cmd/forge-v3/`.

**Acceptance criteria:**
- `CalculateConfidence(task, gateResults) float64` returns 0.0-1.0
- In `ProgressTask()`: if `confidence >= threshold` → auto-approve transition
- If `confidence < threshold` → create approval request with score attached
- Score stored in `task.metadata` for audit trail

---

## Consequences

### Positive
- Tasks flow autonomously: agent writes result file → auto-complete → auto-promote → auto-approve (if confidence high) → done
- Human approval only needed for risky deploys (confidence < 0.95)
- Patrol system becomes the nervous system of the pipeline

### Negative
- Auto-approve creates risk if confidence thresholds are wrong — must tune carefully
- Result file naming convention must be strictly followed by all agents
- Two new patrols increase v3 daemon resource usage slightly

### Dependencies
- Feature 1 depends on: `LaneManager.ProgressTask()` (exists ✅)
- Feature 2 depends on: `/api/tasks/{id}/complete` endpoint (exists ✅), `.forge/heartbeat/results/` convention (established ✅)
- Feature 3 depends on: Feature 1 + 2 working (confidence scores quality gate results)

---

## Implementation Owners

| Feature | Owner | Effort | Blocker |
|---------|-------|--------|---------|
| Auto-Promote Patrol | sati fleet agent | 1-2 hrs | None |
| Result File Monitor | sati fleet agent | 1-2 hrs | None |
| Confidence Scoring | sati lead or kimi | 3-4 hrs | Features 1+2 first |

---

## Minimum Viable Dark Factory Demo (Today)

Without implementing anything, you can run a partial demo:

```bash
# 1. Create task in dev lane
curl -X POST http://localhost:8081/api/tasks \
  -d '{"domain":"test","project":"demo","type":"feature","title":"DF Demo","priority":5}'

# 2. Claim it
curl -X POST http://localhost:8081/api/tasks/{id}/claim \
  -d '{"agent_id":"test-agent"}'

# 3. Complete it (simulating agent work)
curl -X POST http://localhost:8081/api/tasks/{id}/complete \
  -d '{"agent_id":"test-agent","result_summary":"done"}'

# 4. Manually promote lane (this step becomes automatic with Feature 1)
curl -X POST http://localhost:8081/api/tasks/{id}/lane/complete

# 5. Approve (this becomes automatic with Feature 3)
curl -X POST http://localhost:8081/api/approvals/{approval-id}/approve

# Check state
forge task show {id}
```

**What works:** FSM, gates, approval service, lease system, patrol recovery.
**What requires manual calls:** Steps 4 and 5 (closed by Features 1 and 3).

---

## Related ADRs

- **ADR-009** — Agentic Patterns (partial: FSM partial)
- **ADR-012** — Confidence Scoring (proposed, zero code → Feature 3 implements it)
- **ADR-028** — Task FSM (✅ complete: 7 states, atomic transitions)
- **ADR-030** — Config Model (thresholds should live in `~/.forge/config.toml`)
