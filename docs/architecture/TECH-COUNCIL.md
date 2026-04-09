# Tech Council Protocol

Multi-model decision review for important technical choices. Three AI models from different providers provide independent analysis before decisions proceed.

## Council Members

| Seat | Model | Provider | Perspective | Agent |
|------|-------|----------|-------------|-------|
| 1 | Claude (Opus) | Anthropic | Conservative, safety-focused, strong reasoning | prya orchestrator |
| 2 | Gemini 3 | Google | Research-oriented, broad knowledge, fast | gemini agent |
| 3 | Kimi K2.5 | Moonshot | Code-focused, practical, implementation-aware | is-lead / kimi agent |

## When to Convene

A Tech Council convenes when any of these triggers apply:

| Trigger | Examples | Risk Level |
|---------|----------|------------|
| Architecture decisions | New service, data model redesign, state management | HIGH |
| Breaking changes | API contract changes, schema migrations, removed endpoints | HIGH |
| Security-sensitive changes | Auth flow changes, new API keys, data exposure | CRITICAL |
| API design | New public endpoints, contract changes, versioning | HIGH |
| Dependency major upgrades | Framework version bumps, ORM changes, runtime updates | MEDIUM |
| Performance trade-offs | Caching strategy, query optimization, indexing | MEDIUM |
| Cross-domain impact | Changes affecting 3+ domains | HIGH |
| Cost implications | New paid services, increased API usage > $50/mo | MEDIUM |

**Does NOT require council:**
- Bug fixes with clear root cause
- Test additions
- Documentation updates
- Lint/type fixes
- Single-domain cosmetic changes

## Council Process

### Phase 1: Problem Statement (Orchestrator)

Orchestrator creates a council dispatch with:

```markdown
# Tech Council Decision: [TC-XXX] [Title]

## Context
[What problem are we solving? What's the current state?]

## Options
- **Option A:** [Description, pros, cons]
- **Option B:** [Description, pros, cons]
- **Option C:** [Description, pros, cons]

## Constraints
[Budget, timeline, compatibility, human gates]

## Questions for Council
1. [Specific question 1]
2. [Specific question 2]
```

### Phase 2: Independent Analysis (Parallel)

Each council member receives the same problem statement and provides:

1. **Recommendation** — Which option and why
2. **Risk assessment** — What could go wrong (1-5 scale)
3. **Implementation complexity** — Effort estimate (S/M/L/XL)
4. **Concerns** — Edge cases, security, scalability
5. **Alternative** — Any option not listed that should be considered

Members write responses to `.forge/council/TC-XXX/{model}.md`.

### Phase 3: Synthesis (Orchestrator)

Orchestrator reads all three analyses and produces:

```markdown
# Council Decision: TC-XXX

## Votes
| Member | Recommendation | Risk | Complexity | Concerns |
|--------|---------------|------|------------|----------|
| Claude | Option A | 2/5 | M | [summary] |
| Gemini | Option A | 3/5 | M | [summary] |
| Kimi   | Option B | 2/5 | S | [summary] |

## Consensus: [UNANIMOUS / MAJORITY / SPLIT]
## Decision: [Option chosen]
## Rationale: [Why]
## Action Items: [Next steps]
```

### Phase 4: Execution or Escalation

| Outcome | Action |
|---------|--------|
| **Unanimous agreement** | Auto-proceed, log decision |
| **Majority (2/3)** | Auto-proceed if low-risk, notify human if high-risk |
| **Split (no majority)** | Escalate to Bogdan/Trinity with all 3 analyses |
| **Security concern raised** | Always escalate regardless of consensus |
| **Cost > $100/mo** | Always escalate regardless of consensus |

## Human Notification Rules

### Notify Bogdan/Trinity when:
- Council cannot reach consensus (split decision)
- Human action required (deploy, API keys, payments)
- Security implications flagged by any member
- Cost implications > $100/month
- Breaking change to live production service

### Auto-proceed when:
- All 3 models agree on approach
- Low-risk, reversible change
- No security concerns raised
- No cost implications
- Change is internal (not user-facing)

## Dispatch Template

Save council requests to `.forge/council/TC-XXX/request.md`:

```markdown
# Tech Council: TC-XXX — [Title]

**Date:** YYYY-MM-DD
**Requester:** [agent/human]
**Domain:** [affected domain(s)]
**Trigger:** [architecture | breaking | security | api | dependency | performance | cross-domain | cost]
**Risk:** [LOW | MEDIUM | HIGH | CRITICAL]

## Problem
[Clear problem statement]

## Options
### Option A: [Name]
- Pros: [list]
- Cons: [list]
- Effort: [S/M/L/XL]

### Option B: [Name]
- Pros: [list]
- Cons: [list]
- Effort: [S/M/L/XL]

## Constraints
[Any hard constraints]

## Council Instructions
Respond in `.forge/council/TC-XXX/{your-model}.md` with:
1. Your recommendation (Option A/B/other)
2. Risk assessment (1-5)
3. Implementation complexity (S/M/L/XL)
4. Concerns and edge cases
5. Any alternative not listed
```

## File Structure

```
.forge/council/
├── TC-001/
│   ├── request.md      # Problem statement
│   ├── claude.md        # Claude's analysis
│   ├── gemini.md        # Gemini's analysis
│   ├── kimi.md          # Kimi's analysis
│   └── decision.md      # Final synthesis
├── TC-002/
│   └── ...
└── INDEX.md             # Decision log
```

## Decision Log

Maintain `.forge/council/INDEX.md`:

```markdown
| ID | Date | Topic | Consensus | Decision | Risk |
|----|------|-------|-----------|----------|------|
| TC-001 | 2026-03-02 | API versioning | UNANIMOUS | URL prefix /v2 | LOW |
| TC-002 | 2026-03-02 | Cache strategy | MAJORITY | Redis + 5min TTL | MEDIUM |
```

## Dashboard Integration

The CC dashboard should show:

1. **Active Councils** — Pending decisions awaiting member responses
2. **Recent Decisions** — Last 5 council decisions with consensus level
3. **Decision Stats** — Unanimous/Majority/Split ratio

Data source: Read `.forge/council/*/decision.md` files.

## Orchestrator Workflow

When a council-worthy decision arises:

```bash
# 1. Create council directory
mkdir -p .forge/council/TC-XXX

# 2. Write request
# (orchestrator writes request.md)

# 3. Dispatch to council members (parallel)
forge dispatch send forge:gemini "Tech Council TC-XXX: Read .forge/council/TC-XXX/request.md — Write your analysis to .forge/council/TC-XXX/gemini.md. EXECUTE — NO research doc."

forge dispatch send forge:is-lead "Tech Council TC-XXX: Read .forge/council/TC-XXX/request.md — Write your analysis to .forge/council/TC-XXX/kimi.md."

# 4. Orchestrator writes own analysis to claude.md

# 5. Synthesize when all 3 respond
# 6. Write decision.md
# 7. Update INDEX.md
# 8. Notify human if needed, otherwise proceed
```

## Example: First Council Decision

When ready to use the Tech Council for the first time, pick a real pending decision:

- IS API versioning strategy
- VC deployment architecture (Railway vs Fly.io)
- Cross-domain auth approach
- Dashboard state management refactor

Start with a MEDIUM-risk decision to calibrate the process before using it for CRITICAL decisions.
