package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// newTestTrinityCmd returns a fresh trinityCmd tree for isolation.
// We use the package-level trinityCmd for registration tests, but a fresh
// tree for execution tests to avoid flag-redefinition panics in parallel tests.
func newTestTrinityCmd() *cobra.Command {
	root := &cobra.Command{Use: "forge", SilenceUsage: true, SilenceErrors: true}

	delegate := &cobra.Command{
		Use:  "delegate [task description]",
		RunE: runTrinityDelegate,
	}
	delegate.Flags().String("priority", "medium", "")
	delegate.Flags().String("context", "", "")
	delegate.Flags().Bool("notify", true, "")
	delegate.Flags().Bool("dry-run", false, "")

	list := &cobra.Command{
		Use:  "list",
		RunE: runTrinityList,
	}
	list.Flags().Int("last", 10, "")

	trinity := &cobra.Command{Use: "trinity"}
	trinity.AddCommand(delegate)
	trinity.AddCommand(list)
	root.AddCommand(trinity)
	return root
}

// ── Registration tests ────────────────────────────────────────────────────────

func TestTrinityCmd_Registration(t *testing.T) {
	if trinityCmd.Use != "trinity" {
		t.Errorf("trinityCmd.Use = %q, want %q", trinityCmd.Use, "trinity")
	}
	if trinityCmd.Short == "" {
		t.Error("trinityCmd.Short must not be empty")
	}
}

func TestTrinityDelegateCmd_Registration(t *testing.T) {
	if trinityDelegateCmd.Use == "" {
		t.Error("trinityDelegateCmd.Use must not be empty")
	}
	if !strings.Contains(trinityDelegateCmd.Use, "delegate") {
		t.Errorf("trinityDelegateCmd.Use %q must contain 'delegate'", trinityDelegateCmd.Use)
	}
	if trinityDelegateCmd.Short == "" {
		t.Error("trinityDelegateCmd.Short must not be empty")
	}
	if trinityDelegateCmd.RunE == nil {
		t.Error("trinityDelegateCmd.RunE must not be nil")
	}
}

func TestTrinityListCmd_Registration(t *testing.T) {
	if trinityListCmd.Use == "" {
		t.Error("trinityListCmd.Use must not be empty")
	}
	if !strings.Contains(trinityListCmd.Use, "list") {
		t.Errorf("trinityListCmd.Use %q must contain 'list'", trinityListCmd.Use)
	}
	if trinityListCmd.Short == "" {
		t.Error("trinityListCmd.Short must not be empty")
	}
	if trinityListCmd.RunE == nil {
		t.Error("trinityListCmd.RunE must not be nil")
	}
}

// ── Flag existence tests ──────────────────────────────────────────────────────

func TestTrinityDelegateCmd_Flags(t *testing.T) {
	flagTests := []struct {
		name     string
		wantType string
	}{
		{"priority", "string"},
		{"context", "string"},
		{"notify", "bool"},
		{"dry-run", "bool"},
	}

	for _, tt := range flagTests {
		f := trinityDelegateCmd.Flags().Lookup(tt.name)
		if f == nil {
			t.Errorf("flag --%s not found on trinityDelegateCmd", tt.name)
			continue
		}
		if f.Value.Type() != tt.wantType {
			t.Errorf("flag --%s type = %q, want %q", tt.name, f.Value.Type(), tt.wantType)
		}
	}
}

func TestTrinityListCmd_Flags(t *testing.T) {
	f := trinityListCmd.Flags().Lookup("last")
	if f == nil {
		t.Error("flag --last not found on trinityListCmd")
		return
	}
	if f.Value.Type() != "int" {
		t.Errorf("flag --last type = %q, want %q", f.Value.Type(), "int")
	}
	if f.DefValue != "10" {
		t.Errorf("flag --last default = %q, want %q", f.DefValue, "10")
	}
}

func TestTrinityDelegateCmd_FlagDefaults(t *testing.T) {
	priorityFlag := trinityDelegateCmd.Flags().Lookup("priority")
	if priorityFlag == nil {
		t.Fatal("flag --priority not found")
	}
	if priorityFlag.DefValue != "medium" {
		t.Errorf("--priority default = %q, want %q", priorityFlag.DefValue, "medium")
	}

	notifyFlag := trinityDelegateCmd.Flags().Lookup("notify")
	if notifyFlag == nil {
		t.Fatal("flag --notify not found")
	}
	if notifyFlag.DefValue != "true" {
		t.Errorf("--notify default = %q, want %q", notifyFlag.DefValue, "true")
	}

	dryRunFlag := trinityDelegateCmd.Flags().Lookup("dry-run")
	if dryRunFlag == nil {
		t.Fatal("flag --dry-run not found")
	}
	if dryRunFlag.DefValue != "false" {
		t.Errorf("--dry-run default = %q, want %q", dryRunFlag.DefValue, "false")
	}
}

// ── Missing args error test ───────────────────────────────────────────────────

func TestTrinityDelegate_MissingArgs_ReturnsError(t *testing.T) {
	root := newTestTrinityCmd()
	root.SetArgs([]string{"trinity", "delegate"})
	var buf bytes.Buffer
	root.SetErr(&buf)

	err := root.Execute()
	if err == nil {
		t.Fatal("expected error when no task description is provided, got nil")
	}

	errMsg := err.Error()
	if !strings.Contains(errMsg, "task description required") {
		t.Errorf("error message %q must contain 'task description required'", errMsg)
	}
	// Must include recovery hint (Usage example)
	if !strings.Contains(errMsg, "Usage:") && !strings.Contains(errMsg, "forge trinity delegate") {
		t.Errorf("error message %q must contain recovery hint with usage example", errMsg)
	}
}

// ── Dry-run output tests ──────────────────────────────────────────────────────

func TestTrinityDelegate_DryRun_ShowsExpectedLines(t *testing.T) {
	root := newTestTrinityCmd()

	var out bytes.Buffer
	root.SetOut(&out)

	root.SetArgs([]string{
		"trinity", "delegate", "Deploy VC to Railway",
		"--priority", "high",
		"--context", "Bogdan asked via Telegram",
		"--dry-run",
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	output := out.String()

	checks := []string{
		"[dry-run]",
		"[TRINITY\u2192PRYA]",
		"Deploy VC to Railway",
		"high",
		"Bogdan asked via Telegram",
		"forge:prya",
		"Telegram",
	}
	for _, want := range checks {
		if !strings.Contains(output, want) {
			t.Errorf("dry-run output missing %q\nFull output:\n%s", want, output)
		}
	}
}

func TestTrinityDelegate_DryRun_NoNotify_OmitsTelegramLine(t *testing.T) {
	root := newTestTrinityCmd()

	var out bytes.Buffer
	root.SetOut(&out)

	root.SetArgs([]string{
		"trinity", "delegate", "Test task",
		"--notify=false",
		"--dry-run",
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	output := out.String()
	if strings.Contains(output, "Telegram") {
		t.Errorf("dry-run with --notify=false should not mention Telegram, got:\n%s", output)
	}
}

func TestTrinityDelegate_DryRun_TitleFormat(t *testing.T) {
	root := newTestTrinityCmd()

	var out bytes.Buffer
	root.SetOut(&out)

	root.SetArgs([]string{
		"trinity", "delegate", "Some Task",
		"--dry-run",
	})

	if err := root.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	output := out.String()
	// Title must be prefixed with [TRINITY→PRYA]
	if !strings.Contains(output, "[TRINITY\u2192PRYA] Some Task") {
		t.Errorf("dry-run output must contain [TRINITY→PRYA] Some Task\nGot:\n%s", output)
	}
}

// ── Invalid priority test ─────────────────────────────────────────────────────

func TestTrinityDelegate_InvalidPriority_ReturnsError(t *testing.T) {
	root := newTestTrinityCmd()
	root.SetArgs([]string{
		"trinity", "delegate", "Some task",
		"--priority", "urgent",
		"--dry-run",
	})

	err := root.Execute()
	if err == nil {
		t.Fatal("expected error for invalid priority 'urgent', got nil")
	}
	if !strings.Contains(err.Error(), "invalid priority") {
		t.Errorf("error %q should mention 'invalid priority'", err.Error())
	}
}

// ── logDelegation unit tests ──────────────────────────────────────────────────

func TestLogDelegation_CreatesFileAndDirectory(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	err := logDelegation("[TRINITY→PRYA] Test task", "high", "some context", "TASK-123")
	if err != nil {
		t.Fatalf("logDelegation failed: %v", err)
	}

	logPath := filepath.Join(tmpDir, ".forge", "state", "trinity", "delegations.log")
	data, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("log file not created at %s: %v", logPath, err)
	}

	content := string(data)
	checks := []string{
		"priority=high",
		"task=TASK-123",
		"[TRINITY\u2192PRYA] Test task",
		"context: some context",
	}
	for _, want := range checks {
		if !strings.Contains(content, want) {
			t.Errorf("log missing %q\nFull content:\n%s", want, content)
		}
	}
}

func TestLogDelegation_AppendsMultipleEntries(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	if err := logDelegation("[TRINITY→PRYA] First", "medium", "", ""); err != nil {
		t.Fatalf("first logDelegation failed: %v", err)
	}
	if err := logDelegation("[TRINITY→PRYA] Second", "low", "", "TASK-456"); err != nil {
		t.Fatalf("second logDelegation failed: %v", err)
	}

	logPath := filepath.Join(tmpDir, ".forge", "state", "trinity", "delegations.log")
	data, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("log file not found: %v", err)
	}

	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 2 {
		t.Errorf("expected 2 log lines, got %d\nContent:\n%s", len(lines), string(data))
	}
}

func TestLogDelegation_NoTaskID(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	err := logDelegation("[TRINITY→PRYA] No task ID", "medium", "", "")
	if err != nil {
		t.Fatalf("logDelegation failed: %v", err)
	}

	logPath := filepath.Join(tmpDir, ".forge", "state", "trinity", "delegations.log")
	data, _ := os.ReadFile(logPath)
	if strings.Contains(string(data), "task=") {
		t.Error("log line should not contain 'task=' when task ID is empty")
	}
}

// ── trinityListCmd tests ──────────────────────────────────────────────────────

func TestTrinityList_NoLog_ShowsMessage(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	root := newTestTrinityCmd()
	var out bytes.Buffer
	root.SetOut(&out)
	root.SetArgs([]string{"trinity", "list"})

	if err := root.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !strings.Contains(out.String(), "No delegations") {
		t.Errorf("output %q should mention 'No delegations'", out.String())
	}
}

func TestTrinityList_WithEntries_ShowsLines(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)

	// Pre-create log
	logDir := filepath.Join(tmpDir, ".forge", "state", "trinity")
	if err := os.MkdirAll(logDir, 0755); err != nil {
		t.Fatal(err)
	}
	logContent := "[2026-03-27 10:00:00 UTC] priority=high | [TRINITY→PRYA] Deploy VC\n" +
		"[2026-03-27 10:05:00 UTC] priority=medium | [TRINITY→PRYA] Check status\n"
	if err := os.WriteFile(filepath.Join(logDir, "delegations.log"), []byte(logContent), 0644); err != nil {
		t.Fatal(err)
	}

	root := newTestTrinityCmd()
	var out bytes.Buffer
	root.SetOut(&out)
	root.SetArgs([]string{"trinity", "list"})

	if err := root.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	output := out.String()
	if !strings.Contains(output, "Deploy VC") {
		t.Errorf("output missing 'Deploy VC'\nGot:\n%s", output)
	}
	if !strings.Contains(output, "Check status") {
		t.Errorf("output missing 'Check status'\nGot:\n%s", output)
	}
}
