//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"os"
	"testing"
	"time"
)


func setupStateSyncDB(t *testing.T) func() {
	t.Helper()
	dbPath := fmt.Sprintf("/tmp/test_state_sync_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("setupStateSyncDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		os.Remove(dbPath)
		t.Fatalf("MigrateUp: %v", err)
	}
	oldDB := getDBConn()
	setDBConn(db)
	return func() {
		setDBConn(oldDB)
		db.Close()
		os.Remove(dbPath)
	}
}

// insertTaskForSync inserts a minimal task row for state sync testing.
func insertTaskForSync(t *testing.T, id, domain, title, status, state string) {
	t.Helper()
	db := getDBConn()
	_, err := db.Exec(
		`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		 VALUES (?, ?, 'proj', 'feature', ?, ?, ?, 5, datetime('now'), datetime('now'))`,
		id, domain, title, status, state,
	)
	if err != nil {
		t.Fatalf("insertTaskForSync: %v", err)
	}
}

func TestSyncStatusState_NoDesynced(t *testing.T) {
	cleanup := setupStateSyncDB(t)
	defer cleanup()

	db := getDBConn()
	// Insert task already in sync
	insertTaskForSync(t, "task-sync-1", "forge", "Synced task", "queued", "QUEUED")

	// Should not error and total = 0
	syncStatusState(db) // verifies it runs without panic
}

func TestSyncStatusState_FixesDesynced(t *testing.T) {
	cleanup := setupStateSyncDB(t)
	defer cleanup()

	db := getDBConn()
	// Insert task with status=queued but state='' (desynced)
	insertTaskForSync(t, "task-desync-1", "forge", "Desynced queued", "queued", "")
	insertTaskForSync(t, "task-desync-2", "forge", "Desynced assigned", "assigned", "")
	insertTaskForSync(t, "task-desync-3", "forge", "Desynced executing", "executing", "")
	insertTaskForSync(t, "task-desync-4", "forge", "Desynced completed", "completed", "")

	syncStatusState(db)

	// Verify states were updated
	tests := []struct{ id, wantState string }{
		{"task-desync-1", "QUEUED"},
		{"task-desync-2", "DISPATCHED"},
		{"task-desync-3", "RUNNING"},
		{"task-desync-4", "COMPLETED"},
	}
	for _, tc := range tests {
		var state string
		err := db.QueryRow("SELECT state FROM tasks WHERE id = ?", tc.id).Scan(&state)
		if err != nil {
			t.Errorf("task %s: query error: %v", tc.id, err)
			continue
		}
		if state != tc.wantState {
			t.Errorf("task %s: expected state %q, got %q", tc.id, tc.wantState, state)
		}
	}
}

func TestSyncStatusState_DoesNotTouchAPPROVED(t *testing.T) {
	cleanup := setupStateSyncDB(t)
	defer cleanup()

	db := getDBConn()
	// APPROVED task — should not be touched even if status says queued
	insertTaskForSync(t, "task-approved-1", "forge", "Approved task", "queued", "APPROVED")

	syncStatusState(db)

	var state string
	err := db.QueryRow("SELECT state FROM tasks WHERE id = ?", "task-approved-1").Scan(&state)
	if err != nil {
		t.Fatalf("query error: %v", err)
	}
	if state != "APPROVED" {
		t.Errorf("expected APPROVED to be preserved, got %q", state)
	}
}

func TestSyncStatusState_FailedMapsToCompleted(t *testing.T) {
	cleanup := setupStateSyncDB(t)
	defer cleanup()

	db := getDBConn()
	insertTaskForSync(t, "task-failed-1", "forge", "Failed task", "failed", "")

	syncStatusState(db)

	var state string
	db.QueryRow("SELECT state FROM tasks WHERE id = ?", "task-failed-1").Scan(&state)
	if state != "COMPLETED" {
		t.Errorf("expected failed->COMPLETED, got %q", state)
	}
}

func TestStartStateSyncJob_RunsWithoutPanic(t *testing.T) {
	cleanup := setupStateSyncDB(t)
	defer cleanup()

	db := getDBConn()
	// Use a very long interval so it runs once immediately, then test exits
	startStateSyncJob(db, 24*time.Hour)
	// Give goroutine time to run the first sync
	time.Sleep(50 * time.Millisecond)
	// No panic = pass
}
