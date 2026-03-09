//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"database/sql"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// wave68SetupRegistryDB creates an in-memory DB with the agents table wired up.
func wave68SetupRegistryDB(t *testing.T) (*sql.DB, func()) {
	t.Helper()
	db, cleanup := setupClaimTestDB(t)
	return db, cleanup
}

// TestWave68_UpdateContext_ClampAbove100 verifies UpdateContext clamps pct > 100 to 100.
func TestWave68_UpdateContext_ClampAbove100(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)

	// Register an agent first.
	agent := &Agent{
		ID:     "wave68-agent-clamp",
		Name:   "test",
		Node:   "prya",
		Role:   "worker",
		Tier:   "lightweight",
		Status: "idle",
	}
	if err := r.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	// Set pct > 100 — should be clamped to 100.
	if err := r.UpdateContext("wave68-agent-clamp", 150.0); err != nil {
		t.Fatalf("UpdateContext: %v", err)
	}

	got, ok := r.GetAgent("wave68-agent-clamp")
	if !ok {
		t.Fatal("agent not found after UpdateContext")
	}
	if got.ContextPct != 100.0 {
		t.Errorf("expected ContextPct=100, got %.1f", got.ContextPct)
	}
}

// TestWave68_UpdateContext_ClampBelow0 verifies UpdateContext clamps pct < 0 to 0.
func TestWave68_UpdateContext_ClampBelow0(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)

	agent := &Agent{
		ID:     "wave68-agent-neg",
		Name:   "test-neg",
		Node:   "prya",
		Role:   "worker",
		Tier:   "lightweight",
		Status: "idle",
	}
	if err := r.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	if err := r.UpdateContext("wave68-agent-neg", -10.0); err != nil {
		t.Fatalf("UpdateContext: %v", err)
	}

	got, ok := r.GetAgent("wave68-agent-neg")
	if !ok {
		t.Fatal("agent not found after UpdateContext")
	}
	if got.ContextPct != 0.0 {
		t.Errorf("expected ContextPct=0, got %.1f", got.ContextPct)
	}
}

// TestWave68_UpdateContext_EmptyID verifies UpdateContext is a no-op for empty agentID.
func TestWave68_UpdateContext_EmptyID(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)
	if err := r.UpdateContext("", 50.0); err != nil {
		t.Errorf("UpdateContext with empty ID should return nil, got: %v", err)
	}
}

// TestWave68_UpdateStatus_AgentNotInMemory verifies UpdateStatus updates DB even
// when the agent is not in the in-memory map.
func TestWave68_UpdateStatus_AgentNotInMemory(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)

	// Update status for a non-existent agent — should not error (UPDATE 0 rows is OK).
	if err := r.UpdateStatus("nonexistent-agent-wave68", "active"); err != nil {
		t.Errorf("UpdateStatus for non-existent agent: %v", err)
	}
}

// TestWave68_Registry_GetActiveAgents verifies GetActiveAgents filters by status="active".
func TestWave68_Registry_GetActiveAgents(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)

	agents := []*Agent{
		{ID: "wave68-active-1", Name: "a1", Node: "prya", Role: "worker", Tier: "lightweight", Status: "active"},
		{ID: "wave68-idle-1", Name: "a2", Node: "prya", Role: "worker", Tier: "lightweight", Status: "idle"},
		{ID: "wave68-active-2", Name: "a3", Node: "sati", Role: "worker", Tier: "medium", Status: "active"},
	}
	for _, a := range agents {
		if err := r.Register(a); err != nil {
			t.Fatalf("Register %s: %v", a.ID, err)
		}
	}

	active := r.GetActiveAgents()
	if len(active) != 2 {
		t.Errorf("expected 2 active agents, got %d", len(active))
	}
	for _, a := range active {
		if a.Status != "active" {
			t.Errorf("expected status=active, got %s", a.Status)
		}
	}
}

// TestWave68_Registry_Heartbeat_ClampPct verifies Heartbeat clamps pct values.
func TestWave68_Registry_Heartbeat_ClampPct(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)

	agent := &Agent{
		ID:     "wave68-hb-clamp",
		Name:   "hb-test",
		Node:   "prya",
		Role:   "worker",
		Tier:   "lightweight",
		Status: "idle",
	}
	if err := r.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	// Pct > 100 should be clamped.
	if err := r.Heartbeat("wave68-hb-clamp", "busy", 200.0); err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}

	got, ok := r.GetAgent("wave68-hb-clamp")
	if !ok {
		t.Fatal("agent not found")
	}
	if got.ContextPct != 100.0 {
		t.Errorf("expected ContextPct clamped to 100, got %.1f", got.ContextPct)
	}
	if got.Status != "busy" {
		t.Errorf("expected status=busy, got %s", got.Status)
	}
}

// TestWave68_Registry_Unregister verifies agents are removed from memory and DB.
func TestWave68_Registry_Unregister(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)

	agent := &Agent{
		ID:     "wave68-unreg",
		Name:   "unreg-test",
		Node:   "prya",
		Role:   "worker",
		Tier:   "lightweight",
		Status: "idle",
	}
	if err := r.Register(agent); err != nil {
		t.Fatalf("Register: %v", err)
	}

	if _, ok := r.GetAgent("wave68-unreg"); !ok {
		t.Fatal("agent should exist before Unregister")
	}

	if err := r.Unregister("wave68-unreg"); err != nil {
		t.Fatalf("Unregister: %v", err)
	}

	if _, ok := r.GetAgent("wave68-unreg"); ok {
		t.Error("agent should not exist after Unregister")
	}
}

// TestWave68_Registry_Unregister_EmptyID verifies Unregister("") is a no-op.
func TestWave68_Registry_Unregister_EmptyID(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)
	if err := r.Unregister(""); err != nil {
		t.Errorf("Unregister('') should return nil, got: %v", err)
	}
}

// TestWave68_Registry_ListAgents verifies ListAgents returns all registered agents.
func TestWave68_Registry_ListAgents(t *testing.T) {
	db, cleanup := wave68SetupRegistryDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 5*time.Minute)

	for i := 0; i < 3; i++ {
		a := &Agent{
			ID:     "wave68-list-agent-" + string(rune('A'+i)),
			Name:   "list-test",
			Node:   "prya",
			Role:   "worker",
			Tier:   "lightweight",
			Status: "idle",
		}
		if err := r.Register(a); err != nil {
			t.Fatalf("Register agent %d: %v", i, err)
		}
	}

	agents := r.ListAgents()
	if len(agents) < 3 {
		t.Errorf("expected at least 3 agents, got %d", len(agents))
	}
}
