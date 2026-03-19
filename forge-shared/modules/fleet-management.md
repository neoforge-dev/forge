# Fleet Management Protocol

## Context Management (CRITICAL)

**Always monitor context usage.** When an agent exceeds 50%:
1. Complete current atomic task
2. Run `/handoff` to save state
3. Update `docs/PROMPT.md` with current status
4. Start fresh session with `/resume` or read handoff

**Never let context exceed 80%.** At 80%+ Claude auto-compacts which loses detail.

## Before System Restart
```bash
./harness/scripts/fleet-save.sh --verbose           # Save all agent state
cat .forge_fleet/state.json | jq '.agents | keys'   # Verify
```

## After System Restart
```bash
./harness/scripts/fleet-restore.sh --autonomous prime  # Restore agents
./harness/scripts/fleet-heartbeat.sh                   # Verify started
./harness/scripts/fleet-monitor.sh --auto-handoff &    # Continuous monitoring
```

## Efficient Agent Monitoring
```bash
# GOOD - Structured summary
./harness/scripts/fleet-heartbeat.sh 2>/dev/null | grep -E "^(forge|Summary|Alerts)"

# BAD - Dumps raw pane content
tmux capture-pane -p -t forge:tech -S -100
```

## Fleet Commands
| Command | Purpose |
|---------|---------|
| `/fleet-status` | Quick status overview |
| `/fleet-save` | Save state before restart |
| `/fleet-restore` | Restore agents after restart |
| `/fleet-heartbeat` | Health check with actions |
| `/fleet-monitor` | Start background monitoring |

## Handoff Protocol

When context > 50% or switching tasks:
1. Create handoff: `/handoff` or update `docs/PROMPT.md`
2. Include: current task, progress, key files, next actions, blockers
3. Save fleet state: `./harness/scripts/fleet-save.sh`
4. New session reads: `Read docs/PROMPT.md`

## Agent Assignment

| Agent | Best For |
|-------|----------|
| forge:tech | Backend, infrastructure, providers |
| forge:game | Frontend UI, game logic |
| forge:codex | Component libraries, design systems |
| forge:qa | Testing, validation |
| forge:claude | Documentation, analysis, planning |
| Domain agents | Project-specific work |

## Long-Running Tasks

```bash
forge loop run -d DOMAIN -p PROJECT            # Ralph loop for feature dev
forge loop run -d DOMAIN -p PROJECT --full    # Flywheel: scan → generate → implement
forge status --watch                            # Monitor fleet in real-time
```
