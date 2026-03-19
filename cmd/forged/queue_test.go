package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
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

// ── Closed-DB error path coverage ─────────────────────────────────────────────

// setupQueueWithClosedDB creates a TaskQueue whose underlying DB is immediately
// closed, so all SQL-backed operations return "sql: database is closed".
// This exercises the error-handling branches throughout queue.go.
func setupQueueWithClosedDB(t *testing.T) (TaskQueue, func()) {
	t.Helper()
	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("test_qclosed_%d_%d.db", time.Now().UnixNano(), os.Getpid()))

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("setupQueueWithClosedDB: open: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("setupQueueWithClosedDB: migrate: %v", err)
	}

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		db.Close()
		t.Fatalf("setupQueueWithClosedDB: new queue: %v", err)
	}

	// Close the DB NOW — subsequent queue calls will get "sql: database is closed".
	db.Close()

	cleanup := func() {
		os.Remove(dbPath)
		os.Remove(dbPath + "-wal")
		os.Remove(dbPath + "-shm")
	}
	return q, cleanup
}

func TestQueue_ClosedDB_Enqueue(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	task := Task{
		ID: "closed-db-1", Domain: "d", Project: "p", Type: TaskTypeFeature, Priority: 5,
	}
	if err := q.Enqueue(context.Background(), task); err == nil {
		t.Error("expected error on Enqueue with closed DB")
	}
}

func TestQueue_ClosedDB_Dequeue(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	if _, err := q.Dequeue(context.Background(), "agent-x"); err == nil {
		t.Error("expected error on Dequeue with closed DB")
	}
}

func TestQueue_ClosedDB_ClaimTask(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	if err := q.ClaimTask(context.Background(), "no-task", "agent-x"); err == nil {
		t.Error("expected error on ClaimTask with closed DB")
	}
}

func TestQueue_ClosedDB_GetTask(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	if _, err := q.GetTask(context.Background(), "no-task"); err == nil {
		t.Error("expected error on GetTask with closed DB")
	}
}

func TestQueue_ClosedDB_GetStatus(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	if _, err := q.GetStatus(context.Background(), "no-task"); err == nil {
		t.Error("expected error on GetStatus with closed DB")
	}
}

func TestQueue_ClosedDB_ListAllTasks(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	if _, err := q.ListAllTasks(context.Background(), 100); err == nil {
		t.Error("expected error on ListAllTasks with closed DB")
	}
}

func TestQueue_ClosedDB_Pause(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	if err := q.Pause(context.Background(), "no-task"); err == nil {
		t.Error("expected error on Pause with closed DB")
	}
}

func TestQueue_ClosedDB_Resume(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	if err := q.Resume(context.Background(), "no-task"); err == nil {
		t.Error("expected error on Resume with closed DB")
	}
}

// TestQueue_EventTable_Missing covers the error path in writeEvent (line 882: _ = err).
// writeEvent is called after the main SQL operation succeeds, so to hit its error
// path we need the task_events table to be absent while the tasks table exists.
func TestQueue_EventTable_Missing(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	// Only create tasks table — no task_events table.
	if _, err := db.Exec(`CREATE TABLE tasks (id TEXT PRIMARY KEY, domain TEXT DEFAULT '', project TEXT DEFAULT '')`); err != nil {
		t.Fatalf("create tasks: %v", err)
	}

	q := &sqliteTaskQueue{db: db, bad: make(map[string]bool)}
	// writeEvent tries INSERT INTO task_events, which fails silently.
	q.writeEvent(context.Background(), "t1", "domain", "proj", "test.event", nil)
	// Test passes if no panic — the error is intentionally swallowed.
}

// TestQueuePendingDispatch_UnmarshalablePayload covers the json.Marshal error path
// in QueuePendingDispatch (return err when Marshal fails).
func TestQueuePendingDispatch_UnmarshalablePayload(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	// channels are not JSON-serializable; json.Marshal will return an error.
	badPayload := map[string]interface{}{"ch": make(chan int)}
	err := q.QueuePendingDispatch(context.Background(), "task-id", "agent-id", badPayload)
	if err == nil {
		t.Error("expected error from QueuePendingDispatch with unmarshalable payload")
	}
}

// TestQueue_ClosedDB_QueuePendingDispatch covers the ExecContext error path.
func TestQueue_ClosedDB_QueuePendingDispatch(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	err := q.QueuePendingDispatch(context.Background(), "t1", "agent", nil)
	if err == nil {
		t.Error("expected error from QueuePendingDispatch with closed DB")
	}
}

// TestQueue_ClosedDB_GetPendingDispatches covers the QueryContext error path.
func TestQueue_ClosedDB_GetPendingDispatches(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	_, err := q.GetPendingDispatches(context.Background(), "agent-id")
	if err == nil {
		t.Error("expected error from GetPendingDispatches with closed DB")
	}
}

// TestQueue_ClosedDB_MarkDispatchSent covers the ExecContext error path.
func TestQueue_ClosedDB_MarkDispatchSent(t *testing.T) {
	q, cleanup := setupQueueWithClosedDB(t)
	defer cleanup()

	err := q.MarkDispatchSent(context.Background(), 1)
	if err == nil {
		t.Error("expected error from MarkDispatchSent with closed DB")
	}
}
