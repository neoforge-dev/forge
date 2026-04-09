# Human Gates

<!-- Last Updated: 2026-04-04 (S194) -->

Escalate to human review before implementing changes in these areas.
Agents STOP and document the gate — never proceed without explicit human approval.

---

## Routing Convention (Council S194)

**All human gates route through nova.** Nova is the human interface node — Bogdan works directly with the nova lead for deploys, configs, App Store, Stripe, E2E testing, and device testing.

**When any node discovers a human gate:**
1. Document the gate in your `PROMPT-{node}.md`
2. Forward to nova: `forge lead send --to-node nova --summary "GATE-ID: description (est. time)" --durable`
3. Nova's lead batches gates and executes with Bogdan

**Source of truth:** `config/domains.yaml` → `fleet.human_gate_node: nova`

---

## Revenue Sprint Gates (April 2026 — Active)

These are the current blocking gates for the revenue sprint. All require human action.

| Gate ID | Domain | Description | Effort |
|---------|--------|-------------|--------|
| **GATE-STRIPE-TEAM** | codeswiftr-com (IS) | Create Team Stripe products for Interview Simulator | ~30 min |
| **GATE-VC** | brandfocus-ai | `railway up` + API keys for Voice Coach deploy | ~15 min |
| ~~GATE-SF~~ | ~~thebrightharbor-com~~ | ~~Add Stripe keys to Railway for Study Flow~~ | ~~KILLED S159~~ |
| **GATE-C** | babybit-es, calmconnect-io, leanvibe-ai | App Store Connect setup + GitHub secrets | ~60 min |
| **GATE-CONTENT** | all | LinkedIn posts / launch content publishing | Ongoing |

> **Railway prep (Mirrably + Voice Coach, P1):** `docs/runbooks/RAILWAY_S191_HUMAN_GATES_PREP.md`

> Source of truth: `config/domains.yaml` → `human_actions_needed` per domain.

---

## Security (CRITICAL)

Human review required before any changes to:

- Authentication / authorization logic
- JWT secret or token handling
- Password hashing or storage
- API key management
- Webhook signature validation
- Data encryption changes

---

## Compliance (CRITICAL)

| Regulation | Scope | Gate |
|------------|-------|------|
| **COPPA** | thebrightharbor-com, leanvibe-ai (kiddo-rewards) | Any change affecting child users |
| **HIPAA-lite** | calmconnect-io | Health data handling, storage, access logs |
| **GDPR** | All EU-facing products | Data retention, deletion, export features |
| **Payment/PCI** | All Stripe integrations | Billing, card handling, webhook endpoints |

---

## Architecture (HIGH)

- Database schema migrations in production
- New external service integrations (new third-party APIs)
- Breaking API contract changes
- Major dependency upgrades (major version bumps)
- Infrastructure changes (Railway, Cloudflare, Vercel)

---

## Business Logic (MEDIUM)

- Pricing or quota changes
- User tier / plan modifications
- Analytics event schema changes (`docs/POSTHOG_EVENTS.md`)
- Email notification templates

---

## Domain-Specific Gates

| Domain | Additional Gates |
|--------|------------------|
| thebrightharbor-com | COPPA compliance review, age-adaptive UI changes |
| calmconnect-io | HIPAA-lite data handling, anonymized data policies |
| babybit-es | Spanish localization accuracy, pediatric safety claims |
| brandfocus-ai | Brand rule enforcement changes, multi-tenant isolation |
| leanvibe-ai | COPPA (kiddo-rewards), health claim accuracy |

---

## Escalation Process

1. Document the proposed change with context
2. Note which gate is triggered
3. Write a clear ask: what decision is needed from the human
4. Wait for explicit "approved" or "proceed" before continuing
5. Log the approval in the relevant domain's `.forge/context/{domain}/decisions.md`

---

## Resolved / Cleared Gates

| Gate | Resolution | Date |
|------|------------|------|
| ~~GATE-SF~~ | Study Flow killed — council vote 4-0 (FSRS commoditized) | 2026-03-27 |

Gates are cleared when `stripe_live: true` and `deploy_status: live` in `config/domains.yaml`.
Active gates: GATE-VC, GATE-C, GATE-CONTENT.
