//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// --- nodesHealthHandler ---

func TestNodesHealthHandler_EmptyDB(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/nodes/health", nil)
	rr := httptest.NewRecorder()

	nodesHealthHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := resp["nodes"]; !ok {
		t.Error("response missing 'nodes' key")
	}
	if _, ok := resp["total_nodes"]; !ok {
		t.Error("response missing 'total_nodes' key")
	}
}

func TestNodesHealthHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/nodes/health", nil)
	rr := httptest.NewRecorder()

	nodesHealthHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rr.Code)
	}
}

// --- fleetRecommendationsHandler ---

func TestFleetRecommendationsHandler_EmptyDB(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/recommendations", nil)
	rr := httptest.NewRecorder()

	fleetRecommendationsHandler(rr, req)

	// Either 200 (empty list) or 500 if scale_recommendations table missing.
	if rr.Code != http.StatusOK && rr.Code != http.StatusInternalServerError {
		t.Fatalf("unexpected status = %d; body: %s", rr.Code, rr.Body.String())
	}
}

func TestFleetRecommendationsHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/fleet/recommendations", nil)
	rr := httptest.NewRecorder()

	fleetRecommendationsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rr.Code)
	}
}

// --- patrolExecutionsHandler ---

func TestPatrolExecutionsHandler_EmptyDB(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/patrol-executions", nil)
	rr := httptest.NewRecorder()

	patrolExecutionsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := resp["executions"]; !ok {
		t.Error("response missing 'executions' key")
	}
	if _, ok := resp["count"]; !ok {
		t.Error("response missing 'count' key")
	}
}

func TestPatrolExecutionsHandler_WithQueryParams(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/patrol-executions?hours=6&limit=10&status=completed", nil)
	rr := httptest.NewRecorder()

	patrolExecutionsHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}
}

func TestPatrolExecutionsHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/patrol-executions", nil)
	rr := httptest.NewRecorder()

	patrolExecutionsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rr.Code)
	}
}

// --- dashboardThroughputHandler ---

func TestDashboardThroughputHandler_EmptyDB(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/throughput", nil)
	rr := httptest.NewRecorder()

	dashboardThroughputHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, ok := resp["total_today"]; !ok {
		t.Error("response missing 'total_today' key")
	}
	if _, ok := resp["throughput_per_hour"]; !ok {
		t.Error("response missing 'throughput_per_hour' key")
	}
}

func TestDashboardThroughputHandler_WithHoursParam(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/dashboard/throughput?hours=48", nil)
	rr := httptest.NewRecorder()

	dashboardThroughputHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	// hours param should be 48
	if resp["hours"] != float64(48) {
		t.Errorf("hours = %v, want 48", resp["hours"])
	}
}

func TestDashboardThroughputHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/dashboard/throughput", nil)
	rr := httptest.NewRecorder()

	dashboardThroughputHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rr.Code)
	}
}

// --- lease.go: NewLeaseManager / NewLeaseManagerWithStateMachine ---

func TestNewLeaseManager_ValidPath(t *testing.T) {
	// Use a fresh DB file so we have a clean leases table.
	dbPath := t.TempDir() + "/lease_test.db"

	// First run migrations so leases table exists.
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("MigrateUp: %v", err)
	}
	db.Close()

	lm, err := NewLeaseManager(dbPath)
	if err != nil {
		t.Fatalf("NewLeaseManager: %v", err)
	}
	if lm == nil {
		t.Fatal("NewLeaseManager returned nil")
	}
}

func TestNewLeaseManagerWithStateMachine_ValidPath(t *testing.T) {
	dbPath := t.TempDir() + "/lease_sm_test.db"

	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		t.Fatalf("MigrateUp: %v", err)
	}
	db.Close()

	// Use a real TaskStateMachine with a fresh DB.
	db2, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB 2: %v", err)
	}
	defer db2.Close()

	sm := NewTaskStateMachine(db2)
	lm, err := NewLeaseManagerWithStateMachine(dbPath, sm)
	if err != nil {
		t.Fatalf("NewLeaseManagerWithStateMachine: %v", err)
	}
	if lm == nil {
		t.Fatal("NewLeaseManagerWithStateMachine returned nil")
	}
}

// --- protocol_validator.go: ValidateTaskClaim, GetProtocolVersion ---

func TestProtocolValidatorValidateTaskClaim_Valid(t *testing.T) {
	pv := NewProtocolValidator()

	validClaim := []byte(`{"agent_id":"agent-test-001"}`)
	if err := pv.ValidateTaskClaim(validClaim); err != nil {
		t.Fatalf("ValidateTaskClaim valid input: %v", err)
	}
}

func TestProtocolValidatorValidateTaskClaim_InvalidJSON(t *testing.T) {
	pv := NewProtocolValidator()

	if err := pv.ValidateTaskClaim([]byte(`{not valid json`)); err == nil {
		t.Fatal("ValidateTaskClaim with invalid JSON: expected error, got nil")
	}
}

func TestProtocolValidatorValidateTaskClaim_MissingAgentID(t *testing.T) {
	pv := NewProtocolValidator()

	// Empty JSON object has no agent_id.
	if err := pv.ValidateTaskClaim([]byte(`{}`)); err == nil {
		t.Fatal("ValidateTaskClaim with empty payload: expected error, got nil")
	}
}

func TestGetProtocolVersion_Extra(t *testing.T) {
	version := GetProtocolVersion()
	if version == "" {
		t.Error("GetProtocolVersion returned empty string")
	}
}

// --- protocol_validator.go: ValidationError.Error() ---

func TestValidationErrorError(t *testing.T) {
	ve := ValidationError{
		Field:  "agent_id",
		Reason: "required field missing",
		Code:   "REQUIRED",
	}
	msg := ve.Error()
	if msg == "" {
		t.Fatal("ValidationError.Error() returned empty string")
	}
}

// --- metrics.go: RecordTaskState ---

func TestMetricsRecordTaskState(t *testing.T) {
	// RecordTaskState is a method on *Metrics — just verify it doesn't panic.
	m := &Metrics{}
	defer func() {
		if r := recover(); r != nil {
			t.Errorf("RecordTaskState panicked: %v", r)
		}
	}()
	m.RecordTaskState("QUEUED", 1)
}

// --- withDB helper ---

func TestWithDB_NilDB(t *testing.T) {
	// Save and clear dbConn so withDB sees nil.
	old := getDBConn()
	setDBConn(nil)
	defer setDBConn(old)

	rr := httptest.NewRecorder()
	called := false
	withDB(rr, func(_ *sql.DB) {
		called = true
	})

	if called {
		t.Error("withDB callback should not have been called when db is nil")
	}
	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("withDB nil db: status = %d, want 503", rr.Code)
	}
}

func TestWithDB_CallsCallbackWhenAvailable(t *testing.T) {
	defer setupCoverageQueue(t)()

	rr := httptest.NewRecorder()
	called := false
	withDB(rr, func(db *sql.DB) {
		called = true
	})

	if !called {
		t.Error("withDB callback was not called when db is available")
	}
}

// --- agentTelemetryHandler ---

func TestAgentTelemetryHandler_EmptyResult(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/agents/agent-xyz/telemetry", nil)
	rr := httptest.NewRecorder()

	agentTelemetryHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("agentTelemetryHandler status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}
}

func TestAgentTelemetryHandler_MethodNotAllowed_Extra(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/agents/agent-xyz/telemetry", nil)
	rr := httptest.NewRecorder()

	agentTelemetryHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("agentTelemetryHandler POST: status = %d, want 405", rr.Code)
	}
}

// --- nodeCapabilitiesHandler ---

func TestNodeCapabilitiesHandler_OK(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/node/capabilities", nil)
	rr := httptest.NewRecorder()

	nodeCapabilitiesHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("nodeCapabilitiesHandler status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}
}

func TestNodeCapabilitiesHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/node/capabilities", nil)
	rr := httptest.NewRecorder()

	nodeCapabilitiesHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("nodeCapabilitiesHandler POST: status = %d, want 405", rr.Code)
	}
}

// --- completeTaskWithApprovalHandler ---

func TestCompleteTaskWithApprovalHandler_MethodNotAllowed_Extra(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/tasks/task-001/complete-with-approval", nil)
	rr := httptest.NewRecorder()

	completeTaskWithApprovalHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("completeTaskWithApprovalHandler GET: status = %d, want 405", rr.Code)
	}
}

// --- GitGuard: ReleaseBranchLock, GetBranchLock ---

func TestGitGuardReleaseBranchLock(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, ".")
	ctx := context.Background()

	// Release a lock that doesn't exist — must not error.
	if err := gg.ReleaseBranchLock(ctx, "no-such-task"); err != nil {
		t.Fatalf("ReleaseBranchLock (nonexistent): %v", err)
	}
}

func TestGitGuardGetBranchLock_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	gg := NewGitGuard(db, ".")
	ctx := context.Background()

	branch, err := gg.GetBranchLock(ctx, "no-such-task")
	if err != nil {
		t.Fatalf("GetBranchLock (nonexistent): %v", err)
	}
	if branch != "" {
		t.Errorf("GetBranchLock (nonexistent) = %q, want empty string", branch)
	}
}

// --- fleetSummaryHandler ---

func TestFleetSummaryHandler_EmptyDB(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	rr := httptest.NewRecorder()
	fleetSummaryHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var envelope struct {
		Success bool           `json:"success"`
		Data    PWAFleetSummary `json:"data"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&envelope); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !envelope.Success {
		t.Error("expected success=true")
	}
	// Empty DB — task counts should be zero.
	if envelope.Data.TaskSummary.Total != 0 {
		t.Errorf("unexpected non-zero task count in empty DB: %+v", envelope.Data.TaskSummary)
	}
}

func TestFleetSummaryHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	req := httptest.NewRequest(http.MethodPost, "/api/fleet/summary", nil)
	rr := httptest.NewRecorder()
	fleetSummaryHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rr.Code)
	}
}

func TestFleetSummaryHandler_WithAgentsAndTasks(t *testing.T) {
	defer setupCoverageQueue(t)()

	db := getDBConn()

	// Insert a queued task (priority 5 = p1 bucket).
	_, err := db.Exec(`
		INSERT INTO tasks (id, title, status, priority, domain, project, type, created_at, updated_at)
		VALUES ('task-fleet-1', 'test task', 'queued', 5, 'test', 'proj', 'feature', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/fleet/summary", nil)
	rr := httptest.NewRecorder()
	fleetSummaryHandler(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var envelope struct {
		Success bool           `json:"success"`
		Data    PWAFleetSummary `json:"data"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&envelope); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !envelope.Success {
		t.Error("expected success=true")
	}
	if envelope.Data.TaskSummary.Total != 1 {
		t.Errorf("task_summary.total = %d, want 1", envelope.Data.TaskSummary.Total)
	}
}

// --- approvals count endpoint ---

func TestApprovalCountHandler_Empty(t *testing.T) {
	defer setupCoverageQueue(t)()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	handler := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/count", nil)
	rr := httptest.NewRecorder()
	handler.handleApprovalCount(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if pending, ok := resp["pending"]; !ok {
		t.Fatal("response missing 'pending' key")
	} else if pending.(float64) != 0 {
		t.Errorf("pending = %v, want 0", pending)
	}
}

func TestApprovalCountHandler_MethodNotAllowed(t *testing.T) {
	defer setupCoverageQueue(t)()

	store := NewApprovalStore(getDBConn())
	svc := NewApprovalService(store)
	handler := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/approvals/count", nil)
	rr := httptest.NewRecorder()
	handler.handleApprovalCount(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("status = %d, want 405", rr.Code)
	}
}

func TestApprovalCountHandler_WithPending(t *testing.T) {
	defer setupCoverageQueue(t)()

	db := getDBConn()
	// Insert a task for the approval FK.
	_, err := db.Exec(`
		INSERT INTO tasks (id, title, status, priority, domain, project, type, created_at, updated_at)
		VALUES ('task-for-approval', 'approval task', 'completed', 5, 'test', 'proj', 'feature', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	// Insert a pending approval directly (description must be non-NULL due to scan).
	_, err = db.Exec(`
		INSERT INTO approvals (id, type, task_id, agent_id, domain, title, description, status, tier, risk_score, confidence_score, expires_at)
		VALUES ('appr-count-1', 'task_completion', 'task-for-approval', 'agent-1', 'test', 'Review task', 'needs review', 'pending', 'phone', 0.5, 0.7, datetime('now', '+1 hour'))
	`)
	if err != nil {
		t.Fatalf("insert approval: %v", err)
	}

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	handler := NewApprovalHandler(svc)

	req := httptest.NewRequest(http.MethodGet, "/api/approvals/count", nil)
	rr := httptest.NewRecorder()
	handler.handleApprovalCount(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body: %s", rr.Code, rr.Body.String())
	}

	var resp map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if pending, ok := resp["pending"]; !ok {
		t.Fatal("response missing 'pending' key")
	} else if pending.(float64) != 1 {
		t.Errorf("pending = %v, want 1", pending)
	}
}

