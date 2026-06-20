// Package main provides integration tests for the V4 CLI
package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/neoforge-dev/forge/internal"
	"github.com/spf13/cobra"
	flag "github.com/spf13/pflag"
)

// TestRunner provides utilities for running CLI commands in tests
type TestRunner struct {
	t       *testing.T
	rootCmd *cobra.Command
}

// NewTestRunner creates a new test runner with fresh commands
func NewTestRunner(t *testing.T) *TestRunner {
	// Normalize to the FORGE repo root so commands that rely on `.forge/...` work.
	if _, file, _, ok := runtime.Caller(0); ok {
		forgeRoot := filepath.Clean(filepath.Join(filepath.Dir(file), "..", ".."))
		_ = os.Setenv("FORGE_ROOT", forgeRoot)
		_ = os.Chdir(forgeRoot)
	}

	// Disable HTTP retries so CLI commands that call the API fail fast on
	// connection errors (1 attempt instead of 4, no backoff delays).
	// This prevents tests from hanging when the remote daemon (prya) is
	// unreachable or not listening on the expected port.
	t.Setenv("FORGE_NO_RETRY", "1")
	if current := os.Getenv("FORGE_API_URL"); current == "" || strings.Contains(current, "localhost:8081") || strings.Contains(current, "nova:8081") {
		t.Setenv("FORGE_API_URL", "http://127.0.0.1:1")
	}

	// Clean up mutable local state so tests don't pollute each other.
	// The approval state file is written by "approval decide" commands and
	// must be removed after each test to ensure subsequent tests start
	// with fresh demo data.
	t.Cleanup(func() {
		os.Remove(filepath.Join(".forge", "approvals", "approvals.json"))
	})

	// Create fresh command instances for testing
	root := &cobra.Command{
		Use:   "forge",
		Short: "FORGE CLI",
		Run:   func(cmd *cobra.Command, args []string) {},
	}
	root.PersistentFlags().StringVar(&outputFormat, "format", "table", "output format (table, json, csv, quiet)")

	// Add commands - these are package-level vars that persist state
	// For integration tests, we test execution paths rather than output content
	root.AddCommand(domainCmd)
	root.AddCommand(portfolioCmd)
	root.AddCommand(approvalCmd)
	root.AddCommand(configCmd)
	root.AddCommand(patternCmd)
	root.AddCommand(NewDispatchCmd())
	root.AddCommand(shipCmd)
	root.AddCommand(NewWorkCmd())
	root.AddCommand(laneCmd)
	root.AddCommand(contextCmd)
	root.AddCommand(taskCmd)
	root.AddCommand(agentCmd)
	root.AddCommand(patrolCmd)
	root.AddCommand(daemonCmd)
	root.AddCommand(versionCmd)
	root.AddCommand(statusCmd)
	root.AddCommand(blueprintCmd)
	root.AddCommand(queueCmd)
	root.AddCommand(recoverCmd)

	return &TestRunner{t: t, rootCmd: root}
}

func resetAllFlags(cmd *cobra.Command) {
	resetFlagSet := func(fs *flag.FlagSet) {
		if fs == nil {
			return
		}
		fs.VisitAll(func(f *flag.Flag) {
			_ = f.Value.Set(f.DefValue)
			f.Changed = false
		})
	}
	resetFlagSet(cmd.Flags())
	resetFlagSet(cmd.PersistentFlags())

	for _, c := range cmd.Commands() {
		resetAllFlags(c)
	}
}

// Execute runs a command and returns output.
// It captures both cobra's output buffer (SetOut) and os.Stdout directly,
// since some commands write via internal.NewFormatter(format, nil) → os.Stdout.
func (tr *TestRunner) Execute(args ...string) (combined string, execErr error) {
	buf := new(bytes.Buffer)
	errBuf := new(bytes.Buffer)
	tr.rootCmd.SetOut(buf)
	tr.rootCmd.SetErr(errBuf)
	tr.rootCmd.SetArgs(args)

	defer func() {
		tr.rootCmd.SetOut(nil)
		tr.rootCmd.SetErr(nil)
	}()

	// Reset all flag values to defaults before each execution to avoid state
	// bleeding between consecutive Execute() calls on shared cobra command objects.
	resetAllFlags(tr.rootCmd)

	// Capture os.Stdout since some commands write via NewFormatter(format, nil) → os.Stdout
	// rather than through cobra's SetOut. We use a SEPARATE stdoutBuf to avoid a data race
	// between the goroutine writing to stdoutBuf and cobra writing to buf directly.
	stdoutBuf := new(bytes.Buffer)
	pr, pw, pipeErr := os.Pipe()
	if pipeErr == nil {
		oldStdout := os.Stdout
		os.Stdout = pw

		stdoutDone := make(chan struct{})
		go func() {
			defer close(stdoutDone)
			io.Copy(stdoutBuf, pr)
			pr.Close()
		}()

		defer func() {
			// Restore stdout, close writer, then wait for goroutine to drain.
			// Named return `combined` is assigned after the goroutine finishes.
			os.Stdout = oldStdout
			pw.Close()
			<-stdoutDone
			combined = buf.String() + stdoutBuf.String() + errBuf.String()
		}()
	}

	execErr = tr.rootCmd.Execute()
	if pipeErr != nil {
		// Pipe failed; fall back to just the cobra buffer.
		combined = buf.String() + errBuf.String()
	}
	return
}

// ExecuteJSON runs a command and parses JSON output
func (tr *TestRunner) ExecuteJSON(target interface{}, args ...string) error {
	allArgs := append(args, "--format", "json")
	output, err := tr.Execute(allArgs...)
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(output), target)
}

// setenv sets env key=value and returns a restore func (for use in other _test.go files)
func setenv(key, value string) func() {
	old, had := os.LookupEnv(key)
	os.Setenv(key, value)
	return func() {
		if had {
			os.Setenv(key, old)
		} else {
			os.Unsetenv(key)
		}
	}
}

// ==================== Command registration ====================

func TestCommandRegistration(t *testing.T) {
	runner := NewTestRunner(t)
	expected := []string{"domain", "approval", "portfolio", "config", "pattern", "dispatch", "ship", "work"}
	for _, cmd := range expected {
		found := false
		for _, c := range runner.rootCmd.Commands() {
			if c.Name() == cmd {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("command %q not registered", cmd)
		}
	}
}

// ==================== Domain Tests ====================

func TestDomainCommands(t *testing.T) {
	tests := []struct {
		name    string
		args    []string
		wantErr bool
	}{
		{
			name:    "list domains",
			args:    []string{"domain", "list"},
			wantErr: false,
		},
		{
			name:    "list domains with active filter",
			args:    []string{"domain", "list", "--active"},
			wantErr: false,
		},
		{
			name:    "show domain",
			args:    []string{"domain", "show", "codeswiftr-com"},
			wantErr: false,
		},
		{
			name:    "show nonexistent domain",
			args:    []string{"domain", "show", "nonexistent-domain"},
			wantErr: true,
		},
		{
			name:    "create domain missing key",
			args:    []string{"domain", "create", "--name", "Test Domain"},
			wantErr: true,
		},
		{
			name:    "create domain missing name",
			args:    []string{"domain", "create", "--key", "test-domain"},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			runner := NewTestRunner(t)
			_, err := runner.Execute(tt.args...)
			if tt.wantErr && err == nil {
				t.Error("expected error but got none")
			}
			if !tt.wantErr && err != nil {
				t.Errorf("unexpected error: %v", err)
			}
		})
	}
}

func TestDomainJSONOutput(t *testing.T) {
	runner := NewTestRunner(t)

	var domains []internal.Domain
	err := runner.ExecuteJSON(&domains, "domain", "list")
	if err != nil {
		t.Fatalf("domain list --format json failed: %v", err)
	}
	if len(domains) == 0 {
		t.Error("expected at least one domain")
	}
}

// ==================== Approval Tests ====================

func TestApprovalCommands(t *testing.T) {
	tests := []struct {
		name    string
		args    []string
		wantErr bool
	}{
		{
			name:    "list approvals",
			args:    []string{"approval", "list"},
			wantErr: false,
		},
		{
			name:    "list pending approvals",
			args:    []string{"approval", "list", "--pending"},
			wantErr: false,
		},
		{
			name:    "show approval",
			args:    []string{"approval", "show", "01APP456XYZ"},
			wantErr: false,
		},
		{
			name:    "show nonexistent approval",
			args:    []string{"approval", "show", "NONEXISTENT"},
			wantErr: true,
		},
		{
			name:    "decide without approve or reject",
			args:    []string{"approval", "decide", "01APP456XYZ"},
			wantErr: true,
		},
		{
			name:    "approve approval",
			args:    []string{"approval", "decide", "01APP456XYZ", "--approve"},
			wantErr: false,
		},
		{
			name:    "reject approval with reason",
			args:    []string{"approval", "decide", "01APP789ABC", "--reject", "--reason", "Tests failing"},
			wantErr: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			runner := NewTestRunner(t)
			writeTestApprovals(t, testApprovals)
			_, err := runner.Execute(tt.args...)
			if tt.wantErr && err == nil {
				t.Error("expected error but got none")
			}
			if !tt.wantErr && err != nil {
				t.Errorf("unexpected error: %v", err)
			}
		})
	}
}

func TestApprovalJSONOutput(t *testing.T) {
	// Use an unreachable API URL so the command falls back to local file data.
	restore := setenv("FORGE_API_URL", "http://127.0.0.1:1")
	defer restore()

	runner := NewTestRunner(t)
	writeTestApprovals(t, testApprovals)

	var approvals []internal.Approval
	err := runner.ExecuteJSON(&approvals, "approval", "list")
	if err != nil {
		t.Fatalf("approval list --format json failed: %v", err)
	}
	if len(approvals) == 0 {
		t.Error("expected at least one approval")
	}
}

// ==================== Config Tests ====================

func TestConfigCommands(t *testing.T) {
	runner := NewTestRunner(t)

	tests := []struct {
		name     string
		args     []string
		wantErr  bool
		contains string
	}{
		{
			name:     "list config",
			args:     []string{"config", "list"},
			wantErr:  false,
			contains: "api_url",
		},
		{
			name:     "get specific config key",
			args:     []string{"config", "get", "api_url"},
			wantErr:  false,
			contains: "http://",
		},
		{
			name:     "get all config (no key)",
			args:     []string{"config", "get"},
			wantErr:  false,
			contains: "API URL",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			output, err := runner.Execute(tt.args...)
			if tt.wantErr && err == nil {
				t.Errorf("expected error but got none, output: %s", output)
			}
			if !tt.wantErr && err != nil {
				t.Errorf("unexpected error: %v, output: %s", err, output)
			}
			if tt.contains != "" && !strings.Contains(output, tt.contains) {
				t.Errorf("expected output to contain %q, got: %s", tt.contains, output)
			}
		})
	}
}

func TestConfigJSONOutput(t *testing.T) {
	runner := NewTestRunner(t)

	var config internal.Config
	err := runner.ExecuteJSON(&config, "config", "list")
	if err != nil {
		t.Fatalf("config list --format json failed: %v", err)
	}
	if config.APIURL == "" {
		t.Error("expected api_url to be set")
	}
}

// ==================== Pattern Tests ====================

func TestPatternCommands(t *testing.T) {
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:1")
	runner := NewTestRunner(t)

	tests := []struct {
		name     string
		args     []string
		wantErr  bool
		contains string
	}{
		{
			name:     "list patterns",
			args:     []string{"pattern", "list"},
			wantErr:  false,
			contains: "Total:",
		},
		{
			name:     "list patterns by category",
			args:     []string{"pattern", "list", "--category", "code"},
			wantErr:  false,
			contains: "Total:",
		},
		{
			name:     "list patterns by tag",
			args:     []string{"pattern", "list", "--tag", "python"},
			wantErr:  false,
			contains: "Total:",
		},
		{
			name:     "show pattern",
			args:     []string{"pattern", "show", "api_endpoint:simple"},
			wantErr:  false,
			contains: "Simple API",
		},
		{
			name:     "show pattern with render",
			args:     []string{"pattern", "show", "documentation:standard", "--render"},
			wantErr:  false,
			contains: "documentation",
		},
		{
			name:     "show nonexistent pattern",
			args:     []string{"pattern", "show", "nonexistent-pattern"},
			wantErr:  true,
			contains: "not found",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			output, err := runner.Execute(tt.args...)
			if tt.wantErr && err == nil {
				t.Errorf("expected error but got none, output: %s", output)
			}
			if !tt.wantErr && err != nil {
				t.Errorf("unexpected error: %v, output: %s", err, output)
			}
			if tt.contains != "" && !strings.Contains(output, tt.contains) {
				t.Errorf("expected output to contain %q, got: %s", tt.contains, output)
			}
		})
	}
}

func TestPatternJSONOutput(t *testing.T) {
	t.Setenv("FORGE_API_URL", "http://127.0.0.1:1")
	runner := NewTestRunner(t)

	var patterns []Pattern
	err := runner.ExecuteJSON(&patterns, "pattern", "list")
	if err != nil {
		t.Fatalf("pattern list --format json failed: %v", err)
	}
	if len(patterns) == 0 {
		t.Error("expected at least one pattern")
	}
}

// ==================== Dispatch Workflow Tests ====================

func TestDispatchWorkflow(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/api/tasks" && r.Method == http.MethodPost:
			var req internal.CreateTaskRequest
			json.NewDecoder(r.Body).Decode(&req)
			task := internal.Task{
				ID:          "task-int-001",
				Title:       req.Subject,
				Description: req.Description,
				Status:      "queued",
			}
			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(task)
		case strings.HasPrefix(r.URL.Path, "/api/tasks/") && strings.HasSuffix(r.URL.Path, "/claim") && r.Method == http.MethodPost:
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(map[string]interface{}{"task": map[string]string{"id": "task-int-001", "status": "claimed"}})
		default:
			w.WriteHeader(http.StatusNotFound)
			json.NewEncoder(w).Encode(map[string]string{"error": "not found"})
		}
	}))
	defer server.Close()

	restore := setenv("FORGE_API_URL", server.URL)
	defer restore()

	runner := NewTestRunner(t)

	tests := []struct {
		name     string
		args     []string
		wantErr  bool
		contains string
	}{
		{
			name:     "dispatch send",
			args:     []string{"dispatch", "send", "forge:kimi", "Integration test task"},
			wantErr:  false,
			contains: "Dispatched",
		},
		{
			name:     "dispatch send missing agent",
			args:     []string{"dispatch", "send"},
			wantErr:  true,
			contains: "agent",
		},
		{
			name:     "dispatch send missing message",
			args:     []string{"dispatch", "send", "forge:kimi"},
			wantErr:  true,
			contains: "message",
		},
		{
			name:     "dispatch show missing task ID",
			args:     []string{"dispatch", "show"},
			wantErr:  true,
			contains: "accepts 1 arg",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			output, err := runner.Execute(tt.args...)
			if tt.wantErr && err == nil {
				t.Errorf("expected error but got none, output: %s", output)
			}
			if !tt.wantErr && err != nil {
				t.Errorf("unexpected error: %v, output: %s", err, output)
			}
			if tt.contains != "" && !strings.Contains(output, tt.contains) {
				t.Errorf("expected output to contain %q, got: %s", tt.contains, output)
			}
		})
	}
}

// ==================== Ship Workflow Tests ====================

func TestShipWorkflow(t *testing.T) {
	runner := NewTestRunner(t)

	tests := []struct {
		name     string
		args     []string
		wantErr  bool
		contains string
	}{
		{
			name:     "ship dry run",
			args:     []string{"ship", "-m", "Test commit", "--dry-run"},
			wantErr:  false,
			contains: "DRY RUN",
		},
		{
			name:     "ship missing message",
			args:     []string{"ship"},
			wantErr:  true,
			contains: "message",
		},
		{
			name:     "ship run with message",
			args:     []string{"ship", "run", "-m", "Test commit", "--dry-run"},
			wantErr:  false,
			contains: "DRY RUN",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			output, err := runner.Execute(tt.args...)
			if tt.wantErr && err == nil {
				t.Errorf("expected error but got none, output: %s", output)
			}
			if !tt.wantErr && err != nil {
				t.Errorf("unexpected error: %v, output: %s", err, output)
			}
			if tt.contains != "" && !strings.Contains(output, tt.contains) {
				t.Errorf("expected output to contain %q, got: %s", tt.contains, output)
			}
		})
	}
}

// ==================== Format Tests ====================

func TestOutputFormats(t *testing.T) {
	runner := NewTestRunner(t)

	t.Run("table format", func(t *testing.T) {
		output, err := runner.Execute("domain", "list", "--format", "table")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if !strings.Contains(output, "codeswiftr-com") {
			t.Errorf("expected output to contain domain name, got: %s", output)
		}
	})

	t.Run("json format", func(t *testing.T) {
		output, err := runner.Execute("domain", "list", "--format", "json")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		var domains []internal.Domain
		if err := json.Unmarshal([]byte(output), &domains); err != nil {
			t.Errorf("output is not valid JSON: %v", err)
		}
	})

	t.Run("csv format", func(t *testing.T) {
		output, err := runner.Execute("pattern", "list", "--format", "csv")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if !strings.Contains(output, "ID") {
			t.Errorf("expected CSV headers, got: %s", output)
		}
	})

	t.Run("quiet format", func(t *testing.T) {
		output, err := runner.Execute("domain", "list", "--format", "quiet")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		// quiet format outputs one ID per line (domain keys only)
		for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
			if line == "" {
				continue
			}
			if strings.ContainsAny(line, " \t") {
				t.Errorf("quiet format should output bare IDs, got line with spaces: %q", line)
			}
		}
	})
}

// ==================== Error Handling Tests ====================

func TestDaemonUnreachableHint(t *testing.T) {
	// Use a server URL that becomes unreachable (close immediately).
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	baseURL := server.URL
	server.Close()

	restore := setenv("FORGE_API_URL", baseURL)
	defer restore()

	runner := NewTestRunner(t)
	output, err := runner.Execute("task", "list")
	if err == nil {
		t.Fatalf("expected error but got none, output: %s", output)
	}
	if !strings.Contains(output, "forge daemon start") && !strings.Contains(err.Error(), "forge daemon start") {
		t.Fatalf("expected recovery hint to contain %q, output: %s, err: %v", "forge daemon start", output, err)
	}
}

func TestErrorHandling(t *testing.T) {
	runner := NewTestRunner(t)

	tests := []struct {
		name    string
		args    []string
		wantErr bool
	}{
		{
			name:    "invalid command",
			args:    []string{"invalid-command"},
			wantErr: true,
		},
		{
			name:    "domain show without key",
			args:    []string{"domain", "show"},
			wantErr: true,
		},
		{
			name:    "approval show without id",
			args:    []string{"approval", "show"},
			wantErr: true,
		},
		{
			name:    "project show without key",
			args:    []string{"project", "show"},
			wantErr: true,
		},
		{
			name:    "pattern show without id",
			args:    []string{"pattern", "show"},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := runner.Execute(tt.args...)
			if tt.wantErr && err == nil {
				t.Errorf("expected error but got none")
			}
			if !tt.wantErr && err != nil {
				t.Errorf("unexpected error: %v", err)
			}
		})
	}
}
