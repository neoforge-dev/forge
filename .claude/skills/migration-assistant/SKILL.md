---
name: migration-assistant
description: Assist with database migrations, schema changes, and data migrations across FORGE projects with safety checks and rollback procedures
trigger: user-invoked
tools: [Bash, Read, Write, Grep]
---

# Migration Assistant

Comprehensive skill for safe database migrations in FORGE projects. Handles Alembic migrations, schema validation, data migration planning, and rollback procedures.

## Why This Skill Exists

Database migrations are high-risk operations that can cause:
- Production downtime from breaking changes
- Data loss from improperly tested migrations
- Rollback failures without proper procedures
- Cross-project inconsistencies in migration patterns

This skill provides standardized, safe migration workflows with validation at every step.

## Usage

```bash
# Generate a new migration
/migrate generate --description "add user preferences"

# Validate migrations for breaking changes
/migrate validate

# Plan data migration for large tables
/migrate plan --table orders --rows 10M

# Generate rollback procedure
/migrate rollback --revision abc123

# Test migration strategy
/migrate test --revision head

# Check cross-project migration status
/migrate status
```

## Commands

### Generate Migration

Creates a new Alembic migration with automatic review.

```bash
/migrate generate --description "add user preferences" [--auto]
```

**Process:**
1. Detect changes in SQLAlchemy models
2. Generate migration with `alembic revision --autogenerate`
3. Run breaking change detection
4. Prompt for review (unless --auto)

### Validate Migration

Checks for breaking changes and migration safety issues.

```bash
/migrate validate [--revision REVISION] [--strict]
```

**Checks:**
- Column drops (breaking)
- Type changes (potentially breaking)
- Constraint drops
- Index changes affecting performance
- Missing downgrade paths

### Plan Data Migration

Creates strategy for large table migrations.

```bash
/migrate plan --table TABLE --rows N [--strategy batch|cte|temp]
```

**Strategies:**
| Strategy | When to Use | Duration |
|----------|-------------|----------|
| `batch` | < 1M rows, simple transforms | Minutes |
| `cte` | 1M-10M rows, complex logic | Hours |
| `temp` | > 10M rows, zero downtime | Hours-days |

### Rollback Procedure

Generates detailed rollback steps.

```bash
/migrate rollback --revision REVISION [--preview]
```

### Test Migration

Tests migration in isolated environment.

```bash
/migrate test --revision REVISION [--with-data]
```

### Cross-Project Status

Shows migration status across FORGE projects.

```bash
/migrate status [--project PROJECT] [--domain DOMAIN]
```

---

## Breaking Change Detection

### Critical (Blocks Migration)

| Change Type | Risk | Detection |
|-------------|------|-----------|
| Column drop | Data loss | SQL parser + model diff |
| Table drop | Complete data loss | Model registry check |
| NOT NULL added | Insert failures | Constraint analysis |
| Type narrowing | Data truncation | Type compatibility matrix |

### Warning (Requires Review)

| Change Type | Risk | Action |
|-------------|------|--------|
| Index drop | Performance | Verify query patterns |
| Constraint change | Integrity | Check foreign keys |
| Default value change | Behavior | Document in changelog |
| Column rename | App breakage | Add compatibility layer |

---

## Migration Safety Patterns

### Pattern 1: Expand-Contract for Column Changes

```python
# Step 1: Add new column (deploy)
# migration 001_add_new_column.py
op.add_column('users', sa.Column('email_new', sa.String()))

# Step 2: Backfill data (deploy)
# migration 002_backfill_email.py
op.execute("UPDATE users SET email_new = email")

# Step 3: Update app to use new column (deploy)
# App code change

# Step 4: Drop old column (deploy)
# migration 003_drop_old_column.py
op.drop_column('users', 'email')
```

### Pattern 2: Batch Processing for Large Tables

```python
# For tables > 1M rows, use batch processing
BATCH_SIZE = 10000

def upgrade():
    conn = op.get_bind()
    
    while True:
        result = conn.execute(sa.text("""
            UPDATE users 
            SET normalized_email = LOWER(email)
            WHERE id IN (
                SELECT id FROM users 
                WHERE normalized_email IS NULL
                LIMIT :batch_size
            )
            RETURNING id
        """), {"batch_size": BATCH_SIZE})
        
        if result.rowcount == 0:
            break
```

### Pattern 3: Zero-Downtime with Temp Table

```python
# For critical tables, use temp table + rename

def upgrade():
    # 1. Create new table
    op.create_table('users_new', ...)
    
    # 2. Migrate data in chunks
    # ... batch processing ...
    
    # 3. Swap tables (atomic)
    op.execute("BEGIN;")
    op.execute("ALTER TABLE users RENAME TO users_old;")
    op.execute("ALTER TABLE users_new RENAME TO users;")
    op.execute("COMMIT;")
    
    # 4. Drop old table (later)
```

---

## Rollback Procedures

### Standard Rollback

```bash
# 1. Check current revision
alembic current

# 2. Preview rollback
alembic downgrade -1 --sql > rollback_preview.sql

# 3. Execute rollback
alembic downgrade -1

# 4. Verify
alembic current
```

### Emergency Rollback

```bash
# When migration is stuck/hung
# 1. Check for locks
SELECT * FROM pg_locks WHERE NOT granted;

# 2. Cancel long-running queries
SELECT pg_cancel_backend(pid) FROM pg_stat_activity 
WHERE state = 'active' AND query LIKE '%ALTER TABLE%';

# 3. Force rollback (use with caution)
alembic downgrade -1
```

---

## Testing Migrations

### Pre-Migration Checklist

```bash
# 1. Backup database
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d_%H%M%S).sql

# 2. Run validation
/migrate validate --strict

# 3. Test in staging
/migrate test --revision head --with-data

# 4. Review rollback
/migrate rollback --revision head --preview
```

### Migration Testing Script

```python
# tests/test_migrations.py
import pytest
from alembic import command
from alembic.config import Config

def test_migration_up_down():
    """Test upgrade and downgrade are reversible."""
    alembic_cfg = Config("alembic.ini")
    
    # Upgrade
    command.upgrade(alembic_cfg, "head")
    
    # Downgrade
    command.downgrade(alembic_cfg, "-1")
    
    # Upgrade again
    command.upgrade(alembic_cfg, "+1")
```

---

## Cross-Project Coordination

### Shared Migration Patterns

When multiple FORGE projects share database patterns:

```bash
# Check migration status across domain
/migrate status --domain saas-tools

# Sync migration patterns
/migrate sync --source project-a --target project-b
```

### Dependency Tracking

Projects with database dependencies should document:

```yaml
# .forge/migration-deps.yaml
dependencies:
  - project: auth-service
    min_revision: abc123
    reason: "users table schema"
  
migrations:
  - revision: def456
    breaking: false
    requires_downtime: false
    rollback_time: "5 minutes"
```

---

## Scripts

### migrate-generate.sh

Generates migration with validation.

```bash
./.claude/skills/migration-assistant/scripts/migrate-generate.sh \
  --description "add user preferences" \
  --project backend
```

### migrate-validate.sh

Validates migrations for breaking changes.

```bash
./.claude/skills/migration-assistant/scripts/migrate-validate.sh \
  --strict \
  --report validation-report.md
```

### migrate-plan.sh

Plans data migration for large tables.

```bash
./.claude/skills/migration-assistant/scripts/migrate-plan.sh \
  --table orders \
  --rows 10000000 \
  --output migration-plan.md
```

---

## Prompts

### Breaking Change Review

```
Read prompts/breaking-change-review.md for detailed checklist
when reviewing potentially breaking migrations.
```

### Large Table Migration

```
Read prompts/large-table-migration.md for planning
data migrations on tables with >1M rows.
```

### Rollback Checklist

```
Read prompts/rollback-checklist.md for comprehensive
rollback procedure verification.
```

---

## Examples

### Example 1: Adding a Column

```bash
# Generate migration
/migrate generate --description "add user preferences column"

# Output:
# Generated: alembic/versions/20240208_add_user_preferences.py
# 
# Changes detected:
#   + users.preferences (JSONB, nullable)
#
# Validation: ✓ No breaking changes
# Review: Recommended - verify JSON schema
```

### Example 2: Changing Column Type

```bash
# Generate migration  
/migrate generate --description "change email to citext"

# Output:
# ⚠️  WARNING: Potentially breaking change detected
#
# Change: users.email VARCHAR → CITEXT
# Risk: Medium - existing indexes may need rebuild
#
# Recommendation: Use expand-contract pattern
# See: .forge/memories/migration-patterns.md#expand-contract
```

### Example 3: Large Table Migration

```bash
# Plan migration for 10M row table
/migrate plan --table events --rows 10000000

# Output:
# Table: events (10,000,000 estimated rows)
# Strategy: batch (recommended for this size)
# 
# Migration Plan:
# 1. Add new column (instant)
# 2. Backfill in 10K batches (~2 hours)
# 3. Create index concurrently (30 min)
# 4. Validate constraints (5 min)
# 
# Downtime: None
# Total time: ~2.5 hours
# Rollback time: 5 minutes
```

### Example 4: Cross-Project Coordination

```bash
# Check all projects in domain
/migrate status --domain saas-tools

# Output:
# Domain: saas-tools
# 
# auth-service:     head @ 20240208_add_sessions
# billing-service:  head @ 20240207_fix_invoices
# analytics-service: behind by 3 revisions
#
# Recommendation: Sync analytics-service before
# deploying auth-service changes (shared users table)
```

---

## Integration with Other Skills

### Before Migration

- `/auto-test-runner` - Ensure tests pass
- `/auto-security-scan` - Check for security issues in migration

### After Migration

- `/ship-feature` - Commit migration with proper message
- `/living-docs update` - Update schema documentation

### During Issues

- `/fleet-ops save` - Save state before risky migration
- `/handoff-clean` - Document migration state for handoff

---

## Troubleshooting

### "No changes detected"

```bash
# Ensure models are imported in alembic env.py
# Check: alembic/env.py should import all models

# Force generation
alembic revision -m "manual migration"
```

### "Migration fails on downgrade"

```bash
# Check downgrade is implemented
/migrate validate --strict

# Test downgrade
/migrate test --revision head
```

### "Lock timeout during migration"

```bash
# Use statement timeout
SET statement_timeout = '5s';

# Or run during low-traffic period
# See: prompts/large-table-migration.md
```

### "Cross-project dependency conflict"

```bash
# Check dependency status
/migrate status --domain <domain>

# Coordinate deployment order
# Document in .forge/migration-deps.yaml
```

---

## Quick Reference

```bash
# Most common workflow
/migrate generate --description "add X column"
/migrate validate
/migrate test --revision head
/migrate rollback --revision head --preview
# Deploy to staging → production

# Large table changes
/migrate plan --table <table> --rows <count>
# Follow generated plan

# Emergency rollback
alembic downgrade -1
```

---

## Files

- `scripts/migrate-generate.sh` - Generate migrations
- `scripts/migrate-validate.sh` - Validate migrations
- `scripts/migrate-plan.sh` - Plan large migrations
- `scripts/migrate-rollback.sh` - Generate rollback procedures
- `prompts/breaking-change-review.md` - Review checklist
- `prompts/large-table-migration.md` - Large table guide
- `prompts/rollback-checklist.md` - Rollback verification

**Last Updated**: 2026-02-08
**Version**: 1.0
