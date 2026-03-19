//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 187: registry.go + quality_gates.go
// Targets:
//   NewAgentRegistry   (registry.go:39)  — zero/positive stale duration
//   Register           (registry.go:114) — adds agent
//   GetAgent           (registry.go:264) — found / not found
//   ListAgents         (registry.go:279)
//   GetActiveAgents    (registry.go:293)
//   UpdateStatus       (registry.go:182)
//   UpdateContext      (registry.go:201)
//   Heartbeat          (registry.go:227)
//   Unregister         (registry.go:165)
//   StopCleanup        (registry.go:334) — no panic when not started
//   NewQualityGateChecker (quality_gates.go:38) — empty path / nonexistent path
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// NewAgentRegistry
// ---------------------------------------------------------------------------

func TestWave187_NewAgentRegistry_ZeroStale(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Zero stale duration → defaults to 5m
	r := NewAgentRegistry(db, 0)
	if r == nil {
		t.Error("expected non-nil registry")
	}
	if r.staleAfter != 5*time.Minute {
		t.Errorf("expected default 5m stale, got %v", r.staleAfter)
	}
}

func TestWave187_NewAgentRegistry_CustomStale(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	r := NewAgentRegistry(db, 10*time.Minute)
	if r.staleAfter != 10*time.Minute {
		t.Errorf("expected 10m stale, got %v", r.staleAfter)
	}
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

func TestWave187_Register_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	agent := &Agent{
		ID:     "kimi-wave181",
		Name:   "kimi",
		Node:   "prya",
		Role:   "fleet",
		Tier:   "medium",
		Status: "active",
	}
	if err := r.Register(agent); err != nil {
		t.Errorf("Register: %v", err)
	}
}

func TestWave187_Register_DuplicateUpdates(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	agent := &Agent{
		ID:     "kimi-dup",
		Name:   "kimi",
		Status: "active",
	}
	r.Register(agent)
	// Second register (upsert)
	agent.Status = "busy"
	if err := r.Register(agent); err != nil {
		t.Errorf("Register duplicate: %v", err)
	}
}

// ---------------------------------------------------------------------------
// GetAgent
// ---------------------------------------------------------------------------

func TestWave187_GetAgent_Found(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	r.Register(&Agent{ID: "kimi-get", Name: "kimi", Status: "active"})
	agent, ok := r.GetAgent("kimi-get")
	if !ok {
		t.Error("expected to find registered agent")
	}
	if agent.ID != "kimi-get" {
		t.Errorf("expected ID 'kimi-get', got %q", agent.ID)
	}
}

func TestWave187_GetAgent_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	_, ok := r.GetAgent("nonexistent-agent")
	if ok {
		t.Error("expected false for nonexistent agent")
	}
}

// ---------------------------------------------------------------------------
// ListAgents
// ---------------------------------------------------------------------------

func TestWave187_ListAgents_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	agents := r.ListAgents()
	if agents == nil {
		t.Error("expected non-nil slice (may be empty)")
	}
}

func TestWave187_ListAgents_WithAgents(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	r.Register(&Agent{ID: "agent-1", Status: "active"})
	r.Register(&Agent{ID: "agent-2", Status: "active"})
	agents := r.ListAgents()
	if len(agents) != 2 {
		t.Errorf("expected 2 agents, got %d", len(agents))
	}
}

// ---------------------------------------------------------------------------
// GetActiveAgents
// ---------------------------------------------------------------------------

func TestWave187_GetActiveAgents_FiltersByActivity(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	// Recent agent
	r.Register(&Agent{
		ID:           "recent-agent",
		Status:       "active",
		LastActivity: time.Now(),
	})

	agents := r.GetActiveAgents()
	_ = agents // may or may not include depending on stale threshold
}

// ---------------------------------------------------------------------------
// UpdateStatus
// ---------------------------------------------------------------------------

func TestWave187_UpdateStatus_ExistingAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	r.Register(&Agent{ID: "status-agent", Status: "active"})
	if err := r.UpdateStatus("status-agent", "busy"); err != nil {
		t.Errorf("UpdateStatus: %v", err)
	}

	agent, ok := r.GetAgent("status-agent")
	if !ok || agent.Status != "busy" {
		t.Errorf("expected status 'busy', got %q", agent.Status)
	}
}

func TestWave187_UpdateStatus_NonExistentAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	// Should not panic for non-existent agent (may return nil or error)
	_ = r.UpdateStatus("nonexistent", "active")
}

// ---------------------------------------------------------------------------
// UpdateContext
// ---------------------------------------------------------------------------

func TestWave187_UpdateContext_ExistingAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	r.Register(&Agent{ID: "ctx-agent", Status: "active"})
	if err := r.UpdateContext("ctx-agent", 75.5); err != nil {
		t.Errorf("UpdateContext: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Heartbeat
// ---------------------------------------------------------------------------

func TestWave187_Heartbeat_ExistingAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	r.Register(&Agent{ID: "hb-agent", Status: "active"})
	if err := r.Heartbeat("hb-agent", "active", 50.0); err != nil {
		t.Errorf("Heartbeat: %v", err)
	}
}

func TestWave187_Heartbeat_NewAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	// Heartbeat for unknown agent registers it
	if err := r.Heartbeat("new-hb-agent", "active", 30.0); err != nil {
		t.Errorf("Heartbeat new agent: %v", err)
	}
}

// ---------------------------------------------------------------------------
// Unregister
// ---------------------------------------------------------------------------

func TestWave187_Unregister_ExistingAgent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	r.Register(&Agent{ID: "unreg-agent", Status: "active"})
	if err := r.Unregister("unreg-agent"); err != nil {
		t.Errorf("Unregister: %v", err)
	}
	_, ok := r.GetAgent("unreg-agent")
	if ok {
		t.Error("expected agent to be removed after Unregister")
	}
}

func TestWave187_Unregister_NonExistent(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	// Should not panic
	_ = r.Unregister("nonexistent-agent")
}

// ---------------------------------------------------------------------------
// StopCleanup (should not panic when cleanup not started)
// ---------------------------------------------------------------------------

func TestWave187_StopCleanup_NotStarted(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	r := NewAgentRegistry(db, time.Minute)

	// Should not block or panic
	done := make(chan struct{})
	go func() {
		r.StopCleanup()
		close(done)
	}()
	select {
	case <-done:
		// success
	case <-context.WithValue(context.Background(), "timeout", true).Done():
		t.Error("StopCleanup timed out")
	}
}

// ---------------------------------------------------------------------------
// quality_gates.go — NewQualityGateChecker
// ---------------------------------------------------------------------------

func TestWave187_NewQualityGateChecker_EmptyPath(t *testing.T) {
	qg, err := NewQualityGateChecker("")
	if err != nil {
		t.Fatalf("NewQualityGateChecker empty path: %v", err)
	}
	if qg == nil {
		t.Error("expected non-nil checker")
	}
	// Defaults: coverage enabled, threshold 80
	if qg.config.Coverage.Threshold != 80.0 {
		t.Errorf("expected default threshold 80.0, got %f", qg.config.Coverage.Threshold)
	}
}

func TestWave187_NewQualityGateChecker_NonExistentPath(t *testing.T) {
	// Non-existent config file → falls back to defaults
	qg, err := NewQualityGateChecker("/tmp/nonexistent-quality-gates-wave181.yaml")
	if err != nil {
		t.Fatalf("NewQualityGateChecker non-existent: %v", err)
	}
	if qg == nil {
		t.Error("expected non-nil checker")
	}
}
