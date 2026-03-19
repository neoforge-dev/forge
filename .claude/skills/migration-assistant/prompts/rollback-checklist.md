# Migration Rollback Checklist

Use this checklist when preparing to rollback a database migration.

## Pre-Rollback Assessment

### 1. Understand the Impact

- [ ] **Why rollback?**
  - [ ] Application errors
  - [ ] Performance degradation
  - [ ] Data corruption
  - [ ] Feature flag disable
  - [ ] Business decision

- [ ] **Scope of impact**
  - [ ] Single service
  - [ ] Multiple services
  - [ ] User-facing features
  - [ ] Internal/admin features

- [ ] **Urgency level**
  - [ ] Critical (data loss/corruption) - Immediate
  - [ ] High (user impact) - Within 15 minutes
  - [ ] Medium (performance) - Within 1 hour
  - [ ] Low (feature change) - Scheduled window

### 2. Verify Rollback Feasibility

- [ ] **Downgrade exists**
  ```bash
  alembic history --verbose
  # Verify target revision has downgrade path
  ```

- [ ] **Downgrade tested**
  - [ ] Tested in development
  - [ ] Tested in staging
  - [ ] Tested with production-like data

- [ ] **No data dependencies**
  - [ ] New data since migration can be lost OR
  - [ ] Plan to preserve new data exists

## Rollback Preparation

### 3. Create Safety Backup

```bash
# Full database backup
pg_dump $DATABASE_URL > full_backup_$(date +%Y%m%d_%H%M%S).sql

# Or just affected tables
pg_dump -t affected_table1 -t affected_table2 $DATABASE_URL > partial_backup.sql

# Verify backup
pg_restore --list full_backup_*.sql | tail -10
```

- [ ] Backup created
- [ ] Backup integrity verified
- [ ] Backup location documented
- [ ] Restore procedure tested (if critical)

### 4. Check System State

- [ ] **Database state**
  ```bash
  # Current revision
  alembic current
  
  # Active connections
  psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity;"
  
  # Long-running transactions
  psql $DATABASE_URL -c "SELECT pid, usename, state, query_start, query 
                         FROM pg_stat_activity 
                         WHERE state = 'active' AND xact_start < NOW() - INTERVAL '5 minutes';"
  ```

- [ ] **Application state**
  - [ ] Deployed version noted
  - [ ] Feature flags documented
  - [ ] Health checks status
  - [ ] Error rate baseline

- [ ] **Infrastructure state**
  - [ ] Replica lag acceptable (< 30s)
  - [ ] Disk space sufficient (> 20%)
  - [ ] No scheduled maintenance

## Rollback Execution

### 5. Prepare Application

- [ ] **Enable maintenance mode** (if applicable)
  ```bash
  # Set maintenance flag
  heroku maintenance:on  # or equivalent
  
  # Or circuit breaker
  curl -X POST $API_URL/admin/circuit-breaker/enable
  ```

- [ ] **Drain connections**
  - [ ] Stop background workers
  - [ ] Wait for HTTP requests to complete
  - [ ] Verify connection count: `SELECT count(*) FROM pg_stat_activity;`

### 6. Execute Rollback

```bash
# Preview (dry run)
alembic downgrade <target> --sql > rollback_preview.sql
cat rollback_preview.sql

# Execute
alembic downgrade <target>
```

- [ ] Preview reviewed
- [ ] Execution started
- [ ] Progress monitored
- [ ] No errors encountered

### 7. Verify Rollback

```bash
# Check revision
alembic current
# Should show target revision

# Verify schema
psql $DATABASE_URL -c "\d table_name"

# Verify data counts
psql $DATABASE_URL -c "SELECT count(*) FROM important_table;"
```

- [ ] Revision matches target
- [ ] Schema correct
- [ ] Row counts reasonable
- [ ] Sample data spot-checked

### 8. Restore Application

- [ ] **Disable maintenance mode**
  ```bash
  heroku maintenance:off  # or equivalent
  ```

- [ ] **Restart services**
  - [ ] Background workers
  - [ ] API servers
  - [ ] Cache warm-up

- [ ] **Verify health**
  - [ ] Health checks pass
  - [ ] Smoke tests pass
  - [ ] Error rates normal

## Post-Rollback

### 9. Verify System Health

- [ ] **Application metrics**
  - [ ] Error rate < baseline
  - [ ] Response time normal
  - [ ] Success rate > 99.9%

- [ ] **Database metrics**
  - [ ] Connection count normal
  - [ ] No long-running queries
  - [ ] Replication lag acceptable

- [ ] **Business metrics**
  - [ ] User actions succeeding
  - [ ] Revenue/event flow restored
  - [ ] No customer complaints

### 10. Document and Communicate

- [ ] **Incident timeline**
  - [ ] Migration deployed at: ___
  - [ ] Issue detected at: ___
  - [ ] Rollback started at: ___
  - [ ] Rollback completed at: ___

- [ ] **Stakeholder notification**
  - [ ] Team notified
  - [ ] Management updated (if significant impact)
  - [ ] Customers notified (if user-facing)

- [ ] **Documentation**
  - [ ] Rollback reason documented
  - [ ] Lessons learned captured
  - [ ] Runbook updated if needed

## Special Scenarios

### Scenario: Rollback with Data Loss

When new data exists that can't be migrated back:

1. **Preserve new data**
   ```sql
   CREATE TABLE new_data_backup AS 
   SELECT * FROM table WHERE created_at > 'migration_time';
   ```

2. **Execute rollback**

3. **Handle preserved data**
   - Export for manual processing
   - Or accept loss with approval

### Scenario: Downgrade Missing

When downgrade() is not implemented:

1. **Manual SQL rollback**
   - Write SQL to reverse changes
   - Test on copy first
   - Execute with monitoring

2. **Restore from backup**
   - If manual SQL too risky
   - Accept data loss window

3. **Add downgrade after**
   - Fix migration with proper downgrade
   - Forward-fix instead of rollback

### Scenario: Cascading Rollbacks

When multiple migrations need rollback:

```bash
# Rollback multiple steps
alembic downgrade -3  # 3 steps back

# Or to specific revision
alembic downgrade abc123
```

- Rollback in reverse order of application
- Verify each step
- Test between each rollback

### Scenario: Failed Rollback

When rollback fails:

1. **Don't panic**
   - Stop and assess
   - Don't retry blindly

2. **Preserve state**
   ```bash
   # Current state
   alembic current > rollback_failure_state.txt
   pg_dump $DATABASE_URL > emergency_backup.sql
   ```

3. **Analyze error**
   - Check logs
   - Identify blocking issue
   - Determine if fixable

4. **Options**
   - Fix blocking issue, retry
   - Restore from backup
   - Forward-fix with new migration

## Rollback Verification Queries

```sql
-- Verify table structure
\d table_name

-- Verify constraints
SELECT conname, contype, pg_get_constraintdef(oid) 
FROM pg_constraint 
WHERE conrelid = 'table_name'::regclass;

-- Verify indexes
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'table_name';

-- Verify row counts
SELECT 
    schemaname,
    relname,
    n_live_tup as row_count
FROM pg_stat_user_tables 
WHERE relname = 'table_name';

-- Check for data anomalies
SELECT 
    count(*) as total,
    count(column_name) as non_null,
    count(DISTINCT column_name) as unique_values
FROM table_name;
```

## Emergency Contacts

- Database Admin: ___________
- Platform Engineer: ___________
- Team Lead: ___________
- Escalation: ___________

---

**Last Updated:** 2026-02-08  
**Version:** 1.0
