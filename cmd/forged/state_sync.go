//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"log"
	"time"
)

// syncStatusState copies legacy status values to the state column for any desynced tasks.
// Runs on a ticker to fix tasks that were created before FSM wiring.
// Does not touch APPROVED tasks (terminal FSM state).
func syncStatusState(db *sql.DB) {
	mappings := []struct{ status, state string }{
		{"queued", "QUEUED"},
		{"assigned", "DISPATCHED"},
		{"executing", "RUNNING"},
		{"completed", "COMPLETED"},
		{"failed", "COMPLETED"}, // terminal state per council decision
	}
	total := 0
	for _, m := range mappings {
		// Update tasks where status matches and state is desynced
		// Exclude APPROVED tasks (terminal FSM state per constraint)
		res, err := db.Exec(
			`UPDATE tasks SET state = ?, updated_at = datetime('now') 
			 WHERE status = ? 
			 AND (state IS NULL OR state = '' OR state != ?)
			 AND (state IS NULL OR state != 'APPROVED')`,
			m.state, m.status, m.state,
		)
		if err != nil {
			log.Printf("[sync] status->state error (%s->%s): %v", m.status, m.state, err)
			continue
		}
		n, _ := res.RowsAffected()
		if n > 0 {
			log.Printf("[sync] status->state: synced %d tasks (%s->%s)", n, m.status, m.state)
		}
		total += int(n)
	}
	if total > 0 {
		log.Printf("[sync] status->state: total synced %d desynced tasks", total)
	} else {
		log.Printf("[sync] status->state: no desynced tasks found")
	}
}

// startStateSyncJob starts a background goroutine that syncs status->state every interval.
func startStateSyncJob(db *sql.DB, interval time.Duration) {
	go func() {
		// Run once immediately on startup to fix existing desync
		syncStatusState(db)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for range ticker.C {
			syncStatusState(db)
		}
	}()
}
