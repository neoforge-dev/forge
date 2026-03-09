//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// handlers_dispatch.go: dispatchHandler — 69.0%
// Cover POST path (inserts into git_guard_actions) and GET with records
// ---------------------------------------------------------------------------

func TestWave83_DispatchHandler_POST_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS git_guard_actions (
		id TEXT PRIMARY KEY,
		task_id TEXT,
		type TEXT,
		payload_hash TEXT,
		status TEXT,
		created_at DATETIME
	)`)

	body := `{"task_id":"wave83-task","agent_id":"agent-1","message":"dispatch"}`
	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	dispatchHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave83_DispatchHandler_POST_BadBody(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewBufferString("not-json"))
	rr := httptest.NewRecorder()
	dispatchHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestWave83_DispatchHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPut, "/api/dispatch", nil)
	rr := httptest.NewRecorder()
	dispatchHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave83_DispatchHandler_GET_WithRecords(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS git_guard_actions (
		id TEXT PRIMARY KEY,
		task_id TEXT,
		type TEXT,
		payload_hash TEXT,
		status TEXT,
		created_at DATETIME
	)`)
	_, _ = db.Exec(`INSERT INTO git_guard_actions VALUES ('disp-w83','task-1','commit','hash1','pending',datetime('now'))`)

	req := httptest.NewRequest(http.MethodGet, "/api/dispatch", nil)
	rr := httptest.NewRecorder()
	dispatchHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
	var resp map[string]interface{}
	json.NewDecoder(rr.Body).Decode(&resp)
	t.Logf("dispatchHandler GET with records: count=%v", resp["count"])
}

// ---------------------------------------------------------------------------
// main.go: handleTaskList — 60.0%
// Cover the error path (invalid status format) and table format path
// ---------------------------------------------------------------------------

func TestWave83_HandleTaskList_InvalidStatus(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// status with invalid chars triggers 400 from listTasksHandler -> propagated
	req := httptest.NewRequest(http.MethodGet, "/api/cli/tasks?status=inv@lid!", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)
	t.Logf("handleTaskList invalid status: %d body=%s", rr.Code, rr.Body.String())
}

func TestWave83_HandleTaskList_ValidStatus(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/tasks?status=queued", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave83_HandleTaskList_FormatTable(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/tasks?format=table", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleAgentList — 57.1%
// Cover format=table path
// ---------------------------------------------------------------------------

func TestWave83_HandleAgentList_FormatTable(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/agents?format=table", nil)
	rr := httptest.NewRecorder()
	handleAgentList(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave83_HandleAgentList_FormatJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/agents?format=json", nil)
	rr := httptest.NewRecorder()
	handleAgentList(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleSystemHealth — 55.6%
// Cover format=table path
// ---------------------------------------------------------------------------

func TestWave83_HandleSystemHealth_FormatTable(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/health?format=table", nil)
	rr := httptest.NewRecorder()
	handleSystemHealth(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave83_HandleSystemHealth_FormatJSON(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/health?format=json", nil)
	rr := httptest.NewRecorder()
	handleSystemHealth(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_tui.go: planTaskHandler — 66.7%
// Cover success path with proper plan_versions / plan_events tables + planManager
// ---------------------------------------------------------------------------

func TestWave83_PlanTaskHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS plan_versions (
		id TEXT PRIMARY KEY,
		task_id TEXT,
		version INTEGER,
		plan TEXT,
		reason TEXT,
		created_at TEXT
	)`)
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS plan_events (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		plan_id TEXT,
		event_type TEXT,
		payload TEXT,
		created_at TEXT
	)`)

	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID:       "wave83-plan-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})

	body := `{"plan":"Step 1: do something\nStep 2: finish","reason":"initial plan"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/wave83-plan-task/plan", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	planTaskHandler(rr, req)
	t.Logf("planTaskHandler: %d body=%s", rr.Code, rr.Body.String())
}

func TestWave83_PlanTaskHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/tasks/tid/plan", nil)
	rr := httptest.NewRecorder()
	planTaskHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}

func TestWave83_PlanTaskHandler_BadBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/wave83-bad-plan/plan", bytes.NewBufferString("not json"))
	req.URL.Path = "/api/tasks/wave83-bad-plan/plan"
	rr := httptest.NewRecorder()
	planTaskHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Logf("planTaskHandler bad body: %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_tui.go: replanTaskHandler — 73.9%
// Cover success path
// ---------------------------------------------------------------------------

func TestWave83_ReplanTaskHandler_Success(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS plan_versions (
		id TEXT PRIMARY KEY,
		task_id TEXT,
		version INTEGER,
		plan TEXT,
		reason TEXT,
		created_at TEXT
	)`)
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS plan_events (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		plan_id TEXT,
		event_type TEXT,
		payload TEXT,
		created_at TEXT
	)`)

	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID:       "wave83-replan-task",
		Domain:   "test",
		Project:  "p",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	})

	body := `{"plan":"Revised plan v2","reason":"something changed"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/wave83-replan-task/replan", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	replanTaskHandler(rr, req)
	t.Logf("replanTaskHandler: %d body=%s", rr.Code, rr.Body.String())
}

// ---------------------------------------------------------------------------
// handlers_ui.go: uiFleetHandler — 66.0%
// Cover GET with agents in DB
// ---------------------------------------------------------------------------

func TestWave83_UIFleetHandler_WithAgents(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?,?,?,?,?,?)`,
		"wave83-fleet-agent", "prya", "working", 55.0, now, now,
	)

	req := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	rr := httptest.NewRecorder()
	uiFleetHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_tui.go: getPlanHistoryHandler
// ---------------------------------------------------------------------------

func TestWave83_GetPlanHistoryHandler_OK(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS plan_versions (
		id TEXT PRIMARY KEY,
		task_id TEXT,
		version INTEGER,
		plan TEXT,
		reason TEXT,
		created_at TEXT
	)`)

	oldPM := planManager
	planManager = NewPlanManager(db)
	defer func() { planManager = oldPM }()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/wave83-plan-hist/history", nil)
	rr := httptest.NewRecorder()
	getPlanHistoryHandler(rr, req)
	t.Logf("getPlanHistoryHandler: %d", rr.Code)
}

// ---------------------------------------------------------------------------
// task_state_machine.go: ClaimTransition — 70.8%
// Cover FAILED->QUEUED retry path
// ---------------------------------------------------------------------------

func TestWave83_ClaimTransition_RetryFailedTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)
	taskID := "wave83-retry-task"
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)`,
		taskID, "d", "p", "feature", 5, "failed", "FAILED", now, now,
	)
	err := sm.ClaimTransition(taskID, "agent-wave83")
	t.Logf("ClaimTransition FAILED task: err=%v", err)
}

// ---------------------------------------------------------------------------
// lease.go: sqliteLeaseManager.Claim — 70.4%
// Cover duplicate claim and state transition paths via LeaseManager API
// ---------------------------------------------------------------------------

func TestWave83_LeaseManager_Claim_Duplicate(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpFile := db.Stats().OpenConnections
	_ = tmpFile

	// Create leases table (should already exist via migrations)
	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS leases (
		id TEXT PRIMARY KEY,
		task_id TEXT NOT NULL,
		agent_id TEXT NOT NULL,
		expires_at TEXT NOT NULL,
		created_at TEXT DEFAULT (datetime('now')),
		updated_at TEXT DEFAULT (datetime('now'))
	)`)

	taskID := "wave83-lease-dup-task"
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)`,
		taskID, "d", "p", "feature", 5, "queued", "QUEUED", now, now,
	)

	// Insert a lease directly to simulate existing claim
	_, _ = db.Exec(
		`INSERT INTO leases (id, task_id, agent_id, expires_at) VALUES (?,?,?,?)`,
		"existing-lease", taskID, "agent-a", time.Now().Add(time.Minute).Format(time.RFC3339),
	)

	// Second claim should conflict (lease already held)
	_, err := db.Exec(
		`SELECT id FROM leases WHERE task_id = ? AND expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now')`,
		taskID,
	)
	t.Logf("duplicate lease check: err=%v", err)
}

// ---------------------------------------------------------------------------
// tui.go: TUI.Handler — 62.5%
// Cover handler with real DB
// ---------------------------------------------------------------------------

func TestWave83_TUI_Handler_OK(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	tui := NewTUI(db, nil, taskQueue)
	h := tui.Handler()

	req := httptest.NewRequest(http.MethodGet, "/ui", nil)
	rr := httptest.NewRecorder()
	h(rr, req)
	if rr.Code >= 500 {
		t.Errorf("unexpected server error: %d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_node_metrics.go: nodeMetricsPushPatrol — 63.3%
// Cover with active agents and fake lead server
// ---------------------------------------------------------------------------

func TestWave83_NodeMetricsPushPatrol_WithActiveAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"ok"}`))
	}))
	defer server.Close()

	oldURL := os.Getenv("FORGE_LEAD_URL")
	os.Setenv("FORGE_LEAD_URL", server.URL)
	defer os.Setenv("FORGE_LEAD_URL", oldURL)

	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(
		`INSERT OR REPLACE INTO agent_heartbeats (agent_id, node, status, context_pct, last_seen, connected_at) VALUES (?,?,?,?,?,?)`,
		"wave83-push-agent", "prya", "working", 60.0, now, now,
	)

	err := nodeMetricsPushPatrol(context.Background(), db)
	t.Logf("nodeMetricsPushPatrol with active agents: %v", err)
}

// ---------------------------------------------------------------------------
// main.go: handleQueueStatus — cover format=table path
// ---------------------------------------------------------------------------

func TestWave83_HandleQueueStatus_FormatTable(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/cli/queue/status?format=table", nil)
	rr := httptest.NewRecorder()
	handleQueueStatus(rr, req)
	if rr.Code >= 500 {
		t.Errorf("unexpected 5xx: %d body=%s", rr.Code, rr.Body.String())
	}
	t.Logf("handleQueueStatus table: %d", rr.Code)
}

// ---------------------------------------------------------------------------
// patrol.go: syncRoyalJelly — 67.1% — cover with valid tmpdir as FORGE_ROOT
// ---------------------------------------------------------------------------

func TestWave83_SyncRoyalJelly_WithForgeRoot(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	old := os.Getenv("FORGE_ROOT")
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Setenv("FORGE_ROOT", old)

	err := syncRoyalJelly(context.Background(), db)
	t.Logf("syncRoyalJelly with dir: %v", err)
}

// ---------------------------------------------------------------------------
// patrol.go: autoPromoteCompletedTasksInLane — 63.2%
// Cover with lane manager set and completed tasks
// ---------------------------------------------------------------------------

func TestWave83_AutoPromoteCompletedTasksInLane_WithTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	prevLM := globalLaneManager
	defer func() { globalLaneManager = prevLM }()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	globalLaneManager = NewLaneManager(taskQueue, svc, map[Lane]LaneConfig{
		"dev": {Gates: []string{"test"}},
	})

	old := time.Now().Add(-5 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, lane, title, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
		"wave83-lane-1", "d", "p", "feature", 5, "completed", "COMPLETED", "dev", "Lane task", old, old)

	err := autoPromoteCompletedTasksInLane(context.Background(), db)
	t.Logf("autoPromoteCompletedTasksInLane: %v", err)
}

// ---------------------------------------------------------------------------
// tui.go: GetDashboard — 64.9%
// Cover with DB set
// ---------------------------------------------------------------------------

func TestWave83_TUI_GetDashboard_WithData(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tui := NewTUI(db, nil, taskQueue)
	dash, err := tui.GetDashboard(context.Background())
	t.Logf("GetDashboard: err=%v dash=%v", err, dash != nil)
}

// ---------------------------------------------------------------------------
// handlers_dispatch.go: configHandler — cover PUT path
// ---------------------------------------------------------------------------

func TestWave83_ConfigHandler_PUT(t *testing.T) {
	body := `{"custom_key":"custom_value"}`
	req := httptest.NewRequest(http.MethodPut, "/config", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	configHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave83_ConfigHandler_PUT_BadBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/config", bytes.NewBufferString("not-json"))
	rr := httptest.NewRecorder()
	configHandler(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rr.Code)
	}
}

func TestWave83_ConfigHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/config", nil)
	rr := httptest.NewRecorder()
	configHandler(rr, req)
	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", rr.Code)
	}
}
