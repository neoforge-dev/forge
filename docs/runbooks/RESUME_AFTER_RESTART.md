# Resume After Restart

Use this after a reboot, daemon restart, or lost tmux session.

This runbook reflects the current `forge` CLI, not the older wrapper-first
workflow.

## 1. Fast Path

### On `prya`

```bash
forge node up
forge daemon status
forge status
forge node list
tmux ls
```

### On a non-hub node

```bash
forge node up
forge status
forge node list
tmux ls
```

`forge node up` is idempotent. It is the current canonical boot/resume path.

## 2. What A Healthy `prya` Restart Should Produce

After `forge node up` on `prya`, the expected state is:

1. `forged` is running and healthy.
2. `tmux` session `forge` exists with the `prya` lead window.
3. `tmux` session `forge-monitor` exists.
4. `prya` is registered and online in `forge node list`.
5. Patrols are active under the daemon.
6. Node self-heartbeats keep `prya` visible as online.
7. The lead can rebuild context from current state files and session artifacts.

## 3. Rehydrate Context

Read these in order:

```bash
sed -n '1,220p' docs/PROMPT-prya.md
sed -n '1,220p' docs/PLAN.md
sed -n '1,220p' .forge/session-persist/latest.md
forge preflight --json
forge gate status
```

If the latest session snapshot points to a transcript you still need, open the
transcript path recorded in `.forge/session-persist/latest.md`.

## 4. If `tmux` Sessions Are Missing

`forge node up` should recreate the standard sessions. Verify:

```bash
tmux ls
tmux list-windows -t forge
tmux list-windows -t forge-monitor
```

If the lead window is present but idle, resume from:
- `docs/PROMPT-{node}.md`
- `docs/PLAN.md`
- latest handoff in `.forge_sessions/`
- latest snapshot in `.forge/session-persist/`

## 5. If The Daemon Is Unhealthy

```bash
forge daemon status
forge daemon restart
forge status
```

If health still fails:

```bash
curl -sf http://localhost:8081/health
tail -n 200 ~/.forge/logs/v3-daemon.log
forge preflight --json
```

## 6. If Node Registration Looks Wrong

Use:

```bash
forge node list
forge node status prya
forge agent list
```

Check for:
- node offline or stale
- agents registered on the wrong node
- `nova` unavailable when human gates are queued there

## 7. Recover Previous Human-Written Context

Fastest sources:

```bash
find .forge_sessions -maxdepth 1 -type f | sort | tail -n 20
find .forge/session-persist/sessions -maxdepth 1 -type f | sort | tail -n 20
find .forge/heartbeat/results -maxdepth 1 -type f | sort | tail -n 40
find .forge/council -maxdepth 3 -type f | sort | tail -n 40
```

Use handoffs and session snapshots before opening full transcripts.

## 8. Common Issues

### Stale git lock

```bash
rm -f .git/index.lock
forge recover
```

### Dispatch target is just a shell

Recent `forge dispatch` now checks liveness, but if a window is stuck:

1. attach to the tmux pane
2. clear the stuck process
3. retry via `forge dispatch send ...`

### `nova` is offline

This is a structural issue, not a cosmetic one. `nova` is the designated
human-interface node, so deploy and App Store gates pile up when it is down.

## 9. Do Not Use As Canonical Restart Steps

These are no longer the primary restart surface:

- `.forge/scripts/forge-startup.sh`
- raw `tmux send-keys` for normal task delivery
- `docs/PROMPT.md`

Use `forge node up`, `forge lead send`, and the current node-specific prompt
files instead.
