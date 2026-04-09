# Node Restore Runbook — Single Command Recovery

**When:** After restart, after git pull, after any "node is broken" situation.
**Where:** Run this ON the node being restored (SSH in or use local terminal).

## One-Command Restore (All Nodes)

```bash
cd ~/work/FORGE && git pull --no-rebase && .forge/scripts/forge-startup.sh
```

That's it. The script is idempotent and handles:
- Git hooks installation
- Daemon start (hub only — skips on workers)
- tmux session creation (forge + forge-monitor)
- Agent window setup (per-node allocation)

## Per-Node Details

### prya (Hub — 16GB RAM)
```bash
# prya is the hub. Daemon runs HERE.
cd ~/work/FORGE && git pull --no-rebase && .forge/scripts/forge-startup.sh
# Then start agents in tmux windows:
# tmux attach -t forge → navigate to agent windows → start each agent
```
Agents: kimi, pi, gemini, minimax, glm (lightweight only — NO opencode/kilo)

### sati (Worker — 64GB RAM)
```bash
# Kill any accidental local daemon first
pkill -f "forged --port" 2>/dev/null
cd ~/work/FORGE && git pull --no-rebase && .forge/scripts/forge-startup.sh
# Verify pointing to prya:
echo $FORGE_API_URL  # should be http://prya:8081
curl -sf http://prya:8081/health  # should return {"status":"ok"}
```
Agents: kimi, gemini, pi, minimax, glm, opencode, kilo (heavy agents OK here)

### nova (Worker — 48GB RAM, macOS)
```bash
pkill -f "forged --port" 2>/dev/null
cd ~/work/FORGE && git pull --no-rebase && .forge/scripts/forge-startup.sh
# Verify:
curl -sf http://prya:8081/health
```
Agents: kimi, gemini, pi (+ iOS builds via forge-ios)

### gaea (Worker — 16GB RAM, macOS laptop)
```bash
pkill -f "forged --port" 2>/dev/null
cd ~/work/FORGE && git pull --no-rebase && .forge/scripts/forge-startup.sh
```
Agents: kimi, pi (lightweight only — laptop, off-hours)

### vega (Auxiliary — 16GB RAM, macOS Ventura)
```bash
pkill -f "forged --port" 2>/dev/null
cd ~/work/FORGE && git pull --no-rebase && .forge/scripts/forge-startup.sh
```
Agents: pi only (too old for modern Xcode, auxiliary use)

## Starting Agents in tmux Windows

After startup script creates windows:
```bash
tmux attach -t forge
# Navigate to agent window (Ctrl-b + window number)
# Start the agent:
kimi -y          # kimi window
gemini -y        # gemini window
pi               # pi window
minimax          # minimax window
glm              # glm window
opencode         # opencode window (sati/nova only)
kilo             # kilo window (sati/nova only)
```

## Verify Node is Healthy

From the node itself:
```bash
forge status                           # Shows fleet status from hub
curl -sf http://prya:8081/health       # Hub reachable?
tmux list-windows -t forge             # Agent windows exist?
```

From prya (remote check):
```bash
forge node list                        # Shows all registered nodes
forge node status <node-name>          # Ping specific node
```

## Common Issues

**"forged daemon started on :8081" on a worker node:**
```bash
pkill -f "forged --port"  # Kill it — only prya runs the daemon
git pull --no-rebase       # Get the hub-spoke enforcement fix
```

**Agent shows "offline" despite running in tmux:**
The agent CLI hasn't sent a heartbeat yet. Either:
- Wait 30s for auto-heartbeat
- Or manually: `forge agent heartbeat <agent-name>`

**"Cannot reach prya:8081" from worker:**
```bash
tailscale status | grep prya    # Is Tailscale connected?
ping prya                        # DNS resolves?
curl http://prya:8081/health     # Daemon running on prya?
```
