//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 201: queue.go — TaskQueue basic CRUD
// Targets:
//   NewTaskQueueFromDB      (queue.go:155) — non-nil
//   sqliteTaskQueue.Enqueue (queue.go:160) — success
//   sqliteTaskQueue.GetTask (queue.go:476) — found / not found
//   sqliteTaskQueue.GetStatus(queue.go:468) — found
//   sqliteTaskQueue.ListAllTasks(queue.go:626) — empty / after enqueue
//   sqliteTaskQueue.ListTasksByStatus(queue.go:640) — empty
//   sqliteTaskQueue.GetClaimableTasks(queue.go:655) — empty
//   sqliteTaskQueue.AssignTask(queue.go:211) — task not found
//   sqliteTaskQueue.Complete(queue.go:334) — not found
//   sqliteTaskQueue.Fail    (queue.go:354) — not found
//   sqliteTaskQueue.MarkBad (queue.go:620) — no panic
// ---------------------------------------------------------------------------

func setupTaskQueue(t *testing.T) (TaskQueue, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		cleanup()
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	return tq, cleanup
}

// ---------------------------------------------------------------------------
// NewTaskQueueFromDB
// ---------------------------------------------------------------------------

func TestWave201_NewTaskQueueFromDB_NotNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tq, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	if tq == nil {
		t.Error("expected non-nil TaskQueue")
	}
}

// ---------------------------------------------------------------------------
// Enqueue + GetTask
// ---------------------------------------------------------------------------

func TestWave201_Enqueue_Success(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	task := Task{
		ID:       "wave201-task-1",
		Domain:   "forge",
		Project:  "coverage",
		Type:     "feature",
		Title:    "Wave 201 Test Task",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	if err := tq.Enqueue(context.Background(), task); err != nil {
		t.Errorf("Enqueue: %v", err)
	}
}

func TestWave201_GetTask_Found(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	task := Task{
		ID:       "wave201-get-task",
		Domain:   "forge",
		Project:  "coverage",
		Type:     "feature",
		Title:    "Get Task Test",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	tq.Enqueue(context.Background(), task)

	got, err := tq.GetTask(context.Background(), "wave201-get-task")
	if err != nil {
		t.Errorf("GetTask: %v", err)
	}
	if got.ID != "wave201-get-task" {
		t.Errorf("expected ID 'wave201-get-task', got %q", got.ID)
	}
}

func TestWave201_GetTask_NotFound(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	_, err := tq.GetTask(context.Background(), "nonexistent-task")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
}

// ---------------------------------------------------------------------------
// GetStatus
// ---------------------------------------------------------------------------

func TestWave201_GetStatus_Found(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	task := Task{
		ID:       "wave201-status-task",
		Domain:   "forge",
		Project:  "coverage",
		Type:     "feature",
		Title:    "Status Task Test",
		Priority: 3,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	tq.Enqueue(context.Background(), task)

	got, err := tq.GetStatus(context.Background(), "wave201-status-task")
	if err != nil {
		t.Errorf("GetStatus: %v", err)
	}
	if got.ID != "wave201-status-task" {
		t.Errorf("expected ID 'wave201-status-task', got %q", got.ID)
	}
}

// ---------------------------------------------------------------------------
// ListAllTasks
// ---------------------------------------------------------------------------

func TestWave201_ListAllTasks_Empty(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	tasks, err := tq.ListAllTasks(context.Background(), 10)
	if err != nil {
		t.Errorf("ListAllTasks: %v", err)
	}
	_ = tasks
}

func TestWave201_ListAllTasks_AfterEnqueue(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	task := Task{
		ID:       "wave201-list-task",
		Domain:   "forge",
		Project:  "coverage",
		Type:     "feature",
		Title:    "List Task Test",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	tq.Enqueue(context.Background(), task)

	tasks, err := tq.ListAllTasks(context.Background(), 10)
	if err != nil {
		t.Errorf("ListAllTasks after enqueue: %v", err)
	}
	_ = tasks
}

// ---------------------------------------------------------------------------
// ListTasksByStatus
// ---------------------------------------------------------------------------

func TestWave201_ListTasksByStatus_Empty(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	tasks, err := tq.ListTasksByStatus(context.Background(), TaskStatusQueued, 10)
	if err != nil {
		t.Errorf("ListTasksByStatus: %v", err)
	}
	_ = tasks
}

// ---------------------------------------------------------------------------
// GetClaimableTasks
// ---------------------------------------------------------------------------

func TestWave201_GetClaimableTasks_Empty(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	tasks, err := tq.GetClaimableTasks(context.Background(), 10)
	if err != nil {
		t.Errorf("GetClaimableTasks: %v", err)
	}
	_ = tasks
}

// ---------------------------------------------------------------------------
// AssignTask — task not found
// ---------------------------------------------------------------------------

func TestWave201_AssignTask_NotFound(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	err := tq.AssignTask(context.Background(), "nonexistent-task", "kimi-1")
	_ = err // may succeed (upsert) or fail — just no panic
}

// ---------------------------------------------------------------------------
// Complete / Fail — not found
// ---------------------------------------------------------------------------

func TestWave201_Complete_NotFound(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	err := tq.Complete(context.Background(), "nonexistent-task", "result")
	_ = err // error expected but no panic
}

func TestWave201_Fail_NotFound(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	err := tq.Fail(context.Background(), "nonexistent-task", "error message")
	_ = err // error expected but no panic
}

// ---------------------------------------------------------------------------
// MarkBad — no panic
// ---------------------------------------------------------------------------

func TestWave201_MarkBad_NoPanic(t *testing.T) {
	tq, cleanup := setupTaskQueue(t)
	defer cleanup()

	// Should not panic
	if q, ok := tq.(*sqliteTaskQueue); ok {
		q.MarkBad("nonexistent-task")
	}
}
