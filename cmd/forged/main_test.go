//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestMain_TimeoutHandler covers the withTimeout wrapper (line 740-742 in main.go).
func TestMain_TimeoutHandler(t *testing.T) {
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	handler := withTimeout(h, 5*time.Second)

	r := httptest.NewRequest(http.MethodGet, "/", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 from withTimeout handler, got %d", w.Code)
	}
}

// TestHandleTaskLogs_MissingID_Main covers the empty taskID → 400 branch (main_test.go version).
func TestHandleTaskLogs_MissingID_Main(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleTaskLogs_ErrorFromQueue covers the rr.status >= 400 branch —
// closes the DB so getTaskEventsHandler returns 500, which handleTaskLogs forwards.
func TestHandleTaskLogs_ErrorFromQueue(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Close the test DB so GetTaskEvents returns an error.
	getDBConn().Close()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs?id=any-task-id", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	if w.Code < 400 {
		t.Errorf("expected 4xx/5xx response when DB is closed, got %d", w.Code)
	}
}

// TestHandleTaskLogs_Success covers the normal success path with valid task ID.
func TestHandleTaskLogs_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs?id=nonexistent-task", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	// Non-existent task returns 200 with empty events array (not an error).
	if w.Code >= 500 {
		t.Errorf("unexpected server error for nonexistent task: %d %s", w.Code, w.Body.String())
	}
}

// TestHandleQueueDepth_WithQueue_Main covers the normal path when taskQueue is set (main_test.go version).
func TestHandleQueueDepth_WithQueue_Main(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for queue depth, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleQueueDepth_WithFormat covers the format query param code path.
func TestHandleQueueDepth_WithFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for queue depth with format, got %d", w.Code)
	}
}

// TestHandleAgentList_OK covers the handleAgentList success path.
func TestHandleAgentList_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for agent list, got %d: %s", w.Code, w.Body.String())
	}
}

// TestHandleAgentStatus_MissingID_Main covers the id == "" → 400 branch (main_test.go version).
func TestHandleAgentStatus_MissingID_Main(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents/status", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent id, got %d", w.Code)
	}
}

// TestHandleAgentStatus_NotFound_Main covers the getAgentHealth error → 404 branch (main_test.go version).
func TestHandleAgentStatus_NotFound_Main(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents/status?id=nonexistent-agent", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for nonexistent agent, got %d", w.Code)
	}
}
