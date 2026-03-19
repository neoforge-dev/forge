---
name: review
description: Code review for security, performance, and quality
---

# Review

Review code for security, performance, and quality.

## Review Checklist

### 1. Security
- [ ] Input validation on user inputs
- [ ] No SQL/XSS injection vulnerabilities
- [ ] Auth/authz properly implemented
- [ ] Secrets not hardcoded

### 2. Performance
- [ ] No N+1 query problems
- [ ] Efficient loops
- [ ] Proper caching
- [ ] Async where beneficial

### 3. Code Quality
- [ ] Clear naming
- [ ] DRY principle
- [ ] Single responsibility
- [ ] Proper error handling

### 4. Tests
- [ ] Unit tests for new code
- [ ] Edge cases covered
- [ ] Error scenarios tested

## Output Format

```
## Review: [Target]

### Summary
- **Assessment**: APPROVE | REQUEST CHANGES
- **Risk**: Low | Medium | High

### Critical Issues
**[CRITICAL-1]** `file:line` - Issue

### Important Suggestions
**[IMPORTANT-1]** `file:line` - Suggestion

### Minor Notes
- `file:line` - Minor suggestion
```

## Severity Levels

| Level | Action |
|-------|--------|
| CRITICAL | Must fix before merge |
| IMPORTANT | Strongly recommended |
| MINOR | Nice to have |
