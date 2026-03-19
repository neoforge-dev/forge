---
name: prime
description: Prime session with context for focused work
---

# Prime Session

Load context and prepare for focused work.

## Focus Areas

| Area | Description | Key Files |
|------|-------------|-----------|
| `dev` | Development, coding, features | `PLAN.md`, `progress.md`, tests |
| `content` | Blog posts, marketing copy, docs | `content/`, `blog/`, marketing-template |
| `ops` | DevOps, deployment, infrastructure | Dockerfile, CI/CD, Railway/Cloudflare |
| `marketing` | Landing pages, SEO, analytics | marketing-template, PostHog |
| `security` | Auth, compliance, audits | auth code, HIPAA/COPPA |
| `testing` | Test coverage, QA, quality | tests/, pytest, vitest |
| `design` | UI/UX, components, styling | frontend/, components/, Tailwind |

## Steps

### 1. Load Context (always)
Read in order:
- Root `CLAUDE.md` / `AGENTS.md` - portfolio rules
- `docs/PLAN.md` - current sprint
- `.forge/memories/INDEX.md` - available memory/context

If project specified:
- `{domain}/CLAUDE.md` - domain rules
- `{domain}/{project}/docs/` - project docs

### 2. Check Completions (orchestrator only)
```bash
tail -20 .forge/heartbeat/orchestrator.log | grep SIGNAL
# Or: .forge/scripts/check-agent-completion.sh
```
If completions present: commit deliverables, update PROMPT.md, dispatch follow-up before new work.

### 3. Check State
- `git status` - working tree
- `git branch -v` - current branch
- `git log --oneline -5` - recent commits

### 4. Generate Priorities
Suggest 3 priorities based on project and focus:

| Priority | Task | Area | Effort |
|----------|------|------|--------|
| 1 | [Most urgent] | [area] | S/M/L |
| 2 | [Second priority] | [area] | S/M/L |
| 3 | [Third priority] | [area] | S/M/L |
