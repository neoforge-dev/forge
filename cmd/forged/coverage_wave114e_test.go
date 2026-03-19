//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestWave114e_CompleteTaskWithApproval_NeedsApproval_Direct exercises the
// "needs_approval" branch of CompleteTaskWithApproval directly (low confidence →
// create approval → update task to pending_approval → return error).
func TestWave114e_CompleteTaskWithApproval_NeedsApproval_Direct(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(taskQueue, svc)

	taskID := "wave114e-needs-approval"
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "wave114e",
		Type:     TaskTypeFeature,
		Title:    "Needs approval",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	ctx := ConfidenceContext{
		TestResults:  &TestResult{Passed: 0, Failed: 10, Skipped: 0, Coverage: 5},
		PatternMatch: 0.0,
		BlastRadius:  100,
		Reversible:   false,
	}

	err := tai.CompleteTaskWithApproval(context.Background(), taskID, "agent-1", "attempted", ctx)
	if err == nil {
		t.Fatal("expected non-nil error for low-confidence completion (needs approval)")
	}
	if !bytes.Contains([]byte(err.Error()), []byte("approval")) {
		t.Errorf("expected error to mention approval, got: %v", err)
	}

	updated, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if updated.Status != TaskStatus("pending_approval") {
		t.Errorf("expected task status pending_approval, got %s", updated.Status)
	}
}

// TestWave114e_CompleteTaskWithApproval_HighConfidence_Completes exercises the
// high-confidence path (score >= 0.7 → complete immediately).
func TestWave114e_CompleteTaskWithApproval_HighConfidence_Completes(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	store := NewApprovalStore(db)
	svc := NewApprovalService(store)
	tai := NewTaskApprovalIntegration(taskQueue, svc)

	taskID := "wave114e-high-conf"
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "wave114e",
		Type:     TaskTypeFeature,
		Title:    "High confidence",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := taskQueue.ClaimTask(context.Background(), taskID, "agent-1"); err != nil {
		t.Fatalf("ClaimTask: %v", err)
	}

	ctx := ConfidenceContext{
		TestResults:  &TestResult{Passed: 100, Failed: 0, Skipped: 0, Coverage: 95},
		PatternMatch: 1.0,
		BlastRadius:  1,
		Reversible:   true,
	}

	err := tai.CompleteTaskWithApproval(context.Background(), taskID, "agent-1", "done", ctx)
	if err != nil {
		t.Fatalf("expected nil error for high-confidence completion, got: %v", err)
	}

	updated, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if updated.Status != TaskStatusCompleted {
		t.Errorf("expected task status completed, got %s", updated.Status)
	}
}

// TestWave114e_CompleteTaskWithApproval_NeedsApprovalBranch exercises the
// pending_approval path via completeTaskWithApprovalHandler (HTTP).
func TestWave114e_CompleteTaskWithApproval_NeedsApprovalBranch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Enqueue a task that will require approval due to low confidence.
	taskID := "wave114e-approval-task"
	task := Task{
		ID:       taskID,
		Domain:   "test",
		Project:  "wave114e",
		Type:     TaskTypeFeature,
		Title:    "Wave114e approval task",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	if err := taskQueue.Enqueue(context.Background(), task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Build a completion request with very low confidence so it goes through the
	// "needs approval" branch and returns pending_approval.
	bodyReq := TaskCompletionRequest{
		AgentID: "agent-wave114e",
		Result:  "attempted",
		Confidence: ConfidenceFactorsInput{
			TestCoverage:   5,
			TestsPassed:    0,
			TestsFailed:    10,
			TestsSkipped:   0,
			PatternMatch:   0.0,
			BlastRadius:    100,
			Reversible:     false,
			CodeComplexity: 0.9,
		},
	}
	payload, err := json.Marshal(bodyReq)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/tasks/"+taskID+"/complete-with-approval", bytes.NewReader(payload))
	rr := httptest.NewRecorder()

	completeTaskWithApprovalHandler(rr, req)

	var resp TaskCompletionResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err == nil {
		// When the integration returns a normal pending_approval response, the
		// handler should surface it with 202 Accepted.
		if resp.Status == "pending_approval" {
			if rr.Code != http.StatusAccepted {
				t.Fatalf("expected 202 Accepted for pending approval, got %d: %s", rr.Code, rr.Body.String())
			}
		}
	}

	// The task should now be in pending_approval state in the queue.
	updated, err := taskQueue.GetTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if updated.Status != TaskStatus("pending_approval") {
		t.Errorf("expected task status pending_approval, got %s", updated.Status)
	}
}

// TestWave114e_HandleTaskList_Empty verifies handleTaskList returns an empty list
// with the expected JSON envelope when there are no tasks.
func TestWave114e_HandleTaskList_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list?format=json", nil)
	rr := httptest.NewRecorder()

	handleTaskList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleTaskList empty: status=%d body=%s", rr.Code, rr.Body.String())
	}

	var envelope struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&envelope); err != nil {
		t.Fatalf("decode: %v (body=%s)", err, rr.Body.String())
	}
	if envelope.Count != 0 || len(envelope.Tasks) != 0 {
		t.Errorf("expected empty task list, got count=%d len=%d", envelope.Count, len(envelope.Tasks))
	}
}

// TestWave114e_HandleTaskList_WithStatusFilter verifies status filtering and JSON output.
func TestWave114e_HandleTaskList_WithStatusFilter(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	// Insert tasks in various statuses.
	ctx := context.Background()
	tasks := []Task{
		{
			ID:       "wave114e-t1",
			Domain:   "test",
			Project:  "wave114e",
			Type:     TaskTypeFeature,
			Title:    "Queued task",
			Priority: 5,
			Status:   TaskStatusQueued,
			State:    StateQueued,
		},
		{
			ID:       "wave114e-t2",
			Domain:   "test",
			Project:  "wave114e",
			Type:     TaskTypeFeature,
			Title:    "Completed task",
			Priority: 5,
			Status:   TaskStatusCompleted,
			State:    StateCompleted,
		},
		{
			ID:       "wave114e-t3",
			Domain:   "test",
			Project:  "wave114e",
			Type:     TaskTypeFeature,
			Title:    "Failed task",
			Priority: 5,
			Status:   TaskStatusFailed,
			State:    StateFailed,
		},
	}
	for _, task := range tasks {
		if err := taskQueue.Enqueue(ctx, task); err != nil {
			t.Fatalf("Enqueue %s: %v", task.ID, err)
		}
	}

	// Filter for completed tasks and request JSON output through the CLI handler.
	req := httptest.NewRequest(http.MethodGet, "/cli/task/list?status=completed&limit=10&format=json", nil)
	rr := httptest.NewRecorder()

	handleTaskList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleTaskList status filter: status=%d body=%s", rr.Code, rr.Body.String())
	}

	var envelope struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&envelope); err != nil {
		t.Fatalf("decode: %v (body=%s)", err, rr.Body.String())
	}
	if envelope.Count != 1 || len(envelope.Tasks) != 1 {
		t.Fatalf("expected exactly 1 completed task, got count=%d len=%d", envelope.Count, len(envelope.Tasks))
	}
	if envelope.Tasks[0].Status != TaskStatusCompleted {
		t.Errorf("expected filtered task to be completed, got status=%s", envelope.Tasks[0].Status)
	}
}

// TestWave114e_HandleTaskList_NoFormat verifies handleTaskList with no format query (default JSON).
func TestWave114e_HandleTaskList_NoFormat(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleTaskList no format: status=%d body=%s", rr.Code, rr.Body.String())
	}
	var out struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v (body=%s)", err, rr.Body.String())
	}
	if out.Count != 0 || len(out.Tasks) != 0 {
		t.Errorf("expected empty list, got count=%d len=%d", out.Count, len(out.Tasks))
	}
}

// TestWave114e_HandleTaskList_StatusQueued verifies filtering by status=queued.
func TestWave114e_HandleTaskList_StatusQueued(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()
	task := Task{
		ID:       "wave114e-queued",
		Domain:   "test",
		Project:  "wave114e",
		Type:     TaskTypeFeature,
		Title:    "Queued",
		Priority: 5,
		Status:   TaskStatusQueued,
		State:    StateQueued,
	}
	if err := taskQueue.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list?status=queued&format=json", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleTaskList status=queued: status=%d body=%s", rr.Code, rr.Body.String())
	}
	var out struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out.Count != 1 || len(out.Tasks) != 1 {
		t.Fatalf("expected 1 queued task, got count=%d len=%d", out.Count, len(out.Tasks))
	}
	if out.Tasks[0].Status != TaskStatusQueued {
		t.Errorf("expected status queued, got %s", out.Tasks[0].Status)
	}
}

// TestWave114e_HandleTaskList_StatusFailed verifies filtering by status=failed.
func TestWave114e_HandleTaskList_StatusFailed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	ctx := context.Background()
	task := Task{
		ID:       "wave114e-failed",
		Domain:   "test",
		Project:  "wave114e",
		Type:     TaskTypeFeature,
		Title:    "Failed",
		Priority: 5,
		Status:   TaskStatusFailed,
		State:    StateFailed,
	}
	if err := taskQueue.Enqueue(ctx, task); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/cli/task/list?status=failed&format=json", nil)
	rr := httptest.NewRecorder()
	handleTaskList(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("handleTaskList status=failed: status=%d body=%s", rr.Code, rr.Body.String())
	}
	var out struct {
		Tasks []Task `json:"tasks"`
		Count int    `json:"count"`
	}
	if err := json.NewDecoder(rr.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out.Count != 1 || len(out.Tasks) != 1 {
		t.Fatalf("expected 1 failed task, got count=%d len=%d", out.Count, len(out.Tasks))
	}
	if out.Tasks[0].Status != TaskStatusFailed {
		t.Errorf("expected status failed, got %s", out.Tasks[0].Status)
	}
}

