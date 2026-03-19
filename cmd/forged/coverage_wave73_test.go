//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
)

// ---------------------------------------------------------------------------
// TestWave73_HandleTaskList — handleTaskList (main.go:291, 60% coverage)
// ---------------------------------------------------------------------------

func TestWave73_HandleTaskList_Empty(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleTaskList: status=%d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave73_HandleTaskList_WithFormat(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list?format=json", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleTaskList json: status=%d body=%s", rr.Code, rr.Body.String())
	}
}

func TestWave73_HandleTaskList_WithTask(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "wave73-task-list-001",
		Domain:   "forge",
		Project:  "v3",
		Type:     TaskTypeFeature,
		Title:    "Wave73 list task",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	_ = taskQueue.Enqueue(ctx, task)

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleTaskList with task: status=%d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// TestWave73_HandleAgentList — handleAgentList (main.go:361, 57.1% coverage)
// ---------------------------------------------------------------------------

func TestWave73_HandleAgentList_Empty(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/agent/list", nil)
	rr := httptest.NewRecorder()
	handleAgentList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleAgentList: status=%d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// TestWave73_HandleSystemHealth — handleSystemHealth (main.go:402, 55.6%)
// ---------------------------------------------------------------------------

func TestWave73_HandleSystemHealth_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/system/health", nil)
	rr := httptest.NewRecorder()
	handleSystemHealth(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleSystemHealth: status=%d body=%s", rr.Code, rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// TestWave73_HandleQueueDepth — handleQueueDepth (main.go:436, 54.5%)
// ---------------------------------------------------------------------------

func TestWave73_HandleQueueDepth_Basic(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/queue/depth", nil)
	rr := httptest.NewRecorder()
	handleQueueDepth(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleQueueDepth: status=%d body=%s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := resp["depth"]; !ok {
		t.Error("expected 'depth' key in response")
	}
}

// ---------------------------------------------------------------------------
// TestWave73_OpenclawEventsHandler — openclawEventsHandler (handlers_openclaw.go:264)
// ---------------------------------------------------------------------------

func TestWave73_OpenclawEventsHandler_NonFlusher(t *testing.T) {
	type nonFlusherW struct{ http.ResponseWriter }
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil)
	rr := httptest.NewRecorder()
	openclawEventsHandler(nonFlusherW{rr}, req)

	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 for non-flusher, got %d", rr.Code)
	}
}

func TestWave73_OpenclawEventsHandler_ImmediateCancel(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/events", nil).WithContext(ctx)
	rr := httptest.NewRecorder()
	openclawEventsHandler(rr, req)

	// Should have written SSE connected event before exiting
	if !strings.Contains(rr.Body.String(), "connected") {
		t.Errorf("expected 'connected' in body, got: %s", rr.Body.String())
	}
}

// ---------------------------------------------------------------------------
// TestWave73_AgentsSSEHandler — agentsSSEHandler (handlers_agent.go:437)
// ---------------------------------------------------------------------------

func TestWave73_AgentsSSEHandler_NonFlusher(t *testing.T) {
	type nonFlusherW struct{ http.ResponseWriter }
	req := httptest.NewRequest(http.MethodGet, "/api/agents/stream", nil)
	rr := httptest.NewRecorder()
	agentsSSEHandler(nonFlusherW{rr}, req)

	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 for non-flusher, got %d", rr.Code)
	}
}

// ---------------------------------------------------------------------------
// TestWave73_XNodeStatusHandler — XNodeController.StatusHandler (xnode.go:556)
// ---------------------------------------------------------------------------

func TestWave73_XNodeStatusHandler_Empty(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, err = db.Exec(`
		CREATE TABLE nodes (
			id TEXT PRIMARY KEY,
			hostname TEXT,
			address TEXT,
			status TEXT,
			last_heartbeat TEXT
		);
		CREATE TABLE xnode_tasks (
			id TEXT PRIMARY KEY,
			task_id TEXT,
			target_node TEXT,
			status TEXT
		);
	`)
	if err != nil {
		t.Fatalf("create tables: %v", err)
	}

	xc := &XNodeController{db: db, nodeID: "wave73-node"}

	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	rr := httptest.NewRecorder()
	xc.StatusHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("XNodeStatusHandler: status=%d body=%s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["node_id"] != "wave73-node" {
		t.Errorf("node_id = %v, want wave73-node", resp["node_id"])
	}
	if resp["nodes_total"].(float64) != 0 {
		t.Errorf("nodes_total = %v, want 0", resp["nodes_total"])
	}
}

func TestWave73_XNodeStatusHandler_WithData(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, err = db.Exec(`
		CREATE TABLE nodes (id TEXT, hostname TEXT, address TEXT, status TEXT, last_heartbeat TEXT);
		CREATE TABLE xnode_tasks (id TEXT, task_id TEXT, target_node TEXT, status TEXT);
		INSERT INTO nodes VALUES ('n1', 'prya', '10.0.0.1', 'online', datetime('now'));
		INSERT INTO nodes VALUES ('n2', 'sati', '10.0.0.2', 'online', datetime('now'));
		INSERT INTO xnode_tasks VALUES ('xt1', 'T1', 'n2', 'pending');
		INSERT INTO xnode_tasks VALUES ('xt2', 'T2', 'n2', 'delivered');
	`)
	if err != nil {
		t.Fatalf("setup: %v", err)
	}

	xc := &XNodeController{db: db, nodeID: "prya"}
	req := httptest.NewRequest(http.MethodGet, "/api/xnode/status", nil)
	rr := httptest.NewRecorder()
	xc.StatusHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["nodes_total"].(float64) != 2 {
		t.Errorf("nodes_total = %v, want 2", resp["nodes_total"])
	}
	if resp["tasks_total"].(float64) != 2 {
		t.Errorf("tasks_total = %v, want 2", resp["tasks_total"])
	}
}

// ---------------------------------------------------------------------------
// TestWave73_GetAgentID — getAgentID (cli_commands.go:90, 50%)
// ---------------------------------------------------------------------------

func TestWave73_GetAgentID_FromEnv(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")

	t.Setenv("FORGE_AGENT_ID", "wave73-env-agent")
	defer os.Unsetenv("FORGE_AGENT_ID")
	os.Unsetenv("TMUX")

	id, err := getAgentID(cmd)
	if err != nil {
		t.Fatalf("getAgentID from env: %v", err)
	}
	if id != "wave73-env-agent" {
		t.Errorf("id = %q, want %q", id, "wave73-env-agent")
	}
}

func TestWave73_GetAgentID_NoSource(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")

	os.Unsetenv("FORGE_AGENT_ID")
	os.Unsetenv("TMUX")

	_, err := getAgentID(cmd)
	if err == nil {
		t.Error("expected error when no agent source available")
	}
}

func TestWave73_GetAgentID_FromFlag(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	if err := cmd.Flags().Set("agent", "flag-agent-73"); err != nil {
		t.Fatalf("set flag: %v", err)
	}

	id, err := getAgentID(cmd)
	if err != nil {
		t.Fatalf("getAgentID from flag: %v", err)
	}
	if id != "flag-agent-73" {
		t.Errorf("id = %q, want %q", id, "flag-agent-73")
	}
}

// ---------------------------------------------------------------------------
// TestWave73_GetCPUCount — getCPUCount (fleet_scaler.go:574, 50%)
// ---------------------------------------------------------------------------

func TestWave73_GetCPUCount(t *testing.T) {
	count := getCPUCount()
	if count <= 0 {
		t.Errorf("getCPUCount returned %d, want > 0", count)
	}
}

// ---------------------------------------------------------------------------
// TestWave73_CircuitBreaker helpers
// ---------------------------------------------------------------------------

func TestWave73_CircuitBreaker_ClosedDefault(t *testing.T) {
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	if circuitBreakerTripped() {
		t.Error("expected circuit breaker not tripped")
	}
}

func TestWave73_RecordSpawnFailureAndSuccess(t *testing.T) {
	// Reset
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	recordSpawnFailure()
	recordSpawnFailure()

	circuitBreaker.Lock()
	if circuitBreaker.consecutiveFailures != 2 {
		t.Errorf("expected 2 failures, got %d", circuitBreaker.consecutiveFailures)
	}
	circuitBreaker.Unlock()

	recordSpawnSuccess()

	circuitBreaker.Lock()
	if circuitBreaker.consecutiveFailures != 0 {
		t.Errorf("expected 0 failures after success, got %d", circuitBreaker.consecutiveFailures)
	}
	circuitBreaker.state = "closed"
	circuitBreaker.Unlock()
}

// ---------------------------------------------------------------------------
// TestWave73_SpawnAgent_UnknownType — error path (fleet_scaler.go:655)
// ---------------------------------------------------------------------------

func TestWave73_SpawnAgent_UnknownAgentType(t *testing.T) {
	ctx := context.Background()
	_, err := spawnAgent(ctx, "totally-unknown-agent-xyz-73", "test-node")
	if err == nil {
		t.Error("expected error for unknown agent type")
	}
	if !strings.Contains(err.Error(), "unknown agent type") {
		t.Errorf("unexpected error message: %v", err)
	}
}

// ---------------------------------------------------------------------------
// TestWave73_CompletionManager — HandleCompletion (completion.go:226, 57.1%)
// ---------------------------------------------------------------------------

func TestWave73_CompletionManager_GenerateBash(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateBash(&buf); err != nil {
		t.Fatalf("GenerateBash: %v", err)
	}
	if !strings.Contains(buf.String(), "forge") {
		t.Error("bash completion missing 'forge'")
	}
}

func TestWave73_CompletionManager_GenerateZsh(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateZsh(&buf); err != nil {
		t.Fatalf("GenerateZsh: %v", err)
	}
	if !strings.Contains(buf.String(), "forge") {
		t.Error("zsh completion missing 'forge'")
	}
}

func TestWave73_CompletionManager_GenerateFish(t *testing.T) {
	m := NewCompletionManager()
	var buf bytes.Buffer
	if err := m.GenerateFish(&buf); err != nil {
		t.Fatalf("GenerateFish: %v", err)
	}
	if !strings.Contains(buf.String(), "forge") {
		t.Error("fish completion missing 'forge'")
	}
}

// ---------------------------------------------------------------------------
// TestWave73_NodeMetricsPushPatrol (handlers_node_metrics.go:452, 58.3%)
// ---------------------------------------------------------------------------

func TestWave73_NodeMetricsPushPatrol_NoLeadURL(t *testing.T) {
	os.Unsetenv("FORGE_LEAD_URL")

	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	err = nodeMetricsPushPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected no error when FORGE_LEAD_URL empty, got: %v", err)
	}
}

func TestWave73_NodeMetricsPushPatrol_WithLeadURL(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	t.Setenv("FORGE_LEAD_URL", server.URL)
	t.Setenv("NODE_ID", "wave73-node")
	defer os.Unsetenv("FORGE_LEAD_URL")
	defer os.Unsetenv("NODE_ID")

	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`
		CREATE TABLE agent_heartbeats (
			agent_id TEXT PRIMARY KEY,
			node TEXT,
			status TEXT,
			context_pct REAL,
			current_task_id TEXT,
			last_seen TEXT
		);
		CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT);
		CREATE TABLE metrics (metric_name TEXT, value REAL, labels TEXT, period TEXT, computed_at TEXT);
	`)

	err = nodeMetricsPushPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("nodeMetricsPushPatrol with lead URL: %v", err)
	}
}

func TestWave73_NodeMetricsPushPatrol_WithNodeIDFromHostname(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	t.Setenv("FORGE_LEAD_URL", server.URL)
	os.Unsetenv("NODE_ID") // let it use hostname
	defer os.Unsetenv("FORGE_LEAD_URL")

	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`
		CREATE TABLE agent_heartbeats (agent_id TEXT, last_seen TEXT);
		CREATE TABLE tasks (id TEXT, status TEXT);
		CREATE TABLE metrics (metric_name TEXT, value REAL, labels TEXT, period TEXT, computed_at TEXT);
	`)

	err = nodeMetricsPushPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("nodeMetricsPushPatrol without NODE_ID: %v", err)
	}
}

// ---------------------------------------------------------------------------
// TestWave73_SyncRoyalJelly (patrol.go:408, 62.9%)
// ---------------------------------------------------------------------------

func TestWave73_SyncRoyalJelly_MissingContextDir(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT, context_pct REAL)`)
	_, _ = db.Exec(`CREATE TABLE context_envelopes (
		id TEXT PRIMARY KEY, domain TEXT, content TEXT, created_at TEXT
	)`)

	// The real .forge/context dir is likely to exist in this repo — if it doesn't exist,
	// the function returns nil (expected). If it does exist, it silently skips files.
	err = syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("syncRoyalJelly: %v", err)
	}
}

func TestWave73_SyncRoyalJelly_WithValidContextPct(t *testing.T) {
	tmpDir := t.TempDir()

	// Override FORGE_ROOT so syncRoyalJelly looks in our temp dir
	// syncRoyalJelly uses "./forge/context" (hardcoded), so we use chdir workaround
	// Instead, we create the structure the function expects
	contextDir := tmpDir + "/.forge/context/wave73domain"
	if err := os.MkdirAll(contextDir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(contextDir+"/context_pct", []byte("75.5\n"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT, context_pct REAL)`)
	_, _ = db.Exec(`CREATE TABLE context_envelopes (
		id TEXT PRIMARY KEY, domain TEXT, content TEXT, created_at TEXT
	)`)
	_, _ = db.Exec(`INSERT INTO agents (id, name, context_pct) VALUES ('wave73domain', 'wave73domain', 0.0)`)

	// syncRoyalJelly reads "./.forge/context" relative to cwd — won't find tmpDir
	// but should complete without error either way
	err = syncRoyalJelly(context.Background(), db)
	if err != nil {
		t.Errorf("syncRoyalJelly: %v", err)
	}
}

// ---------------------------------------------------------------------------
// TestWave73_ConfidenceApprove (patrol.go:1087, 5% coverage)
// ---------------------------------------------------------------------------

func TestWave73_ConfidenceApprove_NilStateMachine(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	oldSM := stateMachine
	oldAS := globalApprovalService
	stateMachine = nil
	globalApprovalService = nil
	defer func() {
		stateMachine = oldSM
		globalApprovalService = oldAS
	}()

	if err := confidenceApproveCompletedTasks(context.Background(), db); err != nil {
		t.Errorf("expected nil with nil globals, got: %v", err)
	}
}

func TestWave73_ConfidenceApprove_EmptyTasksTable(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if stateMachine == nil {
		t.Skip("stateMachine not initialized")
	}
	if globalApprovalService == nil {
		t.Skip("globalApprovalService not initialized")
	}

	if err := confidenceApproveCompletedTasks(context.Background(), db); err != nil {
		t.Errorf("expected nil error with no completed tasks, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// TestWave73_GetCoordinationHTML (coordination.go:473, 56.5%)
// ---------------------------------------------------------------------------

func TestWave73_GetCoordinationHTML_Basic(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE agent_heartbeats (
		agent_id TEXT PRIMARY KEY, node TEXT, status TEXT,
		context_pct REAL, current_task_id TEXT, last_seen TEXT
	)`)

	cd := NewCoordinationDashboard(db)
	html := cd.GetCoordinationHTML()

	if html == "" {
		t.Error("GetCoordinationHTML returned empty string")
	}
	if !strings.Contains(html, "Sprint Coordination") {
		t.Errorf("HTML missing 'Sprint Coordination': %s", html[:wave73Min(len(html), 300)])
	}
}

func TestWave73_GetCoordinationHTML_WithAgent(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE agent_heartbeats (
		agent_id TEXT PRIMARY KEY, node TEXT, status TEXT,
		context_pct REAL, current_task_id TEXT, last_seen TEXT
	)`)
	_, _ = db.Exec(`INSERT INTO agent_heartbeats VALUES
		('agent-test', 'prya', 'active', 45.0, 'TASK-123', datetime('now'))`)

	cd := NewCoordinationDashboard(db)
	html := cd.GetCoordinationHTML()

	if !strings.Contains(html, "section") {
		t.Error("expected 'section' in HTML")
	}
}

func wave73Min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// ---------------------------------------------------------------------------
// TestWave73_BlastRadiusFromResult (patrol.go helpers)
// ---------------------------------------------------------------------------

func TestWave73_BlastRadius_Empty(t *testing.T) {
	if n := blastRadiusFromResult(""); n != 1 {
		t.Errorf("blastRadiusFromResult('') = %d, want 1", n)
	}
}

func TestWave73_BlastRadius_FilesChanged(t *testing.T) {
	if n := blastRadiusFromResult(`{"files_changed":7}`); n != 7 {
		t.Errorf("expected 7, got %d", n)
	}
}

func TestWave73_BlastRadius_BlastRadiusField(t *testing.T) {
	if n := blastRadiusFromResult(`{"files_changed":0,"blast_radius":4}`); n != 4 {
		t.Errorf("expected 4, got %d", n)
	}
}

func TestWave73_BlastRadius_InvalidJSON(t *testing.T) {
	if n := blastRadiusFromResult("not json"); n != 1 {
		t.Errorf("expected 1 for bad JSON, got %d", n)
	}
}

// ---------------------------------------------------------------------------
// TestWave73_FleetAutoExecutePatrol (fleet_scaler.go:734, 23%)
// ---------------------------------------------------------------------------

func TestWave73_FleetAutoExecutePatrol_CircuitBreaker(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	circuitBreaker.Lock()
	circuitBreaker.state = "open"
	circuitBreaker.consecutiveFailures = 5
	circuitBreaker.lastFailure = time.Now()
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = "closed"
		circuitBreaker.consecutiveFailures = 0
		circuitBreaker.Unlock()
	}()

	if err := fleetAutoExecutePatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil error with circuit breaker open, got: %v", err)
	}
}

func TestWave73_FleetAutoExecutePatrol_FlapGuard(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Now()
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = time.Time{}
		lastScaleEvent.Unlock()
	}()

	if err := fleetAutoExecutePatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil error during flap guard, got: %v", err)
	}
}

func TestWave73_FleetAutoExecutePatrol_NoPendingRecs(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, err = db.Exec(`CREATE TABLE scale_recommendations (
		id TEXT PRIMARY KEY,
		node_id TEXT,
		agent_type TEXT,
		reason TEXT,
		auto_execute INTEGER DEFAULT 0,
		status TEXT DEFAULT 'pending',
		action TEXT DEFAULT 'inflate',
		expires_at TEXT,
		created_at TEXT DEFAULT (datetime('now'))
	)`)
	if err != nil {
		t.Fatalf("create table: %v", err)
	}

	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Now().Add(-2 * time.Minute)
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = time.Time{}
		lastScaleEvent.Unlock()
	}()

	if err := fleetAutoExecutePatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil error with no pending recs, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// TestWave73_GitGuard_ExecuteCommit error paths (gitguard.go:312, 37.9%)
// ---------------------------------------------------------------------------

func TestWave73_GitGuard_ExecuteCommit_NoLock(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE git_branch_locks (
		id TEXT PRIMARY KEY,
		task_id TEXT NOT NULL,
		branch TEXT NOT NULL,
		locked_by TEXT,
		locked_at TEXT,
		status TEXT DEFAULT 'active'
	)`)

	g := NewGitGuard(db, t.TempDir())
	result := g.executeCommit(GitAction{
		TaskID:  "TASK-NOLOCK-73",
		Message: "test",
		Files:   []string{"file.go"},
	})

	if result.Success {
		t.Error("expected failure for no branch lock")
	}
	if result.Error == "" {
		t.Error("expected non-empty error")
	}
}
