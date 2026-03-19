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
	"time"
)

// Wave 48: dispatchHandler, agentTelemetrySummaryHandler, claimTaskHandler deeper paths,
//          fleetAutoDeflatePatrol, lane ProgressTask/CheckGates

// ─── dispatchHandler ──────────────────────────────────────────────────────

func TestDispatchHandler_GET_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestDispatchHandler_POST_InvalidJSON_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewBufferString("not-json"))
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestDispatchHandler_POST_Valid_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	body, _ := json.Marshal(map[string]string{
		"task_id":  "TASK-W48-DISPATCH",
		"agent_id": "agent-w48",
		"message":  "do the thing",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewReader(body))
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestDispatchHandler_DELETE_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodDelete, "/api/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// ─── agentTelemetrySummaryHandler ────────────────────────────────────────

func TestAgentTelemetrySummaryHandler_NilDB_W48(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestAgentTelemetrySummaryHandler_WithData_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.ExecContext(ctx, `
		INSERT INTO agent_telemetry (agent_id, node, context_pct, task_id, status, tool, timestamp)
		VALUES ('agent-w48-tel', 'prya', 45.0, 'TASK-W48', 'working', 'Read', ?)
	`, now)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ─── claimTaskHandler deeper paths ───────────────────────────────────────

func TestClaimTaskHandler_InvalidJSON_W48(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-X/claim", bytes.NewBufferString("bad"))
	req.URL.Path = "/api/tasks/TASK-X/claim"
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestClaimTaskHandler_MissingAgentID_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body, _ := json.Marshal(map[string]string{})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-W48-CLM/claim", bytes.NewReader(body))
	req.URL.Path = "/api/tasks/TASK-W48-CLM/claim"
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent_id, got %d: %s", w.Code, w.Body.String())
	}
}

func TestClaimTaskHandler_TaskNotFound_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	body, _ := json.Marshal(map[string]string{"agent_id": "agent-w48"})
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/NO-SUCH-TASK-W48/claim", bytes.NewReader(body))
	req.URL.Path = "/api/tasks/NO-SUCH-TASK-W48/claim"
	w := httptest.NewRecorder()
	claimTaskHandler(w, req)
	if w.Code == http.StatusOK {
		t.Error("expected non-200 for missing task, got 200")
	}
}

// ─── fleetAutoDeflatePatrol ───────────────────────────────────────────────

func TestFleetAutoDeflatePatrol_NoDrainingAgents_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// No draining agents → returns nil immediately.
	if err := fleetAutoDeflatePatrol(ctx, db); err != nil {
		t.Errorf("expected nil with no draining agents, got %v", err)
	}
}

func TestFleetAutoDeflatePatrol_DrainingAgentTimedOut_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// Insert a draining agent with expired drain time (20 min ago).
	drainStart := time.Now().Add(-20 * time.Minute).UTC().Format(time.RFC3339)
	_, _ = db.ExecContext(ctx, `
		INSERT INTO agent_inventory (agent_type, node_id, drain_state, started_at)
		VALUES ('lightweight', 'prya', 'draining', ?)
	`, drainStart)

	// Should run without panic (may call killAgent which is a no-op here).
	if err := fleetAutoDeflatePatrol(ctx, db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

func TestFleetAutoDeflatePatrol_DrainingAgentStillActive_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// Insert a draining agent that started 1 minute ago (still in window).
	drainStart := time.Now().Add(-1 * time.Minute).UTC().Format(time.RFC3339)
	_, _ = db.ExecContext(ctx, `
		INSERT INTO agent_inventory (agent_type, node_id, drain_state, started_at)
		VALUES ('lightweight', 'prya', 'draining', ?)
	`, drainStart)

	if err := fleetAutoDeflatePatrol(ctx, db); err != nil {
		t.Errorf("expected nil for still-active draining agent, got %v", err)
	}
}

// ─── lane CheckGates (no-config path) ────────────────────────────────────

func TestLaneCheckGates_NoConfig_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)

	// Empty laneConfigs → CheckGates always passes (no config path).
	lm := NewLaneManager(q, svc, map[Lane]LaneConfig{})
	task := &Task{ID: "TASK-W48-LANE", Lane: "dev"}

	result, err := lm.CheckGates(context.Background(), task, LaneTest)
	if err != nil {
		t.Errorf("expected nil err, got %v", err)
	}
	if result == nil {
		t.Error("expected non-nil GateResult")
	}
	if !result.Passed {
		t.Error("expected gates to pass with no config")
	}
}

func TestLaneProgressTask_UnknownLane_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	_, _ = db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, lane, created_at, updated_at)
		VALUES ('TASK-W48-LPT', 'test', 'proj', 'feature', 'Lane Task', 'queued', 'QUEUED', 50, 'done', datetime('now'), datetime('now'))
	`)

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	lm := NewLaneManager(q, svc, map[Lane]LaneConfig{})

	// 'done' lane → ProgressTask returns error (already final lane)
	_, err = lm.ProgressTask(ctx, "TASK-W48-LPT")
	if err == nil {
		t.Error("expected error for task in final lane, got nil")
	}
}

func TestLaneProgressTask_TaskNotFound_W48(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	lm := NewLaneManager(q, svc, map[Lane]LaneConfig{})

	_, err = lm.ProgressTask(context.Background(), "NO-SUCH-TASK-W48")
	if err == nil {
		t.Error("expected error for nonexistent task, got nil")
	}
}
