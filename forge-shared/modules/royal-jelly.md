# Royal Jelly — Persistent Domain Context

Persistent context that survives agent restarts, handoffs, and `/clear` cycles. Named after the substance that transfers knowledge in a hive.

## Why

Agents lose deep reasoning when context is compacted or cleared. Royal Jelly files persist domain knowledge on disk so the next session starts with full situational awareness instead of re-discovering everything.

## Directory Structure

```
.forge/context/
├── is/                    # Interview Simulator (codeswiftr-com)
├── vc/                    # Voice Coach (brandfocus-ai)
├── nf/                    # NeoCode (neoforge-dev)
├── forge/                 # FORGE Infrastructure
├── cc/                    # CalmConnect (calmconnect)
├── lv/                    # LeanVibe (leanvibe-ai)
├── ag/                    # AdGuild (adguild-io)
├── sf/                    # StudyFlow (thebrightharbor-com)
├── da/                    # DiscoverAI (discoverai-co)
├── pk/                    # PKM.ai (brandfocus-ai)
└── oc/                    # OpenClaw (openclaw)
```

Each domain directory contains:

| File | Purpose | Updated When | Updated By |
|------|---------|--------------|------------|
| `lead-context.md` | Current state, blockers, priorities, key files | Every handoff / session end | Domain lead |
| `decisions.md` | Architectural choices with rationale (append-only) | When a non-obvious decision is made | Domain lead or worker |
| `failures.md` | What was tried and failed (append-only) | When an approach fails | Domain lead or worker |

## File Templates

### lead-context.md

```markdown
# {Domain} — Lead Context

**Last Updated:** {date}
**Lead:** {agent name}

## Current State
- Architecture: {one-line summary}
- Tests: {count} passing, {coverage}% coverage ← **MUST be verified by running tests, not copied**
- Active blockers: {list or "none"}
- Recent changes: {last 2-3 commits}

## Data Accuracy (Council TC-S159)
- Last test run: {date} — command: `pytest -q` / `go test ./...`
- Metrics source: {actual test run / copied from prior session}
- **Rule: Never update test counts or coverage without running tests first.**

## Next 3 Priorities
1. {priority}
2. {priority}
3. {priority}

## Key Files
- {file}: {what it does}
- {file}: {what it does}
```

### decisions.md

```markdown
# {Domain} — Decisions

Append-only log. Newest at bottom.

## {date}: {Decision Title}

**Decision:** {what was decided}
**Alternatives considered:** {what else was evaluated}
**Rationale:** {why this choice — 1-2 sentences}
```

### failures.md

```markdown
# {Domain} — Failures

Append-only log. Prevents agents from retrying known-bad approaches.

## {date}: {What Failed}

**Attempted:** {what was tried}
**Result:** {what happened}
**Lesson:** {why it failed — so next agent doesn't repeat it}
```

## Protocol

### On Session Start

1. Detect your domain (from dispatch file, task prompt, or PROMPT.md)
2. Read `.forge/context/{domain}/lead-context.md`
3. If the file doesn't exist, create it from the template above

### On Every Handoff

1. Update `.forge/context/{domain}/lead-context.md` with current state
2. If you made architectural decisions this session, append to `decisions.md`
3. If an approach failed, append to `failures.md`

### On PreCompact (Automatic)

The `heartbeat_eval_compact.sh` hook saves emergency state. Royal Jelly files supplement this with structured, domain-specific context that survives across sessions.

## Domain Short Codes

| Short | Domain | Primary Project |
|-------|--------|----------------|
| `is` | codeswiftr-com | interview-simulator |
| `vc` | brandfocus-ai | voice-coach |
| `nf` | neoforge-dev | neocode |
| `forge` | (infrastructure) | harness, CC, CLI |
| `cc` | calmconnect | allergen-coach |
| `lv` | leanvibe-ai | calorie-coach |
| `ag` | adguild-io | adguild-platform |
| `sf` | thebrightharbor-com | study-flow |
| `da` | discoverai-co | discover-ai |
| `pk` | brandfocus-ai | pkm-ai |
| `oc` | openclaw | openclaw |

## Canonical Terminology Hierarchy

TC-TAXONOMY-S150 (council ratified 4-0, 2026-03-22) established the canonical domain hierarchy:

```
Portfolio > Domain > Product > Task > Plan
```

| Term | Definition |
|------|-----------|
| **Portfolio** | The full collection of 95 products across 11 domains |
| **Domain** | A business vertical (e.g., codeswiftr-com, brandfocus-ai) |
| **Product** | A repository or deployable unit within a domain (canonical term) |
| **Task** | A unit of work assigned to an agent |
| **Plan** | A multi-task execution plan for a product |

**"product" is the canonical term.** "project" is deprecated — use "product" in all new code and docs.

Backward-compat surfaces (do NOT break without a migration):
- `/projects` HTTP routes remain (backward-compat view in DB)
- `type Project = Product` alias in `cmd/forge/internal/types.go`
- `--project` CLI flag (hidden, deprecated alias for `--product`)
- `ProjectListResponse` struct (deprecated; use `ProductListResponse`)

---

## Research Integration (S161)

Heartbeat research results MUST flow into decisions.md. See `forge-shared/modules/knowledge-flow.md` for the full protocol.

**Summary:** After any research task completes:
1. Orchestrator reads `.forge/heartbeat/results/{agent}-{task}.md` within 48h
2. Extracts 1-3 decisions
3. Appends to `.forge/context/{domain}/decisions.md` using template:
   ```
   ## {date}: {Decision Title}
   **Decision:** {what was decided}
   **Source:** .forge/heartbeat/results/{agent}-{task}-{date}.md
   **Why:** {rationale}
   ```
4. Updates `lead-context.md` if domain state changed
5. Creates follow-up task if action needed

## Rules

1. **lead-context.md is mandatory** — update it on every handoff, no exceptions
2. **decisions.md is mandatory** — every domain must have meaningful entries. Process heartbeat results within 48h.
3. **Append-only for decisions.md and failures.md** — never delete entries
4. **Keep it concise** — lead-context.md should be < 50 lines, decisions/failures < 5 lines per entry
5. **Short codes are canonical** — use 2-letter codes for directory names, not full domain names
6. **All nodes share the same repo** — Royal Jelly propagates on `git pull`
7. **Check before building** — consult `docs/PATTERNS.md` and `docs/COMMON_MISTAKES.md` before starting work
