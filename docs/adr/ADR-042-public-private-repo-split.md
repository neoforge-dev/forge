# ADR-042: Public/Private Repository Split

**Date:** 2026-03-10
**Status:** Proposed
**Decision Makers:** Lead orchestrator (prya), council review deferred
**Reference:** `docs/SURFACE_DISCIPLINE_PLAN.md` H3-3

---

## Context

FORGE is a self-hosted AI Ops Platform with two distinct audiences:

1. **OSS contributors / evaluators** — care about the agent control plane, CLI, daemon, and routing infrastructure. They want to contribute and run their own fleet.
2. **Bogdan's business operations** — portfolio stage data, Royal Jelly domain context, revenue-sensitive sprint state, and `.forge/` runtime artifacts.

Currently, both are in the same repository. This creates three problems:

**Problem 1: Business context leaks into public commits.** Every `git log` on `github.com/neoforge-dev/forge` exposes sprint state (`docs/PROMPT.md`), portfolio revenue projections (`docs/PLAN.md`), and domain-specific context (`.forge/context/`). This is competitive intelligence visible to anyone.

**Problem 2: `.forge/` noise pollutes the public signal.** The `.forge/` directory (39 dirs, hundreds of files) is partially gitignored but patrols and agents write state files that leak into `git status` and confuse OSS contributors who clone the repo.

**Problem 3: Contributor friction.** Anyone who wants to contribute to `cmd/forge/` or `cmd/forged/` has to work around FORGE's own operational state embedded in the same tree.

**Current mitigation** (gitignore-only): `.gitignore` excludes some `.forge/` artifacts but not all. `docs/PROMPT.md` and `docs/PLAN.md` are tracked in git, exposing sprint-level business intelligence with every push.

---

## Decision

Split the repository into two:

### Public Repository: `github.com/neoforge-dev/forge`

Contains the infrastructure layer — everything an OSS contributor needs:

| Path | Contents |
|------|----------|
| `cmd/forge/` | CLI (all 8 root commands + hidden fleet commands) |
| `cmd/forged/` | Daemon (HTTP API, SQLite, FSM, patrols) |
| `forge-shared/` | Reusable modules (dispatch, git-workflow, tech-stack) |
| `config/routing/` | Agent/node registry YAML (de-personalized) |
| `docs/adr/` | Architecture Decision Records (ADR-001 onward) |
| `docs/guides/` | Infrastructure guides (CROSS_AGENT_PATTERNS, etc.) |
| `docs/AGENT_QUICK_START.md` | Onboarding for new agents |
| `CLAUDE.md` | Agent instructions (infrastructure roles only) |
| `AGENTS.md` | Fleet operations reference |
| `harness/forge_harness/ios_harness/` | iOS build automation |

### Private Repository: `github.com/bogdan-veliscu/forge-ops` (or similar)

Contains business operations — everything that drives the portfolio:

| Path | Contents |
|------|----------|
| `portfolio/` | All 95 projects, 11 domains |
| `config/portfolio/` | Revenue stage data, launch checklists |
| `.forge/context/` | Royal Jelly per-domain context |
| `.forge/memories/` | Learned agent patterns |
| `docs/PLAN.md` | Live sprint planning |
| `docs/PROMPT.md` | Session state / handoffs |
| `docs/INFRA_REVIEW_*.md` | Internal reviews |
| `.forge/dispatches/` | Agent dispatch files |
| `.forge/heartbeat/` | Agent heartbeats and results |

### Shared via Git Submodule or Subtree

The private repo references the public repo as a submodule (or read-only dependency). The orchestrator checks out both; fleet agents only need the public repo.

---

## Consequences

### Positive

- **Clean OSS signal**: Public repo contains only infrastructure. No business data in commit history going forward.
- **Business context protected**: Revenue projections, portfolio stage, sprint state stay in private repo, not exposed to competitors.
- **Contributor-friendly**: OSS contributors can `git clone`, `go build`, and contribute without wading through `.forge/` state.
- **Separation of concerns**: Infrastructure evolution (cmd/forge/, cmd/forged/) decouples from business operations cadence.
- **Portfolio as product**: The private repo becomes the actual "operating system" — portfolio managed independently from the platform it runs on.

### Negative

- **Git history contains business data permanently**: The current `github.com/neoforge-dev/forge` repo has years of commit history with PROMPT.md, PLAN.md, and context files. Rewriting history (git-filter-repo) is complex and breaks all existing clones/forks.
- **Multi-repo operational overhead**: PRs span two repos. Keeping submodule refs in sync requires discipline (or automation). CI/CD pipelines duplicate.
- **Submodule fragility**: Fleet agents checking out the private repo need both repos accessible. SSH key or token management per node.
- **Bootstrap complexity**: New node setup now requires credentials for both repos.
- **Orchestrator rules split**: `CLAUDE.md` and `AGENTS.md` live in the public repo but reference private paths. Cross-repo context loading needs explicit protocol.

### Risks

- **Incomplete split**: If `portfolio/` imports from `cmd/forge/` at the Go level (it doesn't — they're separate binaries), this would break. Current: no Go import dependency between portfolio and cmd/. Safe.
- **Context drift**: Royal Jelly context in private repo gets out of sync with public infrastructure. Need a protocol for context handoff on cross-repo work.
- **Contributor confusion**: A contributor working on `cmd/forged/` sees `CLAUDE.md` referencing `.forge/context/` paths that don't exist in their checkout. Requires updated onboarding docs.

---

## Migration Plan

### Phase 1: Prepare (1 week)

1. Identify ALL paths that cross the public/private boundary:
   - `grep -r "\.forge/context" cmd/` — verify no code imports from `.forge/`
   - `grep -r "portfolio/" cmd/` — confirm no cross-imports
   - `grep -r "docs/PLAN\|PROMPT" cmd/` — confirm only docs reference these

2. Create `forge-ops` private repo (empty).

3. Update `.gitignore` in public repo to exclude all business paths:
   ```
   docs/PLAN.md
   docs/PROMPT.md
   docs/INFRA_REVIEW_*.md
   portfolio/
   .forge/context/
   .forge/memories/
   .forge/heartbeat/
   .forge/dispatches/
   ```

4. Write `CONTRIBUTING.md` for the public repo explaining the split.

### Phase 2: Execute (1 day)

1. `git filter-repo` on the public repo to remove business paths from history (optional — only if history exposure is unacceptable; irreversible).

2. Push current state of business paths to `forge-ops` private repo:
   ```bash
   cd /tmp/forge-ops-init
   git init
   git add portfolio/ .forge/context/ .forge/memories/ docs/PLAN.md docs/PROMPT.md
   git commit -m "init: migrate business context from forge public repo"
   git remote add origin git@github.com:bogdan-veliscu/forge-ops.git
   git push -u origin main
   ```

3. Add `forge-ops` as submodule in the private checkout:
   ```bash
   git submodule add git@github.com:bogdan-veliscu/forge-ops.git ops
   ```

4. Update `CLAUDE.md` and `AGENTS.md` in the public repo to document the split.

### Phase 3: Stabilize (2 weeks)

1. Update all agents' startup scripts to check out both repos.
2. Update Royal Jelly protocol to write to `ops/.forge/context/`.
3. Update CI/CD: public repo CI runs `cmd/forge/` and `cmd/forged/` tests only.
4. Delete business paths from the public repo working tree (after Phase 2 migration).

---

## Alternative Considered: Gitignore-Only Approach

**Why insufficient:** `.gitignore` prevents new commits of business data but does not:
- Remove existing history (business data remains accessible via `git log`)
- Prevent agents from accidentally committing untracked files
- Signal to OSS contributors that certain paths are off-limits
- Separate the CI/CD concerns (portfolio tests vs. infrastructure tests)

The gitignore approach is a patch; the repo split is the correct long-term architecture.

---

## Council Review Timing

Council dispatch is deferred until the split becomes blocking or the runtime simplification work is stable enough to make the split actionable.

1. **History rewrite? → No (defer).** Existing history exposure (PROMPT.md, PLAN.md, portfolio/ in git log) is visible but not a material business risk today — no trade secrets, no credentials, only sprint state. `git filter-repo` is irreversible and invalidates all existing forks. Defer until the public repo has real external contributors who would be confused or harmed by the history.

2. **Submodule vs. monorepo tooling? → Gitignore-only first.** Add `docs/PLAN.md`, `docs/PROMPT.md`, `portfolio/`, `.forge/context/`, `.forge/memories/` to `.gitignore`. This prevents future commits of business data without submodule friction. The full submodule/worktree structure is Phase 3 — revisit after 90 days of clean pushes under gitignore discipline.

3. **Timing? → After first Voice Coach revenue.** The split introduces operational overhead (two repos, two CI pipelines, SSH key per node) before revenue validates the need. Trigger: Voice Coach first 5 paying signups OR iOS App Store approval — whichever comes first.

**Immediate action (Phase 1 only):** Update `.gitignore` to exclude business paths. No repo creation, no submodule, no history rewrite.
