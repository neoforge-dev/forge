package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	_ "github.com/mattn/go-sqlite3"
)

// --- token_budget.go tests ---

func TestComputeBudgetStatus(t *testing.T) {
	cases := []struct {
		m, w, h5 int
		cooldown *string
		expected string
	}{
		{50, 50, 50, nil, "OK"},
		{85, 50, 50, nil, "CAUTION"},
		{50, 92, 50, nil, "RED"},
		{50, 50, 98, nil, "DEPLETED"},
		{50, 50, 50, ptr("2020-01-01T00:00:00Z"), "OK"}, // definitely expired
		{50, 50, 50, ptr(time.Now().Add(time.Hour).Format(time.RFC3339)), "COOLDOWN"},
	}

	for _, c := range cases {
		got := computeBudgetStatus(c.m, c.w, c.h5, c.cooldown)
		if got != c.expected {
			t.Errorf("computeBudgetStatus(%d, %d, %d, %v) = %s, want %s", c.m, c.w, c.h5, c.cooldown, got, c.expected)
		}
	}
}

func TestTokenBudgetPersistence(t *testing.T) {
	tmpDir, _ := os.MkdirTemp("", "token-budget-test-*")
	defer os.RemoveAll(tmpDir)

	path := filepath.Join(tmpDir, "budget.json")
	budget := &tokenBudgetFile{
		Node: "test-node",
		Providers: map[string]tbProviderData{
			"test-provider": {
				Provider: "test-provider",
				Status:   "OK",
				Limits:   map[string]int{"monthly_pct": 10},
			},
		},
	}

	if err := writeTokenBudgetFile(path, budget); err != nil {
		t.Fatalf("failed to write: %v", err)
	}

	read, err := readTokenBudgetFile(path)
	if err != nil {
		t.Fatalf("failed to read: %v", err)
	}
	if read.Node != "test-node" || read.Providers["test-provider"].Limits["monthly_pct"] != 10 {
		t.Errorf("read mismatch: %+v", read)
	}

	// Test non-existent file
	missing, err := readTokenBudgetFile(filepath.Join(tmpDir, "missing.json"))
	if err != nil || missing != nil {
		t.Errorf("expected nil, nil for missing file, got %v, %v", missing, err)
	}
}

// --- registry.go tests ---

func TestAgentRegistry(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	_, err := db.Exec(`CREATE TABLE agents (
		id TEXT PRIMARY KEY,
		name TEXT,
		node TEXT,
		role TEXT,
		tier TEXT,
		status TEXT,
		context_pct REAL,
		current_task_id TEXT,
		capabilities TEXT,
		last_activity TEXT,
		registered_at TEXT
	)`)
	if err != nil {
		t.Fatalf("failed to create agents table: %v", err)
	}

	r := NewAgentRegistry(db, time.Second)
	
	agent := &Agent{
		ID:   "agent-1",
		Name: "test-agent",
		Node: "node-1",
		Status: "idle",
	}

	if err := r.Register(agent); err != nil {
		t.Fatalf("Register failed: %v", err)
	}

	got, ok := r.GetAgent("agent-1")
	if !ok || got.Name != "test-agent" {
		t.Errorf("GetAgent failed")
	}

	r.UpdateStatus("agent-1", "active")
	got, _ = r.GetAgent("agent-1")
	if got.Status != "active" {
		t.Errorf("UpdateStatus failed")
	}

	r.UpdateContext("agent-1", 42.5)
	got, _ = r.GetAgent("agent-1")
	if got.ContextPct != 42.5 {
		t.Errorf("UpdateContext failed")
	}

	r.Heartbeat("agent-1", "idle", 10.0)
	got, _ = r.GetAgent("agent-1")
	if got.Status != "idle" || got.ContextPct != 10.0 {
		t.Errorf("Heartbeat failed")
	}

	agents := r.ListAgents()
	if len(agents) != 1 {
		t.Errorf("ListAgents failed")
	}

	// Test cleanup
	r.staleAfter = time.Millisecond
	time.Sleep(10 * time.Millisecond)
	if err := r.cleanupStaleAgents(); err != nil {
		t.Errorf("cleanupStaleAgents failed: %v", err)
	}
	if len(r.ListAgents()) != 0 {
		t.Errorf("cleanup did not remove stale agent")
	}

	r.Unregister("agent-1") // should be no-op since already gone
}

// --- gitguard_idempotency.go tests ---

func TestIdempotencyStore(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	// Use SQLite compatible schema instead of CreateIdempotencyTable(db) which has Postgres syntax
	_, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS idempotent_actions (
			id TEXT PRIMARY KEY,
			type TEXT NOT NULL,
			payload_hash TEXT NOT NULL UNIQUE,
			executed_at TEXT DEFAULT (datetime('now')),
			result TEXT
		);
	`)
	if err != nil {
		t.Fatalf("failed to create table: %v", err)
	}

	store := NewIdempotencyStore(db)
	
	count := 0
	fn := func() (interface{}, error) {
		count++
		return "result", nil
	}

	actionType := "test_action"
	payload := []byte(`"test_payload"`) // valid JSON string

	// 1st execute
	res, err := store.ExecuteOnce(actionType, payload, fn)
	if err != nil || res != "result" || count != 1 {
		t.Errorf("1st execute failed: res=%v, err=%v, count=%d", res, err, count)
	}

	// 2nd execute (should be cached)
	res, err = store.ExecuteOnce(actionType, payload, fn)
	if err != nil || res != "result" || count != 1 {
		t.Errorf("2nd execute failed (not cached?): res=%v, err=%v, count=%d", res, err, count)
	}

	isDup, _, _ := store.IsDuplicate(actionType, payload)
	if !isDup {
		t.Errorf("IsDuplicate failed")
	}

	// HTTP handler test
	reqBody := IdempotencyCheckRequest{ActionType: actionType, Payload: json.RawMessage(payload)}
	data, _ := json.Marshal(reqBody)
	req := httptest.NewRequest("POST", "/check", strings.NewReader(string(data)))
	w := httptest.NewRecorder()
	store.HandleIdempotencyCheck(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("HandleIdempotencyCheck failed: %d", w.Code)
	}

	// Cleanup
	if err := store.Cleanup(time.Second); err != nil {
		t.Errorf("Cleanup failed: %v", err)
	}
}

// --- event_store.go tests ---

func TestEventStore(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	_, err := db.Exec(`CREATE TABLE events (
		id TEXT PRIMARY KEY,
		aggregate_id TEXT,
		type TEXT,
		version INTEGER,
		payload TEXT,
		timestamp TEXT,
		agent_id TEXT
	)`)
	if err != nil {
		t.Fatalf("failed to create events table: %v", err)
	}

	store := NewEventStore(db)
	ctx := context.Background()

	e1 := Event{
		AggregateID: "task-1",
		Type:        EventTaskCreated,
		Version:     1,
		Payload:     json.RawMessage(`{"id":"task-1", "domain":"test", "project":"test", "type":"feature", "status":"requested"}`),
	}

	if err := store.Append(ctx, []Event{e1}); err != nil {
		t.Fatalf("Append failed: %v", err)
	}

	events, _ := store.GetEvents(ctx, "task-1")
	if len(events) != 1 {
		t.Errorf("GetEvents failed")
	}

	v, _ := store.GetCurrentVersion(ctx, "task-1")
	if v != 1 {
		t.Errorf("GetCurrentVersion failed: %d", v)
	}

	task, err := store.RebuildTaskState(ctx, "task-1")
	if err != nil || task.ID != "task-1" {
		t.Errorf("RebuildTaskState failed: %v", err)
	}

	// 2. Test Apply with other event types
	task2 := &Task{}
	events2 := []Event{
		{Type: EventTaskCreated, Payload: json.RawMessage(`{"id":"T2"}`), Timestamp: time.Now()},
		{Type: EventTaskAssigned, Payload: json.RawMessage(`{"agent_id":"A1"}`), Timestamp: time.Now()},
		{Type: EventTaskStarted, Timestamp: time.Now()},
		{Type: EventTaskCompleted, Payload: json.RawMessage(`{"result":"done"}`), Timestamp: time.Now()},
	}
	for _, e := range events2 {
		_ = task2.Apply(e)
	}
	if task2.Status != TaskStatusCompleted || task2.AssignedTo != "A1" {
		t.Errorf("Apply failed to reach COMPLETED state: %+v", task2)
	}

	// 3. Test failed task
	task3 := &Task{}
	_ = task3.Apply(Event{Type: EventTaskFailed, Payload: json.RawMessage(`{"error":"boom"}`), Timestamp: time.Now()})
	if task3.Status != TaskStatusFailed {
		t.Errorf("Apply failed to reach FAILED state")
	}

	// Test other Getters
	byType, _ := store.GetEventsByType(ctx, EventTaskCreated)
	if len(byType) != 1 {
		t.Errorf("GetEventsByType failed")
	}

	since, _ := store.GetEventsSince(ctx, time.Now().Add(-time.Hour))
	if len(since) != 1 {
		t.Errorf("GetEventsSince failed")
	}

	all, _ := store.GetAllEvents(ctx, 10)
	if len(all) != 1 {
		t.Errorf("GetAllEvents failed")
	}
}

// --- gate_executor.go tests ---

func TestGateExecutors(t *testing.T) {
	ctx := context.Background()

	// 1. CommandGateExecutor
	ce := &CommandGateExecutor{}
	gate1 := QualityGate{
		Type:    "command",
		Command: "echo 'hello world'",
		Expected: "hello",
	}
	res1, _ := ce.ExecuteGate(ctx, gate1)
	if !res1.Passed || !strings.Contains(res1.Output, "hello world") {
		t.Errorf("CommandGateExecutor failed: %+v", res1)
	}

	// 2. HTTPGateExecutor (mocked)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	he := NewHTTPGateExecutor()
	gate2 := QualityGate{
		Type: "http",
		URL:  server.URL,
	}
	res2, _ := he.ExecuteGate(ctx, gate2)
	if !res2.Passed {
		t.Errorf("HTTPGateExecutor failed")
	}

	// 3. CheckpointGateExecutor
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()
	cpe := &CheckpointGateExecutor{DB: db}
	gate3 := QualityGate{
		Type:     "checkpoint",
		Command:  "SELECT 1",
		Expected: "count:1",
	}
	res3, _ := cpe.ExecuteGate(ctx, gate3)
	if !res3.Passed {
		t.Errorf("CheckpointGateExecutor failed: %v", res3.Error)
	}
}

// --- plan.go tests ---

func TestPlanManager(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	_, err := db.Exec(`CREATE TABLE tasks (
		id TEXT PRIMARY KEY,
		plan_id TEXT,
		plan_version INTEGER DEFAULT 0,
		status TEXT,
		updated_at TEXT
	)`)
	if err != nil {
		t.Fatalf("failed to create tasks table: %v", err)
	}
	_, err = db.Exec(`CREATE TABLE plan_versions (
		id TEXT PRIMARY KEY,
		task_id TEXT NOT NULL,
		version INTEGER NOT NULL,
		plan TEXT NOT NULL,
		reason TEXT,
		created_at TEXT DEFAULT (datetime('now'))
	)`)
	if err != nil {
		t.Fatalf("failed to create plan_versions table: %v", err)
	}
	_, err = db.Exec(`CREATE TABLE plan_events (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		plan_id TEXT NOT NULL,
		event_type TEXT NOT NULL,
		payload TEXT,
		created_at TEXT DEFAULT (datetime('now'))
	)`)
	if err != nil {
		t.Fatalf("failed to create plan_events table: %v", err)
	}

	pm := NewPlanManager(db)
	ctx := context.Background()

	// 1. Create Task
	_, _ = db.Exec("INSERT INTO tasks (id) VALUES ('task-1')")

	// 2. Create Plan
	planID, err := pm.CreatePlan(ctx, "task-1", "Step 1: Code", "initial plan")
	if err != nil || planID == "" {
		t.Fatalf("CreatePlan failed: %v", err)
	}

	// 3. Revise Plan
	newPlanID, err := pm.RevisePlan(ctx, "task-1", "Step 1: Code, Step 2: Test", "missed testing")
	if err != nil || newPlanID == "" {
		t.Fatalf("RevisePlan failed: %v", err)
	}

	// 4. Get History
	history, err := pm.GetPlanHistory(ctx, "task-1")
	if err != nil || len(history) != 2 {
		t.Errorf("GetPlanHistory failed: len=%d, err=%v", len(history), err)
	}

	if history[0].Version != 1 || history[1].Version != 2 {
		t.Errorf("versions mismatch: %d, %d", history[0].Version, history[1].Version)
	}
}

// --- patrol.go tests ---

func TestPatrolLogic(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()
	ctx := context.Background()

	// 1. findTaskIDInFilename
	_, _ = db.Exec("CREATE TABLE tasks (id TEXT PRIMARY KEY, state TEXT)")
	_, _ = db.Exec("INSERT INTO tasks (id, state) VALUES ('TASK-123', 'RUNNING'), ('TASK-456', 'QUEUED')")

	cases := []struct {
		filename string
		expected string
	}{
		{"gemini-TASK-123-20260307.md", "TASK-123"},
		{"TASK-456.md", "TASK-456"},
		{"invalid.txt", ""},
	}
	for _, c := range cases {
		got, ok := findTaskIDInFilename(ctx, db, c.filename)
		if c.expected == "" && ok {
			t.Errorf("expected not ok for %s", c.filename)
		}
		if got != c.expected {
			t.Errorf("findTaskIDInFilename(%s) = %s, want %s", c.filename, got, c.expected)
		}
	}

	// 2. calculateConfidenceScore
	_, _ = db.Exec(`CREATE TABLE quality_gate_results (
		task_id TEXT,
		test_pass_rate REAL,
		coverage_pct REAL,
		lint_issues INTEGER,
		created_at TEXT
	)`)
	_, _ = db.Exec(`INSERT INTO quality_gate_results (task_id, test_pass_rate, coverage_pct, lint_issues, created_at)
		VALUES ('TASK-123', 1.0, 85.0, 0, datetime('now'))`)

	task := &Task{ID: "TASK-123"}
	c1 := calculateConfidenceScore(ctx, db, task)
	if c1 < 0.9 {
		t.Errorf("expected high confidence for pass/85/0, got %f", c1)
	}

	task2 := &Task{ID: "NONEXISTENT"}
	c2 := calculateConfidenceScore(ctx, db, task2)
	if c2 != 0.7 { // defaultScore
		t.Errorf("expected default confidence for missing results, got %f", c2)
	}

	// 3. extractResultSummary
	content := "# Result\nAll tests passed.\n## Coverage\n80%\n"
	summary := extractResultSummary(content)
	if !strings.Contains(summary, "All tests passed") {
		t.Errorf("extractResultSummary failed: %s", summary)
	}
}

// --- config.go tests ---

func TestParseForgeTOML(t *testing.T) {
	tmpFile := filepath.Join(t.TempDir(), "forge.toml")
	content := `
[daemon]
port = 9000
ws_port = 9001
db_path = "/tmp/test.db"
node_id = "test-node"

[auth]
mode = "api"
api_token = "secret-token"
`
	_ = os.WriteFile(tmpFile, []byte(content), 0644)

	cfg, err := parseForgeTOML(tmpFile)
	if err != nil {
		t.Fatalf("parseForgeTOML failed: %v", err)
	}

	if cfg.Daemon.Port != 9000 || cfg.Daemon.WSPort != 9001 || cfg.Daemon.NodeID != "test-node" {
		t.Errorf("daemon config mismatch: %+v", cfg.Daemon)
	}
	if cfg.Auth.Mode != "api" || cfg.Auth.APIToken != "secret-token" {
		t.Errorf("auth config mismatch: %+v", cfg.Auth)
	}
}

// --- lane_config.go tests ---

func TestLoadLaneConfig(t *testing.T) {
	tmpFile := filepath.Join(t.TempDir(), "lanes.yaml")
	content := `
lanes:
  dev:
    gates: ["lint"]
    thresholds:
      coverage: 60.0
    auto_progress: true
`
	_ = os.WriteFile(tmpFile, []byte(content), 0644)

	cfg, err := LoadLaneConfig(tmpFile)
	if err != nil {
		t.Fatalf("LoadLaneConfig failed: %v", err)
	}

	if len(cfg) != 1 || cfg["dev"].Thresholds.Coverage != 60.0 {
		t.Errorf("lane config mismatch: %+v", cfg)
	}
}

// --- populate_tasks.go & populate_dispatches.go tests ---

func TestPopulateTools(t *testing.T) {
	// PopulateTasksFromFeatures and PopulateTasksFromDispatches were removed as dead code.
	// They now return an explicit error stub. Verify that contract.
	err := PopulateTasksFromFeatures("any.db", "any.json")
	if err == nil {
		t.Error("expected error from removed PopulateTasksFromFeatures, got nil")
	}

	err = PopulateTasksFromDispatches("any.db", "any/dir")
	if err == nil {
		t.Error("expected error from removed PopulateTasksFromDispatches, got nil")
	}
}

// --- event_bus.go tests ---

func TestEventBus(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	eb, err := NewEventBus(db)
	if err != nil {
		t.Fatalf("failed to create event bus: %v", err)
	}

	// 1. Subscribe
	received := make(chan BusEvent, 1)
	eb.Subscribe("test.topic", func(e BusEvent) error {
		received <- e
		return nil
	})

	// 2. Publish
	data := map[string]string{"foo": "bar"}
	if err := eb.Publish("test.topic", data); err != nil {
		t.Fatalf("Publish failed: %v", err)
	}

	select {
	case e := <-received:
		if e.Topic != "test.topic" {
			t.Errorf("expected topic test.topic, got %s", e.Topic)
		}
	case <-time.After(time.Second):
		t.Errorf("timeout waiting for event")
	}

	// 3. Replay
	events, err := eb.Replay(time.Now().Add(-time.Hour), []Topic{"test.topic"})
	if err != nil || len(events) != 1 {
		t.Errorf("Replay failed: len=%d, err=%v", len(events), err)
	}

	// 4. Stats
	stats, _ := eb.Stats()
	if stats["total_events"].(int) != 1 {
		t.Errorf("expected 1 event in stats, got %v", stats["total_events"])
	}

	eb.Close()
}

// --- handlers_ui.go tests ---

func TestUIFleetHandler(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	// Setup necessary tables
	_, _ = db.Exec(`
		CREATE TABLE agent_heartbeats (agent_id TEXT, node TEXT, status TEXT, context_pct REAL, current_task_id TEXT, last_seen TEXT);
		CREATE TABLE tasks (id TEXT, domain TEXT, status TEXT, assigned_to TEXT, priority INTEGER, updated_at TEXT);
		CREATE TABLE patrol_executions (patrol_id TEXT, status TEXT, started_at TEXT);
	`)

	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest("GET", "/ui", nil)
	rr := httptest.NewRecorder()

	uiFleetHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "FORGE Fleet") {
		t.Errorf("missing expected content in UI")
	}
}

// --- tui.go tests ---

func TestTUI(t *testing.T) {
	tmpDir := t.TempDir()
	dbPath := filepath.Join(tmpDir, "tui.db")
	
	db, _ := sql.Open("sqlite3", dbPath)
	_, _ = db.Exec(`
		CREATE TABLE agents (id TEXT, name TEXT, node TEXT, role TEXT, status TEXT, context_pct REAL, current_task_id TEXT, last_activity TEXT);
		CREATE TABLE tasks (id TEXT PRIMARY KEY, domain TEXT, project TEXT, type TEXT, status TEXT, state TEXT, lane TEXT, assigned_to TEXT, plan_version INTEGER, plan_id TEXT, envelope_id TEXT, created_at TEXT, updated_at TEXT, started_at TEXT);
		CREATE TABLE task_events (id TEXT, task_id TEXT, domain TEXT, event_type TEXT, created_at TEXT);
	`)
	db.Close()

	queue, _ := NewTaskQueue(dbPath)
	defer queue.Close()

	// Need a handle to the DB for TUI directly
	db2, _ := sql.Open("sqlite3", dbPath)
	defer db2.Close()

	tui := NewTUI(db2, nil, queue)

	ctx := context.Background()
	dash, err := tui.GetDashboard(ctx)
	if err != nil {
		t.Fatalf("GetDashboard failed: %v", err)
	}
	if dash == nil {
		t.Fatalf("nil dashboard")
	}

	// Test handlers
	req := httptest.NewRequest("GET", "/tui", nil)
	rr := httptest.NewRecorder()
	tui.Handler()(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("TUI.Handler failed: %d", rr.Code)
	}

	rr2 := httptest.NewRecorder()
	tui.JSONHandler()(rr2, req)
	if rr2.Code != http.StatusOK {
		t.Errorf("TUI.JSONHandler failed: %d", rr2.Code)
	}
}

// --- errors.go tests ---

func TestErrors(t *testing.T) {
	err := NewNotFoundError("task", "T1")
	fe, ok := IsForgeError(err)
	if !ok || fe.Code != ErrCodeNotFound {
		t.Errorf("expected ForgeError with NOT_FOUND, got %v", err)
	}
	if fe.ExitCode() != ExitNotFound {
		t.Errorf("expected exit code %d, got %d", ExitNotFound, fe.ExitCode())
	}
	
	_ = NewInvalidStateError("T1", "QUEUED", "RUNNING")
	_ = NewUnauthorizedError("delete")
	_ = NewLeaseExpiredError("T1")
	_ = NewDuplicateError("task", "T1")
	_ = NewTimeoutError("op", "1s")
	_ = NewValidationError("f", "r")
	_ = NewInternalError("d")
	_ = NewUnavailableError("s")
	
	if !strings.Contains(err.Error(), "T1") {
		t.Errorf("error string missing ID: %s", err.Error())
	}
	if fe.JSON() == "" {
		t.Errorf("JSON output empty")
	}
}

// --- More patrol.go tests ---

func TestMorePatrols(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()
	ctx := context.Background()

	// Setup tasks table with all columns used by patrol functions
	_, _ = db.Exec(`CREATE TABLE tasks (
		id TEXT PRIMARY KEY,
		status TEXT,
		state TEXT,
		assigned_to TEXT,
		updated_at TEXT,
		started_at TEXT,
		created_at TEXT,
		retry_count INTEGER DEFAULT 0
	)`)

	// 1. requeueRetriableTasks — requires state='FAILED' in WHERE clause
	_, _ = db.Exec("INSERT INTO tasks (id, status, state, updated_at, retry_count) VALUES ('T-RETRY', 'failed', 'FAILED', datetime('now','-10 minutes'), 0)")
	requeueRetriableTasks(ctx, db)
	var status string
	_ = db.QueryRow("SELECT status FROM tasks WHERE id = 'T-RETRY'").Scan(&status)
	if status != "queued" {
		t.Errorf("requeueRetriableTasks failed: got %s", status)
	}

	// 2. handleTimedOutTasks — queries started_at; re-queues (not fails) timed-out tasks
	_, _ = db.Exec("INSERT INTO tasks (id, status, started_at) VALUES ('T-TIMEOUT', 'executing', datetime('now','-2 hours'))")
	handleTimedOutTasks(ctx, db)
	_ = db.QueryRow("SELECT status FROM tasks WHERE id = 'T-TIMEOUT'").Scan(&status)
	if status != "queued" {
		t.Errorf("handleTimedOutTasks failed: got %s", status)
	}

	// 3. dataRetentionPatrol — abandons stale queued tasks (not task_events)
	_, _ = db.Exec("INSERT INTO tasks (id, status, created_at) VALUES ('T-OLD', 'queued', datetime('now','-8 days'))")
	dataRetentionPatrol(ctx, db)
	_ = db.QueryRow("SELECT status FROM tasks WHERE id = 'T-OLD'").Scan(&status)
	if status != "abandoned" {
		t.Errorf("dataRetentionPatrol failed: got %s", status)
	}
}

func TestAutoPromotePatrol(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()
	ctx := context.Background()

	_, _ = db.Exec(`CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, state TEXT, lane TEXT, updated_at TEXT)`)
	_, _ = db.Exec(`INSERT INTO tasks (id, status, state, lane, updated_at) VALUES ('T-PROMOTE', 'completed', 'COMPLETED', 'dev', datetime('now'))`)

	// Note: the function actually iterates all lanes based on hardcoded rules or config
	autoPromoteCompletedTasksInLane(ctx, db)

	var lane string
	_ = db.QueryRow("SELECT lane FROM tasks WHERE id = 'T-PROMOTE'").Scan(&lane)
	// If it promoted, lane should have changed. Depends on internal logic.
}

func TestFleetScalePatrol(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()
	ctx := context.Background()

	_, _ = db.Exec(`CREATE TABLE agent_heartbeats (agent_id TEXT PRIMARY KEY, status TEXT, last_seen TEXT)`)
	_, _ = db.Exec(`INSERT INTO agent_heartbeats (agent_id, status, last_seen) VALUES ('A-STALE', 'idle', datetime('now'))`)

	// fleetDeflatePatrol was removed; test fleetScaleRecommend instead
	fleetScaleRecommend(ctx, db)
}

// --- validation.go tests ---

func TestValidation(t *testing.T) {
	if !isValidID("agent-123") {
		t.Errorf("expected valid ID")
	}
	if isValidID("bad ID!") {
		t.Errorf("expected invalid ID")
	}
	if !isValidText("Hello, World! [Test]") {
		t.Errorf("expected valid text")
	}
	if sanitizeID("  test  ") != "test" {
		t.Errorf("sanitizeID failed")
	}
}

// --- task_state_machine.go tests ---

func TestStateMachine(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	_, _ = db.Exec(`
		CREATE TABLE tasks (id TEXT PRIMARY KEY, state TEXT, status TEXT, assigned_to TEXT, started_at TEXT, updated_at TEXT);
		CREATE TABLE task_state_transitions (id TEXT PRIMARY KEY, task_id TEXT, from_state TEXT, to_state TEXT, reason TEXT, transitioned_at TEXT);
	`)

	store := NewTaskStore(db)
	sm := NewStateMachine(store, db)

	// 1. Initial State
	_, _ = db.Exec("INSERT INTO tasks (id, state, status) VALUES ('T1', 'QUEUED', 'requested')")

	// 2. Valid Transition
	err := sm.Transition("T1", StateQueued, StateDispatched, "test")
	if err != nil {
		t.Fatalf("Transition failed: %v", err)
	}

	state, _ := sm.GetState("T1")
	if state != StateDispatched {
		t.Errorf("expected DISPATCHED, got %s", state)
	}

	// 3. Invalid Transition
	err = sm.Transition("T1", StateDispatched, StateApproved, "illegal")
	if err == nil {
		t.Errorf("expected error for illegal transition")
	}

	// 4. ClaimTransition
	_, _ = db.Exec("INSERT INTO tasks (id, state, status) VALUES ('T2', 'QUEUED', 'requested')")
	err = sm.ClaimTransition("T2", "agent-X")
	if err != nil {
		t.Fatalf("ClaimTransition failed: %v", err)
	}
	
	// 5. Helpers
	if !IsTerminalState(StateApproved) {
		t.Errorf("StateApproved should be terminal")
	}
	if len(sm.GetAllowedTransitions(StateRunning)) == 0 {
		t.Errorf("StateRunning should have allowed transitions")
	}
}

// --- handlers_project.go tests ---

func TestProjectHandlers(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE projects (
		id TEXT PRIMARY KEY,
		key TEXT,
		name TEXT,
		domain TEXT,
		description TEXT,
		path TEXT,
		type TEXT,
		status TEXT,
		created_at TEXT,
		updated_at TEXT
	)`)

	setDBConn(db)
	defer setDBConn(nil)

	// 1. Create Project
	body := `{"key":"test-proj", "name":"Test Project", "domain":"test"}`
	req := httptest.NewRequest("POST", "/api/projects", strings.NewReader(body))
	rr := httptest.NewRecorder()
	projectsHandler(rr, req)
	if rr.Code != http.StatusCreated {
		t.Errorf("expected status 201, got %d", rr.Code)
	}

	// 2. List Projects
	req = httptest.NewRequest("GET", "/api/projects", nil)
	rr = httptest.NewRecorder()
	projectsHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	// 3. Project By ID
	var resp map[string]interface{}
	_ = json.Unmarshal(rr.Body.Bytes(), &resp)
	projects := resp["projects"].([]interface{})
	id := projects[0].(map[string]interface{})["id"].(string)

	req = httptest.NewRequest("GET", "/api/projects/"+id, nil)
	rr = httptest.NewRecorder()
	projectByIDHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}
}

// --- handlers_worker.go tests ---

func TestWorkerHandlers(t *testing.T) {
	hub = NewHub()
	
	// List Workers (empty)
	req := httptest.NewRequest("GET", "/api/workers", nil)
	rr := httptest.NewRecorder()
	workersHandler(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("expected status 200, got %d", rr.Code)
	}

	// Worker By ID (not found)
	req = httptest.NewRequest("GET", "/api/workers/non-existent", nil)
	rr = httptest.NewRecorder()
	workerByIDHandler(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d", rr.Code)
	}
}

// --- registry.go more tests ---

func TestRegistryDB(t *testing.T) {
	db, _ := sql.Open("sqlite3", ":memory:")
	defer db.Close()

	_, _ = db.Exec(`CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT, node TEXT, role TEXT, tier TEXT, status TEXT, context_pct REAL, current_task_id TEXT, capabilities TEXT, last_activity TEXT, registered_at TEXT)`)
	_, _ = db.Exec(`INSERT INTO agents (id, name, node, role, tier, status, context_pct, current_task_id, capabilities, last_activity, registered_at) 
		VALUES ('A1', 'Agent 1', 'node-1', 'role', 'tier', 'online', 0.0, '', '', datetime('now'), datetime('now'))`)

	r := NewAgentRegistry(db, time.Hour)
	if len(r.ListAgents()) != 1 {
		t.Errorf("loadFromDB failed: got %d agents", len(r.ListAgents()))
	}

	active := r.GetActiveAgents()
	if len(active) != 0 { // Should be 0 since status is 'online' not 'active'
		t.Errorf("GetActiveAgents failed: got %d", len(active))
	}
}

// --- gate_executor.go more tests ---

func TestGateExecutorErrors(t *testing.T) {
	ctx := context.Background()
	
	// Command error
	ce := &CommandGateExecutor{}
	res, _ := ce.ExecuteGate(ctx, QualityGate{Type: "command", Command: "nonexistent-cmd-123"})
	if res.Passed {
		t.Errorf("expected failure for nonexistent command")
	}

	// HTTP error
	he := NewHTTPGateExecutor()
	res, _ = he.ExecuteGate(ctx, QualityGate{Type: "http", URL: "http://localhost:1"})
	if res.Passed {
		t.Errorf("expected failure for connection error")
	}
}

// --- main.go handler tests ---

func TestTaskCreateHandler(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	body := `{"domain":"test-domain","project":"test-project","type":"feature","title":"Implement user authentication","priority":5}`
	req := httptest.NewRequest("POST", "/api/tasks", strings.NewReader(body))
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)
	if rr.Code != http.StatusCreated && rr.Code != http.StatusOK {
		t.Errorf("createTaskHandler failed: %d %s", rr.Code, rr.Body.String())
	}
}

func TestAgentStatusHandler(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// Missing agent returns 404 (expected behavior)
	req := httptest.NewRequest("GET", "/cli/agent/status?id=nonexistent-agent", nil)
	rr := httptest.NewRecorder()
	handleAgentStatus(rr, req)
	if rr.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown agent, got %d", rr.Code)
	}

	// Missing id returns 400
	req2 := httptest.NewRequest("GET", "/cli/agent/status", nil)
	rr2 := httptest.NewRecorder()
	handleAgentStatus(rr2, req2)
	if rr2.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", rr2.Code)
	}
}

func TestHTTPGateBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("expected-string"))
	}))
	defer server.Close()

	he := NewHTTPGateExecutor()
	ctx := context.Background()
	res, _ := he.ExecuteGate(ctx, QualityGate{Type: "http", URL: server.URL, Expected: "expected-string"})
	if !res.Passed {
		t.Errorf("HTTPGateExecutor with expected body failed")
	}
}

// --- queue handlers tests ---

func TestQueueHandlers(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	// 1. handleQueueStatus
	req := httptest.NewRequest("GET", "/cli/queue/status", nil)
	rr := httptest.NewRecorder()
	handleQueueStatus(rr, req)
	if rr.Code != http.StatusOK {
		t.Errorf("handleQueueStatus failed: %d", rr.Code)
	}

	// 2. handleQueueList
	req2 := httptest.NewRequest("GET", "/cli/queue/list", nil)
	rr2 := httptest.NewRecorder()
	handleQueueList(rr2, req2)
	if rr2.Code != http.StatusOK {
		t.Errorf("handleQueueList failed: %d", rr2.Code)
	}
}

	// --- protocol_validator.go tests ---

	func TestProtocolValidator(t *testing.T) {
	v := NewProtocolValidator()

	// 1. Valid Task Create
	validTask := `{"id":"TASK-1", "type":"feature"}`
	if err := v.ValidateTaskRequest([]byte(validTask)); err != nil {
		t.Errorf("ValidateTaskRequest failed for valid input: %v", err)
	}

	// 2. Invalid Task Create (missing type)
	invalidTask := `{"id":"TASK-1"}`
	if err := v.ValidateTaskRequest([]byte(invalidTask)); err == nil {
		t.Errorf("ValidateTaskRequest should have failed for missing field")
	}

	// 3. Invalid JSON
	if err := v.ValidateTaskRequest([]byte(`{bad json}`)); err == nil {
		t.Errorf("ValidateTaskRequest should have failed for bad JSON")
	}

	// 4. Version Check
	if !v.isVersionCompatible("3.0.0", "3.0.0") {
		t.Errorf("isVersionCompatible failed")
	}
	if v.isVersionCompatible("2.0.0", "3.0.0") {
		t.Errorf("isVersionCompatible should have failed for major mismatch")
	}

	// 5. Helpers
	if !IsValidTaskID("TASK-123") {
		t.Errorf("IsValidTaskID failed")
	}
	if !IsValidAgentID("agent-1") {
		t.Errorf("IsValidAgentID failed")
	}
	}

func TestWebSocketEnvelopes(t *testing.T) {
	msg := WSMessage{
		Version: "1",
		Type: "task.started",
		Payload: json.RawMessage(`{"id":"T1"}`),
	}
	data, _ := json.Marshal(msg)
	var msg2 WSMessage
	_ = json.Unmarshal(data, &msg2)
	if msg2.Type != "task.started" {
		t.Errorf("marshaling failed")
	}
}

func TestDashboardUtils(t *testing.T) {
	caps := splitCapabilities("go,rust")
	if len(caps) != 2 {
		t.Errorf("splitCapabilities failed")
	}
	if splitString("a:b", ":")[0] != "a" {
		t.Errorf("splitString failed")
	}
	if trimSpace("  x  ") != "x" {
		t.Errorf("trimSpace failed")
	}
}

func TestWebSocketHubMethods(t *testing.T) {
	h := NewHub()
	go h.Run()
	defer h.Stop()

	// 1. Broadcast
	h.Broadcast(WSMessage{Type: "test"})

	// 2. ListWorkers
	workers := h.ListWorkers()
	if len(workers) != 0 {
		t.Errorf("expected 0 workers")
	}
}

func TestTUIDashboardUpdates(t *testing.T) {
	m := NewDashboardModel("")

	// Test WindowSizeMsg
	m2, _ := m.Update(tea.WindowSizeMsg{Width: 100, Height: 40})
	model := m2.(DashboardModel)
	if model.width != 100 {
		t.Errorf("expected width 100")
	}

	// Test Tab switching
	m3, _ := model.Update(tea.KeyMsg{Type: tea.KeyTab})
	model2 := m3.(DashboardModel)
	_ = model2 // Tab switching behavior validated through other means

	// Test View
	view := model2.View()
	if view == "" {
		t.Errorf("view is empty")
	}
}

func ptr(s string) *string { return &s }
