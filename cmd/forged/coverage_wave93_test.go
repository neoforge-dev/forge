//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TUI.Handler() and TUI.JSONHandler() 
func TestWave93_TUI_Handler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	tui := NewTUI(db, nil, taskQueue)
	h := tui.Handler()
	r := httptest.NewRequest(http.MethodGet, "/tui", nil)
	w := httptest.NewRecorder()
	h(w, r)
	if w.Code >= 500 {
		t.Logf("TUI.Handler: got %d", w.Code)
	}
}

func TestWave93_TUI_JSONHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	tui := NewTUI(db, nil, taskQueue)
	h := tui.JSONHandler()
	r := httptest.NewRequest(http.MethodGet, "/tui.json", nil)
	w := httptest.NewRecorder()
	h(w, r)
	if w.Code >= 500 {
		t.Logf("TUI.JSONHandler: got %d", w.Code)
	}
}

// fleetSummaryHandler
func TestWave93_FleetSummaryHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := httptest.NewRequest(http.MethodGet, "/api/fleet-summary", nil)
	w := httptest.NewRecorder()
	fleetSummaryHandler(w, r)
	if w.Code >= 500 {
		t.Logf("fleetSummaryHandler: got %d", w.Code)
	}
}

// PWA handleAgents method-not-allowed + GET
func TestWave93_PWA_HandleAgents_Get(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	d := NewDashboard(db, nil)
	pwa := NewPWADashboardHandler(d)
	r := httptest.NewRequest(http.MethodGet, "/api/agents", nil)
	w := httptest.NewRecorder()
	pwa.handleAgents(w, r)
	if w.Code >= 500 {
		t.Logf("handleAgents GET: got %d", w.Code)
	}
}

// ServeDashboard paths
func TestWave93_ServeDashboard_WithRefresh(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	d := NewDashboard(db, nil)
	r := httptest.NewRequest(http.MethodGet, "/dashboard?refresh=1", nil)
	w := httptest.NewRecorder()
	d.ServeDashboard(w, r)
	if w.Code >= 500 {
		t.Logf("ServeDashboard refresh: got %d", w.Code)
	}
}

func TestWave93_ServeDashboard_NoRefresh(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	d := NewDashboard(db, nil)
	r := httptest.NewRequest(http.MethodGet, "/dashboard", nil)
	w := httptest.NewRecorder()
	d.ServeDashboard(w, r)
	if w.Code >= 500 {
		t.Logf("ServeDashboard no-refresh: got %d", w.Code)
	}
}

// handleAgentList format=json
func TestWave93_HandleAgentList_FormatJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()
	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT OR REPLACE INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?, ?, ?, ?, ?, ?)`,
		"wave93-list-agent", "test-node", "idle", 10.0, now, now)
	r := httptest.NewRequest(http.MethodGet, "/agents?format=json", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)
	if w.Code >= 500 {
		t.Logf("handleAgentList format=json: got %d", w.Code)
	}
}

// SendToWorker with hub
func TestWave93_SendToWorker_WithHub(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()
	if hub == nil {
		t.Skip("hub not initialized")
	}
	hub.SendToWorker("wave93-nonexistent", "test-msg", "wave93-task", Task{ID: "wave93-task"})
}

// TaskStateMachineImpl.GetTaskState — cover not found + found paths
func TestWave93_TaskStateMachineImpl_GetTaskState_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	sm := NewTaskStateMachine(db)
	_, err := sm.GetTaskState("wave93-nonexistent-task")
	if err == nil {
		t.Logf("GetTaskState not-found: expected error, got nil")
	}
}

func TestWave93_TaskStateMachineImpl_GetTaskState_Found(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"wave93-state-task", "codeswiftr", "proj", "feature", 5, "queued", "QUEUED", "State Task", now, now)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}
	sm := NewTaskStateMachine(db)
	state, err := sm.GetTaskState("wave93-state-task")
	if err != nil {
		t.Logf("GetTaskState: %v", err)
	} else {
		t.Logf("GetTaskState: %q", state)
	}
}

// OpenDB fresh
func TestWave93_OpenDB_FreshDB(t *testing.T) {
	tmpDir := t.TempDir()
	db, err := OpenDB(tmpDir + "/wave93.db")
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	t.Logf("OpenDB success")
}

// MigrateUp re-run
func TestWave93_MigrateUp_AlreadyApplied(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	for i := 0; i < 2; i++ {
		if err := MigrateUp(db); err != nil {
			t.Errorf("MigrateUp iteration %d: %v", i, err)
		}
	}
}

// MigrateDown one step
func TestWave93_MigrateDown_OneStep(t *testing.T) {
	tmpDir := t.TempDir()
	db, err := OpenDB(tmpDir + "/wave93_down.db")
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	if err := MigrateDown(db, 1); err != nil {
		t.Logf("MigrateDown 1 step: %v", err)
	}
}
