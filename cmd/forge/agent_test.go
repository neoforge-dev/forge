package main

import (
	"strings"
	"testing"
)

// TestAgentCommand tests agent command
func TestAgentCommand(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent")
	if err != nil {
		t.Logf("agent: %v", err)
	}
}

// TestAgentList tests agent list command
func TestAgentList(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent", "list")
	if err != nil {
		t.Logf("agent list: %v", err)
	}
}

// TestAgentStatusRemoved verifies that forge agent status no longer exists (CLI Phase 3).
// Its functionality is now available via: forge agent list --work-state
func TestAgentStatusRemoved(t *testing.T) {
	// Verify by inspecting the registered subcommands directly — cobra returns
	// exit 0 for unknown subcommands (prints help), so Execute() is not reliable.
	for _, sub := range agentCmd.Commands() {
		if sub.Name() == "status" {
			t.Error("'agent status' subcommand still registered — should have been removed in CLI Phase 3; use 'agent list --work-state' instead")
		}
	}
}

// TestAgentListWorkStateFlag verifies that forge agent list accepts --work-state flag.
func TestAgentListWorkStateFlag(t *testing.T) {
	if agentListCmd.Flags().Lookup("work-state") == nil {
		t.Error("agentListCmd missing --work-state flag")
	}
}

// TestAgentHealth tests agent health command
func TestAgentHealth(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent", "health", "forge:minimax")
	if err != nil {
		t.Logf("agent health: %v", err)
	}
}

// TestAgentTelemetry tests agent telemetry command
func TestAgentTelemetry(t *testing.T) {
	runner := NewTestRunner(t)

	_, err := runner.Execute("agent", "telemetry", "--status", "active")
	if err != nil {
		t.Logf("agent telemetry: %v", err)
	}
}

func TestGetLocalAgentID(t *testing.T) {
	id := getLocalAgentID()
	if id == "" {
		t.Error("getLocalAgentID() returned empty string")
	}
	// Should be either the hostname or the fallback "my-worker"
	if id == "my-worker" {
		t.Logf("note: hostname unavailable, using fallback %q", id)
	}
}

// ─── agent start subcommand ───────────────────────────────────────────────────

// TestAgentStartSubCmd_Registered verifies the command is registered with the
// correct Use string, Short description, and RunE handler.
func TestAgentStartSubCmd_Registered(t *testing.T) {
	if agentStartSubCmd.Use == "" {
		t.Error("agentStartSubCmd.Use must not be empty")
	}
	if !strings.HasPrefix(agentStartSubCmd.Use, "start") {
		t.Errorf("agentStartSubCmd.Use should start with 'start', got %q", agentStartSubCmd.Use)
	}
	if agentStartSubCmd.Short == "" {
		t.Error("agentStartSubCmd.Short must not be empty")
	}
	if agentStartSubCmd.RunE == nil {
		t.Error("agentStartSubCmd.RunE must be set")
	}
}

// TestAgentStartSubCmd_Flags verifies all expected flags exist on the command.
func TestAgentStartSubCmd_Flags(t *testing.T) {
	for _, flagName := range []string{"restart", "no-heartbeat", "session", "dry-run"} {
		if agentStartSubCmd.Flags().Lookup(flagName) == nil {
			t.Errorf("agentStartSubCmd missing flag --%s", flagName)
		}
	}
}

// TestAgentStartCommands_AllExpectedEntries verifies the canonical map contains
// every agent listed in CLAUDE.md's Agent Start Commands table.
func TestAgentStartCommands_AllExpectedEntries(t *testing.T) {
	required := []string{"kimi", "minimax", "gemini", "pi", "opencode", "kilo", "glm", "codex"}
	for _, name := range required {
		if _, ok := agentStartCommands[name]; !ok {
			t.Errorf("agentStartCommands missing required agent %q", name)
		}
	}
}

// TestAgentStartCommands_NonEmpty ensures every entry has a non-empty command.
func TestAgentStartCommands_NonEmpty(t *testing.T) {
	for name, cmd := range agentStartCommands {
		if strings.TrimSpace(cmd) == "" {
			t.Errorf("agentStartCommands[%q] has empty command string", name)
		}
	}
}

// TestAgentStartCommands_CorrectCommands spot-checks critical flag-sensitive
// mappings that must match CLAUDE.md exactly.
func TestAgentStartCommands_CorrectCommands(t *testing.T) {
	cases := []struct {
		agent string
		want  string
	}{
		{"kimi", "kimi -y"},
		{"gemini", "gemini -y"},
		{"codex", "codex --dangerously-bypass-approvals-and-sandbox"},
		{"glm", "glm"},
		{"minimax", "minimax"},
	}
	for _, c := range cases {
		got, ok := agentStartCommands[c.agent]
		if !ok {
			t.Errorf("agentStartCommands missing %q", c.agent)
			continue
		}
		if got != c.want {
			t.Errorf("agentStartCommands[%q] = %q, want %q", c.agent, got, c.want)
		}
	}
}

// TestRunAgentStartSubCmd_UnknownAgent verifies that an unknown agent name is
// rejected with a descriptive error that includes a recovery hint.
func TestRunAgentStartSubCmd_UnknownAgent(t *testing.T) {
	err := runAgentStartSubCmd("not-a-real-agent", "forge", false, false, false)
	if err == nil {
		t.Fatal("expected error for unknown agent, got nil")
	}
	msg := err.Error()
	if !strings.Contains(msg, "unknown agent") {
		t.Errorf("error should mention 'unknown agent', got: %s", msg)
	}
	if !strings.Contains(msg, "agentStartCommands") {
		t.Errorf("error should include recovery hint referencing agentStartCommands, got: %s", msg)
	}
}

// TestRunAgentStartSubCmd_DryRunKnownAgent verifies dry-run mode prints the
// plan and returns nil without touching tmux.
func TestRunAgentStartSubCmd_DryRunKnownAgent(t *testing.T) {
	runner := NewTestRunner(t)
	out, err := runner.Execute("agent", "start", "kimi", "--dry-run")
	if err != nil {
		t.Fatalf("unexpected error in dry-run: %v", err)
	}
	for _, needle := range []string{"dry-run", "kimi", "forge"} {
		if !strings.Contains(out, needle) {
			t.Errorf("dry-run output missing %q; got:\n%s", needle, out)
		}
	}
}

// TestRunAgentStartSubCmd_DryRunUnknownAgent verifies that name validation runs
// before dry-run output, so unknown agents are still rejected.
func TestRunAgentStartSubCmd_DryRunUnknownAgent(t *testing.T) {
	runner := NewTestRunner(t)
	_, err := runner.Execute("agent", "start", "nobody", "--dry-run")
	if err == nil {
		t.Fatal("expected error for unknown agent even in dry-run mode")
	}
}

// TestRunAgentStartSubCmd_DryRunNoHeartbeat verifies --no-heartbeat suppresses
// the heartbeat line in dry-run output.
func TestRunAgentStartSubCmd_DryRunNoHeartbeat(t *testing.T) {
	runner := NewTestRunner(t)
	out, err := runner.Execute("agent", "start", "kimi", "--dry-run", "--no-heartbeat")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.Contains(out, "Heartbeat") {
		t.Errorf("--no-heartbeat: heartbeat line should be absent, got:\n%s", out)
	}
}

// TestRunAgentStartSubCmd_DryRunCustomSession verifies --session is reflected
// in the dry-run output.
func TestRunAgentStartSubCmd_DryRunCustomSession(t *testing.T) {
	runner := NewTestRunner(t)
	out, err := runner.Execute("agent", "start", "kimi", "--dry-run", "--session", "mysession")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "mysession") {
		t.Errorf("expected custom session name 'mysession' in dry-run output, got:\n%s", out)
	}
}
