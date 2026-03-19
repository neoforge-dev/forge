---
name: execute
description: Implement plan with pragmatic TDD
---

# Execute

Implement the plan systematically.

## Execution Loop

```
For each task:
1. Read task from plan
2. Assess: needs TDD? (business logic = yes, infra = no)
3. If TDD: write failing test first
4. Implement
5. Run ALL tests
6. If pass: commit, mark done, next task
7. If fail: fix and retry
```

## TDD Decision

| Scenario | TDD? |
|----------|------|
| Business logic | Yes |
| API endpoint | Yes |
| Bug fix | Yes |
| Database migration | No |
| UI styling | No |
| Prototype | No |

## At Checkpoints

1. Run full test suite
2. Verify checkpoint criteria
3. Review for regressions
4. Commit checkpoint state

## Validation (End of Execution)

Before marking complete:
- [ ] All tests passing
- [ ] Success criteria met
- [ ] No regressions
- [ ] Code reviewed (or review pending)
