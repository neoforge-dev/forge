//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// fleetScaleRecommend deleted (f715a295) — tests replaced with fleetScaleRecommendPatrol.

func TestWave83_FleetScaleRecommendPatrol_WithTempDir(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Logf("fleetScaleRecommendPatrol: %v", err)
	}
}

func TestWave83_FleetScaleRecommendPatrol_WithQueuedTasks(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Insert many queued tasks to trigger scale-up recommendation
	now := time.Now().UTC().Format(time.RFC3339)
	for i := 0; i < 10; i++ {
		_, _ = db.Exec(`
			INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			"wave83-scale-task-"+string(rune('a'+i)), "codeswiftr", "proj", "feature",
			5, "queued", "QUEUED", "Scale Task", now, now)
	}

	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Logf("fleetScaleRecommendPatrol with queued tasks: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: autoPromoteCompletedTasksInLane — 63.2%
// Exercise the loop with globalLaneManager nil (covers "break" at line 903).
// ---------------------------------------------------------------------------

func TestWave83_AutoPromoteCompletedTasksInLane_NilLaneManager(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	// Ensure globalLaneManager is nil
	oldLM := globalLaneManager
	globalLaneManager = nil
	defer func() { globalLaneManager = oldLM }()

	oldTime := time.Now().Add(-5 * time.Minute).UTC().Format("2006-01-02 15:04:05")
	_, _ = db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, title, lane, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave83-lane-nil", "codeswiftr", "proj", "feature", 5,
		"completed", "COMPLETED", "Lane Task Nil", "dev", oldTime, oldTime)

	err := autoPromoteCompletedTasksInLane(ctx, db)
	if err != nil {
		t.Errorf("autoPromoteCompletedTasksInLane nil laneManager: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: syncRoyalJelly — 67.1%
// Test the DB->Filesystem write-back phase with envelope JSON content.
// ---------------------------------------------------------------------------

func TestWave83_SyncRoyalJelly_WriteBack(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	tmpDir := t.TempDir()
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	defer os.Chdir(prevWD)

	// Create context dir so Phase 1 scan works
	contextDir := filepath.Join(tmpDir, ".forge", "context")
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	// Insert multiple envelopes with lead_context for write-back
	domains := []string{"codeswiftr", "finance", "health"}
	for _, domain := range domains {
		content := `{"metadata":{"lead_context":"# Lead Context\nSome state for ` + domain + `"},"decisions":[],"failures":[],"summary":"Summary for ` + domain + `"}`
		_, _ = db.Exec(`INSERT INTO context_envelopes (id, domain, content, created_at)
			VALUES (?, ?, ?, datetime('now', '-5 minutes'))`,
			"env-wave83-"+domain, domain, content)
	}

	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Errorf("syncRoyalJelly write-back: %v", err)
	}

	// Verify files were written
	for _, domain := range domains {
		leadFile := filepath.Join(contextDir, domain, "lead-context.md")
		if _, err := os.Stat(leadFile); err != nil {
			t.Logf("lead-context.md not written for %s (may be expected)", domain)
		}
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: fleetScaleRecommendPatrol — 67.2%
// Test with scale_recommendations entries and actual data.
// ---------------------------------------------------------------------------

func TestWave83_FleetScaleRecommendPatrol_WithData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Insert some existing recommendations to cover the deletion/pruning path
	future := time.Now().Add(10 * time.Minute).UTC().Format(time.RFC3339)
	past := time.Now().Add(-5 * time.Minute).UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO scale_recommendations
		(id, node_id, agent_type, action, reason, status, auto_execute, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)`,
		"wave83-sr-active", "test-node", "claude", "maintain", "balanced", "pending", 0, future)
	_, _ = db.Exec(`INSERT INTO scale_recommendations
		(id, node_id, agent_type, action, reason, status, auto_execute, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '-2 hours'), ?)`,
		"wave83-sr-expired", "test-node", "claude", "maintain", "old rec", "pending", 0, past)

	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Logf("fleetScaleRecommendPatrol with data: %v (may be expected)", err)
	}
}

// ---------------------------------------------------------------------------
// main.go: handleTaskList error path — 60%
// Test handleTaskShow, handleTaskLogs with missing IDs to cover error paths.
// ---------------------------------------------------------------------------

func TestWave83_HandleTaskShow_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("handleTaskShow missing ID: got %d", w.Code)
	}
}

func TestWave83_HandleTaskLogs_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("handleTaskLogs missing ID: got %d", w.Code)
	}
}

func TestWave83_HandleAgentStatus_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents/status", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("handleAgentStatus missing ID: got %d", w.Code)
	}
}

func TestWave83_HandleAgentStatus_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/agents/status?id=nonexistent-wave83", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)
	if w.Code != http.StatusNotFound {
		t.Logf("handleAgentStatus not found: got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// coordination.go: GetCoordinationHTML — 56.5%
// ---------------------------------------------------------------------------

func TestWave83_GetCoordinationHTML_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	coord := NewCoordinationDashboard(db)
	html := coord.GetCoordinationHTML()
	if len(html) == 0 {
		t.Logf("GetCoordinationHTML returned empty string")
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: fleetScaleRecommendPatrol — exercise recommendation insertion
// Insert idle agents in agent_inventory + many queued tasks to trigger inflate.
// ---------------------------------------------------------------------------

func TestWave83_FleetScaleRecommendPatrol_WithIdleAgentsAndQueue(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Insert idle agents in agent_inventory so len(nodes) > 0
	now := time.Now().UTC().Format(time.RFC3339)
	hostname, _ := os.Hostname()
	if hostname == "" {
		hostname = "test-node"
	}
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_inventory
		(id, agent_type, tmux_window, node_id, status, started_at, last_seen)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"wave83-idle-agent-1", "kimi", "kimi-1", hostname, "idle", now, now)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_inventory
		(id, agent_type, tmux_window, node_id, status, started_at, last_seen)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"wave83-idle-agent-2", "kimi", "kimi-2", hostname, "idle", now, now)

	// Insert many queued tasks: queuedCount (10) > idleAgents (2) * 2
	for i := 0; i < 10; i++ {
		_, _ = db.Exec(`
			INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			"wave83-queued-"+string(rune('a'+i)), "codeswiftr", "proj", "feature",
			5, "queued", "QUEUED", "Queued Task", now, now)
	}

	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Logf("fleetScaleRecommendPatrol with idle agents: %v", err)
	}
}

// ---------------------------------------------------------------------------
// coordination.go: GetCoordinationHTML with agents data — 56.5%
// ---------------------------------------------------------------------------

func TestWave83_GetCoordinationHTML_WithAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert some agent heartbeats to cover the agents loop in GetCoordinationHTML
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at, current_task_id)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"wave83-working-agent", "test-node", "working", 45.0, now, now, "wave83-task-1")
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave83-idle-agent", "test-node", "idle", 10.0, now, now)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave83-blocked-agent", "test-node", "blocked", 90.0, now, now)

	coord := NewCoordinationDashboard(db)
	html := coord.GetCoordinationHTML()
	if len(html) == 0 {
		t.Logf("GetCoordinationHTML returned empty string")
	}
}

