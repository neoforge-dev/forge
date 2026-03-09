//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

func TestTaskStore(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer db.Close()

	// Setup schema
	_, err = db.Exec(`
		CREATE TABLE tasks (
			id TEXT PRIMARY KEY,
			domain TEXT NOT NULL,
			project TEXT NOT NULL,
			type TEXT NOT NULL,
			priority INTEGER DEFAULT 0,
			status TEXT DEFAULT 'requested',
			state TEXT DEFAULT 'QUEUED',
			lane TEXT,
			assigned_to TEXT,
			plan_version INTEGER DEFAULT 0,
			plan_id TEXT,
			envelope_id TEXT,
			created_at TEXT DEFAULT (datetime('now')),
			updated_at TEXT DEFAULT (datetime('now'))
		);

		CREATE TABLE task_state_transitions (
			id TEXT PRIMARY KEY,
			task_id TEXT NOT NULL,
			from_state TEXT,
			to_state TEXT NOT NULL,
			reason TEXT,
			transitioned_at TEXT NOT NULL DEFAULT (datetime('now')),
			FOREIGN KEY (task_id) REFERENCES tasks(id)
		);
	`)
	if err != nil {
		t.Fatalf("failed to create tables: %v", err)
	}

	store := NewTaskStore(db)

	// Insert a test task
	taskID := "test-task-1"
	_, err = db.Exec(`
		INSERT INTO tasks (id, domain, project, type, state)
		VALUES (?, 'test', 'test', 'feature', 'QUEUED')`, taskID)
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	// Test GetTasksByState
	tasks, err := store.GetTasksByState(StateQueued)
	if err != nil {
		t.Errorf("GetTasksByState failed: %v", err)
	}
	if len(tasks) != 1 || tasks[0].ID != taskID {
		t.Errorf("expected 1 task with ID %s, got %d tasks", taskID, len(tasks))
	}

	// Test RecordTransition
	err = store.RecordTransition(taskID, StateQueued, StateDispatched, "testing transition")
	if err != nil {
		t.Errorf("RecordTransition failed: %v", err)
	}

	// Verify update
	var newState TaskState
	err = db.QueryRow("SELECT state FROM tasks WHERE id = ?", taskID).Scan(&newState)
	if err != nil {
		t.Fatalf("failed to query new state: %v", err)
	}
	if newState != StateDispatched {
		t.Errorf("expected state %s, got %s", StateDispatched, newState)
	}

	// Test GetTransitionHistory
	history, err := store.GetTransitionHistory(taskID)
	if err != nil {
		t.Errorf("GetTransitionHistory failed: %v", err)
	}
	if len(history) != 1 {
		t.Errorf("expected 1 history entry, got %d", len(history))
	} else {
		if history[0].FromState != StateQueued || history[0].ToState != StateDispatched {
			t.Errorf("incorrect transition log: %v", history[0])
		}
	}
}
