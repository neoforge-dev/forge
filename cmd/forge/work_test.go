package main

import (
	"testing"
)

// TestWorkCommand tests work command
func TestWorkCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("work")
	if err != nil {
		t.Logf("work: %v", err)
	}
}

// TestWorkStatus tests work status command
func TestWorkStatus(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("work", "status")
	if err != nil {
		t.Logf("work status: %v", err)
	}
}

// TestWorkStart tests work start command
func TestWorkStart(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("work", "start")
	if err != nil {
		t.Logf("work start: %v", err)
	}
}

// TestWorkDone tests work done command
func TestWorkDone(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("work", "done")
	if err != nil {
		t.Logf("work done: %v", err)
	}
}

// TestWorkFormats tests work with different formats
func TestWorkFormats(t *testing.T) {
	runner := NewTestRunner(t)

	formats := []string{"table", "json"}
	for _, format := range formats {
		_, err := runner.Execute("work", "status", "--format", format)
		if err != nil {
			t.Errorf("work status --format %s failed: %v", format, err)
		}
	}
}
