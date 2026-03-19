//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Wave 188: context.go pure functions + getters, plan.go store methods
// Targets:
//   generateULID             (context.go:93)  — non-empty, unique
//   generateTaskID           (context.go:102) — format TASK-X-X-NNN
//   ContextEnvelope getters  (context.go:533) — Get{ID,AgentID,Domain,Project,TaskID,Summary}
//   NewContextManager        (context.go:70)  — non-nil
//   NewPlanManager           (plan.go:42)     — non-nil
//   PlanManager.CreatePlan   (plan.go:46)     — creates plan, returns ID
//   PlanManager.GetPlanHistory (plan.go:137)  — empty
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// generateULID
// ---------------------------------------------------------------------------

func TestWave188_GenerateULID_NonEmpty(t *testing.T) {
	id := generateULID()
	if id == "" {
		t.Error("expected non-empty ULID")
	}
}

func TestWave188_GenerateULID_Unique(t *testing.T) {
	id1 := generateULID()
	time.Sleep(time.Millisecond) // ensure different timestamp
	id2 := generateULID()
	if id1 == id2 {
		t.Error("expected unique ULIDs")
	}
}

// ---------------------------------------------------------------------------
// generateTaskID
// ---------------------------------------------------------------------------

func TestWave188_GenerateTaskID_Format(t *testing.T) {
	id := generateTaskID()
	if !strings.HasPrefix(id, "TASK-") {
		t.Errorf("expected TASK- prefix, got %q", id)
	}
	// Format: TASK-ADJECTIVE-NOUN-NNN (3 dashes minimum)
	parts := strings.Split(id, "-")
	if len(parts) < 4 {
		t.Errorf("expected at least 4 dash-separated parts, got %d: %q", len(parts), id)
	}
}

func TestWave188_GenerateTaskID_Uppercase(t *testing.T) {
	id := generateTaskID()
	if id != strings.ToUpper(id) {
		t.Errorf("expected uppercase ID, got %q", id)
	}
}

// ---------------------------------------------------------------------------
// ContextEnvelope getters
// ---------------------------------------------------------------------------

func TestWave188_ContextEnvelope_Getters(t *testing.T) {
	now := time.Now()
	e := &ContextEnvelope{
		ID:        "env-123",
		AgentID:   "kimi-1",
		Domain:    "forge",
		Project:   "test-project",
		TaskID:    "task-456",
		Summary:   "test summary",
		CreatedAt: now,
		ExpiresAt: now.Add(time.Hour),
	}

	if e.GetID() != "env-123" {
		t.Errorf("GetID: expected 'env-123', got %q", e.GetID())
	}
	if e.GetAgentID() != "kimi-1" {
		t.Errorf("GetAgentID: expected 'kimi-1', got %q", e.GetAgentID())
	}
	if e.GetDomain() != "forge" {
		t.Errorf("GetDomain: expected 'forge', got %q", e.GetDomain())
	}
	if e.GetProject() != "test-project" {
		t.Errorf("GetProject: expected 'test-project', got %q", e.GetProject())
	}
	if e.GetTaskID() != "task-456" {
		t.Errorf("GetTaskID: expected 'task-456', got %q", e.GetTaskID())
	}
	if e.GetSummary() != "test summary" {
		t.Errorf("GetSummary: expected 'test summary', got %q", e.GetSummary())
	}
	if e.GetCreatedAt().IsZero() {
		t.Error("GetCreatedAt: expected non-zero time")
	}
	if e.GetExpiresAt().IsZero() {
		t.Error("GetExpiresAt: expected non-zero time")
	}
}

// ---------------------------------------------------------------------------
// NewContextManager
// ---------------------------------------------------------------------------

func TestWave188_NewContextManager_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	cm := testContextManager(t, db, t.TempDir())
	if cm == nil {
		t.Error("expected non-nil ContextManager")
	}
	defer cm.Stop()
}

// ---------------------------------------------------------------------------
// plan.go — NewPlanManager
// ---------------------------------------------------------------------------

func TestWave188_NewPlanManager_NonNil(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	pm := NewPlanManager(db)
	if pm == nil {
		t.Error("expected non-nil PlanManager")
	}
}

// ---------------------------------------------------------------------------
// PlanManager.CreatePlan
// ---------------------------------------------------------------------------

func TestWave188_PlanManager_CreatePlan(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	pm := NewPlanManager(db)

	id, err := pm.CreatePlan(context.Background(), "task-plan-182", "Do all the things", "initial plan")
	if err != nil {
		t.Fatalf("CreatePlan: %v", err)
	}
	if id == "" {
		t.Error("expected non-empty plan ID")
	}
}

// ---------------------------------------------------------------------------
// PlanManager.GetPlanHistory
// ---------------------------------------------------------------------------

func TestWave188_PlanManager_GetPlanHistory_Empty(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	pm := NewPlanManager(db)

	history, err := pm.GetPlanHistory(context.Background(), "nonexistent-task")
	if err != nil {
		t.Errorf("GetPlanHistory: %v", err)
	}
	if len(history) != 0 {
		t.Errorf("expected empty history, got %d", len(history))
	}
}

func TestWave188_PlanManager_GetPlanHistory_WithPlan(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	pm := NewPlanManager(db)

	pm.CreatePlan(context.Background(), "task-hist", "Plan A", "initial")
	history, err := pm.GetPlanHistory(context.Background(), "task-hist")
	if err != nil {
		t.Errorf("GetPlanHistory: %v", err)
	}
	if len(history) == 0 {
		t.Error("expected non-empty history after CreatePlan")
	}
}
