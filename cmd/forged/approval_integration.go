//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
)

// TaskApprovalIntegration connects the approval system with task lifecycle
type TaskApprovalIntegration struct {
	queue           TaskQueue
	approvalService *ApprovalService
}

// NewTaskApprovalIntegration creates a new integration instance
func NewTaskApprovalIntegration(queue TaskQueue, approvalService *ApprovalService) *TaskApprovalIntegration {
	return &TaskApprovalIntegration{
		queue:           queue,
		approvalService: approvalService,
	}
}

// CompleteTaskWithApproval completes a task with confidence checking and approval workflow
func (tai *TaskApprovalIntegration) CompleteTaskWithApproval(
	ctx context.Context,
	taskID string,
	agentID string,
	result string,
	confidenceCtx ConfidenceContext,
) error {
	// Calculate confidence score
	confidence := tai.approvalService.CalculateConfidence(confidenceCtx)

	// If confidence is high enough, complete immediately
	if confidence.Score >= 0.7 {
		return tai.queue.Complete(ctx, taskID, result)
	}

	// Low confidence - create approval request
	approval, err := tai.approvalService.CreateApprovalRequest(
		ctx,
		ApprovalTaskCompletion,
		&taskID,
		agentID,
		"forge",
		fmt.Sprintf("Task %s completion requires approval", taskID),
		fmt.Sprintf("Confidence score: %.0f%% - %s", confidence.Score*100, confidence.Reasoning),
		nil,
		confidenceCtx,
	)
	if err != nil {
		return fmt.Errorf("failed to create approval request: %w", err)
	}

	// If auto-approved, complete the task
	if approval.Status == StatusAutoApproved {
		return tai.queue.Complete(ctx, taskID, result)
	}

	// Update task status to pending approval
	if err := tai.queue.UpdateTaskStatus(taskID, "pending_approval", ""); err != nil {
		return fmt.Errorf("failed to update task status: %w", err)
	}

	return &ApprovalRequiredError{
		TaskID:     taskID,
		ApprovalID: approval.ID,
		Confidence: confidence.Score,
		Reason:     confidence.Reasoning,
	}
}

// ProcessApprovedTask checks if a task has been approved and completes it
func (tai *TaskApprovalIntegration) ProcessApprovedTask(ctx context.Context, approvalID string) error {
	// Get approval details
	approval, err := tai.approvalService.store.Get(ctx, approvalID)
	if err != nil {
		return fmt.Errorf("failed to get approval: %w", err)
	}

	// Check if approval is approved
	if approval.Status != StatusApproved {
		return fmt.Errorf("approval %s is not approved (status: %s)", approvalID, approval.Status)
	}

	// Check if approval is for a task
	if approval.TaskID == nil {
		return fmt.Errorf("approval %s is not associated with a task", approvalID)
	}

	taskID := *approval.TaskID

	// Get task to verify it's in pending_approval state
	task, err := tai.queue.GetTask(ctx, taskID)
	if err != nil {
		return fmt.Errorf("failed to get task: %w", err)
	}

	if task.Status != TaskStatus("pending_approval") {
		return fmt.Errorf("task %s is not in pending_approval state (status: %s)", taskID, task.Status)
	}

	// Complete the task
	result := fmt.Sprintf("Approved by %s via approval %s", *approval.ResolvedBy, approvalID)
	return tai.queue.Complete(ctx, taskID, result)
}

// ProcessRejectedTask handles rejected approvals
func (tai *TaskApprovalIntegration) ProcessRejectedTask(ctx context.Context, approvalID string) error {
	// Get approval details
	approval, err := tai.approvalService.store.Get(ctx, approvalID)
	if err != nil {
		return fmt.Errorf("failed to get approval: %w", err)
	}

	// Check if approval is rejected
	if approval.Status != StatusRejected {
		return fmt.Errorf("approval %s is not rejected (status: %s)", approvalID, approval.Status)
	}

	// Check if approval is for a task
	if approval.TaskID == nil {
		return fmt.Errorf("approval %s is not associated with a task", approvalID)
	}

	taskID := *approval.TaskID

	// Fail the task
	return tai.queue.Fail(ctx, taskID, fmt.Sprintf("Approval rejected by %s", *approval.ResolvedBy))
}

// CheckPendingApprovals processes all pending approvals and updates task statuses
func (tai *TaskApprovalIntegration) CheckPendingApprovals(ctx context.Context) error {
	// Get all pending approvals for tasks
	approvals, err := tai.approvalService.GetPending(ctx, 100)
	if err != nil {
		return fmt.Errorf("failed to get pending approvals: %w", err)
	}

	for _, approval := range approvals {
		if approval.TaskID == nil {
			continue
		}

		// Check if approval has expired
		if time.Now().After(approval.ExpiresAt) {
			// Mark approval as expired
			if _, err := tai.approvalService.store.ExpireOldApprovals(ctx, time.Now()); err != nil {
				// Log error but continue
				continue
			}

			// Fail the associated task
			tai.queue.Fail(ctx, *approval.TaskID, "Approval expired")
		}
	}

	return nil
}

// ConfidenceFactorsInput represents input for calculating confidence
type ConfidenceFactorsInput struct {
	TestCoverage   float64 `json:"test_coverage"`   // 0-100
	TestsPassed    int     `json:"tests_passed"`
	TestsFailed    int     `json:"tests_failed"`
	TestsSkipped   int     `json:"tests_skipped"`
	PatternMatch   float64 `json:"pattern_match"`   // 0-1
	BlastRadius    int     `json:"blast_radius"`    // Number of files changed
	Reversible     bool    `json:"reversible"`
	CodeComplexity float64 `json:"code_complexity"` // 0-1
}

// CalculateConfidenceFromInput calculates confidence from input factors
func CalculateConfidenceFromInput(input ConfidenceFactorsInput) ConfidenceResult {
	testResult := &TestResult{
		Passed:   input.TestsPassed,
		Failed:   input.TestsFailed,
		Skipped:  input.TestsSkipped,
		Coverage: input.TestCoverage,
	}

	ctx := ConfidenceContext{
		TestResults:  testResult,
		PatternMatch: input.PatternMatch,
		BlastRadius:  input.BlastRadius,
		Reversible:   input.Reversible,
	}

	// Use a default approval service just for calculation
	service := &ApprovalService{}
	return service.CalculateConfidence(ctx)
}

// ApprovalRequiredError indicates a task requires approval
type ApprovalRequiredError struct {
	TaskID       string
	ApprovalID   string
	Confidence   float64
	Reason       string
}

func (e *ApprovalRequiredError) Error() string {
	return fmt.Sprintf("task %s requires approval %s (confidence: %.0f%%): %s", 
		e.TaskID, e.ApprovalID, e.Confidence*100, e.Reason)
}

// TaskCompletionRequest represents a request to complete a task with confidence data
type TaskCompletionRequest struct {
	TaskID      string                `json:"task_id"`
	AgentID     string                `json:"agent_id"`
	Result      string                `json:"result"`
	Confidence  ConfidenceFactorsInput `json:"confidence"`
}

// TaskCompletionResponse represents the response to a task completion request
type TaskCompletionResponse struct {
	Status      string  `json:"status"`
	TaskID      string  `json:"task_id"`
	ApprovalID  *string `json:"approval_id,omitempty"`
	Confidence  float64 `json:"confidence"`
	Message     string  `json:"message"`
}

// HandleTaskCompletion handles task completion with approval workflow
func (tai *TaskApprovalIntegration) HandleTaskCompletion(
	ctx context.Context,
	req TaskCompletionRequest,
) (*TaskCompletionResponse, error) {
	// Build confidence context
	testResult := &TestResult{
		Passed:   req.Confidence.TestsPassed,
		Failed:   req.Confidence.TestsFailed,
		Skipped:  req.Confidence.TestsSkipped,
		Coverage: req.Confidence.TestCoverage,
	}

	confidenceCtx := ConfidenceContext{
		TestResults:  testResult,
		PatternMatch: req.Confidence.PatternMatch,
		BlastRadius:  req.Confidence.BlastRadius,
		Reversible:   req.Confidence.Reversible,
	}

	// Try to complete with approval check
	err := tai.CompleteTaskWithApproval(ctx, req.TaskID, req.AgentID, req.Result, confidenceCtx)
	if err == nil {
		// Task completed successfully
		return &TaskCompletionResponse{
			Status:     "completed",
			TaskID:     req.TaskID,
			Confidence: CalculateConfidenceFromInput(req.Confidence).Score,
			Message:    "Task completed successfully",
		}, nil
	}

	// Check if it's an approval required error
	if approvalErr, ok := err.(*ApprovalRequiredError); ok {
		return &TaskCompletionResponse{
			Status:     "pending_approval",
			TaskID:     req.TaskID,
			ApprovalID: &approvalErr.ApprovalID,
			Confidence: approvalErr.Confidence,
			Message:    approvalErr.Reason,
		}, nil
	}

	// Other error
	return nil, err
}

// mustJSON marshals data to JSON or returns nil on error
func mustJSON(v interface{}) json.RawMessage {
	data, err := json.Marshal(v)
	if err != nil {
		// Return nil on marshal error - callers should handle gracefully
		return nil
	}
	return data
}
