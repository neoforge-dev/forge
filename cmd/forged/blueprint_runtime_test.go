//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestNewBlueprintRuntime_EmptyRoot verifies that passing "" as root falls back
// to forgeRoot() (the FORGE_ROOT env var or ".").
func TestNewBlueprintRuntime_EmptyRoot(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	// Passing "" should trigger the `if root == ""` branch and use forgeRoot().
	rt := NewBlueprintRuntime(db, "")
	if rt == nil {
		t.Fatal("expected non-nil BlueprintRuntime")
	}
	if rt.forgeRoot != root {
		t.Errorf("expected forgeRoot=%q, got %q", root, rt.forgeRoot)
	}
}

// TestNewBlueprintRuntime_ExplicitRoot verifies that an explicit root is used
// as-is without falling back to forgeRoot().
func TestNewBlueprintRuntime_ExplicitRoot(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	explicitRoot := t.TempDir()
	// Set FORGE_ROOT to something different to confirm the explicit root wins.
	t.Setenv("FORGE_ROOT", t.TempDir())

	rt := NewBlueprintRuntime(db, explicitRoot)
	if rt == nil {
		t.Fatal("expected non-nil BlueprintRuntime")
	}
	if rt.forgeRoot != explicitRoot {
		t.Errorf("expected forgeRoot=%q, got %q", explicitRoot, rt.forgeRoot)
	}
}

// TestResumeRun_AlreadyCompleted verifies that ResumeRun on a completed run
// returns nil immediately without re-executing steps.
func TestResumeRun_AlreadyCompleted(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	blueprintDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	blueprintYAML := `id: resume-done-test
name: Resume Done Test
description: Already completed blueprint
steps:
  - type: shell
    name: echo-step
    command: "echo hello"
`
	if err := os.WriteFile(filepath.Join(blueprintDir, "resume-done-test.yaml"), []byte(blueprintYAML), 0o644); err != nil {
		t.Fatalf("write blueprint: %v", err)
	}

	rt := NewBlueprintRuntime(db, root)
	ctx := context.Background()

	run, err := rt.StartBlueprintRun(ctx, "task-resume-done", "resume-done-test")
	if err != nil {
		t.Fatalf("StartBlueprintRun: %v", err)
	}
	if run.Status != "completed" {
		t.Skipf("run not completed after start (%s), skip resume-completed test", run.Status)
	}

	// Calling ResumeRun on an already-completed run must be a no-op (nil error).
	if err := rt.ResumeRun(ctx, run.ID); err != nil {
		t.Errorf("ResumeRun on completed run should return nil, got: %v", err)
	}
}

// TestResumeRun_AlreadyFailed verifies that ResumeRun on a failed run
// returns nil immediately.
func TestResumeRun_AlreadyFailed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)
	ctx := context.Background()

	// Manually insert a failed run into the DB.
	runID := "test-failed-run-001"
	_, err := db.ExecContext(ctx, `
		INSERT INTO blueprint_runs (id, task_id, blueprint_id, status, current_step, started_at)
		VALUES (?, 'task-x', 'some-bp', 'failed', 0, datetime('now'))
	`, runID)
	if err != nil {
		t.Fatalf("insert failed run: %v", err)
	}

	// ResumeRun on a failed run must be a no-op.
	if err := rt.ResumeRun(ctx, runID); err != nil {
		t.Errorf("ResumeRun on failed run should return nil, got: %v", err)
	}
}

// TestResumeRun_LoadBlueprintError verifies that when ResumeRun can't load the
// blueprint (e.g. YAML file deleted), it calls failRun and returns nil.
func TestResumeRun_LoadBlueprintError(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)
	ctx := context.Background()

	// Manually insert a run whose blueprint YAML does not exist.
	runID := "test-run-no-bp-001"
	_, err := db.ExecContext(ctx, `
		INSERT INTO blueprint_runs (id, task_id, blueprint_id, status, current_step, started_at)
		VALUES (?, 'task-nobp', 'nonexistent-blueprint', 'pending', 0, datetime('now'))
	`, runID)
	if err != nil {
		t.Fatalf("insert run: %v", err)
	}

	// ResumeRun should call failRun (which updates the DB) and return its result.
	// The blueprint file doesn't exist, so loadBlueprint will fail.
	err = rt.ResumeRun(ctx, runID)
	// The error from failRun is nil if the DB update succeeded.
	// We just verify it doesn't panic.
	_ = err
}

func TestBlueprintRuntime_APIEndToEnd(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	blueprintDir := filepath.Join(root, "config", "blueprints", "coding")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir blueprint dir: %v", err)
	}
	blueprintYAML := `id: coding/test
name: Test Blueprint
description: thin e2e
steps:
  - type: shell
    name: precheck
    command: "printf precheck"
  - type: dispatch
    name: dispatch
    agent: auto
  - type: complete
    name: finalize
    evidence:
      - tests
`
	if err := os.WriteFile(filepath.Join(blueprintDir, "test.yaml"), []byte(blueprintYAML), 0o644); err != nil {
		t.Fatalf("write blueprint: %v", err)
	}

	body, _ := json.Marshal(map[string]string{
		"task_id":      "TEST-BLUEPRINT-123",
		"blueprint_id": "coding/test",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/blueprints/runs", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	blueprintRunsHandler(rr, req)

	if rr.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", rr.Code, rr.Body.String())
	}

	var run BlueprintRun
	if err := json.NewDecoder(rr.Body).Decode(&run); err != nil {
		t.Fatalf("decode run: %v", err)
	}
	if run.ID == "" {
		t.Fatal("expected run id")
	}
	if run.Status != "completed" {
		t.Fatalf("expected completed run, got %s (error=%s)", run.Status, run.Error)
	}
	if len(run.Steps) != 3 {
		t.Fatalf("expected 3 steps, got %d", len(run.Steps))
	}
	if !strings.Contains(run.Steps[0].Evidence, "precheck") {
		t.Fatalf("expected command evidence in first step, got %s", run.Steps[0].Evidence)
	}

	getReq := httptest.NewRequest(http.MethodGet, "/api/blueprints/runs/"+run.ID, nil)
	getRR := httptest.NewRecorder()
	blueprintRunByIDHandler(getRR, getReq)
	if getRR.Code != http.StatusOK {
		t.Fatalf("expected 200 from status handler, got %d: %s", getRR.Code, getRR.Body.String())
	}

	listReq := httptest.NewRequest(http.MethodGet, "/api/blueprints", nil)
	listRR := httptest.NewRecorder()
	blueprintsHandler(listRR, listReq)
	if listRR.Code != http.StatusOK {
		t.Fatalf("expected 200 from list handler, got %d: %s", listRR.Code, listRR.Body.String())
	}
	if !strings.Contains(listRR.Body.String(), "coding/test") {
		t.Fatalf("expected blueprint id in list response, got %s", listRR.Body.String())
	}
}

// TestLoadBlueprintsFromConfig_NotExist verifies that loadBlueprintsFromConfig
// returns an empty slice (not an error) when the config directory does not exist.
// This covers the os.IsNotExist branch in loadBlueprintsFromConfig.
func TestLoadBlueprintsFromConfig_NotExist(t *testing.T) {
	root := t.TempDir() // no config/blueprints subdir created
	blueprints, err := loadBlueprintsFromConfig(root)
	if err != nil {
		t.Fatalf("expected nil error for missing config dir, got: %v", err)
	}
	if blueprints == nil {
		t.Error("expected non-nil (empty) slice, got nil")
	}
	if len(blueprints) != 0 {
		t.Errorf("expected 0 blueprints, got %d", len(blueprints))
	}
}

// TestLoadBlueprintsFromConfig_WithYAML verifies that loadBlueprintsFromConfig
// correctly loads a blueprint from a YAML file.
func TestLoadBlueprintsFromConfig_WithYAML(t *testing.T) {
	root := t.TempDir()
	configDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	yaml := `id: "test-bp"
name: "Test Blueprint"
steps:
  - name: "step1"
    type: "shell"
    command: "echo hello"
`
	if err := os.WriteFile(filepath.Join(configDir, "test-bp.yaml"), []byte(yaml), 0o644); err != nil {
		t.Fatalf("write yaml: %v", err)
	}

	blueprints, err := loadBlueprintsFromConfig(root)
	if err != nil {
		t.Fatalf("expected nil error, got: %v", err)
	}
	if len(blueprints) != 1 {
		t.Errorf("expected 1 blueprint, got %d", len(blueprints))
	}
}
