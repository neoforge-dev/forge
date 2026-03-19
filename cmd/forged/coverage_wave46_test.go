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

// Wave 46: contextsHandler filters, handleQueueStatus paths,
//          getTaskEventsHandler, releaseTaskHandler, calculateSummary

// ─── contextsHandler ──────────────────────────────────────────────────────

func TestContextsHandler_Empty_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestContextsHandler_FilterByAgentID_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()
	_, _ = db.ExecContext(ctx, `
		INSERT INTO context_envelopes (id, agent_id, domain, project, task_id, summary, content, created_at)
		VALUES ('ctx-w46-1', 'agent-w46', 'test', 'proj', 'TASK-W46', 'summary text', 'content text', datetime('now'))
	`)

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?agent_id=agent-w46", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestContextsHandler_FilterByDomain_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?domain=test-domain-w46", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestContextsHandler_FilterByProject_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?project=my-project-w46", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestContextsHandler_FilterByTaskID_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?task_id=TASK-W46-CTX", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// ─── handleQueueStatus ────────────────────────────────────────────────────

func TestHandleQueueStatus_NilQueue_W46(t *testing.T) {
	orig := taskQueue
	taskQueue = nil
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestHandleQueueStatus_WithQueue_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleQueueStatus_FormatText_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status?format=text", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestHandleQueueStatus_WithTasks_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	for _, row := range []struct{ id, status string }{
		{"TASK-W46-Q1", "queued"},
		{"TASK-W46-Q2", "assigned"},
		{"TASK-W46-Q3", "completed"},
		{"TASK-W46-Q4", "failed"},
	} {
		_, _ = db.ExecContext(ctx, `
			INSERT INTO tasks (id, domain, project, type, title, status, state, priority, created_at, updated_at)
			VALUES (?, 'test', 'proj', 'feature', ?, ?, 'QUEUED', 50, datetime('now'), datetime('now'))
		`, row.id, "Task "+row.id, row.status)
	}

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── getTaskEventsHandler ─────────────────────────────────────────────────

func TestGetTaskEventsHandler_MissingID_W46(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks//events", nil)
	req.URL.Path = "/api/tasks//events"
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing task ID, got %d", w.Code)
	}
}

func TestGetTaskEventsHandler_WithQueue_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/NO-SUCH-TASK-W46/events", nil)
	req.URL.Path = "/api/tasks/NO-SUCH-TASK-W46/events"
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	// Returns empty events or 500 — just no crash.
	_ = w.Code
}

func TestGetTaskEventsHandler_LimitClamped_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/TASK-W46-EVT/events?limit=999", nil)
	req.URL.Path = "/api/tasks/TASK-W46-EVT/events"
	w := httptest.NewRecorder()
	getTaskEventsHandler(w, req)
	// Clamped to 100 — just verify no crash.
	_ = w.Code
}

// ─── releaseTaskHandler ───────────────────────────────────────────────────

func TestReleaseTaskHandler_InvalidJSON_W46(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-X/release", bytes.NewBufferString("not-json"))
	req.URL.Path = "/api/tasks/TASK-X/release"
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestReleaseTaskHandler_TaskNotFound_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body, _ := json.Marshal(map[string]string{"agent_id": "agent-w46", "reason": "testing"})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NO-SUCH-TASK-W46/release", bytes.NewReader(body))
	req.URL.Path = "/api/tasks/NO-SUCH-TASK-W46/release"
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestReleaseTaskHandler_WrongAgent_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	_, _ = db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, assigned_to, created_at, updated_at)
		VALUES ('TASK-W46-REL', 'test', 'proj', 'feature', 'Release Task', 'assigned', 'RUNNING', 50, 'correct-agent', datetime('now'), datetime('now'))
	`)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body, _ := json.Marshal(map[string]string{"agent_id": "wrong-agent", "reason": "testing"})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-W46-REL/release", bytes.NewReader(body))
	req.URL.Path = "/api/tasks/TASK-W46-REL/release"
	w := httptest.NewRecorder()
	releaseTaskHandler(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("expected 403, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── calculateSummary ─────────────────────────────────────────────────────

func TestCalculateSummary_AllStatuses_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := &Dashboard{db: db}

	taskStr := "TASK-W46-SUMMARY"
	agents := []AgentHealth{
		{AgentID: "a1", Status: AgentStatusIdle, ContextPercent: 30.0},
		{AgentID: "a2", Status: AgentStatusWorking, ContextPercent: 60.0, CurrentTask: &taskStr},
		{AgentID: "a3", Status: AgentStatusOffline, ContextPercent: 0.0},
		{AgentID: "a4", Status: AgentStatusError, ContextPercent: 80.0},
		{AgentID: "a5", Status: AgentStatusBlocked, ContextPercent: 50.0},
	}

	summary := d.calculateSummary(agents)

	if summary.TotalAgents != 5 {
		t.Errorf("expected 5 total agents, got %d", summary.TotalAgents)
	}
	if summary.IdleAgents != 1 {
		t.Errorf("expected 1 idle agent, got %d", summary.IdleAgents)
	}
	if summary.OfflineAgents != 1 {
		t.Errorf("expected 1 offline agent, got %d", summary.OfflineAgents)
	}
	if summary.ErrorAgents != 1 {
		t.Errorf("expected 1 error agent, got %d", summary.ErrorAgents)
	}
	if summary.ActiveTasks != 1 {
		t.Errorf("expected 1 active task, got %d", summary.ActiveTasks)
	}
	if summary.AvgContextUsage == 0 {
		t.Error("expected non-zero avg context usage")
	}
	// With 1 error agent → warning or critical
	if summary.SystemHealth == "healthy" {
		t.Error("expected warning/critical system health, got healthy")
	}
}

func TestCalculateSummary_Empty_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := &Dashboard{db: db}
	summary := d.calculateSummary([]AgentHealth{})

	if summary.TotalAgents != 0 {
		t.Errorf("expected 0 total agents, got %d", summary.TotalAgents)
	}
	if summary.SystemHealth != "healthy" {
		t.Errorf("expected healthy for empty agents, got %s", summary.SystemHealth)
	}
	if summary.AvgContextUsage != 0 {
		t.Errorf("expected 0 avg context for empty agents, got %f", summary.AvgContextUsage)
	}
}

func TestCalculateSummary_MoreOfflineThanOnline_W46(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := &Dashboard{db: db}
	agents := []AgentHealth{
		{AgentID: "a1", Status: AgentStatusOffline},
		{AgentID: "a2", Status: AgentStatusOffline},
		{AgentID: "a3", Status: AgentStatusOffline},
		{AgentID: "a4", Status: AgentStatusIdle},
	}

	summary := d.calculateSummary(agents)
	if summary.SystemHealth != "critical" {
		t.Errorf("expected critical when more offline than online, got %s", summary.SystemHealth)
	}
}
