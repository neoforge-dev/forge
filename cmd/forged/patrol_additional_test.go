//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// TestWave66_ConfidenceApprove_NilGlobals verifies that confidenceApproveCompletedTasks
// returns nil immediately when stateMachine or globalApprovalService is nil.
func TestWave66_ConfidenceApprove_NilGlobals(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Ensure globals are nil (default state before server init).
	origSM := stateMachine
	origAS := globalApprovalService
	stateMachine = nil
	globalApprovalService = nil
	defer func() {
		stateMachine = origSM
		globalApprovalService = origAS
	}()

	err := confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil error when globals are nil, got: %v", err)
	}
}

// TestWave66_ConfidenceApprove_EmptyCompletedTasks verifies that
// confidenceApproveCompletedTasks handles an empty COMPLETED task list gracefully.
func TestWave66_ConfidenceApprove_EmptyCompletedTasks(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Ensure stateMachine and globalApprovalService are non-nil (set up by setupClaimTestDB).
	if stateMachine == nil {
		t.Skip("stateMachine not initialized by setupClaimTestDB")
	}
	if globalApprovalService == nil {
		t.Skip("globalApprovalService not initialized")
	}

	// No tasks in COMPLETED state — should return nil with no work.
	err := confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil error with no completed tasks, got: %v", err)
	}
}

// TestWave66_ConfidenceApprove_WithCompletedTask verifies that confidenceApproveCompletedTasks
// processes COMPLETED tasks that are older than 30 seconds.
func TestWave66_ConfidenceApprove_WithCompletedTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	if stateMachine == nil {
		t.Skip("stateMachine not initialized by setupClaimTestDB")
	}
	if globalApprovalService == nil {
		t.Skip("globalApprovalService not initialized")
	}

	// Insert a task in COMPLETED state with updated_at > 30s ago.
	oldTime := time.Now().UTC().Add(-2 * time.Minute).Format("2006-01-02 15:04:05")
	_, err := db.Exec(`
		INSERT INTO tasks (id, title, domain, project, type, priority, status, state, assigned_to, result, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, "wave66-completed-task", "Wave66 Test Task", "test-domain", "test-proj", "feature", 5,
		"completed", "COMPLETED", "agent-wave66", "", oldTime)
	if err != nil {
		t.Fatalf("insert COMPLETED task: %v", err)
	}

	// Should process without error (may auto-approve or create an approval request).
	err = confidenceApproveCompletedTasks(context.Background(), db)
	if err != nil {
		t.Errorf("expected nil error processing completed task, got: %v", err)
	}
}
