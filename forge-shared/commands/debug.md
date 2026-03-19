---
name: debug
description: Investigate and fix bugs systematically
---

# Debug

Investigate and fix bugs. For complex issues, use a debug specialist agent.

## Complex Bug Indicators
- Intermittent/flaky failures
- Race conditions or timing issues
- Production-only bugs
- Memory leaks or resource exhaustion
- Issues spanning multiple systems

## Workflow

### 1. Document the Symptom
- What exactly is the observed behavior?
- What is the expected behavior?
- How often does it occur?
- When did this start? What changed?

### 2. Investigation Framework

```
## Debug Investigation

### Symptom Analysis
- Observed Behavior: [What's happening]
- Expected Behavior: [What should happen]
- Frequency: [Always / Intermittent / Specific conditions]

### Evidence Collected
- Logs: [Key entries]
- Timeline: [Sequence of events]
- Patterns: [Any patterns observed]

### Hypotheses
#### Hypothesis 1: [Name]
- Theory: [What might cause it]
- Evidence For/Against: [Supporting data]
- Test: [How to confirm/eliminate]

### Root Cause
- Location: `file:line`
- Issue: [What's actually wrong]
- Why It Happens: [Underlying cause]

### Recommended Fix
[Code or configuration change]

### Prevention
- [ ] Add test for this scenario
- [ ] Add monitoring for early detection
```

## Techniques
- Binary search debugging (bisect changes)
- Differential debugging (compare working vs failing)
- Stress testing (apply load/constraints)
- Instrumentation (strategic logging)
- Minimization (smallest repro case)
