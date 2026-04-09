# New Agent Quick Start

> Read this first. Everything else is detail. You can be productive in 5 minutes.

## 0. First Time on This Node? (Run Once)

```bash
git pull && forge init
```

`forge init` writes:

- **`~/.forge/forge.toml`** — v3 **daemon** config (ADR-030): port, `db_path`, `node_id`, etc.
- **`~/.forge/config.toml`** — CLI-oriented **`[control_plane] url`** (and node id) so `forge status` and other commands resolve the hub.

The **forge** binary resolves the control plane as: **`FORGE_API_URL`** env → **`~/.forge/config.toml`** → repo **`.forge/config.toml`** → **`.forge/forge.yaml`** (see `cmd/forge/internal/endpoint.go`). It does **not** read `forge.toml` for that URL.

**Architecture:** prya is the default v3 control-plane hub. All nodes connect via Tailscale. Local daemons are optional and mainly for prya or explicit fallback work.

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
| hostname (`prya`, `sati`, `nova`…) | Lead Orchestrator |
| agent name (`kimi`, `glm`, `gemini`, `pi`, `minimax`…) | Fleet Agent |
| (no tmux window — subprocess) | Worktree Agent |

---

## 2. Find Your Task

```bash
ls .forge/dispatches/          # list all dispatches
cat .forge/dispatches/YOURNAME-TASKID-DATE.md    # read yours
```

Your dispatch file contains: task description, acceptance criteria, output location.

---

## 3. If You're a Worktree Agent

Worktree agents are spawned by the orchestrator via the Task tool with `isolation: "worktree"`. You work in an isolated git worktree copy of the repo.

**Key differences from fleet agents:**
- YES, you CAN commit (to your assigned branch only, e.g., `feat/TASK-X`)
- YES, you CAN push your branch
- You work in a separate directory (your worktree path)
- Your scope ends when acceptance criteria pass
- DO NOT commit to `main` — only your assigned branch

**How to know you're a worktree agent:**
- No tmux window — you're spawned as a subprocess
- Working directory is under `.claude/worktrees/` or similar
- Your prompt includes specific files and a test command

---

## 4. Before You Code (High-Risk Work)

For architecture-affecting, cross-domain, auth/billing/data-flow, or new-pattern work:

1. Check `docs/PATTERNS.md` — does a solution already exist?
2. Check `docs/COMMON_MISTAKES.md` — are you about to repeat a known failure?
3. Complete `.forge/dispatch-templates/design-review.md` and append answers to domain `decisions.md`

Skip this for bug fixes, content, small UI tweaks, test additions, or docs updates.

## 5. Execute

Do the work in the dispatch. That's it. No planning docs, no research summaries unless asked.

---

## 6. Write Your Result

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

## 7. Stop

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

## Troubleshooting

### Git Lock Contention (GIT_INDEX_FILE error)

On nodes with sandbox restrictions (vega, gaea), `git add`/`commit` may fail with:
```
fatal: unable to write new index file
```

Use the workaround:
```bash
# Save index
cp .git/index /tmp/forge-git-index-N

# Use temp index for operation
GIT_INDEX_FILE=/tmp/forge-git-index-N git add <files>

# Restore
cp /tmp/forge-git-index-N .git/index
```

### Daemon Connectivity Failures

If `forge status` shows daemon as DOWN or agents can't connect:
```bash
# Check daemon health directly
curl -sf http://prya:8081/health

# If DOWN on prya, restart
forge daemon restart

# If remote node, check network (Tailscale)
curl -sf http://{node}:8081/health
```

### Dispatch Failures

**"Agent not found"** — agent not connected or wrong name:
```bash
forge agent list   # see all connected agents
```

**"File not found" on dispatch** — the dispatch file doesn't exist. Verify:
```bash
ls .forge/dispatches/   # list available dispatches
```

**Task dispatched but agent not responding** — check agent is alive:
```bash
forge agent ping {agent-name}
```

### Common Git Errors

**"nothing to commit" after edit** — file wasn't staged. Run `git add` first.

**"Your branch is up to date" after commit** — this is normal. Commits go to your feature branch, not main.

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
|------|--------|---|
| Code change or test run | **Task tool** | Blocks until done, 100% reliable |
| Queue routine fleet work | **`forge task create ...`** | Primary path when agent identity does not matter |
| Message to fleet agent (this node) | **`forge dispatch send AGENT "msg"`** | Async, tmux notification |
| Cross-node (XNode / hub) | **`forge lead send --to-node NODE --summary "..." [--task-id ID] [--durable]`** | Native Go → daemon `/api/xnode/forward` (preferred) |
| Cross-node (legacy git outbox) | **`forge message send --to NODE --type task --subject "..." --body "..."`** | Deprecated in CLI — use **`lead`** for new work; still git-tracked JSONL |
| Read agent screen | `tmux capture-pane -t forge:AGENT -p` | Read-only, never dispatch |

---

## Lead Orchestrator Only

### Daemon health

```bash
# Check configured control plane + local daemon state
forge status

# Explicit hub check
curl -sf http://prya:8081/health

# Start local daemon only when you actually need local mode
forge daemon start

# See live node mesh
forge node list
```

### Dispatch tasks to fleet agents

```bash
# Write dispatch file first:
# .forge/dispatches/AGENT-TASKID-DATE.md

# Then send to an agent on this node:
forge dispatch send kimi "Read .forge/dispatches/AGENT-TASK-DATE.md — EXECUTE now"
# Or with file:
forge dispatch send kimi --file .forge/dispatches/AGENT-TASK-DATE.md

# Cross-node (preferred — XNode via daemon):
forge lead send --to-node nova --summary "See .forge/dispatches/..." --task-id TASK-123 --durable

# Legacy git outbox (deprecated CLI path — still in tree for older playbooks):
# forge message send --to nova --type task --subject "Sprint work" --body "See .forge/dispatches/..."
```

`fleet`, `lead`, and `message` are **hidden** from default `forge --help`; run **`forge advanced`** to see names. See `forge-shared/modules/dispatch-decision.md` for the full decision tree.

---

## Worktree-First Pattern (ADR-046)

**Council S163+S164:** Worktree isolation is the **hard ban** for all code changes. This pattern is enforced in CLAUDE.md and orchestrator rules.

### When to Use Worktree Agents vs Direct Edit

| Scenario | Method | Why |
|----------|--------|-----|
| Code changes, tests, refactors | **Worktree agent** (Task tool) | Isolated branch, 100% reliable, parallel-safe |
| Documentation, config tweaks | Direct edit (orchestrator) | Fast, low risk, no branch overhead |
| Research, analysis, audits | **Fleet dispatch** | No code changes, read-only work |
| Hotfix on main (emergency) | Direct edit with `[direct-edit]` tag | Council TC-S158 escape hatch |

**HARD BAN:** `forge dispatch send` MUST NEVER be used for code changes. Fleet agents cannot commit. Code changes go through worktree-isolated agents only.

### How the Agent Tool's `isolation: "worktree"` Works

When the orchestrator spawns an agent via the Task tool with `isolation: "worktree"`:

1. **Git worktree created:** A new worktree is created at `.claude/worktrees/{branch-name}/`
2. **Branch auto-created:** Named `worktree-agent-{id}` or `feat/{task-id}`
3. **Agent works in isolation:** All file operations happen in the worktree directory
4. **Commits allowed:** The agent CAN commit and push to its assigned branch
5. **Zero conflicts:** Multiple agents work in parallel without file contention

Example orchestrator command:
```bash
# Task tool spawns worktree agent
Agent tool with isolation: "worktree", branch: "worktree-agent-123"
```

### Git Branch Naming Convention

| Pattern | Use Case |
|---------|----------|
| `worktree-agent-{id}` | General worktree agent work |
| `feat/{task-id}` | Feature work tied to specific task |
| `fix/{bug-desc}` | Bug fix branches |
| `hotfix/{desc}` | Emergency fixes (direct-edit escape hatch) |

### How to Merge Worktree Changes Back to Main

**Step 1: Verify work is complete**
```bash
git status  # in worktree directory — should be clean
```

**Step 2: Push branch (if not already pushed)**
```bash
git push origin worktree-agent-{id}
```

**Step 3: Merge to main (orchestrator only)**
```bash
# Switch to main repo (not worktree)
cd /path/to/main/repo

# Fetch and merge
git fetch origin
git merge origin/worktree-agent-{id} --no-ff -m "Merge worktree-agent-{id}: {description}"

# Or use squash for clean history
git merge --squash origin/worktree-agent-{id}
git commit -m "{task-id}: {description}"
```

**Step 4: Clean up worktree**
```bash
# Remove worktree after merge
git worktree remove .claude/worktrees/worktree-agent-{id}
git branch -d worktree-agent-{id}  # delete local branch
git push origin --delete worktree-agent-{id}  # delete remote branch
```

### The Direct-Edit Escape Hatch (Council TC-S158)

If worktree creation fails AND no other node is available, the orchestrator MAY edit source directly on main.

**Requirements:**
1. Tag commit message with `[direct-edit]` for review
2. Document reason in commit body (e.g., "worktree creation failed, node unavailable")
3. Use only for urgent fixes, not routine work

**Disk threshold check before worktree creation:**
```bash
# Verify >3GB free before launching worktree agents
df -h /
# If below threshold, use direct edit or route to another node
```

### Worktree Best Practices

1. **Scope is self-contained:** Each worktree agent gets a clear, bounded task
2. **No cross-worktree dependencies:** Agents don't wait on each other
3. **Commit early, commit often:** Push progress to remote branch
4. **Zero merge conflicts:** Proper scoping produces zero conflicts (verified: 4 agents, 1,278 files, 0 conflicts)

---

## Autonomous Work Loop (Fleet Agents)

Once node is set up, start claiming tasks automatically:

```bash
FORGE_AGENT_TYPE=fleet FORGE_AGENT_NAME=kimi forge work --daemon --interval 15s
```

The `--daemon` flag loops: claim → execute → write result → repeat.
Result files land in `.forge/heartbeat/results/AGENTNAME-TASKID.md`.
Dark Factory (F2 patrol) auto-completes tasks when results appear.

---

## Validate Your Environment

```bash
forge status                        # daemon + fleet health
forge doctor                        # comprehensive health check (primary)
```
