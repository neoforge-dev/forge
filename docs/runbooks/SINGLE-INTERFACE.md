# Single Interface Runbook

Talk to prya. Prya delegates to the fleet.

---

## Creating Tasks for Remote Nodes

```bash
forge task create --node sati --domain brandfocus-ai --product voice-coach \
  --title "Fix pricing copy" --priority high

forge task create --node any --domain interview-simulator --type content \
  --title "Write comparison post"
```

## Checking Node Status

```bash
forge preflight          # Full fleet health check
forge node list          # All nodes and status
forge node status sati   # Specific node
```

## Human Gates

All approvals route to nova:

```bash
forge gate status        # Show pending gates
forge gate approve <id>  # Approve
forge gate reject <id> --reason "Needs tests"
```

## Viewing Results

```bash
forge task results <task-id>    # Local results
git pull                        # Pull remote results
ls .forge/heartbeat/results/    # View files
forge fleet results --today     # Fleet summary
```

## Dark Factory

Auto-dispatches queued tasks every 10 minutes.

```bash
forge queue list         # View queue
forge queue pause        # Pause
forge queue resume       # Resume
forge queue dispatch --now  # Force immediate
```

## Cross-Node Messaging

```bash
forge lead send --to-node sati "Priority: ship landing page today"
forge lead broadcast "New API key in 1Password"
forge lead status --sent-today
```

## Quick Reference

| Action | Command |
|--------|---------|
| Fleet health | `forge preflight` |
| Create remote task | `forge task create --node <name>` |
| Approve gate | `forge gate approve <id>` |
| Pull results | `git pull && ls .forge/heartbeat/results/` |
| Send directive | `forge lead send --to-node <name> "..."` |

---

*Last updated: 2026-04-04*
