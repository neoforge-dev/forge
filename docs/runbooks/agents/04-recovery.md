# Agent Packet 04: Recovery

Purpose: recover from stale context, dispatch failures, and node drift.

## 1. Context Recovery

When context > 50%:

```bash
/handoff-clean
forge handoff read
```

## 2. Dispatch Recovery

If dispatch fails:

```bash
forge doctor
forge dispatch send forge:agent "Task: .forge/dispatches/dispatch-retry.md" --json
```

If cross-node delivery fails:

```bash
forge lead send \
  --to-node prya \
  --task-id RETRY-1 \
  --summary "retry" \
  --strict \
  --json
```

## 3. Node Recovery

```bash
forge status
forge node list
forge lead inbox
```

## 4. Escalation

1. `lead preflight` failing on URL/token/health: fix config and retry.
2. stale target node: re-bootstrap node before dispatch.
3. repeated API 429/5xx: rotate task lane and log blocker in `.forge/heartbeat/results/*`.
