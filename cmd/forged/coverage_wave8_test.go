//go:build !tmux_bridge
// +build !tmux_bridge

// coverage_wave8_test.go — Coverage wave 8: tui handlers, plan handlers, dispatch, populate_tasks
// Target: +2pp from ~57% to ~59%
package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// --- handlers_tui.go: planTaskHandler, replanTaskHandler ---
// Note: TestPlanTaskHandler_MethodNotAllowed moved to handler_method_validation_test.go

func TestPlanTaskHandler_BadJSON(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-001/plan", bytes.NewReader([]byte("{bad")))
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

func TestPlanTaskHandler_EmptyTaskID(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	body, _ := json.Marshal(map[string]string{"plan": "do stuff", "reason": "test"})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//plan", bytes.NewReader(body))
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	// Empty task ID should return 400
	if w.Code == http.StatusOK {
		t.Error("expected non-200 for empty task ID")
	}
}

// Note: TestReplanTaskHandler_MethodNotAllowed moved to handler_method_validation_test.go

func TestReplanTaskHandler_BadJSON(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-001/replan", bytes.NewReader([]byte("{bad")))
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

// --- handlers_misc.go: extendLeaseHandler ---

func TestExtendLeaseHandler_BadJSON(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-001/extend-lease", bytes.NewReader([]byte("{bad")))
	w := httptest.NewRecorder()
	extendLeaseHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

// --- handlers_plan.go: queueTaskHandler, pauseTaskHandler ---
// Note: TestQueueTaskHandler_MethodNotAllowed moved to handler_method_validation_test.go

func TestQueueTaskHandler_TaskNotFound(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NONEXISTENT/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for missing task, got %d", w.Code)
	}
}

// Note: TestPauseTaskHandler_MethodNotAllowed moved to handler_method_validation_test.go

// --- handlers_dispatch.go: configHandler ---
// Note: TestConfigHandler_Delete_MethodNotAllowed moved to handler_method_validation_test.go
