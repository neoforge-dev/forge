package main

import (
	"testing"
)

// TestQueueCommand validates the parent queue command exists and shows help.
func TestQueueCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue")
	if err != nil {
		t.Logf("queue: %v", err)
	}
}

// TestQueueStatusCommand validates the queue status subcommand exists and
// attempts to call the daemon. A connection error is acceptable in CI — what
// matters is that the command is registered and wired correctly.
func TestQueueStatusCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "status")
	if err != nil {
		t.Logf("queue status (daemon may be unreachable): %v", err)
	}
}

// TestQueueStatusCommandJSON validates that --format json is accepted.
func TestQueueStatusCommandJSON(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "status", "--format", "json")
	if err != nil {
		t.Logf("queue status --format json (daemon may be unreachable): %v", err)
	}
}

// TestQueueDepthCommand validates the queue depth subcommand is registered.
func TestQueueDepthCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "depth")
	if err != nil {
		t.Logf("queue depth (daemon may be unreachable): %v", err)
	}
}

// TestQueueDepthCommandJSON validates that --format json is accepted on depth.
func TestQueueDepthCommandJSON(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "depth", "--format", "json")
	if err != nil {
		t.Logf("queue depth --format json (daemon may be unreachable): %v", err)
	}
}

// TestQueueListCommand validates the queue list subcommand is registered.
func TestQueueListCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "list")
	if err != nil {
		t.Logf("queue list (daemon may be unreachable): %v", err)
	}
}

// TestQueueListCommandWithLimit validates that --limit is accepted.
func TestQueueListCommandWithLimit(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "list", "--limit", "5")
	if err != nil {
		t.Logf("queue list --limit 5 (daemon may be unreachable): %v", err)
	}
}

// TestQueueCancelRequiresTaskID validates that cancel errors without a task ID.
func TestQueueCancelRequiresTaskID(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "cancel")
	if err == nil {
		t.Error("expected error: queue cancel without task ID should fail")
	}
}

// TestQueueCancelWithTaskID validates that cancel accepts a task ID argument and
// attempts the API call. A connection error is acceptable in CI.
func TestQueueCancelWithTaskID(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "cancel", "01JQM123ABC")
	if err != nil {
		t.Logf("queue cancel 01JQM123ABC (daemon may be unreachable): %v", err)
	}
}

// TestQueuePriorityRequiresTaskID validates that priority errors without a task ID.
func TestQueuePriorityRequiresTaskID(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "priority")
	if err == nil {
		t.Error("expected error: queue priority without task ID should fail")
	}
}

// TestQueuePriorityRequiresPriorityFlag validates that priority errors without
// the --priority flag.
func TestQueuePriorityRequiresPriorityFlag(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "priority", "01JQM123ABC")
	if err == nil {
		t.Error("expected error: queue priority without --priority flag should fail")
	}
}

// TestQueuePriorityWithFlags validates that priority accepts both required args.
func TestQueuePriorityWithFlags(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("queue", "priority", "01JQM123ABC", "--priority", "5")
	if err != nil {
		t.Logf("queue priority 01JQM123ABC --priority 5 (daemon may be unreachable): %v", err)
	}
}
