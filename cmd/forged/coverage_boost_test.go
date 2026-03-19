//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestCreateTaskHandler_ValidInput verifies that POST /api/tasks with a valid
// payload returns 200 and a task with a generated ID.
func TestCreateTaskHandler_ValidInput(t *testing.T) {
	defer setupCoverageQueue(t)()

	body := `{"domain":"forge","project":"v3","type":"feature","title":"Coverage Test","priority":5}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	createTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp Task
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.ID == "" {
		t.Error("expected non-empty ID in response")
	}
	if resp.Domain != "forge" {
		t.Errorf("domain = %q, want %q", resp.Domain, "forge")
	}
	if resp.Title != "Coverage Test" {
		t.Errorf("title = %q, want %q", resp.Title, "Coverage Test")
	}

	// Confirm the task was persisted (createTaskHandler uses status "requested" on creation).
	got, err := taskQueue.GetTask(context.Background(), resp.ID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if got.ID == "" {
		t.Error("persisted task has empty ID")
	}
}

// TestGetAgentsHealthHandler verifies that the agentsHealthHandler returns 200
// with a JSON body containing "agents" and "count" keys when the agents table
// is empty.
func TestGetAgentsHealthHandler(t *testing.T) {
	defer setupCoverageQueue(t)()

	// Use the path that triggers the "list all" branch in agentsHealthHandler:
	// strings.TrimPrefix("/api/agents/", "/api/agents/") == "" → list-all path.
	req := httptest.NewRequest(http.MethodGet, "/api/agents/", nil)
	rr := httptest.NewRecorder()

	agentsHealthHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	ct := rr.Header().Get("Content-Type")
	if !strings.HasPrefix(ct, "application/json") {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}

	var resp struct {
		Agents []interface{} `json:"agents"`
		Count  int           `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	// An empty DB has no agents, but the response structure must be present.
	if resp.Count != len(resp.Agents) {
		t.Errorf("count=%d does not match len(agents)=%d", resp.Count, len(resp.Agents))
	}
}

// TestClaimTaskHandler_SuccessCoverage creates a queued task then claims it
// via claimTaskHandler and asserts the response is 201 and the task is
// transitioned to assigned.
func TestClaimTaskHandler_SuccessCoverage(t *testing.T) {
	defer setupCoverageQueue(t)()

	taskID := "claim-coverage-001"
	task := Task{
		ID:       taskID,
		Domain:   "forge",
		Project:  "v3",
		Type:     TaskTypeFeature,
		Title:    "Claim Coverage Task",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	payload := `{"agent_id":"test-agent"}`
	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/claim", bytes.NewBufferString(payload))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	claimTaskHandler(rr, req)

	if rr.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201; body: %s", rr.Code, rr.Body.String())
	}

	updated, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("GetTask after claim: %v", err)
	}
	if updated.Status != TaskStatusAssigned {
		t.Errorf("status = %s, want %s", updated.Status, TaskStatusAssigned)
	}
	if updated.AssignedTo != "test-agent" {
		t.Errorf("assigned_to = %q, want %q", updated.AssignedTo, "test-agent")
	}
}

// TestAbandonTaskHandler_Success enqueues a queued task and abandons it via
// abandonTaskHandler, expecting a 200 JSON response.
func TestAbandonTaskHandler_Success(t *testing.T) {
	defer setupCoverageQueue(t)()

	taskID := "abandon-001"
	task := Task{
		ID:       taskID,
		Domain:   "forge",
		Project:  "v3",
		Type:     TaskTypeFeature,
		Title:    "Task to Abandon",
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/abandon", nil)
	rr := httptest.NewRecorder()

	abandonTaskHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["status"] != "ok" {
		t.Errorf("status = %v, want ok", resp["status"])
	}
}

// TestAbandonTaskHandler_NotFound calls abandonTaskHandler for a non-existent
// task and expects 404.
func TestAbandonTaskHandler_NotFound(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/no-such-task/abandon", nil)
	rr := httptest.NewRecorder()

	abandonTaskHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404; body: %s", rr.Code, rr.Body.String())
	}
}

// TestAbandonTaskHandler_MethodNotAllowed sends a GET to abandonTaskHandler
// and expects 405.
func TestAbandonTaskHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/any-id/abandon", nil)
	rr := httptest.NewRecorder()

	abandonTaskHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}

// TestPruneTasksHandler_Empty calls pruneTasksHandler with no stale tasks in
// the DB; expects 200 with pruned=0.
func TestPruneTasksHandler_Empty(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/prune", nil)
	rr := httptest.NewRecorder()

	pruneTasksHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["status"] != "ok" {
		t.Errorf("status field = %v, want ok", resp["status"])
	}
}

// TestPruneTasksHandler_MethodNotAllowed sends a GET and expects 405.
func TestPruneTasksHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/prune", nil)
	rr := httptest.NewRecorder()

	pruneTasksHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}

// TestListPatternsHandler_Empty calls listPatternsHandler against an empty DB
// and expects 200 with an empty patterns array.
func TestListPatternsHandler_Empty(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/patterns", nil)
	rr := httptest.NewRecorder()

	listPatternsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp struct {
		Patterns []interface{} `json:"patterns"`
		Count    int           `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Count != 0 {
		t.Errorf("count = %d, want 0", resp.Count)
	}
}

// TestAgentMetricsHandler_Success calls agentMetricsHandler for a known agent
// against an empty telemetry table and expects 200 with an empty metrics list.
func TestAgentMetricsHandler_Success(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/forge:test-agent/metrics", nil)
	rr := httptest.NewRecorder()

	agentMetricsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp["agent_id"] == nil {
		t.Error("expected agent_id in response")
	}
}

// TestAgentMetricsHandler_MethodNotAllowed sends a POST and expects 405.
func TestAgentMetricsHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/forge:test-agent/metrics", nil)
	rr := httptest.NewRecorder()

	agentMetricsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}

// ============================================================================
// CoverageBoost tests — targeting low-coverage functions identified in the task
// ============================================================================

// setupCoverageBoostDB creates an isolated SQLite DB with all migrations applied.
// It returns the DB and a cleanup function. Unlike setupCoverageQueue it does NOT
// wire global state, so callers that need globals must call setDBConn separately.
func setupCoverageBoostDB(t *testing.T) (*sql.DB, func()) {
	t.Helper()
	dbPath := filepath.Join(os.TempDir(), fmt.Sprintf("test_covboost_%d_%d.db", time.Now().UnixNano(), os.Getpid()))
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("setupCoverageBoostDB OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("setupCoverageBoostDB MigrateUp: %v", err)
	}
	return db, func() {
		db.Close()
		_ = os.Remove(dbPath)
		_ = os.Remove(dbPath + "-wal")
		_ = os.Remove(dbPath + "-shm")
	}
}

// insertCoverageBoostOpenclawTask inserts an openclaw-domain task with no lane
// into the given DB for coverage-boost tests.
func insertCoverageBoostOpenclawTask(t *testing.T, db *sql.DB, id, taskType string) {
	t.Helper()
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, created_at, updated_at)
		VALUES (?, 'openclaw', 'test-project', ?, ?, 50, 'queued', 'QUEUED', ?, ?)
	`, id, taskType, "Test openclaw task "+id, now, now)
	if err != nil {
		t.Fatalf("insertCoverageBoostOpenclawTask(%s): %v", id, err)
	}
}

// ── orchestratorRouteOpenClaw ─────────────────────────────────────────────────

// TestCoverageBoost_RouteOpenClaw_NoTasks verifies that orchestratorRouteOpenClaw
// returns nil without error when there are no openclaw tasks without a lane.
func TestCoverageBoost_RouteOpenClaw_NoTasks(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw (no tasks): %v", err)
	}
}

// TestCoverageBoost_RouteOpenClaw_AssignsLane inserts an openclaw task with
// NULL lane and verifies orchestratorRouteOpenClaw assigns a non-empty lane.
func TestCoverageBoost_RouteOpenClaw_AssignsLane(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	insertCoverageBoostOpenclawTask(t, db, "covboost-oc-1", "feature")

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw: %v", err)
	}

	var lane sql.NullString
	if err := db.QueryRow(`SELECT lane FROM tasks WHERE id = 'covboost-oc-1'`).Scan(&lane); err != nil {
		t.Fatalf("query lane: %v", err)
	}
	if !lane.Valid || lane.String == "" {
		t.Errorf("expected non-empty lane for openclaw task, got %v", lane)
	}
}

// TestCoverageBoost_RouteOpenClaw_MultipleTasks inserts 3 openclaw tasks with
// NULL lanes and verifies all receive lane assignments.
func TestCoverageBoost_RouteOpenClaw_MultipleTasks(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	ids := []string{"covboost-oc-m1", "covboost-oc-m2", "covboost-oc-m3"}
	types := []string{"feature", "bug", "research"}
	for i, id := range ids {
		insertCoverageBoostOpenclawTask(t, db, id, types[i])
	}

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw (multiple): %v", err)
	}

	for _, id := range ids {
		var lane sql.NullString
		if err := db.QueryRow(`SELECT lane FROM tasks WHERE id = ?`, id).Scan(&lane); err != nil {
			t.Fatalf("query lane for %s: %v", id, err)
		}
		if !lane.Valid || lane.String == "" {
			t.Errorf("task %s: expected lane to be set, got %v", id, lane)
		}
	}
}

// TestCoverageBoost_RouteOpenClaw_SkipsAlreadyLaned verifies that tasks with
// an existing lane are not overwritten.
func TestCoverageBoost_RouteOpenClaw_SkipsAlreadyLaned(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, title, priority, status, state, lane, created_at, updated_at)
		VALUES ('covboost-oc-laned', 'openclaw', 'test-project', 'feature', 'Already laned', 50, 'queued', 'QUEUED', 'implementation', ?, ?)
	`, now, now)
	if err != nil {
		t.Fatalf("insert laned task: %v", err)
	}

	if err := orchestratorRouteOpenClaw(context.Background(), db); err != nil {
		t.Fatalf("orchestratorRouteOpenClaw: %v", err)
	}

	var lane string
	if err := db.QueryRow(`SELECT lane FROM tasks WHERE id = 'covboost-oc-laned'`).Scan(&lane); err != nil {
		t.Fatalf("query lane: %v", err)
	}
	if lane != "implementation" {
		t.Errorf("expected lane='implementation' (unchanged), got %q", lane)
	}
}

// ── orchestratorAutoDispatch ──────────────────────────────────────────────────

// TestCoverageBoost_AutoDispatch_NilDB verifies orchestratorAutoDispatch returns
// nil without error when called with a nil DB (early guard).
func TestCoverageBoost_AutoDispatch_NilDB(t *testing.T) {
	if err := orchestratorAutoDispatch(context.Background(), nil); err != nil {
		t.Fatalf("orchestratorAutoDispatch(nil): expected nil, got %v", err)
	}
}

// TestCoverageBoost_AutoDispatch_MultipleSubErrors exercises the error
// accumulation path by making orchestratorHeartbeat produce an error (pointing
// the NODE_ID at a non-existent row so the UPDATE affects 0 rows — which is
// not an error — but the DB query itself succeeds). We instead verify it runs
// without panicking with a healthy DB to increase statement coverage.
func TestCoverageBoost_AutoDispatch_HealthyDB(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	// Insert the orchestrator heartbeat row so orchestratorHeartbeat can update it.
	nodeID := "covboost-orchestrator"
	t.Setenv("NODE_ID", nodeID)
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen)
		VALUES (?, 'test-node', 'online', ?)
	`, nodeID, now)
	if err != nil {
		t.Fatalf("insert heartbeat: %v", err)
	}

	// Should complete without error: heartbeat updated, no queued tasks, no
	// stale tasks, no openclaw tasks → all four sub-functions succeed.
	if err := orchestratorAutoDispatch(context.Background(), db); err != nil {
		t.Fatalf("orchestratorAutoDispatch: %v", err)
	}
}

// TestCoverageBoost_AutoDispatch_WithOpenclawTasks exercises the full
// orchestratorAutoDispatch path including the openclaw routing step.
func TestCoverageBoost_AutoDispatch_WithOpenclawTasks(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	nodeID := "covboost-orch-full"
	t.Setenv("NODE_ID", nodeID)
	now := time.Now().UTC().Format(time.RFC3339)

	// Insert orchestrator heartbeat row.
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen)
		VALUES (?, 'test-node', 'online', ?)
	`, nodeID, now)
	if err != nil {
		t.Fatalf("insert orchestrator heartbeat: %v", err)
	}

	// Insert an openclaw task with no lane.
	insertCoverageBoostOpenclawTask(t, db, "covboost-ad-oc-1", "feature")

	if err := orchestratorAutoDispatch(context.Background(), db); err != nil {
		t.Fatalf("orchestratorAutoDispatch with openclaw task: %v", err)
	}

	// The openclaw task should now have a lane assigned.
	var lane sql.NullString
	if err := db.QueryRow(`SELECT lane FROM tasks WHERE id = 'covboost-ad-oc-1'`).Scan(&lane); err != nil {
		t.Fatalf("query lane: %v", err)
	}
	if !lane.Valid || lane.String == "" {
		t.Errorf("expected lane to be assigned after auto dispatch, got %v", lane)
	}
}

// ── pickMostSpecificByName ────────────────────────────────────────────────────

// TestCoverageBoost_PickMostSpecificByName_SingleEntry verifies that with one
// entry the function returns it.
func TestCoverageBoost_PickMostSpecificByName_SingleEntry(t *testing.T) {
	got := pickMostSpecificByName([]string{"builder"}, []int{2})
	if got != "builder" {
		t.Errorf("single entry: got %q, want %q", got, "builder")
	}
}

// TestCoverageBoost_PickMostSpecificByName_PrefersLowestCount verifies that the
// role with the smallest stageCount wins.
func TestCoverageBoost_PickMostSpecificByName_PrefersLowestCount(t *testing.T) {
	names := []string{"deploy", "builder", "qa"}
	counts := []int{1, 2, 2}
	got := pickMostSpecificByName(names, counts)
	if got != "deploy" {
		t.Errorf("lowest count: got %q, want %q", got, "deploy")
	}
}

// TestCoverageBoost_PickMostSpecificByName_TieBreakAlpha verifies that among
// equal-count roles the alphabetically earlier name wins.
func TestCoverageBoost_PickMostSpecificByName_TieBreakAlpha(t *testing.T) {
	names := []string{"zebra-role", "alpha-role", "mango-role"}
	counts := []int{1, 1, 1}
	got := pickMostSpecificByName(names, counts)
	if got != "alpha-role" {
		t.Errorf("tie-break alpha: got %q, want %q", got, "alpha-role")
	}
}

// TestCoverageBoost_PickMostSpecificByName_MixedCounts verifies picking with
// varying counts across 4 entries.
func TestCoverageBoost_PickMostSpecificByName_MixedCounts(t *testing.T) {
	names := []string{"a", "b", "c", "d"}
	counts := []int{3, 1, 2, 1}
	// b and d both have count 1; alphabetically b < d → b wins.
	got := pickMostSpecificByName(names, counts)
	if got != "b" {
		t.Errorf("mixed counts: got %q, want %q", got, "b")
	}
}

// TestCoverageBoost_PickMostSpecificByName_AllSameCount verifies stable
// alphabetical ordering when all counts are the same.
func TestCoverageBoost_PickMostSpecificByName_AllSameCount(t *testing.T) {
	names := []string{"worker", "intake", "builder"}
	counts := []int{5, 5, 5}
	got := pickMostSpecificByName(names, counts)
	if got != "builder" {
		t.Errorf("all same count: got %q, want %q", got, "builder")
	}
}

// ── messageRelayPatrol ────────────────────────────────────────────────────────

// setupCoverageBoostRelayDB returns an in-memory SQLite DB with the minimal
// tables needed by messageRelayPatrol and a temp FORGE_ROOT.
func setupCoverageBoostRelayDB(t *testing.T) (*sql.DB, string) {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open in-memory relay db: %v", err)
	}
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS tasks (
			id TEXT PRIMARY KEY,
			domain TEXT NOT NULL DEFAULT '',
			project TEXT NOT NULL DEFAULT '',
			type TEXT NOT NULL DEFAULT 'research',
			title TEXT NOT NULL DEFAULT '',
			description TEXT DEFAULT '',
			priority INTEGER DEFAULT 5,
			status TEXT NOT NULL DEFAULT 'queued',
			state TEXT NOT NULL DEFAULT 'QUEUED',
			lane TEXT,
			assigned_to TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
		CREATE TABLE IF NOT EXISTS task_events (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			task_id TEXT NOT NULL,
			domain TEXT DEFAULT '',
			project TEXT DEFAULT '',
			event_type TEXT NOT NULL,
			payload TEXT DEFAULT '{}',
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
		CREATE TABLE IF NOT EXISTS nodes (
			id TEXT PRIMARY KEY,
			hostname TEXT NOT NULL DEFAULT '',
			address TEXT NOT NULL DEFAULT '',
			status TEXT NOT NULL DEFAULT 'offline',
			last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
	`)
	if err != nil {
		t.Fatalf("create relay tables: %v", err)
	}

	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)
	return db, tmpDir
}

// TestCoverageBoost_MessageRelayPatrol_EmptyInbox verifies that
// messageRelayPatrol returns nil when the inbox has no messages.
func TestCoverageBoost_MessageRelayPatrol_EmptyInbox(t *testing.T) {
	db, _ := setupCoverageBoostRelayDB(t)
	defer db.Close()

	if err := messageRelayPatrol(context.Background(), db); err != nil {
		t.Fatalf("messageRelayPatrol (empty inbox): %v", err)
	}
}

// TestCoverageBoost_MessageRelayPatrol_DirectiveMessage verifies that a
// directive-type message is processed without error and results in a
// task_event entry being recorded.
func TestCoverageBoost_MessageRelayPatrol_DirectiveMessage(t *testing.T) {
	db, tmpDir := setupCoverageBoostRelayDB(t)
	defer db.Close()

	inDir := filepath.Join(tmpDir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatalf("mkdir inbox: %v", err)
	}

	msg := Message{
		ID:        "covboost-dir-001",
		From:      "sati",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MsgTypeDirective,
		Subject:   "Run coverage boost",
		Body:      "Execute patrol now.",
		Ack:       false,
	}
	path := filepath.Join(inDir, "sati.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatalf("write inbox: %v", err)
	}

	if err := messageRelayPatrol(context.Background(), db); err != nil {
		t.Fatalf("messageRelayPatrol (directive): %v", err)
	}

	// A task_event should exist for the processed message.
	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM task_events WHERE task_id = 'covboost-dir-001'`).Scan(&count); err != nil {
		t.Fatalf("query task_events: %v", err)
	}
	if count == 0 {
		t.Error("expected task_event to be recorded for directive message")
	}
}

// TestCoverageBoost_MessageRelayPatrol_StatusMessage verifies that a status
// message causes an attempt to update the nodes table (no error even if row
// is absent because UPDATE on missing row is not an error in SQLite).
func TestCoverageBoost_MessageRelayPatrol_StatusMessage(t *testing.T) {
	db, tmpDir := setupCoverageBoostRelayDB(t)
	defer db.Close()

	inDir := filepath.Join(tmpDir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatalf("mkdir inbox: %v", err)
	}

	msg := Message{
		ID:        "covboost-status-001",
		From:      "nova",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MsgTypeStatus,
		Subject:   "node status ping",
		Body:      "",
		Ack:       false,
	}
	path := filepath.Join(inDir, "nova.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatalf("write inbox: %v", err)
	}

	// Should not return an error even though the node row doesn't exist.
	if err := messageRelayPatrol(context.Background(), db); err != nil {
		t.Fatalf("messageRelayPatrol (status): %v", err)
	}
}

// TestCoverageBoost_MessageRelayPatrol_ResearchMessage verifies that a research
// message is processed and persisted as a task_event.
func TestCoverageBoost_MessageRelayPatrol_ResearchMessage(t *testing.T) {
	db, tmpDir := setupCoverageBoostRelayDB(t)
	defer db.Close()

	inDir := filepath.Join(tmpDir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatalf("mkdir inbox: %v", err)
	}

	msg := Message{
		ID:        "covboost-research-001",
		From:      "gemini",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MsgTypeResearch,
		Subject:   "Research findings",
		Body:      "Discovered something useful.",
		Ack:       false,
	}
	path := filepath.Join(inDir, "gemini.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatalf("write inbox: %v", err)
	}

	if err := messageRelayPatrol(context.Background(), db); err != nil {
		t.Fatalf("messageRelayPatrol (research): %v", err)
	}

	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM task_events WHERE task_id = 'covboost-research-001'`).Scan(&count); err != nil {
		t.Fatalf("query task_events: %v", err)
	}
	if count == 0 {
		t.Error("expected task_event recorded for research message")
	}
}

// TestCoverageBoost_MessageRelayPatrol_AckMessage verifies that an ack-type
// message is processed (logged) without error.
func TestCoverageBoost_MessageRelayPatrol_AckMessage(t *testing.T) {
	db, tmpDir := setupCoverageBoostRelayDB(t)
	defer db.Close()

	inDir := filepath.Join(tmpDir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatalf("mkdir inbox: %v", err)
	}

	msg := Message{
		ID:        "covboost-ack-001",
		From:      "kimi",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MsgTypeAck,
		Subject:   "ack:some-task",
		Body:      `{"acked_id":"xyz"}`,
		Ack:       false,
	}
	path := filepath.Join(inDir, "kimi.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatalf("write inbox: %v", err)
	}

	if err := messageRelayPatrol(context.Background(), db); err != nil {
		t.Fatalf("messageRelayPatrol (ack): %v", err)
	}
}

// TestCoverageBoost_MessageRelayPatrol_UnknownType verifies that an unknown
// message type is skipped gracefully without error.
func TestCoverageBoost_MessageRelayPatrol_UnknownType(t *testing.T) {
	db, tmpDir := setupCoverageBoostRelayDB(t)
	defer db.Close()

	inDir := filepath.Join(tmpDir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatalf("mkdir inbox: %v", err)
	}

	msg := Message{
		ID:        "covboost-unknown-001",
		From:      "someagent",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MessageType("unsupported-type"),
		Subject:   "mystery message",
		Body:      "",
		Ack:       false,
	}
	path := filepath.Join(inDir, "someagent.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatalf("write inbox: %v", err)
	}

	if err := messageRelayPatrol(context.Background(), db); err != nil {
		t.Fatalf("messageRelayPatrol (unknown type): %v", err)
	}
}

// TestCoverageBoost_MessageRelayPatrol_DuplicateSkipped verifies that a
// message whose ID is already in task_events is skipped (dedup path).
func TestCoverageBoost_MessageRelayPatrol_DuplicateSkipped(t *testing.T) {
	db, tmpDir := setupCoverageBoostRelayDB(t)
	defer db.Close()

	seenID := "covboost-dup-001"

	// Pre-insert as seen.
	_, err := db.Exec(`
		INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
		VALUES (?, 'infrastructure', 'cross-node', 'relay_message_task', '{}', CURRENT_TIMESTAMP)
	`, seenID)
	if err != nil {
		t.Fatalf("pre-insert seen event: %v", err)
	}

	inDir := filepath.Join(tmpDir, ".forge", "messages", "inbox")
	if err := os.MkdirAll(inDir, 0755); err != nil {
		t.Fatalf("mkdir inbox: %v", err)
	}

	msg := Message{
		ID:        seenID,
		From:      "sati",
		To:        "prya",
		Timestamp: time.Now(),
		Type:      MsgTypeTask,
		Subject:   "duplicate task",
		Body:      "",
		Ack:       false,
	}
	path := filepath.Join(inDir, "sati.jsonl")
	line, _ := json.Marshal(msg)
	if err := os.WriteFile(path, []byte(string(line)+"\n"), 0644); err != nil {
		t.Fatalf("write inbox: %v", err)
	}

	if err := messageRelayPatrol(context.Background(), db); err != nil {
		t.Fatalf("messageRelayPatrol (duplicate): %v", err)
	}

	// task count should be 0 — the duplicate was skipped, not re-processed.
	var taskCount int
	if err := db.QueryRow(`SELECT COUNT(*) FROM tasks`).Scan(&taskCount); err != nil {
		t.Fatalf("count tasks: %v", err)
	}
	if taskCount != 0 {
		t.Errorf("expected 0 tasks (duplicate skipped), got %d", taskCount)
	}
}

// ── agentTTLCleanup ───────────────────────────────────────────────────────────

// TestCoverageBoost_AgentTTLCleanup_NoStaleAgents verifies the patrol returns
// nil without modifying anything when there are no offline stale agents.
func TestCoverageBoost_AgentTTLCleanup_NoStaleAgents(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	// Insert an online agent — should NOT be cleaned up.
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen)
		VALUES ('covboost-ttl-online', 'test-node', 'online', ?)
	`, now)
	if err != nil {
		t.Fatalf("insert online agent: %v", err)
	}

	if err := agentTTLCleanup(context.Background(), db); err != nil {
		t.Fatalf("agentTTLCleanup (no stale): %v", err)
	}

	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM agent_heartbeats WHERE agent_id = 'covboost-ttl-online'`).Scan(&count); err != nil {
		t.Fatalf("query agent: %v", err)
	}
	if count != 1 {
		t.Error("expected online agent to survive agentTTLCleanup")
	}
}

// TestCoverageBoost_AgentTTLCleanup_DeletesStaleOfflineAgents verifies that
// offline agents with last_seen older than 24 hours are deleted.
// Uses SQLite datetime() to insert the stale timestamp so it matches the
// datetime('now', '-24 hours') comparison in agentTTLCleanup.
func TestCoverageBoost_AgentTTLCleanup_DeletesStaleOfflineAgents(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	// Insert an offline agent last seen 25 hours ago using SQLite datetime()
	// to guarantee the format matches agentTTLCleanup's WHERE predicate.
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen)
		VALUES ('covboost-ttl-stale', 'test-node', 'offline', datetime('now', '-25 hours'))
	`)
	if err != nil {
		t.Fatalf("insert stale offline agent: %v", err)
	}

	if err := agentTTLCleanup(context.Background(), db); err != nil {
		t.Fatalf("agentTTLCleanup (stale): %v", err)
	}

	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM agent_heartbeats WHERE agent_id = 'covboost-ttl-stale'`).Scan(&count); err != nil {
		t.Fatalf("query agent: %v", err)
	}
	if count != 0 {
		t.Errorf("expected stale offline agent to be deleted, got count=%d", count)
	}
}

// TestCoverageBoost_AgentTTLCleanup_PreservesRecentOfflineAgents verifies that
// offline agents last seen within 24 hours are NOT deleted.
func TestCoverageBoost_AgentTTLCleanup_PreservesRecentOfflineAgents(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	// Agent went offline only 2 hours ago — within the 24h window.
	recentTime := time.Now().Add(-2 * time.Hour).UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen)
		VALUES ('covboost-ttl-recent-offline', 'test-node', 'offline', ?)
	`, recentTime)
	if err != nil {
		t.Fatalf("insert recent offline agent: %v", err)
	}

	if err := agentTTLCleanup(context.Background(), db); err != nil {
		t.Fatalf("agentTTLCleanup (recent offline): %v", err)
	}

	var count int
	if err := db.QueryRow(`SELECT COUNT(*) FROM agent_heartbeats WHERE agent_id = 'covboost-ttl-recent-offline'`).Scan(&count); err != nil {
		t.Fatalf("query agent: %v", err)
	}
	if count != 1 {
		t.Errorf("expected recently-offline agent to survive TTL cleanup, got count=%d", count)
	}
}

// TestCoverageBoost_AgentTTLCleanup_MultipleAgentsMixed verifies that with a
// mix of online, recent-offline, and stale-offline agents only the stale
// offline ones are deleted.
// Uses SQLite datetime() literals for the stale agent to avoid the RFC3339
// vs YYYY-MM-DD HH:MM:SS format mismatch with agentTTLCleanup's SQL predicate.
func TestCoverageBoost_AgentTTLCleanup_MultipleAgentsMixed(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	// Insert online and recent-offline agents using Go RFC3339 strings.
	now := time.Now().UTC().Format(time.RFC3339)
	recentOffline := time.Now().Add(-1 * time.Hour).UTC().Format(time.RFC3339)
	_, err := db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen) VALUES
		('cb-ttl-online', 'n', 'online', ?),
		('cb-ttl-recent', 'n', 'offline', ?)
	`, now, recentOffline)
	if err != nil {
		t.Fatalf("insert online/recent agents: %v", err)
	}

	// Insert stale-offline agent using SQLite datetime() to ensure the string
	// format matches what agentTTLCleanup's WHERE clause produces.
	_, err = db.Exec(`
		INSERT INTO agent_heartbeats (agent_id, node, status, last_seen)
		VALUES ('cb-ttl-stale', 'n', 'offline', datetime('now', '-30 hours'))
	`)
	if err != nil {
		t.Fatalf("insert stale agent: %v", err)
	}

	if err := agentTTLCleanup(context.Background(), db); err != nil {
		t.Fatalf("agentTTLCleanup (mixed): %v", err)
	}

	for _, tc := range []struct {
		id          string
		wantSurvive bool
	}{
		{"cb-ttl-online", true},
		{"cb-ttl-recent", true},
		{"cb-ttl-stale", false},
	} {
		var count int
		if err := db.QueryRow(`SELECT COUNT(*) FROM agent_heartbeats WHERE agent_id = ?`, tc.id).Scan(&count); err != nil {
			t.Fatalf("query %s: %v", tc.id, err)
		}
		survived := count == 1
		if survived != tc.wantSurvive {
			t.Errorf("agent %s: survived=%v, want %v", tc.id, survived, tc.wantSurvive)
		}
	}
}

// ── blueprintsHandler ─────────────────────────────────────────────────────────

// TestCoverageBoost_BlueprintsHandler_GetEmptyDB verifies that GET /api/blueprints
// returns 200 with an empty list when the DB is healthy but no blueprints exist
// on disk.
func TestCoverageBoost_BlueprintsHandler_GetEmptyDB(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	setDBConn(db)
	defer setDBConn(nil)

	// Point FORGE_ROOT at an empty temp dir so ListBlueprints returns [].
	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/blueprints", nil)
	rr := httptest.NewRecorder()

	blueprintsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp struct {
		Blueprints []interface{} `json:"blueprints"`
		Count      int           `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Count != 0 {
		t.Errorf("expected count=0 for empty blueprints dir, got %d", resp.Count)
	}
}

// TestCoverageBoost_BlueprintsHandler_MethodNotAllowed verifies that non-GET
// methods return 405.
func TestCoverageBoost_BlueprintsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodDelete, "/api/blueprints", nil)
	rr := httptest.NewRecorder()

	blueprintsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}

// TestCoverageBoost_BlueprintsHandler_NilDB verifies the handler returns 503
// when the global DB connection is nil.
func TestCoverageBoost_BlueprintsHandler_NilDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/blueprints", nil)
	rr := httptest.NewRecorder()

	blueprintsHandler(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503; body: %s", rr.Code, rr.Body.String())
	}
}

// TestCoverageBoost_BlueprintsHandler_WithBlueprints verifies that the handler
// correctly lists blueprints when YAML files exist on disk.
func TestCoverageBoost_BlueprintsHandler_WithBlueprints(t *testing.T) {
	db, cleanup := setupCoverageBoostDB(t)
	defer cleanup()

	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	blueprintDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir blueprints dir: %v", err)
	}

	blueprintYAML := `id: covboost-bp-1
name: Coverage Boost Blueprint
description: Test blueprint for coverage
steps:
  - name: step1
    type: shell
    command: "echo coverage"
`
	if err := os.WriteFile(filepath.Join(blueprintDir, "covboost-bp-1.yaml"), []byte(blueprintYAML), 0o644); err != nil {
		t.Fatalf("write blueprint yaml: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/blueprints", nil)
	rr := httptest.NewRecorder()

	blueprintsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp struct {
		Blueprints []interface{} `json:"blueprints"`
		Count      int           `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Count < 1 {
		t.Errorf("expected at least 1 blueprint in response, got %d", resp.Count)
	}
	if len(resp.Blueprints) != resp.Count {
		t.Errorf("blueprints len=%d does not match count=%d", len(resp.Blueprints), resp.Count)
	}
}

// TestCoverageBoost_BlueprintsHandler_PutMethodNotAllowed verifies that PUT
// also returns 405.
func TestCoverageBoost_BlueprintsHandler_PutMethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/api/blueprints", strings.NewReader("{}"))
	rr := httptest.NewRecorder()

	blueprintsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want 405", rr.Code)
	}
}
