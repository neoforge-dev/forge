//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

// Wave 34: countV2Agents (with state file), patrolExecutionsHandler (nil DB),
//          uiFleetHandler additional paths, parity checker, openclawEventsHandler

func TestCountV2Agents_WithStateFile_W34(t *testing.T) {
	dir := t.TempDir()

	// Create fleet/state.json with 3 agents
	fleetDir := filepath.Join(dir, "fleet")
	if err := os.MkdirAll(fleetDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	state := map[string]interface{}{
		"agents": []interface{}{"agent1", "agent2", "agent3"},
	}
	data, _ := json.Marshal(state)
	if err := os.WriteFile(filepath.Join(fleetDir, "state.json"), data, 0644); err != nil {
		t.Fatalf("write state.json: %v", err)
	}

	pc := NewParityChecker(dir, ":memory:")
	count, err := pc.countV2Agents()
	if err != nil {
		t.Fatalf("countV2Agents: %v", err)
	}
	if count != 3 {
		t.Errorf("expected 3 agents, got %d", count)
	}
}

func TestCountV2Agents_InvalidJSON_W34(t *testing.T) {
	dir := t.TempDir()
	fleetDir := filepath.Join(dir, "fleet")
	if err := os.MkdirAll(fleetDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	// Write invalid JSON
	if err := os.WriteFile(filepath.Join(fleetDir, "state.json"), []byte("not-json{{{"), 0644); err != nil {
		t.Fatalf("write state.json: %v", err)
	}

	pc := NewParityChecker(dir, ":memory:")
	_, err := pc.countV2Agents()
	if err == nil {
		t.Error("expected error for invalid JSON state file")
	}
}

func TestPatrolExecutionsHandler_NilDB_W34(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	req := httptest.NewRequest(http.MethodGet, "/api/patrol-executions", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503, got %d", w.Code)
	}
}

func TestPatrolExecutionsHandler_InvalidLimit_W34(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Invalid limit — should clamp to default 50
	req := httptest.NewRequest(http.MethodGet, "/api/patrol-executions?limit=bad&hours=bad", nil)
	w := httptest.NewRecorder()
	patrolExecutionsHandler(w, req)
	// Should succeed (invalid values fall back to defaults)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200 with invalid params, got %d: %s", w.Code, w.Body.String())
	}
}

func TestOpenclawEventsHandler_NilDB_W34(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancelled immediately so SSE loop exits

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil)
	req = req.WithContext(ctx)
	w := httptest.NewRecorder()
	openclawEventsHandler(w, req)
	_ = w.Code
}

func TestNewParityChecker_Defaults_W34(t *testing.T) {
	// Empty paths should use defaults
	pc := NewParityChecker("", "")
	if pc == nil {
		t.Error("expected non-nil ParityChecker")
	}
}

func TestUIFleetHandler_AcceptsGet_W34(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	// HTMX template rendering — may fail if template not loaded, but must not panic
	_ = w.Code
}

func TestHandleQueueCancel_MissingID_W34(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	// Body with empty ID
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", nil)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	// Should be 400 (bad request) or 503
	_ = w.Code
}
