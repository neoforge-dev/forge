//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"fmt"
	"os"
	"testing"
	"time"
)

// setupRegistryDB creates a temp file-backed DB with migrations run, returns
// an AgentRegistry and a cleanup func.
func setupRegistryDB(t *testing.T) (*AgentRegistry, func()) {
	t.Helper()
	dbPath := fmt.Sprintf("/tmp/test_registry_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("setupRegistryDB OpenDB: %v", err)
	}
	if err := MigrateUp(db); err != nil {
		db.Close()
		os.Remove(dbPath)
		t.Fatalf("setupRegistryDB MigrateUp: %v", err)
	}

	reg := NewAgentRegistry(db, 5*time.Minute)
	return reg, func() {
		db.Close()
		os.Remove(dbPath)
	}
}

// --- NewAgentRegistry ---

func TestNewAgentRegistry_Empty(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	agents := reg.ListAgents()
	if len(agents) != 0 {
		t.Errorf("expected 0 agents in fresh registry, got %d", len(agents))
	}
}

func TestNewAgentRegistry_DefaultStaleAfter(t *testing.T) {
	dbPath := fmt.Sprintf("/tmp/test_registry_default_%d.db", time.Now().UnixNano())
	db, err := OpenDB(dbPath)
	if err != nil {
		t.Fatalf("OpenDB: %v", err)
	}
	defer db.Close()
	defer os.Remove(dbPath)
	if err := MigrateUp(db); err != nil {
		t.Fatalf("MigrateUp: %v", err)
	}

	// staleAfter = 0 should default to 5 minutes internally (no panic).
	reg := NewAgentRegistry(db, 0)
	if reg == nil {
		t.Fatal("NewAgentRegistry returned nil")
	}
}

// --- Register ---

func TestRegister_NewAgent(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{
		ID:     "agent-001",
		Name:   "glm",
		Node:   "prya",
		Role:   "fleet",
		Tier:   "standard",
		Status: "active",
	}
	if err := reg.Register(a); err != nil {
		t.Fatalf("Register: %v", err)
	}

	got, ok := reg.GetAgent("agent-001")
	if !ok {
		t.Fatal("GetAgent returned false after Register")
	}
	if got.Name != "glm" {
		t.Errorf("Name = %q, want glm", got.Name)
	}
	if got.Node != "prya" {
		t.Errorf("Node = %q, want prya", got.Node)
	}
}

func TestRegister_UpdateExisting(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{ID: "agent-002", Name: "kimi", Node: "prya", Status: "active"}
	if err := reg.Register(a); err != nil {
		t.Fatalf("Register initial: %v", err)
	}

	// Re-register with updated status.
	a.Status = "idle"
	if err := reg.Register(a); err != nil {
		t.Fatalf("Register update: %v", err)
	}

	got, ok := reg.GetAgent("agent-002")
	if !ok {
		t.Fatal("GetAgent: not found after update")
	}
	if got.Status != "idle" {
		t.Errorf("Status = %q, want idle", got.Status)
	}
}

func TestRegister_NilAgent(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	err := reg.Register(nil)
	if err == nil {
		t.Fatal("expected error for nil agent, got nil")
	}
}

func TestRegister_EmptyID(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	err := reg.Register(&Agent{ID: "", Name: "nobody"})
	if err == nil {
		t.Fatal("expected error for empty agent ID")
	}
}

func TestRegister_WithCapabilities(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{
		ID:           "agent-003",
		Name:         "gemini",
		Node:         "sati",
		Status:       "active",
		Capabilities: []string{"go", "python", "typescript"},
	}
	if err := reg.Register(a); err != nil {
		t.Fatalf("Register: %v", err)
	}

	got, ok := reg.GetAgent("agent-003")
	if !ok {
		t.Fatal("GetAgent not found")
	}
	if len(got.Capabilities) != 3 {
		t.Errorf("Capabilities len = %d, want 3", len(got.Capabilities))
	}
}

// --- Unregister ---

func TestUnregister_ExistingAgent(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{ID: "agent-004", Name: "pi", Node: "prya", Status: "active"}
	if err := reg.Register(a); err != nil {
		t.Fatalf("Register: %v", err)
	}

	if err := reg.Unregister("agent-004"); err != nil {
		t.Fatalf("Unregister: %v", err)
	}

	_, ok := reg.GetAgent("agent-004")
	if ok {
		t.Error("expected agent not found after Unregister")
	}
}

func TestUnregister_EmptyID(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	// Empty ID is a no-op and must not error.
	if err := reg.Unregister(""); err != nil {
		t.Fatalf("Unregister empty ID: %v", err)
	}
}

func TestUnregister_NonExistentID(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	// Unregistering an unknown ID must not error (DELETE WHERE id=? affects 0 rows).
	if err := reg.Unregister("does-not-exist"); err != nil {
		t.Fatalf("Unregister non-existent: %v", err)
	}
}

// --- UpdateStatus ---

func TestUpdateStatus_Known(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{ID: "agent-005", Name: "minimax", Node: "prya", Status: "active"}
	reg.Register(a)

	if err := reg.UpdateStatus("agent-005", "idle"); err != nil {
		t.Fatalf("UpdateStatus: %v", err)
	}

	got, _ := reg.GetAgent("agent-005")
	if got.Status != "idle" {
		t.Errorf("Status = %q, want idle", got.Status)
	}
}

func TestUpdateStatus_EmptyID(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	// Empty ID is a no-op; must not error.
	if err := reg.UpdateStatus("", "idle"); err != nil {
		t.Fatalf("UpdateStatus empty ID: %v", err)
	}
}

// --- UpdateContext ---

func TestUpdateContext_ValidPct(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{ID: "agent-006", Name: "glm", Node: "prya", Status: "active"}
	reg.Register(a)

	if err := reg.UpdateContext("agent-006", 42.5); err != nil {
		t.Fatalf("UpdateContext: %v", err)
	}

	got, _ := reg.GetAgent("agent-006")
	if got.ContextPct != 42.5 {
		t.Errorf("ContextPct = %f, want 42.5", got.ContextPct)
	}
}

func TestUpdateContext_ClampNegative(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{ID: "agent-007", Name: "glm", Node: "prya", Status: "active"}
	reg.Register(a)

	if err := reg.UpdateContext("agent-007", -10); err != nil {
		t.Fatalf("UpdateContext negative: %v", err)
	}

	got, _ := reg.GetAgent("agent-007")
	if got.ContextPct < 0 {
		t.Errorf("ContextPct should be clamped to 0, got %f", got.ContextPct)
	}
}

func TestUpdateContext_ClampOver100(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{ID: "agent-008", Name: "glm", Node: "prya", Status: "active"}
	reg.Register(a)

	if err := reg.UpdateContext("agent-008", 150); err != nil {
		t.Fatalf("UpdateContext >100: %v", err)
	}

	got, _ := reg.GetAgent("agent-008")
	if got.ContextPct > 100 {
		t.Errorf("ContextPct should be clamped to 100, got %f", got.ContextPct)
	}
}

func TestUpdateContext_EmptyID(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	if err := reg.UpdateContext("", 50); err != nil {
		t.Fatalf("UpdateContext empty ID: %v", err)
	}
}

// --- Heartbeat ---

func TestHeartbeat_UpdatesLastActivity(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{
		ID:     "agent-009",
		Name:   "kimi",
		Node:   "prya",
		Status: "active",
	}
	reg.Register(a)

	if err := reg.Heartbeat("agent-009", "busy", 55.0); err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}

	got, ok := reg.GetAgent("agent-009")
	if !ok {
		t.Fatal("GetAgent not found after Heartbeat")
	}
	if got.Status != "busy" {
		t.Errorf("Status = %q, want busy", got.Status)
	}
	if got.ContextPct != 55.0 {
		t.Errorf("ContextPct = %f, want 55.0", got.ContextPct)
	}
}

func TestHeartbeat_EmptyID(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	if err := reg.Heartbeat("", "active", 0); err != nil {
		t.Fatalf("Heartbeat empty ID: %v", err)
	}
}

func TestHeartbeat_ClampContext(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{ID: "agent-010", Name: "kimi", Node: "prya", Status: "active"}
	reg.Register(a)

	// Pct > 100 should be clamped.
	if err := reg.Heartbeat("agent-010", "active", 200); err != nil {
		t.Fatalf("Heartbeat >100 pct: %v", err)
	}
	got, _ := reg.GetAgent("agent-010")
	if got.ContextPct > 100 {
		t.Errorf("ContextPct should be clamped, got %f", got.ContextPct)
	}
}

// --- GetAgent ---

func TestGetAgent_NotFound(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	_, ok := reg.GetAgent("nonexistent")
	if ok {
		t.Error("expected false for unknown agent ID")
	}
}

func TestGetAgent_ReturnsCopy(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	a := &Agent{ID: "agent-011", Name: "glm", Node: "prya", Status: "active", Capabilities: []string{"go"}}
	reg.Register(a)

	got, _ := reg.GetAgent("agent-011")
	// Mutate the returned copy — original must not change.
	got.Capabilities = append(got.Capabilities, "extra")

	got2, _ := reg.GetAgent("agent-011")
	if len(got2.Capabilities) != 1 {
		t.Errorf("expected 1 capability in original, got %d (copy not isolated)", len(got2.Capabilities))
	}
}

// --- ListAgents ---

func TestListAgents_Multiple(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	for i := 0; i < 5; i++ {
		a := &Agent{
			ID:     fmt.Sprintf("list-agent-%d", i),
			Name:   fmt.Sprintf("agent-%d", i),
			Node:   "prya",
			Status: "active",
		}
		if err := reg.Register(a); err != nil {
			t.Fatalf("Register agent-%d: %v", i, err)
		}
	}

	agents := reg.ListAgents()
	if len(agents) != 5 {
		t.Errorf("ListAgents = %d, want 5", len(agents))
	}
}

// --- GetActiveAgents ---

func TestGetActiveAgents_FiltersByStatus(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	active := &Agent{ID: "active-1", Name: "a1", Node: "prya", Status: "active"}
	idle := &Agent{ID: "idle-1", Name: "i1", Node: "prya", Status: "idle"}
	reg.Register(active)
	reg.Register(idle)

	got := reg.GetActiveAgents()
	if len(got) != 1 {
		t.Errorf("GetActiveAgents = %d, want 1", len(got))
	}
	if got[0].ID != "active-1" {
		t.Errorf("got[0].ID = %q, want active-1", got[0].ID)
	}
}

func TestGetActiveAgents_Empty(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	got := reg.GetActiveAgents()
	if got == nil {
		// nil is acceptable for empty result
		return
	}
	if len(got) != 0 {
		t.Errorf("GetActiveAgents on empty registry = %d, want 0", len(got))
	}
}

// --- StartCleanup / StopCleanup ---

func TestStartCleanup_RunsWithoutPanic(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	// Start with a very short interval — just verify it doesn't panic.
	reg.StartCleanup(50 * time.Millisecond)

	// Let it tick once.
	time.Sleep(100 * time.Millisecond)

	// StopCleanup must also not panic.
	reg.StopCleanup()
}

func TestStartCleanup_IdempotentOnce(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	// StartCleanup uses sync.Once — calling twice must not panic or duplicate goroutines.
	reg.StartCleanup(100 * time.Millisecond)
	reg.StartCleanup(100 * time.Millisecond)

	time.Sleep(50 * time.Millisecond)
	reg.StopCleanup()
}

func TestStartCleanup_RemovesStaleAgents(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	// Manually register an agent with an old LastActivity.
	staleTime := time.Now().UTC().Add(-10 * time.Minute)
	a := &Agent{
		ID:           "stale-agent-001",
		Name:         "stale",
		Node:         "prya",
		Status:       "active",
		LastActivity: staleTime,
	}
	if err := reg.Register(a); err != nil {
		t.Fatalf("Register stale agent: %v", err)
	}

	// Run cleanup directly (not via goroutine) to avoid timing issues.
	if err := reg.cleanupStaleAgents(); err != nil {
		t.Fatalf("cleanupStaleAgents: %v", err)
	}

	_, ok := reg.GetAgent("stale-agent-001")
	if ok {
		t.Error("expected stale agent to be removed by cleanup")
	}
}

func TestStartCleanup_KeepsFreshAgents(t *testing.T) {
	reg, cleanup := setupRegistryDB(t)
	defer cleanup()

	// Register a fresh agent (LastActivity = now).
	a := &Agent{
		ID:     "fresh-agent-001",
		Name:   "fresh",
		Node:   "prya",
		Status: "active",
	}
	if err := reg.Register(a); err != nil {
		t.Fatalf("Register: %v", err)
	}

	if err := reg.cleanupStaleAgents(); err != nil {
		t.Fatalf("cleanupStaleAgents: %v", err)
	}

	_, ok := reg.GetAgent("fresh-agent-001")
	if !ok {
		t.Error("fresh agent was incorrectly removed by cleanup")
	}
}
