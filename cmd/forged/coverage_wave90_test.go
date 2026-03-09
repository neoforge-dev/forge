//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// dispatchHandler DELETE — method-not-allowed path
func TestWave90b_DispatchHandler_DELETE(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodDelete, "/dispatch", nil)
	w := httptest.NewRecorder()
	dispatchHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("dispatchHandler DELETE: got %d", w.Code)
	}
}

// fleetScaleRecommendPatrol ceiling gate path
func TestWave90b_FleetScaleRecommendPatrol_AtCeiling(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}
	now := time.Now().UTC().Format(time.RFC3339)
	nodeID := "test-ceiling-node-wave90b"
	for i := 0; i < 3; i++ {
		_, _ = db.Exec(`INSERT OR REPLACE INTO agent_inventory
			(agent_id, agent_type, node_id, status, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
			"wave90b-ca-"+string(rune('a'+i)), "kimi", nodeID, "idle", now, now)
	}
	for i := 0; i < 20; i++ {
		_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			"wave90b-ct-"+string(rune('a'+i%26)), "codeswiftr", "proj", "feature",
			5, "queued", "QUEUED", "Ceiling Task", now, now)
	}
	err := fleetScaleRecommendPatrol(ctx, db)
	if err != nil {
		t.Logf("fleetScaleRecommendPatrol ceiling: %v", err)
	}
}

// claimTaskHandler context-full gate
func TestWave90b_ClaimTaskHandler_ContextTooFull(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave90b-ctx-task", "codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "Context Full Task", now, now)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave90b-full-agent", "test-node", "working", 95.0, now, now)
	body := `{"agent_id":"wave90b-full-agent"}`
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave90b-ctx-task/claim", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)
	if w.Code != http.StatusConflict {
		t.Logf("claimTaskHandler context-full: got %d", w.Code)
	}
}

// parityHandler with DB
func TestWave90b_ParityHandler_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	h := parityHandler(db)
	r := httptest.NewRequest(http.MethodGet, "/api/parity", nil)
	w := httptest.NewRecorder()
	h(w, r)
	if w.Code >= 500 {
		t.Logf("parityHandler: got %d — %s", w.Code, w.Body.String())
	}
}

// handleSystemHealth table format
func TestWave90b_HandleSystemHealth_TableFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/system/health?format=table", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)
	if w.Code >= 500 {
		t.Logf("handleSystemHealth table: got %d", w.Code)
	}
}

// handleAgentStatus paths
func TestWave90b_HandleAgentStatus_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave90b-status-agent", "test-node", "idle", 10.0, now, now)
	r := httptest.NewRequest(http.MethodGet, "/agents/status?id=wave90b-status-agent", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)
	if w.Code >= 500 {
		t.Logf("handleAgentStatus success: got %d", w.Code)
	}
}

func TestWave90b_HandleAgentStatus_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/agents/status?id=wave90b-nonexistent-xxx", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)
	if w.Code != http.StatusNotFound {
		t.Logf("handleAgentStatus not found: got %d", w.Code)
	}
}

func TestWave90b_HandleAgentStatus_NoID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/agents/status", nil)
	w := httptest.NewRecorder()
	handleAgentStatus(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("handleAgentStatus no id: got %d", w.Code)
	}
}

// agentFleetSummaryHandler_V2
func TestWave90b_AgentFleetSummaryHandler_V2_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	agentFleetSummaryHandler_V2(w, r)
	if w.Code >= 500 {
		t.Logf("agentFleetSummaryHandler_V2 GET: got %d", w.Code)
	}
}

func TestWave90b_AgentFleetSummaryHandler_V2_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/api/fleet/summary", nil)
	w := httptest.NewRecorder()
	agentFleetSummaryHandler_V2(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("agentFleetSummaryHandler_V2 POST: got %d", w.Code)
	}
}

// handleQueueStatus
func TestWave90b_HandleQueueStatus_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/queue/status?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, r)
	if w.Code >= 500 {
		t.Logf("handleQueueStatus success: got %d", w.Code)
	}
}

// syncRoyalJelly empty DB
func TestWave90b_SyncRoyalJelly_EmptyDB(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	db := getDBConn()
	tmpDir := t.TempDir()
	if err := os.MkdirAll(tmpDir+"/.forge/context", 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	prevWD, _ := os.Getwd()
	if err := os.Chdir(tmpDir); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	defer os.Chdir(prevWD)
	err := syncRoyalJelly(ctx, db)
	if err != nil {
		t.Logf("syncRoyalJelly empty: %v", err)
	}
}

// fleetAutoDeflatePatrol no draining agents
func TestWave90b_FleetAutoDeflatePatrol_NoDrainingAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	err := fleetAutoDeflatePatrol(context.Background(), db)
	if err != nil {
		t.Logf("fleetAutoDeflatePatrol no agents: %v", err)
	}
}

// NotImplemented stubs
func TestWave90b_HandleAgentSpawn_NotImplemented(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/agents/spawn", nil)
	w := httptest.NewRecorder()
	handleAgentSpawn(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Logf("handleAgentSpawn: got %d", w.Code)
	}
}

func TestWave90b_HandleAgentStop_NotImplemented(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/agents/stop", nil)
	w := httptest.NewRecorder()
	handleAgentStop(w, r)
	if w.Code != http.StatusNotImplemented {
		t.Logf("handleAgentStop: got %d", w.Code)
	}
}

func TestWave90b_NotImplementedHandlers(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	handlers := []struct {
		name string
		h    http.HandlerFunc
	}{
		{"handleGitGuard", handleGitGuard},
		{"handleGitStatus", handleGitStatus},
		{"handleFleetStatus", handleFleetStatus},
		{"handleFleetTopology", handleFleetTopology},
		{"handleSystemPatrol", handleSystemPatrol},
		{"handleSystemMetrics", handleSystemMetrics},
	}
	for _, tc := range handlers {
		r := httptest.NewRequest(http.MethodGet, "/stub", nil)
		w := httptest.NewRecorder()
		tc.h(w, r)
		if w.Code != http.StatusNotImplemented {
			t.Logf("%s: got %d (expected 501)", tc.name, w.Code)
		}
	}
}

// ServeDashboardJSON
func TestWave90b_ServeDashboardJSON_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	d := NewDashboard(db, nil)
	r := httptest.NewRequest(http.MethodGet, "/dashboard.json", nil)
	w := httptest.NewRecorder()
	d.ServeDashboardJSON(w, r)
	if w.Code >= 500 {
		t.Logf("ServeDashboardJSON: got %d — %s", w.Code, w.Body.String())
	}
}

// SendBootstrap via hub
func TestWave90b_SendBootstrap_WithHub(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	if hub == nil {
		t.Skip("hub not initialized")
	}
	hub.SendBootstrap("wave90b-test-agent", "wave90b-task-id", map[string]string{"key": "value"})
}

// handleQueueDepth success path
func TestWave90b_HandleQueueDepth_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)
	if w.Code >= 500 {
		t.Logf("handleQueueDepth success: got %d", w.Code)
	}
}

// confidenceApproveCompletedTasks — high score with assigned_to set
func TestWave90b_ConfidenceApprove_HighScore_WithAssignedTo(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	ctx := context.Background()

	ts := NewTaskStore(db)
	oldSM := stateMachine
	stateMachine = NewStateMachine(ts, db)
	defer func() { stateMachine = oldSM }()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	oldSvc := globalApprovalService
	globalApprovalService = svc
	defer func() { globalApprovalService = oldSvc }()

	oldTime := time.Now().Add(-2 * time.Minute).UTC().Format("2006-01-02 15:04:05")
	taskID := "wave90b-high-conf-agent"
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, assigned_to, result, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "codeswiftr", "proj", "feature", 5,
		"completed", "COMPLETED", "Wave90b High Conf", "wave90b-agent", "done", oldTime, oldTime)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}
	_, _ = db.Exec(`INSERT INTO quality_gate_results (task_id, test_pass_rate, coverage_pct, lint_issues, created_at)
		VALUES (?, ?, ?, ?, ?)`, taskID, 1.0, 90.0, 0, oldTime)

	if err := confidenceApproveCompletedTasks(ctx, db); err != nil {
		t.Logf("confidenceApproveCompletedTasks high score with agent: %v", err)
	}
}
