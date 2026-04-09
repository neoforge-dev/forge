//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
)

// FleetCounts holds the canonical agent and task counts used across all
// status/summary endpoints. All five control-plane endpoints (forge status,
// /api/openclaw/status, /api/dashboard/summary, /api/fleet/summary, /ui)
// call getFleetCounts so they always report identical numbers.
type FleetCounts struct {
	// Agent counts
	OnlineAgents int // last_seen within 5 minutes
	TotalAgents  int // all rows in agent_heartbeats

	// Task counts
	QueuedTasks      int // status = 'queued'
	RunningTasks     int // status IN ('assigned', 'executing')
	CompletedTasks24h int // status = 'completed' AND updated_at within 24 hours
}

// getFleetCounts executes a single round-trip per table to fetch the canonical
// fleet state. It is safe to call from concurrent HTTP handlers.
//
// Canonical query definitions:
//
//	OnlineAgents  — agent_heartbeats.last_seen > datetime('now', '-5 minutes')
//	TotalAgents   — COUNT(*) FROM agent_heartbeats
//	QueuedTasks   — tasks.status = 'queued'
//	RunningTasks  — tasks.status IN ('assigned', 'executing')
//	CompletedTasks24h — tasks.status = 'completed' AND updated_at > datetime('now', '-24 hours')
func getFleetCounts(ctx context.Context, db *sql.DB) FleetCounts {
	var counts FleetCounts
	if db == nil {
		return counts
	}

	// Agent totals — one query, two aggregations.
	db.QueryRowContext(ctx, `
		SELECT
			COUNT(*),
			COALESCE(SUM(CASE WHEN last_seen > datetime('now', '-5 minutes') THEN 1 ELSE 0 END), 0)
		FROM agent_heartbeats
	`).Scan(&counts.TotalAgents, &counts.OnlineAgents)

	// Task totals — one query, three aggregations.
	db.QueryRowContext(ctx, `
		SELECT
			COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN status IN ('assigned', 'executing') THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN status = 'completed'
			              AND updated_at > datetime('now', '-24 hours') THEN 1 ELSE 0 END), 0)
		FROM tasks
	`).Scan(&counts.QueuedTasks, &counts.RunningTasks, &counts.CompletedTasks24h)

	return counts
}
