# ADR-013: Race Mode - Competitive Validation for High-Risk Tasks

**Date:** 2026-03-02
**Status:** Withdrawn
**Decision Makers:**
- kilo (pool-t2 agent, Architecture Review)
- cursor (pool-t2 agent, Technical Review)
- amp (pool-t2 agent, Operations Review)
- gemini (nf.lead agent, Strategic Review)

## Withdrawal Note (2026-03-06)

Withdrawn: Superseded by ADR-010 Lease System.

The lease system provides task exclusivity guarantees (30-min lease with
recovery on agent death) that make competitive race mode redundant at
current fleet sizes. The original use case — competitive validation of
high-risk decisions — is better served by the manual council review
process already in place.

If fleet scales beyond 20 concurrent agents on a single task type,
this ADR should be reconsidered.

See: ADR-010 for lease implementation.

---

## Context

Some tasks have high stakes where the cost of choosing the wrong approach far exceeds the cost of exploring multiple approaches. Examples include security fixes, architecture decisions, and database migrations.

The question: How can FORGE v3 automatically validate multiple approaches and select the best one?

### Inspiration

- **Brethorst**: "Quality through competition" - spin up multiple instances, let them implement independently, run deterministic tests, choose winner
- **Osmani**: Try multiple models/agents in parallel for cross-checking
- **AgileCoder**: Multiple agents with different roles working on same problem

### Requirements

1. **Isolation** - Each candidate must work independently without interference
2. **Deterministic validation** - Objective criteria for selecting winner
3. **Resource constraints** - Don't exhaust fleet capacity
4. **Automatic selection** - System picks winner without human intervention (in most cases)
5. **Audit trail** - Record why a particular candidate was chosen

### Alternatives Considered

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Sequential retry** | Simple, low resource | Slow, no comparison | ❌ REJECTED |
| **Human selection** | Maximum judgment | Slow, operator burden | ❌ REJECTED |
| **Race mode (competition)** | Parallel, objective, automated | Higher resource usage | ✅ **ACCEPTED** |
| **Always race** | Maximum quality | Prohibitively expensive | ❌ REJECTED |

---

## Decision

Implement **Race Mode** as an opt-in execution strategy for high-risk tasks.

### Core Principle

> "Quality through competition with constraints"

Race only when:
- Cost of choosing wrong >> cost of wasted compute
- Task is parallelizable (multiple valid approaches exist)
- Sufficient resources available

### When to Race

**Always Race:**
- Security-critical changes (auth, payments, encryption)
- Architecture decisions (hard to reverse)
- Database migrations (data integrity risk)
- Production deploy strategies (outage risk)

**Never Race:**
- T1 tasks (typo fixes, documentation) - waste rate 50-75%
- Clear solutions (obvious bug fix) - redo is cheaper
- Resource-constrained periods

**Flag-Gated (Context Dependent):**
- T2 features with multiple valid approaches
- Performance optimizations
- Refactoring large modules

### Configuration

```go
type RaceConfig struct {
    // Global limits
    Enabled              bool
    MaxConcurrentRaces   int           // 3 fleet-wide
    MaxCandidatesPerRace int           // 4 hard cap
    
    // Resource constraints
    NodeAffinity         []string      // ["node-3", "node-2"] - never node-1
    VerificationTimeout  time.Duration // 10-20 min per candidate
    
    // Early termination
    EarlyTerminationThreshold float64  // 0.95 confidence
    
    // Auto-triggers
    Triggers []RaceTrigger
}

type RaceTrigger struct {
    TaskType     string   // security_change, architecture_decision
    RiskTier     string   // high, critical
    MinRaceCount int      // 2 or 3
}

// Default configuration
var DefaultRaceConfig = RaceConfig{
    Enabled:              true,
    MaxConcurrentRaces:   3,
    MaxCandidatesPerRace: 4,
    NodeAffinity:         []string{"node-3", "node-2"},
    VerificationTimeout:  15 * time.Minute,
    EarlyTerminationThreshold: 0.95,
    
    Triggers: []RaceTrigger{
        // Security: always race
        {TaskType: "security_change", RiskTier: "critical", MinRaceCount: 3},
        {TaskType: "security_change", RiskTier: "high", MinRaceCount: 2},
        
        // Architecture: always race
        {TaskType: "architecture_decision", RiskTier: "*", MinRaceCount: 2},
        
        // Database: race high-risk migrations
        {TaskType: "database_migration", RiskTier: "high", MinRaceCount: 2},
        
        // Deploy: race production strategies
        {TaskType: "production_deploy", RiskTier: "high", MinRaceCount: 2},
    },
}
```

### Race Execution Flow

```
Task Enqueued
     ↓
Check Race Triggers
     ↓
┌─────────────────────────────────────────┐
│  Match trigger? AND resources available?│
│  → Start Race                           │
└─────────────────────────────────────────┘
     ↓ no
Standard Execution
     ↓ yes
Create N Worktrees
     ↓
Dispatch to N Candidates (different agents/models)
     ↓
Run in Parallel
     ↓
Verification Phase (tests, lint, security scan)
     ↓
Score Each Candidate
     ↓
┌─────────────────────────────────────────┐
│  Any candidate > 0.95 confidence?       │
│  → Early termination: declare winner    │
└─────────────────────────────────────────┘
     ↓ no
Wait for all candidates
     ↓
Select Winner (highest score)
     ↓
Merge Winner's Branch
     ↓
Cleanup Losers (worktrees, branches)
     ↓
Emit Race Completed Event
```

### Worktree Isolation

```go
type Workspace struct {
    ID         string
    Path       string   // /path/to/worktrees/race-{taskID}-{candidateID}
    Branch     string   // race/{taskID}/{candidateID}
    Candidate  Candidate
}

func (r *RaceManager) CreateWorkspaces(ctx context.Context, task Task, candidates []Candidate) ([]Workspace, error) {
    workspaces := make([]Workspace, len(candidates))
    
    for i, candidate := range candidates {
        workspaceID := fmt.Sprintf("%s-%s", task.ID, candidate.ID)
        branch := fmt.Sprintf("race/%s/%s", task.ID, candidate.ID)
        path := filepath.Join(r.WorktreeRoot, workspaceID)
        
        // Create worktree
        cmd := exec.CommandContext(ctx, "git", "worktree", "add", path, "-b", branch, task.BaseBranch)
        if err := cmd.Run(); err != nil {
            return nil, fmt.Errorf("create worktree: %w", err)
        }
        
        workspaces[i] = Workspace{
            ID:        workspaceID,
            Path:      path,
            Branch:    branch,
            Candidate: candidate,
        }
    }
    
    return workspaces, nil
}
```

### Candidate Selection

```go
type Candidate struct {
    ID       string   // "candidate-1", "candidate-2"
    AgentID  string   // Specific agent (if pre-registered)
    ModelTag string   // "claude-sonnet", "gpt-4", "glm-5"
    Tier     string   // t1, t2, t3
    
    // Constraints for this candidate
    Constraints TaskConstraints
}

func SelectCandidates(task Task, availableAgents []Agent) []Candidate {
    candidates := []Candidate{}
    
    // Always include at least one T2 agent
    t2Agents := filterByTier(availableAgents, "t2")
    if len(t2Agents) > 0 {
        candidates = append(candidates, Candidate{
            ID:       "candidate-1",
            AgentID:  t2Agents[0].ID,
            ModelTag: t2Agents[0].ModelTag,
            Tier:     "t2",
        })
    }
    
    // For high-risk tasks, add diversity
    if task.RiskTier == "critical" {
        // Add different model
        if t3Agents := filterByTier(availableAgents, "t3"); len(t3Agents) > 0 {
            candidates = append(candidates, Candidate{
                ID:       "candidate-2",
                AgentID:  t3Agents[0].ID,
                ModelTag: t3Agents[0].ModelTag,
                Tier:     "t3",
            })
        }
        
        // Add T1 for speed comparison
        if t1Agents := filterByTier(availableAgents, "t1"); len(t1Agents) > 0 {
            candidates = append(candidates, Candidate{
                ID:       "candidate-3",
                AgentID:  t1Agents[0].ID,
                ModelTag: t1Agents[0].ModelTag,
                Tier:     "t1",
            })
        }
    }
    
    return candidates
}
```

### Verification and Scoring

```go
type VerificationResult struct {
    Passed    bool
    Score     float64         // 0.0-1.0
    Details   string          // Human-readable summary
    Metrics   map[string]any  // Extensible metrics
    Duration  time.Duration   // How long verification took
}

type VerificationPipeline struct {
    Steps []VerificationStep
}

var DefaultVerificationPipeline = VerificationPipeline{
    Steps: []VerificationStep{
        {Name: "lint", Weight: 0.1, Command: "make lint"},
        {Name: "format", Weight: 0.1, Command: "make format-check"},
        {Name: "unit_tests", Weight: 0.3, Command: "make test-unit"},
        {Name: "integration_tests", Weight: 0.3, Command: "make test-integration"},
        {Name: "security_scan", Weight: 0.2, Command: "make security-scan"},
    },
}

func (p *VerificationPipeline) Run(ctx context.Context, workspace Workspace) (VerificationResult, error) {
    totalScore := 0.0
    details := []string{}
    
    for _, step := range p.Steps {
        cmd := exec.CommandContext(ctx, "sh", "-c", step.Command)
        cmd.Dir = workspace.Path
        
        output, err := cmd.CombinedOutput()
        passed := err == nil
        
        if passed {
            totalScore += step.Weight
            details = append(details, fmt.Sprintf("✓ %s", step.Name))
        } else {
            details = append(details, fmt.Sprintf("✗ %s: %s", step.Name, string(output)))
        }
    }
    
    return VerificationResult{
        Passed:   totalScore >= 0.8,  // 80% to pass
        Score:    totalScore,
        Details:  strings.Join(details, "\n"),
        Metrics:  map[string]any{"steps_run": len(p.Steps)},
        Duration: time.Since(start),
    }, nil
}
```

### Winner Selection

```go
type RaceResult struct {
    TaskID     string
    Winner     *Candidate
    Candidates []CandidateResult
    SelectionMethod string  // "score", "early_termination", "human"
}

type CandidateResult struct {
    Candidate     Candidate
    Verification  VerificationResult
    FinalScore    float64
}

func (r *RaceManager) SelectWinner(results []CandidateResult) (*Candidate, error) {
    // Check for clear winner (>5% margin)
    sort.Slice(results, func(i, j int) bool {
        return results[i].FinalScore > results[j].FinalScore
    })
    
    winner := results[0]
    runnerUp := results[1]
    
    margin := winner.FinalScore - runnerUp.FinalScore
    
    if margin > 0.05 {
        // Clear winner
        return &winner.Candidate, nil
    }
    
    // Tie or close race - escalate to human
    return nil, ErrCloseRaceRequiresHumanJudgment
}
```

### Early Termination

```go
func (r *RaceManager) MonitorRace(ctx context.Context, raceID string) {
    ticker := time.NewTicker(5 * time.Second)
    defer ticker.Stop()
    
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            results := r.GetCurrentResults(raceID)
            
            for _, result := range results {
                if result.FinalScore >= r.Config.EarlyTerminationThreshold {
                    // Declare winner early
                    r.TerminateRace(raceID, result.Candidate, "early_termination")
                    return
                }
            }
        }
    }
}
```

### Cleanup

```go
func (r *RaceManager) CleanupRace(ctx context.Context, raceID string, winner *Candidate, losers []Candidate) error {
    // Remove loser worktrees and branches
    for _, loser := range losers {
        workspace := r.GetWorkspace(raceID, loser.ID)
        
        // Remove worktree
        cmd := exec.CommandContext(ctx, "git", "worktree", "remove", "--force", workspace.Path)
        if err := cmd.Run(); err != nil {
            log.Printf("Failed to remove worktree %s: %v", workspace.Path, err)
        }
        
        // Delete branch
        cmd = exec.CommandContext(ctx, "git", "branch", "-D", workspace.Branch)
        if err := cmd.Run(); err != nil {
            log.Printf("Failed to delete branch %s: %v", workspace.Branch, err)
        }
    }
    
    // Winner's branch is kept for merging
    return nil
}
```

---

## Resource Management

### Fleet Capacity Analysis (from amp)

| Node | RAM | Agents | Headroom | Race Eligible? |
|------|-----|--------|----------|----------------|
| node-1 | 16 GB | 4 | ~9 GB | ❌ No (CC overhead) |
| node-2 | 64 GB | 7 | ~57 GB | ✅ Yes |
| node-3 | 48 GB | 2-3 | ~45 GB | ✅ Yes (best) |
| node-4 | 16 GB | 2 | ~15 GB | ⚠️ Limited |

### Token Budget Impact

| Agent | Hourly Limit | Race-3 Impact |
|-------|-------------|---------------|
| kimi | 250K tokens | ~150K (60% of budget) |
| gemini | 1M tokens | ~150K (15% of budget) |
| Pool workers | Free tier | Tokens not constraint |

### Resource Constraints

```go
func (r *RaceManager) CanStartRace(ctx context.Context, task Task) error {
    // Check concurrent race limit
    activeRaces := r.CountActiveRaces()
    if activeRaces >= r.Config.MaxConcurrentRaces {
        return ErrMaxConcurrentRacesReached
    }
    
    // Check token budget
    for _, agent := range r.SelectCandidateAgents(task) {
        remaining, err := r.TokenBudgetTracker.Remaining(agent.ID)
        if err != nil {
            return err
        }
        
        // Estimate tokens needed (conservative: 3 outputs)
        estimated := agent.EstimateTokens(task) * 3
        if remaining < estimated {
            return fmt.Errorf("insufficient token budget for agent %s", agent.ID)
        }
    }
    
    // Check node capacity
    targetNode := r.SelectRaceNode()
    if targetNode == nil {
        return ErrNoEligibleNodes
    }
    
    return nil
}
```

---

## Schema

```sql
-- Race executions
CREATE TABLE races (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,  -- running, completed, cancelled
    
    -- Configuration
    candidate_count INTEGER NOT NULL,
    trigger_type TEXT,  -- security_change, architecture_decision, etc.
    
    -- Timing
    started_at TEXT NOT NULL,
    completed_at TEXT,
    
    -- Winner
    winner_candidate_id TEXT,
    selection_method TEXT,  -- score, early_termination, human
    
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

-- Race candidates
CREATE TABLE race_candidates (
    id TEXT PRIMARY KEY,
    race_id TEXT NOT NULL,
    candidate_number INTEGER NOT NULL,
    agent_id TEXT NOT NULL,
    model_tag TEXT,
    
    -- Workspace
    worktree_path TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    
    -- Results
    verification_score REAL,
    verification_details TEXT,
    final_score REAL,
    completed_at TEXT,
    
    FOREIGN KEY (race_id) REFERENCES races(id)
);

-- Race events for audit trail
CREATE TABLE race_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- race.started, candidate.completed, winner.selected
    payload TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

## Consequences

### Positive

1. **Higher quality for critical tasks** - Multiple approaches evaluated
2. **Objective selection** - Tests determine winner, not bias
3. **Reduced human burden** - Automated for clear winners
4. **Audit trail** - Complete record of race and selection
5. **Resource efficiency** - Early termination, cleanup

### Negative

1. **Higher compute cost** - 2-4× tokens for raced tasks
2. **Complexity** - Worktree management, race coordination
3. **Latency** - Must wait for verification (or early termination)
4. **Edge cases** - Ties, verification failures, resource exhaustion

### Mitigations

| Risk | Mitigation |
|------|------------|
| Token budget exhaustion | Pre-flight check, early termination |
| Resource exhaustion | Max 3 concurrent races, node affinity |
| Ties | Escalate to human judgment |
| Verification flakiness | Multiple verification runs, timeout |
| Worktree leaks | Cleanup patrol, TTL on worktrees |

---

## Implementation Timeline

### Phase 3 (Weeks 13-16): Race Mode

**Week 13:**
- RaceManager core
- Worktree isolation
- Candidate selection

**Week 14:**
- Verification pipeline
- Winner selection
- Early termination

**Week 15:**
- Token budget integration
- Resource constraints
- Cleanup

**Week 16:**
- Race mode triggers
- Testing
- Documentation

---

## Related Decisions

- ADR-008: FORGE CLI v3 Rewrite (parent)
- ADR-009: Agentic Patterns (race mode as pattern)
- ADR-010: Lease System (race candidate leases)
- ADR-012: Confidence Scoring (winner selection)

## References

- Brethorst: Orchestrating Agentic Coding
- Osmani: My LLM Coding Workflow 2026
- AgileCoder: arXiv:2406.11912
- FORGE CLI v3 Agentic Patterns: `docs/plans/FORGE_CLI_V3_AGENTIC_PATTERNS_FINAL.md`

---

**Status: ACCEPTED**

Max Concurrent Races: 3
Max Candidates: 4
Node Affinity: node-3, node-2
Early Termination: 0.95 confidence
Default Triggers: security, architecture, DB migrations
