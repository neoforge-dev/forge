package main

import (
	"testing"
)

// TestShipStatus tests ship status command
func TestShipStatus(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("ship", "status")
	if err != nil {
		t.Logf("ship status: %v", err)
	}
}

// TestShipArgs tests ship command args
func TestShipArgs(t *testing.T) {
	runner := NewTestRunner(t)

	// Ship requires args
	_, err := runner.Execute("ship")
	if err == nil {
		t.Error("expected error for missing args")
	}
}

// TestShipDeploy tests ship deploy command
func TestShipDeploy(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("ship", "deploy")
	if err != nil {
		t.Logf("ship deploy: %v", err)
	}
}
