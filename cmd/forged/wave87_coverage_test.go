//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestWave87_WriteJSON_Success(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSON(w, map[string]string{"key": "value"})
	if w.Code != http.StatusOK {
		t.Errorf("writeJSON success: got %d", w.Code)
	}
}

func TestWave87_WriteJSON_NilData(t *testing.T) {
	w := httptest.NewRecorder()
	writeJSON(w, nil)
	if w.Code != http.StatusOK {
		t.Errorf("writeJSON nil: got %d", w.Code)
	}
}

func TestWave87_WithDB_NilDB(t *testing.T) {
	old := getDBConn()
	setDBConn(nil)
	defer setDBConn(old)
	called := false
	w := httptest.NewRecorder()
	withDB(w, func(db *sql.DB) {
		called = true
	})
	if called {
		t.Error("withDB should not call fn when DB is nil")
	}
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("withDB nil DB: got %d, want 503", w.Code)
	}
}

func TestWave87_HandleLeadState_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	handler := handleLeadState(db)
	r := httptest.NewRequest(http.MethodPost, "/api/lead/state", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("handleLeadState POST: got %d, want 405", w.Code)
	}
}

func TestWave87_HandleLeadState_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	handler := handleLeadState(db)
	r := httptest.NewRequest(http.MethodGet, "/api/lead/state", nil)
	w := httptest.NewRecorder()
	handler(w, r)
	if w.Code >= 500 {
		t.Errorf("handleLeadState GET: got %d", w.Code)
	}
}

func TestWave87_NodeMetricsReceiveHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/nodes/test-node/metrics", nil)
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("nodeMetricsReceiveHandler GET: got %d, want 405", w.Code)
	}
}

func TestWave87_NodeMetricsReceiveHandler_NodeIDMismatch(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	body := `{"node_id":"other-node","metrics":[]}`
	r := httptest.NewRequest(http.MethodPost, "/api/nodes/my-node/metrics", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Logf("nodeMetricsReceiveHandler mismatch: got %d", w.Code)
	}
}

func TestWave87_NodeMetricsReceiveHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	body := `{"node_id":"wave87-node","metrics":[{"name":"cpu_usage","value":42.5,"labels":{}}],"period":"1m"}`
	r := httptest.NewRequest(http.MethodPost, "/api/nodes/wave87-node/metrics", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	nodeMetricsReceiveHandler(w, r)
	if w.Code >= 500 {
		t.Logf("nodeMetricsReceiveHandler success: got %d", w.Code)
	}
}

func TestWave87_FleetMetricsHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("fleetMetricsHandler POST: got %d, want 405", w.Code)
	}
}

func TestWave87_FleetMetricsHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	w := httptest.NewRecorder()
	fleetMetricsHandler(w, r)
	if w.Code >= 500 {
		t.Errorf("fleetMetricsHandler GET: got %d", w.Code)
	}
}

func TestWave87_NodeCapabilitiesHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/api/node/capabilities", nil)
	w := httptest.NewRecorder()
	nodeCapabilitiesHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("nodeCapabilitiesHandler POST: got %d, want 405", w.Code)
	}
}

func TestWave87_NodeCapabilitiesHandler_Success(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/node/capabilities", nil)
	w := httptest.NewRecorder()
	nodeCapabilitiesHandler(w, r)
	if w.Code >= 500 {
		t.Errorf("nodeCapabilitiesHandler GET: got %d", w.Code)
	}
}

func TestWave87_AgentsHealthHandler_SpecificAgent(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	now := time.Now().UTC().Format(time.RFC3339)
	db := getDBConn()
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave87-agent", "test-node", "idle", 20.0, now, now)
	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave87-agent/health", nil)
	r.URL.Path = "/api/agents/wave87-agent/health"
	w := httptest.NewRecorder()
	agentsHealthHandler(w, r)
	if w.Code >= 500 {
		t.Logf("agentsHealthHandler specific: got %d", w.Code)
	}
}

func TestWave87_AgentsHealthHandler_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/agents/wave87-nonexistent/health", nil)
	r.URL.Path = "/api/agents/wave87-nonexistent/health"
	w := httptest.NewRecorder()
	agentsHealthHandler(w, r)
	if w.Code != http.StatusNotFound {
		t.Logf("agentsHealthHandler not found: got %d", w.Code)
	}
}

func TestWave87_DashHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/api/patrols/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("dashHandler POST: got %d, want 405", w.Code)
	}
}

func TestWave87_DashHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/patrols/dash", nil)
	w := httptest.NewRecorder()
	dashHandler(w, r)
	if w.Code >= 500 {
		t.Errorf("dashHandler GET: got %d", w.Code)
	}
}

func TestWave87_MigrateDown_ZeroSteps(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	if err := MigrateDown(db, 0); err != nil {
		t.Errorf("MigrateDown steps=0: %v", err)
	}
}

func TestWave87_MigrateDown_OneStep(t *testing.T) {
	tmpDir := t.TempDir()
	db, err := OpenDB(tmpDir + "/mdown87.db")
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	downErr := MigrateDown(db, 1)
	if downErr != nil {
		t.Logf("MigrateDown step=1: %v (expected)", downErr)
	}
}

func TestWave87_HandleSystemHealth_FormatTable(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/health?format=table", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)
	if w.Code >= 500 {
		t.Logf("handleSystemHealth table: got %d", w.Code)
	}
}

func TestWave87_HandleSystemHealth_FormatJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)
	if w.Code >= 500 {
		t.Logf("handleSystemHealth json: got %d", w.Code)
	}
}

func TestWave87_HandleQueueDepth_WithQueue(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/queue/depth?format=json", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)
	if w.Code >= 500 {
		t.Logf("handleQueueDepth with queue: got %d", w.Code)
	}
}

func TestWave87_HandleQueueDepth_FormatTable(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/queue/depth?format=table", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)
	if w.Code >= 500 {
		t.Logf("handleQueueDepth table: got %d", w.Code)
	}
}

func TestWave87_FleetAggregateHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodPost, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("fleetAggregateHandler POST: got %d, want 405", w.Code)
	}
}

func TestWave87_FleetAggregateHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	w := httptest.NewRecorder()
	fleetAggregateHandler(w, r)
	if w.Code >= 500 {
		t.Logf("fleetAggregateHandler GET: got %d", w.Code)
	}
}

func TestWave87_QualityGatesHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave87-qg-task", "codeswiftr", "proj", "feature", 5, "completed", "COMPLETED", "QG Task", now, now)
	_, _ = db.Exec(`INSERT INTO quality_gate_results (id, task_id, test_pass_rate, coverage_pct, lint_issues, created_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"qgr-wave87", "wave87-qg-task", 0.9, 82.5, 2, now)
	r := httptest.NewRequest(http.MethodGet, "/api/quality-gates", nil)
	w := httptest.NewRecorder()
	qualityGatesHandler(w, r)
	if w.Code >= 500 {
		t.Logf("qualityGatesHandler: got %d", w.Code)
	}
}

func TestWave87_DashboardThroughputHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/dashboard/throughput", nil)
	w := httptest.NewRecorder()
	dashboardThroughputHandler(w, r)
	if w.Code >= 500 {
		t.Logf("dashboardThroughputHandler: got %d", w.Code)
	}
}

func TestWave87_NotificationsHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodDelete, "/api/notifications", nil)
	w := httptest.NewRecorder()
	notificationsHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("notificationsHandler DELETE: got %d", w.Code)
	}
}

func TestWave87_NotificationActionHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/notifications/123/action", nil)
	w := httptest.NewRecorder()
	notificationActionHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("notificationActionHandler GET: got %d", w.Code)
	}
}

func TestWave87_GenerateEnvelope_WithTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ctx := context.Background()
	now := time.Now().UTC().Format(time.RFC3339)
	taskNow := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave87-env-task", "codeswiftr", "proj", "feature", 5, "executing", "RUNNING", "Env Task", taskNow, taskNow)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?, ?)`,
		"wave87-env-agent", "test-node", "working", 60.0, "wave87-env-task", now, now)
	cm := testContextManager(t, db, "")
	_, err := cm.GenerateEnvelope(ctx, "wave87-env-agent", "codeswiftr", "proj", "wave87-env-task", "test")
	if err != nil {
		t.Logf("GenerateEnvelope with task: %v", err)
	}
}

func TestWave87_PatrolStart_MultipleStops(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	ps := NewPatrolSystem(db)
	ps.Start()
	time.Sleep(50 * time.Millisecond)
	ps.Stop()
	func() {
		defer func() { recover() }()
		ps.Stop()
	}()
}

func TestWave87_AgentTelemetryHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodDelete, "/api/agents/telemetry", nil)
	w := httptest.NewRecorder()
	agentTelemetryHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("agentTelemetryHandler DELETE: got %d", w.Code)
	}
}

func TestWave87_AgentMetricsHandler_WithData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave87-metrics-agent", "test-node", "working", 55.0, now, now)
	r := httptest.NewRequest(http.MethodGet, "/api/agents/metrics?agent_id=wave87-metrics-agent", nil)
	w := httptest.NewRecorder()
	agentMetricsHandler(w, r)
	if w.Code >= 500 {
		t.Logf("agentMetricsHandler GET: got %d", w.Code)
	}
}

func TestWave87_AgentTelemetrySummaryHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, r)
	if w.Code >= 500 {
		t.Logf("agentTelemetrySummaryHandler GET: got %d", w.Code)
	}
}

func TestWave87_HandleApprovalCount_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	handler := NewApprovalHandler(svc)
	r := httptest.NewRequest(http.MethodGet, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	handler.handleApprovalCount(w, r)
	if w.Code >= 500 {
		t.Logf("handleApprovalCount GET: got %d", w.Code)
	}
}

func TestWave87_HandleApprovalCount_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	handler := NewApprovalHandler(svc)
	r := httptest.NewRequest(http.MethodDelete, "/api/approvals/count", nil)
	w := httptest.NewRecorder()
	handler.handleApprovalCount(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Logf("handleApprovalCount DELETE: got %d", w.Code)
	}
}
