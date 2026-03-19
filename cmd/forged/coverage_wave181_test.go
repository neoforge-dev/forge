//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ============================================================
// coverage_wave181_test.go — handleTaskLogs + claimTaskHandler error paths
// Target: handleTaskLogs (main.go:332), claimTaskHandler (handlers_task.go:474)
// ============================================================

// TestWave181_HandleTaskLogs_MissingID tests missing id query param.
func TestWave181_HandleTaskLogs_MissingID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/task-logs", nil)
	w := httptest.NewRecorder()

	handleTaskLogs(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "task id required") {
		t.Errorf("expected 'task id required' error, got %s", w.Body.String())
	}
}

// TestWave181_HandleTaskLogs_UnknownTask tests handling of unknown task.
// Note: getTaskEventsHandler returns 200 with empty events for unknown tasks,
// so we just verify the handler doesn't crash.
func TestWave181_HandleTaskLogs_UnknownTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/task-logs?id=nonexistent-task-xyz", nil)
	w := httptest.NewRecorder()

	handleTaskLogs(w, req)

	// getTaskEventsHandler returns 200 with empty events for unknown tasks
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (empty events), got %d", w.Code)
	}
}

// TestWave181_HandleTaskLogs_HappyPath tests successful task logs retrieval.
func TestWave181_HandleTaskLogs_HappyPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Setup task queue and create a task
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	task := Task{
		ID:        "task-181-logs",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "test task for logs",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/task-logs?id=task-181-logs", nil)
	w := httptest.NewRecorder()

	handleTaskLogs(w, req)

	// Should succeed (200) - no events but valid task
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave181_ClaimTaskHandler_MethodNotAllowed tests GET rejection.
// TestWave181_ClaimTaskHandler_EmptyTaskID tests empty task ID in path.
func TestWave181_ClaimTaskHandler_EmptyTaskID(t *testing.T) {
	body := strings.NewReader(`{"agent_id":"test-agent"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//claim", body)
	req.URL.Path = "/api/tasks//claim"
	w := httptest.NewRecorder()

	claimTaskHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestWave181_ClaimTaskHandler_InvalidJSON tests malformed request body.
func TestWave181_ClaimTaskHandler_InvalidJSON(t *testing.T) {
	body := strings.NewReader(`{invalid json}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-181-claim/claim", body)
	req.URL.Path = "/api/tasks/task-181-claim/claim"
	w := httptest.NewRecorder()

	claimTaskHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestWave181_ClaimTaskHandler_EmptyAgentID tests missing agent_id in body.
func TestWave181_ClaimTaskHandler_EmptyAgentID(t *testing.T) {
	body := strings.NewReader(`{"agent_id":""}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-181-claim/claim", body)
	req.URL.Path = "/api/tasks/task-181-claim/claim"
	w := httptest.NewRecorder()

	claimTaskHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestWave181_ClaimTaskHandler_TaskNotFound tests claiming nonexistent task.
func TestWave181_ClaimTaskHandler_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Setup task queue
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	body := strings.NewReader(`{"agent_id":"test-agent"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-task-xyz/claim", body)
	req.URL.Path = "/api/tasks/nonexistent-task-xyz/claim"
	w := httptest.NewRecorder()

	claimTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave181_ClaimTaskHandler_Success tests successful task claim.
func TestWave181_ClaimTaskHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Setup task queue
	origQueue := taskQueue
	var err error
	taskQueue, err = NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	defer func() { taskQueue = origQueue }()

	// Create a task
	task := Task{
		ID:        "task-181-claim",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "test task to claim",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	body := strings.NewReader(`{"agent_id":"agent-181"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-181-claim/claim", body)
	req.URL.Path = "/api/tasks/task-181-claim/claim"
	w := httptest.NewRecorder()

	claimTaskHandler(w, req)

	if w.Code != http.StatusCreated {
		t.Errorf("expected 201, got %d: %s", w.Code, w.Body.String())
	}
}
