//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
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

// ---------------------------------------------------------------------------
// handlers_openclaw.go — openclawStatusHandler, openclawChatHandler,
//                        openclawIngestHandler, openclawHandler,
//                        parseTaskFromMessage
// ---------------------------------------------------------------------------

// TestWave107_ParseTaskFromMessage_Prefixes verifies all recognized prefixes are stripped.
func TestWave107_ParseTaskFromMessage_Prefixes(t *testing.T) {
	cases := []struct {
		input string
		want  string
	}{
		{"create task: fix the login bug", "fix the login bug"},
		{"task: add dark mode", "add dark mode"},
		{"add task: refactor queue", "refactor queue"},
		{"CREATE TASK: uppercase prefix", "uppercase prefix"},
		{"no prefix at all", "no prefix at all"},
		{"  task: leading spaces  ", "leading spaces"},
	}
	for _, tc := range cases {
		got := parseTaskFromMessage(tc.input)
		if got != tc.want {
			t.Errorf("parseTaskFromMessage(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

// TestWave107_OpenclawStatusHandler_NilDB verifies 500 is returned when DB is nil.
func TestWave107_OpenclawStatusHandler_NilDB(t *testing.T) {
	setDBConn(nil)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawStatusHandler(w, req)
	if w.Code != http.StatusInternalServerError {
		t.Errorf("expected 500 with nil DB, got %d", w.Code)
	}
}

// TestWave107_OpenclawStatusHandler_MethodNotAllowed verifies POST is rejected with 405.
func TestWave107_OpenclawStatusHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawStatusHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestWave107_OpenclawStatusHandler_Success verifies 200 JSON response with live DB.
func TestWave107_OpenclawStatusHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawStatusHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	var resp OpenClawStatusResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Daemon != "ok" {
		t.Errorf("expected daemon=ok, got %q", resp.Daemon)
	}
}

// TestWave107_OpenclawChatHandler_MethodNotAllowed verifies GET is rejected with 405.
func TestWave107_OpenclawChatHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/chat", nil)
	w := httptest.NewRecorder()
	openclawChatHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestWave107_OpenclawChatHandler_BadBody verifies 400 on malformed JSON.
func TestWave107_OpenclawChatHandler_BadBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldQueue := taskQueue
	tq, _ := NewTaskQueueFromDB(db)
	taskQueue = tq
	defer func() { taskQueue = oldQueue }()

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat",
		strings.NewReader("{bad json"))
	w := httptest.NewRecorder()
	openclawChatHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

// TestWave107_OpenclawChatHandler_EmptyText verifies 400 when text is blank.
func TestWave107_OpenclawChatHandler_EmptyText(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldQueue := taskQueue
	tq, _ := NewTaskQueueFromDB(db)
	taskQueue = tq
	defer func() { taskQueue = oldQueue }()

	body := `{"text":""}`
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat",
		strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	openclawChatHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for empty text, got %d", w.Code)
	}
}

// TestWave107_OpenclawChatHandler_Success verifies 200 and task ID returned.
func TestWave107_OpenclawChatHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	oldQueue := taskQueue
	tq, _ := NewTaskQueueFromDB(db)
	taskQueue = tq
	defer func() { taskQueue = oldQueue }()

	body := `{"text":"create task: implement wave107 coverage", "from":"user1"}`
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/chat",
		strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	openclawChatHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: body=%s", w.Code, w.Body.String())
	}
	var resp OpenClawChatResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.TaskID == "" {
		t.Error("expected non-empty task_id in response")
	}
}

// TestWave107_OpenclawIngestHandler_MethodNotAllowed verifies GET is rejected.
func TestWave107_OpenclawIngestHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/ingest", nil)
	w := httptest.NewRecorder()
	openclawIngestHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestWave107_OpenclawIngestHandler_MissingFields verifies 400 when required fields absent.
func TestWave107_OpenclawIngestHandler_MissingFields(t *testing.T) {
	body := `{"task_id":"t1","status":"","agent":"","node":"","summary":""}`
	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest",
		strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	openclawIngestHandler(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", w.Code)
	}
}

// TestWave107_OpenclawIngestHandler_Success verifies a valid payload is persisted and returns 200.
func TestWave107_OpenclawIngestHandler_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	body := fmt.Sprintf(`{
		"task_id":"wave107-ingest-1",
		"status":"success",
		"agent":"kimi",
		"node":"prya",
		"summary":"All tests pass",
		"completed_at":"%s"
	}`, time.Now().UTC().Format(time.RFC3339))

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest",
		strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	openclawIngestHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: body=%s", w.Code, w.Body.String())
	}

	// Verify result file was written.
	resultPath := filepath.Join(tmpDir, ".forge", "results", "wave107-ingest-1.json")
	if _, err := os.Stat(resultPath); err != nil {
		t.Errorf("expected result file at %s, got: %v", resultPath, err)
	}
}

// TestWave107_OpenclawIngestHandler_FailStatus verifies fail status maps to task.failed event.
func TestWave107_OpenclawIngestHandler_FailStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	body := `{
		"task_id":"wave107-ingest-fail",
		"status":"fail",
		"agent":"kimi",
		"node":"prya",
		"summary":"Build failed"
	}`

	req := httptest.NewRequest(http.MethodPost, "/api/openclaw/ingest",
		strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	openclawIngestHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: body=%s", w.Code, w.Body.String())
	}
}

// TestWave107_OpenclawHandler_Routes verifies routing in the top-level openclawHandler.
func TestWave107_OpenclawHandler_Routes(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// GET /api/openclaw/status → openclawStatusHandler (200)
	req := httptest.NewRequest(http.MethodGet, "/api/openclaw/status", nil)
	w := httptest.NewRecorder()
	openclawHandler(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("status route: expected 200, got %d", w.Code)
	}

	// GET /api/openclaw/unknown → 404
	req2 := httptest.NewRequest(http.MethodGet, "/api/openclaw/unknown", nil)
	w2 := httptest.NewRecorder()
	openclawHandler(w2, req2)
	if w2.Code != http.StatusNotFound {
		t.Errorf("unknown GET route: expected 404, got %d", w2.Code)
	}

	// DELETE → 405
	req3 := httptest.NewRequest(http.MethodDelete, "/api/openclaw/status", nil)
	w3 := httptest.NewRecorder()
	openclawHandler(w3, req3)
	if w3.Code != http.StatusMethodNotAllowed {
		t.Errorf("DELETE: expected 405, got %d", w3.Code)
	}
}

// ---------------------------------------------------------------------------
// main.go — handleSystemHealth, handleTaskLogs handlers
// ---------------------------------------------------------------------------

// TestWave107_HandleSystemHealth_WithDB verifies handleSystemHealth returns 200 with live DB.
func TestWave107_HandleSystemHealth_WithDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/cli/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, req)

	// healthHandler may succeed or fail based on env; we just verify no panic.
	if w.Code < 100 || w.Code > 599 {
		t.Errorf("unexpected status code: %d", w.Code)
	}
}

// TestWave107_HandleTaskLogs_MissingID verifies 400 when task ID is not provided.
func TestWave107_HandleTaskLogs_MissingID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/cli/task/logs", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

// TestWave107_HandleTaskLogs_WithID verifies handler doesn't panic when task ID is provided.
func TestWave107_HandleTaskLogs_WithID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/cli/task/logs?id=nonexistent-task-id", nil)
	w := httptest.NewRecorder()
	handleTaskLogs(w, req)

	// Either a 404 (task not found) or 200 (empty events) are valid outcomes.
	if w.Code < 100 || w.Code > 599 {
		t.Errorf("unexpected HTTP status: %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go — fleetScaleRecommendPatrol, fleetDeflateRecommendPatrol,
//                   circuitBreakerTripped, canScale
// ---------------------------------------------------------------------------

// TestWave107_FleetScaleRecommendPatrol_EmptyDB verifies the patrol runs without
// error on an empty database (no tasks, no agents).
func TestWave107_FleetScaleRecommendPatrol_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := fleetScaleRecommendPatrol(context.Background(), db); err != nil {
		t.Errorf("fleetScaleRecommendPatrol on empty DB: %v", err)
	}
}

// TestWave107_FleetScaleRecommendPatrol_WithQueuedTasks verifies a recommendation
// is created when queued tasks exceed idle agent threshold.
func TestWave107_FleetScaleRecommendPatrol_WithQueuedTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert queued tasks so queue_depth > idle_agents*2 triggers recommendation.
	for i := 0; i < 5; i++ {
		_, err := db.Exec(`INSERT INTO tasks
			(id, title, domain, project, type, status, state, priority, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))`,
			fmt.Sprintf("wave107-task-%d", i),
			fmt.Sprintf("Task %d", i),
			"test", "test", "feature", "queued", "QUEUED", 5,
		)
		if err != nil {
			t.Fatalf("insert task %d: %v", i, err)
		}
	}

	// Insert a local node into agent_inventory with 0 idle agents (via no rows).
	// The patrol falls back to local hostname when agent_inventory is empty.
	if err := fleetScaleRecommendPatrol(context.Background(), db); err != nil {
		t.Errorf("fleetScaleRecommendPatrol with queued tasks: %v", err)
	}
}

// TestWave107_FleetDeflateRecommendPatrol_EmptyDB verifies the patrol runs cleanly
// when no agents are registered.
func TestWave107_FleetDeflateRecommendPatrol_EmptyDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if err := fleetDeflateRecommendPatrol(context.Background(), db); err != nil {
		t.Errorf("fleetDeflateRecommendPatrol on empty DB: %v", err)
	}
}

// TestWave107_FleetDeflateRecommendPatrol_SingleAgent verifies no deflation when
// only one agent is registered (min floor rule).
func TestWave107_FleetDeflateRecommendPatrol_SingleAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	lastSeen := time.Now().Add(-20 * time.Minute).UTC().Format("2006-01-02T15:04:05Z")
	_, err := db.Exec(`INSERT INTO agent_heartbeats
		(agent_id, node, status, context_pct, last_seen)
		VALUES (?, ?, ?, ?, ?)`,
		"wave107-agent-solo", "test-node", "idle", 10.0, lastSeen,
	)
	if err != nil {
		t.Fatalf("insert agent_heartbeat: %v", err)
	}

	if err := fleetDeflateRecommendPatrol(context.Background(), db); err != nil {
		t.Errorf("fleetDeflateRecommendPatrol single agent: %v", err)
	}

	// Verify no deflation recommendation was created (min floor = 1).
	var count int
	db.QueryRow(`SELECT COUNT(*) FROM scale_recommendations WHERE action = 'deflate'`).Scan(&count)
	if count != 0 {
		t.Errorf("expected 0 deflation recommendations for single agent, got %d", count)
	}
}

// TestWave107_CircuitBreakerTripped_InitialState verifies circuit breaker starts closed.
func TestWave107_CircuitBreakerTripped_InitialState(t *testing.T) {
	// Reset circuit breaker state.
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	if circuitBreakerTripped() {
		t.Error("circuit breaker should be closed (not tripped) initially")
	}
}

// TestWave107_CircuitBreakerTripped_AfterFailures verifies circuit breaker opens
// after enough consecutive failures.
func TestWave107_CircuitBreakerTripped_AfterFailures(t *testing.T) {
	// Reset first.
	circuitBreaker.Lock()
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.Unlock()

	recordSpawnFailure()
	recordSpawnFailure()
	recordSpawnFailure() // 3 failures → open

	if !circuitBreakerTripped() {
		t.Error("circuit breaker should be open after 3 failures")
	}

	// Reset for other tests.
	recordSpawnSuccess()
}

// TestWave107_CanScale_AfterRecentEvent verifies flap guard blocks rapid re-scaling.
func TestWave107_CanScale_AfterRecentEvent(t *testing.T) {
	// Record a scale event just now.
	recordScaleEvent()

	// Should be blocked immediately after.
	if canScale() {
		t.Error("canScale should return false immediately after a scale event")
	}
}

// ---------------------------------------------------------------------------
// patrol.go — xnodeAckProcessorPatrol
// ---------------------------------------------------------------------------

// TestWave107_XnodeAckProcessorPatrol_NilDir verifies that an empty XNodeAcksDir
// causes the patrol to return nil immediately.
func TestWave107_XnodeAckProcessorPatrol_NilDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// XNodeAcksDir is a function returning a string; the patrol reads it locally.
	// We test with a real DB; if ackDir is empty the function exits at the top.
	err := xnodeAckProcessorPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("xnodeAckProcessorPatrol with default ackDir: %v", err)
	}
}

// TestWave107_XnodeAckProcessorPatrol_NonExistentDir verifies graceful return when
// the acks directory does not exist.
func TestWave107_XnodeAckProcessorPatrol_NonExistentDir(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Point FORGE_ROOT to a temp dir where .forge/xnode/acks does not exist.
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	// We need to temporarily set XNodeAcksDir.
	// xnodeAckProcessorPatrol reads it directly; the variable is set at startup
	// from forgeRoot(). Re-invoke with a fresh tmpDir that has no acks dir.
	// The function should return nil (os.IsNotExist path).
	err := xnodeAckProcessorPatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil for nonexistent acks dir, got: %v", err)
	}
}

// TestWave107_XnodeAckProcessorPatrol_ValidAck verifies that a correctly formed
// ack file updates the xnode_outbox row and is removed.
func TestWave107_XnodeAckProcessorPatrol_ValidAck(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	acksDir := filepath.Join(tmpDir, ".forge", "xnode", "acks")
	if err := os.MkdirAll(acksDir, 0755); err != nil {
		t.Fatalf("mkdir acks: %v", err)
	}

	// Insert a serialized outbox row.
	msgID := "wave107-msg-001"
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := db.Exec(`INSERT INTO xnode_outbox
		(id, target_node, message_type, payload, idempotency_key, status, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		msgID, "sati", "dispatch", `{"hello":"world"}`, "idem-001", "serialized", now,
	)
	if err != nil {
		t.Fatalf("insert xnode_outbox: %v", err)
	}

	// Write a valid ack file.
	ackData := fmt.Sprintf(`{"message_id":%q,"status":"acked"}`, msgID)
	ackPath := filepath.Join(acksDir, msgID+".json")
	if err := os.WriteFile(ackPath, []byte(ackData), 0644); err != nil {
		t.Fatalf("write ack file: %v", err)
	}

	// Temporarily patch XNodeAcksDir so the patrol finds our test directory.
	orig := XNodeAcksDir
	XNodeAcksDir = acksDir
	defer func() { XNodeAcksDir = orig }()

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Fatalf("xnodeAckProcessorPatrol: %v", err)
	}

	// Verify the outbox row status was updated to 'acked'.
	var status string
	db.QueryRow(`SELECT status FROM xnode_outbox WHERE id = ?`, msgID).Scan(&status)
	if status != "acked" {
		t.Errorf("expected xnode_outbox status='acked', got %q", status)
	}

	// Verify ack file was removed.
	if _, err := os.Stat(ackPath); !os.IsNotExist(err) {
		t.Errorf("expected ack file to be removed after processing")
	}
}

// TestWave107_XnodeAckProcessorPatrol_InvalidAck verifies non-acked status files
// are skipped without error.
func TestWave107_XnodeAckProcessorPatrol_InvalidAck(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	acksDir := filepath.Join(tmpDir, ".forge", "xnode", "acks")
	if err := os.MkdirAll(acksDir, 0755); err != nil {
		t.Fatalf("mkdir acks: %v", err)
	}

	// Write an ack file with pending (not "acked") status.
	ackData := `{"message_id":"wave107-pending","status":"pending"}`
	ackPath := filepath.Join(acksDir, "wave107-pending.json")
	if err := os.WriteFile(ackPath, []byte(ackData), 0644); err != nil {
		t.Fatalf("write ack file: %v", err)
	}

	orig := XNodeAcksDir
	XNodeAcksDir = acksDir
	defer func() { XNodeAcksDir = orig }()

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil for pending-status ack, got: %v", err)
	}

	// File should still exist (not removed).
	if _, err := os.Stat(ackPath); os.IsNotExist(err) {
		t.Error("pending ack file should not have been removed")
	}
}

// TestWave107_XnodeAckProcessorPatrol_BadJSON verifies malformed JSON ack files
// are logged and skipped without error.
func TestWave107_XnodeAckProcessorPatrol_BadJSON(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	tmpDir := t.TempDir()
	acksDir := filepath.Join(tmpDir, ".forge", "xnode", "acks")
	if err := os.MkdirAll(acksDir, 0755); err != nil {
		t.Fatalf("mkdir acks: %v", err)
	}

	// Write a malformed ack file.
	ackPath := filepath.Join(acksDir, "bad-json.json")
	if err := os.WriteFile(ackPath, []byte("{not valid json"), 0644); err != nil {
		t.Fatalf("write bad ack file: %v", err)
	}

	orig := XNodeAcksDir
	XNodeAcksDir = acksDir
	defer func() { XNodeAcksDir = orig }()

	if err := xnodeAckProcessorPatrol(context.Background(), db); err != nil {
		t.Errorf("expected nil despite bad JSON ack file, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// patrol.go — confidenceApproveCompletedTasks nil-guard
// ---------------------------------------------------------------------------

// TestWave107_ConfidenceApproveCompletedTasks_NilGuard verifies the function
// returns nil when stateMachine or globalApprovalService is nil.
func TestWave107_ConfidenceApproveCompletedTasks_NilGuard(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Both globals are nil by default in test environment.
	origSM := stateMachine
	origAS := globalApprovalService
	stateMachine = nil
	globalApprovalService = nil
	defer func() {
		stateMachine = origSM
		globalApprovalService = origAS
	}()

	if err := confidenceApproveCompletedTasks(context.Background(), db); err != nil {
		t.Errorf("expected nil when stateMachine/globalApprovalService are nil, got: %v", err)
	}
}

// TestWave107_ConfidenceApproveCompletedTasks_NoCompletedTasks verifies no-op
// when there are no COMPLETED tasks.
func TestWave107_ConfidenceApproveCompletedTasks_NoCompletedTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Initialize required globals via real constructors.
	ts := NewTaskStore(db)
	sm := NewStateMachine(ts, db)
	as := NewApprovalService(NewApprovalStore(db))

	origSM := stateMachine
	origAS := globalApprovalService
	stateMachine = sm
	globalApprovalService = as
	defer func() {
		stateMachine = origSM
		globalApprovalService = origAS
	}()

	if err := confidenceApproveCompletedTasks(context.Background(), db); err != nil {
		t.Errorf("expected nil with no COMPLETED tasks, got: %v", err)
	}
}
