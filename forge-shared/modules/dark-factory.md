# Dark Factory

Dark Factory is the FORGE autonomous task pipeline, designed to allow the fleet to execute and validate work without human intervention.

## Pipeline Flow
1. **Create**: Orchestrator creates a task via `forge task create`.
2. **Claim**: A fleet agent claims the task via `forge task claim ID` (or daemon auto-claims via `forge work --daemon`).
3. **Run**: Agent executes the task autonomously.
4. **Report**: Agent writes a result file to `.forge/heartbeat/results/`.
5. **Auto-Complete [F2]**: `monitorResultFiles()` detects the result file, matches it to a task, and moves task to COMPLETED. Processed files move to `results/processed/`.
6. **Auto-Promote [F1]**: `autoPromoteCompletedTasksInLane()` advances COMPLETED tasks to the next lane (dev → test → deploy → done) via `LaneManager.ProgressTask()`.
7. **Confidence-Approve [F3]**: `confidenceApproveCompletedTasks()` calculates confidence score and auto-approves if >= 0.65 threshold. Below threshold creates a human approval request.
8. **Done**: Task is merged and archived.

## Implementation Status (S158)

All three Dark Factory features are **fully implemented** and wired into the patrol registry:

| Feature | Function | File:Line | Patrol Schedule | Status |
|---------|----------|-----------|-----------------|--------|
| **F1: Auto-Promote** | `autoPromoteCompletedTasksInLane()` | `patrol.go:958` | Every 5 min | ✅ LIVE |
| **F2: Result Monitor** | `monitorResultFiles()` via `resultMonitorPatrol()` | `patrol.go:999` / `patrol.go:2000` | Every 2 min | ✅ LIVE |
| **F3: Confidence Scoring** | `confidenceApproveCompletedTasks()` + `calculateConfidenceScore()` | `patrol.go:1166` / `patrol.go:1100` | Every 3 min | ✅ LIVE |

### Confidence Scoring Weights (F3)
- **40%**: Test pass rate (0.0–1.0)
- **30%**: Coverage percentage / 100
- **20%**: max(0, 1.0 - lint_issues/50.0)
- **10%**: Fixed 1.0 (git-based = reversible)
- **Default**: 0.70 when no `quality_gate_results` data exists
- **Auto-approve threshold**: 0.65

### Database Support
- `quality_gate_results` table: migration `035_quality_gate_results.sql`
- Columns: `task_id`, `test_pass_rate`, `coverage_pct`, `lint_issues`, `created_at`
- **Gap**: No code currently populates this table during task execution — patrols fall back to default 0.70 score

### Supporting Functions
- `findTaskIDInFilename()` — matches result filenames to task IDs (patrol.go:1073)
- `extractResultSummary()` — pulls summary from result file content
- `blastRadiusFromResult()` — extracts blast radius from task result JSON (patrol.go:1137)
- `calculateConfidenceScore()` — weighted score from quality gate data (patrol.go:1100)

## How to Participate (Agent Guide)

To be a good citizen of the Dark Factory:
1. **Claim the Task**: Never work on a task without claiming it first.
2. **Execution**: Always build and test your changes.
3. **Write Results**: Your work is "invisible" to the factory until you write:
   `.forge/heartbeat/results/{agent}-{taskID}.md`
4. **No Commits**: Do not commit your changes; the factory or lead will handle it after validation.

## Testing Dark Factory Patrols

```bash
# Run all patrol tests
cd cmd/forged && go test -run TestAutoPromote -v
cd cmd/forged && go test -run TestResultMonitor -v
cd cmd/forged && go test -run TestConfidenceApprove -v

# Verify patrol registry
cd cmd/forged && go test -run TestStandardPatrols -v
```

## Full Specification
See [ADR-033: Dark Factory Autonomy](../../docs/adr/ADR-033-dark-factory-autonomy.md) for the original design specification.
