# ADR-018: Pattern Library and RL Learning Store

**Date:** 2026-03-05
**Status:** HOLD — not on Phase 1 critical path; revisit Phase 3
**Decision Makers:**
- Bogdan Veliscu (CTO, FORGE)

---

## Context

FORGE uses pattern stratification to predict task success rates and guide agent assignment (e.g., `authentication` at 100%, `api_endpoint` at 42%). Currently this data lives in:

1. **Python `meta_learning/` module**: In-memory pattern tracking, persisted to `.forge/learning/patterns.json`
2. **CC patterns API**: FastAPI endpoints for CRUD on patterns
3. **Flat JSON files**: `.forge/learning/patterns.json`, `.forge/learning/decisions.json`

This approach lacks queryability (can't ask "what's the 7-day rolling success rate for `api_endpoint:stateful`?"), has no formal reward/outcome tracking, and is coupled to the Python CC being retired (ADR-014).

### Alternatives Considered

| Alternative | Pros | Cons | Verdict |
|-------------|------|------|---------|
| Keep JSON files, add query scripts | Minimal change | No aggregation, no history, poor performance | ❌ REJECTED |
| Full ML pipeline (MLflow) | Production-grade RL | Massive overhead for current scale | ❌ REJECTED |
| **Git YAML definitions + SQLite learning data** | **Human-readable patterns, queryable outcomes, simple** | **Two storage locations** | ✅ **ACCEPTED** |

---

## Decision

Pattern **definitions** live in git as YAML files (`.forge/patterns/<domain>/<pattern>.yaml`). Pattern **learning data** (run outcomes, rewards) lives in SQLite tables managed by the v3 binary.

### Pattern Definition (YAML in Git)

```yaml
# .forge/patterns/codeswiftr-com/api_endpoint_simple.yaml
id: api_endpoint:simple
domain: codeswiftr-com
name: Simple API Endpoint
description: GET, DELETE, or basic POST endpoints with no complex state
category: api_endpoint
tier: simple
success_rate: 0.92        # Updated by patrol from SQLite stats
sample_size: 24
tags: [api, crud, stateless]
agent_affinity: [claude, kimi]
estimated_tokens: 5000
created_at: 2026-01-15
updated_at: 2026-03-05
```

Patterns are versioned by git, human-reviewable in PRs, and editable by agents via GitGuard.

### Learning Data (SQLite)

```sql
CREATE TABLE pattern_runs (
    run_id      TEXT PRIMARY KEY,  -- ULID
    pattern_id  TEXT NOT NULL,     -- e.g. "api_endpoint:simple"
    task_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    domain      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    completed_at TEXT,
    success     INTEGER,          -- 1 = success, 0 = failure, NULL = in-progress
    confidence  REAL,             -- 0.0-1.0 pre-run confidence
    tokens_used INTEGER,
    duration_s  INTEGER,
    error_type  TEXT,             -- NULL on success, categorized on failure
    metadata    TEXT              -- JSON blob for pattern-specific data
);

CREATE TABLE pattern_rewards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES pattern_runs(run_id),
    reward_type TEXT NOT NULL,    -- "test_pass", "review_approved", "deploy_success", "revert"
    value       REAL NOT NULL,   -- positive = good, negative = bad
    reason      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_runs_pattern ON pattern_runs(pattern_id, started_at DESC);
CREATE INDEX idx_runs_domain ON pattern_runs(domain, started_at DESC);
CREATE INDEX idx_rewards_run ON pattern_rewards(run_id);
```

### Patrol: Rolling Stats → YAML Update

The stats patrol computes rolling statistics from SQLite and updates YAML `success_rate` and `sample_size` via GitGuard:

```sql
-- 30-day rolling success rate per pattern
SELECT pattern_id,
       COUNT(*) as sample_size,
       ROUND(AVG(success), 4) as success_rate
FROM pattern_runs
WHERE started_at > datetime('now', '-30 days')
  AND success IS NOT NULL
GROUP BY pattern_id;
```

Changes to YAML files are committed via GitGuard (single-writer, atomic).

### What This Replaces

| Current Component | Replaced By |
|-------------------|-------------|
| Python `meta_learning/` module | SQLite `pattern_runs` + `pattern_rewards` |
| CC patterns API (`api/patterns.py`) | v3 HTTP API endpoints |
| `.forge/learning/patterns.json` | `.forge/patterns/<domain>/<pattern>.yaml` |
| `.forge/learning/decisions.json` | `pattern_runs` table with decision context |

### v3 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/patterns` | List all patterns (optional `?domain=` filter) |
| `GET` | `/api/patterns/:id` | Get pattern definition + rolling stats |
| `POST` | `/api/patterns/:id/run` | Record a new pattern run |
| `POST` | `/api/patterns/:id/reward` | Record a reward signal for a run |
| `GET` | `/api/patterns/:id/stats` | Get detailed statistics (success rate, avg tokens, trend) |

---

## Consequences

### Positive

1. **Queryable history**: SQLite enables rolling stats, trend analysis, agent-specific success rates
2. **Human-readable definitions**: YAML in git is reviewable, diffable, and version-controlled
3. **Reward tracking**: Formal reward signals enable future RL optimization
4. **Stratification enforcement**: API requires `tier` field, preventing unstratified patterns (the 42% problem)
5. **Decoupled from CC**: Works entirely within v3 binary

### Negative

1. **Two storage locations**: Definitions in git, data in SQLite — requires sync discipline
2. **YAML update latency**: Patrol updates YAML on a schedule (not real-time)
3. **Migration effort**: Existing `patterns.json` must be converted to per-pattern YAML files

### Neutral

1. **Reward types**: Extensible via `reward_type` string — no schema change needed for new signal types
2. **Pattern inheritance**: Not implemented in Phase 1; `api_endpoint:simple` is a flat ID, not a hierarchy

---

## Related Decisions

- ADR-008: FORGE CLI v3 Rewrite (defines SQLite store and patrol system)
- ADR-014: Retire Command Center (removes CC patterns API)
- ADR-012: v3 Confidence Scoring (consumes pattern success rates)

---

**Status: PROPOSED**

Decision review target: 2026-03-10
