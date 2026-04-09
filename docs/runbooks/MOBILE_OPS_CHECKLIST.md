# Mobile Operator Readiness Checklist

**FORGE Dark Factory Operations — Mobile/Terminal Guide**
**⚠️ DARK FACTORY MODE: Most commands in this file reference features that were planned but NEVER SHIPPED (evaluator, approve, balancer, loop, df, claims). Use `forge status` and `forge task list` for actual v4 operations.**

For operators managing the fleet from mobile devices, tablets, or remote terminals.

---

## 1. Prerequisites

### 1.1 Required CLI Tools

| Tool | Version | Check Command | Install If Missing |
|------|---------|---------------|-------------------|
| forge CLI v2 | 2.0.0+ | `forge --version` | `pip install -e harness/` |
| Python | 3.11+ | `python --version` | uv/conda install |
| tmux | 3.3+ | `tmux -V` | system package manager |
| ssh | OpenSSH 8+ | `ssh -V` | system default |
| curl | 7.8+ | `curl --version` | system default |

Verify installation:
```bash
forge --version    # Should show "forge, version 2.0.0"
```

### 1.2 Terminal App Configuration

**Recommended Apps:**
- iOS: Blink Shell, Termius, a-Shell
- Android: Termux, JuiceSSH
- Desktop: iTerm2 (macOS), Windows Terminal, GNOME Terminal

**Required Settings:**

| Setting | Value | Why |
|---------|-------|-----|
| Font | JetBrains Mono or Fira Code | Unicode support, readable |
| Font Size | 12-14pt | Mobile readability |
| Color Theme | Dark mode | Battery saving |
| Escape Sequence | `^[` (default) | tmux compatibility |
| SSH Keepalive | 60 seconds | Prevent timeout |
| Scrollback | 10,000+ lines | Full output capture |

**Key Bindings:**
```
Ctrl+A          # tmux prefix (customize if needed)
Ctrl+A then C   # New window
Ctrl+A then N   # Next window
Ctrl+A then P   # Previous window
Ctrl+A then D   # Detach
Ctrl+A then [   # Scroll mode
```

### 1.3 SSH Access to Nodes

**Required Access:**

| Node | Role | SSH Target | Check Command |
|------|------|------------|---------------|
| prya | forged daemon (lead) | `ssh prya` | `ssh prya "forge status"` |
| sati | Fleet node | `ssh sati` | `ssh sati "forge status"` |
| nova | Dev lead | `ssh nova` | `ssh nova "forge status"` |
| code-vega | Secondary node | `ssh code-vega` | `ssh code-vega "forge health"` |

**SSH Config Template** (`~/.ssh/config`):
```ssh
Host prya
    HostName prya.local
    User openclaw
    IdentityFile ~/.ssh/forge_ops
    ServerAliveInterval 60
    ServerAliveCountMax 3

Host sati
    HostName sati.local
    User openclaw
    IdentityFile ~/.ssh/forge_ops
    ServerAliveInterval 60

Host nova
    HostName nova.local
    User openclaw
    IdentityFile ~/.ssh/forge_ops
    ServerAliveInterval 60

Host code-vega
    HostName code-vega.local
    User openclaw
    IdentityFile ~/.ssh/forge_ops
    ServerAliveInterval 60
```

### 1.4 Authentication Tokens

**Required Environment Variables:**

| Variable | Source | Set In |
|----------|--------|--------|
| `FORGE_WEBHOOK_TOKEN` | forged daemon admin | `~/.bashrc` or terminal profile |
| `FORGE_API_URL` | Default: http://localhost:8081 | `~/.bashrc` or terminal profile |
| `FORGE_ROOT` | Your FORGE clone path | `~/.bashrc` or terminal profile |

**Quick Verify:**
```bash
echo $FORGE_WEBHOOK_TOKEN | head -c 20 && echo "..."
```

### 1.5 forged daemon Bookmark

**Bookmark URL:** `http://localhost:8081` (when tunneled) or your deployed URL

**Mobile-Friendly Dashboard Access:**
```bash
# Create SSH tunnel to forged daemon
ssh -L 8080:localhost:8081 prya -N

# Then access in browser: http://localhost:8081
```

---

## 2. Quick Status Commands

### 2.1 Essential Status Commands

> ⚠️ **Dark Factory commands (evaluator, approve, balancer, loop) were NEVER SHIPPED.** Use only the commands below.

```bash
# === FLEET OVERVIEW ===
forge status                    # Full fleet status (agents, tasks)
forge status --json             # Machine-readable output

# === TASK QUEUE ===
forge task list                 # Pending tasks
forge task list --status pending
forge task list --priority high
forge task list                 # Queue view

# === NODE HEALTH ===
forge node status               # All-node heartbeat
forge node list                 # Cross-node mesh

# === NOT YET SHIPPED ===
# forge evaluator summary/status  — Dark Factory quality pipeline
# forge approve --list           — Human-review queue
# forge balancer status/agents   — Load balancer
# forge status --watch           — Live TUI (never shipped)
```

### 2.2 Node-Specific Commands

```bash
# === PRYA (forged daemon) ===
ssh prya "forge status"

# === SATI (Fleet Node) ===
ssh sati "forge status"
ssh sati "forge node status"

# === NOVA (Dev Lead) ===
ssh nova "forge status"
ssh nova "forge node status"
```

### 2.3 JSON Output for Scripting

```bash
# Parse with jq (install if needed)
forge status --json | jq '.agents[] | select(.status == "error")'
forge task list --json | jq '.tasks[] | select(.priority >= 4)'
```

---

## 3. Incident Response from Mobile

### 3.1 Lane Pause/Resume

**Pause a Lane:**
```bash
# Pause specific lane
forge dispatch send lead "PAUSE lane=docs reason=quality-drop"

# Pause multiple lanes
forge dispatch send lead "PAUSE lane=api_simple,api_stateful reason=evaluator-backlog"

# Check active tasks
forge task list

> ⚠️ Dark Factory lane pause/resume was planned but never implemented via CLI.
```

**Resume:**
```bash
forge lead send --to-node prya --task-id RESUME-001 \
  --summary "RESUME normal operations" \
  --strict
```

**Reference:** See `DARK_LANE_INCIDENT_RUNBOOK.md` Section 4 for full protocol.

### 3.2 Node Pause/Drain

**Graceful Node Drain:**
```bash
# Stop new task assignment to node
forge dispatch send forge:prya "NODE_DRAIN reason=maintenance"

# Check node status
forge node status
forge node list
```

**Emergency Node Pause:**
```bash
# Immediate pause (use with caution)
forge dispatch send forge:prya "NODE_PAUSE reason=critical-error"
```

### 3.3 Node Health Check

```bash
# Node heartbeat and mesh status
forge node status
forge node list
```

> ⚠️ **NOT YET SHIPPED:** `forge evaluator summary/status/results` — Dark Factory quality pipeline never shipped.

### 3.4 View Recent Errors

```bash
# Recent events (polling fallback)
curl -s "http://localhost:8081/api/events/recent?limit=20" \
  -H "Authorization: Bearer $FORGE_WEBHOOK_TOKEN" | jq '.data.events'

# Task failures
forge task list --status failed

# Agent errors
forge status --json | jq '.agents[] | select(.status == "error")'
```

### 3.5 Emergency Contacts & Escalation

| Situation | First Contact | Escalation Path |
|-----------|---------------|-----------------|
| Node down | Check `forge node status` | Escalate to lead orchestrator |
| Task failures | Check `forge task list` | Investigate failed tasks |
| Security incident | Immediate lockdown | Security team + lead |
| Git lock contention | `rm -f .git/index.lock` | Alert if persists |

**Lead Orchestrator Contact:**
```bash
# Send urgent message to lead
forge lead send --to-node nova \
  --task-id URGENT-$(date +%s) \
  --priority high \
  --summary "URGENT: [describe issue]" \
  --strict
```

---

## 4. Daily Operator Routine

### 4.1 Morning Check (5 minutes)

```bash
#!/bin/bash
# Morning routine — run on prya or local

echo "=== FLEET STATUS ==="
forge status

echo "=== TASK QUEUE ==="
forge task list

echo "=== NODE HEALTH ==="
forge node status
```

**What to look for:**
- Any agents in `error` or `stale` status
- Tasks in `failed` state
- Node heartbeat issues

### 4.2 Midday Check (3 minutes)

```bash
# Quick fleet health
forge status

# Check for stuck tasks
forge task list
```

**What to look for:**
- Tasks stuck in `pending` > 10 items

### 4.3 Evening Check (5 minutes)

```bash
#!/bin/bash
# Evening routine

echo "=== FLEET STATUS ==="
forge status

echo "=== TASK QUEUE ==="
forge task list

echo "=== NODE HEALTH ==="
forge node status
```

**End-of-day checklist:**
- [ ] All critical tasks completed or queued for tomorrow
- [ ] No agents in `error` state
- [ ] No unacknowledged alerts

### 4.4 Weekly Review

```bash
# Weekly summary
forge portfolio status
forge status

# Check coverage
cat .forge/heartbeat/results/cli-parity-gap-analysis-s20.txt
```

---

## 5. Mobile-Friendly Tips

### 5.1 Terminal Shortcuts

Add to your terminal profile (`~/.forge/terminal-profile.sh`):

```bash
#!/bin/bash
# FORGE Mobile Operator Terminal Profile
export FORGE_ROOT=~/work/FORGE
export FORGE_API_URL=http://localhost:8081

# Quick aliases
alias fs='forge status'
alias ft='forge task list'
alias fn='forge node list'

# Mobile-friendly formats
alias fsj='forge status --json | jq'

# Incident response
alias fpause='forge dispatch send lead'
```

**Load profile:**
```bash
source ~/.forge/terminal-profile.sh
```

### 5.2 One-Line Status

```bash
# Ultra-compact status for small screens
forge status --json | jq -r '[.agents[] | .status] | group_by(.) | map("\(.[0]): \(length)") | join(", ")'
```

### 5.3 Copy-Paste Templates

**Incident Report Template:**
```
INCIDENT: [brief description]
TIME: $(date -Iseconds)
NODE: [affected node]
LANE: [affected lane]
STATUS: $(forge status --json | jq -c '.agents | map({id, status})')
ACTION TAKEN: [what you did]
```

**Handoff Template:**
```
HANDOFF: Shift change
FROM: [your name/node]
TO: [next operator]
STATUS: $(forge status --json | jq '.summary')
PENDING: $(forge task list | wc -l) tasks in queue
ALERTS: [any ongoing issues]
```

---

## 6. Troubleshooting from Mobile

### 6.1 Connection Issues

```bash
# Test SSH connectivity
for node in prya sati nova; do
  echo -n "$node: "
  ssh -o ConnectTimeout=5 $node "echo OK" 2>/dev/null || echo "FAIL"
done

# Test forged daemon
curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:8081/health || echo "forged down"
```

### 6.2 Slow Response

```bash
# Check node load
for node in prya sati nova; do
  echo "$node: $(ssh $node 'uptime')"
done

# Check agent count
forge status --json | jq '.agents | length'
```

### 6.3 Git Lock Issues

```bash
# Auto-fix git locks
forge git-guard --fix

# Check for stale locks
find ~/work/FORGE -name "index.lock" -type f -mmin +10 2>/dev/null
```

### 6.4 Token Expiration

```bash
# Verify token works
curl -s http://localhost:8081/api/health \
  -H "Authorization: Bearer $FORGE_WEBHOOK_TOKEN" | jq '.status'

# If 401, token expired — contact lead for refresh
```

---

## 7. Reference Links

| Document | Purpose | Quick Access |
|----------|---------|--------------|
| `DARK_FACTORY_OPERATOR_GUIDE.md` | Full operator manual | `cat docs/runbooks/DARK_FACTORY_OPERATOR_GUIDE.md` |
| `DARK_LANE_INCIDENT_RUNBOOK.md` | Lane pause/resume protocol | Section 4 |
| `CLI_REFERENCE.md` | All forge commands | `forge --help` |
| `AGENTS.md` | Fleet operations | Top-level project file |

---

## 8. Quick Reference Card

### Essential Commands (Memorize These)

```
fs      = forge status
ft      = forge task list
fn      = forge node list
forge dispatch send    = Send command to agent
forge lead send --strict = Cross-node message
```

---

**Last Updated:** 2026-02-23  
**Version:** 1.0  
**Owner:** FORGE Operations Team
