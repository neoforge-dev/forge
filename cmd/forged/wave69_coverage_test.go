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

// ---- wave69FakeQueue: minimal TaskQueue mock for approval_integration tests ----

type wave69FakeQueue struct {
	tasks         map[string]*Task
	completeErr   error
	failErr       error
	updateErr     error
	completeCalls []string
	failCalls     []string
}

func newWave69FakeQueue() *wave69FakeQueue {
	return &wave69FakeQueue{tasks: make(map[string]*Task)}
}

func (q *wave69FakeQueue) Enqueue(_ context.Context, task Task) error {
	q.tasks[task.ID] = &task
	return nil
}
func (q *wave69FakeQueue) AssignTask(_ context.Context, taskID, _ string) error { return nil }
func (q *wave69FakeQueue) Dequeue(_ context.Context, _ string) (*Task, error)   { return nil, nil }
func (q *wave69FakeQueue) Complete(_ context.Context, taskID, _ string) error {
	q.completeCalls = append(q.completeCalls, taskID)
	return q.completeErr
}
func (q *wave69FakeQueue) Fail(_ context.Context, taskID, _ string) error {
	q.failCalls = append(q.failCalls, taskID)
	return q.failErr
}
func (q *wave69FakeQueue) Pause(_ context.Context, _ string) error  { return nil }
func (q *wave69FakeQueue) Resume(_ context.Context, _ string) error { return nil }
func (q *wave69FakeQueue) GetStatus(_ context.Context, taskID string) (Task, error) {
	if t, ok := q.tasks[taskID]; ok {
		return *t, nil
	}
	return Task{}, nil
}
func (q *wave69FakeQueue) GetTask(_ context.Context, taskID string) (Task, error) {
	if t, ok := q.tasks[taskID]; ok {
		return *t, nil
	}
	return Task{}, nil
}
func (q *wave69FakeQueue) UpdateTaskStatus(taskID, status, _ string) error {
	if q.updateErr != nil {
		return q.updateErr
	}
	if t, ok := q.tasks[taskID]; ok {
		t.Status = TaskStatus(status)
	}
	return nil
}
func (q *wave69FakeQueue) ListAllTasks(_ context.Context, _ int) ([]Task, error)        { return nil, nil }
func (q *wave69FakeQueue) ListTasksByStatus(_ context.Context, _ TaskStatus, _ int) ([]Task, error) {
	return nil, nil
}
func (q *wave69FakeQueue) GetClaimableTasks(_ context.Context, _ int) ([]Task, error) { return nil, nil }
func (q *wave69FakeQueue) ClaimTask(_ context.Context, _, _ string) error              { return nil }
func (q *wave69FakeQueue) ReleaseTask(_ context.Context, _, _, _ string) error         { return nil }
func (q *wave69FakeQueue) ExtendLease(_ context.Context, _, _ string) error            { return nil }
func (q *wave69FakeQueue) GetTaskEvents(_ context.Context, _ string, _ int) ([]TaskEvent, error) {
	return nil, nil
}
func (q *wave69FakeQueue) QueuePendingDispatch(_ context.Context, _, _ string, _ map[string]interface{}) error {
	return nil
}
func (q *wave69FakeQueue) GetPendingDispatches(_ context.Context, _ string) ([]PendingDispatch, error) {
	return nil, nil
}
func (q *wave69FakeQueue) MarkDispatchSent(_ context.Context, _ int64) error { return nil }
func (q *wave69FakeQueue) Close() error                                       { return nil }

// ---- approval_integration: CompleteTaskWithApproval branches ----

// TestWave69_CompleteTaskWithApproval_HighConfidence — score >= 0.7 → direct Complete
func TestWave69_CompleteTaskWithApproval_HighConfidence(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	task := Task{ID: "tai-task-1", Domain: "test", Project: "p", Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued}
	fq.tasks[task.ID] = &task

	tai := NewTaskApprovalIntegration(fq, svc)

	// High confidence: all tests pass, high coverage, reversible, low blast radius
	ctx := ConfidenceContext{
		TestResults:  &TestResult{Passed: 100, Failed: 0, Skipped: 0, Coverage: 95},
		PatternMatch: 1.0,
		BlastRadius:  1,
		Reversible:   true,
	}

	err := tai.CompleteTaskWithApproval(context.Background(), task.ID, "agent-1", "done", ctx)
	if err != nil {
		t.Errorf("expected nil error for high confidence, got: %v", err)
	}
	if len(fq.completeCalls) != 1 || fq.completeCalls[0] != task.ID {
		t.Errorf("expected Complete called with task ID, got: %v", fq.completeCalls)
	}
}

// TestWave69_CompleteTaskWithApproval_LowConfidence_PendingApproval — score < 0.7 → approval created + status update
func TestWave69_CompleteTaskWithApproval_LowConfidence_PendingApproval(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	task := Task{ID: "tai-task-2", Domain: "test", Project: "p", Type: TaskTypeFeature, Priority: 5, Status: TaskStatusQueued}
	fq.tasks[task.ID] = &task

	tai := NewTaskApprovalIntegration(fq, svc)

	// Low confidence: failing tests, large blast radius, not reversible
	ctx := ConfidenceContext{
		TestResults:  &TestResult{Passed: 0, Failed: 20, Skipped: 0, Coverage: 10},
		PatternMatch: 0.1,
		BlastRadius:  100,
		Reversible:   false,
	}

	err := tai.CompleteTaskWithApproval(context.Background(), task.ID, "agent-2", "done", ctx)
	if err == nil {
		t.Fatal("expected error for low confidence requiring approval")
	}
	if !strings.Contains(err.Error(), "requires approval") {
		t.Errorf("expected 'requires approval' in error, got: %v", err)
	}
	// UpdateTaskStatus should have been called (task status set to pending_approval)
	if task.Status != TaskStatus("pending_approval") {
		t.Errorf("expected task status 'pending_approval', got: %s", task.Status)
	}
}

// TestWave69_CompleteTaskWithApproval_CreateApprovalError — store fails
func TestWave69_CompleteTaskWithApproval_CreateApprovalStoreError(t *testing.T) {
	// Use a closed DB so the store fails to create the approval
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close DB immediately — store.Create will fail

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	task := Task{ID: "tai-task-3", Status: TaskStatusQueued}
	fq.tasks[task.ID] = &task

	tai := NewTaskApprovalIntegration(fq, svc)

	// Low confidence so it tries to create an approval
	ctx := ConfidenceContext{
		TestResults:  &TestResult{Passed: 0, Failed: 10},
		PatternMatch: 0.0,
		BlastRadius:  200,
		Reversible:   false,
	}

	err := tai.CompleteTaskWithApproval(context.Background(), task.ID, "agent-3", "done", ctx)
	if err == nil {
		t.Fatal("expected error when store is unavailable")
	}
}

// TestWave69_ProcessApprovedTask_NotApprovedStatus — approval exists but not StatusApproved
func TestWave69_ProcessApprovedTask_NotApproved(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	tai := NewTaskApprovalIntegration(fq, svc)

	// Create an approval in "pending" status (not approved)
	taskID := "proc-task-1"
	approval := Approval{
		ID:        generateULID(),
		Type:      ApprovalTaskCompletion,
		TaskID:    &taskID,
		AgentID:   "agent-1",
		Domain:    "test",
		Title:     "test approval",
		Status:    StatusPending,
		Tier:      TierPhone,
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
	}
	if err := store.Create(context.Background(), approval); err != nil {
		t.Fatalf("create approval: %v", err)
	}

	err := tai.ProcessApprovedTask(context.Background(), approval.ID)
	if err == nil {
		t.Fatal("expected error for non-approved approval")
	}
	if !strings.Contains(err.Error(), "not approved") {
		t.Errorf("expected 'not approved' in error, got: %v", err)
	}
}

// TestWave69_ProcessApprovedTask_NoTaskID — approval has no task ID
func TestWave69_ProcessApprovedTask_NoTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	tai := NewTaskApprovalIntegration(fq, svc)

	// Create approval with StatusApproved but no TaskID
	resolvedBy := "user-1"
	now := time.Now()
	approval := Approval{
		ID:         generateULID(),
		Type:       ApprovalTaskCompletion,
		TaskID:     nil, // no task
		AgentID:    "agent-1",
		Domain:     "test",
		Title:      "no-task approval",
		Status:     StatusApproved,
		Tier:       TierPhone,
		CreatedAt:  now,
		ExpiresAt:  now.Add(time.Hour),
		ResolvedAt: &now,
		ResolvedBy: &resolvedBy,
	}
	if err := store.Create(context.Background(), approval); err != nil {
		t.Fatalf("create approval: %v", err)
	}

	err := tai.ProcessApprovedTask(context.Background(), approval.ID)
	if err == nil {
		t.Fatal("expected error for approval with no task ID")
	}
	if !strings.Contains(err.Error(), "not associated with a task") {
		t.Errorf("expected 'not associated with a task', got: %v", err)
	}
}

// TestWave69_ProcessApprovedTask_TaskNotInPendingApproval — task not in pending_approval state
func TestWave69_ProcessApprovedTask_WrongTaskState(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	tai := NewTaskApprovalIntegration(fq, svc)

	taskID := "proc-task-3"
	fq.tasks[taskID] = &Task{ID: taskID, Status: TaskStatusQueued} // NOT pending_approval

	resolvedBy := "user-1"
	now := time.Now()
	approval := Approval{
		ID:         generateULID(),
		Type:       ApprovalTaskCompletion,
		TaskID:     &taskID,
		AgentID:    "agent-1",
		Domain:     "test",
		Title:      "wrong-state approval",
		Status:     StatusApproved,
		Tier:       TierPhone,
		CreatedAt:  now,
		ExpiresAt:  now.Add(time.Hour),
		ResolvedAt: &now,
		ResolvedBy: &resolvedBy,
	}
	if err := store.Create(context.Background(), approval); err != nil {
		t.Fatalf("create approval: %v", err)
	}

	err := tai.ProcessApprovedTask(context.Background(), approval.ID)
	if err == nil {
		t.Fatal("expected error for task not in pending_approval state")
	}
	if !strings.Contains(err.Error(), "not in pending_approval state") {
		t.Errorf("expected 'not in pending_approval state', got: %v", err)
	}
}

// TestWave69_ProcessApprovedTask_Success — happy path
func TestWave69_ProcessApprovedTask_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	tai := NewTaskApprovalIntegration(fq, svc)

	taskID := "proc-task-ok"
	fq.tasks[taskID] = &Task{ID: taskID, Status: TaskStatus("pending_approval")}

	resolvedBy := "user-ok"
	now := time.Now()
	approval := Approval{
		ID:         generateULID(),
		Type:       ApprovalTaskCompletion,
		TaskID:     &taskID,
		AgentID:    "agent-1",
		Domain:     "test",
		Title:      "success approval",
		Status:     StatusApproved,
		Tier:       TierPhone,
		CreatedAt:  now,
		ExpiresAt:  now.Add(time.Hour),
		ResolvedAt: &now,
		ResolvedBy: &resolvedBy,
	}
	if err := store.Create(context.Background(), approval); err != nil {
		t.Fatalf("create approval: %v", err)
	}

	err := tai.ProcessApprovedTask(context.Background(), approval.ID)
	if err != nil {
		t.Errorf("expected success, got: %v", err)
	}
	if len(fq.completeCalls) != 1 {
		t.Errorf("expected Complete called once, got %d", len(fq.completeCalls))
	}
}

// TestWave69_ProcessRejectedTask_NotRejected — approval not in rejected status
func TestWave69_ProcessRejectedTask_NotRejected(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	tai := NewTaskApprovalIntegration(fq, svc)

	taskID := "rej-task-1"
	approval := Approval{
		ID:        generateULID(),
		Type:      ApprovalTaskCompletion,
		TaskID:    &taskID,
		AgentID:   "agent-1",
		Domain:    "test",
		Title:     "not-rejected approval",
		Status:    StatusPending, // not rejected
		Tier:      TierPhone,
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
	}
	if err := store.Create(context.Background(), approval); err != nil {
		t.Fatalf("create approval: %v", err)
	}

	err := tai.ProcessRejectedTask(context.Background(), approval.ID)
	if err == nil {
		t.Fatal("expected error for non-rejected approval")
	}
	if !strings.Contains(err.Error(), "not rejected") {
		t.Errorf("expected 'not rejected' in error, got: %v", err)
	}
}

// TestWave69_ProcessRejectedTask_NoTaskID — rejected but no task
func TestWave69_ProcessRejectedTask_NoTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	tai := NewTaskApprovalIntegration(fq, svc)

	resolvedBy := "user-x"
	now := time.Now()
	approval := Approval{
		ID:         generateULID(),
		Type:       ApprovalTaskCompletion,
		TaskID:     nil,
		AgentID:    "agent-1",
		Domain:     "test",
		Title:      "no-task rejected",
		Status:     StatusRejected,
		Tier:       TierPhone,
		CreatedAt:  now,
		ExpiresAt:  now.Add(time.Hour),
		ResolvedAt: &now,
		ResolvedBy: &resolvedBy,
	}
	if err := store.Create(context.Background(), approval); err != nil {
		t.Fatalf("create approval: %v", err)
	}

	err := tai.ProcessRejectedTask(context.Background(), approval.ID)
	if err == nil {
		t.Fatal("expected error for rejected approval with no task")
	}
	if !strings.Contains(err.Error(), "not associated with a task") {
		t.Errorf("expected 'not associated with a task', got: %v", err)
	}
}

// TestWave69_CheckPendingApprovals_WithExpired — expired approval triggers expire path
func TestWave69_CheckPendingApprovals_WithExpired(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	tai := NewTaskApprovalIntegration(fq, svc)

	taskID := "expired-task-1"
	fq.tasks[taskID] = &Task{ID: taskID, Status: TaskStatus("pending_approval")}

	// Create an approval that's already expired
	now := time.Now()
	approval := Approval{
		ID:        generateULID(),
		Type:      ApprovalTaskCompletion,
		TaskID:    &taskID,
		AgentID:   "agent-1",
		Domain:    "test",
		Title:     "expired approval",
		Status:    StatusPending,
		Tier:      TierPhone,
		CreatedAt: now.Add(-2 * time.Hour),
		ExpiresAt: now.Add(-1 * time.Hour), // already expired
	}
	if err := store.Create(context.Background(), approval); err != nil {
		t.Fatalf("create approval: %v", err)
	}

	err := tai.CheckPendingApprovals(context.Background())
	if err != nil {
		t.Errorf("CheckPendingApprovals should return nil, got: %v", err)
	}
}

// TestWave69_CheckPendingApprovals_NoTaskID — approval with nil task ID is skipped
func TestWave69_CheckPendingApprovals_SkipsNilTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	tai := NewTaskApprovalIntegration(fq, svc)

	// Approval with nil task ID — should be skipped
	approval := Approval{
		ID:        generateULID(),
		Type:      ApprovalTaskCompletion,
		TaskID:    nil,
		AgentID:   "agent-1",
		Domain:    "test",
		Title:     "no-task pending",
		Status:    StatusPending,
		Tier:      TierPhone,
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
	}
	if err := store.Create(context.Background(), approval); err != nil {
		t.Fatalf("create approval: %v", err)
	}

	err := tai.CheckPendingApprovals(context.Background())
	if err != nil {
		t.Errorf("expected nil error, got: %v", err)
	}
	if len(fq.failCalls) != 0 {
		t.Errorf("Fail should not be called for nil taskID approval")
	}
}

// TestWave69_HandleTaskCompletion_HighConfidence — task completes directly
func TestWave69_HandleTaskCompletion_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	fq := newWave69FakeQueue()

	task := Task{ID: "htc-task-1", Status: TaskStatusQueued}
	fq.tasks[task.ID] = &task

	tai := NewTaskApprovalIntegration(fq, svc)

	req := TaskCompletionRequest{
		TaskID:  task.ID,
		AgentID: "agent-1",
		Result:  "done",
		Confidence: ConfidenceFactorsInput{
			TestsPassed:  100,
			TestsFailed:  0,
			TestCoverage: 95,
			PatternMatch: 1.0,
			BlastRadius:  1,
			Reversible:   true,
		},
	}

	resp, err := tai.HandleTaskCompletion(context.Background(), req)
	if err != nil {
		t.Errorf("expected success, got: %v", err)
	}
	if resp == nil {
		t.Fatal("expected non-nil response")
	}
	if resp.Status != "completed" {
		t.Errorf("expected status 'completed', got: %s", resp.Status)
	}
}

// ---- handoffs: uncovered branches in Accept/Get/handleHandoffAction ----

// TestWave69_HandoffService_Accept_NonPendingStatus
func TestWave69_HandoffService_Accept_NonPendingStatus(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	// Create and accept handoff to move it to "accepted" state
	h, err := svc.Create(ctx, "agent-a", "agent-b", "test task", nil, "high")
	if err != nil {
		t.Fatalf("create handoff: %v", err)
	}

	// Accept it first
	_, err = svc.Accept(ctx, h.ID, "agent-b")
	if err != nil {
		t.Fatalf("first accept: %v", err)
	}

	// Try to accept again — should fail since it's now "accepted" not "pending"
	_, err = svc.Accept(ctx, h.ID, "agent-b")
	if err == nil {
		t.Fatal("expected error when accepting non-pending handoff")
	}
	if !strings.Contains(err.Error(), "cannot accept") {
		t.Errorf("expected 'cannot accept' in error, got: %v", err)
	}
}

// TestWave69_HandoffService_Accept_WithEmptyAcceptingAgent
func TestWave69_HandoffService_Accept_DefaultToToAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	h, err := svc.Create(ctx, "from-agent", "to-agent", "desc", nil, "")
	if err != nil {
		t.Fatalf("create: %v", err)
	}

	// Accept with empty accepting agent — should default to ToAgent
	accepted, err := svc.Accept(ctx, h.ID, "")
	if err != nil {
		t.Fatalf("accept: %v", err)
	}
	if accepted.AcceptingAgent == nil || *accepted.AcceptingAgent != "to-agent" {
		t.Errorf("expected AcceptingAgent 'to-agent', got: %v", accepted.AcceptingAgent)
	}
}

// TestWave69_HandoffService_Get_NotFound
func TestWave69_HandoffService_Get_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	ctx := context.Background()

	_, err := store.Get(ctx, "nonexistent-id")
	if err == nil {
		t.Fatal("expected error for nonexistent handoff")
	}
	if !strings.Contains(err.Error(), "not found") {
		t.Errorf("expected 'not found' in error, got: %v", err)
	}
}

// TestWave69_HandoffHandler_handleHandoffAction_MethodNotAllowed
func TestWave69_HandoffHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	h := NewHandoffHandler(svc)

	// GET on action route (only POST allowed)
	req := httptest.NewRequest(http.MethodGet, "/api/handoffs/some-id/accept", nil)
	w := httptest.NewRecorder()
	h.handleHandoffAction(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestWave69_HandoffHandler_handleHandoffAction_UnknownAction
func TestWave69_HandoffHandler_UnknownAction(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	h := NewHandoffHandler(svc)

	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/some-id/unknownaction", nil)
	w := httptest.NewRecorder()
	h.handleHandoffAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for unknown action, got %d", w.Code)
	}
}

// TestWave69_HandoffHandler_handleHandoffAction_InvalidPath
func TestWave69_HandoffHandler_InvalidPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	h := NewHandoffHandler(svc)

	// Path without action part (only 1 component after prefix)
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/just-an-id", nil)
	w := httptest.NewRecorder()
	h.handleHandoffAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for invalid path, got %d", w.Code)
	}
}

// TestWave69_HandoffHandler_Reject_MissingReason
func TestWave69_HandoffHandler_Reject_MissingReason(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	h := NewHandoffHandler(svc)

	// Create a handoff to reject
	ctx := context.Background()
	hf, err := svc.Create(ctx, "a1", "a2", "task desc", nil, "low")
	if err != nil {
		t.Fatalf("create handoff: %v", err)
	}

	// POST reject with empty reason body
	body, _ := json.Marshal(map[string]string{"reason": ""})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/"+hf.ID+"/reject", bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.handleHandoffAction(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing reject reason, got %d", w.Code)
	}
}

// TestWave69_HandoffHandler_handleHandoffs_MethodNotAllowed
func TestWave69_HandoffHandler_HandleHandoffs_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	h := NewHandoffHandler(svc)

	req := httptest.NewRequest(http.MethodDelete, "/api/handoffs", nil)
	w := httptest.NewRecorder()
	h.handleHandoffs(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestWave69_HandoffHandler_createHandoff_MissingFields
func TestWave69_HandoffHandler_Create_MissingFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	h := NewHandoffHandler(svc)

	// Missing required fields (no to_agent)
	body, _ := json.Marshal(map[string]string{"from_agent": "a1"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs", bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.handleHandoffs(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing fields, got %d", w.Code)
	}
}

// TestWave69_HandoffHandler_listHandoffs_WithQueryParams
func TestWave69_HandoffHandler_List_WithFilters(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	h := NewHandoffHandler(svc)

	// Create a handoff first
	ctx := context.Background()
	svc.Create(ctx, "from-agent", "to-agent", "desc", nil, "")

	// List with filter params
	req := httptest.NewRequest(http.MethodGet, "/api/handoffs?status=pending&from_agent=from-agent&limit=10", nil)
	w := httptest.NewRecorder()
	h.handleHandoffs(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// TestWave69_HandoffHandler_Reject_WithReason — success path for reject
func TestWave69_HandoffHandler_Reject_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	h := NewHandoffHandler(svc)

	ctx := context.Background()
	hf, err := svc.Create(ctx, "from-x", "to-x", "task", nil, "medium")
	if err != nil {
		t.Fatalf("create: %v", err)
	}

	body, _ := json.Marshal(map[string]string{"reason": "not taking this"})
	req := httptest.NewRequest(http.MethodPost, "/api/handoffs/"+hf.ID+"/reject", bytes.NewReader(body))
	w := httptest.NewRecorder()
	h.handleHandoffAction(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body: %s", w.Code, w.Body.String())
	}
}

// ---- handleTaskShow: uncovered branch ----

// TestWave69_HandleTaskShow_MissingID — missing ?id param → 400
func TestWave69_HandleTaskShow_MissingID(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/tasks/show", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing id, got %d", w.Code)
	}
}

// TestWave69_HandleTaskShow_NotFound — unknown task → 404 from getTaskHandler
func TestWave69_HandleTaskShow_NotFound(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/cli/tasks/show?id=nonexistent-task-xyz", nil)
	w := httptest.NewRecorder()
	handleTaskShow(w, req)

	// Should propagate error from getTaskHandler (404)
	if w.Code < 400 {
		t.Errorf("expected error status for nonexistent task, got %d", w.Code)
	}
}

// ---- coordination.go: GetCoordinationHTML uncovered paths ----

// TestWave69_CoordinationDashboard_GetCoordinationHTML
func TestWave69_CoordinationDashboard_GetCoordinationHTML(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)
	html := cd.GetCoordinationHTML()

	if html == "" {
		t.Fatal("expected non-empty HTML from GetCoordinationHTML")
	}
	if !strings.Contains(html, "Sprint Coordination") {
		t.Errorf("expected 'Sprint Coordination' in HTML, got excerpt: %s", html[:min69(len(html), 100)])
	}
}

// TestWave69_CoordinationDashboard_ServeHTTP_NoSprint
func TestWave69_CoordinationDashboard_ServeHTTP(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)
	req := httptest.NewRequest(http.MethodGet, "/api/coordination?sprint=test-sprint", nil)
	w := httptest.NewRecorder()
	cd.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
}

// TestWave69_CoordinationDashboard_EstimateETA_Branches
func TestWave69_CoordinationDashboard_EstimateETA(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)

	// Test complete
	status := &SprintStatus{Completion: 100, StartedAt: time.Now().Add(-time.Hour)}
	eta := cd.estimateETA(status)
	if eta != "Complete" {
		t.Errorf("expected 'Complete', got: %s", eta)
	}

	// Test unknown (completion <= 0)
	status2 := &SprintStatus{Completion: 0, StartedAt: time.Now().Add(-time.Hour)}
	eta2 := cd.estimateETA(status2)
	if eta2 != "Unknown" {
		t.Errorf("expected 'Unknown', got: %s", eta2)
	}

	// Test sub-hour remaining
	status3 := &SprintStatus{
		Completion: 95,
		StartedAt:  time.Now().Add(-time.Hour),
	}
	eta3 := cd.estimateETA(status3)
	if eta3 == "" {
		t.Error("expected non-empty ETA for near-complete sprint")
	}
}

// TestWave69_CoordinationDashboard_DetectBlockers
func TestWave69_CoordinationDashboard_DetectBlockers(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)

	agents := []AgentSprintStatus{
		{Name: "agent-idle", Status: "idle", LastCommit: time.Now().Add(-45 * time.Minute)},
		{Name: "agent-blocked", Status: "working", LastCommit: time.Now().Add(-90 * time.Minute)},
		{Name: "agent-high-ctx", Status: "high_context", LastCommit: time.Now().Add(-5 * time.Minute)},
		{Name: "agent-ok", Status: "working", LastCommit: time.Now().Add(-2 * time.Minute)},
	}

	blockers := cd.detectBlockers(agents)
	if len(blockers) == 0 {
		t.Error("expected at least one blocker detected")
	}
}

// TestWave69_CoordinationDashboard_CalculateCompletion_NoBlocers
func TestWave69_CoordinationDashboard_CalculateCompletion(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cd := NewCoordinationDashboard(db)

	status := &SprintStatus{
		Agents: []AgentSprintStatus{
			{Status: "working"},
			{Status: "working"},
		},
		Commits:  CommitSummary{Total: 10},
		Blockers: []Blocker{},
	}

	pct := cd.calculateCompletion(status)
	if pct <= 0 {
		t.Errorf("expected positive completion, got %.2f", pct)
	}
}

// TestWave69_TruncateString
func TestWave69_TruncateString(t *testing.T) {
	s := truncateString("hello world", 5)
	if !strings.HasSuffix(s, "...") {
		t.Errorf("expected truncated string to end with '...', got: %s", s)
	}

	// Short string — no truncation
	s2 := truncateString("hi", 10)
	if s2 != "hi" {
		t.Errorf("expected 'hi', got: %s", s2)
	}
}

// ---- handlers_context.go: contextsHandler ----

// TestWave69_ContextsHandler_MethodNotAllowed
func TestWave69_ContextsHandler_MethodNotAllowed(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodPost, "/api/contexts", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

// TestWave69_ContextsHandler_GetWithFilters
func TestWave69_ContextsHandler_GetWithFilters(t *testing.T) {
	_, cleanup := setupClaimTestDB(t)
	defer cleanup()

	req := httptest.NewRequest(http.MethodGet, "/api/contexts?agent_id=test-agent&domain=test&project=myproj&task_id=t1", nil)
	w := httptest.NewRecorder()
	contextsHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body: %s", w.Code, w.Body.String())
	}
}

// ---- parseHandoffNullTime / parseHandoffTime edge cases ----

// TestWave69_ParseHandoffTime_Formats
func TestWave69_ParseHandoffTime_Formats(t *testing.T) {
	// Valid RFC3339
	t1 := parseHandoffTime("2024-01-15T10:30:00Z")
	if t1.IsZero() {
		t.Error("expected non-zero time for RFC3339 input")
	}

	// Valid RFC3339Nano
	t2 := parseHandoffTime("2024-01-15T10:30:00.123456789Z")
	if t2.IsZero() {
		t.Error("expected non-zero time for RFC3339Nano input")
	}

	// Valid simple format
	t3 := parseHandoffTime("2024-01-15 10:30:00")
	if t3.IsZero() {
		t.Error("expected non-zero time for simple format input")
	}

	// Invalid — returns zero
	t4 := parseHandoffTime("not-a-date")
	if !t4.IsZero() {
		t.Error("expected zero time for invalid input")
	}
}

// TestWave69_HandoffService_Complete_NonAccepted
func TestWave69_HandoffService_Complete_NonAccepted(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	// Create a pending handoff (not yet accepted)
	h, _ := svc.Create(ctx, "a", "b", "task", nil, "")

	_, err := svc.Complete(ctx, h.ID, "done")
	if err == nil {
		t.Fatal("expected error completing a pending handoff")
	}
	if !strings.Contains(err.Error(), "cannot complete") {
		t.Errorf("expected 'cannot complete' in error, got: %v", err)
	}
}

// TestWave69_HandoffStore_List_AllFilters
func TestWave69_HandoffStore_List_AllFilters(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	store := NewHandoffStore(db)
	svc := NewHandoffService(store)
	ctx := context.Background()

	svc.Create(ctx, "from-a", "to-b", "test task", []string{"f1"}, "high")

	// List with all three filters
	results, err := store.List(ctx, "pending", "from-a", "to-b", 5)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(results) == 0 {
		t.Error("expected at least one handoff result")
	}

	// List with default limit (0 → 50)
	results2, err := store.List(ctx, "", "", "", 0)
	if err != nil {
		t.Fatalf("list default limit: %v", err)
	}
	if len(results2) == 0 {
		t.Error("expected results with default limit")
	}
}

// min69 is a local helper to avoid import conflicts
func min69(a, b int) int {
	if a < b {
		return a
	}
	return b
}
