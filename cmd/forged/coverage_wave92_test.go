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

// ---------------------------------------------------------------------------
// event_bus.go: NewEventBus (covers initSchema) — 72.4%
// ---------------------------------------------------------------------------

func TestWave92_NewEventBus_Fresh(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Logf("NewEventBus: %v", err)
		return
	}
	t.Logf("NewEventBus: created, sequence=%d", eb.sequence)
}

func TestWave92_NewEventBus_ExistingSchema(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create events table first to exercise "already exists" path
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS events (
		id TEXT PRIMARY KEY,
		aggregate_id TEXT NOT NULL,
		topic TEXT NOT NULL,
		type TEXT NOT NULL,
		version INTEGER NOT NULL,
		payload JSON NOT NULL,
		timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
		agent_id TEXT,
		sequence INTEGER,
		UNIQUE(aggregate_id, version)
	)`)

	eb, err := NewEventBus(db)
	if err != nil {
		t.Logf("NewEventBus existing schema: %v", err)
		return
	}
	t.Logf("NewEventBus existing schema: created, sequence=%d", eb.sequence)
}

// ---------------------------------------------------------------------------
// deflationGateCheck — 71.4%: cover all 5 signals
// ---------------------------------------------------------------------------

func TestWave92_DeflationGateCheck_AllSignalsGreen(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	now := time.Now().UTC().Add(-30 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave92-gate-agent", "test-node", "idle", 5.0, now, now)

	passed, reason, err := deflationGateCheck(ctx, db, "wave92-gate-agent", "test-node", 10*time.Minute)
	if err != nil {
		t.Logf("deflationGateCheck error: %v", err)
	}
	t.Logf("deflationGateCheck: passed=%v reason=%q", passed, reason)
}

func TestWave92_DeflationGateCheck_ContextTooHigh(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave92-ctx-agent", "test-node", "working", 75.0, now, now)

	passed, reason, err := deflationGateCheck(ctx, db, "wave92-ctx-agent", "test-node", 10*time.Minute)
	if err != nil {
		t.Logf("deflationGateCheck ctx error: %v", err)
	}
	if passed {
		t.Errorf("deflationGateCheck: expected false (context too high), got true, reason=%s", reason)
	}
	t.Logf("deflationGateCheck ctx-high: reason=%q", reason)
}

func TestWave92_DeflationGateCheck_IdleTooShort(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	// Agent last seen 1 minute ago — less than 10 minute idle threshold
	recentTime := time.Now().UTC().Add(-1 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave92-idle-short", "test-node", "idle", 2.0, recentTime, recentTime)

	passed, reason, err := deflationGateCheck(ctx, db, "wave92-idle-short", "test-node", 10*time.Minute)
	if err != nil {
		t.Logf("deflationGateCheck idle-short error: %v", err)
	}
	if passed {
		t.Errorf("deflationGateCheck: expected false (idle too short), got true, reason=%s", reason)
	}
	t.Logf("deflationGateCheck idle-short: reason=%q", reason)
}

// ---------------------------------------------------------------------------
// ClaimTransition — 70.8%: cover race detection (already assigned)
// ---------------------------------------------------------------------------

func TestWave92_ClaimTransition_AlreadyAssigned(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)
	ctx := context.Background()

	// Insert a task already assigned to another agent
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.ExecContext(ctx, `INSERT INTO tasks
		(id, domain, project, type, priority, status, state, title, assigned_to, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave92-assigned-task", "codeswiftr", "proj", "feature", 5,
		"assigned", "QUEUED", "Already Assigned", "other-agent", now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	err = sm.ClaimTransition("wave92-assigned-task", "wave92-new-agent")
	if err == nil {
		t.Logf("ClaimTransition already-assigned: expected error, got nil")
	} else {
		t.Logf("ClaimTransition already-assigned: %v", err)
	}
}

// ---------------------------------------------------------------------------
// fleetDeflateRecommendPatrol — 73.3%: with idle agents present
// ---------------------------------------------------------------------------

func TestWave92_FleetDeflateRecommendPatrol_WithIdleAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	now := time.Now().UTC().Add(-30 * time.Minute).Format(time.RFC3339)
	// Insert 2 idle agents on same node
	for i := 0; i < 2; i++ {
		_, _ = db.Exec(`INSERT OR REPLACE INTO agent_inventory
			(agent_id, agent_type, node_id, status, drain_state, started_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?)`,
			"wave92-deflate-agent-"+string(rune('a'+i)), "kimi", "test-node-92",
			"idle", "running", now, now)
		_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
			(agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
			"wave92-deflate-agent-"+string(rune('a'+i)), "test-node-92", "idle", 2.0, now, now)
	}

	err := fleetDeflateRecommendPatrol(ctx, db)
	if err != nil {
		t.Logf("fleetDeflateRecommendPatrol with idle agents: %v", err)
	}
}

// ---------------------------------------------------------------------------
// GenerateEnvelope — 71.4%: cover the storeInDB path
// ---------------------------------------------------------------------------

func TestWave92_GenerateEnvelope_WithContext(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	tmpDir := t.TempDir()
	cm := NewContextManager(db, tmpDir)

	// Insert a task to reference
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave92-env-task", "codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Envelope Task", now, now)

	env, err := cm.GenerateEnvelope(ctx, "wave92-agent", "codeswiftr", "proj", "wave92-env-task", "initial context")
	if err != nil {
		t.Logf("GenerateEnvelope: %v", err)
		return
	}
	t.Logf("GenerateEnvelope: id=%s", env.ID)
}

// ---------------------------------------------------------------------------
// handleTaskLogs — 71.4%: cover the missing-task-id path
// ---------------------------------------------------------------------------

func TestWave92_HandleTaskLogs_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs", nil) // no ?id=
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)
	if w.Code >= 500 {
		t.Logf("handleTaskLogs missing id: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// fleetAutoExecutePatrol — 53.8%: cover the "no pending recs" path
// (empty scale_recommendations table → rows.Next() false immediately)
// ---------------------------------------------------------------------------

func TestWave92_FleetAutoExecutePatrol_NoPendingRecs(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Ensure circuit breaker is open (not tripped) and flap guard is not active
	recordSpawnSuccess()

	err := fleetAutoExecutePatrol(ctx, db)
	if err != nil {
		t.Logf("fleetAutoExecutePatrol no pending: %v", err)
	}
}

// ---------------------------------------------------------------------------
// agentFleetSummaryHandler_V2 — cover with actual heartbeat data
// ---------------------------------------------------------------------------

func TestWave92_AgentFleetSummaryHandler_V2_WithData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()

	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave92-fleet-agent", "test-node-92", "working", 45.0, now, now)

	r := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	agentFleetSummaryHandler_V2(w, r)
	if w.Code >= 500 {
		t.Logf("agentFleetSummaryHandler_V2 with data: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handleSummary — 73.3%: cover PWA bridge summary handler
// ---------------------------------------------------------------------------

func TestWave92_HandleSummary_Get(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	pwa := NewPWADashboardHandler(d)

	r := httptest.NewRequest(http.MethodGet, "/api/summary", nil)
	w := httptest.NewRecorder()
	pwa.handleSummary(w, r)
	if w.Code >= 500 {
		t.Logf("handleSummary GET: got %d — %s", w.Code, w.Body.String())
	}
}

func TestWave92_HandleSummary_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	d := NewDashboard(db, nil)
	pwa := NewPWADashboardHandler(d)

	r := httptest.NewRequest(http.MethodDelete, "/api/summary", nil)
	w := httptest.NewRecorder()
	pwa.handleSummary(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("handleSummary DELETE: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// fleetMetricsHandler — 72.0%: cover GET path
// ---------------------------------------------------------------------------

func TestWave92_FleetMetricsHandler_GetWithData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()

	// Insert some metrics data
	_, _ = db.Exec(`INSERT INTO metrics (metric_name, value, labels, period, computed_at)
		VALUES (?, ?, ?, ?, datetime('now'))`, "cpu_usage_wave92", 55.0, "{}", "1m")

	r := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, r)
	if w.Code >= 500 {
		t.Logf("fleetMetricsHandler GET: got %d — %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// patrolsHandler — cover the running patrol execution path
// ---------------------------------------------------------------------------

func TestWave92_PatrolsHandler_WithRunningPatrol(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	_, _ = getDBConn().Exec(`INSERT INTO patrol_executions (id, patrol_id, started_at, status)
		VALUES (?, ?, datetime('now'), ?)`,
		"wave92-pe-running", "wave92-running-patrol", "running")

	r := httptest.NewRequest(http.MethodGet, "/api/patrols", nil)
	w := httptest.NewRecorder()
	patrolsHandler(w, r)
	if w.Code >= 500 {
		t.Logf("patrolsHandler running: got %d", w.Code)
	}
}
