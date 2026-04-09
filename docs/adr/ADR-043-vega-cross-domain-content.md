# ADR-043: Vega Cross-Domain Content Pipeline

**Date:** 2026-03-22
**Status:** Proposed
**Decision Makers:** vega orchestrator (council format, pending ratification)

## Context

Vega node (16GB, auxiliary) owns brandfocus-ai. As of 2026-03-22, all agent-doable technical debt on brandfocus-ai is resolved: 1216+ tests, 94% coverage, 8 blog posts, all deploy blockers fixed. Remaining blockers are human gates only (railway deploy, API keys, user interviews).

Meanwhile, a portfolio-wide audit reveals **~25 blog post URLs referenced in landing page CTAs across 6 active domains** that have no corresponding `.md` files. This creates:
- Broken links on production landing pages (404s)
- SEO signal loss (URLs in navigation but no content)
- Conversion leakage (CTAs point to nonexistent pages)

The question: what should vega do next?

## Options Evaluated

### A) Vega shifts to cross-domain blog content creation
- Write ~25 missing blog posts for codeswiftr, adguild, babybit, calmconnect, leanvibe-ai, neoforge, thebrightharbor
- Low risk: blog posts are `.md` files, not source code
- High impact: fixes broken CTAs across all active landing pages
- No domain ownership conflict: content assistance explicitly allowed

### B) Vega assists other nodes' infrastructure
- Help with deploy guides, cross-domain testing
- Limited value: other nodes' blockers are also human gates (Stripe, Railway)
- Would require coordination overhead without clear deliverable

### C) Vega focuses on brandfocus-ai SEO/content expansion
- More blog posts for Voice Coach (already has 8)
- Diminishing returns — domain needs market validation, not more content

### D) Vega goes idle until human gates clear
- No wasted effort
- But 16GB node sitting idle while portfolio has broken CTAs

## Decision

**Option A: Cross-domain blog content creation.**

Vega will write missing blog posts referenced in landing page CTAs across all active domains, prioritized by deploy-readiness:

| Priority | Domain | Owner | Posts Needed | Rationale |
|----------|--------|-------|--------------|-----------|
| P0 | codeswiftr-com | sati | 6 | Deploy-ready, highest revenue target |
| P0 | thebrightharbor-com | gaea | 3 | Deploy-ready |
| P1 | adguild-io | gaea | 3 | Active |
| P1 | babybit-es | nova | 5 | Near-ready |
| P1 | calmconnect-io | nova | 3 | Active |
| P2 | leanvibe-ai | gaea | 3 | Active |
| P2 | neoforge-dev | prya | 2 | Active |

**Execution plan:**
1. Create `docs/blog/{domain}/` directories for each domain
2. Write posts matching the slugs referenced in each domain's JSON config
3. Run `build-blog-manifest.mjs` to regenerate manifest
4. Validate no broken CTA links remain

**Constraints:**
- Blog posts only — no source code changes
- Match each domain's voice and audience
- Owner nodes retain final review authority
- Vega commits to main (orchestrator privilege) and pushes

## Consequences

### Positive
- Fixes ~25 broken CTA links across 6 active domains
- Unblocks landing page deploys for all domains simultaneously
- SEO content pipeline established for portfolio
- Vega node stays productive while brandfocus-ai awaits human gates

### Negative
- Cross-domain content may not perfectly match owner's vision (mitigated: owners can edit post-merge)
- Vega context budget consumed on non-owned domains

### Neutral
- Sets precedent for cross-domain content assistance
- Blog post quality is MVP-grade, not publication-polished
