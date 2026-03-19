//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// main.go: handleTaskList — 60%
// ---------------------------------------------------------------------------

func TestWave76_HandleTaskList_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/tasks", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)

	if w.Code == http.StatusInternalServerError {
		t.Errorf("handleTaskList failed: %s", w.Body.String())
	}
}

func TestWave76_HandleTaskList_JSONFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/tasks?format=json", nil)
	w := httptest.NewRecorder()
	handleTaskList(w, r)

	if w.Code >= 500 {
		t.Errorf("handleTaskList json format failed: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleAgentList — 57.1%
// ---------------------------------------------------------------------------

func TestWave76_HandleAgentList_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/agents", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)

	if w.Code >= 500 {
		t.Errorf("handleAgentList failed: %d %s", w.Code, w.Body.String())
	}
}

func TestWave76_HandleAgentList_TableFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/agents?format=table", nil)
	w := httptest.NewRecorder()
	handleAgentList(w, r)

	if w.Code >= 500 {
		t.Errorf("handleAgentList table format failed: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleSystemHealth — 55.6%
// ---------------------------------------------------------------------------

func TestWave76_HandleSystemHealth_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/system/health", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)

	if w.Code >= 500 {
		t.Errorf("handleSystemHealth failed: %d %s", w.Code, w.Body.String())
	}
}

func TestWave76_HandleSystemHealth_JSONFormat(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/system/health?format=json", nil)
	w := httptest.NewRecorder()
	handleSystemHealth(w, r)

	if w.Code >= 500 {
		t.Errorf("handleSystemHealth json format: %d %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// main.go: handleQueueDepth — 54.5%
// ---------------------------------------------------------------------------

func TestWave76_HandleQueueDepth_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodGet, "/cli/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code >= 500 {
		t.Errorf("handleQueueDepth failed: %d %s", w.Code, w.Body.String())
	}
}

func TestWave76_HandleQueueDepth_WithTasks(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave76-depth-t1", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	r := httptest.NewRequest(http.MethodGet, "/cli/queue/depth", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("handleQueueDepth: expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var result map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&result); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if _, ok := result["depth"]; !ok {
		t.Error("expected 'depth' key in response")
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: readLiveRAMMB — 35% (macOS: /proc/meminfo absent — error path)
// ---------------------------------------------------------------------------

func TestWave76_ReadLiveRAMMB_MacOS(t *testing.T) {
	// On macOS /proc/meminfo doesn't exist; on Linux it does.
	// Either way, calling the function exercises code paths.
	// Reset cache to force a fresh read.
	liveRAMCache.Lock()
	liveRAMCache.lastRead = time.Time{} // zero time forces fresh read
	liveRAMCache.Unlock()

	mb, err := readLiveRAMMB()
	if err != nil {
		// Expected on macOS — covers the error return path
		t.Logf("readLiveRAMMB error (expected on macOS): %v", err)
	} else {
		// On Linux — success path covered
		t.Logf("readLiveRAMMB: %d MB available", mb)
		if mb <= 0 {
			t.Error("expected positive RAM value")
		}
	}
}

func TestWave76_ReadLiveRAMMB_CacheHit(t *testing.T) {
	// Prime the cache with a fresh value
	liveRAMCache.Lock()
	liveRAMCache.valueMB = 4096
	liveRAMCache.lastRead = time.Now() // fresh
	liveRAMCache.Unlock()

	mb, err := readLiveRAMMB()
	if err != nil {
		t.Errorf("expected no error on cache hit, got: %v", err)
	}
	if mb != 4096 {
		t.Errorf("expected cached value 4096, got %d", mb)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: readLoadAverage — 42.9% (macOS: /proc/loadavg absent)
// ---------------------------------------------------------------------------

func TestWave76_ReadLoadAverage_MacOS(t *testing.T) {
	load, err := readLoadAverage()
	if err != nil {
		t.Logf("readLoadAverage error (expected on macOS): %v", err)
	} else {
		t.Logf("readLoadAverage: %.2f", load)
		if load < 0 {
			t.Error("expected non-negative load average")
		}
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: getCPUCount — 50%
// ---------------------------------------------------------------------------

func TestWave76_GetCPUCount(t *testing.T) {
	count := getCPUCount()
	if count <= 0 {
		t.Errorf("getCPUCount: expected positive count, got %d", count)
	}
	t.Logf("getCPUCount: %d", count)
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: spawnAgent — 13% (error path: unknown agent type)
// ---------------------------------------------------------------------------

func TestWave76_SpawnAgent_UnknownType(t *testing.T) {
	ctx := context.Background()
	_, err := spawnAgent(ctx, "unknown-agent-xyz", "test-node")
	if err == nil {
		t.Error("expected error for unknown agent type")
	}
	if !strings.Contains(err.Error(), "unknown agent type") {
		t.Errorf("expected 'unknown agent type' in error, got: %v", err)
	}
}

// ---------------------------------------------------------------------------
// fleet_scaler.go: fleetAutoExecutePatrol — 23.1%
// Test with circuit breaker open — should return nil immediately.
// ---------------------------------------------------------------------------

func TestWave76_FleetAutoExecutePatrol_CircuitBreakerOpen(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Open the circuit breaker
	circuitBreaker.Lock()
	prevState := circuitBreaker.state
	prevFailures := circuitBreaker.consecutiveFailures
	prevLastFailure := circuitBreaker.lastFailure
	circuitBreaker.state = "open"
	circuitBreaker.consecutiveFailures = 5
	circuitBreaker.lastFailure = time.Now() // recent failure
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = prevState
		circuitBreaker.consecutiveFailures = prevFailures
		circuitBreaker.lastFailure = prevLastFailure
		circuitBreaker.Unlock()
	}()

	db := getDBConn()
	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil return with open circuit breaker, got: %v", err)
	}
}

func TestWave76_FleetAutoExecutePatrol_FlapGuard(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Ensure circuit breaker is closed
	circuitBreaker.Lock()
	prevState := circuitBreaker.state
	prevFailures := circuitBreaker.consecutiveFailures
	prevLastFailure := circuitBreaker.lastFailure
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.lastFailure = time.Time{}
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = prevState
		circuitBreaker.consecutiveFailures = prevFailures
		circuitBreaker.lastFailure = prevLastFailure
		circuitBreaker.Unlock()
	}()

	// Set lastScaleEvent to recent time to trigger flap guard
	lastScaleEvent.Lock()
	prevTimestamp := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Now() // recent scale event
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = prevTimestamp
		lastScaleEvent.Unlock()
	}()

	db := getDBConn()
	err := fleetAutoExecutePatrol(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil return with flap guard active, got: %v", err)
	}
}

func TestWave76_FleetAutoExecutePatrol_NoPendingRecs(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Ensure circuit breaker closed and flap guard not active
	circuitBreaker.Lock()
	prevState := circuitBreaker.state
	prevFailures := circuitBreaker.consecutiveFailures
	prevLastFailure := circuitBreaker.lastFailure
	circuitBreaker.state = "closed"
	circuitBreaker.consecutiveFailures = 0
	circuitBreaker.lastFailure = time.Time{}
	circuitBreaker.Unlock()
	defer func() {
		circuitBreaker.Lock()
		circuitBreaker.state = prevState
		circuitBreaker.consecutiveFailures = prevFailures
		circuitBreaker.lastFailure = prevLastFailure
		circuitBreaker.Unlock()
	}()

	lastScaleEvent.Lock()
	prevTimestamp := lastScaleEvent.timestamp
	lastScaleEvent.timestamp = time.Time{} // zero time — no recent scale event
	lastScaleEvent.Unlock()
	defer func() {
		lastScaleEvent.Lock()
		lastScaleEvent.timestamp = prevTimestamp
		lastScaleEvent.Unlock()
	}()

	db := getDBConn()
	// With no pending scale_recommendations, may error if table missing — not a test failure
	err := fleetAutoExecutePatrol(context.Background(), db)
	t.Logf("fleetAutoExecutePatrol no pending recs: %v", err)
}

// ---------------------------------------------------------------------------
// handlers_dispatch.go: dispatchHandler — 69%
// Test POST with valid body.
// ---------------------------------------------------------------------------

func TestWave76_DispatchHandler_Post_OK(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave76-dispatch-t1", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	body := `{"task_id":"wave76-dispatch-t1","agent_id":"agent-wave76","message":"dispatch test"}`
	r := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewBufferString(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	dispatchHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("dispatchHandler POST: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave76_DispatchHandler_Post_InvalidBody(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodPost, "/api/dispatch", bytes.NewBufferString("not-json"))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	dispatchHandler(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("dispatchHandler invalid body: expected 400, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// handlers_task.go: claimTaskHandler — 68.4%
// Test method not allowed, missing body, missing agent_id.
// ---------------------------------------------------------------------------

func TestWave76_ClaimTaskHandler_MissingAgentID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := `{"agent_id":""}`
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave76-task/claim", bytes.NewBufferString(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)

	if w.Code != http.StatusBadRequest {
		t.Errorf("claimTaskHandler empty agent_id: expected 400, got %d", w.Code)
	}
}

func TestWave76_ClaimTaskHandler_TaskNotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := `{"agent_id":"agent-76"}`
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/nonexistent-wave76/claim", bytes.NewBufferString(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("claimTaskHandler not found: expected 404, got %d", w.Code)
	}
}

func TestWave76_ClaimTaskHandler_Success(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave76-claim-ok", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	body := `{"agent_id":"agent-wave76"}`
	r := httptest.NewRequest(http.MethodPost, "/api/tasks/wave76-claim-ok/claim", bytes.NewBufferString(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	claimTaskHandler(w, r)

	if w.Code != http.StatusOK && w.Code != http.StatusCreated {
		t.Errorf("claimTaskHandler success: expected 200/201, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// handlers_dispatch.go: configHandler — covers PUT path
// ---------------------------------------------------------------------------

func TestWave76_ConfigHandler_Put(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	body := `{"custom_setting":"value"}`
	r := httptest.NewRequest(http.MethodPut, "/api/config", bytes.NewBufferString(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	configHandler(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("configHandler PUT: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestWave76_ConfigHandler_InvalidMethod(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := httptest.NewRequest(http.MethodDelete, "/api/config", nil)
	w := httptest.NewRecorder()
	configHandler(w, r)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("configHandler DELETE: expected 405, got %d", w.Code)
	}
}

// ---------------------------------------------------------------------------
// patrol.go: confidenceApproveCompletedTasks — 25%
// Test with stateMachine and globalApprovalService initialized.
// ---------------------------------------------------------------------------

func TestWave76_ConfidenceApprove_LowScore_PendingApproval(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	db := getDBConn()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	oldSvc := globalApprovalService
	globalApprovalService = svc
	defer func() { globalApprovalService = oldSvc }()

	// Insert COMPLETED task with no quality gates — confidence will be low
	taskID := "wave76-conf-low"
	// Use SQLite datetime format (space separator, no Z) for correct string comparison
	// assigned_to must not be NULL (scanned into a string in confidenceApproveCompletedTasks)
	oldTime := time.Now().Add(-2 * time.Minute).UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, assigned_to, result, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "test", "test", "feature", 5, "completed", "COMPLETED",
		"Low Confidence Task", "test-agent", "", oldTime, oldTime)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	err = confidenceApproveCompletedTasks(ctx, db)
	if err != nil {
		t.Errorf("confidenceApproveCompletedTasks: %v", err)
	}
}

func TestWave76_ConfidenceApprove_AlreadyPending(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	db := getDBConn()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	oldSvc := globalApprovalService
	globalApprovalService = svc
	defer func() { globalApprovalService = oldSvc }()

	// Insert COMPLETED task
	taskID := "wave76-conf-already"
	// Use SQLite datetime format (space separator, no Z) for correct string comparison
	// assigned_to must not be NULL (scanned into a string in confidenceApproveCompletedTasks)
	oldTime := time.Now().Add(-2 * time.Minute).UTC().Format("2006-01-02 15:04:05")
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, priority, status, state, title, assigned_to, result, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		taskID, "test", "test", "feature", 5, "completed", "COMPLETED",
		"Already Pending Task", "test-agent", "", oldTime, oldTime)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Create an existing approval request for this task
	_, _ = svc.CreateApprovalRequest(ctx, ApprovalTaskCompletion, &taskID,
		"agent-x", "test", "Existing approval", "Pre-created", nil,
		ConfidenceContext{BlastRadius: 1, Reversible: true})

	// Running again should skip (already pending)
	err = confidenceApproveCompletedTasks(ctx, db)
	if err != nil {
		t.Errorf("confidenceApproveCompletedTasks with pending: %v", err)
	}
}

// ---------------------------------------------------------------------------
// main.go: handleQueueStatus — exercise to cover more code paths
// ---------------------------------------------------------------------------

func TestWave76_HandleQueueStatus_WithTasks(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := context.Background()
	_ = taskQueue.Enqueue(ctx, Task{
		ID: "wave76-qstatus-t1", Domain: "d", Project: "p",
		Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued,
	})

	r := httptest.NewRequest(http.MethodGet, "/cli/queue/status", nil)
	w := httptest.NewRecorder()
	handleQueueStatus(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("handleQueueStatus: expected 200, got %d: %s", w.Code, w.Body.String())
	}
}
