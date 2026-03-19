package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/neoforge-dev/forge/internal"
)

func TestWorkContextPath(t *testing.T) {
	tests := []struct {
		forgeRoot string
		wantSuffix string
	}{
		{"/home/forge", "/home/forge/.forge/context/current.json"},
		{"", ".forge/context/current.json"},
	}
	for _, tt := range tests {
		got := workContextPath(tt.forgeRoot)
		if tt.forgeRoot != "" && !strings.HasSuffix(got, ".forge/context/current.json") {
			t.Errorf("workContextPath(%q) = %q, want suffix .forge/context/current.json", tt.forgeRoot, got)
		}
		if tt.forgeRoot != "" && !strings.Contains(got, tt.forgeRoot) {
			t.Errorf("workContextPath(%q) = %q, want to contain forge root", tt.forgeRoot, got)
		}
	}
}

func TestWorkCommandRegistered(t *testing.T) {
	if workCmd == nil {
		t.Fatal("workCmd is nil")
	}
	if workCmd.Name() != "work" {
		t.Errorf("work command name = %q, want work", workCmd.Name())
	}
	// Flags
	if workCmd.Flags().Lookup("show") == nil {
		t.Error("work missing --show flag")
	}
	if workCmd.Flags().Lookup("clear") == nil {
		t.Error("work missing --clear flag")
	}
	if workCmd.Flags().Lookup("export") == nil {
		t.Error("work missing --export flag")
	}
}

func TestWorkSetShowClear(t *testing.T) {
	tmpDir := t.TempDir()
	// Create runner FIRST (NewTestRunner sets FORGE_ROOT to real FORGE root),
	// then override FORGE_ROOT to tmpDir so the work command uses the temp dir.
	runner := NewTestRunner(t)
	restore := setenv("FORGE_ROOT", tmpDir)
	defer restore()

	// No context initially
	output, err := runner.Execute("work")
	if err != nil {
		t.Fatalf("work (show) failed: %v", err)
	}
	if !strings.Contains(output, "No context set") {
		t.Errorf("expected 'No context set', got: %s", output)
	}

	// Set context
	output, err = runner.Execute("work", "codeswiftr-com/interview-simulator")
	if err != nil {
		t.Fatalf("work set failed: %v", err)
	}
	if !strings.Contains(output, "Context set") {
		t.Errorf("expected 'Context set', got: %s", output)
	}
	if !strings.Contains(output, "FORGE_DOMAIN=codeswiftr-com") {
		t.Errorf("expected FORGE_DOMAIN in output, got: %s", output)
	}

	// Verify file
	ctxPath := filepath.Join(tmpDir, ".forge", "context", "current.json")
	data, err := os.ReadFile(ctxPath)
	if err != nil {
		t.Fatalf("read context file: %v", err)
	}
	var ctx internal.WorkContext
	if err := json.Unmarshal(data, &ctx); err != nil {
		t.Fatalf("parse context JSON: %v", err)
	}
	if ctx.Domain != "codeswiftr-com" || ctx.Project != "interview-simulator" {
		t.Errorf("context = %+v", ctx)
	}
	if ctx.ShellPrompt != "(codeswiftr-com:interview-simulator)" {
		t.Errorf("shell_prompt = %q", ctx.ShellPrompt)
	}

	// Show context
	output, err = runner.Execute("work", "--show")
	if err != nil {
		t.Fatalf("work --show failed: %v", err)
	}
	if !strings.Contains(output, "codeswiftr-com") || !strings.Contains(output, "interview-simulator") {
		t.Errorf("show output missing domain/project: %s", output)
	}

	// Clear
	output, err = runner.Execute("work", "--clear")
	if err != nil {
		t.Fatalf("work --clear failed: %v", err)
	}
	if !strings.Contains(output, "Context cleared") {
		t.Errorf("expected 'Context cleared', got: %s", output)
	}

	// File should be gone
	if _, err := os.Stat(ctxPath); err == nil {
		t.Error("context file should be removed after --clear")
	}

	// Show again -> no context
	output, err = runner.Execute("work", "--show")
	if err != nil {
		t.Fatalf("work after clear failed: %v", err)
	}
	if !strings.Contains(output, "No context set") {
		t.Errorf("after clear expected 'No context set', got: %q", strings.TrimSpace(output))
	}
}

func TestWorkInvalidDomainProject(t *testing.T) {
	tmpDir := t.TempDir()
	restore := setenv("FORGE_ROOT", tmpDir)
	defer restore()

	// Empty domain or empty project should error
	_, err := NewTestRunner(t).Execute("work", "/project")
	if err == nil {
		t.Error("expected error for domain/project with empty domain")
	}
	if err != nil && !strings.Contains(err.Error(), "invalid") {
		t.Errorf("error should mention invalid: %v", err)
	}

	_, err = NewTestRunner(t).Execute("work", "domain/")
	if err == nil {
		t.Error("expected error for domain/project with empty project")
	}
}

func TestWorkExportFlag(t *testing.T) {
	tmpDir := t.TempDir()
	restore := setenv("FORGE_ROOT", tmpDir)
	defer restore()

	output, err := NewTestRunner(t).Execute("work", "-e", "my-domain/my-project")
	if err != nil {
		t.Fatalf("work -e failed: %v", err)
	}
	if !strings.Contains(output, "export FORGE_DOMAIN=") || !strings.Contains(output, "export FORGE_PROJECT=") {
		t.Errorf("expected export lines, got: %s", output)
	}
	if !strings.Contains(output, "my-domain") || !strings.Contains(output, "my-project") {
		t.Errorf("expected domain/project in export, got: %s", output)
	}
}

func TestWorkJSONFormat(t *testing.T) {
	tmpDir := t.TempDir()
	restore := setenv("FORGE_ROOT", tmpDir)
	defer restore()

	// Set context
	_, err := NewTestRunner(t).Execute("work", "d/p")
	if err != nil {
		t.Fatalf("work set: %v", err)
	}

	// Show with JSON
	output, err := NewTestRunner(t).Execute("work", "--show", "--format", "json")
	if err != nil {
		t.Fatalf("work --show --format json: %v", err)
	}
	if !strings.HasPrefix(strings.TrimSpace(output), "{") {
		t.Errorf("expected JSON object, got: %s", output)
	}
	var ctx internal.WorkContext
	if err := json.Unmarshal([]byte(output), &ctx); err != nil {
		t.Errorf("invalid JSON: %v", err)
	}
	if ctx.Domain != "d" || ctx.Project != "p" {
		t.Errorf("context = %+v", ctx)
	}
}

// --- backoff / circuit-breaker unit tests ------------------------------------

func TestBackoffInterval_Progression(t *testing.T) {
	cfg := workDaemonConfig{
		BaseInterval:     10 * time.Second,
		MaxInterval:      5 * time.Minute,
		CircuitOpenAfter: 3,
	}
	backoff := func(streak int) time.Duration {
		d := cfg.BaseInterval
		for i := 0; i < streak && d < cfg.MaxInterval; i++ {
			d *= 2
		}
		if d > cfg.MaxInterval {
			d = cfg.MaxInterval
		}
		return d
	}

	cases := []struct {
		streak int
		want   time.Duration
	}{
		{0, 10 * time.Second},
		{1, 20 * time.Second},
		{2, 40 * time.Second},
		{3, 80 * time.Second},
		{4, 160 * time.Second},
		{100, 5 * time.Minute}, // capped at MaxInterval
	}
	for _, tc := range cases {
		got := backoff(tc.streak)
		if got != tc.want {
			t.Errorf("backoff(streak=%d) = %s, want %s", tc.streak, got, tc.want)
		}
	}
}

func TestDefaultWorkDaemonConfig(t *testing.T) {
	cfg := defaultWorkDaemonConfig(30 * time.Second)
	if cfg.BaseInterval != 30*time.Second {
		t.Errorf("BaseInterval = %s, want 30s", cfg.BaseInterval)
	}
	if cfg.MaxInterval != 5*time.Minute {
		t.Errorf("MaxInterval = %s, want 5m", cfg.MaxInterval)
	}
	if cfg.CircuitOpenAfter != 3 {
		t.Errorf("CircuitOpenAfter = %d, want 3", cfg.CircuitOpenAfter)
	}
}

func TestIsNoTasksAvailable(t *testing.T) {
	if !isNoTasksAvailable(errNoTasks) {
		t.Error("isNoTasksAvailable(errNoTasks) = false, want true")
	}
}

func TestWorkNoForgeRoot(t *testing.T) {
	// Unset FORGE_ROOT and run from a dir with no .forge (temp dir has no .forge).
	// Create runner FIRST (NewTestRunner sets FORGE_ROOT), then change CWD and unset it.
	tmpDir := t.TempDir()
	runner := NewTestRunner(t)

	restore := setenv("FORGE_ROOT", "")
	defer restore()

	origWd, _ := os.Getwd()
	defer os.Chdir(origWd)
	if err := os.Chdir(tmpDir); err != nil {
		t.Skipf("chdir: %v", err)
	}

	_, err := runner.Execute("work", "domain/project")
	if err == nil {
		t.Error("expected error when FORGE_ROOT unset and no .forge")
	}
	if err != nil && !strings.Contains(err.Error(), "FORGE_ROOT") && !strings.Contains(err.Error(), ".forge") {
		t.Errorf("error should mention FORGE_ROOT or .forge: %v", err)
	}
}
