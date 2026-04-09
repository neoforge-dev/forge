# FORGE Troubleshooting Guide

Common issues and fixes for the FORGE v4 Go CLI (`forge`) and daemon (`forged`).

---

## 1. Daemon Not Starting

**Symptoms:** `forge` commands return connection errors, `curl http://localhost:8081/health` fails.

```bash
forge daemon status      # Check if running
forge daemon logs -f     # Stream logs
forge daemon restart     # Rebuild + restart
```

If `forge daemon restart` fails, try the manual path:

```bash
cd cmd/forged && go build -o forged .
kill $(pgrep -f "forged --port") 2>/dev/null || true
tmux send-keys -t forge-monitor:forged \
  "cd $(git rev-parse --show-toplevel) && ./cmd/forged/forged --port 8081 --ws-port 8082 --db .forge/forge-v3.db 2>&1 | tee -a /tmp/forged.log" ENTER
sleep 3 && curl -sf http://localhost:8081/health
```

---

## 2. `forge daemon restart` Fails with "unknown command build"

The `go` binary may not be on PATH correctly (mise shim issue).

**Fix:**

```bash
mise reshim
# or use the direct path:
mise exec go -- go build -C cmd/forged -o forged .
```

---

## 3. Agent Dispatches Not Received

**Symptoms:** Tasks created in the queue but agents never pick them up.

Agents must be running `forge work --daemon --interval 15s` to auto-claim tasks from the queue.

**Diagnostic:**

```bash
forge task list                          # See queued tasks
forge fleet windows                      # See which agents are alive in tmux
forge agent list                         # See registered agents
```

**Alternatives:**

```bash
# Send directly via tmux (bypasses queue)
tmux send-keys -t forge:AGENT "command" Enter

# Dispatch with tmux notification
forge dispatch send forge:AGENT --file .forge/dispatches/FILE.md
```

---

## 4. Port Already in Use

**Symptoms:** Daemon fails to bind `:8081` or `:8082`.

```bash
kill $(pgrep -f "forged --port") 2>/dev/null
sleep 2
forge daemon start
```

If the port is held by a zombie process:

```bash
lsof -i :8081    # Find the PID
kill -9 <PID>
forge daemon start
```

---

## 5. CLI Binary Stale (Missing Commands)

**Symptoms:** `forge <command>` returns "unknown command" for commands that should exist.

```bash
forge self-update   # Rebuild from source
# or manually:
go build -C cmd/forge -o ~/.local/bin/forge .
```

---

## 6. WebSocket Connection Issues

**Symptoms:** Agents can't connect, heartbeat not updating, fleet status stale.

```bash
curl -sf http://localhost:8081/health      # Check HTTP
wscat -c ws://localhost:8082/ws            # Check WebSocket
```

If WebSocket is down but HTTP works, the daemon may need a restart:

```bash
forge daemon restart
```

---

## 7. Tmux Session Problems

FORGE uses `tmux` for agent windows. Issues here prevent agents from starting.

**Diagnostic:**

```bash
tmux list-sessions                        # List all sessions
tmux list-windows -t forge                # List agent windows
forge fleet windows                       # FORGE-aware window list
```

**Common fixes:**

```bash
# Attach to an agent window to inspect
tmux select-window -t forge:AGENT

# If tmux is unresponsive (CAUTION: kills all sessions)
tmux kill-server
```

---

## 8. Context Exhaustion

**Symptoms:** Agent generates repetitive output, stops making progress, or reports high context usage.

```bash
forge agent list    # Check context % for each agent
```

**Fixes:**
- Run `/handoff` in the agent to save state and start fresh
- Break tasks into smaller sub-tasks
- Use `forge work --daemon` which auto-handles context rotation

---

## 9. Git Index Lock

**Symptoms:** Git operations fail with "Unable to create .git/index.lock".

```bash
rm -f .git/index.lock    # Always 0-byte stale lock
```

---

## 10. `grep -E` Broken

In this environment, `grep -E` is aliased to `rg` (ripgrep) which has different syntax.

**Fix:** Use `rg` directly, or `command grep -E` to bypass the alias. Agents should use the `Grep` tool instead.

---

## Quick Reference

| Need | Command |
|------|---------|
| System health | `forge status` |
| Daemon control | `forge daemon start/stop/restart` |
| View logs | `forge daemon logs -f` |
| Fleet status | `forge fleet windows` |
| Task queue | `forge task list` |
| Agent list | `forge agent list` |
| Rebuild CLI | `forge self-update` |
| Rebuild daemon | `go build -C cmd/forged -o forged .` |
