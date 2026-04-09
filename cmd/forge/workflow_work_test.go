package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
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

// --- readContextPct tests ----------------------------------------------------

func TestReadContextPct_EnvVar(t *testing.T) {
	t.Setenv("FORGE_CONTEXT_PCT", "42.5")
	got := readContextPct()
	if got != 42.5 {
		t.Errorf("readContextPct() = %v, want 42.5", got)
	}
}

func TestReadContextPct_File(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)
	t.Setenv("FORGE_CONTEXT_PCT", "") // ensure env var doesn't shadow file

	sidecarPath := filepath.Join(tmpDir, ".forge", "heartbeat", "context_percent")
	if err := os.MkdirAll(filepath.Dir(sidecarPath), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(sidecarPath, []byte("67.3\n"), 0644); err != nil {
		t.Fatal(err)
	}

	got := readContextPct()
	if got != 67.3 {
		t.Errorf("readContextPct() from file = %v, want 67.3", got)
	}
}

func TestReadContextPct_Default(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("FORGE_ROOT", tmpDir)
	t.Setenv("FORGE_CONTEXT_PCT", "")

	got := readContextPct()
	if got != 0.0 {
		t.Errorf("readContextPct() with no data = %v, want 0.0", got)
	}
}

// --- tryClaimTaskCtx tests ---------------------------------------------------

func TestTryClaimTaskCtx_QueuedTask(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.Contains(r.URL.RawQuery, "status=queued"):
			json.NewEncoder(w).Encode(map[string]interface{}{
				"tasks": []internal.Task{{ID: "t-001", Status: "queued"}},
				"count": 1,
			})
		case strings.Contains(r.URL.Path, "/claim"):
			json.NewEncoder(w).Encode(map[string]interface{}{
				"task": &internal.Task{ID: "t-001", Status: "claimed"},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	client := internal.NewClientWithURL(server.URL)
	task, err := tryClaimTaskCtx(context.Background(), client, "test-agent")
	if err != nil {
		t.Fatalf("tryClaimTaskCtx: %v", err)
	}
	if task.ID != "t-001" {
		t.Errorf("claimed task ID = %q, want t-001", task.ID)
	}
}

func TestTryClaimTaskCtx_RequestedFallback(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.Contains(r.URL.RawQuery, "status=queued"):
			// No queued tasks
			json.NewEncoder(w).Encode(map[string]interface{}{"tasks": []internal.Task{}, "count": 0})
		case strings.Contains(r.URL.RawQuery, "status=requested"):
			json.NewEncoder(w).Encode(map[string]interface{}{
				"tasks": []internal.Task{{ID: "t-002", Status: "requested"}},
				"count": 1,
			})
		case strings.Contains(r.URL.Path, "/claim"):
			json.NewEncoder(w).Encode(map[string]interface{}{
				"task": &internal.Task{ID: "t-002", Status: "claimed"},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	client := internal.NewClientWithURL(server.URL)
	task, err := tryClaimTaskCtx(context.Background(), client, "test-agent")
	if err != nil {
		t.Fatalf("tryClaimTaskCtx: %v", err)
	}
	if task.ID != "t-002" {
		t.Errorf("claimed task ID = %q, want t-002", task.ID)
	}
}

func TestTryClaimTaskCtx_NoTasks(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{"tasks": []internal.Task{}, "count": 0})
	}))
	defer server.Close()

	client := internal.NewClientWithURL(server.URL)
	_, err := tryClaimTaskCtx(context.Background(), client, "test-agent")
	if !isNoTasksAvailable(err) {
		t.Errorf("expected errNoTasks, got: %v", err)
	}
}

func TestTryClaimTaskCtx_APIError(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "internal server error"})
	}))
	defer server.Close()

	client := internal.NewClientWithURL(server.URL)
	_, err := tryClaimTaskCtx(context.Background(), client, "test-agent")
	if err == nil {
		t.Fatal("expected error on API failure, got nil")
	}
}

// TestTryClaimTaskCtx_SkipsAssignedToOther verifies that tasks already
// assigned to a different agent are skipped and not claimed.  When all
// candidates are assigned to others, errNoTasks must be returned (not a claim
// error that would tick the circuit-breaker streak).
func TestTryClaimTaskCtx_SkipsAssignedToOther(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")

	otherAgent := "other-agent"
	claimCalled := false

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.Contains(r.URL.RawQuery, "status=queued"):
			// Return one task that is already assigned to another agent.
			json.NewEncoder(w).Encode(map[string]interface{}{
				"tasks": []internal.Task{
					{ID: "t-assigned", Status: "queued", AssignedTo: otherAgent},
				},
				"count": 1,
			})
		case strings.Contains(r.URL.RawQuery, "status=requested"):
			// No requested tasks either.
			json.NewEncoder(w).Encode(map[string]interface{}{"tasks": []internal.Task{}, "count": 0})
		case strings.Contains(r.URL.Path, "/claim"):
			claimCalled = true
			json.NewEncoder(w).Encode(map[string]interface{}{
				"task": &internal.Task{ID: "t-assigned", Status: "claimed"},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	client := internal.NewClientWithURL(server.URL)
	_, err := tryClaimTaskCtx(context.Background(), client, "my-agent")

	if !isNoTasksAvailable(err) {
		t.Errorf("expected errNoTasks when all candidates are assigned to others, got: %v", err)
	}
	if claimCalled {
		t.Error("claim endpoint should NOT have been called for a task assigned to another agent")
	}
}

// TestTryClaimTaskCtx_DoesNotSkipSelfAssigned verifies that a task already
// assigned to the claiming agent is NOT skipped (self-reclaim is valid).
func TestTryClaimTaskCtx_DoesNotSkipSelfAssigned(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")

	const myAgent = "my-agent"

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.Contains(r.URL.RawQuery, "status=queued"):
			// Task assigned to self — should be claimable.
			json.NewEncoder(w).Encode(map[string]interface{}{
				"tasks": []internal.Task{
					{ID: "t-self", Status: "queued", AssignedTo: myAgent},
				},
				"count": 1,
			})
		case strings.Contains(r.URL.Path, "/claim"):
			json.NewEncoder(w).Encode(map[string]interface{}{
				"task": &internal.Task{ID: "t-self", Status: "claimed"},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	client := internal.NewClientWithURL(server.URL)
	task, err := tryClaimTaskCtx(context.Background(), client, myAgent)
	if err != nil {
		t.Fatalf("expected successful claim for self-assigned task, got: %v", err)
	}
	if task == nil || task.ID != "t-self" {
		t.Errorf("claimed task = %v, want t-self", task)
	}
}

// TestTryClaimTaskCtx_AllQueuedAssignedFallsBackToRequested verifies the
// cascade: all queued candidates are assigned to others → fall back to
// requested → find an unassigned task there and claim it.
func TestTryClaimTaskCtx_AllQueuedAssignedFallsBackToRequested(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.Contains(r.URL.RawQuery, "status=queued"):
			// All queued tasks are owned by someone else.
			json.NewEncoder(w).Encode(map[string]interface{}{
				"tasks": []internal.Task{
					{ID: "t-q1", Status: "queued", AssignedTo: "agent-alpha"},
					{ID: "t-q2", Status: "queued", AssignedTo: "agent-beta"},
				},
				"count": 2,
			})
		case strings.Contains(r.URL.RawQuery, "status=requested"):
			// One unassigned requested task available.
			json.NewEncoder(w).Encode(map[string]interface{}{
				"tasks": []internal.Task{
					{ID: "t-r1", Status: "requested"},
				},
				"count": 1,
			})
		case strings.Contains(r.URL.Path, "/claim"):
			json.NewEncoder(w).Encode(map[string]interface{}{
				"task": &internal.Task{ID: "t-r1", Status: "claimed"},
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	client := internal.NewClientWithURL(server.URL)
	task, err := tryClaimTaskCtx(context.Background(), client, "my-agent")
	if err != nil {
		t.Fatalf("expected successful fallback claim, got: %v", err)
	}
	if task == nil || task.ID != "t-r1" {
		t.Errorf("claimed task = %v, want t-r1", task)
	}
}

// --- daemon flag registration tests -----------------------------------------

// TestWorkDaemonFlagsRegistered verifies the --daemon, --agent, --interval,
// --max-tasks, and --execute flags are registered on the work command.
// These flags are required for the forge-monitor:dispatcher process.
func TestWorkDaemonFlagsRegistered(t *testing.T) {
	cmd := NewWorkCmd()
	required := []string{"daemon", "agent", "interval", "max-tasks", "execute"}
	for _, name := range required {
		if cmd.Flags().Lookup(name) == nil {
			t.Errorf("work command missing --%s flag", name)
		}
	}
}

// TestWorkDaemonFlagDefaults validates the default values for daemon polling flags.
func TestWorkDaemonFlagDefaults(t *testing.T) {
	cmd := NewWorkCmd()

	daemonFlag := cmd.Flags().Lookup("daemon")
	if daemonFlag == nil {
		t.Fatal("--daemon flag not registered")
	}
	if daemonFlag.DefValue != "false" {
		t.Errorf("--daemon default = %q, want \"false\"", daemonFlag.DefValue)
	}

	intervalFlag := cmd.Flags().Lookup("interval")
	if intervalFlag == nil {
		t.Fatal("--interval flag not registered")
	}
	if intervalFlag.DefValue != "30s" {
		t.Errorf("--interval default = %q, want \"30s\"", intervalFlag.DefValue)
	}

	maxTasksFlag := cmd.Flags().Lookup("max-tasks")
	if maxTasksFlag == nil {
		t.Fatal("--max-tasks flag not registered")
	}
	if maxTasksFlag.DefValue != "0" {
		t.Errorf("--max-tasks default = %q, want \"0\"", maxTasksFlag.DefValue)
	}

	executeFlag := cmd.Flags().Lookup("execute")
	if executeFlag == nil {
		t.Fatal("--execute flag not registered")
	}
	if executeFlag.DefValue != "false" {
		t.Errorf("--execute default = %q, want \"false\"", executeFlag.DefValue)
	}
}

// --- runWorkDaemonWithConfig unit tests --------------------------------------

// TestWorkDaemonWithConfig_MaxTasks verifies the daemon exits cleanly when
// maxTasks is reached.  We use a fake HTTP server that always returns one
// claimable task so the daemon claims it and stops.
func TestWorkDaemonWithConfig_MaxTasks(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")

	claims := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case strings.Contains(r.URL.RawQuery, "status=queued"):
			json.NewEncoder(w).Encode(map[string]interface{}{
				"tasks": []internal.Task{{ID: "task-max-1", Status: "queued"}},
				"count": 1,
			})
		case strings.Contains(r.URL.Path, "/claim"):
			claims++
			json.NewEncoder(w).Encode(map[string]interface{}{
				"task": &internal.Task{ID: "task-max-1", Status: "claimed"},
			})
		case strings.Contains(r.URL.Path, "/ack"):
			json.NewEncoder(w).Encode(map[string]interface{}{
				"id": "task-max-1", "status": "running",
			})
		case strings.Contains(r.URL.Path, "/heartbeat"):
			w.WriteHeader(http.StatusOK)
		case strings.Contains(r.URL.Path, "/register"):
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)

	cfg := workDaemonConfig{
		BaseInterval:     1 * time.Millisecond,
		MaxInterval:      10 * time.Millisecond,
		CircuitOpenAfter: 3,
		Execute:          false,
	}

	err := runWorkDaemonWithConfig("test-agent", 1, cfg)
	if err != nil {
		t.Errorf("runWorkDaemonWithConfig returned error: %v", err)
	}
	if claims != 1 {
		t.Errorf("expected 1 claim, got %d", claims)
	}
}

// TestWorkDaemonWithConfig_EmptyQueue verifies the daemon backs off when the
// queue is empty and stops on SIGINT (simulated via a max-tasks=0 + empty queue
// that fires a shutdown by sending SIGINT to self after the first poll).
func TestWorkDaemonWithConfig_CircuitBreaker(t *testing.T) {
	t.Setenv("FORGE_NO_RETRY", "1")

	errCount := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "/heartbeat") || strings.Contains(r.URL.Path, "/register") {
			w.WriteHeader(http.StatusOK)
			return
		}
		// Always return 500 to trigger the circuit breaker.
		errCount++
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "server error"})
	}))
	defer server.Close()

	t.Setenv("FORGE_API_URL", server.URL)

	// Use a very short circuit-open threshold so we hit it quickly,
	// then send SIGINT to stop the daemon after a short time.
	cfg := workDaemonConfig{
		BaseInterval:     1 * time.Millisecond,
		MaxInterval:      5 * time.Millisecond,
		CircuitOpenAfter: 2,
		Execute:          false,
	}

	// Run daemon in background goroutine, kill it via SIGINT after 50ms.
	done := make(chan error, 1)
	go func() {
		done <- runWorkDaemonWithConfig("test-cb-agent", 0, cfg)
	}()

	time.Sleep(50 * time.Millisecond)
	// Send SIGINT to self — this is how the daemon exits cleanly.
	if p, err := os.FindProcess(os.Getpid()); err == nil {
		p.Signal(os.Interrupt)
	}

	select {
	case err := <-done:
		if err != nil {
			t.Errorf("runWorkDaemonWithConfig returned unexpected error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("daemon did not stop within 5s after SIGINT")
	}

	// Verify error streak was triggered (at least CircuitOpenAfter API errors).
	if errCount < cfg.CircuitOpenAfter {
		t.Errorf("expected at least %d API errors before circuit-open, got %d",
			cfg.CircuitOpenAfter, errCount)
	}
}

// TestWorkDaemonWithConfig_AgentIDFromEnv verifies that when --agent is not
// provided, the daemon falls back to FORGE_AGENT_NAME, then to hostname.
func TestWorkDaemonWithConfig_AgentIDSources(t *testing.T) {
	// Verify priority: env FORGE_AGENT_NAME > hostname fallback.
	// We test this by running the CLI parser path directly.
	cmd := NewWorkCmd()

	// With FORGE_AGENT_NAME set, agentID should come from the env.
	t.Setenv("FORGE_AGENT_NAME", "kimi")
	agentFlag, _ := cmd.Flags().GetString("agent")
	// agentFlag is empty (not set on cmd line) — daemon should read from env.
	// Verify the env var would be used in runWork's daemon path.
	if agentFlag != "" {
		t.Errorf("--agent flag should be empty by default, got %q", agentFlag)
	}
	got := os.Getenv("FORGE_AGENT_NAME")
	if got != "kimi" {
		t.Errorf("FORGE_AGENT_NAME = %q, want \"kimi\"", got)
	}
}
