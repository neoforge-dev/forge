# Dark Factory Rollback Runbook

**Scope:** Emergency procedures for rogue dark-factory patrols  
**Patrols Covered:** `auto-promote`, `result-monitor`, `work-strategy`  
**Flag Gate:** `FORGE_DARK_FACTORY` env var  
**Date:** 2026-04-06  

---

## Quick Reference

```bash
# EMERGENCY DISABLE (immediate)
export FORGE_DARK_FACTORY=false
forge patrol disable auto-promote
forge patrol disable result-monitor
forge patrol disable work-strategy

# Verify disabled
forge patrol list | grep -E "auto-promote|result-monitor|work-strategy"
```

---

## Detection: Symptoms by Patrol

### auto-promote (Risk: HIGH)
**What it does:** Advances tasks from COMPLETED to next lane

| Symptom | Severity | Detection Query |
|---------|----------|-----------------|
| Tasks in wrong lane | HIGH | `SELECT id, lane, state FROM tasks WHERE updated_at > NOW() - INTERVAL '10 minutes' AND origin = 'patrol:auto-promote' ORDER BY updated_at DESC;` |
| Tasks promoted too early | MEDIUM | Check if task had approval gate before promotion |
| Rapid lane changes | HIGH | `SELECT task_id, COUNT(*) FROM task_transitions WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY task_id HAVING COUNT(*) > 3;` |

**False positive check:**
```bash
# Check if promotions were user-initiated
forge task show <task-id> | grep -E "origin|triggered_by"
# User promotions show origin="manual" or triggered_by="orchestrator"
```

### result-monitor (Risk: MEDIUM)
**What it does:** Processes result files, marks tasks complete

| Symptom | Severity | Detection Query |
|---------|----------|-----------------|
| Tasks completed without result file | HIGH | `SELECT t.id FROM tasks t LEFT JOIN task_results r ON t.id = r.task_id WHERE t.state = 'COMPLETED' AND r.id IS NULL AND t.completed_at > NOW() - INTERVAL '1 hour';` |
| Wrong task marked complete | HIGH | `SELECT * FROM tasks WHERE state = 'COMPLETED' AND completed_at > NOW() - INTERVAL '10 minutes' ORDER BY completed_at DESC;` |
| Result files not moved to processed/ | MEDIUM | `ls .forge/heartbeat/results/*.md | wc -l` growing unexpectedly |

**Verify corruption:**
```bash
# Check if result file exists for completed task
find .forge/heartbeat/results -name "*<task-id>*.md" 2>/dev/null
# Should exist in results/ or processed/
```

### work-strategy (Risk: MEDIUM-HIGH)
**What it does:** Creates tasks from catalog when queue empty

| Symptom | Severity | Detection Query |
|---------|----------|-----------------|
| Spam tasks created | HIGH | `SELECT origin, COUNT(*) FROM tasks WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY origin HAVING origin LIKE 'patrol:work-strategy%';` |
| Tasks for wrong domain | HIGH | `SELECT * FROM tasks WHERE origin = 'patrol:work-strategy' AND domain != expected_domain;` |
| Catalog items without human vetting | MEDIUM | Check `.forge/work-strategy/catalog.toml` for unvetted entries |

**Check catalog state:**
```bash
cat .forge/work-strategy/catalog.toml | grep -c "vetted = true"
cat .forge/work-strategy/catalog.toml | grep -c "vetted = false"
```

---

## Immediate Rollback Steps

### Step 1: Disable via Environment (10 seconds)

On the affected node:
```bash
# Edit daemon environment
export FORGE_DARK_FACTORY=false

# If using systemd:
sudo systemctl edit forged --full
# Add: Environment="FORGE_DARK_FACTORY=false"

# If using tmux/screen:
tmux send-keys -t forged 'export FORGE_DARK_FACTORY=false' Enter
```

### Step 2: Disable via API (5 seconds)

```bash
# Disable specific patrols
forge patrol disable auto-promote
forge patrol disable result-monitor
forge patrol disable work-strategy

# Or disable all dark-factory patrols at once
forge patrol disable --tag=dark-factory
```

### Step 3: Verify Disable (5 seconds)

```bash
# Check patrol status
forge patrol list --format=table

# Should show:
# auto-promote    | disabled
# result-monitor  | disabled
# work-strategy   | disabled

# Check logs for confirmation
tail -50 /Users/bogdan/.forge/logs/v3-daemon.log | grep -i "dark\|patrol.*disabled"
```

### Step 4: Restart Daemon (if needed) (30 seconds)

```bash
# Graceful restart
forge daemon restart

# Or hard restart if unresponsive
sudo pkill -f forged && forge daemon start
```

---

## Data Recovery

### Recovery: auto-promote

**Scenario:** Tasks promoted to wrong lane

```bash
# Identify affected tasks
sqlite3 /Users/bogdan/.forge/data/forge.db <<EOF
SELECT 
    t.id, 
    t.lane as current_lane,
    tt.from_lane as previous_lane,
    t.updated_at
FROM tasks t
JOIN task_transitions tt ON t.id = tt.task_id
WHERE tt.to_lane = t.lane
  AND tt.created_at > datetime('now', '-1 hour')
  AND t.origin LIKE '%patrol:auto-promote%'
ORDER BY tt.created_at DESC;
EOF

# Rollback specific task
sqlite3 /Users/bogdan/.forge/data/forge.db <<EOF
UPDATE tasks 
SET lane = 'dev', state = 'COMPLETED', updated_at = datetime('now')
WHERE id = 'TASK-XXX-YYY';

INSERT INTO task_transitions (task_id, from_lane, to_lane, reason, created_at)
VALUES ('TASK-XXX-YYY', 'test', 'dev', 'ROLLBACK: auto-promote error', datetime('now'));
EOF
```

**Bulk rollback (use with extreme caution):**
```bash
# Rollback all auto-promote actions in last hour
sqlite3 /Users/bogdan/.forge/data/forge.db <<EOF
UPDATE tasks 
SET lane = 'dev'
WHERE lane = 'test'
  AND state = 'COMPLETED'
  AND updated_at > datetime('now', '-1 hour')
  AND origin LIKE '%patrol:auto-promote%';
EOF
```

### Recovery: result-monitor

**Scenario:** Task marked complete incorrectly

```bash
# Identify affected tasks
sqlite3 /Users/bogdan/.forge/data/forge.db <<EOF
SELECT 
    id, 
    state,
    completed_at,
    result_summary
FROM tasks
WHERE state = 'COMPLETED'
  AND completed_at > datetime('now', '-1 hour')
  AND (result_summary IS NULL OR result_summary = '')
ORDER BY completed_at DESC;
EOF

# Revert to previous state
sqlite3 /Users/bogdan/.forge/data/forge.db <<EOF
UPDATE tasks 
SET state = 'RUNNING', 
    completed_at = NULL,
    result_summary = NULL,
    updated_at = datetime('now')
WHERE id = 'TASK-XXX-YYY';

-- Requeue for processing
UPDATE tasks 
SET state = 'QUEUED',
    assigned_to = NULL,
    started_at = NULL
WHERE id = 'TASK-XXX-YYY';
EOF

# Move result file back for reprocessing (if exists)
mv .forge/heartbeat/results/processed/kimi-TASK-XXX-YYY.md \
   .forge/heartbeat/results/kimi-TASK-XXX-YYY.md 2>/dev/null || true
```

### Recovery: work-strategy

**Scenario:** Spam tasks created

```bash
# Identify auto-created tasks
sqlite3 /Users/bogdan/.forge/data/forge.db <<EOF
SELECT 
    id,
    title,
    domain,
    origin,
    created_at
FROM tasks
WHERE origin = 'patrol:work-strategy'
  AND created_at > datetime('now', '-1 hour')
ORDER BY created_at DESC;
EOF

# Cancel spam tasks (don't delete — audit trail)
sqlite3 /Users/bogdan/.forge/data/forge.db <<EOF
UPDATE tasks 
SET state = 'CANCELLED',
    result_summary = 'CANCELLED: work-strategy rollback',
    updated_at = datetime('now')
WHERE origin = 'patrol:work-strategy'
  AND created_at > datetime('now', '-1 hour')
  AND state IN ('QUEUED', 'RUNNING');
EOF

# Disable catalog temporarily
cp .forge/work-strategy/catalog.toml .forge/work-strategy/catalog.toml.bak
cat > .forge/work-strategy/catalog.toml <<EOF
# Catalog disabled due to work-strategy rollback
# Restore from catalog.toml.bak after investigation
EOF
```

---

## Post-Incident Checklist

### Immediate (within 1 hour)
- [ ] All 3 patrols disabled
- [ ] Affected tasks identified
- [ ] Data recovery complete (or in progress)
- [ ] Incident logged to `.forge/incidents/YYYY-MM-DD-{patrol-name}.md`

### Short-term (within 24 hours)
- [ ] Root cause identified
- [ ] Fix deployed (if code issue)
- [ ] Catalog vetted (if work-strategy issue)
- [ ] Re-enable with `FORGE_DARK_FACTORY=true` on staging first

### Long-term (within 1 week)
- [ ] Incident review completed
- [ ] Runbook updated with new learnings
- [ ] Tests added to prevent recurrence
- [ ] Monitoring alerts tuned

---

## Incident Template

Create file: `.forge/incidents/YYYY-MM-DD-{patrol}.md`

```markdown
# Incident: {patrol} Rollback

**Date:** YYYY-MM-DD  
**Patrol:** auto-promote | result-monitor | work-strategy  
**Severity:** HIGH | MEDIUM | LOW  

## Summary

{One paragraph describing what happened.}

## Detection

- Symptom: {what was observed}
- Time: {when first observed}
- Detector: {who/what found it}

## Impact

- Tasks affected: {count}
- Domains affected: {list}
- Data integrity: {impact assessment}

## Response

| Time | Action | Owner |
|------|--------|-------|
| T+0 | Disabled FORGE_DARK_FACTORY | {name} |
| T+5 | Rolled back {N} tasks | {name} |
| T+30 | Root cause identified | {name} |

## Root Cause

{Technical explanation.}

## Lessons Learned

1. {What went well}
2. {What could be improved}
3. {Action items}

## Follow-up

- [ ] {Action item} — Owner: {name} — Due: {date}
```

---

## Prevention

### Before Enabling Dark Factory
- [ ] Staging burn-in complete (≥48 hours)
- [ ] Catalog items human-vetted (≥3 entries)
- [ ] Rollback runbook reviewed
- [ ] Incident response team notified

### Monitoring
```bash
# Add to monitoring dashboard
forge patrol list --format=json | jq '.[] | select(.id | contains("auto-promote","result-monitor","work-strategy")) | {id, enabled, last_run, error_count}'

# Alert on:
# - error_count > 0 for any dark-factory patrol
# - task state changes > 10/minute from patrol origin
# - result files not processed > 5 minutes
```

---

## See Also

- `.forge/dispatch-templates/result-format-spec.md` — Result file format
- `docs/adr/ADR-049-autonomous-loop-lifecycle.md` — Patrol lifecycle ADR
- `cmd/forged/patrol.go` — Patrol implementation
