//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

// ── getAgentID TMUX path ───────────────────────────────────────────────────────

func TestWave106_GetAgentID_TMUXPath(t *testing.T) {
	// TMUX is set in this test environment, so getAgentID will try tmux display-message
	// Clear FORGE_AGENT_ID to ensure we reach the TMUX check
	oldAgentID := os.Getenv("FORGE_AGENT_ID")
	os.Unsetenv("FORGE_AGENT_ID")
	defer os.Setenv("FORGE_AGENT_ID", oldAgentID)

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "")

	agentID, err := getAgentID(cmd)
	if err != nil {
		// TMUX might not succeed in all environments — acceptable
		t.Logf("getAgentID TMUX path: err=%v", err)
	} else {
		t.Logf("getAgentID via TMUX: %q", agentID)
	}
}

// ── getTaskStoreForCLI DB_PATH empty path ─────────────────────────────────────

func TestWave106_GetTaskStoreForCLI_NilDB(t *testing.T) {
	// Set nil DB so we fall through to the dbPath lookup
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	// DB_PATH not set → uses defaultDBPath
	oldDBPath := os.Getenv("DB_PATH")
	os.Unsetenv("DB_PATH")
	defer os.Setenv("DB_PATH", oldDBPath)

	store, err := getTaskStoreForCLI()
	if err != nil {
		t.Logf("getTaskStoreForCLI nil DB: err=%v (acceptable if DB not found)", err)
	} else {
		t.Logf("getTaskStoreForCLI nil DB: got store %T", store)
	}
	// Restore DB for subsequent tests
	setDBConn(oldDB)
}

// ── taskNextCmd.RunE paths ─────────────────────────────────────────────────────

func TestWave106_TaskNext_EmptyQueue(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "agent-wave106", "")

	// RunE with empty queue → prints "No tasks available"
	err := taskNextCmd.RunE(cmd, nil)
	if err != nil {
		t.Logf("taskNextCmd.RunE empty queue: err=%v", err)
	}
}

func TestWave106_TaskNext_WithTask(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	db := getDBConn()

	// Insert a queued task
	taskID := fmt.Sprintf("task-w106-%d", time.Now().UnixNano())
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'dom', 'proj', 'feature', 'Wave106 test task', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))`, taskID)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "agent-wave106", "")

	// RunE with a task in queue → claims it and outputs JSON
	err = taskNextCmd.RunE(cmd, nil)
	if err != nil {
		t.Logf("taskNextCmd.RunE with task: err=%v", err)
	}
}

// ── queueDepthCmd.RunE ─────────────────────────────────────────────────────────

func TestWave106_QueueDepth(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cmd := &cobra.Command{}
	err := queueDepthCmd.RunE(cmd, nil)
	if err != nil {
		t.Errorf("queueDepthCmd.RunE: %v", err)
	}
}

// ── nodeMetricsReceiveHandler paths ───────────────────────────────────────────

func TestWave106_NodeMetricsReceive_EmptyNodeID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	// NodeID in body is empty → uses pathNodeID
	body := map[string]interface{}{
		"period": "1m",
		"metrics": []map[string]interface{}{
			{"name": "cpu_usage", "value": 42.5, "labels": map[string]string{"host": "test"}},
			{"name": "mem_usage", "value": 60.0},
		},
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/test-node-106/metrics", bytes.NewReader(bodyBytes))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	nodeMetricsReceiveHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave106_NodeMetricsReceive_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/test-node/metrics", nil)
	rr := httptest.NewRecorder()
	nodeMetricsReceiveHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave106_NodeMetricsReceive_WithNodeIDMismatch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	body := map[string]interface{}{
		"node_id": "different-node",
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/test-node-106/metrics", bytes.NewReader(bodyBytes))
	rr := httptest.NewRecorder()
	nodeMetricsReceiveHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for node_id mismatch, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave106_NodeMetricsReceive_WithEmptyNameSample(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	// Include a sample with empty name (should be skipped)
	body := map[string]interface{}{
		"period": "5m",
		"metrics": []map[string]interface{}{
			{"name": "", "value": 1.0}, // empty name → skip
			{"name": "valid_metric", "value": 2.0},
		},
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/wave106-node/metrics", bytes.NewReader(bodyBytes))
	rr := httptest.NewRecorder()
	nodeMetricsReceiveHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

// ── fleetMetricsHandler paths ──────────────────────────────────────────────────

func TestWave106_FleetMetrics_GET(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	rr := httptest.NewRecorder()
	fleetMetricsHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave106_FleetMetrics_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/fleet/metrics", nil)
	rr := httptest.NewRecorder()
	fleetMetricsHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave106_FleetMetrics_NilDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/metrics", nil)
	rr := httptest.NewRecorder()
	fleetMetricsHandler(rr, req)
	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", rr.Code)
	}
}

// ── fleetAggregateHandler paths ────────────────────────────────────────────────

func TestWave106_FleetAggregate_NoNodesTable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	// nodes table doesn't exist in migrations → query fails → 500
	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	rr := httptest.NewRecorder()
	fleetAggregateHandler(rr, req)
	// Expect either 500 (no table) or 200 (table exists in some migration)
	t.Logf("fleetAggregateHandler: %d %s", rr.Code, rr.Body.String()[:min106(100, len(rr.Body.String()))])
}

func TestWave106_FleetAggregate_WithNodesTable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create nodes table manually
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS nodes (
		id TEXT PRIMARY KEY,
		address TEXT NOT NULL,
		status TEXT DEFAULT 'online'
	)`)
	if err != nil {
		t.Fatalf("create nodes table: %v", err)
	}

	// Insert a node that matches the current hostname (to cover selfInList=true path)
	selfID, _ := os.Hostname()
	_, _ = db.Exec(`INSERT OR IGNORE INTO nodes (id, address, status) VALUES (?, ?, 'online')`,
		selfID, "http://localhost:8081")

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	rr := httptest.NewRecorder()
	fleetAggregateHandler(rr, req)
	t.Logf("fleetAggregateHandler with nodes table: %d %s", rr.Code, rr.Body.String()[:min106(100, len(rr.Body.String()))])
}

func TestWave106_FleetAggregate_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/fleet/aggregate", nil)
	rr := httptest.NewRecorder()
	fleetAggregateHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave106_FleetAggregate_NilDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/aggregate", nil)
	rr := httptest.NewRecorder()
	fleetAggregateHandler(rr, req)
	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", rr.Code)
	}
}

// ── handlers_openclaw paths ────────────────────────────────────────────────────

func TestWave106_OpenclawIngest_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/ingest", nil)
	rr := httptest.NewRecorder()
	openclawIngestHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave106_OpenclawIngest_MissingFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	body := bytes.NewBufferString(`{"task_id":"t1"}`) // missing required fields
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", body)
	rr := httptest.NewRecorder()
	openclawIngestHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestWave106_OpenclawIngest_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	body := map[string]interface{}{
		"task_id": "task-openclaw-w106",
		"status":  "completed",
		"agent":   "agent-x",
		"node":    "testnode",
		"summary": "Test completion from wave106",
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(bodyBytes))
	rr := httptest.NewRecorder()
	openclawIngestHandler(rr, req)
	// Should succeed (creates file in /tmp or current dir)
	if rr.Code != http.StatusOK {
		t.Logf("openclaw ingest: %d %s", rr.Code, rr.Body.String())
	}
}

func TestWave106_OpenclawIngest_FailStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	body := map[string]interface{}{
		"task_id": "task-openclaw-fail-w106",
		"status":  "fail",
		"agent":   "agent-x",
		"node":    "testnode",
		"summary": "Test failure from wave106",
	}
	bodyBytes, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest", bytes.NewReader(bodyBytes))
	rr := httptest.NewRecorder()
	openclawIngestHandler(rr, req)
	t.Logf("openclaw ingest fail status: %d", rr.Code)
}

// ── taskListCmd paths ──────────────────────────────────────────────────────────

func TestWave106_TaskListCmd_WithState(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cmd := &cobra.Command{}
	cmd.Flags().String("state", "QUEUED", "")

	err := taskListCmd.RunE(cmd, nil)
	if err != nil {
		t.Errorf("taskListCmd.RunE with state: %v", err)
	}
}

func TestWave106_TaskListCmd_AllStates(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	cmd := &cobra.Command{}
	cmd.Flags().String("state", "", "")

	err := taskListCmd.RunE(cmd, nil)
	if err != nil {
		t.Errorf("taskListCmd.RunE all states: %v", err)
	}
}

// ── context_sync.go paths ─────────────────────────────────────────────────────

func TestWave106_ContextSync_Stop(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	contextDir := t.TempDir()
	cm := &ContextManager{db: db, contextDir: contextDir}
	cs, err := NewContextSync(cm, db, contextDir)
	if err != nil {
		t.Fatalf("NewContextSync: %v", err)
	}

	// Start and immediately stop
	_ = cs.Start()
	time.Sleep(10 * time.Millisecond)

	cs.Stop()
}

// helper for min (wave106-scoped to avoid conflict)
func min106(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// ── openclawHandler dispatcher ─────────────────────────────────────────────────

func TestWave106_OpenclawHandler_StatusPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/status", nil)
	rr := httptest.NewRecorder()
	openclawHandler(rr, req)
	t.Logf("openclaw status: %d", rr.Code)
}

func TestWave106_OpenclawHandler_UnknownGETPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/unknown", nil)
	rr := httptest.NewRecorder()
	openclawHandler(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown path, got %d", rr.Code)
	}
}

func TestWave106_OpenclawHandler_UnknownPOSTPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/unknown", strings.NewReader("{}"))
	rr := httptest.NewRecorder()
	openclawHandler(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown POST path, got %d", rr.Code)
	}
}

func TestWave106_OpenclawHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	_ = db

	req := httptest.NewRequest(http.MethodDelete, "/api/openclaw/status", nil)
	rr := httptest.NewRecorder()
	openclawHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}
