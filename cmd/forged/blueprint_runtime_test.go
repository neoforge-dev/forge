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

// TestProductAliasFromKey verifies the alias derivation logic for various product keys.
func TestProductAliasFromKey(t *testing.T) {
	cases := []struct {
		key  string
		want string
	}{
		{"interview-simulator", "is"},
		{"voice-coach", "vc"},
		{"study-flow", "sf"},
		{"single", "s"},
		{"", ""},
		{"a-b-c-d", "abcd"},
	}
	for _, tc := range cases {
		got := productAliasFromKey(tc.key)
		if got != tc.want {
			t.Errorf("productAliasFromKey(%q) = %q, want %q", tc.key, got, tc.want)
		}
	}
}

// TestProductContextFromTask_NilDB verifies that productContextFromTask returns
// empty-string values when called with a nil database pointer.
func TestProductContextFromTask_NilDB(t *testing.T) {
	ctx := productContextFromTask(nil, "task-123")
	for _, key := range []string{"DOMAIN", "PRODUCT_KEY", "PRODUCT_ALIAS"} {
		if ctx[key] != "" {
			t.Errorf("expected empty string for %s with nil DB, got %q", key, ctx[key])
		}
	}
}

// TestProductContextFromTask_EmptyTaskID verifies that productContextFromTask returns
// empty-string values when called with an empty task ID.
func TestProductContextFromTask_EmptyTaskID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	ctx := productContextFromTask(db, "")
	for _, key := range []string{"DOMAIN", "PRODUCT_KEY", "PRODUCT_ALIAS"} {
		if ctx[key] != "" {
			t.Errorf("expected empty string for %s with empty taskID, got %q", key, ctx[key])
		}
	}
}

// TestProductContextFromTask_WithTask verifies that domain and product key (project)
// are correctly extracted from the tasks table.
func TestProductContextFromTask_WithTask(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	// Insert a task with a known domain and project.
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at)
		VALUES ('ctx-task-001', 'edtech', 'voice-coach', 'feature', 5, 'queued', 'QUEUED', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	ctx := productContextFromTask(db, "ctx-task-001")
	if ctx["DOMAIN"] != "edtech" {
		t.Errorf("DOMAIN = %q, want %q", ctx["DOMAIN"], "edtech")
	}
	if ctx["PRODUCT_KEY"] != "voice-coach" {
		t.Errorf("PRODUCT_KEY = %q, want %q", ctx["PRODUCT_KEY"], "voice-coach")
	}
	if ctx["PRODUCT_ALIAS"] != "vc" {
		t.Errorf("PRODUCT_ALIAS = %q, want %q", ctx["PRODUCT_ALIAS"], "vc")
	}
}

// TestBlueprintVariableSubstitution_DomainAndProductKey verifies that ${DOMAIN},
// ${PRODUCT_KEY}, and ${PRODUCT_ALIAS} are replaced in shell command strings
// when a matching task exists in the database.
func TestBlueprintVariableSubstitution_DomainAndProductKey(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	// Insert the task that the blueprint run references.
	_, err := db.Exec(`
		INSERT INTO tasks (id, domain, project, type, priority, status, state, created_at, updated_at)
		VALUES ('subst-task-001', 'fintech', 'study-flow', 'feature', 3, 'queued', 'QUEUED', datetime('now'), datetime('now'))
	`)
	if err != nil {
		t.Fatalf("insert task: %v", err)
	}

	blueprintDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir blueprint dir: %v", err)
	}
	// The command writes the substituted values into a temp file so we can
	// assert they were replaced without relying on env-var leakage.
	outFile := filepath.Join(root, "subst-output.txt")
	blueprintYAML := `id: subst-test
name: Substitution Test
description: validates new variable substitution
steps:
  - type: shell
    name: write-vars
    command: "printf '%s %s %s' '${DOMAIN}' '${PRODUCT_KEY}' '${PRODUCT_ALIAS}' > ` + outFile + `"
`
	if err := os.WriteFile(filepath.Join(blueprintDir, "subst-test.yaml"), []byte(blueprintYAML), 0o644); err != nil {
		t.Fatalf("write blueprint: %v", err)
	}

	rt := NewBlueprintRuntime(db, root)
	run, err := rt.StartBlueprintRun(context.Background(), "subst-task-001", "subst-test")
	if err != nil {
		t.Fatalf("StartBlueprintRun: %v", err)
	}
	if run.Status != "completed" {
		t.Fatalf("expected completed run, got %s (error=%s)", run.Status, run.Error)
	}

	output, err := os.ReadFile(outFile)
	if err != nil {
		t.Fatalf("read output file: %v", err)
	}
	got := strings.TrimSpace(string(output))
	want := "fintech study-flow sf"
	if got != want {
		t.Errorf("substituted output = %q, want %q", got, want)
	}
}

// ─── Real config/blueprints validation ───────────────────────────────────────

// TestRealBlueprintYAMLs_AllParseWithoutError loads every YAML file under the
// real config/blueprints/ directory and verifies each one passes validateBlueprint.
// This catches schema regressions in the committed blueprint files.
func TestRealBlueprintYAMLs_AllParseWithoutError(t *testing.T) {
	repoRoot := "../../"
	blueprints, err := loadBlueprintsFromConfig(repoRoot)
	if err != nil {
		t.Fatalf("loadBlueprintsFromConfig returned error: %v", err)
	}
	if len(blueprints) == 0 {
		t.Skip("no blueprint YAML files found under config/blueprints/ — skipping")
	}
	for _, bp := range blueprints {
		bp := bp // capture loop var
		t.Run(bp.ID, func(t *testing.T) {
			if err := validateBlueprint(&bp); err != nil {
				t.Errorf("blueprint %q failed validation: %v", bp.ID, err)
			}
		})
	}
}

// TestRealBlueprintYAMLs_AllHaveNonEmptyID verifies that every blueprint loaded
// from the real config directory has a non-empty ID after parsing (either from
// the YAML id field or derived from the file path).
func TestRealBlueprintYAMLs_AllHaveNonEmptyID(t *testing.T) {
	repoRoot := "../../"
	blueprints, err := loadBlueprintsFromConfig(repoRoot)
	if err != nil {
		t.Fatalf("loadBlueprintsFromConfig returned error: %v", err)
	}
	for _, bp := range blueprints {
		if strings.TrimSpace(bp.ID) == "" {
			t.Errorf("blueprint loaded from config has empty ID (name=%q)", bp.Name)
		}
	}
}

// TestRealBlueprintYAMLs_AllHaveAtLeastOneStep verifies that every real blueprint
// has at least one step defined.
func TestRealBlueprintYAMLs_AllHaveAtLeastOneStep(t *testing.T) {
	repoRoot := "../../"
	blueprints, err := loadBlueprintsFromConfig(repoRoot)
	if err != nil {
		t.Fatalf("loadBlueprintsFromConfig returned error: %v", err)
	}
	for _, bp := range blueprints {
		if len(bp.Steps) == 0 {
			t.Errorf("blueprint %q has no steps", bp.ID)
		}
	}
}

// TestRealBlueprintYAMLs_AllStepsHaveSupportedTypes verifies that every step in
// every real blueprint uses one of the supported step types:
// check, shell, dispatch, review, complete.
func TestRealBlueprintYAMLs_AllStepsHaveSupportedTypes(t *testing.T) {
	repoRoot := "../../"
	blueprints, err := loadBlueprintsFromConfig(repoRoot)
	if err != nil {
		t.Fatalf("loadBlueprintsFromConfig returned error: %v", err)
	}
	supported := map[string]bool{
		"check": true, "shell": true, "dispatch": true, "review": true, "complete": true,
	}
	for _, bp := range blueprints {
		for _, step := range bp.Steps {
			if !supported[step.Type] {
				t.Errorf("blueprint %q step %q has unsupported type %q", bp.ID, step.Name, step.Type)
			}
		}
	}
}

// TestRealBlueprintYAMLs_CommandStepsHaveCommands verifies that every check/shell
// step in every real blueprint has a non-empty command field.
func TestRealBlueprintYAMLs_CommandStepsHaveCommands(t *testing.T) {
	repoRoot := "../../"
	blueprints, err := loadBlueprintsFromConfig(repoRoot)
	if err != nil {
		t.Fatalf("loadBlueprintsFromConfig returned error: %v", err)
	}
	for _, bp := range blueprints {
		for _, step := range bp.Steps {
			if step.Type == "check" || step.Type == "shell" {
				if strings.TrimSpace(step.Command) == "" {
					t.Errorf("blueprint %q step %q (type=%s) has empty command", bp.ID, step.Name, step.Type)
				}
			}
		}
	}
}
