# SATI Node Bootstrap Runbook

**Objective:** Bootstrap `sati` as a FORGE agent workhorse node (64GB RAM / 32-thread Linux).

**Target Node:** sati  
**Node Role:** Secondary agent workhorse — runs heavy agents (OpenCode, Kilo, GLM)  
**Lead Node:** prya (forged daemon)

---

## Prerequisites

- SSH access to sati
- FORGE repo cloned at `~/work/FORGE`
- uv installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- `~/.forgerc` configured (see below)

### ~/.forgerc Template

Create `~/.forgerc` on sati:

```bash
# FORGE CLI node defaults
FORGE_API_URL=http://prya.queue-great.ts.net:8081
FORGE_WEBHOOK_TOKEN=<TOKEN_FROM_PRYA>
FORGE_NODE_ID=sati
```

Get the token from prya: `cat ~/.forgerc | grep FORGE_WEBHOOK_TOKEN`

---

## Step 1: Install Agent CLIs

Install the CLIs for each agent type sati will run:

```bash
# Claude Code (npm)
npm install -g @anthropic-ai/claude-code

# Kimi CLI (pip/uv)
pip install kimi-cli
# OR: uv pip install kimi-cli

# Codex CLI (npm — included with Claude Code or separate)
npm install -g @openai/codex

# Gemini CLI (npm)
npm install -g @google/gemini-cli

# OpenCode (heavy agent — requires 32GB+ RAM)
# Download from https://github.com/opencode-ai/opencode/releases
curl -L https://github.com/opencode-ai/opencode/releases/latest/download/opencode-linux-x64 -o ~/.local/bin/opencode
chmod +x ~/.local/bin/opencode

# Kilo CLI (npm)
npm install -g kilo-cli
```

Verify installations:
```bash
which claude kimi codex gemini opencode kilo
claude --version
kimi --version
codex --version
gemini --version
opencode --version
kilo --version
```

---

## Step 2: Sync FORGE Repository

```bash
cd ~/work/FORGE
git pull origin main
cd harness && uv sync
```

---

## Step 3: Install Git Hooks

```bash
cd ~/work/FORGE
.forge/scripts/install-hooks.sh
```

This installs:
- Pre-commit hook for quality gates
- Claude Code hooks (context guard, heartbeat eval)

---

## Step 4: Start Node Services

With `~/.forgerc` configured, start services with zero arguments:

```bash
cd ~/work/FORGE
.forge/scripts/node-startup.sh
```

This creates a `forge-monitor` tmux session with:
- **xnode** window: XNode listener (SSE to prya forged)
- **heartbeat** window: Node telemetry publisher (30s interval)
- **status** window: Local status display

Verify services:
```bash
.forge/scripts/node-startup.sh --check
```

Expected output:
```
[OK]   XNode Listener running
[OK]   forge-monitor session (3 windows)
```

---

## Step 5: Create Fleet Agent Session

Create the `forge` tmux session with agent windows:

```bash
cd ~/work/FORGE

# Create session with sati-lead window
tmux new-session -d -s forge -n sati-lead -c ~/work/FORGE
tmux set-option -t forge remain-on-exit on

# Create agent windows
for agent in kimi claude minimax glm gemini opencode kilo-max kilo-glm; do
    tmux new-window -t forge -n "$agent" -c ~/work/FORGE
done

# Select lead window
tmux select-window -t forge:sati-lead
```

Verify:
```bash
tmux list-windows -t forge
```

Expected: `sati-lead`, `kimi`, `claude`, `minimax`, `glm`, `gemini`, `opencode`, `kilo-max`, `kilo-glm`

---

## Step 6: Start Agents

Launch CLIs in each agent window. From **sati-lead**:

```bash
# Example: Start kimi agent
tmux send-keys -t forge:kimi "kimi" Enter

# Example: Start claude agent  
tmux send-keys -t forge:claude "claude" Enter

# Example: Start opencode agent
tmux send-keys -t forge:opencode "opencode" Enter
```

Or use the preferred dispatch method from prya:
```bash
# From prya:
forge dispatch send forge:kimi "Task: dispatch-kimi-backlog.md"
```

---

## Step 7: Verify Node Registration

From **prya** (lead node), verify sati appears in fleet:

```bash
forge nodes list --offline | grep sati
```

Or check the heartbeat file:
```bash
cat .forge/heartbeat/nodes/sati.json
```

Expected capabilities: `claude`, `kimi`, `docker`, `opencode`, `kilo` (if >=32GB RAM)

---

## Step 8: Test Cross-Node Dispatch

Send a test message from prya to sati:

```bash
forge lead send \
  --to-node sati \
  --task-id SATI-TEST-001 \
  --summary "Test cross-node dispatch to sati" \
  --strict
```

On sati, check inbox:
```bash
tail -5 .forge/xnode/lead-inbox/prya.jsonl
```

---

## Quick Reference

| Command | Purpose |
|---------|---------|
| `tmux attach -t forge` | Agent fleet session |
| `tmux attach -t forge-monitor` | Infrastructure monitoring |
| `.forge/scripts/node-startup.sh --check` | Service status |
| `.forge/scripts/node-startup.sh --stop` | Stop services |
| `forge nodes heartbeat` | Manual heartbeat publish |

---

## Troubleshooting

### XNode Listener Won't Start
```bash
# Check logs
tail -f /tmp/forge-xnode-sati.log

# Verify forged connectivity
curl http://prya.queue-great.ts.net:8081/health
```

### Agent CLI Not Found
```bash
# Check PATH
echo $PATH
# Should include: ~/.local/bin, ~/.npm-global/bin, ~/.opencode/bin

# Add to ~/.bashrc if missing:
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/.opencode/bin:$PATH"
```

### Git Lock Issues
```bash
# Remove stale lock
rm -f ~/work/FORGE/.git/index.lock
```

### Capabilities Not Detected
Heavy agents (opencode, kilo) only advertised on nodes with >=32GB RAM. Verify:
```bash
python3 -c "import os; print((os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')) / (1024**2))"
```

---

## Post-Bootstrap Checklist

- [ ] sati shows in `forge nodes list --offline`
- [ ] Capabilities include: claude, kimi, docker, opencode, kilo
- [ ] XNode listener connected (check `forge-monitor:xnode` window)
- [ ] Heartbeat publishing (check `forge-monitor:heartbeat` window)
- [ ] Agent windows created in `forge` session
- [ ] Test dispatch from prya received on sati

---

*Last updated: 2026-02-27*  
*For issues: Check AGENTS.md or run `forge doctor`*
