# ADR-053: Semantic Context Search — mempalace CLI Evaluation (Hold)

**Date:** 2026-04-07 (revised after council vote)
**Status:** Hold — council approved (pi 5/5, kimi2 5/5)
**Decision Makers:** pi (APPROVE), kimi2 (APPROVE)
**Inspired by:** [mempalace](https://github.com/milla-jovovich/mempalace) v3.0.0 analysis

---

## Context

FORGE currently searches accumulated context using `qmd search` (BM25 over `.md` files). This works well for keyword lookups but fails for intent-based queries like:

- "What decisions affect the auth flow?"
- "Which domain has had the most failures with Railway deploys?"
- "What have agents learned about Stripe integration?"

BM25 requires the exact terms to appear in the document. As the knowledge base grows (17 domains × 3 context files + 100+ heartbeat results + auto-memory), semantic retrieval would improve agent onboarding and reduce repeated mistakes.

**mempalace** (v3.0.0, analyzed 2026-04-07) provides:
- Hierarchical palace structure: Wings → Rooms → Halls → Closets → Drawers
- 4-layer memory stack: L0 identity (always loaded) → L1 critical facts → L2 on-demand room recall → L3 semantic search via ChromaDB
- CLI: `mempalace init / mine / search`
- MCP server (19 tools) — **NOT adoptable under FORGE's No MCP rule (CLAUDE.md)**
- 96.6% LongMemEval R@5 (zero API calls); hybrid + rerank reaches 100% on standard benchmark

### Mapping mempalace concepts to FORGE

| mempalace concept | FORGE equivalent |
|-------------------|-----------------|
| Palace | `.forge/` (entire context store) |
| Wing | Domain (e.g., `codeswiftr-com`, `forge-tryon`) |
| Room | Context type (decisions, failures, lead-context) |
| Hall / Drawer | Individual `.md` files |
| L0 identity | `docs/PROMPT-{node}.md` header |
| L1 critical facts (AAAK) | `memory/MEMORY.md` index |
| L2 room recall | `qmd search` (current) |
| L3 semantic search | ChromaDB — **not yet deployed** |

The structural concept is already a natural fit. What's missing is the semantic search layer.

### Why this is on Hold

| Concern | Detail |
|---------|--------|
| ChromaDB runtime dependency | Requires a running vector store; adds ops complexity |
| MCP server banned | mempalace's primary retrieval interface is MCP — FORGE uses CLI only |
| Current `qmd` is sufficient | At <200 documents, BM25 recall is adequate |
| Thin test suite | mempalace has 4 tests; production reliability unclear |
| No multi-user model | FORGE has 5 nodes; palace is single-user by design |
| FORGE is write-heavy | mempalace mine is read-optimized (chat export formats); FORGE's `.forge/context/` is git-tracked markdown written by orchestrators, not conversational exports |

---

## Decision

**Hold.** Do not adopt mempalace now. Re-evaluate when **all three** trigger conditions are met:

| Trigger | Current state | Threshold | Council amendment |
|---------|---------------|-----------|-----------------|
| Knowledge base size | ~278 context docs | > **500** `.md` files in `.forge/context/` + `memory/` | pi: raised from 300 to 500 (300 reached in 2-4 weeks — too soon) |
| `qmd search` recall | **Unbaselined** → measure now | < 70% agent-reported relevance on context queries | pi + kimi2: run 10 baseline queries now |
| Operator availability | 0 MRR | > $500 MRR (can afford ops overhead) | Unchanged |

### Immediate action: `qmd` relevance baseline (council amendment)

Before this ADR can be activated, establish a search quality baseline. Protocol:
1. Run 10 representative semantic queries through `qmd search` (e.g., "what decisions affect auth flow", "Railway deploy failures", "Mirrably outreach status")
2. For each query, score top-5 results: Relevant (1) / Not Relevant (0)
3. Calculate precision@5 = relevant results / 5
4. Record in `.forge/reports/qmd-search-baseline.md`
5. Re-run quarterly or when doc count crosses 500

---

## What to Adopt Now (structural pattern, zero cost)

The **L0/L1 layer structure** concept from mempalace maps directly to FORGE's existing hierarchy. No new dependencies — just naming discipline:

| Layer | Always loaded? | FORGE file | Size target |
|-------|---------------|-----------|-------------|
| L0 — Identity | Yes | `docs/PROMPT-{node}.md` header (first 10 lines) | < 100 tokens |
| L1 — Critical facts | Yes | `memory/MEMORY.md` index | < 200 tokens |
| L2 — Domain context | On-demand | `.forge/context/{domain}/lead-context.md` | < 500 tokens each |
| L3 — Deep search | On-demand | `qmd search` or future ChromaDB | Unbounded |

**Recommended change (no ADR required):** Enforce L0 token budget in `docs/PROMPT-{node}.md` — first section must be ≤100 tokens (currently unconstrained, leading to 200+ line PROMPT files that exceed context budget). This is the mempalace L0 insight applied at zero cost.

---

## If Trigger Conditions Met: Implementation Path

When conditions are met, bring ADR-053 to council with this concrete plan:

1. **Install mempalace CLI** (no MCP server): `uv add mempalace` in `harness/` or standalone venv
2. **Initialize palace**: `mempalace init .forge/palace/` — separate from git-tracked `.forge/context/`
3. **Mine existing context**: `mempalace mine .forge/context/ --mode files` + `mempalace mine ~/.claude/projects/*/memory/`
4. **Add `forge memory search` command** — wraps `mempalace search` CLI, surfaces results in forge output format
5. **Nightly re-mine patrol** — `forge patrol` triggers `mempalace mine` on changed domains
6. **Disable MCP server explicitly** — never start `mempalace.mcp_server`

**HARD CONSTRAINT:** Never expose mempalace as an MCP server. CLI-only interface. If mempalace removes the CLI in a future version, do not adopt that version.

---

## Consequences

### Positive (if activated)
- Semantic search enables intent queries across 17 domains without grep
- L0/L1 pattern reduces agent onboarding context load (critical for prya's 16GB RAM constraint)
- 96.6% LongMemEval R@5 benchmark is significantly better than BM25 for multi-hop context queries
- Local-only ChromaDB — no cloud data exposure, GDPR-safe

### Negative (if activated)
- ChromaDB runtime adds ~200MB RAM + startup time per node
- `mempalace mine` on full context corpus: unknown latency (needs benchmarking before commit)
- Thin test suite (4 tests) — FORGE team would need to own reliability, not upstream
- Context drift risk: `mempalace mine` auto-extracts insights — may surface outdated decisions if stale contexts aren't pruned first

### Neutral
- Structural L0/L1/L2/L3 naming convention can be adopted immediately with zero dependencies (see above)
- mempalace's auto-save hooks (Stop/PreCompact) overlap with ADR-052 — do not double-register

---

## Alternatives Considered

| Alternative | Decision |
|-------------|---------|
| Adopt mempalace now (full) | REJECTED — ChromaDB overhead, 4 tests, MCP server conflict |
| Adopt mempalace MCP server | REJECTED — No MCP rule (CLAUDE.md, unanimous) |
| Build custom vector search in forged | REJECTED — Premature; `qmd` BM25 is sufficient at current scale |
| OpenSearch / Elasticsearch | REJECTED — External service dependency; operational overhead |
| Continue with `qmd` BM25 only | ACCEPTED for now — revisit at trigger threshold |
