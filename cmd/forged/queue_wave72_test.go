//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
)

// ---------------------------------------------------------------------------
// queue.go: ReleaseTask — covers agent mismatch path
// ---------------------------------------------------------------------------

func TestWave72_ReleaseTask_AgentMismatch(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave72-release-mismatch", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})
	_ = taskQueue.ClaimTask(ctx, "wave72-release-mismatch", "agent-owner")

	err := taskQueue.ReleaseTask(ctx, "wave72-release-mismatch", "agent-other", "test")
	if err == nil {
		t.Error("expected error releasing task assigned to different agent")
	}
}

func TestWave72_ReleaseTask_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	err := taskQueue.ReleaseTask(ctx, "nonexistent-task-72", "agent-x", "test")
	if err == nil {
		t.Error("expected error for nonexistent task")
	}
}

func TestWave72_ReleaseTask_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave72-release-ok", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})
	_ = taskQueue.ClaimTask(ctx, "wave72-release-ok", "agent-owner")

	err := taskQueue.ReleaseTask(ctx, "wave72-release-ok", "agent-owner", "voluntary release")
	if err != nil {
		t.Errorf("expected successful release, got: %v", err)
	}

	task, err := taskQueue.GetTask(ctx, "wave72-release-ok")
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if task.Status != TaskStatusQueued {
		t.Errorf("expected queued status after release, got %s", task.Status)
	}
}

// ---------------------------------------------------------------------------
// task_store.go: GetTasksByState — covers populated results path
// ---------------------------------------------------------------------------

func TestWave72_TaskStore_GetTasksByState_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewTaskStore(db)
	tasks, err := store.GetTasksByState(StateQueued)
	if err != nil {
		t.Fatalf("GetTasksByState: %v", err)
	}
	_ = tasks
}

func TestWave72_TaskStore_GetTasksByState_WithTasks(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave72-ts-state1", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})
	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave72-ts-state2", Domain: "d", Project: "p",
		Type: TaskTypeBugfix, Priority: 3, Status: TaskStatusQueued,
	})

	store := NewTaskStore(getDBConn())
	tasks, err := store.GetTasksByState(StateQueued)
	if err != nil {
		t.Fatalf("GetTasksByState: %v", err)
	}
	if len(tasks) < 2 {
		t.Errorf("expected at least 2 tasks in QUEUED state, got %d", len(tasks))
	}
}

// ---------------------------------------------------------------------------
// queue.go: checkCircular — covers blocking cycle detection
// ---------------------------------------------------------------------------

func TestWave72_CheckCircular_NoDependency(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave72-circ-a", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})
	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave72-circ-b", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	q, ok := taskQueue.(*sqliteTaskQueue)
	if !ok {
		t.Skip("taskQueue is not sqliteTaskQueue")
	}

	visited := make(map[string]bool)
	if err := q.checkCircular(ctx, "wave72-circ-a", "wave72-circ-b", visited); err != nil {
		t.Errorf("expected no circular dependency, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// queue.go: ListAllTasks with result field
// ---------------------------------------------------------------------------

func TestWave72_ScanTasks_WithResult(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave72-scan-result", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})
	_ = taskQueue.ClaimTask(ctx, "wave72-scan-result", "agent-scan-r")
	_ = taskQueue.Complete(ctx, "wave72-scan-result", "done output")

	tasks, err := taskQueue.ListAllTasks(ctx, 100)
	if err != nil {
		t.Fatalf("ListAllTasks: %v", err)
	}

	for _, task := range tasks {
		if task.ID == "wave72-scan-result" {
			if task.Result != "done output" {
				t.Errorf("expected result='done output', got '%s'", task.Result)
			}
			return
		}
	}
	t.Error("expected to find wave72-scan-result in task list")
}
