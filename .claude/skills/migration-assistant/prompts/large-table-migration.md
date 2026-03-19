# Large Table Migration Guide

For tables with >1M rows, standard migrations can cause downtime. Use these patterns.

## Decision Tree

```
Row Count?
├── < 100K: Standard migration (seconds)
├── 100K - 1M: Batch migration (minutes)
├── 1M - 10M: CTE batch migration (hours)
└── > 10M: Temp table swap (hours, minimal downtime)
```

## Pattern 1: Standard Migration (< 100K rows)

For small tables, direct DDL is acceptable.

```python
def upgrade():
    op.add_column('small_table', sa.Column('new_field', sa.String()))
    op.execute("UPDATE small_table SET new_field = 'default'")
    op.alter_column('small_table', 'new_field', nullable=False)
```

**Duration:** < 30 seconds
**Downtime:** Brief lock during ALTER
**Rollback:** Simple downgrade

## Pattern 2: Batch Migration (100K - 1M rows)

Process in small batches to avoid long locks.

```python
import time
from alembic import op
import sqlalchemy as sa

BATCH_SIZE = 10000

def upgrade():
    conn = op.get_bind()
    
    # Add column as nullable first
    op.add_column('medium_table', sa.Column('new_field', sa.String()))
    
    # Process in batches
    while True:
        result = conn.execute(sa.text("""
            UPDATE medium_table 
            SET new_field = compute_value(old_field)
            WHERE id IN (
                SELECT id FROM medium_table 
                WHERE new_field IS NULL
                LIMIT :batch_size
            )
            RETURNING id
        """), {"batch_size": BATCH_SIZE})
        
        rows_updated = result.rowcount
        if rows_updated == 0:
            break
            
        # Brief pause to reduce load
        time.sleep(0.1)
    
    # Add constraint after backfill
    op.alter_column('medium_table', 'new_field', nullable=False)
```

**Duration:** 10-60 minutes
**Downtime:** None (row-level locks only)
**Rollback:** Drop column

### Monitoring

```sql
-- Progress query
SELECT 
    COUNT(*) FILTER (WHERE new_field IS NULL) as pending,
    COUNT(*) FILTER (WHERE new_field IS NOT NULL) as completed,
    ROUND(100.0 * COUNT(*) FILTER (WHERE new_field IS NOT NULL) / COUNT(*), 2) as pct_complete
FROM medium_table;
```

## Pattern 3: CTE Migration (1M - 10M rows)

Use CTEs for efficient batch processing with visibility.

```python
import time
from alembic import op
import sqlalchemy as sa

BATCH_SIZE = 50000

def upgrade():
    conn = op.get_bind()
    
    # Add column
    op.add_column('large_table', sa.Column('new_field', sa.String()))
    
    # Create index for efficient filtering
    op.create_index(
        'idx_large_table_new_field_null',
        'large_table',
        ['id'],
        postgresql_where=sa.text('new_field IS NULL')
    )
    
    batch_num = 0
    while True:
        result = conn.execute(sa.text("""
            WITH batch AS (
                SELECT id 
                FROM large_table 
                WHERE new_field IS NULL
                LIMIT :batch_size
            )
            UPDATE large_table t
            SET new_field = compute_value(t.old_field)
            FROM batch b
            WHERE t.id = b.id
        """), {"batch_size": BATCH_SIZE})
        
        rows_updated = result.rowcount
        batch_num += 1
        
        print(f"Batch {batch_num}: {rows_updated} rows updated")
        
        if rows_updated == 0:
            break
            
        # Longer pause for large datasets
        time.sleep(0.5)
    
    # Add constraint
    op.alter_column('large_table', 'new_field', nullable=False)
    
    # Clean up temp index
    op.drop_index('idx_large_table_new_field_null')
```

**Duration:** 1-4 hours
**Downtime:** None
**Rollback:** Drop column

### Optimization Tips

1. **Increase work_mem temporarily:**
   ```sql
   SET work_mem = '256MB';
   ```

2. **Disable triggers if not needed:**
   ```sql
   ALTER TABLE large_table DISABLE TRIGGER ALL;
   -- ... migration ...
   ALTER TABLE large_table ENABLE TRIGGER ALL;
   ```

3. **Monitor locks:**
   ```sql
   SELECT * FROM pg_locks WHERE NOT granted;
   ```

## Pattern 4: Temp Table Swap (> 10M rows)

For very large tables, create new table and atomically swap.

```python
from alembic import op
import sqlalchemy as sa

CHUNK_SIZE = 100000

def upgrade():
    conn = op.get_bind()
    
    # 1. Create new table structure
    op.execute("""
        CREATE TABLE large_table_new (LIKE large_table INCLUDING ALL);
        ALTER TABLE large_table_new ADD COLUMN new_field VARCHAR NOT NULL DEFAULT '';
    """)
    
    # 2. Create trigger for keeping data in sync
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_large_table_new()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO large_table_new VALUES (NEW.*, '');
                RETURN NEW;
            ELSIF TG_OP = 'UPDATE' THEN
                UPDATE large_table_new 
                SET = NEW.*, new_field = compute_value(NEW.old_field)
                WHERE id = NEW.id;
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                DELETE FROM large_table_new WHERE id = OLD.id;
                RETURN OLD;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
        
        CREATE TRIGGER large_table_sync_trigger
        AFTER INSERT OR UPDATE OR DELETE ON large_table
        FOR EACH ROW EXECUTE FUNCTION sync_large_table_new();
    """)
    
    # 3. Backfill in chunks with progress
    offset = 0
    while True:
        result = conn.execute(sa.text("""
            INSERT INTO large_table_new 
            SELECT *, compute_value(old_field) as new_field
            FROM large_table
            ORDER BY id
            LIMIT :chunk_size OFFSET :offset
        """), {"chunk_size": CHUNK_SIZE, "offset": offset})
        
        if result.rowcount == 0:
            break
            
        offset += CHUNK_SIZE
        print(f"Migrated {offset} rows...")
    
    # 4. Verify counts match
    old_count = conn.execute(sa.text("SELECT COUNT(*) FROM large_table")).scalar()
    new_count = conn.execute(sa.text("SELECT COUNT(*) FROM large_table_new")).scalar()
    
    if old_count != new_count:
        raise Exception(f"Count mismatch: {old_count} vs {new_count}")
    
    # 5. Atomic swap (brief lock)
    op.execute("""
        BEGIN;
        ALTER TABLE large_table RENAME TO large_table_old;
        ALTER TABLE large_table_new RENAME TO large_table;
        COMMIT;
    """)
    
    # 6. Cleanup (can be done later)
    op.execute("DROP TABLE large_table_old;")
    op.execute("DROP TRIGGER large_table_sync_trigger ON large_table;")
    op.execute("DROP FUNCTION sync_large_table_new;")
```

**Duration:** 2-8 hours (mostly backfill)
**Downtime:** < 1 second (during swap)
**Rollback:** Swap tables back

### Pre-Swap Checklist

- [ ] Row counts match
- [ ] Sample data verified (10+ random rows)
- [ ] Index count matches
- [ ] Constraint verification passed
- [ ] Application reconnection tested
- [ ] Rollback procedure rehearsed
- [ ] Maintenance window scheduled
- [ ] Monitoring in place

## General Best Practices

### Before Migration

1. **Always backup:**
   ```bash
   pg_dump -t table_name $DATABASE_URL > backup_$(date +%s).sql
   ```

2. **Test on copy:**
   - Restore production backup to staging
   - Run full migration
   - Verify application works

3. **Monitor resources:**
   - Check disk space (need 2x table size for temp table)
   - Monitor replication lag
   - Alert on long-running queries

### During Migration

1. **Progress tracking:**
   - Log batch progress
   - Update external status if long-running
   - Have kill switch ready

2. **Lock monitoring:**
   ```sql
   SELECT pid, state, query_start, query 
   FROM pg_stat_activity 
   WHERE state = 'active' AND query LIKE '%your_table%';
   ```

3. **Resource limits:**
   ```sql
   SET statement_timeout = '1h';
   SET lock_timeout = '10s';
   ```

### After Migration

1. **Verification:**
   ```sql
   SELECT pg_size_pretty(pg_total_relation_size('table_name'));
   SELECT COUNT(*) FROM table_name;
   \d table_name
   ```

2. **Performance check:**
   - Run EXPLAIN ANALYZE on common queries
   - Verify index usage
   - Check for sequential scans

3. **Cleanup:**
   - Update statistics: `ANALYZE table_name;`
   - Remove temporary indexes
   - Archive old table if kept

## Emergency Procedures

### Migration Hangs

```sql
-- Find blocking queries
SELECT * FROM pg_locks WHERE NOT granted;

-- Cancel (polite)
SELECT pg_cancel_backend(pid);

-- Terminate (forceful)
SELECT pg_terminate_backend(pid);
```

### Need to Abort

```sql
-- If in transaction
ROLLBACK;

-- If already committed, prepare rollback migration
alembic downgrade -1
```

### Data Corruption Detected

1. Stop migration immediately
2. Restore from backup
3. Analyze root cause
4. Fix migration and retry on copy
