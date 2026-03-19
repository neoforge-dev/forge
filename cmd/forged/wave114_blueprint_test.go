//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// wave114_blueprint_test.go — resolveBlueprintPath coverage
// Target: blueprint.go resolveBlueprintPath 42.9% → 80%+

// TestWave114_ResolveBlueprintPath_Empty verifies that an empty ref returns an empty path.
func TestWave114_ResolveBlueprintPath_Empty(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)
	result := rt.resolveBlueprintPath("")
	if result != "" {
		t.Errorf("expected empty string for empty ref, got %q", result)
	}
}

// TestWave114_ResolveBlueprintPath_AbsoluteYAML verifies that an absolute .yaml path is returned as-is.
func TestWave114_ResolveBlueprintPath_AbsoluteYAML(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	absPath := filepath.Join(root, "some", "absolute.yaml")
	result := rt.resolveBlueprintPath(absPath)
	if result != absPath {
		t.Errorf("expected absolute path returned as-is, got %q", result)
	}
}

// TestWave114_ResolveBlueprintPath_AbsoluteYML verifies that an absolute .yml path is returned as-is.
func TestWave114_ResolveBlueprintPath_AbsoluteYML(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	absPath := filepath.Join(root, "some", "config.yml")
	result := rt.resolveBlueprintPath(absPath)
	if result != absPath {
		t.Errorf("expected absolute .yml path returned as-is, got %q", result)
	}
}

// TestWave114_ResolveBlueprintPath_RelativeYAML verifies that a relative .yaml ref
// is joined with forgeRoot.
func TestWave114_ResolveBlueprintPath_RelativeYAML(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	result := rt.resolveBlueprintPath("subdir/mybp.yaml")
	expected := filepath.Join(root, "subdir/mybp.yaml")
	if result != expected {
		t.Errorf("expected %q, got %q", expected, result)
	}
}

// TestWave114_ResolveBlueprintPath_RelativeYML verifies that a relative .yml ref
// is joined with forgeRoot.
func TestWave114_ResolveBlueprintPath_RelativeYML(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	result := rt.resolveBlueprintPath("other/config.yml")
	expected := filepath.Join(root, "other/config.yml")
	if result != expected {
		t.Errorf("expected %q, got %q", expected, result)
	}
}

// TestWave114_ResolveBlueprintPath_BareID verifies that a bare ID (no extension)
// is resolved to the config/blueprints directory with .yaml appended.
func TestWave114_ResolveBlueprintPath_BareID(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	result := rt.resolveBlueprintPath("my-blueprint")
	expected := filepath.Join(root, "config", "blueprints", "my-blueprint.yaml")
	if result != expected {
		t.Errorf("expected %q, got %q", expected, result)
	}
}

// TestWave114_ResolveBlueprintPath_BareIDWithSlash verifies that a bare ID with slash
// component is resolved under config/blueprints.
func TestWave114_ResolveBlueprintPath_BareIDWithSlash(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	result := rt.resolveBlueprintPath("coding/deploy")
	expected := filepath.Join(root, "config", "blueprints", "coding", "deploy.yaml")
	if result != expected {
		t.Errorf("expected %q, got %q", expected, result)
	}
}

// TestWave114_LoadBlueprint_ValidFile verifies that loadBlueprint correctly loads a valid YAML file.
func TestWave114_LoadBlueprint_ValidFile(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	bpDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(bpDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	content := `id: test-bp
name: Test
description: A test blueprint
steps:
  - type: shell
    name: run-tests
    command: "echo hello"
`
	if err := os.WriteFile(filepath.Join(bpDir, "test-bp.yaml"), []byte(content), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	bp, err := rt.loadBlueprint("test-bp")
	if err != nil {
		t.Fatalf("loadBlueprint: %v", err)
	}
	if bp.ID != "test-bp" {
		t.Errorf("expected ID 'test-bp', got %q", bp.ID)
	}
	if len(bp.Steps) != 1 {
		t.Errorf("expected 1 step, got %d", len(bp.Steps))
	}
}

// TestWave114_LoadBlueprint_MissingFile verifies that loadBlueprint returns an error
// when the file does not exist.
func TestWave114_LoadBlueprint_MissingFile(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	_, err := rt.loadBlueprint("nonexistent-bp")
	if err == nil {
		t.Error("expected error for missing blueprint file, got nil")
	}
	if !strings.Contains(err.Error(), "read blueprint") {
		t.Errorf("expected 'read blueprint' in error, got: %v", err)
	}
}

// TestWave114_LoadBlueprint_IDFromFilename verifies that when YAML has no id field,
// the blueprint ID is derived from the filename.
func TestWave114_LoadBlueprint_IDFromFilename(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	bpDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(bpDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	// No id field — should derive from filename
	content := `name: No ID Blueprint
steps:
  - type: shell
    name: run
    command: "echo ok"
`
	if err := os.WriteFile(filepath.Join(bpDir, "from-filename.yaml"), []byte(content), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	bp, err := rt.loadBlueprint("from-filename")
	if err != nil {
		t.Fatalf("loadBlueprint: %v", err)
	}
	if bp.ID != "from-filename" {
		t.Errorf("expected ID 'from-filename', got %q", bp.ID)
	}
}

// TestWave114_LoadBlueprint_AbsPath verifies that an absolute path ref is used directly.
func TestWave114_LoadBlueprint_AbsPath(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	// Write to an absolute path outside the config dir
	absFile := filepath.Join(root, "explicit.yaml")
	content := `id: explicit-bp
name: Explicit
steps:
  - type: review
    name: check
`
	if err := os.WriteFile(absFile, []byte(content), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	bp, err := rt.loadBlueprint(absFile)
	if err != nil {
		t.Fatalf("loadBlueprint abs path: %v", err)
	}
	if bp.ID != "explicit-bp" {
		t.Errorf("expected ID 'explicit-bp', got %q", bp.ID)
	}
}

// TestWave114_LoadBlueprint_IDFromAbsFilename verifies that abs path with no id field
// derives ID from the filename stem.
func TestWave114_LoadBlueprint_IDFromAbsFilename(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	absFile := filepath.Join(root, "stem-test.yaml")
	content := `name: Stem Test
steps:
  - type: complete
    name: done
`
	if err := os.WriteFile(absFile, []byte(content), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	bp, err := rt.loadBlueprint(absFile)
	if err != nil {
		t.Fatalf("loadBlueprint: %v", err)
	}
	if bp.ID != "stem-test" {
		t.Errorf("expected ID 'stem-test', got %q", bp.ID)
	}
}

// TestWave114_ValidateBlueprint_NilBlueprint verifies validateBlueprint returns error on nil.
func TestWave114_ValidateBlueprint_NilBlueprint(t *testing.T) {
	err := validateBlueprint(nil)
	if err == nil {
		t.Error("expected error for nil blueprint")
	}
}

// TestWave114_ValidateBlueprint_MissingID verifies validateBlueprint returns error on empty ID.
func TestWave114_ValidateBlueprint_MissingID(t *testing.T) {
	bp := &Blueprint{
		ID:    "  ",
		Steps: []BlueprintSpec{{Type: "shell", Name: "step1", Command: "echo"}},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Error("expected error for missing ID")
	}
}

// TestWave114_ValidateBlueprint_NoSteps verifies validateBlueprint returns error on empty steps.
func TestWave114_ValidateBlueprint_NoSteps(t *testing.T) {
	bp := &Blueprint{
		ID:    "my-bp",
		Steps: []BlueprintSpec{},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Error("expected error for empty steps")
	}
}

// TestWave114_ValidateBlueprint_StepMissingName verifies error on step with no name.
func TestWave114_ValidateBlueprint_StepMissingName(t *testing.T) {
	bp := &Blueprint{
		ID: "my-bp",
		Steps: []BlueprintSpec{
			{Type: "shell", Name: "", Command: "echo ok"},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Error("expected error for step missing name")
	}
}

// TestWave114_ValidateBlueprint_DuplicateStepName verifies error on duplicate step names.
func TestWave114_ValidateBlueprint_DuplicateStepName(t *testing.T) {
	bp := &Blueprint{
		ID: "my-bp",
		Steps: []BlueprintSpec{
			{Type: "shell", Name: "step1", Command: "echo 1"},
			{Type: "shell", Name: "step1", Command: "echo 2"},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Error("expected error for duplicate step names")
	}
	if !strings.Contains(err.Error(), "duplicate step name") {
		t.Errorf("expected 'duplicate step name', got: %v", err)
	}
}

// TestWave114_ValidateBlueprint_StepMissingCommand verifies error when check/shell step has no command.
func TestWave114_ValidateBlueprint_StepMissingCommand(t *testing.T) {
	bp := &Blueprint{
		ID: "my-bp",
		Steps: []BlueprintSpec{
			{Type: "check", Name: "check-step", Command: ""},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Error("expected error for check step missing command")
	}
}

// TestWave114_ValidateBlueprint_UnsupportedStepType verifies error for unknown step type.
func TestWave114_ValidateBlueprint_UnsupportedStepType(t *testing.T) {
	bp := &Blueprint{
		ID: "my-bp",
		Steps: []BlueprintSpec{
			{Type: "unknown-type", Name: "bad-step"},
		},
	}
	err := validateBlueprint(bp)
	if err == nil {
		t.Error("expected error for unsupported step type")
	}
	if !strings.Contains(err.Error(), "unsupported type") {
		t.Errorf("expected 'unsupported type', got: %v", err)
	}
}

// TestWave114_ValidateBlueprint_ValidAllTypes verifies valid blueprint with all supported step types.
func TestWave114_ValidateBlueprint_ValidAllTypes(t *testing.T) {
	bp := &Blueprint{
		ID: "all-types",
		Steps: []BlueprintSpec{
			{Type: "shell", Name: "shell-step", Command: "echo shell"},
			{Type: "check", Name: "check-step", Command: "echo check"},
			{Type: "dispatch", Name: "dispatch-step"},
			{Type: "review", Name: "review-step"},
			{Type: "complete", Name: "complete-step"},
		},
	}
	err := validateBlueprint(bp)
	if err != nil {
		t.Errorf("expected no error for valid blueprint, got: %v", err)
	}
}

// TestWave114_BlueprintConfigRoot verifies blueprintConfigRoot produces correct path.
func TestWave114_BlueprintConfigRoot(t *testing.T) {
	root := t.TempDir()
	result := blueprintConfigRoot(root)
	expected := filepath.Join(root, "config", "blueprints")
	if result != expected {
		t.Errorf("expected %q, got %q", expected, result)
	}
}

// TestWave114_ListBlueprints_EmptyDir verifies ListBlueprints returns empty slice
// when config dir doesn't exist.
func TestWave114_ListBlueprints_EmptyDir(t *testing.T) {
	root := t.TempDir()
	rt := NewBlueprintRuntime(nil, root)

	bps, err := rt.ListBlueprints()
	if err != nil {
		t.Fatalf("ListBlueprints with missing config dir: %v", err)
	}
	if len(bps) != 0 {
		t.Errorf("expected empty list, got %d blueprints", len(bps))
	}
}

// TestWave114_ListBlueprints_MultipleFiles verifies ListBlueprints loads and sorts multiple files.
func TestWave114_ListBlueprints_MultipleFiles(t *testing.T) {
	root := t.TempDir()
	bpDir := filepath.Join(root, "config", "blueprints")
	if err := os.MkdirAll(bpDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}

	for _, name := range []string{"zebra", "alpha", "middle"} {
		content := "id: " + name + "\nname: " + name + "\nsteps:\n  - type: review\n    name: step\n"
		if err := os.WriteFile(filepath.Join(bpDir, name+".yaml"), []byte(content), 0o644); err != nil {
			t.Fatalf("write %s: %v", name, err)
		}
	}

	rt := NewBlueprintRuntime(nil, root)
	bps, err := rt.ListBlueprints()
	if err != nil {
		t.Fatalf("ListBlueprints: %v", err)
	}
	if len(bps) != 3 {
		t.Errorf("expected 3 blueprints, got %d", len(bps))
	}
	// Verify sorted
	if bps[0].ID != "alpha" {
		t.Errorf("expected first blueprint 'alpha', got %q", bps[0].ID)
	}
	if bps[2].ID != "zebra" {
		t.Errorf("expected last blueprint 'zebra', got %q", bps[2].ID)
	}
}
