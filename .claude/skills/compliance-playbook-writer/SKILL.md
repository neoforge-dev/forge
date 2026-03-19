---
name: compliance-playbook-writer
description: Generate and maintain policy documents, SOPs, and compliance checklists for regulated MVP domains using existing research.
auto_execute: true
disable-model-invocation: false
allowed-tools: [Read, Write, Grep]
---

# Compliance Playbook Writer

## When to Use
- Drafting or updating policy docs for domains with regulatory touchpoints (babybit.es, calmconnect.io, codeswiftr.com, future regulated products).  
- Translating research findings or legal feedback into actionable playbooks.

## Reference Material
- `babybit-es/allergen-coach/docs/policies.md` (safety + GDPR).  
- `calmconnect-io/burnout-pulse/docs/moderator-playbook.md` (anonymity + crisis).  
- `codeswiftr-com/tech-diligence-snapshot/docs/reporting-guide.md` (investor deliverables).  
- Research responses stored in `{domain}/{idea}/research/response.json`.  
- `docs/20-reference-index.md` for additional governance docs.

## Workflow
1. **Gather Inputs**
   - Review latest research summaries and policy requirements in domain docs.  
   - Identify gaps flagged in `docs/PLAN.md` or `docs/active-context.md`.

2. **Draft Structure**
   - Outline sections: Purpose, Scope, Procedures, Checklists, Incident Response.  
   - Align terminology with existing playbooks to stay consistent across portfolio.

3. **Populate Content**
   - Pull authoritative guidance from research or regulations; cite sources inline.  
   - Convert requirements into actionable steps (e.g., onboarding copy, escalation flow).  
   - Add checklists with `[ ]` placeholders for teams to track completion.

4. **Validation**
   - Ensure instructions map to product features (onboarding UX, backend logging, etc.).  
   - Highlight any unresolved legal questions or dependencies.  
   - If legal review needed, flag in `docs/active-context.md` and Level 0 digest.

5. **Propagation**
   - Save document under `{domain}/{idea}/docs/` with clear filename (`policies.md`, `moderator-playbook.md`, etc.).  
   - Update domain snapshot + Level 0 digest if policy adds new obligations.  
   - Log summary in `docs/progress.md`.

## Output Checklist
- [ ] Policy/playbook file created or updated with clear steps.  
- [ ] Checklists + escalation procedures defined.  
- [ ] References + dependencies recorded.  
- [ ] Documentation hierarchy updated.
