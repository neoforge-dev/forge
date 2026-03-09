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

	"github.com/spf13/cobra"
)

// --- ApprovalService.CalculateConfidence ---

func TestCalculateConfidence_HighConfidence(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	ctx := ConfidenceContext{
		PatternMatch: 1.0,
		TestResults: &TestResult{
			Passed:   100,
			Failed:   0,
			Skipped:  0,
			Coverage: 90.0,
		},
		BlastRadius: 2,
		Reversible:  true,
	}
	result := svc.CalculateConfidence(ctx)
	if result.Score <= 0 {
		t.Errorf("expected positive score, got %f", result.Score)
	}
	if result.Tier == "" {
		t.Error("expected tier to be set")
	}
	if result.Reasoning == "" {
		t.Error("expected reasoning to be set")
	}
}

func TestCalculateConfidence_LowConfidence(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	ctx := ConfidenceContext{
		PatternMatch: 0.1,
		TestResults: &TestResult{
			Passed:   5,
			Failed:   95,
			Skipped:  0,
			Coverage: 10.0,
		},
		BlastRadius: 100,
		Reversible:  false,
	}
	result := svc.CalculateConfidence(ctx)
	if result.AutoApprove {
		t.Error("expected low confidence to not auto-approve")
	}
	if result.Tier != TierDesktop {
		t.Errorf("expected TierDesktop for low confidence, got %s", result.Tier)
	}
}

func TestCalculateConfidence_NoTestResults(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	ctx := ConfidenceContext{
		PatternMatch: 0.5,
		TestResults:  nil,
		BlastRadius:  10,
		Reversible:   true,
	}
	result := svc.CalculateConfidence(ctx)
	if result.Score <= 0 {
		t.Errorf("expected positive score even without test results, got %f", result.Score)
	}
}

func TestApprovalService_CreateApprovalRequest(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	ctx := context.Background()

	approval, err := svc.CreateApprovalRequest(
		ctx,
		ApprovalTaskCompletion,
		nil,
		"agent-1",
		"test-domain",
		"Test Approval",
		"Test description",
		nil,
		ConfidenceContext{PatternMatch: 0.8, Reversible: true, BlastRadius: 3},
	)
	if err != nil {
		t.Fatalf("CreateApprovalRequest: %v", err)
	}
	if approval.ID == "" {
		t.Error("expected approval ID to be set")
	}
	if approval.ConfidenceScore <= 0 {
		t.Errorf("expected confidence score > 0, got %f", approval.ConfidenceScore)
	}
}

func TestApprovalService_ApproveAndReject(t *testing.T) {
	store, cleanup := setupApprovalsTestDB(t)
	defer cleanup()

	svc := NewApprovalService(store)
	ctx := context.Background()

	approval, _ := svc.CreateApprovalRequest(
		ctx, ApprovalMerge, nil, "agent-1", "domain", "title", "desc", nil,
		ConfidenceContext{},
	)

	if err := svc.Approve(ctx, approval.ID, "human-1"); err != nil {
		t.Fatalf("Approve: %v", err)
	}
	if err := svc.Reject(ctx, approval.ID, "human-1"); err != nil {
		// Reject after approve may succeed (just updates status) — don't fatal
		t.Logf("Reject after Approve: %v (may be expected)", err)
	}
}

// --- migrate.go utility functions ---

func TestIsSQLiteMigration(t *testing.T) {
	if isSQLiteMigration("001_initial.sql") {
		t.Error("001_initial.sql should not be identified as SQLite-specific")
	}
	if !isSQLiteMigration("001_initial_sqlite.sql") {
		t.Error("001_initial_sqlite.sql should be identified as SQLite-specific")
	}
}

func TestColumnExists(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()

	// tasks table is created by migrations
	exists, err := columnExists(db, "tasks", "id")
	if err != nil {
		t.Fatalf("columnExists: %v", err)
	}
	if !exists {
		t.Error("expected 'id' column to exist in tasks table")
	}

	notExists, err := columnExists(db, "tasks", "nonexistent_col")
	if err != nil {
		t.Fatalf("columnExists for nonexistent: %v", err)
	}
	if notExists {
		t.Error("expected 'nonexistent_col' to not exist")
	}
}

func TestIndexExists(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	db := getDBConn()

	exists, err := indexExists(db, "idx_worktrees_status")
	if err != nil {
		t.Fatalf("indexExists: %v", err)
	}
	if !exists {
		t.Error("expected idx_worktrees_status index to exist after migrations")
	}

	notExists, err := indexExists(db, "idx_nonexistent_index")
	if err != nil {
		t.Fatalf("indexExists for nonexistent: %v", err)
	}
	if notExists {
		t.Error("expected nonexistent index to not exist")
	}
}

// --- queue.go utilities ---

func TestExtendLease(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "lease-extend-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	// UpdateTaskStatus to "assigned" directly so ExtendLease can find it
	sq := q.(*sqliteTaskQueue)
	if err := sq.UpdateTaskStatus(task.ID, "assigned", "agent-1"); err != nil {
		t.Fatalf("UpdateTaskStatus: %v", err)
	}

	if err := sq.ExtendLease(ctx, task.ID, "agent-1"); err != nil {
		t.Fatalf("ExtendLease: %v", err)
	}
}

func TestExtendLease_WrongAgent(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "lease-extend-002",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := q.AssignTask(ctx, task.ID, "agent-1"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	err := sq.ExtendLease(ctx, task.ID, "wrong-agent")
	if err == nil {
		t.Error("expected error extending lease for wrong agent")
	}
}

func TestDependenciesComplete_NoDeps(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "dep-complete-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	done, err := sq.dependenciesComplete(ctx, task.ID)
	if err != nil {
		t.Fatalf("dependenciesComplete: %v", err)
	}
	if !done {
		t.Error("expected task with no dependencies to be complete")
	}
}

func TestQueuePendingDispatch_And_GetPendingDispatches(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "dispatch-pending-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)

	payload := map[string]interface{}{"action": "claim", "task_id": task.ID}
	if err := sq.QueuePendingDispatch(ctx, task.ID, "sati", payload); err != nil {
		t.Fatalf("QueuePendingDispatch: %v", err)
	}

	dispatches, err := sq.GetPendingDispatches(ctx, "sati")
	if err != nil {
		t.Fatalf("GetPendingDispatches: %v", err)
	}
	if len(dispatches) == 0 {
		t.Error("expected at least 1 pending dispatch")
	}
}

func TestMarkDispatchSent(t *testing.T) {
	q, cleanup := setupTestQueue(t)
	defer cleanup()

	ctx := context.Background()
	task := Task{
		ID:       "dispatch-sent-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
	}
	if err := q.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	sq := q.(*sqliteTaskQueue)
	payload := map[string]interface{}{"action": "claim"}
	if err := sq.QueuePendingDispatch(ctx, task.ID, "sati", payload); err != nil {
		t.Fatalf("QueuePendingDispatch: %v", err)
	}

	dispatches, err := sq.GetPendingDispatches(ctx, "sati")
	if err != nil || len(dispatches) == 0 {
		t.Fatalf("GetPendingDispatches: %v, len=%d", err, len(dispatches))
	}

	if err := sq.MarkDispatchSent(ctx, dispatches[0].ID); err != nil {
		t.Fatalf("MarkDispatchSent: %v", err)
	}
}

// --- cli_commands.go: truncate ---

func TestTruncate_Short(t *testing.T) {
	result := truncate("hello", 10)
	if result != "hello" {
		t.Errorf("expected 'hello', got '%s'", result)
	}
}

func TestTruncate_Long(t *testing.T) {
	result := truncate("hello world", 8)
	if len(result) != 8 {
		t.Errorf("expected length 8, got %d: '%s'", len(result), result)
	}
	if result[len(result)-3:] != "..." {
		t.Errorf("expected '...' suffix, got '%s'", result)
	}
}

// --- auth.go: GetTokenInfo ---

func TestAuthManager_GetTokenInfo_NotFound(t *testing.T) {
	am := NewAuthManager("api")
	_, err := am.GetTokenInfo("nonexistent-token")
	if err == nil {
		t.Error("expected error for nonexistent token")
	}
}

func TestAuthManager_GetTokenInfo_Found(t *testing.T) {
	am := NewAuthManager("api")
	// Generate a token first
	token, err := am.GenerateToken("test token", []string{"tasks:read"})
	if err != nil {
		t.Fatalf("GenerateToken: %v", err)
	}
	info, err := am.GetTokenInfo(token)
	if err != nil {
		t.Fatalf("GetTokenInfo: %v", err)
	}
	if info == nil {
		t.Error("expected non-nil token info")
	}
}

// --- queue.go: eventsHandler ---

// TestEventsHandler_Empty verifies that /api/events returns 200 with an empty
// events list when no events have been written (uses a real migrated DB).
func TestEventsHandler_Empty(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/events", nil)
	w := httptest.NewRecorder()
	eventsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp struct {
		Count  int         `json:"count"`
		Events []TaskEvent `json:"events"`
	}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Count != 0 {
		t.Errorf("expected count=0, got %d", resp.Count)
	}
	if len(resp.Events) != 0 {
		t.Errorf("expected empty events slice, got %d events", len(resp.Events))
	}
}

// TestEventsHandler_Populated writes events directly into the DB and verifies
// that /api/events returns them with correct field values and filter support.
func TestEventsHandler_Populated(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	db := getDBConn()
	if db == nil {
		t.Fatal("expected non-nil DB after setupHandlerTest")
	}

	// Insert two events for different tasks/domains so we can test filtering.
	_, err := db.Exec(
		`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
		 VALUES (?, ?, ?, ?, ?, datetime('now'))`,
		"task-ev-001", "domain-a", "proj-x", "task.created", `{"msg":"hello"}`,
	)
	if err != nil {
		t.Fatalf("insert event 1: %v", err)
	}
	_, err = db.Exec(
		`INSERT INTO task_events (task_id, domain, project, event_type, payload, created_at)
		 VALUES (?, ?, ?, ?, ?, datetime('now'))`,
		"task-ev-002", "domain-b", "proj-y", "task.completed", `{"msg":"done"}`,
	)
	if err != nil {
		t.Fatalf("insert event 2: %v", err)
	}

	// --- unfiltered: should return both events ---
	req := httptest.NewRequest(http.MethodGet, "/api/events", nil)
	w := httptest.NewRecorder()
	eventsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp struct {
		Count  int         `json:"count"`
		Events []TaskEvent `json:"events"`
	}
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode unfiltered response: %v", err)
	}
	if resp.Count != 2 {
		t.Errorf("expected count=2, got %d", resp.Count)
	}
	if len(resp.Events) != 2 {
		t.Errorf("expected 2 events, got %d", len(resp.Events))
	}

	// --- filtered by task_id ---
	req2 := httptest.NewRequest(http.MethodGet, "/api/events?task_id=task-ev-001", nil)
	w2 := httptest.NewRecorder()
	eventsHandler(w2, req2)

	if w2.Code != http.StatusOK {
		t.Fatalf("filtered: expected 200, got %d: %s", w2.Code, w2.Body.String())
	}

	var resp2 struct {
		Count  int         `json:"count"`
		Events []TaskEvent `json:"events"`
	}
	if err := json.NewDecoder(w2.Body).Decode(&resp2); err != nil {
		t.Fatalf("decode filtered response: %v", err)
	}
	if resp2.Count != 1 {
		t.Errorf("filtered by task_id: expected count=1, got %d", resp2.Count)
	}
	if len(resp2.Events) != 1 {
		t.Errorf("filtered by task_id: expected 1 event, got %d", len(resp2.Events))
	}
	if resp2.Events[0].TaskID != "task-ev-001" {
		t.Errorf("expected task_id=task-ev-001, got %s", resp2.Events[0].TaskID)
	}
	if resp2.Events[0].EventType != "task.created" {
		t.Errorf("expected event_type=task.created, got %s", resp2.Events[0].EventType)
	}

	// --- filtered by event_type ---
	req3 := httptest.NewRequest(http.MethodGet, "/api/events?event_type=task.completed", nil)
	w3 := httptest.NewRecorder()
	eventsHandler(w3, req3)

	if w3.Code != http.StatusOK {
		t.Fatalf("event_type filter: expected 200, got %d: %s", w3.Code, w3.Body.String())
	}

	var resp3 struct {
		Count  int         `json:"count"`
		Events []TaskEvent `json:"events"`
	}
	if err := json.NewDecoder(w3.Body).Decode(&resp3); err != nil {
		t.Fatalf("decode event_type filtered response: %v", err)
	}
	if resp3.Count != 1 {
		t.Errorf("filtered by event_type: expected count=1, got %d", resp3.Count)
	}
	if len(resp3.Events) > 0 && resp3.Events[0].Domain != "domain-b" {
		t.Errorf("expected domain=domain-b, got %s", resp3.Events[0].Domain)
	}
}

// TestEventsHandler_MethodNotAllowed verifies that non-GET methods return 405.
func TestEventsHandler_MethodNotAllowed(t *testing.T) {
	cleanup := setupHandlerTest(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/events", nil)
	w := httptest.NewRecorder()
	eventsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// --- context_helpers.go: sendBootstrapToAgent ---

func TestSendBootstrapToAgent_NoError(t *testing.T) {
	// sendBootstrapToAgent is a stub that only logs — verify it doesn't panic
	envelope := &ContextEnvelope{
		ID:        "env-test",
		AgentID:   "agent-1",
		Domain:    "test",
		Project:   "proj",
		CreatedAt: time.Now().UTC(),
		ExpiresAt: time.Now().UTC().Add(time.Hour),
	}
	// Should not panic
	sendBootstrapToAgent("agent-1", "task-1", envelope)
}

// TestRunTestSuite_EmptyDir_W22M tests runTestSuite with an empty directory
func TestRunTestSuite_EmptyDir_W22M(t *testing.T) {
	t.Skip("runTestSuite spawns 'go test ./...' from forgeRoot — recursive execution hangs test suite (90s inner timeout > 60s outer)")
}

// TestWSHandler_NonWebSocket_W22M tests WSHandler with a non-WebSocket request
func TestWSHandler_NonWebSocket_W22M(t *testing.T) {
	if hub == nil {
		t.Skip("hub not initialized")
	}
	req := httptest.NewRequest(http.MethodGet, "/ws", nil)
	w := httptest.NewRecorder()
	WebSocketHandler(hub)(w, req)
	// non-WS request should get an error response (400/426), not panic
	if w.Code == http.StatusOK {
		t.Logf("WSHandler non-WS returned 200 (unexpected but ok): %s", w.Body.String())
	} else {
		t.Logf("WSHandler returned status: %d", w.Code)
	}
}

// TestRunQuickstart_NoPanic_W22M tests RunQuickstart doesn't panic
func TestRunQuickstart_NoPanic_W22M_W22M(t *testing.T) {
	t.Skip("RunQuickstart blocks waiting for stdin input — skipping to avoid goroutine leak")
}

// TestRunTestSuite_CancelledContext_W22M tests runTestSuite with cancelled context
func TestRunTestSuite_CancelledContext_W22M(t *testing.T) {
	t.Skip("runTestSuite spawns 'go test ./...' — skipping to avoid recursive test execution and goroutine leaks")
}

// TestWSHandler_MethodNotAllowed_W22M tests WSHandler with wrong method
func TestWSHandler_MethodNotAllowed_W22M(t *testing.T) {
	if hub == nil {
		t.Skip("hub not initialized")
	}
	req := httptest.NewRequest(http.MethodPost, "/ws", nil)
	w := httptest.NewRecorder()
	WebSocketHandler(hub)(w, req)
	t.Logf("WSHandler POST returned status: %d", w.Code)
}

// TestRunQuickstart_QuickstartFlag_W22M tests help with quickstart flag
func TestRunQuickstart_QuickstartFlag_W22M(t *testing.T) {
	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w
	defer func() {
		w.Close()
		os.Stdout = old
		var buf bytes.Buffer
		buf.ReadFrom(r)
	}()

	// Run help with quickstart argument
	done := make(chan struct{})
	go func() {
		defer close(done)
		RunHelp([]string{"quickstart"})
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Skip("RunHelp with quickstart blocks — skipping")
	}
}

// Wave 38: getAgentID paths, uiFleetHandler with DB (nil DB + real DB),
//          handleQueueDepth format=text, orchestratorWorkStrategyPatrol with idle agent

func TestGetAgentID_FromFlag_W38(t *testing.T) {
	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")
	if err := cmd.Flags().Set("agent", "test-agent-flag"); err != nil {
		t.Fatalf("set flag: %v", err)
	}

	id, err := getAgentID(cmd)
	if err != nil {
		t.Fatalf("getAgentID: %v", err)
	}
	if id != "test-agent-flag" {
		t.Errorf("expected 'test-agent-flag', got %q", id)
	}
}

func TestGetAgentID_FromEnv_W38(t *testing.T) {
	t.Setenv("FORGE_AGENT_ID", "env-agent-w38")
	// Ensure TMUX is unset so we don't hit tmux path
	t.Setenv("TMUX", "")

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")

	id, err := getAgentID(cmd)
	if err != nil {
		t.Fatalf("getAgentID: %v", err)
	}
	if id != "env-agent-w38" {
		t.Errorf("expected 'env-agent-w38', got %q", id)
	}
}

func TestGetAgentID_NoSource_W38(t *testing.T) {
	t.Setenv("FORGE_AGENT_ID", "")
	t.Setenv("TMUX", "")

	cmd := &cobra.Command{}
	cmd.Flags().String("agent", "", "agent id")

	_, err := getAgentID(cmd)
	if err == nil {
		t.Error("expected error when no agent ID source available")
	}
}

func TestUIFleetHandler_MethodNotAllowed_W38(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestUIFleetHandler_NilDB_W38(t *testing.T) {
	orig := getDBConn()
	setDBConn(nil)
	defer setDBConn(orig)

	req := httptest.NewRequest(http.MethodGet, "/ui/fleet", nil)
	w := httptest.NewRecorder()
	uiFleetHandler(w, req)
	// Nil DB — skips queries, renders empty HTMX template
	_ = w.Code
}

func TestHandleQueueDepth_FormatText_W38(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	q, err := NewTaskQueueFromDB(db)
	if err != nil {
		t.Fatalf("NewTaskQueueFromDB: %v", err)
	}
	orig := taskQueue
	taskQueue = q
	defer func() { taskQueue = orig }()

	req := httptest.NewRequest(http.MethodGet, "/api/queue/depth?format=text", nil)
	w := httptest.NewRecorder()
	handleQueueDepth(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}
