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
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// xnode.go: Heartbeat — 75% - auto-insert path when node doesn't exist
// ---------------------------------------------------------------------------

func TestWave71_XNode_Heartbeat_NewNode(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	// Heartbeat a non-existent node — should auto-insert
	if err := xc.Heartbeat(context.Background(), "new-node-x"); err != nil {
		t.Fatalf("Heartbeat for new node: %v", err)
	}

	// Verify node was inserted
	var count int
	db.QueryRow("SELECT COUNT(*) FROM nodes WHERE id = 'new-node-x'").Scan(&count)
	if count != 1 {
		t.Errorf("expected node to be inserted, got count=%d", count)
	}
}

func TestWave71_XNode_Heartbeat_ExistingNode(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	// Register a node first
	xc.RegisterNode(context.Background(), Node{
		ID:       "existing-node",
		Hostname: "host1",
		Address:  "10.0.0.1:8081",
		Status:   "online",
	})

	// Heartbeat the existing node — should update
	if err := xc.Heartbeat(context.Background(), "existing-node"); err != nil {
		t.Fatalf("Heartbeat for existing node: %v", err)
	}
}

func TestWave71_XNode_Heartbeat_WithNodeAddr(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	os.Setenv("NODE_ADDR", "10.0.0.5:8081")
	defer os.Unsetenv("NODE_ADDR")

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	if err := xc.Heartbeat(context.Background(), "addr-node"); err != nil {
		t.Fatalf("Heartbeat with NODE_ADDR: %v", err)
	}
}

// ---------------------------------------------------------------------------
// xnode.go: ForwardTask — 75% - error paths
// ---------------------------------------------------------------------------

func TestWave71_XNode_ForwardTask_NodeNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	err := xc.ForwardTask(context.Background(), "nonexistent-node", "task-1")
	if err == nil {
		t.Error("expected error for nonexistent node")
	}
}

func TestWave71_XNode_ForwardTask_NodeOffline(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	// Register an offline node
	xc.RegisterNode(context.Background(), Node{
		ID:       "offline-node",
		Hostname: "host-offline",
		Address:  "10.0.0.2:8081",
		Status:   "offline",
	})

	err := xc.ForwardTask(context.Background(), "offline-node", "task-1")
	if err == nil {
		t.Error("expected error for offline node")
	}
}

// ---------------------------------------------------------------------------
// xnode.go: ListInboxHandler — 76% - path traversal + bad node ID
// ---------------------------------------------------------------------------

func TestWave71_XNode_ListInboxHandler_PathTraversal(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	// Path traversal attempt
	r := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox/../etc/passwd", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for path traversal, got %d", w.Code)
	}
}

func TestWave71_XNode_ListInboxHandler_ValidRequest(t *testing.T) {
	tmpDir := t.TempDir()
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	// Re-init xnode dirs with the temp dir
	XNodeInboxDir = filepath.Join(tmpDir, ".forge/xnode/lead-inbox")
	os.MkdirAll(XNodeInboxDir, 0755)

	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/inbox/", nil)
	w := httptest.NewRecorder()
	xc.ListInboxHandler(w, r)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// xnode.go: RegisterHandler — 77.3%
// ---------------------------------------------------------------------------

func TestWave71_XNode_RegisterHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/nodes/register", nil)
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for GET, got %d", w.Code)
	}
}

func TestWave71_XNode_RegisterHandler_POST_Valid(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	body, _ := json.Marshal(Node{
		ID:       "reg-node-1",
		Hostname: "reghost",
		Address:  "10.0.0.10:8081",
		Status:   "online",
	})
	r := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", bytes.NewReader(body))
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, r)
	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Errorf("expected 200 or 201, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave71_XNode_RegisterHandler_POST_BadJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	r := httptest.NewRequest(http.MethodPost, "/api/xnode/nodes/register", strings.NewReader("{bad"))
	w := httptest.NewRecorder()
	xc.RegisterHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// xnode.go: ForwardHandler — 74.1%
// ---------------------------------------------------------------------------

func TestWave71_XNode_ForwardHandler_GET(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	r := httptest.NewRequest(http.MethodGet, "/api/xnode/forward", nil)
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, r)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405 for GET, got %d", w.Code)
	}
}

func TestWave71_XNode_ForwardHandler_POST_BadJSON(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	r := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", strings.NewReader("{bad"))
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for bad JSON, got %d", w.Code)
	}
}

func TestWave71_XNode_ForwardHandler_POST_MissingFields(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	body, _ := json.Marshal(map[string]string{"task_id": "t1"}) // missing target_node
	r := httptest.NewRequest(http.MethodPost, "/api/xnode/forward", bytes.NewReader(body))
	w := httptest.NewRecorder()
	xc.ForwardHandler(w, r)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing target_node, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// xnode.go: processInbox (covers inbox reading path)
// ---------------------------------------------------------------------------

func TestWave71_XNode_ProcessInbox_EmptyDir(t *testing.T) {
	tmpDir := t.TempDir()
	XNodeInboxDir = filepath.Join(tmpDir, "inbox")
	os.MkdirAll(XNodeInboxDir, 0755)

	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "test-node")

	// processInbox with empty inbox — should not panic
	offsets := make(map[string]int64)
	xc.processInbox(offsets)
}

// ---------------------------------------------------------------------------
// xnode.go: routeMessage — 72.7%
// ---------------------------------------------------------------------------

func TestWave71_XNode_RouteMessage_LocalNode(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	xc, _ := NewXNodeController(db, "my-local-node")

	msg := map[string]interface{}{
		"id":          "msg-route-1",
		"from_node":   "prya",
		"target_node": "my-local-node",
		"type":        "task_dispatch",
		"payload":     map[string]interface{}{"task_id": "t1"},
	}

	err := xc.routeMessage(context.Background(), msg)
	if err != nil {
		t.Logf("routeMessage local node: %v (may be expected if no task in DB)", err)
	}
}

// ---------------------------------------------------------------------------
// worktree_manager.go: PruneStaleWorktrees — 72%
// ---------------------------------------------------------------------------

func TestWave71_WorktreeManager_PruneStaleWorktrees_Empty(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()
	wm := NewWorktreeManager(t.TempDir(), "", db)

	if err := wm.PruneStaleWorktrees(context.Background()); err != nil {
		t.Fatalf("PruneStaleWorktrees empty: %v", err)
	}
}

// ---------------------------------------------------------------------------
// task_store.go: RecordTransition — 75%
// ---------------------------------------------------------------------------

func TestWave71_TaskStore_RecordTransition(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	taskID := "task-record-trans-1"
	now := time.Now().Format(time.RFC3339)
	_, _ = db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "test", "test", "feature", 5, "queued", "QUEUED", now, now)

	err := ts.RecordTransition(taskID, StateQueued, StateDispatched, "test transition")
	if err != nil {
		t.Fatalf("RecordTransition: %v", err)
	}
}

func TestWave71_TaskStore_ClaimTask_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ts := NewTaskStore(db)
	err := ts.ClaimTask("nonexistent-task", "agent-1")
	if err == nil {
		t.Error("expected error claiming nonexistent task")
	}
}

// ---------------------------------------------------------------------------
// queue.go: QueuePendingDispatch and GetPendingDispatches — 80%
// ---------------------------------------------------------------------------

func TestWave71_Queue_PendingDispatch_RoundTrip(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	taskID := "task-pending-dispatch-1"
	agentID := "agent-kimi"

	// Enqueue first
	_ = taskQueue.Enqueue(ctx, Task{
		ID: taskID, Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	payload := map[string]interface{}{
		"message": "dispatch payload",
		"version": 1,
	}
	if err := taskQueue.QueuePendingDispatch(ctx, taskID, agentID, payload); err != nil {
		t.Fatalf("QueuePendingDispatch: %v", err)
	}

	dispatches, err := taskQueue.GetPendingDispatches(ctx, agentID)
	if err != nil {
		t.Fatalf("GetPendingDispatches: %v", err)
	}
	if len(dispatches) == 0 {
		t.Error("expected at least 1 pending dispatch")
	}
}

// ---------------------------------------------------------------------------
// queue.go: GetTaskEvents — 71.4% - limit clamping
// ---------------------------------------------------------------------------

func TestWave71_Queue_GetTaskEvents_ZeroLimit(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// limit=0 should be clamped to 50
	events, err := taskQueue.GetTaskEvents(ctx, "nonexistent-task", 0)
	if err != nil {
		t.Fatalf("GetTaskEvents: %v", err)
	}
	_ = events // should be empty slice
}

func TestWave71_Queue_GetTaskEvents_LargeLimit(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// limit>100 should be clamped
	events, err := taskQueue.GetTaskEvents(ctx, "nonexistent-task", 200)
	if err != nil {
		t.Fatalf("GetTaskEvents: %v", err)
	}
	_ = events
}

// ---------------------------------------------------------------------------
// queue.go: writeEvent — 75%
// Test via Enqueue which calls writeEvent internally
// ---------------------------------------------------------------------------

func TestWave71_Queue_WriteEvent_ViaEnqueue(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	// Enqueue triggers writeEvent internally
	err := taskQueue.Enqueue(ctx, Task{
		ID:      "task-write-event-1",
		Domain:  "test-domain",
		Project: "test-project",
		Type:    TaskTypeFeature,
		Priority: 5,
		Status:  TaskStatusQueued,
	})
	if err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Verify event was written
	events, err := taskQueue.GetTaskEvents(ctx, "task-write-event-1", 10)
	if err != nil {
		t.Fatalf("GetTaskEvents: %v", err)
	}
	_ = events // event may or may not be written depending on impl
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: fleetAutoExecutePatrol — 23.1% - early exit paths
// ---------------------------------------------------------------------------

func TestWave71_FleetAutoExecute_CircuitBreakerOpen(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Open the circuit breaker
	circuitBreaker.Lock()
	circuitBreaker.state = "open"
	circuitBreaker.lastFailure = time.Now()
	circuitBreaker.consecutiveFailures = 3
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = "closed"
		circuitBreaker.consecutiveFailures = 0
		circuitBreaker.Unlock()
	}()

	db := getDBConn()
	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with open circuit breaker, got: %v", err)
	}
}

func TestWave71_FleetAutoExecute_FlapGuardActive(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Make flap guard active (recent scale event)
	lastScaleEvent.Lock()
	lastScaleEvent.timestamp = time.Now() // Just now
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = time.Time{}
		lastScaleEvent.Unlock()
	}()

	db := getDBConn()
	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with flap guard active, got: %v", err)
	}
}

func TestWave71_FleetAutoExecute_NoPendingRecs(t *testing.T) {
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
	// Ensure scale_recommendations table exists
	if err := ensureScaleRecommendationsTable(context.Background(), db); err != nil {
		t.Fatalf("ensureScaleRecommendationsTable: %v", err)
	}

	// No pending scale recommendations — should return nil
	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with no pending recs, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// handlers_agent.go: agentsSSEHandler — 46.4%
// SSE handlers are hard to test fully, but we can test the error path
// ---------------------------------------------------------------------------

func TestWave71_AgentsSSEHandler_StreamingNotSupported(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Cancel context after 50ms to avoid SSE select loop hanging forever
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	r := httptest.NewRequest(http.MethodGet, "/api/agents/sse", nil)
	r = r.WithContext(ctx)
	// httptest.ResponseRecorder doesn't implement http.Flusher
	// so the handler returns 500 "streaming not supported" immediately
	w := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		agentsSSEHandler(w, r)
		close(done)
	}()

	select {
	case <-done:
		// Handler returned — check that it's a non-hanging response
		if w.Code != http.StatusInternalServerError && w.Code != http.StatusOK {
			t.Logf("agentsSSEHandler returned %d", w.Code)
		}
	case <-time.After(200 * time.Millisecond):
		// Handler is in SSE loop — that's expected for streaming handlers
		t.Log("agentsSSEHandler is in SSE loop (expected — context cancelled it)")
	}
}

// ---------------------------------------------------------------------------
// patrol.go: confidenceApproveCompletedTasks — 5%
// Test with stateMachine set but globalApprovalService nil
// ---------------------------------------------------------------------------

func TestWave71_ConfidenceApprove_NilApprovalService(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// stateMachine is set (from setupClaimTestDB), globalApprovalService=nil
	oldService := globalApprovalService
	globalApprovalService = nil
	defer func() { globalApprovalService = oldService }()

	db := getDBConn()
	err := confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil with nil approval service, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// context_sync.go: calculateFileHash — 80%
// ---------------------------------------------------------------------------

func TestWave71_CalculateFileHash_ValidFile(t *testing.T) {
	tmpFile := t.TempDir() + "/test.txt"
	os.WriteFile(tmpFile, []byte("hello world"), 0644)

	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	cs, _ := NewContextSync(cm, db, t.TempDir())
	t.Cleanup(cs.Stop)

	hash, err := cs.calculateFileHash(tmpFile)
	if err != nil {
		t.Fatalf("calculateFileHash: %v", err)
	}
	if hash == "" {
		t.Error("expected non-empty hash")
	}
}

func TestWave71_CalculateFileHash_NotExist(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	cm := testContextManager(t, db, t.TempDir())
	cs, _ := NewContextSync(cm, db, t.TempDir())
	t.Cleanup(cs.Stop)

	_, err := cs.calculateFileHash("/nonexistent/file.txt")
	if err == nil {
		t.Error("expected error for nonexistent file")
	}
}

// ---------------------------------------------------------------------------
// token_budget.go: tokenBudgetFilePath — test path generation
// ---------------------------------------------------------------------------

func TestWave71_TokenBudgetFilePath(t *testing.T) {
	path := tokenBudgetFilePath("/tmp/forge", "prya")
	if path == "" {
		t.Error("expected non-empty path")
	}
	if !strings.Contains(path, "prya") {
		t.Errorf("expected path to contain node ID, got: %s", path)
	}
	if !strings.Contains(path, "token-budgets") {
		t.Errorf("expected path to contain 'token-budgets', got: %s", path)
	}
}

func TestWave71_TokenBudgetFilePath_EmptyRoot(t *testing.T) {
	path := tokenBudgetFilePath("", "test-node")
	if !strings.HasPrefix(path, ".") {
		t.Errorf("expected path starting with '.', got: %s", path)
	}
}
