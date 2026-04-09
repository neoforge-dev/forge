# Node Lead Setup Guide

**For:** New lead orchestrators on sati, nova, vega, or any new node.
**Updated:** 2026-02-28 (S52)

This guide covers: status line configuration, context sidecar trick, heartbeat hooks, and cross-node communication setup.

---

## 1. Prerequisites

```bash
# Ensure FORGE repo is cloned and up to date
cd $FORGE_ROOT && git pull   # FORGE_ROOT set in ~/.forgerc

# Ensure forge CLI is available
forge --version   # Should show CLI v2

# Ensure tmux is running
tmux new-session -d -s forge -n $(hostname)
```

---

## 2. Status Line + Context Sidecar (Critical)

The status line writes `context_window.used_percentage` to a sidecar file on every render. Hooks then read this to detect when context is critical (>75%).

### 2a. Copy the status line script

```bash
# Create the status line script:
cat > ~/.claude/starship-statusline.sh << 'SCRIPT'
#!/bin/bash
# Claude Code Status Line - writes context % to sidecar for FSM hooks

input=$(cat)

current_dir=$(echo "$input" | jq -r '.workspace.current_dir // .cwd')
model_name=$(echo "$input" | jq -r '.model.display_name // .model.id')
ctx_pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0')

# === THE KEY TRICK: export context % to sidecar file ===
FORGE_DIR="${FORGE_ROOT:-$HOME/FORGE}"
if [[ "$current_dir" == "$FORGE_DIR"* ]] && [[ "$ctx_pct" =~ ^[0-9]+$ ]]; then
  echo "$ctx_pct" > "$FORGE_DIR/.forge/heartbeat/context_percent" 2>/dev/null
fi

# Basic display
username=$(whoami)
hostname_short=$(hostname -s)
dir_short=$(echo "$current_dir" | sed "s|$HOME|~|" | rev | cut -d/ -f-3 | rev)
branch=$(git -C "$current_dir" branch --show-current 2>/dev/null)
git_info=""
if [ -n "$branch" ]; then
  modified=""
  [ -n "$(git -C "$current_dir" status --porcelain 2>/dev/null)" ] && modified=" [modified]"
  git_info=" $branch$modified"
fi

printf "%s@%s %s%s ctx:%s%% > %s" "$username" "$hostname_short" "$dir_short" "$git_info" "$ctx_pct" "$model_name"
SCRIPT
chmod +x ~/.claude/starship-statusline.sh
```

### 2b. Configure Claude Code to use it

Edit `~/.claude/settings.json`:

```json
{
  "model": "opus",
  "statusLine": {
    "type": "command",
    "command": "bash $HOME/.claude/starship-statusline.sh"
  }
}
```

### 2c. Verify it works

```bash
# Start Claude Code, then check:
cat $FORGE_ROOT/.forge/heartbeat/context_percent
# Should show a number like "12" (percent)
```

---

## 3. Heartbeat Hooks (Stop + PreCompact + SessionStart)

These hooks create the autonomous orchestration loop:
- **Stop hook (heartbeat_eval.sh):** Fires after every lead response. Outputs FSM state: dirty files, idle agents, new results, context %.
- **Stop hook (context_guard.sh):** Blocks stop when context >75% — forces /handoff.
- **PreCompact hook:** Saves working state before auto-compaction.
- **SessionStart[clear] hook:** Restores context from PROMPT.md after /clear.

### 3a. Copy project-level hooks

The hooks are already in the repo at `.claude/hooks/`. Verify they exist:

```bash
ls -la $FORGE_ROOT/.claude/hooks/
# Expected:
# heartbeat_eval.sh      — Stop hook (FSM state output)
# context_guard.sh       — Stop hook (blocks stop at >75%)
# heartbeat_eval_compact.sh — PreCompact hook (state snapshot)
```

### 3b. Configure project settings

The project `.claude/settings.json` should already be tracked. Verify it has hooks:

```bash
cat $FORGE_ROOT/.claude/settings.json
```

Expected content:
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "clear",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/session_start_clear.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/heartbeat_eval.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/context_guard.sh"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "auto",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/heartbeat_eval_compact.sh"
          }
        ]
      }
    ]
  }
}
```

### 3c. How the sidecar trick works

```
Status Line (renders every ~1s)
    │
    ├─ Reads: context_window.used_percentage from JSON input
    ├─ Writes: .forge/heartbeat/context_percent (sidecar file)
    │
Stop Hook (heartbeat_eval.sh, fires after every response)
    │
    ├─ Reads: .forge/heartbeat/context_percent
    ├─ If >70%: outputs CONTEXT_CRITICAL trigger
    │
Stop Hook (context_guard.sh)
    │
    ├─ Reads: .forge/heartbeat/context_percent
    ├─ If >75%: outputs {"decision":"block"} → forces /handoff
    │
PreCompact Hook (heartbeat_eval_compact.sh)
    │
    ├─ Saves state to .forge/heartbeat/pre_compact_state.md
    ├─ Includes: dirty files, pending results, recent commits, PROMPT.md
```

---

## 4. Cross-Node Communication

### 4a. Start XNode Listener

The XNode listener polls `.forge/xnode/lead-inbox/{node}.jsonl` for incoming directives.

```bash
# Start in forge-monitor tmux session
tmux new-window -t forge-monitor -n xnode
tmux send-keys -t forge-monitor:xnode "forge lead inbox" Enter
```

### 4b. Sending directives to other nodes

```bash
# Send a task to another node's lead
forge lead send \
  --to-node sati \
  --task-id T-123 \
  --priority high \
  --summary "Run full test suite on heavy workloads" \
  --durable

# Check sent messages
forge lead acks

# Preflight check (validates connectivity)
forge lead preflight --to-node nova
```

### 4c. Receiving directives

```bash
# Check inbox for incoming directives
forge lead inbox

# Acknowledge a received directive
forge lead ack MSG_ID --status completed --summary "Done — 36K tests pass"
```

### 4d. How cross-node works

```
Node A (prya)                          Node B (sati)
    │                                      │
    ├─ forge lead send --to-node sati      │
    │   └─ Writes to:                      │
    │     .forge/xnode/lead-inbox/sati.jsonl│
    │                                      │
    │          (git push)                  │
    │     ─────────────────────────>       │
    │                                      │
    │                              XNode listener polls
    │                              Reads sati.jsonl
    │                              Injects into lead context
    │                                      │
    │                              forge lead ack MSG_ID
    │     <─────────────────────────       │
    │          (git push)                  │
```

The key insight: cross-node communication uses **git as the transport layer**. Both nodes must be on the same branch (main) and push/pull regularly.

---

## 5. Fleet Agent Identity (New in S52)

When spawning fleet agents, inject identity env vars:

```bash
# In forge-startup.sh or manually:
for agent in minimax glm kimi gemini pi; do
    tmux new-window -t forge -n "$agent" -c "$FORGE_ROOT"
    tmux send-keys -t "forge:$agent" "export FORGE_AGENT_NAME=$agent FORGE_AGENT_TYPE=fleet" Enter
done

# For the lead window:
tmux send-keys -t "forge:$(hostname)" "export FORGE_AGENT_NAME=$(hostname) FORGE_AGENT_TYPE=orchestrator" Enter
```

Agents check `$FORGE_AGENT_TYPE` to know their role. See `CLAUDE.md` Agent Quick-Start section.

---

## 6. Full Startup Sequence

```bash
# 1. One-command startup (idempotent)
.forge/scripts/forge-startup.sh

# 2. Or manual setup:
# a. Start forged daemon (in forge-monitor:daemon) — CC Backend removed (ADR-040)
forge daemon start

# b. Check lead inbox (in forge-monitor:xnode window)
forge lead inbox

# c. Start fleet tmux session
tmux new-session -d -s forge -n $(hostname) -c $FORGE_ROOT
# ... create agent windows with identity env vars

# 3. Verify
forge doctor          # All checks should pass
forge status          # Fleet health snapshot
forge lead preflight --to-node prya  # Cross-node check
```

---

## 7. Migrating from Old Heartbeat

If you were using the old `orchestrator-heartbeat.sh` or `orchestrator-loop.sh`:

| Old | New |
|-----|-----|
| `scripts/orchestrator-heartbeat.sh` | Stop hook (`heartbeat_eval.sh`) — automatic, no script needed |
| `scripts/orchestrator-loop.sh` | Stop hook loop — fires after every response |
| `scripts/check-agent-health.sh` | `forge agent ping --agent forge:AGENT` |
| `scripts/check-agent-readiness.sh` | `forge agent status --agent forge:AGENT` |
| `scripts/update-heartbeat.sh` | Status line sidecar (`context_percent` file) |
| `scripts/dispatch-task.sh` | `forge dispatch send forge:AGENT "msg"` |
| Manual context tracking | Automatic via status line → sidecar → hooks |

The key migration: **heartbeat is no longer a cron/loop script**. It's event-driven via Claude Code hooks:
- Stop hook fires after every lead response → evaluates state
- PreCompact hook fires before auto-compact → saves state
- SessionStart[clear] hook fires after /clear → restores state
- Status line fires every ~1s → writes context % to sidecar

No manual heartbeat process needed.

---

## Quick Verification Checklist

- [ ] `cat ~/.claude/settings.json` shows statusLine config
- [ ] `cat $FORGE_ROOT/.claude/settings.json` shows hooks
- [ ] `cat $FORGE_ROOT/.forge/heartbeat/context_percent` shows a number
- [ ] `forge doctor` passes all checks
- [ ] `forge lead preflight --to-node prya` succeeds
- [ ] Fleet agents have `$FORGE_AGENT_TYPE=fleet` set
- [ ] Lead window has `$FORGE_AGENT_TYPE=orchestrator` set
