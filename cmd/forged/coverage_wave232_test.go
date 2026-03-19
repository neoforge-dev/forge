//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// ============================================================
// coverage_wave232_test.go — TaskStore methods
// Target: task_store.go (NewTaskStore, GetTasksByState, RecordTransition,
//         ClaimTask standalone, GetTransitionHistory)
// ============================================================

func setupTaskStoreDB(t *testing.T) (*TaskStore, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	store := NewTaskStore(db)
	return store, cleanup
}

func makeTestTask232(id string) Task {
	return Task{
		ID:        id,
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "test task " + id,
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
}

func TestWave232_NewTaskStore(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewTaskStore(db)
	if store == nil {
		t.Fatal("NewTaskStore returned nil")
	}
}

func TestWave232_GetTasksByState_Empty(t *testing.T) {
	store, cleanup := setupTaskStoreDB(t)
	defer cleanup()

	tasks, err := store.GetTasksByState(StateQueued)
	if err != nil {
		t.Fatalf("GetTasksByState: %v", err)
	}
	if len(tasks) != 0 {
		t.Errorf("expected 0 tasks, got %d", len(tasks))
	}
}

func TestWave232_GetTasksByState_WithTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Setup queue and enqueue tasks
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	task := makeTestTask232("task-232-state")
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	store := NewTaskStore(db)
	tasks, err := store.GetTasksByState(StateQueued)
	if err != nil {
		t.Fatalf("GetTasksByState: %v", err)
	}
	if len(tasks) == 0 {
		t.Error("expected at least 1 task in QUEUED state")
	}
}

func TestWave232_RecordTransition(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	// Create a task first
	task := makeTestTask232("task-232-trans")
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	store := NewTaskStore(db)
	if err := store.RecordTransition("task-232-trans", StateQueued, StateDispatched, "test transition"); err != nil {
		t.Fatalf("RecordTransition: %v", err)
	}
}

func TestWave232_GetTransitionHistory_Empty(t *testing.T) {
	store, cleanup := setupTaskStoreDB(t)
	defer cleanup()

	history, err := store.GetTransitionHistory("nonexistent-task")
	if err != nil {
		t.Fatalf("GetTransitionHistory: %v", err)
	}
	if len(history) != 0 {
		t.Errorf("expected 0 transitions, got %d", len(history))
	}
}

func TestWave232_GetTransitionHistory_WithHistory(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	task := makeTestTask232("task-232-hist")
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	store := NewTaskStore(db)
	store.RecordTransition("task-232-hist", StateQueued, StateDispatched, "transition 1")
	store.RecordTransition("task-232-hist", StateDispatched, StateRunning, "transition 2")

	history, err := store.GetTransitionHistory("task-232-hist")
	if err != nil {
		t.Fatalf("GetTransitionHistory: %v", err)
	}
	if len(history) < 2 {
		t.Errorf("expected at least 2 transitions, got %d", len(history))
	}
}

func TestWave232_ClaimTask_Standalone(t *testing.T) {
	// Test ClaimTask when stateMachine is nil (standalone mode)
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	// Temporarily nil the state machine to test standalone path
	origSM := stateMachine
	stateMachine = nil
	defer func() { stateMachine = origSM }()

	task := makeTestTask232("task-232-claim-sa")
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	store := NewTaskStore(db)
	if err := store.ClaimTask("task-232-claim-sa", "agent-232"); err != nil {
		t.Fatalf("ClaimTask standalone: %v", err)
	}
}

func TestWave232_ClaimTask_Standalone_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	origSM := stateMachine
	stateMachine = nil
	defer func() { stateMachine = origSM }()

	store := NewTaskStore(db)
	err := store.ClaimTask("nonexistent-task", "agent-232")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
}
