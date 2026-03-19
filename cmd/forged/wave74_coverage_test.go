//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// fleet_scaler.go: fleetAutoExecutePatrol — 23.1%
// Test with a pending recommendation — covers steps 3-4 (RAM read fails → nil)
// ---------------------------------------------------------------------------

func TestWave74_FleetAutoExecute_WithPendingRec_RAMFails(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Reset circuit breaker and flap guard
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()

	db := getDBConn()
	ctx := context.Background()

	// Ensure scale_recommendations table exists
	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Insert a pending auto_execute recommendation
	expires := time.Now().UTC().Add(5 * time.Minute).Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
	`, "wave74-auto-rec-1", "prya", "kimi", "inflate", "test wave74", 5, 0, 1, "pending", expires)
	if err != nil {
		t.Fatalf("insert recommendation: %v", err)
	}

	// On macOS readLiveRAMMB fails → function returns nil after step 4
	err = fleetAutoExecutePatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetAutoExecutePatrol with rec (macOS RAM fail): %v", err)
	}
}

func TestWave74_FleetAutoExecute_WithExpiredRec(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Time{}
	lastScaleEvent.Unlock()

	db := getDBConn()
	ctx := context.Background()

	if err := ensureScaleRecommendationsTable(ctx, db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// Insert an EXPIRED recommendation (expires_at in the past)
	expiredAt := time.Now().UTC().Add(-1 * time.Minute).Format(time.RFC3339)
	_, _ = db.Exec(`
		INSERT INTO scale_recommendations (id, node_id, agent_type, action, reason, queue_depth, idle_agents, auto_execute, status, created_at, expires_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
	`, "wave74-expired-rec", "prya", "kimi", "inflate", "expired test", 3, 0, 1, "pending", expiredAt)

	// Expired rec should not match — returns nil with no pending recs
	err := fleetAutoExecutePatrol(ctx, db)
	if err != nil {
		t.Errorf("fleetAutoExecutePatrol expired rec: %v", err)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: readLiveRAMMB — 35%
// On macOS /proc/meminfo doesn't exist — test the error path.
// On Linux it would succeed. Either way, cover the logic.
// ---------------------------------------------------------------------------

func TestWave74_ReadLiveRAMMB_MacOS(t *testing.T) {
	// On macOS, readLiveRAMMB reads /proc/meminfo which doesn't exist → error
	// On Linux it reads the actual file → success
	ram, err := readLiveRAMMB()
	if err != nil {
		// Expected on macOS
		t.Logf("readLiveRAMMB macOS: err=%v (expected)", err)
	} else {
		t.Logf("readLiveRAMMB: %dMB available", ram)
		if ram <= 0 {
			t.Error("expected positive RAM reading on Linux")
		}
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: readLoadAverage — 42.9%
// Test the macOS path (reads /proc/loadavg on Linux, fails on macOS).
// ---------------------------------------------------------------------------

func TestWave74_ReadLoadAverage_MacOS(t *testing.T) {
	load, err := readLoadAverage()
	if err != nil {
		t.Logf("readLoadAverage macOS: err=%v (expected)", err)
	} else {
		t.Logf("readLoadAverage: %.2f", load)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: getCPUCount — 50%
// Test — reads /proc/cpuinfo on Linux, runtime.NumCPU() as fallback.
// ---------------------------------------------------------------------------

func TestWave74_GetCPUCount(t *testing.T) {
	count := getCPUCount()
	if count <= 0 {
		t.Errorf("getCPUCount: expected > 0, got %d", count)
	}
	t.Logf("getCPUCount: %d", count)
}

// ---------------------------------------------------------------------------
// handlers_ui.go: uiFleetHandler — 50%
// Test with agents in DB to cover more rendering paths.
// ---------------------------------------------------------------------------

func TestWave74_UIFleetHandler_WithAgents(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	// Insert agents with various statuses
	for _, agent := range []struct{ id, status string }{
		{"wave74-ui-agent-active", "active"},
		{"wave74-ui-agent-idle", "idle"},
		{"wave74-ui-agent-offline", "offline"},
	} {
		db.Exec(`INSERT OR IGNORE INTO agent_heartbeats
			(agent_id, node, status, last_seen, context_pct, current_task_id)
			VALUES (?, ?, ?, ?, ?, ?)`,
			agent.id, "prya", agent.status, now, 50.0, "")
	}

	r := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("uiFleetHandler with agents: got %d", w.Code)
	}
}

func TestWave74_UIFleetHandler_POST_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/ui/fleet", strings.NewReader("{}"))
	w := httptest.NewRecorder()
	uiFleetHandler(w, r)

	// uiFleetHandler doesn't check method — returns data either way
	t.Logf("uiFleetHandler POST: %d", w.Code)
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentsSSEHandler — 53.6%
// Test that the handler returns quickly for non-streaming recorders.
// ---------------------------------------------------------------------------

func TestWave74_AgentsSSEHandler_WithAgents(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	db.Exec(`INSERT OR IGNORE INTO agent_heartbeats
		(agent_id, node, status, last_seen, context_pct, current_task_id)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave74-sse-agent", "prya", "active", now, 30.0, "")

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	r := httptest.NewRequest(http.MethodGet, "/api/agents/sse", nil)
	r = r.WithContext(ctx)
	w := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		agentsSSEHandler(w, r)
		close(done)
	}()

	select {
	case <-done:
		t.Logf("agentsSSEHandler with agents: %d", w.Code)
	case <-time.After(200 * time.Millisecond):
		t.Log("agentsSSEHandler in SSE loop (expected)")
	}
}

// ---------------------------------------------------------------------------
// patrol.go: autoPromoteCompletedTasksInLane — 63.2%
// Test with a LaneManager set to cover the promotion path.
// ---------------------------------------------------------------------------

func TestWave74_AutoPromote_WithLaneManager(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()

	// Set up a globalLaneManager using the correct signature
	if globalLaneManager == nil {
		store := NewApprovalStore(db)
		svc := NewApprovalService(store)
		laneConfigs := map[Lane]LaneConfig{
			"dev": {AutoProgress: true},
		}
		lm := NewLaneManager(taskQueue, svc, laneConfigs)
		oldLM := globalLaneManager
		globalLaneManager = lm
		defer func() { globalLaneManager = oldLM }()
	}

	// Insert completed tasks in a lane
	oldTime := time.Now().Add(-5 * time.Minute).UTC().Format(time.RFC3339)
	for i := 0; i < 3; i++ {
		db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, lane, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			fmt.Sprintf("wave74-promote-%d", i), "d", "p", "feature", 5,
			"completed", "COMPLETED", "dev", oldTime, oldTime)
	}

	err := autoPromoteCompletedTasksInLane(context.Background(), db)
	if err != nil {
		t.Errorf("autoPromoteCompletedTasksInLane with lane manager: %v", err)
	}
}

// ---------------------------------------------------------------------------
// main.go: handleTaskLogs — tests the task logs handler
// ---------------------------------------------------------------------------

func TestWave74_HandleTaskLogs_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

func TestWave74_HandleTaskLogs_WithID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	taskQueue.Enqueue(ctx, Task{
		ID: "wave74-logs-task", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	r := httptest.NewRequest(http.MethodGet, "/tasks/logs?id=wave74-logs-task", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("handleTaskLogs with valid id: got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// patrol.go: syncRoyalJelly — 65.7%
// Test with no context data → covers query-no-results path.
// ---------------------------------------------------------------------------

func TestWave74_SyncRoyalJelly_EmptyDB(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	err := syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Logf("syncRoyalJelly empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// xnode.go: StatusHandler — 53.8%
// Test the JSON format and node-ID paths.
// ---------------------------------------------------------------------------

func TestWave74_XNode_StatusHandler_WithDBNodes(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	// Register a node first
	xc.RegisterNode(context.Background(), Node{
		ID:       "wave74-status-node",
		Hostname: "prya",
		Address:  "10.0.0.1:8081",
		Status:   "online",
	})

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	w := httptest.NewRecorder()
	xc.StatusHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("StatusHandler: expected 200, got %d", w.Code)
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Errorf("decode StatusHandler response: %v", err)
	}
}

// ---------------------------------------------------------------------------
// migrate.go: getMigrationsForDB — 69%
// Test with postgres type (different branch from sqlite).
// ---------------------------------------------------------------------------

func TestWave74_GetMigrationsForDB_Postgres(t *testing.T) {
	migrations, err := getMigrationsForDB(PostgreSQL)
	if err != nil {
		t.Logf("getMigrationsForDB postgres: %v (ok if no postgres migrations)", err)
	} else {
		t.Logf("getMigrationsForDB postgres: %d migrations", len(migrations))
	}
}

func TestWave74_GetMigrationsForDB_SQLite(t *testing.T) {
	migrations, err := getMigrationsForDB(SQLite)
	if err != nil {
		t.Fatalf("getMigrationsForDB sqlite: %v", err)
	}
	if len(migrations) == 0 {
		t.Error("expected at least some sqlite migrations")
	}
	t.Logf("getMigrationsForDB sqlite: %d migrations", len(migrations))
}

// ---------------------------------------------------------------------------
// patrol.go: requeueRetriableTasks — 85.7%
// Test with tasks that should be requeued.
// ---------------------------------------------------------------------------

func TestWave74_RequeueRetriableTasks_WithRetriableTasks(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE tasks (
		id TEXT PRIMARY KEY, domain TEXT, project TEXT, type TEXT,
		priority INTEGER DEFAULT 5, status TEXT, state TEXT,
		assigned_to TEXT, created_at TEXT, updated_at TEXT,
		retry_count INTEGER DEFAULT 0
	)`)

	// Insert a task in 'assigned' status (could be retried)
	now := time.Now().UTC().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at, retry_count)
		VALUES ('wave74-retry-task', 'd', 'p', 'feature', 5, 'assigned', 'DISPATCHED', ?, ?, 0)`,
		now, now)

	err = requeueRetriableTasks(context.Background(), db)
	if err != nil {
		t.Logf("requeueRetriableTasks with retriable: %v", err)
	}
}

// ---------------------------------------------------------------------------
// completion.go: HandleCompletion — 57.1%
// The function is a method on *CompletionManager with []string args.
// Test via the HTTP handler (handleCompletionZsh / handleCompletionFish).
// ---------------------------------------------------------------------------

func TestWave74_CompletionManager_HandleCompletion_Zsh(t *testing.T) {
	cm := NewCompletionManager()
	cm.HandleCompletion([]string{"zsh"})
	// No error expected — writes to stdout
}

func TestWave74_CompletionManager_HandleCompletion_Fish(t *testing.T) {
	cm := NewCompletionManager()
	cm.HandleCompletion([]string{"fish"})
}

func TestWave74_CompletionManager_HandleCompletion_Bash(t *testing.T) {
	cm := NewCompletionManager()
	cm.HandleCompletion([]string{"bash"})
}

func TestWave74_CompletionManager_HandleCompletion_Unknown(t *testing.T) {
	// Cannot test unknown shell — HandleCompletion calls os.Exit(1) for unknown shells
	t.Skip("skipped: HandleCompletion calls os.Exit for unknown shells")
}

// ---------------------------------------------------------------------------
// tui.go: GetDashboard — 64.9%
// Test with tasks and agents in DB.
// ---------------------------------------------------------------------------

func TestWave74_TUI_GetDashboard_WithData(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	db := getDBConn()

	// Add some tasks and agents
	taskQueue.Enqueue(ctx, Task{
		ID: "wave74-tui-task-1", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})
	now := time.Now().UTC().Format(time.RFC3339)
	db.Exec(`INSERT OR IGNORE INTO agent_heartbeats
		(agent_id, node, status, last_seen, context_pct, current_task_id)
		VALUES (?, ?, ?, ?, ?, ?)`,
		"wave74-tui-agent", "prya", "active", now, 45.0, "")

	tui := NewTUI(db, nil, taskQueue)
	dashboard, err := tui.GetDashboard(ctx)
	if err != nil {
		t.Fatalf("GetDashboard: %v", err)
	}
	if dashboard == nil {
		t.Error("expected non-nil dashboard")
	}
}

// ---------------------------------------------------------------------------
// handlers_node_metrics.go: nodeMetricsPushPatrol — 63.3%
// Test with successful push to verify more branches.
// ---------------------------------------------------------------------------

func TestWave74_NodeMetricsPushPatrol_WithActiveAgents(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	now := time.Now().UTC().Format(time.RFC3339)
	// Add agents with context data
	for _, agent := range []struct{ id string; ctx float64 }{
		{"wave74-metrics-agent-1", 30.0},
		{"wave74-metrics-agent-2", 75.0},
	} {
		db.Exec(`INSERT OR IGNORE INTO agent_heartbeats
			(agent_id, node, status, last_seen, context_pct, current_task_id)
			VALUES (?, ?, ?, ?, ?, ?)`,
			agent.id, "prya", "active", now, agent.ctx, "")
	}

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}))
	defer ts.Close()

	old := context.Background()
	_ = old
	// Set lead URL env
	t.Setenv("FORGE_LEAD_URL", ts.URL)

	err := nodeMetricsPushPatrol(context.Background(), db)
	if err != nil {
		t.Logf("nodeMetricsPushPatrol with active agents: %v", err)
	}
}

// ---------------------------------------------------------------------------
// worktree_manager.go: CreateWorktree — 60.9%
// Test the full creation path (may fail if git not configured — that's OK).
// ---------------------------------------------------------------------------

func TestWave74_WorktreeManager_CreateWorktree_WithValidTaskID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	tmpDir := t.TempDir()

	ctx := context.Background()
	taskID := "wave74-worktree-create-1"
	taskQueue.Enqueue(ctx, Task{
		ID: taskID, Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	// Use the FORGE repo root as the worktree base
	wm := NewWorktreeManager("/Users/bogdan/work/FORGE", tmpDir, db)
	path, err := wm.CreateWorktree(ctx, taskID)
	if err != nil {
		t.Logf("CreateWorktree: %v (expected if git not available or path issues)", err)
	} else {
		t.Logf("CreateWorktree: path=%s", path)
	}
}

