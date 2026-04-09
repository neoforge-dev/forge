# Cross-Agent Patterns: Multi-Agent Agentic Coding Workflows

Research findings and recommendations for making FORGE's multi-agent fleet work predictably across Claude Code, Gemini CLI, Kimi, Cursor, Aider, and OpenCode. Covers skill portability, inter-agent dispatch, QMD integration, compounding loops, and progressive disclosure.

**Status:** Living document  
**Last updated:** 2026-02-05  
**Sources:** CLAUDE.md, AGENTS.md, harness scripts, `.claude/skills/dispatch`, `forge_harness/parallel_dispatch.py`, workflow-analysis-tech.md, QMD docs, flywheel/feedback_loops.

---

## 1. Current State Analysis

### 1.1 Files Reviewed

| File | Purpose | Findings |
|------|---------|----------|
| **CLAUDE.md** | Portfolio entry, orchestrator rules | Uses skills (`/dispatch`, `/ship-feature`), Infrastructure Discovery table, no raw tmux. Imports modules (orchestrator-rules, fleet-dispatch, tech-stack). |
| **AGENTS.md** | Universal agent entry | Progressive disclosure table (Level 0–3 + Skills), 5 essential commands, fleet architecture (forge / forge-{domain}), agent types (Gemini, Codex, Pi, Kimi, OpenCode). |
| **.claude/skills/dispatch/SKILL.md** | Dispatch skill | Documents `tmux-send-to-agent.sh` and fleet-dispatch; quota detection, fallback strategy; references `harness/scripts/tmux-send-to-agent.sh`, `lib.sh`, `forge_harness/dispatcher.py`. |
| **harness/scripts/tmux-send-to-agent.sh** | Low-level tmux send | **Exists.** Session:window target, chunked literal send (-l), C-u clear, Enter separate; --wait, --verify, --timeout, --cli, --restart; detects Claude/Codex/Gemini via lib.sh. |
| **harness/scripts/fleet-dispatch.sh** | CLI wrapper | Forwards to `fleet_dispatch.py`; args: --agent, --task, --autonomous, --agent-type, --dir, --no-verify. |
| **harness/scripts/fleet_dispatch.py** | Python dispatch | Validates session:window; literal send + separate Enter; C-c before send (unless --no-cancel); agent-type auto/claude/pi/amp/opencode/kimi/gemini; verification patterns per agent; updates `.forge_fleet/state.json`. |
| **harness/forge_harness/parallel_dispatch.py** | Async parallel dispatch | HRN-001: TaskDefinition/TaskResult, ParallelDispatcher, isolated workspaces, semaphore (max 5), timeout per task; **in-process** agent execution (agent_func), not tmux. |

### 1.2 Gaps Identified

1. **Two dispatch models:** (a) tmux send-to-agent (fleet_dispatch.py, tmux-send-to-agent.sh) for live sessions; (b) parallel_dispatch.py for in-process async tasks. No single "inter-agent dispatch API" that unifies both.
2. **Skill format:** Skills are Markdown (SKILL.md) + optional scripts. Cursor/Aider/OpenCode/Gemini/Kimi each have different slash-command or invocation semantics; no canonical "skill contract" that all agents consume.
3. **QMD:** Documented in workflow-analysis and QMD_QUICK_REFERENCE as primary search gateway; not yet mandated in agent bootstrap or skill preconditions.
4. **Compounding loops:** Implemented in flywheel + feedback_loops + Ralph Loop; documentation is harness-centric. Orchestrators and external agents lack a short "how to contribute to compounding" checklist.
5. **Progressive disclosure:** AGENTS.md and INFRASTRUCTURE_MAP have levels; CLAUDE.md uses an Infrastructure Discovery table. No single "orchestrator discovery path" that says "read X then Y then Z when you need dispatch."

---

## 2. Making Claude Code Skills Work Across Agentic Coders

### 2.1 Agent Capability Matrix

| Agent | Slash / skills | Context | Best invocation |
|-------|----------------|---------|------------------|
| **Claude Code** | Reads .cursorrules, @import | Full project | Skills in AGENTS.md + CLAUDE.md |
| **Gemini CLI** | No slash; natural language | Single session | Copy-paste skill steps or `gemini -y "Do X; see AGENTS.md"` |
| **Kimi** | Custom prompts | Session | Explicit "Read .claude/skills/dispatch/SKILL.md then…" |
| **Cursor** | Rules, Composer | IDE | Rules reference AGENTS.md; Composer gets same docs |
| **Aider** | No built-in skills | Chat | Pre-paste skill text or point to SKILL.md path |
| **OpenCode** | Project-specific | Session | Same as Gemini: doc path + task text |

### 2.2 Skill Portability Patterns

1. **Single source of truth:** Keep skill logic in `.claude/skills/<name>/SKILL.md`. All agents can be instructed to "read SKILL.md for skill X."
2. **Skill frontmatter + one-line description:** Every SKILL.md has `name` and `description` for auto-discovery (Claude/Cursor) and for orchestrators to show "use /dispatch for…."
3. **Command-line equivalent:** Each skill documents a concrete shell command (e.g. `./harness/scripts/tmux-send-to-agent.sh forge:gemini "…" --cli gemini --verify`). Non-Claude agents can run that via their shell tool.
4. **Agent-agnostic task text:** When dispatching, write task text that is self-contained (e.g. "Read docs/X.md. Then do Y. Report result to docs/PROMPT.md.") so any agent can execute without assuming a specific CLI.
5. **Minimal per-agent adapters:** Optional one-pagers per agent (e.g. `docs/onboarding/GEMINI_QUICK.md`) that say "To dispatch: run this script. To run a skill: read .claude/skills/<name>/SKILL.md and follow steps."

### 2.3 Predictability Checklist

- [ ] Every skill has a **When to Use** and **Prerequisites**.
- [ ] Every skill has a **CLI/shell fallback** so non-Claude agents can execute equivalent behavior.
- [ ] Orchestrator rules (orchestrator-rules.md) say "prefer skill name over raw command" and list where to find skills (`.claude/skills/README.md`).
- [ ] New agents get onboarding that includes: read AGENTS.md → read PROMPT.md → use /dispatch or script for delegation.

---

## 3. Wrapper Patterns for Sending Messages Between Agents (tmux)

### 3.1 Review: `harness/scripts/tmux-send-to-agent.sh`

**Exists:** Yes, at `harness/scripts/tmux-send-to-agent.sh`.

**Behavior:**

- **Usage:** `./tmux-send-to-agent.sh <session:window> "<message>" [options]`
- **Options:** `--wait`, `--timeout <sec>`, `--verify`, `--cli <type>`, `--restart`
- **Mechanics:** Validates session/window; optionally detects/restarts CLI (via lib.sh: `detect_llm_cli`, `ensure_cli_running`); C-u to clear line; sends message in chunks (50 chars) with `-l` (literal); separate `Enter`.
- **Verification:** Can wait for pane content to match processing indicators (e.g. "thinking", "Reading").

**Strengths:** Literal send avoids shell parsing; chunking reduces buffer issues; CLI detection and restart improve reliability.

**Gaps:** Path is harness/scripts (not in PATH by default); Python fleet_dispatch.py duplicates logic (no shared "send one message" library); no formal "dispatch API" that returns success/failure and optional verification result.

### 3.2 Recommended Wrapper API for Inter-Agent Dispatch

Provide a **single entry point** that both humans and orchestrators can use, and that hides tmux vs in-process details.

**Option A: Extend fleet-dispatch as the canonical API**

- **CLI:** `./harness/scripts/fleet-dispatch.sh <session:window> "<task>" [--cli TYPE] [--no-verify] [--dir PATH]` (already exists).
- **Python:** Call `fleet_dispatch.dispatch_task(...)` from orchestrator code or from `forge_harness`; ensure it uses the same rules (literal send, separate Enter, optional verify).
- **Behavior:** Keep `tmux-send-to-agent.sh` as the low-level implementation used by fleet_dispatch.py when target is a tmux session (or document that fleet_dispatch.py is the canonical implementation and shell script is legacy/convenience).

**Option B: Unified dispatch API in forge_harness**

- Add `forge_harness.dispatch.send_to_agent(target: str, task: str, *, verify: bool = True, agent_type: str | None = None) -> DispatchResult`.
- Implement `send_to_agent` by:
  - If target looks like `session:window`, call into fleet_dispatch logic (or subprocess to fleet_dispatch.py) to send via tmux.
  - Optionally support `target="parallel"` and route to `parallel_dispatch.ParallelDispatcher` for batch in-process tasks.
- Document: "For sending to a live tmux agent, use `send_to_agent('forge:gemini', '...')`. For parallel in-process tasks, use `ParallelDispatcher`."

**Recommended short term:** Option A — document that **fleet-dispatch.sh / fleet_dispatch.py** is the **recommended wrapper**. All skills and docs point to it; tmux-send-to-agent.sh remains the low-level script that fleet_dispatch.py can call if we refactor Python to shell out for send. Add a one-line "Dispatch API" section to CLAUDE.md and AGENTS.md:

```markdown
## Inter-Agent Dispatch (canonical)
- **CLI:** `./harness/scripts/fleet-dispatch.sh <session:window> "<task>"`  
  Or: `forge fleet dispatch <session:window> "<task>"` if available.
- **Python:** Use `harness/scripts/fleet_dispatch.dispatch_task(...)` with same semantics (literal send, separate Enter, state update).
- **Do not** send raw `tmux send-keys` from orchestrators; use this wrapper for verification and state.
```

---

## 4. QMD (Quality-Driven Markdown Documentation) Integration Patterns

### 4.1 Current QMD Use

- **Tool:** Local semantic search (BM25 + optional embeddings). Commands: `qmd search`, `qmd query`, `qmd get`, `qmd status`; collections: forge-docs, forge-projects, forge-sessions, harness-docs.
- **Docs:** `docs/QMD_QUICK_REFERENCE.md`, `docs/openclaw/QMD_INTEGRATION.md`; workflow-analysis-tech.md recommends "QMD as primary search gateway" and "mandatory doc check in agent bootstrap."

### 4.2 Integration Patterns for Multi-Agent Workflows

1. **Bootstrap (context-loader / session start):** Before starting a task, run:
   - `qmd search "<relevant terms>" -n 5` or `qmd query "<natural language question>"`;
   - Read top results; optionally flag docs older than 30 days.
2. **Skill precondition:** For skills that depend on docs (e.g. living-docs, dispatch), document: "If unsure, run `qmd search 'handoff protocol' -c forge-sessions` first."
3. **Orchestrator discovery:** In progressive disclosure Level 0/1, add: "To find where X is documented: `qmd query 'X'` or see docs/QMD_QUICK_REFERENCE.md."
4. **Abstraction:** Use `forge-search` (or equivalent) when available so agents don't depend on QMD binary name; point to QMD_QUICK_REFERENCE for actual commands.
5. **Staleness:** In living-docs or a dedicated skill, add "QMD + last_updated" check: e.g. list docs that haven't been updated in 30 days and suggest review.

### 4.3 Suggested Addition to AGENTS.md / QUICK_START

- Under "Before starting work" or "Load context": "Run `qmd search '<your topic>' -n 5` (or `qmd query '...'`) to pull relevant docs; read PROMPT.md and any high-ranking results."

---

## 5. Compounding Reinforcement Loops (Each Iteration Improves the Next)

### 5.1 Existing Mechanisms

- **Flywheel** (`forge_harness/flywheel.py`): Scan → generate features → Ralph Loop → feedback loops; sessions index to Code Atlas; pattern learning improves decisions.
- **Feedback loops** (`forge_harness/meta_learning/feedback_loops.py`): Post-session indexing to Code Atlas, tech-debt feature generation, human-gate threshold optimization.
- **Ralph Loop:** Uses SimpleHistory and Code Atlas for history-aware prioritization; triggers feedback loops at session end so outcomes feed back into learning store and pattern effectiveness.

### 5.2 Patterns for "Each Agent Iteration Improves the Next"

1. **Always update shared state after meaningful work:** Write to `docs/PROMPT.md` or session log; run `/handoff-clean` when context is high so the next agent gets current state.
2. **Record outcomes in a machine-readable way:** Use heartbeat, completion hooks, or a small "session summary" (e.g. what was done, success/failure) so feedback_loops and Code Atlas can ingest it.
3. **Use Code Atlas before starting:** When starting a task, query Code Atlas (or QMD over harness/forge docs) for similar past work so the agent benefits from previous iterations.
4. **Single ownership of compounding wiring:** Ralph Loop + Flywheel own the compounding logic; other agents "participate" by: (a) running in the harness (Ralph/flywheel), or (b) writing structured handoffs and progress so a later Ralph run can index them.
5. **Document the loop for orchestrators:** Add a short "Compounding 101" to AGENTS.md or docs: "After you complete work: update PROMPT.md, run /complete-task or /ship-feature so heartbeat and feedback loops run. That improves the next run."

### 5.3 Checklist for Agents Contributing to Compounding

- [ ] On session end or task end: update `docs/PROMPT.md` (or session log) with outcome and next steps.
- [ ] When using harness: use `/complete-task` or `/ship-feature` so heartbeat and post-session hooks run.
- [ ] Before starting: optionally run `qmd query "past work on X"` or use Code Atlas to load relevant context.
- [ ] Do not bypass handoff: when context > 50%, run `/handoff-clean` so the next agent gets a clean state.

---

## 6. Progressive Disclosure for Orchestrators (Discover Tools as Needed)

### 6.1 Current State

- **AGENTS.md:** Documentation levels 0–3 + Skills + Patterns; "5 Essential Commands"; Fleet Commands table.
- **CLAUDE.md:** "Infrastructure Discovery" table (orchestrator rules, fleet dispatch, fleet management, CLI, skills, etc.).
- **INFRASTRUCTURE_MAP.md:** Level 0 (get something done), Level 1 (CLI, dashboards, heartbeat, fleet), Level 2 (skills, scripts).
- **workflow-analysis-tech.md:** Tier 0–3 skills (essential → dev → fleet → specialized); `/help dev`, `/help fleet`, `/help all`.

### 6.2 Recommended Progressive Disclosure Structure

1. **Level 0 — "I need to do one thing"**  
   Single table: task → command/skill (e.g. "Send to agent" → `/dispatch` or fleet-dispatch; "Check fleet" → `/fleet-ops status`). No need to read full AGENTS.md.

2. **Level 1 — "I'm an orchestrator / I use the fleet daily"**  
   - Read: PROMPT.md, then AGENTS.md sections: Critical Rules, 5 Essential Commands, Fleet Architecture, Fleet Commands.
   - Point to: `.claude/modules/fleet-dispatch.md`, `.claude/modules/fleet-management.md`, `.claude/skills/README.md`.
   - Discovery: "For dispatch details, read .claude/skills/dispatch/SKILL.md."

3. **Level 2 — "I need to add or change behavior"**  
   - CLI reference: `harness/docs/CLI_REFERENCE.md`.
   - Scripts: `harness/scripts/` (fleet-dispatch.sh, tmux-send-to-agent.sh, fleet-spawn, etc.).
   - Python: `forge_harness/parallel_dispatch.py`, `flywheel.py`, `meta_learning/feedback_loops.py`.

4. **Level 3 — "I need full context"**  
   - Living docs pyramid (00-portfolio-digest, domain CLAUDE, project CLAUDE).
   - Harness architecture: `harness/CLAUDE.md`, `docs/HARNESS_ARCHITECTURE_ANALYSIS.md`.
   - Research: `docs/research/workflow-analysis-tech.md`, QMD docs.

### 6.3 Orchestrator Discovery Path (Proposed)

In CLAUDE.md or AGENTS.md, add an explicit path:

```markdown
## Orchestrator discovery path
1. **Start:** docs/PROMPT.md → AGENTS.md (Critical Rules + 5 Commands + Fleet).
2. **To dispatch:** .claude/skills/dispatch/SKILL.md and harness/scripts/fleet-dispatch.sh.
3. **To find docs:** docs/QMD_QUICK_REFERENCE.md or qmd query.
4. **To understand compounding:** harness/docs/FLYWHEEL.md and docs/CROSS_AGENT_PATTERNS.md §5.
5. **Full reference:** harness/docs/CLI_REFERENCE.md and INFRASTRUCTURE_MAP.md.
```

---

## 7. Recommended Wrapper API for Inter-Agent Dispatch (Summary)

- **Canonical entry:** `fleet-dispatch.sh` / `fleet_dispatch.py` for sending a task to a tmux session:window.
- **Contract:** Literal task text; send and Enter as separate steps; optional verification; update `.forge_fleet/state.json`.
- **Skills:** Always use `/dispatch` or the script; document in dispatch SKILL.md that the script path is `harness/scripts/fleet-dispatch.sh` (or `tmux-send-to-agent.sh` with same semantics when verification/restart are needed).
- **Future:** Optional `forge_harness.dispatch.send_to_agent()` that wraps fleet_dispatch.dispatch_task for use from Python orchestrators.

---

## 8. Skill Portability Patterns (Summary)

- One SKILL.md per skill with frontmatter + description + **When to Use** + **CLI equivalent**.
- Task text when dispatching: self-contained, doc pointers, report-to-PROMPT.md.
- Single catalog: `.claude/skills/README.md`; orchestrator rules say "use skills, not raw commands."
- Optional per-agent one-pagers that reference the same SKILL.md and give exact commands for that agent.

---

## 9. Proposed AGENTS.md Structure (High Level)

Keep existing content; add or emphasize these sections in this order:

1. **First time / Quick Start** (existing table: QUICK_START, PROMPT.md, role).
2. **Documentation levels (progressive disclosure)** (existing table: Level 0–3, Skills, Patterns).
3. **Critical rules** (existing: orchestrator, context, PROMPT.md, skills).
4. **5 Essential Commands** (existing).
5. **Inter-agent dispatch (canonical):** One short paragraph: use `/dispatch` or fleet-dispatch.sh; do not use raw tmux; link to `.claude/skills/dispatch/SKILL.md`.
6. **Orchestrator discovery path:** Numbered list (PROMPT → AGENTS → dispatch skill → QMD → compounding → full reference) as in §6.3.
7. **Project overview, memory system, tiers** (existing).
8. **Multi-agent fleet** (architecture, fleet commands, agent types, autonomous mode) (existing).
9. **Compounding 101:** Short bullet list: update PROMPT.md, use /complete-task or /ship-feature, optional QMD/Code Atlas before start, /handoff-clean when context high.
10. **Backend/Frontend patterns, troubleshooting, handoff protocol** (existing).
11. **Skills catalog:** Pointer to `.claude/skills/README.md` and "For dispatch, read .claude/skills/dispatch/SKILL.md."

This keeps AGENTS.md as the universal entry while making dispatch, discovery, and compounding explicit and easy to find.

---

## 10. References

| Topic | Location |
|-------|----------|
| Orchestrator rules | `.claude/modules/orchestrator-rules.md` |
| Fleet dispatch (module) | `.claude/modules/fleet-dispatch.md` |
| Dispatch skill | `.claude/skills/dispatch/SKILL.md` |
| Tmux send script | `harness/scripts/tmux-send-to-agent.sh` |
| Fleet dispatch (CLI/Python) | `harness/scripts/fleet-dispatch.sh`, `harness/scripts/fleet_dispatch.py` |
| Parallel dispatch (in-process) | `harness/forge_harness/parallel_dispatch.py` |
| Flywheel / compounding | `harness/forge_harness/flywheel.py`, `harness/docs/FLYWHEEL.md` |
| Feedback loops | `harness/forge_harness/meta_learning/feedback_loops.py` |
| QMD | `docs/QMD_QUICK_REFERENCE.md`, `docs/openclaw/QMD_INTEGRATION.md` |
| Workflow analysis | `docs/research/workflow-analysis-tech.md` |
| Infrastructure map | `docs/INFRASTRUCTURE_MAP.md` |
| Agent result schema | `docs/AGENT_RESULT_SCHEMA.md` |
