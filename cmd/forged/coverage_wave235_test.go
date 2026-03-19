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
// coverage_wave235_test.go — handlers_task.go remaining paths
// Target: extractTaskID, taskHistoryHandler, pruneTasksHandler,
//         getTaskHandler, listTasksHandler, approveTaskHandler,
//         claimableTasksHandler, abandonTaskHandler
// ============================================================

// --- extractTaskID ---

func TestWave235_ExtractTaskID_Basic(t *testing.T) {
	got := extractTaskID("/api/tasks/task-123/approve", "/approve")
	if got != "task-123" {
		t.Errorf("expected 'task-123', got %s", got)
	}
}

func TestWave235_ExtractTaskID_History(t *testing.T) {
	got := extractTaskID("/api/tasks/task-abc/history", "/history")
	if got != "task-abc" {
		t.Errorf("expected 'task-abc', got %s", got)
	}
}

func TestWave235_ExtractTaskID_Empty(t *testing.T) {
	got := extractTaskID("/api/tasks/", "")
	if got != "" {
		t.Errorf("expected empty, got %s", got)
	}
}

// --- taskHistoryHandler ---

func TestWave235_TaskHistory_EmptyID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks//history", nil)
	req.URL.Path = "/api/tasks//history"
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestWave235_TaskHistory_NoDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-xyz/history", nil)
	req.URL.Path = "/api/tasks/task-xyz/history"
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave235_TaskHistory_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-235-hist/history", nil)
	req.URL.Path = "/api/tasks/task-235-hist/history"
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if !strings.Contains(body, "transitions") {
		t.Errorf("expected 'transitions' in response: %s", body)
	}
}

// --- pruneTasksHandler ---

func TestWave235_PruneTasks_NoDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave235_PruneTasks_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "pruned") {
		t.Errorf("expected 'pruned' in response: %s", w.Body.String())
	}
}

// --- getTaskHandler ---

func TestWave235_GetTask_NotFound(t *testing.T) {
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

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/nonexistent-235", nil)
	req.URL.Path = "/api/tasks/nonexistent-235"
	w := httptest.NewRecorder()
	getTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave235_GetTask_Found(t *testing.T) {
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

	task := Task{
		ID:        "task-235-found",
		Domain:    "forge",
		Project:   "test",
		Type:      "feature",
		Priority:  5,
		Status:    TaskStatusQueued,
		State:     StateQueued,
		Title:     "test task",
		CreatedAt: time.Now(),
		UpdatedAt: time.Now(),
	}
	taskQueue.Enqueue(context.Background(), task)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-235-found", nil)
	req.URL.Path = "/api/tasks/task-235-found"
	w := httptest.NewRecorder()
	getTaskHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- listTasksHandler ---

func TestWave235_ListTasks_WithDB(t *testing.T) {
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

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave235_ListTasks_WithStatus(t *testing.T) {
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

	req := httptest.NewRequest(http.MethodGet, "/api/tasks?status=queued&limit=5", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// --- approveTaskHandler ---

func TestWave235_ApproveTask_NoStateMachine(t *testing.T) {
	origSM := stateMachine
	stateMachine = nil
	defer func() { stateMachine = origSM }()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-235/approve", strings.NewReader(`{"approved_by":"test"}`))
	req.URL.Path = "/api/tasks/task-235/approve"
	w := httptest.NewRecorder()
	approveTaskHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// --- claimableTasksHandler ---

func TestWave235_ClaimableTasks_WithDB(t *testing.T) {
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

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/claimable?limit=5", nil)
	w := httptest.NewRecorder()
	claimableTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// --- abandonTaskHandler ---
