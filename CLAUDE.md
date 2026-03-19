# FORGE Portfolio Agent Instructions

Multi-domain MVP portfolio with **95 projects** across **11 domains**.

## Agent Quick-Start (Read This First)

> **New agent?** Read `docs/AGENT_QUICK_START.md` first — 5 minutes to productive. Then return here for your role details.

**Step 1: Detect your role.** Run `echo $FORGE_AGENT_TYPE` in your shell. The result tells you who you are:

| `$FORGE_AGENT_TYPE` | You are | Follow section |
|---------------------|---------|----------------|
| `fleet` | A Fleet Agent | **Fleet Agent** below |
| `orchestrator` | The Lead Orchestrator | **Lead Orchestrator** below |
| *(empty/unset)* | Run `tmux display-message -p '#W'` | If output = hostname → Orchestrator; if output = agent name (glm/kimi/etc) → Fleet Agent; else → Worktree Agent |

**Step 2: Follow your role section below.**

---

### I am a Fleet Agent (`$FORGE_AGENT_TYPE=fleet`)

**You are: `$FORGE_AGENT_NAME`** (minimax / glm / kimi / gemini / pi / kilo / opencode / open-max)

**Agent capability table** — lead consults this to pick the right agent for a task:

| Agent pattern | Best for | Avoid |
|---------------|----------|-------|
| `claude` | Primary implementation: features, refactors, hard bugs, production code | — |
| `gemini*` | Research, audits, architecture, planning | Long-running code tasks |
| `minimax*` | Implementation, docs, runbooks, multi-file features | go-test |
| `glm*` | Implementation, scaffolding, refactors | go-test, ios-builds |
| `opencode*` | Implementation, multi-file refactors | — |
| `kilo*` | Implementation, multi-file features, docs | — |
| `kimi*` | Coverage, triage, rapid fix loops | Large refactors |
| `pi*` | Fast triage, quick edits, analysis | Heavy code tasks |
| `cursor*` | Human-steered interactive editing | Autonomous tasks |

**Hotswap rule:** if primary agent is offline, pick the next agent in the same row's pattern (e.g. `kimi` → `kimi-2`).

**You are NOT the orchestrator.** You do NOT commit, push, create handoffs, or dispatch to other agents.

**Find your task:** `ls .forge/dispatches/`
**Dispatch pattern:** `.forge/dispatches/AGENT-TASKID-DATE.md`
**Results pattern:** `.forge/heartbeat/results/AGENTNAME-TASKID.md`

1. Read your dispatch file
2. Execute the task — no research, no planning docs, no handoffs
3. **ONLY modify files listed in your dispatch.** If you need to modify other files, STOP and write to your results file explaining why.
4. **NEVER modify source code** (`.py`, `.go`, `.tsx`, `.ts`) unless your dispatch explicitly says to. Fleet agents do research, content, and analysis. Code changes go through worktree agents.
5. Write results to `.forge/heartbeat/results/`
6. Your scope ends when results are written to the results file
7. **DO NOT COMMIT** — lead commits all work
8. **DO NOT PUSH**
9. **DO NOT create handoff documents** — orchestrator-only
10. **DO NOT dispatch to other agents** — orchestrator-only
11. **DO NOT run these lead-only operations:** `forge dispatch send` (dispatching to others), `git commit`, `git push`, `forge approval decide`, `forge lane promote` — fleet agents never commit, push, approve, or dispatch

**Your tools:**
- `Read` — read files (NOT Bash cat)
- `Grep` — search code (NOT Bash grep)
- `Glob` — find files by pattern (NOT Bash find)
- `Edit` / `Write` — modify files
- `Bash` — only for: `git`, `go build`/`go test`, `npm`, `uv` commands

**Dispatch protocol:** See `forge-shared/modules/dispatch-decision.md`
**Domain context:** See `forge-shared/modules/royal-jelly.md`

**On every handoff or session end**, update your domain's context:
```bash
# Your domain's persistent context lives here:
.forge/context/{domain-short}/lead-context.md   # Current state — ALWAYS update
.forge/context/{domain-short}/decisions.md       # Append architectural decisions
.forge/context/{domain-short}/failures.md        # Append what didn't work and why
```

**On session start**, read your domain's context:
```bash
cat .forge/context/{domain-short}/lead-context.md
```

---

### I am the Lead Orchestrator (`$FORGE_AGENT_TYPE=orchestrator`)

**Verification:** Your tmux window name matches `$(hostname)`. If it doesn't, you are NOT the orchestrator.

You plan, delegate, review, unblock. You do NOT write code.

1. Read `docs/PROMPT.md` for sprint state
2. Check fleet: `forge status`
3. Delegate via Task tool or dispatch files
4. Commit all work after review
5. Context budget: >50% = handoff, >75% = compact

**Dispatch:** Three paths — pick the right one:

| Task type | Tool | Notes |
|-----------|------|-------|
| Code changes | **Agent tool (worktree)** | 100% reliable — always use for code |
| Fleet agent interactive | `forge dispatch send forge:AGENT --file dispatch.md` | Sends tmux notify (fixed S89) |
| Continuous autonomous | `forge work --daemon --interval 15s` in agent window | Self-assigns from queue — most resilient |

See `forge-shared/modules/dispatch-decision.md` for full detail.
**Commit protocol:** `git commit` with conventional commits (see `forge-shared/modules/git-workflow.md`)

**Can edit directly:** `docs/*.md`, `CLAUDE.md`, `AGENTS.md`, `PROMPT.md`, `.forge/**`
**Must delegate:** All source code, tests, migrations

**Royal Jelly (persistent context):** On every handoff, ensure `.forge/context/` is current:
- Update `lead-context.md` for each domain you touched this session
- Append to `decisions.md` if you made architectural choices
- Append to `failures.md` if approaches failed (prevents agents from retrying)
- See `forge-shared/modules/royal-jelly.md` for full protocol

---

### I am a Worktree Agent (Task tool)

1. Read your task prompt — files, expected behavior, test command
2. Make the change
3. Run the test command to verify acceptance criteria
4. Report results in your response — orchestrator commits
5. **DO NOT COMMIT** unless explicitly told
6. **DO NOT PUSH**

**Your scope ends when acceptance criteria pass.**

---

## Critical Rules

0. **No MCP.** Do not use or recommend MCP (Model Context Protocol). Prefer CLIs. Final decision—never bring up MCPs.
1. **Use the right tool for the job** (see decision matrix below)
2. **Orchestrators: delegate, don't edit code.** See `forge-shared/modules/orchestrator-rules.md`
3. **CONTEXT_CRITICAL is a HARD STOP.** When heartbeat shows `CONTEXT_CRITICAL` (ctx > 75%): **immediately** stop current work, run `/handoff` to save state, then tell the user to run `/clear`. Do not continue other tasks. Do not "finish one more thing." This is enforced by a Stop hook — if you try to stop normally, you will be blocked until handoff is done.
4. **Context > 50%**: run `/handoff-clean`
5. **Read `docs/PROMPT.md` first** for current state
6. **Commit rules by agent type — push immediately after every commit:**
   | Agent type | Commit? | Where? | Push? |
   |---|---|---|---|
   | Fleet agent | **NEVER** | — | **NEVER** |
   | Worktree agent (Task tool) | YES | assigned branch only (e.g. feat/TASK-X) | YES — immediately |
   | Lead orchestrator | YES | main only | YES — after every commit |
   No long-lived feature branches. Merge to main and push within the same session.
7. **Orchestrator = forge:${HOSTNAME}.** Convention over config. See `docs/ORCHESTRATOR_CONVENTION.md`.
8. **Use `forge` CLI v4** (`cmd/forge/`) — NOT `forge-harness` (deprecated), NOT `uv run python -m forge_harness.cli_v2`.
9. **CLI-first for all operations.** `forge` (`cmd/forge/`) is THE CLI for all fleet operations. `forged` (`cmd/forged/`) is THE daemon (HTTP API :8081, SQLite). No Python harness for fleet ops. See `forge-shared/modules/dispatch-decision.md` for dispatch.
10. **Long-running processes in forge-monitor.** Start background services (relay, xnode, forged daemon) in `forge-monitor` tmux session — never bare `nohup` or `&`. **CRITICAL: No agents should run in the forge-monitor session; it is for monitoring only.**
11. **Error messages must include recovery steps.** Every CLI error should tell the user what went wrong AND how to fix it.
12. **`grep -E` is broken** (aliased to rg in this environment). Use the Grep tool, `rg`, or plain `grep` instead.
13. **`.forge/dispatches/` is gitignored** — local only. Fleet agents read dispatch files but must NOT edit or commit them.
14. **Test file convention (Council S118).** When writing tests for `cmd/forged/`, ALWAYS check if a canonical test file for that module already exists (e.g., `patrol_test.go`, `task_queue_test.go`). **Extend the existing file** — never create a new `coverage_wave*_test.go`. See `cmd/forged/TEST_MAP.md` for the canonical test file map and coverage skip-list.

## The Two Canonical Binaries

| Binary | Source | Role |
|--------|--------|------|
| `forge` | `cmd/forge/` | CLI — all fleet operations |
| `forged` | `cmd/forged/` | Daemon — HTTP API :8081, SQLite |

Everything else is either deleted or iOS/portfolio-specific Python harness.
Key commands: `forge up`, `forge down`, `forge monitor`, `forge daemon restart`

### Tool Selection

| Need | Use |
|------|-----|
| Architecture / "what does X do" | `qmd search "query"` (BM25, .md only) |
| Find docs / guides | `qmd search "topic" --files` |
| Search files by name | `Glob` pattern |
| Search code | `Grep` regex |
| Read files | `Read` tool |
| Dispatch | `forge dispatch send` — see `.claude/rules/fleet-dispatch.md` |

> **Full tool selection, dispatch rules, tmux patterns, and agent start commands:** See `.claude/rules/fleet-dispatch.md`

## Current Focus (March 2026)

- **Sprint**: See `docs/PLAN.md` + `docs/PROMPT.md` for live state
- **Infrastructure**: CLI v4 Go (`cmd/forge/`) + forged daemon (`cmd/forged/`) + iOS harness only
- **Coverage**: forged 83.4% (structural ceiling), 4000+ tests

**Reading order:** See `docs/INFRASTRUCTURE_MAP.md` for progressive disclosure (Tier 1 → Tier 3).

## Infrastructure Discovery

> **Full map:** `docs/INFRASTRUCTURE_MAP.md` — progressive disclosure (Tier 1 → Tier 3)

| Need | Where |
|------|-------|
| Current state | `docs/PROMPT.md` |
| Fleet dispatch | `forge-shared/modules/dispatch-decision.md` |
| Git & code quality | `forge-shared/modules/git-workflow.md` + `code-quality.md` |
| iOS | `harness/CLAUDE.md` + `/ios-agent` skill |
| Skills | `.claude/skills/README.md` |
| Path-scoped rules | `.claude/rules/` (go-development, fleet-dispatch, git-conventions, iOS) |
| Domain context | `.forge/context/{domain}/lead-context.md` |
| ADR decisions | `docs/adr/INDEX.md` |

## Session Hygiene

When context > 50% or switching tasks:
1. Capture to `docs/PROMPT.md` or `/handoff-clean`
2. Include: decisions, patterns, blockers, next steps
3. **Update Royal Jelly**: `.forge/context/{domain}/lead-context.md` for every domain you touched

## Unified State Directory

`.forge/` consolidates all persistent state (memories, heartbeat, research, config).

## Content Workflow

```bash
/content generate <domain> <project> --type blog_posts
/content batch <domain> --count 10
```

### Notion Integration
```bash
export NOTION_API_TOKEN="ntn_xxx"
export NOTION_DATA_SOURCE_ID="14636e9f-74a5-8151-8ddd-000bd1c0cc0a"
export NOTION_LINKED_DATABASE_ID="14636e9f-74a5-8175-a3ff-f0993a288e9a"
```

## Forge CLI Quick Reference

**CLI:** `forge` (Go, `cmd/forge/`). Run `forge --help` for full command list.

| Essential Commands | Purpose |
|-------------------|---------|
| `forge status` | System health |
| `forge task list/create/complete` | Task queue |
| `forge dispatch send forge:AGENT` | Dispatch to fleet agent |
| `forge fleet windows` | Live tmux agent windows |
| `forge agent list` | Connected agents |
| `forge daemon start/stop/restart` | Daemon control |
| `forge patrol list` | Background patrols |

**iOS only:** `cd harness && uv run python -m forge_harness.cli_v2 ios <cmd>`

> **Full CLI reference + daemon + state locations:** See `docs/INFRASTRUCTURE_MAP.md`

### Node Resource Budget (CRITICAL — OOM risk on node-1)

| Node | RAM | Max Agents | Allowed Models |
|------|-----|------------|----------------|
| **node-1** | 16 GB | 2 max | Claude Code (minimax/glm), Kimi — NO OpenCode/Kilo |
| **node-2** | 64 GB | 5-6 | OpenCode, Kilo, Kimi, GLM — heavy workloads |
| **node-3** | 48 GB | 3-4 | Worktree agents, iOS builds |
| **node-4** | 16 GB | 1-2 | Auxiliary only (no iOS — Ventura too old) |
| **node-5** | 16 GB | 2-3 | M1 Pro laptop, off-hours only |

**NEVER spawn OpenCode or Kilo on node-1 — causes OOM at 93% RAM.**

**Node setup (new node / migration):** `git pull && bash bin/node-migrate-v3`
All nodes point to `FORGE_API_URL=http://node-1:8081` — no local daemon needed (ADR-025 deferred).
See `docs/NODE_SETUP.md` for full setup guide.

