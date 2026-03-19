# Breaking Change Review Checklist

Use this checklist when reviewing migrations that may contain breaking changes.

## Critical Breaking Changes (BLOCK DEPLOYMENT)

### Data Loss Risks

- [ ] **Column Drops**
  - Verify column is not referenced by application code
  - Confirm data has been migrated or is no longer needed
  - Check for foreign key dependencies
  - Ensure backups exist

- [ ] **Table Drops**
  - Confirm table is truly unused (check query logs)
  - Verify no foreign keys reference this table
  - Ensure complete data export exists
  - Document reason for removal

- [ ] **Database/Schema Drops**
  - Verify no cross-database dependencies
  - Confirm all objects backed up
  - Check replication/lag concerns

### Constraint Changes

- [ ] **NOT NULL Added**
  - Verify no NULL values exist in column
  - Provide DEFAULT value or backfill strategy
  - Check application handles constraint violations
  - Consider: add as NULL first, backfill, then add constraint

- [ ] **Unique Constraints**
  - Verify no duplicate values exist
  - Check case sensitivity requirements
  - Consider partial unique indexes for soft deletes
  - Plan for violation handling

- [ ] **Check Constraints**
  - Verify all existing data passes constraint
  - Check constraint logic is correct
  - Consider performance impact on writes

### Type Changes

- [ ] **Column Type Narrowing**
  - Check for data truncation (e.g., VARCHAR(100) → VARCHAR(50))
  - Verify all values fit in new type
  - Check index rebuild requirements
  - Test with maximum values

- [ ] **Column Type Changes**
  - Verify cast compatibility (TEXT → INT may fail)
  - Check for timezone handling (TIMESTAMP → TIMESTAMPTZ)
  - Verify no precision loss (FLOAT → DECIMAL)
  - Consider using USING clause for custom conversion

## Warning Level Changes (REQUIRE REVIEW)

### Index Changes

- [ ] **Index Drops**
  - Check query performance impact (EXPLAIN ANALYZE)
  - Verify index not used for constraints
  - Review query logs for index usage
  - Consider marking unused before drop

- [ ] **Index Type Changes**
  - Verify benefits outweigh migration cost
  - Check disk space for rebuild
  - Plan for concurrent index creation

### Foreign Key Changes

- [ ] **FK Addition**
  - Verify referential integrity of existing data
  - Check for orphaned records
  - Plan for CASCADE behavior
  - Consider: add without VALIDATE, then validate separately

- [ ] **FK Removal**
  - Verify application enforces referential integrity
  - Check for data consistency concerns
  - Document reason for removal

### Default Value Changes

- [ ] **Default Value Modified**
  - Check impact on existing INSERT statements
  - Verify new default is semantically correct
  - Document behavioral change
  - Consider application-level default instead

## Application Compatibility

### API/Schema Changes

- [ ] **Column Renames**
  - Add new column with new name first
  - Dual-write to both columns
  - Update application to use new name
  - Drop old column later
  - Never rename in-place in production

- [ ] **Enum Value Changes**
  - Adding values: usually safe
  - Removing values: requires data migration
  - Renaming values: breaking change
  - Consider using lookup tables for evolving enums

### Timing Considerations

- [ ] **Long-Running Migrations**
  - Estimate migration time on production-like data
  - Plan for deployment window
  - Consider batching for large tables
  - Test lock behavior

- [ ] **Lock-Heavy Operations**
  - ACCESS EXCLUSIVE locks block reads/writes
  - Plan for low-traffic window
  - Consider online migration alternatives
  - Have kill switch ready

## Review Questions

For each breaking change, answer:

1. **What data is affected?** (rows, tables, columns)
2. **What applications are affected?** (services, APIs, workers)
3. **What is the rollback plan?** (tested, documented, < 5 min?)
4. **What is the blast radius?** (users, revenue, data integrity)
5. **What monitoring is in place?** (alerts, metrics, error tracking)

## Decision Matrix

| Change Type | Development | Staging | Production |
|-------------|-------------|---------|------------|
| Column drop | Auto-OK | Review | Block + Expand-Contract |
| Type change | Auto-OK | Review | Block + Dual-schema |
| Index drop | Auto-OK | OK | Review + Query check |
| NOT NULL add | Auto-OK | Review | Block + 3-step deploy |
| Table drop | Review | Block | Block + Archive first |

## Post-Review Actions

- [ ] Document breaking changes in CHANGELOG
- [ ] Update API documentation
- [ ] Notify dependent teams
- [ ] Schedule deployment window if needed
- [ ] Prepare monitoring dashboards
- [ ] Test rollback procedure
- [ ] Have emergency contact ready
