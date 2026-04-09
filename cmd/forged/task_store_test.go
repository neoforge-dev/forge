//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"testing"
	"time"

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
			origin TEXT DEFAULT '',
			requester TEXT DEFAULT '',
			source_channel TEXT DEFAULT '',
			failure_context TEXT DEFAULT '',
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

// Wave 264: TaskStore coverage — ClaimTask with StateMachine, GetTransitionHistory ordering.

// TestWave264_TaskStore_ClaimTask_WithStateMachine exercises the branch where
// a global StateMachine is present and ClaimTask delegates to ClaimTransition.
func TestWave264_TaskStore_ClaimTask_WithStateMachine(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a QUEUED task the state machine can claim.
	taskID := "wave264-claim-sm"
	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'test', 'proj', 'feature', 'Wave264 SM claim', 5, 'queued', 'QUEUED', ?, ?)
	`, taskID, now, now); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	// Wire the global stateMachine and restore after the test.
	origSM := stateMachine
	stateMachine = sm
	defer func() { stateMachine = origSM }()

	if err := store.ClaimTask(taskID, "agent-264"); err != nil {
		t.Fatalf("ClaimTask with stateMachine: %v", err)
	}

	// Verify state transitioned to DISPATCHED via the FSM.
	state, err := sm.GetState(taskID)
	if err != nil {
		t.Fatalf("GetState: %v", err)
	}
	if state != StateDispatched {
		t.Errorf("expected state %s, got %s", StateDispatched, state)
	}
}

// TestWave264_TaskStore_GetTransitionHistory_Order ensures that
// GetTransitionHistory returns transitions in ascending time order and that
// multiple transitions are surfaced for a single task.
func TestWave264_TaskStore_GetTransitionHistory_Order(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)

	// Insert a task and record several transitions.
	taskID := "wave264-history"
	now := time.Now().UTC().Format(time.RFC3339)
	if _, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'test', 'proj', 'feature', 'Wave264 history', 5, 'queued', 'QUEUED', ?, ?)
	`, taskID, now, now); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	if err := store.RecordTransition(taskID, StateQueued, StateDispatched, "wave264 step 1"); err != nil {
		t.Fatalf("RecordTransition 1: %v", err)
	}
	// Small sleep to make timestamps distinct in normal environments; not strictly
	// required for ORDER BY but keeps the test intuitive.
	time.Sleep(5 * time.Millisecond)
	if err := store.RecordTransition(taskID, StateDispatched, StateRunning, "wave264 step 2"); err != nil {
		t.Fatalf("RecordTransition 2: %v", err)
	}
	time.Sleep(5 * time.Millisecond)
	if err := store.RecordTransition(taskID, StateRunning, StateCompleted, "wave264 step 3"); err != nil {
		t.Fatalf("RecordTransition 3: %v", err)
	}

	history, err := store.GetTransitionHistory(taskID)
	if err != nil {
		t.Fatalf("GetTransitionHistory: %v", err)
	}
	if len(history) < 3 {
		t.Fatalf("expected at least 3 transitions, got %d", len(history))
	}

	// Ensure transitioned_at is non-decreasing.
	for i := 1; i < len(history); i++ {
		if history[i].TransitionedAt.Before(history[i-1].TransitionedAt) {
			t.Errorf("transition[%d] time %v is before transition[%d] time %v",
				i, history[i].TransitionedAt, i-1, history[i-1].TransitionedAt)
		}
	}
}

// TestTaskStore_ClosedDB_RecordTransition covers the db.Begin() error path.
func TestTaskStore_ClosedDB_RecordTransition(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	store := NewTaskStore(db)
	cleanup() // close before use

	err := store.RecordTransition("any-task", StateQueued, StateDispatched, "test")
	if err == nil {
		t.Error("expected error from RecordTransition with closed DB")
	}
}

// TestTaskStore_ClosedDB_GetTransitionHistory covers the db.Query() error path.
func TestTaskStore_ClosedDB_GetTransitionHistory(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	store := NewTaskStore(db)
	cleanup()

	_, err := store.GetTransitionHistory("any-task")
	if err == nil {
		t.Error("expected error from GetTransitionHistory with closed DB")
	}
}

// TestTaskStore_ClosedDB_GetTasksByState covers the db.Query() error path.
func TestTaskStore_ClosedDB_GetTasksByState(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	store := NewTaskStore(db)
	cleanup()

	_, err := store.GetTasksByState(StateQueued)
	if err == nil {
		t.Error("expected error from GetTasksByState with closed DB")
	}
}
