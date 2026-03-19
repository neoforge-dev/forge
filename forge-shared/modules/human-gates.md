# Human Gates

Escalate to human review before implementing changes in these areas:

## Security (CRITICAL)

- Authentication/authorization changes
- JWT secret or token handling
- Password hashing or storage
- API key management
- Webhook signature validation
- Data encryption changes

## Compliance (CRITICAL)

- **COPPA**: Any changes affecting child users (TheBrightHarbor)
- **HIPAA-lite**: Health data handling (CalmConnect)
- **GDPR**: Data retention, deletion, export
- **Payment/PCI**: Billing or credit card handling

## Architecture (HIGH)

- Database schema migrations (production)
- New external service integrations
- Breaking API contract changes
- Major dependency upgrades
- Infrastructure changes (Railway, Cloudflare)

## Business Logic (MEDIUM)

- Pricing or quota changes
- User tier/plan modifications
- Analytics event schema changes
- Email notification templates

## Domain-Specific Gates

| Domain | Additional Gates |
|--------|------------------|
| thebrightharbor-com | COPPA compliance, age-adaptive UI |
| calmconnect-io | HIPAA-lite, anonymized data |
| babybit-es | Spanish localization, pediatric safety |
| brandfocus-ai | Brand rule changes, multi-tenant isolation |

## Escalation Process

1. Document the proposed change
2. Note the gate triggered
3. Ask human for approval
4. Proceed only with explicit "approved" or "proceed"
