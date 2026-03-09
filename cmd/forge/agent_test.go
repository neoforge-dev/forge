package main

import (
	"testing"
)

// TestAgentCommand tests agent command
func TestAgentCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent")
	if err != nil {
		t.Logf("agent: %v", err)
	}
}

// TestAgentList tests agent list command
func TestAgentList(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent", "list")
	if err != nil {
		t.Logf("agent list: %v", err)
	}
}

// TestAgentStatus tests agent status command
func TestAgentStatus(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent", "status")
	if err != nil {
		t.Logf("agent status: %v", err)
	}
}

// TestAgentHealth tests agent health command
func TestAgentHealth(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent", "health", "forge:minimax")
	if err != nil {
		t.Logf("agent health: %v", err)
	}
}

// TestAgentTelemetry tests agent telemetry command
func TestAgentTelemetry(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent", "telemetry", "--status", "active")
	if err != nil {
		t.Logf("agent telemetry: %v", err)
	}
}
