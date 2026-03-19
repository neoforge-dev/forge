//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 180: agentTasksHandler + MigrateDown steps=0 + fleetAutoPatrol error path
// Targets:
//   agentTasksHandler   (handlers_plan.go:138) — 73.3% → method-not-allowed,
//                        missing id, db error, happy path with rows
//   MigrateDown         (migrate.go:311)       — 72.9% → steps<=0 early return
//   fleetAutoPatrol     (patrol.go:1913)        — 66.7% → error log branch
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// agentTasksHandler
// ---------------------------------------------------------------------------

func TestWave180_AgentTasksHandler_MissingAgentID(t *testing.T) {
	// Path that resolves to empty id after trim
	req := httptest.NewRequest(http.MethodGet, "/api/agents//tasks", nil)
	req.URL.Path = "/api/agents//tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	// id = TrimPrefix("/api/agents//tasks", "/api/agents/") → "/tasks"
	// then TrimSuffix("/tasks", "/tasks") → ""
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing agent id, got %d", w.Code)
	}
}

func TestWave180_AgentTasksHandler_DBError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close DB so query fails
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/tasks", nil)
	req.URL.Path = "/api/agents/kimi/tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 on DB error, got %d", w.Code)
	}
}

func TestWave180_AgentTasksHandler_HappyPath_NoTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/nobody/tasks", nil)
	req.URL.Path = "/api/agents/nobody/tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

func TestWave180_AgentTasksHandler_HappyPath_WithTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)
	setDBConn(db)
	defer setDBConn(nil)

	// Insert a task assigned to agent "kimi"
	_, err := db.Exec(`INSERT INTO tasks
		(id, domain, project, type, title, status, priority, assigned_to, created_at, updated_at)
		VALUES ('wave180-t1','test','p1','feature','T1','assigned',5,'kimi',
		        datetime('now'), datetime('now'))`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/agents/kimi/tasks", nil)
	req.URL.Path = "/api/agents/kimi/tasks"
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	body := w.Body.String()
	if body == "" {
		t.Error("expected non-empty JSON body")
	}
}

// ---------------------------------------------------------------------------
// MigrateDown — steps <= 0 returns nil immediately
// ---------------------------------------------------------------------------

func TestWave180_MigrateDown_ZeroSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	if err := MigrateDown(db, 0); err != nil {
		t.Errorf("expected nil for steps=0, got %v", err)
	}
}

func TestWave180_MigrateDown_NegativeSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	if err := MigrateDown(db, -1); err != nil {
		t.Errorf("expected nil for steps=-1, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// fleetAutoPatrol — error log branch (line 1915)
// Make fleetAutoExecutePatrol return error by calling with no scale_recommendations table
// and bypassing NoAutoSpawnNodes + circuit breaker guards.
// ---------------------------------------------------------------------------

func TestWave180_FleetAutoPatrol_ExecPatrolError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	t.Cleanup(cleanup)

	// Bypass NoAutoSpawnNodes guard
	hostname, _ := os.Hostname()
	origVal := NoAutoSpawnNodes[hostname]
	delete(NoAutoSpawnNodes, hostname)
	defer func() {
		if origVal {
			NoAutoSpawnNodes[hostname] = true
		}
	}()

	// Bypass circuit breaker
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	// Bypass flap guard — set lastScaleEvent to zero time
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()

	// The DB from setupClaimTestDB may or may not have scale_recommendations.
	// Drop it to force a query error that causes fleetAutoExecutePatrol to return error,
	// which triggers the log branch in fleetAutoPatrol.
	db.Exec(`DROP TABLE IF EXISTS scale_recommendations`)

	// fleetAutoPatrol logs the error but returns the result of fleetAutoDeflatePatrol
	err := fleetAutoPatrol(context.Background(), db)
	_ = err // return value depends on fleetAutoDeflatePatrol; just ensure no panic
}
