//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
	"strings"

	_ "github.com/mattn/go-sqlite3"
)

func TestWave265_PatrolRunHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	
	ps := NewPatrolSystem(db)
	ps.patrols = []Patrol{
		{
			ID: "test-patrol", 
			Name: "Test",
			Action: func(ctx context.Context, db *sql.DB) error {
				return nil
			},
		},
	}
	globalPatrolSystem = ps
	defer func() { globalPatrolSystem = nil }()

	req := httptest.NewRequest(http.MethodPost, "/api/patrols/test-patrol/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), `"ok":true`) {
		t.Errorf("unexpected body: %s", w.Body.String())
	}
}

func TestWave265_PatrolRunHandler_ErrorPaths(t *testing.T) {
	// 1. Method not allowed
	req := httptest.NewRequest(http.MethodGet, "/api/patrols/test/run", nil)
	w := httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}

	// 2. Not found (empty ID)
	req = httptest.NewRequest(http.MethodPost, "/api/patrols//run", nil)
	w = httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}

	// 3. System unavailable
	globalPatrolSystem = nil
	req = httptest.NewRequest(http.MethodPost, "/api/patrols/test/run", nil)
	w = httptest.NewRecorder()
	patrolRunHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestWave265_FleetRecommendationsHandler_Complex(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Create table manually
	_, _ = db.Exec(`
		CREATE TABLE IF NOT EXISTS scale_recommendations (
			id TEXT PRIMARY KEY,
			node_id TEXT NOT NULL,
			action TEXT NOT NULL,
			agent_type TEXT NOT NULL,
			reason TEXT NOT NULL,
			queue_depth INTEGER NOT NULL,
			idle_agents INTEGER NOT NULL,
			auto_execute INTEGER NOT NULL,
			status TEXT NOT NULL,
			created_at DATETIME NOT NULL,
			expires_at DATETIME NOT NULL,
			processed_at DATETIME
		)
	`)

	// 1. Pending recommendation
	db.Exec(`INSERT INTO scale_recommendations (id, node_id, action, agent_type, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES ('r1', 'n1', 'inflate', 'k', 't', 1, 0, 0, 'pending', datetime('now'), datetime('now', '+1 hour'))`)
	
	// 2. Approved recommendation (should NOT be returned by handler)
	db.Exec(`INSERT INTO scale_recommendations (id, node_id, action, agent_type, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES ('r2', 'n1', 'deflate', 'k', 't', 0, 1, 1, 'approved', datetime('now'), datetime('now', '+1 hour'))`)

	// 3. Processed recommendation (should NOT be returned by handler as it only looks for pending)
	db.Exec(`INSERT INTO scale_recommendations (id, node_id, action, agent_type, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at, processed_at)
		VALUES ('r3', 'n1', 'inflate', 'k', 't', 1, 0, 0, 'processed', datetime('now'), datetime('now', '+1 hour'), datetime('now'))`)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/recommendations", nil)
	w := httptest.NewRecorder()
	fleetRecommendationsHandler(w, req)

	if !strings.Contains(w.Body.String(), `"count":1`) {
		t.Errorf("expected 1 pending recommendation, got body: %s", w.Body.String())
	}
}

func TestWave265_PatrolExecutionsHandler_Complex(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert various executions
	db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, status) VALUES ('e1', 'p1', datetime('now', '-30 minutes'), 'completed')`)
	db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, status) VALUES ('e2', 'p1', datetime('now', '-10 minutes'), 'error')`)
	db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, status) VALUES ('e3', 'p2', datetime('now'), 'running')`)

	// 1. Filter by patrol_id only
	req := httptest.NewRequest(http.MethodGet, "/api/patrol-executions?patrol_id=p1", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	if !strings.Contains(w.Body.String(), `"count":2`) {
		t.Errorf("expected count 2 for p1, got %s", w.Body.String())
	}

	// 2. Filter by status only
	req = httptest.NewRequest(http.MethodGet, "/api/patrol-executions?status=running", nil)
	w = httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	if !strings.Contains(w.Body.String(), `"count":1`) {
		t.Errorf("expected count 1 for running, got %s", w.Body.String())
	}

	// 3. Method not allowed
	req = httptest.NewRequest(http.MethodPost, "/api/patrol-executions", nil)
	w = httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestWave265_DashHandler_Complete(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// 1. Insert data for all sections
	db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, status) VALUES ('e1', 'p1', datetime('now'), 'completed')`)
	db.Exec(`INSERT INTO tasks (id, title, domain, project, type, lane, status, state) VALUES ('t1', 'T', 'd', 'p', 'feature', 'dev', 'queued', 'QUEUED')`)
	db.Exec(`INSERT INTO approvals (id, title, agent_id, domain, type, risk_score, confidence_score, status, created_at, expires_at) 
		VALUES ('a1', 'Approve 1', 'agent-1', 'd1', 'task_completion', 0.1, 0.9, 'pending', datetime('now'), datetime('now', '+1 day'))`)

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	handler := http.HandlerFunc(dashHandler)
	handler.ServeHTTP(w, req)

	body := w.Body.String()
	if !strings.Contains(body, "Total Runs") || !strings.Contains(body, "Pending Approvals") || !strings.Contains(body, "Approve 1") {
		t.Errorf("missing sections in dash body. Sample: %s", body[:500])
	}
}

func TestWave265_NodesHealthHandler_AllBranches(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// 1. Healthy
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, last_seen) VALUES ('a1', 'node-h', 'online', datetime('now'))`)
	// 2. Degraded
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, last_seen) VALUES ('a2', 'node-d', 'offline', datetime('now'))`)
	
	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	w := httptest.NewRecorder()
	nodesHealthHandler(w, req)

	if !strings.Contains(w.Body.String(), `"status":"healthy"`) || !strings.Contains(w.Body.String(), `"status":"degraded"`) {
		t.Errorf("missing status branches: %s", w.Body.String())
	}
}

func TestWave265_FleetSnapshotHandler_FullCoverage(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Add data with future last_seen to avoid drift issues in test
	db.Exec(`INSERT INTO agent_heartbeats (agent_id, node, status, current_task_id, context_pct, last_seen) 
		VALUES ('a1', 'n1', 'online', 't1', 0.5, datetime('now', '+1 minute'))`)
	db.Exec(`INSERT INTO tasks (id, title, domain, project, type, lane, status, state) VALUES ('t1', 'Title', 'd', 'p', 'f', 'dev', 'queued', 'QUEUED')`)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/snapshot", nil)
	w := httptest.NewRecorder()
	fleetSnapshotHandler(w, req)

	if !strings.Contains(w.Body.String(), `"total_agents":1`) {
		t.Errorf("expected total_agents: 1, got: %s", w.Body.String())
	}
}

func TestWave265_PatrolsHandler_AllBranches(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ps := NewPatrolSystem(db)
	ps.patrols = []Patrol{{ID: "p1", Name: "P1", Schedule: 1 * time.Minute}}
	globalPatrolSystem = ps
	defer func() { globalPatrolSystem = nil }()

	// Add running execution to trigger isRunning = 1
	db.Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, status) VALUES ('e1', 'p1', datetime('now'), 'running')`)

	req := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, req)

	if !strings.Contains(w.Body.String(), `"status":"running"`) {
		t.Error("expected running status in patrols output")
	}
}

func TestWave265_PatrolsHandler_TimestampNormalization(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert execution with SQLite format timestamp
	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO patrol_executions (id, patrol_id, started_at, status)
		VALUES ('exec-1', 'test-patrol', ?, 'completed')
	`, now)
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, req)

	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	patrols := resp["patrols"].([]interface{})
	
	found := false
	for _, p := range patrols {
		pe := p.(map[string]interface{})
		if pe["id"] == "test-patrol" {
			found = true
			if pe["last_run"] == nil {
				t.Error("expected last_run to be set")
			} else {
				lastRun := pe["last_run"].(string)
				if !strings.Contains(lastRun, "T") {
					t.Errorf("expected RFC3339 format, got %s", lastRun)
				}
			}
		}
	}
	if !found {
		t.Error("test-patrol not found in results")
	}
}

func TestWave265_DashHandler_DurationCalculation(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert execution with duration
	start := "2026-03-14 10:00:00"
	end := "2026-03-14 10:00:05"
	_, err := db.Exec(`
		INSERT INTO patrol_executions (id, patrol_id, started_at, completed_at, status)
		VALUES ('exec-dur', 'dur-patrol', ?, ?, 'completed')
	`, start, end)
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodGet, "/dash", nil)
	w := httptest.NewRecorder()
	handler := http.HandlerFunc(dashHandler)
	handler.ServeHTTP(w, req)

	body := w.Body.String()
	if !strings.Contains(body, "5.0s") {
		t.Errorf("expected duration 5.0s in dashboard output")
	}
}
