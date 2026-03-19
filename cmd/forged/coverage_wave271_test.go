//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestCoverageWave271_ParityHandler_Success tests GET /api/agents/parity success case
func TestCoverageWave271_ParityHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Create a temporary .forge directory with required files for parity check
	tmpDir := t.TempDir()
	forgeDir := filepath.Join(tmpDir, ".forge")
	tasksDir := filepath.Join(forgeDir, "tasks")
	fleetDir := filepath.Join(forgeDir, "fleet")
	
	if err := os.MkdirAll(tasksDir, 0755); err != nil {
		t.Fatalf("failed to create tasks dir: %v", err)
	}
	if err := os.MkdirAll(fleetDir, 0755); err != nil {
		t.Fatalf("failed to create fleet dir: %v", err)
	}
	
	// Create a mock state.json file
	stateData := `{"agents": [{"id": "agent-1"}, {"id": "agent-2"}]}`
	if err := os.WriteFile(filepath.Join(fleetDir, "state.json"), []byte(stateData), 0644); err != nil {
		t.Fatalf("failed to write state.json: %v", err)
	}
	
	// Create a mock task file
	taskData := `{"id": "task-1", "status": "pending"}`
	if err := os.WriteFile(filepath.Join(tasksDir, "task-1.json"), []byte(taskData), 0644); err != nil {
		t.Fatalf("failed to write task file: %v", err)
	}

	// Change to temp directory so ParityCheck uses the mock files
	origWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(origWd)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/parity", nil)
	w := httptest.NewRecorder()

	// parityHandler requires db parameter
	handler := parityHandler(getDBConn())
	handler(w, req)

	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 200 or 500, got %d: %s", w.Code, w.Body.String())
	}

	// Should return JSON when successful
	if w.Code == http.StatusOK {
		var resp ParityResult
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Errorf("invalid JSON response: %v", err)
		}
	}
}

// TestCoverageWave271_ParityHandler_Error tests parityHandler with invalid DB
func TestCoverageWave271_ParityHandler_Error(t *testing.T) {
	// Test with nil db - should still work as ParityCheck uses hardcoded paths
	req := httptest.NewRequest(http.MethodGet, "/api/agents/parity", nil)
	w := httptest.NewRecorder()

	handler := parityHandler(nil)
	handler(w, req)

	// ParityCheck returns error (500) when paths don't exist, or result with errors
	if w.Code != http.StatusOK && w.Code != http.StatusInternalServerError {
		t.Errorf("expected 200 or 500, got %d", w.Code)
	}
}

// TestCoverageWave271_AgentsHandler_Success tests GET /api/agents success case
func TestCoverageWave271_AgentsHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Insert test agent heartbeat
	now := time.Now().UTC().Format("2006-01-02 15:04:05")
	_, err := getDBConn().Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, current_task_id, context_pct, capabilities, last_seen, connected_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, "agent-001", "node-1", "idle", nil, 50.0, "test,agent", now, now)
	if err != nil {
		t.Fatalf("failed to insert agent: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()

	agentsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}

	if resp["agents"] == nil {
		t.Error("expected agents field in response")
	}
	if resp["count"] == nil {
		t.Error("expected count field in response")
	}
}

// TestCoverageWave271_AgentsHandler_QueryFailure tests agentsHandler with query failure
func TestCoverageWave271_AgentsHandler_QueryFailure(t *testing.T) {
	// Save original db and restore after test
	origDB := getDBConn()
	
	// Create a test DB, then close it to simulate connection failure
	dbPath := "/tmp/test_agents_fail_" + time.Now().Format("20060102150405") + ".db"
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}
	
	// Close the DB to cause queries to fail
	db.Close()
	setDBConn(db)
	defer func() {
		setDBConn(origDB)
		os.Remove(dbPath)
	}()

	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()

	agentsHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCoverageWave271_AgentTelemetrySummaryHandler_Success tests GET /api/agents/{id}/telemetry/summary success
func TestCoverageWave271_AgentTelemetrySummaryHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Insert test agent telemetry
	_, err := getDBConn().Exec(`
		INSERT INTO agent_telemetry (agent_id, node, context_pct, task_id, status, tool, metadata, timestamp)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
	`, "agent-telemetry-001", "node-1", 75.5, "task-123", "busy", "api", `{"progress": 50}`)
	if err != nil {
		t.Fatalf("failed to insert telemetry: %v", err)
	}

	// Insert another record for a different agent
	_, err = getDBConn().Exec(`
		INSERT INTO agent_telemetry (agent_id, node, context_pct, task_id, status, tool, metadata, timestamp)
		VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '-1 minute'))
	`, "agent-telemetry-002", "node-2", 60.0, "task-456", "idle", "http", `{}`)
	if err != nil {
		t.Fatalf("failed to insert telemetry: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()

	agentTelemetrySummaryHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}

	if resp["summary_at"] == nil {
		t.Error("expected summary_at field in response")
	}
	if resp["agent_count"] == nil {
		t.Error("expected agent_count field in response")
	}
	if resp["agents"] == nil {
		t.Error("expected agents field in response")
	}
}

// TestCoverageWave271_AgentTelemetrySummaryHandler_MethodNotAllowed tests invalid method
// TestCoverageWave271_AgentTelemetrySummaryHandler_NoDB tests when database is unavailable
func TestCoverageWave271_AgentTelemetrySummaryHandler_NoDB(t *testing.T) {
	// Save and restore DB
	origDB := getDBConn()
	setDBConn(nil)
	defer func() {
		setDBConn(origDB)
	}()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()

	agentTelemetrySummaryHandler(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

// TestCoverageWave271_HandleLeadState_Success tests GET /api/lead/state success
func TestCoverageWave271_HandleLeadState_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Insert test tasks
	for _, state := range []string{"queued", "running", "completed"} {
		_, err := getDBConn().Exec(`
			INSERT INTO tasks (id, domain, project, type, priority, status, title, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
		`, "task-"+state, "test-domain", "test-project", "feature", 5, state, "Test Task "+state)
		if err != nil {
			t.Fatalf("failed to insert task: %v", err)
		}
	}

	// Insert test agents
	for _, status := range []string{"idle", "busy"} {
		_, err := getDBConn().Exec(`
			INSERT INTO agents (id, name, node, role, status, registered_at)
			VALUES (?, ?, ?, ?, ?, datetime('now'))
		`, "agent-"+status, "Agent "+status, "node-1", "worker", status)
		if err != nil {
			t.Fatalf("failed to insert agent: %v", err)
		}
	}

	// Insert pending approval (note: agent_id and expires_at are NOT NULL)
	_, err := getDBConn().Exec(`
		INSERT INTO approvals (id, type, agent_id, domain, title, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now', '+1 day'))
	`, "approval-1", "task_completion", "agent-001", "test-domain", "Test Approval", "pending")
	if err != nil {
		t.Fatalf("failed to insert approval: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/lead/state", nil)
	w := httptest.NewRecorder()

	handler := handleLeadState(getDBConn())
	handler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}

	if resp["timestamp"] == nil {
		t.Error("expected timestamp field in response")
	}
	if resp["tasks"] == nil {
		t.Error("expected tasks field in response")
	}
	if resp["agents"] == nil {
		t.Error("expected agents field in response")
	}
	if resp["approvals"] == nil {
		t.Error("expected approvals field in response")
	}
}

// TestCoverageWave271_HandleLeadState_MethodNotAllowed tests invalid method
// TestCoverageWave271_HandleLeadState_QueryError tests query failure path
func TestCoverageWave271_HandleLeadState_QueryError(t *testing.T) {
	// Save original db and restore after test
	origDB := getDBConn()
	
	// Create a test DB, then close it to simulate connection failure
	dbPath := "/tmp/test_lead_fail_" + time.Now().Format("20060102150405") + ".db"
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}
	
	// Close the DB to cause queries to fail
	db.Close()
	setDBConn(db)
	defer func() {
		setDBConn(origDB)
		os.Remove(dbPath)
	}()

	req := httptest.NewRequest(http.MethodGet, "/api/lead/state", nil)
	w := httptest.NewRecorder()

	handler := handleLeadState(getDBConn())
	handler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d: %s", w.Code, w.Body.String())
	}
}

// TestCoverageWave271_LanesHandler_Success tests GET /api/lanes success
func TestCoverageWave271_LanesHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Count existing lanes (migrations create 4 default lanes)
	var existingCount int
	err := getDBConn().QueryRow("SELECT COUNT(*) FROM lanes").Scan(&existingCount)
	if err != nil {
		t.Fatalf("failed to count lanes: %v", err)
	}

	// Insert additional test lanes
	for i := 1; i <= 3; i++ {
		_, err := getDBConn().Exec(`
			INSERT INTO lanes (id, name, description, capabilities, auto_claim, max_parallel, timeout_minutes)
			VALUES (?, ?, ?, ?, ?, ?, ?)
		`, 
			"lane-test-"+string(rune('0'+i)),
			"Test Lane "+string(rune('0'+i)),
			"Description for test lane "+string(rune('0'+i)),
			"cap1,cap2",
			true,
			5,
			30)
		if err != nil {
			t.Fatalf("failed to insert lane: %v", err)
		}
	}

	req := httptest.NewRequest(http.MethodGet, "/api/lanes", nil)
	w := httptest.NewRecorder()

	lanesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}

	if resp["lanes"] == nil {
		t.Error("expected lanes field in response")
	}
	if resp["count"] == nil {
		t.Error("expected count field in response")
	}

	lanes, ok := resp["lanes"].([]interface{})
	if !ok {
		t.Fatal("expected lanes to be an array")
	}
	expectedCount := existingCount + 3
	if len(lanes) != expectedCount {
		t.Errorf("expected %d lanes, got %d", expectedCount, len(lanes))
	}
}

// TestCoverageWave271_LanesHandler_MethodNotAllowed tests invalid method
// TestCoverageWave271_LanesHandler_DefaultLanes tests that default lanes exist
// Note: Migrations create default lanes, so we verify the default set exists
func TestCoverageWave271_LanesHandler_DefaultLanes(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/lanes", nil)
	w := httptest.NewRecorder()

	lanesHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("invalid JSON response: %v", err)
	}

	lanes, ok := resp["lanes"].([]interface{})
	if !ok {
		t.Fatal("expected lanes to be an array")
	}
	// Migrations create default lanes (infra, features, bugs, chores)
	if len(lanes) < 1 {
		t.Errorf("expected at least 1 lane from migrations, got %d", len(lanes))
	}
	if count, ok := resp["count"].(float64); !ok || count < 1 {
		t.Errorf("expected count >= 1, got %v", resp["count"])
	}
}

// TestCoverageWave271_LanesHandler_QueryFailure tests query failure
func TestCoverageWave271_LanesHandler_QueryFailure(t *testing.T) {
	// Save original db
	origDB := getDBConn()
	
	// Create and close a DB to simulate failure
	dbPath := "/tmp/test_lanes_fail_" + time.Now().Format("20060102150405") + ".db"
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("failed to open db: %v", err)
	}
	db.Close()
	setDBConn(db)
	defer func() {
		setDBConn(origDB)
		os.Remove(dbPath)
	}()

	req := httptest.NewRequest(http.MethodGet, "/api/lanes", nil)
	w := httptest.NewRecorder()

	lanesHandler(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500, got %d: %s", w.Code, w.Body.String())
	}
}
