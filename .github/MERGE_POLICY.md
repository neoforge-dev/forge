# FORGE V4 CLI Merge Policy

**Effective:** 2026-03-05  
**Enforced by:** CI Quality Gates  
**Owner:** Senior Engineering Team

---

## 🛑 MERGE BLOCKED

**Current Status:** DO NOT MERGE TO MAIN

### Block Reasons
1. **Test Coverage:** 30.7% (Target: 70%+)
2. **P0 Features Missing Tests:**
   - ❌ V4-001 queue noun - 0% coverage
   - ❌ V4-006 lane noun - 0% coverage
3. **Critical Path Untested:** dispatch workflow, work workflow

---

## Quality Gates (Required for Merge)

### 1. Test Coverage ≥ 70%
```bash
cd cmd/forge
go test -cover ./...
# Must show: coverage: 70%+ of statements
```

### 2. All P0 Features Tested
Required test files:
- ✅ `internal/websocket/client_test.go` (exists)
- ❌ `queue_test.go` (missing - BLOCKING)
- ❌ `lane_test.go` (missing - BLOCKING)
- ❌ `work_test.go` (missing)
- ❌ `fleet_test.go` (missing)

### 3. All Tests Pass
```bash
cd cmd/forge
go test -race ./...
# Must return: ok (no failures)
```

### 4. Build Success
```bash
cd cmd/forge
go build -o forge .
# Must create binary without errors
```

### 5. Code Quality
```bash
cd cmd/forge
go vet ./...        # No issues
gofmt -l .          # No unformatted files
```

---

## Current Coverage by Feature

| Feature | Status | Coverage | Blocker |
|---------|--------|----------|---------|
| V4-001 queue | 🔄 In Progress | 0% | 🔴 YES |
| V4-002 domain | ✅ Complete | ~40% | 🟡 No |
| V4-003 approval | ✅ Complete | ~40% | 🟡 No |
| V4-004 project | ✅ Complete | ~40% | 🟡 No |
| V4-005 context | 🔄 In Progress | 0% | 🟡 No |
| V4-006 lane | 🔄 In Progress | 0% | 🔴 YES |
| V4-007 patrol | ⏳ Not Started | 0% | 🟡 No |
| V4-008 pattern | ✅ Complete | ~30% | 🟡 No |
| V4-009 config | ✅ Complete | ~30% | 🟡 No |
| V4-010 dispatch | ✅ Complete | ~20% | 🟡 No |
| V4-011 fleet | 🔄 In Progress | 0% | 🟡 No |
| V4-012 ship | ✅ Complete | ~20% | 🟡 No |
| V4-013 work | 🔄 In Progress | 0% | 🟡 No |
| V4-014 WebSocket | ✅ Complete | 11.9% | 🟡 No |
| V4-015 migration | ✅ Complete | N/A | N/A |

**Blockers:** V4-001 (queue), V4-006 (lane) - P0 features with 0% coverage

---

## Agent Test Assignments

| Agent | Test Task | Priority | Due |
|-------|-----------|----------|-----|
| minimax | queue_test.go | P0 | 2026-03-06 |
| gemini | lane_test.go | P0 | 2026-03-06 |
| cursor | dispatch_test.go | P0 | 2026-03-06 |
| glm | WebSocket coverage 70% | P0 | 2026-03-06 |
| pi | integration tests | P1 | 2026-03-07 |
| kimi | fleet_test.go | P1 | 2026-03-07 |

---

## Unblock Checklist

Merge to main ONLY when:
- [ ] Overall coverage ≥ 70%
- [ ] queue_test.go exists and passes
- [ ] lane_test.go exists and passes
- [ ] All quality gates pass
- [ ] CI workflow succeeds

---

## Emergency Override

**Requires:** CTO approval + documented risk acceptance

Use only for:
- Critical security fixes
- Production outage resolution
- Time-sensitive customer commitments

**Override Process:**
1. Document business justification
2. Get CTO written approval
3. Create follow-up ticket for tests
4. Merge with `[OVERRIDE]` in commit message

---

## Contact

For questions about this policy:
- Check: `.forge/heartbeat/V4-CLI-TDD-STATUS-20260305.md`
- CI Status: GitHub Actions → V4 CLI Quality Gates
- Owner: Senior Engineering Team
