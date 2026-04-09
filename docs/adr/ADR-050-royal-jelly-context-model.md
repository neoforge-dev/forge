# ADR-050: Royal Jelly Context Persistence Model

**Status:** RATIFIED
**Date:** 2026-04-05
**Author:** nova (orchestrator) — expanded by fleet
**Supersedes:** TC-S158, TC-S159, TC-S162 discovery patterns

---

## Decision

Royal Jelly adopts a three-file schema with staleness SLA, cross-node git propagation, and mandatory update triggers. The model is intentionally markdown-native for grep/diff friendliness.

---

## 1. Three-File Schema

### Decision: `lead-context.md` is the state file; `decisions.md` and `failures.md` are append-only history.

**Rationale:** Collapsing state into history breaks the "current snapshot" use case. Git history is not a substitute for a queryable state file.

| File | Schema | Cadence | Lines |
|------|--------|---------|-------|
| `lead-context.md` | Structured snapshot (status, metrics, blockers) | Every handoff | <60 |
| `decisions.md` | ADR-style entries | On architectural choice | <8/entry |
| `failures.md` | Post-mortem entries | On dead-end | <8/entry |

**Schema — lead-context.md:**
```markdown
# {Domain} — Lead Context
**Last Updated:** YYYY-MM-DD by {agent}
**Sprint:** SXXX
**Status:** active | paused | orphaned | killed

## Current State
- Architecture: {one-liner}
- Metrics: {MRR, signups, coverage — all with (verified YYYY-MM-DD)}
- Active blockers: {list or "none"}

## Active Work
- {in-flight epics / tasks}

## Next Up
- {queued priorities}

## Kill History (if applicable)
- {date}: {reason} — revisit if {condition}

## References
- decisions.md, failures.md
```

**Schema — decisions.md (append-only):**
```markdown
## YYYY-MM-DD — {Title}
**Decision:** {what was chosen}
**Rationale:** {1-2 sentences why}
**Alternatives:** {what was rejected}
**Source:** {optional: dispatch or heartbeat result file}
```

**Schema — failures.md (append-only):**
```markdown
## YYYY-MM-DD — {Approach Tried}
**Goal:** {what was attempted}
**Root Cause:** {why it failed}
**Don't retry unless:** {what would change the outcome}
```

---

## 2. Staleness Detection

### Decision: Staleness is defined by `lead-context.md` age. The existing 7-day threshold is confirmed. Alert via `.forge/reports/royal-jelly-staleness.md`.

**Evidence from codebase:** The staleness report at `.forge/reports/royal-jelly-staleness.md` already generates on a 7-day threshold. As of 2026-04-05, 8 of 17 domains were stale (gaea, nova, forge, oc, prya, sati, codeswiftr-com, thebrightharbor-com). Two domains (`interview-simulator`, `voice-coach`) use legacy short codes and have missing files.

**Implementation:**

| Signal | Threshold | Action |
|--------|-----------|--------|
| `lead-context.md` age | >7 days | Flag in staleness report |
| `lead-context.md` age | >14 days | Escalate: create recovery task |
| File missing (any active domain) | N/A | Create from template immediately |
| `(verified YYYY-MM-DD)` age | >30 days | Flag metrics as untrusted |

**Patrol:** `royal-jelly-staleness` runs via daemon. Alert target: next `/continue` on that node.

---

## 3. Cross-Node Sync

### Decision: Context propagates via `git pull`. No custom sync layer. All nodes share the same `.forge/context/` tree.

**Evidence from codebase:** `royal-jelly.md` explicitly states "All nodes share the same repo — Royal Jelly propagates on `git pull`." Domain ownership (per `config/domains.yaml`) means only the owning node's orchestrator writes; others read.

**Protocol:**
1. On session start: `git pull` + read `lead-context.md` for assigned domain
2. On session end: write `lead-context.md` + append `decisions.md`/`failures.md` as needed + `git commit && git push`
3. Remote nodes: pull before reading, push after writing

**Conflict resolution:** Last-write-wins by commit timestamp. If two nodes write the same domain simultaneously (ownership violation), the push that arrives second triggers a rebase conflict that the owning node must resolve manually.

**Cross-domain decisions:** Live in **both** domains with a cross-ref:
```
**Cross-ref:** `.forge/context/{other-domain}/decisions.md#YYYY-MM-DD`
```

---

## 4. Update Triggers

### Decision: `lead-context.md` update is mandatory on every session handoff. Decisions and failures update on occurrence.

| Trigger | File | Actor |
|---------|------|-------|
| Session handoff | `lead-context.md` | Owning orchestrator |
| Architectural choice | `decisions.md` | Any agent on domain |
| Dead-end | `failures.md` | Any agent on domain |
| Heartbeat research result (within 48h) | `decisions.md` | Owning orchestrator |

**Evidence from codebase:** `gaea/decisions.json` coexists with `gaea/decisions.md` — the JSON is a machine-readable index. Both should be kept in sync; JSON is optional, markdown is canonical.

---

## 5. Failure Recording Patterns

### Decision: Good failures include root cause + retry guardrail. Bad failures omit both.

**Good example (from `forge/failures.md` S164):**
```markdown
## 2026-04-04: CLI docs vs code drift
**Symptom:** Agents try commands documented in INFRASTRUCTURE_MAP.md that don't exist in forge binary.
**Root Cause:** INFRASTRUCTURE_MAP.md documents aspirational Dark Factory layer never shipped to binary.
**Don't retry unless:** The command appears in `forge --help` output.
```

**Bad example (hypothetical):**
```markdown
## Tried Webhooks — Failed
Webhook setup didn't work.
```

| Field | Good | Bad |
|-------|------|-----|
| Symptom | Precise, reproducible | Vague |
| Root Cause | Named mechanism | Absent |
| Retry Guardrail | Specific condition | Absent |
| Source File | Optional | Omitted |

---

## 6. Open Questions — Resolved

### Q1: Should `decisions.md` entries become proper ADRs?

**Answer:** Yes, when the decision has **cross-domain impact** or **requires human approval**. Use the ADR number as the cross-ref anchor. Routine domain-local decisions stay in `decisions.md`.

**Evidence:** `forge/decisions.md` S164 entry on "hard-ban code via dispatch" affects all domains → candidate for ADR. Domain-specific decisions like "Interview scheduling timeline" (forge S164) → stay in `decisions.md`.

### Q2: Cross-domain decisions — where do they live?

**Answer:** In **both** domains, with bidirectional cross-ref. The primary lives in the domain that initiated the decision; the secondary is a stub with a cross-ref link.

**Evidence:** `forge/decisions.md` S162 "Smoke test protocol" affected IS, VC, SF — entries exist only in `forge/decisions.md` with "Cross-domain: affects IS/VC/SF" noted. This ADR formalizes that pattern: add stub entries to affected domains.

### Q3: Automatic "verified" date updates via patrol?

**Answer:** No. Patrols detect staleness; they do not update metrics. Metric accuracy requires running actual commands (tests, deploys, queries) — only a live agent can verify. Patrol flags the staleness; agent refreshes.

**Evidence:** `forge/lead-context.md` requires `(verified YYYY-MM-DD)` on all numeric claims (ADR-046 Proposal 3). The staleness report flags stale `lead-context.md`; it does not attempt to refresh it.

---

## Alternatives Considered

| Alt | Why Rejected |
|-----|--------------|
| SQLite-backed context | Overkill for <20 domains; markdown is grep/diff-friendly |
| YAML frontmatter only | Loses prose in decisions/failures |
| Single file per domain | Conflates state (snapshot) with history (append-only) |
| Auto-refresh patrol | Would generate unverified stale data |

---

## Consequences

**Positive:**
- Predictable schema across 17 domains
- Grep-friendly; auditable in git history
- Decisions prevent repeated dead-ends (e.g., CLI drift failure entry)

**Negative:**
- Manual discipline required; schema drift likely
- Append-only files grow unbounded → year-based archiving (`.forge/context/archive/`)
- Two domains still use legacy short codes (`interview-simulator`, `voice-coach`) → migrate to canonical domain names

---

## Implementation Checklist

| Item | Status |
|------|--------|
| Three-file pattern in active use | ✅ |
| Staleness report (`.forge/reports/royal-jelly-staleness.md`) | ✅ |
| Domain ownership in `config/domains.yaml` | ✅ |
| This ADR ratified | ✅ |
| Data accuracy `(verified YYYY-MM-DD)` linter | 🔲 |
| Schema-check patrol | 🔲 |
| Migrate `interview-simulator` / `voice-coach` to canonical codes | 🔲 |
| Cross-domain decisions: stub entries in affected domains | 🔲 |
