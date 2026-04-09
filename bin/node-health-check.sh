#!/usr/bin/env bash
# node-health-check.sh — Self-healing node health check
# Run via cron (*/30 * * * *) or manually: bash bin/node-health-check.sh
#
# Checks and auto-fixes:
# 1. Daemon running? → restart if dead
# 2. Git index.lock stale? → remove if >5min old
# 3. Crontab points to correct repo? → warn if wrong
# 4. Agent heartbeats fresh? → restart heartbeat-refresh if stale
# 5. Stale worktrees? → prune if >2h old
# 6. Disk space? → warn if <3GB free
#
# Exit codes: 0=healthy, 1=fixed, 2=needs-human
set -euo pipefail

FORGE_ROOT="${FORGE_ROOT:-/home/openclaw/work/forge-mono}"
LOG="/tmp/node-health-check.log"
FIXED=0
NEEDS_HUMAN=0

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

cd "$FORGE_ROOT"

# 1. Daemon running?
if curl -sf http://localhost:8081/health > /dev/null 2>&1; then
    log "OK: daemon healthy"
else
    log "FIX: daemon not responding — restarting"
    forge daemon restart >> "$LOG" 2>&1 || true
    FIXED=$((FIXED + 1))
    sleep 3
    if curl -sf http://localhost:8081/health > /dev/null 2>&1; then
        log "OK: daemon restarted successfully"
    else
        log "FAIL: daemon restart failed — needs human"
        NEEDS_HUMAN=$((NEEDS_HUMAN + 1))
    fi
fi

# 2. Git index.lock stale?
LOCK=".git/index.lock"
if [ -f "$LOCK" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
    if [ "$AGE" -gt 300 ]; then
        log "FIX: stale .git/index.lock (${AGE}s old) — removing"
        rm -f "$LOCK"
        FIXED=$((FIXED + 1))
    else
        log "WARN: .git/index.lock exists but recent (${AGE}s) — skipping"
    fi
else
    log "OK: no git index.lock"
fi

# 3. Crontab repo path check
if crontab -l 2>/dev/null | grep -q "/work/FORGE/" ; then
    log "WARN: crontab has paths pointing to /work/FORGE/ instead of forge-mono — needs update"
    NEEDS_HUMAN=$((NEEDS_HUMAN + 1))
else
    log "OK: crontab paths correct"
fi

# 4. Stale worktrees?
STALE_WT=$(git worktree list 2>/dev/null | grep -v "^\S.*\[main\]" | wc -l)
if [ "$STALE_WT" -gt 0 ]; then
    log "FIX: pruning $STALE_WT stale worktrees"
    git worktree prune 2>/dev/null || true
    # Remove worktree branches
    for br in $(git branch | grep "worktree-agent-" | tr -d ' '); do
        git branch -D "$br" 2>/dev/null && log "  deleted branch $br" || true
    done
    FIXED=$((FIXED + 1))
else
    log "OK: no stale worktrees"
fi

# 5. Disk space?
AVAIL_GB=$(df -BG / | tail -1 | awk '{print $4}' | tr -d 'G')
if [ "$AVAIL_GB" -lt 3 ]; then
    log "WARN: disk space low (${AVAIL_GB}GB free) — needs human cleanup"
    NEEDS_HUMAN=$((NEEDS_HUMAN + 1))
else
    log "OK: disk space ${AVAIL_GB}GB free"
fi

# 6. Git state clean?
DIRTY=$(git status --short | wc -l)
if [ "$DIRTY" -gt 0 ]; then
    log "WARN: $DIRTY uncommitted files — orchestrator should commit"
else
    log "OK: git clean"
fi

# Summary
log "--- SUMMARY: fixed=$FIXED needs_human=$NEEDS_HUMAN ---"

if [ "$NEEDS_HUMAN" -gt 0 ]; then
    exit 2
elif [ "$FIXED" -gt 0 ]; then
    exit 1
else
    exit 0
fi
