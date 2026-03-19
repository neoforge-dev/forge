//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"strings"
	"testing"
)

func TestWave132_NewPlanManager(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	pm := NewPlanManager(db)
	if pm == nil {
		t.Error("expected non-nil PlanManager")
	}
}

func TestWave132_CreatePlan_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup() // close before use
	pm := NewPlanManager(db)
	_, err := pm.CreatePlan(context.Background(), "task-1", "plan text", "reason")
	if err == nil {
		t.Error("expected error for closed DB")
	}
	t.Logf("CreatePlan closed DB: %v", err)
}

func TestWave132_CreatePlan_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create a task first with all required fields
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, status) 
		VALUES (?, ?, ?, ?, ?)`, 
		"task-wave132", "test-domain", "test-proj", "feature", "queued")
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	pm := NewPlanManager(db)
	planID, err := pm.CreatePlan(context.Background(), "task-wave132", "my plan", "testing")
	if err != nil {
		t.Errorf("CreatePlan failed: %v", err)
		return
	}
	if planID == "" {
		t.Error("expected non-empty plan ID")
	}

	// Verify task update
	var status TaskStatus
	err = db.QueryRow("SELECT status FROM tasks WHERE id = ?", "task-wave132").Scan(&status)
	if err != nil {
		t.Errorf("failed to query task status: %v", err)
	}
	if status != TaskStatusPlanned {
		t.Errorf("expected status %s, got %s", TaskStatusPlanned, status)
	}
}

func TestWave132_RevisePlan_TaskNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	pm := NewPlanManager(db)
	_, err := pm.RevisePlan(context.Background(), "nonexistent-task", "new plan", "test")
	if err == nil {
		t.Error("expected error for missing task")
	}
	t.Logf("RevisePlan task not found: %v", err)
}

func TestWave132_RevisePlan_ClosedDB(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	cleanup()
	pm := NewPlanManager(db)
	_, err := pm.RevisePlan(context.Background(), "task-wave132", "new plan", "test")
	if err == nil {
		t.Error("expected error")
	}
}

func TestWave132_RevisePlan_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Create a task with initial plan
	_, err := db.Exec(`INSERT INTO tasks (id, domain, project, type, status, plan_version) 
		VALUES (?, ?, ?, ?, ?, ?)`, 
		"task-revise-132", "test-domain", "test-proj", "feature", TaskStatusPlanned, 1)
	if err != nil {
		t.Fatalf("failed to insert task: %v", err)
	}

	pm := NewPlanManager(db)
	planID, err := pm.RevisePlan(context.Background(), "task-revise-132", "revised plan", "refinement")
	if err != nil {
		t.Errorf("RevisePlan failed: %v", err)
		return
	}
	if planID == "" {
		t.Error("expected non-empty plan ID")
	}

	// Verify task update
	var version int
	err = db.QueryRow("SELECT plan_version FROM tasks WHERE id = ?", "task-revise-132").Scan(&version)
	if err != nil {
		t.Errorf("failed to query task version: %v", err)
	}
	if version != 2 {
		t.Errorf("expected plan_version 2, got %d", version)
	}
}

func TestWave132_GetPlanHistory_Success(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert some history
	_, err := db.Exec("INSERT INTO plan_versions (id, task_id, version, plan, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		"p1", "task-hist-132", 1, "plan 1", "initial", "2026-03-09T08:00:00Z")
	if err != nil {
		t.Fatalf("failed to insert history p1: %v", err)
	}
	_, err = db.Exec("INSERT INTO plan_versions (id, task_id, version, plan, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
		"p2", "task-hist-132", 2, "plan 2", "revised", "2026-03-09T09:00:00Z")
	if err != nil {
		t.Fatalf("failed to insert history p2: %v", err)
	}

	pm := NewPlanManager(db)
	history, err := pm.GetPlanHistory(context.Background(), "task-hist-132")
	if err != nil {
		t.Errorf("GetPlanHistory failed: %v", err)
		return
	}
	if len(history) != 2 {
		t.Errorf("expected 2 history entries, got %d", len(history))
	}
	if history[0].Version != 1 || history[1].Version != 2 {
		t.Error("history versions out of order or incorrect")
	}
}

func TestWave132_HandleCompletion_Bash(t *testing.T) {
	var buf strings.Builder
	cm := NewCompletionManager()
	err := cm.GenerateBash(&buf)
	if err != nil {
		t.Errorf("GenerateBash: %v", err)
	}
	if !strings.Contains(buf.String(), "bash") && !strings.Contains(buf.String(), "complete") {
		t.Logf("GenerateBash output doesn't look like bash: %s", buf.String()[:50])
	}
	if !strings.Contains(buf.String(), "_forge_completion") {
		t.Error("expected _forge_completion in bash script")
	}
}

func TestWave132_HandleCompletion_Zsh(t *testing.T) {
	var buf strings.Builder
	cm := NewCompletionManager()
	err := cm.GenerateZsh(&buf)
	if err != nil {
		t.Errorf("GenerateZsh: %v", err)
	}
	if !strings.Contains(buf.String(), "#compdef forge") {
		t.Error("expected #compdef forge in zsh script")
	}
	if !strings.Contains(buf.String(), "_forge()") {
		t.Error("expected _forge() in zsh script")
	}
}

func TestWave132_HandleCompletion_Fish(t *testing.T) {
	var buf strings.Builder
	cm := NewCompletionManager()
	err := cm.GenerateFish(&buf)
	if err != nil {
		t.Errorf("GenerateFish: %v", err)
	}
	if !strings.Contains(buf.String(), "complete -c forge") {
		t.Error("expected complete -c forge in fish script")
	}
}
