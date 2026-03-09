//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
)

// --- Pause / Resume ---

func TestQueuePause_Success(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID: "pause-test-001", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	if err := sq.Pause(ctx, task.ID); err != nil {
		t.Fatalf("Pause: %v", err)
	}

	got, _ := q.GetTask(ctx, task.ID)
	if got.Status != TaskStatusPaused {
		t.Errorf("status = %s, want %s", got.Status, TaskStatusPaused)
	}
}

func TestQueuePause_NotFound(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	sq := q.(*sqliteTaskQueue)
	if err := sq.Pause(context.Background(), "no-such"); err == nil {
		t.Fatal("expected error for unknown task")
	}
}

func TestQueuePause_AlreadyCompleted(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID: "pause-test-002", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	_, _ = q.Dequeue(ctx, "agent-1")
	_ = q.Complete(ctx, task.ID, "done")

	sq := q.(*sqliteTaskQueue)
	// Pausing a completed task must fail (no rows updated).
	if err := sq.Pause(ctx, task.ID); err == nil {
		t.Fatal("expected error when pausing a completed task")
	}
}

func TestQueueResume_Success(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID: "resume-test-001", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	_ = sq.Pause(ctx, task.ID)

	if err := sq.Resume(ctx, task.ID); err != nil {
		t.Fatalf("Resume: %v", err)
	}

	got, _ := q.GetTask(ctx, task.ID)
	if got.Status != TaskStatusExecuting {
		t.Errorf("status = %s, want %s", got.Status, TaskStatusExecuting)
	}
}

func TestQueueResume_NotPaused(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID: "resume-test-002", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	// Resuming a task that's not paused should fail.
	if err := sq.Resume(ctx, task.ID); err == nil {
		t.Fatal("expected error when resuming a non-paused task")
	}
}

// --- GetStatus ---

func TestQueueGetStatus(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID: "status-test-001", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	got, err := sq.GetStatus(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetStatus: %v", err)
	}
	if got.ID != task.ID {
		t.Errorf("ID = %s, want %s", got.ID, task.ID)
	}
	if got.Status != TaskStatusQueued {
		t.Errorf("Status = %s, want %s", got.Status, TaskStatusQueued)
	}
}

func TestQueueGetStatus_NotFound(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	sq := q.(*sqliteTaskQueue)
	_, err := sq.GetStatus(context.Background(), "no-such")
	if err == nil {
		t.Fatal("expected error for unknown task")
	}
}

// --- ListAllTasks ---

func TestQueueListAllTasks(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	for i := 0; i < 3; i++ {
		task := Task{
			ID:       "list-test-" + string(rune('0'+i)),
			Domain:   "test", Project: "proj",
			Type:     TaskTypeFeature,
			Priority: 5, Status: TaskStatusQueued,
		}
		if err := q.Enqueue(ctx, task); err != nil {
			t.Fatalf("Enqueue %d: %v", i, err)
		}
	}

	sq := q.(*sqliteTaskQueue)
	tasks, err := sq.ListAllTasks(ctx, 10)
	if err != nil {
		t.Fatalf("ListAllTasks: %v", err)
	}
	if len(tasks) != 3 {
		t.Errorf("len(tasks) = %d, want 3", len(tasks))
	}
}

func TestQueueListAllTasks_Limit(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	for i := 0; i < 5; i++ {
		task := Task{
			ID:       "limit-test-" + string(rune('0'+i)),
			Domain:   "test", Project: "proj",
			Type:     TaskTypeFeature,
			Priority: 5, Status: TaskStatusQueued,
		}
		_ = q.Enqueue(ctx, task)
	}

	sq := q.(*sqliteTaskQueue)
	tasks, err := sq.ListAllTasks(ctx, 2)
	if err != nil {
		t.Fatalf("ListAllTasks: %v", err)
	}
	if len(tasks) != 2 {
		t.Errorf("len(tasks) = %d, want 2 (limit applied)", len(tasks))
	}
}

// --- ListTasksByStatus ---

func TestQueueListTasksByStatus(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	for i := 0; i < 2; i++ {
		task := Task{
			ID:       "bystatus-queued-" + string(rune('0'+i)),
			Domain:   "test", Project: "proj",
			Type:     TaskTypeFeature,
			Priority: 5, Status: TaskStatusQueued,
		}
		_ = q.Enqueue(ctx, task)
	}
	// Enqueue one, assign it directly, then complete it.
	done := Task{
		ID: "bystatus-done", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	_ = q.Enqueue(ctx, done)
	_ = q.AssignTask(ctx, done.ID, "agent-1")
	_ = q.Complete(ctx, done.ID, "result")

	sq := q.(*sqliteTaskQueue)
	queued, err := sq.ListTasksByStatus(ctx, TaskStatusQueued, 10)
	if err != nil {
		t.Fatalf("ListTasksByStatus queued: %v", err)
	}
	if len(queued) != 2 {
		t.Errorf("queued count = %d, want 2", len(queued))
	}

	completed, err := sq.ListTasksByStatus(ctx, TaskStatusCompleted, 10)
	if err != nil {
		t.Fatalf("ListTasksByStatus completed: %v", err)
	}
	if len(completed) != 1 {
		t.Errorf("completed count = %d, want 1", len(completed))
	}
}

// --- GetClaimableTasks ---

func TestQueueGetClaimableTasks(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	// Two claimable tasks.
	for i := 0; i < 2; i++ {
		task := Task{
			ID:       "claimable-" + string(rune('0'+i)),
			Domain:   "test", Project: "proj",
			Type:     TaskTypeFeature,
			Priority: 5, Status: TaskStatusQueued,
		}
		_ = q.Enqueue(ctx, task)
	}
	// One already assigned.
	assigned := Task{
		ID: "claimable-assigned", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	_ = q.Enqueue(ctx, assigned)
	_ = q.ClaimTask(ctx, assigned.ID, "agent-1")

	sq := q.(*sqliteTaskQueue)
	tasks, err := sq.GetClaimableTasks(ctx, 10)
	if err != nil {
		t.Fatalf("GetClaimableTasks: %v", err)
	}
	if len(tasks) != 2 {
		t.Errorf("claimable count = %d, want 2", len(tasks))
	}
}

// --- ReleaseTask ---

func TestQueueReleaseTask(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID: "release-test-001", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := q.ClaimTask(ctx, task.ID, "agent-1"); err != nil {
		t.Fatalf("ClaimTask: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	if err := sq.ReleaseTask(ctx, task.ID, "agent-1", "testing release"); err != nil {
		t.Fatalf("ReleaseTask: %v", err)
	}

	got, _ := q.GetTask(ctx, task.ID)
	if got.Status != TaskStatusQueued {
		t.Errorf("status after release = %s, want %s", got.Status, TaskStatusQueued)
	}
	if got.AssignedTo != "" {
		t.Errorf("assigned_to after release = %s, want empty", got.AssignedTo)
	}
}

func TestQueueReleaseTask_WrongAgent(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID: "release-test-002", Domain: "test", Project: "proj",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	}
	_ = q.Enqueue(ctx, task)
	_ = q.ClaimTask(ctx, task.ID, "agent-1")

	sq := q.(*sqliteTaskQueue)
	if err := sq.ReleaseTask(ctx, task.ID, "agent-2", "wrong agent"); err == nil {
		t.Fatal("expected error when releasing a task assigned to a different agent")
	}
}

// --- MarkBad ---

func TestQueueMarkBad(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	sq := q.(*sqliteTaskQueue)
	// MarkBad just marks in-memory map — verify it doesn't panic.
	sq.MarkBad("some-task-id")
	if !sq.bad["some-task-id"] {
		t.Error("expected task to be marked bad")
	}
}
