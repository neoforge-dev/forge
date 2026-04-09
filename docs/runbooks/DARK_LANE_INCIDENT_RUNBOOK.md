# Dark Lane Incident Runbook

Date: 2026-02-23
Task: DF-4005
Plan Reference: `docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md`
Code Reference: `harness/forge_harness/dark_factory/lane_policy_enforcer.py`
Policy Reference: `docs/runbooks/LANE_POLICY_MATRIX.md`

---

## Purpose

This runbook is the canonical operator guide for detecting, classifying, and responding to Dark Factory lane incidents. It covers the complete rollback ladder from targeted lane-level intervention to full human-gated mode, and provides recovery procedures for returning lanes to autonomous operation safely.

Read this before enabling any autonomous lane. Practice the tabletop scenarios before a production rollout.

---

## 1. Background: What Is a Dark Lane Incident?

A "dark lane incident" is any condition where autonomous task completion produces an unacceptable outcome. The FORGE Dark Factory routes tasks through one of three canonical lanes:

- `autonomous` — agent completes, evaluator verifies, no human touch
- `human-review` — agent completes, evaluator runs, human must approve
- `blocked` — human action required; agents may not apply changes

An incident occurs when the `autonomous` lane produces outputs that breach quality, safety, or data integrity expectations. The incident response goal is to halt the damage, restore a safe operating state, and recover without losing completed work.

Rollback does not mean starting over. It means temporarily moving lanes back toward human-gated mode while the root cause is identified and fixed.

---

## 2. Incident Detection

### 2.1 Real-Time Signals

These signals indicate a potential dark lane incident. Monitor them during and after any autonomous rollout.

**Fleet and task health:**
```bash
# Overall fleet status
forge status

# Task queue state
forge task list

# Node and mesh status
forge node status
forge node list
```

> ⚠️ **NOT YET SHIPPED:** `forge evaluator summary/status` (Dark Factory quality pipeline). These commands were planned but never implemented.

**Evaluator anomalies to watch for:**
> ⚠️ **NOT YET SHIPPED:** Dark Factory evaluator was planned but never shipped. Use `forge task list` to monitor task health instead.

**Lease and node health:**
```bash
# Node heartbeat and availability
forge node status

# Cross-node mesh status
forge node list
```

**Lease anomalies to watch for:**
- Nodes reporting `availability_score` below 0.3 for more than 5 consecutive heartbeats
- High `expired` lease count without corresponding `requeued` transitions
- Path lock collisions appearing in scheduler logs (`PATH CONFLICT` in structured log output)

### 2.2 Indirect Signals

These signals appear in external systems and may indicate a dark lane failure that the evaluator did not catch (false-pass):

| Signal | Where to Check | Threshold |
|---|---|---|
| Test suite regression | CI run results | Any new test failure in a lane that was previously green |
| Type error increase | mypy output | Any increase in error count vs. baseline |
| API error rate spike | PostHog / Sentry | >1% error rate increase in endpoints recently modified |
| Linting violation | ruff output | Any new violation introduced after an autonomous task completed |
| Data integrity error | Application logs | Any constraint violation, unexpected null, or foreign key error |
| Security alert | Sentry / server logs | Any 401/403 anomaly or unexpected auth flow change |

### 2.3 Daily Scorecard

> ⚠️ **NOT YET SHIPPED:** Dark Factory evaluator never shipped. Use `forge task list` and `forge status` for task health monitoring.

```bash
forge task list
forge status
```

Scorecard thresholds from DF-0001:

| Metric | Green | Yellow | Red |
|---|---|---|---|
| `dispatch_ack` rate | >= 99% | 95-99% | < 95% |
| `evaluator_pass_rate` | >= 98% | 94-98% | < 94% |
| `requeue_rate` | <= 5% | 5-15% | > 15% |
| `human_touch_ratio` in autonomous lanes | <= 2% | 2-10% | > 10% |
| `escaped_defects` (false-passes) | 0 | 1-2 | >= 3 |

---

## 3. Severity Classification

Classify the incident before taking any action. Misclassifying up or down wastes time or causes unnecessary downtime.

### P0 — Data Corruption or Security Breach

**Definition:** An autonomous task has modified data in a way that is inconsistent, irreversible, or that violates security invariants. A human must act immediately.

**Examples:**
- An `autonomous` task ran a database migration or touched a `blocked` task type
- Auth tokens were rotated or JWT logic was modified without a `blocked` gate firing
- PII was written to logs or an unencrypted field
- A production deployment completed without human approval

**Immediate action:** Full rollback (Section 4.3). Engage on-call human immediately.

**Do not wait for more data. Roll back first, investigate second.**

### P1 — SLO Breach or Task Loss

**Definition:** Autonomous lanes are producing failures at a rate that breaches agreed SLOs, or tasks are being lost (dropped from queues, stuck indefinitely, or completing with incorrect output).

**Examples:**
- Evaluator `pass_rate` below 94% for more than 30 minutes
- `requeue_rate` above 15% for more than 2 consecutive scorecard windows
- Tasks stuck in `evaluator_failed` with no retries for more than 1 hour
- A lane producing zero completions for more than 2 hours during an active sprint

**Immediate action:** Node-level or lane-level rollback (Sections 4.1 or 4.2) while investigating root cause.

### P2 — Degraded Quality

**Definition:** Autonomous lanes are completing tasks, but the output quality is below threshold. Evaluator is passing tasks that would fail manual review. No data corruption or security issue is present.

**Examples:**
- Evaluator false-pass rate above 2% in the audit sample
- Test coverage decreasing despite test-writing tasks completing
- Documentation quality metrics declining after `docs-update` lane tasks

**Immediate action:** Increase audit sampling. Tighten evaluator checks. Consider lane-level rollback if quality metrics do not recover within one sprint cycle.

---

## 4. Rollback Procedures

Work from narrowest to broadest. Start with the smallest scope that stops the incident.

### 4.1 Lane-Level Rollback

Disable autonomous completion for a specific lane while leaving all other lanes running.

**Use when:** The incident is isolated to one `task_type` or work-cell lane. Other lanes are healthy.

**Step 1: Identify the affected lane.**
```bash
# See which task types are currently running in autonomous mode
forge task list

# Check which task types are producing failures
forge task list --status failed

> ⚠️ **NOT YET SHIPPED:** `forge evaluator`, `forge tasks list --lane`, `forge approve` — Dark Factory never shipped.
```

**Step 2: Tighten the lane policy to human-review.**

The `LanePolicyEnforcer` accepts runtime config updates without restart. To move a lane from `autonomous` to `human-review`, update the enforcer config via the API or by editing the running config.

Using the forged daemon API (preferred when forged is running):
```bash
# Demote 'docs' lane from autonomous to human-review
curl -X PATCH http://localhost:8081/api/lane-policy \
  -H "Authorization: Bearer $FORGE_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "docs": {
      "can_auto_complete": false,
      "requires_human_review": true,
      "is_hard_gated": false
    }
  }'

# Demote 'api_simple' lane
curl -X PATCH http://localhost:8081/api/lane-policy \
  -H "Authorization: Bearer $FORGE_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "api-simple": {
      "can_auto_complete": false,
      "requires_human_review": true,
      "is_hard_gated": false
    }
  }'

# Demote 'test_writing' lane
curl -X PATCH http://localhost:8081/api/lane-policy \
  -H "Authorization: Bearer $FORGE_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "test-writing": {
      "can_auto_complete": false,
      "requires_human_review": true,
      "is_hard_gated": false
    }
  }'
```

**Step 3: Verify the policy change took effect.**
```bash
> ⚠️ **NOT YET SHIPPED:** Dark Factory lane policy enforcement was planned but never implemented.

**Step 4: Halt new dispatch for the affected task type.**
```bash
forge lead send --to-node <node> --task-id INCIDENT-$(date +%s) \
  --summary "HALT: stop dispatching [task-type] tasks" --priority high --strict
```

**Step 5: Release in-flight task leases.**
```bash
# Release a task's lease via API
curl -X POST http://localhost:8081/api/task/TASK_ID/release \
  -H "Authorization: Bearer $FORGE_WEBHOOK_TOKEN"
```

**Step 6: Write an incident note.**
```bash
forge task create "incident: [task-type] isolated — investigate before resuming" --priority high
```

---

### 4.2 Node-Level Rollback

Pause all dark-lane task dispatch on a specific node while leaving other nodes running.

**Use when:** The incident appears node-specific (one node showing anomalous behavior, high requeue rate, or lease expiry storms while others are healthy).

**Step 1: Identify the affected node.**
```bash
# Check all node heartbeat scores
forge node status

# Cross-node mesh
forge node list

# Check lease stats per node (look for high expired counts)
forge status
```

**Step 2: Exclude the node from scheduler recommendations.**

The `SchedulerPolicy.recommend()` method accepts an `exclude_nodes` list. Prevent new tasks from being dispatched to the affected node:
```bash
# Send exclusion directive via lead channel
forge lead send --to-node NODE_NAME \
  --task-id "incident-$(date +%s)" \
  --summary "INCIDENT: pause all dark-lane task intake on this node immediately" \
  --strict
```

Alternatively, stop the forged daemon on the affected node to halt dispatch:
```bash
# On the affected node (via tmux or SSH)
# Stop the forged daemon (this pauses dispatch without losing state)
# Do NOT kill the agent processes — they will finish their current tasks
forge daemon stop
```

**Step 3: Verify no new tasks are being dispatched to the node.**
```bash
forge task list
```

**Step 4: Let in-flight tasks drain or migrate.**

Tasks that are in `active` state on the excluded node will continue until their lease expires. Use `forge daemon restart` on the affected node after isolating it.

To speed up migration:
```bash
# Check active tasks via API
curl -H "Authorization: Bearer $TOKEN" http://localhost:8081/api/tasks?status=active
```

**Step 5: Restart and requalify the node before re-enabling.**

See Section 5 (Recovery Steps) before bringing the node back.

---

### 4.3 Full Rollback — Revert All Lanes to Human-Gated Mode

Disable autonomous completion across all lanes. Every task will require human approval before completion.

**Use when:** P0 incident, unknown blast radius, or when you cannot isolate which lane or node is affected.

**Step 1: Stop all autonomous dispatch immediately.**

This is the emergency brake. It stops new autonomous tasks from being queued without killing in-flight work.

```bash
# Post an emergency halt directive to all nodes
for node in prya sati nova vega; do
  forge lead send --to-node "$node" \
    --task-id "emergency-halt-$(date +%s)" \
    --summary "EMERGENCY: halt all autonomous task intake. Switch to human-review mode for all lanes." \
    --strict
done
```

**Step 2: Update lane policy to human-review for all autonomous lanes.**

```bash
# Demote all three autonomous lanes simultaneously
curl -X PATCH http://localhost:8081/api/lane-policy \
  -H "Authorization: Bearer $FORGE_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "docs": {
      "can_auto_complete": false,
      "requires_human_review": true,
      "is_hard_gated": false
    },
    "test-writing": {
      "can_auto_complete": false,
      "requires_human_review": true,
      "is_hard_gated": false
    },
    "api-simple": {
      "can_auto_complete": false,
      "requires_human_review": true,
      "is_hard_gated": false
    }
  }'
```

> ⚠️ **NOT YET SHIPPED:** `forge approve --list` and `forge approve --stats` — Dark Factory human-review queue was planned but never implemented.

**Step 4: Audit all tasks completed in the last 24 hours.**
```bash
forge task list
```

**Step 5: For P0 incidents, revert code changes from the affected tasks.**

If the incident involves data corruption or security breach:
```bash
# Identify commits from autonomous tasks in the incident window
git log --since="24 hours ago" --pretty=format:"%H %s" | grep -i "autonomous\|dark\|auto"

# Review each commit before reverting
git show COMMIT_HASH

# Revert in a branch (never directly on main)
# NOTE: Use /dispatch to delegate the revert to a Task agent — do not commit inline
```

---

## 5. Recovery Steps

After the incident is contained, use these steps to safely re-enable dark lanes. Do not skip steps.

### 5.1 Root Cause Analysis

Before re-enabling any lane:

> ⚠️ **NOT YET SHIPPED:** Dark Factory evaluator was planned but never implemented. Use `forge task list` to verify task health.

1. Identify which tasks are still failing using `forge task list`.
2. Review audit log for lane overrides:
   ```bash
   tail -50 .forge/audit/lane-overrides.jsonl 2>/dev/null || echo "No overrides recorded"
   ```
3. Confirm `forge task list` shows no failed tasks before re-enabling.
4. Write a short post-mortem note in `docs/postmortems/` before re-enabling.

### 5.2 Re-Enable a Single Lane

Re-enable lanes one at a time. Start with the lowest-risk autonomous lane (`docs` or `test-writing`) and observe for at least 2 hours before enabling the next.

> ⚠️ **NOT YET SHIPPED:** Dark Factory evaluator and lane-policy enforcement were planned but never implemented. Steps below use only shipped commands.

**Step 1: Verify the fix is working.**
```bash
forge task list
forge task list --status failed
```

**Step 2: Send re-enable directive.**
```bash
forge lead send --to-node <node> --task-id RECOVER-$(date +%s) \
  --summary "RECOVER: resume dispatching [task-type] tasks" --priority high --strict
```

**Step 3: Monitor task health for 2 hours.**
```bash
forge task list
```

> ⚠️ **NOT YET SHIPPED:** Dark Factory evaluator, canary audit sampling, lane-policy API — none of these were implemented.

**Step 4: Log recovery in the incident record.**
```bash
forge task create "incident resolved: [task-type] recovered — [DATE]" --priority low
```

### 5.3 Re-Enable Node After Node-Level Rollback

**Step 1: Restart the forged daemon on the affected node.**
```bash
# On the affected node
forge daemon restart
```

**Step 2: Verify the node heartbeat is healthy.**
```bash
forge node status
forge node list
```

**Step 3: Confirm the scheduler is recommending the node again.**
```bash
forge status
```

**Step 4: Remove the node from any manual exclusion lists.**

If the node was manually excluded via `--exclude_nodes` in any dispatch config, remove the exclusion.

---

## 6. Communication Template

Use this template for stakeholder status updates. Send at incident open, every 30 minutes during active response, and at resolution.

```
FORGE DARK LANE INCIDENT — [STATUS: OPEN / UPDATE / RESOLVED]

Date/Time:    [UTC timestamp]
Severity:     [P0 / P1 / P2]
Incident ID:  [task ID from forge tasks create]

SUMMARY
[1-2 sentences describing what happened and what is currently affected.]

SCOPE
- Lanes affected: [e.g. autonomous/docs, autonomous/test-writing, ALL]
- Nodes affected: [e.g. prya, ALL]
- Tasks affected: [count and task IDs if known]
- Data impact: [None / Possible / Confirmed — describe if present]

CURRENT STATE
- Rollback applied: [lane-level / node-level / full]
- Tasks in failed state: [count from forge task list --status failed]
- Evaluator pass rate: [N/A — Dark Factory evaluator never shipped]
- In-flight tasks draining: [Y/N, estimated time]

NEXT ACTION
[One sentence describing the next step and who owns it.]

TIMELINE
[HH:MM] [Event]
[HH:MM] [Event]
...

RESOLUTION (fill in when resolved)
Root cause: [brief description]
Fix applied: [what changed]
Lane re-enabled at: [UTC timestamp]
Post-mortem: [link to docs/postmortems/ entry]
```

---

## 7. Tabletop Scenarios

Practice these scenarios before enabling autonomous lanes in production. Each scenario should take 10-15 minutes to walk through with the on-call operator.

---

### Scenario A — Evaluator False-Pass on a Docs Lane Task

**Setup:**
The `docs` lane has been autonomous for 3 days. The daily scorecard shows `pass_rate: 1.0` (all 100% passing), but a manual spot-check on a recently completed `docs-update` task reveals the agent replaced a critical API parameter description with placeholder text. The evaluator did not catch it because the check only verified file modification count, not content quality.

**Discussion questions:**
1. What severity do you assign? Why?
2. Which evaluator check failed — and how does that affect your rollback decision?
3. Do you roll back the `docs` lane only, or do you broaden scope?

**Expected operator actions:**
1. Classify as P2 (quality regression, no data corruption or security issue).
2. Run `forge task list` to confirm the false-pass pattern.
3. Apply task-isolation for the affected task type (Section 4.1).
4. Investigate task output quality manually.
5. Re-enable task dispatch after confirming fix.

> ⚠️ **NOT YET SHIPPED:** Dark Factory evaluator, lane policy, and audit sampling were planned but never implemented.

**Key learning:** False-pass incidents are P2 by default unless evidence of propagation to security or data domains is found. Evaluator check profiles must test content, not just process exit codes.

---

### Scenario B — Path Lock Collision Storm on Node `sati`

**Setup:**
Node `sati` is running 5 agents. The scheduler logs begin showing repeated `PATH CONFLICT` entries for `harness/forge_harness/webhook_server/routes.py`. Three tasks are stuck in `claimed` state with stale leases (lease TTL expired 20 minutes ago, auto-recovery worker appears to have not run). A fourth task transitions to `failed` state with no clear reason.

**Discussion questions:**
1. Is this P0, P1, or P2?
2. Does the stuck lease situation require a full rollback, or is a node-level response sufficient?
3. What is the risk of force-releasing the stale leases vs. waiting for the recovery worker?

**Expected operator actions:**
1. Classify as P1 (SLO breach — tasks stuck, requeue rate elevated).
2. Apply node-level rollback for `sati` (Section 4.2) — exclude from scheduler.
3. Check node status:
   ```bash
   forge node status
   forge node list
   ```
4. Clear the stale index lock if present:
   ```bash
   rm -f /home/openclaw/work/FORGE/.git/index.lock
   ```
5. Investigate why tasks are stuck — check forged daemon on `sati`.
6. Restart forged daemon on `sati` (`forge daemon restart`). Verify it reconnects.
6. Investigate why the stale-lease recovery worker stopped. Check forged daemon process on `sati`.
7. Restart forged daemon on `sati` (`forge daemon restart`). Verify it reconnects to the heartbeat store.
8. Re-enable `sati` after 30-minute clean window with no new PATH CONFLICT entries.

**Key learning:** Path lock collisions and stale leases are often recoverable without a full rollback. Node-level isolation + manual requeue is sufficient when no data corruption is involved.

---

### Scenario C — Silent Autonomous Completion of a Blocked Task Type

**Setup:**
At 02:00 UTC during an overnight autonomous run, a task with `task_type=deployment` and `risk_tier=medium` is found in `completed` state with `auto_approved=true`. According to the lane policy matrix, `deployment/medium` should be `human-review`, not `autonomous`. The task appears to have completed a staging deployment. No evaluator failure was recorded. The lane policy enforcer logs show no override entry.

**Discussion questions:**
1. What severity is this? What additional information do you need before deciding?
2. How might a `deployment/medium` task have ended up in the autonomous pathway?
3. What is the rollback scope?

**Expected operator actions:**
1. Classify as P0 immediately (a deployment completed without human approval — this is a policy violation regardless of whether damage occurred).
2. Trigger full rollback (Section 4.3) — halt all autonomous dispatch across all nodes.
3. Check the override audit log:
   ```bash
   cat .forge/audit/lane-overrides.jsonl | python3 -m json.tool
   ```
4. Check the task's policy decision metadata:
   ```bash
   curl -H "Authorization: Bearer $TOKEN" http://localhost:8081/api/task/TASK_ID
   ```

> ⚠️ **NOT YET SHIPPED:** Dark Factory lane policy enforcement and override audit log were planned but never implemented.
5. Examine the staging deployment result for any unintended changes. Coordinate with the human who owns the staging environment.
6. Identify the policy enforcement gap: Was the enforcer config modified? Was the task mislabeled at creation time? Was there a code bug in `LanePolicyEnforcer`?
7. Do not re-enable any autonomous lanes until the root cause is identified, fixed, and a test is added to prevent recurrence.
8. Write a post-mortem in `docs/postmortems/` before re-enabling.

**Key learning:** Any autonomous completion of a task type that should be `human-review` or `blocked` is P0, regardless of outcome. Policy enforcement gaps must be treated as security issues. The `LanePolicyEnforcer` invariants (security/deploy never autonomous) are hard gates — a breach means the gate failed.

---

## 8. Quick Reference Card

### Status Commands

```bash
forge status                          # Fleet dashboard
forge node status                    # All-node heartbeat
forge node list                       # Cross-node mesh status
forge task list                      # Task queue
```

> ⚠️ **NOT YET SHIPPED:** `forge evaluator`, `forge approve`, `forge status --watch` (TUI) — these Dark Factory commands were planned but never implemented in the binary.

### Triage Commands

```bash
# Return a stuck task to the queue
forge task complete TASK_ID  # marks done; create new task to re-queue

# Check lane override audit log
cat .forge/audit/lane-overrides.jsonl

# Clear stale git index lock
rm -f .git/index.lock
```

# Check lane override audit log
cat .forge/audit/lane-overrides.jsonl

# Clear stale git index lock
rm -f .git/index.lock
```

### Rollback Commands (narrowest to broadest)

> ⚠️ **NOT YET SHIPPED:** `/api/lane-policy` and Dark Factory lane policy enforcement were planned but never implemented. Use `forge lead send` for cross-node coordination.

```bash
# Node-level: halt dispatch to a specific node
forge lead send --to-node NODE_NAME \
  --task-id "incident-$(date +%s)" \
  --summary "INCIDENT: pause all task intake on this node immediately" \
  --strict

# Full rollback: halt all nodes
for node in prya sati nova vega; do
  forge lead send --to-node "$node" \
    --task-id "emergency-halt-$(date +%s)" \
    --summary "EMERGENCY: halt all task intake immediately" \
    --strict
done
```

### Recovery Commands (re-enable a node)

```bash
# Send recovery directive to a node
forge lead send --to-node NODE_NAME \
  --task-id "recover-$(date +%s)" \
  --summary "RECOVER: resume normal task intake" \
  --strict

# Confirm recovery
forge node status
forge node list
```

> ⚠️ **NOT YET SHIPPED:** Dark Factory lane policy enforcement was planned but never implemented.

### Severity Decision Tree

```
Incident detected
 └── Is there data corruption, security breach, or policy bypass?
       ├── YES → P0. Full rollback now. No further questions.
       └── NO → Are SLOs breached? (task failures > threshold, tasks stuck > 1h?)
                  ├── YES → P1. Node or task isolation. Investigate in parallel.
                  └── NO → Is quality declining?
                              ├── YES → P2. Investigate task output. Isolate affected task type.
                              └── NO → Not an incident. Log observation. Continue.
```

---

## 9. Related Documents

| Document | Purpose |
|---|---|
| `docs/runbooks/LANE_POLICY_MATRIX.md` | Lane assignment rules and override protocol |
| `docs/runbooks/NODE_ROLLOUT_CHECKLIST.md` | Pre-requisites before enabling dark lanes on a node |
| `docs/runbooks/MULTI_NODE_LEASE_OPERATIONS.md` | Lease management and path lock operations |
| `docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md` | Full DF transition plan |
| `forge-shared/modules/human-gates.md` | Human gate trigger catalog |
| `harness/forge_harness/dark_factory/lane_policy_enforcer.py` | Enforcer implementation |
| `harness/forge_harness/webhook_server/models/lane_policy.py` | Lane policy matrix as code |
| `harness/forge_harness/models/lease.py` | Lease state machine |
| `harness/forge_harness/webhook_server/services/evaluator.py` | Evaluator orchestrator |
| `harness/forge_harness/webhook_server/services/scheduler_policy.py` | Scheduler policy |
