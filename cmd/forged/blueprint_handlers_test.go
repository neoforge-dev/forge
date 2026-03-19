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

func TestValidateBlueprint_NilBlueprint(t *testing.T) {
	err := validateBlueprint(nil)
	if err == nil {
		t.Fatal("expected error for nil blueprint")
	}
	if !strings.Contains(err.Error(), "nil") {
		t.Errorf("expected 'nil' in error, got %s", err.Error())
	}
}

func TestValidateBlueprint_MissingID(t *testing.T) {
	bp := &Blueprint{
		Name:        "Test",
		Description: "Test blueprint",
		Steps:       []BlueprintSpec{{Name: "step1", Type: "shell", Command: "echo test"}},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Fatal("expected error for missing ID")
	}
	if !strings.Contains(err.Error(), "missing blueprint id") {
		t.Errorf("expected 'missing blueprint id' in error, got %s", err.Error())
	}
}

func TestValidateBlueprint_NoSteps(t *testing.T) {
	bp := &Blueprint{
		ID:   "test-bp",
		Name: "Test",
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Fatal("expected error for no steps")
	}
	if !strings.Contains(err.Error(), "at least one step") {
		t.Errorf("expected 'at least one step' in error, got %s", err.Error())
	}
}

func TestValidateBlueprint_MissingStepName(t *testing.T) {
	bp := &Blueprint{
		ID:   "test-bp",
		Name: "Test",
		Steps: []BlueprintSpec{
			{Type: "shell", Command: "echo test"},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Fatal("expected error for missing step name")
	}
	if !strings.Contains(err.Error(), "missing name") {
		t.Errorf("expected 'missing name' in error, got %s", err.Error())
	}
}

func TestValidateBlueprint_DuplicateStepName(t *testing.T) {
	bp := &Blueprint{
		ID:   "test-bp",
		Name: "Test",
		Steps: []BlueprintSpec{
			{Name: "step1", Type: "shell", Command: "echo test"},
			{Name: "step1", Type: "shell", Command: "echo test2"},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Fatal("expected error for duplicate step name")
	}
	if !strings.Contains(err.Error(), "duplicate step name") {
		t.Errorf("expected 'duplicate step name' in error, got %s", err.Error())
	}
}

func TestValidateBlueprint_ShellStepMissingCommand(t *testing.T) {
	bp := &Blueprint{
		ID:   "test-bp",
		Name: "Test",
		Steps: []BlueprintSpec{
			{Name: "step1", Type: "shell"},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Fatal("expected error for shell step without command")
	}
	if !strings.Contains(err.Error(), "requires command") {
		t.Errorf("expected 'requires command' in error, got %s", err.Error())
	}
}

func TestValidateBlueprint_CheckStepMissingCommand(t *testing.T) {
	bp := &Blueprint{
		ID:   "test-bp",
		Name: "Test",
		Steps: []BlueprintSpec{
			{Name: "step1", Type: "check"},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Fatal("expected error for check step without command")
	}
	if !strings.Contains(err.Error(), "requires command") {
		t.Errorf("expected 'requires command' in error, got %s", err.Error())
	}
}

func TestValidateBlueprint_UnsupportedStepType(t *testing.T) {
	bp := &Blueprint{
		ID:   "test-bp",
		Name: "Test",
		Steps: []BlueprintSpec{
			{Name: "step1", Type: "unknown"},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Fatal("expected error for unsupported step type")
	}
	if !strings.Contains(err.Error(), "unsupported type") {
		t.Errorf("expected 'unsupported type' in error, got %s", err.Error())
	}
}

func TestValidateBlueprint_ValidBlueprints(t *testing.T) {
	tests := []struct {
		name string
		bp   *Blueprint
	}{
		{
			name: "shell step",
			bp: &Blueprint{
				ID:   "test-shell",
				Name: "Shell Test",
				Steps: []BlueprintSpec{
					{Name: "run", Type: "shell", Command: "echo test"},
				},
			},
		},
		{
			name: "check step",
			bp: &Blueprint{
				ID:   "test-check",
				Name: "Check Test",
				Steps: []BlueprintSpec{
					{Name: "verify", Type: "check", Command: "test -f /tmp/foo"},
				},
			},
		},
		{
			name: "dispatch step",
			bp: &Blueprint{
				ID:   "test-dispatch",
				Name: "Dispatch Test",
				Steps: []BlueprintSpec{
					{Name: "send", Type: "dispatch", Agent: "auto"},
				},
			},
		},
		{
			name: "review step",
			bp: &Blueprint{
				ID:   "test-review",
				Name: "Review Test",
				Steps: []BlueprintSpec{
					{Name: "review", Type: "review"},
				},
			},
		},
		{
			name: "complete step",
			bp: &Blueprint{
				ID:   "test-complete",
				Name: "Complete Test",
				Steps: []BlueprintSpec{
					{Name: "done", Type: "complete", Evidence: []string{"test"}},
				},
			},
		},
		{
			name: "multiple steps",
			bp: &Blueprint{
				ID:   "test-multi",
				Name: "Multi Step Test",
				Steps: []BlueprintSpec{
					{Name: "step1", Type: "shell", Command: "echo 1"},
					{Name: "step2", Type: "dispatch", Agent: "auto"},
					{Name: "step3", Type: "complete"},
				},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateBlueprint(tt.bp)
			if err != nil {
				t.Errorf("unexpected error: %v", err)
			}
		})
	}
}

func TestBlueprintRuntime_LoadBlueprint(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	blueprintDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir blueprint dir: %v", err)
	}

	blueprintYAML := `id: test-bp
name: Test Blueprint
description: Test
steps:
  - name: step1
    type: shell
    command: "echo test"
`
	if err := os.WriteFile(filepath.Join(blueprintDir, "test-bp.yaml"), []byte(blueprintYAML), 0o644); err != nil {
		t.Fatalf("write blueprint: %v", err)
	}

	rt := NewBlueprintRuntime(db, root)

	bp, err := rt.loadBlueprint("test-bp")
	if err != nil {
		t.Fatalf("loadBlueprint error: %v", err)
	}
	if bp.ID != "test-bp" {
		t.Errorf("expected id 'test-bp', got %s", bp.ID)
	}
	if len(bp.Steps) != 1 {
		t.Errorf("expected 1 step, got %d", len(bp.Steps))
	}
}

func TestBlueprintRuntime_LoadBlueprintNotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	_, err := rt.loadBlueprint("nonexistent")
	if err == nil {
		t.Fatal("expected error for nonexistent blueprint")
	}
}

func TestBlueprintRuntime_LoadBlueprintInvalidYAML(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	blueprintDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir blueprint dir: %v", err)
	}

	invalidYAML := `id: invalid
name: Invalid
steps:
  - name: step1
    type: shell
`
	if err := os.WriteFile(filepath.Join(blueprintDir, "invalid.yaml"), []byte(invalidYAML), 0o644); err != nil {
		t.Fatalf("write blueprint: %v", err)
	}

	rt := NewBlueprintRuntime(db, root)

	_, err := rt.loadBlueprint("invalid")
	if err == nil {
		t.Fatal("expected error for invalid blueprint")
	}
}

func TestBlueprintRuntime_ListBlueprints(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	blueprintDir := filepath.Join(root, "config", "blueprints", "test")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir blueprint dir: %v", err)
	}

	for i := 1; i <= 3; i++ {
		blueprintYAML := `id: test/bp` + string(rune('0'+i)) + `
name: Test Blueprint ` + string(rune('0'+i)) + `
description: Test ` + string(rune('0'+i)) + `
steps:
  - name: step` + string(rune('0'+i)) + `
    type: shell
    command: "echo ` + string(rune('0'+i)) + `"
`
		if err := os.WriteFile(filepath.Join(blueprintDir, "bp"+string(rune('0'+i))+".yaml"), []byte(blueprintYAML), 0o644); err != nil {
			t.Fatalf("write blueprint: %v", err)
		}
	}

	rt := NewBlueprintRuntime(db, root)

	blueprints, err := rt.ListBlueprints()
	if err != nil {
		t.Fatalf("ListBlueprints error: %v", err)
	}
	if len(blueprints) != 3 {
		t.Errorf("expected 3 blueprints, got %d", len(blueprints))
	}
}

func TestBlueprintRuntime_GetRun_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	_, err := rt.GetRun(context.Background(), "nonexistent-run-id")
	if err == nil {
		t.Fatal("expected error for nonexistent run")
	}
}

func TestBlueprintRuntime_StartBlueprintRun_InvalidBlueprint(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	_, err := rt.StartBlueprintRun(context.Background(), "task-1", "nonexistent")
	if err == nil {
		t.Fatal("expected error for nonexistent blueprint")
	}
}

func TestBlueprintHandler_BlueprintsHandler_MethodNotAllowed(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/blueprints", nil)
	rr := httptest.NewRecorder()

	blueprintsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestBlueprintHandler_BlueprintRunsHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/blueprints/runs", nil)
	rr := httptest.NewRecorder()

	blueprintRunsHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestBlueprintHandler_BlueprintRunsHandler_InvalidBody(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodPost, "/api/blueprints/runs", bytes.NewReader([]byte("invalid json")))
	rr := httptest.NewRecorder()

	blueprintRunsHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestBlueprintHandler_BlueprintRunsHandler_MissingFields(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	tests := []struct {
		name     string
		body     map[string]string
		expected int
	}{
		{
			name:     "missing both",
			body:     map[string]string{},
			expected: http.StatusBadRequest,
		},
		{
			name:     "missing blueprint_id",
			body:     map[string]string{"task_id": "task-1"},
			expected: http.StatusBadRequest,
		},
		{
			name:     "missing task_id",
			body:     map[string]string{"blueprint_id": "bp-1"},
			expected: http.StatusBadRequest,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body, _ := json.Marshal(tt.body)
			req := httptest.NewRequest(http.MethodPost, "/api/blueprints/runs", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			rr := httptest.NewRecorder()

			blueprintRunsHandler(rr, req)

			if rr.Code != tt.expected {
				t.Errorf("expected status %d, got %d: %s", tt.expected, rr.Code, rr.Body.String())
			}
		})
	}
}

// TestBlueprintRunByIDHandler_NilDB verifies that blueprintRunByIDHandler
// returns 503 when the DB connection is nil (blueprintRuntime() returns nil).
func TestBlueprintRunByIDHandler_NilDB(t *testing.T) {
	oldDB := getDBConn()
	setDBConn(nil)
	defer setDBConn(oldDB)

	req := httptest.NewRequest(http.MethodGet, "/api/blueprints/runs/some-run-id", nil)
	rr := httptest.NewRecorder()
	blueprintRunByIDHandler(rr, req)
	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("expected 503 for nil DB, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestBlueprintHandler_BlueprintRunByIDHandler_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodPut, "/api/blueprints/runs/run-123", nil)
	rr := httptest.NewRecorder()

	blueprintRunByIDHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestBlueprintHandler_BlueprintRunByIDHandler_MissingID(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/blueprints/runs/", nil)
	rr := httptest.NewRecorder()

	blueprintRunByIDHandler(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("expected status 400, got %d", rr.Code)
	}
}

func TestBlueprintHandler_BlueprintRunByIDHandler_NotFound(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/blueprints/runs/nonexistent-run-id", nil)
	rr := httptest.NewRecorder()

	blueprintRunByIDHandler(rr, req)

	if rr.Code != http.StatusNotFound {
		t.Errorf("expected status 404, got %d: %s", rr.Code, rr.Body.String())
	}
}

func TestBlueprintHandler_ResumeRun(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	blueprintDir := filepath.Join(root, "config", "blueprints", "resume-test")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir blueprint dir: %v", err)
	}

	blueprintYAML := `id: resume-test/bp
name: Resume Test
description: Test resume
steps:
  - name: step1
    type: shell
    command: "echo step1"
  - name: step2
    type: shell
    command: "echo step2"
`
	if err := os.WriteFile(filepath.Join(blueprintDir, "bp.yaml"), []byte(blueprintYAML), 0o644); err != nil {
		t.Fatalf("write blueprint: %v", err)
	}

	body, _ := json.Marshal(map[string]string{
		"task_id":      "TEST-RESUME-123",
		"blueprint_id": "resume-test/bp",
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

	if run.Status != "completed" {
		t.Errorf("expected completed run, got %s (error=%s)", run.Status, run.Error)
	}
	if len(run.Steps) != 2 {
		t.Errorf("expected 2 steps, got %d", len(run.Steps))
	}
}

func TestBlueprintHandler_ResumeEndpoint(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	blueprintDir := filepath.Join(root, "config", "blueprints", "resume-manual")
	if err := os.MkdirAll(blueprintDir, 0o755); err != nil {
		t.Fatalf("mkdir blueprint dir: %v", err)
	}

	blueprintYAML := `id: resume-manual/bp
name: Manual Resume Test
description: Test manual resume
steps:
  - name: step1
    type: shell
    command: "echo step1"
`
	if err := os.WriteFile(filepath.Join(blueprintDir, "bp.yaml"), []byte(blueprintYAML), 0o644); err != nil {
		t.Fatalf("write blueprint: %v", err)
	}

	body, _ := json.Marshal(map[string]string{
		"task_id":      "TEST-MANUAL-RESUME",
		"blueprint_id": "resume-manual/bp",
	})
	createReq := httptest.NewRequest(http.MethodPost, "/api/blueprints/runs", bytes.NewReader(body))
	createReq.Header.Set("Content-Type", "application/json")
	createRR := httptest.NewRecorder()

	blueprintRunsHandler(createRR, createReq)

	if createRR.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", createRR.Code, createRR.Body.String())
	}

	var run BlueprintRun
	if err := json.NewDecoder(createRR.Body).Decode(&run); err != nil {
		t.Fatalf("decode run: %v", err)
	}

	if run.Status != "completed" {
		t.Skipf("run already completed, skip resume test: %s", run.Status)
		return
	}

	resumeReq := httptest.NewRequest(http.MethodPost, "/api/blueprints/runs/"+run.ID+"/resume", nil)
	resumeRR := httptest.NewRecorder()

	blueprintRunByIDHandler(resumeRR, resumeReq)

	if resumeRR.Code != http.StatusOK {
		t.Errorf("expected 200, got %d: %s", resumeRR.Code, resumeRR.Body.String())
	}
}

func TestBlueprintHandler_ResumeEndpoint_MethodNotAllowed(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()
	setDBConn(db)
	defer setDBConn(nil)

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	req := httptest.NewRequest(http.MethodGet, "/api/blueprints/runs/run-123/resume", nil)
	rr := httptest.NewRecorder()

	blueprintRunByIDHandler(rr, req)

	if rr.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected status 405, got %d", rr.Code)
	}
}

func TestBlueprintRuntime_ExecuteStep_ShellFails(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	run := &BlueprintRun{
		ID:          "test-run",
		TaskID:      "task-1",
		BlueprintID: "test",
		Status:      "running",
	}

	step := BlueprintSpec{
		Type:    "shell",
		Name:    "fail-step",
		Command: "exit 1",
	}

	err := rt.executeStep(context.Background(), run, step, 0)
	if err == nil {
		t.Fatal("expected error for failing shell command")
	}
}

func TestBlueprintRuntime_ExecuteStep_Dispatch(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	run := &BlueprintRun{
		ID:          "test-run",
		TaskID:      "task-1",
		BlueprintID: "test",
		Status:      "running",
	}

	step := BlueprintSpec{
		Type:  "dispatch",
		Name:  "dispatch-step",
		Agent: "auto",
	}

	err := rt.executeStep(context.Background(), run, step, 0)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestBlueprintRuntime_ExecuteStep_Review(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	run := &BlueprintRun{
		ID:          "test-run",
		TaskID:      "task-1",
		BlueprintID: "test",
		Status:      "running",
	}

	step := BlueprintSpec{
		Type: "review",
		Name: "review-step",
	}

	err := rt.executeStep(context.Background(), run, step, 0)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestBlueprintRuntime_ExecuteStep_Complete(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	run := &BlueprintRun{
		ID:          "test-run",
		TaskID:      "task-1",
		BlueprintID: "test",
		Status:      "running",
	}

	step := BlueprintSpec{
		Type:     "complete",
		Name:     "complete-step",
		Evidence: []string{"test1", "test2"},
	}

	err := rt.executeStep(context.Background(), run, step, 0)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestBlueprintRuntime_FailStepAndFailRun(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	err := rt.failRun(context.Background(), "nonexistent", 0, "test error")
	if err != nil {
		t.Errorf("failRun should not error: %v", err)
	}
}

func TestBlueprintRuntime_ResolveBlueprintPath(t *testing.T) {
	db, cleanup := setupClaimTestDB(t)
	defer cleanup()

	root := t.TempDir()
	t.Setenv("FORGE_ROOT", root)

	rt := NewBlueprintRuntime(db, root)

	tests := []struct {
		name     string
		ref      string
		expected string
	}{
		{
			name:     "yaml suffix",
			ref:      "test.yaml",
			expected: filepath.Join(root, "test.yaml"),
		},
		{
			name:     "yml suffix",
			ref:      "test.yml",
			expected: filepath.Join(root, "test.yml"),
		},
		{
			name:     "no suffix",
			ref:      "test",
			expected: filepath.Join(root, "config", "blueprints", "test.yaml"),
		},
		{
			name:     "empty",
			ref:      "",
			expected: "",
		},
		{
			name:     "absolute path",
			ref:      "/tmp/blueprint.yaml",
			expected: "/tmp/blueprint.yaml",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := rt.resolveBlueprintPath(tt.ref)
			if result != tt.expected {
				t.Errorf("expected %s, got %s", tt.expected, result)
			}
		})
	}
}
