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

// TestTaskLogsCommand tests task logs command exists and requires task ID
func TestTaskLogsCommand(t *testing.T) {
	runner := NewTestRunner(t)

	// Should fail without task ID
	_, err := runner.Execute("task", "logs")
	if err == nil {
		t.Error("expected error for missing task ID")
	}
}

// TestTaskLogsWithID tests task logs with a task ID
func TestTaskLogsWithID(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("task", "logs", "01JQM123ABC")
	if err != nil {
		t.Logf("task logs (daemon may be unreachable): %v", err)
	}
}

// TestTaskLogsJSON tests task logs with JSON format
func TestTaskLogsJSON(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("task", "logs", "01JQM123ABC", "--format", "json")
	if err != nil {
		t.Logf("task logs --format json (daemon may be unreachable): %v", err)
	}
}
