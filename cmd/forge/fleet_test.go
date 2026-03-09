package main

import (
	"os"
	"testing"
	"time"
)

// TestFleetCommand tests fleet command
func TestFleetCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("fleet")
	if err != nil {
		t.Logf("fleet: %v", err)
	}
}

// TestFleetList tests fleet list command
func TestFleetList(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("fleet", "list")
	if err != nil {
		t.Logf("fleet list: %v", err)
	}
}

// TestFleetStatus tests fleet status command
func TestFleetStatus(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("fleet", "status")
	if err != nil {
		t.Logf("fleet status: %v", err)
	}
}

// TestFleetStateManager tests fleet state manager creation
func TestFleetStateManager(t *testing.T) {
	fsm := NewFleetStateManager()
	if fsm == nil {
		t.Fatal("expected fleet state manager")
	}
	if fsm.StateDir == "" {
		t.Error("expected state dir")
	}
}

// TestFleetStateManagerGetAgentsEmpty tests getting agents from empty dir
func TestFleetStateManagerGetAgentsEmpty(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Set FORGE_ROOT to isolate from main repo
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	// Create .forge directory structure
	os.MkdirAll(".forge/workflow/state", 0755)

	fsm := NewFleetStateManager()
	agents, err := fsm.GetAgents()
	if err != nil {
		t.Fatalf("GetAgents failed: %v", err)
	}
	if len(agents) != 0 {
		t.Error("expected empty agents")
	}
}

// TestFleetStateManagerSaveAndGetAgent tests saving and getting an agent
func TestFleetStateManagerSaveAndGetAgent(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Set FORGE_ROOT to isolate from main repo
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	// Create .forge directory structure
	os.MkdirAll(".forge/workflow/state", 0755)

	fsm := NewFleetStateManager()

	now := time.Now()
	agent := &FleetAgent{
		ID:              "forge:test",
		State:           StateIdle,
		LastStateChange: now,
	}

	err := fsm.SaveAgent(agent)
	if err != nil {
		t.Fatalf("SaveAgent failed: %v", err)
	}

	// Get the agent back
	got, err := fsm.GetAgent("forge:test")
	if err != nil {
		t.Fatalf("GetAgent failed: %v", err)
	}
	if got.ID != "forge:test" {
		t.Error("expected forge:test")
	}
}

// TestFleetStateManagerGetAgentNotFound tests getting non-existent agent
func TestFleetStateManagerGetAgentNotFound(t *testing.T) {
	tmpDir := t.TempDir()
	oldWd, _ := os.Getwd()
	os.Chdir(tmpDir)
	defer os.Chdir(oldWd)

	// Set FORGE_ROOT to isolate from main repo
	os.Setenv("FORGE_ROOT", tmpDir)
	defer os.Unsetenv("FORGE_ROOT")

	os.MkdirAll(".forge/workflow/state", 0755)

	fsm := NewFleetStateManager()
	_, err := fsm.GetAgent("nonexistent")
	if err == nil {
		t.Error("expected error for nonexistent agent")
	}
}
