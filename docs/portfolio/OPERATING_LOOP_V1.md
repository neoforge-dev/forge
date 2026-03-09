# Portfolio Operating Loop V1

**Status:** Active as of 2026-03-08
**Source of truth:** [portfolio-state.yaml](portfolio-state.yaml)
**CLI:** `forge portfolio status|list|show`

## Purpose

FORGE already coordinates engineering work well. V1 of the portfolio loop makes product decisions explicit so the system optimizes for launched revenue, not just internal throughput.

The loop is intentionally small:

`idea -> validate -> build -> deploy -> measure -> monetize -> scale|kill`

## Rules

1. Every tracked product must be in exactly one stage.
2. `build` is not allowed without a validation hypothesis and target ICP.
3. `deploy` is not complete until deploy, analytics, and rollback are defined.
4. `measure` is not complete until one primary metric is named and instrumented.
5. `monetize` means billing is live or a real paid pilot exists.
6. `scale` and `kill` are explicit decisions, not drift.

## Required Product Fields

- `key`, `name`, `domain`
- `stage`, `status`, `owner`
- `icp`
- `current_mrr`, `target_mrr`
- `deploy_ready`, `analytics_ready`, `billing_ready`
- `next_gate`, `next_action`
- `primary_metric`, `primary_risk`

## Why This Exists

The main FORGE gap is not dispatch power. It is the absence of a hard operating model from idea to revenue. This file and the portfolio state file create that thin operating layer without inventing a new service.
