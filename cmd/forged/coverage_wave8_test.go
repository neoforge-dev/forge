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

func TestPlanTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/plan", nil)
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

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

func TestReplanTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/replan", nil)
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

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

func TestQueueTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

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

func TestPauseTaskHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-001/pause", nil)
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- handlers_dispatch.go: configHandler ---

func TestConfigHandler_Delete_MethodNotAllowed(t *testing.T) {
	cleanup := setupWave5(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodDelete, "/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("configHandler DELETE: expected 405, got %d", w.Code)
	}
}
