# ADR-044: Fix Broken Navigation Anchors Portfolio-Wide

**Date:** 2026-03-22
**Status:** Accepted
**Decision Makers:** vega orchestrator

## Context

Audit of all 11 domain JSON configs reveals ~40 broken `href="#..."` anchors pointing to section IDs that don't exist on the page. The landing-app renders sections with specific IDs (`how-it-works`, `modules`, `pricing`, `trust`, `lead`, `faq`, `testimonials`, `blog`). Many configs reference non-existent IDs like `#features`, `#steps`, `#about`, `#demo`, etc.

This means nav links, CTAs, and footer links scroll to nothing — users click and nothing happens.

## Decision

Fix all broken anchors using these mappings:

| Broken Anchor | Correct Target | Rationale |
|---|---|---|
| `#features` | `#modules` | modules-section.ts renders features |
| `#steps` | `#how-it-works` | how-it-works.ts has steps |
| `#product` | `#modules` | same as features |
| `#howItWorks` | `#how-it-works` | kebab-case, not camelCase |
| `#how` | `#how-it-works` | truncated version |
| `#demo` | `#lead` | lead form is the demo CTA |
| `#about` | `/` | footer link, point to home |
| `#integrations` | `#modules` | integrations are a module |
| `#guides` | `#blog` | guides = blog posts |
| `#routines` | `#modules` | routines are product modules |
| `#caregivers` | `#trust` | trust section has resources |
| `#brand` | `#modules` | brand guidelines = module |
| `#recipes` | `#blog` | recipes = content |

Anchors unique to paused domains (leanvibe-dev `#scanner`, `#manual`, `#pack`, `#process`; bogdanveliscu `#rules`, `#app-store`) will also be fixed for consistency.

## Consequences

### Positive
- All navigation actually works across 11 domains
- CTA conversion flow unbroken
- No more dead-end clicks

### Negative
- Some mappings are approximate (e.g., `#about` → `/`)
