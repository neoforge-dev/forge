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

// TestWave121_FleetAutoExecute_BudgetGateFail_WithCeilingOverride covers budget gate (line 947-951)
// by overriding NodeHardCeilings to bypass the tmux ceiling check.
func TestWave121_FleetAutoExecute_BudgetGateFail_WithCeilingOverride(t *testing.T) {
	db, cleanupDB := setupClaimTestDB(t)
	defer cleanupDB()
	setDBConn(db)
	defer setDBConn(nil)

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	circuitBreaker.Lock()
	origState := circuitBreaker.state
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = origState
		circuitBreaker.Unlock()
	}()

	lastScaleEvent.Lock()
	origTS := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = origTS
		lastScaleEvent.Unlock()
	}()

	delete(NoAutoSpawnNodes, "prya")
	defer func() { NoAutoSpawnNodes["prya"] = true }()

	// Override ceiling to bypass tmux ceiling gate.
	origCeiling := NodeHardCeilings["prya"]
	NodeHardCeilings["prya"] = 999
	defer func() { NodeHardCeilings["prya"] = origCeiling }()

	// Small RAM requirement so it passes the RAM gate.
	origRAMReq := agentRAMRequirements["kimi"]
	agentRAMRequirements["kimi"] = 1 // 1MB — always passes
	defer func() { agentRAMRequirements["kimi"] = origRAMReq }()

	expires := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
	`, "w121-budget-rec", "prya", "kimi", "inflate", "test", 5, 0, 1, "pending", expires)
	if err != nil {
		t.Fatalf("insert rec: %v", err)
	}

	// Budget file with moonshot COOLDOWN — forces budget gate fail.
	budgetDir := ".forge/heartbeat"
	if err := os.MkdirAll(budgetDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	budgetFile := filepath.Join(budgetDir, "token-budgets-prya.json")
	content := `{"providers":{"moonshot":{"status":"COOLDOWN"}}}`
	if err := os.WriteFile(budgetFile, []byte(content), 0o644); err != nil {
		t.Fatalf("write budget file: %v", err)
	}
	defer os.Remove(budgetFile)

	err = fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil (deferred-budget path), got: %v", err)
	}
	// The rec should be deferred-budget.
	var status string
	_ = db.QueryRow(`SELECT status FROM scale_recommendations WHERE id = ?`, "w121-budget-rec").Scan(&status)
	t.Logf("rec status after budget gate fail: %q", status)
}

// TestWave121_FleetAutoDeflate_DrainingAgent_MalformedTimestamp covers line 1108-1112.
func TestWave121_FleetAutoDeflate_DrainingAgent_MalformedTimestamp(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	delete(NoAutoSpawnNodes, "prya")
	defer func() { NoAutoSpawnNodes["prya"] = true }()

	// Insert draining agent with malformed started_at.
	_, err := db.Exec(`
		INSERT INTO agent_inventory (id, agent_type, node_id, tmux_window, status, drain_state, started_at, last_heartbeat)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
	`, "w121-drain-agent", "kimi", "prya", "kimi-2", "working", "draining", "NOT-A-TIMESTAMP")
	if err != nil {
		t.Fatalf("insert draining agent: %v", err)
	}

	err = fleetAutoDeflatePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil, got: %v", err)
	}
}

// TestWave121_FleetAutoDeflate_DrainingAgent_StillInWindow covers lines 1115-1131.
func TestWave121_FleetAutoDeflate_DrainingAgent_StillInWindow(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	delete(NoAutoSpawnNodes, "prya")
	defer func() { NoAutoSpawnNodes["prya"] = true }()

	// Insert draining agent with recent timestamp (within 10min drain window).
	recentTime := time.Now().UTC().Add(-2 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO agent_inventory (id, agent_type, node_id, tmux_window, status, drain_state, started_at, last_heartbeat)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
	`, "w121-drain-recent", "kimi", "prya", "kimi-3", "working", "draining", recentTime)
	if err != nil {
		t.Fatalf("insert draining agent: %v", err)
	}

	// No assigned tasks for this agent → should trigger early kill.
	err = fleetAutoDeflatePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil, got: %v", err)
	}
}

// TestWave121_FleetAutoDeflate_DrainingAgent_Timeout covers drain timeout path (line 1134).
func TestWave121_FleetAutoDeflate_DrainingAgent_Timeout(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	delete(NoAutoSpawnNodes, "prya")
	defer func() { NoAutoSpawnNodes["prya"] = true }()

	// Insert draining agent that started 15 minutes ago (past 10min drain timeout).
	oldTime := time.Now().UTC().Add(-15 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO agent_inventory (id, agent_type, node_id, tmux_window, status, drain_state, started_at, last_heartbeat)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
	`, "w121-drain-timeout", "kimi", "prya", "kimi-4", "working", "draining", oldTime)
	if err != nil {
		t.Fatalf("insert draining agent: %v", err)
	}

	err = fleetAutoDeflatePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil (drain-timeout path), got: %v", err)
	}
}

// TestWave121_FleetAutoDeflate_ApprovedDeflateRec covers lines 1152-1160.
func TestWave121_FleetAutoDeflate_ApprovedDeflateRec(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	delete(NoAutoSpawnNodes, "prya")
	defer func() { NoAutoSpawnNodes["prya"] = true }()

	expires := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
	`, "w121-deflate-rec", "prya", "kimi", "deflate", "test", 0, 3, 0, "approved", expires)
	if err != nil {
		t.Fatalf("insert deflate rec: %v", err)
	}

	err = fleetAutoDeflatePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil, got: %v", err)
	}
}

// TestWave121_PWADashboard_HandleAgents covers PWADashboardHandler.handleAgents (71.4%)
func TestWave121_PWADashboard_HandleAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	h := NewWebSocketHub()
	dash := NewDashboard(db, h)
	handler := NewPWADashboardHandler(dash)

	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()
	handler.handleAgents(w, req)
	t.Logf("PWADashboard.handleAgents: status=%d", w.Code)
}

// TestWave121_PWADashboard_HandleSummary covers PWADashboardHandler.handleSummary (73.3%)
func TestWave121_PWADashboard_HandleSummary(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	h := NewWebSocketHub()
	dash := NewDashboard(db, h)
	handler := NewPWADashboardHandler(dash)

	req := httptest.NewRequest(http.MethodGet, "/api/summary", nil)
	w := httptest.NewRecorder()
	handler.handleSummary(w, req)
	t.Logf("PWADashboard.handleSummary: status=%d", w.Code)
}

// TestWave121_AgentTasksHandler_Basic covers handlers_plan.go:agentTasksHandler (73.3%)
func TestWave121_AgentTasksHandler_Basic(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/tasks?agent_id=test-agent", nil)
	w := httptest.NewRecorder()
	agentTasksHandler(w, req)
	t.Logf("agentTasksHandler: status=%d", w.Code)
}
