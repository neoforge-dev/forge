# FORGE CLI — One Way To Do Each Thing

> "There should be one — and preferably only one — obvious way to do it."

This is the **canonical command reference**. If a command isn't listed here, check `forge --help` — but prefer the paths below.

---

## 5 Core Workflows

### 1. Deploy a Product

```bash
# Pre-flight (checks CLIs, auth, network)
./bin/deploy-preflight.sh

# Deploy all products interactively (prompts for credentials)
./bin/deploy-gate-runner.sh all

# Deploy specific product
./bin/deploy-gate-runner.sh vc          # Voice Coach
./bin/deploy-gate-runner.sh mirrably    # Mirrably/VTO
./bin/deploy-gate-runner.sh septica     # Septica

# Dry run (show what would happen)
./bin/deploy-gate-runner.sh all --dry-run

# Post-deploy smoke test
./bin/deploy-smoke-test.sh
```

**NOT these:** ~~bin/deploy-all.sh~~ (IS-only), ~~railway up~~ (raw, no checks)

### 2. Check System Health

```bash
# Quick status (agents, queue, commits, patrols)
forge status

# Full health check with fixes
forge doctor

# Boot sequence on session start
forge preflight
```

**NOT these:** ~~forge fleet list~~ (use `forge agent list`), ~~bin/node-health-check.sh~~ (use `forge doctor`)

### 3. Manage Tasks

```bash
# List all tasks
forge task list

# Create a task
forge task create --domain forge --product forge --type research --priority high \
  --title "Task title" --description "What to do"

# Complete a task
forge task complete TASK-ID

# See what agents are working on
forge agent list
```

**NOT these:** ~~forge queue~~ (use `forge task list`), ~~forge fleet list~~ (use `forge agent list`)

### 4. Dispatch Work to Fleet

```bash
# Create task → daemon auto-dispatches to idle agents via patrol (30s cycle)
forge task create --domain X --product Y --type research --priority high \
  --title "..." --description "..."

# Named agent dispatch (research/docs only, NOT code)
forge dispatch send forge:AGENT --file .forge/dispatches/FILE.md

# Cross-node directive (urgent, durable)
forge lead send --to-node nova --task-id TASK-ID --summary "..." --durable

# Code changes → MUST use worktree agent (Agent tool with isolation: "worktree")
```

**NOT these:** ~~tmux send-keys~~ (25% reliability), ~~forge dispatch send for code~~ (banned by council S163)

### 5. Safe Git Operations

```bash
# On multi-agent nodes (gaea, nova, sati) — use gitsafe wrapper
bash bin/gitsafe.sh add <files>
bash bin/gitsafe.sh commit -m "message"

# On single-agent nodes (prya, vega) — regular git is fine
git add <files> && git commit -m "message"

# Always: pull before push
git pull --rebase origin main && git push origin main

# If push fails (remote changed)
GIT_OPTIONAL_LOCKS=0 git pull --rebase origin main && git push origin main
```

**NOT these:** ~~bin/forge-push.sh~~ (deleted), ~~git commit --amend~~ (create new commit instead)

---

## Dark Factory (Autonomous Operations)

```bash
# Check patrol status
forge patrol list              # All 35 patrols
forge patrol list --all        # With individual details

# Key patrols
# task-dispatcher (30s)  — assigns queued tasks to idle tmux agents
# auto-promote (5min)    — promotes completed tasks through lanes
# confidence-approve     — auto-approves high-confidence results
# result-monitor (2min)  — watches .forge/heartbeat/results/

# Cron jobs (crontab -l)
# */2  min  — heartbeat-refresh.sh (agent keep-alive)
# */10 min  — dark-factory-v3.sh (autonomous dispatch)
# */30 min  — forge preflight (health check)
# 7:03 UTC  — forge notify daily (Telegram digest)
# */4h      — forge notify gates (gate alerts)

# Manual dark factory run
.forge/scripts/dark-factory-v3.sh
```

---

## Node Management

```bash
# This node's identity
hostname                       # Convention: hostname = node ID

# Boot a node
forge up                       # Start daemon + tmux + agents

# Shut down
forge down                     # Stop agents + daemon

# Check node status
forge node status              # All nodes in mesh
forge node status nova         # Specific node

# Cross-node messaging
forge lead send --to-node nova --task-id X --summary "..." --durable
```

---

## Quick Reference

| I want to... | Command |
|-------------|---------|
| See fleet status | `forge status` |
| List agents | `forge agent list` |
| List tasks | `forge task list` |
| Create a task | `forge task create ...` |
| Deploy products | `./bin/deploy-gate-runner.sh all` |
| Check health | `forge doctor` |
| Start session | `forge preflight` |
| Send alert | `forge notify alert "message"` |
| Live dashboard | `forge tui` |
| Safe git commit | `bash bin/gitsafe.sh commit -m "msg"` |
| Notify another node | `forge lead send --to-node X ...` |

---

## Commands to AVOID (duplicates/deprecated)

| Don't Use | Use Instead | Why |
|-----------|-------------|-----|
| `forge fleet list` | `forge agent list` | Same data, agent list uses live API |
| `forge queue` | `forge task list` | Queue is subset view |
| `forge dashboard` | `forge tui` | Dashboard is static markdown |
| `forge monitor` | `forge tui` | Monitor has fewer views |
| `tmux send-keys` for dispatch | `forge task create` | 25% vs 99% reliability |
| `bin/deploy-all.sh` | `bin/deploy-gate-runner.sh` | gate-runner covers all products |
| Raw `railway up` | `bin/deploy-gate-runner.sh` | No preflight, no verification |

---

*Last updated: 2026-04-04 (S188)*
