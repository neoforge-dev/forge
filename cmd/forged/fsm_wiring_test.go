//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSyncStatusState(t *testing.T) {
	// Set up a fresh DB with migrations for this test.
	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("test_sync_state_%d_%d.db", time.Now().UnixNano(), os.Getpid()))
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("MigrateUp: %v", err)
	}
	setDBConn(db)
	defer func() {
		db.Close()
		setDBConn(nil)
		os.Remove(dbPath)
	}()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	ctx := context.Background()
	task := Task{
		ID: "sync-test-001", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Manually desync: set state to NULL
	if _, err := db.Exec("UPDATE tasks SET state = NULL WHERE id = ?", task.ID); err != nil {
		t.Fatalf("desync setup: %v", err)
	}

	syncStatusState(db)

	// Read state directly — GetTask does not select the state column.
	var gotState sql.NullString
	if err := db.QueryRow("SELECT state FROM tasks WHERE id = ?", task.ID).Scan(&gotState); err != nil {
		t.Fatalf("query state after sync: %v", err)
	}
	if !gotState.Valid || TaskState(gotState.String) != StateQueued {
		t.Errorf("after sync: state = %q, want %q", gotState.String, StateQueued)
	}
}
