# New Agent Quick Start

> Read this first. Everything else is detail. You can be productive in 5 minutes.

## 0. First Time on This Node? (Run Once)

```bash
git pull && bash bin/node-migrate-v3
```

Then add to `~/.bashrc` / `~/.zshrc` (script prints the exact lines):

```bash
export FORGE_ROOT=$HOME/FORGE
export FORGE_API_URL=http://node-1:8081
export PATH="$HOME/FORGE/cmd/forge:$PATH"
```

**Architecture:** node-1 is the default v3 control-plane hub. All nodes connect via Tailscale. Local daemons are optional and mainly for node-1 or explicit fallback work.

---

## 1. Who Are You?

```bash
echo $FORGE_AGENT_TYPE    # "fleet" → you are a fleet agent
echo $FORGE_AGENT_NAME    # your name: kimi, gemini, minimax, pi, glm, etc.
```

Your orchestrator sets `FORGE_AGENT_TYPE` when spawning you. If unset, check your tmux window:

```bash
tmux display-message -p '#W'
```

| Window name | Your role |
|---|---|
| hostname (`node-1`, `node-2`, `node-3`…) | Lead Orchestrator |
| agent name (`kimi`, `glm`, `gemini`, `pi`, `minimax`…) | Fleet Agent |
| spawned by Task tool (no tmux) | Worktree Agent |

---

## 2. Find Your Task

```bash
ls .forge/dispatches/          # list all dispatches
cat .forge/dispatches/YOURNAME-TASKID-DATE.md    # read yours
```

Your dispatch file contains: task description, acceptance criteria, output location.

---

## 3. Execute

Do the work in the dispatch. That's it. No planning docs, no research summaries unless asked.

---

## 4. Write Your Result

```bash
# Write to:
.forge/heartbeat/results/YOURNAME-TASKID.md

# Examples of valid filenames:
# kimi-ADR-028-FSM.md
# minimax-V4-QUEUE-PRUNE.md
# gemini-DARK-FACTORY-AUDIT.md
```

| Section | Required? | Example |
|---|---|---|
| `## Status` | **YES** | `COMPLETE` / `BLOCKED` / `FAILED` |
| `## Deliverables` | YES (if COMPLETE) | `- [x] file.go created` |
| `## Evidence` | YES (if COMPLETE) | `- Build: go build ./... OK` |
| `## Blockers` | YES (if BLOCKED/FAILED) | `- API endpoint missing: POST /api/x` |

```markdown
## Status: COMPLETE

## Deliverables
- [x] path/to/file.go — created/modified
- [x] go build ./... passes

## Evidence
- File: path/to/file.go (42 lines)
- Test: go test ./... PASS
```

---

## 5. Stop

**DO NOT commit. DO NOT push. DO NOT dispatch to other agents.**

Lead orchestrator commits all work after review.

---

## Environment Notes

| Tool | Use | NOT this |
|---|---|---|
| Read | Read files | cat |
| Grep | Search code | grep -E (broken alias) |
| Glob | Find files | find |
| Edit/Write | Change files | sed/awk |
| Bash | git, go build/test, npm, uv | general shell |

---

## Commit Rules

| Who you are | Commit? |
|---|---|
| Fleet agent | **NEVER** |
| Worktree agent (Task tool) | OK on assigned branch |
| Lead orchestrator | YES, to main |

---

## Context Rules

| Context % | Action |
|---|---|
| > 50% | Run `/handoff-clean` |
| > 75% | **HARD STOP** — run `/handoff` immediately |

---

## When to Use Each Dispatch Method

| Goal | Method | Why |
|------|--------|-----|
| Code change or test run | **Task tool** | Blocks until done, 100% reliable |
| Message to fleet agent (this node) | **`forge dispatch send forge:AGENT "msg"`** | Async, 95%+ delivery |
| Cross-node (node-2/node-3/node-5) | **`harness lead send --to-node NODE`** | Durable — survives reboot |
| Read agent screen | `tmux capture-pane -t forge:AGENT -p` | Read-only, never dispatch |

---

## Lead Orchestrator Only

### Daemon health

```bash
# Check configured control plane + local daemon state
forge status

# Explicit hub check
curl -sf http://node-1:8081/health

# Start local daemon only when you actually need local mode
forge daemon start

# See live node mesh
forge node list
```

### Dispatch tasks to fleet agents

```bash
# Write dispatch file first:
# .forge/dispatches/AGENT-TASKID-DATE.md

# Then send:
forge dispatch send forge:AGENT "Read .forge/dispatches/AGENT-TASK-DATE.md — EXECUTE now"

# Cross-node:
harness lead send --to-node NODE --task-id ID --summary "msg" --durable
```

See `forge-shared/modules/dispatch-decision.md` for full decision tree.

---

## Autonomous Work Loop (Fleet Agents)

Once node is set up, start claiming tasks automatically:

```bash
FORGE_AGENT_TYPE=fleet FORGE_AGENT_NAME=kimi forge work --daemon
```

The `--daemon` flag loops: claim → execute → write result → repeat.
Result files land in `.forge/heartbeat/results/AGENTNAME-TASKID.md`.
Dark Factory (F2 patrol) auto-completes tasks when results appear.

---

## Validate Your Environment

```bash
forge status
bash .forge/scripts/check-docs.sh   # verify CLI docs match binary
```
