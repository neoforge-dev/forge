package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func setupTestQueue(t *testing.T) (TaskQueue, func()) {
	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("test_queue_%d_%d.db", time.Now().UnixNano(), os.Getpid()))

	// Open DB and run migrations first
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}

	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("failed to run migrations: %v", err)
	}
	db.Close()

	q, err := NewTaskQueue(dbPath)
	if err != nil {
		t.Fatalf("failed to create queue: %v", err)
	}

	cleanup := func() {
		q.Close()
		os.Remove(dbPath)
	}

	return q, cleanup
}

func TestQueueEnqueue(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	task := Task{
		ID:       "test-task-001",
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Priority: 50,
		Status:   TaskStatusQueued,
	}

	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Verify task was stored
	retrieved, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask failed: %v", err)
	}

	if retrieved.ID != task.ID {
		t.Errorf("expected ID %s, got %s", task.ID, retrieved.ID)
	}

	if retrieved.Domain != task.Domain {
		t.Errorf("expected Domain %s, got %s", task.Domain, retrieved.Domain)
	}

	if retrieved.Status != TaskStatusQueued {
		t.Errorf("expected Status %s, got %s", TaskStatusQueued, retrieved.Status)
	}
}

func TestQueueEnqueueWithDependencies(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	// First enqueue dependency
	depTask := Task{
		ID:       "dep-task-001",
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Priority: 50,
		Status:   TaskStatusQueued,
	}

	if err := q.Enqueue(ctx, depTask); err != nil {
		t.Fatalf("Enqueue dependency failed: %v", err)
	}

	// Then enqueue task with dependency
	task := Task{
		ID:           "test-task-002",
		Domain:       "test-domain",
		Project:      "test-project",
		Type:         TaskTypeFeature,
		Priority:     50,
		Status:       TaskStatusQueued,
		Dependencies: []string{"dep-task-001"},
	}

	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue with deps failed: %v", err)
	}

	// Verify dependencies
	retrieved, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask failed: %v", err)
	}

	if len(retrieved.Dependencies) != 1 || retrieved.Dependencies[0] != "dep-task-001" {
		t.Errorf("expected dependencies [dep-task-001], got %v", retrieved.Dependencies)
	}
}

func TestQueueDequeue(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	agentID := "test-agent-001"

	// Enqueue a task
	task := Task{
		ID:       "test-task-003",
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Priority: 50,
		Status:   TaskStatusQueued,
	}

	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Dequeue the task
	dequeued, err := q.Dequeue(ctx, agentID)
	if err != nil {
		t.Fatalf("Dequeue failed: %v", err)
	}

	if dequeued.ID != task.ID {
		t.Errorf("expected ID %s, got %s", task.ID, dequeued.ID)
	}

	if dequeued.Status != TaskStatusAssigned {
		t.Errorf("expected Status %s after dequeue, got %s", TaskStatusAssigned, dequeued.Status)
	}

	if dequeued.AssignedTo != agentID {
		t.Errorf("expected AssignedTo %s, got %s", agentID, dequeued.AssignedTo)
	}
}

func TestQueueDequeueRespectsDependencies(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	agentID := "test-agent-001"

	// Enqueue two tasks where task2 depends on task1
	task1 := Task{
		ID:       "task-dep-001",
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Priority: 50,
		Status:   TaskStatusQueued,
	}

	task2 := Task{
		ID:           "task-dep-002",
		Domain:       "test-domain",
		Project:      "test-project",
		Type:         TaskTypeFeature,
		Priority:     100, // Higher priority
		Status:       TaskStatusQueued,
		Dependencies: []string{"task-dep-001"},
	}

	if err := q.Enqueue(ctx, task1); err != nil {
		t.Fatalf("Enqueue task1 failed: %v", err)
	}
	if err := q.Enqueue(ctx, task2); err != nil {
		t.Fatalf("Enqueue task2 failed: %v", err)
	}

	// Dequeue should return task1 (task2 has unmet dependency)
	dequeued, err := q.Dequeue(ctx, agentID)
	if err != nil {
		t.Fatalf("Dequeue failed: %v", err)
	}

	if dequeued.ID != "task-dep-001" {
		t.Errorf("expected task-dep-001 (dependency ready), got %s", dequeued.ID)
	}

	// Complete task1
	if err := q.Complete(ctx, "task-dep-001", "done"); err != nil {
		t.Fatalf("Complete failed: %v", err)
	}

	// Now dequeue should return task2
	dequeued2, err := q.Dequeue(ctx, agentID)
	if err != nil {
		t.Fatalf("Second Dequeue failed: %v", err)
	}

	if dequeued2.ID != "task-dep-002" {
		t.Errorf("expected task-dep-002 (now unblocked), got %s", dequeued2.ID)
	}
}

func TestQueueAssignTask(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	agentID := "test-agent-002"

	// Enqueue a task
	task := Task{
		ID:       "test-task-004",
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Priority: 50,
		Status:   TaskStatusQueued,
	}

	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Assign task directly
	if err := q.AssignTask(ctx, task.ID, agentID); err != nil {
		t.Fatalf("AssignTask failed: %v", err)
	}

	// Verify assignment
	retrieved, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask failed: %v", err)
	}

	if retrieved.AssignedTo != agentID {
		t.Errorf("expected AssignedTo %s, got %s", agentID, retrieved.AssignedTo)
	}

	if retrieved.Status != TaskStatusExecuting {
		t.Errorf("expected Status %s after assign, got %s", TaskStatusExecuting, retrieved.Status)
	}
}

func TestQueueComplete(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	// Enqueue and dequeue a task
	task := Task{
		ID:       "test-task-005",
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Priority: 50,
		Status:   TaskStatusQueued,
	}

	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	_, err := q.Dequeue(ctx, "agent-001")
	if err != nil {
		t.Fatalf("Dequeue failed: %v", err)
	}

	// Complete the task
	result := "Task completed successfully"
	if err := q.Complete(ctx, task.ID, result); err != nil {
		t.Fatalf("Complete failed: %v", err)
	}

	// Verify completion
	retrieved, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask failed: %v", err)
	}

	if retrieved.Status != TaskStatusCompleted {
		t.Errorf("expected Status %s after complete, got %s", TaskStatusCompleted, retrieved.Status)
	}

	if retrieved.Result != result {
		t.Errorf("expected Result %s, got %s", result, retrieved.Result)
	}
}

func TestQueueFail(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	// Enqueue and dequeue a task
	task := Task{
		ID:       "test-task-006",
		Domain:   "test-domain",
		Project:  "test-project",
		Type:     TaskTypeFeature,
		Priority: 50,
		Status:   TaskStatusQueued,
	}

	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	_, err := q.Dequeue(ctx, "agent-001")
	if err != nil {
		t.Fatalf("Dequeue failed: %v", err)
	}

	// Fail the task
	errMsg := "Something went wrong"
	if err := q.Fail(ctx, task.ID, errMsg); err != nil {
		t.Fatalf("Fail failed: %v", err)
	}

	// Verify failure
	retrieved, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask failed: %v", err)
	}

	if retrieved.Status != TaskStatusFailed {
		t.Errorf("expected Status %s after fail, got %s", TaskStatusFailed, retrieved.Status)
	}

	if retrieved.Error != errMsg {
		t.Errorf("expected Error %s, got %s", errMsg, retrieved.Error)
	}
}

func TestQueueGetTaskNotFound(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	_, err := q.GetTask(ctx, "non-existent-task")
	if err != ErrTaskNotFound {
		t.Errorf("expected ErrTaskNotFound, got %v", err)
	}
}

func TestQueueDequeueEmpty(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()

	_, err := q.Dequeue(ctx, "agent-001")
	if err == nil {
		t.Error("expected error when dequeuing from empty queue")
	}
}
