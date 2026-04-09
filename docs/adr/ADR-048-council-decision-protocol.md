# ADR-048: Council Decision Protocol

**Status:** PROPOSED
**Date:** 2026-04-05
**Author:** nova (orchestrator)
**Style:** FORWARD-PRESCRIPTIVE (per kimi council review)

---

## Decision

Standardize the council review protocol: every architectural decision follows a defined quorum, dispatch template, timeout, and record-keeping process. This makes fleet governance reproducible and auditable.

---

## Context

The fleet reaches architectural decisions via "council votes" — parallel dispatch to 2-3 fleet agents (gemini, kimi, codex/claude) who review a plan or ADR in isolation and return a verdict + reasoning. This pattern has been used ~15 times in Q1 2026 (see Council Vote History below). It is effective but **undocumented as protocol**.

Drift symptoms observed:
- Quorum rules ad-hoc (2-of-3? 3-of-3? orchestrator tiebreak?)
- Dispatch format varies — sometimes template, sometimes freeform
- Result aggregation done by hand each time
- No record of dissent in ADR INDEX (only final vote)
- TIMEOUT results counted differently across sessions (~50% timeout rate)

## Protocol Rules

### 1. Quorum

2 reviews returned (from 3 dispatched) with non-trivial content = decision can proceed.

| Scenario | Outcome |
|----------|---------|
| 3 reviews returned | Full council — majority vote wins |
| 2 reviews returned | Quorum met — majority of 2 wins |
| 1 review returned | No quorum — orchestrator re-dispatches or escalates |
| 0 reviews returned | No quorum — orchestrator re-dispatches or escalates |

Orchestrator may break a 1-1 tie with written rationale appended to the vote record.

### 2. Timeout Policy

**TIMEOUT counts as ABSTAIN**, not "Against." Analysis of Q1 2026 sessions shows ~50% of dispatched agents time out (11 of 22 results). Counting timeouts as rejections would paralyze the fleet.

| Priority | Timeout SLA | Action on timeout |
|----------|-------------|-------------------|
| URGENT | 1 hour | Drop from quorum; proceed with 1 review if received |
| MEDIUM | 2 hours | Drop from quorum; re-dispatch if quorum not met |
| LARGE | 6 hours | Drop from quorum; re-dispatch if quorum not met |

No retry to the same agent within a single session. Re-dispatch to a different model family.

### 3. Low-Quality Response Policy

Responses that are pure "LGTM" without at least one concrete strength or weakness are treated as **non-responses** (procedural rejection). The orchestrator:

1. Marks the response as `REJECTED — no substantive content`
2. Excludes it from quorum
3. May re-dispatch to a different model family

**Minimum viable review:** must include a vote, confidence score, and at least one specific finding (strength, gap, or risk).

### 4. Dispatch Template

All council dispatches use `.forge/dispatch-templates/council-review.md`. Required fields:

- `{TITLE}` — ADR or plan name
- `{AGENT}` — target agent
- `{PRIORITY}` — URGENT / MEDIUM / LARGE
- `{DOCUMENT_PATH}` — file under review
- `{REVIEW_ID}` — unique identifier for result file

The template mandates structured output (vote, confidence score, strengths, gaps, risks) and git-safe conventions for multi-agent nodes.

### 5. Panel Selection

Default panel: **gemini + kimi + (claude OR codex)**.

| Domain | Recommended Panel |
|--------|-------------------|
| Code architecture | gemini, kimi, claude or codex, plus one of (opencode, amp) |
| Research / planning | gemini, kimi, codex |
| Content / product | gemini, kimi, claude |

Panel must include at least 2 distinct model families. Orchestrator should avoid dispatching to agents that share the same underlying model.

### 6. Decision Record

Record in ADR Council Vote Record table (see INDEX.md) with columns:

| Column | Content |
|--------|---------|
| ADR | Number |
| Date | Session date |
| Vote | ACCEPTED / REJECTED / HOLD / SUPERSEDED / DROPPED |
| Score | e.g. 2-1 (for/against) |
| Reasoning | 1-line per agent; dissent preserved inline |

**Dissent is recorded inline in INDEX.md**, not in separate files. This matches the existing pattern (see ADR-026 codex dissent on UDS) and avoids file-system bloat while keeping minority opinions visible.

### 7. Scope — When Is a Council Vote Required?

| Category | Council Vote? |
|----------|---------------|
| New ADR | ✅ Required |
| Status flip (Partial↔Implemented↔Superseded) | ✅ Required |
| Scope-split or scope-expand decisions | ✅ Required |
| Cross-domain architectural changes | ✅ Required |
| Doc fixes, typos, formatting | ❌ Not required |
| Routine feature work within a domain | ❌ Not required |
| Agent-local refactors | ❌ Not required |

---

## Council Vote History (Q1 2026)

| ADR | Date | Vote | Score | Key Reason / Dissent |
|-----|------|------|-------|----------------------|
| 007 | 03-09 | SUPERSEDED | 3-0 | v3 SQLite tasks make Beads irrelevant. |
| 016 | 03-09 | SUPERSEDED | 3-0 | Direct HTTP model is reality. |
| 019 | 03-09 | HOLD | 3-0 | Phase 1 done, Phase 2 deferred. |
| 026 | 03-09 | ACCEPTED | 2-1 | Bearer+Tailscale sufficient. **Codex dissent: UDS gap.** |
| 027 | 03-09 | DROPPED | 3-0 | Hub model replaces cross-node aggregation. |
| 040 | 03-09 | COMPLETE | 3-0 | Waves 1–6 successful. |

---

## Alternatives Considered

| Alternative | Why Rejected |
|-------------|--------------|
| **Synchronous consensus (all 3 must agree)** | Too slow; blocks on absent agents; ~50% timeout rate makes 3-of-3 impractical. |
| **Orchestrator-only decisions** | Loses diversity benefit; orchestrator blind spots compound over time. |
| **Numeric scoring rubric (weighted criteria)** | Over-engineered for current fleet size; adds dispatch prep overhead. |
| **TIMEOUT = Against** | Would paralyze decision-making given observed 50% timeout rate. |
| **Separate dissent files** | File-system bloat; inline recording in INDEX.md is sufficient (proven by ADR-026). |

## Consequences

Positive:
- Predictable decision latency (1-6h depending on priority)
- Minority dissent preserved in record for audit trail
- Template reuse (`council-review.md`) reduces dispatch prep time
- Timeout-as-abstain prevents paralysis on partial responses

Negative:
- 6h SLA on LARGE tasks may gate fast-moving work — mitigation: URGENT priority allows 1-review proceed
- Panel diversity requires ≥2 distinct model families online — fleet availability is the binding constraint
- Procedural rejection of low-quality reviews adds orchestrator overhead per session

## Implementation Status

- [x] Pattern in active use (~15 votes in Q1)
- [ ] This ADR ratified by council vote
- [ ] `council-review.md` dispatch template verified and referenced
- [ ] ADR INDEX template updated with Council Vote Record row format
- [ ] `forge council dispatch` CLI shortcut (nice-to-have, deferred)

---

**Dispatch template:** `.forge/dispatch-templates/council-review.md`
**Index reference:** `docs/adr/INDEX.md` — Council Vote Record section
