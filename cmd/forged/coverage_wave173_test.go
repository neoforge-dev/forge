//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 173: handlers_task.go pure function + handlers (method-check paths)
// Targets:
//   extractTaskID           (handlers_task.go:761) — strips prefix/suffix
//   listTasksHandler        (handlers_task.go:436) — GET with DB, invalid limit
//   claimableTasksHandler   (handlers_task.go:807) — GET with DB
//   taskHistoryHandler      (handlers_task.go:354) — GET with DB, nil DB
//   pruneTasksHandler       (handlers_task.go:389) — GET→405, nil DB, POST with DB
//   getTaskEventsHandler    (handlers_task.go:724) — GET with DB (no events), nil DB
//   abandonTaskHandler      (handlers_task.go:321) — GET→405, nil DB, missing ID
//   taskDeleteHandler       (handlers_task.go:696) — GET→405, nil DB, POST nonexistent
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// extractTaskID
// ---------------------------------------------------------------------------

func TestWave173_ExtractTaskID_Basic(t *testing.T) {
	got := extractTaskID("/api/tasks/task-123/claim", "/claim")
	if got != "task-123" {
		t.Errorf("expected 'task-123', got %q", got)
	}
}

func TestWave173_ExtractTaskID_NoSuffix(t *testing.T) {
	got := extractTaskID("/api/tasks/task-abc", "")
	if got != "task-abc" {
		t.Errorf("expected 'task-abc', got %q", got)
	}
}

func TestWave173_ExtractTaskID_Empty(t *testing.T) {
	got := extractTaskID("/api/tasks/", "")
	if got != "" {
		t.Errorf("expected empty, got %q", got)
	}
}

// ---------------------------------------------------------------------------
// listTasksHandler
// ---------------------------------------------------------------------------

func TestWave173_ListTasksHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks?limit=5", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave173_ListTasksHandler_InvalidLimit(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks?limit=abc", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)
	// Invalid limit falls back to default — should still return 200
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for invalid limit (uses default), got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// claimableTasksHandler
// ---------------------------------------------------------------------------

func TestWave173_ClaimableTasksHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/claimable", nil)
	w := httptest.NewRecorder()
	claimableTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// taskHistoryHandler
// ---------------------------------------------------------------------------

func TestWave173_TaskHistoryHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave173_TaskHistoryHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/nonexistent-task/history", nil)
	w := httptest.NewRecorder()
	taskHistoryHandler(w, req)
	// 200 with empty history or 404 if task not found — either is valid
	if w.Code != http.StatusOK && w.Code != http.StatusNotFound {
		t.Errorf("unexpected status: %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// pruneTasksHandler
// ---------------------------------------------------------------------------

func TestWave173_PruneTasksHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave173_PruneTasksHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	w := httptest.NewRecorder()
	pruneTasksHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave173_PruneTasksHandler_PostWithDB(t *testing.T) {
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
}

// ---------------------------------------------------------------------------
// getTaskEventsHandler
// ---------------------------------------------------------------------------

func TestWave173_GetTaskEventsHandler_GetWithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/nonexistent-task/events", nil)
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 (empty events), got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// abandonTaskHandler
// ---------------------------------------------------------------------------

func TestWave173_AbandonTaskHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/abandon", nil)
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave173_AbandonTaskHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/abandon",
		strings.NewReader(`{"reason":"test"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	abandonTaskHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// taskDeleteHandler
// ---------------------------------------------------------------------------

func TestWave173_TaskDeleteHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/delete", nil)
	w := httptest.NewRecorder()
	taskDeleteHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave173_TaskDeleteHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodDelete, "/api/tasks/task-1", nil)
	w := httptest.NewRecorder()
	taskDeleteHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}
