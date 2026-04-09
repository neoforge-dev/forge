# Domain Validation Pipeline

Every domain must pass through these 5 stages before receiving build investment. No exceptions.

## Stages

```
ASSUMPTIONS → RECRUIT → INTERVIEW → DECIDE → BUILD
```

### Stage 1: ASSUMPTIONS
Map P0 assumptions that must be true for the product to succeed.

**Input:** Domain strategy, competitive analysis
**Output:** 3-5 P0 assumptions per domain in `docs/validation/{domain}.md`
**Example:** "IS-1: Devs will pay $19-29/mo for AI interview practice"

### Stage 2: RECRUIT
Find 5 target users per domain to interview.

**Input:** Assumption list, recruitment templates (`docs/fleet-results/S157/kimi-interview-recruitment-nova-gaea.md`)
**Output:** 5 confirmed interview slots
**Channels:** LinkedIn, Reddit, Discord, Facebook groups, Toastmasters, email

### Stage 3: INTERVIEW
Run structured interviews, capture findings.

**Input:** Interview slots, synthesis template (`docs/fleet-results/S157/kimi-interview-synthesis-template.md`)
**Output:** Completed interview synthesis per person
**Script:** `docs/STRATEGY_REVENUE_SPRINT.md` Part 4

### Stage 4: DECIDE
Council vote GO/KILL based on evidence.

**Input:** Interview findings, assumption test results
**Output:** GO (proceed to build) or KILL (archive domain)
**Decision recorded in:** `docs/validation/{domain}.md` + `config/domains.yaml`
**Rule:** Need ≥3 of 5 interviewees confirming willingness to pay

### Stage 5: BUILD
Only for validated domains. Build what users said they'd pay for.

**Input:** Validated assumptions, user pain points
**Output:** Painkiller MVP focused on #1 pain point
**Rule:** Ship within 2 weeks of GO decision

## CLI Support

```bash
forge domain validate <domain>        # Show current stage + blockers
forge domain assumptions <domain>     # List P0 assumptions + test status
forge domain decide <domain> GO|KILL  # Record decision with reasoning
```

## Tracker Location

Per-domain validation trackers: `docs/validation/{domain}.md`

## Kill Criteria (Universal)

Kill a domain if by its deadline:
- No interviews completed
- <5 users show interest
- Users won't pay stated price
- Technical infeasibility discovered
