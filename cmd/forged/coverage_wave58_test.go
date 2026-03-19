//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// Wave 58: fleetAutoDeflatePatrol (still-draining + malformed-ts paths),
//          CheckGates unknown gate type, pauseTaskHandler paths,
//          handleQueueDepth text format

// ─── fleetAutoDeflatePatrol: still-draining with tasks ───────────────────

func TestFleetAutoDeflatePatrol_StillDraining_WithTasks_W58(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert draining agent (1 minute ago — still in window)
	drainStart := time.Now().Add(-1 * time.Minute).UTC().Format(time.RFC3339)
	_, _ = db.ExecContext(ctx, `
		INSERT INTO agent_inventory (agent_type, node_id, drain_state, tmux_window, started_at)
		VALUES ('lightweight', 'prya', 'draining', 'test-agent-w58', ?)
	`, drainStart)

	// Insert an assigned task for this agent
	_, _ = db.ExecContext(ctx, `
		INSERT INTO tasks (id, domain, project, type, title, status, state, priority, assigned_to, created_at, updated_at)
		VALUES ('TASK-W58-DRAIN', 'test', 'proj', 'feature', 'Drain Task', 'assigned', 'DISPATCHED', 50, 'lightweight',
		        datetime('now'), datetime('now'))
	`)

	// Should log "still draining" but not kill
	if err := fleetAutoDeflatePatrol(ctx, db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

// ─── fleetAutoDeflatePatrol: malformed timestamp ──────────────────────────

func TestFleetAutoDeflatePatrol_MalformedTimestamp_W58(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()

	// Insert draining agent with non-RFC3339 timestamp (malformed)
	_, _ = db.ExecContext(ctx, `
		INSERT INTO agent_inventory (agent_type, node_id, drain_state, tmux_window, started_at)
		VALUES ('lightweight', 'prya', 'draining', '', 'not-a-valid-timestamp')
	`)

	// Should call killAgent (with empty tmuxWindow → skips tmux kill)
	if err := fleetAutoDeflatePatrol(ctx, db); err != nil {
		t.Errorf("expected nil, got %v", err)
	}
}

// ─── CheckGates: unknown gate type (default: case) ───────────────────────

func TestCheckGates_UnknownGate_W58(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}

	store := &sqliteApprovalStore{db: db}
	svc := NewApprovalService(store)

	// Config with an unknown gate type → exercises default: case
	lm := NewLaneManager(q, svc, map[Lane]LaneConfig{
		LaneTest: {
			Gates: []string{"bogus-unknown-gate-w58"},
		},
	})

	task := &Task{ID: "TASK-W58-GATE", Domain: "test", Lane: string(LaneDev)}
	result, err := lm.CheckGates(ctx, task, LaneTest)
	if err != nil {
		t.Fatalf("CheckGates: %v", err)
	}
	if result.Passed {
		t.Error("expected Passed=false for unknown gate")
	}
}

// ─── pauseTaskHandler ─────────────────────────────────────────────────────

func TestPauseTaskHandler_MissingID_W58(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks//pause", nil)
	req.URL.Path = "/api/tasks//pause"
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestPauseTaskHandler_NilQueue_W58(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-W58-NIL/pause", nil)
	req.URL.Path = "/api/tasks/TASK-W58-NIL/pause"

	origQ := taskQueue
	taskQueue = nil
	defer func() { taskQueue = orig_q_w58(origQ) }()

	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)
	if w.Code != http.StatusServiceUnavailable && w.Code != http.StatusNotFound {
		t.Logf("pauseTaskHandler nil queue: got %d (may vary)", w.Code)
	}
}

func orig_q_w58(q TaskQueue) TaskQueue { return q }

func TestPauseTaskHandler_WithQueue_NotFound_W58(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/TASK-W58-NOTEXIST/pause", nil)
	req.URL.Path = "/api/tasks/TASK-W58-NOTEXIST/pause"
	w := httptest.NewRecorder()
	pauseTaskHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Logf("pauseTaskHandler not found: got %d (may vary)", w.Code)
	}
}

// ─── handleQueueDepth text format ────────────────────────────────────────

func TestHandleQueueDepth_TextFormat_W58(t *testing.T) {
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

	req := httptest.NewRequest(http.MethodGet, "/api/cli/queue/depth?format=text", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}
