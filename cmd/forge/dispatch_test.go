package main

import (
	"testing"
)

// TestDispatchCommand tests dispatch command
func TestDispatchCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch")
	if err != nil {
		t.Logf("dispatch: %v", err)
	}
}

// TestDispatchSendArgs tests that send requires arguments
func TestDispatchSendArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch", "send")
	if err == nil {
		t.Error("expected error for missing agent")
	}
}

// TestDispatchSend tests dispatch send command
func TestDispatchSend(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch", "send", "forge:minimax", "Test message")
	if err != nil {
		t.Logf("dispatch send: %v", err)
	}
}

// TestDispatchShowArgs tests that show requires arguments
func TestDispatchShowArgs(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch", "show")
	if err == nil {
		t.Error("expected error for missing task ID")
	}
}

// TestDispatchList tests dispatch list command
func TestDispatchList(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("dispatch", "list")
	if err != nil {
		t.Logf("dispatch list: %v", err)
	}
}

// TestDispatchFormats tests dispatch with different formats
func TestDispatchFormats(t *testing.T) {
	runner := NewTestRunner(t)

	formats := []string{"table", "json", "csv", "quiet"}
	for _, format := range formats {
		_, err := runner.Execute("dispatch", "list", "--format", format)
		if err != nil {
			t.Errorf("dispatch list --format %s failed: %v", format, err)
		}
	}
}
