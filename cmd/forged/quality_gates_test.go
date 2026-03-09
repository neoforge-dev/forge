//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestNewQualityGateChecker_Defaults(t *testing.T) {
	q, err := NewQualityGateChecker("")
	if err != nil {
		t.Fatalf("expected no error, got: %v", err)
	}
	if q == nil {
		t.Fatal("expected checker, got nil")
	}
	if !q.config.Coverage.Enabled {
		t.Error("expected coverage enabled by default")
	}
	if q.config.Coverage.Threshold != 80.0 {
		t.Errorf("expected threshold 80.0, got %.1f", q.config.Coverage.Threshold)
	}
	if q.config.Coverage.Command == "" {
		t.Error("expected default coverage command")
	}
	if !q.config.Lint.Enabled {
		t.Error("expected lint enabled by default")
	}
	if !q.config.TypeCheck.Enabled {
		t.Error("expected type check enabled by default")
	}
}

func TestNewQualityGateChecker_NonExistentFile(t *testing.T) {
	// Non-existent file should silently fall back to defaults
	q, err := NewQualityGateChecker("/tmp/nonexistent-quality-config-xyz.yaml")
	if err != nil {
		t.Fatalf("expected no error, got: %v", err)
	}
	if q.config.Coverage.Threshold != 80.0 {
		t.Errorf("expected default threshold 80.0, got %.1f", q.config.Coverage.Threshold)
	}
}

func TestNewQualityGateChecker_WithYAMLConfig(t *testing.T) {
	dir := t.TempDir()
	configPath := filepath.Join(dir, "quality.yaml")

	yaml := `coverage:
  enabled: true
  threshold: 70.0
  command: "echo custom-coverage"
lint:
  enabled: false
  max_issues: 5
  command: "echo custom-lint"
type_check:
  enabled: true
  command: "echo custom-typecheck"
`
	if err := os.WriteFile(configPath, []byte(yaml), 0644); err != nil {
		t.Fatalf("failed to write config: %v", err)
	}

	q, err := NewQualityGateChecker(configPath)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if q.config.Coverage.Threshold != 70.0 {
		t.Errorf("expected threshold 70.0, got %.1f", q.config.Coverage.Threshold)
	}
	if q.config.Coverage.Command != "echo custom-coverage" {
		t.Errorf("expected custom coverage command, got %q", q.config.Coverage.Command)
	}
	if q.config.Lint.Enabled {
		t.Error("expected lint disabled from config")
	}
	if q.config.Lint.MaxIssues != 5 {
		t.Errorf("expected max_issues 5, got %d", q.config.Lint.MaxIssues)
	}
}

func TestNewQualityGateChecker_EnvOverrides(t *testing.T) {
	os.Setenv("FORGE_TEST_COVERAGE_CMD", "echo env-coverage")
	os.Setenv("FORGE_TEST_LINT_CMD", "echo env-lint")
	os.Setenv("FORGE_TEST_TYPECHECK_CMD", "echo env-typecheck")
	defer func() {
		os.Unsetenv("FORGE_TEST_COVERAGE_CMD")
		os.Unsetenv("FORGE_TEST_LINT_CMD")
		os.Unsetenv("FORGE_TEST_TYPECHECK_CMD")
	}()

	q, err := NewQualityGateChecker("")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if q.config.Coverage.Command != "echo env-coverage" {
		t.Errorf("expected env coverage cmd, got %q", q.config.Coverage.Command)
	}
	if q.config.Lint.Command != "echo env-lint" {
		t.Errorf("expected env lint cmd, got %q", q.config.Lint.Command)
	}
	if q.config.TypeCheck.Command != "echo env-typecheck" {
		t.Errorf("expected env typecheck cmd, got %q", q.config.TypeCheck.Command)
	}
}

func TestQualityGateChecker_CheckTypeCheck_Pass(t *testing.T) {
	os.Setenv("FORGE_TEST_TYPECHECK_CMD", "true")
	defer os.Unsetenv("FORGE_TEST_TYPECHECK_CMD")

	q, _ := NewQualityGateChecker("")
	err := q.CheckTypeCheck(context.Background())
	if err != nil {
		t.Errorf("expected pass, got error: %v", err)
	}
}

func TestQualityGateChecker_CheckTypeCheck_Fail(t *testing.T) {
	os.Setenv("FORGE_TEST_TYPECHECK_CMD", "false")
	defer os.Unsetenv("FORGE_TEST_TYPECHECK_CMD")

	q, _ := NewQualityGateChecker("")
	err := q.CheckTypeCheck(context.Background())
	if err == nil {
		t.Error("expected error from failing command")
	}
}

func TestQualityGateChecker_CheckCoverage_ParseOutput(t *testing.T) {
	// Simulate go test -cover output with two packages
	os.Setenv("FORGE_TEST_COVERAGE_CMD", `printf "ok  pkg1\tcoverage: 60.0%% of statements\nok  pkg2\tcoverage: 80.0%% of statements\n"`)
	defer os.Unsetenv("FORGE_TEST_COVERAGE_CMD")

	q, _ := NewQualityGateChecker("")
	cov, err := q.CheckCoverage(context.Background())
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	// Average of 60.0 and 80.0 = 70.0
	if cov < 69.0 || cov > 71.0 {
		t.Errorf("expected ~70.0%% coverage, got %.1f%%", cov)
	}
}

func TestQualityGateChecker_CheckCoverage_NoOutput(t *testing.T) {
	os.Setenv("FORGE_TEST_COVERAGE_CMD", "echo no-coverage-here")
	defer os.Unsetenv("FORGE_TEST_COVERAGE_CMD")

	q, _ := NewQualityGateChecker("")
	cov, err := q.CheckCoverage(context.Background())
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if cov != 0.0 {
		t.Errorf("expected 0.0 when no coverage found, got %.1f", cov)
	}
}

func TestQualityGateChecker_CheckLint_EmptyOutput(t *testing.T) {
	os.Setenv("FORGE_TEST_LINT_CMD", `echo '{"Issues":[]}'`)
	defer os.Unsetenv("FORGE_TEST_LINT_CMD")

	q, _ := NewQualityGateChecker("")
	issues, err := q.CheckLint(context.Background())
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if len(issues) != 0 {
		t.Errorf("expected 0 issues, got %d", len(issues))
	}
}

func TestQualityGateChecker_CheckLint_WithIssues(t *testing.T) {
	jsonOut := `{"Issues":[{"Text":"unused variable","Pos":{"Filename":"foo.go","Line":42},"FromLinter":"unused"}]}`
	os.Setenv("FORGE_TEST_LINT_CMD", `echo '`+jsonOut+`'`)
	defer os.Unsetenv("FORGE_TEST_LINT_CMD")

	q, _ := NewQualityGateChecker("")
	issues, err := q.CheckLint(context.Background())
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
	if len(issues) != 1 {
		t.Fatalf("expected 1 issue, got %d", len(issues))
	}
	if issues[0].File != "foo.go" {
		t.Errorf("expected file foo.go, got %q", issues[0].File)
	}
	if issues[0].Line != 42 {
		t.Errorf("expected line 42, got %d", issues[0].Line)
	}
	if issues[0].Linter != "unused" {
		t.Errorf("expected linter 'unused', got %q", issues[0].Linter)
	}
}

func TestQualityGateChecker_CheckLint_InvalidJSON(t *testing.T) {
	os.Setenv("FORGE_TEST_LINT_CMD", "echo not-json")
	defer os.Unsetenv("FORGE_TEST_LINT_CMD")

	q, _ := NewQualityGateChecker("")
	issues, err := q.CheckLint(context.Background())
	// Invalid JSON returns nil issues, no error (soft fail)
	if err != nil {
		t.Errorf("expected no error on invalid JSON, got: %v", err)
	}
	if issues != nil {
		t.Errorf("expected nil issues on invalid JSON, got %v", issues)
	}
}
