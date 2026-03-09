//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// setupCoverageQueue creates an isolated DB + queue and wires all globals.
// Mirrors setupClaimTestDB so that handlers using getDBConn()/stateMachine work.
func setupCoverageQueue(t *testing.T) func() {
	t.Helper()
	dbPath := fmt.Sprintf("/tmp/test_coverage_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("setupCoverageQueue OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("setupCoverageQueue MigrateUp: %v", err)
	}

	oldDB := getDBConn()
	setDBConn(db)

	oldQueue := taskQueue
	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("setupCoverageQueue NewTaskQueueFromDB: %v", err)
	}
	taskQueue = q

	oldHub := hub
	hub = NewWebSocketHub()

	oldSM := stateMachine
	stateMachine = NewStateMachine(NewTaskStore(db), db)

	return func() {
		db.Close()
		os.Remove(dbPath)
		setDBConn(oldDB)
		taskQueue = oldQueue
		hub = oldHub
		stateMachine = oldSM
	}
}

// --- ClaimTask ---

func TestClaimTask_Success(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "claim-test-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	if err := q.ClaimTask(ctx, task.ID, "agent-1"); err != nil {
		t.Fatalf("ClaimTask: %v", err)
	}

	got, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.Status != TaskStatusAssigned {
		t.Errorf("status = %s, want %s", got.Status, TaskStatusAssigned)
	}
	if got.AssignedTo != "agent-1" {
		t.Errorf("assigned_to = %s, want agent-1", got.AssignedTo)
	}
}

func TestClaimTask_NotFound(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	err := q.ClaimTask(context.Background(), "no-such-task", "agent-1")
	if err == nil {
		t.Fatal("expected error for unknown task")
	}
}

func TestClaimTask_AlreadyAssigned(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "claim-test-002",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// First claim succeeds.
	if err := q.ClaimTask(ctx, task.ID, "agent-1"); err != nil {
		t.Fatalf("first ClaimTask: %v", err)
	}

	// Second claim by different agent must fail.
	if err := q.ClaimTask(ctx, task.ID, "agent-2"); err == nil {
		t.Fatal("expected error when claiming already-assigned task")
	}
}

func TestClaimTask_SameAgentIdempotent(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "claim-test-003",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := q.ClaimTask(ctx, task.ID, "agent-1"); err != nil {
		t.Fatalf("first ClaimTask: %v", err)
	}
	// Same agent re-claiming an already-assigned task should be rejected
	// (task is no longer queued/requested).
	if err := q.ClaimTask(ctx, task.ID, "agent-1"); err == nil {
		t.Fatal("expected error: task status is no longer queued")
	}
}

// --- UpdateTaskStatus ---

func TestUpdateTaskStatus_InProgress(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "uts-test-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	if err := sq.UpdateTaskStatus(task.ID, "in_progress", "agent-x"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	got, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.Status != TaskStatusExecuting {
		t.Errorf("status = %s, want %s", got.Status, TaskStatusExecuting)
	}
	if got.AssignedTo != "agent-x" {
		t.Errorf("assigned_to = %s, want agent-x", got.AssignedTo)
	}
}

func TestUpdateTaskStatus_Completed(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "uts-test-002",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	if err := sq.UpdateTaskStatus(task.ID, "completed", "agent-x"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	got, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.Status != TaskStatusCompleted {
		t.Errorf("status = %s, want %s", got.Status, TaskStatusCompleted)
	}
}

func TestUpdateTaskStatus_Failed(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "uts-test-003",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	if err := sq.UpdateTaskStatus(task.ID, "failed", "agent-x"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	got, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.Status != TaskStatusFailed {
		t.Errorf("status = %s, want %s", got.Status, TaskStatusFailed)
	}
}

func TestUpdateTaskStatus_CustomStatus(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "uts-test-004",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	// Arbitrary status string is stored as-is.
	if err := sq.UpdateTaskStatus(task.ID, "paused", "agent-x"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	got, err := q.GetTask(ctx, task.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.Status != TaskStatus("paused") {
		t.Errorf("status = %s, want paused", got.Status)
	}
}

// --- createTaskHandler ---

func TestCreateTaskHandler_ValidTask(t *testing.T) {
	defer setupCoverageQueue(t)()

	body := `{"domain":"test","project":"proj","type":"feature","title":"Hello task","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}
	var resp Task
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.ID == "" {
		t.Error("expected generated ID in response")
	}
	if resp.Domain != "test" {
		t.Errorf("domain = %s, want test", resp.Domain)
	}
	if resp.Title != "Hello task" {
		t.Errorf("title = %s, want 'Hello task'", resp.Title)
	}
}

func TestCreateTaskHandler_MissingDomain(t *testing.T) {
	defer setupCoverageQueue(t)()

	body := `{"project":"proj","type":"feature","title":"No domain","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rr.Code)
	}
}

func TestCreateTaskHandler_MissingTitle(t *testing.T) {
	defer setupCoverageQueue(t)()

	body := `{"domain":"test","project":"proj","type":"feature","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rr.Code)
	}
}

func TestCreateTaskHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}

func TestCreateTaskHandler_PriorityNormalization(t *testing.T) {
	defer setupCoverageQueue(t)()

	// priority=0 should default to 5
	body := `{"domain":"test","project":"proj","type":"feature","title":"Zero priority","priority":0}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}
	var resp Task
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Priority != 5 {
		t.Errorf("priority = %d, want 5 (default)", resp.Priority)
	}
}

func TestCreateTaskHandler_PriorityLegacyMapping(t *testing.T) {
	defer setupCoverageQueue(t)()

	// priority=50 (legacy 0-100 scale) should map to 5 (1-10 scale)
	body := `{"domain":"test","project":"proj","type":"feature","title":"Legacy priority","priority":50}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}
	var resp Task
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Priority < 1 || resp.Priority > 10 {
		t.Errorf("priority = %d, want 1-10 after legacy mapping", resp.Priority)
	}
}

func TestCreateTaskHandler_NegativePriority(t *testing.T) {
	defer setupCoverageQueue(t)()

	body := `{"domain":"test","project":"proj","type":"feature","title":"Bad priority","priority":-1}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rr.Code)
	}
}

// --- getTaskHandler ---

func TestGetTaskHandler_ExistingCoverage(t *testing.T) {
	defer setupCoverageQueue(t)()

	task := Task{
		ID:       "get-test-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Title:    "Existing Task",
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/get-test-001", nil)
	rr := httptest.NewRecorder()

	getTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}
	var resp Task
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.ID != "get-test-001" {
		t.Errorf("id = %s, want get-test-001", resp.ID)
	}
}

func TestGetTaskHandler_NotFoundCoverage(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/no-such-task", nil)
	rr := httptest.NewRecorder()

	getTaskHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rr.Code)
	}
}

// --- completeTaskHandler ---

func TestCompleteTaskHandler_SuccessCoverage(t *testing.T) {
	defer setupCoverageQueue(t)()

	task := Task{
		ID:       "comp-test-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Title:    "Task to complete",
		Priority: 5,
		Status:   TaskStatusExecuting,
		State:    StateRunning,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	body := `{"result":"done","status":"completed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/comp-test-001/complete", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	completeTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}
	
	got, _ := taskQueue.GetTask(context.Background(), "comp-test-001")
	if got.Status != TaskStatusCompleted {
		t.Errorf("status = %s, want completed", got.Status)
	}
}

func TestCompleteTaskHandler_NotFoundCoverage(t *testing.T) {
	defer setupCoverageQueue(t)()

	body := `{"result":"done","status":"completed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/not-found/complete", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	completeTaskHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rr.Code)
	}
}

// --- listTasksHandler ---

func TestListTasksHandler_AllCoverage(t *testing.T) {
	defer setupCoverageQueue(t)()

	for i := 1; i <= 3; i++ {
		task := Task{
			ID:      fmt.Sprintf("list-test-%d", i),
			Domain:  "test",
			Project: "proj",
			Title:   fmt.Sprintf("Task %d", i),
			Status:  TaskStatusQueued,
		}
		taskQueue.Enqueue(context.Background(), task)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	rr := httptest.NewRecorder()

	listTasksHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}
	var resp struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Count != 3 {
		t.Errorf("count = %d, want 3", resp.Count)
	}
}

func TestListTasksHandler_FilterStatusCoverage(t *testing.T) {
	defer setupCoverageQueue(t)()

	task1 := Task{ID: "t1", Status: TaskStatusQueued, Domain: "d", Project: "p"}
	task2 := Task{ID: "t2", Status: TaskStatusCompleted, Domain: "d", Project: "p"}
	taskQueue.Enqueue(context.Background(), task1)
	taskQueue.Enqueue(context.Background(), task2)

	// Filter for queued
	req := httptest.NewRequest(http.MethodGet, "/api/tasks?status=queued", nil)
	rr := httptest.NewRecorder()

	listTasksHandler(rr, req)

	var resp struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	json.NewDecoder(rr.Body).Decode(&resp)

	if resp.Count != 1 {
		t.Errorf("count = %d, want 1", resp.Count)
	}
	if resp.Tasks[0].ID != "t1" {
		t.Errorf("got id %s, want t1", resp.Tasks[0].ID)
	}
}

// --- patrolsHandler ---

func TestPatrolsHandler_EmptyDB(t *testing.T) {
	defer setupCoverageQueue(t)()

	// No patrol_executions rows; globalPatrolSystem is nil in test context.
	req := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	rr := httptest.NewRecorder()

	patrolsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}

	var resp struct {
		Patrols []map[string]interface{} `json:"patrols"`
		Count   int                      `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	if resp.Patrols == nil {
		t.Error("patrols key missing from response")
	}
	if resp.Count != len(resp.Patrols) {
		t.Errorf("count=%d does not match len(patrols)=%d", resp.Count, len(resp.Patrols))
	}
}

func TestPatrolsHandler_WithExecutionData(t *testing.T) {
	defer setupCoverageQueue(t)()

	db := getDBConn()
	// Insert a synthetic patrol execution record so the DB path is exercised.
	_, err := db.Exec(`
		INSERT INTO patrol_executions (id, patrol_id, started_at, completed_at, status)
		VALUES ('test-exec-1', 'health-check', datetime('now','-1 minute'), datetime('now'), 'completed')
	`)
	if err != nil {
		t.Fatalf("insert patrol_executions: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	rr := httptest.NewRecorder()

	patrolsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}

	var resp struct {
		Patrols []map[string]interface{} `json:"patrols"`
		Count   int                      `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Count == 0 {
		t.Fatal("expected at least one patrol in response")
	}

	// Find the patrol we inserted and verify required fields are present.
	var found bool
	for _, p := range resp.Patrols {
		if p["id"] == "health-check" {
			found = true
			if _, ok := p["name"]; !ok {
				t.Error("patrol missing 'name' field")
			}
			if _, ok := p["status"]; !ok {
				t.Error("patrol missing 'status' field")
			}
			if _, ok := p["error_count"]; !ok {
				t.Error("patrol missing 'error_count' field")
			}
		}
	}
	if !found {
		t.Error("expected 'health-check' patrol in response")
	}
}

func TestPatrolsHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/patrols", nil)
	rr := httptest.NewRecorder()

	patrolsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rr.Code)
	}
}
