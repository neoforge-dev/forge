//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// Wave 47: gitguardHandler paths, listTasksHandler paths,
//          uiFleetHandler, planTaskHandler, completeTaskHandler,
//          PWA bridge handleSummary/handleAgents nil DB

// ─── gitguardHandler ──────────────────────────────────────────────────────

func setupGitGuard(t *testing.T) (*GitGuard, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	return NewGitGuard(db, t.TempDir()), cleanup
}

func TestGitguardHandler_GET_NoFilter_W47(t *testing.T) {
	gg, cleanup := setupGitGuard(t)
	defer cleanup()

	handler := gitguardHandler(gg)
	req := httptest.NewRequest(http.MethodGet, "/api/gitguard", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGitguardHandler_GET_WithTaskID_W47(t *testing.T) {
	gg, cleanup := setupGitGuard(t)
	defer cleanup()

	handler := gitguardHandler(gg)
	req := httptest.NewRequest(http.MethodGet, "/api/gitguard?task_id=TASK-W47", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGitguardHandler_POST_InvalidJSON_W47(t *testing.T) {
	gg, cleanup := setupGitGuard(t)
	defer cleanup()

	handler := gitguardHandler(gg)
	req := httptest.NewRequest(http.MethodPost, "/api/gitguard", bytes.NewBufferString("not-json"))
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestGitguardHandler_POST_MissingTaskID_W47(t *testing.T) {
	gg, cleanup := setupGitGuard(t)
	defer cleanup()

	handler := gitguardHandler(gg)
	body, _ := json.Marshal(map[string]string{"action": "commit"})
	req := httptest.NewRequest(http.MethodPost, "/api/gitguard", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing task_id, got %d", w.Code)
	}
}

func TestGitguardHandler_POST_MissingAction_W47(t *testing.T) {
	gg, cleanup := setupGitGuard(t)
	defer cleanup()

	handler := gitguardHandler(gg)
	body, _ := json.Marshal(map[string]string{"task_id": "TASK-W47"})
	req := httptest.NewRequest(http.MethodPost, "/api/gitguard", bytes.NewReader(body))
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing action, got %d", w.Code)
	}
}

func TestGitguardHandler_MethodDelete_W47(t *testing.T) {
	gg, cleanup := setupGitGuard(t)
	defer cleanup()

	handler := gitguardHandler(gg)
	req := httptest.NewRequest(http.MethodDelete, "/api/gitguard", nil)
	w := httptest.NewRecorder()
	handler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ─── listTasksHandler ─────────────────────────────────────────────────────

func TestListTasksHandler_ByStatus_W47(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks?status=queued", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestListTasksHandler_LimitClamped_W47(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks?limit=999", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestListTasksHandler_WithTasks_W47(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	for i, status := range []string{"queued", "assigned", "completed"} {
		_, _ = db.ExecContext(ctx, `
			INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
			VALUES (?, 'test', 'proj', 'feature', ?, ?, 'QUEUED', 50, datetime('now'), datetime('now'))
		`, taskIDf("TASK-W47-LIST-%d", i), "Task "+status, status)
	}

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
	w := httptest.NewRecorder()
	listTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// helper to format task IDs without import fmt collision
func taskIDf(format string, i int) string {
	return format[:len(format)-2] + string(rune('0'+i))
}

// ─── uiFleetHandler ───────────────────────────────────────────────────────

func TestUIFleetHandler_WithDB_W47(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	// Returns HTML — just verify it renders without crash
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestUIFleetHandler_NilDB_W47(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	req := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	// Nil DB — still renders template with empty data
	_ = w.Code
}

// ─── planTaskHandler ──────────────────────────────────────────────────────

func TestPlanTaskHandler_InvalidJSON_W47(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-X/plan", bytes.NewBufferString("not-json"))
	req.URL.Path = "/api/tasks/TASK-X/plan"
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestPlanTaskHandler_MissingTaskID_W47(t *testing.T) {
	body, _ := json.Marshal(map[string]string{"plan": "do the thing"})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//plan", bytes.NewReader(body))
	req.URL.Path = "/api/tasks//plan"
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty task ID, got %d", w.Code)
	}
}

// ─── completeTaskHandler ──────────────────────────────────────────────────

func TestCompleteTaskHandler_InvalidJSON_W47(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-X/complete", bytes.NewBufferString("bad"))
	req.URL.Path = "/api/tasks/TASK-X/complete"
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestCompleteTaskHandler_TaskNotFound_W47(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body, _ := json.Marshal(map[string]string{"result": "done"})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NO-SUCH-TASK-W47/complete", bytes.NewReader(body))
	req.URL.Path = "/api/tasks/NO-SUCH-TASK-W47/complete"
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

func TestCompleteTaskHandler_NilQueue_NilDB_W47(t *testing.T) {
	orig := taskQueue
	taskQueue = nil
	defer func() { taskQueue = orig }()

	origDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(origDB)

	body, _ := json.Marshal(map[string]string{"result": "done"})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-X/complete", bytes.NewReader(body))
	req.URL.Path = "/api/tasks/TASK-X/complete"
	w := httptest.NewRecorder()
	completeTaskHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// ─── PWA handleSummary / handleAgents nil dashboard ───────────────────────

func TestPWAHandleSummary_NilDashboard_W47(t *testing.T) {
	h := NewPWADashboardHandler(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/pwa/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestPWAHandleAgents_NilDashboard_W47(t *testing.T) {
	h := NewPWADashboardHandler(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/pwa/agents", nil)
	w := httptest.NewRecorder()
	h.handleAgents(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestPWAHandleSummary_WithDB_W47(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := &Dashboard{db: db}
	h := NewPWADashboardHandler(d)
	req := httptest.NewRequest(http.MethodGet, "/api/pwa/summary", nil)
	w := httptest.NewRecorder()
	h.handleSummary(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}
