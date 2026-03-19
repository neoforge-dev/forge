# Node Quick Start — Get Any Node Working in 2 Minutes

## Prerequisites
- Node has `git`, `go 1.21+`, Tailscale connected
- Can reach node-1: `curl http://node-1:8081/health`

## Step 1: Pull Latest (30 seconds)
```bash
cd ~/work/FORGE && git pull
```

## Step 2: Build CLI (30 seconds)
```bash
cd cmd/forge && go build -o forge . && sudo cp forge /usr/local/bin/
```

## Step 3: Set Environment (10 seconds)
```bash
# Add to ~/.profile or ~/.zshrc if not already there
export FORGE_API_URL=http://node-1:8081
export FORGE_AGENT_TYPE=fleet
export FORGE_AGENT_NAME=$(hostname)
```

## Step 4: Register Node (10 seconds)
```bash
source ~/.profile  # or ~/.zshrc
forge node join
forge status
```

## Step 5: Start an Agent (30 seconds)
```bash
# Pick the right agent for your node's RAM:
# 16GB nodes (node-1, node-4, node-5): kimi, minimax, pi, glm
# 64GB nodes (node-2): opencode, kilo, kimi, glm
# 48GB nodes (node-3): kimi, minimax, worktree agents

# Start in autonomous mode (polls queue, no tmux dependency):
tmux new-session -d -s forge
tmux new-window -t forge -n agent1
tmux send-keys -t forge:agent1 "FORGE_AGENT_NAME=kimi forge work --daemon --interval 15s" Enter
```

## That's It

The agent will:
- Poll node-1:8081 for tasks every 15 seconds
- Claim, execute, and complete tasks autonomously
- Send heartbeats so node-1 knows it's alive

## Node Inventory

| Node | RAM | Location | Best Agents | Max Agents |
|------|-----|----------|-------------|------------|
| node-1 | 16GB | VPS (always on) | kimi, minimax, pi | 2-3 |
| node-2 | 64GB | VPS (always on) | opencode, kilo, kimi, glm | 5-6 |
| node-3 | 48GB | MacBook (primary) | kimi, minimax, worktrees | 3-4 |
| node-4 | 16GB | MacBook 2018 | kimi, pi | 1-2 |
| node-5 | 16GB | M1 MacBook | kimi, minimax | 2-3 |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Can't reach node-1 | `tailscale status` — check connection |
| CLI not found | `cd ~/work/FORGE/cmd/forge && go build -o forge . && sudo cp forge /usr/local/bin/` |
| Agent not claiming tasks | Check `FORGE_API_URL=http://node-1:8081` is set |
| Node not showing in `forge node list` | Run `forge node join` |
