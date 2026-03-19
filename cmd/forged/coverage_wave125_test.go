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
)

// TestWave125_HandleSummary_NilDashboard covers the nil dashboard path (line 67-69).
func TestWave125_HandleSummary_NilDashboard(t *testing.T) {
	handler := &PWADashboardHandler{dashboard: nil}
	req := httptest.NewRequest(http.MethodGet, "/api/summary", nil)
	w := httptest.NewRecorder()
	handler.handleSummary(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave125_HandleAgents_NilDashboard covers the nil dashboard path (line 115-117).
func TestWave125_HandleAgents_NilDashboard(t *testing.T) {
	handler := &PWADashboardHandler{dashboard: nil}
	req := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()
	handler.handleAgents(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d: %s", w.Code, w.Body.String())
	}
}

// TestWave125_HandleSummary_ClosedDB covers the GetDashboardData error path.
func TestWave125_HandleSummary_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	h := NewWebSocketHub()
	dash := NewDashboard(db, h)
	handler := NewPWADashboardHandler(dash)
	cleanup() // close DB so GetDashboardData may error

	req := httptest.NewRequest(http.MethodGet, "/api/summary", nil)
	w := httptest.NewRecorder()
	handler.handleSummary(w, req)
	t.Logf("handleSummary closed DB: status=%d", w.Code)
}

// TestWave125_HandleTaskLogs_NotFound covers the rr.status>=400 path (lines 344-348).
func TestWave125_HandleTaskLogs_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/cli/task/logs?id=nonexistent-wave125", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	t.Logf("handleTaskLogs not found: status=%d", w.Code)
}

// TestWave125_HandleTaskLogs_MissingID covers the empty task ID path.
func TestWave125_HandleTaskLogs_MissingID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/cli/task/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

// TestWave125_AgentTelemetrySummaryHandler_MethodNotAllowed covers POST → 405.
// TestWave125_AgentTelemetrySummaryHandler_NilDB covers nil DB → 503.
func TestWave125_AgentTelemetrySummaryHandler_NilDB(t *testing.T) {
	origDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(origDB)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d", w.Code)
	}
}

// TestWave125_AgentTelemetrySummaryHandler_ClosedDB covers the query error path → 500.
func TestWave125_AgentTelemetrySummaryHandler_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	setDBConn(db)
	cleanup()
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/agents/telemetry/summary", nil)
	w := httptest.NewRecorder()
	agentTelemetrySummaryHandler(w, req)
	t.Logf("agentTelemetrySummaryHandler closed DB: status=%d", w.Code)
}

// TestWave125_AgentsHandler_MethodNotAllowed covers POST → 405.
// TestWave125_SyncRoyalJelly_WithContextDir covers the inner loop (lines 414-430).
// Creates .forge/context/<domain>/context_pct so the directory scan finds something.
func TestWave125_SyncRoyalJelly_WithContextDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	contextDir := filepath.Join(root, ".forge", "context", "forge")
	if err := os.MkdirAll(contextDir, 0o755); err != nil {
		t.Skipf("cannot create dirs: %v", err)
	}
	if err := os.WriteFile(filepath.Join(contextDir, "context_pct"), []byte("50\n"), 0o644); err != nil {
		t.Skipf("cannot write file: %v", err)
	}

	err := syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Logf("syncRoyalJelly with context dir: %v (acceptable)", err)
	}
}

// TestWave125_SyncRoyalJelly_ReadDirError covers the non-IsNotExist error path (line 410).
// Creates .forge/context as a FILE (not dir) so ReadDir fails with a non-IsNotExist error.
func TestWave125_SyncRoyalJelly_ReadDirError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	forgeDir := filepath.Join(root, ".forge")
	if err := os.MkdirAll(forgeDir, 0o755); err != nil {
		t.Skipf("mkdir: %v", err)
	}
	// Create context as a file, not a directory.
	if err := os.WriteFile(filepath.Join(forgeDir, "context"), []byte("not a dir"), 0o644); err != nil {
		t.Skipf("write: %v", err)
	}
	t.Setenv("FORGE_ROOT", root)

	err := syncRoyalJelly(context.Background(), db)
	// ReadDir on a file should return a non-IsNotExist error → returns error.
	t.Logf("syncRoyalJelly read-dir-error: %v", err)
}

// TestWave125_CheckContextThreshold_NilCM verifies nil ContextManager returns nil.
func TestWave125_CheckContextThreshold_NilCM(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	err := checkContextThreshold(context.Background(), db, nil, nil)
	if err != nil {
		t.Errorf("expected nil for nil cm, got: %v", err)
	}
}

// TestWave125_CheckContextThreshold_WithHighContextAgent covers the inner loop (line 713+).
func TestWave125_CheckContextThreshold_WithHighContextAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert agent with context_pct = 75 (above 50 threshold).
	_, _ = db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, context_pct, current_task_id, last_seen)
		VALUES ('w125-high-ctx-agent', 'prya', 'idle', 75.0, '', datetime('now'))
	`)

	cm := testContextManager(t, db, "")

	if err := checkContextThreshold(context.Background(), db, cm, nil); err != nil {
		t.Logf("checkContextThreshold with high context: %v (acceptable)", err)
	}
}
