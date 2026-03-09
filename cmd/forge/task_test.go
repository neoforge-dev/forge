package main

import (
	"testing"
)

// TestTaskCommand tests task command
func TestTaskCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("task")
	if err != nil {
		t.Logf("task: %v", err)
	}
}

// TestTaskList tests task list command
func TestTaskList(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("task", "list")
	if err != nil {
		t.Logf("task list: %v", err)
	}
}

// TestTaskShowArgs tests that show requires arguments
func TestTaskShowArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("task", "show")
	if err == nil {
		t.Error("expected error for missing task ID")
	}
}

// TestTaskCreate tests task create command
func TestTaskCreate(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("task", "create", "Test Task")
	if err != nil {
		t.Logf("task create: %v", err)
	}
}

// TestTaskClaim tests task claim command
func TestTaskClaim(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("task", "claim", "TASK-001")
	if err != nil {
		t.Logf("task claim: %v", err)
	}
}

// TestTaskComplete tests task complete command
func TestTaskComplete(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("task", "complete", "TASK-001")
	if err != nil {
		t.Logf("task complete: %v", err)
	}
}
