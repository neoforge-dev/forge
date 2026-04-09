# Product Registry

<!-- Last Updated: 2026-03-22 -->
<!-- Canonical domain config: config/domains.yaml -->
<!-- TC-TAXONOMY-S150: renamed from project-registry.md (ratified 4-0) -->

## Portfolio Summary

**95 products across 11 domains**

| Stat | Value |
|------|-------|
| Total domains | 11 |
| Active domains | 8 |
| Paused/inactive domains | 3 (mumchef-io, leanvibe-dev, bogdanveliscu-com) |
| Submodules | 55+ |
| Active revenue products | IS (Interview Simulator), VC (Voice Coach) |

> Canonical domain config: `config/domains.yaml` (source of truth for ownership, deploy status, human gates)

---

## Priority Tiers (March 2026)

| Tier | Focus | Allocation |
|------|-------|------------|
| 1 - Revenue | Live products gating on Stripe/deploy | 60% |
| 2 - Authority | B2B/thought leadership products | 25% |
| 3 - Consumer/Games | Entertainment products | 10% |
| 4 - COPPA/Compliance | Kids apps requiring compliance | 5% |
| 5 - Development | Early stage products | Maintenance |

---

## Tier 1: Revenue Focus

| Product | Domain | Owner Node | Status | Gate |
|---------|--------|-----------|--------|------|
| Interview Simulator (IS) | codeswiftr-com | sati | Deploy-ready | GATE-STRIPE-TEAM |
| Voice Coach (VC) | brandfocus-ai | vega | Deploy-ready | GATE-VC |
| ~~Study Flow (SF)~~ | ~~thebrightharbor-com~~ | ~~gaea~~ | ~~KILLED S159~~ | ~~Product discontinued~~ |
| BabyBites Mobile | babybit-es | nova | Near-ready | GATE-C |

---

## Tier 2: Authority Building

| Product | Domain | Status |
|---------|--------|--------|
| Code Atlas | codeswiftr-com | Active dev |
| GraphRAG Patterns | neoforge-dev | Active dev |
| Technical Debt Analyzer | leanvibe-dev | Docs complete |
| Strategic Tech Newsletter | codeswiftr-com | Production |
| Tech Diligence Snapshot | codeswiftr-com | Active dev |
| Campaign Intelligence | adguild-io | Active dev |
| AdGuild Platform | adguild-io | Building |

---

## Tier 3: Consumer/Games

| Product | Domain | Status |
|---------|--------|--------|
| Septica | bogdanveliscu-com | Production ready |
| Sedma | neoforge-dev | Production ready |
| Rummy Rivals | leanvibe-dev | Development |
| Clan Wars | leanvibe-dev | Development |

---

## Tier 4: Compliance-Gated

| Product | Domain | Compliance | Gate |
|---------|--------|------------|------|
| Kiddo Rewards | leanvibe-ai | COPPA | App Store Connect |
| ~~Study Flow~~ | ~~thebrightharbor-com~~ | ~~COPPA~~ | ~~KILLED S159~~ |
| Code Ship | thebrightharbor-com | COPPA | GATE-C |
| Story Grow | thebrightharbor-com | COPPA | GATE-C |
| Calm Connect iOS | calmconnect-io | HIPAA-lite | GATE-C + therapist interviews |
| Math Sprinter | thebrightharbor-com | COPPA | Development |
| Fluency Sprinter | thebrightharbor-com | COPPA | Development |
| Agency Quest | thebrightharbor-com | COPPA | Development |

---

## Tier 5: Development / Early Stage

| Product | Domain | Status |
|---------|--------|--------|
| Allergen Coach | babybit-es | Active |
| Calm Connect API | calmconnect-io | Development |
| Burnout Pulse | calmconnect-io | Docs complete |
| Dopamine Detox | calmconnect-io | Docs complete |
| Screen Time Coach | calmconnect-io | Docs complete |
| Ad Asset Finder | adguild-io | Docs complete |
| Bid Orchestrator | adguild-io | Development |
| Brand Brain | brandfocus-ai | Active dev |
| PKM.ai | brandfocus-ai | Beta ready |
| BrandFocus Platform | brandfocus-ai | Validation |
| Orchestrator CLI | neoforge-dev | Production ready |
| Fast Conduit | neoforge-dev | Development |
| RAG Cost Optimizer | neoforge-dev | Development |
| Synapse GraphRAG | neoforge-dev | Development |
| Agentic SwiftUI Boilerplate | neoforge-dev | Template |
| Graph RAG Mastery | codeswiftr-com | MVP complete |
| Interview Insights | codeswiftr-com | Development |
| Startup Simulator | codeswiftr-com | Development |
| Allergen Guardian | mumchef-io | Development (paused) |
| Family Meal Planner | mumchef-io | Docs complete (paused) |
| Calorie Coach | leanvibe-ai | Building |

---

## Active Domain Summary

| Domain | Node Owner | Active Products | Deploy Status |
|--------|-----------|-----------------|---------------|
| codeswiftr-com | sati | interview-simulator, code-atlas | deploy-ready |
| brandfocus-ai | vega | voice-coach | deploy-ready |
| thebrightharbor-com | gaea | study-flow | deploy-ready |
| babybit-es | nova | babybites-mobile, allergen-coach | near-ready |
| calmconnect-io | nova | calm-connect-ios, calm-connect-api | building |
| adguild-io | gaea | adguild-platform, campaign-intelligence | building |
| neoforge-dev | prya | graphrag-patterns, code-atlas | building |
| leanvibe-ai | gaea | kiddo-rewards, calorie-coach | building |
| leanvibe-dev | — | technical-debt-analyzer | paused |
| mumchef-io | — | — | paused |
| bogdanveliscu-com | — | — | inactive |

---

## Shared Resources

| Resource | Location | Used By |
|----------|----------|---------|
| FORGE Shared | `forge-shared/` | LLM client, retry/fallback patterns, modules |
| Marketing Template | `marketing-template/` | All landing pages |
| Marketing API | `marketing-api/` | Content automation API |
| Auth Patterns | `docs/templates/` | All backends |
| PostHog Events | `docs/POSTHOG_EVENTS.md` | All products |
| iOS Harness | `harness/` | iOS build/test/screenshot automation |

---

## Quick Navigation

```bash
# Tier 1 — Revenue (prioritize these)
cd codeswiftr-com/interview-simulator/backend       # IS — GATE-STRIPE-TEAM
cd brandfocus-ai/voice-coach/app/backend            # VC — GATE-VC
cd thebrightharbor-com/study-flow/backend           # SF — GATE-SF
cd babybit-es/babybites-mobile                      # BB — GATE-C

# Tier 2 — Authority
cd codeswiftr-com/code-atlas
cd neoforge-dev/graphrag-patterns/backend
cd codeswiftr-com/strategic-tech-newsletter
cd adguild-io/campaign-intelligence/backend

# Domain configs
cat config/domains.yaml                             # Canonical source of truth
forge domain list                                   # CLI view
```

---

_Note: `config/domains.yaml` is the canonical source of truth for domain ownership,
deploy status, and human gates. This registry is a human-readable summary.
The full portfolio digest is at `docs/00-portfolio-digest.md`._
