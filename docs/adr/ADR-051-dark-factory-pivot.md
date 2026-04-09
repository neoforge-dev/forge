# ADR-051: Dark Factory Pivot

**Status:** Proposed  
**Date:** 2026-04-05  
**Author:** FORGE Council  
**Context:** S195 P5 Dark Factory Pivot Mandate

---

## Context

Dark Factory has been in "content generation mode":
- 1,457 blog posts generated
- 0 users reading them
- $0 revenue attributed
- 0% distribution verification

## Decision

Rewrite `SAFE_PATTERNS` to prioritize deploy-support over content-generation.

### SAFE_PATTERNS Rewrite

**Remove:**
```
blog|seo|content|newsletter|linkedin|post|article|write|draft
```

**Add:**
```
smoke.test|health.check|deploy.verify|regression|uptime|e2e|integration.test
```

### Queue Priority

1. Deploy verification (P0)
2. Analytics setup (P1)
3. Email capture (P1)
4. Outreach prep (P2)
5. Content generation (P3, explicit flag only)

### Content Exception

Content allowed ONLY with:
- `--content` flag
- 100+ users verified
- <10% daily quota
- Orchestrator request

---

## Fleet Boundaries

**CAN autonomously dispatch:**
- Smoke tests
- Health checks
- Deploy verification
- Integration tests

**CANNOT autonomously dispatch:**
- Blog posts
- LinkedIn content
- Newsletters
- SEO optimization

---

## Consequences

### Positive
- Factory output tied to deploy readiness
- Reduced coordination overhead

### Negative
- Content backlog may stall
- Requires explicit orchestrator approval

---

## Success Criteria

- [ ] SAFE_PATTERNS rewritten
- [ ] Content tasks require `--content` flag
- [ ] Content:Deploy ratio improves to 1:10
- [ ] Deploy verification is primary output

---

COMPLETE
