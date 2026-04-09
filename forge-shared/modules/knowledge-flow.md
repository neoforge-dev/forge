# Knowledge Flow — Research → Decision → Action

How knowledge moves from research results to durable decisions to follow-up actions.

## The Problem

Research results live in `.forge/heartbeat/results/` but never propagate to domain decisions. Knowledge dies at session boundaries. As of S161, 47+ heartbeat results exist without corresponding `decisions.md` entries.

## Canonical Ownership

| Artifact | Location | Lifecycle | Who Writes |
|----------|----------|-----------|------------|
| **Heartbeat results** | `.forge/heartbeat/results/{agent}-{task}.md` | Ephemeral — raw findings | Fleet agents |
| **decisions.md** | `.forge/context/{domain}/decisions.md` | Permanent — durable domain record | Orchestrator |
| **ADR** | `docs/adr/ADR-{N}-{title}.md` | Permanent — portfolio-wide policy | Orchestrator + council |
| **failures.md** | `.forge/context/{domain}/failures.md` | Permanent — anti-pattern log | Orchestrator or worker |

**Rule:** Heartbeat results are *input*. Decisions are *output*. Don't skip the middle step.

## Flow

```
1. Fleet agent completes research → writes heartbeat result
2. Orchestrator reads result before next handoff (not after 48h — before session ends)
3. Orchestrator extracts 1-3 decisions
4. Orchestrator appends to .forge/context/{domain}/decisions.md
5. Orchestrator updates lead-context.md if state changed
6. Orchestrator creates follow-up task if action needed
```

**SLA:** Process results before your session ends, not on a clock. If you generated the research, you process it. If you inherited results from a prior session, process them in your first 15 minutes.

## Decision Entry Template

```markdown
## {date}: {Decision Title}

**Decision:** {what was decided}
**Source:** `.forge/heartbeat/results/{agent}-{task}-{date}.md`
**Why:** {1-2 sentences — rationale, not just the decision restated}
```

## Source Verification (S161 lesson)

When multiple agents research the same topic, their findings may **contradict** each other. Example: minimax claimed "no Spanish support" for Solid Starts; pi verified full Spanish support exists.

**Rule:** When processing contradictory results:
1. Flag the contradiction explicitly in decisions.md
2. Mark the incorrect report as **INCORRECT** with the correcting source
3. If the contradiction can't be resolved by reading code/docs, escalate to human verification
4. Never silently pick one source over another — document why

## When to Create an ADR Instead

Promote a domain decision to an ADR when it:
- Affects multiple domains (cross-domain pattern)
- Changes platform infrastructure (forge CLI, forged daemon)
- Was contentious (council vote needed)
- Has significant reversal cost

## Orchestrator Checklist

### Post-Session (before handoff)
- [ ] Read new heartbeat results for owned domains
- [ ] Extract decisions → append to `decisions.md`
- [ ] Update `lead-context.md` if state changed
- [ ] Create follow-up tasks if action needed

### Pre-Dispatch
- [ ] Check `docs/PATTERNS.md` — does a solution already exist?
- [ ] Check `docs/COMMON_MISTAKES.md` — are we about to repeat a known failure?
- [ ] For high-risk work: include `design-review.md` in dispatch
