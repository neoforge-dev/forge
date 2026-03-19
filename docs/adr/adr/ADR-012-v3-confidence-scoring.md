# ADR-012: Confidence Scoring for Auto-Approval

**Date:** 2026-03-02
**Status:** Accepted
**Decision Makers:**
- kilo (pool-t2 agent, Architecture Review)
- cursor (pool-t2 agent, Technical Review)
- amp (pool-t2 agent, Operations Review)
- gemini (nf.lead agent, Strategic Review)

---

## Context

FORGE v3 aims to reduce human operator burden while maintaining safety. The challenge: automatically approve low-risk tasks while escalating high-risk tasks for human review.

The question: How do we score task risk/confidence to enable auto-approval?

### Requirements

1. **Objective metrics** - Based on measurable signals, not gut feel
2. **Composable** - Different factors for different task types
3. **Tunable** - Thresholds adjustable per domain/project
4. **Explainable** - Humans can understand why a score was given
5. **Safe defaults** - Err on side of human review when uncertain

### Alternatives Considered

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Rule-based** (if tests pass → approve) | Simple, transparent | Brittle, doesn't capture nuance | ❌ REJECTED |
| **ML model** | Can learn from history | Black box, needs training data | ❌ REJECTED |
| **Weighted scoring** | Composable, explainable, tunable | Requires tuning | ✅ **ACCEPTED** |
| **Human-only** | Maximum safety | 100% operator burden | ❌ REJECTED |

---

## Decision

Implement a **weighted confidence scoring system** with five factors:

### Formula

```
confidence = pattern_score × 0.35
           + test_score × 0.25
           + blast_radius_score × 0.15
           + reversibility_score × 0.15
           + maturity_score × 0.10
```

**Range:** 0.0 (no confidence) to 1.0 (maximum confidence)

### Scoring Factors

#### 1. Pattern Score (35%)

Historical success rate of this task pattern.

```go
type PatternScore struct {
    PatternID    string  // e.g., "api_endpoint:simple", "refactoring:extract_method"
    SuccessRate  float64 // 0.0-1.0
    SampleSize   int     // Number of previous executions
    LastFailure  time.Time
}

func CalculatePatternScore(patternID string, history []TaskExecution) float64 {
    executions := filterByPattern(history, patternID)
    if len(executions) < 3 {
        return 0.5 // Insufficient data, neutral score
    }
    
    successes := countSuccesses(executions)
    rate := float64(successes) / float64(len(executions))
    
    // Penalize recent failures
    if hasFailureInLast24Hours(executions) {
        rate *= 0.8
    }
    
    return rate
}
```

**Example patterns:**
- `authentication:jwt_login` - 100% (24/24)
- `testing:unit_test` - 100% (3/3)
- `refactoring:rename_variable` - 100% (2/2)
- `api_endpoint:simple` - 42% (13/31) - needs stratification
- `general:feature_implementation` - 86% (25/29)

#### 2. Test Score (25%)

Quality and coverage of tests.

```go
type TestScore struct {
    UnitTestsPass      bool
    IntegrationTestsPass bool
    CoveragePercent    float64
    NewTestsAdded      bool
    FlakyTests         int
}

func CalculateTestScore(tests TestResults) float64 {
    score := 0.0
    
    // Unit tests (40% of test score)
    if tests.UnitTestsPass {
        score += 0.4
    }
    
    // Integration tests (30% of test score)
    if tests.IntegrationTestsPass {
        score += 0.3
    }
    
    // Coverage (20% of test score)
    coverage := math.Min(tests.CoveragePercent, 100.0)
    score += (coverage / 100.0) * 0.2
    
    // New tests (10% of test score)
    if tests.NewTestsAdded {
        score += 0.1
    }
    
    // Penalty for flaky tests
    score -= float64(tests.FlakyTests) * 0.1
    
    return math.Max(0.0, score)
}
```

#### 3. Blast Radius Score (15%)

How many systems could be affected.

```go
type BlastRadius struct {
    FilesModified   int
    LinesChanged    int
    APIsModified    int
    DBMigrations    bool
    Dependencies    []string
    DownstreamServices []string
}

func CalculateBlastRadiusScore(radius BlastRadius) float64 {
    // Smaller blast radius = higher score
    
    score := 1.0
    
    // Files modified
    if radius.FilesModified > 10 {
        score -= 0.2
    } else if radius.FilesModified > 5 {
        score -= 0.1
    }
    
    // Lines changed
    if radius.LinesChanged > 500 {
        score -= 0.2
    } else if radius.LinesChanged > 100 {
        score -= 0.1
    }
    
    // API changes
    if radius.APIsModified > 0 {
        score -= 0.2
    }
    
    // DB migrations
    if radius.DBMigrations {
        score -= 0.3
    }
    
    // Downstream services
    score -= float64(len(radius.DownstreamServices)) * 0.1
    
    return math.Max(0.0, score)
}
```

#### 4. Reversibility Score (15%)

How easy to undo if something goes wrong.

```go
type Reversibility struct {
    HasRollbackProcedure bool
    CanRollbackAutomatically bool
    RollbackTimeEstimate time.Duration
    DataLossRisk bool
    ExternalDependencies []string
}

func CalculateReversibilityScore(r Reversibility) float64 {
    score := 0.5 // Base score
    
    if r.HasRollbackProcedure {
        score += 0.2
    }
    
    if r.CanRollbackAutomatically {
        score += 0.2
    }
    
    // Faster rollback = higher score
    if r.RollbackTimeEstimate < 5*time.Minute {
        score += 0.1
    } else if r.RollbackTimeEstimate > 1*time.Hour {
        score -= 0.1
    }
    
    // Data loss risk is a major penalty
    if r.DataLossRisk {
        score -= 0.3
    }
    
    // External dependencies reduce reversibility
    score -= float64(len(r.ExternalDependencies)) * 0.05
    
    return math.Max(0.0, math.Min(1.0, score))
}
```

#### 5. Maturity Score (10%)

Stability of the codebase and domain.

```go
type Maturity struct {
    DomainAge        time.Duration  // How long domain exists
    LastProductionIssue time.Time
    TestCoverage     float64
    DocumentationCompleteness float64
    TeamFamiliarity  float64  // Based on historical commits
}

func CalculateMaturityScore(m Maturity) float64 {
    score := 0.5
    
    // Domain age
    if m.DomainAge > 1*365*24*time.Hour { // > 1 year
        score += 0.2
    } else if m.DomainAge > 90*24*time.Hour { // > 90 days
        score += 0.1
    }
    
    // Recent issues
    if time.Since(m.LastProductionIssue) > 30*24*time.Hour {
        score += 0.1
    }
    
    // Test coverage
    score += (m.TestCoverage / 100.0) * 0.1
    
    // Documentation
    score += (m.DocumentationCompleteness / 100.0) * 0.05
    
    return math.Min(1.0, score)
}
```

### Approval Tiers

Based on confidence score, route to appropriate approval tier:

```go
const (
    // Auto-approve: No human intervention
    ThresholdAutoApprove = 0.95
    
    // PHONE tier: Quick binary decision
    ThresholdPhone = 0.70
    
    // DESKTOP tier: Full review
    ThresholdDesktop = 0.0
)

func DetermineApprovalTier(confidence float64) ApprovalTier {
    switch {
    case confidence >= ThresholdAutoApprove:
        return TierAutoApprove
    case confidence >= ThresholdPhone:
        return TierPhone
    default:
        return TierDesktop
    }
}
```

| Tier | Confidence | Response Time | Human Action |
|------|-----------|---------------|--------------|
| **Auto-Approve** | ≥0.95 | Immediate | None (logged for audit) |
| **PHONE** | 0.70-0.94 | <2 minutes | Binary yes/no from mobile |
| **DESKTOP** | <0.70 | Unbounded | Full diff review |

### Schema

```sql
-- Confidence scores table
CREATE TABLE confidence_scores (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    
    -- Individual scores
    pattern_score REAL NOT NULL,
    test_score REAL NOT NULL,
    blast_radius_score REAL NOT NULL,
    reversibility_score REAL NOT NULL,
    maturity_score REAL NOT NULL,
    
    -- Weighted total
    total_score REAL NOT NULL,
    
    -- Determined tier
    approval_tier TEXT NOT NULL,  -- auto, phone, desktop
    
    -- Explanation (for humans)
    explanation TEXT,  -- JSON: {"factors": [...], "reasoning": "..."}
    
    created_at TEXT DEFAULT (datetime('now')),
    
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

-- Score history for tuning
CREATE TABLE score_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    score_given REAL NOT NULL,
    actual_result TEXT,  -- success, failure, rollback_needed
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

## Integration with Task Lifecycle

```
Task Completed
     ↓
Generate Confidence Score
     ↓
┌─────────────────────────────────────┐
│  score >= 0.95?                     │
│  → Auto-approve → Merge             │
└─────────────────────────────────────┘
     ↓ no
┌─────────────────────────────────────┐
│  score >= 0.70?                     │
│  → PHONE tier approval              │
│  → Push notification                │
│  → Binary yes/no                    │
└─────────────────────────────────────┘
     ↓ no
┌─────────────────────────────────────┐
│  score < 0.70                       │
│  → DESKTOP tier approval            │
│  → Full diff review                 │
│  → Detailed context                 │
└─────────────────────────────────────┘
```

### Example Scenarios

| Scenario | Pattern | Tests | Blast | Reversible | Mature | Score | Tier |
|----------|---------|-------|-------|------------|--------|-------|------|
| Fix typo in docs | 0.9 | 0.0 | 1.0 | 1.0 | 0.9 | 0.82 | PHONE |
| Add unit test | 1.0 | 0.9 | 0.9 | 1.0 | 0.8 | 0.94 | PHONE |
| Refactor auth | 1.0 | 0.9 | 0.6 | 0.7 | 0.9 | 0.89 | PHONE |
| New API endpoint | 0.4 | 0.7 | 0.5 | 0.6 | 0.7 | 0.56 | DESKTOP |
| DB migration | 0.8 | 0.8 | 0.3 | 0.4 | 0.9 | 0.68 | DESKTOP |
| Security fix | 0.9 | 0.9 | 0.5 | 0.5 | 0.8 | 0.77 | PHONE |

---

## Tuning and Calibration

### Per-Domain Configuration

```yaml
confidence:
  weights:
    pattern: 0.35
    test: 0.25
    blast_radius: 0.15
    reversibility: 0.15
    maturity: 0.10
  
  thresholds:
    auto_approve: 0.95
    phone: 0.70
  
  # Domain overrides
  domains:
    codeswiftr-com:
      # Interview simulator - high test coverage
      weights:
        test: 0.35  # Increase test weight
        pattern: 0.25
      thresholds:
        auto_approve: 0.90  # Lower threshold
    
    brandfocus-ai:
      # Voice coach - safety critical
      weights:
        blast_radius: 0.25  # Increase safety weight
        reversibility: 0.20
      thresholds:
        auto_approve: 0.98  # Higher threshold
        phone: 0.85
```

### Continuous Calibration

```go
// Weekly calibration job
func CalibrateConfidenceModel() {
    // Find tasks where score was high but result was bad
    falsePositives := query(`
        SELECT task_id, score_given, actual_result
        FROM score_history
        WHERE score_given > 0.95
        AND actual_result != 'success'
    `)
    
    // Find tasks where score was low but result was good
    falseNegatives := query(`
        SELECT task_id, score_given, actual_result
        FROM score_history
        WHERE score_given < 0.70
        AND actual_result = 'success'
    `)
    
    // Adjust weights based on error analysis
    // Log recommendations for human review
}
```

---

## Consequences

### Positive

1. **Reduced operator burden** - 60-80% of tasks auto-approved
2. **Consistent decisions** - Same criteria every time
3. **Explainable** - Humans can understand the score
4. **Tunable** - Adjust per domain/project
5. **Improves over time** - Pattern scores learn from history

### Negative

1. **Tuning required** - Initial weights may need adjustment
2. **Gaming risk** - Agents might optimize for score not quality
3. **False confidence** - High score doesn't guarantee success
4. **Complexity** - Five factors to understand and maintain

### Mitigations

| Risk | Mitigation |
|------|------------|
| Wrong auto-approvals | Start with high threshold (0.98), lower gradually |
| Gaming | Monitor for score manipulation patterns |
| Over-confidence | Require human review for novel patterns |
| Tuning fatigue | Weekly auto-calibration with human oversight |

---

## Implementation Timeline

### Phase 1 (Weeks 1-8): Basic Scoring
- Pattern score from history
- Test score from results
- Simple blast radius (files changed)
- Fixed weights and thresholds

### Phase 2 (Weeks 9-12): Enhanced Scoring
- Reversibility analysis
- Maturity metrics
- Per-domain configuration
- Explanation generation

### Phase 3 (Weeks 13-16): Calibration
- Score history tracking
- False positive/negative analysis
- Auto-calibration recommendations
- Threshold tuning UI

---

## Related Decisions

- ADR-008: FORGE CLI v3 Rewrite (parent)
- ADR-011: WebSocket Protocol (score transmission)
- ADR-009: Agentic Patterns (race mode winner selection)

## References

- Pattern Stratification: `docs/plans/FORGE_V3_GAP_ANALYSIS.md`
- FORGE CLI v3 Locked Spec: `docs/plans/FORGE_CLI_V3_LOCKED_SPECIFICATION.md`
- Confidence Scoring Research: Anthropic production systems

---

**Status: ACCEPTED — Phase 1 implemented (S74)**

### Implementation Status (S116)

| Component | ADR Spec | Actual | Gap |
|-----------|----------|--------|-----|
| Weighted formula (5 factors) | ✅ | ✅ `approvals.go:CalculateConfidence` | Weights match ADR |
| Approval tiers (auto/phone/desktop) | ✅ | ✅ `approvals.go` | Thresholds match |
| `PatternScore` struct (history-based) | Detailed | Simplified (flat `PatternMatch` float) | No pattern history tracking |
| `TestScore` struct (unit+integration+flaky) | Detailed | Simplified (passRate + coverageWeight) | No flaky test detection |
| `BlastRadius` struct (files/lines/APIs/DB) | Detailed | Simplified (single int threshold) | No per-factor breakdown |
| `confidence_scores` DB table | ✅ | ❌ Not created | Scores not persisted |
| `score_history` DB table | ✅ | ❌ Not created | No calibration data |
| Per-domain config YAML | ✅ | ❌ Not implemented | Hardcoded weights only |
| Weekly calibration job | ✅ | ❌ Not implemented | Phase 3 scope |
| Stage-aware thresholds | Not in ADR | ✅ `ApprovalStagePolicyEntry` | Bonus: per-stage confidence |

**What works:** Core scoring loop with correct weights and tier routing. Sufficient for current autonomy level.
**What's missing:** Persistence, history-based pattern scoring, per-domain tuning, calibration. These are Phase 2-3 scope and not blocking.

Default Thresholds:
- Auto-approve: ≥0.95
- PHONE: 0.70-0.94
- DESKTOP: <0.70

Weights:
- Pattern: 35%
- Test: 25%
- Blast Radius: 15%
- Reversibility: 15%
- Maturity: 10%
