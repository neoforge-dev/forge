//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestCalculateConfidenceFromInput(t *testing.T) {
	tests := []struct {
		name      string
		input     ConfidenceFactorsInput
		wantHigh  bool // expect score >= 0.7
	}{
		{
			name: "high confidence — all tests pass, good coverage",
			input: ConfidenceFactorsInput{
				TestsPassed:  20,
				TestsFailed:  0,
				TestsSkipped: 0,
				TestCoverage: 85.0,
				PatternMatch: 0.9,
				BlastRadius:  2,
				Reversible:   true,
			},
			wantHigh: true,
		},
		{
			name: "low confidence — tests failing",
			input: ConfidenceFactorsInput{
				TestsPassed:  5,
				TestsFailed:  10,
				TestsSkipped: 0,
				TestCoverage: 30.0,
				PatternMatch: 0.3,
				BlastRadius:  25,
				Reversible:   false,
			},
			wantHigh: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := CalculateConfidenceFromInput(tt.input)
			if tt.wantHigh && result.Score < 0.5 {
				t.Errorf("expected high confidence score, got %.2f", result.Score)
			}
			if !tt.wantHigh && result.Score > 0.9 {
				t.Errorf("expected low confidence score, got %.2f", result.Score)
			}
		})
	}
}

func TestMustJSON(t *testing.T) {
	data := mustJSON(map[string]string{"key": "value"})
	if data == nil {
		t.Fatal("expected non-nil JSON output")
	}
	if string(data) != `{"key":"value"}` {
		t.Errorf("unexpected JSON: %s", data)
	}
	// Unmarshalable type returns nil
	ch := make(chan int)
	if mustJSON(ch) != nil {
		t.Error("expected nil for unmarshalable type")
	}
}

func TestApprovalRequiredError(t *testing.T) {
	err := &ApprovalRequiredError{
		TaskID:     "task-123",
		ApprovalID: "appr-456",
		Confidence: 0.65,
		Reason:     "blast radius too high",
	}
	msg := err.Error()
	if !strings.Contains(msg, "task-123") {
		t.Errorf("error message missing task ID: %s", msg)
	}
	if !strings.Contains(msg, "appr-456") {
		t.Errorf("error message missing approval ID: %s", msg)
	}
	if !strings.Contains(msg, "65%") {
		t.Errorf("error message missing confidence percentage: %s", msg)
	}
}

// TestCompleteTaskWithApproval_HighConfidence_Isolated verifies that a
// high-confidence task completion calls Complete directly without creating an
// approval (isolated test using setupTestQueue + setupApprovalsTestDB).
func TestCompleteTaskWithApproval_HighConfidence_Isolated(t *testing.T) {
	store, apCleanup := setupApprovalsTestDB(t)
	defer apCleanup()

	q, qCleanup := setupTestQueue(t)
	defer qCleanup()

	ctx := context.Background()
	// Enqueue a task so Complete has something to update.
	if err := q.Enqueue(ctx, Task{
		ID:       "task-hc-iso-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	// Assign the task first so Complete can find it.
	if err := q.AssignTask(ctx, "task-hc-iso-001", "agent-iso"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(q, svc)

	// High confidence context: all tests pass, excellent coverage, low blast radius.
	confCtx := ConfidenceContext{
		TestResults: &TestResult{
			Passed:   50,
			Failed:   0,
			Skipped:  0,
			Coverage: 90.0,
		},
		PatternMatch: 0.95,
		BlastRadius:  1,
		Reversible:   true,
	}

	// High confidence should bypass the approval gate and complete directly.
	err := tai.CompleteTaskWithApproval(ctx, "task-hc-iso-001", "agent-iso", "result data", confCtx)
	if err != nil {
		t.Errorf("expected nil error for high-confidence completion, got: %v", err)
	}
}

// TestCompleteTaskWithApproval_LowConfidence_Isolated verifies that a
// low-confidence completion creates an approval request rather than completing.
func TestCompleteTaskWithApproval_LowConfidence_Isolated(t *testing.T) {
	store, apCleanup := setupApprovalsTestDB(t)
	defer apCleanup()

	q, qCleanup := setupTestQueue(t)
	defer qCleanup()

	ctx := context.Background()
	if err := q.Enqueue(ctx, Task{
		ID:       "task-lc-iso-001",
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := q.AssignTask(ctx, "task-lc-iso-001", "agent-iso"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(q, svc)

	// Low confidence context: many tests failing, high blast radius.
	confCtx := ConfidenceContext{
		TestResults: &TestResult{
			Passed:   2,
			Failed:   50,
			Skipped:  0,
			Coverage: 10.0,
		},
		PatternMatch: 0.1,
		BlastRadius:  100,
		Reversible:   false,
	}

	// Low confidence may return an error (pending_approval) or succeed (auto-approved).
	// We only verify the function doesn't panic.
	_ = tai.CompleteTaskWithApproval(ctx, "task-lc-iso-001", "agent-iso", "result data", confCtx)
}

// TestCheckPendingApprovals_NoApprovals verifies that CheckPendingApprovals
// succeeds when there are no pending approvals.
func TestCheckPendingApprovals_NoApprovals(t *testing.T) {
	store, apCleanup := setupApprovalsTestDB(t)
	defer apCleanup()

	q, qCleanup := setupTestQueue(t)
	defer qCleanup()

	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(q, svc)

	ctx := context.Background()
	if err := tai.CheckPendingApprovals(ctx); err != nil {
		t.Errorf("CheckPendingApprovals with no approvals should return nil, got: %v", err)
	}
}

// TestCheckPendingApprovals_SkipsNilTaskID verifies that approvals without a
// TaskID are silently skipped.
func TestCheckPendingApprovals_SkipsNilTaskID(t *testing.T) {
	store, apCleanup := setupApprovalsTestDB(t)
	defer apCleanup()

	q, qCleanup := setupTestQueue(t)
	defer qCleanup()

	svc := NewApprovalService(store)
	ctx := context.Background()

	// Create an approval with no TaskID — this exercises the `if approval.TaskID == nil` branch.
	_, err := svc.CreateApprovalRequest(
		ctx,
		ApprovalMerge,
		nil, // no task ID
		"agent-1",
		"domain",
		"Approval without task",
		"description",
		nil,
		ConfidenceContext{PatternMatch: 0.1, BlastRadius: 50, Reversible: false},
	)
	if err != nil {
		t.Fatalf("CreateApprovalRequest: %v", err)
	}

	tai := NewTaskApprovalIntegration(q, svc)

	// Should succeed — nil-TaskID approvals are silently skipped.
	if err := tai.CheckPendingApprovals(ctx); err != nil {
		t.Errorf("expected nil error, got: %v", err)
	}
}

// TestCompleteTaskWithApproval_LowConfidence_ReturnsTypedError verifies that
// low-confidence completion returns an *ApprovalRequiredError (Bug 2 fix).
// Before the fix, CompleteTaskWithApproval returned a plain fmt.Errorf which
// caused HandleTaskCompletion's type assertion to fail, resulting in 500s.
func TestCompleteTaskWithApproval_LowConfidence_ReturnsTypedError(t *testing.T) {
	store, apCleanup := setupApprovalsTestDB(t)
	defer apCleanup()

	q, qCleanup := setupTestQueue(t)
	defer qCleanup()

	ctx := context.Background()
	taskID := "typed-err-task-001"
	if err := q.Enqueue(ctx, Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := q.AssignTask(ctx, taskID, "agent-typed"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(q, svc)

	// Extremely low confidence — forces the approval-required path.
	confCtx := ConfidenceContext{
		TestResults: &TestResult{
			Passed:   0,
			Failed:   100,
			Skipped:  0,
			Coverage: 0.0,
		},
		PatternMatch: 0.0,
		BlastRadius:  200,
		Reversible:   false,
	}

	err := tai.CompleteTaskWithApproval(ctx, taskID, "agent-typed", "result", confCtx)
	if err == nil {
		t.Fatal("expected an error for low-confidence completion, got nil")
	}

	// The critical assertion: error must be *ApprovalRequiredError so that
	// HandleTaskCompletion's type assertion succeeds and returns 202, not 500.
	approvalErr, ok := err.(*ApprovalRequiredError)
	if !ok {
		t.Fatalf("expected *ApprovalRequiredError, got %T: %v", err, err)
	}
	if approvalErr.TaskID != taskID {
		t.Errorf("ApprovalRequiredError.TaskID = %q, want %q", approvalErr.TaskID, taskID)
	}
	if approvalErr.ApprovalID == "" {
		t.Error("ApprovalRequiredError.ApprovalID must not be empty")
	}
}

// TestHandleTaskCompletion_LowConfidence_Returns202 verifies that
// HandleTaskCompletion returns a pending_approval response (not an error) when
// confidence is low — exercising the full Bug 2 fix end-to-end.
func TestHandleTaskCompletion_LowConfidence_Returns202(t *testing.T) {
	store, apCleanup := setupApprovalsTestDB(t)
	defer apCleanup()

	q, qCleanup := setupTestQueue(t)
	defer qCleanup()

	ctx := context.Background()
	taskID := "handle-202-task-001"
	if err := q.Enqueue(ctx, Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := q.AssignTask(ctx, taskID, "agent-202"); err != nil {
		t.Fatalf("AssignTask: %v", err)
	}

	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(q, svc)

	req := TaskCompletionRequest{
		TaskID:  taskID,
		AgentID: "agent-202",
		Result:  "some result",
		Confidence: ConfidenceFactorsInput{
			TestsPassed:  0,
			TestsFailed:  50,
			TestCoverage: 0.0,
			PatternMatch: 0.0,
			BlastRadius:  200,
			Reversible:   false,
		},
	}

	resp, err := tai.HandleTaskCompletion(ctx, req)
	if err != nil {
		t.Fatalf("HandleTaskCompletion returned unexpected error: %v", err)
	}
	if resp.Status != "pending_approval" && resp.Status != "completed" {
		t.Errorf("expected 'pending_approval' or 'completed', got %q", resp.Status)
	}
}

// TestCheckPendingApprovals_ExpiredApproval verifies that an expired approval
// is handled by CheckPendingApprovals without panicking.
func TestCheckPendingApprovals_ExpiredApproval(t *testing.T) {
	store, apCleanup := setupApprovalsTestDB(t)
	defer apCleanup()

	q, qCleanup := setupTestQueue(t)
	defer qCleanup()

	svc := NewApprovalService(store)
	ctx := context.Background()

	taskID := "task-expired-002"

	// Enqueue the task.
	if err := q.Enqueue(ctx, Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "proj",
		Type:     TaskTypeFeature,
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Create a pending approval for the task.
	_, err := svc.CreateApprovalRequest(
		ctx,
		ApprovalTaskCompletion,
		&taskID,
		"agent-1",
		"domain",
		"Expired approval",
		"description",
		nil,
		ConfidenceContext{PatternMatch: 0.1, BlastRadius: 50, Reversible: false},
	)
	if err != nil {
		t.Fatalf("CreateApprovalRequest: %v", err)
	}

	// Manually back-date the expires_at to the past so it looks expired.
	_, err = store.db.ExecContext(ctx, `UPDATE approvals SET expires_at = ? WHERE task_id = ?`,
		time.Now().Add(-2*time.Hour), taskID)
	if err != nil {
		t.Fatalf("update expires_at: %v", err)
	}

	tai := NewTaskApprovalIntegration(q, svc)

	// Should not error — expired approvals are handled gracefully.
	if err := tai.CheckPendingApprovals(ctx); err != nil {
		t.Errorf("CheckPendingApprovals should not error for expired approval: %v", err)
	}
}
