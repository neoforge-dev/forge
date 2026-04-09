# Retro and Backlog Process

**Status:** Canonical  
**Applies to:** All FORGE leads (nova, prya, sati, code-vega)  
**Enforcement:** Automated via orchestrator + CI

---

## 1. Process Overview

```
Complete Phase → Retro → Update Backlog → Prioritize → Start Next Phase
     ↑___________________________________________________________|
```

---

## 2. Step-by-Step

### Step 1: Phase Completion

**Trigger:** Phase criteria met (automated via orchestrator)

**Actions:**
1. Run `scripts/orchestrator-heartbeat.sh`
2. Verify phase completion in `.forge/orchestrator/phase_state.json`
3. Create handoff: `forge handoff create --from-agent $(hostname -s)`

**Automation:**
```bash
# This runs automatically when phase completes
.forge/scripts/phase-completion-hook.sh
```

---

### Step 2: Retro

**Trigger:** Immediately after phase completion

**Required Output:**
- File: `docs/retro-YYYY-MM-DD.md`
- Sections: What Went Good, What Went Bad, Action Items
- Metrics: Test counts, commits, velocity

**Template:**
```bash
cp docs/templates/RETRO_TEMPLATE.md docs/retro-$(date +%Y-%m-%d).md
```

**Enforcement:**
- CI check fails if retro not created within 1 hour of phase completion
- Block next phase until retro exists

---

### Step 3: Update Backlog

**Trigger:** After retro complete

**Required Actions:**
1. Read `docs/retro-YYYY-MM-DD.md`
2. Extract action items
3. Add to `docs/BACKLOG.md` with format:

```markdown
### BACKLOG-XXX: Title
**Status:** open
**Priority:** [critical|high|medium|low]
**Category:** [process|infra|testing|docs|fleet]
**Source:** Retro YYYY-MM-DD
**Assigned:** unassigned

**Problem:** Description
**Solution:** Proposed fix
**Acceptance:** How to verify done
```

**Automation:**
```bash
# Extract action items from retro and add to backlog
.forge/scripts/update-backlog.sh docs/retro-$(date +%Y-%m-%d).md
```

**Enforcement:**
- CI check: Backlog updated within 2 hours of retro
- Block next phase if backlog stale

---

### Step 4: Prioritize

**Trigger:** Before starting next phase

**Required Actions:**
1. Review `docs/BACKLOG.md`
2. Select items to work on
3. Mark as `in-progress` and assign
4. Update priority based on current context

**Command:**
```bash
# Show critical/high items
forge backlog list --priority critical,high

# Assign item
forge backlog assign BACKLOG-001 --to-agent nova
```

---

### Step 5: Start Next Phase

**Trigger:** After prioritization

**Prerequisites (enforced by orchestrator):**
- [ ] Previous phase retro exists
- [ ] Backlog updated
- [ ] At least 1 backlog item assigned
- [ ] Git clean (no uncommitted changes)

**Command:**
```bash
forge phase start --phase 5
```

---

## 3. Enforcement Mechanisms

### 3.1 CI Checks

**File:** `.github/workflows/process-compliance.yml`

```yaml
on:
  push:
    branches: [main]

jobs:
  retro-check:
    runs-on: ubuntu-latest
    steps:
      - name: Check retro exists for completed phase
        run: |
          PHASE=$(jq -r '.current_phase' .forge/orchestrator/phase_state.json)
          if [ ! -f "docs/retro-$(date +%Y-%m-%d).md" ]; then
            echo "ERROR: Retro required before starting phase $PHASE"
            exit 1
          fi

  backlog-check:
    runs-on: ubuntu-latest
    steps:
      - name: Check backlog updated
        run: |
          RETRO_DATE=$(date +%Y-%m-%d)
          if ! grep -q "Retro $RETRO_DATE" docs/BACKLOG.md; then
            echo "ERROR: Backlog not updated with retro items"
            exit 1
          fi
```

### 3.2 Orchestrator Blocks

**In `scripts/orchestrator-heartbeat.sh`:**

```bash
# Before allowing phase advancement
check_prerequisites() {
    local PHASE=$1
    
    # Check retro exists
    if [ ! -f "docs/retro-$(date +%Y-%m-%d).md" ]; then
        echo "ERROR: Create retro before advancing to phase $PHASE"
        return 1
    fi
    
    # Check backlog updated
    if ! grep -q "Retro $(date +%Y-%m-%d)" docs/BACKLOG.md; then
        echo "ERROR: Update BACKLOG.md with retro items"
        return 1
    fi
    
    return 0
}
```

### 3.3 Fleet Dispatch Blocks

**In `.forge/scripts/agent-message.sh`:**

```bash
# Before dispatching to fleet
check_process_compliance() {
    # If phase complete but no retro, block dispatches
    if phase_complete_but_no_retro; then
        echo "ERROR: Complete retro process before dispatching new work"
        return 1
    fi
}
```

### 3.4 Slack/Notification Alerts

**On phase completion:**
```
🎉 Phase 4 Complete!

Required actions (blocking):
1. Create retro: docs/retro-2026-02-25.md
2. Update backlog: docs/BACKLOG.md
3. Prioritize next items

Run: forge phase complete --retro --backlog
```

---

## 4. Lead Responsibilities

### Nova (Development Lead)
- Ensure retro created after each phase
- Update backlog with action items
- Assign critical items before next phase

### Prya (Control Plane Lead)
- Review retros from all nodes
- Consolidate cross-node improvements
- Update canonical process docs

### Sati (Capacity Lead)
- Track backlog item completion
- Report velocity metrics
- Identify capacity bottlenecks

---

## 5. Templates

### Retro Template

**Location:** `docs/templates/RETRO_TEMPLATE.md`

```markdown
# Retro - YYYY-MM-DD

## Metrics
- Duration:
- Commits:
- Tests Fixed:
- Agents Used:

## What Went Good
1.

## What Went Bad
1.

## Action Items
| Item | Priority | Owner |
|------|----------|-------|

## Backlog Items Created
- BACKLOG-XXX
```

### Backlog Item Template

**Location:** `docs/templates/BACKLOG_ITEM.md`

**Location:** `docs/templates/BACKLOG_ITEM.md`

```markdown
### BACKLOG-XXX: Title
**Status:** open
**Priority:** [critical|high|medium|low]
**Category:** [process|infra|testing|docs|fleet]
**Source:** Retro YYYY-MM-DD
**Assigned:** unassigned

**Problem:**
**Solution:**
**Acceptance:**
```

---

## 6. Commands

```bash
# Create retro from template
forge retro create

# Update backlog from retro
forge backlog update-from-retro docs/retro-YYYY-MM-DD.md

# List open backlog items
forge backlog list --status open

# Assign item
forge backlog assign BACKLOG-001 --to-agent nova

# Close item
forge backlog close BACKLOG-001 --resolution "Fixed in commit ABC"

# Check process compliance
forge process check
```

---

## 7. Exceptions

**Emergency Bypass:**
- Critical production issue
- Security incident
- Fleet-wide outage

**Bypass Process:**
1. Document exception reason
2. Create retro within 24 hours
3. Update backlog with lessons learned

---

*Process Version: 1.0*  
*Last Updated: 2026-02-28*  
*Owner: FORGE Leads*
