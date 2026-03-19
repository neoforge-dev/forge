---
name: continue
description: Resume work from a handoff prompt
---

# Continue From Handoff

Resume work from a previous session.

## Startup Sequence

### 1. Load Context
- Read `docs/PROMPT.md` (handoff)
- Read `docs/PLAN.md` (current plan)
- Read `.forge/memories/INDEX.md` (shared memory)
- Check `git status` and recent commits

### 2. Verify Environment
- Run test command from PROMPT.md
- Verify build works

### 3. Identify Resume Point
From the plan, find:
- Current phase
- Current task (in progress or next pending)
- Any blockers noted

### 4. Begin Execution
Continue with the plan as a pragmatic senior engineer:
- Apply Pareto principle (20% effort, 80% value)
- TDD for business logic
- YAGNI - don't build what isn't needed
- Commit after each completed task
- Update plan status as you progress

## When to Stop
Only stop when:
1. Plan fully implemented
2. Tests passing
3. Committed
4. Plan updated

OR when encountering truly blocking issues.
