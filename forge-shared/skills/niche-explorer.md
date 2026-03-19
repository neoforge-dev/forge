---
name: niche-explorer
description: Structured domain exploration that produces actionable market analysis and MVP recommendations
---


# Niche Explorer Skill

Performs structured market exploration within a FORGE domain, analyzing existing projects, identifying gaps, and recommending MVPs with data-driven justification.

## When to Use

- Starting work in a new domain
- Looking for the next MVP to build
- Validating market opportunities
- Filling gaps in domain coverage

## Prerequisites

- Domain CLAUDE.md exists at `{domain}/CLAUDE.md`
- Access to web search for market research
- Domain context from living docs

## Inputs

```yaml
domain: babybit-es
focus: baby nutrition
constraints:
  - COPPA compliant
  - Parents as primary users
```

## Workflow

### Step 1: Load Domain Context

Read the domain's CLAUDE.md and understand:
- Target market
- Existing projects and their status
- Domain-specific compliance requirements
- Technology stack preferences

### Step 2: Analyze Existing Projects

For each project in the domain:
- Current status (complete, in-progress, docs-only)
- Coverage area (what problem it solves)
- Gaps (what it doesn't cover)

### Step 3: Market Research

Conduct competitive analysis:
- Identify 3-5 top competitors
- Analyze their feature sets
- Find underserved segments
- Estimate market sizes

### Step 4: Gap Analysis

Compare existing projects to market needs:
- What problems are unsolved?
- What segments are underserved?
- What adjacent opportunities exist?
- What compliance gaps exist?

### Step 5: Generate Recommendations

Rank opportunities by:
- **Market Size**: Large (1M+ TAM), Medium (100K-1M), Small (<100K)
- **Competition**: Low (0-2 competitors), Medium (3-5), High (6+)
- **Fit Score**: 0.0-1.0 based on domain expertise, tech stack match, compliance readiness

### Step 6: Output Structured Report

Generate exploration report following this template:

```markdown
# Niche Exploration: {domain}

**Date:** {date}
**Focus:** {focus_area}

## Executive Summary

1-2 paragraph summary of findings and top recommendation.

## Market Analysis

### Target Segment
- Primary users: [description]
- Pain points: [bullet list]
- Current solutions: [what exists]

### Market Size
- TAM (Total Addressable Market): [estimate]
- SAM (Serviceable Addressable Market): [estimate]
- SOM (Serviceable Obtainable Market): [estimate]

## Existing Projects

| Project | Status | Coverage | Gaps |
|---------|--------|----------|------|
| project-1 | 80% complete | Feature A, B | Missing C, D |
| project-2 | Docs only | N/A | Not started |

## Competitive Landscape

| Competitor | Strengths | Weaknesses | Market Share |
|------------|-----------|------------|--------------|
| Competitor A | Feature X, Y | Poor UX | 35% |
| Competitor B | Great design | Missing Z | 20% |

## Gap Analysis

### Opportunity 1: {Name}
- **Problem**: [What pain point does this solve?]
- **Solution**: [High-level approach]
- **Market Size**: Large/Medium/Small
- **Competition**: Low/Medium/High
- **Fit Score**: 0.85
- **Why Now**: [Market timing, trends]

### Opportunity 2: {Name}
[Same format]

## Recommended MVP

**{MVP Name}** - {One-line description}

### Problem Statement
[Detailed description of the problem this solves]

### Target Users
- Primary: [user persona]
- Secondary: [user persona]

### Solution Overview
[How this MVP solves the problem]

### Differentiator
[What makes this unique vs competitors]

### Success Metrics
- Metric 1: [e.g., 100 active users in 30 days]
- Metric 2: [e.g., 70% user retention]
- Metric 3: [e.g., 4.5+ app store rating]

### Tech Stack Fit
- Backend: [FastAPI / Go / Node.js]
- Frontend: [React PWA / Lit / HTMX]
- Auth: [JWT / OAuth2]
- Database: [PostgreSQL / MongoDB]
- Compliance: [COPPA / HIPAA / GDPR requirements]

### Estimated Effort
- Complexity: Low/Medium/High
- Timeline: [X weeks]
- P0 Features: [count]

## Next Steps

1. Run `/mvp-spec-writer` to generate features.json
2. Create features.json with 5-7 P0 features
3. Run `/forge loop run -d DOMAIN -p PROJECT` to start building
4. Track in Command Center

