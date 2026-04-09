# Lane Policy Matrix

Date: 2026-02-22
Plan Reference: `docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md` (DF-0005)
Code Reference: `harness/forge_harness/webhook_server/models/lane_policy.py`

## Purpose

This matrix defines which completion lane applies to every combination of `task_type` and
`risk_tier` in the FORGE Dark Factory model. All autonomous agent work is routed through
this policy before execution. No agent may bypass the matrix without an audited override.

---

## 1. Lane Definitions

| Lane | ID | Description |
|---|---|---|
| `autonomous` | A | Agent completes and evaluator verifies. No human touch required. Task moves to `completed` after evaluator passes. |
| `human-review` | H | Agent completes and evaluator runs, but a human must explicitly approve before the task is marked `done`. Approval waits up to 24 hours before auto-escalating. |
| `blocked` | B | Cannot be executed by agents. Requires direct human action. Agent may prepare artifacts and document findings, but must not apply changes. |

---

## 2. Risk Tier Criteria

| Tier | Definition |
|---|---|
| `low` | No production impact. No auth, security, or data mutations. Fully reversible. All changes are covered by automated tests. Examples: adding a test, fixing a typo in docs, generating marketing copy. |
| `medium` | Affects existing behavior but has test coverage and does not touch security or payments. Changes are staged or reversible. Examples: refactoring an existing function, fixing a bug in a covered code path, updating a dependency minor version. |
| `high` | Touches auth, payments, PII, or production behavior. Requires additional review. Changes are either hard to reverse or affect user-visible behavior. Examples: new API endpoint with auth, updating JWT logic, dependency major version upgrade. |
| `critical` | Irreversible or directly production-impacting. A mistake requires manual incident response. Examples: production deployment, database migration, secret rotation, infrastructure changes. |

---

## 3. Policy Matrix

Rows = `task_type`. Columns = `risk_tier`. Cells = lane assignment.

| task_type | low | medium | high | critical |
|---|---|---|---|---|
| `test-writing` | A | A | H | H |
| `docs-update` | A | A | H | H |
| `content-generation` | A | A | H | B |
| `code-refactor` | A | H | H | B |
| `api-endpoint` | A | H | H | B |
| `security-change` | H | H | B | B |
| `deployment` | H | H | B | B |
| `database-migration` | H | H | B | B |
| `dependency-update` | A | H | H | B |
| `config-change` | A | H | H | B |
| `new-feature` | A | H | H | B |
| `bug-fix` | A | H | H | B |

Legend: **A** = autonomous, **H** = human-review, **B** = blocked

### Key Policy Invariants

1. Security changes are never autonomous at `high` or `critical` risk.
2. Deployments are always `blocked` at `critical` risk.
3. Database migrations are `blocked` at `high` and `critical` risk.
4. No task type is `autonomous` at `critical` risk.
5. All `blocked` tasks generate a human-actionable artifact (summary doc or approval request) so the human has full context before acting.

---

## 4. Override Protocol

### Who Can Override

| Requester | Allowed Overrides | Prohibited |
|---|---|---|
| `backend-lead` agent | `low` → `medium` lane assignment only | Cannot override `blocked` |
| `docs-lead` agent | `docs-update` lane relaxation only | Cannot override security/deploy lanes |
| Human operator (CLI `forge override`) | Any lane, any task type | Must provide reason and ticket reference |
| Orchestrator (`forge:${HOSTNAME}`) | Emergency `human-review` → `autonomous` for non-security types | Must write audit entry before execution |

### Audit Requirements

Every override must produce an audit entry with:
- `task_id`: The task being overridden
- `original_lane`: The lane assigned by the matrix
- `override_lane`: The lane after override
- `requester`: Agent ID or human operator username
- `reason`: Free-text justification (minimum 20 characters)
- `timestamp`: ISO 8601 UTC
- `ticket_ref`: Optional but strongly recommended for production overrides

Audit entries are written to `.forge/audit/lane-overrides.jsonl` and are immutable (append-only).

### Escalation Path

```
Agent requests override
  └─ Is task_type security/deployment/database-migration?
       ├─ YES → Block. Route to human operator via approval queue.
       └─ NO → Is risk_tier critical?
                ├─ YES → Block. Route to human operator.
                └─ NO → Log audit entry. Apply override. Proceed.
```

---

## 5. Examples by Lane

### Autonomous Examples

| Scenario | task_type | risk_tier | Lane |
|---|---|---|---|
| Add unit tests for a new utility module | `test-writing` | low | autonomous |
| Fix a spelling error in API docs | `docs-update` | low | autonomous |
| Generate blog post for brandfocus-ai | `content-generation` | low | autonomous |
| Rename a private helper function | `code-refactor` | low | autonomous |
| Fix a bug with full test coverage | `bug-fix` | low | autonomous |
| Add a minor version dependency bump | `dependency-update` | low | autonomous |

### Human-Review Examples

| Scenario | task_type | risk_tier | Lane |
|---|---|---|---|
| Refactor an existing public API handler | `code-refactor` | medium | human-review |
| Add a new REST endpoint with JWT auth | `api-endpoint` | high | human-review |
| Update a dependency to a new major version | `dependency-update` | high | human-review |
| Implement a new Interview Simulator feature | `new-feature` | medium | human-review |
| Update a CORS configuration | `config-change` | medium | human-review |
| Add password reset flow | `security-change` | medium | human-review |
| Run a staging deployment | `deployment` | medium | human-review |

### Blocked Examples

| Scenario | task_type | risk_tier | Lane |
|---|---|---|---|
| Deploy voice-coach to Railway production | `deployment` | critical | blocked |
| Run a production database migration | `database-migration` | high | blocked |
| Rotate JWT signing secret | `security-change` | critical | blocked |
| Upgrade production infrastructure on Railway | `config-change` | critical | blocked |
| Rebuild the auth middleware from scratch | `security-change` | high | blocked |

---

## 6. Integration with Task Lifecycle

The lane is resolved at task creation time by calling `get_lane(task_type, risk_tier)`.
The resolved lane is stored on the task record and enforced by the evaluator pipeline:

- `autonomous` tasks: evaluator must pass before `completed` state is reached.
- `human-review` tasks: evaluator must pass AND an approval record must be present before `completed`.
- `blocked` tasks: task is created in `blocked` state; an approval request is generated automatically; agent is not dispatched.

See also:
- `harness/forge_harness/webhook_server/models/lane_policy.py` (policy as code)
- `forge-shared/modules/human-gates.md` (human gate trigger catalog)
- `docs/plans/FORGE_DARK_FACTORY_TRANSITION_PLAN_2026-02-22.md` (DF-0005, DF-1002)
