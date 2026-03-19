//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 165: task_store.go — TaskStore methods
// Targets:
//   NewTaskStore               (task_store.go:30)  — constructor
//   TaskStore.GetTasksByState  (task_store.go:35)  — empty result, with tasks
//   TaskStore.RecordTransition (task_store.go:72)  — valid transition
//   TaskStore.ClaimTask        (task_store.go:108) — no stateMachine path, not claimable
//   TaskStore.GetTransitionHistory (task_store.go:149) — no history, with history
// ---------------------------------------------------------------------------

func newTestTaskStore(t *testing.T) (*TaskStore, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	return NewTaskStore(db), cleanup
}

// ---------------------------------------------------------------------------
// NewTaskStore
// ---------------------------------------------------------------------------

func TestWave165_NewTaskStore_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ts := NewTaskStore(db)
	if ts == nil {
		t.Error("expected non-nil TaskStore")
	}
}

// ---------------------------------------------------------------------------
// TaskStore.GetTasksByState
// ---------------------------------------------------------------------------

func TestWave165_GetTasksByState_Empty(t *testing.T) {
	ts, cleanup := newTestTaskStore(t)
	defer cleanup()
	tasks, err := ts.GetTasksByState(StateQueued)
	if err != nil {
		t.Fatalf("GetTasksByState: %v", err)
	}
	// Empty DB — nil slice is acceptable
	_ = tasks
}

func TestWave165_GetTasksByState_WithTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ts := NewTaskStore(db)

	// Insert a task in QUEUED state
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('task-wave165-1', 'test', 'proj', 'feature', 'wave165 task', 'queued', 'QUEUED', 5, ?, ?)`, now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	tasks, err := ts.GetTasksByState(StateQueued)
	if err != nil {
		t.Fatalf("GetTasksByState: %v", err)
	}
	if len(tasks) == 0 {
		t.Error("expected at least 1 task in QUEUED state")
	}
}

// ---------------------------------------------------------------------------
// TaskStore.RecordTransition
// ---------------------------------------------------------------------------

func TestWave165_RecordTransition_Valid(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ts := NewTaskStore(db)

	// Insert a task to transition
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('task-wave165-2', 'test', 'proj', 'feature', 'wave165 rec', 'queued', 'QUEUED', 5, ?, ?)`, now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	err = ts.RecordTransition("task-wave165-2", StateQueued, StateDispatched, "test transition")
	if err != nil {
		t.Errorf("RecordTransition: %v", err)
	}

	// Verify state was updated
	var state string
	db.QueryRow(`SELECT state FROM tasks WHERE id = 'task-wave165-2'`).Scan(&state)
	if state != string(StateDispatched) {
		t.Errorf("expected state DISPATCHED, got %q", state)
	}
}

func TestWave165_RecordTransition_NonExistentTask(t *testing.T) {
	ts, cleanup := newTestTaskStore(t)
	defer cleanup()
	// Non-existent task — UPDATE affects 0 rows but no error
	err := ts.RecordTransition("nonexistent-task-xyz", StateQueued, StateDispatched, "test")
	// RecordTransition may succeed (0 rows updated) or fail at insert (FK constraint)
	// Either way, just verify no panic
	_ = err
}

// ---------------------------------------------------------------------------
// TaskStore.ClaimTask (standalone path — stateMachine is nil)
// ---------------------------------------------------------------------------

func TestWave165_ClaimTask_Standalone_NotClaimable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ts := NewTaskStore(db)

	// Insert a task in DISPATCHED state (not QUEUED) → cannot claim
	now := time.Now().UTC().Format(time.RFC3339)
	db.Exec(`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('task-wave165-3', 'test', 'proj', 'feature', 'wave165 claim', 'assigned', 'DISPATCHED', 5, ?, ?)`, now, now)

	// Ensure stateMachine is nil for standalone path
	prevSM := stateMachine
	stateMachine = nil
	defer func() { stateMachine = prevSM }()

	err := ts.ClaimTask("task-wave165-3", "kimi")
	if err == nil {
		t.Error("expected error when task not in QUEUED state")
	}
}

func TestWave165_ClaimTask_Standalone_Claimable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ts := NewTaskStore(db)

	now := time.Now().UTC().Format(time.RFC3339)
	db.Exec(`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('task-wave165-4', 'test', 'proj', 'feature', 'wave165 claimable', 'queued', 'QUEUED', 5, ?, ?)`, now, now)

	prevSM := stateMachine
	stateMachine = nil
	defer func() { stateMachine = prevSM }()

	err := ts.ClaimTask("task-wave165-4", "kimi")
	if err != nil {
		t.Errorf("ClaimTask: %v", err)
	}

	var state string
	db.QueryRow(`SELECT state FROM tasks WHERE id = 'task-wave165-4'`).Scan(&state)
	if state != string(StateDispatched) {
		t.Errorf("expected DISPATCHED after claim, got %q", state)
	}
}

// ---------------------------------------------------------------------------
// TaskStore.GetTransitionHistory
// ---------------------------------------------------------------------------

func TestWave165_GetTransitionHistory_Empty(t *testing.T) {
	ts, cleanup := newTestTaskStore(t)
	defer cleanup()
	history, err := ts.GetTransitionHistory("nonexistent-task-xyz")
	if err != nil {
		t.Fatalf("GetTransitionHistory: %v", err)
	}
	_ = history // nil is acceptable for no history
}

func TestWave165_GetTransitionHistory_WithHistory(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ts := NewTaskStore(db)

	now := time.Now().UTC().Format(time.RFC3339)
	db.Exec(`INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
		VALUES ('task-wave165-5', 'test', 'proj', 'feature', 'wave165 hist', 'queued', 'QUEUED', 5, ?, ?)`, now, now)

	if err := ts.RecordTransition("task-wave165-5", StateQueued, StateDispatched, "test"); err != nil {
		t.Fatalf("RecordTransition: %v", err)
	}

	history, err := ts.GetTransitionHistory("task-wave165-5")
	if err != nil {
		t.Fatalf("GetTransitionHistory: %v", err)
	}
	if len(history) == 0 {
		t.Error("expected at least 1 transition in history")
	}
	if history[0].FromState != StateQueued || history[0].ToState != StateDispatched {
		t.Errorf("unexpected transition: %v→%v", history[0].FromState, history[0].ToState)
	}
}
