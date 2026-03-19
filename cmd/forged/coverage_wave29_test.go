//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Wave 29: SetActiveConnections, uiFleetHandler, StatusHandler, getAppliedMigrations,
//          handleQueueCancel, countV2Agents (previously 50-54%)

func TestSetActiveConnections_W29(t *testing.T) {
	// Just exercises the Prometheus gauge Set — should not panic
	metrics.SetActiveConnections(42)
	metrics.SetActiveConnections(0)
}

func TestUIFleetHandler_Get_W29(t *testing.T) {
	// With nil DB this will render partially but should not panic
	req := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	// Any non-5xx response (200 partial HTML is fine)
	if w.Code >= 500 {
		t.Errorf("expected non-5xx, got %d: %s", w.Code, w.Body.String())
	}
}

func TestXNodeStatusHandler_EmptyDB_W29(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	xc, err := NewXNodeController(db, "test-node")
	if err != nil {
		t.Fatalf("NewXNodeController: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestGetAppliedMigrations_W29(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// setupClaimTestDB runs all migrations, so applied_migrations should have entries
	applied, err := getAppliedMigrations(db)
	if err != nil {
		t.Fatalf("getAppliedMigrations: %v", err)
	}
	if len(applied) == 0 {
		t.Error("expected at least one applied migration")
	}
}

func TestHandleQueueCancel_NilQueue_W29(t *testing.T) {
	orig := taskQueue
	taskQueue = nil
	defer func() { taskQueue = orig }()

	body := strings.NewReader(`{"id":"TASK-X","reason":"test"}`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", body)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 with nil queue, got %d", w.Code)
	}
}

func TestHandleQueueCancel_InvalidBody_W29(t *testing.T) {
	// taskQueue must be non-nil for this path
	if taskQueue == nil {
		t.Skip("taskQueue not initialized")
	}
	body := strings.NewReader(`not json`)
	req := httptest.NewRequest(http.MethodPost, "/api/queue/cancel", body)
	w := httptest.NewRecorder()
	handleQueueCancel(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid JSON, got %d", w.Code)
	}
}

func TestCountV2Agents_NoStateFile_W29(t *testing.T) {
	pc := &ParityChecker{
		v2StatePath: t.TempDir(), // empty dir — no fleet/state.json
	}
	count, err := pc.countV2Agents()
	if err != nil {
		t.Errorf("countV2Agents with missing file: %v", err)
	}
	if count != 0 {
		t.Errorf("expected 0 agents with no state file, got %d", count)
	}
}

func TestGetAppliedMigrations_ClosedDB_W29(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close immediately

	_, err := getAppliedMigrations(db)
	if err == nil {
		t.Error("expected error from closed DB")
	}
}

// Suppress unused import warning — context is used below
var _ = context.Background

// TestParityChecker_CountV2Agents_InvalidJSON covers the json.Unmarshal error
// path in countV2Agents (fleet/state.json has invalid JSON).
func TestParityChecker_CountV2Agents_InvalidJSON(t *testing.T) {
	root := t.TempDir()
	fleetDir := filepath.Join(root, "fleet")
	if err := os.MkdirAll(fleetDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(fleetDir, "state.json"), []byte("{invalid json"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}
	pc := NewParityChecker(root, filepath.Join(root, "forge-v3.db"))
	_, err := pc.countV2Agents()
	if err == nil {
		t.Error("expected error for invalid JSON in state.json")
	}
}

// TestParityChecker_Check_V3TasksError covers the countV3Tasks error append path in Check.
// A directory-as-DB-path causes SQLite to fail on first query.
func TestParityChecker_Check_V3TasksError(t *testing.T) {
	root := t.TempDir()
	dbPath := filepath.Join(root, "invalid-db-dir")
	if err := os.Mkdir(dbPath, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	pc := NewParityChecker(root, dbPath)
	result, err := pc.Check()
	if err != nil {
		t.Fatalf("Check returned unexpected error: %v", err)
	}
	// The v3 tasks/agents count should have failed, populating result.Errors.
	if len(result.Errors) == 0 {
		t.Error("expected at least one error in result.Errors for invalid v3 DB path")
	}
}
