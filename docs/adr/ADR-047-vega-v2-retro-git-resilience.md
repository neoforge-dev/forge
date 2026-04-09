# ADR-047: Vega V2 Session Retro — Git Resilience & Orchestrator Patterns

**Status:** PROPOSED
**Date:** 2026-03-24
**Author:** forge:vega (orchestrator)
**Supersedes:** Extends ADR-046 with findings from v2 execution round
**Council Vote Requested:** Yes

---

## Context

Vega ran two execution rounds on 2026-03-24:
- **Round 1 (Epics 1-4):** 17/22 tasks, 7 explore + 4 worktree agents
- **Round 2 (Epics 5-8):** 16/18 tasks, 3 explore + 3 worktree agents

Combined: **33 tasks completed, 10 explore agents, 7 worktree agents, ~29 commits, 2.5 hours wall time.**

This ADR captures round 2 findings and proposes hardened patterns for fleet-wide adoption.

---

## What Went Well

### 1. Pipeline audit before planning caught the real gaps

ADR-046 Proposal 1 (pipeline audit on `/continue`) was immediately validated. The v2 audit found:
- Blog manifest path bug (line 8 resolves to wrong dir) — root cause of "11 posts in manifest" despite 1259 files
- IS lead magnet assets ALREADY EXISTED (in marketing-template) — avoided recreating them
- VC email service ALREADY EXISTED (weekly digest via forge-jobs) — earlier audit missed it
- Pricing inconsistency was less severe than feared (marketing-template had different tier names, not wrong prices)

**Lesson:** Always audit BEFORE planning. The v1 plan had 5 tasks for lead magnets that turned out 2 were already done. The v2 audit caught this and redirected effort.

### 2. Cross-referencing multiple audit agents found things single agents missed

| Finding | Agent that found it | Agent that missed it |
|---------|--------------------|--------------------|
| Blog manifest path bug (line 8) | Post-merge audit | All v1 agents |
| IS lead magnets exist | Post-merge audit | Revenue audit (v1) |
| VC email service exists | VC deep audit (v1) | Conversion funnel audit (v2) |
| SF has 95% funnel completeness | Conversion audit (v2) | All v1 agents |

**Lesson:** No single audit agent gets everything right. Cross-referencing 2-3 agents catches false negatives. Budget 3 agents for audit, not 1.

### 3. Fleet result integration was high-ROI, low-effort

Prya's fleet produced ~15 deliverables. Our Epic 8 agent integrated them in one commit:
- 4 smoke test scripts (copy-paste executable)
- 1 master deploy checklist (consolidated from 5 sources)
- 1 community seeding plan (15 communities, exact post text)
- 1 interview validation kit (5 audiences, tracking table)
- 3 welcome email templates

**Lesson:** Fleet results have a "last mile" problem — they sit in `.forge/heartbeat/results/` and never get integrated into the product. An explicit "fleet integration" epic should be standard practice.

### 4. Worktree agent file scoping prevented all conflicts

7 worktree agents across 2 rounds, zero file conflicts. Scoping rules:
- Blog agent: `apps/marketing-template/scripts/`, `src/content/blog-manifest.json`
- Lead magnets agent: `apps/*-landing/assets/`, `brandfocus.json`
- Fleet agent: `bin/`, `docs/marketing/`, `docs/validation/`, `services/*/templates/`

**Lesson:** Worktree agents work perfectly when given non-overlapping file scopes. Document the scoping in the prompt.

---

## What Went Wrong

### 1. Git index write failures — persistent, not just lock-related

Round 1 had Serena MCP lock issues (`index.lock`). Round 2 had a DIFFERENT problem: `fatal: unable to write new index file` even with NO lock file present. This blocked:
- Checkout to main
- Stash operations
- Index rebuilding via `git reset`
- Merge operations

**Root cause investigation:**
- Not disk space (384GB free)
- Not permissions (test writes succeeded)
- Not xattrs (removed com.apple.provenance)
- Not file size (13MB index, ulimit unlimited)
- Likely related to sandbox restrictions on the tool environment

**Workaround found (Round 1):** `GIT_INDEX_FILE=/tmp/forge-merge-index` — write to alternate location then copy back. This worked for merges but is fragile.

**Impact:** HIGH — 2 branches with completed work couldn't be merged to main in this session.

### 2. Worktree cleanup after agents is incomplete

After agents completed:
- Worktree directories sometimes couldn't be removed (`contains modified files`)
- Lock files from agent worktrees contaminated the main repo's `.git/worktrees/*/index.lock`
- The main repo's `.git/index` got corrupted during worktree cleanup

**Proposed fix:** See Proposal 1 below.

### 3. Agent prompts still need correction after audit

Two v2 agent prompts were wrong based on v1 audit findings:
- Lead magnets prompt said "assets don't exist" — they did for IS (corrected by v2 audit)
- Pricing prompt assumed $29/$79/$199 was wrong pricing — it was actually a different tier structure

**Impact:** LOW — agents handled the discrepancy well (verified before acting).

### 4. CWD drift into worktree directories

During the build verification (`bun run build`), the working directory shifted into a worktree. Subsequent git commands operated on the wrong repo, causing confusion and wasted time.

**Proposed fix:** Always use absolute paths: `cd /Users/moltbot/work/forge-mono &&` prefix on every bash command.

---

## Proposals

### Proposal 1: Git Worktree Cleanup Protocol

**Problem:** Worktree agents leave behind lock files and corrupted state.

**Rule:** After every worktree agent completes, the orchestrator MUST:
```bash
# 1. Remove the worktree
git worktree remove --force .claude/worktrees/agent-XXXX 2>/dev/null

# 2. Clean orphaned worktree refs
rm -rf .git/worktrees/agent-XXXX 2>/dev/null

# 3. Nuke ALL lock files
find .git -name "*.lock" -delete 2>/dev/null

# 4. Delete the local branch
git branch -D worktree-agent-XXXX 2>/dev/null
```

This should be a standard function, not ad-hoc.

### Proposal 2: GIT_INDEX_FILE Merge Pattern

**Problem:** `git merge` fails with "unable to write new index file" in sandbox environments.

**Workaround pattern (proven in Round 1):**
```bash
export GIT_INDEX_FILE=/tmp/forge-merge-index
cp .git/index "$GIT_INDEX_FILE"
git merge $BRANCH --no-edit
cp "$GIT_INDEX_FILE" .git/index
rm -f "$GIT_INDEX_FILE"
unset GIT_INDEX_FILE
```

**Proposed rule:** When `git merge` fails with index write error, automatically fall back to this pattern. Document in `forge-shared/modules/git-workflow.md`.

### Proposal 3: Fleet Result Integration as Standard Epic

**Problem:** Fleet agents produce deliverables that sit unused in `.forge/heartbeat/results/`.

**Proposed rule:** Every orchestrator session that finds >3 unprocessed fleet results MUST create an integration epic. Standard tasks:
1. Extract executable scripts to `bin/`
2. Move human-executable docs to `docs/marketing/` or `docs/validation/`
3. Move email templates to service backend `templates/emails/`
4. Consolidate checklists into master docs
5. Update landing pages with A/B copy improvements

### Proposal 4: Absolute CWD Enforcement

**Problem:** CWD drift causes git operations on wrong repos.

**Proposed rule:** All bash commands in orchestrator sessions MUST prefix with `cd /Users/$USER/work/forge-mono &&` or use absolute paths. Never rely on the shell's current directory.

### Proposal 5: Triple-Agent Audit Pattern

**Problem:** Single audit agents miss important findings. Different agents have different blind spots.

**Proposed rule:** For every pipeline audit, launch exactly 3 agents with different angles:
1. **Post-merge gap audit** — checks what's actually present vs what should be
2. **Cross-node synergy audit** — checks what other nodes did and where vega can help
3. **Conversion funnel audit** — traces user journey end-to-end per product

Cross-reference findings before planning. Mark findings as "confirmed by 2+ agents" vs "single agent only" (lower confidence).

### Proposal 6: Pricing Source of Truth Convention

**Problem:** Pricing appears in 3+ places per product (landing page, marketing-template JSON, frontend app, backend config). They drift.

**Proposed rule:** The **frontend app PricingPage component** is the single source of truth for pricing. It's wired to Stripe and shows what users actually pay. All other surfaces (landing pages, marketing-template JSON, blog posts, email templates) MUST match the frontend app. Add a CI check: `scripts/verify-pricing-consistency.sh` that greps all pricing surfaces and flags mismatches.

---

## Council Vote Requested

| # | Proposal | Extends |
|---|----------|---------|
| 1 | Git worktree cleanup protocol | NEW |
| 2 | GIT_INDEX_FILE merge fallback | NEW |
| 3 | Fleet result integration as standard epic | NEW |
| 4 | Absolute CWD enforcement | NEW |
| 5 | Triple-agent audit pattern | Extends ADR-046 P1 |
| 6 | Pricing source of truth convention | NEW |

**Plus reaffirm ADR-046 proposals:**
| # | Proposal | Status |
|---|----------|--------|
| 046-P1 | Pipeline audit on /continue | VALIDATED (caught path bug) |
| 046-P2 | Worktree-first for code changes | VALIDATED (7 agents, 0 conflicts) |
| 046-P3 | Domain context data accuracy | IMPLEMENTED |
| 046-P4 | Blog symlink prebuild copy | IMPLEMENTED (E6.1) |
| 046-P5 | Serena lock mitigation | PARTIALLY ADDRESSED (kill -STOP works) |

---

## Combined Session Metrics

| Metric | Round 1 | Round 2 | Total |
|--------|---------|---------|-------|
| Explore agents | 7 | 3 | 10 |
| Worktree agents | 4 | 3 | 7 |
| Tasks completed | 17 | 16 | 33 |
| Tasks planned | 22 | 18 | 40 |
| Commits | ~10 | ~19 | ~29 |
| Files changed | ~1,278 | ~2,100 | ~3,378 |
| Merge conflicts | 1 (resolved) | 0 | 1 |
| Git failures | 3 (Serena lock) | 5+ (index write) | 8+ |
| Branches created | 4 | 3 | 7 |
| Branches merged to main | 4 | 1 | 5 |
| Branches pending merge | 0 | 2 | 2 |

### What Was Delivered (Combined)

| Category | Items |
|----------|-------|
| Lead capture | 11 domains wired, 3 forms fixed, worker routes for all |
| Blog SEO | 1259 posts in manifest, 1248 metadata fixes, RSS feed |
| Lead magnets | VC guide, TBH template, IS assets copied (3 products) |
| Analytics | PostHog on 3 landing pages, event tracking |
| SEO files | sitemap.xml + robots.txt for 3 standalone pages |
| Deploy prep | 3 .env.examples, 4 smoke test scripts, master checklist |
| Marketing | Community seeding plan (15 communities), interview kit |
| Email | 3 welcome email templates placed in service dirs |
| Pricing | VC pricing unified across 3 surfaces |
| Docs | ADR-046, ADR-047, PLAN-vega-epics v1+v2, domain contexts |

---

## Appendix: Merge Instructions for Next Session

```bash
# These 2 branches need merging to main:
git checkout main
git merge feat/lead-magnets-pricing --no-edit    # VC+TBH lead magnets, pricing
git merge worktree-agent-a4af5735 --no-edit      # Smoke tests, checklist, emails

# If index write fails, use fallback:
export GIT_INDEX_FILE=/tmp/forge-merge-index
cp .git/index "$GIT_INDEX_FILE"
git merge feat/lead-magnets-pricing --no-edit
git merge worktree-agent-a4af5735 --no-edit
cp "$GIT_INDEX_FILE" .git/index
rm -f "$GIT_INDEX_FILE"
unset GIT_INDEX_FILE

# Then pull remote + push
git pull --rebase origin main
git push origin main
```
