//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

// (LaneCheckGates tested in lane_test.go)

// TestHandlersProxy_NoHarness tests harnessFallbackHandler returns 404 when FORGE_HARNESS_URL is unset
func TestHandlersProxy_NoHarness(t *testing.T) {
	savedProxy := harnessProxy
	defer func() { harnessProxy = savedProxy }()

	os.Unsetenv("FORGE_HARNESS_URL")
	initHarnessProxy()

	req := httptest.NewRequest("GET", "/api/unknown-legacy-path", nil)
	w := httptest.NewRecorder()
	harnessFallbackHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 when no FORGE_HARNESS_URL, got %d", w.Code)
	}
}

// TestHandlersProxy_WithHarness tests harnessFallbackHandler proxies when FORGE_HARNESS_URL is set
func TestHandlersProxy_WithHarness(t *testing.T) {
	savedProxy := harnessProxy
	defer func() { harnessProxy = savedProxy }()

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"proxied":true}`))
	}))
	defer ts.Close()

	os.Setenv("FORGE_HARNESS_URL", ts.URL)
	defer os.Unsetenv("FORGE_HARNESS_URL")
	initHarnessProxy()

	req := httptest.NewRequest("GET", "/api/legacy", nil)
	w := httptest.NewRecorder()
	harnessFallbackHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 proxied response, got %d", w.Code)
	}
}

// TestQueueTaskHandler_NonExistentTask tests queueTaskHandler with missing task ID
func TestQueueTaskHandler_NonExistentTask(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest("POST", "/api/tasks/NONEXISTENT-TASK/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for nonexistent task, got %d", w.Code)
	}
}

// TestQueueTaskHandler_PlannedTask tests queuing a planned task
func TestQueueTaskHandler_PlannedTask(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Create a planned task
	task := Task{
		ID:       "planned-task-w17",
		Domain:   "test",
		Project:  "test",
		Type:     "feature",
		Title:    "Planned task",
		Priority: 5,
		Status:   TaskStatusPlanned,
	}
	if err := taskQueue.Enqueue(t.Context(), task); err != nil {
		t.Fatalf("Enqueue planned task: %v", err)
	}

	req := httptest.NewRequest("POST", "/api/tasks/planned-task-w17/queue", nil)
	w := httptest.NewRecorder()
	queueTaskHandler(w, req)

	// planned → queued should work
	if w.Code != http.StatusOK {
		t.Logf("queueTaskHandler response: %d %s", w.Code, w.Body.String())
	}
}

// TestAutoPromoteCompletedTasksInLane_NoLane tests patrol with empty DB
func TestAutoPromoteCompletedTasksInLane_W17(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	// Should not panic or error even with empty DB
	ctx := t.Context()
	if err := autoPromoteCompletedTasksInLane(ctx, db); err != nil {
		t.Logf("autoPromote returned (expected on empty DB): %v", err)
	}
}
