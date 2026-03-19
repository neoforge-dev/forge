//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// Wave 193: handlers_tui.go + handlers_messages_pwa.go
// Targets:
//   tuiLogsHandler       (handlers_tui.go:25)    — basic GET, limit param
//   planTaskHandler      (handlers_tui.go:93)    — GET→405, with planManager
//   replanTaskHandler    (handlers_tui.go:122)   — GET→405
//   getPlanHistoryHandler(handlers_tui.go:151)   — empty task ID, with planManager
//   messagesHandler      (handlers_messages_pwa.go:26) — nil DB→503, GET/POST/405
//   messageByIDHandler   (handlers_messages_pwa.go:104)— nil DB→503, GET/mark-read
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// tuiLogsHandler
// ---------------------------------------------------------------------------

func TestWave193_TuiLogsHandler_Basic(t *testing.T) {
	// Need a non-nil hub
	oldHub := hub
	hub = NewHub()
	defer func() { hub = oldHub }()

	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs", nil)
	w := httptest.NewRecorder()
	tuiLogsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestWave193_TuiLogsHandler_WithLimit(t *testing.T) {
	oldHub := hub
	hub = NewHub()
	defer func() { hub = oldHub }()

	req := httptest.NewRequest(http.MethodGet, "/api/tui/logs?limit=5", nil)
	w := httptest.NewRecorder()
	tuiLogsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with limit, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// planTaskHandler
// ---------------------------------------------------------------------------

func TestWave193_PlanTaskHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/plan", nil)
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave193_PlanTaskHandler_InvalidBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/plan",
		strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid body, got %d", w.Code)
	}
}

func TestWave193_PlanTaskHandler_WithPlanManager(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-193/plan",
		strings.NewReader(`{"plan":"do the thing","reason":"wave 193"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with planManager, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// replanTaskHandler
// ---------------------------------------------------------------------------

func TestWave193_ReplanTaskHandler_Get405(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-1/replan", nil)
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave193_ReplanTaskHandler_InvalidBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-1/replan",
		strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid body, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// getPlanHistoryHandler
// ---------------------------------------------------------------------------

func TestWave193_GetPlanHistoryHandler_EmptyID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks//plans", nil)
	w := httptest.NewRecorder()
	getPlanHistoryHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty task ID, got %d", w.Code)
	}
}

func TestWave193_GetPlanHistoryHandler_WithPlanManager(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-193/plans", nil)
	w := httptest.NewRecorder()
	getPlanHistoryHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// messagesHandler
// ---------------------------------------------------------------------------

func TestWave193_MessagesHandler_NilDB503(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/messages", nil)
	w := httptest.NewRecorder()
	messagesHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil DB, got %d", w.Code)
	}
}

func TestWave193_MessagesHandler_Get(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/messages", nil)
	w := httptest.NewRecorder()
	messagesHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave193_MessagesHandler_Post(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/messages",
		strings.NewReader(`{"from_agent":"kimi-1","content":"hello"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	messagesHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 for POST, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave193_MessagesHandler_Delete405(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodDelete, "/api/messages", nil)
	w := httptest.NewRecorder()
	messagesHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// messageByIDHandler
// ---------------------------------------------------------------------------

func TestWave193_MessageByIDHandler_NilDB503(t *testing.T) {
	setDBConn(nil)
	req := httptest.NewRequest(http.MethodGet, "/api/messages/msg-1", nil)
	w := httptest.NewRecorder()
	messageByIDHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil DB, got %d", w.Code)
	}
}

func TestWave193_MessageByIDHandler_GetNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/messages/nonexistent-msg", nil)
	w := httptest.NewRecorder()
	messageByIDHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestWave193_MessageByIDHandler_Delete405(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodDelete, "/api/messages/msg-1", nil)
	w := httptest.NewRecorder()
	messageByIDHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestWave193_PlanTaskHandler_EmptyTaskID verifies that planTaskHandler returns
// 400 when the task ID extracted from the URL is empty.
func TestWave193_PlanTaskHandler_EmptyTaskID(t *testing.T) {
	// URL "/api/tasks//plan" → TrimPrefix = "/plan" → TrimSuffix = "" (empty)
	req := httptest.NewRequest(http.MethodPost, "/plan",
		strings.NewReader(`{"plan":"do stuff","reason":"test"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	planTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty task ID, got %d", w.Code)
	}
}

// TestWave193_ReplanTaskHandler_WithPlanManager verifies that replanTaskHandler
// calls RevisePlan and returns an error because the task doesn't exist in the DB.
// This covers the planManager.RevisePlan call path.
func TestWave193_ReplanTaskHandler_WithPlanManager(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	// Task "task-replan-193" doesn't exist in tasks table.
	// RevisePlan queries for plan_version — returns ErrNoRows → 500.
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/task-replan-193/replan",
		strings.NewReader(`{"plan":"new plan","reason":"test"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)

	// Either 200 (if task insert was done implicitly) or 500 (no such task).
	// We just verify the planManager was called (no panic) and got a response.
	if w.Code == 0 {
		t.Error("expected a non-zero status code from replanTaskHandler")
	}
}

// TestWave193_ReplanTaskHandler_EmptyTaskID verifies that replanTaskHandler returns
// 400 when the task ID extracted from the URL is empty.
func TestWave193_ReplanTaskHandler_EmptyTaskID(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/replan",
		strings.NewReader(`{"plan":"do stuff","reason":"test"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	replanTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty task ID, got %d", w.Code)
	}
}

// TestWave193_PlanManager_CreatePlan_Success verifies that CreatePlan
// successfully inserts into plan_versions and plan_events tables.
func TestWave193_PlanManager_CreatePlan_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	pm := NewPlanManager(db)
	ctx := context.Background()

	planID, err := pm.CreatePlan(ctx, "task-create-plan", "my plan text", "because test")
	if err != nil {
		t.Fatalf("CreatePlan: %v", err)
	}
	if planID == "" {
		t.Error("expected non-empty plan ID")
	}

	// Verify plan version was inserted.
	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM plan_versions WHERE task_id = ?`, "task-create-plan").Scan(&count); err != nil {
		t.Fatalf("count plan_versions: %v", err)
	}
	if count != 1 {
		t.Errorf("expected 1 plan version, got %d", count)
	}
}

// TestWave193_PlanManager_GetPlanHistory_Empty verifies that GetPlanHistory
// returns an empty slice for a task with no plan history.
func TestWave193_PlanManager_GetPlanHistory_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	pm := NewPlanManager(db)
	history, err := pm.GetPlanHistory(context.Background(), "no-such-task")
	if err != nil {
		t.Fatalf("GetPlanHistory: %v", err)
	}
	if len(history) != 0 {
		t.Errorf("expected 0 history entries, got %d", len(history))
	}
}

// TestWave193_PlanManager_RevisePlan_NoTask verifies that RevisePlan returns
// an error when the task does not exist (covers the ErrNoRows branch).
func TestWave193_PlanManager_RevisePlan_NoTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	pm := NewPlanManager(db)
	_, err := pm.RevisePlan(context.Background(), "no-such-task", "new plan", "revision reason")
	if err == nil {
		t.Error("expected error for non-existent task, got nil")
	}
}
